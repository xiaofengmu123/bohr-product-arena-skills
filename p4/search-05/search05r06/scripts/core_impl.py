#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- 多数据库联合检索核心库（仅标准库，确定性，无网络）。

本库是流水线的事实引擎：把 /input 下的异构响应解析为统一中间表示，
再做去重、合并、覆盖比较与证据抽取。所有结论均可沿
「库—查询—原始响应—字段映射—去重—合并」链路回溯。

设计取舍（本版与直接拼接原始响应的区别）：
  1. 不直接把原始响应塞进 result.json，而是先生成两条结构化中间产物：
     - plan.json        （检索执行计划快照：输入清单、库分类、规则选择、覆盖目标）
     - record_model.json（记录中间表示：归一化记录、逐字段来源、去重簇、合并决策、冲突）
     result.json 由 record_model.json 派生，任何字段都能回指到原始响应与映射规则。
  2. 归一化只做“语义等价”映射（标题/作者/期刊/年份/标识符），不做改写；
     缺失与未知严格区分（null = 源里没有，not_reported = 源里有占位但无值）。
  3. 去重采用确定性优先级：DOI 精确 > 标题+一作姓氏+年份 > 标题模糊（同一年）。
  4. 合并按字段来源优先级取非空值，冲突不静默吞掉而是写入 conflicts。

只依赖 Python 标准库；不联网；同输入重复运行内容确定（runtime 时间戳除外）。
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "2.0.0"
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024

# 固定输出字段（当 /input/case.json 未提供 output_contract 时的兜底契约）。
DEFAULT_CONTRACT = [
    "case_id", "status", "research_question", "search_scope", "candidates",
    "ranking", "evidence_records", "coverage", "empty_results", "conflicts",
    "missing_metadata", "permission_issues", "limitations", "sources",
    "uncertainty", "change_log", "errors", "degradations", "pending_human",
    "runtime",
]

# 数据库合并优先级：数值越大越优先。标识符字段另有专用优先级。
DB_PRIORITY = {
    "crossref": 50,
    "europepmc": 40,
    "pubmed": 30,
    "publisher": 20,
    "generic": 10,
    "unknown": 0,
}

# 每个规范字段的“优先来源库”序列；落在序列中的库先于其它库取值。
FIELD_PREFERRED_DB: Dict[str, List[str]] = {
    "doi": ["crossref", "europepmc", "pubmed", "publisher"],
    "pmid": ["pubmed", "europepmc", "crossref", "publisher"],
    "pmcid": ["europepmc", "pubmed", "crossref", "publisher"],
    "abstract": ["europepmc", "pubmed", "crossref", "publisher"],
    "issn": ["crossref", "europepmc", "pubmed", "publisher"],
}

CANONICAL_SCALAR_FIELDS = [
    "title", "year", "journal", "volume", "issue", "pages",
    "doi", "pmid", "pmcid", "doc_type", "abstract", "publisher",
]
CANONICAL_LIST_FIELDS = ["authors", "issn"]

_STOPWORDS = {
    "a", "an", "the", "of", "and", "or", "in", "on", "for", "with", "to",
    "by", "at", "from", "as", "is", "are", "was", "were", "into", "using",
    "via", "et", "al",
}


def iso_utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 文本 / 文件工具
# --------------------------------------------------------------------------- #
def discover_input_files(input_dir: Path) -> List[Path]:
    """返回按相对路径排序的输入文件；超限文件跳过并返回其说明（经 return）。"""
    if not input_dir.is_dir():
        return []
    out: List[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if p.is_file():
            out.append(p)
    return out


def classify_file(p: Path) -> str:
    suf = p.suffix.lower()
    if suf == ".json":
        return "json"
    if suf == ".pdf":
        return "pdf"
    if suf in {".txt", ".md", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".html"}:
        return "text"
    return "other"


def read_text(p: Path) -> Tuple[str, List[str]]:
    deg: List[str] = []
    try:
        raw = p.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            return "", ["file_too_large(%d bytes): %s" % (len(raw), p.name)]
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw.decode(enc), deg
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), ["bad_encoding: %s" % p.name]
    except Exception as e:  # noqa: BLE001
        return "", ["read_failed: %s: %s" % (p.name, e)]


