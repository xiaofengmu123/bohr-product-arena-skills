#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- 多数据库联合检索核心库（仅标准库，确定性，无网络）。

本模块把「发现输入 → 字段归一 → 去重 → 合并 → 覆盖比较 → 证据 → 不确定性」全部
下沉为可独立复现的纯函数。main.py 只负责编排与落盘，selftest.py 负责交付前校验。

设计取舍（相对朴素拼接的升级点）：
  1. 字段级溯源：每条合并记录不只有记录级 provenance，还保留每个字段来自哪个库、
     哪个原始片段（locator + 逐字 quote）以及取值优先级 tier。
  2. 双重置信度：记录级 identity_confidence（去重/合并是否可靠）与字段级
     field_confidence（值是否确切 / 归一 / 有歧义 / 冲突 / 缺失）。
  3. 缺失与未知分离：missing_metadata 显式区分「所有来源都缺失」与「存在但冲突」，
     绝不把缺失静默成 null 而不说明。
  4. 降级矩阵化：异常按类型映射到 errors/degradations/limitations，并给出 status。
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "2.0.0"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 输出契约的固定字段。若 case.json 提供 output_contract，其键会在此之上原样保留。
DEFAULT_CONTRACT: List[str] = [
    "case_id", "research_question", "search_scope", "candidates", "ranking",
    "evidence_records", "coverage", "empty_results", "conflicts",
    "missing_metadata", "permission_issues", "limitations", "status",
    "sources", "uncertainty", "change_log", "errors", "degradations",
    "pending_human", "runtime",
]

# 文件大小上限（字节），超限跳过并记录 degradations。
MAX_FILE_BYTES = 2 * 1024 * 1024

