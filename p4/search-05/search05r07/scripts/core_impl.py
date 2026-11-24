#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- 多数据库联合检索核心实现（仅标准库，确定性，无网络）。

职责：输入发现 -> 响应分类 -> 记录抽取 -> 字段归一 -> 去重 -> 合并 ->
覆盖比较 -> 证据记录 -> 相关性提示与排序。所有异常都被捕获并降级，
任何输入（空目录、坏 JSON、坏编码、缺文件、超大文件、缺工具）都不崩溃。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_SIZE = 2 * 1024 * 1024          # 2MB
MAX_QUOTE_CHARS = 2000                   # 逐字片段上限
PDF_TIMEOUT = 60

# 输出契约的固定字段；case.json 若自带 output_contract 会额外并入。
DEFAULT_CONTRACT = [
    "case_id", "status", "research_question", "search_scope", "artifacts",
    "candidates", "ranking", "evidence_records", "coverage",
    "empty_results", "conflicts", "missing_metadata", "permission_issues",
    "limitations", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime",
]

# 候选记录的统一输出字段。
OUTPUT_FIELDS = [
    "title", "authors", "year", "journal", "volume", "issue", "pages",
    "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher",
    "identifiers",
]

# 库优先级：数字越小越可信（字段合并时的取值顺序）。
DB_PRIORITY = {
    "crossref": 0, "europepmc": 1, "pubmed-esummary": 2, "pubmed-esearch": 2,
    "pubmed": 2, "publisher": 3, "generic": 4, "unknown": 9,
}

# 标识符字段的库优先级覆盖（PMID/PMCID 优先来自 PubMed / EuropePMC）。
FIELD_DB_ORDER = {
    "doi": ["crossref", "europepmc", "pubmed", "pubmed-esummary", "publisher", "generic"],
    "pmid": ["pubmed", "pubmed-esummary", "europepmc", "publisher", "generic"],
    "pmcid": ["europepmc", "pubmed", "pubmed-esummary", "publisher", "generic"],
    "title": ["crossref", "pubmed", "pubmed-esummary", "europepmc", "publisher", "generic"],
    "year": ["crossref", "europepmc", "pubmed", "pubmed-esummary", "publisher", "generic"],
    "journal": ["crossref", "pubmed", "pubmed-esummary", "europepmc", "publisher", "generic"],
    "abstract": ["europepmc", "publisher", "crossref", "generic"],
    "doc_type": ["crossref", "europepmc", "pubmed", "pubmed-esummary", "publisher", "generic"],
}

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "with", "to",
    "from", "by", "as", "at", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "that", "this", "these",
    "those", "it", "its", "we", "you", "they", "he", "she", "which", "who",
    "whom", "what", "when", "where", "why", "how", "not", "no", "yes",
    "between", "among", "via", "per", "using", "use", "used", "based",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 发现与读取（鲁棒性核心：任何读/解失败都降级为错误记录，不抛异常）
# --------------------------------------------------------------------------

def discover_input_files(input_dir: Path) -> Tuple[List[Path], List[Dict]]:
    """扫描输入目录，返回 (文件列表, 跳过记录)。跳过目录不存在/超大/不可读。"""
    files: List[Path] = []
    skipped: List[Dict] = []
    try:
        if not input_dir.exists():
            return files, [{"path": str(input_dir), "reason": "input_dir_missing"}]
        for p in sorted(input_dir.rglob("*")):
            try:
                if not p.is_file():
                    continue
                size = p.stat().st_size
            except OSError as exc:  # 无权限、符号链接等
                skipped.append({"path": str(p), "reason": "stat_failed: %s" % exc})
                continue
            if size > MAX_FILE_SIZE:
                skipped.append({"path": str(p), "reason": "oversize: %dB" % size})
                continue
            files.append(p)
    except Exception as exc:  # 极端防御
        skipped.append({"path": str(input_dir), "reason": "scan_failed: %s" % exc})
    return files, skipped


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".txt", ".md", ".csv", ".tsv", ".xml", ".yaml", ".yml"):
        return "text"
    return "other"


def load_json(path: Path) -> Tuple[Optional[Any], List[str]]:
    """读 JSON，容忍 BOM / 坏编码 / 坏 JSON，返回 (对象或 None, 错误列表)。"""
    errors: List[str] = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, ["read_failed: %s" % exc]
    text: Optional[str] = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
        errors.append("encoding_replaced")
    try:
        return json.loads(text), errors
    except Exception as exc:
        return None, errors + ["json_parse: %s" % exc]


def read_text(path: Path) -> Tuple[str, List[str]]:
    """读文本，容错编码。"""
    degradations: List[str] = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return "", ["read_failed: %s" % exc]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), degradations
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ["encoding_replaced"]


