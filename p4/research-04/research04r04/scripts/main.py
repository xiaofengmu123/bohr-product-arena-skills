# -*- coding: utf-8 -*-
"""main.py — entry wrapper for the research-frontier-trend Skill.

Usage (relative to the skill root):
    python3 scripts/main.py prepare   [--input /input] [--work /app/work] [--output /app/output]
    python3 scripts/main.py analyze   [--input /input] [--work /app/work] [--output /app/output]
    python3 scripts/main.py selfcheck [--work /app/work] [--output /app/output]
    python3 scripts/main.py run       [--input /input] [--work /app/work] [--output /app/output]

Defaults match the runtime contract: /input, /app/work, /app/output.
Only the Python standard library is used.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def _now():
    return core_impl.now_utc()


def cmd_prepare(args):
    t0 = _now()
    skeleton, manifest = core_impl.prepare(args.input, args.work, args.output,
                                           started_at=t0)
    print("prepare ok: case_id={0} docs={1} status={2}".format(
        skeleton.get("case_id"), len(manifest.get("docs", [])),
        skeleton.get("status")))
    return 0


def cmd_analyze(args):
    result = core_impl.analyze(args.work, args.output)
    print("analyze ok: status={0} evidence={1} uncertainty={2}".format(
        result.get("status"), len(result.get("evidence_map", [])),
        len(result.get("uncertainty", []))))
    return 0


def cmd_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    result, note = core_impl.load_json(result_path)
    if result is None:
        print("selfcheck: result.json unreadable ({0})".format(note))
        core_impl.write_json(os.path.join(args.work, "selfcheck.json"),
                             {"ok": False, "findings": [{
                                 "level": "error", "check": "result_json",
                                 "ok": False, "message": note}]})
        return 1
    findings = core_impl.check_contract(
        result, materials_dir=os.path.join(args.work, "materials"))
    errors = [f for f in findings if f["level"] == "error" and not f["ok"]]
    warns = [f for f in findings if f["level"] == "warn" and not f["ok"]]
    core_impl.write_json(os.path.join(args.work, "selfcheck.json"),
                         {"ok": len(errors) == 0, "errors": errors,
                          "warnings": warns, "findings": findings})
    print("selfcheck: errors={0} warnings={1}".format(len(errors), len(warns)))
    for f in errors + warns:
        print("  [{0}] {1}: {2}".format(f["level"], f["check"], f["message"]))
    return 0 if not errors else 1


def cmd_run(args):
    rc = cmd_prepare(args)
    if rc != 0:
        return rc
    rc = cmd_analyze(args)
    if rc != 0:
        return rc
    return cmd_selfcheck(args)


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="research-frontier-trend skill: prepare / analyze / selfcheck")
    p.add_argument("command", choices=["prepare", "analyze", "selfcheck", "run"])
    p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {"prepare": cmd_prepare, "analyze": cmd_analyze,
            "selfcheck": cmd_selfcheck, "run": cmd_run}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
