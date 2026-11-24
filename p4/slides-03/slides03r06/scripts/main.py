#!/usr/bin/env python3
"""Entry point for the academic-talk-organizer Skill.

Subcommands
-----------
scaffold   Write an early valid result.json skeleton plus plan.json and
           figure_model.json. Safe to run in the first minutes; never raises.
update     Merge the intermediate representations back into result.json.
validate   Check the contract, evidence provenance and artifact existence.
summary    Print a compact pipeline status.

All operations use only the Python standard library and paths passed by the
caller. Defaults point at the evaluator locations (/input, /app/work,
/app/output) but can be overridden for local demo runs. The common options
(--case/--task/--work/--output) are accepted both before and after the
subcommand, so every invocation form in SKILL.md works unchanged.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def add_common(parser, include_case_task=True):
    """Attach the shared path options to a parser.

    They are declared on both the top-level parser and each subparser so the
    options may appear before or after the subcommand word.
    """
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    if include_case_task:
        parser.add_argument("--case", default="/input/case.json")
        parser.add_argument("--task", default="/input/task.md")


def cmd_scaffold(args):
    result, ok = core_impl.build_scaffold(
        args.case, args.task, args.work, args.output)
    print(json.dumps({
        "ok": ok,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "result": os.path.join(args.output, "result.json"),
        "plan": os.path.join(args.work, "plan.json"),
        "figure_model": os.path.join(args.work, "figure_model.json"),
        "uncertainty": len(result.get("uncertainty") or []),
        "errors": result.get("errors"),
    }, ensure_ascii=False))
    return 0


def cmd_update(args):
    errors, warnings, result = core_impl.merge(
        args.result, args.plan, args.figures)
    print(json.dumps({
        "ok": not errors,
        "status": result.get("status"),
        "evidence_entries": len(result.get("evidence_map") or []),
        "errors": errors,
        "warnings": warnings,
    }, ensure_ascii=False))
    return 0


def cmd_validate(args):
    errors, warnings, ok = core_impl.validate(
        args.result, args.work, args.output, strict=args.strict)
    print(json.dumps({
        "ok": ok,
        "strict": args.strict,
        "errors": errors,
        "warnings": warnings,
    }, ensure_ascii=False))
    return 0 if ok else 1


def cmd_summary(args):
    summary = core_impl.summarize(args.result, args.plan, args.figures)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="academic-talk-organizer pipeline (standard library only)")
    add_common(ap)
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scaffold", help="write early partial skeleton")
    add_common(s)
    s.set_defaults(func=cmd_scaffold)

    u = sub.add_parser("update", help="merge plan.json + figure_model.json")
    u.add_argument("--result", default="/app/output/result.json")
    u.add_argument("--plan", default="/app/work/plan.json")
    u.add_argument("--figures", default="/app/work/figure_model.json")
    u.set_defaults(func=cmd_update)

    v = sub.add_parser("validate", help="validate contract and evidence")
    add_common(v, include_case_task=False)
    v.add_argument("--result", default="/app/output/result.json")
    v.add_argument("--strict", action="store_true",
                   help="treat evidence/artifact problems as errors")
    v.set_defaults(func=cmd_validate)

    m = sub.add_parser("summary", help="compact status")
    m.add_argument("--result", default="/app/output/result.json")
    m.add_argument("--plan", default="/app/work/plan.json")
    m.add_argument("--figures", default="/app/work/figure_model.json")
    m.set_defaults(func=cmd_summary)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
