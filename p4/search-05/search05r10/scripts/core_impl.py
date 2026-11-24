#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- 多数据库联合检索核心库（仅标准库，确定性，无网络）。

本模块是 Skill 的中间表示层：把异构数据库响应归一成统一记录模型，
再做可解释的去重、合并、覆盖比较、证据溯源与确定性相关性提示。
设计原则：
  1. 先发现后假设：不依赖固定文件名/键名，靠结构指纹识别库类型；
  2. 每次字段取值都带 field_sources（来源库 + locator + 逐字片段）；
  3. 去重/合并/排序全部确定性，同输入多次运行结果一致；
  4. 任何异常都降级为说明而非崩溃。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "2.0.0"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB
MAX_QUOTE_CHARS = 2400            # evidence quote 上限

# 固定输出契约：case_id + 题目 output_contract 全部字段 + 固定字段。
DEFAULT_CONTRACT = [
    "research_question", "search_scope", "candidates", "ranking",
    "evidence_records", "coverage", "empty_results", "conflicts",
    "missing_metadata", "permission_issues", "limitations", "sources",
    "uncertainty", "change_log", "errors", "degradations",
    "pending_human", "runtime",
]

FIXED_FIELDS = ["case_id", "status"] + DEFAULT_CONTRACT

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def safe_json_dumps(obj: Any, indent: int = 2) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=True)


def norm_text(s: Any) -> str:
    """把任意值转成可比较的规范化文本。"""
    if s is None:
        return ""
    if isinstance(s, (dict, list)):
        return ""
    t = str(s)
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\u00ad", "")  # soft hyphen
    return t.strip()


def norm_title(s: Any) -> str:
    t = norm_text(s)
    t = re.sub(r"[\[\](){}<>]", " ", t)
    t = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", t).lower()
    return re.sub(r"\s+", " ", t).strip()


