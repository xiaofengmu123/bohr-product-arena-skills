#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（纯标准库、确定性、无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json --input /input --output /app/output

流程：
  1. 读 /input/case.json 与 /input/task.md，立即写 result.json 骨架（status=partial）
  2. 发现并解析数据库响应 JSON 与论文 PDF/笔记，字段归一
  3. 去重 -> 合并 -> 覆盖比较 -> 证据溯源（逐条 locator + quote）-> 相关性提示
  4. 写 result.json / merged_records.json / evidence_map.json / coverage_report.md
  5. 若提供 --decisions，则以模型/人工仲裁覆盖排序、状态与局限后再落盘
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core_impl as ci


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def extract_output_contract(case_meta: Dict) -> List[str]:
    """从 case.json 抽取 output_contract 的全部字段键。"""
    if not isinstance(case_meta, dict):
        return list(ci.DEFAULT_CONTRACT)
    oc = case_meta.get("output_contract", case_meta.get("outputContract"))
    if oc is None:
        return list(ci.DEFAULT_CONTRACT)
    if isinstance(oc, dict):
        props = oc.get("properties") if isinstance(oc.get("properties"), dict) \
            else None
        if props:
            return list(props.keys())
        return list(oc.keys())
    if isinstance(oc, list):
        return [str(k) for k in oc if isinstance(k, str)]
    return list(ci.DEFAULT_CONTRACT)


def ensure_contract_keys(res: Dict, contract: List[str]) -> None:
    for k in ci.FIXED_FIELDS + contract:
        if k not in res:
            res[k] = None


def build_skeleton(case_id: Optional[str], started: str) -> Dict:
    return {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "candidates": [],
        "ranking": [],
        "evidence_records": [],
        "evidence_map": {
            "claims": [], "field_sources": {}, "dedup_clusters": [],
            "conflicts": [], "coverage_evidence": [],
        },
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
            "detail": "先落盘合法 partial 骨架，后续只做增量改进",
        }],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": started, "finished_at": None, "seconds": 0,
                    "pipeline_version": ci.VERSION,
                    "python": sys.version.split()[0]},
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
                if decisions[key] in ci.STATUSES:
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
        res.setdefault("change_log", []).append({
            "step": "model_refine", "status": "applied", "at": at,
            "detail": "applied decisions: " + ", ".join(applied),
        })
    return applied


# --------------------------------------------------------------------------- #
# 材料解析
# --------------------------------------------------------------------------- #
def read_case_meta(input_dir: Path) -> (Dict, List[str]):
    """读取 case.json；缺失/坏 JSON 返回空 dict 并记录错误。"""
    cp = input_dir / "case.json"
    if not cp.exists():
        return {}, ["missing: %s" % ci._rel(cp, input_dir)]
    obj, errs = ci.load_json(cp)
    if obj is None or not isinstance(obj, dict):
        return {}, errs + ["case_json_not_object"]
    # 兼容 { "case": {...} } 包装形态
    if "case" in obj and isinstance(obj["case"], dict) and \
            not any(k in obj for k in ("case_id", "research_question",
                                       "question", "task")):
        obj = obj["case"]
    return obj, errs


def _member_dict(db: str, source_file: str, pointer: str, record_index: int,
                 request: Optional[str], raw: Dict) -> Dict:
    norm = ci.normalize_record(raw, db)
    snapshot = {}
    for f in ("title", "journal", "year", "doi"):
        kp = norm["key_paths"].get(f)
        if kp:
            rv = ci._raw_at_key_path(raw, kp)
            snapshot[f] = ci.render_quote(
                rv if rv is not None else norm["values"].get(f))
    return {
        "db": db, "source_file": source_file,
        "pointer": pointer, "locator": source_file + "#" + pointer,
        "record_index": record_index, "request": request, "raw": raw,
        "values": norm["values"], "key_paths": norm["key_paths"],
        "identifiers": norm["identifiers"], "raw_snapshot": snapshot,
        "record_id": None,
    }


