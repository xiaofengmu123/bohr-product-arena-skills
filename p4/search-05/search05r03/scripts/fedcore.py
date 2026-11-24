#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fedcore.py -- 多数据库联合检索核心库（仅标准库、确定性、无网络）。

职责：
  - 发现并解析 /input 下的异构数据库响应（Crossref / EuropePMC / PubMed / 出版社）与论文
  - 跨库字段归一、可解释去重、稳定合并、覆盖比较、证据溯源、词项相关性提示
  - 所有集合排序、计数、指纹均为确定性计算；同一输入、不同时刻运行内容一致

确定性约定：
  - 文件遍历按相对路径字典序；列表处理使用 sorted(key=...) 稳定排序
  - 去重簇内部、字段取值优先级、候选排序均不依赖 dict 迭代顺序或本地时区
  - ``compute_fingerprint`` 对去除易变字段（运行时刻/time 值）后的结果求 SHA256，
    供 selftest 在两次独立运行间比较内容一致性
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "2.0.0"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_CHARS = 2400

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 固定输出字段（output_contract 里额外声明的键会在此之上补齐）
FIXED_FIELDS = [
    "case_id", "status", "research_question", "search_scope",
    "candidates", "ranking", "evidence_records", "coverage",
    "empty_results", "conflicts", "missing_metadata", "permission_issues",
    "limitations", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "screening", "runtime",
]

