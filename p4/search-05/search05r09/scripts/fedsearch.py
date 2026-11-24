#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fedsearch.py -- 多数据库联合检索核心库（仅标准库，确定性，无网络）。

职责：发现输入 -> 类型识别 -> 数据库响应解析 -> 跨库字段归一 -> 去重 ->
合并 -> 覆盖比较 -> 证据记录 -> 相关性提示。被 main.py / selftest.py 复用。

设计约定：
  * 不依赖第三方包，不联网，不读用户环境变量之外的文件。
  * 全部排序确定性（sorted / 固定顺序），同输入重复运行结果一致。
  * 缺失值用 None/[] 表示，不编造；未知数据库标记 "unknown"。
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "1.1.0"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

MAX_FILE_BYTES = 2 * 1024 * 1024

# 结果契约的固定字段；若 case.json 提供 output_contract 则在其上并集。
DEFAULT_CONTRACT = [
    "case_id", "research_question", "search_scope", "candidates", "ranking",
    "evidence_records", "coverage", "empty_results", "conflicts",
    "missing_metadata", "permission_issues", "limitations", "status",
    "sources", "uncertainty", "change_log", "errors", "degradations",
    "pending_human", "runtime",
]

CANONICAL_FIELDS = [
    "title", "authors", "year", "journal", "volume", "issue", "pages",
    "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher",
]

DB_PRIORITY = {"crossref": 0, "europepmc": 1, "pubmed": 2, "publisher": 3,
               "pubmed_esearch": 3, "unknown": 4}