def load_json(p: Path) -> Tuple[Optional[Any], List[str]]:
    text, deg = read_text(p)
    if not text.strip():
        return None, ["empty_json: %s" % p.name]
    try:
        return json.loads(text), deg
    except json.JSONDecodeError as e:
        # 容错：截到最后一个可解析的顶层闭合处再试一次。
        for cut in (e.pos, len(text)):
            try:
                return json.loads(text[:cut]), deg + ["json_truncated_recovered: %s" % p.name]
            except json.JSONDecodeError:
                continue
        return None, deg + ["bad_json: %s: %s" % (p.name, e)]


def parse_pdf_text(p: Path) -> Tuple[str, List[str]]:
    """用 pdftotext 抽文本；缺工具或失败时降级并记录，绝不编造正文。"""
    deg: List[str] = []
    exe = shutil.which("pdftotext")
    if not exe:
        return "", ["pdftotext_missing: PDF 未抽文本: %s" % p.name]
    try:
        fd, tmp = tempfile.mkstemp(suffix=".txt")
        import os
        os.close(fd)
        r = subprocess.run(
            [exe, "-enc", "UTF-8", str(p), tmp],
            capture_output=True, timeout=90,
        )
        if r.returncode != 0:
            return "", ["pdftotext_failed(%d): %s" % (r.returncode, p.name)]
        text = Path(tmp).read_text(encoding="utf-8", errors="replace")
        Path(tmp).unlink(missing_ok=True)
        return text, deg
    except Exception as e:  # noqa: BLE001
        return "", ["pdftotext_error: %s: %s" % (p.name, e)]


# --------------------------------------------------------------------------- #
# 字符串归一化（只做等价映射，不改写语义）
# --------------------------------------------------------------------------- #
def _strip(s: Any) -> Optional[str]:
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return s or None


