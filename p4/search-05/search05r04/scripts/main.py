#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \
      --input /input --output /app/output --work /app/work

流程：
  1. 读取 /input/case.json 与 /input/task.md，立即写入 result.json 骨架（status=partial）
  2. 发现并解析 /input 下全部材料，识别数据库响应与论文
  3. 字段归一 → 去重 → 合并 → 覆盖比较 → 证据 → 不确定性 → 相关性提示
  4. 写 result.json 及 merged_records.json / evidence_map.json / coverage_report.md
  5. 若提供 --decisions，则用人工/模型仲裁结果覆盖排序、状态、局限等，再落盘
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core_impl as cs


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_skeleton(case_id: str, started: str) -> Dict[str, Any]:
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
        "runtime": {"started_at": started, "finished_at": None, "seconds": 0,
                    "pipeline_version": cs.VERSION, "python": sys.version.split()[0]},
    }


def ensure_contract_keys(res: Dict[str, Any], contract: Optional[List[str]]) -> None:
    """确保 result 含默认契约键与 case.json 提供的 output_contract 键（原样保留）。"""
    keys = contract if contract is not None else cs.DEFAULT_CONTRACT
    for k in keys:
        if k not in res:
            res[k] = None


def apply_decisions(res: Dict[str, Any], decisions: Dict[str, Any], at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts", "permission_issues",
                "missing_metadata"):
        if key in decisions and decisions[key] is not None:
            if key == "status":
                if decisions[key] in cs.STATUSES:
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


def read_case(input_dir: Path) -> Dict[str, Any]:
    """读取 case.json（容错）；返回 {case, degradations, errors}。"""
    degradations: List[str] = []
    errors: List[str] = []
    case: Dict[str, Any] = {}
    case_path = input_dir / "case.json"
    if not case_path.exists():
        errors.append("missing: %s" % case_path.as_posix())
        return {"case": case, "degradations": degradations, "errors": errors}
    obj, deg = cs.load_json(case_path)
    degradations.extend(deg)
    if obj is None:
        errors.append("case_json_unparsed: case.json")
        return {"case": case, "degradations": degradations, "errors": errors}
    if not isinstance(obj, dict):
        errors.append("case_json_not_object: case.json")
        return {"case": case, "degradations": degradations, "errors": errors}
    return {"case": obj, "degradations": degradations, "errors": errors}


def parse_materials(input_dir: Path, case: Dict[str, Any]) -> Dict[str, Any]:
    """解析输入材料；返回 sources / records / degradations / pdf_texts。"""
    sources: List[Dict[str, Any]] = []
    all_records: List[Dict[str, Any]] = []
    degradations: List[str] = []
    pdf_texts: List[Dict[str, Any]] = []

    for p in cs.discover_input_files(input_dir):
        if p.name in ("case.json", "task.md"):
            continue
        kind = cs.classify_file(p)
        rel = p.relative_to(input_dir).as_posix()
        if kind == "json":
            obj, errs = cs.load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                continue
            db_detailed = cs.classify_response(obj)
            db = cs.canonical_db(db_detailed)
            recs = cs.extract_records(obj)
            norm_recs = []
            for raw, loc in recs:
                rec = cs.normalize_record(raw, db_detailed)
                rec["_db"] = db
                rec["_source_file"] = rel
                rec["_locator"] = loc
                rec["_request"] = cs.extract_request(obj)
                rec["_raw"] = raw
                norm_recs.append(rec)
                all_records.append(rec)
            sources.append({
                "source_id": "file:" + rel, "type": "database_response",
                "db": db, "path": rel, "record_count": len(norm_recs),
                "record_locators": [r["_locator"] for r in norm_recs],
                "note": None if norm_recs else "no bibliographic records detected",
            })
        elif kind == "pdf":
            text, deg = cs.parse_pdf_text(p)
            degradations.extend(deg)
            pdf_texts.append({"path": rel, "text": text})
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        else:
            text, deg = cs.read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })

    return {"sources": sources, "records": all_records,
            "degradations": degradations, "pdf_texts": pdf_texts}


def extract_case_fields(case: Dict[str, Any]) -> Dict[str, Any]:
    """从 case.json 抽取研究问题、查询词、范围与 output_contract。"""
    rq = (case.get("research_question") or case.get("question") or case.get("task")
          or case.get("researchQuestion"))
    queries: List[str] = []
    for k in ("queries", "keywords", "search_terms", "searchTerms", "terms"):
        v = case.get(k)
        if isinstance(v, list):
            queries.extend(str(x) for x in v)
        elif isinstance(v, str) and v.strip():
            queries.append(v)
    databases: List[str] = []
    dbv = case.get("databases") or case.get("search_scope", {}).get("databases") \
        if isinstance(case.get("search_scope"), dict) else case.get("databases")
    if isinstance(dbv, list):
        databases = [str(x) for x in dbv]
    scope_obj = case.get("search_scope")
    if isinstance(scope_obj, dict):
        databases = [str(x) for x in scope_obj.get("databases", [])]
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        contract = list(contract.keys())
    elif not isinstance(contract, list):
        contract = None
    return {
        "research_question": rq if isinstance(rq, str) else None,
        "queries": queries,
        "databases": databases,
        "contract": contract,
    }


