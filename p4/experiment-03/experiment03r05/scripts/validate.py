#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""有界实验参数优化 Skill —— 契约与可行域校验器（仅标准库）。

子命令
    plan   校验 /app/work/plan.json（schema + 可行域）
    result 校验 /app/output/result.json（顶层契约 + plan + 产物存在性）

退出码：0 = 通过；1 = 存在阻断错误；2 = 仅警告（可交付，警告须在 limitations 说明）。
可选 --report 写出 JSON 报告（errors / warnings / counts）。
"""

import argparse
import json
import os
import sys

try:
    import main as M
except ImportError:  # pragma: no cover - 便于直接运行
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import main as M


class Reporter:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, path, msg):
        self.errors.append({"path": path, "message": msg})

    def warning(self, path, msg):
        self.warnings.append({"path": path, "message": msg})


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_parameter(p, idx, rep):
    base = "plan.parameters[%d]" % idx
    if not isinstance(p, dict):
        rep.error(base, "参数项必须是对象")
        return
    for key in ("name", "unit"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            rep.error(base + "." + key, "缺失或非字符串")
    rng = p.get("feasible_range")
    if not isinstance(rng, dict):
        rep.error(base + ".feasible_range", "缺失或非对象")
    else:
        mn, mx = rng.get("min"), rng.get("max")
        if not _num(mn) or not _num(mx):
            rep.error(base + ".feasible_range", "min/max 必须是数值")
        elif mn > mx:
            rep.error(base + ".feasible_range", "min > max")
    step = p.get("step")
    if step is None:
        rep.warning(base + ".step", "步进缺失（材料不足时置 null）")
    elif not _num(step) or step <= 0:
        rep.error(base + ".step", "步进必须为正数")
    sv = p.get("suggested_value")
    if sv is None:
        rep.warning(base + ".suggested_value", "建议值缺失（材料不足时置 null）")
    elif not _num(sv):
        rep.error(base + ".suggested_value", "建议值必须是数值或 null")
    elif isinstance(rng, dict) and _num(rng.get("min")) and _num(rng.get("max")):
        if not (rng["min"] - 1e-9 <= sv <= rng["max"] + 1e-9):
            rep.error(base + ".suggested_value",
                      "建议值 %s 越界 [%s, %s]" % (sv, rng["min"], rng["max"]))
    if not isinstance(p.get("constraint_source"), str) or not p["constraint_source"].strip():
        rep.warning(base + ".constraint_source", "约束来源缺失")
    if not isinstance(p.get("constraint_quote"), str) or not p["constraint_quote"].strip():
        rep.warning(base + ".constraint_quote", "逐字 quote 缺失")
    if p.get("bounds_origin") is None:
        rep.warning(base + ".bounds_origin", "约束类型缺失")
    elif p.get("bounds_origin") not in M.BOUNDS_ORIGIN_ENUM:
        rep.warning(base + ".bounds_origin", "约束类型未知：%s" % p.get("bounds_origin"))


def _check_metric(m, idx, rep):
    base = "plan.response_metrics[%d]" % idx
    if not isinstance(m, dict):
        rep.error(base, "响应指标项必须是对象")
        return
    for key in ("name", "definition", "unit", "measurement_method"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            rep.error(base + "." + key, "缺失或非字符串")
    d = m.get("direction")
    if d not in M.DIRECTION_ENUM:
        rep.error(base + ".direction", "direction 必须是 %s" % "/".join(M.DIRECTION_ENUM))
    else:
        if d == "maximize" and m.get("acceptable_lower_bound") is None:
            rep.warning(base + ".acceptable_lower_bound", "maximize 缺可接受下限")
        if d == "minimize" and m.get("acceptable_upper_bound") is None:
            rep.warning(base + ".acceptable_upper_bound", "minimize 缺可接受上限")
        if d == "target" and m.get("target") is None:
            rep.warning(base + ".target", "target 方向缺目标值")
    if not isinstance(m.get("prediction_method"), str) or not m["prediction_method"].strip():
        rep.warning(base + ".prediction_method", "预测方式未披露")
    if not isinstance(m.get("prediction_uncertainty"), str) or not m["prediction_uncertainty"].strip():
        rep.warning(base + ".prediction_uncertainty", "预测不确定度未披露")


def validate_plan(plan, rep, prefix="plan"):
    if not isinstance(plan, dict):
        rep.error(prefix, "plan 必须是对象")
        return
    if not isinstance(plan.get("case_id"), str) or not plan["case_id"].strip():
        rep.error(prefix + ".case_id", "case_id 缺失或为空")
    for key in M.PLAN_CONTENT_KEYS:
        if key not in plan:
            rep.error(prefix + "." + key, "缺少字段")
    params = plan.get("parameters")
    if not isinstance(params, list):
        rep.error(prefix + ".parameters", "必须是列表")
    else:
        for i, p in enumerate(params):
            _check_parameter(p, i, rep)
    metrics = plan.get("response_metrics")
    if not isinstance(metrics, list):
        rep.error(prefix + ".response_metrics", "必须是列表")
    else:
        for i, m in enumerate(metrics):
            _check_metric(m, i, rep)
    ss = plan.get("search_strategy")
    if not isinstance(ss, dict):
        rep.error(prefix + ".search_strategy", "缺失或非对象")
    else:
        if not isinstance(ss.get("method"), str) or not ss["method"].strip():
            rep.error(prefix + ".search_strategy.method", "搜索方法为空")
        elif ss["method"] not in M.SEARCH_METHOD_ENUM:
            rep.warning(prefix + ".search_strategy.method", "搜索方法未知：%s" % ss["method"])
        if not isinstance(ss.get("stop_criteria"), list) or not ss["stop_criteria"]:
            rep.error(prefix + ".search_strategy.stop_criteria", "停止判据缺失")
        cc = ss.get("candidate_count")
        if not _num(cc) or cc < 1:
            rep.error(prefix + ".search_strategy.candidate_count", "候选点数量必须 ≥1")
    cb = plan.get("cost_budget")
    if not isinstance(cb, dict):
        rep.error(prefix + ".cost_budget", "缺失或非对象")
    else:
        if not isinstance(cb.get("items"), list):
            rep.error(prefix + ".cost_budget.items", "成本明细必须是列表")
        if not isinstance(cb.get("total"), str) or not cb["total"].strip():
            rep.warning(prefix + ".cost_budget.total", "总成本与口径未填写")
        if not isinstance(cb.get("tradeoff_note"), str) or not cb["tradeoff_note"].strip():
            rep.warning(prefix + ".cost_budget.tradeoff_note", "多目标取舍未说明")


def _check_source(s, idx, rep):
    base = "sources[%d]" % idx
    if not isinstance(s, dict):
        rep.error(base, "来源项必须是对象")
        return
    if not isinstance(s.get("locator"), str) or not s["locator"].strip():
        rep.error(base + ".locator", "locator 缺失")
    if not isinstance(s.get("quote"), str) or not s["quote"].strip():
        rep.error(base + ".quote", "逐字 quote 缺失")


def validate_result(result, output_dir, rep):
    if not isinstance(result, dict):
        rep.error("result", "result.json 必须是对象")
        return
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        rep.error("case_id", "case_id 缺失或为空")
    if result.get("status") not in M.STATUS_ENUM:
        rep.error("status", "status 必须是 %s" % "/".join(M.STATUS_ENUM))
    plan = result.get("plan")
    validate_plan(plan, rep, prefix="plan")
    if isinstance(plan, dict) and result.get("case_id") != plan.get("case_id"):
        rep.error("plan.case_id", "与顶层 case_id 不一致")
    for key in M.RESULT_META_KEYS:
        if key not in result:
            rep.error(key, "缺少顶层字段")
    sources = result.get("sources")
    if not isinstance(sources, list):
        rep.error("sources", "必须是列表")
    else:
        for i, s in enumerate(sources):
            _check_source(s, i, rep)
    for key in ("uncertainty", "limitations", "pending_human", "change_log",
                "errors", "degradations", "runtime"):
        if key in result and result[key] is not None and not isinstance(result[key], (list, dict)):
            rep.error(key, "必须是列表或对象")
    clog = result.get("change_log")
    if not isinstance(clog, list) or not clog:
        rep.error("change_log", "必须是非空审计轨迹")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        rep.error("artifacts", "必须是列表")
    else:
        for i, a in enumerate(artifacts):
            if not isinstance(a, dict) or not isinstance(a.get("path"), str):
                rep.error("artifacts[%d]" % i, "产物项须含 path 字符串")
                continue
            p = a["path"]
            if not os.path.isabs(p):
                p = os.path.join(output_dir or "", p)
            if not os.path.exists(p):
                rep.error("artifacts[%d]" % i, "产物文件不存在：%s" % p)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="validate.py",
                                     description="契约与可行域校验器")
    sub = parser.add_subparsers(dest="mode", required=True)
    p_plan = sub.add_parser("plan", help="校验 plan.json")
    p_plan.add_argument("--plan", required=True)
    p_plan.add_argument("--report", default=None)
    p_res = sub.add_parser("result", help="校验 result.json")
    p_res.add_argument("--result", required=True)
    p_res.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)

    rep = Reporter()
    if args.mode == "plan":
        obj, err = M.json_load_safe(args.plan)
        if obj is None:
            rep.error("plan.json", "无法读取或解析：%s" % err)
        else:
            validate_plan(obj, rep)
        report_path = args.report
    else:
        obj, err = M.json_load_safe(args.result)
        if obj is None:
            rep.error("result.json", "无法读取或解析：%s" % err)
        else:
            validate_result(obj, args.output_dir, rep)
        report_path = None

    report = {
        "errors": rep.errors,
        "warnings": rep.warnings,
        "counts": {"errors": len(rep.errors), "warnings": len(rep.warnings)},
    }
    if report_path:
        M.write_json(report_path, report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if rep.errors:
        return 1
    if rep.warnings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