def parse_materials(input_dir: Path, case_meta: Dict) -> Dict[str, Any]:
    sources: List[Dict] = []
    members: List[Dict] = []
    degradations: List[str] = []
    doc_texts: Dict[str, str] = {}
    source_raw_text: Dict[str, str] = {}
    fallback_queries = _collect_queries(case_meta)

    for p in ci.discover_input_files(input_dir):
        if p.name.lower() in ("case.json", "task.md"):
            # 案例元数据与任务说明已单独读取，不作为数据库/文献材料来源
            continue
        rel = ci._rel(p, input_dir)
        kind = ci.classify_file(p)
        if kind == "json":
            text, deg = ci.read_text_bytes(p)
            degradations.extend(deg)
            source_raw_text[rel] = text
            obj, errs = ci.load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                sources.append({"source_id": "file:" + rel,
                                "type": "database_response", "db": "unknown",
                                "path": rel, "record_count": 0,
                                "record_locators": [],
                                "note": "json parse failed"})
                continue
            db_internal = ci.classify_response(obj)
            db = ci.db_label(db_internal)
            req = ci.extract_request(obj)
            recs = ci.extract_records(obj)
            locators = []
            norm_recs = []
            for raw, ptr in recs:
                idx = len(members)
                m = _member_dict(db, rel, ptr, idx,
                                 req or (fallback_queries[0]
                                         if fallback_queries else None), raw)
                norm_recs.append(m)
                members.append(m)
                locators.append(rel + "#" + ptr)
            note = None
            if db_internal == "pubmed_esearch":
                note = "esearch idlist only (no bibliographic records)"
            elif not recs:
                note = "no bibliographic records detected"
            sources.append({
                "source_id": "file:" + rel, "type": "database_response",
                "db": db, "path": rel, "record_count": len(norm_recs),
                "record_locators": locators, "note": note,
            })
        elif kind == "pdf":
            text, deg = ci.parse_pdf_text(p)
            degradations.extend(deg)
            doc_texts[rel] = text
            sources.append({"source_id": "file:" + rel, "type": "document",
                            "path": rel, "text_chars": len(text),
                            "note": ("pdftotext unavailable; path listed only"
                                     if not text else None)})
        else:
            text, deg = ci.read_text_bytes(p)
            degradations.extend(deg)
            doc_texts[rel] = text
            sources.append({"source_id": "file:" + rel, "type": "text",
                            "path": rel, "text_chars": len(text)})

    return {"sources": sources, "members": members,
            "degradations": degradations, "doc_texts": doc_texts,
            "source_raw_text": source_raw_text}


def _collect_queries(case_meta: Dict) -> List[str]:
    out: List[str] = []
    for k in ("queries", "search_terms", "keywords", "search_terms_list"):
        v = case_meta.get(k)
        if isinstance(v, list):
            out.extend(str(x) for x in v if isinstance(x, str))
        elif isinstance(v, str):
            out.append(v)
    q = case_meta.get("research_question") or case_meta.get("question") \
        or case_meta.get("task")
    if isinstance(q, str):
        out.append(q)
    dedup = []
    for x in out:
        if x.strip() and x not in dedup:
            dedup.append(x.strip())
    return dedup


def _query_terms(queries: List[str]) -> List[str]:
    """从查询与研究问题中抽取用于相关性命中的词项（过滤停用词与短词）。"""
    stopwords = {"for", "and", "the", "with", "using", "based", "from",
                 "into", "towards", "review", "systematic", "analysis",
                 "meta", "study", "of", "in", "on", "to", "a", "an", "via"}
    terms: List[str] = []
    for q in queries:
        for t in re_split_terms(q):
            tl = t.lower()
            if len(tl) >= 3 and tl not in stopwords and t not in terms:
                terms.append(t)
    return terms


def re_split_terms(s: str) -> List[str]:
    import re
    parts = re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", s)
    return [p for p in parts if p]


# --------------------------------------------------------------------------- #
# 汇总与状态
# --------------------------------------------------------------------------- #
def determine_status(res: Dict, started_ok: bool, has_errors: bool,
                     has_degradations: bool, has_conflicts: bool,
                     n_candidates: int) -> str:
    if not started_ok or has_errors:
        return "input_error"
    if n_candidates == 0:
        return "insufficient_evidence"
    if has_degradations or has_conflicts or res.get("limitations") or \
            res.get("missing_metadata") or res.get("empty_results") or \
            res.get("permission_issues"):
        return "partial"
    return "ok"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def build_search_scope(case_meta: Dict, queries: List[str]) -> Dict:
    dbs = case_meta.get("databases") or case_meta.get("search_databases") or []
    if isinstance(dbs, str):
        dbs = [dbs]
    return {
        "databases": [str(x) for x in dbs],
        "queries": list(queries),
        "derived_query_terms": _query_terms(queries),
    }