def to_year(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 1000 <= v <= 9999 else None
    m = re.search(r"(1[6-9]\d{2}|20\d{2})", str(v))
    if not m:
        return None
    y = int(m.group(1))
    return y if 1000 <= y <= 9999 else None


def norm_doi(v: Any) -> str:
    t = norm_text(v).lower().replace(" ", "")
    if not t:
        return ""
    if t.startswith("doi:"):
        t = t[4:]
    t = t.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return t.strip().strip(".,;:()[]")


def norm_identifier(kind: str, v: Any) -> str:
    t = norm_text(v)
    if not t:
        return ""
    if kind == "doi":
        return norm_doi(t)
    if kind == "pmid":
        m = re.search(r"\d+", t)
        return m.group(0) if m else ""
    if kind == "pmcid":
        t = re.sub(r"(?i)^pmc", "", t.strip())
        return t
    if kind == "issn":
        m = re.search(r"\d{4}-?\d{3}[\dXx]", t)
        return m.group(0).upper() if m else ""
    return t


def sortable_authors(authors: List[str]) -> str:
    return "|".join(sorted(norm_text(a).lower() for a in authors if norm_text(a)))


def first_author(authors: List[str]) -> str:
    for a in authors:
        t = norm_text(a)
        if t:
            return t.lower()
    return ""


def parse_authors(v: Any) -> List[str]:
    """把多种作者表示统一成字符串列表。"""
    out: List[str] = []
    if v is None:
        return out
    if isinstance(v, str):
        for part in re.split(r";|,|\band\b", v, flags=re.IGNORECASE):
            t = norm_text(part)
            if t and t.lower() not in ("na", "n/a", "et al", "et al."):
                out.append(t)
        return out
    if isinstance(v, list):
        for item in v:
            if isinstance(item, str):
                t = norm_text(item)
                if t:
                    out.append(t)
            elif isinstance(item, dict):
                fam = norm_text(item.get("family") or item.get("lastName") or item.get("surname") or "")
                giv = norm_text(item.get("given") or item.get("firstName") or item.get("forename") or "")
                name = item.get("name")
                coll = norm_text(item.get("collective") or item.get("collective-name") or "")
                if coll:
                    out.append(coll)
                elif name:
                    out.append(norm_text(name))
                elif fam or giv:
                    out.append((giv + " " + fam).strip())
                else:
                    flat = norm_text(item)
                    if flat:
                        out.append(flat)
            else:
                t = norm_text(item)
                if t:
                    out.append(t)
    return [a for a in out if a]


# ---------------------------------------------------------------------------
# 库结构指纹（先发现后假设）
# ---------------------------------------------------------------------------

def fingerprint(obj: Any) -> str:
    """根据结构特征识别数据库响应类型，返回库名。"""
    if not isinstance(obj, dict):
        return "generic"
    message = obj.get("message")
    if isinstance(message, dict) and isinstance(message.get("items"), list):
        return "crossref"
    rl = obj.get("resultList")
    if isinstance(rl, dict) and isinstance(rl.get("result"), list):
        return "europepmc"
    # PubMed esummary: result 是 dict（UID -> record）
    result = obj.get("result")
    if isinstance(result, dict):
        vals = list(result.values())
        if vals and all(isinstance(x, dict) for x in vals):
            return "pubmed"
    if isinstance(result, list):
        return "pubmed" if any(isinstance(x, dict) and ("uid" in x or "pmid" in x) for x in result) else "generic"
    if "esearchresult" in obj:
        return "pubmed"
    # 出版社/通用包装
    for key in ("records", "items", "data", "results", "publications", "documents"):
        v = obj.get(key)
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            return "generic"
    return "generic"


def extract_records(obj: Any) -> List[Tuple[dict, str]]:
    """从数据库响应对象抽取 (raw_record, locator) 列表。

    返回原始字典 + 一个可回溯的 JSON 指针式 locator。
    """
    out: List[Tuple[dict, str]] = []
    if isinstance(obj, list):
        for i, it in enumerate(obj):
            if isinstance(it, dict):
                out.append((it, f"[{i}]"))
        return out
    if not isinstance(obj, dict):
        return out

    def add_from_list(arr: list, base: str) -> None:
        for i, it in enumerate(arr):
            if isinstance(it, dict):
                out.append((it, f"{base}[{i}]"))

    message = obj.get("message")
    if isinstance(message, dict) and isinstance(message.get("items"), list):
        add_from_list(message["items"], "message.items")
        return out
    rl = obj.get("resultList")
    if isinstance(rl, dict) and isinstance(rl.get("result"), list):
        add_from_list(rl["result"], "resultList.result")
        return out
    result = obj.get("result")
    if isinstance(result, dict):
        for uid, rec in result.items():
            if isinstance(rec, dict):
                out.append((rec, f"result.{uid}"))
        return out
    if isinstance(result, list):
        add_from_list(result, "result")
        return out
    for key in ("records", "items", "data", "results", "publications", "documents"):
        v = obj.get(key)
        if isinstance(v, list):
            add_from_list(v, key)
            return out
    return out


def extract_request(obj: Any) -> Dict[str, Any]:
    """尽力提取响应里携带的请求/查询信息，没有则不编造。"""
    if not isinstance(obj, dict):
        return {}
    req: Dict[str, Any] = {}
    for key in ("query", "request", "search", "search_term", "q"):
        v = obj.get(key)
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)) or isinstance(v, list):
            req[key] = v
        elif isinstance(v, dict):
            req[key] = {k: vv for k, vv in v.items() if isinstance(vv, (str, int, float, bool))}
    for key in ("query", "q", "term"):
        msg = obj.get("message")
        if isinstance(msg, dict) and key in msg:
            v = msg[key]
            if isinstance(v, (str, int, float, bool, list)):
                req.setdefault(key, v)
    return req


# ---------------------------------------------------------------------------
# 字段映射与归一
# ---------------------------------------------------------------------------

def _g(d: dict, *paths: str) -> Any:
    """按候选路径取第一个非空值。"""
    for p in paths:
        cur: Any = d
        ok = True
        for seg in p.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def _issn_from_record(d: dict) -> Optional[str]:
    for key in ("issn", "ISSN", "issn-print", "issn-electronic", "journal_issn"):
        v = _g(d, key)
        if v is not None:
            ids = norm_identifier("issn", v)
            if ids:
                return ids
    for key in ("issn-type", "issn_type"):
        arr = d.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict):
                    ids = norm_identifier("issn", it.get("value"))
                    if ids:
                        return ids
    return None


def _page_from_dict(d: dict) -> Optional[str]:
    for key in ("first-page", "first_page", "start_page", "start"):
        v = _g(d, key)
        if v is not None:
            return norm_text(v)
    rng = _g(d, "page-range")
    return norm_text(rng)


