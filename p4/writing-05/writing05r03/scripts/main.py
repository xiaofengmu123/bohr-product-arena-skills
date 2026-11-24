#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for the evidence-driven-writing Skill (stdlib only).

Subcommands
    scaffold  write an honest partial result.json skeleton immediately
    finalize  assemble result.json + evidence_table.md + manuscript.md
    validate  check an existing result.json against the output contract
    selftest  run the determinism/robustness self-check (delegates to selftest.py)

All paths are passed explicitly; the Agent should call these with absolute
paths at runtime (/input, /app/output, /app/work). Local self-tests may pass
any directory. ``--fixed-time`` makes change_log/runtime timestamps
deterministic so two runs over the same input are byte-identical.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _print_outcome(name, result, errors):
    payload = {
        "step": name,
        "status": result.get("status") if isinstance(result, dict) else "failed",
        "case_id": result.get("case_id") if isinstance(result, dict) else None,
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_scaffold(args):
    result, errors = core_impl.scaffold(
        args.case, args.task, args.out, args.work, fixed_time=args.fixed_time
    )
    _print_outcome("scaffold", result, errors)
    return 0


def cmd_finalize(args):
    result, errors = core_impl.finalize(
        args.analysis, args.case, args.out, args.work, fixed_time=args.fixed_time
    )
    _print_outcome("finalize", result, errors)
    return 0


def cmd_validate(args):
    result, errors = core_impl.load_json_safe(args.result)
    if not isinstance(result, dict):
        print(json.dumps({"step": "validate", "ok": False, "errors": errors}, ensure_ascii=False))
        return 1
    problems = core_impl.validate_result(result)
    print(
        json.dumps(
            {"step": "validate", "ok": not problems, "problems": problems},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not problems else 1


def cmd_selftest(args):
    selftest = os.path.join(HERE, "selftest.py")
    if not os.path.exists(selftest):
        print(json.dumps({"step": "selftest", "ok": False, "errors": ["selftest.py missing"]}, ensure_ascii=False))
        return 1
    import subprocess

    code = subprocess.run(
        [sys.executable, selftest, "--fixed-time", args.fixed_time or "2026-01-01T00:00:00Z"]
    ).returncode
    return 0 if code == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scaffold", help="write partial result.json skeleton")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--fixed-time", default=None)
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("finalize", help="assemble final artifacts")
    p.add_argument("--analysis", default="/app/work/analysis.json")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--fixed-time", default=None)
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("validate", help="check an existing result.json")
    p.add_argument("--result", default="/app/output/result.json")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("selftest", help="run determinism/robustness self-check")
    p.add_argument("--fixed-time", default="2026-01-01T00:00:00Z")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