_SKIP_NAMES = {"case.json", "task.md", "result.json"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _as_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    if isinstance(v, (int, float)):
        return str(v)
    return None


def _first_nonempty(vals: Iterable[Any]) -> Optional[str]:
    for v in vals:
        s = _as_str(v)
        if s:
            return s
    return None


def _norm(s: Any) -> str:
    """标题/文本归一：小写、去变音符、非字母数字转空格、压缩空白。"""
    if s is None:
        return ""
    t = str(s)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm_doi(v: Any) -> Optional[str]:
    s = _as_str(v)
    if not s:
        return None
    s = s.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.strip()
    return s or None


def _extract_year(v: Any) -> Optional[str]:
    """从任意日期值提取 4 位年份；找不到返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        s = str(int(v))
        m = re.search(r"(19|20)\d{2}", s)
        return m.group(0) if m else s if re.fullmatch(r"\d{4}", s) else None
    m = re.search(r"(19|20)\d{2}", str(v))
    return m.group(0) if m else None


def _author_last(author: str) -> str:
    a = author.strip()
    if not a:
        return ""
    if "," in a:
        a = a.split(",", 1)[0].strip()
    parts = [p for p in re.split(r"\s+", a) if p]
    return _norm(parts[-1]) if parts else _norm(a)


def _sorted_uniq(items: Iterable[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in sorted(items):
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _json_dump_ascii(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# 输入发现与读取
# ---------------------------------------------------------------------------

def discover_input_files(input_dir: Path) -> List[Path]:
    if not input_dir or not input_dir.is_dir():
        return []
    out = []
    for p in sorted(input_dir.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            rel = p.relative_to(input_dir).as_posix()
            if not any(part in ("__pycache__",) or part.startswith(".") for part in rel.split("/")):
                out.append(p)
    return out


def classify_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".pdf":
        return "pdf"
    if ext in (".txt", ".md", ".csv", ".tsv", ".ris", ".bib", ".xml", ".html", ".htm"):
        return "text"
    return "other"


def load_json(path: Path) -> Tuple[Optional[Any], List[str]]:
    errs: List[str] = []
    if path.stat().st_size > MAX_FILE_BYTES:
        return None, ["file_exceeds_limit: %s" % path.name]
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except Exception:
            continue
    if raw is None:
        try:
            raw = path.read_bytes().decode("utf-8", errors="replace")
        except Exception:
            return None, ["unreadable: %s" % path.name]
    try:
        return json.loads(raw), errs
    except Exception as e:
        return None, errs + ["json_parse_error: %s (%s)" % (path.name, e)]


def read_text(path: Path) -> Tuple[str, List[str]]:
    deg: List[str] = []
    if path.stat().st_size > MAX_FILE_BYTES:
        return "", ["file_exceeds_limit: %s" % path.name]
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace"), deg
    except Exception as e:
        deg.append("unreadable_text: %s (%s)" % (path.name, e))
        return "", deg


def parse_pdf_text(path: Path) -> Tuple[str, List[str]]:
    deg: List[str] = []
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, timeout=120,
        )
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", errors="replace"), deg
        deg.append("pdftotext_failed(%d): %s" % (proc.returncode, path.name))
        return "", deg
    except FileNotFoundError:
        deg.append("pdftotext_missing: %s (PDF 仅列路径)" % path.name)
        return "", deg
    except Exception as e:
        deg.append("pdf_parse_error: %s (%s)" % (path.name, e))
        return "", deg


# ---------------------------------------------------------------------------
# 响应类型识别与记录抽取
# ---------------------------------------------------------------------------

def classify_response(obj: Any) -> str:
    if not isinstance(obj, dict):
        return "unknown"
    msg = obj.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("items"), list):
        return "crossref"
    rl = obj.get("resultList")
    if isinstance(rl, dict) and isinstance(rl.get("result"), list):
        return "europepmc"
    res = obj.get("result")
    if isinstance(res, dict) and res and all(
        isinstance(v, dict) for v in res.values()
    ) and any(
        isinstance(v, dict) and ("title" in v or "articleids" in v or "authors" in v)
        for v in res.values()
    ):
        return "pubmed"
    if isinstance(obj.get("esearchresult"), dict) and isinstance(
        obj.get("esearchresult", {}).get("idlist"), list
    ):
        return "pubmed_esearch"
    for key in ("items", "records", "results", "documents"):
        if isinstance(obj.get(key), list):
            return "publisher"
    # 递归兜底：查找带文献特征的 list[dict]
    found = _find_biblio_lists(obj, "$")
    return "publisher" if found else "unknown"


_JSON_KEYS = ("title", "doi", "DOI", "pmid", "journal", "container-title",
              "articleTitle", "fulljournalname", "source")


def _looks_biblio(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    keys = set(d.keys())
    return bool(keys & set(_JSON_KEYS)) and (
        any(isinstance(d.get(k), (str, list)) and d.get(k) for k in
            ("title", "DOI", "doi", "articleTitle"))
        or any(d.get(k) for k in ("pmid", "uid"))
    )


def _find_biblio_lists(node: Any, path: str, out: List[Tuple[str, List[dict]]]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _find_biblio_lists(v, "%s.%s" % (path, k), out)
    elif isinstance(node, list):
        if node and all(_looks_biblio(x) for x in node):
            out.append((path, node))
        else:
            for i, v in enumerate(node):
                _find_biblio_lists(v, "%s[%d]" % (path, i), out)


def extract_records(obj: Any) -> List[Tuple[dict, str]]:
    db = classify_response(obj)
    recs: List[Tuple[dict, str]] = []
    if db == "crossref":
        items = obj.get("message", {}).get("items", [])
        for i, it in enumerate(items):
            if isinstance(it, dict):
                recs.append((it, "message.items[%d]" % i))
    elif db == "europepmc":
        for i, it in enumerate(obj.get("resultList", {}).get("result", [])):
            if isinstance(it, dict):
                recs.append((it, "resultList.result[%d]" % i))
    elif db == "pubmed":
        for uid in sorted(obj.get("result", {}).keys()):
            it = obj["result"][uid]
            if isinstance(it, dict):
                recs.append((it, "result.%s" % uid))
    elif db == "publisher":
        for key in ("items", "records", "results", "documents"):
            lst = obj.get(key)
            if isinstance(lst, list) and lst and all(isinstance(x, dict) for x in lst):
                for i, it in enumerate(lst):
                    recs.append((it, "%s[%d]" % (key, i)))
                break
        if not recs:
            found: List[Tuple[str, List[dict]]] = []
            _find_biblio_lists(obj, "$", found)
            for path, lst in found:
                for i, it in enumerate(lst):
                    recs.append((it, "%s[%d]" % (path, i)))
    return recs


def extract_request(obj: Any) -> Optional[str]:
    """尽力从响应体里找回查询式（无则 None，绝不编造）。"""
    if not isinstance(obj, dict):
        return None
    for key in ("query", "queryString", "query_string", "search_terms", "term", "q"):
        if obj.get(key):
            return _as_str(obj[key])
    rl = obj.get("resultList")
    if isinstance(rl, dict):
        req = rl.get("request")
        if isinstance(req, dict) and req.get("queryString"):
            return _as_str(req["queryString"])
    msg = obj.get("message")
    if isinstance(msg, dict):
        mq = msg.get("query")
        if isinstance(mq, str) and mq.strip():
            return mq.strip()
        if isinstance(mq, dict):
            for k in ("search-terms", "query", "terms", "q", "queryString"):
                if mq.get(k):
                    return _as_str(mq[k])
    return None


# ---------------------------------------------------------------------------
# 字段归一（跨库 -> 统一 schema）
# ---------------------------------------------------------------------------

def _authors_from_raw(raw: dict, db: str) -> List[str]:
    if db == "crossref":
        out = []
        for a in raw.get("author") or []:
            if isinstance(a, dict):
                name = " ".join(filter(None, [a.get("given"), a.get("family")])) \
                    or a.get("name") or ""
                if name.strip():
                    out.append(name.strip())
            elif isinstance(a, str) and a.strip():
                out.append(a.strip())
        return out
    if db == "europepmc":
        if raw.get("authorString"):
            return [x.strip() for x in re.split(r"[;,]| and ", str(raw["authorString"])) if x.strip()]
        out = []
        for a in (raw.get("authorList", {}).get("author") or []):
            if isinstance(a, dict):
                name = " ".join(filter(None, [
                    a.get("firstName"), a.get("initials"), a.get("lastName")]))
                if name.strip():
                    out.append(name.strip())
        return out
    if db == "pubmed":
        out = []
        for a in raw.get("authors") or []:
            if isinstance(a, dict) and a.get("name"):
                out.append(str(a["name"]).strip())
        return out
    # publisher / unknown：常见键
    for key in ("authors", "author", "creators", "creator", "contributors", "byline"):
        v = raw.get(key)
        if isinstance(v, list):
            out = []
            for a in v:
                if isinstance(a, dict):
                    name = " ".join(filter(None, [
                        a.get("given_name"), a.get("given"), a.get("first_name"),
                        a.get("family_name"), a.get("family"), a.get("last_name"),
                    ])) or a.get("name") or a.get("full_name") or ""
                    if name.strip():
                        out.append(name.strip())
                elif isinstance(a, str) and a.strip():
                    out.append(a.strip())
            if out:
                return out
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in re.split(r"[;,]| and ", v) if x.strip()]
    return []


def _pick(raw: dict, keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        v = raw.get(k)
        if isinstance(v, list):
            s = _first_nonempty(v)
            if s:
                return s
        else:
            s = _as_str(v)
            if s:
                return s
    return None


def normalize_record(raw: dict, db: str) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "title": None, "authors": [], "year": None, "journal": None,
        "volume": None, "issue": None, "pages": None, "doi": None,
        "pmid": None, "pmcid": None, "issn": None, "doc_type": None,
        "abstract": None, "publisher": None, "identifiers": {},
    }
    if db == "crossref":
        rec["title"] = _first_nonempty(raw.get("title") or [])
        rec["authors"] = _authors_from_raw(raw, db)
        y = raw.get("issued", {}).get("date-parts", [[None]])[0][0] if isinstance(raw.get("issued"), dict) else None
        rec["year"] = _extract_year(y)
        rec["journal"] = _first_nonempty(raw.get("container-title") or [])
        rec["volume"] = _as_str(raw.get("volume"))
        rec["issue"] = _as_str(raw.get("issue"))
        rec["pages"] = _as_str(raw.get("page"))
        rec["doi"] = _norm_doi(raw.get("DOI"))
        rec["issn"] = _as_str((raw.get("ISSN") or [None])[0] if isinstance(raw.get("ISSN"), list) else raw.get("ISSN"))
        rec["doc_type"] = _as_str(raw.get("type"))
        rec["abstract"] = _as_str(raw.get("abstract"))
        rec["publisher"] = _as_str(raw.get("publisher"))
    elif db == "europepmc":
        rec["title"] = _as_str(raw.get("title"))
        rec["authors"] = _authors_from_raw(raw, db)
        rec["year"] = _extract_year(raw.get("pubYear"))
        ji = raw.get("journalInfo") if isinstance(raw.get("journalInfo"), dict) else {}
        jt = raw.get("journalTitle")
        if not jt and ji.get("journal") and isinstance(ji["journal"], dict):
            jt = ji["journal"].get("title")
        rec["journal"] = _as_str(jt)
        rec["volume"] = _as_str(raw.get("journalVolume") or (ji.get("volume") if isinstance(ji, dict) else None))
        rec["issue"] = _as_str(raw.get("issue") or (ji.get("issue") if isinstance(ji, dict) else None))
        rec["pages"] = _as_str(raw.get("pageInfo"))
        rec["doi"] = _norm_doi(raw.get("doi"))
        rec["pmid"] = _as_str(raw.get("pmid"))
        rec["pmcid"] = _as_str(raw.get("pmcid"))
        j = ji.get("journal") if isinstance(ji, dict) else None
        if isinstance(j, dict):
            rec["issn"] = _as_str(j.get("issn"))
        rec["doc_type"] = _as_str((raw.get("pubTypeList", {}).get("pubType") or [None])[0] if isinstance(raw.get("pubTypeList"), dict) else raw.get("pubType"))
        rec["abstract"] = _as_str(raw.get("abstractText"))
        rec["publisher"] = _as_str(raw.get("publisher"))
        if raw.get("source"):
            rec["identifiers"]["source"] = str(raw["source"])
    elif db == "pubmed":
        rec["title"] = _as_str(raw.get("title"))
        rec["authors"] = _authors_from_raw(raw, db)
        rec["year"] = _extract_year(raw.get("pubdate") or raw.get("epubdate"))
        rec["journal"] = _as_str(raw.get("fulljournalname") or raw.get("source"))
        rec["volume"] = _as_str(raw.get("volume"))
        rec["issue"] = _as_str(raw.get("issue"))
        rec["pages"] = _as_str(raw.get("pages"))
        rec["pmid"] = _as_str(raw.get("uid"))
        for a in raw.get("articleids") or []:
            if isinstance(a, dict):
                idt = str(a.get("idtype", "")).lower()
                val = _as_str(a.get("value"))
                if idt == "doi":
                    rec["doi"] = _norm_doi(val)
                elif idt == "pmcid":
                    rec["pmcid"] = _as_str(val)
                elif idt == "pmid":
                    rec["pmid"] = rec["pmid"] or _as_str(val)
        rec["issn"] = _as_str(raw.get("issn"))
        rec["doc_type"] = _as_str((raw.get("pubtype") or [None])[0] if isinstance(raw.get("pubtype"), list) else raw.get("pubtype"))
    else:
        # publisher / unknown 通用映射
        rec["title"] = _pick(raw, ("title", "Title", "articleTitle", "name", "headline",
                                   "title_display", "display_name"))
        rec["authors"] = _authors_from_raw(raw, db)
        rec["year"] = _extract_year(_pick(raw, ("year", "pubYear", "publicationYear",
                                                "published", "publication_date",
                                                "coverDate", "date")))
        rec["journal"] = _pick(raw, ("journal", "journalTitle", "container-title",
                                     "publicationName", "source", "source_title",
                                     "venue"))
        rec["volume"] = _as_str(_pick(raw, ("volume", "journalVolume")))
        rec["issue"] = _as_str(_pick(raw, ("issue", "number")))
        rec["pages"] = _as_str(_pick(raw, ("pages", "pageInfo", "page_range", "page")))
        rec["doi"] = _norm_doi(_pick(raw, ("doi", "DOI", "prism:doi", "pii", "id")))
        rec["pmid"] = _as_str(_pick(raw, ("pmid", "PMID")))
        rec["pmcid"] = _as_str(_pick(raw, ("pmcid", "PMCID")))
        rec["issn"] = _as_str(_pick(raw, ("issn", "ISSN", "prism:issn")))
        rec["doc_type"] = _as_str(_pick(raw, ("type", "pubType", "docType",
                                              "article_type", "contentType")))
        rec["abstract"] = _as_str(_pick(raw, ("abstract", "Abstract", "description",
                                              "summary", "abstractText")))
        rec["publisher"] = _as_str(_pick(raw, ("publisher", "Publisher", "publisher_name")))
    rec["identifiers"]["db"] = db
    return rec


# ---------------------------------------------------------------------------
# 去重（union-find）
# ---------------------------------------------------------------------------

def _first_author_last(authors: List[str]) -> str:
    return _author_last(authors[0]) if authors else ""


def dedup(records: List[Dict[str, Any]]) -> List[List[int]]:
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    doi_map: Dict[str, int] = {}
    pmid_map: Dict[str, int] = {}
    pmcid_map: Dict[str, int] = {}
    for i, r in enumerate(records):
        if r.get("doi"):
            if r["doi"] in doi_map:
                union(i, doi_map[r["doi"]])
            else:
                doi_map[r["doi"]] = i
        for key, m in (("pmid", pmid_map), ("pmcid", pmcid_map)):
            if r.get(key):
                if r[key] in m:
                    union(i, m[r[key]])
                else:
                    m[r[key]] = i

    sig_map: Dict[tuple, int] = {}
    for i, r in enumerate(records):
        t = _norm(r.get("title"))
        fa = _first_author_last(r.get("authors"))
        y = r.get("year")
        if t and fa and y:
            sig = (t, fa, y)
            if sig in sig_map:
                union(i, sig_map[sig])
            else:
                sig_map[sig] = i

    by_year: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        y = r.get("year")
        if y and _norm(r.get("title")):
            by_year.setdefault(str(y), []).append(i)
    for y, idxs in by_year.items():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                ia, ib = idxs[a], idxs[b]
                if find(ia) == find(ib):
                    continue
                ta = _norm(records[ia].get("title"))
                tb = _norm(records[ib].get("title"))
                if ta and tb and difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.90:
                    union(ia, ib)

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return [sorted(v) for v in clusters.values()]


def _dedup_method(records: List[Dict[str, Any]], idxs: List[int]) -> str:
    if len(idxs) <= 1:
        return "singleton"
    dois = {records[i].get("doi") for i in idxs if records[i].get("doi")}
    if len(dois) == 1:
        return "doi_exact"
    pmids = {records[i].get("pmid") for i in idxs if records[i].get("pmid")}
    pmcids = {records[i].get("pmcid") for i in idxs if records[i].get("pmcid")}
    if len(pmids) == 1 or len(pmcids) == 1:
        return "identifier_exact"
    sigs = {
        (_norm(records[i].get("title")), _first_author_last(records[i].get("authors")), records[i].get("year"))
        for i in idxs
    }
    if len(sigs) == 1:
        return "title_author_year"
    return "fuzzy_title"


# ---------------------------------------------------------------------------
# 合并与冲突消解
# ---------------------------------------------------------------------------

def _field_priority(db: str) -> int:
    return DB_PRIORITY.get(db, 9)


def _raw_snapshot(raw: dict, db: str, file: str, locator: str) -> Dict[str, Any]:
    fields = {}
    for k in ("title", "DOI", "doi", "pmid", "pmcid", "pubYear", "pubdate",
              "container-title", "journalTitle", "fulljournalname", "authorString",
              "authors", "author", "pageInfo", "pages", "volume", "issue"):
        if raw.get(k) is not None:
            fields[k] = raw[k]
    return {"db": db, "source_file": file, "locator": locator,
            "verbatim_fields": fields}


def merge_cluster(records: List[Dict[str, Any]], idxs: List[int]) -> Dict[str, Any]:
    ordered = sorted(idxs, key=lambda i: (_field_priority(records[i].get("_db")), i))
    members = [records[i] for i in ordered]

    merged: Dict[str, Any] = {
        "title": None, "authors": [], "year": None, "journal": None,
        "volume": None, "issue": None, "pages": None, "doi": None,
        "pmid": None, "pmcid": None, "issn": None, "doc_type": None,
        "abstract": None, "publisher": None, "identifiers": {},
    }
    field_sources: Dict[str, List[Dict[str, Any]]] = {}
    conflicts: List[Dict[str, Any]] = []

    for f in CANONICAL_FIELDS:
        vals: List[Dict[str, Any]] = []
        for m in members:
            v = m.get(f)
            if v not in (None, "", []):
                vals.append({"db": m.get("_db"), "source_file": m.get("_source_file"),
                             "locator": m.get("_locator"), "value": v})
        if not vals:
            continue
        field_sources[f] = vals
        if f in ("authors",):
            best = max(vals, key=lambda x: len(x["value"]))
        elif f in ("title", "abstract"):
            best = max(vals, key=lambda x: len(str(x["value"])))
        elif f in ("doi", "pmid", "pmcid", "year"):
            uniq = {str(x["value"]).strip().lower() for x in vals}
            if len(uniq) > 1:
                conflicts.append({
                    "field": f, "values": vals,
                    "resolution": "kept value from highest-priority source",
                })
            best = vals[0]
        else:
            best = vals[0]
        merged[f] = best["value"]
        merged.setdefault("_field_winner", {})[f] = best

    ids: Dict[str, List[str]] = {}
    for m in members:
        for k in m.get("identifiers", {}):
            if k != "db":
                ids.setdefault(k, [])
                if m["identifiers"][k] not in ids[k]:
                    ids[k].append(m["identifiers"][k])
    merged["identifiers"] = {k: _sorted_uniq(v) for k, v in ids.items()}

    contrib = []
    for m in members:
        contrib.append({
            "db": m.get("_db"), "source_file": m.get("_source_file"),
            "locator": m.get("_locator"), "query": m.get("_request"),
        })
    snapshots = []
    for m in members:
        snapshots.append(_raw_snapshot(m.get("_raw") or {}, m.get("_db"),
                                       m.get("_source_file"), m.get("_locator")))

    method = _dedup_method(records, idxs)
    merged["_provenance"] = {
        "contributing_records": contrib,
        "dedup": {"method": method, "member_indices": sorted(idxs)},
        "merge": {"primary_index": ordered[0], "rule": "field_priority_by_db"},
    }
    merged["_raw_snapshots"] = snapshots
    merged["_conflicts"] = conflicts
    merged["_field_sources"] = field_sources
    return merged


def finalize_candidate(m: Dict[str, Any], idx: int, query_terms: List[str]) -> Dict[str, Any]:
    rec_id = "rec:%04d" % idx
    hint = relevance_hint(m, query_terms)
    candidate = {
        "record_id": rec_id,
        "title": m["title"], "authors": m["authors"], "year": m["year"],
        "journal": m["journal"], "volume": m["volume"], "issue": m["issue"],
        "pages": m["pages"], "doi": m["doi"], "pmid": m["pmid"],
        "pmcid": m["pmcid"], "issn": m["issn"], "doc_type": m["doc_type"],
        "abstract": m["abstract"], "publisher": m["publisher"],
        "identifiers": m["identifiers"],
        "field_sources": {f: [{"db": v["db"], "source_file": v["source_file"],
                               "locator": v["locator"]} for v in vals]
                          for f, vals in m["_field_sources"].items()},
        "provenance": m["_provenance"],
        "raw_snapshot": m["_raw_snapshots"],
        "relevance_hint": hint,
    }
    return candidate


# ---------------------------------------------------------------------------
# 相关性提示与排序
# ---------------------------------------------------------------------------

def _term_text(m: Dict[str, Any]) -> str:
    parts = [m.get("title") or "", m.get("abstract") or "",
             " ".join(m.get("authors") or []), m.get("journal") or ""]
    return " ".join(filter(None, parts))


def relevance_hint(m: Dict[str, Any], query_terms: List[str]) -> Dict[str, Any]:
    text = _norm(_term_text(m))
    hits = [t for t in query_terms if t and t in text]
    score = len(hits)
    level = "high" if score >= 2 else ("medium" if score == 1 else "low")
    return {"level": level, "hits": _sorted_uniq(hits), "score": score,
            "method": "deterministic_term_hit"}


def default_ranking(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(c: Dict[str, Any]) -> tuple:
        rh = c.get("relevance_hint", {})
        score = rh.get("score", 0)
        y = c.get("year")
        year_key = int(y) if str(y).isdigit() else 0
        title = (c.get("title") or "").lower()
        return (-score, -year_key, title)
    ordered = sorted(candidates, key=key)
    out = []
    for rank, c in enumerate(ordered, 1):
        rh = c.get("relevance_hint", {})
        dbs = sorted({x["db"] for x in c["provenance"]["contributing_records"]})
        out.append({
            "record_id": c["record_id"],
            "rank": rank,
            "relevance": rh.get("level", "low"),
            "rationale": "matched_terms=%s; sources=%s; dedup=%s" % (
                ",".join(rh.get("hits", [])) or "none",
                "+".join(dbs), c["provenance"]["dedup"]["method"]),
        })
    return out


# ---------------------------------------------------------------------------
# 覆盖比较与证据
# ---------------------------------------------------------------------------

def build_coverage(sources: List[Dict[str, Any]], all_records: List[Dict[str, Any]],
                   merged: List[Dict[str, Any]]) -> Dict[str, Any]:
    db_records: Dict[str, List[Dict[str, Any]]] = {}
    for r in all_records:
        db_records.setdefault(r["_db"], []).append(r)
    per_db: Dict[str, Dict[str, Any]] = {}
    for db in sorted(db_records):
        recs = db_records[db]
        missing = [f for f in CANONICAL_FIELDS
                   if all(r.get(f) in (None, "", []) for r in recs)]
        field_cov = {f: sum(1 for r in recs if r.get(f) not in (None, "", []))
                     for f in CANONICAL_FIELDS}
        per_db[db] = {"total_records": len(recs), "fields_missing": missing,
                      "field_coverage": field_cov}
    single: Dict[str, List[str]] = {}
    for m in merged:
        dbs = {c["db"] for c in m["_provenance"]["contributing_records"]}
        if len(dbs) == 1:
            db = next(iter(dbs))
            single.setdefault(db, []).append(m["_record_id"])
    databases = sorted(db_records)
    return {
        "databases": databases,
        "total_input_records": len(all_records),
        "merged_record_count": len(merged),
        "duplicates_merged": len(all_records) - len(merged),
        "records_in_single_db_only": single,
        "systematically_missing_fields": per_db,
        "per_database": per_db,
    }


def build_evidence(merged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in merged:
        rid = m["_record_id"]
        for s in m["_raw_snapshots"]:
            title = s["verbatim_fields"].get("title")
            quote = title
            if quote is None:
                quote = _json_dump_ascii(s["verbatim_fields"])
            elif isinstance(quote, list):
                quote = quote[0] if quote else _json_dump_ascii(s["verbatim_fields"])
            out.append({
                "evidence_id": "ev:%s:src:%s" % (rid, s["db"]),
                "type": "source_record",
                "db": s["db"],
                "source_file": s["source_file"],
                "locator": s["locator"],
                "quote": str(quote),
                "claim": "原始文献记录，字段逐字来自该响应",
            })
        if m["_provenance"]["dedup"]["method"] != "singleton":
            titles = []
            for s in m["_raw_snapshots"]:
                t = s["verbatim_fields"].get("title")
                titles.append(str(t[0] if isinstance(t, list) and t else t))
            out.append({
                "evidence_id": "ev:%s:merge" % rid,
                "type": "dedup_merge",
                "db": ",".join(sorted({c["db"] for c in m["_provenance"]["contributing_records"]})),
                "source_file": ",".join(sorted({c["source_file"] for c in m["_provenance"]["contributing_records"]})),
                "locator": ",".join(sorted({c["locator"] for c in m["_provenance"]["contributing_records"]})),
                "quote": " | ".join(titles),
                "claim": "按规则 %s 归并为一条记录" % m["_provenance"]["dedup"]["method"],
            })
    return out


# ---------------------------------------------------------------------------
# 查询词提取
# ---------------------------------------------------------------------------

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "with",
         "by", "is", "are", "be", "was", "were", "what", "which", "how", "does",
         "effect", "effects", "between", "from", "using", "about", "into", "their",
         "its", "that", "this", "these", "those", "study", "research", "based"}


def derive_query_terms(queries: List[str], question: Optional[str]) -> List[str]:
    text = " ".join([q or "" for q in queries] + [question or ""])
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", text.lower())
    terms = []
    for w in words:
        if w not in _STOP and w not in terms:
            terms.append(w)
    return sorted(terms)


# ---------------------------------------------------------------------------
# case.json 解析
# ---------------------------------------------------------------------------

def load_case(input_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    case_path = input_dir / "case.json" if input_dir else None
    errs: List[str] = []
    if case_path is None or not case_path.is_file():
        return {"case_id": "unknown", "present": False}, ["missing: case.json"]
    obj, e = load_json(case_path)
    errs.extend(e)
    if obj is None or not isinstance(obj, dict):
        return {"case_id": "unknown", "present": True}, errs + ["case.json 不可解析"]
    cid = _as_str(obj.get("case_id") or obj.get("id") or obj.get("caseId")) or "unknown"
    question = (_as_str(obj.get("research_question") or obj.get("question")
                        or obj.get("task") or obj.get("objective"))
                or _as_str(obj.get("query")))
    queries = []
    for k in ("queries", "keywords", "search_terms", "terms"):
        v = obj.get(k)
        if isinstance(v, list):
            queries.extend(_as_str(x) for x in v if _as_str(x))
        elif _as_str(v):
            queries.append(_as_str(v))
    queries = _sorted_uniq(queries)
    dbs = []
    for k in ("databases", "sources", "dbs", "search_scope"):
        v = obj.get(k)
        if isinstance(v, list):
            dbs.extend(_as_str(x) for x in v if _as_str(x))
        elif isinstance(v, dict):
            for kk in ("databases", "sources", "include", "targets"):
                if isinstance(v.get(kk), list):
                    dbs.extend(_as_str(x) for x in v[kk] if _as_str(x))
        elif _as_str(v):
            dbs.append(_as_str(v))
    dbs = _sorted_uniq(dbs)
    contract = None
    oc = obj.get("output_contract")
    if isinstance(oc, list):
        contract = [_as_str(x) for x in oc if _as_str(x)]
    elif isinstance(oc, dict):
        contract = sorted(oc.keys())
    return {
        "case_id": cid, "present": True, "research_question": question,
        "queries": queries, "databases": dbs, "contract": contract,
        "raw": obj,
    }, errs
