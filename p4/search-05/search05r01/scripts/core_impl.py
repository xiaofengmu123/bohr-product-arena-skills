#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- 多数据库联合检索核心库（纯标准库、确定性、无网络）。

证据溯源强化变体。本模块保证：
  1. 每条关键结论（字段取值 / 去重判定 / 冲突消解 / 覆盖缺口 / 相关性提示）都携带
     - locator： "文件路径#JSON指针" 或 "文件路径#页码/行号"
     - quote：   从原始响应中抽取的逐字值（不做改写、不重述）
  2. evidence_map 细分为 claims / field_sources / dedup_clusters / conflicts /
     coverage_evidence 五个可单独核对的面。
  3. 确定性：所有记录、去重、合并与编号顺序均由排序键唯一决定，无随机源。
  4. 失败降级：缺字段填 null 并记 missing_metadata；缺库/坏文件记 degradations。

JSON 指针遵循 RFC 6901（~1 -> "/"，~0 -> "~"）。quote 取 leaf 值经
json.dumps(..., ensure_ascii=False) 无损序列化，字符区间 char_span 尽力定位。
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024

# 固定输出字段（无论案例是否提供 output_contract 都写入）
FIXED_FIELDS = [
    "case_id", "status", "research_question", "search_scope", "candidates",
    "ranking", "evidence_records", "evidence_map", "coverage",
    "empty_results", "conflicts", "missing_metadata", "permission_issues",
    "limitations", "sources", "uncertainty", "change_log", "errors",
    "degradations", "pending_human", "runtime",
]

DEFAULT_CONTRACT = [
    "research_question", "search_scope", "candidates", "ranking",
    "evidence_records", "coverage", "empty_results", "conflicts",
    "missing_metadata", "permission_issues", "limitations",
]

# 记录身份的弱键（用于通用记录识别，值量适中避免误判）
_STRONG_ID_KEYS = {"doi", "pmid", "pmcid", "pubmedid"}
_WEAK_RECORD_KEYS = {
    "title", "authors", "author", "authorlist", "year", "pubyear",
    "journal", "journaltitle", "publicationtitle", "container-title",
    "abstract", "abstracttext", "volume", "issue", "pages", "pageinfo",
    "publisher", "pubtype",
}
_NON_RECORD_CONTEXT = {
    "query", "facet", "facetcounts", "link", "author", "journal",
    "journalinfo", "affiliation", "license", "score", "count",
}

# 字段级合并优先级：值越小优先级越高；未列出的字段用 DB_ORDER。
DB_ORDER = {
    "crossref": 0, "europepmc": 1, "pubmed": 2, "publisher": 3,
    "generic": 4, "unknown": 5,
}
FIELD_PRIORITY = {
    "doi": {"crossref": 0, "europepmc": 0, "pubmed": 1, "publisher": 1},
    "pmid": {"pubmed": 0, "europepmc": 1},
    "pmcid": {"europepmc": 0, "pubmed": 1},
    "abstract": {"europepmc": 0, "pubmed": 0, "crossref": 1, "publisher": 2},
    "authors": {"crossref": 0, "europepmc": 1, "pubmed": 1},
    "year": {"crossref": 0, "europepmc": 1, "pubmed": 1},
    "doc_type": {"crossref": 0, "europepmc": 1, "pubmed": 1},
}

CANONICAL_SCALAR_FIELDS = [
    "title", "year", "journal", "volume", "issue", "pages", "doi", "pmid",
    "pmcid", "doc_type", "abstract", "publisher",
]
CANONICAL_LIST_FIELDS = ["authors", "issn"]

_JSON_UTF = "utf-8-sig"


# --------------------------------------------------------------------------- #
# 基础 IO
# --------------------------------------------------------------------------- #
def read_text_bytes(path: Path) -> Tuple[str, List[str]]:
    """按 utf-8-sig -> gb18030 -> latin-1 顺序容错解码，返回 (text, degradations)。"""
    data = path.read_bytes()
    for enc in (_JSON_UTF, "gb18030", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), \
        ["undecodable_text: %s" % _rel(path)]


def load_json(path: Path) -> Tuple[Optional[Any], List[str]]:
    """读取并解析 JSON；坏 JSON 返回 (None, 错误列表)。"""
    if path.stat().st_size > MAX_FILE_BYTES:
        return None, ["file_too_large: %s" % _rel(path)]
    text, deg = read_text_bytes(path)
    try:
        return json.loads(text), deg
    except (ValueError, TypeError) as exc:
        return None, deg + ["json_parse_error: %s (%s)" % (_rel(path), exc)]


def discover_input_files(input_dir: Path) -> List[Path]:
    """发现输入目录下所有材料文件，按相对路径排序，超限文件跳过并返回。"""
    if not input_dir.exists():
        return []
    out = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        out.append(p)
    return out


def classify_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".pdf":
        return "pdf"
    if ext in (".txt", ".md", ".tsv", ".csv"):
        return "text"
    return "other"


def _rel(path: Path, base: Optional[Path] = None) -> str:
    try:
        if base is not None:
            return path.relative_to(base).as_posix()
        return path.as_posix()
    except ValueError:
        return path.as_posix()


