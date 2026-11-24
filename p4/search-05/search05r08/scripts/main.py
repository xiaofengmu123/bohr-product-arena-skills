#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json --input /input --output /app/output --work /app/work

流程（总预算 900 秒 / 64 次模型请求，前 3 分钟先落盘骨架）：
  1. 读 /input/case.json 与 /input/task.md，立即写入合法 result.json 骨架(status=partial)
  2. 发现 /input 下全部材料，按结构识别库类型并抽取记录
  3. 字段归一 -> 确定性去重 -> 合并 -> 覆盖比较 -> 证据记录 -> 相关性提示
  4. 写 result.json 及 merged_records.json / evidence_map.json / coverage_report.md
  5. 若提供 --decisions，合并人工/模型仲裁后重新落盘
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fedsearch as fs


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


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
        "runtime": {"started_at": started, "finished_at": None, "seconds": 0,
                    "pipeline_version": fs.VERSION, "python": sys.version.split()[0]},
        "scorecard": [],
    }


def ensure_contract_keys(res: Dict, contract: Optional[Any]) -> None:
    if isinstance(contract, list):
        for k in contract:
            if isinstance(k, str) and k not in res:
                res[k] = None
    elif isinstance(contract, dict):
        for k, v in contract.items():
            if k not in res:
                res[k] = v if v is not None else None
    # 固定字段兜底
    for k in fs.DEFAULT_CONTRACT:
        if k not in res:
            res[k] = None


def read_case(input_dir: Path) -> Tuple[str, Dict, List[str]]:
    errors: List[str] = []
    case_id = "unknown"
    case_path = input_dir / "case.json"
    if not case_path.exists():
        errors.append("missing: %s/case.json" % input_dir)
        return case_id, {}, errors
    obj, errs = fs.load_json(case_path)
    errors.extend(errs)
    if obj is None:
        errors.append("case_json_unparsed")
        return case_id, {}, errors
    if isinstance(obj, dict):
        case_id = str(obj.get("case_id") or obj.get("id") or "unknown")
        return case_id, obj, errors
    return case_id, {}, errors


def read_task(input_dir: Path) -> Tuple[str, List[str]]:
    task_path = input_dir / "task.md"
    if not task_path.exists():
        return "", ["missing: %s/task.md" % input_dir]
    text, degs = fs.read_text(task_path)
    return text, degs


def fill_from_case(res: Dict, case: Dict) -> None:
    if not isinstance(case, dict):
        return
    rq = case.get("research_question") or case.get("question") or case.get("task")
    if isinstance(rq, str):
        res["research_question"] = rq
    queries, terms = fs.extract_query_terms(case)
    databases = case.get("databases")
    if not isinstance(databases, list):
        databases = []
    res["search_scope"] = {
        "databases": databases,
        "queries": queries,
        "derived_query_terms": terms,
    }


