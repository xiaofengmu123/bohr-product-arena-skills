#!/usr/bin/env python3
"""Bootstrap and maintain /app/output/result.json for the translation Skill.

Subcommands:
  init       Write a legal partial result skeleton early (first-3-minute guarantee).
  add-list   Append one item to a list field (deliverables / fact_source_map / ...).
  set        Set a top-level scalar or object field (e.g. status).
  finalize   Set final status and append a change_log entry.
  validate   Check result.json against the output contract; report, never crash.

Stdlib only. Atomic writes (tmp + os.replace). Defaults match the runtime
contract: --case /input/case.json, --out /app/output, --work /app/work.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_CONTRACT = {
    "deliverables": [],
    "fact_source_map": [],
    "version_boundary": {
        "modifications": [],
        "human_confirm": [],
        "material_gaps": [],
        "anomalies": [],
        "editable": True,
    },
}

LIST_FIELDS = {
    "deliverables", "fact_source_map", "sources", "uncertainty",
    "pending_human", "change_log", "modifications", "human_confirm",
    "material_gaps", "anomalies",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def extract_case_id(case):
    if not isinstance(case, dict):
        return "unknown"
    for key in ("case_id", "id", "caseId"):
        v = case.get(key)
        if v:
            return v
    return "unknown"


def contract_fields(case):
    """Return an ordered dict of output_contract field -> default value.

    Honours case.json.output_contract when present (list of names or dict of
    keys); otherwise falls back to the topic's three declared fields.
    Unknown keys are preserved with null and never renamed or dropped.
    """
    oc = case.get("output_contract") if isinstance(case, dict) else None
    if oc is None:
        return dict(DEFAULT_CONTRACT)
    fields = {}
    if isinstance(oc, dict):
        names = list(oc.keys())
    elif isinstance(oc, list):
        names = []
        for item in oc:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                names.append(item.get("name") or item.get("key") or item.get("field"))
        names = [n for n in names if isinstance(n, str) and n]
    else:
        names = []
    for name in names:
        if name in DEFAULT_CONTRACT:
            fields[name] = json.loads(json.dumps(DEFAULT_CONTRACT[name]))
        elif name in LIST_FIELDS:
            fields[name] = []
        else:
            fields[name] = None
    return fields


def build_skeleton(case_path):
    case = read_json(case_path, {})
    if not isinstance(case, dict):
        case = {}
    result = {"case_id": extract_case_id(case)}
    result.update(contract_fields(case))
    result["status"] = "in_progress"
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result["change_log"].append({
        "time": now_iso(), "action": "init", "key": "result.json",
        "detail": "partial result skeleton written early",
    })
    return result


def load_result(out_path):
    return read_json(out_path, None)


def cmd_init(args):
    skeleton = build_skeleton(args.case)
    out = os.path.join(args.out, "result.json")
    atomic_write_json(out, skeleton)
    print(json.dumps({"ok": True, "out": out, "case_id": skeleton["case_id"]},
                     ensure_ascii=False))
    return 0


def _append_change_log(result, action, key, detail):
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({"time": now_iso(), "action": action, "key": key, "detail": detail})


def cmd_add_list(args):
    out = os.path.join(args.out, "result.json")
    result = load_result(out)
    if result is None:
        result = build_skeleton(args.case)
    field = args.field
    try:
        item = json.loads(args.item) if args.item else None
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": "bad JSON item: %s" % e}), file=sys.stderr)
        return 2
    lst = result.setdefault(field, [])
    if not isinstance(lst, list):
        lst = []
        result[field] = lst
    lst.append(item)
    _append_change_log(result, "add-list", field, "appended 1 item")
    atomic_write_json(out, result)
    print(json.dumps({"ok": True, "field": field, "count": len(lst)}, ensure_ascii=False))
    return 0


def cmd_set(args):
    out = os.path.join(args.out, "result.json")
    result = load_result(out)
    if result is None:
        result = build_skeleton(args.case)
    value = args.value
    if args.as_json:
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": "bad JSON value: %s" % e}), file=sys.stderr)
            return 2
    result[args.field] = value
    _append_change_log(result, "set", args.field, str(value)[:200])
    atomic_write_json(out, result)
    print(json.dumps({"ok": True, "field": args.field}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    out = os.path.join(args.out, "result.json")
    result = load_result(out)
    if result is None:
        result = build_skeleton(args.case)
    status = args.status
    if status not in ("success", "partial", "failed"):
        print(json.dumps({"ok": False, "error": "status must be success|partial|failed"}),
              file=sys.stderr)
        return 2
    result["status"] = status
    _append_change_log(result, "finalize", "status", status)
    atomic_write_json(out, result)
    print(json.dumps({"ok": True, "status": status}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    out = os.path.join(args.out, "result.json")
    result = load_result(out)
    problems = []
    if result is None:
        problems.append("result.json missing or not parseable JSON")
    else:
        if not isinstance(result, dict):
            problems.append("result.json root is not an object")
        else:
            if not result.get("case_id"):
                problems.append("missing case_id")
            if "status" not in result:
                problems.append("missing status")
            required = ["deliverables", "fact_source_map", "version_boundary",
                        "sources", "uncertainty", "change_log"]
            for k in required:
                if k not in result:
                    problems.append("missing field: %s" % k)
            for d in result.get("deliverables", []):
                p = d.get("path") if isinstance(d, dict) else None
                if not p:
                    problems.append("deliverable without path")
                elif not os.path.exists(p):
                    problems.append("deliverable path does not exist: %s" % p)
    report = {"ok": not problems, "problems": problems, "out": out}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("init", help="write early partial result skeleton")

    p = sub.add_parser("add-list", help="append an item to a list field")
    p.add_argument("--field", required=True)
    p.add_argument("--item", required=True, help="JSON object")

    p = sub.add_parser("set", help="set a top-level field")
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--as-json", action="store_true")

    p = sub.add_parser("finalize", help="set final status")
    p.add_argument("--status", required=True)

    sub.add_parser("validate", help="check result.json against contract")

    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "add-list":
        return cmd_add_list(args)
    if args.cmd == "set":
        return cmd_set(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
