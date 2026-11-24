#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Results & Discussion writing Skill — CLI entry point.

Usage (from the SKILL.md directory; relative script paths work):

    python scripts/main.py \
        --case /input/case.json --task /input/task.md \
        --input-dir /input --work /app/work --output /app/output

Phases (default runs discover -> skeleton -> scaffold, the 3-minute bootstrap):

    --phase validate    validate result.json against the output_contract
    --phase selftest    run the self-test fixture (no case required)
    --phase numeric     dump the numeric inventory to stdout (JSON)

Standard library only. All phases are deterministic and never raise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def cmd_bootstrap(args) -> int:
    outcome = core.run_pipeline(args.case, args.task, args.input_dir,
                                args.work, args.output)
    print(json.dumps({
        "ok": True,
        "case_id": outcome.get("summary", {}).get("case_id"),
        "result_path": outcome.get("result_path"),
        "scaffolds": outcome.get("scaffold_paths"),
        "errors": outcome.get("errors"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args) -> int:
    result_path = os.path.join(args.output, "result.json")
    summary_path = os.path.join(args.work, "input_summary.json")
    if not os.path.isfile(result_path):
        print(json.dumps({"ok": False, "error": f"missing {result_path}"},
                         ensure_ascii=False, indent=2))
        return 2
    issues = core.validate_result(result_path, summary_path)
    print(json.dumps({"ok": len(issues) == 0, "issues": issues},
                     ensure_ascii=False, indent=2))
    return 0 if not issues else 1


def cmd_numeric(args) -> int:
    summary_path = os.path.join(args.work, "input_summary.json")
    summary, err = core.read_json(summary_path)
    if err is not None:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False,
                         indent=2))
        return 2
    inventory = core.build_numeric_inventory(summary)
    print(json.dumps(inventory, ensure_ascii=False, indent=2))
    return 0


def cmd_selftest(_args) -> int:
    from selftest import run as selftest_run  # noqa: E402  (same dir)
    ok = selftest_run()
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Results & Discussion section writing Skill")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--phase", default="bootstrap",
                    choices=["bootstrap", "validate", "numeric", "selftest"])
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    handlers = {
        "bootstrap": cmd_bootstrap,
        "validate": cmd_validate,
        "numeric": cmd_numeric,
        "selftest": cmd_selftest,
    }
    return handlers[args.phase](args)


if __name__ == "__main__":
    sys.exit(main())