def parse_pdf_text(path: Path) -> Tuple[str, List[str]]:
    """抽取 PDF 文本；缺 pdftotext 或失败时返回空串并记录降级。"""
    degradations: List[str] = []
    exe = shutil.which("pdftotext")
    if not exe:
        return "", ["pdftotext_not_found"]
    try:
        proc = subprocess.run(
            [exe, "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=PDF_TIMEOUT, check=False)
        if proc.returncode != 0:
            return "", ["pdftotext_rc:%d" % proc.returncode]
        return proc.stdout.decode("utf-8", errors="replace"), []
    except subprocess.TimeoutExpired:
        return "", ["pdftotext_timeout"]
    except Exception as exc:  # 缺可执行权限、Windows 命名管道等
        return "", ["pdftotext_failed: %s" % exc]


# --------------------------------------------------------------------------
# 响应分类与记录抽取
# --------------------------------------------------------------------------

def classify_response(obj: Any) -> str:
    """按结构识别数据库响应类型，返回 db 名或 None。"""
    if not isinstance(obj, dict):
        if isinstance(obj, list) and obj:
            return "publisher"
        return None
    if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("items"), list):
        return "crossref"
    if isinstance(obj.get("resultList"), dict) and isinstance(obj["resultList"].get("result"), list):
        return "europepmc"
    if "esearchresult" in obj and isinstance(obj["esearchresult"], dict):
        return "pubmed-esearch"
    if isinstance(obj.get("result"), dict):
        result = obj["result"]
        vals = [v for v in result.values() if isinstance(v, dict)]
        if vals and any(("uid" in v) or ("title" in v) for v in vals):
            return "pubmed-esummary"
        if isinstance(result.get("uids"), list):
            return "pubmed-esummary"
    for k in ("items", "results", "records", "data", "publications", "documents", "hits"):
        if isinstance(obj.get(k), list):
            return "publisher"
    return "generic"


def _record_list_keys() -> List[Tuple[str, str]]:
    return [
        ("message.items", "message.items[%d]"),
        ("resultList.result", "resultList.result[%d]"),
        ("items", "items[%d]"),
        ("results", "results[%d]"),
        ("records", "records[%d]"),
        ("data", "data[%d]"),
        ("publications", "publications[%d]"),
        ("documents", "documents[%d]"),
        ("hits", "hits[%d]"),
    ]


def extract_records(obj: Any) -> List[Tuple[Dict, str]]:
    """从响应中抽取 (原始记录 dict, locator) 列表。"""
    out: List[Tuple[Dict, str]] = []
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, dict):
                out.append((item, "[%d]" % i))
        return out
    if not isinstance(obj, dict):
        return out
    # PubMed esummary：result 下 uid -> 记录 dict
    if isinstance(obj.get("result"), dict):
        result = obj["result"]
        uids = result.get("uids") or []
        for uid in uids:
            rec = result.get(uid)
            if isinstance(rec, dict):
                out.append((rec, "result.%s" % uid))
        if out:
            return out
    for key_path, loc_fmt in _record_list_keys():
        node = _dig_path(obj, key_path.split("."))
        if isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, dict):
                    out.append((item, loc_fmt % i))
            if out:
                return out
    return out


def _dig_path(obj: Any, keys: List[str]) -> Any:
    cur = obj
    for k in keys:
        if isinstance(cur, dict):
            if k not in cur:
                return None
            cur = cur[k]
        elif isinstance(cur, list):
            try:
                cur = cur[int(k)]
            except (ValueError, IndexError, TypeError):
                return None
        else:
            return None
    return cur


# --------------------------------------------------------------------------
# 字段归一
# --------------------------------------------------------------------------

def _as_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).strip()
    return s or None


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _first_nonempty(v: Any) -> Any:
    if isinstance(v, list):
        for x in v:
            s = _as_str(x)
            if s:
                return x
        return None
    return v


def norm_title(s: Any) -> Optional[str]:
    s = _as_str(_first_nonempty(s))
    if not s:
        return None
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def norm_doi(s: Any) -> Optional[str]:
    s = _as_str(_first_nonempty(s))
    if not s:
        return None
    s = s.lower().strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s or None


def _title_key(title: Optional[str]) -> str:
    t = (title or "").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    ka, kb = _title_key(a), _title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