def parse_pdf_text(path: Path) -> Tuple[str, List[str]]:
    """优先调用 pdftotext（预留 -enc UTF-8），失败则降级为路径占位。"""
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(path), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.decode("utf-8", errors="replace"), []
        return "", ["pdftotext_failed: %s rc=%s" % (_rel(path), proc.returncode)]
    except FileNotFoundError:
        return "", ["pdftotext_missing: %s" % _rel(path)]
    except Exception as exc:  # noqa: BLE001
        return "", ["pdftotext_error: %s (%s)" % (_rel(path), exc)]


# --------------------------------------------------------------------------- #
# JSON 指针与逐字引用
# --------------------------------------------------------------------------- #
def escape_pointer_seg(seg: str) -> str:
    return seg.replace("~", "~0").replace("/", "~1")


def unescape_pointer_seg(seg: str) -> str:
    return seg.replace("~1", "/").replace("~0", "~")


def join_pointer(*segs: str) -> str:
    parts = [escape_pointer_seg(str(s)) for s in segs if str(s) not in ("", "/")]
    return "/" + "/".join(parts)


def navigate_pointer(obj: Any, pointer: str) -> Any:
    """按 RFC6901 指针定位；指针为空或 '/' 表示根对象。"""
    if pointer in ("", "/", "$", "#"):
        return obj
    if pointer.startswith("#"):
        pointer = pointer[1:]
    cur = obj
    for raw in pointer.split("/"):
        if raw == "":
            continue
        seg = unescape_pointer_seg(raw)
        if isinstance(cur, dict):
            if seg not in cur:
                raise KeyError("pointer key not found: %s" % seg)
            cur = cur[seg]
        elif isinstance(cur, list):
            cur = cur[int(seg)]
        else:
            raise TypeError("cannot navigate into non-container at %s" % seg)
    return cur


def navigate_key_path(record: Dict, key_path: str) -> Any:
    """在单条记录 dict 内按点分路径取值（如 journalInfo.journal.title）。"""
    cur = record
    for raw in key_path.split("."):
        if isinstance(cur, dict):
            if raw not in cur:
                raise KeyError("key_path missing: %s" % raw)
            cur = cur[raw]
        elif isinstance(cur, list):
            cur = cur[int(raw)]
        else:
            raise TypeError("bad key_path segment %s" % raw)
    return cur