def build_result(case_id: str, started: str, case: Dict[str, Any],
                 materials: Dict[str, Any]) -> Dict[str, Any]:
    res = build_skeleton(case_id, started)
    cf = extract_case_fields(case)
    res["research_question"] = cf["research_question"]
    res["search_scope"] = {
        "databases": cf["databases"],
        "queries": cf["queries"],
        "derived_query_terms": sorted(set(
            t for q in (cf["queries"] + ([cf["research_question"]] if cf["research_question"] else []))
            for t in cs._tokenize(q))),
    }

    sources = materials["sources"]
    records = materials["records"]
    degradations = materials["degradations"]

    # 去重聚类与合并
    clusters_raw = cs.cluster_records(records)
    clusters: List[Dict[str, Any]] = []
    for idx, cluster in enumerate(clusters_raw, 1):
        merged = cs.merge_cluster(cluster, "rec:%04d" % idx)
        merged["relevance_hint"] = cs.relevance_hint(
            cf["research_question"] or "", cf["queries"], merged)
        clusters.append(merged)

    res["candidates"] = [strip_internal(c) for c in clusters]
    res["evidence_records"] = cs.build_evidence_records(sources, clusters)
    res["coverage"] = cs.compute_coverage(sources, clusters, len(records))

    # 冲突与缺失
    all_conflicts: List[Dict[str, Any]] = []
    for c in clusters:
        for conflict in c.get("conflicts", []):
            all_conflicts.append({"record_id": c["record_id"], **conflict})
    res["conflicts"] = all_conflicts
    res["missing_metadata"] = cs.build_missing_metadata(clusters)
    res["sources"] = [s for s in sources]
    res["uncertainty"] = cs.build_uncertainty(
        clusters, sources, degradations, all_conflicts, res["missing_metadata"])
    res["degradations"] = list(degradations)
    res["limitations"] = build_limitations(sources, clusters, degradations, cf)

    # 确定性排序：按 relevance_hint 等级 + 命中数 + 身份置信度
    res["ranking"] = deterministic_ranking(clusters)

    # status 判定
    res["status"] = compute_status(sources, clusters, degradations, all_conflicts)

    if not clusters:
        res["empty_results"].append({
            "detail": "no merged candidates produced",
            "cause": "input contained no bibliographic records or all files unparsed",
        })

    res["runtime"]["finished_at"] = cs.iso_now()
    return res


def strip_internal(rec: Dict[str, Any]) -> Dict[str, Any]:
    """去掉内部字段 _db/_source_file/_locator/_request/_raw，输出干净记录。"""
    out = dict(rec)
    for k in ("_db", "_source_file", "_locator", "_request", "_raw"):
        out.pop(k, None)
    return out


