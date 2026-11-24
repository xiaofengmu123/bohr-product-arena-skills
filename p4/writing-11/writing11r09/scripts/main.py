#!/usr/bin/env python3
"""result.json lifecycle for academic-translation-bilingual.

Flags (mutually usable):
  --init            read case.json, write skeleton result.json + case_brief.json
  --set KEY VAL     set a top-level field (VAL parsed as JSON, fallback string)
  --add-list KEY VAL append a JSON value to a list field (creates [] if absent)
  --log ACTION KEY DETAIL  append a change_log entry
  --finalize        set status and ensure required top-level fields exist

Standard library only. Self-locates via __file__, so it runs from any cwd.
"""
import argparse
import datetime
import json
import os
import sys

DEFAULT_CONTRACT = ["deliverables", "fact_source_map", "version_boundary"]

EMPTY_VALUE = {
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

REQUIRED_KEYS = ["case_id", "status", "sources", "uncertainty", "change_log"]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json_safe(path):
    """Return (obj, err). Tolerates BOM, bad encoding, bad JSON, missing file."""
    last = None
    if not os.path.exists(path):
        return None, "missing file"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f), None
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            last = str(e)
        except OSError as e:
            return None, str(e)
    return None, last


def atomic_write(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "caseId", "id"):
        v = case.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def extract_output_contract(case):
    if not isinstance(case, dict):
        return list(DEFAULT_CONTRACT)
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        keys = list(oc.keys())
        return keys if keys else list(DEFAULT_CONTRACT)
    if isinstance(oc, list):
        return [str(x) for x in oc] or list(DEFAULT_CONTRACT)
    return list(DEFAULT_CONTRACT)


def guess_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".docx", ".doc", ".pptx", ".xlsx"):
        return "office"
    if ext in (".md", ".txt", ".tex", ".json", ".csv", ".yaml", ".yml"):
        return "text"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        return "image"
    return "other"


def inventory(base):
    items = []
    if not os.path.isdir(base):
        return items
    for root, _dirs, files in os.walk(base):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            items.append({"path": p, "type": guess_type(fn), "size": size})
    return items


def cmd_init(args):
    case, err = read_json_safe(args.case)
    if case is None or not isinstance(case, dict):
        case = {}
    case_id = extract_case_id(case)
    contract = extract_output_contract(case)

    skeleton = {"case_id": case_id, "status": "in_progress"}
    for key in contract:
        skeleton[key] = EMPTY_VALUE.get(key, None)
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["change_log"] = [
        {"time": now_iso(), "action": "init", "key": "result", "detail": "skeleton"}
    ]
    atomic_write(args.out, skeleton)

    base = os.path.dirname(os.path.abspath(args.case))
    brief = {
        "case_id": case_id,
        "case_path": os.path.abspath(args.case),
        "output_contract": contract,
        "materials": inventory(base),
        "missing": [] if case_id else ["case_id"],
        "note": err if err else None,
    }
    atomic_write(os.path.join(args.work, "case_brief.json"), brief)
    print(json.dumps({"ok": True, "case_id": case_id, "output_contract": contract},
                     ensure_ascii=False))


def _parse_value(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _load_or_empty(args):
    data, _ = read_json_safe(args.out)
    if not isinstance(data, dict):
        data = {}
    return data


def cmd_set(args):
    data = _load_or_empty(args)
    data[args.key] = _parse_value(args.value)
    _log(data, "set", args.key, args.value)
    atomic_write(args.out, data)
    print(json.dumps({"ok": True, "set": args.key}, ensure_ascii=False))


def cmd_add_list(args):
    data = _load_or_empty(args)
    if not isinstance(data.get(args.key), list):
        data[args.key] = []
    data[args.key].append(_parse_value(args.value))
    _log(data, "add", args.key, args.value)
    atomic_write(args.out, data)
    print(json.dumps({"ok": True, "add": args.key, "count": len(data[args.key])},
                     ensure_ascii=False))


def _log(data, action, key, detail):
    entry = {"time": now_iso(), "action": action, "key": key, "detail": detail}
    if not isinstance(data.get("change_log"), list):
        data["change_log"] = []
    data["change_log"].append(entry)


def cmd_log(args):
    data = _load_or_empty(args)
    _log(data, args.action, args.key, args.detail)
    atomic_write(args.out, data)
    print(json.dumps({"ok": True, "action": args.action}, ensure_ascii=False))


def cmd_finalize(args):
    data = _load_or_empty(args)
    data["status"] = args.status
    for key in REQUIRED_KEYS:
        data.setdefault(key, [] if key in ("sources", "uncertainty", "change_log") else None)
    case, _ = read_json_safe(args.case)
    contract = extract_output_contract(case) if case is not None else DEFAULT_CONTRACT
    for key in contract:
        data.setdefault(key, EMPTY_VALUE.get(key, None))
    atomic_write(args.out, data)
    summary = {
        "ok": True,
        "status": data["status"],
        "keys": sorted(data.keys()),
    }
    print(json.dumps(summary, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--out", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--set", nargs=2, metavar=("KEY", "VAL"))
    ap.add_argument("--add-list", nargs=2, metavar=("KEY", "VAL"))
    ap.add_argument("--log", nargs=3, metavar=("ACTION", "KEY", "DETAIL"))
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--status", choices=["success", "partial", "failed"], default="partial")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    outdir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(outdir, exist_ok=True)

    if args.init:
        cmd_init(args)
        return 0
    if args.set:
        cmd_set(args)
        return 0
    if args.add_list:
        cmd_add_list(args)
        return 0
    if args.log:
        cmd_log(args)
        return 0
    if args.finalize:
        cmd_finalize(args)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
