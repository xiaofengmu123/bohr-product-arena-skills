#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py -- 多库联合检索核心库（仅标准库，确定性，无网络）。

被 main.py 与 selftest.py 导入。职责：
  1. 输入发现与分类（json / pdf / text），坏文件降级不崩溃
  2. 异构数据库响应识别（Crossref / EuropePMC / PubMed / 出版社 / 其他）
  3. 跨库字段归一（作者 / 期刊 / 年份 / 标识符 / 文献类型）
  4. 确定性去重（DOI/PMID/PMCID 精确 -> 标题+一作+年份 -> 标题模糊 >=0.90）
  5. 合并与冲突消解（按字段优先级保留主值，冲突全部记录）
  6. 覆盖比较（每库 record_count / fields_missing / single_db_only）
  7. 证据溯源（source_file + locator + 逐字 quote）
  8. 阶段预算（总 900s / 64 请求，P0 前 3 分钟先落盘 partial 骨架）

确定性约定：输入文件按字典序发现；去重簇按首记录输入顺序稳定；
输出 JSON 用 ensure_ascii=False、固定键序；时间戳只写入 runtime/change_log，
供 selftest 做去时间戳后的逐字节比较。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "1.1.0"
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

TOTAL_BUDGET_SECONDS = 900
TOTAL_MODEL_CALLS = 64
MAX_RESPONSE_TOKENS = 16384
MAX_FILE_BYTES = 2 * 1024 * 1024
PDF_SNAPSHOT_CHARS = 4000

# 阶段预算：P0 前 3 分钟先落盘 partial 骨架，之后只做增量改进。
PHASES: List[Dict[str, Any]] = [
    {"id": "P0_skeleton",   "name": "落盘骨架",     "budget_seconds": 180, "max_model_calls": 2,
     "desc": "读 /input/case.json 与 /input/task.md 后立即写合法 partial 骨架"},
    {"id": "P1_parse_fuse", "name": "解析与融合",   "budget_seconds": 420, "max_model_calls": 16,
     "desc": "发现材料、分类响应、字段归一、去重、合并、覆盖、证据"},
    {"id": "P2_rank",       "name": "相关性与排序", "budget_seconds": 180, "max_model_calls": 32,
     "desc": "按研究问题判相关性，写 decisions.json 后合并仲裁"},
    {"id": "P3_selfcheck",  "name": "自检收尾",     "budget_seconds": 120, "max_model_calls": 14,
     "desc": "自检逐项勾选，修复 FAIL 后重跑"},
]

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

# 字段取值优先级：按 db 顺序降级；同 db 内按输入顺序先到先得。
FIELD_PRIORITY: Dict[str, List[str]] = {
    "title":     ["crossref", "europepmc", "pubmed", "publisher"],
    "authors":   ["crossref", "europepmc", "pubmed", "publisher"],
    "year":      ["crossref", "pubmed", "europepmc", "publisher"],
    "journal":   ["crossref", "europepmc", "pubmed", "publisher"],
    "volume":    ["crossref", "europepmc", "pubmed", "publisher"],
    "issue":     ["crossref", "europepmc", "pubmed", "publisher"],
    "pages":     ["crossref", "europepmc", "pubmed", "publisher"],
    "doi":       ["crossref", "europepmc", "pubmed", "publisher"],
    "pmid":      ["pubmed", "europepmc", "publisher"],
    "pmcid":     ["europepmc", "pubmed", "publisher"],
    "issn":      ["crossref", "europepmc", "pubmed", "publisher"],
    "doc_type":  ["crossref", "pubmed", "europepmc", "publisher"],
    "publisher": ["crossref", "publisher", "europepmc", "pubmed"],
    "abstract":  ["europepmc", "pubmed", "crossref", "publisher"],
}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _is_hidden(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.split("/"))


def discover_input_files(input_dir: Path) -> List[Path]:
    """按字典序发现输入文件；超过 2MB 或隐藏文件跳过（记录到调用方）。"""
    if not input_dir.exists():
        return []
    out: List[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if _is_hidden(p.relative_to(input_dir).as_posix()):
            continue
        out.append(p)
    return out


def classify_file(p: Path) -> str:
    suf = p.suffix.lower()
    if suf == ".json":
        return "json"
    if suf == ".pdf":
        return "pdf"
    return "text"


def read_text(p: Path) -> Tuple[str, List[str]]:
    degs: List[str] = []
    raw: Optional[bytes] = None
    try:
        raw = p.read_bytes()
    except OSError as e:
        return "", ["read_failed:%s:%s" % (p.name, e)]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), degs
        except (UnicodeDecodeError, LookupError):
            continue
    degs.append("decode_failed:%s" % p.name)
    return "", degs