def deterministic_ranking(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {"high": 0, "medium": 1, "unknown": 2, "low": 3}
    conf_order = {"high": 0, "medium": 1, "low": 2}

    def key(c: Dict[str, Any]) -> Tuple[Any, ...]:
        hint = c.get("relevance_hint", {})
        return (order.get(hint.get("level", "unknown"), 9),
                -int(hint.get("hit_count", 0)),
                conf_order.get(c.get("identity_confidence", "low"), 3),
                c.get("record_id", ""))
    ranked = sorted(clusters, key=key)
    out = []
    for rank, c in enumerate(ranked, 1):
        hint = c.get("relevance_hint", {})
        out.append({
            "record_id": c.get("record_id"),
            "rank": rank,
            "relevance": hint.get("level", "unknown"),
            "rationale": "deterministic term-hit ranking: %d matched terms %s" % (
                hint.get("hit_count", 0), hint.get("matched_terms", [])),
        })
    return out


def build_limitations(sources: List[Dict[str, Any]], clusters: List[Dict[str, Any]],
                      degradations: List[str], cf: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    dbs_found = sorted({s.get("db", "generic") for s in sources
                        if s.get("type") == "database_response"})
    declared = cf["databases"]
    if declared:
        missing_dbs = [d for d in declared if d not in dbs_found]
        if missing_dbs:
            out.append("declared databases not covered by input responses: %s" % ", ".join(missing_dbs))
    for d in degradations:
        out.append("degradation: %s" % d)
    low_conf = [c.get("record_id") for c in clusters if c.get("identity_confidence") == "low"]
    if low_conf:
        out.append("records merged with low identity confidence (title-fuzzy only): %s" % ", ".join(low_conf))
    return out


def compute_status(sources: List[Dict[str, Any]], clusters: List[Dict[str, Any]],
                   degradations: List[str], conflicts: List[Dict[str, Any]]) -> str:
    if not sources and not clusters:
        return "insufficient_evidence"
    if not clusters:
        return "insufficient_evidence"
    if degradations or conflicts or any(
            c.get("identity_confidence") == "low" for c in clusters):
        return "partial"
    return "ok"


def write_aux(output_dir: Path, clusters: List[Dict[str, Any]],
              res: Dict[str, Any], materials: Dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    merged = {
        "case_id": res.get("case_id"),
        "merged_records": [strip_internal(c) for c in clusters],
        "coverage": res.get("coverage"),
    }
    (output_dir / "merged_records.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    evmap = {
        "case_id": res.get("case_id"),
        "evidence_records": res.get("evidence_records"),
        "conflicts": res.get("conflicts"),
        "missing_metadata": res.get("missing_metadata"),
        "uncertainty": res.get("uncertainty"),
    }
    (output_dir / "evidence_map.json").write_text(
        json.dumps(evmap, ensure_ascii=False, indent=2), encoding="utf-8")
    report = render_coverage_report(res, clusters, materials)
    (output_dir / "coverage_report.md").write_text(report, encoding="utf-8")


def render_coverage_report(res: Dict[str, Any], clusters: List[Dict[str, Any]],
                           materials: Dict[str, Any]) -> str:
    lines = ["# 覆盖报告", "",
             "- case_id: %s" % res.get("case_id"),
             "- status: %s" % res.get("status"),
             "- 输入记录总数: %s" % (res.get("coverage") or {}).get("total_input_records", 0),
             "- 合并后记录数: %s" % (res.get("coverage") or {}).get("merged_record_count", 0),
             "- 合并去重数: %s" % (res.get("coverage") or {}).get("duplicates_merged", 0),
             ""]
    cov = res.get("coverage") or {}
    lines.append("## 每库字段缺失")
    for db, missing in cov.get("fields_missing_per_database", {}).items():
        lines.append("- %s: %s" % (db, ", ".join(missing) if missing else "无"))
    lines.append("")
    lines.append("## 仅单库收录的记录")
    for item in cov.get("records_in_single_db_only", []):
        lines.append("- %s (%s): %s" % (item.get("record_id"), item.get("db"), item.get("title")))
    lines.append("")
    lines.append("## 候选清单")
    for c in clusters:
        lines.append("- %s [%s] %s (%s)" % (
            c.get("record_id"), c.get("identity_confidence"),
            c.get("title") or "(无标题)", c.get("year") or "年份缺失"))
    lines.append("")
    lines.append("## 降级与不确定性")
    for d in res.get("degradations", []):
        lines.append("- degradation: %s" % d)
    for lim in res.get("limitations", []):
        lines.append("- limitation: %s" % lim)
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    parser = argparse.ArgumentParser(description="多数据库联合检索流水线")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--decisions", default=None)
    parser.add_argument("--skeleton-only", action="store_true")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    started = cs.iso_now()
    case_info = read_case(input_dir)
    case = case_info["case"]
    case_id = case.get("case_id") or case.get("id") or "unknown"

    res = build_skeleton(case_id, started)
    res["errors"] = list(case_info["errors"])
    res["degradations"] = list(case_info["degradations"])

    # 先落盘骨架（立即、合法、partial）
    write_result(output_dir, res, work_dir)

    if args.skeleton_only:
        print(json.dumps({"status": res["status"],
                          "result": str(output_dir / "result.json")},
                         ensure_ascii=False))
        return 0

    # 完整解析与合并
    materials = parse_materials(input_dir, case)
    res = build_result(case_id, started, case, materials)
    res["errors"] = list(case_info["errors"])
    # 保留模型仲裁前状态
    res["change_log"].append(
        {"step": "pipeline", "status": "done", "at": cs.iso_now(),
         "detail": "parsed %d sources, %d input records, %d merged candidates" % (
             len(materials["sources"]), len(materials["records"]),
             len(res["candidates"]))})

    # 合并 decisions
    if args.decisions and Path(args.decisions).exists():
        dec_obj, _ = cs.load_json(Path(args.decisions))
        if isinstance(dec_obj, dict):
            applied = apply_decisions(res, dec_obj, cs.iso_now())
            res["change_log"].append(
                {"step": "model_refine", "status": "applied", "at": cs.iso_now(),
                 "detail": "decisions applied: %s" % ", ".join(applied)})

    # 确保契约键存在
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        contract = list(contract.keys())
    elif not isinstance(contract, list):
        contract = None
    ensure_contract_keys(res, contract)

    res["runtime"]["finished_at"] = cs.iso_now()
    write_result(output_dir, res, work_dir)
    write_aux(output_dir, res["candidates"], res, materials)

    print(json.dumps({"status": res["status"],
                      "result": str(output_dir / "result.json"),
                      "candidates": len(res["candidates"])},
                     ensure_ascii=False))
    return 0


def write_result(output_dir: Path, res: Dict[str, Any], work_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp = output_dir / "result.json.tmp"
    target = output_dir / "result.json"
    try:
        tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        # 写盘失败：尝试直接写目标，不崩溃
        try:
            target.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
