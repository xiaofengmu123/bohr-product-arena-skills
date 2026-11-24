#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_plan.py — schema + feasible-domain + rubric-gate validation.

Validates /app/work/plan.json (default) or /app/output/result.json. Exit codes:
    0 = pass (no blocking errors)
    1 = blocking error (invalid JSON, missing case_id, out-of-domain value while
        status=ok, or status=ok with a failed hard rubric gate)
    2 = warnings only (acceptable for partial / insufficient_evidence)

The RUBRIC table is the single machine-readable source for the 16 scoring points;
main.py `audit` imports compute_rubric_audit from this module.
"""

import argparse
import json
import os
import sys

SCHEMA_VERSION = "2.0.0"
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
HARD_GATES = {"R01", "R02", "R04", "R13", "R16"}
EXCEPTION_SCENARIOS = ("无可行解", "约束冲突", "数据缺失", "设备报错",
                       "指标矛盾", "infeasible", "conflict", "missing",
                       "equipment", "contradict")

RUBRIC = [
    {"id": "R01", "dimension": "实际效果", "point": "任务完成度", "weight": 15},
    {"id": "R02", "dimension": "实际效果", "point": "结果正确性", "weight": 15},
    {"id": "R03", "dimension": "实际效果", "point": "结果完整性", "weight": 5},
    {"id": "R04", "dimension": "实际效果", "point": "结果可核验性", "weight": 5},
    {"id": "R05", "dimension": "使用增益", "point": "质量增益", "weight": 15},
    {"id": "R06", "dimension": "使用增益", "point": "效率增益", "weight": 5},
    {"id": "R07", "dimension": "使用增益", "point": "增益可验证性", "weight": 5},
    {"id": "R08", "dimension": "创新增益", "point": "新增能力", "weight": 5},
    {"id": "R09", "dimension": "创新增益", "point": "方法改进", "weight": 5},
    {"id": "R10", "dimension": "创新增益", "point": "创新证据", "weight": 5},
    {"id": "R11", "dimension": "可靠体验", "point": "调用与易用性", "weight": 3},
    {"id": "R12", "dimension": "可靠体验", "point": "运行稳定性", "weight": 4},
    {"id": "R13", "dimension": "可靠体验", "point": "异常处理", "weight": 3},
    {"id": "R14", "dimension": "生态贡献", "point": "文档完整性", "weight": 3},
    {"id": "R15", "dimension": "生态贡献", "point": "资源可复用性", "weight": 4},
    {"id": "R16", "dimension": "生态贡献", "point": "分享推广价值", "weight": 3},
]


def read_text_safe(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def json_load_safe(path):
    raw = read_text_safe(path)
    if raw is None:
        return None, "cannot read file"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:
        return None, "parse error: %s" % exc


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_within(value, lo, hi):
    if not _num(value):
        return True  # cannot check non-numeric; reported separately
    if _num(lo) and value < lo:
        return False
    if _num(hi) and value > hi:
        return False
    return True


def _nonempty(obj, key):
    v = obj.get(key)
    return bool(v) if not isinstance(v, (dict, list)) else bool(v)


def check_domain(plan, errors, warnings):
    for i, p in enumerate(plan.get("parameters", []) or []):
        if not isinstance(p, dict):
            errors.append("parameters[%d] not an object" % i)
            continue
        fr = p.get("feasible_range") or {}
        sv = p.get("suggested_value")
        if not _is_within(sv, fr.get("min"), fr.get("max")):
            errors.append(
                "parameter '%s' suggested_value %s outside feasible_range %s"
                % (p.get("name", "?"), sv, fr))
        for limit_key in ("equipment_limit", "material_limit",
                          "safety_threshold"):
            lim = p.get(limit_key) or {}
            if not _is_within(sv, lim.get("min"), lim.get("max")):
                errors.append(
                    "parameter '%s' suggested_value %s violates %s %s"
                    % (p.get("name", "?"), sv, limit_key, lim))
        if not (fr.get("min") is None and fr.get("max") is None):
            if fr.get("min") is None or fr.get("max") is None:
                warnings.append(
                    "parameter '%s' feasible_range missing one bound"
                    % p.get("name", "?"))
        if not p.get("quote") or not p.get("locator"):
            warnings.append(
                "parameter '%s' missing locator/quote" % p.get("name", "?"))


def check_schema(obj, errors):
    # Accept both plan.json (plan-shaped) and result.json (wrapped).
    if "plan" in obj:
        plan = obj["plan"]
    else:
        plan = obj
    for key in ("case_id", "status"):
        if key not in plan:
            errors.append("missing key '%s'" % key)
            return
    if not plan.get("case_id"):
        errors.append("case_id empty")
    if plan.get("status") not in STATUS_ENUM:
        errors.append("status not in %s" % (STATUS_ENUM,))


def _exception_coverage(plan):
    items = plan.get("exception_handling", []) or []
    if not items:
        return 0
    text = json.dumps(items, ensure_ascii=False).lower()
    hit = 0
    for kw in ("无可行解", "infeasible", "约束冲突", "conflict",
               "数据缺失", "missing", "设备报错", "equipment",
               "指标矛盾", "contradict"):
        if kw.lower() in text:
            hit += 1
    # We require at least five distinct keyword families; reuse kw families.
    families = ["无可行解/infeasible", "约束冲突/conflict",
                "数据缺失/missing", "设备报错/equipment",
                "指标矛盾/contradict"]
    hit = sum(1 for fam in families if any(kw.lower() in text
             for kw in fam.split("/")))
    return hit


def _confirmation_coverage(plan):
    items = plan.get("confirmations", []) or []
    text = json.dumps(items, ensure_ascii=False).lower()
    hit = 0
    for fam in ["安全/safety", "伦理/ethic", "设备/equipment/avail",
                "专家/expert"]:
        if any(kw.lower() in text for kw in fam.split("/")):
            hit += 1
    return hit


def compute_rubric_audit(obj):
    plan = obj.get("plan", {}) if "plan" in obj else obj
    ss = plan.get("search_strategy", {}) or {}
    cb = plan.get("cost_budget", {}) or {}
    ev = obj.get("evidence_map") or plan.get("evidence_map") or {}
    params = plan.get("parameters", []) or []
    metrics = plan.get("response_metrics", []) or []
    trace = plan.get("rubric_trace", []) or []

    bounded = 0
    for p in params:
        fr = p.get("feasible_range") or {}
        if isinstance(fr.get("min"), (int, float)) and \
           isinstance(fr.get("max"), (int, float)):
            bounded += 1
    exc_hits = _exception_coverage(plan)
    conf_hits = _confirmation_coverage(plan)
    baseline = None
    for key in ("baseline_score", "baseline_notes", "baseline"):
        if obj.get(key) is not None or plan.get(key) is not None:
            baseline = obj.get(key, plan.get(key, None))

    gates = {
        "R01": bool(params) and bool(metrics) and bool(ss.get("method")) and
               (bool(cb.get("items")) or bool(cb.get("total"))),
        "R02": all(_is_within(p.get("suggested_value"),
                              (p.get("feasible_range") or {}).get("min"),
                              (p.get("feasible_range") or {}).get("max"))
                   and _is_within(p.get("suggested_value"),
                                  (p.get("equipment_limit") or {}).get("min"),
                                  (p.get("equipment_limit") or {}).get("max"))
                   and _is_within(p.get("suggested_value"),
                                  (p.get("material_limit") or {}).get("min"),
                                  (p.get("material_limit") or {}).get("max"))
                   and _is_within(p.get("suggested_value"),
                                  (p.get("safety_threshold") or {}).get("min"),
                                  (p.get("safety_threshold") or {}).get("max"))
                   for p in params if isinstance(p, dict)),
        "R03": bool(params) and bool(metrics) and bool(ss) and bool(cb) and
               bool(plan.get("assumptions")) and bool(plan.get("variables"))
               and bool(plan.get("controls")) and bool(plan.get("procedures")),
        "R04": isinstance(ss.get("seed"), (int, float)) and
               bool(ss.get("initial_points")) and bool(ss.get("per_round"))
               and bool(ss.get("stop_criteria")) and
               bool((ev.get("claims") or [])),
        "R05": bounded >= 1 and bool((ev.get("claims") or [])) and
               all(p.get("locator") and p.get("quote")
                   for p in params if isinstance(p, dict)),
        "R06": bool(ss.get("per_round")) and bool(ss.get("stop_criteria")),
        "R07": baseline is not None,
        "R08": bounded >= 1,
        "R09": bool(metrics) and
               ss.get("method") in ("grid", "sequential", "bayes", "staged"),
        "R10": bool(ss.get("stop_criteria")) and
               any(m.get("prediction_note") for m in metrics
                   if isinstance(m, dict)),
        "R11": bool(ss.get("method")) and bool(ss.get("initial_points"))
               and bool(plan.get("procedures")),
        "R12": isinstance(ss.get("seed"), (int, float)) and
               not obj.get("errors"),
        "R13": exc_hits >= 5,
        "R14": conf_hits >= 4,
        "R15": bool(obj.get("artifacts", {}).get("plan.md")),
        "R16": bool(obj.get("artifacts", {}).get("plan.json")) and
               bool(plan.get("change_log")),
    }

    rows = []
    failures = []
    for r in RUBRIC:
        passed = bool(gates.get(r["id"], False))
        hard = r["id"] in HARD_GATES
        mapped = [t for t in trace
                  if isinstance(t, dict) and t.get("rubric_id") == r["id"]]
        state = "pass" if passed else ("fail" if hard else "warn")
        if not passed and hard:
            failures.append({"rubric_id": r["id"], "point": r["point"],
                             "dimension": r["dimension"]})
        rows.append({
            "rubric_id": r["id"], "dimension": r["dimension"],
            "point": r["point"], "weight": r["weight"], "gate": state,
            "evidence": [m.get("evidence") for m in mapped],
            "target_fields": [m.get("target_fields") for m in mapped],
        })
    summary = {"pass": sum(1 for x in rows if x["gate"] == "pass"),
               "warn": sum(1 for x in rows if x["gate"] == "warn"),
               "fail": sum(1 for x in rows if x["gate"] == "fail"),
               "na": sum(1 for x in rows if x["gate"] == "na")}
    return {"schema_version": SCHEMA_VERSION, "rubric": rows,
            "summary": summary, "failures": failures}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", help="plan.json or result.json to validate")
    p.add_argument("--report", help="write JSON report to this path")
    p.add_argument("--work", help="work dir for audit_report.json (audit only)")
    args = p.parse_args(argv)

    obj, err = json_load_safe(args.path)
    if err is not None:
        print("BLOCK: %s" % err)
        return 1

    errors, warnings = [], []
    check_schema(obj, errors)
    plan = obj.get("plan", obj)
    check_domain(plan, errors, warnings)

    audit = compute_rubric_audit(obj)
    status = obj.get("status", "partial")
    if status == "ok" and audit["failures"]:
        errors.append("status=ok but hard rubric gates failed: %s"
                      % [f["rubric_id"] for f in audit["failures"]])
    if status == "ok" and warnings:
        errors.append("status=ok but %d warnings remain" % len(warnings))

    report = {"errors": errors, "warnings": warnings,
              "rubric_audit": audit["summary"], "failures": audit["failures"],
              "status": status}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if args.work:
        with open(os.path.join(args.work, "audit_report.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(audit, fh, ensure_ascii=False, indent=2)

    if errors:
        print("ERRORS:")
        for e in errors:
            print(" - " + e)
        return 1
    if warnings:
        print("WARNINGS (%d):" % len(warnings))
        for w in warnings[:20]:
            print(" - " + w)
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