def load_json(p: Path) -> Tuple[Any, List[str]]:
    text, degs = read_text(p)
    if not text.strip():
        return None, degs + ["empty_json:%s" % p.name]
    try:
        return json.loads(text), degs
    except (json.JSONDecodeError, ValueError) as e:
        return None, degs + ["bad_json:%s:%s" % (p.name, e)]


def parse_pdf_text(p: Path) -> Tuple[str, List[str]]:
    """用 pdftotext 抽取文本；缺失或失败只降级，不编造。"""
    degs: List[str] = []
    try:
        cp = subprocess.run(
            ["pdftotext", "-q", str(p), "-"],
            capture_output=True, timeout=60,
        )
        if cp.returncode == 0:
            return cp.stdout.decode("utf-8", errors="replace"), degs
        degs.append("pdftotext_failed:%s:rc=%s" % (p.name, cp.returncode))
        return "", degs
    except FileNotFoundError:
        degs.append("pdftotext_missing:%s" % p.name)
        return "", degs
    except Exception as e:  # noqa: BLE001
        degs.append("pdftotext_error:%s:%s" % (p.name, e))
        return "", degs


# --------------------------------------------------------------------------
# 响应分类与记录抽取
# --------------------------------------------------------------------------
def classify_response(obj: Any) -> str:
    """识别异构响应的来源库。"""
    if isinstance(obj, dict):
        keys = {k.lower() for k in obj.keys()}
        if "message" in keys and isinstance(obj.get("message"), dict):
            if "items" in obj["message"]:
                return "crossref"
        if "resultlist" in keys:
            return "europepmc"
        if "esearchresult" in keys or "esummaryresult" in keys:
            return "pubmed"
        if "result" in keys and isinstance(obj.get("result"), dict):
            rkeys = {k.lower() for k in obj["result"].keys()}
            if "uids" in rkeys or ("esummary" not in rkeys and any(
                    isinstance(v, dict) and ("uid" in v or "title" in v)
                    for v in obj["result"].values())):
                return "pubmed"
        if "query" in keys and ("pmid" in keys or "webenv" in keys):
            return "pubmed"
    return "publisher"


