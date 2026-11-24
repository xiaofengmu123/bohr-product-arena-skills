#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_plan.py — 校验 /app/work/plan.json 的 schema 与可行域约束。

用法:
    python scripts/validate_plan.py <plan.json> [--report <report.json>]

退出码: 0=通过, 1=存在阻断错误, 2=仅警告。
只做可判定的检查：schema 完整性、数值可行域、指标口径、搜索策略、成本、证据。
非数值参数（如“待定”）标为警告而非崩溃。
"""

import argparse
import json
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
DIRECTIONS = ("maximize", "minimize", "target", "max", "min", "")


def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:
        return None, str(exc)


def validate(plan):
    errors, warnings = [], []

    def err(msg):
        errors.append(msg)

    def warn(msg):
        warnings.append(msg)

    if not isinstance(plan, dict):
        return ["plan 不是 JSON 对象"], []

    for key in ("parameters", "response_metrics", "search_strategy",
                "cost_budget"):
        if key not in plan:
            err("缺少顶层字段: %s" % key)

    status = plan.get("status")
    if status and status not in STATUS_ENUM:
        warn("status 值不在允许枚举: %r" % status)

    # 参数
    params = plan.get("parameters")
    if not isinstance(params, list):
        err("parameters 必须是列表")
        params = []
    for i, p in enumerate(params):
        tag = "parameters[%d]" % i
        if not isinstance(p, dict):
            err("%s 不是对象" % tag)
            continue
        if not p.get("name"):
            err("%s 缺少 name" % tag)
        if p.get("unit") in (None, ""):
            warn("%s 缺少 unit" % tag)
        rng = p.get("feasible_range")
        if not isinstance(rng, dict):
            err("%s 缺少 feasible_range（应含 min/max）" % tag)
        else:
            lo, hi = _num(rng.get("min")), _num(rng.get("max"))
            if lo is None or hi is None:
                warn("%s feasible_range 的 min/max 不可数值化" % tag)
            elif lo > hi:
                err("%s feasible_range min>max" % tag)
            else:
                rec = _num(p.get("recommended_value"))
                if rec is not None and (rec < lo or rec > hi):
                    err("%s recommended_value=%r 越出可行区间 [%r, %r]"
                        % (tag, rec, lo, hi))
        if p.get("recommended_value") in (None, ""):
            warn("%s 缺少 recommended_value" % tag)
        step = _num(p.get("step"))
        if step is not None and step <= 0:
            err("%s step 必须 > 0" % tag)
        elif step is None:
            warn("%s 缺少 step（网格搜索需要）" % tag)
        if p.get("constraint_source") and not p.get("quote"):
            warn("%s 有 constraint_source 但缺逐字 quote" % tag)

    # 响应指标
    metrics = plan.get("response_metrics")
    if not isinstance(metrics, list):
        err("response_metrics 必须是列表")
        metrics = []
    for i, m in enumerate(metrics):
        tag = "response_metrics[%d]" % i
        if not isinstance(m, dict):
            err("%s 不是对象" % tag)
            continue
        for k in ("name", "unit", "measurement_method", "target"):
            if m.get(k) in (None, ""):
                warn("%s 缺少 %s" % (tag, k))
        if m.get("direction") not in DIRECTIONS:
            warn("%s direction 应在 maximize/minimize/target 中" % tag)

    # 搜索策略
    strat = plan.get("search_strategy")
    if isinstance(strat, dict):
        if not strat.get("method"):
            warn("search_strategy.method 为空")
        try:
            if int(strat.get("candidate_count", 0)) <= 0:
                err("search_strategy.candidate_count 必须 > 0")
        except (TypeError, ValueError):
            err("search_strategy.candidate_count 不可解析")
    else:
        err("search_strategy 必须是对象")

    # 成本
    cb = plan.get("cost_budget")
    if isinstance(cb, dict):
        if not cb.get("total"):
            warn("cost_budget.total 为空")
        if cb.get("time_hours") is None:
            warn("cost_budget.time_hours 为空")
    else:
        warn("cost_budget 应为对象")

    # 证据溯源
    sources = plan.get("sources")
    if sources is None:
        warn("缺少 sources")
    elif isinstance(sources, list):
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                warn("sources[%d] 不是对象" % i)
                continue
            if not s.get("locator") or not s.get("quote"):
                warn("sources[%d] 缺 locator 或逐字 quote" % i)
    else:
        warn("sources 应为列表")

    if not isinstance(plan.get("uncertainty"), dict):
        warn("uncertainty 应为对象")

    return errors, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="plan.json 路径")
    parser.add_argument("--report", help="可选：校验报告输出路径")
    args = parser.parse_args(argv)

    plan, load_err = load(args.plan)
    if plan is None:
        errors = ["无法读取 plan.json: %s" % load_err]
        warnings = []
    else:
        errors, warnings = validate(plan)

    report = {"plan": args.plan, "errors": errors, "warnings": warnings,
              "exit_code": 1 if errors else (2 if warnings else 0)}
    if args.report:
        try:
            import os
            os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
