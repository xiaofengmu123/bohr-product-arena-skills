#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验解释 Skill 主入口：骨架 / 画像 / 模型 / 渲染 / 校验 / 定稿。

仅使用 Python 标准库。运行时路径约定：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work

用法：
  python scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python scripts/main.py profile  --input /input --output /app/output --work /app/work
  python scripts/main.py model    --input /input --output /app/output --work /app/work
  python scripts/main.py render   --output /app/output --work /app/work
  python scripts/main.py validate --input /app/output/result.json
  python scripts/main.py finalize --input /app/output/result.json
"""

import argparse
import json
import os
import sys

from core_impl import (
    DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK,
    STATUSES, TOP_KEYS, INTERPRETATION_KEYS,
    now_ts, load_json_safe, write_json_atomic, write_text_atomic,
    discover_files, resolve_case_id, merge_output_contract,
    profile_table, build_skeleton, build_model_template, build_figure_template,
    section_satisfied, _has_evidence_list,
)


def _load_case(input_dir):
    return load_json_safe(os.path.join(input_dir, "case.json")) or {}


def cmd_skeleton(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case_id = resolve_case_id(args.input)
    case_obj = _load_case(args.input)

    result = build_skeleton(case_id)
    merged = merge_output_contract(result, case_obj)
    if merged:
        result["change_log"].append({
            "ts": now_ts(), "phase": "skeleton",
            "action": "output_contract keys backfilled", "keys": merged,
        })

    write_json_atomic(os.path.join(args.output, "result.json"), result)

    plan = {
        "case_id": case_id,
        "schema_version": "1.0",
        "created_at": now_ts(),
        "phases": [
            {"name": "profile", "input": "/input", "output": "work/data_profile.json",
             "budget_seconds": 300, "status": "pending"},
            {"name": "model", "input": "work/data_profile.json",
             "output": "work/model.json + work/figure_model.json",
             "budget_seconds": 300, "status": "pending"},
            {"name": "render", "input": "work/model.json",
             "output": "output/result.json + report.md", "status": "pending"},
            {"name": "validate", "input": "output/result.json",
             "output": "output/validation.json", "status": "pending"},
        ],
        "interpretation_targets": [],
        "evidence_map_targets": [],
    }
    write_json_atomic(os.path.join(args.work, "plan.json"), plan)

    report = _skeleton_report(case_id)
    write_text_atomic(os.path.join(args.output, "report.md"), report)

    print("skeleton ok: %s" % os.path.join(args.output, "result.json"))
    return 0


def _skeleton_report(case_id):
    return (
        "# 实验解释报告（骨架）\n\n"
        "- case_id: `%s`\n"
        "- status: partial（本文件为骨架，待模型渲染后自动生成）\n\n"
        "> 说明：正式结论将在 `render` 阶段由 model.json 生成，"
        "每条结论附 `model_ref`、`locator` 与逐字 `quote`。\n" % case_id
    )


def cmd_profile(args):
    os.makedirs(args.work, exist_ok=True)
    files = discover_files(args.input)
    tables = []
    skipped = []
    for f in files:
        ext = f["ext"]
        if ext in (".csv", ".tsv", ".txt", ".tab", ".dat"):
            prof = profile_table(f["path"])
            prof["rel"] = f["rel"]
            tables.append(prof)
        elif ext in (".xlsx", ".xls", ".mat", ".pdf", ".docx", ".pptx"):
            skipped.append({
                "rel": f["rel"], "ext": ext,
                "note": "requires platform tool export (pandas/openpyxl/pdftotext)",
            })
    profile = {
        "case_id": resolve_case_id(args.input),
        "created_at": now_ts(),
        "files": files,
        "tables": tables,
        "skipped_for_conversion": skipped,
    }
    write_json_atomic(os.path.join(args.work, "data_profile.json"), profile)
    print("profile ok: %d tables, %d skipped" % (len(tables), len(skipped)))
    return 0


def cmd_model(args):
    os.makedirs(args.work, exist_ok=True)
    profile = load_json_safe(os.path.join(args.work, "data_profile.json")) or {}
    case_id = resolve_case_id(args.input)
    model = build_model_template(case_id, profile)
    figure = build_figure_template(model)
    write_json_atomic(os.path.join(args.work, "model.json"), model)
    write_json_atomic(os.path.join(args.work, "figure_model.json"), figure)
    print("model template ok: %d series, %d anomaly candidates"
          % (len(model["series_models"]), len(model["anomaly_model"])))
    return 0


# ---------------------------------------------------------------------------
# render：由 model.json 渲染 result.json / report.md / evidence_map.json
# ---------------------------------------------------------------------------

def _as_list(v):
    return v if isinstance(v, list) else []


def _render_interpretation(model):
    interp = {
        "trends": [],
        "anomalies": [],
        "possible_causes": [],
        "verification_steps": [],
        "followup_experiments": [],
    }
    for s in model.get("series_models", []):
        if s.get("trend"):
            interp["trends"].append({
                "series_id": s.get("id"),
                "source": s.get("source"),
                "direction": s["trend"].get("direction"),
                "interval": s["trend"].get("interval"),
                "control": s["trend"].get("control"),
                "fit_method": s["trend"].get("fit_method") or s["trend"].get("method"),
                "amplitude": s["trend"].get("amplitude"),
                "units": s.get("units"),
                "claim": s["trend"].get("claim"),
                "evidence": s["trend"].get("evidence", []),
            })
    for a in model.get("anomaly_model", []):
        interp["anomalies"].append({
            "anomaly_id": a.get("id"),
            "subject": a.get("subject"),
            "observed": a.get("observed"),
            "expected": a.get("expected"),
            "deviation": a.get("deviation"),
            "method": a.get("method"),
            "threshold": a.get("threshold"),
            "relation_to_controls": a.get("relation_to_controls"),
            "claim": a.get("claim"),
            "evidence": a.get("evidence", []),
        })
    for c in model.get("cause_model", []):
        interp["possible_causes"].append({
            "cause_id": c.get("id"),
            "hypothesis": c.get("hypothesis"),
            "rank": c.get("rank"),
            "supporting_evidence": c.get("supporting_evidence", []),
            "excluding_evidence": c.get("excluding_evidence", []),
            "confound_risk": c.get("confound_risk"),
            "testability": c.get("testability"),
        })
    for v in model.get("verification_model", []):
        interp["verification_steps"].append({
            "step_id": v.get("id"),
            "action": v.get("action"),
            "target": v.get("target"),
            "expected_if_supported": v.get("expected_if_supported"),
            "decision_rule": v.get("decision_rule"),
            "evidence": v.get("evidence", []),
        })
    for f in model.get("followup_model", []):
        interp["followup_experiments"].append({
            "followup_id": f.get("id"),
            "change": f.get("change"),
            "reason": f.get("reason"),
            "expected_outcome": f.get("expected_outcome"),
            "evidence": f.get("evidence", []),
        })
    return interp


def cmd_render(args):
    model = load_json_safe(os.path.join(args.work, "model.json")) or {}
    result = load_json_safe(os.path.join(args.output, "result.json")) or {}
    if not isinstance(result, dict) or not result.get("case_id"):
        result = build_skeleton(model.get("case_id") or resolve_case_id(args.input))
    case_obj = _load_case(args.input)
    merge_output_contract(result, case_obj)

    result["interpretation"] = _render_interpretation(model)

    pm = model.get("experiment_plan_model") or {}
    if pm:
        result["experiment_plan"] = pm.get("experiment_plan", result.get("experiment_plan"))
        result["evidence_and_risk"] = pm.get("evidence_and_risk", result.get("evidence_and_risk"))
        result["confirmation_items"] = pm.get("confirmation_items", result.get("confirmation_items"))

    result["intermediate_artifacts"] = {
        "plan": "work/plan.json",
        "profile": "work/data_profile.json",
        "model": "work/model.json",
        "figure_model": "work/figure_model.json",
        "evidence_map": "output/evidence_map.json",
    }

    for key in ("sources", "uncertainty", "limitations", "pending_human",
                "degradations", "errors"):
        mv = model.get(key)
        if isinstance(mv, list):
            result[key] = mv

    result["change_log"].append({
        "ts": now_ts(), "phase": "render",
        "action": "result.json rendered from model.json",
    })

    # evidence_map：汇总所有 locator+quote
    evidence_map = []
    for section in INTERPRETATION_KEYS:
        for item in result["interpretation"].get(section, []):
            for ev in _as_list(item.get("evidence")):
                if ev.get("locator") and ev.get("quote"):
                    evidence_map.append({
                        "section": section, "item_id": item.get(next(
                            (k for k in ("series_id", "anomaly_id", "cause_id",
                                         "step_id", "followup_id") if k in item), None)),
                        "locator": ev["locator"], "quote": ev["quote"],
                    })
    write_json_atomic(os.path.join(args.output, "evidence_map.json"), evidence_map)

    write_json_atomic(os.path.join(args.output, "result.json"), result)
    write_text_atomic(os.path.join(args.output, "report.md"), _render_report(result))
    print("render ok: result.json + report.md + evidence_map.json")
    return 0


def _render_report(result):
    lines = ["# 实验解释报告", "", "- case_id: `%s`" % result.get("case_id", ""),
             "- status: `%s`" % result.get("status", ""), ""]
    summary = result.get("summary") or "（未填写）"
    lines += ["## 结论摘要", "", summary, ""]
    sections = [
        ("trends", "趋势"), ("anomalies", "异常点"), ("possible_causes", "可能原因"),
        ("verification_steps", "复核步骤"), ("followup_experiments", "后续实验建议"),
    ]
    interp = result.get("interpretation", {})
    for key, label in sections:
        lines += ["## %s" % label, ""]
        items = interp.get(key, [])
        if not items:
            lines += ["- 无结论或证据不足（见 limitations/uncertainty）", ""]
            continue
        for i, it in enumerate(items, 1):
            claim = it.get("claim") or it.get("hypothesis") or it.get("action") \
                or it.get("change") or json.dumps(it, ensure_ascii=False)
            lines += ["### %s.%d" % (label, i), "", str(claim), ""]
            evs = it.get("evidence") or it.get("supporting_evidence") or []
            if evs:
                lines += ["证据："]
                for ev in evs:
                    if isinstance(ev, dict):
                        lines += ["- `%s` → “%s”" % (ev.get("locator", ""), ev.get("quote", ""))]
                lines += [""]
    lines += ["## 不确定性", ""]
    for u in result.get("uncertainty", []):
        lines += ["- %s" % u]
    lines += ["", "## 限制", ""]
    for lim in result.get("limitations", []):
        lines += ["- %s" % lim]
    lines += ["", "## 待人工确认", ""]
    for ph in result.get("pending_human", []):
        lines += ["- %s" % ph]
    lines += ["", "## 变更记录", ""]
    for cl in result.get("change_log", []):
        lines += ["- [%s] %s: %s" % (cl.get("ts", ""), cl.get("phase", ""), cl.get("action", ""))]
    lines += [""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# validate / finalize
# ---------------------------------------------------------------------------

def cmd_validate(args):
    result = load_json_safe(args.input)
    if not isinstance(result, dict):
        print("FAIL: result.json not a JSON object")
        return 1
    problems = []
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in STATUSES:
        problems.append("status invalid: %r" % result.get("status"))
    for key in TOP_KEYS:
        if key not in result:
            problems.append("missing top key: %s" % key)
    interp = result.get("interpretation")
    if not isinstance(interp, dict):
        problems.append("interpretation missing/not object")
    else:
        for key in INTERPRETATION_KEYS:
            if key not in interp:
                problems.append("interpretation missing key: %s" % key)
            elif not section_satisfied(key, interp.get(key)):
                problems.append("interpretation.%s empty or lacks required evidence" % key)
    for item in (interp or {}).get("anomalies", []):
        if not (item.get("method") and item.get("threshold") is not None):
            problems.append("anomaly lacks method/threshold: %s" % item.get("anomaly_id"))
    for item in (interp or {}).get("possible_causes", []):
        if not (_has_evidence_list(item.get("supporting_evidence"))
                and isinstance(item.get("excluding_evidence"), list)):
            problems.append("cause lacks supporting/excluding evidence: %s" % item.get("cause_id"))
    report_path = os.path.join(os.path.dirname(os.path.abspath(args.input)), "report.md")
    if not os.path.exists(report_path):
        problems.append("report.md missing")

    validation = {
        "checked_at": now_ts(),
        "result": "ok" if not problems else "fail",
        "problems": problems,
    }
    write_json_atomic(os.path.join(os.path.dirname(os.path.abspath(args.input)),
                                   "validation.json"), validation)
    if problems:
        print("FAIL (%d problems):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("validate ok")
    return 0


def cmd_finalize(args):
    result = load_json_safe(args.input)
    if not isinstance(result, dict):
        print("FAIL: cannot read result.json")
        return 1
    rt = result.setdefault("runtime", {})
    if not rt.get("started_at"):
        rt["started_at"] = now_ts()
    rt["finished_at"] = now_ts()
    rt["seconds"] = None  # 由平台记录真实耗时，脚本不臆造
    result["change_log"].append({
        "ts": now_ts(), "phase": "finalize", "action": "finalized",
    })
    write_json_atomic(args.input, result)
    print("finalize ok")
    return 0


def build_arg_parser():
    p = argparse.ArgumentParser(prog="main.py", description="实验解释 Skill 主入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_io(sp):
        sp.add_argument("--input", default=DEFAULT_INPUT, help="输入目录 /input")
        sp.add_argument("--output", default=DEFAULT_OUTPUT, help="产物目录 /app/output")
        sp.add_argument("--work", default=DEFAULT_WORK, help="工作目录 /app/work")
        return sp

    for name in ("skeleton", "profile", "model", "render"):
        sp = sub.add_parser(name)
        add_io(sp)

    v = sub.add_parser("validate", help="校验 result.json（--input 指向 result.json 路径）")
    v.add_argument("--input", required=True, help="result.json 路径")
    f = sub.add_parser("finalize", help="写定稿时间（--input 指向 result.json 路径）")
    f.add_argument("--input", required=True, help="result.json 路径")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    handlers = {
        "skeleton": cmd_skeleton,
        "profile": cmd_profile,
        "model": cmd_model,
        "render": cmd_render,
        "validate": cmd_validate,
        "finalize": cmd_finalize,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
