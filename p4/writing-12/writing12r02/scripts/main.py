#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- thin CLI for the "latex-writing-compile-budgeted" Skill.

All logic lives in core_impl.py; this module only exposes the command-line
interface the agent drives during a run. Exit codes:

  * bootstrap / discover / checkpoint / finalize / plan / index / quote /
    classify-log return 0 even on graceful degradation (an empty input is a
    valid, honest result, not a crash);
  * selfcheck returns 1 when the on-disk result.json violates the contract, so
    the caller can decide whether to keep improving.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    C.reset_state(args.work, args.input)
    skeleton, keys, case_id, notes = C.build_skeleton(args.input)
    C.write_json_atomic(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "case_id": case_id,
        "contract_keys": keys,
        "files": C.discover_input(args.input),
        "generated_at": C.utcnow(),
    }
    C.write_json_atomic(os.path.join(args.work, "inventory.json"), inventory)
    gov = C.BudgetGovernor(C.load_state(args.work))
    _print({
        "case_id": case_id,
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(args.output, "result.json"),
        "early_flush_s": gov.early_flush,
        "note": "skeleton flushed at bootstrap (t~0), inside the mandatory early window",
    })
    return 0


def cmd_discover(args):
    files = C.discover_input(args.input)
    _print({"input_root": args.input, "files": files})
    return 0


def cmd_checkpoint(args):
    work = args.work
    if args.action == "begin":
        state, report = C.phase_begin(work, args.phase, args.requests)
        _print({"action": "begin", "phase": args.phase, "budget": report})
    elif args.action == "commit":
        state, report = C.phase_commit(work, args.phase, args.requests, args.note or "")
        _print({"action": "commit", "phase": args.phase, "budget": report})
    elif args.action == "report":
        state = C.load_state(work)
        if state is None:
            state = C.reset_state(work, "/input")
        _print({"action": "report", "budget": C.BudgetGovernor(state).report()})
    return 0


def cmd_plan(args):
    state = C.load_state(args.work) or C.reset_state(args.work, args.input)
    _print(C.BudgetGovernor(state).report())
    return 0


def cmd_finalize(args):
    rp = os.path.join(args.output, "result.json")
    result, errs = C.read_json(rp)
    if not isinstance(result, dict):
        result = {"case_id": "unknown", "errors": ["result.json missing or bad at finalize"]}
    inv, _ = C.read_json(os.path.join(args.work, "inventory.json"))
    keys = []
    if isinstance(inv, dict):
        keys = inv.get("contract_keys") or []
    # Never overwrite a status the agent explicitly decided; otherwise downgrade.
    if args.status and args.status in C.ALLOWED_STATUS:
        result["status"] = args.status
    elif result.get("status") not in C.ALLOWED_STATUS:
        result["status"] = "partial" if keys else "input_error"
    for k in keys:
        result.setdefault(k, None)
    rt = result.setdefault("runtime", {})
    rt["finished_at"] = C.utcnow()
    rt["producer"] = "main.py"
    if args.engine:
        rt["engine"] = args.engine
    if args.passes is not None:
        rt["compile_passes"] = args.passes
    result.setdefault("artifacts", [])
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("change_log", [])
    result["change_log"].append({
        "time": C.utcnow(), "step": "finalize", "note": "final validation run",
    })
    C.write_json_atomic(rp, result)
    _print({"status": result["status"], "result_path": rp,
            "contract_keys": keys})
    return 0


def cmd_selfcheck(args):
    rp = os.path.join(args.output, "result.json")
    problems = []
    result, errs = C.read_json(rp)
    if not isinstance(result, dict):
        problems.append("result.json is not a JSON object")
        _print({"ok": False, "problems": problems})
        return 1
    if not result.get("case_id"):
        problems.append("missing case_id")
    if result.get("status") not in C.ALLOWED_STATUS:
        problems.append("status not in %s" % sorted(C.ALLOWED_STATUS))
    for field in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(field), list):
            problems.append("%s must be a list" % field)
    inv, _ = C.read_json(os.path.join(args.work, "inventory.json"))
    keys = []
    if isinstance(inv, dict):
        keys = inv.get("contract_keys") or []
    missing = [k for k in keys if k not in result]
    if missing:
        problems.append("missing contract keys: %s" % missing)
    _print({"ok": not problems, "problems": problems,
            "status": result.get("status"), "contract_keys": keys})
    return 1 if problems else 0


def cmd_index(args):
    out = args.out or os.path.join(args.work, "source_index.json")
    idx = C.index_sources(args.input, out)
    _print({"source_index": out, "file_count": len(idx["files"])})
    return 0


def cmd_quote(args):
    q = C.quote(args.file, args.start, args.end)
    _print(q)
    return 0


def cmd_classify_log(args):
    text, err = C.read_text(args.log)
    if text is None:
        _print({"error": err})
        return 0
    _print(C.classify_log(text, args.main))
    return 0


def _common_suppress():
    """Parent parser so --input/--output/--work may appear after a subcommand."""
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--input", default=argparse.SUPPRESS)
    c.add_argument("--output", default=argparse.SUPPRESS)
    c.add_argument("--work", default=argparse.SUPPRESS)
    return c


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="latex-writing-compile-budgeted Skill driver")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    sub = p.add_subparsers(dest="command")

    b = sub.add_parser("bootstrap", help="flush result.json skeleton + state + inventory",
                       parents=[_common_suppress()])
    b.set_defaults(func=cmd_bootstrap)

    d = sub.add_parser("discover", help="list input files", parents=[_common_suppress()])
    d.set_defaults(func=cmd_discover)

    cp = sub.add_parser("checkpoint", help="phase ledger begin/commit/report",
                        parents=[_common_suppress()])
    cp.add_argument("action", choices=["begin", "commit", "report"])
    cp.add_argument("--phase", default="")
    cp.add_argument("--requests", type=int, default=0)
    cp.add_argument("--note", default="")
    cp.set_defaults(func=cmd_checkpoint)

    pl = sub.add_parser("plan", help="print budget plan and remaining resources",
                        parents=[_common_suppress()])
    pl.set_defaults(func=cmd_plan)

    f = sub.add_parser("finalize", help="validate and stamp result.json",
                       parents=[_common_suppress()])
    f.add_argument("--status", choices=sorted(C.ALLOWED_STATUS))
    f.add_argument("--engine", default=None)
    f.add_argument("--passes", type=int, default=None)
    f.set_defaults(func=cmd_finalize)

    sc = sub.add_parser("selfcheck", help="verify result.json contract (exit 1 on problems)",
                        parents=[_common_suppress()])
    sc.set_defaults(func=cmd_selfcheck)

    ix = sub.add_parser("index", help="build source index", parents=[_common_suppress()])
    ix.add_argument("--out", default=None)
    ix.set_defaults(func=cmd_index)

    q = sub.add_parser("quote", help="extract verbatim evidence lines", parents=[_common_suppress()])
    q.add_argument("--file", required=True)
    q.add_argument("--start", type=int, default=None)
    q.add_argument("--end", type=int, default=None)
    q.set_defaults(func=cmd_quote)

    cl = sub.add_parser("classify-log", help="classify a TeX .log", parents=[_common_suppress()])
    cl.add_argument("--log", required=True)
    cl.add_argument("--main", default=None)
    cl.set_defaults(func=cmd_classify_log)

    return p


def main(argv=None):
    C.reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
