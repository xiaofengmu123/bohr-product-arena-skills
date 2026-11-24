#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审计 — 入口（main.py）。

流程：
1. 扫描 /input 下的 case.json、task.md 与材料，立即构建确定性结果骨架；
2. 生成七类审查项、算术校验、方案比较、伦理扫描、矛盾检测；
3. 写出 /app/output/result.json 与可复核产物（报告、CSV、摘要、运行清单）；
4. 可选 --findings 合并 Agent 人工语义修订（同样输入 → 同样输出）。

约定：
- 仅依赖 Python 标准库，Python >= 3.8。
- 缺省路径 --input/--output/--work 指向 /input、/app/output、/app/work。
- 任何异常都降级为最小合法 JSON，进程退出码 0，不崩溃。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from core_impl import (
    SCHEMA_VERSION,
    SourceIndex,
    build_model,
    build_result,
    canonical_json,
    extract_case_id,
    load_text_file,
    merge_findings,
    parse_json_lenient,
    scan_inputs,
    write_outputs,
    _stdout_utf8,
)

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
CASE_FILE_NAMES = ("case.json", "case.jsonl")


def minimal_result(case_id, errors, degradations, status="input_error"):
    return {
        "case_id": case_id,
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "overall_conclusion": {
            "verdict": "undetermined",
            "summary": "输入不可用，未能完成审查。",
            "blocking_issues": [],
            "conditions": [],
            "unblock_actions": [],
        },
        "experiment_plan": {
            "research_question": None,
            "assumptions": [],
            "variables": [],
            "controls": [],
            "steps": [],
            "parameters": [],
            "resources": [],
            "interpretation_criteria": [],
        },
        "review": {
            "assumptions_variables": [],
            "controls": [],
            "equipment_materials": [],
            "time_cost": [],
            "parameter_ranges": [],
            "risks_exceptions": [],
            "ethics_safety": [],
        },
        "plan_comparison": [],
        "evidence_and_risks": {"evidence": [], "risks": [], "exception_handling": []},
        "contradictions": [],
        "confirmation": {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{"step": "fatal", "action": "降级为最小合法结果", "reason": "输入解析或写出失败"}],
        "runtime": {
            "budget_seconds": 900,
            "budget_requests": 64,
            "wall_clock_seconds": None,
            "note": "实际耗时/请求由平台记录；本 Skill 不写入未经测量的运行时数据",
        },
        "artifacts": ["result.json"],
        "provenance": {"input_sha256": {}, "result_sha256": None, "deterministic": True, "canonicalization": "sort_keys=true"},
    }


def load_case(files, input_dir):
    case = None
    case_path = None
    errors, degradations = [], []
    for f in files:
        if f["relpath"].rsplit("/", 1)[-1] in CASE_FILE_NAMES:
            case_path = f["abspath"]
            if f["status"] == "oversize":
                degradations.append(f"case_file_oversize:{f['relpath']}")
            text, warns = load_text_file(f["abspath"], 16 * 1024 * 1024)
            for w in warns:
                degradations.append(w)
            case, warns = parse_json_lenient(text, f["relpath"])
            for w in warns:
                degradations.append(w)
            break
    if case is None and case_path is None:
        errors.append("case_json_missing")
    return case, case_path, errors, degradations


def run(input_dir, output_dir, work_dir, findings_path=None, merge=None):
    _stdout_utf8()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    files, notes = scan_inputs(input_dir)
    errors = []
    degradations = list(notes)
    case, case_path, errs, degs = load_case(files, input_dir)
    errors.extend(errs)
    degradations.extend(degs)

    if case is None:
        case_id = "unknown"
        # 空目录/缺 case.json → insufficient_evidence；case.json 存在但解析失败 → input_error
        status = "input_error" if case_path is not None else "insufficient_evidence"
        result = minimal_result(case_id, errors, degradations, status=status)
        if status == "insufficient_evidence":
            result["overall_conclusion"]["summary"] = "无可读的 case.json 主输入，缺口已在 errors/pending_human 列出。"
            result["pending_human"] = [{"id": "INPUT", "claim": "缺少 /input/case.json", "action": "提供案例主输入文件"}]
        write_outputs(output_dir, result)
        return result, {"files": files, "case_id": case_id}

    source_index = SourceIndex(files, input_dir)
    model = build_model(case, files, input_dir, source_index)
    checks = build_review_local(case, files, input_dir, source_index, model)
    result = build_result(case, files, input_dir, source_index, model, checks)
    result["errors"] = errors
    result["degradations"] = degradations + model.get("warnings", []) + build_review_warnings(model)

    if findings_path and os.path.exists(findings_path):
        try:
            with open(findings_path, "r", encoding="utf-8") as fh:
                findings = json.load(fh)
            result, merr = merge_findings(result, findings, source_index)
            result["errors"] = result.get("errors", []) + merr
        except Exception as exc:
            result["errors"].append(f"findings_merge_failed:{exc}")

    if merge is not None:
        # 支持直接把 findings JSON 作为字符串传入（用于 selftest 确定性验证）
        try:
            findings = json.loads(merge) if isinstance(merge, str) else merge
            result, merr = merge_findings(result, findings, source_index)
            result["errors"] = result.get("errors", []) + merr
        except Exception as exc:
            result["errors"].append(f"findings_merge_failed:{exc}")

    write_outputs(output_dir, result)
    return result, {"files": files, "case_id": model["case_id"], "model": model}


def build_review_local(case, files, input_dir, source_index, model):
    # 延迟导入避免循环；build_review 在 core_impl 中定义
    from core_impl import build_review

    return build_review(model, source_index)


def build_review_warnings(model):
    w = []
    if not model["plans"]:
        w.append("no_candidate_plans: 未发现候选方案，仅按骨架审查")
    if not model["contracts"]:
        w.append("no_contracts: 未发现资源/价格/预约合同")
    return w


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic experiment feasibility audit")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--findings", default=None, help="Agent 人工修订 JSON 文件路径")
    ap.add_argument("--merge", default=None, help="直接传入 findings JSON 字符串")
    ap.add_argument("--print-digest", action="store_true")
    args = ap.parse_args(argv)

    try:
        result, meta = run(args.input, args.output, args.work, args.findings, args.merge)
    except Exception as exc:  # 兜底：不崩溃，写最小合法结果
        try:
            result = minimal_result("unknown", [f"fatal:{type(exc).__name__}:{exc}"], [])
            write_outputs(args.output, result)
        except Exception:
            pass
        print(json.dumps({"status": "input_error", "error": f"{type(exc).__name__}:{exc}"}, ensure_ascii=False))
        return 0

    if args.print_digest:
        digest_path = os.path.join(args.output, "audit_digest.md")
        if os.path.exists(digest_path):
            print(open(digest_path, encoding="utf-8").read())
    print(
        json.dumps(
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "verdict": result["overall_conclusion"].get("verdict"),
                "review_counts": {d: len(result["review"].get(d, [])) for d in result["review"]},
                "result_sha256": result["provenance"]["result_sha256"],
                "artifacts": result.get("artifacts"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