def render_quote(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def locate_char_span(raw_text: str, value: Any) -> Optional[List[int]]:
    """尽力在原文中定位某值的字符区间 [start, end)；仅当值足够长且唯一时返回，
    避免把 "6"、"21" 这类歧义短值错误定位到无关位置。失败返回 None。"""
    if not raw_text:
        return None
    candidates = []
    if isinstance(value, str):
        candidates.append(value)
    candidates.append(render_quote(value))
    if not isinstance(value, str):
        candidates.append(json.dumps(value, ensure_ascii=True))
        candidates.append(json.dumps(value, ensure_ascii=False))
    for cand in candidates:
        if not cand or len(cand) < 8:
            continue
        if raw_text.count(cand) == 1:
            i = raw_text.find(cand)
            return [i, i + len(cand)]
    return None


# --------------------------------------------------------------------------- #
# 响应分类与记录抽取
# --------------------------------------------------------------------------- #
def classify_response(obj: Any) -> str:
    if not isinstance(obj, dict):
        return "unknown"
    if "message" in obj and isinstance(obj.get("message"), dict) and \
            isinstance(obj["message"].get("items"), list):
        return "crossref"
    if isinstance(obj.get("resultList"), dict) and \
            isinstance(obj["resultList"].get("result"), list):
        return "europepmc"
    if isinstance(obj.get("result"), dict) and \
            isinstance(obj["result"].get("uids"), list):
        return "pubmed_esummary"
    if "esearchresult" in obj:
        return "pubmed_esearch"
    if isinstance(obj.get("response"), dict) and \
            isinstance(obj["response"].get("docs"), list):
        return "publisher"  # 常见出版社/聚合器形态
    return "generic"


def db_label(db: str) -> str:
    """内部库标识 -> 对外统一标签（PubMed 的 esearch/esummary 归并为 pubmed）。"""
    if db.startswith("pubmed"):
        return "pubmed"
    return db


def extract_records(obj: Any) -> List[Tuple[Dict, str]]:
    """返回 [(原始记录 dict, JSON 指针)]。"""
    db = classify_response(obj)
    if db == "crossref":
        items = obj["message"]["items"]
        return [(it, join_pointer("message", "items", i))
                for i, it in enumerate(items) if isinstance(it, dict)]
    if db == "europepmc":
        res = obj["resultList"]["result"]
        return [(r, join_pointer("resultList", "result", i))
                for i, r in enumerate(res) if isinstance(r, dict)]
    if db == "pubmed_esummary":
        res = obj["result"]
        return [(res[u], join_pointer("result", u)) for u in res["uids"]
                if isinstance(res.get(u), dict)]
    if db == "pubmed_esearch":
        # 仅 idlist，无文献级字段，不作为候选；查询级信息由 extract_request 记录
        return []
    if db == "publisher":
        docs = obj["response"]["docs"]
        return [(d, join_pointer("response", "docs", i))
                for i, d in enumerate(docs) if isinstance(d, dict)]
    return _extract_generic(obj)


def extract_request(obj: Any) -> Optional[str]:
    """尽力抽取响应自带的查询式（用于库—查询—响应链路）。"""
    if not isinstance(obj, dict):
        return None
    m = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    for cand in (
        obj.get("request", {}) if isinstance(obj.get("request"), dict) else {},
        m.get("query") if isinstance(m.get("query"), dict) else {},
        obj.get("esearchresult", {}) if isinstance(obj.get("esearchresult"), dict) else {},
    ):
        for k in ("queryString", "search-terms", "searchTerms", "term", "q",
                  "query", "search_query"):
            if k in cand and cand[k]:
                v = cand[k]
                return v if isinstance(v, str) else json.dumps(
                    v, ensure_ascii=False)
    return None


def _extract_generic(obj: Any) -> List[Tuple[Dict, str]]:
    """未知结构：递归找叶子级文献记录 dict。"""
    found: List[Tuple[Dict, str]] = []

    def looks_like_record(d: Dict) -> bool:
        keys = {str(k).lower() for k in d.keys()}
        if keys & _STRONG_ID_KEYS:
            return True
        hit = [k for k in (_WEAK_RECORD_KEYS & keys)]
        return len(hit) >= 3 and ("title" in keys or "journal" in keys)

    def scan(node: Any, ptr: str) -> bool:
        # 返回 True 表示该子树内有记录节点（用于剪枝祖先包装层）
        if isinstance(node, dict):
            if looks_like_record(node):
                found.append((node, ptr))
                return True
            any_child_record = False
            for k, v in node.items():
                if str(k).lower() in _NON_RECORD_CONTEXT:
                    continue
                any_child_record |= scan(v, join_pointer(ptr, k))
            return any_child_record
        if isinstance(node, list):
            out = False
            for i, v in enumerate(node):
                out |= scan(v, join_pointer(ptr, i))
            return out
        return False

    scan(obj, "")
    # 若同时命中祖先与后代，保留后代（更深层级）
    keep = []
    for rec, ptr in found:
        deeper = [p for _, p in found if p != ptr and p.startswith(ptr + "/")]
        if deeper:
            continue
        keep.append((rec, ptr))
    keep.sort(key=lambda t: t[1])
    return keep


# --------------------------------------------------------------------------- #
# 字段归一
# --------------------------------------------------------------------------- #
def _first(x: Any) -> Optional[str]:
    if isinstance(x, list):
        return str(x[0]) if x and x[0] is not None else None
    if x is None:
        return None
    if isinstance(x, str):
        return x
    return json.dumps(x, ensure_ascii=False)


def _to_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return str(x)
    return str(x)


def _norm_doi(x: Any) -> Optional[str]:
    if not x:
        return None
    s = str(x).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = s.strip().lower()
    return s or None


def _norm_issn(x: Any) -> List[str]:
    out = []
    src = x if isinstance(x, list) else [x]
    for item in src:
        if isinstance(item, dict):
            item = item.get("value") or item.get("Value") or _first(
                item.get("ISSN") or [])
        if item:
            out.append(str(item).strip())
    seen = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return sorted(seen)


def _authors_str_list(authors: Any) -> List[str]:
    if not authors:
        return []
    if isinstance(authors, str):
        s = authors.strip()
        if not s:
            return []
        # 优先按分号切；逗号出现在 "Family, Given" 中，此处保守处理
        parts = [p.strip() for p in re.split(r";|\s*,\s*(?=[A-Z][a-z])", s)]
        return [p for p in parts if p]
    out = []
    for a in authors:
        name = a if isinstance(a, str) else a.get("name") or a.get("fullName") \
            or a.get("full_name")
        if not name and isinstance(a, dict):
            g = a.get("given") or a.get("firstName")
            f = a.get("family") or a.get("lastName")
            name = (f + ", " + g) if f and g else (f or g)
        if name:
            out.append(str(name).strip())
    return out


def _extract_year(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        y = int(x)
        return y if 1400 <= y <= 2200 else None
    if isinstance(x, dict):
        for k in ("date-parts", "year"):
            if k in x:
                return _extract_year(x[k])
        for k in ("value", "Value"):
            if k in x:
                return _extract_year(x[k])
        return None
    if isinstance(x, list):
        for v in x:
            y = _extract_year(v)
            if y:
                return y
        return None
    s = str(x).strip()
    m = re.search(r"(18|19|20|21)\d{2}", s)
    if m:
        y = int(m.group(0))
        if 1400 <= y <= 2200:
            return y
    return None


def normalize_record(raw: Dict, db: str) -> Dict:
    """返回 {values, key_paths}。values 为规范字段；key_paths 记录取值来源键路径。"""
    val: Dict[str, Any] = {}
    kp: Dict[str, str] = {}

    def put(field: str, value: Any, path: str, prefer_existing: bool = False):
        if value is None or value == "" or value == []:
            return
        if prefer_existing and val.get(field):
            return
        val[field] = value
        kp[field] = path

    if db == "crossref":
        put("title", _first(raw.get("title")), "title")
        put("authors", _authors_str_list(raw.get("author")), "author")
        put("year", _extract_year(raw.get("issued")), "issued")
        put("journal", _first(raw.get("container-title")), "container-title")
        put("volume", _to_str(raw.get("volume")), "volume")
        put("issue", _to_str(raw.get("issue")), "issue")
        put("pages", _to_str(raw.get("page")), "page", prefer_existing=True)
        put("pages", _to_str(raw.get("article-number")), "article-number")
        put("doi", _norm_doi(raw.get("DOI")), "DOI")
        put("issn", _norm_issn(raw.get("ISSN")), "ISSN")
        put("doc_type", _to_str(raw.get("type")), "type")
        put("abstract", _first(raw.get("abstract")), "abstract")
        put("publisher", _to_str(raw.get("publisher")), "publisher")
    elif db == "europepmc":
        ji = raw.get("journalInfo") if isinstance(raw.get("journalInfo"), dict) \
            else {}
        jr = ji.get("journal") if isinstance(ji.get("journal"), dict) else {}
        put("title", _to_str(raw.get("title")), "title")
        if raw.get("authorList"):
            put("authors", _authors_str_list(raw.get("authorList")), "authorList")
        else:
            put("authors", _authors_str_list(raw.get("authorString")),
                "authorString")
        put("year", _extract_year(raw.get("pubYear")), "pubYear")
        if raw.get("journalTitle"):
            put("journal", _to_str(raw.get("journalTitle")), "journalTitle")
        else:
            put("journal", _to_str(jr.get("title")), "journalInfo.journal.title")
        if raw.get("journalVolume") is not None:
            put("volume", _to_str(raw.get("journalVolume")), "journalVolume")
        else:
            put("volume", _to_str(ji.get("volume")), "journalInfo.volume")
        if raw.get("issue") is not None:
            put("issue", _to_str(raw.get("issue")), "issue")
        else:
            put("issue", _to_str(ji.get("issue")), "journalInfo.issue")
        if raw.get("pageInfo") is not None:
            put("pages", _to_str(raw.get("pageInfo")), "pageInfo")
        else:
            put("pages", _to_str(ji.get("pages")), "journalInfo.pages")
        put("doi", _norm_doi(raw.get("doi")), "doi")
        put("pmid", _to_str(raw.get("pmid")), "pmid")
        put("pmcid", _to_str(raw.get("pmcid")), "pmcid")
        put("issn", _norm_issn(jr.get("issn")), "journalInfo.journal.issn")
        put("doc_type", _to_str(raw.get("pubType")), "pubType")
        put("abstract", _to_str(raw.get("abstractText")), "abstractText")
        put("publisher", _to_str(raw.get("publisher")), "publisher")
    elif db in ("pubmed_esummary", "pubmed"):
        aa = raw.get("authors") or []
        authors = [a.get("name") for a in aa if isinstance(a, dict)] \
            if isinstance(aa, list) else _authors_str_list(aa)
        put("title", _to_str(raw.get("title")), "title")
        put("authors", authors, "authors")
        put("year", _extract_year(raw.get("pubdate")), "pubdate")
        put("journal", _to_str(raw.get("source") or raw.get("fulljournalname")
                               or _to_str(raw.get("journal"))), "source")
        put("volume", _to_str(raw.get("volume")), "volume")
        put("issue", _to_str(raw.get("issue")), "issue")
        put("pages", _to_str(raw.get("pages")), "pages")
        for aid in (raw.get("articleids") or []):
            if isinstance(aid, dict) and aid.get("idtype") == "doi":
                put("doi", _norm_doi(aid.get("value")), "articleids")
        put("doi", _norm_doi(
            _doi_from_elocation(raw.get("elocationid"))), "elocationid")
        put("pmid", _to_str(raw.get("uid")), "uid")
        put("doc_type", _to_str(_first(raw.get("pubtype"))), "pubtype")
        put("abstract", _to_str(raw.get("abstract")), "abstract")
    elif db == "publisher":
        put("title", _to_str(raw.get("title") or raw.get("dc:title")
                             or _to_str(raw.get("articleTitle"))), "title")
        put("authors", _authors_str_list(raw.get("authors")
                                         or raw.get("author")
                                         or raw.get("creators")
                                         or raw.get("dc:creator")), "authors")
        put("year", _extract_year(raw.get("year") or raw.get("publicationDate")
                                  or raw.get("pubyear")), "year")
        put("journal", _to_str(raw.get("journal")
                               or raw.get("publicationTitle")
                               or raw.get("container")
                               or _to_str(raw.get("source"))), "journal")
        put("volume", _to_str(raw.get("volume")), "volume")
        put("issue", _to_str(raw.get("issue")), "issue")
        put("pages", _to_str(raw.get("pages") or raw.get("page")), "pages")
        put("doi", _norm_doi(raw.get("doi") or raw.get("DOI")), "doi")
        put("pmid", _to_str(raw.get("pmid") or raw.get("PMID")), "pmid")
        put("pmcid", _to_str(raw.get("pmcid")), "pmcid")
        put("issn", _norm_issn(raw.get("issn") or raw.get("ISSN")), "issn")
        put("doc_type", _to_str(raw.get("docType") or raw.get("type")), "docType")
        put("abstract", _to_str(raw.get("abstract") or
                                raw.get("description")), "abstract")
        put("publisher", _to_str(raw.get("publisher")), "publisher")
    else:
        _normalize_generic(raw, put)

    # identifiers 汇总
    ids: Dict[str, Any] = {}
    for k in ("doi", "pmid", "pmcid"):
        if val.get(k):
            ids[k] = val[k]
    return {"values": val, "key_paths": kp, "identifiers": ids}


def _doi_from_elocation(x: Any) -> Optional[str]:
    if not x:
        return None
    s = str(x).strip()
    m = re.match(r"doi\s*:\s*(.+)$", s, flags=re.I)
    return m.group(1) if m else s


def _normalize_generic(raw: Dict, put):
    """通用键名别名映射（未知库兜底）。"""
    aliases = {
        "title": ["title", "articleTitle", "name", "dc.title"],
        "authors": ["authors", "author", "authorList", "creators"],
        "year": ["year", "pubYear", "publicationYear", "date"],
        "journal": ["journal", "journalTitle", "publicationTitle",
                    "container-title", "source"],
        "volume": ["volume", "journalVolume"],
        "issue": ["issue"],
        "pages": ["pages", "page", "pageInfo"],
        "doi": ["doi", "DOI"],
        "pmid": ["pmid", "PMID"],
        "pmcid": ["pmcid", "PMCID"],
        "issn": ["issn", "ISSN"],
        "doc_type": ["doc_type", "docType", "type", "pubType"],
        "abstract": ["abstract", "abstractText", "description"],
        "publisher": ["publisher"],
    }
    for field, keys in aliases.items():
        for k in keys:
            if k in raw and raw.get(k) not in (None, "", []):
                if field == "authors":
                    put(field, _authors_str_list(raw.get(k)), k)
                elif field == "year":
                    put(field, _extract_year(raw.get(k)), k)
                elif field == "doi":
                    put(field, _norm_doi(raw.get(k)), k)
                elif field == "issn":
                    put(field, _norm_issn(raw.get(k)), k)
                else:
                    put(field, _to_str(raw.get(k)), k)
                break


def norm_title(x: Optional[str]) -> str:
    if not x:
        return ""
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", x.lower())


def first_author_norm(authors: List[str]) -> str:
    if not authors:
        return ""
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", authors[0].lower())


# --------------------------------------------------------------------------- #
# 去重（union-find，边记录规则用于证据）
# --------------------------------------------------------------------------- #
def dedup_records(records: List[Dict]) -> Tuple[List[List[int]], List[Dict]]:
    """records 为已归一化成员列表；返回 (簇列表, 边列表)。"""
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        parent[rb] = ra
        return True

    edges: List[Dict] = []
    # pass 1：DOI 精确
    by_doi: Dict[str, int] = {}
    for i, r in enumerate(records):
        d = r["values"].get("doi")
        if d:
            if d in by_doi:
                edges.append({"a": by_doi[d], "b": i, "rule": "doi_exact"})
                union(by_doi[d], i)
            else:
                by_doi[d] = i

    # pass 2：标题 + 一作 + 年份精确
    by_key: Dict[str, int] = {}
    for i, r in enumerate(records):
        t = norm_title(r["values"].get("title"))
        fa = first_author_norm(r["values"].get("authors") or [])
        y = r["values"].get("year")
        if t and y is not None:
            key = t + "|" + fa + "|" + str(y)
            if key in by_key:
                edges.append({"a": by_key[key], "b": i, "rule": "title_author_year"})
                union(by_key[key], i)
            else:
                by_key[key] = i

    # pass 3：标题模糊（同一年份桶内 ratio >= 0.90）
    buckets: Dict[int, List[int]] = {}
    for i, r in enumerate(records):
        y = r["values"].get("year")
        t = norm_title(r["values"].get("title"))
        if y is not None and t:
            buckets.setdefault(int(y), []).append(i)
    for _y, idxs in buckets.items():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                ti = norm_title(records[i]["values"].get("title"))
                tj = norm_title(records[j]["values"].get("title"))
                if not ti or not tj:
                    continue
                if difflib.SequenceMatcher(None, ti, tj).ratio() >= 0.90:
                    edges.append({"a": i, "b": j, "rule": "title_fuzzy_0.90"})
                    union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = [sorted(v) for v in groups.values()]
    clusters.sort(key=lambda c: (min(c), c))
    return clusters, edges


# --------------------------------------------------------------------------- #
# 合并
# --------------------------------------------------------------------------- #
def db_by_source(source_file: str) -> str:
    stem = Path(source_file).stem.lower()
    for db in ("crossref", "europepmc", "pubmed", "publisher"):
        if db in stem or db in source_file.lower():
            return db
    return "generic"


def _field_rank(db: str, field: str) -> int:
    if field in FIELD_PRIORITY and db in FIELD_PRIORITY[field]:
        return FIELD_PRIORITY[field][db] * 100 + DB_ORDER.get(db, 9)
    return DB_ORDER.get(db, 9)


def pick_best_indices(members: List[Dict], field: str) -> List[int]:
    """返回贡献非空值的成员索引，按该字段的优先级排序。"""
    order = []
    for i, m in enumerate(members):
        v = m["values"].get(field)
        if field in CANONICAL_LIST_FIELDS:
            if v:
                order.append(i)
        elif v is not None and v != "":
            order.append(i)
    order.sort(key=lambda i: (_field_rank(members[i]["db"], field), i))
    return order


def merge_cluster(members: List[Dict]) -> Dict:
    """把一个去重簇合并为单条候选。members 已按 (db 优先级, 原序) 排序。"""
    merged: Dict[str, Any] = {}
    provenances = []
    field_sources: Dict[str, List[Dict]] = {}
    for m in members:
        provenances.append({
            "db": m["db"], "source_file": m["source_file"],
            "locator": m["locator"], "record_index": m["record_index"],
            "query": m.get("request"), "raw_snapshot": m.get("raw_snapshot", {}),
        })

    dict_fields = set(CANONICAL_SCALAR_FIELDS + CANONICAL_LIST_FIELDS)
    for field in dict_fields:
        idxs = pick_best_indices(members, field)
        if not idxs:
            merged[field] = [] if field in CANONICAL_LIST_FIELDS else None
            continue
        if field in CANONICAL_LIST_FIELDS:
            seen, vals = [], []
            for i in idxs:
                for v in (members[i]["values"].get(field) or []):
                    if v not in seen:
                        seen.append(v)
                        vals.append(v)
            merged[field] = vals
        else:
            vals = [members[i]["values"].get(field) for i in idxs]
            merged[field] = vals[0]
        field_sources[field] = [{
            "db": members[i]["db"], "source_file": members[i]["source_file"],
            "locator": members[i]["locator"], "value": _safe_value(
                members[i]["values"].get(field)), "member_index": i,
        } for i in idxs]

    merged["identifiers"] = _merge_identifiers(members)
    merged["provenance"] = provenances
    merged["field_sources"] = field_sources
    merged["cluster_size"] = len(members)
    return merged


def _safe_value(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return json.loads(json.dumps(v, ensure_ascii=False))


def _merge_identifiers(members: List[Dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for m in members:
        for k, v in (m.get("identifiers") or {}).items():
            if v and k not in out:
                out[k] = v
    return out


# --------------------------------------------------------------------------- #
# 覆盖缺口
# --------------------------------------------------------------------------- #
def build_coverage(all_members: List[Dict], merged_count: int,
                   duplicates_merged: int) -> Dict:
    dbs = sorted({m["db"] for m in all_members})
    per_db: Dict[str, Dict[str, int]] = {}
    for db in dbs:
        ms = [m for m in all_members if m["db"] == db]
        missing: Dict[str, int] = {}
        for f in CANONICAL_SCALAR_FIELDS + CANONICAL_LIST_FIELDS:
            missing[f] = sum(1 for m in ms if not m["values"].get(f))
        per_db[db] = {f: c for f, c in missing.items() if c > 0}

    systematically_missing = []
    for db in dbs:
        for f, c in per_db[db].items():
            systematically_missing.append({
                "db": db, "field": f, "records_missing": c,
            })

    # 仅单库收录：出现在唯一 db 的记录族
    db_of_record: Dict[str, set] = {}
    for m in all_members:
        db_of_record.setdefault(m["record_id"], set()).add(m["db"])
    single = sum(1 for s in db_of_record.values() if len(s) == 1)

    return {
        "databases": dbs,
        "total_input_records": len(all_members),
        "merged_record_count": merged_count,
        "duplicates_merged": duplicates_merged,
        "records_in_single_db_only": single,
        "systematically_missing_fields": sorted(
            systematically_missing, key=lambda x: (x["db"], x["field"])),
        "per_db_fields_missing": per_db,
    }


# --------------------------------------------------------------------------- #
# 证据体系（claims / field_sources / dedup / conflicts / coverage）
# --------------------------------------------------------------------------- #
class EvidenceBuilder:
    """累积证据，最终稳定排序并分配证据编号。"""

    def __init__(self):
        self.evs: List[Dict] = []
        self.claims: List[Dict] = []
        self.dedup_clusters: List[Dict] = []
        self.conflicts: List[Dict] = []
        self.coverage_evidence: List[Dict] = []
        self._c = 0

    def add(self, ev: Dict) -> int:
        idx = len(self.evs)
        self.evs.append(ev)
        return idx

    def eid(self, idx: int) -> str:
        return self.evs[idx].get("evidence_id", "ev:?")

    def finalize(self):
        def key(idx: int) -> tuple:
            e = self.evs[idx]
            return (e.get("type", ""), e.get("source_file", ""),
                    e.get("field", ""), e.get("locator", ""),
                    e.get("rule", ""), e.get("record_index", -1))
        order = sorted(range(len(self.evs)), key=key)
        for rank, i in enumerate(order):
            self.evs[i]["evidence_id"] = "ev:%d" % (rank + 1)


def _raw_at_key_path(raw: Dict, key_path: str) -> Any:
    try:
        return navigate_key_path(raw, key_path)
    except (KeyError, TypeError, ValueError):
        return None


def primary_db(members: List[Dict]) -> str:
    return members[0]["db"] if members else "unknown"


# 主构建入口（在 main 中调用）：构建字段/去重/冲突/覆盖证据
# 返回 (field_ev, conflicted)；claims 与 field_sources 的证据号在 finalize 后由
# build_claims 回填，保证 evidence_id 为最终编号。
def build_evidence(evb: EvidenceBuilder, members: List[Dict],
                   merged_records: List[Dict], clusters: List[List[int]],
                   edges: List[Dict], source_raw_text: Dict[str, str]) -> \
        Tuple[Dict, Dict]:
    field_ev: Dict[Tuple[int, str], int] = {}

    # 1) 字段级证据（每个成员、每个非空字段）
    for mi, m in enumerate(members):
        raw = m["raw"]
        for field in CANONICAL_SCALAR_FIELDS + CANONICAL_LIST_FIELDS:
            v = m["values"].get(field)
            if v is None or v == "" or v == []:
                continue
            kp = m["key_paths"].get(field)
            if not kp:
                continue
            raw_val = _raw_at_key_path(raw, kp)
            quote = render_quote(raw_val if raw_val is not None else v)
            rel = m["source_file"]
            loc = rel + "#" + m["pointer"] + ":" + kp
            idx = evb.add({
                "type": "field_value", "db": m["db"],
                "source_file": rel, "locator": loc,
                "pointer": m["pointer"], "key_path": kp, "field": field,
                "quote": quote, "value": _safe_value(v),
                "record_index": m["record_index"],
                "char_span": locate_char_span(source_raw_text.get(rel, ""),
                                              raw_val if raw_val is not None else v),
            })
            field_ev[(mi, field)] = idx

    # 2) 去重边证据
    for e in edges:
        ma, mb = members[e["a"]], members[e["b"]]
        qa = render_quote(ma["values"].get("title") or "")
        qb = render_quote(mb["values"].get("title") or "")
        la = ma["source_file"] + "#" + ma["pointer"]
        lb = mb["source_file"] + "#" + mb["pointer"]
        evb.add({
            "type": "dedup_match", "db": None, "source_file": None,
            "locator": None, "field": "title", "rule": e["rule"],
            "quote": qa, "quote_left": qa, "quote_right": qb,
            "locator_left": la, "locator_right": lb,
            "record_index": None,
        })

    # 3) dedup_clusters（带成员 + 规则）
    for ci, cluster in enumerate(clusters):
        ci_rule = None
        for e in edges:
            if e["a"] in cluster and e["b"] in cluster:
                ci_rule = e["rule"]
                break
        entries = []
        for i in cluster:
            m = members[i]
            entries.append({
                "member_index": i, "db": m["db"],
                "source_file": m["source_file"], "locator": m["locator"],
                "title": m["values"].get("title"),
            })
        evb.dedup_clusters.append({
            "cluster_id": "cl:%d" % (ci + 1),
            "member_indices": cluster,
            "match_rule": ci_rule or "identity",
            "entries": entries,
        })

    # 4) 冲突消解（字段级）
    conflicted: Dict[Tuple[str, str], bool] = {}
    for merged in merged_records:
        rec_id = merged["record_id"]
        for field in CANONICAL_SCALAR_FIELDS:
            fs = merged.get("field_sources", {}).get(field) or []
            if len(fs) < 2:
                continue
            distinct = _distinct_values(merged, field, members)
            if len(distinct) < 2:
                continue
            chosen = merged.get(field)
            flagged = field in {"year", "title", "doi", "pages"}
            evb.conflicts.append({
                "conflict_id": "cf:%d" % (len(evb.conflicts) + 1),
                "record_id": rec_id, "field": field,
                "values": distinct,
                "resolution": "priority",
                "resolved_value": _safe_value(chosen),
                "rule": "field_priority: %s" % field,
                "needs_human_arbitration": flagged,
                "note": "同一去重簇内多个来源取值不一致，按字段优先级消解" if flagged else
                "同一去重簇内多来源取值不一致，按字段优先级消解，不阻塞交付",
            })
            conflicted[(rec_id, field)] = True

    # 5) 覆盖缺口证据（缺字段：记录容器已定位但键缺失）
    dbs_seen = sorted({m["db"] for m in members})
    for db in dbs_seen:
        ms = [m for m in members if m["db"] == db]
        for f in CANONICAL_SCALAR_FIELDS + CANONICAL_LIST_FIELDS:
            missing = [m for m in ms if not m["values"].get(f)]
            if not missing:
                continue
            locs = [m["source_file"] + "#" + m["pointer"] for m in missing]
            evb.coverage_evidence.append({
                "db": db, "field": f, "absent": True,
                "records_missing": len(missing),
                "record_locators": locs[:20],
                "note": "parsed record container; field %s absent" % f,
            })

    return field_ev, conflicted


def build_claims(evb: EvidenceBuilder, members: List[Dict],
                 merged_records: List[Dict], field_ev: Dict,
                 conflicted: Dict) -> None:
    """在 evb.finalize() 之后调用：为每条合并记录回填 claims 与 field_sources 证据号。"""
    for merged in merged_records:
        rec_id = merged["record_id"]
        for field in CANONICAL_SCALAR_FIELDS + CANONICAL_LIST_FIELDS:
            fs = merged.get("field_sources", {}).get(field) or []
            if not fs:
                continue
            primary = fs[0]
            ev_id = field_ev.get((primary["member_index"], field))
            for src in fs:
                src_ev = field_ev.get((src["member_index"], field))
                if src_ev is not None:
                    src["evidence_id"] = evb.eid(src_ev)
            value = merged.get(field)
            verdict = "conflict_resolved" if conflicted.get(
                (rec_id, field)) else "asserted"
            evb.claims.append({
                "claim_id": "c:%d" % (len(evb.claims) + 1),
                "record_id": rec_id, "field": field,
                "value": _safe_value(value),
                "evidence_id": evb.eid(ev_id) if ev_id is not None else None,
                "verdict": verdict,
            })


def _distinct_values(merged: Dict, field: str, members: List[Dict]) -> List[Dict]:
    """从 field_sources 反推该字段所有不同取值及来源。
    标题/期刊用归一化比较（忽略大小写与标点），避免句末句号等拼写差异误报冲突。"""
    fs = merged.get("field_sources", {}).get(field) or []
    out, seen = [], []
    for src in fs:
        v = src.get("value")
        if field in ("title", "journal"):
            key = norm_title(_to_str(v) or "")
        elif isinstance(v, (dict, list)):
            key = json.dumps(v, ensure_ascii=False, sort_keys=True)
        else:
            key = repr(v)
        if key in seen:
            continue
        seen.append(key)
        out.append({"value": _safe_value(v), "db": src.get("db"),
                    "source_file": src.get("source_file"),
                    "locator": src.get("locator")})
    return out


# --------------------------------------------------------------------------- #
# 相关性提示（确定性词项命中）
# --------------------------------------------------------------------------- #
def relevance_hint(candidate: Dict, query_terms: List[str]) -> Dict:
    text = ((candidate.get("title") or "") + " " +
            (candidate.get("abstract") or "")).lower()
    hits = []
    for t in query_terms:
        tl = t.lower().strip()
        if tl and tl in text and tl not in hits:
            hits.append(tl)
    score = len(hits)
    return {
        "score": score,
        "matched_terms": hits,
        "basis": "token_overlap",
        "rationale": ("命中 %d 个检索词：%s" % (score, ", ".join(hits))
                      if hits else "未命中给定检索词"),
    }


# --------------------------------------------------------------------------- #
# 覆盖报告（Markdown）
# --------------------------------------------------------------------------- #
def render_coverage_report(result: Dict, contract_note: str) -> str:
    lines = [
        "# 多数据库联合检索覆盖报告", "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 研究问题: %s" % (result.get("research_question") or "(未提供)"),
        "- 输出契约字段回填: %s" % contract_note, "",
        "## 来源清单", "",
    ]
    for s in result.get("sources", []):
        extra = ""
        if s.get("note"):
            extra = "（%s）" % s["note"]
        if s.get("record_count") is not None:
            lines.append("- `%s` %s/%s 记录 %s" % (
                s.get("path"), s.get("type"), s.get("db") or "-",
                s.get("record_count")))
        else:
            lines.append("- `%s` %s%s" % (s.get("path"), s.get("type"), extra))
    cov = result.get("coverage") or {}
    lines += ["", "## 去重与覆盖", "",
              "- 输入记录总数: %s" % cov.get("total_input_records"),
              "- 合并后记录数: %s" % cov.get("merged_record_count"),
              "- 去重并入: %s" % cov.get("duplicates_merged"),
              "- 仅单库收录记录: %s" % cov.get("records_in_single_db_only"), "",
              "### 各库系统缺失字段", ""]
    for item in cov.get("systematically_missing_fields") or []:
        lines.append("- `%s` 缺 `%s`（%s 条）" % (
            item.get("db"), item.get("field"), item.get("records_missing")))
    if not cov.get("systematically_missing_fields"):
        lines.append("- （无）")
    lines += ["", "## 冲突", ""]
    for c in result.get("conflicts", []):
        lines.append("- `%s` 字段 `%s`：各源取值 %s，冲突消解保留 `%s`，需人工仲裁=%s" % (
            c.get("record_id"), c.get("field"),
            ", ".join(repr(v.get("value")) for v in c.get("values", [])),
            c.get("resolved_value"), c.get("needs_human_arbitration")))
    if not result.get("conflicts"):
        lines.append("- （无）")
    lines += ["", "## 局限与待人工", ""]
    for l in result.get("limitations", []):
        lines.append("- %s" % l)
    for l in result.get("pending_human", []):
        lines.append("- [待人工] %s" % l)
    if not (result.get("limitations") or result.get("pending_human")):
        lines.append("- （无）")
    return "\n".join(lines) + "\n"