def _pages_from_record(d: dict) -> Optional[str]:
    v = _g(d, "pages", "page", "pagination", "page-range")
    if isinstance(v, dict):
        p = _page_from_dict(v)
        return p or norm_text(v.get("article-number") or v.get("article_number"))
    return norm_text(v)


def _doi_from_record(d: dict) -> str:
    # DOI 可能直接存在，也可能藏在标识符数组里。
    v = _g(d, "doi", "DOI", "doi_full", "id.doi")
    if v is not None:
        d_ = norm_doi(v)
        if d_:
            return d_
    for key in ("identifier", "identifiers", "ids", "articleids", "id_list"):
        arr = d.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, str) and norm_doi(it).startswith("10."):
                    return norm_doi(it)
                if isinstance(it, dict):
                    idt = norm_text(it.get("idtype") or it.get("type") or it.get("kind") or "")
                    val = norm_text(it.get("id") or it.get("value") or it.get("doi") or "")
                    if idt.lower() == "doi" and val.lower().startswith("10."):
                        return norm_doi(val)
                    if val.lower().startswith("10."):
                        return norm_doi(val)
    return ""


def _pmid_from_record(d: dict) -> str:
    v = _g(d, "pmid", "PMID", "id.pmid", "uid")
    if v is not None:
        ids = norm_identifier("pmid", v)
        if ids:
            return ids
    for key in ("identifier", "identifiers", "ids", "articleids"):
        arr = d.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, str) and it.isdigit():
                    return it
                if isinstance(it, dict):
                    idt = norm_text(it.get("idtype") or it.get("type") or it.get("kind") or "")
                    val = norm_text(it.get("id") or it.get("value") or "")
                    if idt.lower() == "pmid" and val.isdigit():
                        return val
    return ""


def _pmcid_from_record(d: dict) -> str:
    v = _g(d, "pmcid", "PMCID", "pmc_id")
    if v is not None:
        return norm_identifier("pmcid", v)
    for key in ("identifier", "identifiers", "ids", "articleids"):
        arr = d.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict):
                    idt = norm_text(it.get("idtype") or it.get("type") or it.get("kind") or "")
                    val = norm_text(it.get("id") or it.get("value") or "")
                    if idt.lower() == "pmcid":
                        return norm_identifier("pmcid", val)
    return ""


def _journal_from_record(d: dict) -> str:
    v = _g(d, "journal", "journal_title", "journalTitle", "journal-title",
           "journal_title", "fulljournalname", "fullJournalName",
           "container-title", "container_title", "source", "publisher_name",
           "publication_title", "journalName")
    if isinstance(v, list):
        return norm_text(v[0] if v else None)
    if isinstance(v, dict):
        return norm_text(v.get("name") or v.get("title"))
    return norm_text(v)


def _title_from_record(d: dict) -> str:
    v = _g(d, "title", "title_display", "articletitle", "articleTitle",
           "article-title", "book_title", "chapter_title")
    if isinstance(v, list):
        return norm_text(v[0] if v else None)
    if isinstance(v, dict):
        return norm_text(v.get("name") or v.get("title") or v.get("value"))
    return norm_text(v)


def _doc_type_from_record(d: dict) -> Optional[str]:
    v = _g(d, "type", "document_type", "documentType", "pubType", "pub_type",
           "publication_type", "article_type", "content_type", "format")
    if isinstance(v, list):
        v = v[0] if v else None
    return norm_text(v) or None


def normalize_record(raw: dict, db: str) -> Dict[str, Any]:
    """把单条原始记录归一成统一字段模型；每个关键字段带取值路径。"""
    authors = parse_authors(_g(raw, "author", "authors", "authorString", "author_string",
                               "creators", "contributors", "creator"))
    year = to_year(_g(raw, "year", "pubYear", "pubyear", "publication_year",
                      "published_year", "published-print.year", "published-online.year",
                      "published.year", "pubdate", "pubDate", "pub_date", "publication_date",
                      "created.date-parts", "issued.date-parts", "date", "epubdate"))
    if year is None:
        dp = _g(raw, "published.date-parts", "issued.date-parts", "created.date-parts")
        if isinstance(dp, list):
            for part in dp:
                if isinstance(part, list) and part:
                    year = to_year(part[0])
                    if year:
                        break

    rec: Dict[str, Any] = {
        "title": _title_from_record(raw),
        "authors": authors,
        "year": year,
        "journal": _journal_from_record(raw),
        "volume": norm_text(_g(raw, "volume", "volume_no", "journalVolume")),
        "issue": norm_text(_g(raw, "issue", "number", "issue_no", "journalIssue")),
        "pages": _pages_from_record(raw),
        "doi": _doi_from_record(raw),
        "pmid": _pmid_from_record(raw),
        "pmcid": _pmcid_from_record(raw),
        "issn": _issn_from_record(raw),
        "doc_type": _doc_type_from_record(raw),
        "abstract": norm_text(_g(raw, "abstract", "abstractText", "description")),
        "publisher": norm_text(_g(raw, "publisher", "publisher_name", "publisherName",
                                  "journal_publisher", "publisherLocation")),
        "identifiers": {},
    }
    rec["identifiers"] = {k: rec[k] for k in ("doi", "pmid", "pmcid", "issn") if rec[k]}
    # 每条记录保留 db 与原始定位，供 field_sources 使用。
    rec["_db"] = db
    return rec


