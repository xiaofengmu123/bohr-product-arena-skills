#!/usr/bin/env python3
"""Entry point for the academic-translation Skill.

The agent calls this script through the official DeepSeek Harness to persist
incremental state, run deterministic checks, and finalise a contract-complete
result.json. Only the standard library is used.

Subcommands (all JSON in --item is a single quoted literal):

  brief          discover /input and print a case brief
  init           write a partial result.json skeleton (first-3-minute guard)
  add-deliverable / add-fact / add-modification / add-uncertainty /
  add-source / add-pending / add-limitation / add-material-gap
                 append one entry to the matching list and log to change_log
  check          run number/term/citation/alignment/contract checks -> report
  validate       validate the contract only (exit 1 on any problem)
  finalize       set status, validate, stamp runtime.ended_at
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402


def _require_result(args, action):
    result = core.load_result(args.out)
    if not isinstance(result, dict):
        print(json.dumps({"ok": False, "error": "no result.json; run 'init' first"},
                         ensure_ascii=False))
        sys.exit(2)
    return result


def _parse_item(raw):
    if raw is None:
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Allow a bare field:value fallback for tiny entries.
        if "=" in raw:
            key, _, val = raw.partition("=")
            return {key.strip(): val.strip()}
        return {"value": raw}
    if not isinstance(obj, dict):
        return {"value": obj}
    return obj


def _emit(args, result):
    core.save_result(args.out, result)
    ok, problems = core.validate_contract(result)
    print(json.dumps({"ok": ok, "status": result.get("status"),
                      "contract_problems": problems},
                     ensure_ascii=False))
    return 0 if ok else 1


def cmd_brief(args):
    brief = core.discover_case(args.input)
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args):
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    brief = core.discover_case(args.input)
    case_id = args.case_id or brief.get("case_id") or "unknown"
    result = core.new_result(case_id, status="in_progress")
    # Persist the case brief so later steps can re-read without re-scanning.
    core.atomic_write_json(os.path.join(args.work, "case_brief.json"), brief)
    core.save_result(args.out, result)
    ok, problems = core.validate_contract(result)
    print(json.dumps({"ok": ok, "case_id": case_id, "status": "in_progress",
                      "contract_problems": problems},
                     ensure_ascii=False))
    return 0 if ok else 1


def cmd_add(args):
    result = _require_result(args, "add")
    item = _parse_item(args.item)
    key = args.list_key
    if key not in ("deliverables", "fact_source_map", "sources", "uncertainty",
                   "pending_human", "limitations"):
        print(json.dumps({"ok": False, "error": "unsupported list: %s" % key},
                         ensure_ascii=False))
        return 2
    # Deliverable path resolution: keep as given; basename must land in /app/output.
    if key == "deliverables":
        p = item.get("path", "")
        if p and not os.path.isabs(p):
            item["path"] = os.path.join(args.out, os.path.basename(p))
    result.setdefault(key, []).append(item)
    core.append_change_log(result, "add", key,
                           "%s" % (item.get("item") or item.get("path") or item.get("title") or "-"))
    return _emit(args, result)


def cmd_add_fact(args):
    result = _require_result(args, "add-fact")
    item = _parse_item(args.item)
    item.setdefault("kind", "claim")
    if item.get("kind") not in core.FACT_KINDS:
        print(json.dumps({"ok": False, "error": "unknown kind: %r" % item.get("kind")},
                         ensure_ascii=False))
        return 2
    if "source" not in item and "locator" not in item:
        item["source"] = args.source or ""
    if args.quote:
        item["quote"] = args.quote
    if args.locator:
        item["locator"] = args.locator
    result.setdefault("fact_source_map", []).append(item)
    core.append_change_log(result, "add-fact", item.get("kind", "claim"),
                           "%s" % (item.get("item") or item.get("value") or "-"))
    return _emit(args, result)


def cmd_add_modification(args):
    result = _require_result(args, "add-modification")
    item = _parse_item(args.item)
    item.setdefault("what", ""); item.setdefault("why", ""); item.setdefault("before_after", {})
    result.setdefault("version_boundary", {}).setdefault("modifications", []).append(item)
    core.append_change_log(result, "add-modification", item.get("what", "-"),
                           "why=%s" % item.get("why", "-"))
    return _emit(args, result)


def cmd_add_pending(args):
    result = _require_result(args, "add-pending")
    item = _parse_item(args.item)
    result.setdefault("pending_human", []).append(item)
    result.setdefault("version_boundary", {}).setdefault("human_confirm", []).append(item)
    core.append_change_log(result, "add-pending", item.get("item", "-"), item.get("reason", "-"))
    return _emit(args, result)


def cmd_add_material_gap(args):
    result = _require_result(args, "add-material-gap")
    item = _parse_item(args.item)
    result.setdefault("version_boundary", {}).setdefault("material_gaps", []).append(item)
    result.setdefault("limitations", []).append(item)
    core.append_change_log(result, "add-material-gap", item.get("item", "-"), item.get("reason", "-"))
    return _emit(args, result)


def cmd_check(args):
    os.makedirs(args.work, exist_ok=True)
    report = core.run_full_checks(
        args.out, args.work,
        source_file=args.source_file,
        translation_file=args.translation_file,
        glossary_file=args.glossary,
        alignment_file=args.alignment,
    )
    report_path = os.path.join(args.work, args.report_name)
    core.atomic_write_json(report_path, report)
    result = core.load_result(args.out) or {}
    core.append_change_log(result, "check", report_path,
                           "errors=%d warnings=%d" % (report["n_errors"], report["n_warnings"]))
    core.save_result(args.out, result)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["n_errors"] == 0 else 1


def cmd_validate(args):
    result = core.load_result(args.out) or {}
    ok, problems = core.validate_contract(result)
    print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_finalize(args):
    result = _require_result(args, "finalize")
    status = args.status
    if status not in core.VALID_STATUS:
        print(json.dumps({"ok": False, "error": "invalid status: %r" % status},
                         ensure_ascii=False))
        return 2
    result["status"] = status
    rt = result.setdefault("runtime", {})
    rt["ended_at"] = core.utc_now()
    core.append_change_log(result, "finalize", "status", status)
    # Sync pending_human into version_boundary.human_confirm exactly once.
    merged = list(result["version_boundary"].get("human_confirm", []))
    for item in result.get("pending_human", []):
        if item not in merged:
            merged.append(item)
    result["version_boundary"]["human_confirm"] = merged
    return _emit(args, result)


def _add_common(p):
    # SUPPRESS keeps the value parsed by the top-level parser; the same flag
    # may therefore appear before or after the subcommand without a default
    # overwriting an earlier value.
    p.add_argument("--input", default=argparse.SUPPRESS)
    p.add_argument("--output", "--out", dest="out", default=argparse.SUPPRESS)
    p.add_argument("--work", default=argparse.SUPPRESS)


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Academic translation Skill entry point")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", "--out", dest="out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("brief"); _add_common(p); p.set_defaults(func=cmd_brief)

    p = sub.add_parser("init"); _add_common(p)
    p.add_argument("--case-id", default=None)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add"); _add_common(p)
    p.add_argument("--list", dest="list_key", required=True)
    p.add_argument("--item", required=True)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("add-fact"); _add_common(p)
    p.add_argument("--item", required=True)
    p.add_argument("--source", default=None)
    p.add_argument("--locator", default=None)
    p.add_argument("--quote", default=None)
    p.set_defaults(func=cmd_add_fact)

    p = sub.add_parser("add-modification"); _add_common(p)
    p.add_argument("--item", required=True)
    p.set_defaults(func=cmd_add_modification)

    p = sub.add_parser("add-pending"); _add_common(p)
    p.add_argument("--item", required=True)
    p.set_defaults(func=cmd_add_pending)

    p = sub.add_parser("add-material-gap"); _add_common(p)
    p.add_argument("--item", required=True)
    p.set_defaults(func=cmd_add_material_gap)

    p = sub.add_parser("check"); _add_common(p)
    p.add_argument("--source-file", default=None)
    p.add_argument("--translation-file", default=None)
    p.add_argument("--glossary", default=None)
    p.add_argument("--alignment", default=None)
    p.add_argument("--report-name", default="check_report.json")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("validate"); _add_common(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("finalize"); _add_common(p)
    p.add_argument("--status", default="partial")
    p.set_defaults(func=cmd_finalize)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
