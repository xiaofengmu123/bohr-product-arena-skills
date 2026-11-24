#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold_result.py — 早落盘：写“通过结构校验的 partial”骨架。

写 /app/output/result.json（全部 output_contract 键 + 信封）与
/app/output/plan.md 骨架，均为原子写。初始化 /app/work/budget.json
（阶段预算账本，start_ts 跨运行粘滞）并追加 change_log 首条。
这是 Tier-1 早落盘：前 3 分钟内产出，且已通过结构校验（validate 退出码 0），
之后任何中断都留下可用 deliverable。
"""
import argparse
import json
import os
import sys
import time

FALLBACK_KEYS = ("hypothesis_and_variables", "controls",
                 "equipment_and_materials", "time_and_cost",
                 "parameter_ranges", "risks_and_anomalies")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def fallback_plan():
    return {
        "hypothesis_and_variables": {"hypothesis": "", "variables": []},
        "controls": [],
        "equipment_and_materials": [],
        "time_and_cost": {
            "steps": [], "total_duration_min": None, "sample_size": None,
            "budget": {"amount": None, "currency": "CNY"}, "cost_items": [],
        },
        "parameter_ranges": [],
        "risks_and_anomalies": [],
    }


def fallback_result(case_id):
    return {
        "case_id": case_id,
        "plan": fallback_plan(),
        "evidence_and_risks": {"evidence_map": [], "risks": []},
        "confirmations": {
            "safety": [], "ethics": [],
            "equipment_availability": [], "expert_review": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {},
    }


def merge_contract(result, contract):
    """Put every contract key into result (deep copy), then envelope keys."""
    merged = json.loads(json.dumps(contract)) if isinstance(contract, dict) else {}
    merged["case_id"] = result["case_id"]
    merged.setdefault("status", result["status"])
    merged.setdefault("sources", result["sources"])
    merged.setdefault("uncertainty", result["uncertainty"])
    merged.setdefault("pending_human", result["pending_human"])
    merged.setdefault("change_log", result["change_log"])
    merged.setdefault("errors", result["errors"])
    merged.setdefault("degradations", result["degradations"])
    merged.setdefault("runtime", result["runtime"])
    return merged


def plan_md_skeleton(case_id, status):
    lines = [
        "# 实验方案（partial 骨架）",
        "",
        "case_id: %s" % case_id,
        "status: %s" % status,
        "",
        "> 本文件为早落盘骨架，分析完成后将逐段填充；内容以 result.json 为准。",
        "",
        "## 假设与变量",
        "",
        "## 对照组",
        "",
        "## 设备材料",
        "",
        "## 时间成本",
        "",
        "## 参数范围",
        "",
        "## 风险与异常",
        "",
        "## 依据与风险（evidence_map 摘录）",
        "",
        "## 确认项（安全/伦理/设备/专家）",
        "",
    ]
    return "\n".join(lines)


def budget_ledger(work_dir):
    """Return the phase-budget ledger, keeping start_ts sticky across runs."""
    path = os.path.join(work_dir, "budget.json")
    if os.path.isfile(path):
        existing = read_json(path)
        if isinstance(existing, dict) and existing.get("start_ts"):
            return path, existing
    ledger = {
        "total_seconds": 900,
        "total_requests": 64,
        "response_tokens": 16384,
        "start_ts": now_iso(),
        "start_epoch": time.time(),
        "phases": [
            {"name": "discover", "deadline_s": 60},
            {"name": "skeleton", "deadline_s": 180},
            {"name": "evidence", "deadline_s": 420},
            {"name": "plan", "deadline_s": 720},
            {"name": "validate", "deadline_s": 840},
            {"name": "finalize", "deadline_s": 900},
        ],
        "tool_calls": 0,
    }
    write_json_atomic(path, ledger)
    return path, ledger


def budget_status(ledger):
    elapsed = time.time() - ledger.get("start_epoch", time.time())
    remaining = max(0.0, ledger["total_seconds"] - elapsed)
    return elapsed, remaining


def main():
    ap = argparse.ArgumentParser(description="Write partial result skeleton early.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    probe = read_json(os.path.join(args.work, "input_probe.json")) or {}
    contract = probe.get("output_contract")
    case_id = probe.get("case_id")

    errors = []
    degradations = []
    if not case_id:
        case_id = "unknown"
        errors.append("case_id_unknown")

    case_json_status = probe.get("case_json_status", "missing")
    task_md = probe.get("task_md")
    files = probe.get("files") or []

    # status 诚实区分：坏 JSON → input_error；空输入 → insufficient_evidence；
    # 其余（含缺部分材料）→ partial，缺口记 errors/pending_human。
    if str(case_json_status).startswith("bad_json"):
        status = "input_error"
        errors.append("case.json_bad_json")
    elif case_json_status == "missing" and not task_md and not files:
        status = "insufficient_evidence"
        errors.append("case.json_missing")
    elif case_json_status == "missing":
        status = "partial"
        errors.append("case.json_missing")
    else:
        status = "partial"

    result = fallback_result(case_id)
    result["status"] = status
    if errors:
        result["errors"] = errors
    if degradations:
        result["degradations"] = degradations

    if isinstance(contract, dict):
        result = merge_contract(result, contract)

    log_entry = {"ts": now_iso(), "event": "skeleton_written",
                 "detail": "early partial commit; status=%s" % status}
    result["change_log"] = [log_entry]

    # 粘滞 budget 账本并更新工具调用计数
    ledger_path, ledger = budget_ledger(args.work)
    ledger["tool_calls"] = int(ledger.get("tool_calls", 0)) + 1
    write_json_atomic(ledger_path, ledger)

    result_path = os.path.join(args.output, "result.json")
    plan_path = os.path.join(args.output, "plan.md")
    write_json_atomic(result_path, result)
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(plan_md_skeleton(case_id, status))

    elapsed, remaining = budget_status(ledger)
    print("scaffold_result: wrote %s and %s" % (result_path, plan_path))
    print("scaffold_result: case_id=%s status=%s contract=%s"
          % (case_id, status, bool(contract)))
    print("BUDGET_STATUS elapsed_s=%.0f remaining_s=%.0f phase=skeleton" % (elapsed, remaining))
    if remaining < 120:
        print("BUDGET_WARNING: remaining<120s, skip refinement and finalize honestly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