def _looks_like_record(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    keys = {str(k).lower() for k in d.keys()}
    has_title = any(k in keys for k in ("title", "articletitle", "dc:title"))
    has_doi = any(k in keys for k in ("doi", "prism:doi", "elocationid"))
    has_pmid = any(k in keys for k in ("pmid", "uid", "pmcid"))
    has_authors = any(k in keys for k in ("author", "authors", "authorstring",
                                          "creator", "authorlist"))
    return has_title or has_doi or has_pmid or has_authors


def _find_record_collections(node: Any, path: str,
                             acc: List[Tuple[List[Any], str, Optional[List[Any]]]]) -> None:
    """收集记录集合：支持 list-of-records 与 dict-of-records（PubMed esummary）。"""
    if isinstance(node, dict):
        rec_items: List[Any] = []
        rec_keys: List[Any] = []
        dict_vals = [v for v in node.values() if isinstance(v, dict)]
        for k, v in node.items():
            if isinstance(v, dict) and _looks_like_record(v):
                rec_items.append(v)
                rec_keys.append(k)
        if rec_items and len(rec_items) >= max(1, len(dict_vals) // 2):
            acc.append((rec_items, path, rec_keys))
        for k, v in node.items():
            _find_record_collections(v, "%s.%s" % (path, k), acc)
    elif isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            n_record = sum(1 for x in node if _looks_like_record(x))
            if n_record >= max(1, len(node) // 2) or (n_record > 0 and n_record >= 3):
                acc.append((node, path, None))
        for i, x in enumerate(node):
            _find_record_collections(x, "%s[%d]" % (path, i), acc)


def extract_records(obj: Any) -> List[Tuple[Dict, str]]:
    """返回 [(raw_dict, locator)]，locator 为 JSON 指针式路径。"""
    found: List[Tuple[List[Any], str, Optional[List[Any]]]] = []
    _find_record_collections(obj, "$", found)
    if not found:
        return []
    # 选包含最多 record 元素的集合，避免元数据数组误判
    best = max(found, key=lambda t: sum(1 for x in t[0] if _looks_like_record(x)))
    items, path, keys = best
    out: List[Tuple[Dict, str]] = []
    for i, item in enumerate(items):
        if _looks_like_record(item):
            loc = "%s.%s" % (path, str(keys[i])) if keys is not None \
                else "%s[%d]" % (path, i)
            out.append((item, loc))
    return out


# --------------------------------------------------------------------------
# 字段归一
# --------------------------------------------------------------------------
def norm_doi(s: Any) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    s = s.strip().strip(".,;\"'")
    s = s.lower()
    return s or None


def norm_title(s: Any) -> Optional[str]:
    if s is None:
        return None
    s = str(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s or None


def _first_of(raw: Dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in raw and raw[k] not in (None, "", [], {}):
            return raw[k]
    return None


def _author_parts(a: Any) -> Tuple[Any, Any, Any]:
    if isinstance(a, str):
        s = a.strip()
        return (None, None, s or None)
    if isinstance(a, dict):
        given = _first_of(a, ("given", "firstName", "forename", "firstname",
                              "given_name", "givenName"))
        family = _first_of(a, ("family", "lastName", "lastname", "surname",
                               "last_name", "lastName"))
        full = _first_of(a, ("name", "fullName", "fullname", "full_name"))
        if not full and (given or family):
            full = " ".join(x for x in (given, family) if x)
        return (given, family, full)
    return (None, None, None)


def norm_authors(raw: Any, author_string: Any = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if isinstance(raw, list):
        for a in raw:
            g, f, full = _author_parts(a)
            if full or g or f:
                out.append({"given": str(g or "").strip(),
                            "family": str(f or "").strip(),
                            "full": str(full or "").strip()})
    if not out and author_string:
        for name in re.split(r"\s*(?:;|,|\band\b)\s*", str(author_string)):
            name = name.strip()
            if name:
                out.append({"given": "", "family": "", "full": name})
    return out


def first_author_key(authors: List[Dict[str, str]]) -> Optional[str]:
    if not authors:
        return None
    a = authors[0]
    parts = [a.get("family", ""), a.get("given", ""), a.get("full", "")]
    s = " ".join(p for p in parts if p).strip()
    return norm_title(s)


def extract_year(raw: Dict) -> Optional[int]:
    candidates: List[Any] = []
    for k in ("year", "pubYear", "pubyear", "publicationYear", "sortpubyear",
              "sortpubdate", "pubdate", "epubdate"):
        v = raw.get(k)
        if v:
            candidates.append(v)
    for dk in ("issued", "published-print", "published-online", "created"):
        d = raw.get(dk)
        if isinstance(d, dict):
            dp = d.get("date-parts") or d.get("date_parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list):
                candidates.append(dp[0][0])
            else:
                candidates.append(d.get("date-time") or d.get("dateTime"))
    for dk in ("firstPublicationDate", "publicationDate",
               "electronicPublicationDate", "pubDate"):
        if raw.get(dk):
            candidates.append(raw[dk])
    for c in candidates:
        m = re.search(r"(18|19|20)\d{2}", str(c))
        if m:
            y = int(m.group(0))
            if 1800 <= y <= 2100:
                return y
    return None


def _normalize_pages(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def normalize_record(raw: Dict, db: str) -> Dict[str, Any]:
    """把单个异构原始记录归一成统一字段，并保留原始快照与定位。"""
    title = _first_of(raw, ("title", "articleTitle", "dc:title"))
    title_s = title[0] if isinstance(title, list) and title else title
    authors_raw = _first_of(raw, ("author", "authors", "authorList", "creator"))
    author_string = _first_of(raw, ("authorString", "author_string"))
    authors = norm_authors(authors_raw, author_string)
    year = extract_year(raw)
    journal = _first_of(raw, ("container-title", "journalTitle",
                              "fullJournalName", "source", "journal"))
    if isinstance(journal, list) and journal:
        journal = journal[0]
    volume = _first_of(raw, ("volume", "volumeNumber"))
    issue = _first_of(raw, ("issue", "issueNumber"))
    pages = _first_of(raw, ("page", "pages", "pageInfo"))
    doi = norm_doi(_first_of(raw, ("DOI", "doi", "prism:doi")))
    pmid = _first_of(raw, ("pmid", "PMID", "uid"))
    pmcid = _first_of(raw, ("pmcid", "PMCID", "pmcID"))
    issn = _first_of(raw, ("ISSN", "issn", "eissn", "electronicIssn"))
    if isinstance(issn, list) and issn:
        issn = issn[0]
    doc_type = _first_of(raw, ("type", "pubType", "pubtype", "documentType"))
    if isinstance(doc_type, list) and doc_type:
        doc_type = doc_type[0]
    abstract = _first_of(raw, ("abstract", "abstractText", "abstract_text"))
    publisher = _first_of(raw, ("publisher", "publisherName", "journalPublisher"))

    identifiers: Dict[str, Any] = {}
    for k in ("alternative-id", "alternativeId", "pii", "PII", "ut", "UT",
              "issn", "ISSN", "eissn", "electronicIssn"):
        if raw.get(k) not in (None, "", []):
            identifiers[k] = raw[k]

    rec = {
        "title": str(title_s).strip() if title_s else None,
        "authors": authors,
        "year": year,
        "journal": str(journal).strip() if journal else None,
        "volume": str(volume).strip() if volume else None,
        "issue": str(issue).strip() if issue else None,
        "pages": _normalize_pages(pages),
        "doi": doi,
        "pmid": str(pmid).strip() if pmid else None,
        "pmcid": str(pmcid).strip() if pmcid else None,
        "issn": str(issn).strip() if issn else None,
        "doc_type": str(doc_type).strip() if doc_type else None,
        "abstract": str(abstract).strip() if abstract else None,
        "publisher": str(publisher).strip() if publisher else None,
        "identifiers": identifiers,
        "_db": db,
        "_norm_title": norm_title(title_s),
        "_first_author_key": first_author_key(authors),
    }
    return rec


# --------------------------------------------------------------------------
# 去重 / 合并
# --------------------------------------------------------------------------
def dedup_records(records: List[Dict]) -> List[Dict]:
    """确定性去重，返回簇列表；每条 link 记录去重理由与命中的字段。"""
    n = len(records)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    links: List[Dict] = []

    def union(i: int, j: int, reason: str, matched: List[str]) -> None:
        ri, rj = find(i), find(j)
        if ri == rj:
            return
        parent[rj] = ri
        links.append({"i": i, "j": j, "reason": reason,
                      "matched_fields": matched})

    doi_idx: Dict[str, List[int]] = {}
    pmid_idx: Dict[str, List[int]] = {}
    pmcid_idx: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        if r.get("doi"):
            doi_idx.setdefault(r["doi"], []).append(i)
        if r.get("pmid"):
            pmid_idx.setdefault(r["pmid"], []).append(i)
        if r.get("pmcid"):
            pmcid_idx.setdefault(r["pmcid"], []).append(i)

    for idxs in doi_idx.values():
        for j in idxs[1:]:
            union(idxs[0], j, "doi_exact", ["doi"])
    for idxs in pmid_idx.values():
        for j in idxs[1:]:
            union(idxs[0], j, "pmid_exact", ["pmid"])
    for idxs in pmcid_idx.values():
        for j in idxs[1:]:
            union(idxs[0], j, "pmcid_exact", ["pmcid"])

    key_idx: Dict[Tuple, List[int]] = {}
    for i, r in enumerate(records):
        t, fa, y = r.get("_norm_title"), r.get("_first_author_key"), r.get("year")
        if t and fa and y:
            key_idx.setdefault((t, fa, y), []).append(i)
    for idxs in key_idx.values():
        for j in idxs[1:]:
            union(idxs[0], j, "title_firstauthor_year",
                  ["title", "first_author", "year"])

    # 标题模糊（difflib >= 0.90）+ 同年
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            ti, tj = records[i].get("_norm_title"), records[j].get("_norm_title")
            if not ti or not tj:
                continue
            yi, yj = records[i].get("year"), records[j].get("year")
            if yi and yj and yi != yj:
                continue
            if difflib.SequenceMatcher(None, ti, tj).ratio() >= 0.90:
                union(i, j, "title_fuzzy_0.90", ["title"])

    clusters_map: Dict[int, List[int]] = {}
    for i in range(n):
        clusters_map.setdefault(find(i), []).append(i)

    clusters: List[Dict] = []
    for root in sorted(clusters_map):
        idxs = sorted(clusters_map[root])
        member = set(idxs)
        clinks = [l for l in links if l["i"] in member and l["j"] in member]
        clusters.append({"indices": idxs, "links": clinks})
    return clusters


def _field_values(records: List[Dict], field: str) -> List[Tuple[int, Any]]:
    """返回 (record_index, value) 的非空取值列表。"""
    out: List[Tuple[int, Any]] = []
    for i, r in enumerate(records):
        v = r.get(field)
        if v not in (None, "", []):
            out.append((i, v))
    return out


def _rank_db(db: str, field: str) -> int:
    order = FIELD_PRIORITY.get(field, [])
    return order.index(db) if db in order else len(order)


def merge_cluster(cluster: Dict, records: List[Dict]) -> Dict:
    """合并一个去重簇，返回 merged / field_sources / conflicts。"""
    idxs = cluster["indices"]
    recs = [records[i] for i in idxs]
    db_set = sorted({r["_db"] for r in recs})
    merged: Dict[str, Any] = {}
    field_sources: Dict[str, Any] = {}
    conflicts: List[Dict] = []

    for field in CANONICAL_FIELDS:
        values = _field_values(recs, field)
        if not values:
            merged[field] = None
            continue
        # 按 db 优先级 + 输入顺序挑选主值
        winner = min(values, key=lambda t: (_rank_db(records[idxs[t[0]]]["_db"],
                                                      field), t[0]))
        wi = idxs[winner[0]]
        wrec = records[wi]
        merged[field] = winner[1]
        field_sources[field] = {
            "value": winner[1],
            "db": wrec["_db"],
            "source_file": wrec["_source_file"],
            "locator": wrec["_locator"],
        }
        # 冲突消解：不同来源给出不同取值时全部保留，不静默丢弃
        distinct = {}
        for t in values:
            key = json.dumps(t[1], ensure_ascii=False, sort_keys=True,
                             default=str)
            distinct.setdefault(key, []).append(t[0])
        if len(distinct) > 1:
            conflict_entries = []
            for _, tids in distinct.items():
                for t in tids:
                    ri = idxs[t]
                    conflict_entries.append({
                        "value": records[ri][field],
                        "db": records[ri]["_db"],
                        "source_file": records[ri]["_source_file"],
                        "locator": records[ri]["_locator"],
                    })
            conflicts.append({
                "field": field,
                "resolved_to": winner[1],
                "sources": conflict_entries,
            })

    record_ids = ["rec:%04d" % (i + 1) for i in idxs]
    reasons = sorted({l["reason"] for l in cluster["links"]}) or ["unique"]
    dedup_fields = sorted({f for l in cluster["links"] for f in l["matched_fields"]})

    merged_record = {
        "record_id": record_ids[0],
        "source_record_ids": record_ids,
        "db_count": len(db_set),
        "databases": db_set,
        "dedup_reason": reasons,
        "dedup_matched_fields": dedup_fields,
        **merged,
        "field_sources": field_sources,
        "conflicts": conflicts,
        "member_records": [
            {
                "record_id": "rec:%04d" % (idxs[k] + 1),
                "db": records[idxs[k]]["_db"],
                "source_file": records[idxs[k]]["_source_file"],
                "locator": records[idxs[k]]["_locator"],
                "request": records[idxs[k]].get("_request"),
                "raw_snapshot": _verbatim_snapshot(records[idxs[k]]["_raw"]),
                "raw_snapshot_truncated": _snapshot_truncated(
                    records[idxs[k]]["_raw"]),
            }
            for k in range(len(idxs))
        ],
    }
    return merged_record


def _verbatim_snapshot(raw: Dict) -> str:
    s = json.dumps(raw, ensure_ascii=False, sort_keys=False, default=str)
    if len(s) > PDF_SNAPSHOT_CHARS:
        return s[:PDF_SNAPSHOT_CHARS - 3] + "..."
    return s


def _snapshot_truncated(raw: Dict) -> bool:
    return len(json.dumps(raw, ensure_ascii=False, sort_keys=False,
                          default=str)) > PDF_SNAPSHOT_CHARS


def _verbatim_quote(raw: Dict) -> str:
    s = _verbatim_snapshot(raw)
    if len(s) > 800:
        return s[:797] + "..."
    return s


# --------------------------------------------------------------------------
# 覆盖 / 证据 / 相关性
# --------------------------------------------------------------------------
def build_coverage(records: List[Dict], clusters: List[Dict]) -> Dict:
    db_names = sorted({r["_db"] for r in records})
    per_db: Dict[str, Dict] = {}
    for db in db_names:
        per_db[db] = {"record_count": 0, "fields_missing": [], "single_db_only": 0}
    for r in records:
        per_db[r["_db"]]["record_count"] += 1
    for db in db_names:
        recs = [r for r in records if r["_db"] == db]
        per_db[db]["fields_missing"] = [
            f for f in CANONICAL_FIELDS
            if not any(r.get(f) not in (None, "", []) for r in recs)
        ]
    single_only: List[str] = []
    for c in clusters:
        dbs = {records[i]["_db"] for i in c["indices"]}
        if len(dbs) == 1:
            db = next(iter(dbs))
            per_db[db]["single_db_only"] += 1
            single_only.append("rec:%04d" % (c["indices"][0] + 1))
    total = len(records)
    merged = len(clusters)
    return {
        "databases": db_names,
        "total_input_records": total,
        "merged_record_count": merged,
        "duplicates_merged": total - merged,
        "records_in_single_db_only": single_only,
        "systematically_missing_fields": {
            db: per_db[db]["fields_missing"] for db in db_names
        },
        "per_database": per_db,
    }


def build_evidence(records: List[Dict], clusters: List[Dict]) -> List[Dict]:
    evs: List[Dict] = []
    eid = 0
    for c in clusters:
        for idx in c["indices"]:
            r = records[idx]
            eid += 1
            evs.append({
                "evidence_id": "ev:%03d" % eid,
                "type": "record_source",
                "db": r["_db"],
                "source_file": r["_source_file"],
                "locator": r["_locator"],
                "quote": _verbatim_quote(r["_raw"]),
                "record_id": "rec:%04d" % (idx + 1),
            })
    return evs


def relevance_hint(record: Dict, terms: List[str]) -> str:
    hay = " ".join(x for x in (record.get("title") or "",
                               record.get("abstract") or "",
                               record.get("journal") or "") if x)
    hay_norm = norm_title(hay) or ""
    hits = [t for t in terms if t and t in hay_norm]
    if len(hits) >= 2:
        return "high"
    if len(hits) == 1:
        return "medium"
    return "low"


# --------------------------------------------------------------------------
# 查询范围 / 词项派生
# --------------------------------------------------------------------------
STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "for", "in", "on", "to", "with",
    "is", "are", "was", "were", "be", "been", "being", "it", "this", "that",
    "these", "those", "as", "by", "at", "from", "into", "over", "under",
    "than", "between", "using", "based", "study", "studies", "research",
}


def derive_query_terms(*texts: Any) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", str(t).lower()):
            if w not in STOPWORDS and len(w) > 2 and w not in seen:
                seen.add(w)
                out.append(w)
    return out


# --------------------------------------------------------------------------
# 结果组装
# --------------------------------------------------------------------------
def ensure_contract_keys(res: Dict, contract: Optional[List[str]]) -> None:
    keys = contract if contract is not None else DEFAULT_CONTRACT
    for k in keys:
        if k not in res:
            res[k] = None


def build_skeleton(case_id: str, started: str) -> Dict:
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
        "change_log": [
            {"step": "skeleton", "status": "written", "at": started,
             "detail": "先落盘合法 partial 骨架，后续只做增量改进"},
        ],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {
            "started_at": started,
            "finished_at": None,
            "seconds": 0,
            "pipeline_version": VERSION,
            "python": sys.version.split()[0],
            "budget": {
                "total_seconds": TOTAL_BUDGET_SECONDS,
                "model_requests": TOTAL_MODEL_CALLS,
                "max_response_tokens": MAX_RESPONSE_TOKENS,
            },
            "phases": [],
        },
    }


def apply_decisions(res: Dict, decisions: Dict, at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts",
                "permission_issues"):
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
            elif key == "uncertainty":
                res["uncertainty"] = decisions[key]
                applied.append("uncertainty")
            else:
                res[key] = decisions[key]
                applied.append(key)
    if applied:
        res.setdefault("change_log", []).append(
            {"step": "model_refine", "status": "applied", "at": at,
             "detail": "applied decisions: " + ", ".join(applied)}
        )
    return applied


def _meta_values(case: Optional[Dict]) -> Dict[str, Any]:
    """从 case.json 抽取问题、查询、数据库等元数据（键名容错）。"""
    if not isinstance(case, dict):
        return {"question": None, "queries": [], "databases": []}
    q = _first_of(case, ("research_question", "question", "task", "query",
                         "researchQuestion"))
    queries = _first_of(case, ("queries", "keywords", "search_terms",
                               "searchTerms", "search_queries"))
    if queries is None:
        queries = []
    if isinstance(queries, (str,)):
        queries = [queries]
    if not isinstance(queries, list):
        queries = []
    dbs = _first_of(case, ("databases", "database", "sources", "db_list"))
    if dbs is None:
        dbs = []
    if isinstance(dbs, str):
        dbs = [dbs]
    if not isinstance(dbs, list):
        dbs = []
    return {"question": q, "queries": queries, "databases": dbs}
