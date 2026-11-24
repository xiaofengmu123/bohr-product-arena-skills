#!/usr/bin/env python3
"""Entry point for the reviewer-revision skill.

All paths below are relative to the skill root (/skills/weekly-skill).

  python scripts/main.py scaffold --case /input/case.json --task /input/task.md \
      --work /app/work --out /app/output
  python scripts/main.py ledger --case /input/case.json --work /app/work --out /app/output
  python scripts/main.py validate --out /app/output
  python scripts/main.py run --case /input/case.json --task /input/task.md \
      --work /app/work --out /app/output

`run` chains scaffold -> ledger -> validate and prints a JSON summary. Exit code is
0 even when the result is partial: this is a scaffold/verify tool, not a grader, and
must never crash on empty/bad/missing input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402


def _summary(action, errors, extra=None):
    doc = {"ok": not errors, "action": action, "errors": errors}
    if extra:
        doc.update(extra)
    print(json.dumps(doc, ensure_ascii=False))
    return 0


def cmd_scaffold(args):
    result, err = core.build_skeleton(args.case, args.task, args.out, args.work)
    return _summary("scaffold", [err] if err else [], {"status": result.get("status"),
                                                       "case_id": result.get("case_id")})


def cmd_ledger(args):
    case_obj, case_err = core.load_json(args.case) if os.path.exists(args.case) else (None, None)
    comments_text, c_err = _first_material(args, "reviewer-comments")
    baseline_text, b_err = _first_material(args, "manuscript-baseline")
    errs = [e for e in (case_err, c_err, b_err) if e]
    _, led_errs = core.build_ledgers(comments_text, baseline_text, args.out, args.work)
    errs.extend(led_errs)
    n = len(core.parse_comment_blocks(comments_text or ""))
    return _summary("ledger", errs, {"comments_extracted": n})


def cmd_validate(args):
    problems = core.validate_result(os.path.join(args.out, "result.json"), args.out)
    return _summary("validate", problems, {"problems": problems})


def cmd_run(args):
    r = cmd_scaffold(args)
    if r != 0:
        return r
    r = cmd_ledger(args)
    if r != 0:
        return r
    return cmd_validate(args)


def _first_material(args, name_key):
    """Find a material file whose basename contains name_key, under the input dir."""
    input_dir = os.path.dirname(args.case) if args.case else "/input"
    candidates = []
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for f in files:
                if name_key in f.lower():
                    candidates.append(os.path.join(root, f))
    candidates.sort()
    if not candidates:
        return None, "material not found: *%s*" % name_key
    return core.read_text(candidates[0])


def main(argv=None):
    ap = argparse.ArgumentParser(description="reviewer-revision scaffold/ledger/validate tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scaffold", "ledger", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--case", default="/input/case.json")
        sp.add_argument("--task", default="/input/task.md")
        sp.add_argument("--work", default="/app/work")
        sp.add_argument("--out", default="/app/output")
        sp.set_defaults(func={"scaffold": cmd_scaffold, "ledger": cmd_ledger,
                              "run": cmd_run}[name])
    vp = sub.add_parser("validate")
    vp.add_argument("--out", default="/app/output")
    vp.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - never crash, always emit an honest summary
        print(json.dumps({"ok": False, "action": args.cmd,
                          "errors": ["unexpected error: %s" % exc]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