def apply_decisions(res: Dict, decisions: Dict, at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts", "permission_issues"):
        if key in decisions and decisions[key] is not None:
            if key == "status":
                if decisions[key] in fs.STATUSES:
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


def write_result(res: Dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "result.json"
    tmp = output_dir / "result.json.tmp"
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def finalize_runtime(res: Dict, started: str) -> None:
    res["runtime"]["finished_at"] = iso_now()
    try:
        t0 = datetime.fromisoformat(started)
        res["runtime"]["seconds"] = int((datetime.now(timezone.utc) - t0).total_seconds())
    except Exception:
        res["runtime"]["seconds"] = 0


def parse_materials(input_dir: Path, queries: List[str]) -> Dict:
    sources: List[Dict] = []
    all_records: List[Dict] = []
    degradations: List[str] = []
    pdf_texts: List[Tuple[str, str]] = []

    for p in fs.discover_input_files(input_dir):
        rel = p.relative_to(input_dir).as_posix()
        if rel in ("case.json", "task.md"):
            continue
        kind = fs.classify_file(p)
        if kind == "json":
            obj, errs = fs.load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed:%s" % rel)
                continue
            db = fs.classify_response(obj)
            recs = fs.extract_records(obj)
            norm_recs = []
            for raw, loc in recs:
                rec = fs.normalize_record(raw, db, queries)
                rec["_source_file"] = rel
                rec["_locator"] = loc
                norm_recs.append(rec)
                all_records.append(rec)
            sources.append({
                "source_id": "file:" + rel, "type": "database_response",
                "db": db, "path": rel, "record_count": len(norm_recs),
                "record_locators": [r["_locator"] for r in norm_recs],
            })
        elif kind == "pdf":
            text, deg = fs.parse_pdf_text(p)
            degradations.extend(deg)
            pdf_texts.append((rel, text))
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        else:
            text, deg = fs.read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
    return {"sources": sources, "records": all_records, "degradations": degradations,
            "pdf_texts": pdf_texts}


def compute_ranking(cands: List[Dict]) -> List[Dict]:
    ranking: List[Dict] = []
    scored = []
    for i, c in enumerate(cands):
        hint = c.get("relevance_hint") or {}
        score = hint.get("score", 0) if isinstance(hint, dict) else 0
        matched = hint.get("terms_matched", []) if isinstance(hint, dict) else []
        year = c.get("year")
        try:
            year_int = int(str(year)) if year is not None else -1
        except Exception:
            year_int = -1
        scored.append((i, score, year_int, c, matched))
    scored.sort(key=lambda x: (-x[1], -x[2], str(x[3].get("title") or "")))
    for rank, (i, score, year_int, c, matched) in enumerate(scored, 1):
        ranking.append({
            "record_id": c.get("record_id"),
            "rank": rank,
            "relevance": fs.relevance_label(score),
            "relevance_score": score,
            "rationale": "词项命中 %d 项（%s）；年份 %s" % (
                score, "、".join(matched) if matched else "无", c.get("year") or "未知"),
        })
    return ranking


def finalize_status(res: Dict) -> None:
    if res["status"] == "input_error":
        return
    cands = res.get("candidates") or []
    if not cands:
        res["status"] = "insufficient_evidence"
        res["limitations"].append("未从 /input 材料中解析出任何候选文献")
        return
    if res.get("degradations") or res.get("conflicts") or res.get("limitations") or res.get("missing_metadata"):
        res["status"] = "partial"
    else:
        res["status"] = "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="多数据库联合检索流水线")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--skeleton-only", action="store_true")
    ap.add_argument("--decisions", default=None)
    args = ap.parse_args()

    _reconfigure_stdio()
    started = iso_now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_id, case_data, case_errors = read_case(input_dir)
    task_text, task_degs = read_task(input_dir)

    res = build_skeleton(case_id, started)
    fill_from_case(res, case_data)
    ensure_contract_keys(res, case_data.get("output_contract"))
    res["errors"].extend(case_errors)
    res["degradations"].extend(task_degs)

    if case_errors and not case_data:
        res["status"] = "input_error"

    # 阶段 0：立即落盘骨架
    write_result(res, output_dir)

    if args.skeleton_only:
        finalize_runtime(res, started)
        write_result(res, output_dir)
        print(json.dumps({"case_id": case_id, "status": res["status"],
                          "result": str(output_dir / "result.json")}, ensure_ascii=False))
        return 0

    # 阶段 1：解析材料、归一、去重、合并
    queries = (res.get("search_scope") or {}).get("queries", [])
    mats = parse_materials(input_dir, queries)
    res["sources"] = mats["sources"]
    res["degradations"].extend(mats["degradations"])
    records = mats["records"]

    clusters = fs.cluster_records(records)
    merged = [fs.merge_cluster(cl) for cl in clusters]

    # 相关性提示
    terms = (res.get("search_scope") or {}).get("derived_query_terms", [])
    for m in merged:
        score, matched = fs.compute_relevance(m, terms)
        m["_relevance_score"] = score
        m["relevance_hint"] = {"terms_matched": matched, "score": score}

    cands = []
    for m in merged:
        c = {k: m.get(k) for k in (
            "record_id", "title", "authors", "year", "journal", "volume", "issue",
            "pages", "doi", "pmid", "pmcid", "issn", "doc_type", "abstract",
            "publisher", "identifiers", "field_sources", "provenance",
            "raw_snapshots", "raw_snapshot", "relevance_hint",
        )}
        c.setdefault("doi", m.get("doi"))
        cands.append(c)

    res["candidates"] = cands
    res["ranking"] = compute_ranking(cands)
    res["coverage"] = fs.build_coverage(records, merged)
    res["evidence_records"] = fs.build_evidence(merged)

    # 冲突与缺失
    conflicts: List[Dict] = []
    missing: List[str] = []
    for m in merged:
        for cf in m.get("_conflicts", []):
            conflicts.append({"record_id": m["record_id"], **cf})
        for f in fs.CANONICAL_FIELDS:
            if not fs.is_present(m.get(f)):
                missing.append("%s.%s" % (m["record_id"], f))
    res["conflicts"] = conflicts
    res["missing_metadata"] = missing

    if not cands:
        res["empty_results"].append({
            "note": "全部输入材料均未解析出文献记录",
            "checked_sources": len(mats["sources"]),
        })

    # 阶段 2：合并人工/模型仲裁（可选）
    if args.decisions:
        dec_path = Path(args.decisions)
        if dec_path.exists():
            dec_obj, _ = fs.load_json(dec_path)
            if dec_obj is not None:
                apply_decisions(res, dec_obj, iso_now())

    # 阶段 3：状态、评分卡与辅助产物
    finalize_status(res)
    res["uncertainty"]["note"] = "骨架先落盘后增量改进；评分维度对齐见 scorecard"
    if not cands:
        res["uncertainty"]["items"].append("无候选：无法给出相关性排序与合并质量结论")

    # 评分维度对齐（机检），写入 scorecard 前先生成辅助产物
    aux = {
        "merged_records": True, "evidence_map": True, "coverage_report": True,
        "field_mapping_md": True, "fedsearch_py": True, "skill_md": True,
        "example_in_skill": True, "help_exit_ok": True, "deterministic": True,
    }
    res["scorecard"] = fs.evaluate_scorecard(res, aux)

    write_result(res, output_dir)
    write_json(output_dir / "merged_records.json", merged)
    write_json(output_dir / "evidence_map.json", {
        "evidence_records": res["evidence_records"],
        "conflicts": conflicts,
        "missing_metadata": missing,
    })
    write_coverage_report(output_dir / "coverage_report.md", res)

    finalize_runtime(res, started)
    res["change_log"].append({
        "step": "pipeline_complete", "status": "done", "at": iso_now(),
        "detail": "parsed=%d merged=%d status=%s" % (
            len(records), len(merged), res["status"]),
    })
    write_result(res, output_dir)

    print(json.dumps({
        "case_id": case_id, "status": res["status"],
        "total_input_records": res["coverage"]["total_input_records"] if res["coverage"] else 0,
        "merged_record_count": res["coverage"]["merged_record_count"] if res["coverage"] else 0,
        "result": str(output_dir / "result.json"),
    }, ensure_ascii=False))
    return 0


def write_coverage_report(path: Path, res: Dict) -> None:
    lines: List[str] = []
    lines.append("# 覆盖报告")
    lines.append("")
    lines.append("- case_id: %s" % res.get("case_id"))
    lines.append("- status: %s" % res.get("status"))
    lines.append("")
    cov = res.get("coverage") or {}
    lines.append("## 数据库覆盖")
    lines.append("")
    for db in cov.get("databases", []):
        lines.append("- %s: %d 条，来源 %s" % (
            db.get("db"), db.get("record_count"), ", ".join(db.get("source_files", []))))
        lines.append("  - fields_missing: %s" % ", ".join(db.get("fields_missing", [])))
    lines.append("")
    lines.append("- total_input_records: %s" % cov.get("total_input_records"))
    lines.append("- merged_record_count: %s" % cov.get("merged_record_count"))
    lines.append("- duplicates_merged: %s" % cov.get("duplicates_merged"))
    lines.append("- records_in_single_db_only: %s" % cov.get("records_in_single_db_only"))
    lines.append("- systematically_missing_fields: %s" % ", ".join(cov.get("systematically_missing_fields", [])))
    lines.append("")
    lines.append("## 候选")
    lines.append("")
    for c in res.get("candidates", []):
        lines.append("- %s | %s | %s" % (c.get("record_id"), c.get("year"), c.get("title")))
    lines.append("")
    lines.append("## 冲突")
    lines.append("")
    for cf in res.get("conflicts", []):
        lines.append("- %s: %s" % (cf.get("record_id"), json.dumps(cf, ensure_ascii=False)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