def _first_str(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return _strip(v)
    if isinstance(v, (list, tuple)):
        for x in v:
            s = _strip(x)
            if s:
                return s
        return None
    if isinstance(v, dict):
        return _first_str(v.get("value"))
    if v is None:
        return None
    s = _strip(v)
    return s or None


def _first_nonempty(*vals: Any) -> Optional[str]:
    for v in vals:
        s = _first_str(v)
        if s:
            return s
    return None


def norm_key(s: Optional[str]) -> str:
    """标题/期刊的归一化键：去重音、小写、去标点、空白折叠。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_doi(s: Optional[str]) -> Optional[str]:
    v = _strip(s)
    if not v:
        return None
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi:\s*", "", v, flags=re.I)
    v = v.strip().lower()
    return v or None


def norm_year(v: Any) -> Optional[str]:
    s = _strip(v)
    if not s:
        return None
    m = re.search(r"(1[6-9]\d{2}|20\d{2})", s)
    return m.group(1) if m else None


def _surname(s: str) -> str:
    parts = [x for x in re.split(r"[,\s]+", s.strip()) if x]
    if not parts:
        return ""
    # "Family, Given" 时取逗号前；否则取最后一个词作为姓氏近似。
    if "," in s:
        return norm_key(parts[0])
    return norm_key(parts[-1])


def _norm_author_list(v: Any) -> Tuple[List[str], str]:
    """返回 (作者显示列表, 一作姓氏键)。"""
    if v is None:
        return [], ""
    entries: List[Any] = []
    if isinstance(v, str):
        # authorString: "A, B, C"
        entries = [x for x in v.split(";") if x.strip()] or [v]
    elif isinstance(v, list):
        entries = v
    elif isinstance(v, dict):
        # EuropePMC authors.author[] 结构
        inner = v.get("author")
        if isinstance(inner, list):
            entries = inner
    names: List[str] = []
    surnames: List[str] = []
    for a in entries:
        if isinstance(a, str):
            s = a.strip()
            if s:
                names.append(s)
                surnames.append(_surname(s))
        elif isinstance(a, dict):
            given = _strip(a.get("given")) or _strip(a.get("firstName")) or _strip(a.get("foreName")) or ""
            family = _strip(a.get("family")) or _strip(a.get("lastName")) or _strip(a.get("surname")) or ""
            full = _strip(a.get("name")) or _strip(a.get("fullName")) or _strip(a.get("full_name")) or ""
            if full and not (given or family):
                names.append(full)
                surnames.append(_surname(full))
            elif family or given:
                names.append(" ".join(x for x in [given, family] if x))
                surnames.append(norm_key(family) or _surname(full or given))
    return names, (surnames[0] if surnames else "")


def _norm_issn(v: Any) -> List[str]:
    out: List[str] = []
    if isinstance(v, str):
        v = [v]
    if isinstance(v, (list, tuple)):
        for x in v:
            if isinstance(x, str):
                s = re.sub(r"[^0-9Xx]", "", x).upper()
                if s:
                    out.append(s)
            elif isinstance(x, dict):
                s = _strip(x.get("value"))
                if s:
                    out.append(re.sub(r"[^0-9Xx]", "", s).upper())
    # 去重保序
    seen = set()
    res = []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def _strip_xml(s: Optional[str]) -> Optional[str]:
    v = _strip(s)
    if not v:
        return None
    v = re.sub(r"<[^>]+>", " ", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip() or None


# --------------------------------------------------------------------------- #
# 响应分类 / 记录抽取
# --------------------------------------------------------------------------- #
def classify_response(obj: Any) -> str:
    if isinstance(obj, list):
        return "generic"
    if not isinstance(obj, dict):
        return "unknown"
    msg = obj.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("items"), list):
        return "crossref"
    rl = obj.get("resultList")
    if isinstance(rl, dict) and "result" in rl:
        return "europepmc"
    result = obj.get("result")
    if isinstance(result, dict) and isinstance(result.get("uids"), list):
        return "pubmed"
    if isinstance(obj.get("esearchresult"), dict):
        return "pubmed"
    if _looks_like_record(obj):
        return "publisher"
    if _contains_record(obj):
        return "publisher"
    return "unknown"


def _looks_like_record(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    title = _first_str(d.get("title")) or _first_str(d.get("Title"))
    ident = d.get("doi") or d.get("DOI") or d.get("pmid") or d.get("PMID") \
        or d.get("pmcid") or d.get("pmcId") or d.get("year") or d.get("pubYear")
    authors = d.get("authors") or d.get("author") or d.get("authorList")
    return bool(title and (ident or authors))


def _contains_record(obj: Any, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(obj, dict):
        if _looks_like_record(obj):
            return True
        return any(_contains_record(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_record(v, depth + 1) for v in obj)
    return False


def _generic_records(obj: Any, path: str, out: List[Tuple[dict, str]], depth: int) -> None:
    if depth > 8 or len(out) > 5000:
        return
    if isinstance(obj, dict):
        if _looks_like_record(obj):
            out.append((obj, path))
            return
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                _generic_records(v, path + "." + str(k), out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                _generic_records(v, "%s[%d]" % (path, i), out, depth + 1)


def extract_records(obj: Any) -> List[Tuple[dict, str]]:
    """从一次数据库响应里抽取 (原始记录, locator)。locator 是 JSON 定位串。"""
    if isinstance(obj, list):
        recs = [(it, "[%d]" % i) for i, it in enumerate(obj) if _looks_like_record(it)]
        return recs
    if not isinstance(obj, dict):
        return []
    msg = obj.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("items"), list):
        return [(it, "message.items[%d]" % i) for i, it in enumerate(msg["items"])]
    rl = obj.get("resultList")
    if isinstance(rl, dict) and isinstance(rl.get("result"), list):
        return [(r, "resultList.result[%d]" % i) for i, r in enumerate(rl["result"])]
    result = obj.get("result")
    if isinstance(result, dict) and isinstance(result.get("uids"), list):
        recs = []
        for uid in result["uids"]:
            doc = result.get(uid)
            if isinstance(doc, dict):
                recs.append((doc, "result[%s]" % uid))
        return recs
    es = obj.get("esearchresult")
    if isinstance(es, dict):
        idlist = es.get("idlist")
        if isinstance(idlist, list) and idlist:
            return [({"uid": u, "_id_only": True}, "esearchresult.idlist[%d]" % i)
                    for i, u in enumerate(idlist)]
    out: List[Tuple[dict, str]] = []
    _generic_records(obj, "$", out, 0)
    return out


def extract_request(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    rl = obj.get("resultList")
    if isinstance(rl, dict) and isinstance(rl.get("request"), dict):
        qs = rl["request"].get("queryString")
        if qs:
            return _strip(qs)
    msg = obj.get("message")
    if isinstance(msg, dict):
        q = msg.get("query")
        if isinstance(q, dict):
            st = q.get("search-terms") or q.get("query") or q.get("queryString")
            return _strip(st) or (json.dumps(q, ensure_ascii=False)[:500] if q else None)
        if q:
            return _strip(q)
    es = obj.get("esearchresult")
    if isinstance(es, dict):
        return _strip(es.get("querytranslation") or es.get("term"))
    # 出版社等异构响应：常见的请求字段
    for k in ("query", "search_terms", "searchTerms", "queryString", "q"):
        if k in obj and obj[k]:
            return _strip(obj[k])
    return None


# --------------------------------------------------------------------------- #
# 字段归一化（各库 → 规范字段）
# --------------------------------------------------------------------------- #
def _year_from_crossref(raw: dict) -> Optional[str]:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        v = raw.get(key)
        if isinstance(v, dict):
            dp = v.get("date-parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                return norm_year(dp[0][0])
    return norm_year(raw.get("year"))


def _year_from_pubdate(s: Any) -> Optional[str]:
    return norm_year(s)


def _articleids(doc: dict) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    for it in doc.get("articleids") or []:
        if isinstance(it, dict):
            idtype = _strip(it.get("idtype"))
            val = _strip(it.get("value"))
            if idtype and val:
                ids[idtype.lower()] = val
    return ids


def _normalize_pubmed(doc: dict) -> Dict[str, Any]:
    ids = _articleids(doc)
    authors = doc.get("authors") or []
    names = [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
    return {
        "title": _first_str(doc.get("title")),
        "authors": names,
        "year": _year_from_pubdate(doc.get("pubdate") or doc.get("epubdate")),
        "journal": _first_str(doc.get("fulljournalname")) or _first_str(doc.get("source")),
        "volume": _first_str(doc.get("volume")),
        "issue": _first_str(doc.get("issue")),
        "pages": _first_str(doc.get("pages")),
        "doi": norm_doi(ids.get("doi") or doc.get("doi")),
        "pmid": _first_str(doc.get("uid") or doc.get("pmid")),
        "pmcid": _first_str(ids.get("pmc") or doc.get("pmcid")),
        "issn": _norm_issn(doc.get("issn")),
        "doc_type": _first_str(doc.get("pubtype")),
        "abstract": None,
        "publisher": _first_str(doc.get("publisher")),
    }


def _normalize_europepmc(r: dict) -> Dict[str, Any]:
    ji = r.get("journalInfo") if isinstance(r.get("journalInfo"), dict) else {}
    authors: Any = r.get("authors")
    if isinstance(authors, dict):
        authors = authors.get("author")
    author_names = []
    if isinstance(authors, list):
        for a in authors:
            if isinstance(a, dict):
                author_names.append(_first_str(a.get("fullName")) or _first_str(a.get("name")))
    if not author_names and r.get("authorString"):
        author_names = [x.strip() for x in str(r["authorString"]).split(";") if x.strip()]
    return {
        "title": _first_str(r.get("title")),
        "authors": author_names,
        "year": _year_from_pubdate(r.get("pubYear")),
        "journal": _first_str(r.get("journalTitle")) or _first_str(ji.get("journal", {}).get("title") if isinstance(ji.get("journal"), dict) else None),
        "volume": _first_str(ji.get("volume") or r.get("volume")),
        "issue": _first_str(ji.get("issue") or r.get("issue")),
        "pages": _first_str(r.get("pageInfo") or r.get("pages")),
        "doi": norm_doi(r.get("doi")),
        "pmid": _first_str(r.get("pmid")),
        "pmcid": _first_str(r.get("pmcid") or r.get("pmcId")),
        "issn": _norm_issn(ji.get("issn") or r.get("issn")),
        "doc_type": _first_str(r.get("pubType") or r.get("source")),
        "abstract": _strip_xml(r.get("abstractText")),
        "publisher": _first_str(r.get("publisher")) or _first_str(ji.get("publisher")),
    }


def _normalize_crossref(it: dict) -> Dict[str, Any]:
    issn = it.get("ISSN") or it.get("issn")
    if not issn and isinstance(it.get("issn-type"), list):
        issn = [x.get("value") for x in it["issn-type"] if isinstance(x, dict)]
    return {
        "title": _first_str(it.get("title")),
        "authors": it.get("author") or [],
        "year": _year_from_crossref(it),
        "journal": _first_str(it.get("container-title")),
        "volume": _first_str(it.get("volume")),
        "issue": _first_str(it.get("issue")),
        "pages": _first_str(it.get("page")),
        "doi": norm_doi(it.get("DOI") or it.get("doi")),
        "pmid": _first_str(it.get("PMID")),
        "pmcid": _first_str(it.get("PMCID")),
        "issn": _norm_issn(issn),
        "doc_type": _first_str(it.get("type")),
        "abstract": _strip_xml(it.get("abstract")),
        "publisher": _first_str(it.get("publisher")),
    }


def _normalize_generic(raw: dict) -> Dict[str, Any]:
    def pick(*keys: str) -> Any:
        for k in keys:
            if k in raw and raw[k] is not None and raw[k] != "":
                return raw[k]
        return None
    authors = pick("authors", "author", "authorList", "creator")
    return {
        "title": _first_str(pick("title", "Title", "article-title", "name")),
        "authors": authors,
        "year": norm_year(pick("year", "pubYear", "publishedYear", "publicationYear", "date")),
        "journal": _first_str(pick("journal", "journal-title", "journalTitle", "publicationTitle", "container-title", "source")),
        "volume": _first_str(pick("volume")),
        "issue": _first_str(pick("issue", "number")),
        "pages": _first_str(pick("pages", "page", "pageInfo")),
        "doi": norm_doi(pick("doi", "DOI")),
        "pmid": _first_str(pick("pmid", "PMID")),
        "pmcid": _first_str(pick("pmcid", "pmcId", "PMCID")),
        "issn": _norm_issn(pick("issn", "ISSN")),
        "doc_type": _first_str(pick("type", "doc_type", "pubType", "publicationType")),
        "abstract": _strip_xml(pick("abstract", "abstractText", "description")),
        "publisher": _first_str(pick("publisher", "publication", "publisherName")),
    }


def normalize_record(raw: Any, db: str) -> Dict[str, Any]:
    """把某库的原始记录归一化为规范字段字典。"""
    if not isinstance(raw, dict):
        raw = {}
    if db == "crossref":
        f = _normalize_crossref(raw)
    elif db == "europepmc":
        f = _normalize_europepmc(raw)
    elif db == "pubmed":
        if raw.get("_id_only"):
            f = {"title": None, "authors": [], "year": None, "journal": None,
                 "volume": None, "issue": None, "pages": None, "doi": None,
                 "pmid": _first_str(raw.get("uid")), "pmcid": None, "issn": [],
                 "doc_type": "id_only", "abstract": None, "publisher": None}
        else:
            f = _normalize_pubmed(raw)
    else:
        f = _normalize_generic(raw)

    authors, first_key = _norm_author_list(f.get("authors"))
    title = _first_str(f.get("title"))
    year = f.get("year")
    doi = f.get("doi")

    rec: Dict[str, Any] = {
        "title": title,
        "authors": authors,
        "first_author": authors[0] if authors else None,
        "year": year,
        "journal": _first_str(f.get("journal")),
        "volume": _first_str(f.get("volume")),
        "issue": _first_str(f.get("issue")),
        "pages": _first_str(f.get("pages")),
        "doi": doi,
        "pmid": _first_str(f.get("pmid")),
        "pmcid": _first_str(f.get("pmcid")),
        "issn": f.get("issn") or [],
        "doc_type": _first_str(f.get("doc_type")),
        "abstract": f.get("abstract"),
        "publisher": _first_str(f.get("publisher")),
        "identifiers": {},
        "field_sources": {},
        "provenance": [],
        "relevance_hint": None,
        # 内部键（不进入最终候选，仅供去重/合并）
        "_title_key": norm_key(title),
        "_first_author_key": first_key,
        "_year_key": year,
        "_provided": set(),
    }
    rec["identifiers"] = {
        "doi": doi, "pmid": rec["pmid"], "pmcid": rec["pmcid"],
        "issn": rec["issn"] or [],
    }
    for field in CANONICAL_SCALAR_FIELDS:
        if rec.get(field):
            rec["_provided"].add(field)
    for field in CANONICAL_LIST_FIELDS:
        v = rec.get(field)
        if v and (isinstance(v, list) and len(v) > 0):
            rec["_provided"].add(field)
    if rec.get("first_author"):
        rec["_provided"].add("first_author")
    return rec


# --------------------------------------------------------------------------- #
# 去重（确定性：DOI 精确 > 标题+一作+年份 > 标题模糊同一年）
# --------------------------------------------------------------------------- #
class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta = set(w for w in a.split() if w not in _STOPWORDS and len(w) > 1)
    tb = set(w for w in b.split() if w not in _STOPWORDS and len(w) > 1)
    jac = 0.0
    if ta or tb:
        jac = len(ta & tb) / max(1, len(ta | tb))
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jac, seq)


def dedup_records(records: Sequence[Dict[str, Any]]) -> Tuple[List[List[int]], List[str]]:
    """返回 (簇列表[每个簇是原始下标列表], 说明)。"""
    n = len(records)
    uf = _UnionFind(n)
    notes: List[str] = []
    # 第 1 层：DOI 精确
    doi_groups: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        d = r.get("doi")
        if d:
            doi_groups.setdefault(d, []).append(i)
    for idxs in doi_groups.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j)
    # 第 2 层：标题 + 一作 + 年份 精确
    tay_groups: Dict[Tuple[str, str, str], List[int]] = {}
    for i, r in enumerate(records):
        key = (r.get("_title_key") or "", r.get("_first_author_key") or "", r.get("_year_key") or "")
        if key[0]:
            tay_groups.setdefault(key, []).append(i)
    for idxs in tay_groups.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j)
    # 第 3 层：标题模糊（同一年）
    year_buckets: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        y = r.get("_year_key")
        if y:
            year_buckets.setdefault(y, []).append(i)
    pair_cap = 200000
    compared = 0
    for idxs in year_buckets.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                compared += 1
                if compared > pair_cap:
                    notes.append("fuzzy_title_pair_cap_hit: 超出 %d 次比对，剩余模糊对未比较" % pair_cap)
                    break
                ia, ib = idxs[a], idxs[b]
                if uf.find(ia) == uf.find(ib):
                    continue
                if _title_similarity(records[ia]["_title_key"], records[ib]["_title_key"]) >= 0.90:
                    uf.union(ia, ib)
            if compared > pair_cap:
                break
        if compared > pair_cap:
            break
    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        clusters.setdefault(uf.find(i), []).append(i)
    # 确定性排序：按簇内最小原始下标排簇，簇内按下标升序
    result = sorted((sorted(members) for members in clusters.values()),
                    key=lambda m: m[0])
    return result, notes


# --------------------------------------------------------------------------- #
# 合并（字段优先级 + 冲突消解）
# --------------------------------------------------------------------------- #
def _field_priority_order(field: str, db: str) -> int:
    pref = FIELD_PREFERRED_DB.get(field, [])
    if field in pref and db in pref:
        # 标识符字段专用序列内的相对顺序
        return 1000 - pref.index(db)
    return DB_PRIORITY.get(db, 0)


def _norm_field_value(field: str, value: Any) -> Optional[Any]:
    if value is None:
        return None
    if field == "doi":
        return norm_doi(value)
    if field in ("year",):
        return norm_year(value)
    if field in ("title", "journal"):
        return norm_key(value)
    if field in ("volume", "issue", "pages", "doc_type", "publisher"):
        return _strip(value).lower() if _strip(value) else None
    if field == "authors":
        return tuple(sorted(norm_key(x) for x in value if x))
    if field == "issn":
        return tuple(sorted(norm_key(x) for x in value))
    return _strip(value)


def merge_cluster(members: List[Dict[str, Any]], cluster_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """合并一个去重簇；返回 (合并记录, 冲突列表)。"""
    merged: Dict[str, Any] = {
        "record_id": "rec:%d" % cluster_id,
        "title": None, "authors": [], "first_author": None, "year": None,
        "journal": None, "volume": None, "issue": None, "pages": None,
        "doi": None, "pmid": None, "pmcid": None, "issn": [], "doc_type": None,
        "abstract": None, "publisher": None, "identifiers": {},
        "field_sources": {}, "provenance": [], "relevance_hint": None,
        "dedup_cluster_id": cluster_id, "source_count": len(members),
    }
    conflicts: List[Dict[str, Any]] = []

    for field in CANONICAL_SCALAR_FIELDS:
        # 按字段优先级排序的非空取值
        cands = []
        for m in members:
            v = m.get(field)
            if field not in m.get("_provided", set()) or not v:
                continue
            cands.append({
                "value": v,
                "norm": _norm_field_value(field, v),
                "db": m.get("_db"),
                "source_file": m.get("_source_file"),
                "locator": m.get("_locator"),
            })
        if not cands:
            continue
        cands.sort(key=lambda c: _field_priority_order(field, c["db"]), reverse=True)
        merged[field] = cands[0]["value"]
        # 字段来源：本簇中所有提供该字段的来源
        sources = []
        for c in cands:
            sources.append({"db": c["db"], "source_file": c["source_file"], "locator": c["locator"]})
        merged["field_sources"][field] = sources
        # 冲突检测：不同库给出不同的非空归一值
        distinct = []
        for c in cands:
            if c["norm"] is not None and all(c["norm"] != d["norm"] for d in distinct):
                distinct.append(c)
        if len(distinct) > 1:
            conflicts.append({
                "field": field,
                "record_id": merged["record_id"],
                "values": [{"value": d["value"], "db": d["db"], "source_file": d["source_file"],
                            "locator": d["locator"]} for d in distinct],
                "resolution": "priority_source",
                "chosen": merged[field],
                "chosen_db": cands[0]["db"],
            })

    for field in CANONICAL_LIST_FIELDS:
        cands = []
        for m in members:
            v = m.get(field)
            if field in m.get("_provided", set()) and v:
                cands.append((_field_priority_order(field, m.get("_db")), m))
        if not cands:
            continue
        cands.sort(key=lambda x: x[0], reverse=True)
        chosen = cands[0][1].get(field) or []
        merged[field] = list(chosen)
        merged["field_sources"][field] = [
            {"db": m.get("_db"), "source_file": m.get("_source_file"), "locator": m.get("_locator")}
            for _, m in cands
        ]

    merged["first_author"] = merged["authors"][0] if merged["authors"] else None
    merged["identifiers"] = {
        "doi": merged["doi"], "pmid": merged["pmid"], "pmcid": merged["pmcid"],
        "issn": merged["issn"] or [],
    }
    # provenance：库—查询—原始响应—映射—去重—合并 可回溯
    for m in sorted(members, key=lambda x: (DB_PRIORITY.get(x.get("_db"), 0), x.get("_idx", 0)), reverse=True):
        merged["provenance"].append({
            "db": m.get("_db"),
            "source_file": m.get("_source_file"),
            "locator": m.get("_locator"),
            "query": m.get("_request"),
            "raw_snapshot": m.get("_raw_snapshot") or {},
        })
    return merged, conflicts


# --------------------------------------------------------------------------- #
# 覆盖 / 证据 / 相关性
# --------------------------------------------------------------------------- #
def build_coverage(sources: List[Dict[str, Any]], merged: List[Dict[str, Any]],
                   total_raw: int, merged_count: int) -> Dict[str, Any]:
    dbs = sorted({s.get("db") for s in sources if s.get("db")}, key=lambda x: DB_PRIORITY.get(x, 0), reverse=True)
    per_db = {}
    for s in sources:
        db = s.get("db")
        if not db:
            continue
        per_db[db] = {
            "source_file": s.get("path"),
            "input_records": s.get("record_count", 0),
            "fields_missing": [],
        }
    # 系统性缺失字段：某库的所有记录都缺的规范字段
    # 由 main 传入 per-source 的 _field_missing_sets
    single_db = 0
    for m in merged:
        prov = m.get("provenance") or []
        dbs_seen = {p.get("db") for p in prov if p.get("db")}
        if len(dbs_seen) == 1:
            single_db += 1
    return {
        "databases": dbs,
        "total_input_records": total_raw,
        "merged_record_count": merged_count,
        "duplicates_merged": max(0, total_raw - merged_count),
        "records_in_single_db_only": single_db,
        "systematically_missing_fields": per_db,
    }


def relevance_hint(record: Dict[str, Any], question: Optional[str], queries: Sequence[str]) -> str:
    terms = set()
    for q in [question] + list(queries or []):
        if not q:
            continue
        for tok in re.findall(r"[a-zA-Z\u4e00-\u9fff]{3,}", q.lower()):
            if tok not in _STOPWORDS:
                terms.add(tok)
    text = ("%s %s" % (record.get("title") or "", record.get("abstract") or "")).lower()
    if not terms:
        return "unknown"
    hits = sum(1 for t in terms if t in text)
    ratio = hits / len(terms)
    if ratio >= 0.25:
        return "high"
    if ratio >= 0.10:
        return "medium"
    return "low"


def build_evidence(merged: List[Dict[str, Any]], conflicts: List[Dict[str, Any]],
                   queries_by_file: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    ev: List[Dict[str, Any]] = []
    for m in merged:
        for p in m.get("provenance") or []:
            snap = p.get("raw_snapshot") or {}
            quote = snap.get("title") or snap.get("doi") or (json.dumps(snap, ensure_ascii=False)[:300])
            ev.append({
                "evidence_id": "ev:record:%s:%s" % (m["record_id"], p.get("locator") or "?loc"),
                "type": "record_origin",
                "db": p.get("db"),
                "source_file": p.get("source_file"),
                "locator": p.get("locator"),
                "quote": quote,
                "query": p.get("query"),
            })
    for c in conflicts:
        ev.append({
            "evidence_id": "ev:conflict:%s:%s" % (c["record_id"], c["field"]),
            "type": "source_conflict",
            "db": None,
            "source_file": None,
            "locator": None,
            "quote": json.dumps(c["values"], ensure_ascii=False)[:800],
            "detail": c,
        })
    for src, q in sorted(queries_by_file.items()):
        if q:
            ev.append({
                "evidence_id": "ev:query:%s" % src,
                "type": "query",
                "db": None,
                "source_file": src,
                "locator": None,
                "quote": q,
            })
    return ev


# --------------------------------------------------------------------------- #
# 中间表示：plan.json / record_model.json
# --------------------------------------------------------------------------- #
def build_plan(input_dir: Path, sources: List[Dict[str, Any]], case_id: str,
               question: Optional[str], queries: List[str], degradations: List[str],
               dedup_notes: List[str]) -> Dict[str, Any]:
    return {
        "schema_version": 2,
        "case_id": case_id,
        "generated_at": iso_utc_now(),
        "input_dir": str(input_dir),
        "input_manifest": sources,
        "research_question": question,
        "queries": queries,
        "rules": {
            "field_mapping": "references/field-mapping.md",
            "dedup_priority": ["doi_exact", "title_first_author_year_exact", "title_fuzzy_same_year(>=0.90)"],
            "merge_resolution": "per_field_priority_source + explicit_conflict_list",
            "db_priority": {k: v for k, v in DB_PRIORITY.items()},
            "field_preferred_db": FIELD_PREFERRED_DB,
        },
        "coverage_targets": ["databases", "fields_missing_per_db", "records_in_single_db_only"],
        "degradations": degradations,
        "dedup_notes": dedup_notes,
    }


def build_record_model(merged: List[Dict[str, Any]], clusters: List[List[int]],
                       raw_records: Sequence[Dict[str, Any]],
                       conflicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """记录中间表示：归一化记录 + 逐字段来源 + 去重簇 + 合并决策。"""
    normalized = []
    for i, r in enumerate(raw_records):
        normalized.append({
            "index": i,
            "db": r.get("_db"),
            "source_file": r.get("_source_file"),
            "locator": r.get("_locator"),
            "title": r.get("title"),
            "authors": r.get("authors"),
            "year": r.get("year"),
            "doi": r.get("doi"),
            "pmid": r.get("pmid"),
            "pmcid": r.get("pmcid"),
            "provided_fields": sorted(r.get("_provided", set())),
            "title_key": r.get("_title_key"),
        })
    cluster_list = []
    for cid, members in enumerate(clusters):
        cluster_list.append({
            "cluster_id": cid,
            "record_id": "rec:%d" % cid,
            "member_indices": members,
            "dedup_basis": _dedup_basis(raw_records, members),
        })
    return {
        "schema_version": 2,
        "generated_at": iso_utc_now(),
        "normalized_records": normalized,
        "dedup_clusters": cluster_list,
        "merged_records": merged,
        "conflicts": conflicts,
    }


def _dedup_basis(raw_records: Sequence[Dict[str, Any]], members: List[int]) -> str:
    if len(members) <= 1:
        return "singleton"
    dois = {raw_records[i].get("doi") for i in members if raw_records[i].get("doi")}
    if len(dois) > 0:
        return "doi_exact"
    keys = {(raw_records[i].get("_title_key"), raw_records[i].get("_first_author_key"),
             raw_records[i].get("_year_key")) for i in members}
    if len(keys) == 1:
        return "title_first_author_year_exact"
    return "title_fuzzy_same_year"


# --------------------------------------------------------------------------- #
# 排序（确定性基线，模型可在 decisions 里覆盖）
# --------------------------------------------------------------------------- #
_RELEVANCE_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def baseline_ranking(merged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(merged, key=lambda m: (
        _RELEVANCE_RANK.get(m.get("relevance_hint") or "unknown", 3),
        -(int(m["year"]) if str(m.get("year") or "").isdigit() else 0),
        (m.get("title") or "").lower(),
    ))
    return [
        {
            "record_id": m["record_id"],
            "rank": i + 1,
            "relevance": m.get("relevance_hint") or "unknown",
            "rationale": "deterministic baseline: relevance_hint term hits, then year desc, then title asc",
        }
        for i, m in enumerate(ordered)
    ]
