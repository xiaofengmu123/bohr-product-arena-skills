#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill —— 契约与可行域校验器（仅标准库）。

子命令
    validate-plan  校验 /app/work/plan.json：schema、参数可行域、口径完整性
    result         校验 /app/output/result.json：契约字段、产物文件存在性

退出码：0=通过（无阻断错误）；1=存在阻断错误；2=仅警告。
本校验器是纯函数：同一输入报告逐字节一致。
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

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
REQUIRED_PLAN_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _err(rep, msg):
    rep["errors"].append(msg)


def _warn(rep, msg):
    rep["warnings"].append(msg)


def validate_plan(plan):
    rep = {"errors": [], "warnings": []}
    if not isinstance(plan, dict):
        _err(rep, "plan is not a JSON object")
        return rep
    for key in REQUIRED_PLAN_KEYS:
        if key not in plan:
            _err(rep, "missing required key: %s" % key)

    if not (plan.get("research_question") or plan.get("objective")):
        _warn(rep, "research_question/objective empty; status may fall to "
                   "insufficient_evidence")

    for p in (plan.get("parameters") or []):
        if not isinstance(p, dict):
            _err(rep, "parameter entry is not an object")
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            _err(rep, "parameter missing name")
            continue
        lo, hi, sug = _num(p.get("min")), _num(p.get("max")), _num(p.get("suggested"))
        if lo is None or hi is None:
            _warn(rep, "parameter %s missing numeric min/max" % name)
        elif sug is not None and not (lo <= sug <= hi):
            _err(rep, "parameter %s suggested=%s outside [%s, %s]"
                 % (name, sug, lo, hi))
        if not p.get("unit"):
            _warn(rep, "parameter %s missing unit" % name)
        if not p.get("step") and not p.get("n_levels"):
            _warn(rep, "parameter %s missing step/n_levels" % name)
        if not (p.get("basis_locator") and p.get("basis_quote")):
            _warn(rep, "parameter %s missing basis locator/quote" % name)

    for m in (plan.get("response_metrics") or []):
        if not isinstance(m, dict):
            _err(rep, "response_metric entry is not an object")
            continue
        name = str(m.get("name", "")).strip()
        if not name:
            _err(rep, "response_metric missing name")
            continue
        for f in ("unit", "method", "direction", "target"):
            if not m.get(f):
                _warn(rep, "response_metric %s missing %s" % (name, f))
        if not m.get("prediction_method"):
            _warn(rep, "response_metric %s missing prediction_method "
                       "(must disclose proxy/empirical basis)" % name)

    ss = plan.get("search_strategy") or {}
    if not isinstance(ss, dict):
        _err(rep, "search_strategy must be an object")
    elif not ss.get("method"):
        _err(rep, "search_strategy.method empty")
    elif not (ss.get("stop_criteria") or ss.get("per_round")):
        _warn(rep, "search_strategy missing stop_criteria/per_round")

    if plan.get("parameters") and not plan.get("response_metrics"):
        _warn(rep, "parameters present but response_metrics empty; "
                   "add at least one measurable metric")

    for s in (plan.get("sources") or []):
        if not isinstance(s, dict):
            continue
        if not (s.get("locator") and s.get("quote")):
            _warn(rep, "source missing locator or verbatim quote")

    if not (plan.get("risks") or plan.get("exception_handling")):
        _warn(rep, "risks/exception_handling empty; feasibility conflicts "
                   "must be named")
    return rep


def check_result(result, output_dir):
    rep = {"errors": [], "warnings": []}
    if not isinstance(result, dict):
        _err(rep, "result is not a JSON object")
        return rep
    if not str(result.get("case_id", "")).strip():
        _err(rep, "case_id missing or empty")
    if result.get("status") not in STATUS_ENUM:
        _err(rep, "status not in %s" % (STATUS_ENUM,))
    if not isinstance(result.get("plan"), dict):
        _err(rep, "plan missing or not an object")
    else:
        for key in REQUIRED_PLAN_KEYS:
            if key not in result["plan"]:
                _err(rep, "plan missing key: %s" % key)
    if not isinstance(result.get("sources"), list):
        _err(rep, "sources must be a list")
    else:
        for s in result["sources"]:
            if isinstance(s, dict) and not (s.get("locator") and s.get("quote")):
                _warn(rep, "source missing locator or verbatim quote")
    if not isinstance(result.get("uncertainty"), dict):
        _err(rep, "uncertainty must be an object")
    if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
        _warn(rep, "change_log empty or not a list")
    for f in ("errors", "degradations", "limitations", "pending_human"):
        if f not in result or not isinstance(result.get(f), list):
            _err(rep, "missing list field: %s" % f)
    arts = result.get("artifacts") or []
    if not isinstance(arts, list):
        _err(rep, "artifacts must be a list")
    else:
        for art in arts:
            p = art.get("path") if isinstance(art, dict) else None
            if not p:
                _warn(rep, "artifact missing path")
            elif not os.path.isfile(os.path.join(output_dir, p)):
                _err(rep, "artifact does not exist: %s" % p)
    if not isinstance(result.get("input_fingerprint"), str) or not result.get("input_fingerprint"):
        _warn(rep, "input_fingerprint missing; determinism not verifiable")
    return rep


def _emit(rep, label):
    for e in rep["errors"]:
        print("[%s:error] %s" % (label, e))
    for w in rep["warnings"]:
        print("[%s:warning] %s" % (label, w))
    if rep["errors"]:
        return 1
    if rep["warnings"]:
        return 2
    return 0


def cmd_validate_plan(argv):
    ap = argparse.ArgumentParser(prog="check.py validate-plan")
    ap.add_argument("plan", help="path to plan.json")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    with open(a.plan, "r", encoding="utf-8-sig") as fh:
        plan = json.load(fh)
    rep = validate_plan(plan)
    if a.report:
        with open(a.report, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    return _emit(rep, "plan")


def cmd_check_result(argv):
    ap = argparse.ArgumentParser(prog="check.py result")
    ap.add_argument("result", help="path to result.json")
    ap.add_argument("--output-dir", default=None)
    a = ap.parse_args(argv)
    with open(a.result, "r", encoding="utf-8-sig") as fh:
        result = json.load(fh)
    out_dir = a.output_dir or os.path.dirname(os.path.abspath(a.result))
    rep = check_result(result, out_dir)
    return _emit(rep, "result")


def run(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "validate-plan":
        return cmd_validate_plan(argv[1:])
    if argv[0] == "result":
        return cmd_check_result(argv[1:])
    print("unknown subcommand: %s" % argv[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
