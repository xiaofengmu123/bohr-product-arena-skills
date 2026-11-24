#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI entry point for the mechanism / pathway figure Skill.

Subcommands (all standard library, all deterministic):
  validate-spec SPEC [--out NORM_OUT] [--report REPORT]
  build-svg     SPEC OUT
  check-result  RESULT --output-dir DIR
  skeleton      --case CASE [--task TASK] --out RESULT
  run           --case CASE [--task TASK] --spec SPEC --out DIR [--work DIR]

Exit codes: 0 = ok, 2 = warnings only, 1 = blocking errors / bad usage.
Paths inside SKILL.md are relative to the Skill root (/skills/weekly-skill).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402


def _emit(obj):
    sys.stdout.write(C.canonical_dumps(obj) + "\n")


def _exit(errors, warnings):
    if errors:
        return 1
    if warnings:
        return 2
    return 0


def cmd_validate(a):
    try:
        spec = C.load_json(a.spec)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    norm, errors, warnings = C.normalize_spec(spec)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(C.canonical_dumps(norm))
    report = {"ok": not errors, "errors": errors, "warnings": warnings, "schema_version": C.SCHEMA_VERSION}
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            fh.write(C.canonical_dumps(report))
    _emit(report)
    return _exit(errors, warnings)


def cmd_build(a):
    try:
        spec = C.load_json(a.spec)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    svg = C.build_svg(spec)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    _emit({"ok": True, "svg": a.out, "sha256": C.sha256_hex(svg), "bytes": len(svg.encode("utf-8"))})
    return 0


def cmd_check(a):
    try:
        result = C.load_json(a.result)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    errors, warnings = C.check_result(result, a.output_dir)
    _emit({"ok": not errors, "errors": errors, "warnings": warnings})
    return _exit(errors, warnings)


def cmd_skeleton(a):
    case_id = C.detect_case_id(a.case, a.task)
    result = C.skeleton_result(case_id)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(C.canonical_dumps(result))
    _emit({"ok": True, "result": a.out, "case_id": case_id, "status": result["status"]})
    return 0


def cmd_run(a):
    case_id = C.detect_case_id(a.case, a.task)
    try:
        spec = C.load_json(a.spec)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc), "case_id": case_id})
        return 1
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    norm, errors, warnings = C.normalize_spec(spec)
    svg = C._build_svg(norm)
    svg_path = os.path.join(a.out, "mechanism.svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    norm_path = os.path.join(a.work, "spec.norm.json")
    with open(norm_path, "w", encoding="utf-8") as fh:
        fh.write(C.canonical_dumps(norm))
    status = "failed" if errors else "partial"
    result, rerr, rwarn = C.build_result(case_id, spec, status=status)
    result_path = os.path.join(a.out, "result.json")
    with open(result_path, "w", encoding="utf-8") as fh:
        fh.write(C.canonical_dumps(result))
    cerr, cwarn = C.check_result(result, a.out)
    _emit({
        "ok": not errors and not cerr,
        "case_id": case_id,
        "status": status,
        "svg": svg_path,
        "result": result_path,
        "spec_errors": errors,
        "spec_warnings": warnings,
        "result_errors": cerr,
        "result_warnings": cwarn,
    })
    # `run` is a mid-pipeline partial producer; warnings are reported in JSON
    # but do not escalate the exit code (final gate is `check-result`).
    return _exit(errors + cerr, [])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="Mechanism/pathway figure Skill CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate-spec", help="validate/normalize spec.json")
    p.add_argument("spec")
    p.add_argument("--out")
    p.add_argument("--report")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("build-svg", help="render deterministic SVG from spec.json")
    p.add_argument("spec")
    p.add_argument("out")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("check-result", help="validate result.json contract and file existence")
    p.add_argument("result")
    p.add_argument("--output-dir", default="/app/output")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("skeleton", help="write minimal partial result.json (pre-extraction)")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--out", default="/app/output/result.json")
    p.set_defaults(fn=cmd_skeleton)

    p = sub.add_parser("run", help="full deterministic pipeline: spec -> SVG -> result.json")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.set_defaults(fn=cmd_run)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