FIELD_LABELS = {
    "title": "标题", "authors": "作者", "year": "年份", "journal": "期刊",
    "volume": "卷", "issue": "期", "pages": "页码", "doi": "DOI",
    "pmid": "PMID", "pmcid": "PMCID", "issn": "ISSN", "doc_type": "文献类型",
    "abstract": "摘要", "publisher": "出版社", "identifiers": "标识符",
}


# ---------------------------------------------------------------------------
# 去重与合并
# ---------------------------------------------------------------------------

def record_identity(rec: Dict[str, Any]) -> str:
    """确定性标识键：优先 DOI，其次 PMID/PMCID，最后标题+一作+年份。"""
    if rec.get("doi"):
        return "doi:" + rec["doi"]
    if rec.get("pmid"):
        return "pmid:" + rec["pmid"]
    if rec.get("pmcid"):
        return "pmcid:" + rec["pmcid"]
    t = norm_title(rec.get("title"))
    fa = first_author(rec.get("authors") or [])
    y = rec.get("year")
    return "tay:" + "|".join([t, fa, str(y or "")])


def cluster_records(records: List[Dict[str, Any]]) -> List[Tuple[int, List[Dict[str, Any]], str]]:
    """把归一记录按确定性规则聚类；返回 (cluster_id, members, 判定依据)。

    两级匹配：标识符精确匹配 > 标题（规范化后）精确 > 标题+年份+一作。
    聚类顺序确定，不依赖输入先后。
    """
    clusters: List[List[Dict[str, Any]]] = []
    reasons: List[str] = []

    def find_cluster(rec: Dict[str, Any]) -> Optional[Tuple[int, str]]:
        doi = norm_doi(rec.get("doi"))
        pmid = rec.get("pmid")
        pmcid = rec.get("pmcid")
        t = norm_title(rec.get("title"))
        fa = first_author(rec.get("authors") or [])
        y = rec.get("year")
        for i, cl in enumerate(clusters):
            for m in cl:
                if doi and norm_doi(m.get("doi")) == doi:
                    return i, "doi"
                if pmid and m.get("pmid") == pmid:
                    return i, "pmid"
                if pmcid and m.get("pmcid") == pmcid:
                    return i, "pmcid"
            if t:
                for m in cl:
                    if norm_title(m.get("title")) == t:
                        return i, "title"
            if t and fa and y:
                for m in cl:
                    if norm_title(m.get("title")) == t and first_author(m.get("authors") or []) == fa and m.get("year") == y:
                        return i, "title-author-year"
        return None

    for rec in records:
        hit = find_cluster(rec)
        if hit is not None:
            clusters[hit[0]].append(rec)
            if len(clusters[hit[0]]) == 2:
                reasons[hit[0]] = hit[1]
        else:
            clusters.append([rec])
            reasons.append("unique")
    return [(i, cl, reasons[i]) for i, cl in enumerate(clusters)]


# 合并优先级：同一字段多个来源时的取值优先级（低数字优先）。
DB_PRIORITY = {"crossref": 0, "europepmc": 1, "pubmed": 2, "generic": 3}
FIELD_PRIORITY = {
    "doi": ["crossref", "pubmed", "europepmc", "generic"],
    "pmid": ["pubmed", "europepmc", "crossref", "generic"],
    "pmcid": ["europepmc", "pubmed", "crossref", "generic"],
    "abstract": ["europepmc", "pubmed", "crossref", "generic"],
    "publisher": ["crossref", "generic", "europepmc", "pubmed"],
}