def run(input_dir: Path, output_dir: Path, work_dir: Path,
        decisions_path: Optional[Path], skeleton_only: bool,
        started: str) -> Dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_meta, case_errs = read_case_meta(input_dir)
    case_id = case_meta.get("case_id") or case_meta.get("caseId")
    if isinstance(case_id, (int, float)):
        case_id = str(case_id)
    contract = extract_output_contract(case_meta)

    res = build_skeleton(case_id, started)
    ensure_contract_keys(res, contract)
    res["change_log"].append({
        "step": "read_inputs", "status": "done", "at": started,
        "detail": "case.json=%s; task.md=%s; contract_fields=%d" % (
            "ok" if not case_errs else "error", "found" if
            (input_dir / "task.md").exists() else "missing", len(contract)),
    })
    if case_errs:
        res["errors"].extend(case_errs)

    # task.md 作为事实依据，不作为答案来源
    task_text = ""
    if (input_dir / "task.md").exists():
        task_text, _ = ci.read_text_bytes(input_dir / "task.md")

    if skeleton_only:
        res["change_log"].append({
            "step": "skeleton_only", "status": "written", "at": started,
            "detail": "仅落盘骨架，未解析材料"})
        write_json(output_dir / "result.json", res)
        return res

    parsed = parse_materials(input_dir, case_meta)
    members: List[Dict] = parsed["members"]
    sources: List[Dict] = parsed["sources"]
    degradations: List[str] = parsed["degradations"]
    doc_texts: Dict[str, str] = parsed["doc_texts"]
    source_raw_text: Dict[str, str] = parsed["source_raw_text"]
    queries = _collect_queries(case_meta)

    res["research_question"] = (case_meta.get("research_question")
                                or case_meta.get("question")
                                or case_meta.get("task"))
    res["search_scope"] = build_search_scope(case_meta, queries)
    res["sources"] = sources
    res["degradations"] = list(dict.fromkeys(degradations))

    clusters, edges = ci.dedup_records(members)

    # 分配 record_id（确定性：簇按最小成员序号排序，成员按 db 优先级再原序）
    ordered_clusters = sorted(clusters, key=lambda c: min(c))
    for ci_idx, cluster in enumerate(ordered_clusters):
        rec_id = "rec:%d" % (ci_idx + 1)
        for i in cluster:
            members[i]["record_id"] = rec_id

    # 文档提及（reference graph）：文本里 DOI/PMID 逐字抽取，并链接到已编号候选
    mention_evs, ref_links = _scan_document_mentions(
        doc_texts, members, input_dir)

    merged_records: List[Dict] = []
    for rec_id_num, cluster in enumerate(ordered_clusters):
        ms = [members[i] for i in cluster]
        ms.sort(key=lambda m: (ci.DB_ORDER.get(m["db"], 9), m["record_index"]))
        merged = ci.merge_cluster(ms)
        rec_id = "rec:%d" % (rec_id_num + 1)
        merged["record_id"] = rec_id
        merged_records.append(merged)

    merged_records.sort(key=lambda m: m["record_id"])

    duplicates_merged = sum(len(c) for c in ordered_clusters) - len(merged_records)
    cov = ci.build_coverage(members, len(merged_records), duplicates_merged)

    evb = ci.EvidenceBuilder()
    field_ev, conflicted = ci.build_evidence(
        evb, members, merged_records, ordered_clusters, edges, source_raw_text)
    # 文档提及证据并入
    for mv in mention_evs:
        evb.add(mv)
    evb.finalize()
    ci.build_claims(evb, members, merged_records, field_ev, conflicted)

    # 相关性提示 + 缺省排序
    terms = _query_terms(queries)
    for merged in merged_records:
        merged["relevance_hint"] = ci.relevance_hint(merged, terms)
    merged_records.sort(key=lambda m: (-m["relevance_hint"]["score"],
                                        (m.get("title") or "").lower()))

    ranking: List[Dict] = []
    for rank, merged in enumerate(merged_records):
        sc = merged["relevance_hint"]["score"]
        rel = "high" if sc >= 3 else ("medium" if sc >= 1 else "low")
        ranking.append({
            "record_id": merged["record_id"], "rank": rank + 1,
            "relevance": rel,
            "rationale": ("脚本词项命中暂排（%s）" %
                          merged["relevance_hint"]["rationale"]),
        })

    # missing_metadata
    missing_meta: List[Dict] = []
    for db in cov["databases"]:
        for item in cov["systematically_missing_fields"]:
            if item["db"] == db and item["records_missing"] > 0:
                missing_meta.append({
                    "db": db, "field": item["field"],
                    "records_missing": item["records_missing"],
                    "note": "字段缺失，填 null；见 coverage_evidence",
                })

    empty_results: List[Dict] = []
    for s in sources:
        if s.get("record_count") == 0 and s.get("type") == "database_response":
            empty_results.append({
                "source_file": s["path"], "db": s.get("db"),
                "note": s.get("note") or "no records",
            })

    res["candidates"] = merged_records
    res["ranking"] = ranking
    res["evidence_records"] = evb.evs
    res["evidence_map"] = {
        "claims": evb.claims,
        "field_sources": _field_sources_map(merged_records),
        "dedup_clusters": evb.dedup_clusters,
        "conflicts": evb.conflicts,
        "coverage_evidence": evb.coverage_evidence,
        "reference_links": ref_links,
    }
    res["coverage"] = cov
    res["conflicts"] = evb.conflicts
    res["missing_metadata"] = missing_meta
    res["empty_results"] = empty_results
    res["uncertainty"] = {
        "items": _uncertainty_items(res),
        "note": ("字段与去重由确定性规则生成；相关性与排序为词项命中提示，"
                 "需模型/人工 refine；各条结论均带 locator+quote 可复核"),
    }
    res["limitations"] = _limitations(res, task_text)
    res["change_log"].append({
        "step": "parse_merge_evidence", "status": "done", "at": started,
        "detail": "members=%d clusters=%d merged=%d evidence=%d" % (
            len(members), len(ordered_clusters), len(merged_records),
            len(evb.evs)),
    })

    # 决定状态
    started_ok = bool(case_meta) and not any(
        e.startswith("missing:") or e == "case_json_not_object" for e in res["errors"])
    status = determine_status(
        res, started_ok, has_errors=bool(
            [e for e in res["errors"] if e not in ("", )]),
        has_degradations=bool(res["degradations"]),
        has_conflicts=bool(res["conflicts"]),
        n_candidates=len(merged_records))
    res["status"] = status

    # 模型/人工仲裁
    decisions = None
    if decisions_path and decisions_path.exists():
        try:
            decisions = json.loads(
                decisions_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            res["errors"].append("decisions_json_error: %s" % exc)
    if decisions is not None:
        applied = apply_decisions(res, decisions, started)
        res["change_log"].append({
            "step": "decisions", "status": "applied" if applied else "none",
            "at": started, "detail": "applied: " + (", ".join(applied)
                                                        if applied else "-"),
        })

    # 收尾计时
    finished = iso_now()
    res["runtime"]["finished_at"] = finished
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        res["runtime"]["seconds"] = max(0, int((t1 - t0).total_seconds()))
    except (ValueError, TypeError):
        res["runtime"]["seconds"] = None
    res["change_log"].append({
        "step": "finalize", "status": "written", "at": finished,
        "detail": "result.json + merged_records.json + evidence_map.json "
                  "+ coverage_report.md"})

    # 写产物
    write_json(output_dir / "result.json", res)
    write_json(output_dir / "merged_records.json", {
        "case_id": case_id, "records": merged_records})
    write_json(output_dir / "evidence_map.json", {
        "case_id": case_id, "evidence_records": evb.evs,
        "evidence_map": res["evidence_map"]})
    (output_dir / "coverage_report.md").write_text(
        ci.render_coverage_report(res, "contract=%d keys" % len(contract)),
        encoding="utf-8")

    return res


# --------------------------------------------------------------------------- #
# 文档提及扫描（参考图：PDF/笔记 -> 候选）
# --------------------------------------------------------------------------- #
def _scan_document_mentions(doc_texts: Dict[str, str], members: List[Dict],
                            input_dir: Path) -> (List[Dict], List[Dict]):
    import re
    evs: List[Dict] = []
    links: List[Dict] = []
    doi_re = re.compile(r"10\.\d{4,9}/[^\s\"'<>,\u4e00-\u9fff]+")
    pmid_re = re.compile(r"(?:PMID|Pubmed ID|PubMed ID)\s*[:：]?\s*(\d{7,9})")
    by_doi = {}
    by_pmid = {}
    for m in members:
        if m["values"].get("doi"):
            by_doi.setdefault(m["values"]["doi"], []).append(m["record_id"])
        if m["values"].get("pmid"):
            by_pmid.setdefault(m["values"]["pmid"], []).append(m["record_id"])
    for rel, text in sorted(doc_texts.items()):
        lines = text.splitlines()
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            for m in doi_re.finditer(line):
                doi = m.group(0).rstrip(".,;")
                rec_ids = sorted({r for r in by_doi.get(doi.lower(), [])
                                  if r})
                evs.append({
                    "type": "document_mention", "db": None,
                    "source_file": rel, "field": "doi",
                    "locator": "%s#L%d" % (rel, lineno),
                    "quote": line,
                    "identifier": doi, "linked_record_ids": rec_ids,
                    "record_index": None,
                })
                if rec_ids:
                    links.append({"source_file": rel, "line": lineno,
                                  "identifier": doi, "record_ids": rec_ids})
            for m in pmid_re.finditer(line):
                pmid = m.group(1)
                rec_ids = sorted({r for r in by_pmid.get(pmid, []) if r})
                evs.append({
                    "type": "document_mention", "db": None,
                    "source_file": rel, "field": "pmid",
                    "locator": "%s#L%d" % (rel, lineno),
                    "quote": line,
                    "identifier": "PMID:" + pmid,
                    "linked_record_ids": rec_ids, "record_index": None,
                })
                if rec_ids:
                    links.append({"source_file": rel, "line": lineno,
                                  "identifier": "PMID:" + pmid,
                                  "record_ids": rec_ids})
    return evs, links


def _field_sources_map(merged_records: List[Dict]) -> Dict:
    out: Dict[str, Dict[str, List[str]]] = {}
    for rec in merged_records:
        fs = {}
        for field, entries in (rec.get("field_sources") or {}).items():
            fs[field] = [e.get("evidence_id") for e in entries
                         if e.get("evidence_id")]
        out[rec["record_id"]] = fs
    return out


def _uncertainty_items(res: Dict) -> List[Dict]:
    items: List[Dict] = []
    if res.get("conflicts"):
        items.append({"cause": "inter_source_conflict",
                      "detail": "%d 个字段级冲突待人工复核" %
                      len(res["conflicts"])})
    if res.get("missing_metadata"):
        items.append({"cause": "missing_metadata",
                      "detail": "%d 项系统性缺失字段" %
                      len(res["missing_metadata"])})
    if res.get("degradations"):
        items.append({"cause": "degradation",
                      "detail": "%d 项降级" % len(res["degradations"])})
    return items


def _limitations(res: Dict, task_text: str) -> List[str]:
    out: List[str] = []
    for d in res.get("degradations") or []:
        out.append("降级：%s" % d)
    if not res.get("candidates") and (res.get("sources") or []):
        out.append("未发现候选项；不补造文献")
    if not task_text.strip():
        out.append("task.md 为空：任务说明未作为事实依据")
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(
        description="多数据库联合检索流水线（证据溯源强化）")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--skeleton-only", action="store_true")
    args = ap.parse_args()

    started = iso_now()
    res = run(Path(args.input), Path(args.output), Path(args.work),
              Path(args.decisions) if args.decisions else None,
              args.skeleton_only, started)
    print(json.dumps({
        "case_id": res.get("case_id"),
        "status": res.get("status"),
        "candidates": len(res.get("candidates") or []),
        "evidence_records": len(res.get("evidence_records") or []),
        "result": str(Path(args.output) / "result.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())