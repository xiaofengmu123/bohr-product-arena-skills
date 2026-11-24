#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_result.py — 结构/类型/算术/六要素/证据自检 + 截止线信号。

只读校验，写 /app/work/validation_report.json，打印摘要。
退出码 0 = 可用（含警告）；1 = 必须修复。
诚实性规则：status==ok 时内容缺口/算术冲突升级为 error（退出 1），
status!=ok 时同级问题仅记 warning（退出 0），使早落盘骨架始终合法。
"""
import argparse
import json
import os
import sys
import time

FALLBACK_KEYS = ("hypothesis_and_variables", "controls",
                 "equipment_and_materials", "time_and_cost",
                 "parameter_ranges", "risks_and_anomalies")
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
TOL = 1e-6


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return None


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, path + "/" + str(k) if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, "%s[%d]" % (path, i))
    else:
        yield path, obj


def iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_dicts(v)


def is_empty(v):
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return False
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def plan_key_filled(result, key):
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    v = plan.get(key)
    if key == "hypothesis_and_variables":
        if not isinstance(v, dict):
            return False
        return (isinstance(v.get("hypothesis"), str) and v.get("hypothesis").strip()) \
            or (isinstance(v.get("variables"), list) and len(v.get("variables")) > 0)
    if key == "time_and_cost":
        if not isinstance(v, dict):
            return False
        return (isinstance(v.get("steps"), list) and len(v.get("steps")) > 0) \
            or not is_empty(v.get("total_duration_min")) \
            or (isinstance(v.get("budget"), dict) and not is_empty(v["budget"].get("amount")))
    return not is_empty(v)


def collect_num_by_key(obj, key):
    out = []
    for path, v in walk(obj):
        if last_key(path) == key and is_num(v):
            out.append((path, float(v)))
    return out


def last_key(path):
    seg = path.rsplit("/", 1)[-1]
    return seg.split("[", 1)[0]


def check_structural(result, probe):
    errors = []
    if not isinstance(result, dict):
        return ["result_not_object"]
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        errors.append("case_id_missing_or_empty")
    if result.get("status") not in ALLOWED_STATUS:
        errors.append("status_invalid_or_missing")
    contract = probe.get("output_contract") if isinstance(probe, dict) else None
    if isinstance(contract, dict):
        for k in contract.keys():
            if k not in result:
                errors.append("contract_key_missing:%s" % k)
    return errors


def check_six_elements(result):
    """Returns list of (key, filled_bool). Contract case handled separately."""
    out = []
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    for key in FALLBACK_KEYS:
        out.append((key, plan_key_filled(result, key)))
    return out


def check_contract_content(result, contract):
    out = []
    for k in contract.keys():
        out.append((k, not is_empty(result.get(k))))
    return out


def check_evidence(result):
    items = []
    for d in iter_dicts(result):
        if isinstance(d, dict) and "locator" in d and "quote" in d:
            items.append(d)
    issues = []
    for i, d in enumerate(items):
        if not (isinstance(d.get("locator"), str) and d["locator"].strip()):
            issues.append("evidence_%d_missing_locator" % i)
        if not (isinstance(d.get("quote"), str) and d["quote"].strip()):
            issues.append("evidence_%d_missing_quote" % i)
    return items, issues


def check_arithmetic(result):
    issues = []
    # 参数：allowed_min <= allowed_max，proposed_value 在区间内
    for d in iter_dicts(result):
        if not isinstance(d, dict):
            continue
        if "allowed_min" in d and "allowed_max" in d and is_num(d["allowed_min"]) and is_num(d["allowed_max"]):
            mn, mx = float(d["allowed_min"]), float(d["allowed_max"])
            if mn > mx:
                issues.append("param_invalid_domain(allowed_min>allowed_max)")
            if "proposed_value" in d and is_num(d["proposed_value"]):
                pv = float(d["proposed_value"])
                if pv < mn - TOL or pv > mx + TOL:
                    issues.append("param_out_of_range(proposed=%.6g allowed=[%.6g,%.6g])" % (pv, mn, mx))
    # 排期：total_duration_min >= max(step durations)
    durations = [v for _p, v in collect_num_by_key(result, "duration_min")]
    totals = [v for _p, v in collect_num_by_key(result, "total_duration_min")]
    if durations and totals:
        mx = max(durations)
        s = sum(durations)
        for t in totals:
            if t < mx - TOL:
                issues.append("schedule_total_lt_max_step(total=%.6g max_step=%.6g)" % (t, mx))
            elif t > s + TOL:
                issues.append("schedule_total_gt_naive_sum(total=%.6g sum=%.6g)" % (t, s))
    # 预算：sum(cost_items.amount) <= budget.amount
    cost = [v for p, v in collect_num_by_key(result, "amount") if "cost" in p.lower()]
    budgets = []
    for d in iter_dicts(result):
        if isinstance(d, dict) and "budget" in d and isinstance(d["budget"], dict) and is_num(d["budget"].get("amount")):
            budgets.append(float(d["budget"]["amount"]))
    if cost and budgets:
        s = sum(cost)
        b = max(budgets)
        if s > b + TOL:
            issues.append("budget_overspend(cost=%.6g budget=%.6g)" % (s, b))
    # 样本可行性：sample_size * per_sample <= time_window
    samples = [v for _p, v in collect_num_by_key(result, "sample_size")]
    per_sample = [v for _p, v in collect_num_by_key(result, "per_sample_duration_min")]
    windows = [v for _p, v in collect_num_by_key(result, "time_window_min")]
    if samples and per_sample and windows:
        n, ps, w = max(samples), max(per_sample), max(windows)
        if n * ps > w + TOL:
            issues.append("feasibility_conflict(sample_size*per_sample=%.6g > window=%.6g)" % (n * ps, w))
    return issues


def check_plan_md(output_dir, case_id):
    path = os.path.join(output_dir, "plan.md")
    if not os.path.isfile(path):
        return ["plan_md_missing"]
    txt = read_text(path) or ""
    issues = []
    if case_id not in txt:
        issues.append("plan_md_case_id_absent")
    for key in FALLBACK_KEYS:
        if key.replace("_", " ") not in txt.lower() and key not in txt:
            issues.append("plan_md_section_missing:%s" % key)
    return issues


def budget_status(work_dir):
    ledger = read_json(os.path.join(work_dir, "budget.json")) or {}
    start = ledger.get("start_epoch")
    if start is None:
        return None, None
    elapsed = time.time() - start
    remaining = max(0.0, ledger.get("total_seconds", 900) - elapsed)
    return elapsed, remaining


def main():
    ap = argparse.ArgumentParser(description="Validate result.json and plan.md.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    result_path = os.path.join(args.output, "result.json")
    probe = read_json(os.path.join(args.work, "input_probe.json")) or {}

    report = {
        "validate_ts": now_iso(),
        "result_path": result_path,
        "status": None,
        "errors": [],
        "warnings": [],
        "exit_code": 0,
        "must_finalize": False,
    }

    if not os.path.isfile(result_path):
        report["errors"].append("result.json_missing")
        report["exit_code"] = 1
    else:
        result = read_json(result_path)
        if result is None:
            report["errors"].append("result.json_unparseable")
            report["exit_code"] = 1
        else:
            report["status"] = result.get("status")
            contract = probe.get("output_contract")
            struct_errors = check_structural(result, probe)
            content_warnings = []

            # 六要素 / 契约内容完整性
            if isinstance(contract, dict):
                completeness = check_contract_content(result, contract)
            else:
                completeness = check_six_elements(result)
            for key, filled in completeness:
                if not filled:
                    content_warnings.append("incomplete:%s" % key)

            # 证据
            _items, ev_issues = check_evidence(result)
            content_warnings.extend(ev_issues)
            if isinstance(contract, dict):
                pass
            else:
                emap = (result.get("evidence_and_risks") or {}).get("evidence_map") \
                    if isinstance(result.get("evidence_and_risks"), dict) else None
                if isinstance(emap, list) and len(emap) == 0:
                    content_warnings.append("evidence_map_empty")

            # 算术
            content_warnings.extend(check_arithmetic(result))

            # change_log
            cl = result.get("change_log")
            if not isinstance(cl, list) or len(cl) == 0:
                content_warnings.append("change_log_empty")

            # plan.md
            content_warnings.extend(check_plan_md(args.output, result.get("case_id") or ""))

            # 诚实性：status==ok 时内容警告升级为 error
            if result.get("status") == "ok" and content_warnings:
                report["errors"].extend(["status_ok_but_%s" % w for w in content_warnings])
                report["exit_code"] = 1
            else:
                report["warnings"].extend(content_warnings)

            report["errors"].extend(struct_errors)
            if struct_errors:
                report["exit_code"] = 1

    # 截止线信号
    elapsed, remaining = budget_status(args.work)
    if remaining is not None:
        report["elapsed_s"] = round(elapsed, 1)
        report["remaining_s"] = round(remaining, 1)
        if remaining < 120:
            report["must_finalize"] = True
            report["warnings"].append("budget_low:remaining<120s_finalize_now")
        print("BUDGET_STATUS elapsed_s=%.0f remaining_s=%.0f" % (elapsed, remaining))
        if report["must_finalize"]:
            print("BUDGET_WARNING: remaining<120s, finalize honestly (do not start new analysis)")

    write_json_atomic(os.path.join(args.work, "validation_report.json"), report)
    print("validate_result: status=%s errors=%d warnings=%d exit=%d"
          % (report["status"], len(report["errors"]), len(report["warnings"]), report["exit_code"]))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