def _match_year(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    m = re.search(r"(1[6-9]\d{2}|20\d{2})", str(raw))
    if m:
        return int(m.group())
    return None


def normalize_authors(raw: Dict, db: str) -> List[str]:
    out: List[str] = []
    if db == "crossref":
        for a in _as_list(raw.get("author")):
            if isinstance(a, dict):
                g = _as_str(a.get("given"))
                f = _as_str(a.get("family"))
                nm = a.get("name")
                if g and f:
                    out.append("%s, %s" % (f, g))
                elif f:
                    out.append(f)
                elif g:
                    out.append(g)
                elif nm:
                    out.append(_as_str(nm) or "")
            elif isinstance(a, str):
                out.append(a.strip())
    elif db == "europepmc":
        astr = _as_str(raw.get("authorString"))
        if astr:
            out = [x.strip() for x in astr.split(",") if x.strip()]
        else:
            al = raw.get("authorList") or {}
            for a in _as_list(al.get("author")):
                if isinstance(a, dict):
                    nm = _as_str(a.get("fullName")) or _as_str(a.get("name"))
                    if nm:
                        out.append(nm)
    elif db.startswith("pubmed"):
        for a in _as_list(raw.get("authors")):
            if isinstance(a, dict):
                nm = _as_str(a.get("name"))
                if nm:
                    out.append(nm)
    else:
        for a in _as_list(raw.get("authors") or raw.get("author") or raw.get("creator")):
            if isinstance(a, str):
                out.append(a.strip())
            elif isinstance(a, dict):
                nm = _as_str(a.get("name")) or _as_str(a.get("fullName"))
                if not nm:
                    g = _as_str(a.get("given"))
                    f = _as_str(a.get("family"))
                    nm = ("%s, %s" % (f, g)) if (f and g) else (f or g)
                if nm:
                    out.append(nm)
    return [x for x in out if x]


def extract_year(raw: Dict, db: str) -> Optional[int]:
    if db == "crossref":
        for k in ("published-print", "published-online", "published", "issued", "created", "deposited"):
            v = raw.get(k)
            if isinstance(v, dict):
                dp = v.get("date-parts") or v.get("date_parts")
                if isinstance(dp, list) and dp:
                    first = dp[0]
                    if isinstance(first, list) and first:
                        y = _match_year(first[0])
                        if y:
                            return y
                    else:
                        y = _match_year(first)
                        if y:
                            return y
        return None
    if db == "europepmc":
        y = _match_year(raw.get("pubYear"))
        if y:
            return y
        for k in ("firstPublicationDate", "firstIndexDate", "electronicPublicationDate"):
            y = _match_year(raw.get(k))
            if y:
                return y
        return None
    if db.startswith("pubmed"):
        for k in ("pubdate", "sortpubdate", "epubdate"):
            y = _match_year(raw.get(k))
            if y:
                return y
        return None
    for k in ("year", "publicationYear", "publishedYear", "pubYear"):
        y = _match_year(raw.get(k))
        if y:
            return y
    for k in ("date", "publishedDate", "publicationDate", "pubDate", "publication_date"):
        y = _match_year(raw.get(k))
        if y:
            return y
    return None


def extract_journal(raw: Dict, db: str) -> Optional[str]:
    if db == "crossref":
        ct = _as_str(_first_nonempty(raw.get("container-title")))
        if ct:
            return ct
        return _as_str(_first_nonempty(raw.get("short-container-title")))
    if db == "europepmc":
        ji = raw.get("journalInfo") or {}
        j = ji.get("journal") or {}
        if isinstance(j, dict):
            t = _as_str(j.get("title"))
            if t:
                return t
        return _as_str(raw.get("journalTitle")) or _as_str(raw.get("journal"))
    if db.startswith("pubmed"):
        return _as_str(raw.get("fulljournalname")) or _as_str(raw.get("source"))
    for k in ("journal", "journalTitle", "journal_title", "container-title", "source", "publication"):
        t = _as_str(_first_nonempty(raw.get(k)))
        if t:
            return t
    return None


def extract_volume_issue_pages(raw: Dict, db: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if db == "europepmc":
        ji = raw.get("journalInfo") or {}
        vol = _as_str(ji.get("volume"))
        iss = _as_str(ji.get("issue"))
        pages = _as_str(raw.get("pageInfo"))
        return vol, iss, pages
    if db == "crossref":
        return _as_str(raw.get("volume")), _as_str(raw.get("issue")), _as_str(raw.get("page"))
    if db.startswith("pubmed"):
        return _as_str(raw.get("volume")), _as_str(raw.get("issue")), _as_str(raw.get("pages"))
    return (_as_str(raw.get("volume")), _as_str(raw.get("issue")),
            _as_str(raw.get("pages") or raw.get("page") or raw.get("pageInfo")))


def extract_identifiers(raw: Dict, db: str) -> Dict[str, Optional[str]]:
    ids: Dict[str, Optional[str]] = {"doi": None, "pmid": None, "pmcid": None, "issn": None}
    if db == "crossref":
        ids["doi"] = norm_doi(raw.get("DOI"))
        issn = _first_nonempty(raw.get("ISSN"))
        ids["issn"] = _as_str(issn)
    elif db == "europepmc":
        ids["doi"] = norm_doi(raw.get("doi"))
        ids["pmid"] = _as_str(raw.get("pmid"))
        ids["pmcid"] = _as_str(raw.get("pmcid"))
        ids["issn"] = _as_str(raw.get("issn"))
    elif db.startswith("pubmed"):
        ids["pmid"] = _as_str(raw.get("uid")) or _as_str(raw.get("pmid"))
        for aid in _as_list(raw.get("articleids")):
            if isinstance(aid, dict):
                idtype = (aid.get("idtype") or "").lower()
                val = _as_str(aid.get("value"))
                if idtype == "doi":
                    ids["doi"] = norm_doi(val)
                elif idtype == "pmcid" or idtype == "pmc":
                    ids["pmcid"] = re.sub(r"^PMC", "", val or "")
                elif idtype == "pubmed":
                    ids["pmid"] = ids["pmid"] or val
        ids["issn"] = _as_str(raw.get("issn"))
    else:
        ids["doi"] = norm_doi(raw.get("doi") or raw.get("DOI"))
        ids["pmid"] = _as_str(raw.get("pmid") or raw.get("PMID"))
        ids["pmcid"] = _as_str(raw.get("pmcid") or raw.get("PMCID"))
        ids["issn"] = _as_str(raw.get("issn") or raw.get("ISSN"))
    return ids


def extract_abstract(raw: Dict, db: str) -> Optional[str]:
    if db == "europepmc":
        t = _as_str(raw.get("abstractText"))
        if t:
            t = re.sub(r"<[^>]+>", " ", t)
            t = re.sub(r"\s+", " ", t).strip()
            return t or None
        return None
    if db == "crossref":
        t = _as_str(raw.get("abstract"))
        if t:
            t = re.sub(r"<[^>]+>", " ", t)
            t = re.sub(r"\s+", " ", t).strip()
            return t or None
        return None
    return _as_str(raw.get("abstract") or raw.get("description"))


def extract_doc_type(raw: Dict, db: str) -> Optional[str]:
    if db == "crossref":
        return _as_str(raw.get("type"))
    if db == "europepmc":
        return _as_str(raw.get("pubType"))
    if db.startswith("pubmed"):
        return _as_str(_first_nonempty(raw.get("pubtype")))
    return _as_str(raw.get("type") or raw.get("docType") or raw.get("documentType"))


def extract_publisher(raw: Dict, db: str) -> Optional[str]:
    if db == "crossref":
        return _as_str(raw.get("publisher"))
    if db == "europepmc":
        ji = raw.get("journalInfo") or {}
        j = ji.get("journal") or {}
        if isinstance(j, dict):
            return _as_str(j.get("publisher"))
        return _as_str(raw.get("publisher"))
    if db.startswith("pubmed"):
        return None
    return _as_str(raw.get("publisher"))


def normalize_record(raw: Dict, db: str) -> Dict:
    ids = extract_identifiers(raw, db)
    vol, iss, pages = extract_volume_issue_pages(raw, db)
    rec = {
        "title": norm_title(raw.get("title")),
        "authors": normalize_authors(raw, db),
        "year": extract_year(raw, db),
        "journal": extract_journal(raw, db),
        "volume": vol,
        "issue": iss,
        "pages": pages,
        "doi": ids["doi"],
        "pmid": ids["pmid"],
        "pmcid": ids["pmcid"],
        "issn": ids["issn"],
        "doc_type": extract_doc_type(raw, db),
        "abstract": extract_abstract(raw, db),
        "publisher": extract_publisher(raw, db),
    }
    rec["identifiers"] = {
        "doi": rec["doi"], "pmid": rec["pmid"], "pmcid": rec["pmcid"], "issn": rec["issn"],
    }
    return rec


# --------------------------------------------------------------------------
# 去重与合并
# --------------------------------------------------------------------------

def _dedupe_doi(rec: Dict) -> Optional[str]:
    d = rec.get("doi")
    return "doi:" + d if d else None


def _dedupe_title_author_year(rec: Dict) -> Optional[str]:
    t = _title_key(rec.get("title"))
    if not t:
        return None
    a = (rec.get("authors") or [""])[0].lower().strip()
    y = rec.get("year") or ""
    return "tay:%s|%s|%s" % (t, a, y)


def _dedupe_title_year(rec: Dict) -> Optional[str]:
    t = _title_key(rec.get("title"))
    if not t:
        return None
    return "ty:%s|%s" % (t, rec.get("year") or "")


def _same_year(a: Dict, b: Dict) -> bool:
    return a.get("year") is not None and a.get("year") == b.get("year")


def cluster_records(records: List[Dict]) -> List[List[Dict]]:
    """确定性聚类：DOI 精确 > 标题+一作+年份 > 标题+年份 > 标题模糊(>=0.90 且同年)。"""
    if not records:
        return []
    groups: List[List[Dict]] = []
    used: set = set()

    def _append(recs: List[Dict]) -> None:
        for r in recs:
            used.add(id(r))
        groups.append(recs)

    # pass 1: DOI 精确
    by_doi: Dict[str, List[Dict]] = {}
    for r in records:
        d = r.get("doi")
        if d:
            by_doi.setdefault(d, []).append(r)
    for recs in by_doi.values():
        _append(recs)

    # pass 2a: 标题+一作+年份
    remaining = [r for r in records if id(r) not in used]
    by_tay: Dict[str, List[Dict]] = {}
    for r in remaining:
        k = _dedupe_title_author_year(r)
        if k:
            by_tay.setdefault(k, []).append(r)
    for recs in by_tay.values():
        _append(recs)

    # pass 2b: 标题+年份
    remaining = [r for r in records if id(r) not in used]
    by_ty: Dict[str, List[Dict]] = {}
    for r in remaining:
        k = _dedupe_title_year(r)
        if k:
            by_ty.setdefault(k, []).append(r)
    for recs in by_ty.values():
        _append(recs)

    # pass 3: 标题模糊 >= 0.90 且同年
    remaining = [r for r in records if id(r) not in used]
    fuzzy: List[List[Dict]] = []
    for r in remaining:
        placed = False
        for g in fuzzy:
            ref = g[0]
            if _same_year(r, ref) and title_similarity(r.get("title"), ref.get("title")) >= 0.90:
                g.append(r)
                placed = True
                break
        if not placed:
            fuzzy.append([r])
    groups.extend(fuzzy)
    return groups


def _db_rank(db: str) -> int:
    return DB_PRIORITY.get(db, 9)


def _field_rank(field: str, db: str) -> int:
    order = FIELD_DB_ORDER.get(field)
    if not order:
        return _db_rank(db)
    return order.index(db) if db in order else 99


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == []


def merge_cluster(cluster: List[Dict]) -> Tuple[Dict, List[Dict]]:
    """合并一个簇，返回 (候选记录, 冲突列表)。冲突消解：按字段优先级取首个非空。"""
    ordered = sorted(cluster, key=lambda r: (_db_rank(r["_db"]), r["_locator"]))
    merged: Dict[str, Any] = {f: None for f in OUTPUT_FIELDS}
    field_sources: Dict[str, List[Dict]] = {}
    conflicts: List[Dict] = []

    for field in OUTPUT_FIELDS:
        sources = []
        chosen = None
        for r in ordered:
            v = r.get(field)
            if _is_empty(v):
                continue
            sources.append({
                "db": r["_db"], "source_file": r["_source_file"],
                "locator": r["_locator"], "value": v,
            })
        if sources:
            # 按字段优先级重新排序，取首个非空
            ranked = sorted(sources, key=lambda s: _field_rank(field, s["db"]))
            chosen = ranked[0]["value"]
        merged[field] = chosen
        field_sources[field] = sources

    # 标量冲突检测：多个来源取值不同即记录，消解方式=优先级
    for field in ("doi", "year", "title", "journal", "volume", "issue", "pages",
                  "pmid", "pmcid", "issn", "doc_type", "publisher"):
        vals: Dict[str, List[Dict]] = {}
        for r in ordered:
            v = r.get(field)
            if _is_empty(v):
                continue
            key = json.dumps(v, ensure_ascii=False, sort_keys=True)
            vals.setdefault(key, []).append({
                "value": v, "db": r["_db"], "source_file": r["_source_file"],
                "locator": r["_locator"],
            })
        if len(vals) > 1:
            conflicts.append({
                "field": field,
                "values": list(vals.values()),
                "resolution": "priority_order",
                "resolved_to": merged.get(field),
            })

    merged["identifiers"] = {
        "doi": merged.get("doi"), "pmid": merged.get("pmid"),
        "pmcid": merged.get("pmcid"), "issn": merged.get("issn"),
    }
    dbs = sorted(set(r["_db"] for r in ordered))
    merge_rule = _dedupe_rule(cluster)
    raw_snapshots = [{
        "db": r["_db"], "source_file": r["_source_file"], "locator": r["_locator"],
        "snapshot": json.dumps(r["_raw"], ensure_ascii=False)[:MAX_QUOTE_CHARS],
    } for r in ordered]
    provenance = {
        "dbs": dbs,
        "source_files": [r["_source_file"] for r in ordered],
        "locators": [r["_locator"] for r in ordered],
        "merge_rule": merge_rule,
        "raw_snapshots": raw_snapshots,
    }
    merged["field_sources"] = field_sources
    merged["provenance"] = provenance
    return merged, conflicts


def _dedupe_rule(cluster: List[Dict]) -> str:
    dbs = {r["_db"] for r in cluster}
    if len(cluster) == 1:
        return "singleton"
    dois = {r.get("doi") for r in cluster if r.get("doi")}
    if len(dois) == 1 and len(dois) == len(cluster):
        return "doi_exact"
    if len(dois) == 1 and len(cluster) > 1:
        return "doi_exact_partial"
    if len(dbs) < len(cluster):
        return "title_author_year"
    return "title_fuzzy"


# --------------------------------------------------------------------------
# 相关性、排序、覆盖、证据
# --------------------------------------------------------------------------

def tokenize(text: Any) -> set:
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def relevance_hint(rec: Dict, question: Optional[str]) -> Dict:
    q_terms = tokenize(question) - STOPWORDS
    if not q_terms:
        return {"score": 0.0, "matched_terms": [], "basis": "no_query"}
    text = " ".join([
        str(rec.get("title") or ""),
        str(rec.get("abstract") or ""),
        " ".join(rec.get("authors") or []),
        str(rec.get("journal") or ""),
    ])
    t_terms = tokenize(text)
    matched = sorted(q_terms & t_terms)
    score = round(len(matched) / len(q_terms), 4) if q_terms else 0.0
    return {"score": score, "matched_terms": matched, "basis": "term_overlap"}


def _rel_label(score: float) -> str:
    if score >= 0.5:
        return "high"
    if score >= 0.2:
        return "medium"
    if score > 0:
        return "low"
    return "unknown"


def build_ranking(candidates: List[Dict], question: Optional[str]) -> List[Dict]:
    scored = []
    for c in candidates:
        hint = c.get("relevance_hint") or relevance_hint(c, question)
        scored.append((hint.get("score", 0.0), c.get("year") or 0, c.get("title") or ""))
    order = sorted(range(len(scored)),
                   key=lambda i: (-scored[i][0], -(scored[i][1] or 0), scored[i][2].lower()))
    ranking = []
    for rank, i in enumerate(order, 1):
        hint = candidates[i].get("relevance_hint") or {}
        matched = hint.get("matched_terms") or []
        rationale = ("term_overlap(%d/%d): %s" % (
            len(matched), len(tokenize(question) - STOPWORDS), ", ".join(matched)
        )) if question else "no_query_terms; sorted by year"
        ranking.append({
            "record_id": candidates[i]["record_id"],
            "rank": rank,
            "relevance": _rel_label(hint.get("score", 0.0)),
            "rationale": rationale,
        })
    return ranking


def build_evidence(clusters: List[List[Dict]]) -> List[Dict]:
    ev: List[Dict] = []
    n = 0
    for cl in clusters:
        for r in cl:
            n += 1
            ev.append({
                "evidence_id": "ev:%d" % n,
                "type": "record_source",
                "db": r["_db"],
                "source_file": r["_source_file"],
                "locator": r["_locator"],
                "quote": json.dumps(r["_raw"], ensure_ascii=False)[:MAX_QUOTE_CHARS],
            })
    return ev


def compute_coverage(records: List[Dict], clusters: List[List[Dict]],
                     dbs: List[str]) -> Dict:
    field_presence = {db: {f: 0 for f in OUTPUT_FIELDS} for db in dbs}
    db_counts = {db: 0 for db in dbs}
    for r in records:
        db = r["_db"]
        if db in db_counts:
            db_counts[db] += 1
            for f in OUTPUT_FIELDS:
                if not _is_empty(r.get(f)):
                    field_presence[db][f] += 1
    fields_missing = {}
    for db in dbs:
        n = db_counts[db]
        fields_missing[db] = [
            f for f in OUTPUT_FIELDS if field_presence[db][f] == 0
        ] if n > 0 else list(OUTPUT_FIELDS)
    single_db_only = []
    for cl in clusters:
        dbs_in = sorted(set(r["_db"] for r in cl))
        if len(dbs_in) == 1:
            single_db_only.append({
                "db": dbs_in[0],
                "title": (cl[0].get("title") or "")[:200],
                "record_count_in_cluster": len(cl),
            })
    return {
        "databases": sorted(dbs),
        "total_input_records": len(records),
        "merged_record_count": len(clusters),
        "duplicates_merged": max(0, len(records) - len(clusters)),
        "records_in_single_db_only": single_db_only,
        "systematically_missing_fields": fields_missing,
        "per_database_record_counts": db_counts,
    }


# --------------------------------------------------------------------------
# 分析流水线（供 main 调用）
# --------------------------------------------------------------------------

def analyze_inputs(input_dir: Path) -> Dict:
    """扫描并解析全部输入，返回 sources / records / degradations / errors / pdf_texts。"""
    files, skipped = discover_input_files(input_dir)
    sources: List[Dict] = []
    records: List[Dict] = []
    degradations: List[str] = []
    errors: List[str] = []
    pdf_texts: List[Tuple[str, str]] = []

    for p in files:
        rel = p.relative_to(input_dir).as_posix() if _is_under(input_dir, p) else p.name
        # case.json 是元数据而非数据库响应：单独记录，不参与文献抽取
        if p.name == "case.json":
            obj, errs = load_json(p)
            degradations.extend(errs)
            cid = (obj or {}).get("case_id") if isinstance(obj, dict) else None
            sources.append({
                "source_id": "file:" + rel, "type": "case_metadata", "path": rel,
                "case_id": cid,
                "note": "case metadata, parsed separately" if obj is not None else "case metadata unparsed",
            })
            continue
        kind = classify_file(p)
        if kind == "json":
            obj, errs = load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                continue
            db = classify_response(obj) or "unknown"
            recs = extract_records(obj)
            if not recs:
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": 0,
                    "note": "no bibliographic records detected (config/empty/esearch)",
                })
                continue
            norm_recs = []
            for raw, loc in recs:
                rec = normalize_record(raw, db)
                rec["_db"] = db
                rec["_source_file"] = rel
                rec["_locator"] = loc
                rec["_request"] = extract_request(obj)
                rec["_raw"] = raw
                norm_recs.append(rec)
                records.append(rec)
            sources.append({
                "source_id": "file:" + rel, "type": "database_response",
                "db": db, "path": rel, "record_count": len(norm_recs),
                "record_locators": [r["_locator"] for r in norm_recs],
            })
        elif kind == "pdf":
            text, deg = parse_pdf_text(p)
            degradations.extend(deg)
            pdf_texts.append((rel, text))
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        elif kind == "text":
            text, deg = read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
        else:
            sources.append({
                "source_id": "file:" + rel, "type": "other", "path": rel,
                "note": "unrecognized extension, skipped",
            })

    for s in skipped:
        degradations.append("skipped %s (%s)" % (s.get("path"), s.get("reason")))
    return {
        "sources": sources,
        "records": records,
        "degradations": degradations,
        "errors": errors,
        "pdf_texts": pdf_texts,
    }


