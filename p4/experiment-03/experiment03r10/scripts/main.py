#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI dispatcher for the experiment-parameter optimization Skill.

Subcommands
    init      discover input files, write /app/work/input_digest.json and an
              /app/output/result.json skeleton (status=partial)
    validate  run the evidence-first pipeline over /app/work/plan.json and write
              /app/work/plan.validation_report.json
    doe       deterministic candidate points from /app/work/plan.json
    finalize  merge /app/work/plan.json into /app/output/result.json, render an
              editable /app/output/plan.md and copy /app/output/plan.json
    check     verify /app/output/result.json contract + artifact existence

Defaults: --input /input --output /app/output --work /app/work.
All paths may be overridden for local testing. Standard library only.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

RESULT_KEYS = ["case_id", "status", "plan", "artifacts", "sources",
               "uncertainty", "limitations", "pending_human", "change_log",
               "errors", "degradations", "runtime"]


def _argparser():
    p = argparse.ArgumentParser(description="Experiment parameter optimization Skill")
    p.add_argument("cmd", choices=["init", "validate", "doe", "finalize", "check"])
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--report", default=None)
    p.add_argument("--strict-sources", action="store_true", default=False)
    return p


def cmd_init(args):
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)
    digest = core.make_input_digest(args.input)
    core.write_json(os.path.join(args.work, "input_digest.json"), digest)
    plan = core.new_skeleton_plan(digest.get("case_id") or "")
    core.write_json(os.path.join(args.work, "plan.json"), plan)
    result = new_skeleton_result(plan, digest)
    core.write_json(os.path.join(args.output, "result.json"), result)
    return result


def new_skeleton_result(plan, digest=None):
    return {
        "case_id": plan.get("case_id", ""),
        "status": "partial",
        "plan": plan,
        "artifacts": [],
        "sources": [],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "conflicts": []},
        "limitations": ["skeleton written before analysis; content pending"],
        "pending_human": [],
        "change_log": [{"ts": core.utcnow(), "step": "init",
                        "note": "partial skeleton落盘，先保证可交付"}],
        "errors": [],
        "degradations": [],
        "runtime": {"seconds": 0.0, "requests_estimate": 0,
                    "input_digest": digest if digest is not None else {}},
    }


def load_plan(args):
    obj, err = core.json_load_safe(os.path.join(args.work, "plan.json"))
    if obj is None:
        print("cannot load %s/plan.json: %s" % (args.work, err), file=sys.stderr)
        sys.exit(1)
    return obj


def cmd_validate(args):
    plan = load_plan(args)
    plan["case_id"] = plan.get("case_id") or ""
    report, normalized = core.validate_plan(plan, sources=plan.get("sources"),
                                            strict_sources=args.strict_sources)
    report_path = args.report or os.path.join(args.work, "plan.validation_report.json")
    core.write_json(report_path, report)
    # write back normalized plan so later steps see schema_version and defaults.
    core.write_json(os.path.join(args.work, "plan.json"), normalized)
    n_err, n_warn = len(report["errors"]), len(report["warnings"])
    print("validate: %d errors, %d warnings, %d sources -> %s"
          % (n_err, n_warn, report.get("source_count", 0), report_path))
    if n_err:
        for e in report["errors"]:
            print("  ERROR: %s" % e, file=sys.stderr)
    if n_warn:
        for w in report["warnings"]:
            print("  WARN: %s" % w, file=sys.stderr)
    sys.exit(1 if n_err else (2 if n_warn else 0))


def cmd_doe(args):
    plan = load_plan(args)
    out = core.generate_candidates(plan, seed=args.seed)
    core.write_json(os.path.join(args.work, "candidates.json"), out)
    print("doe: wrote %d candidates (seed=%s) to %s"
          % (out["count"], out["seed"], os.path.join(args.work, "candidates.json")))


def cmd_finalize(args):
    plan, err = core.json_load_safe(os.path.join(args.work, "plan.json"))
    if plan is None:
        print("cannot load plan.json: %s" % err, file=sys.stderr)
        sys.exit(1)
    plan = core.normalize_plan(plan)
    status = plan.get("status")
    if status not in core.STATUS_ENUM:
        status = "partial"
    plan["status"] = status

    t0 = time.time()
    # Regenerate source registry for result.json (evidence mapping).
    reg = core.collect_sources(plan, plan.get("sources"))
    core.write_json(os.path.join(args.output, "plan.json"), plan)
    plan_md = core.render_plan_md(plan)
    with open(os.path.join(args.output, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(plan_md)

    digest, _ = core.json_load_safe(os.path.join(args.work, "input_digest.json"))
    old, _ = core.json_load_safe(os.path.join(args.output, "result.json"))
    old = old if isinstance(old, dict) else {}
    result = {
        "case_id": plan.get("case_id", "") or old.get("case_id", ""),
        "status": status,
        "plan": plan,
        "artifacts": [
            {"path": "plan.md", "kind": "md",
             "exists": os.path.isfile(os.path.join(args.output, "plan.md"))},
            {"path": "plan.json", "kind": "json",
             "exists": os.path.isfile(os.path.join(args.output, "plan.json"))},
        ],
        "sources": reg.records,
        "uncertainty": plan.get("uncertainty") or old.get("uncertainty") or {
            "unverified_values": [], "missing_data": [], "conflicts": []},
        "limitations": plan.get("limitations") or old.get("limitations") or [],
        "pending_human": plan.get("pending_human") or old.get("pending_human") or [],
        "change_log": (old.get("change_log") or []) + [
            {"ts": core.utcnow(), "step": "finalize",
             "note": "merged plan.json -> result.json; status=%s" % status}],
        "errors": old.get("errors") or [],
        "degradations": old.get("degradations") or [],
        "runtime": {
            "seconds": round(time.time() - t0, 3),
            "requests_estimate": old.get("runtime", {}).get("requests_estimate", 0),
            "input_digest": (digest if isinstance(digest, dict) else
                             old.get("runtime", {}).get("input_digest", {})),
        },
    }
    core.write_json(os.path.join(args.output, "result.json"), result)
    print("finalize: wrote result.json + plan.md + plan.json under %s" % args.output)


def cmd_check(args):
    obj, err = core.json_load_safe(os.path.join(args.output, "result.json"))
    if obj is None:
        print("check: cannot read result.json: %s" % err, file=sys.stderr)
        sys.exit(1)
    errors, warnings = [], []
    for key in RESULT_KEYS:
        if key not in obj:
            errors.append("result.json missing key %r" % key)
    if not (obj.get("case_id") or "").strip():
        errors.append("case_id empty")
    if obj.get("status") not in core.STATUS_ENUM:
        errors.append("invalid status %r" % obj.get("status"))
    for art in obj.get("artifacts", []):
        path = os.path.join(args.output, art.get("path", ""))
        if not os.path.isfile(path):
            errors.append("artifact missing: %s" % path)
    if not obj.get("sources") and obj.get("status") == "ok":
        warnings.append("status=ok but sources empty; evidence mapping recommended")
    if not obj.get("change_log"):
        warnings.append("change_log empty")
    for e in errors:
        print("  ERROR: %s" % e, file=sys.stderr)
    for w in warnings:
        print("  WARN: %s" % w)
    print("check: %d errors, %d warnings" % (len(errors), len(warnings)))
    sys.exit(1 if errors else (2 if warnings else 0))


def main():
    args = _argparser().parse_args()
    return {
        "init": cmd_init,
        "validate": cmd_validate,
        "doe": cmd_doe,
        "finalize": cmd_finalize,
        "check": cmd_check,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
