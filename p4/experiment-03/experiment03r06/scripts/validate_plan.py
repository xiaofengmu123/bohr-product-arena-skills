#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate plan.json / result.json / intermediate representations.

Exit codes
    0  pass (no blocking errors)
    1  blocking errors (schema / feasible-domain / status violations)
    2  warnings only (evidence gaps, unverified values, empty lists)
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

try:
    from main import (PLAN_CONTENT_KEYS, STATUS_ENUM, SCHEMA_VERSION,
                      json_load_safe, _num)
except Exception:  # noqa: BLE001
    PLAN_CONTENT_KEYS = ["research_question", "objective", "assumptions",
                         "variables", "parameters", "controls", "procedures",
                         "resources", "response_metrics", "interpretation_criteria",
                         "search_strategy", "cost_budget", "basis", "risks",
                         "exception_handling", "confirmations"]
    STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
    SCHEMA_VERSION = "1.0"

    def json_load_safe(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh), None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


SEARCH_METHODS = ("grid", "sequential", "bayes", "staged")
REQ_PARAM = ("id", "name", "unit", "recommended_value", "domain")
REQ_METRIC = ("id", "name", "unit", "direction", "measurement_method", "target")


def _err(report, msg):
    report["errors"].append(msg)


def _warn(report, msg):
    report["warnings"].append(msg)


def detect_type(obj):
    if not isinstance(obj, dict):
        return "unknown"
    if "plan" in obj and "case_id" in obj:
        return "result"
    if "feasibility" in obj:
        return "constraint_model"
    if "metrics" in obj and "relations" in obj:
        return "response_model"
    if "events" in obj and "schema_version" in obj:
        return "trace"
    if "cost_items" in obj:
        return "tradeoff_model"
    if "sources" in obj and "method" not in obj and "initial_points" not in obj \
            and "parameters" not in obj:
        return "evidence_map"
    if "method" in obj and "initial_points" in obj:
        return "search_model"
    if "search_strategy" in obj and "parameters" in obj:
        return "plan"
    return "unknown"


def _num_field(obj, key):
    return _num(obj.get(key))