def _is_under(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def extract_request(obj: Any) -> Optional[Dict]:
    """从响应中尽力抽取请求/查询上下文（能取多少取多少，缺失为 null）。"""
    if not isinstance(obj, dict):
        return None
    req = {}
    m = obj.get("message") if isinstance(obj.get("message"), dict) else None
    if m:
        for k in ("query", "items-per-page", "total-results"):
            if k in m:
                req[k] = m[k]
    for k in ("query", "term", "queryString", "request"):
        if k in obj and k not in req:
            req[k] = obj[k]
    return req or None


# --------------------------------------------------------------------------
# 结果组装
# --------------------------------------------------------------------------

def build_skeleton(case_id: str, started: str, contract: Optional[List[str]]) -> Dict:
    res = {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "artifacts": None,
        "candidates": [],
        "ranking": [],
        "evidence_records": [],
        "coverage": None,
        "empty_results": [],
        "conflicts": [],
        "missing_metadata": [],
        "permission_issues": [],
        "limitations": [],
        "sources": [],
        "uncertainty": {"items": [], "note": "initial skeleton"},
        "change_log": [
            {"step": "skeleton", "status": "written", "at": started,
             "detail": "先落盘合法 partial 骨架，后续增量改进"},
        ],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {
            "started_at": started, "finished_at": None, "seconds": 0,
            "pipeline_version": VERSION, "python": sys.version.split()[0],
        },
    }
    ensure_contract_keys(res, contract)
    return res


def ensure_contract_keys(res: Dict, contract: Optional[List[str]]) -> None:
    keys = contract if isinstance(contract, list) else DEFAULT_CONTRACT
    for k in keys:
        if k not in res:
            res[k] = None


def read_case_meta(case_path: Path) -> Tuple[Optional[Dict], List[str]]:
    if not case_path.exists():
        return None, ["missing: %s" % case_path.name]
    obj, errs = load_json(case_path)
    if obj is None:
        return None, errs + ["case_json_unparsed"]
    if not isinstance(obj, dict):
        return None, ["case_json_not_object"]
    return obj, []


def _first_present(d: Dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def build_result(skeleton: Dict, case_meta: Optional[Dict], analysis: Dict) -> Dict:
    res = dict(skeleton)
    skeleton_status = skeleton.get("status")
    cm = case_meta or {}
    question = _first_present(cm, "research_question", "question", "task")
    res["research_question"] = question
    records: List[Dict] = analysis["records"]
    dbs_found = sorted(set(r["_db"] for r in records))

    # search_scope
    queries = _first_present(cm, "queries", "keywords", "search_terms", default=[])
    if isinstance(queries, str):
        queries = [queries]
    derived = sorted(tokenize(question) - STOPWORDS)
    res["search_scope"] = {
        "databases": _first_present(cm, "databases", default=dbs_found) or dbs_found,
        "queries": queries,
        "derived_query_terms": derived,
    }

    clusters = cluster_records(records)
    candidates: List[Dict] = []
    conflicts: List[Dict] = []
    # 确定性排序：按年份、归一标题
    def _cluster_key(cl: List[Dict]):
        years = [r.get("year") for r in cl if r.get("year") is not None]
        return (min(years) if years else 0, _title_key(cl[0].get("title")))
    ordered_clusters = sorted(clusters, key=_cluster_key)
    for idx, cl in enumerate(ordered_clusters, 1):
        cand, confs = merge_cluster(cl)
        cand["record_id"] = "rec:%04d" % idx
        cand["relevance_hint"] = relevance_hint(cand, question)
        candidates.append(cand)
        conflicts.extend(confs)

    # 按 record_id 排序保证输出确定（record_id 已按簇序）
    candidates = sorted(candidates, key=lambda c: c["record_id"])

    res["candidates"] = candidates
    res["ranking"] = build_ranking(candidates, question)
    res["evidence_records"] = build_evidence(clusters)
    res["coverage"] = compute_coverage(records, clusters, dbs_found)
    res["sources"] = analysis["sources"]
    res["conflicts"] = conflicts
    res["degradations"] = list(res.get("degradations") or []) + list(analysis["degradations"])
    res["errors"] = list(res.get("errors") or []) + list(analysis["errors"])
    res["artifacts"] = {
        "result": "result.json",
        "merged_records": "merged_records.json",
        "evidence_map": "evidence_map.json",
        "coverage_report": "coverage_report.md",
    }

    # missing_metadata：缺核心身份字段的记录
    missing = []
    for c in candidates:
        miss = [f for f in ("title", "year", "doi") if _is_empty(c.get(f))]
        if miss:
            missing.append({"record_id": c["record_id"], "missing_fields": miss})
    res["missing_metadata"] = missing

    # empty_results：识别为数据库响应但 0 记录的文件
    empty = [s for s in res["sources"]
             if s.get("type") == "database_response" and s.get("record_count") == 0]
    res["empty_results"] = empty

    res["limitations"] = _build_limitations(res, cm)
    res["uncertainty"] = _build_uncertainty(res)
    res["change_log"] = list(res.get("change_log") or []) + [
        {"step": "analyze", "status": "done", "at": iso_now(),
         "detail": "parsed %d records into %d merged candidates" % (len(records), len(candidates))}
    ]

    # 诚实状态：输入级错误优先；空输入与缺 case.json 区分
    if skeleton_status == "input_error":
        res["status"] = "input_error" if res["sources"] else "insufficient_evidence"
    elif res["errors"] or res["degradations"]:
        res["status"] = "partial"
    elif not candidates:
        res["status"] = "insufficient_evidence"
    elif res["conflicts"] or res["missing_metadata"] or res["empty_results"]:
        res["status"] = "partial"
    else:
        res["status"] = "ok"
    return res


def _build_limitations(res: Dict, cm: Dict) -> List[str]:
    lim: List[str] = []
    if not res["sources"]:
        lim.append("input dir has no readable material files")
    if res["degradations"]:
        lim.append("degradations present; see degradations[] for per-file details")
    if res["empty_results"]:
        lim.append("some database responses contain zero bibliographic records; see empty_results[]")
    if res["missing_metadata"]:
        lim.append("some merged records lack core identity fields; see missing_metadata[]")
    if res["conflicts"]:
        lim.append("field conflicts resolved by priority order; see conflicts[]")
    if res.get("search_scope", {}).get("queries") == []:
        lim.append("no explicit queries provided in case.json; derived terms from research_question only")
    return lim


def _build_uncertainty(res: Dict) -> Dict:
    items = []
    if res["missing_metadata"]:
        items.append({"item": "missing_core_fields",
                      "detail": "%d records missing title/year/doi" % len(res["missing_metadata"])})
    if res["conflicts"]:
        items.append({"item": "field_conflicts",
                      "detail": "%d field conflicts resolved by priority" % len(res["conflicts"])})
    if res["degradations"]:
        items.append({"item": "input_degradations",
                      "detail": "%d degradation events (bad json/encoding/oversize/tool missing)" % len(res["degradations"])})
    note = ("results are deterministic given identical /input; "
            "relevance scores are lexical term-overlap, not semantic judgments")
    return {"items": items, "note": note}


def apply_decisions(res: Dict, decisions: Dict, at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts", "permission_issues"):
        if key in decisions and decisions[key] is not None:
            if key == "status":
                if decisions[key] in STATUSES:
                    res["status"] = decisions[key]
                    applied.append("status")
            elif key == "ranking":
                res["ranking"] = decisions[key]
                applied.append("ranking")
            elif key == "screening":
                res["screening"] = decisions[key]
                applied.append("screening")
            else:
                res[key] = decisions[key]
                applied.append(key)
    if applied:
        res.setdefault("change_log", []).append({
            "step": "model_refine", "status": "applied", "at": at,
            "detail": "applied decisions: " + ", ".join(applied),
        })
    return applied


def finalize(res: Dict, started_monotonic: float, started_iso: str) -> None:
    res["runtime"]["finished_at"] = iso_now()
    res["runtime"]["seconds"] = round(_elapsed(started_monotonic), 3)
    res["runtime"]["started_at"] = res["runtime"].get("started_at") or started_iso


def _elapsed(t0: float) -> float:
    import time
    return time.monotonic() - t0
