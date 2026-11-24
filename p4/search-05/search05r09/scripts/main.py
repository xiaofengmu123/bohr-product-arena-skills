#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \\
      --input /input --output /app/output --work /app/work

流程（骨架优先，之后增量改进）：
  1. 读 case.json/task.md，立即写 result.json 骨架（status=partial）
  2. 发现材料 -> 识别类型 -> 抽取记录 -> 归一 -> 去重 -> 合并
  3. 覆盖比较、证据记录、相关性提示、确定性排序
  4. 写 result.json 与 merged_records.json / evidence_map.json / coverage_report.md
  5. --decisions 合并模型/人工仲裁后再落盘
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import fedsearch as fs


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_skeleton(case_id: str, started: str, errs: List[str]) -> Dict[str, Any]:
    status = "input_error" if errs and case_id == "unknown" else "partial"
    return {
        "case_id": case_id,
        "status": status,
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
        "errors": list(errs),
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": started, "finished_at": None, "seconds": 0,
                    "pipeline_version": fs.VERSION,
                    "python": sys.version.split()[0]},
    }


def ensure_contract_keys(res: Dict[str, Any],
                         contract: Optional[List[str]]) -> None:
    keys = contract if contract is not None else fs.DEFAULT_CONTRACT
    for k in keys:
        if k not in res:
            res[k] = None


def apply_decisions(res: Dict[str, Any], decisions: Dict[str, Any],
                    at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts", "permission_issues"):
        if key not in decisions or decisions[key] is None:
            continue
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
        else:
            res[key] = decisions[key]
            applied.append(key)
    if applied:
        res.setdefault("change_log", []).append(
            {"step": "model_refine", "status": "applied", "at": at,
             "detail": "applied decisions: " + ", ".join(applied)})
    return applied


def run_pipeline(input_dir: Path, work_dir: Path) -> Dict[str, Any]:
    """完整解析并返回可落盘的 result；不写盘。"""
    sources: List[Dict[str, Any]] = []
    all_records: List[Dict[str, Any]] = []
    degradations: List[str] = []
    errors: List[str] = []

    for p in fs.discover_input_files(input_dir):
        rel = p.relative_to(input_dir).as_posix()
        if rel.split("/")[-1] in ("case.json", "task.md"):
            continue
        kind = fs.classify_file(p)
        if kind == "json":
            obj, errs = fs.load_json(p)
            errors.extend(errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                continue
            db = fs.classify_response(obj)
            recs = fs.extract_records(obj)
            if recs:
                norm = []
                for raw, loc in recs:
                    rec = fs.normalize_record(raw, db)
                    rec["_db"] = db
                    rec["_source_file"] = rel
                    rec["_locator"] = loc
                    rec["_request"] = fs.extract_request(obj)
                    rec["_raw"] = raw
                    norm.append(rec)
                    all_records.append(rec)
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": len(norm),
                    "record_locators": [r["_locator"] for r in norm],
                })
            else:
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": 0,
                    "record_locators": [], "note": "no bibliographic records",
                })
        elif kind == "pdf":
            text, deg = fs.parse_pdf_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        elif kind == "text":
            text, deg = fs.read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
        else:
            sources.append({
                "source_id": "file:" + rel, "type": "other", "path": rel,
                "note": "skipped_unsupported",
            })

    clusters = fs.dedup(all_records)
    merged: List[Dict[str, Any]] = []
    for idxs in clusters:
        m = fs.merge_cluster(all_records, idxs)
        m["_record_id"] = "rec:%04d" % (len(merged) + 1)
        merged.append(m)

    query_terms = fs.derive_query_terms(
        _case.get("queries", []), _case.get("research_question"))
    candidates = [fs.finalize_candidate(m, i + 1, query_terms)
                  for i, m in enumerate(merged)]
    ranking = fs.default_ranking(candidates)
    coverage = fs.build_coverage(sources, all_records, merged)
    evidence = fs.build_evidence(merged)

    conflicts = []
    missing_metadata = []
    for m in merged:
        conflicts.extend(m["_conflicts"])
        for f in fs.CANONICAL_FIELDS:
            if m.get(f) in (None, "", []) and f not in ("abstract",):
                missing_metadata.append({
                    "record_id": m["_record_id"], "field": f,
                    "note": "该字段在所有来源均缺失",
                })

    status = "ok"
    limitations: List[str] = []
    if degradations:
        status = "partial"
        limitations.append("存在降级：" + "; ".join(degradations))
    if conflicts:
        status = "partial"
    if missing_metadata:
        status = "partial"
    if not all_records and not any(s.get("type") == "document" for s in sources):
        status = "insufficient_evidence"
        limitations.append("未发现任何文献记录或材料，无法产出候选集")
    declared = {d.lower() for d in _case.get("databases", [])}
    found = {s.get("db") for s in sources if s.get("type") == "database_response"}
    if declared and not declared.issubset({x.lower() for x in found}):
        status = "partial"
        limitations.append("声明库未全部覆盖：declared=%s found=%s" % (
            ",".join(sorted(declared)), ",".join(sorted(found))))

    empty_results = []
    for s in sources:
        if s.get("type") == "database_response" and s.get("record_count", 0) == 0:
            empty_results.append({"db": s.get("db"), "path": s.get("path"),
                                  "note": "响应无文献记录"})

    return {
        "search_scope": {
            "databases": sorted(found),
            "declared_databases": sorted(_case.get("databases", [])),
            "queries": sorted(_case.get("queries", [])),
            "derived_query_terms": query_terms,
        },
        "candidates": candidates,
        "ranking": ranking,
        "evidence_records": evidence,
        "coverage": coverage,
        "empty_results": empty_results,
        "conflicts": conflicts,
        "missing_metadata": missing_metadata,
        "permission_issues": [],
        "limitations": limitations,
        "sources": sources,
        "status": status,
        "degradations": degradations,
        "errors": errors,
    }


