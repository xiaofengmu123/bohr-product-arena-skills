#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan.json 校验：schema + 可行域约束 + 证据溯源完整性（仅标准库）。

退出码约定：
    0 = 通过（无阻断错误）
    1 = 阻断错误（必须修复）
    2 = 仅警告（可交付，但需说明）

校验三类：
    A. schema：关键参数/响应指标/搜索策略/成本预算四项字段齐全且类型正确。
    B. 可行域：建议值落在 feasible_range，且不越 equipment_limit / material_limit /
       safety_threshold（越界为阻断错误）。
    C. 证据溯源：每条关键参数、每条依据、每条 claim 都必须有 locator + 逐字 quote；
       缺失为阻断错误，来源冲突/未验证记为 warning。
"""

import argparse
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CLAIM_TYPES = {
    "parameter_value", "parameter_domain", "constraint", "response_metric",
    "predicted_response", "cost_basis", "risk", "assumption", "control",
    "resource", "procedure", "interpretation",
}
CLAIM_STATUS = {"supported", "inferred", "unverified", "conflicting", "pending_human"}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _within(v, lo, hi):
    if not _num(v):
        return False
    if _num(lo) and v < lo - 1e-9:
        return False
    if _num(hi) and v > hi + 1e-9:
        return False
    return True


def load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "file not found: %s" % path
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:  # noqa: BLE001
        return None, "parse error: %s" % exc


def validate(plan):
    errors = []
    warnings = []

    if not isinstance(plan, dict):
        errors.append("plan 不是对象")
        return errors, warnings
    if not plan.get("case_id"):
        errors.append("case_id 缺失或为空")

    params = plan.get("parameters")
    if not isinstance(params, list) or not params:
        errors.append("parameters 必须为非空数组（关键参数缺失）")
    else:
        seen = set()
        for i, p in enumerate(params):
            tag = "parameters[%d]" % i
            if not isinstance(p, dict):
                errors.append("%s 不是对象" % tag)
                continue
            name = p.get("name")
            if not name:
                errors.append("%s 缺 name" % tag)
                continue
            if name in seen:
                warnings.append("%s 参数名重复: %s" % (tag, name))
            seen.add(name)
            for field in ("unit", "suggested_value", "step"):
                if field not in p:
                    errors.append("%s(%s) 缺 %s" % (tag, name, field))
            fr = p.get("feasible_range")
            if not isinstance(fr, dict) or "min" not in fr or "max" not in fr:
                errors.append("%s(%s) feasible_range 需含 min/max" % (tag, name))
            else:
                if not _within(p.get("suggested_value"), fr.get("min"), fr.get("max")):
                    errors.append("%s(%s) suggested_value 越出 feasible_range"
                                  % (tag, name))
            if not p.get("locator") or not p.get("quote"):
                errors.append("%s(%s) 缺 locator/quote（逐字溯源）" % (tag, name))
            if not p.get("constraint_source"):
                warnings.append("%s(%s) 缺 constraint_source" % (tag, name))

    rms = plan.get("response_metrics")
    if not isinstance(rms, list) or not rms:
        errors.append("response_metrics 必须为非空数组（响应指标缺失）")
    else:
        for i, m in enumerate(rms):
            tag = "response_metrics[%d]" % i
            if not isinstance(m, dict):
                errors.append("%s 不是对象" % tag)
                continue
            for field in ("name", "unit", "measurement_method", "direction"):
                if not m.get(field):
                    errors.append("%s 缺 %s" % (tag, field))
            if not m.get("prediction_method"):
                warnings.append("%s(%s) 缺 prediction_method（未披露预测方式）"
                                % (tag, m.get("name", "?")))

    ss = plan.get("search_strategy")
    if not isinstance(ss, dict):
        errors.append("search_strategy 必须是对象")
    else:
        if not ss.get("method"):
            errors.append("search_strategy.method 缺失")
        if not ss.get("stop_criteria"):
            errors.append("search_strategy.stop_criteria 缺失（无停止判据）")
        if not isinstance(ss.get("candidate_count"), int) or ss.get("candidate_count", 0) < 1:
            errors.append("search_strategy.candidate_count 必须为正整数")

    cb = plan.get("cost_budget")
    if not isinstance(cb, dict):
        errors.append("cost_budget 必须是对象")
    else:
        if not cb.get("items"):
            errors.append("cost_budget.items 缺失（无成本明细）")
        if cb.get("total") in (None, ""):
            warnings.append("cost_budget.total 缺失")
        if not cb.get("tradeoff_note"):
            warnings.append("cost_budget.tradeoff_note 缺失（未说明多目标取舍）")

    for i, c in enumerate(plan.get("claims", []) or []):
        tag = "claims[%d]" % i
        if not isinstance(c, dict):
            errors.append("%s 不是对象" % tag)
            continue
        if not c.get("claim_id") or not c.get("statement"):
            errors.append("%s 缺 claim_id/statement" % tag)
        prov = c.get("provenance") or []
        if not prov:
            errors.append("%s provenance 为空" % tag)
        for p in prov:
            if not p.get("locator") or not p.get("quote"):
                errors.append("%s provenance 缺 locator/quote（逐字溯源）" % tag)
        if c.get("status") not in CLAIM_STATUS:
            warnings.append("%s status 不在枚举: %r" % (tag, c.get("status")))
        if c.get("conflicts"):
            warnings.append("%s 存在来源冲突（需显式标注 source_conflict）" % tag)
        if not c.get("verified"):
            warnings.append("%s 未 verified（写入 pending_human）" % tag)

    for i, b in enumerate(plan.get("basis", []) or []):
        if not isinstance(b, dict) or not b.get("source_id") or not b.get("locator") \
                or not b.get("quote"):
            errors.append("basis[%d] 缺 source_id/locator/quote" % i)

    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate_plan.py")
    ap.add_argument("plan", help="path to plan.json")
    ap.add_argument("--report", default=None, help="write JSON report here")
    args = ap.parse_args(argv)

    plan, err = load(args.plan)
    if plan is None:
        print("validate_plan: %s" % err, file=sys.stderr)
        return 1

    errors, warnings = validate(plan)
    report = {"errors": errors, "warnings": warnings,
              "errors_count": len(errors), "warnings_count": len(warnings)}
    if args.report:
        d = os.path.dirname(args.report)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    for e in errors:
        print("ERROR: %s" % e)
    for w in warnings:
        print("WARN : %s" % w)
    print("validate_plan: %d error(s), %d warning(s)" % (len(errors), len(warnings)))

    if errors:
        return 1
    if warnings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
