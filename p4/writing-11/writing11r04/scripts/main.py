#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Result.json lifecycle manager for the academic-translation-bilingual-writer Skill.

Standard library only. Defaults follow the platform runtime contract:
    input  -> /input
    output -> /app/output
    work   -> /app/work

All JSON writes are atomic (temp file + os.replace). Reads tolerate UTF-8 BOM
and broken encodings. Every subcommand is deterministic and exits 0 on success.

Subcommands
    brief     scan the input directory and write a case_brief.json
    init      write a partial result.json skeleton (status=in_progress)
    set       set a value at a dotted path in result.json
    add       append one item to a list field in result.json
    add-log   append one change_log entry
    finalize  set the final status and append a finalize log entry
    validate  validate result.json schema against the case contract
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone

DEFAULT_INPUT = os.environ.get("SKILL_INPUT", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK", "/app/work")

# Runtime contract always requires these fields on top of the output_contract.
ALWAYS_FIELDS = ("sources", "uncertainty", "pending_human", "limitations", "change_log")

# Standard fallback contract when case.json does not declare output_contract.
FALLBACK_CONTRACT = {
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

DIRECTION_HINTS = [
    "source_language", "src_lang", "source_lang", "language",
    "target_language", "tgt_lang", "target_lang",
    "direction", "translation_direction", "output_form", "deliverable_form",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: str) -> str:
    """Read a text file tolerating UTF-8 BOM and broken encodings."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_json(path: str):
    """Read a JSON file, returning None on any failure instead of raising."""
    try:
        text = read_text(path)
        return json.loads(text)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def atomic_write_json(path: str, obj) -> None:
    """Write obj as UTF-8 JSON atomically; create parent dirs as needed."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".result-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def extract_case_id(case) -> str:
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "caseId", "caseid", "challenge_id"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_output_contract(case):
    """Return the declared output_contract, or the fallback three-field contract."""
    if isinstance(case, dict):
        declared = case.get("output_contract")
        if isinstance(declared, dict) and declared:
            return declared
        if isinstance(declared, list):
            return {name: None for name in declared if isinstance(name, str)}
    return deepcopy(FALLBACK_CONTRACT)


def _empty_for(value):
    """Give a field an empty value whose shape matches the contract declaration."""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return deepcopy(value)
    return None


def build_skeleton(case_id, contract) -> dict:
    result = {"case_id": case_id, "status": "in_progress"}
    for key, value in contract.items():
        result[key] = _empty_for(value)
    for key in ALWAYS_FIELDS:
        result.setdefault(key, [])
    return result


def _navigate(obj, path: str, create: bool = False):
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, dict):
            if part not in cur:
                if not create:
                    raise KeyError(path)
                cur[part] = {}
            cur = cur[part]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            raise TypeError(path)
    return cur, parts[-1]


def set_path(obj, path: str, value) -> None:
    container, last = _navigate(obj, path, create=True)
    if isinstance(container, dict):
        container[last] = value
    elif isinstance(container, list):
        container[int(last)] = value
    else:
        raise TypeError(path)


def get_path(obj, path: str, default=None):
    try:
        container, last = _navigate(obj, path, create=False)
        if isinstance(container, dict):
            return container.get(last, default)
        if isinstance(container, list):
            return container[int(last)]
    except (KeyError, IndexError, TypeError, ValueError):
        return default
    return default


def append_to_list(obj, path: str, item) -> None:
    container, last = _navigate(obj, path, create=True)
    target = container[last] if isinstance(container, dict) else container
    if not isinstance(target, list):
        raise TypeError("path %s is not a list" % path)
    target.append(item)


def log_entry(action: str, key: str = "", detail: str = "") -> dict:
    return {"time": now_iso(), "action": action, "key": key, "detail": detail}


# --------------------------------------------------------------------------- #
# Input discovery / briefing
# --------------------------------------------------------------------------- #
def scan_input(input_dir: str) -> dict:
    files = []
    if os.path.isdir(input_dir):
        for root, _dirs, names in os.walk(input_dir):
            for name in sorted(names):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                files.append({"path": "/input/" + rel, "rel": rel, "size": size})
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case = read_json(case_path) if os.path.isfile(case_path) else None
    task_text = read_text(task_path) if os.path.isfile(task_path) else ""
    return {"case": case, "task_text": task_text, "files": files,
            "case_path": case_path, "task_path": task_path}


def extract_hints(case, task_text: str) -> dict:
    hints = {}
    if isinstance(case, dict):
        for key in DIRECTION_HINTS:
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                hints[key] = value.strip()
        for key in ("task", "description", "instructions", "prompt"):
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                hints.setdefault("_task_text", value.strip())
    text = task_text + "\n" + hints.get("_task_text", "")
    if re.search(r"英译中|EN\s*[→\-—>]+\s*ZH|English\s*to\s*Chinese|source.*[Ee]nglish.*target.*[Cc]hinese", text):
        hints["direction_hint"] = "EN->ZH"
    elif re.search(r"中译英|ZH\s*[→\-—>]+\s*EN|Chinese\s*to\s*English|源语言.*中文|目标语言.*英文", text):
        hints["direction_hint"] = "ZH->EN"
    for form in ("纯译文", "双语对照", "带修订标记", "修订稿", "对照"):
        if form in text:
            hints.setdefault("output_form_hint", form)
            break
    hints.pop("_task_text", None)
    return hints


def cmd_brief(args) -> int:
    scan = scan_input(args.input)
    case_id = extract_case_id(scan["case"])
    contract = extract_output_contract(scan["case"])
    brief = {
        "case_id": case_id,
        "contract_fields": sorted(contract.keys()),
        "contract_declared": isinstance(scan["case"], dict) and "output_contract" in scan["case"],
        "materials": scan["files"],
        "has_case_json": os.path.isfile(scan["case_path"]),
        "has_task_md": os.path.isfile(scan["task_path"]),
        "missing": [f["path"] for f in scan["files"] if f["size"] <= 0],
        "hints": extract_hints(scan["case"], scan["task_text"]),
    }
    atomic_write_json(args.out, brief)
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args) -> int:
    scan = scan_input(args.input)
    case_id = extract_case_id(scan["case"]) or args.case_id
    contract = extract_output_contract(scan["case"])
    result = build_skeleton(case_id, contract)
    result["change_log"] = [log_entry("init", "result.json", "skeleton with status=in_progress")]
    atomic_write_json(args.out, result)
    brief_out = os.path.join(args.work, "case_brief.json")
    try:
        cmd_brief(argparse.Namespace(input=args.input, out=brief_out))
    except OSError:
        pass
    print(json.dumps({"ok": True, "case_id": case_id,
                      "contract_fields": sorted(contract.keys()),
                      "out": args.out}, ensure_ascii=False))
    return 0


