#!/usr/bin/env python3
"""Academic translation / bilingual writing: case discovery + result.json manager.

Pure standard library. The Skill drives an LLM workflow; this script is the
deterministic backbone that (a) inventories /input and writes case_brief.json,
and (b) maintains /app/output/result.json atomically so a partial deliverable
always survives a crash.

Subcommands:
  brief        read /input/case.json + task.md, write case_brief.json
  init         write an atomic result.json skeleton (the 3-minute early-save)
  set          set one top-level key to a JSON value
  add-list     append a JSON object to a top-level list key
  add-note     append a JSON object to a list, incl. dotted version_boundary.*
  add-change   append {time, action, key, detail} to change_log
  finalize     set status and close runtime; keeps every field already written
  dump         print the current result.json (compact)

Default paths are /input, /app/output, /app/work; override with
--input/--output/--work for local testing.
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

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

# List-like keys that must exist in a valid result even when empty.
REQUIRED_LISTS = [
    "sources", "uncertainty", "pending_human", "degradations", "change_log",
    "deliverables", "fact_source_map",
]

VERSION_BOUNDARY_LISTS = ["modifications", "human_confirm", "material_gaps", "anomalies"]

NOTE_KEYS = (
    REQUIRED_LISTS
    + ["version_boundary." + k for k in VERSION_BOUNDARY_LISTS]
)


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_any(path):
    """Read a JSON file tolerantly; return None on bad JSON/encoding/missing."""
    if not path or not os.path.exists(path):
        return None
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def read_text_any(path, limit=2_000_000):
    """Read text tolerantly; return None when missing or over the size cap."""
    if not path or not os.path.exists(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > limit:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def find_case_id(data):
    """Locate case_id / caseId / id, including one level of nesting."""
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, int):
            return str(val)
    for sub in ("case", "task", "meta", "metadata"):
        v = data.get(sub)
        if isinstance(v, dict):
            cid = find_case_id(v)
            if cid is not None:
                return cid
    return None


def detect_language_direction(data):
    """Declared language direction; return None when not declared (no guessing)."""
    if not isinstance(data, dict):
        return {"source_language": None, "target_language": None, "note": "no case data"}
    src = data.get("source_language") or data.get("sourceLanguage") or data.get("src_lang")
    tgt = data.get("target_language") or data.get("targetLanguage") or data.get("tgt_lang")
    note = "declared" if (src and tgt) else "not declared"
    return {"source_language": src, "target_language": tgt, "note": note}


def list_files(root, max_files=500, size_cap=2_000_000):
    out = []
    if not root or not os.path.isdir(root):
        return out
    for base, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            out.append({"path": p, "name": name, "size": size,
                        "oversized": size > size_cap})
            if len(out) >= max_files:
                return out
    return out


def collect_contract_fields(case_data):
    """Return (keys, defaults) from a declared output_contract, else the default 3."""
    if isinstance(case_data, dict):
        for key in ("output_contract", "outputContract"):
            oc = case_data.get(key)
            if isinstance(oc, dict) and oc:
                return list(oc.keys()), dict(oc)
    keys = list(DEFAULT_CONTRACT.keys())
    return keys, {k: json.loads(json.dumps(v)) for k, v in DEFAULT_CONTRACT.items()}


def empty_value_for(key):
    if key in DEFAULT_CONTRACT:
        return json.loads(json.dumps(DEFAULT_CONTRACT[key]))
    return None


def atomic_write_json(path, obj):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_existing(a):
    rp = os.path.join(a.output, "result.json")
    obj = load_json_any(rp)
    if isinstance(obj, dict):
        return rp, obj
    return rp, None


def _ensure_slots(obj):
    for k in REQUIRED_LISTS:
        obj.setdefault(k, [])
    obj.setdefault("version_boundary", {
        "modifications": [], "human_confirm": [], "material_gaps": [],
        "anomalies": [], "editable": True,
    })
    obj.setdefault("runtime", {"start_epoch": None, "end_epoch": None, "seconds": None})
    return obj


def cmd_brief(a):
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)
    case_path = os.path.join(a.input, "case.json")
    task_path = os.path.join(a.input, "task.md")
    case_data = load_json_any(case_path)
    task_text = read_text_any(task_path)
    cid = find_case_id(case_data)
    direction = detect_language_direction(case_data)
    contract_keys, contract = collect_contract_fields(case_data)
    files = list_files(a.input)
    degradations = []
    if case_data is None:
        degradations.append("case.json missing or not valid JSON")
    if task_text is None:
        degradations.append("task.md missing or oversized")
    brief = {
        "case_id": cid,
        "case_file": case_path,
        "task_file": task_path,
        "direction": direction,
        "output_contract_keys": contract_keys,
        "output_contract": contract,
        "input_files": files,
        "degradations": degradations,
        "generated_at": _now_iso(),
    }
    out_path = a.brief_out or os.path.join(a.work, "case_brief.json")
    atomic_write_json(out_path, brief)
    print(json.dumps(brief, ensure_ascii=False))
    return 0


def cmd_init(a):
    os.makedirs(a.output, exist_ok=True)
    rp, existing = _load_existing(a)
    if isinstance(existing, dict) and not a.force:
        _ensure_slots(existing)
        atomic_write_json(rp, existing)
        print(json.dumps({"ok": True, "action": "keep-existing", "path": rp},
                         ensure_ascii=False))
        return 0
    case_data = load_json_any(os.path.join(a.input, "case.json"))
    cid = find_case_id(case_data)
    keys, _contract = collect_contract_fields(case_data)
    obj = {"case_id": cid}
    for k in keys:
        obj[k] = empty_value_for(k)
    _ensure_slots(obj)
    obj["status"] = "in_progress"
    obj["runtime"]["start_epoch"] = time.time()
    obj["change_log"].append({
        "time": _now_iso(), "action": "init", "key": "result",
        "detail": "skeleton created (early-save guarantee)",
    })
    atomic_write_json(rp, obj)
    print(json.dumps({"ok": True, "action": "init", "path": rp,
                      "case_id": cid, "contract_keys": keys},
                     ensure_ascii=False))
    return 0


def _parse_json_value(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text  # fall back to raw string


def cmd_set(a):
    rp, obj = _load_existing(a)
    if obj is None:
        obj = {"case_id": None}
        _ensure_slots(obj)
        obj["status"] = "in_progress"
    obj[a.key] = _parse_json_value(a.value)
    atomic_write_json(rp, obj)
    print(json.dumps({"ok": True, "key": a.key}, ensure_ascii=False))
    return 0


def cmd_add_list(a):
    rp, obj = _load_existing(a)
    if obj is None:
        obj = {"case_id": None}
        _ensure_slots(obj)
    item = _parse_json_value(a.item)
    lst = obj.setdefault(a.key, [])
    if not isinstance(lst, list):
        lst = []
        obj[a.key] = lst
    lst.append(item)
    atomic_write_json(rp, obj)
    print(json.dumps({"ok": True, "key": a.key, "count": len(lst)}, ensure_ascii=False))
    return 0


def cmd_add_note(a):
    rp, obj = _load_existing(a)
    if obj is None:
        obj = {"case_id": None}
        _ensure_slots(obj)
    item = _parse_json_value(a.item)
    if a.key not in NOTE_KEYS:
        print(json.dumps({"ok": False, "error": "unknown list key: " + a.key},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    if a.key.startswith("version_boundary."):
        vb = obj.setdefault("version_boundary", {})
        sub = a.key.split(".", 1)[1]
        lst = vb.setdefault(sub, [])
        if not isinstance(lst, list):
            lst = []
            vb[sub] = lst
    else:
        lst = obj.setdefault(a.key, [])
        if not isinstance(lst, list):
            lst = []
            obj[a.key] = lst
    lst.append(item)
    atomic_write_json(rp, obj)
    print(json.dumps({"ok": True, "key": a.key, "count": len(lst)}, ensure_ascii=False))
    return 0


def cmd_add_change(a):
    rp, obj = _load_existing(a)
    if obj is None:
        obj = {"case_id": None}
        _ensure_slots(obj)
    entry = {"time": _now_iso(), "action": a.action, "key": a.key,
             "detail": a.detail}
    obj.setdefault("change_log", []).append(entry)
    atomic_write_json(rp, obj)
    print(json.dumps({"ok": True, "change_log_count": len(obj["change_log"])},
                     ensure_ascii=False))
    return 0


def cmd_finalize(a):
    rp, obj = _load_existing(a)
    if obj is None:
        print(json.dumps({"ok": False, "error": "no result.json to finalize"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    obj["status"] = a.status
    rt = obj.setdefault("runtime", {})
    rt["end_epoch"] = time.time()
    if isinstance(rt.get("start_epoch"), (int, float)):
        rt["seconds"] = round(rt["end_epoch"] - rt["start_epoch"], 2)
    if a.status in ("success", "partial", "failed"):
        obj["change_log"].append({
            "time": _now_iso(), "action": "finalize", "key": "status",
            "detail": "finalized as " + a.status,
        })
    atomic_write_json(rp, obj)
    print(json.dumps({"ok": True, "status": a.status, "path": rp}, ensure_ascii=False))
    return 0


def cmd_dump(a):
    rp, obj = _load_existing(a)
    if obj is None:
        print(json.dumps({"ok": False, "error": "no result.json"}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="case discovery + result.json manager for the bilingual-writing Skill")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("brief", help="inventory input and write case_brief.json")
    p.add_argument("--brief-out", default=None)
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("init", help="write atomic result.json skeleton")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("set", help="set one top-level key to a JSON value")
    p.add_argument("key")
    p.add_argument("value")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("add-list", help="append a JSON item to a top-level list key")
    p.add_argument("key")
    p.add_argument("item")
    p.set_defaults(func=cmd_add_list)

    p = sub.add_parser("add-note", help="append a JSON item to a list (incl. version_boundary.*)")
    p.add_argument("key")
    p.add_argument("item")
    p.set_defaults(func=cmd_add_note)

    p = sub.add_parser("add-change", help="append a change_log entry")
    p.add_argument("--action", default="change")
    p.add_argument("key")
    p.add_argument("detail")
    p.set_defaults(func=cmd_add_change)

    p = sub.add_parser("finalize", help="set status and close runtime")
    p.add_argument("--status", default="partial", choices=["success", "partial", "failed", "in_progress"])
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("dump", help="print current result.json")
    p.set_defaults(func=cmd_dump)

    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