def merge_cluster(cluster_id: int, members: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    """合并一个簇；返回合并记录，含字段来源与冲突说明。"""
    def pick(field: str) -> Tuple[Any, List[Dict[str, Any]], List[Dict[str, Any]]]:
        vals: List[Dict[str, Any]] = []
        for m in members:
            v = m.get(field)
            if v in (None, "", [], {}):
                continue
            vals.append({"value": v, "db": m.get("_db", "unknown"),
                         "source_file": m.get("_source_file", ""),
                         "locator": m.get("_locator", "")})
        if not vals:
            return None, [], []
        order = FIELD_PRIORITY.get(field, [])
        def rank(vv: Dict[str, Any]) -> Tuple[int, int]:
            db = vv.get("db", "unknown")
            if db in order:
                return (order.index(db), DB_PRIORITY.get(db, 99))
            return (len(order), DB_PRIORITY.get(db, 99))
        vals_sorted = sorted(vals, key=rank)
        chosen = vals_sorted[0]
        return chosen["value"], chosen, vals_sorted

    merged: Dict[str, Any] = {
        "record_id": f"rec:{cluster_id + 1:04d}",
        "dedup_reason": reason,
        "member_count": len(members),
        "field_sources": {},
        "provenance": [],
        "conflicts": [],
        "raw_snapshot": "",
    }
    for field in FIELD_LABELS:
        if field == "identifiers":
            ids: Dict[str, Any] = {}
            for m in members:
                for k in ("doi", "pmid", "pmcid", "issn"):
                    v = m.get(k)
                    if v:
                        ids.setdefault(k, v)
            merged[field] = ids
            continue
        value, chosen, all_vals = pick(field)
        merged[field] = value
        if field == "authors":
            # 作者取来源最多且稳定的一组（按规范化后相等判定）
            value = list(dict.fromkeys([a for m in members for a in (m.get("authors") or [])]))
            merged["authors"] = value
        chosen_list = chosen if isinstance(chosen, list) else [chosen]
        merged["field_sources"][field] = [
            {"db": c["db"], "source_file": c["source_file"], "locator": c["locator"]}
            for c in chosen_list
        ]
        # 冲突检测：不同来源、不同取值的同一字段记为冲突。
        if field in ("year", "doi", "volume", "issue", "pages", "journal", "title"):
            unique = []
            for c in all_vals:
                key = json.dumps(c["value"], ensure_ascii=False, sort_keys=True)
                if key not in unique:
                    unique.append(key)
            if len(unique) > 1:
                merged["conflicts"].append({
                    "field": field,
                    "values": [c["value"] for c in all_vals],
                    "resolved": "priority",
                    "winner": merged[field],
                    "sources": [f'{c["db"]}@{c["locator"]}' for c in all_vals],
                })

    merged["provenance"] = [
        {
            "db": m.get("_db", "unknown"),
            "source_file": m.get("_source_file", ""),
            "locator": m.get("_locator", ""),
            "query": m.get("_request", {}),
        }
        for m in members
    ]
    # 原始片段：逐字 JSON 片段（截断到上限）
    raw_doc = members[0].get("_raw") if members and isinstance(members[0].get("_raw"), dict) else {}
    merged["raw_snapshot"] = safe_json_dumps(raw_doc)[:MAX_QUOTE_CHARS]
    return merged


# ---------------------------------------------------------------------------
# 覆盖比较
# ---------------------------------------------------------------------------

def coverage_metrics(merged: List[Dict[str, Any]], all_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    dbs = sorted({m.get("_db", "unknown") for m in all_records})
    db_counts: Dict[str, int] = {db: 0 for db in dbs}
    for m in all_records:
        db_counts[m.get("_db", "unknown")] = db_counts.get(m.get("_db", "unknown"), 0) + 1

    # 仅单库收录：合并记录中所有成员 db 集合大小 == 1 且 member_count == 1
    single = 0
    single_recs = []
    for m in merged:
        dbs_in = {p["db"] for p in m["provenance"]}
        if len(dbs_in) == 1 and m["member_count"] == 1:
            single += 1
            single_recs.append(m["record_id"])

    # 系统性缺失字段：每个库在所有记录里均缺失的字段
    def present(rec: Dict[str, Any], field: str) -> bool:
        v = rec.get(field)
        return v not in (None, "", [], {})

    per_db_missing: Dict[str, List[str]] = {}
    for db in dbs:
        recs = [r for r in all_records if r.get("_db") == db]
        missing = [f for f in FIELD_LABELS if f != "identifiers" and not any(present(r, f) for r in recs)]
        per_db_missing[db] = missing

    return {
        "databases": dbs,
        "total_input_records": len(all_records),
        "merged_record_count": len(merged),
        "duplicates_merged": sum(1 for m in merged if m["member_count"] > 1),
        "records_in_single_db_only": single,
        "single_db_record_ids": single_recs,
        "db_input_counts": db_counts,
        "fields_missing_per_db": per_db_missing,
    }


# ---------------------------------------------------------------------------
# 证据记录
# ---------------------------------------------------------------------------

def evidence_records_for(merged: List[Dict[str, Any]], all_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ev: List[Dict[str, Any]] = []
    idx = 0
    for m in merged:
        for p in m["provenance"]:
            idx += 1
            # 在 all_records 里找到对应的原始记录以生成逐字 quote。
            quote = ""
            for r in all_records:
                if (r.get("_db") == p["db"] and r.get("_source_file") == p["source_file"]
                        and r.get("_locator") == p["locator"]):
                    quote = safe_json_dumps(r.get("_raw") or {})[:MAX_QUOTE_CHARS]
                    break
            if not quote:
                quote = m.get("raw_snapshot", "")
            ev.append({
                "evidence_id": f"ev:{idx:04d}",
                "type": "record_source",
                "db": p["db"],
                "source_file": p["source_file"],
                "locator": p["locator"],
                "quote": quote,
                "record_id": m["record_id"],
            })
    return ev


# ---------------------------------------------------------------------------
# 相关性提示与确定性排序
# ---------------------------------------------------------------------------

def _tokens(s: Any) -> set:
    t = norm_text(s).lower()
    return set(re.findall(r"[0-9a-z\u4e00-\u9fff]+", t))


def relevance_hint(rec: Dict[str, Any], query_terms: List[str]) -> Tuple[int, str]:
    """基于词项命中的确定性相关性提示；返回 (score, rationale)。"""
    if not query_terms:
        return 0, "no query terms provided"
    kw = rec.get("keywords") or []
    kw_text = " ".join(kw) if isinstance(kw, list) else str(kw)
    text = " ".join([
        str(rec.get("title") or ""),
        str(rec.get("abstract") or ""),
        str(rec.get("journal") or ""),
        kw_text,
    ])
    toks = _tokens(text)
    hits = []
    for qt in query_terms:
        q = _tokens(qt)
        if not q:
            continue
        if any(t in toks for t in q):
            hits.append(qt)
    score = len(hits)
    rationale = "matched query terms: " + (", ".join(hits) if hits else "none")
    return score, rationale


def rank_candidates(merged: List[Dict[str, Any]], query_terms: List[str]) -> List[Dict[str, Any]]:
    scored = []
    for m in merged:
        sc, rat = relevance_hint(m, query_terms)
        m["relevance_hint"] = {"score": sc, "rationale": rat}
        scored.append((sc, m.get("year") or 0, m["record_id"], m))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    ranking = []
    for rank, (_, _, _, m) in enumerate(scored, 1):
        ranking.append({
            "record_id": m["record_id"],
            "rank": rank,
            "relevance": "high" if m["relevance_hint"]["score"] > 0 else "low",
            "rationale": m["relevance_hint"]["rationale"],
        })
    return ranking


# ---------------------------------------------------------------------------
# 输入发现与解析
# ---------------------------------------------------------------------------

def discover_input_files(input_dir: Path) -> List[Path]:
    if not input_dir.exists():
        return []
    out = []
    for p in input_dir.rglob("*"):
        if p.is_file():
            out.append(p)
    out.sort(key=lambda p: p.as_posix())
    return out


def classify_file(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".pdf":
        return "pdf"
    return "text"


def load_json(p: Path) -> Tuple[Optional[Any], List[str]]:
    """读 JSON，容错坏编码/坏语法；返回 (对象或 None, degradations)。"""
    degradations: List[str] = []
    try:
        data = p.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            return None, [f"file_too_large: {p.name} ({len(data)} bytes > {MAX_FILE_BYTES})"]
        text = data.decode("utf-8-sig", errors="replace")
        return json.loads(text), degradations
    except Exception as e:  # noqa: BLE001
        return None, [f"bad_json: {p.name}: {type(e).__name__}: {e}"]


def read_text(p: Path) -> Tuple[str, List[str]]:
    degradations: List[str] = []
    try:
        data = p.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            return "", [f"file_too_large: {p.name} ({len(data)} bytes > {MAX_FILE_BYTES})"]
        return data.decode("utf-8-sig", errors="replace"), degradations
    except Exception as e:  # noqa: BLE001
        return "", [f"read_error: {p.name}: {type(e).__name__}: {e}"]


def parse_pdf_text(p: Path) -> Tuple[str, List[str]]:
    """尝试 pdftotext 抽取；失败仅降级并保留路径说明，不编造文本。"""
    import shutil
    import subprocess
    import tempfile
    degradations: List[str] = []
    exe = shutil.which("pdftotext")
    if not exe:
        return "", [f"pdftotext_unavailable: {p.name} (kept path only)"]
    tmp = Path(tempfile.mkdtemp(prefix="pdfextract-"))
    try:
        out = tmp / "out.txt"
        cp = subprocess.run([exe, "-layout", str(p), str(out)],
                            capture_output=True, timeout=60)
        if cp.returncode != 0:
            return "", [f"pdftotext_failed: {p.name}: {cp.stderr.decode('utf-8', 'replace')[:200]}"]
        return out.read_text(encoding="utf-8", errors="replace"), degradations
    except Exception as e:  # noqa: BLE001
        return "", [f"pdftotext_error: {p.name}: {type(e).__name__}: {e}"]
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 案例元数据解析
# ---------------------------------------------------------------------------

def load_case_meta(input_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """读 case.json 与 task.md；缺失/坏 JSON 都降级说明，不崩溃。"""
    errors: List[str] = []
    case: Dict[str, Any] = {}
    case_path = input_dir / "case.json"
    if not case_path.exists():
        errors.append(f"missing: {case_path.as_posix()}")
    else:
        obj, deg = load_json(case_path)
        errors.extend(deg)
        if isinstance(obj, dict):
            case = obj
        else:
            errors.append(f"case.json_not_object: {case_path.name}")

    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text, deg = read_text(task_path)
        errors.extend(deg)
    else:
        errors.append(f"missing: {task_path.as_posix()}")
    return case, errors


def case_id_from(case: Dict[str, Any], input_dir: Path) -> str:
    for k in ("case_id", "id", "caseId"):
        v = case.get(k)
        if v:
            return str(v)
    return "case-" + hashlib.sha1(input_dir.as_posix().encode("utf-8")).hexdigest()[:10]


def research_question_from(case: Dict[str, Any], task_text: str) -> Optional[str]:
    for k in ("research_question", "question", "task", "query"):
        v = case.get(k)
        if v:
            return str(v)
    # 从 task.md 首行尽力提取，避免编造。
    m = re.search(r"研究问题[:：]\s*(.+)", task_text)
    return m.group(1).strip() if m else None


def query_terms_from(case: Dict[str, Any], rq: Optional[str]) -> List[str]:
    raw = None
    for k in ("queries", "keywords", "search_terms", "terms"):
        v = case.get(k)
        if isinstance(v, list) and v:
            raw = v
            break
        if isinstance(v, str) and v.strip():
            raw = [v]
            break
    if raw is None and rq:
        raw = [rq]
    out: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for kk in ("term", "query", "keyword", "value"):
                    vv = item.get(kk)
                    if isinstance(vv, str) and vv.strip():
                        out.append(vv)
                        break
    return out


def search_scope_from(case: Dict[str, Any], rq: Optional[str]) -> Dict[str, Any]:
    dbs = None
    for k in ("databases", "dbs", "sources"):
        v = case.get(k)
        if isinstance(v, list):
            dbs = [str(x) for x in v]
            break
        if isinstance(v, str):
            dbs = [v]
            break
    queries = []
    for k in ("queries", "keywords", "search_terms", "terms"):
        v = case.get(k)
        if isinstance(v, list):
            queries = [str(x) for x in v if isinstance(x, str)]
            break
        if isinstance(v, str):
            queries = [v]
            break
    scope = case.get("search_scope")
    if isinstance(scope, dict):
        dbs = scope.get("databases", dbs)
        queries = scope.get("queries", queries)
    return {
        "databases": dbs or [],
        "queries": queries,
        "derived_query_terms": query_terms_from(case, rq),
    }