def _load_result(path: str) -> dict:
    result = read_json(path)
    if not isinstance(result, dict):
        raise SystemExit("result.json missing or not a JSON object: %s" % path)
    return result


def cmd_set(args) -> int:
    result = _load_result(args.out)
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError:
        value = args.value
    set_path(result, args.path, value)
    atomic_write_json(args.out, result)
    print(json.dumps({"ok": True, "path": args.path}, ensure_ascii=False))
    return 0


def cmd_add(args) -> int:
    result = _load_result(args.out)
    try:
        item = json.loads(args.item)
    except json.JSONDecodeError:
        item = args.item
    append_to_list(result, args.path, item)
    atomic_write_json(args.out, result)
    print(json.dumps({"ok": True, "path": args.path}, ensure_ascii=False))
    return 0


def cmd_add_log(args) -> int:
    result = _load_result(args.out)
    append_to_list(result, "change_log", log_entry(args.action, args.key, args.detail))
    atomic_write_json(args.out, result)
    print(json.dumps({"ok": True, "action": args.action}, ensure_ascii=False))
    return 0


def cmd_finalize(args) -> int:
    result = _load_result(args.out)
    if args.status not in ("success", "partial", "failed"):
        raise SystemExit("status must be one of success|partial|failed")
    result["status"] = args.status
    append_to_list(result, "change_log", log_entry("finalize", "status", args.status))
    atomic_write_json(args.out, result)
    print(json.dumps({"ok": True, "status": args.status}, ensure_ascii=False))
    return 0


def _validate(result: dict, contract: dict) -> list:
    problems = []
    for key in ("case_id", "status"):
        if not result.get(key):
            problems.append("missing required field: %s" % key)
    for key in contract.keys():
        if key not in result:
            problems.append("missing output_contract field: %s" % key)
    for key in ALWAYS_FIELDS:
        if key not in result:
            problems.append("missing standard field: %s" % key)
    return problems


def cmd_validate(args) -> int:
    result = _load_result(args.out)
    case = read_json(args.case) if os.path.isfile(args.case) else None
    contract = extract_output_contract(case)
    problems = _validate(result, contract)
    out = {
        "ok": not problems,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "problems": problems,
        "contract_fields": sorted(contract.keys()),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("brief", help="scan input and write case_brief.json")
    b.add_argument("--input", default=DEFAULT_INPUT)
    b.add_argument("--out", default=os.path.join(DEFAULT_WORK, "case_brief.json"))
    b.set_defaults(func=cmd_brief)

    i = sub.add_parser("init", help="write result.json skeleton")
    i.add_argument("--input", default=DEFAULT_INPUT)
    i.add_argument("--case", default=os.path.join(DEFAULT_INPUT, "case.json"))
    i.add_argument("--case-id", default=None, help="fallback when case.json lacks an id")
    i.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    i.add_argument("--work", default=DEFAULT_WORK)
    i.set_defaults(func=cmd_init)

    s = sub.add_parser("set", help="set value at dotted path")
    s.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    s.add_argument("--path", required=True)
    s.add_argument("--value", required=True)
    s.set_defaults(func=cmd_set)

    a = sub.add_parser("add", help="append item to a list field")
    a.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    a.add_argument("--path", required=True)
    a.add_argument("--item", required=True)
    a.set_defaults(func=cmd_add)

    lg = sub.add_parser("add-log", help="append a change_log entry")
    lg.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    lg.add_argument("--action", required=True)
    lg.add_argument("--key", default="")
    lg.add_argument("--detail", default="")
    lg.set_defaults(func=cmd_add_log)

    f = sub.add_parser("finalize", help="set final status")
    f.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    f.add_argument("--status", required=True)
    f.set_defaults(func=cmd_finalize)

    v = sub.add_parser("validate", help="validate result.json schema")
    v.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    v.add_argument("--case", default=os.path.join(DEFAULT_INPUT, "case.json"))
    v.set_defaults(func=cmd_validate)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
