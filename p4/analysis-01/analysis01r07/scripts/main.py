#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the sci-data-cleaning-robust skill.

Usage (all standard-library, deterministic, exit code 0 even on bad input):
    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work

Guarantees:
  * bootstrap writes a valid result.json skeleton within seconds (partial),
    so any later failure still leaves a loadable, honest partial deliverable.
  * full discovers materials, profiles and conservatively cleans the main
    tabular source, writes cleaned_data.csv + raw_snapshot.csv +
    data_profile.json + quality_report.md + evidence_map.json, then updates
    result.json in place.
  * selfcheck validates result.json and artifacts without inventing scores.
  * every phase runs inside a last-resort guard: any exception is captured,
    a minimal valid result.json is written if possible, and the process exits
    with code 0 (graceful degradation, never a crash).
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

RESULT_FIELDS = ("case_id", "status", "sources", "uncertainty", "change_log",
                 "errors", "degradations", "pending_human", "runtime",
                 "artifacts")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _resolve(args):
    """Resolve path defaults and ensure output/work directories exist."""
    inp = args.input or DEFAULT_INPUT
    out = args.output or DEFAULT_OUTPUT
    work = args.work or DEFAULT_WORK
    try:
        os.makedirs(out, exist_ok=True)
    except OSError:
        pass
    try:
        os.makedirs(work, exist_ok=True)
    except OSError:
        pass
    return inp, out, work


def _minimal_result(case_id, reason):
    return {
        "case_id": case_id or "unknown",
        "status": "input_error",
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"time": now_iso(), "step": "guard",
             "action": "last-resort fallback: %s" % reason},
        ],
        "errors": [{"step": "guard", "detail": reason}],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started": now_iso(), "phases": ["guard"]},
        "artifacts": {},
    }


def _write_guard_result(out, reason):
    """Write a minimal valid result.json if the normal path failed."""
    try:
        core.write_json_atomic(os.path.join(out, "result.json"),
                               _minimal_result("unknown", reason))
    except Exception:
        try:
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "result.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(_minimal_result("unknown", reason), fh,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass


def cmd_bootstrap(inp, out, work, max_bytes):
    started = now_iso()
    case = core.load_case(inp, max_bytes)
    result = core.build_skeleton(case, started, "partial")
    result["runtime"]["phases"].append("bootstrap")
    result["artifacts"] = {"result_json": os.path.join(out, "result.json")}
    core.write_json_atomic(os.path.join(out, "result.json"), result)
    return result


def cmd_full(inp, out, work, max_bytes):
    case = core.load_case(inp, max_bytes)
    result = core.run_full(case, inp, out, work, max_bytes)
    # Ensure result.json always reflects the latest state.
    result.setdefault("artifacts", {})
    result["artifacts"]["result_json"] = os.path.join(out, "result.json")
    result["runtime"]["phases"].insert(0, "bootstrap")
    core.write_json_atomic(os.path.join(out, "result.json"), result)
    return result


def cmd_selfcheck(inp, out, work, max_bytes):
    checks = []
    result_path = os.path.join(out, "result.json")

    def add(name, passed, detail=""):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    if not os.path.isfile(result_path):
        add("result.json exists", False, result_path)
        report = {"overall": "fail", "checks": checks}
        core.write_json_atomic(os.path.join(out, "selfcheck.json"), report)
        return report

    info = core.read_text(result_path, max_bytes)
    value, status = core.parse_json(info["text"], {})
    if not isinstance(value, dict):
        value = {}

    add("result.json parses", status in ("ok", "degraded"), status)
    add("case_id present", bool(value.get("case_id")), str(value.get("case_id")))
    add("status valid", value.get("status") in STATUS_VALUES,
        str(value.get("status")))
    add("sources is list", isinstance(value.get("sources"), list))
    add("uncertainty is list", isinstance(value.get("uncertainty"), list))
    add("change_log is list", isinstance(value.get("change_log"), list))
    add("errors is list", isinstance(value.get("errors"), list))
    add("pending_human is list", isinstance(value.get("pending_human"), list))
    add("degradations is list", isinstance(value.get("degradations"), list))

    # Contract keys: every key present in case.json's output_contract must be
    # present in result.json (filled with null/[] when unavailable).
    case = core.load_case(inp, max_bytes)
    missing_keys = [k for k in case.get("output_contract", {})
                    if k not in value]
    add("output_contract keys all present",
        not missing_keys,
        "missing: %s" % missing_keys if missing_keys else "all present")

    # Artifact existence.
    artifacts = value.get("artifacts", {})
    if isinstance(artifacts, dict):
        present = []
        missing = []
        for k, p in artifacts.items():
            if k == "result_json":
                continue
            if isinstance(p, str) and os.path.isfile(p):
                present.append(k)
            else:
                missing.append(k)
        add("artifacts exist", not missing,
            "missing: %s" % missing if missing else "ok")
    else:
        add("artifacts is object", False, type(artifacts).__name__)

    # Key-content consistency spot checks.
    if isinstance(value.get("sources"), list):
        bad = [i for i, s in enumerate(value["sources"])
               if not isinstance(s, dict) or not s.get("locator")
               or not s.get("quote")]
        add("sources have locator+quote", not bad,
            "bad entries: %s" % bad if bad else "ok")
    if isinstance(value.get("change_log"), list):
        bad = [i for i, c in enumerate(value["change_log"])
               if not isinstance(c, dict) or not c.get("step")
               or not c.get("action")]
        add("change_log entries have step+action", not bad,
            "bad entries: %s" % bad if bad else "ok")

    overall = "pass" if all(c["passed"] for c in checks) else "fail"
    report = {"overall": overall, "checks": checks,
              "result_path": result_path}
    core.write_json_atomic(os.path.join(out, "selfcheck.json"), report)
    for c in checks:
        mark = "PASS" if c["passed"] else "FAIL"
        print("%s  %s  %s" % (mark, c["check"], c["detail"]))
    print("SELFCHECK %s" % overall.upper())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="sci-data-cleaning-robust CLI (deterministic, exit 0)")
    parser.add_argument("--phase", required=True,
                        choices=["bootstrap", "full", "selfcheck"])
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--work", default=None)
    parser.add_argument("--max-bytes", type=int,
                        default=core.MAX_TEXT_BYTES)
    args, unknown = parser.parse_known_args(argv)
    inp, out, work = _resolve(args)
    try:
        if args.phase == "bootstrap":
            cmd_bootstrap(inp, out, work, args.max_bytes)
        elif args.phase == "full":
            cmd_full(inp, out, work, args.max_bytes)
        elif args.phase == "selfcheck":
            cmd_selfcheck(inp, out, work, args.max_bytes)
        return 0
    except BaseException as exc:  # last-resort: never crash, always exit 0
        reason = "%s: %s" % (type(exc).__name__, exc)
        try:
            _write_guard_result(out, reason)
        except Exception:
            pass
        print("DEGRADED %s" % reason, file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