def validate_param(report, p):
    for key in ("id", "name", "unit"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            _warn(report, "parameter missing %s: %r" % (key, p.get("id", "?")))
    dom = p.get("domain") or {}
    lo, hi, step = _num(dom.get("min")), _num(dom.get("max")), _num(dom.get("step"))
    rec = _num(p.get("recommended_value"))
    if lo is None or hi is None:
        _warn(report, "parameter %s domain min/max missing" % p.get("id", "?"))
    if step is None or step <= 0:
        _warn(report, "parameter %s domain step missing/invalid" % p.get("id", "?"))
    if lo is not None and hi is not None and rec is not None and not (lo <= rec <= hi):
        _err(report, "parameter %s recommended %s outside domain [%s,%s]"
             % (p.get("id", "?"), rec, lo, hi))
    for hb in p.get("hard_bounds") or []:
        if not isinstance(hb, dict):
            continue
        hlo, hhi = _num(hb.get("min")), _num(hb.get("max"))
        if lo is not None and hi is not None:
            if hlo is not None and lo < hlo:
                _err(report, "parameter %s domain min %s < hard bound %s"
                     % (p.get("id", "?"), lo, hlo))
            if hhi is not None and hi > hhi:
                _err(report, "parameter %s domain max %s > hard bound %s"
                     % (p.get("id", "?"), hi, hhi))
        if not (hb.get("locator") and hb.get("quote")):
            _warn(report, "parameter %s hard_bound lacks locator/quote"
                  % p.get("id", "?"))


def validate_constraint_model(obj, report):
    if not isinstance(obj.get("parameters"), list):
        _err(report, "constraint_model.parameters must be a list")
        return
    for p in obj["parameters"]:
        if isinstance(p, dict):
            validate_param(report, p)
    feas = obj.get("feasibility") or {}
    if feas.get("status") not in ("feasible", "conflict", "insufficient_evidence",
                                  "unverified", None):
        _warn(report, "feasibility.status unrecognized: %r" % feas.get("status"))


def validate_response_model(obj, report):
    metrics = obj.get("metrics") or []
    if not metrics:
        _warn(report, "response_model.metrics is empty")
    for m in metrics:
        if not isinstance(m, dict):
            continue
        for key in ("id", "name", "unit", "direction", "measurement_method"):
            if not m.get(key):
                _warn(report, "metric missing %s: %r" % (key, m.get("id", "?")))
        if _num(m.get("target")) is None:
            _warn(report, "metric %s target missing" % m.get("id", "?"))
        pred = m.get("prediction") or {}
        if not pred.get("method"):
            _warn(report, "metric %s prediction.method missing" % m.get("id", "?"))


def validate_search_model(obj, report):
    method = obj.get("method")
    if method and method not in SEARCH_METHODS:
        _err(report, "search method invalid: %r" % method)
    if not obj.get("stop_criteria"):
        _warn(report, "search_model.stop_criteria empty")
    if not obj.get("candidates"):
        _warn(report, "search_model.candidates empty (run scripts/main.py doe)")
    if not obj.get("reproducible", True):
        _warn(report, "search_model.reproducible is false")


def validate_evidence_map(obj, report):
    sources = obj.get("sources") or []
    if not sources:
        _warn(report, "evidence_map.sources empty")
    for s in sources:
        if not isinstance(s, dict):
            continue
        if not s.get("locator"):
            _warn(report, "source missing locator: %r" % s.get("id", "?"))
        if not s.get("quote"):
            _warn(report, "source %s missing verbatim quote" % s.get("id", "?"))


def validate_tradeoff_model(obj, report):
    items = obj.get("cost_items") or []
    if not items:
        _warn(report, "tradeoff_model.cost_items empty")
    for it in items:
        if isinstance(it, dict) and _num(it.get("amount")) is None:
            _warn(report, "cost item %s amount missing" % it.get("name", "?"))
    if not obj.get("tradeoff_note"):
        _warn(report, "tradeoff_model.tradeoff_note empty")


def validate_plan(obj, report):
    for key in PLAN_CONTENT_KEYS:
        if key not in obj:
            _err(report, "plan missing key: %s" % key)
    for p in obj.get("parameters") or []:
        if isinstance(p, dict):
            validate_param(report, p)
    for m in obj.get("response_metrics") or []:
        if isinstance(m, dict):
            for key in ("id", "name", "unit", "direction", "measurement_method"):
                if not m.get(key):
                    _warn(report, "metric missing %s: %r" % (key, m.get("id", "?")))
    ss = obj.get("search_strategy") or {}
    if ss.get("method") and ss["method"] not in SEARCH_METHODS:
        _err(report, "search method invalid: %r" % ss.get("method"))
    if not ss.get("stop_criteria"):
        _warn(report, "search_strategy.stop_criteria empty")
    if not ss.get("candidates"):
        _warn(report, "search_strategy.candidates empty (run doe)")
    if not obj.get("sources"):
        _warn(report, "plan.sources empty")
    for s in obj.get("sources") or []:
        if isinstance(s, dict) and not (s.get("locator") and s.get("quote")):
            _warn(report, "source lacks locator/quote")
    if not obj.get("risks"):
        _warn(report, "plan.risks empty")
    if not obj.get("exception_handling"):
        _warn(report, "plan.exception_handling empty")
    if not obj.get("confirmations"):
        _warn(report, "plan.confirmations empty")


def validate_result(obj, report):
    if not obj.get("case_id"):
        _err(report, "result.case_id empty")
    if obj.get("status") not in STATUS_ENUM:
        _err(report, "result.status invalid: %r" % obj.get("status"))
    if not isinstance(obj.get("plan"), dict):
        _err(report, "result.plan missing/not dict")
    else:
        validate_plan(obj["plan"], report)
    if "sources" not in obj:
        _err(report, "result.sources missing")
    elif not obj["sources"]:
        _warn(report, "result.sources empty")
    if not isinstance(obj.get("uncertainty"), dict):
        _err(report, "result.uncertainty missing")
    if not isinstance(obj.get("change_log"), list):
        _err(report, "result.change_log missing")
    elif not obj["change_log"]:
        _warn(report, "result.change_log empty")
    for path in (obj.get("artifacts") or {}).values():
        if not path:
            _warn(report, "artifact path empty")
        elif not os.path.exists(path):
            _warn(report, "artifact not found: %s" % path)
    if not obj.get("intermediate_representations"):
        _warn(report, "result.intermediate_representations missing")


def validate_obj(obj, report):
    kind = detect_type(obj)
    report["kind"] = kind
    if not isinstance(obj, dict):
        _err(report, "top-level value is not an object")
        return
    if kind == "unknown":
        _warn(report, "unrecognized document type")
        return
    if obj.get("schema_version") and obj["schema_version"] != SCHEMA_VERSION:
        _warn(report, "schema_version %s != expected %s"
              % (obj["schema_version"], SCHEMA_VERSION))
    if "case_id" in obj and not obj.get("case_id"):
        _warn(report, "case_id empty")
    dispatch = {
        "constraint_model": validate_constraint_model,
        "response_model": validate_response_model,
        "search_model": validate_search_model,
        "evidence_map": validate_evidence_map,
        "tradeoff_model": validate_tradeoff_model,
        "plan": validate_plan,
        "result": validate_result,
    }
    if kind in dispatch:
        dispatch[kind](obj, report)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate_plan.py")
    ap.add_argument("path", help="plan.json / result.json / intermediate to validate")
    ap.add_argument("--report", help="write JSON report to this path")
    args = ap.parse_args(argv)
    obj, err = json_load_safe(args.path)
    report = {"path": args.path, "kind": "unknown", "errors": [], "warnings": []}
    if obj is None:
        report["errors"].append("cannot read/parse: %s" % err)
    else:
        validate_obj(obj, report)
    if args.report:
        try:
            os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            report["warnings"].append("cannot write report: %s" % exc)
    print("validate %s: kind=%s errors=%d warnings=%d"
          % (args.path, report["kind"], len(report["errors"]),
             len(report["warnings"])))
    for e in report["errors"]:
        print("  [ERROR] " + e)
    for w in report["warnings"]:
        print("  [WARN ] " + w)
    if report["errors"]:
        return 1
    if report["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