def write_aux(work_dir: Path, res: Dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "merged_records.json").write_text(
        json.dumps(res["candidates"], ensure_ascii=False, indent=2),
        encoding="utf-8")
    evidence_map = {
        "evidence_records": res["evidence_records"],
        "conflicts": res["conflicts"],
        "missing_metadata": res["missing_metadata"],
    }
    (work_dir / "evidence_map.json").write_text(
        json.dumps(evidence_map, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _coverage_markdown(res)
    (work_dir / "coverage_report.md").write_text(report, encoding="utf-8")


def _coverage_markdown(res: Dict[str, Any]) -> str:
    cov = res.get("coverage") or {}
    lines = ["# 覆盖报告", "",
             "- 输入记录数：%s" % cov.get("total_input_records"),
             "- 合并后记录数：%s" % cov.get("merged_record_count"),
             "- 去重合并数：%s" % cov.get("duplicates_merged"), ""]
    per = cov.get("per_database") or {}
    if per:
        lines.append("## 各库覆盖")
        lines.append("")
        lines.append("| 库 | 记录数 | 系统性缺失字段 |")
        lines.append("|---|---|---|")
        for db, info in sorted(per.items()):
            lines.append("| %s | %s | %s |" % (
                db, info.get("total_records"),
                ", ".join(info.get("fields_missing", [])) or "无"))
        lines.append("")
    single = cov.get("records_in_single_db_only") or {}
    if single:
        lines.append("## 仅单库收录")
        lines.append("")
        for db, ids in sorted(single.items()):
            lines.append("- %s: %s" % (db, ", ".join(ids)))
        lines.append("")
    if res.get("limitations"):
        lines.append("## 局限")
        lines.append("")
        for x in res["limitations"]:
            lines.append("- %s" % x)
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(description="多数据库联合检索流水线")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--skeleton-only", action="store_true")
    ap.add_argument("--decisions", default=None)
    args = ap.parse_args(argv)

    started = iso_now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    global _case
    _case, case_errs = fs.load_case(input_dir)
    errs = list(case_errs)

    skeleton = build_skeleton(_case.get("case_id", "unknown"), started, errs)
    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.skeleton_only:
        print(json.dumps({"case_id": skeleton["case_id"],
                          "status": skeleton["status"],
                          "result": str(result_path)}, ensure_ascii=False))
        return 0

    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.is_file():
        task_text, tdeg = fs.read_text(task_path)
        errs.extend(tdeg)

    full = run_pipeline(input_dir, work_dir)
    res = dict(skeleton)
    res.update(full)
    if task_text:
        res["sources"].append({
            "source_id": "file:task.md", "type": "task", "path": "task.md",
            "text_chars": len(task_text),
        })
    if _case.get("present"):
        res["sources"].append({
            "source_id": "file:case.json", "type": "case_metadata",
            "path": "case.json",
        })
    if not res["research_question"]:
        res["research_question"] = _case.get("research_question")

    ensure_contract_keys(res, _case.get("contract"))
    res["change_log"].append({
        "step": "pipeline", "status": "done", "at": iso_now(),
        "detail": "解析 %d 个输入记录，合并为 %d 条候选" % (
            (res.get("coverage") or {}).get("total_input_records", 0),
            len(res.get("candidates", []))),
    })

    if args.decisions:
        dec_path = Path(args.decisions)
        if dec_path.is_file():
            decisions, derrs = fs.load_json(dec_path)
            errs.extend(derrs)
            if isinstance(decisions, dict):
                applied = apply_decisions(res, decisions, iso_now())
                if applied:
                    res["change_log"].append({
                        "step": "decisions_loaded", "status": "applied",
                        "at": iso_now(), "detail": ",".join(applied)})
        else:
            res["degradations"].append("decisions_file_missing: %s" % args.decisions)

    res["errors"] = sorted(set(res.get("errors", []) + errs))
    res["uncertainty"]["note"] = (
        "证据均来自 /input 实际文件；字段级来源见 candidates[].field_sources")
    if res["missing_metadata"]:
        res["uncertainty"]["items"].append(
            "存在字段缺失，见 missing_metadata，未补造值")
    if res["conflicts"]:
        res["uncertainty"]["items"].append(
            "存在跨库字段冲突，见 conflicts，已按来源优先级消解")
    if not res.get("candidates"):
        res["status"] = res["status"] if res["status"] in (
            "input_error", "insufficient_evidence") else "insufficient_evidence"

    finished = iso_now()
    t0 = datetime.fromisoformat(started)
    t1 = datetime.fromisoformat(finished)
    res["runtime"]["finished_at"] = finished
    res["runtime"]["seconds"] = max(0.0, (t1 - t0).total_seconds())

    result_path.write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    write_aux(work_dir, res)

    print(json.dumps({"case_id": res["case_id"], "status": res["status"],
                      "candidates": len(res.get("candidates", [])),
                      "result": str(result_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