# 规范化标题（检索/去重/相关性共同使用）
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "with",
    "from", "by", "using", "as", "at", "is", "are", "was", "were", "their",
    "its", "our", "your", "into", "about", "between", "through",
}
_PUNCT_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def title_key(value: Optional[str]) -> str:
    """标题归一化：小写、去标点、去停用词、空白折叠。"""
    if not value:
        return ""
    s = value.lower()
    s = _PUNCT_RE.sub(" ", s)
    toks = [t for t in s.split() if t and t not in _STOPWORDS]
    return " ".join(toks)


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value))


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def read_text_robust(path: Path) -> Tuple[str, List[str]]:
    """按 utf-8-sig 读取并容错（BOM/坏编码降级不崩溃）。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="replace"), []
        except UnicodeError:
            continue
    return "", ["encode_fallback_failed: %s" % path.name]


def load_json(path: Path) -> Tuple[Optional[Any], List[str]]:
    """读取 JSON，返回 (object_or_None, errors)。坏 JSON 不抛异常。"""
    text, errs = read_text_robust(path)
    if not text.strip():
        return None, errs + ["empty_file: %s" % path.name]
    try:
        return json.loads(text), errs
    except Exception as exc:  # noqa: BLE001
        return None, errs + ["bad_json: %s (%s)" % (path.name, exc)]


def discover_input_files(input_dir: Path) -> Tuple[List[Path], List[str]]:
    """递归发现输入文件：相对路径字典序，超限跳过并降级记录。"""
    degradations: List[str] = []
    files: List[Path] = []
    if not input_dir.exists():
        return files, ["missing_input_dir: %s" % input_dir]
    for p in input_dir.rglob("*"):
        if not p.is_file():
            continue
        name = p.name
        if name.startswith(".") or name in ("case.json", "task.md"):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            degradations.append("file_too_large(>2MB,skipped): %s" % p.relative_to(input_dir).as_posix())
            continue
        files.append(p)
    files.sort(key=lambda p: p.relative_to(input_dir).as_posix())
    return files, degradations


def classify_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".pdf":
        return "pdf"
    return "text"


def classify_response(obj: Any) -> str:
    """按结构识别响应来源库；无法识别返回 generic。"""
    if isinstance(obj, dict):
        if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("items"), list):
            return "crossref"
        rl = obj.get("resultList")
        if isinstance(rl, dict) and isinstance(rl.get("result"), list):
            return "europepmc"
        result = obj.get("result")
        if isinstance(result, dict) and "uids" in result:
            return "pubmed"
        if isinstance(obj.get("esearchresult"), dict):
            return "pubmed"
    return "generic"


def extract_records(obj: Any, db: str) -> List[Tuple[Dict, str]]:
    """抽取单条文献记录及其 JSON 定位符。返回 [(raw, locator), ...]。"""
    out: List[Tuple[Dict, str]] = []
    try:
        if db == "crossref":
            items = ((obj.get("message") or {}).get("items") or [])
            for i, it in enumerate(items):
                if isinstance(it, dict):
                    out.append((it, "message.items[%d]" % i))
        elif db == "europepmc":
            results = ((obj.get("resultList") or {}).get("result") or [])
            for i, r in enumerate(results):
                if isinstance(r, dict):
                    out.append((r, "resultList.result[%d]" % i))
        elif db == "pubmed":
            result = obj.get("result") or {}
            for uid in result.get("uids", []):
                rec = result.get(uid)
                if isinstance(rec, dict):
                    merged = dict(rec)
                    merged.setdefault("uid", uid)
                    out.append((merged, "result.%s" % uid))
        else:
            out.extend(_extract_generic(obj))
    except Exception:  # noqa: BLE001
        pass
    return out


def _extract_generic(obj: Any) -> List[Tuple[Dict, str]]:
    """通用/出版社结构：寻找列表形式的文献记录。"""
    out: List[Tuple[Dict, str]] = []
    if isinstance(obj, list):
        for i, it in enumerate(obj):
            if isinstance(it, dict) and _looks_like_record(it):
                out.append((it, "[%d]" % i))
        return out
    if not isinstance(obj, dict):
        return out
    candidates = []
    for key, val in obj.items():
        if isinstance(val, list):
            for i, it in enumerate(val):
                if isinstance(it, dict) and _looks_like_record(it):
                    candidates.append((it, "%s[%d]" % (key, i)))
    # 优先带 doi/title 的记录，避免把无关子对象当文献
    def score(item: Tuple[Dict, str]) -> int:
        r = item[0]
        s = 0
        if r.get("DOI") or r.get("doi"):
            s += 2
        if r.get("title") or r.get("Title"):
            s += 1
        return s

    candidates.sort(key=lambda x: -score(x))
    seen: set = set()
    for r, loc in candidates:
        sig = _stable_json(sorted(r.items()))
        if sig in seen:
            continue
        seen.add(sig)
        out.append((r, loc))
    return out


def _looks_like_record(rec: Dict) -> bool:
    keys = {str(k).lower() for k in rec.keys()}
    return bool(keys & {"doi", "title", "pmid", "authors", "author", "journal",
                        "publication", "container-title", "issued", "pubyear", "year"})


# ---------------------------------------------------------------- 字段归一 ---

def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _first_str(value: Any) -> Optional[str]:
    for v in _as_list(value):
        s = str(v).strip()
        if s:
            return s
    return None


def _year_from_parts(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value)
    m = re.search(r"(18|19|20)\d{2}", s)
    return int(m.group(0)) if m else None


def _authors_from_list(author_list: Any) -> List[str]:
    """把常见作者对象列表规范成 ['Family, Given', ...]。"""
    out: List[str] = []
    if not isinstance(author_list, list):
        return out
    for a in author_list:
        if not isinstance(a, dict):
            if a:
                out.append(str(a).strip())
            continue
        family = a.get("family") or a.get("lastName") or a.get("lastname") or a.get("surname") or ""
        given = a.get("given") or a.get("firstName") or a.get("firstname") or a.get("forename") or ""
        full = a.get("fullName") or a.get("name") or a.get("full_name") or ""
        if family and given:
            out.append("%s, %s" % (str(family).strip(), str(given).strip()))
        elif full:
            out.append(str(full).strip())
        elif family:
            out.append(str(family).strip())
    return [x for x in out if x]


def _authors_from_string(author_string: Any) -> List[str]:
    if not author_string:
        return []
    s = str(author_string).strip()
    return [p.strip() for p in re.split(r"\s*[;,]\s*|\s+&\s+", s) if p.strip()]


def normalize_record(raw: Dict, db: str) -> Dict:
    """把源记录归一化为 canonical 字段 + 溯源私有字段。"""
    r: Dict[str, Any] = {}
    if db == "crossref":
        r["title"] = _first_str(raw.get("title"))
        r["authors"] = _authors_from_list(raw.get("author"))
        r["journal"] = _first_str(raw.get("container-title"))
        r["volume"] = _first_str(raw.get("volume"))
        r["issue"] = _first_str(raw.get("issue"))
        r["pages"] = _first_str(raw.get("page"))
        r["doi"] = _first_str(raw.get("DOI"))
        r["issn"] = _first_str(raw.get("ISSN"))
        r["doc_type"] = _first_str(raw.get("type"))
        r["publisher"] = _first_str(raw.get("publisher"))
        r["abstract"] = _one_abstract(raw.get("abstract"))
        r["year"] = _year_from_parts(_first_crossref_date(raw))
    elif db == "europepmc":
        r["title"] = _first_str(raw.get("title"))
        r["authors"] = _authors_from_list(raw.get("authorList")) or _authors_from_string(raw.get("authorString"))
        j = raw.get("journalInfo") or {}
        r["journal"] = _first_str(j.get("journal", {}).get("title")) or _first_str(raw.get("journalTitle")) or _first_str(
            j.get("journal", {}).get("medlineAbbreviation"))
        r["volume"] = _first_str(raw.get("volume"))
        r["issue"] = _first_str(raw.get("issue"))
        r["pages"] = _first_str(raw.get("pageInfo"))
        r["doi"] = _first_str(raw.get("doi"))
        r["pmid"] = _first_str(raw.get("pmid"))
        r["pmcid"] = _first_str(raw.get("pmcid"))
        r["issn"] = _first_str(j.get("journal", {}).get("issn")) or _first_str(raw.get("issn"))
        r["doc_type"] = _first_str(raw.get("pubType"))
        r["publisher"] = _first_str(raw.get("publisher"))
        r["abstract"] = _one_abstract(raw.get("abstractText"))
        r["year"] = _year_from_parts(raw.get("pubYear"))
    elif db == "pubmed":
        r["title"] = _first_str(raw.get("title"))
        names = raw.get("authors") or []
        r["authors"] = [str(a.get("name", "")).strip() for a in names if isinstance(a, dict) and a.get("name")]
        r["authors"] = [x for x in r["authors"] if x]
        r["journal"] = _first_str(raw.get("fulljournalname")) or _first_str(raw.get("source"))
        r["volume"] = _first_str(raw.get("volume"))
        r["issue"] = _first_str(raw.get("issue"))
        r["pages"] = _first_str(raw.get("pages"))
        ids = {}
        for aid in raw.get("articleids") or []:
            if isinstance(aid, dict) and aid.get("idtype") and aid.get("value"):
                ids[str(aid["idtype"]).lower()] = str(aid["value"])
        r["doi"] = ids.get("doi") or ids.get("pii")
        r["pmid"] = _first_str(raw.get("uid")) or ids.get("pmid")
        r["pmcid"] = ids.get("pmc") or ids.get("pmcid")
        r["issn"] = _first_str(raw.get("issn"))
        pt = raw.get("pubtype") or []
        r["doc_type"] = ", ".join(pt) if pt else None
        r["publisher"] = _first_str(raw.get("publishername"))
        r["abstract"] = None
        r["year"] = _year_from_parts(raw.get("pubdate")) or _year_from_parts(raw.get("epubdate"))
    else:
        r["title"] = _first_str(_pick(raw, "title", "Title", "articleTitle", "article_title", "name"))
        r["authors"] = _authors_from_list(_pick(raw, "authors", "author", "Authors", "creator")) or _authors_from_string(
            _pick(raw, "authorString", "author_string"))
        r["journal"] = _first_str(_pick(raw, "journal", "journalTitle", "source", "publication", "container-title", "publicationName"))
        r["volume"] = _first_str(_pick(raw, "volume", "Volume"))
        r["issue"] = _first_str(_pick(raw, "issue", "number"))
        r["pages"] = _first_str(_pick(raw, "pages", "page", "pageInfo", "pagination"))
        r["doi"] = _first_str(_pick(raw, "DOI", "doi"))
        r["pmid"] = _first_str(_pick(raw, "PMID", "pmid"))
        r["pmcid"] = _first_str(_pick(raw, "PMCID", "pmcid", "pmc"))
        r["issn"] = _first_str(_pick(raw, "ISSN", "issn"))
        r["doc_type"] = _first_str(_pick(raw, "type", "doc_type", "documentType", "pubType"))
        r["publisher"] = _first_str(_pick(raw, "publisher", "publisherName", "Publisher"))
        r["abstract"] = _one_abstract(_pick(raw, "abstract", "Abstract", "abstractText"))
        r["year"] = _year_from_parts(_pick(raw, "year", "Year", "pubYear", "issued", "publication_date", "date"))
    r["doi"] = r["doi"].lower().strip() if r.get("doi") else None
    r["title"] = _clean_title(r.get("title"))
    r["doc_type"] = _clean_doc_type(r.get("doc_type"))
    ids: Dict[str, str] = {}
    for idk in ("doi", "pmid", "pmcid", "issn", "pii", "isbn"):
        if r.get(idk):
            ids[idk] = str(r[idk])
    r["identifiers"] = ids
    return r


def _pick(raw: Dict, *keys: Any) -> Any:
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


def _clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    t = _strip_tags(str(title)).strip()
    t = re.sub(r"\s+", " ", t)
    return t or None


def _first_crossref_date(raw: Dict) -> Optional[Any]:
    for key in ("published-print", "published-online", "issued", "created", "published"):
        v = raw.get(key)
        if isinstance(v, dict) and v.get("date-parts"):
            try:
                return v["date-parts"][0][0]
            except (IndexError, TypeError):
                continue
    return raw.get("year")


def _one_abstract(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, list):
        value = " ".join(str(v) for v in value if v)
    text = _strip_tags(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _clean_doc_type(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, list):
        value = " ".join(str(v) for v in value if v)
    text = _PUNCT_RE.sub(" ", str(value).lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


# ---------------------------------------------------------------- 去重与合并 ---

def _field_key(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, bool):
        return "1" if val else "0"
    return _strip_tags(str(val)).strip().lower()


def dedup_signature(rec: Dict) -> Dict:
    """生成去重签名：DOI/PMID 精确 + 标题/一作/年份。"""
    return {
        "doi": rec.get("doi"),
        "pmid": rec.get("pmid"),
        "title": title_key(rec.get("title")),
        "first_author": _first_author_key(rec),
        "year": rec.get("year"),
    }


def _first_author_key(rec: Dict) -> str:
    authors = rec.get("authors") or []
    if authors:
        first = authors[0]
        fam = first.split(",")[0] if "," in first else first.split()[-1] if first.split() else first
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(fam).lower())
    return ""


def dedup_reason(a: Dict, b: Dict) -> Optional[str]:
    """返回 a、b 等价的去重依据；不匹配返回 None。优先级为 DO小 > PMID > 标题+一作+年份 > 标题模糊+同年。"""
    sa, sb = dedup_signature(a), dedup_signature(b)
    if sa["doi"] and sb["doi"] and sa["doi"] == sb["doi"]:
        return "doi_exact"
    if sa["pmid"] and sb["pmid"] and sa["pmid"] == sb["pmid"]:
        return "pmid_exact"
    if (sa["title"] and sb["title"] and sa["title"] == sb["title"]
            and sa["first_author"] and sb["first_author"] and sa["first_author"] == sb["first_author"]
            and sa["year"] and sb["year"] and sa["year"] == sb["year"]):
        return "title_author_year"
    if sa["title"] and sb["title"]:
        ratio = difflib.SequenceMatcher(None, sa["title"], sb["title"]).ratio()
        if ratio >= 0.90 and sa["year"] and sb["year"] and sa["year"] == sb["year"]:
            return "title_fuzzy(%.2f)" % ratio
    return None


def cluster_records(records: List[Dict]) -> List[List[Dict]]:
    """确定性并查集聚类；链接式传递闭包，顺序由记录列表决定（列表已排序）。"""
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if find(i) == find(j):
                continue
            if dedup_reason(records[i], records[j]) is not None:
                parent[find(i)] = find(j)
    groups: Dict[int, List[int]] = {}
    for i in range(len(records)):
        root = find(i)
        groups.setdefault(root, []).append(i)
    clusters = [[records[i] for i in idxs] for idxs in groups.values()]
    clusters.sort(key=lambda c: sorted((title_key(r.get("title")), r.get("_locator", "")) for r in c)[0])
    return clusters


_MAJOR_FIELDS = ["title", "authors", "journal", "volume", "issue", "pages", "doi", "pmid",
                 "pmcid", "issn", "doc_type", "publisher", "year"]
_DB_PRIORITY = ["crossref", "europepmc", "pubmed", "generic"]


def merge_cluster(cluster: List[Dict], idx: int, total: int) -> Tuple[Dict, List[Dict]]:
    """把去重簇合并成一条 merged 记录；返回 (merged, conflicts)。"""
    order = sorted(cluster, key=lambda r: (_DB_PRIORITY.index(r["_db"]) if r["_db"] in _DB_PRIORITY else 999,
                                            r["_locator"]))
    merged: Dict[str, Any] = {}
    field_sources: Dict[str, List[Dict]] = {}
    conflicts: List[Dict] = []
    for f in _MAJOR_FIELDS:
        distinct: Dict[str, Any] = {}
        providers: List[Dict] = []
        for r in order:
            val = r.get(f)
            if val is None or val == "" or (isinstance(val, list) and not val):
                continue
            providers.append(_src_marker(r))
            distinct.setdefault(_field_key(val), val)
        if not distinct:
            merged[f] = None
            continue
        # 优先级：按 order 顺序取第一个出现的非空值
        for r in order:
            val = r.get(f)
            if val is None or val == "" or (isinstance(val, list) and not val):
                continue
            merged[f] = val
            break
        field_sources[f] = providers
        if len(distinct) > 1:
            conflicts.append({
                "field": f, "values": list(distinct.values()),
                "sources": providers, "resolution": "priority_order",
            })
    # abstract 取最长非空（可解释的确定性规则）
    abstracts = [(r, r.get("abstract")) for r in order if r.get("abstract")]
    merged["abstract"] = max(abstracts, key=lambda x: len(x[1].strip()))[1] if abstracts else None
    if abstracts:
        field_sources["abstract"] = [_src_marker(r) for r, _ in abstracts]
    # 标识符取并集（确定性排序）
    merged["identifiers"] = {}
    for r in order:
        for rid, val in sorted((r.get("identifiers") or {}).items()):
            merged["identifiers"].setdefault(rid, val)
    # 摘要保留各源逐字片段
    snapshots = [{"db": r["_db"], "source_file": r["_source_file"], "locator": r["_locator"],
                  "raw_snapshot": r["_snapshot"]} for r in order]
    db_set = sorted({r["_db"] for r in order})
    sources = [{
        "db": r["_db"], "source_file": r["_source_file"], "locator": r["_locator"],
        "query": _query_of(r),
    } for r in order]

    merged["record_id"] = "rec:%04d" % (idx + 1)
    merged["field_sources"] = field_sources
    merged["provenance"] = {
        "duplicate_of_source_records": [r["record_id"] for r in order],
        "source_count": len(order),
        "databases": db_set,
        "dedup_method": _reasons_between(cluster),
        "sources": sources,
        "raw_snapshots": snapshots,
    }
    # 相关性占位：由调用方基于研究问题补算，确定性
    merged["relevance_hint"] = {}
    return merged, conflicts


def _src_marker(r: Dict) -> Dict:
    return {"db": r["_db"], "source_file": r["_source_file"], "locator": r["_locator"]}


def _query_of(r: Dict) -> Optional[str]:
    return r.get("_request", {}).get("query") if isinstance(r.get("_request"), dict) else None


def _reasons_between(cluster: List[Dict]) -> List[str]:
    reasons: List[str] = []
    for i in range(len(cluster)):
        for j in range(i + 1, len(cluster)):
            reason = dedup_reason(cluster[i], cluster[j])
            if reason:
                reasons.append(reason)
    return sorted(set(reasons)) or ["single_record"]


# ---------------------------------------------------------------- 相关性/排序 ---

def build_terms(case: Dict) -> List[str]:
    """从 case.json 提取相关性词项（去停用词、去重、保序）。"""
    text_bits = []
    for k in ("research_question", "question", "task", "title", "description"):
        v = case.get(k)
        if isinstance(v, str):
            text_bits.append(v)
    for k in ("queries", "keywords", "search_terms", "terms"):
        v = case.get(k)
        if isinstance(v, list):
            text_bits.extend(str(x) for x in v)
        elif isinstance(v, str):
            text_bits.append(v)
    terms: List[str] = []
    seen: set = set()
    for tok in _PUNCT_RE.sub(" ", " ".join(text_bits)).lower().split():
        if len(tok) >= 3 and tok not in _STOPWORDS and tok not in seen:
            seen.add(tok)
            terms.append(tok)
    return terms


def relevance_score(rec: Dict, terms: List[str]) -> Dict:
    title = title_key(rec.get("title") or "")
    abstract = title_key(rec.get("abstract") or "")
    matched: List[str] = []
    score = 0
    for t in terms:
        if t in title:
            score += 2
            matched.append(t)
        elif t in abstract:
            score += 1
            matched.append(t)
    if score >= 3:
        hint = "high"
    elif score >= 1:
        hint = "medium"
    else:
        hint = "low"
    return {"score": score, "matched_terms": sorted(set(matched)), "method": "lexical_overlap", "label": hint}


# ---------------------------------------------------------------- 覆盖比较 ---

def _non_null_records(merged_list: List[Dict], field: str) -> int:
    n = 0
    for m in merged_list:
        v = m.get(field)
        if v is not None and v != "" and v != []:
            n += 1
    return n


def build_coverage(records: List[Dict], clusters: List[List[Dict]], merged_list: List[Dict]) -> Dict:
    per_db: Dict[str, Dict[str, Any]] = {}
    for r in records:
        db = r["_db"]
        d = per_db.setdefault(db, {"record_count": 0, "fields_present": set(), "unique_record_ids": []})
        d["record_count"] += 1
        d["unique_record_ids"].append(r.get("record_id", ""))
        for f in _MAJOR_FIELDS + ["abstract"]:
            v = r.get(f)
            if v is not None and v not in ("", []):
                d["fields_present"].add(f)
    per_database = []
    for db in sorted(per_db):
        d = per_db[db]
        per_database.append({
            "db": db, "record_count": d["record_count"],
            "fields_missing": sorted(set(_MAJOR_FIELDS + ["abstract"]) - d["fields_present"]),
            "unique_record_ids": sorted(set(x for x in d["unique_record_ids"] if x)),
        })
    single_db = []
    for idx, clu in enumerate(clusters):
        dbs = {r["_db"] for r in clu}
        if len(dbs) == 1 and clu and idx < len(merged_list):
            single_db.append(merged_list[idx]["record_id"])
    total_fields = _MAJOR_FIELDS + ["abstract"]
    systematically_missing = [f for f in total_fields if _non_null_records(merged_list, f) == 0]
    return {
        "databases": sorted(per_db),
        "per_database": per_database,
        "total_input_records": len(records),
        "merged_record_count": len(merged_list),
        "duplicates_merged": len(records) - len(merged_list),
        "records_in_single_db_only": sorted(single_db),
        "systematically_missing_fields": systematically_missing,
    }


# ---------------------------------------------------------------- 证据 / 骨架 ---

def build_evidence(merged_list: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for m in merged_list:
        for snap in m.get("provenance", {}).get("raw_snapshots", []):
            src = next((s for s in m["provenance"].get("sources", [])
                        if s.get("locator") == snap.get("locator")
                        and s.get("source_file") == snap.get("source_file")), {})
            out.append({
                "evidence_id": "ev:%s:%s" % (m["record_id"], safe_name(snap.get("locator", "loc"))),
                "record_id": m["record_id"],
                "type": "bibliographic_record",
                "db": snap.get("db"),
                "source_file": snap.get("source_file"),
                "locator": snap.get("locator"),
                "query": src.get("query"),
                "quote": snap.get("raw_snapshot"),
            })
    return out


def build_skeleton(case_id: str, started: str, work_dir: Optional[Path] = None) -> Dict:
    return {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": None,
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
        "change_log": [{
            "step": "skeleton", "status": "written", "at": started,
            "detail": "先落盘合法 partial 骨架，后续增量改进",
        }],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "screening": None,
        "runtime": {
            "started_at": started, "finished_at": None, "seconds": 0,
            "pipeline_version": VERSION, "python": "", "content_fingerprint": None,
        },
    }


def ensure_contract(res: Dict, contract: Any) -> None:
    """output_contract 可能是键列表或模板 dict；补齐缺失键为空值，不复制示例值。"""
    keys: List[str] = list(FIXED_FIELDS)
    if isinstance(contract, list):
        keys = [str(k) for k in contract] + keys
    elif isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()] + keys
    for k in keys:
        if k not in res:
            res[k] = None


def apply_decisions(res: Dict, decisions: Dict, at: str) -> List[str]:
    """合并决策文件（状态/排序/局限等），返回应用的关键字。"""
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
            else:
                res[key] = decisions[key]
                applied.append(key)
    if applied:
        res.setdefault("change_log", []).append({
            "step": "model_refine", "status": "applied", "at": at,
            "detail": "applied decisions: " + ", ".join(applied),
        })
    return applied


def compute_fingerprint(res: Dict) -> str:
    """对去除易变时刻字段后的结果求 SHA256，用于跨运行确定性校验。"""
    r = _strip_volatile(res)
    canonical = _stable_json(r)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_volatile(res: Dict) -> Dict:
    r = copy.deepcopy(res)
    rt = r.get("runtime")
    if isinstance(rt, dict):
        for k in ("started_at", "finished_at", "seconds", "content_fingerprint"):
            rt.pop(k, None)
    for entry in r.get("change_log", []):
        if isinstance(entry, dict) and "at" in entry:
            entry["at"] = ""
    return r


def parse_pdf_text(path: Path) -> Tuple[str, List[str]]:
    """尽力抽取 PDF 文本；缺 pdftotext 时降级不崩溃。"""
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=60,
        )
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace"), []
        return "", ["pdftotext exit %s: %s" % (proc.returncode, path.name)]
    except FileNotFoundError:
        return "", ["pdftotext missing; pdf listed by path only: %s" % path.name]
    except Exception as exc:  # noqa: BLE001
        return "", ["pdftotext failed: %s (%s)" % (path.name, exc)]