# 归一化后的字段取值优先级 tier；数值越小越可信。合并时按 (tier, source_file, locator) 稳定排序。
FIELD_PRIORITY: Dict[str, Dict[str, int]] = {
    "title":    {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "journal":  {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "year":     {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "volume":   {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "issue":    {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "pages":    {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "doi":      {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "pmid":     {"pubmed": 1, "europepmc": 2, "crossref": 3, "generic": 4},
    "pmcid":    {"europepmc": 1, "pubmed": 2, "crossref": 3, "generic": 4},
    "issn":     {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "publisher":{"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "abstract": {"europepmc": 1, "pubmed": 2, "crossref": 3, "generic": 4},
    "authors":  {"crossref": 1, "europepmc": 2, "pubmed": 3, "generic": 4},
    "doc_type": {"europepmc": 1, "pubmed": 2, "crossref": 3, "generic": 4},
}

# 记录级字段名集合（用于缺失字段统计）。
RECORD_FIELDS: Tuple[str, ...] = (
    "title", "authors", "year", "journal", "volume", "issue", "pages",
    "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher",
)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 输入发现与读取
# --------------------------------------------------------------------------- #
def discover_input_files(input_dir: Path) -> List[Path]:
    """递归发现输入目录下的所有文件；跳过目录与超限文件（记录在调用方）。"""
    if not input_dir.exists():
        return []
    out: List[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if p.is_file():
            out.append(p)
    return out


def classify_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".pdf":
        return "pdf"
    return "text"


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(path: Path) -> Tuple[Optional[Any], List[str]]:
    """读取 JSON；返回 (obj, degradations)。坏 JSON/坏编码/超限均不抛异常。"""
    degradations: List[str] = []
    try:
        size = path.stat().st_size
    except OSError:
        return None, ["stat_failed: %s" % path.name]
    if size > MAX_FILE_BYTES:
        return None, ["file_too_large(%d bytes): %s" % (size, path.name)]
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, ["read_failed: %s (%s)" % (path.name, e)]
    text = _decode(raw)
    try:
        return json.loads(text), degradations
    except json.JSONDecodeError as e:
        return None, ["bad_json: %s (%s)" % (path.name, e)]


def read_text(path: Path) -> Tuple[str, List[str]]:
    degradations: List[str] = []
    try:
        size = path.stat().st_size
    except OSError:
        return "", ["stat_failed: %s" % path.name]
    if size > MAX_FILE_BYTES:
        return "", ["file_too_large(%d bytes): %s" % (size, path.name)]
    try:
        raw = path.read_bytes()
    except OSError as e:
        return "", ["read_failed: %s (%s)" % (path.name, e)]
    return _decode(raw), degradations


def parse_pdf_text(path: Path) -> Tuple[str, List[str]]:
    """抽取 PDF 文本；缺 pdftotext 时只返回空文本并记录 degradations。"""
    degradations: List[str] = []
    if shutil.which("pdftotext") is None:
        return "", ["pdftotext_missing: %s (PDF 仅列路径，未抽文本)" % path.name]
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", ["pdftotext_failed: %s (%s)" % (path.name, e)]
    if proc.returncode != 0:
        return "", ["pdftotext_rc=%d: %s" % (proc.returncode, path.name)]
    return proc.stdout.decode("utf-8", errors="replace"), degradations


# --------------------------------------------------------------------------- #
# 响应分类与记录抽取
# --------------------------------------------------------------------------- #
def classify_response(obj: Any) -> str:
    """按结构识别数据库响应类型。"""
    if not isinstance(obj, dict):
        return "generic"
    if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("items"), list):
        return "crossref"
    if isinstance(obj.get("resultList"), dict) and isinstance(obj["resultList"].get("result"), list):
        return "europepmc"
    if "esearchresult" in obj:
        return "pubmed_esearch"
    if isinstance(obj.get("result"), dict):
        result = obj["result"]
        if "uids" in result and isinstance(result["uids"], list):
            return "pubmed_esummary"
    return "generic"


def canonical_db(db: str) -> str:
    """把细分响应类型归一到稳定库名（用于优先级、sources、coverage）。"""
    if db in ("pubmed_esummary", "pubmed_esearch"):
        return "pubmed"
    return db


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def extract_records(obj: Any) -> List[Tuple[Dict, str]]:
    """从数据库响应抽取文献记录；返回 [(原始 dict, locator), ...]。

    locator 为可复现的 JSON Pointer 风格路径（如 message.items[0]）。
    """
    out: List[Tuple[Dict, str]] = []
    if not isinstance(obj, dict):
        return out

    db = classify_response(obj)
    if db == "crossref":
        items = obj["message"]["items"]
        for i, it in enumerate(items):
            if isinstance(it, dict):
                out.append((it, "message.items[%d]" % i))
    elif db == "europepmc":
        items = obj["resultList"]["result"]
        for i, it in enumerate(items):
            if isinstance(it, dict):
                out.append((it, "resultList.result[%d]" % i))
    elif db == "pubmed_esummary":
        result = obj["result"]
        uids = result.get("uids", [])
        for i, uid in enumerate(uids):
            rec = result.get(str(uid))
            if isinstance(rec, dict):
                out.append((rec, "result[%s]" % uid))
            else:
                out.append(({"uid": uid}, "result.uids[%d]" % i))
    elif db == "pubmed_esearch":
        # esearch 只给 idlist，无完整文献记录；记为 identifier-only 记录。
        idlist = obj.get("esearchresult", {}).get("idlist", [])
        for i, uid in enumerate(idlist):
            out.append(({"uid": uid}, "esearchresult.idlist[%d]" % i))
    else:
        # generic：遍历常见列表键，否则把顶层对象本身当作单条记录。
        for key in ("records", "results", "data", "items", "papers", "publications", "documents"):
            val = obj.get(key)
            if isinstance(val, list):
                for i, it in enumerate(val):
                    if isinstance(it, dict):
                        out.append((it, "%s[%d]" % (key, i)))
                return out
        # 顶层对象看起来像一条记录
        if any(k in obj for k in ("title", "doi", "pmid", "id", "authors")):
            out.append((obj, "$"))
    return out


def extract_request(obj: Any) -> Dict[str, Any]:
    """从响应里尽力抽取请求/查询线索（不回填虚构值）。"""
    req: Dict[str, Any] = {}
    if not isinstance(obj, dict):
        return req
    db = classify_response(obj)
    if db == "crossref":
        m = obj.get("message", {})
        if isinstance(m.get("query"), dict):
            req["query"] = m["query"]
        for k in ("type", "sort", "rows", "start"):
            if k in m:
                req[k] = m[k]
    elif db == "europepmc":
        rl = obj.get("resultList", {})
        if "query" in rl:
            req["query"] = rl["query"]
        if "hitCount" in rl:
            req["hit_count"] = rl["hitCount"]
        if isinstance(obj.get("request"), dict):
            req.update(obj["request"])
    elif db in ("pubmed_esearch", "pubmed_esummary"):
        es = obj.get("esearchresult", {})
        if "querytranslation" in es:
            req["query_translation"] = es["querytranslation"]
        if "count" in es:
            req["count"] = es["count"]
    return req


# --------------------------------------------------------------------------- #
# 字段归一化
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _authors_from_crossref(raw: Dict) -> List[str]:
    authors: List[str] = []
    for a in _as_list(raw.get("author")):
        if not isinstance(a, dict):
            continue
        given = _clean(a.get("given")) or ""
        family = _clean(a.get("family")) or ""
        name = (family + " " + given).strip()
        if name:
            authors.append(name)
    return authors


def _authors_from_europepmc(raw: Dict) -> List[str]:
    authors: List[str] = []
    for a in _as_list(raw.get("authorList", {}).get("author")):
        if not isinstance(a, dict):
            continue
        first = _clean(a.get("firstName")) or ""
        last = _clean(a.get("lastName")) or ""
        name = (last + " " + first).strip()
        if name:
            authors.append(name)
    if not authors and _clean(raw.get("authorString")):
        authors = [s.strip() for s in str(raw.get("authorString")).split(",") if s.strip()]
    return authors


def _authors_from_pubmed(raw: Dict) -> List[str]:
    authors: List[str] = []
    for a in _as_list(raw.get("authors")):
        if isinstance(a, dict) and _clean(a.get("name")):
            authors.append(a["name"].strip())
    return authors


def _authors_from_generic(raw: Dict) -> List[str]:
    authors: List[str] = []
    for a in _as_list(raw.get("authors")):
        if isinstance(a, dict):
            name = _clean(a.get("name")) or _clean(a.get("full_name")) or _clean(a.get("fullName"))
            if name:
                authors.append(name)
            continue
        if isinstance(a, str) and a.strip():
            authors.append(a.strip())
    return authors


def _year_from_crossref(raw: Dict) -> Optional[str]:
    for k in ("published-print", "published-online", "published", "issued"):
        node = raw.get(k)
        if isinstance(node, dict):
            dp = node.get("date-parts") or node.get("date_parts")
            if isinstance(dp, list) and dp:
                first = _first(dp)
                if isinstance(first, list) and first:
                    v = _clean(first[0])
                    if v and v.isdigit():
                        return v
                if isinstance(first, dict):
                    v = _clean(first.get("year"))
                    if v:
                        return v
    return None


def _identifiers_from(raw: Dict, db: str) -> Dict[str, Any]:
    ids: Dict[str, Any] = {}
    doi = _clean(raw.get("doi") or raw.get("DOI"))
    if doi:
        ids["doi"] = doi.lower()
    pmid = _clean(raw.get("pmid") or raw.get("PMID"))
    if pmid:
        ids["pmid"] = pmid
    pmcid = _clean(raw.get("pmcid") or raw.get("PMCID"))
    if pmcid:
        ids["pmcid"] = pmcid.upper().replace("PMC", "")
    if db == "pubmed" or (db == "europepmc" and not pmid):
        pass
    return ids


def _pubmed_ids(raw: Dict) -> Dict[str, Any]:
    ids: Dict[str, Any] = {}
    uid = _clean(raw.get("uid"))
    if uid:
        ids["pmid"] = uid
    for art in _as_list(raw.get("articleids")):
        if isinstance(art, dict):
            idtype = str(art.get("idtype", "")).lower()
            value = _clean(art.get("value"))
            if not value:
                continue
            if idtype in ("pubmed", "pmid"):
                ids["pmid"] = value
            elif idtype == "pmc":
                ids["pmcid"] = value.upper().replace("PMC", "")
            elif idtype == "doi":
                ids["doi"] = value.lower()
    return ids


def _pubmed_year(raw: Dict) -> Optional[str]:
    for k in ("pubdate", "sortpubdate", "epubdate"):
        s = _clean(raw.get(k))
        if not s:
            continue
        m = re.search(r"\b(19|20)\d{2}\b", s)
        if m:
            return m.group(0)
    return None


def _pubmed_journal(raw: Dict) -> Optional[str]:
    return _clean(raw.get("fulljournalname") or raw.get("source") or raw.get("journal"))


def _pubmed_pages(raw: Dict) -> Optional[str]:
    return _clean(raw.get("pages") or raw.get("elocationid"))


def normalize_record(raw: Dict, db: str) -> Dict[str, Any]:
    """把一条原始记录归一化为统一字段；值缺失返回 None/[]。"""
    rec: Dict[str, Any] = {}

    if db == "crossref":
        title = _first(raw.get("title"))
        rec["title"] = _clean(title)
        rec["authors"] = _authors_from_crossref(raw)
        rec["year"] = _year_from_crossref(raw)
        ct = _first(raw.get("container-title"))
        rec["journal"] = _clean(ct)
        rec["volume"] = _clean(raw.get("volume"))
        rec["issue"] = _clean(raw.get("issue"))
        rec["pages"] = _clean(raw.get("page"))
        ids = _identifiers_from(raw, db)
        rec["doi"] = ids.get("doi")
        rec["pmid"] = ids.get("pmid")
        rec["pmcid"] = ids.get("pmcid")
        issn = _as_list(raw.get("ISSN")) or _as_list(raw.get("issn"))
        rec["issn"] = [s.strip() for s in issn if isinstance(s, str) and s.strip()]
        rec["doc_type"] = _clean(raw.get("type"))
        rec["abstract"] = _clean(raw.get("abstract"))
        rec["publisher"] = _clean(raw.get("publisher"))
    elif db == "europepmc":
        rec["title"] = _clean(raw.get("title"))
        rec["authors"] = _authors_from_europepmc(raw)
        rec["year"] = _clean(raw.get("pubYear"))
        rec["journal"] = _clean(raw.get("journalTitle"))
        rec["volume"] = _clean(raw.get("journalVolume"))
        rec["issue"] = _clean(raw.get("issue"))
        rec["pages"] = _clean(raw.get("pageInfo"))
        ids = _identifiers_from(raw, db)
        rec["doi"] = ids.get("doi")
        rec["pmid"] = ids.get("pmid")
        rec["pmcid"] = ids.get("pmcid")
        issn = _as_list(raw.get("journalIssn"))
        rec["issn"] = [s.strip() for s in issn if isinstance(s, str) and s.strip()]
        pt = _clean(raw.get("pubType"))
        rec["doc_type"] = pt.split(",")[0].strip() if pt else None
        rec["abstract"] = _clean(raw.get("abstractText"))
        rec["publisher"] = _clean(raw.get("publisher"))
    elif db in ("pubmed_esummary", "pubmed_esearch"):
        rec["title"] = _clean(raw.get("title"))
        rec["authors"] = _authors_from_pubmed(raw)
        rec["year"] = _pubmed_year(raw)
        rec["journal"] = _pubmed_journal(raw)
        rec["volume"] = _clean(raw.get("volume"))
        rec["issue"] = _clean(raw.get("issue"))
        rec["pages"] = _pubmed_pages(raw)
        ids = _pubmed_ids(raw)
        rec["doi"] = ids.get("doi")
        rec["pmid"] = ids.get("pmid")
        rec["pmcid"] = ids.get("pmcid")
        issn = _as_list(raw.get("issn"))
        rec["issn"] = [s.strip() for s in issn if isinstance(s, str) and s.strip()]
        pt = raw.get("pubtype")
        if isinstance(pt, list) and pt:
            rec["doc_type"] = _clean(_first(pt))
        else:
            rec["doc_type"] = _clean(pt)
        rec["abstract"] = None  # esummary 通常不含摘要
        rec["publisher"] = None
    else:
        rec["title"] = _clean(raw.get("title"))
        rec["authors"] = _authors_from_generic(raw)
        rec["year"] = _clean(raw.get("year") or raw.get("publication_year") or raw.get("pubyear"))
        rec["journal"] = _clean(raw.get("journal") or raw.get("journal_title") or raw.get("venue"))
        rec["volume"] = _clean(raw.get("volume"))
        rec["issue"] = _clean(raw.get("issue") or raw.get("number"))
        rec["pages"] = _clean(raw.get("pages") or raw.get("page"))
        ids = _identifiers_from(raw, db)
        rec["doi"] = ids.get("doi")
        rec["pmid"] = ids.get("pmid")
        rec["pmcid"] = ids.get("pmcid")
        issn = _as_list(raw.get("issn") or raw.get("ISSN"))
        rec["issn"] = [s.strip() for s in issn if isinstance(s, str) and s.strip()]
        rec["doc_type"] = _clean(raw.get("type") or raw.get("doc_type") or raw.get("publication_type"))
        rec["abstract"] = _clean(raw.get("abstract") or raw.get("summary"))
        rec["publisher"] = _clean(raw.get("publisher"))

    # 统一空值
    for k in RECORD_FIELDS:
        if k not in rec:
            rec[k] = None
        if rec[k] == "" :
            rec[k] = None
    if rec["authors"] is None:
        rec["authors"] = []
    if rec["issn"] is None:
        rec["issn"] = []
    return rec


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #
def _norm_title(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _first_author_surname(authors: Sequence[str]) -> str:
    if not authors:
        return ""
    return authors[0].split()[-1].lower() if authors[0].split() else authors[0].lower()


def record_identity_keys(rec: Dict[str, Any]) -> Dict[str, str]:
    """为单条归一化记录生成去重身份键。"""
    keys: Dict[str, str] = {}
    if rec.get("doi"):
        keys["doi"] = "doi:" + str(rec["doi"]).lower()
    if rec.get("pmid"):
        keys["pmid"] = "pmid:" + str(rec["pmid"])
    if rec.get("pmcid"):
        keys["pmcid"] = "pmcid:" + str(rec["pmcid"])
    t = _norm_title(rec.get("title"))
    y = str(rec.get("year") or "")
    fa = _first_author_surname(rec.get("authors") or [])
    if t and y:
        keys["title_year"] = "ty:" + t + "|" + y
        if fa:
            keys["title_fa_year"] = "tfa:" + t + "|" + fa + "|" + y
    return keys


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def cluster_records(records: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """确定性去重：标识符精确 > 标题+年份 > 标题+一作+年份 > 标题模糊(>=0.90 同年)。

    返回簇列表；每个簇内记录按 (priority, source_file, locator) 稳定排序。
    """
    clusters: List[List[Dict[str, Any]]] = []
    used: set = set()

    def key_priority(rec: Dict[str, Any]) -> int:
        db = rec.get("_db", "generic")
        # 使用 doi 字段优先级近似（越小越可信），缺失用 generic 的 4。
        return FIELD_PRIORITY.get("doi", {}).get(db, 4)

    def sort_key(rec: Dict[str, Any]) -> Tuple[Any, ...]:
        return (key_priority(rec), rec.get("_source_file", ""), rec.get("_locator", ""))

    ordered = sorted(records, key=sort_key)

    # 第 1 轮：标识符精确匹配（doi / pmid / pmcid）。
    for rec in ordered:
        if id(rec) in used:
            continue
        keys = record_identity_keys(rec)
        matched: List[Dict[str, Any]] = [rec]
        used.add(id(rec))
        changed = True
        while changed:
            changed = False
            for other in ordered:
                if id(other) in used:
                    continue
                okeys = record_identity_keys(other)
                for k in ("doi", "pmid", "pmcid"):
                    if keys.get(k) and okeys.get(k) and keys[k] == okeys[k]:
                        matched.append(other)
                        used.add(id(other))
                        for kk, vv in okeys.items():
                            keys.setdefault(kk, vv)
                        changed = True
                        break
        clusters.append(sorted(matched, key=sort_key))

    # 第 2 轮：标题+一作+年份 / 标题+年份。
    for rec in ordered:
        if id(rec) in used:
            continue
        keys = record_identity_keys(rec)
        matched = [rec]
        used.add(id(rec))
        for other in ordered:
            if id(other) in used:
                continue
            okeys = record_identity_keys(other)
            if keys.get("title_fa_year") and okeys.get("title_fa_year") and keys["title_fa_year"] == okeys["title_fa_year"]:
                matched.append(other)
                used.add(id(other))
        clusters.append(sorted(matched, key=sort_key))

    # 第 3 轮：标题模糊 (>=0.90) 且同年。
    for rec in ordered:
        if id(rec) in used:
            continue
        matched = [rec]
        used.add(id(rec))
        rt = _norm_title(rec.get("title"))
        ry = str(rec.get("year") or "")
        for other in ordered:
            if id(other) in used:
                continue
            ot = _norm_title(other.get("title"))
            oy = str(other.get("year") or "")
            if (rt and ot and ry and oy and ry == oy
                    and _similarity(rt, ot) >= 0.90):
                matched.append(other)
                used.add(id(other))
        clusters.append(sorted(matched, key=sort_key))

    # 第 4 轮：剩余单条成簇。
    for rec in ordered:
        if id(rec) in used:
            continue
        used.add(id(rec))
        clusters.append([rec])

    # 稳定排序簇：按簇内第一条的 (doi/pmid/pmcid/title) 排序。
    def cluster_sort_key(cluster: List[Dict[str, Any]]) -> str:
        first = cluster[0]
        return (first.get("doi") or first.get("pmid") or first.get("pmcid")
                or _norm_title(first.get("title")) or "").lower()
    return sorted(clusters, key=cluster_sort_key)


# --------------------------------------------------------------------------- #
# 合并
# --------------------------------------------------------------------------- #
def _priority_for(db: str, field: str) -> int:
    return FIELD_PRIORITY.get(field, {}).get(db, 5)


def merge_cluster(cluster: List[Dict[str, Any]], cluster_id: str) -> Dict[str, Any]:
    """合并同一簇内记录：字段按优先级取值，冲突显式记录。"""
    field_sources: Dict[str, List[Dict[str, Any]]] = {}
    conflicts: List[Dict[str, Any]] = []
    missing_fields: List[str] = []

    # 每个字段收集 (value, db, source_file, locator, quote)，按优先级排序
    for field in RECORD_FIELDS:
        candidates: List[Dict[str, Any]] = []
        for rec in cluster:
            db = rec.get("_db", "generic")
            value = rec.get(field)
            if value in (None, [], ""):
                continue
            raw = rec.get("_raw", {})
            quote = _verbatim_fragment(raw, field)
            candidates.append({
                "value": value, "db": db,
                "source_file": rec.get("_source_file", ""),
                "locator": rec.get("_locator", ""),
                "quote": quote,
                "priority": _priority_for(db, field),
            })
        if not candidates:
            missing_fields.append(field)
            field_sources[field] = []
            continue
        candidates.sort(key=lambda c: (c["priority"], c["source_file"], c["locator"]))
        best = candidates[0]
        # 冲突检测：同优先级（严格同 tier）但值不同 → 冲突
        conflicting = [c for c in candidates[1:] if c["priority"] == best["priority"]
                       and _values_differ(c["value"], best["value"])]
        if conflicting:
            conflicts.append({
                "field": field,
                "resolved_value": best["value"],
                "resolution_rule": "priority_tier_equal_then_stable_source_order",
                "candidates": [
                    {"value": c["value"], "db": c["db"], "source_file": c["source_file"],
                     "locator": c["locator"], "quote": c["quote"]}
                    for c in [best] + conflicting
                ],
            })
        field_sources[field] = candidates

    # 生成合并记录值
    merged: Dict[str, Any] = {
        "record_id": cluster_id,
        "title": None, "authors": [], "year": None, "journal": None,
        "volume": None, "issue": None, "pages": None, "doi": None,
        "pmid": None, "pmcid": None, "issn": [], "doc_type": None,
        "abstract": None, "publisher": None,
    }
    for field in RECORD_FIELDS:
        src = field_sources[field]
        if not src:
            merged[field] = None
            continue
        best = src[0]
        if field in ("authors", "issn"):
            # 列表字段：按优先级取第一条的列表，但保留其来源。
            merged[field] = list(best["value"]) if isinstance(best["value"], list) else [best["value"]]
        else:
            merged[field] = best["value"]
    if merged["authors"] is None:
        merged["authors"] = []
    if merged["issn"] is None:
        merged["issn"] = []

    # 字段级来源（含逐字片段）与置信度
    field_sources_out: Dict[str, List[Dict[str, Any]]] = {}
    field_confidence: Dict[str, str] = {}
    for field in RECORD_FIELDS:
        srcs = field_sources[field]
        if not srcs:
            field_confidence[field] = "missing"
            field_sources_out[field] = []
            continue
        best = srcs[0]
        others = srcs[1:]
        if any(_values_differ(o["value"], best["value"]) for o in others
               if o["priority"] == best["priority"]):
            field_confidence[field] = "conflicting"
        elif len({_repr_value(o["value"]) for o in srcs}) > 1:
            field_confidence[field] = "ambiguous"
        elif _is_normalized(best["quote"], best["value"]):
            field_confidence[field] = "normalized"
        else:
            field_confidence[field] = "exact"
        field_sources_out[field] = [
            {"db": s["db"], "source_file": s["source_file"], "locator": s["locator"],
             "quote": s["quote"], "priority": s["priority"]}
            for s in srcs
        ]

    # 记录级身份置信度
    identity_confidence = _identity_confidence(cluster)

    db_sources = sorted({rec.get("_db", "generic") for rec in cluster})
    merged["identifiers"] = {
        k: v for k, v in {
            "doi": merged["doi"], "pmid": merged["pmid"], "pmcid": merged["pmcid"],
        }.items() if v
    }
    merged["field_sources"] = field_sources_out
    merged["field_confidence"] = field_confidence
    merged["identity_confidence"] = identity_confidence
    merged["conflicting_fields"] = [c["field"] for c in conflicts]
    merged["missing_fields"] = missing_fields
    merged["provenance"] = {
        "cluster_id": cluster_id,
        "record_count": len(cluster),
        "source_dbs": db_sources,
        "matched_by": _matched_by(cluster),
        "merge_strategy": "identifier_exact > title_year > title_firstauthor_year > title_fuzzy(>=0.90)",
        "members": [
            {"source_file": r.get("_source_file", ""), "locator": r.get("_locator", ""),
             "db": r.get("_db", "generic")}
            for r in cluster
        ],
    }
    merged["raw_snapshots"] = [
        {"source_file": r.get("_source_file", ""), "locator": r.get("_locator", ""),
         "db": r.get("_db", "generic"), "snapshot": _snapshot(r.get("_raw", {}))}
        for r in cluster
    ]
    merged["conflicts"] = conflicts
    return merged


def _values_differ(a: Any, b: Any) -> bool:
    return _repr_value(a) != _repr_value(b)


def _repr_value(v: Any) -> str:
    if isinstance(v, list):
        return "|".join(str(x).strip().lower() for x in v)
    return str(v).strip().lower()


def _is_normalized(quote: str, value: Any) -> bool:
    """值经过去空白/大小写等归一后与原始片段不完全逐字一致时视为 normalized。"""
    if value is None:
        return False
    q = quote.strip().lower()
    v = str(value).strip().lower()
    return q != v and (q.replace(" ", "") == v.replace(" ", "") or q in v or v in q)


def _snapshot(raw: Dict[str, Any], limit: int = 400) -> str:
    try:
        s = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        s = str(raw)
    return s[:limit]


def _verbatim_fragment(raw: Dict[str, Any], field: str) -> str:
    """从原始响应里取该字段的逐字片段；取不到返回空字符串。

    PubMed esummary 把 doi/pmid/pmcid 放在 articleids 列表里，因此对这三个字段
    额外回退到 articleids 中对应 idtype 的 value。
    """
    if not isinstance(raw, dict):
        return ""
    candidates = _raw_keys_for_field(field)
    for k in candidates:
        if k in raw and raw[k] is not None:
            val = raw[k]
            if isinstance(val, (list, dict)):
                try:
                    return json.dumps(val, ensure_ascii=False)
                except (TypeError, ValueError):
                    return str(val)
            return str(val)
    # PubMed articleids 回退
    if field in ("doi", "pmid", "pmcid"):
        idtype_map = {"doi": "doi", "pmid": "pubmed", "pmcid": "pmc"}
        want = idtype_map[field]
        for art in _as_list(raw.get("articleids")):
            if isinstance(art, dict):
                idtype = str(art.get("idtype", "")).lower()
                if idtype in (want,) or (field == "pmid" and idtype in ("pubmed", "pmid")):
                    if art.get("value") is not None:
                        return str(art["value"])
    return ""


def _raw_keys_for_field(field: str) -> List[str]:
    table = {
        "title": ["title", "Title"],
        "journal": ["container-title", "journalTitle", "fulljournalname", "source", "journal"],
        "year": ["published-print", "published-online", "published", "issued", "pubYear", "pubdate", "sortpubdate"],
        "volume": ["volume", "journalVolume"],
        "issue": ["issue"],
        "pages": ["page", "pageInfo", "pages", "elocationid"],
        "doi": ["doi", "DOI"],
        "pmid": ["pmid", "PMID", "uid"],
        "pmcid": ["pmcid", "PMCID"],
        "issn": ["ISSN", "issn", "journalIssn"],
        "doc_type": ["type", "pubType", "pubtype"],
        "abstract": ["abstract", "abstractText"],
        "publisher": ["publisher"],
        "authors": ["author", "authors", "authorList"],
    }
    return table.get(field, [field])


def _identity_confidence(cluster: List[Dict[str, Any]]) -> str:
    ids = set()
    for rec in cluster:
        for v in (rec.get("doi"), rec.get("pmid"), rec.get("pmcid")):
            if v:
                ids.add(str(v))
    if ids:
        return "high"
    titles = {_norm_title(rec.get("title")) for rec in cluster if rec.get("title")}
    years = {str(rec.get("year")) for rec in cluster if rec.get("year")}
    if len(titles) == 1 and len(years) <= 1:
        return "medium"
    return "low"


def _matched_by(cluster: List[Dict[str, Any]]) -> str:
    if len(cluster) == 1:
        return "single_source"
    for rec in cluster:
        if rec.get("doi"):
            return "doi_exact" if any(r.get("doi") for r in cluster) else "identifier"
    if any(rec.get("pmid") for rec in cluster):
        return "pmid_exact"
    if any(rec.get("pmcid") for rec in cluster):
        return "pmcid_exact"
    t = _norm_title(cluster[0].get("title"))
    y = str(cluster[0].get("year") or "")
    fa = _first_author_surname(cluster[0].get("authors") or [])
    for rec in cluster[1:]:
        if _norm_title(rec.get("title")) != t:
            return "title_fuzzy"
    if fa:
        return "title_firstauthor_year"
    return "title_year"


# --------------------------------------------------------------------------- #
# 覆盖比较
# --------------------------------------------------------------------------- #
def compute_coverage(sources: List[Dict[str, Any]], clusters: List[Dict[str, Any]],
                     total_input_records: int) -> Dict[str, Any]:
    """按库统计字段覆盖与仅单库记录。"""
    per_db: Dict[str, Dict[str, int]] = {}
    db_record_count: Dict[str, int] = {}
    for src in sources:
        db = src.get("db", "generic")
        if db not in per_db:
            per_db[db] = {f: 0 for f in RECORD_FIELDS}
            db_record_count[db] = 0
        db_record_count[db] += int(src.get("record_count", 0))

    for rec in clusters:
        for db in rec.get("provenance", {}).get("source_dbs", []):
            if db not in per_db:
                per_db[db] = {f: 0 for f in RECORD_FIELDS}
            for f in RECORD_FIELDS:
                # 该库对这条记录至少贡献了该字段的值（粗略按来源存在与否）
                if any(s["db"] == db for s in rec.get("field_sources", {}).get(f, [])):
                    per_db[db][f] += 1

    fields_missing_per_db: Dict[str, List[str]] = {}
    for db, counts in per_db.items():
        total = db_record_count.get(db, 0)
        missing = [f for f in RECORD_FIELDS if counts[f] == 0] if total == 0 else \
                  [f for f in RECORD_FIELDS if counts[f] < total]
        fields_missing_per_db[db] = missing

    single_db_only: List[Dict[str, Any]] = []
    for rec in clusters:
        dbs = rec.get("provenance", {}).get("source_dbs", [])
        if len(dbs) == 1:
            single_db_only.append({"record_id": rec.get("record_id"),
                                   "db": dbs[0],
                                   "title": rec.get("title")})

    merged_count = len(clusters)
    dup_merged = max(0, total_input_records - merged_count)
    return {
        "databases": sorted(per_db.keys()),
        "per_database": per_db,
        "fields_missing_per_database": fields_missing_per_db,
        "total_input_records": total_input_records,
        "merged_record_count": merged_count,
        "duplicates_merged": dup_merged,
        "records_in_single_db_only": single_db_only,
        "systematically_missing_fields": sorted({
            f for f in RECORD_FIELDS
            if all(f in missing for missing in fields_missing_per_db.values())
            if fields_missing_per_db
        }),
    }


# --------------------------------------------------------------------------- #
# 相关性提示（确定性词项命中）
# --------------------------------------------------------------------------- #
def _tokenize(text: Optional[str]) -> List[str]:
    if not text:
        return []
    s = text.lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return [t for t in s.split() if len(t) > 1]


def relevance_hint(research_question: str, queries: List[str], rec: Dict[str, Any]) -> Dict[str, Any]:
    """确定性词项命中；不替代模型判断，只提供可复核的初始提示。"""
    qterms = _tokenize(research_question) + [t for q in queries for t in _tokenize(q)]
    qterms_set = set(qterms)
    text = " ".join(str(rec.get(k) or "") for k in ("title", "abstract", "journal"))
    tterms = set(_tokenize(text))
    hits = sorted(qterms_set & tterms) if qterms_set else []
    hit_count = len(hits)
    if hit_count == 0:
        level = "unknown"
    elif hit_count >= 3:
        level = "high"
    elif hit_count >= 1:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "hit_count": hit_count, "matched_terms": hits,
            "query_terms": sorted(qterms_set), "deterministic": True}


# --------------------------------------------------------------------------- #
# 证据记录
# --------------------------------------------------------------------------- #
def build_evidence_records(sources: List[Dict[str, Any]], clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    i = 0
    for src in sources:
        i += 1
        out.append({
            "evidence_id": "ev:%04d" % i,
            "type": "source_manifest",
            "db": src.get("db", "generic"),
            "source_file": src.get("path", ""),
            "locator": None,
            "quote": "source file listed with record_count=%s" % src.get("record_count", 0),
        })
    for rec in clusters:
        # 每个字段的第一个来源作为逐字证据
        for field in RECORD_FIELDS:
            srcs = rec.get("field_sources", {}).get(field, [])
            if srcs:
                s = srcs[0]
                i += 1
                out.append({
                    "evidence_id": "ev:%04d" % i,
                    "type": "field_value",
                    "db": s["db"],
                    "record_id": rec.get("record_id"),
                    "field": field,
                    "source_file": s["source_file"],
                    "locator": s["locator"],
                    "quote": s["quote"],
                })
    return out


# --------------------------------------------------------------------------- #
# 不确定性
# --------------------------------------------------------------------------- #
def build_uncertainty(clusters: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                      degradations: List[str], conflicts_global: List[Dict[str, Any]],
                      missing_global: List[Dict[str, Any]]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    i = 0
    for rec in clusters:
        for field in RECORD_FIELDS:
            conf = rec.get("field_confidence", {}).get(field, "missing")
            if conf in ("conflicting", "ambiguous", "missing"):
                i += 1
                items.append({
                    "uncertainty_id": "u:%04d" % i,
                    "scope": "field",
                    "record_id": rec.get("record_id"),
                    "field": field,
                    "kind": conf,
                    "detail": "field %s has confidence=%s" % (field, conf),
                    "evidence": [
                        {"source_file": s["source_file"], "locator": s["locator"], "quote": s["quote"]}
                        for s in rec.get("field_sources", {}).get(field, [])[:3]
                    ],
                    "impact": "high" if conf == "conflicting" else "medium",
                    "resolution": "documented" if conf == "missing" else "pending_human",
                })
        if rec.get("identity_confidence") in ("medium", "low"):
            i += 1
            items.append({
                "uncertainty_id": "u:%04d" % i,
                "scope": "candidate",
                "record_id": rec.get("record_id"),
                "kind": "dedup_inference",
                "detail": "merged by %s; identity_confidence=%s" % (
                    rec.get("provenance", {}).get("matched_by", "unknown"),
                    rec.get("identity_confidence")),
                "evidence": [],
                "impact": "medium",
                "resolution": "pending_human" if rec.get("identity_confidence") == "low" else "documented",
            })
    for d in degradations:
        i += 1
        items.append({
            "uncertainty_id": "u:%04d" % i,
            "scope": "global",
            "kind": "degraded",
            "detail": d,
            "evidence": [],
            "impact": "medium",
            "resolution": "documented",
        })
    note = ("字段级置信度标记 exact/normalized/ambiguous/conflicting/missing；"
            "记录级 identity_confidence 标记去重推断的可信度；"
            "所有不确定项均可按 source_file+locator 回溯。")
    return {"items": items, "note": note}


def build_missing_metadata(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in clusters:
        for f in rec.get("missing_fields", []):
            out.append({
                "record_id": rec.get("record_id"),
                "field": f,
                "reason": "absent_in_all_sources",
                "detail": "field not reported by any source record in cluster",
            })
        for f in rec.get("conflicting_fields", []):
            out.append({
                "record_id": rec.get("record_id"),
                "field": f,
                "reason": "present_but_conflicting",
                "detail": "same-tier sources disagree; resolution recorded in conflicts",
            })
    return out
