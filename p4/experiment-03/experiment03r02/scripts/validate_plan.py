#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill — 方案校验器（仅标准库）。

校验 plan.json 的：
  - schema 完整性（关键字段存在且类型正确）
  - 关键参数可行域：value 落在 [min,max]、step>0、不越设备/材料/安全硬阈值
  - 约束冲突：建议值/目标值 与 硬阈值 冲突时给出阻断错误
  - 响应指标口径：unit / 方向 / 目标 / 可接受下限
  - 搜索策略：method、candidate_count、seed 可复现
  - 成本算术：明细 subtotal = quantity * unit_price、total = Σsubtotal、预算上限
  - 候选方案评估一致性：feasible 与 violations 不矛盾

退出码：0=通过；1=阻断错误；2=仅警告。
用法：
  python scripts/validate_plan.py /app/work/plan.json [--report /app/work/plan.validation_report.json]
"""

import argparse
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REQUIRED_TOP = [
    "case_id", "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources", "response_metrics",
    "interpretation_criteria", "search_strategy", "cost_budget", "basis",
    "risks", "exception_handling", "confirmations",
]


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return json.load(fh), None
        except Exception as exc:
            return None, "无法读取（编码/解析）: %s" % exc
    except json.JSONDecodeError as exc:
        return None, "无效 JSON: %s" % exc
    except OSError as exc:
        return None, "无法读取文件: %s" % exc


def _near(a, b, tol=1e-6):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _check_top(plan, errors, warnings):
    if not isinstance(plan, dict):
        errors.append("plan.json 顶层不是对象")
        return
    for key in REQUIRED_TOP:
        if key not in plan:
            errors.append("顶层缺字段: %s" % key)
    if not isinstance(plan.get("assumptions"), list):
        warnings.append("assumptions 应为列表")
    if not isinstance(plan.get("variables"), list):
        warnings.append("variables 应为列表")
    if not isinstance(plan.get("sources"), list):
        warnings.append("sources 应为列表")
    if not isinstance(plan.get("change_log"), list):
        warnings.append("change_log 应为列表")


def _check_parameters(plan, errors, warnings):
    params = plan.get("parameters")
    if not isinstance(params, list) or not params:
        warnings.append("parameters 为空：建议值尚未填充")
        return
    seen_ids = set()
    for i, p in enumerate(params):
        tag = "parameters[%d]" % i
        if not isinstance(p, dict):
            errors.append("%s 不是对象" % tag)
            continue
        pid = p.get("id") or p.get("name")
        if not pid:
            errors.append("%s 缺 id/name" % tag)
            continue
        if pid in seen_ids:
            warnings.append("%s id 重复: %s" % (tag, pid))
        seen_ids.add(pid)
        if not p.get("unit"):
            warnings.append("%s(%s) 缺 unit" % (tag, pid))
        value = _num(p.get("value"))
        lo = _num(p.get("min"))
        hi = _num(p.get("max"))
        step = _num(p.get("step"))
        if value is None:
            errors.append("%s(%s) 建议值 value 缺失或非数值" % (tag, pid))
        if lo is None or hi is None:
            errors.append("%s(%s) 缺有限定义域 min/max" % (tag, pid))
        elif lo >= hi:
            errors.append("%s(%s) min>=max，定义域非法" % (tag, pid))
        if step is None or step <= 0:
            errors.append("%s(%s) step 缺失或非正" % (tag, pid))
        if value is not None and lo is not None and hi is not None:
            if value < lo or value > hi:
                errors.append("%s(%s) value=%s 越出 [%s, %s]" % (tag, pid, value, lo, hi))
            elif step and step > 0:
                rem = (value - lo) / step
                if not _near(rem, round(rem)):
                    warnings.append("%s(%s) value=%s 不在 step=%s 的网格上" % (tag, pid, value, step))
        # hard thresholds
        for limkey, label in (
            ("device_limit", "设备量程"),
            ("material_limit", "材料耐温/限制"),
            ("safety_limit", "安全阈值"),
        ):
            lim = p.get(limkey)
            limv = _num(lim.get("value") if isinstance(lim, dict) else lim)
            if limv is not None and value is not None and value > limv:
                errors.append("%s(%s) value=%s 超过%s %s" % (tag, pid, value, label, limv))
        # target vs limit conflict (desired goal vs hard threshold)
        target = _num(p.get("target"))
        for limkey, label in (
            ("device_limit", "设备量程"),
            ("material_limit", "材料耐温/限制"),
            ("safety_limit", "安全阈值"),
        ):
            lim = p.get(limkey)
            limv = _num(lim.get("value") if isinstance(lim, dict) else lim)
            if limv is not None and target is not None and target > limv:
                errors.append("%s(%s) 目标值 target=%s 与%s %s 冲突" % (tag, pid, target, label, limv))


def _check_response_metrics(plan, errors, warnings):
    metrics = plan.get("response_metrics")
    if not isinstance(metrics, list) or not metrics:
        warnings.append("response_metrics 为空")
        return
    for i, m in enumerate(metrics):
        tag = "response_metrics[%d]" % i
        if not isinstance(m, dict):
            errors.append("%s 不是对象" % tag)
            continue
        if not (m.get("id") or m.get("name")):
            errors.append("%s 缺 id/name" % tag)
        if not m.get("unit"):
            warnings.append("%s 缺 unit" % tag)
        if m.get("direction") not in ("max", "min", "target"):
            errors.append("%s direction 应为 max/min/target" % tag)
        if not m.get("measurement_method"):
            warnings.append("%s 缺 measurement_method" % tag)
        if _num(m.get("target")) is None:
            warnings.append("%s 缺数值 target" % tag)
        if not m.get("prediction_method"):
            warnings.append("%s 缺 prediction_method（历史拟合/代理模型/经验规则）" % tag)


def _check_search_strategy(plan, errors, warnings):
    ss = plan.get("search_strategy")
    if not isinstance(ss, dict):
        errors.append("search_strategy 不是对象")
        return
    if not ss.get("method"):
        errors.append("search_strategy.method 为空")
    try:
        n = int(ss.get("candidate_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        errors.append("search_strategy.candidate_count 应为正整数")
    if "seed" not in ss:
        warnings.append("search_strategy 缺 seed（缺省 0）")
    if not isinstance(ss.get("stop_criteria"), list) or not ss.get("stop_criteria"):
        warnings.append("search_strategy.stop_criteria 为空：缺少停止判据")


def _check_cost_budget(plan, errors, warnings):
    cb = plan.get("cost_budget")
    if not isinstance(cb, dict):
        errors.append("cost_budget 不是对象")
        return
    if not cb.get("currency"):
        warnings.append("cost_budget 缺 currency")
    items = cb.get("items")
    if not isinstance(items, list) or not items:
        warnings.append("cost_budget.items 为空")
    else:
        computed = 0.0
        for i, item in enumerate(items):
            tag = "cost_budget.items[%d]" % i
            if not isinstance(item, dict):
                errors.append("%s 不是对象" % tag)
                continue
            q = _num(item.get("quantity"))
            up = _num(item.get("unit_price"))
            if q is None or up is None:
                errors.append("%s 缺数值 quantity/unit_price" % tag)
                continue
            subtotal = _num(item.get("subtotal"))
            if subtotal is None:
                errors.append("%s 缺 subtotal" % tag)
            elif not _near(subtotal, q * up, 1e-6):
                warnings.append("%s subtotal=%s != quantity*unit_price=%s" % (tag, subtotal, q * up))
            computed += q * up
        total = _num(cb.get("total"))
        if total is None:
            warnings.append("cost_budget 缺 total")
        elif not _near(total, computed, 1e-6):
            warnings.append("cost_budget.total=%s != Σsubtotal=%s" % (total, computed))
        cap = _num(cb.get("budget_cap"))
        if cap is not None and total is not None and total > cap:
            errors.append("成本 total=%s 超过预算上限 budget_cap=%s" % (total, cap))
    t = _num(cb.get("time_hours"))
    tb = _num(cb.get("time_budget_hours"))
    if tb is not None and t is not None and t > tb:
        errors.append("耗时 time_hours=%s 超过时间预算 time_budget_hours=%s" % (t, tb))
    if not cb.get("tradeoff_note"):
        warnings.append("cost_budget.tradeoff_note 为空：未说明多目标取舍")


def _check_candidates(plan, errors, warnings):
    evals = plan.get("candidate_evaluation")
    if not isinstance(evals, list) or not evals:
        return
    for i, e in enumerate(evals):
        tag = "candidate_evaluation[%d]" % i
        if not isinstance(e, dict):
            errors.append("%s 不是对象" % tag)
            continue
        feasible = e.get("feasible")
        violations = e.get("violations")
        if feasible is True and isinstance(violations, list) and violations:
            errors.append("%s feasible=true 但 violations 非空，不一致" % tag)
        if feasible is False and isinstance(violations, list) and not violations:
            warnings.append("%s feasible=false 但 violations 为空" % tag)


def main(argv=None):
    ap = argparse.ArgumentParser(description="实验参数优化方案校验器")
    ap.add_argument("plan_path")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    plan, err = _load(args.plan_path)
    errors = []
    warnings = []
    if plan is None:
        errors.append(err or "无法读取 plan.json")
        report = {"errors": errors, "warnings": warnings}
        if args.report:
            try:
                with open(args.report, "w", encoding="utf-8") as fh:
                    json.dump(report, fh, ensure_ascii=False, indent=2)
            except OSError:
                pass
        for e in errors:
            print("ERROR: %s" % e)
        return 1

    _check_top(plan, errors, warnings)
    _check_parameters(plan, errors, warnings)
    _check_response_metrics(plan, errors, warnings)
    _check_search_strategy(plan, errors, warnings)
    _check_cost_budget(plan, errors, warnings)
    _check_candidates(plan, errors, warnings)

    report = {"errors": errors, "warnings": warnings}
    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            warnings.append("无法写 report: %s" % exc)
    for e in errors:
        print("ERROR: %s" % e)
    for w in warnings:
        print("WARN: %s" % w)
    if errors:
        print("RESULT: %d errors, %d warnings" % (len(errors), len(warnings)))
        return 1
    if warnings:
        print("RESULT: pass with %d warnings" % len(warnings))
        return 2
    print("RESULT: pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
