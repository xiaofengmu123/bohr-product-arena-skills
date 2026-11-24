#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for the Results & Discussion writing skill.

Subcommands (all deterministic, standard library only):

    read      normalize input        -> /app/work/input_summary.json
    skeleton  write partial skeleton -> /app/output/result.json
    claim     auto-extract numbers   -> /app/work/claims.json
    draft     build manuscript.md    -> /app/output/manuscript.md
    merge     write ledger into result.json facts_sources
    check     validate result.json + consistency + overclaim + source coverage
    selftest  run the built-in self-test (delegates to scripts/selftest.py)
    pipeline  read -> skeleton -> claim -> draft -> merge -> check

Default (no subcommand): pipeline with the safe /input /app/output /app/work
paths. Any step leaves a parseable result.json behind.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import core

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_summary(work_dir):
    path = os.path.join(work_dir, "input_summary.json")
    if not os.path.exists(path):
        raise SystemExit("input_summary.json not found; run `main.py read` first")
    return core.load_json(path, {})


def _load_ledger(work_dir):
    path = os.path.join(work_dir, "claims.json")
    if not os.path.exists(path):
        return {"claims": [], "manual": []}
    return core.load_json(path, {"claims": [], "manual": []})


def cmd_read(a):
    core.normalize_input(a.case, a.task, a.input, a.work)
    print(json.dumps({"ok": True, "summary": os.path.join(a.work, "input_summary.json")},
                     ensure_ascii=False))
    return 0


def cmd_skeleton(a):
    summary = _load_summary(a.work)
    core.build_skeleton(summary, a.output, "partial")
    print(json.dumps({"ok": True, "result": os.path.join(a.output, "result.json")},
                     ensure_ascii=False))
    return 0


def cmd_claim(a):
    summary = _load_summary(a.work)
    ledger = core.build_claims(summary, a.work)
    print(json.dumps({"ok": True, "claims": len(ledger.get("claims", [])),
                      "file": os.path.join(a.work, "claims.json")}, ensure_ascii=False))
    return 0


def cmd_draft(a):
    ledger = _load_ledger(a.work)
    path = core.draft_from_claims(ledger, a.output)
    print(json.dumps({"ok": True, "manuscript": path}, ensure_ascii=False))
    return 0


def cmd_merge(a):
    summary = _load_summary(a.work)
    ledger = _load_ledger(a.work)
    result = core.load_json(os.path.join(a.output, "result.json"), {})
    if not result:
        result = core.build_skeleton(summary, a.output, "partial")
    core.merge_ledger_into_result(result, ledger, a.output)
    print(json.dumps({"ok": True, "result": os.path.join(a.output, "result.json")},
                     ensure_ascii=False))
    return 0


def cmd_check(a):
    summary = _load_summary(a.work)
    report, errors = core.check_result(os.path.join(a.output, "result.json"),
                                       summary, a.work, a.output)
    print(json.dumps({"ok": errors == 0, "hard_errors": errors,
                      "warnings": len(report.get("warnings", [])),
                      "report": os.path.join(a.work, "check_report.json")},
                     ensure_ascii=False))
    return 0 if errors == 0 else 1


def cmd_selftest(a):
    selftest_path = os.path.join(HERE, "selftest.py")
    if not os.path.exists(selftest_path):
        print(json.dumps({"ok": False, "error": "selftest.py missing"}, ensure_ascii=False))
        return 1
    import subprocess
    r = subprocess.run([sys.executable, selftest_path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    return r.returncode


def cmd_pipeline(a):
    result = core.run_pipeline(a.case, a.task, a.input, a.output, a.work)
    print(json.dumps({"ok": True, "case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "result": os.path.join(a.output, "result.json")},
                     ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="Results & Discussion writing skill entry point")
    ap.add_argument("--case", default="/input/case.json", help="case.json path")
    ap.add_argument("--task", default="/input/task.md", help="task.md path")
    ap.add_argument("--input", default="/input", help="input dir")
    ap.add_argument("--output", default="/app/output", help="output dir")
    ap.add_argument("--work", default="/app/work", help="work dir")
    sub = ap.add_subparsers(dest="cmd")

    for name in ("read", "skeleton", "claim", "draft", "merge", "check",
                 "selftest", "pipeline"):
        sp = sub.add_parser(name, help=name)
        sp.set_defaults(func=globals()["cmd_" + name])
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "cmd", None):
        a.cmd = "pipeline"
        a.func = cmd_pipeline
    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
