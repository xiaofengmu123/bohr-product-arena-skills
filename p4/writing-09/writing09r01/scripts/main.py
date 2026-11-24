#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry point for the results-and-discussion writing skill.

Subcommands:
  read     normalize input dir -> <work>/input_summary.json
  skeleton build valid partial result.json from input_summary.json
  patch    deep-merge a JSON patch into result.json (lists replaced wholesale)
  check    validate result.json structure/evidence/consistency/overclaim
  run      read + skeleton in one shot (first honest partial deliverable)

All paths default to the runtime contract: /input, /app/output, /app/work.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as ci  # noqa: E402


def _add_paths(p):
    p.add_argument("--input", default="/input")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def cmd_read(a):
    summary, path = ci.normalize_input(a.input, a.work)
    print(json.dumps({"ok": True, "summary": path, "case_id": summary.get("case_id"),
                      "materials": len(summary.get("materials", [])),
                      "errors": len(summary.get("errors", []))}, ensure_ascii=False))
    return 0


def cmd_skeleton(a):
    summary, _ = ci.load_json_tolerant(os.path.join(a.work, "input_summary.json"))
    if summary is None:
        summary, _ = ci.normalize_input(a.input, a.work)
    result, path = ci.build_skeleton(summary, a.out)
    print(json.dumps({"ok": True, "result": path, "status": result.get("status"),
                      "fields": sorted(result.keys())}, ensure_ascii=False))
    return 0


def cmd_patch(a):
    result, err = ci.apply_patch(os.path.join(a.out, "result.json"), a.patch)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "case_id": result.get("case_id"),
                      "status": result.get("status")}, ensure_ascii=False))
    return 0


def cmd_check(a):
    summary_path = a.summary or os.path.join(a.work, "input_summary.json")
    if not os.path.exists(summary_path):
        summary_path = None
    report = ci.check_result(os.path.join(a.out, "result.json"), summary_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_run(a):
    summary, _ = ci.normalize_input(a.input, a.work)
    result, path = ci.build_skeleton(summary, a.out)
    print(json.dumps({"ok": True, "result": path, "case_id": result.get("case_id"),
                      "status": result.get("status")}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="results-and-discussion writing skill runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_read = _add_paths(sub.add_parser("read", help="normalize input"))
    p_read.set_defaults(func=cmd_read)

    p_skel = _add_paths(sub.add_parser("skeleton", help="build valid partial result skeleton"))
    p_skel.set_defaults(func=cmd_skeleton)

    p_patch = _add_paths(sub.add_parser("patch", help="merge patch into result.json"))
    p_patch.add_argument("--patch", required=True)
    p_patch.set_defaults(func=cmd_patch)

    p_check = _add_paths(sub.add_parser("check", help="validate result.json"))
    p_check.add_argument("--summary", default=None)
    p_check.set_defaults(func=cmd_check)

    p_run = _add_paths(sub.add_parser("run", help="read + skeleton"))
    p_run.set_defaults(func=cmd_run)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
