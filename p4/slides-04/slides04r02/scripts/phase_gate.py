#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""phase_gate.py -- maintain and check the 900s / 64-request phase budget ledger.

Commands (all stdlib-only):
  --begin ID        mark a phase active (records started_epoch)
  --end ID          close a phase (records actual_s from started_epoch)
  --requests N      add N to total request count (optionally --phase ID)
  --status [--json] print the budget table and slack
  --check           verify hard gates; exit 0 pass / 1 warnings / 2 violations

Ledger lives at <work>/phase_ledger.json (created by init_result.py).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from init_result import (  # noqa: E402
    DEFAULT_PHASES, REQUEST_BUDGET, RESERVE_REQ, SKELETON_DEADLINE_S,
    BUDGET_S, load_json, write_json_atomic, now_iso,
)

LEDGER_NAME = "phase_ledger.json"


def ledger_path(work):
    return Path(work) / LEDGER_NAME


def new_ledger():
    phases = []
    for p in DEFAULT_PHASES:
        phases.append(dict(p, started_at=None, started_epoch=None, ended_at=None,
                           ended_epoch=None, actual_s=None, status="planned", req_used=0))
    return {"schema": "phase-ledger-v1", "started_at": None, "started_epoch": None,
            "skeleton_written_at": None, "skeleton_written_epoch": None,
            "skeleton_deadline_s": SKELETON_DEADLINE_S, "total_budget_s": BUDGET_S,
            "total_req_budget": REQUEST_BUDGET, "req_reserve": RESERVE_REQ,
            "requests_used_total": 0, "phases": phases, "events": []}


def load_ledger(work):
    obj, _ = load_json(ledger_path(work))
    return obj if isinstance(obj, dict) else new_ledger()


def find_phase(ledger, pid):
    for p in ledger.get("phases", []):
        if p.get("id") == pid:
            return p
    return None


def event(ledger, kind, detail, now):
    ledger.setdefault("events", []).append({"ts": now_iso(now), "kind": kind, "detail": detail})


def cmd_begin(ledger, pid, now):
    p = find_phase(ledger, pid)
    if p is None:
        return 1, "unknown phase %s" % pid
    p["status"] = "active"
    p["started_at"] = now_iso(now)
    p["started_epoch"] = now
    event(ledger, "begin", pid, now)
    return 0, "begun %s" % pid


def cmd_end(ledger, pid, now):
    p = find_phase(ledger, pid)
    if p is None:
        return 1, "unknown phase %s" % pid
    if not p.get("started_epoch"):
        p["started_epoch"] = now
    p["ended_at"] = now_iso(now)
    p["ended_epoch"] = now
    p["actual_s"] = round(now - p["started_epoch"], 1)
    p["status"] = "done"
    event(ledger, "end", "%s actual_s=%s" % (pid, p["actual_s"]), now)
    return 0, "ended %s actual_s=%s" % (pid, p["actual_s"])


def cmd_requests(ledger, n, pid, now):
    ledger["requests_used_total"] = ledger.get("requests_used_total", 0) + n
    if pid:
        p = find_phase(ledger, pid)
        if p is None:
            return 1, "unknown phase %s" % pid
        p["req_used"] = p.get("req_used", 0) + n
    event(ledger, "requests", "+%s total=%s" % (n, ledger["requests_used_total"]), now)
    return 0, "requests_used_total=%s" % ledger["requests_used_total"]


def check(ledger, now):
    problems, warnings = [], []
    if not ledger.get("started_epoch"):
        problems.append("ledger has no started_epoch")
        return problems, warnings
    sk_epoch = ledger.get("skeleton_written_epoch")
    deadline = ledger["started_epoch"] + SKELETON_DEADLINE_S
    if not sk_epoch:
        problems.append("skeleton_written_epoch missing")
    elif sk_epoch > deadline:
        problems.append("skeleton written %.1fs after 180s deadline" % (sk_epoch - deadline))
    if now - ledger["started_epoch"] > BUDGET_S:
        problems.append("elapsed %.0fs > %ds" % (now - ledger["started_epoch"], BUDGET_S))
    if ledger.get("requests_used_total", 0) > REQUEST_BUDGET:
        problems.append("requests %s > %s" % (ledger["requests_used_total"], REQUEST_BUDGET))
    for p in ledger.get("phases", []):
        if p.get("status") == "done" and p.get("actual_s") is not None \
                and p["actual_s"] > p["deadline_s"]:
            warnings.append("%s overran deadline by %.0fs" % (p["id"], p["actual_s"] - p["deadline_s"]))
        if p.get("req_used", 0) > p.get("req_budget", 0):
            warnings.append("%s used %s requests > budget %s" % (p["id"], p["req_used"], p["req_budget"]))
    return problems, warnings


def status_text(ledger, now):
    elapsed = (now - ledger["started_epoch"]) if ledger.get("started_epoch") else 0
    lines = ["elapsed %.0fs / %ss; requests %s / %s (reserve %s)" % (
        elapsed, ledger.get("total_budget_s", BUDGET_S),
        ledger.get("requests_used_total", 0), ledger.get("total_req_budget", REQUEST_BUDGET),
        ledger.get("req_reserve", RESERVE_REQ))]
    for p in ledger.get("phases", []):
        s = p.get("status", "planned")
        mark = {"done": "D", "active": ">", "planned": "-"}.get(s, "?")
        actual = "%ss" % int(p["actual_s"]) if p.get("actual_s") is not None else "-"
        req = "%s/%s" % (p.get("req_used", 0), p.get("req_budget", 0))
        lines.append("  [%s] %-13s dl=%3ss actual=%5s req=%s" % (mark, p["id"], p["deadline_s"], actual, req))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--begin", metavar="ID")
    ap.add_argument("--end", metavar="ID")
    ap.add_argument("--requests", type=int, metavar="N")
    ap.add_argument("--phase", metavar="ID", help="attribute --requests to this phase")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit --status as JSON")
    args = ap.parse_args(argv)

    now = time.time()
    ledger = load_ledger(args.work)
    ledger.setdefault("started_epoch", now)
    ledger.setdefault("started_at", now_iso(now))

    code, msg = 0, ""
    if args.begin:
        code, msg = cmd_begin(ledger, args.begin, now)
    if args.end:
        c, m = cmd_end(ledger, args.end, now)
        code = code or c
        msg = "%s; %s" % (msg, m) if msg else m
    if args.requests is not None:
        c, m = cmd_requests(ledger, args.requests, args.phase, now)
        code = code or c
        msg = "%s; %s" % (msg, m) if msg else m

    try:
        write_json_atomic(ledger_path(args.work), ledger)
    except OSError as exc:
        print("FATAL: cannot write ledger: %s" % exc, file=sys.stderr)
        return 3

    if msg:
        print(msg)
    if args.status:
        if args.json:
            print(json.dumps(ledger, ensure_ascii=False, indent=2))
        else:
            print(status_text(ledger, now))
    if args.check:
        problems, warnings = check(ledger, now)
        for w in warnings:
            print("WARN: %s" % w)
        for p in problems:
            print("FAIL: %s" % p)
        if problems:
            return 2
        if warnings:
            return 1
        print("GATES: pass")
        return 0
    return code


if __name__ == "__main__":
    sys.exit(main())
