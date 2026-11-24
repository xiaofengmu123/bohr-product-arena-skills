#!/usr/bin/env python3
"""academic-translation-bilingual entry point.

Deterministic, stdlib-only plumbing that the agent calls while translating:
discover -> skeleton -> (agent writes deliverables/intermediates) -> finalize.

Subcommands
  discover  scan /input, write a case brief to the work directory
  skeleton  write the initial legal result.json (partial / in_progress)
  update    incrementally patch result.json (set fields / add list items)
  finalize  validate result.json and pin the final status
  selftest  run contract self-checks (delegates to selftest.py)

Defaults: --input /input, --output /app/output, --work /app/work.
Every command exits 0 even on degraded input; problems are recorded inside the
result instead of crashing, so a partial result always survives.
"""
import argparse
import json
import os
import sys

import core


def _load_result(out_dir):
    path = os.path.join(out_dir, "result.json")
    obj, note = core.safe_read_json(path)
    return obj, path, note


def cmd_discover(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    case_obj, case_note = core.read_case(args.input)
    items = core.discover_inputs(args.input)
    brief = {
        "case_id": core.case_id_of(case_obj) if isinstance(case_obj, dict) else None,
        "output_contract": core.declared_output_contract(case_obj),
        "input_files": items,
        "case_read_note": case_note,
        "generated_at": core.now_iso(),
    }
    out = os.path.join(args.work, "case_brief.json")
    core.write_json_atomic(out, brief)
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


def cmd_skeleton(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    case_obj, case_note = core.read_case(args.input)
    result, declared = core.new_result(case_obj)
    if case_note:
        result.setdefault("degradations", []).append(case_note)
    # Record sources actually discovered (read-only inventory, not fabrication).
    for it in core.discover_inputs(args.input):
        role = "task" if it["path"].endswith(("task.md", "case.json")) else "material"
        result.setdefault("sources", []).append(
            {"path": it["path"], "type": it["ext"] or "unknown", "role": role}
        )
    out = os.path.join(args.output, "result.json")
    core.write_json_atomic(out, result)
    print(json.dumps({"ok": True, "result": out, "contract_fields": declared},
                     ensure_ascii=False))
    return 0


def _resolve_json_value(args_value, args_file, fallback):
    """Resolve a JSON value from --value or --value-file (file wins if both).

    Falls back to the raw string when JSON parsing fails, so a shell that
    mangles embedded quotes can still record a degrade-to-string item rather
    than crashing.
    """
    if args_file:
        text, note = core.safe_read_text(args_file)
        if text is None:
            return None, note
        try:
            return json.loads(text), None
        except ValueError as err:
            return text, "bad-json(%s): %s" % (err, args_file)
    if args_value is not None:
        try:
            return json.loads(args_value), None
        except ValueError as err:
            return args_value, "bad-json(%s)" % err
    return fallback, None


def cmd_update(args):
    result, path, note = _load_result(args.output)
    if result is None:
        # No prior skeleton: rebuild it so update never crashes on a fresh dir.
        case_obj, _case_note = core.read_case(args.input)
        result, _declared = core.new_result(case_obj)
    value, parse_note = _resolve_json_value(args.value, args.value_file, None)
    core.append_change(result, args.action, args.key, args.detail)
    if value is not None:
        result[args.key] = value
    if parse_note:
        result.setdefault("degradations", []).append(
            {"key": args.key, "reason": parse_note})
    core.write_json_atomic(path, result)
    print(json.dumps({"ok": True, "result": path}, ensure_ascii=False))
    return 0


def cmd_add_list(args):
    result, path, note = _load_result(args.output)
    if result is None:
        case_obj, _case_note = core.read_case(args.input)
        result, _declared = core.new_result(case_obj)
    if not isinstance(result.get(args.key), list):
        result[args.key] = []
    item, parse_note = _resolve_json_value(args.item_json, args.item_file, {})
    if not isinstance(item, dict):
        item = {"_raw": str(item)}
        if parse_note:
            item["_note"] = parse_note
    result[args.key].append(item)
    core.append_change(result, "add_list", args.key,
                       (item.get("path") or item.get("item") or str(item))[:200])
    core.write_json_atomic(path, result)
    print(json.dumps({"ok": True, "result": path}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    result, path, note = _load_result(args.output)
    if result is None:
        case_obj, _case_note = core.read_case(args.input)
        result, _declared = core.new_result(case_obj)
    status = args.status or "partial"
    result["status"] = status
    # Deterministic completeness probe: which contract fields are still empty.
    missing = []
    for key in ("deliverables", "fact_source_map", "version_boundary"):
        if key in result and not result[key]:
            missing.append(key)
    result["_finalize_check"] = {
        "status": status,
        "empty_contract_fields": missing,
        "deliverable_paths_exist": _check_deliverables(result, args.output),
    }
    core.append_change(result, "finalize", "status", status)
    core.write_json_atomic(path, result)
    print(json.dumps({"ok": True, "status": status, "missing": missing},
                     ensure_ascii=False))
    return 0


def _check_deliverables(result, out_dir):
    exists = []
    for d in result.get("deliverables") or []:
        p = d.get("path") if isinstance(d, dict) else None
        if not p:
            exists.append({"path": None, "exists": False})
            continue
        if not os.path.isabs(p):
            p = os.path.join(out_dir, p)
        exists.append({"path": p, "exists": os.path.exists(p)})
    return exists


def cmd_selftest(args):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.py")
    if os.path.exists(script):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import selftest
        return selftest.run(args.input, args.output, args.work)
    print("selftest.py missing", file=sys.stderr)
    return 1


def build_parser():
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="academic-translation-bilingual deterministic plumbing")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover").set_defaults(func=cmd_discover)
    sub.add_parser("skeleton").set_defaults(func=cmd_skeleton)

    up = sub.add_parser("update")
    up.add_argument("--key", required=True)
    up.add_argument("--value", default=None)
    up.add_argument("--value-file", default=None,
                    help="read the value as JSON from a file (robust to shell quoting)")
    up.add_argument("--action", default="set")
    up.add_argument("--detail", default="")
    up.set_defaults(func=cmd_update)

    al = sub.add_parser("add-list")
    al.add_argument("--key", required=True)
    al.add_argument("--item-json", default=None)
    al.add_argument("--item-file", default=None,
                    help="read the item as JSON from a file (robust to shell quoting)")
    al.add_argument("--detail", default="")
    al.set_defaults(func=cmd_add_list)

    fin = sub.add_parser("finalize")
    fin.add_argument("--status", default=None, choices=["success", "partial", "failed"])
    fin.set_defaults(func=cmd_finalize)

    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
