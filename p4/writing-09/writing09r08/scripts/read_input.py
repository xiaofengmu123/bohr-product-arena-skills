#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read and normalize runtime inputs into /app/work/input_summary.json.

Standard library only. All paths are explicit arguments with defaults matching
the Linux runtime; the script may be executed from any working directory.

Usage:
  python scripts/read_input.py \
      --case /input/case.json --task /input/task.md \
      --input-dir /input --work /app/work
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip larger files, record the skip
CLUE_LIMIT = 40

DEFAULT_CONTRACT = {
    "deliverable": "object",
    "facts_sources": "list",
    "version_boundaries": "object",
}

FIG_RE = re.compile(r"\b(Fig(?:ure)?s?\.?|Table|Supplementary|Supplemental)\b", re.I)
STAT_RE = re.compile(r"\b[pP]\s*[<>=]\s*0?\.?\d|±|\bn\s*=\s*\d|\b(SD|SE|SEM|CI|IQR)\b", re.I)
REF_RE = re.compile(r"\[\d+\]|\([A-Z][\w\-' ]+,?\s*(19|20)\d{2}[a-z]?\)", re.I)


def read_text(path):
    """Read text with BOM/bad-encoding tolerance; never raise."""
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def load_json_tolerant(path):
    text = read_text(path)
    if not text:
        return None, "empty or missing"
    try:
        return json.loads(text), None
    except Exception as e:
        return None, "invalid JSON: %s" % e


def find_output_contract(obj, depth=0):
    """Locate output_contract in a case.json with a flexible shape."""
    if depth > 2 or not isinstance(obj, (dict, list)):
        return None
    if isinstance(obj, dict):
        for key in ("output_contract", "outputContract", "output_contract_fields"):
            if key in obj and obj[key] is not None:
                return obj[key]
        for key, val in obj.items():
            if key.startswith("output") and isinstance(val, (dict, list)):
                return val
        for val in obj.values():
            found = find_output_contract(val, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_output_contract(item, depth + 1)
            if found is not None:
                return found
    return None


def contract_fields(contract):
    """Return ordered {field_name: declared_type}."""
    fields = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            if isinstance(v, dict):
                fields[k] = v.get("type", v.get("default_type", "any"))
            elif isinstance(v, list):
                fields[k] = "list"
            else:
                fields[k] = "any"
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields[item] = "any"
            elif isinstance(item, dict):
                k = item.get("name", item.get("field", item.get("key")))
                if k:
                    fields[k] = item.get("type", "any")
    if not fields:
        fields = dict(DEFAULT_CONTRACT)
    return fields


def collect_clues(text):
    figures, stats, refs = [], [], []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        if FIG_RE.search(line) and len(figures) < CLUE_LIMIT:
            figures.append({"line": i + 1, "text": line[:240]})
        if STAT_RE.search(line) and len(stats) < CLUE_LIMIT:
            stats.append({"line": i + 1, "text": line[:240]})
        if REF_RE.search(line) and len(refs) < CLUE_LIMIT:
            refs.append({"line": i + 1, "text": line[:240]})
    return figures, stats, refs


def scan_materials(input_dir, exclude=()):
    mats = []
    warns = []
    if not os.path.isdir(input_dir):
        return mats, ["input dir not found: %s" % input_dir]
    excl = {e.lower() for e in exclude}
    for name in sorted(os.listdir(input_dir)):
        if name.lower() in excl:
            continue
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        item = {"name": name, "path": p, "bytes": size}
        if size > MAX_FILE_BYTES:
            item["skipped"] = True
            item["note"] = "larger than %d bytes, skipped" % MAX_FILE_BYTES
            warns.append("material %s skipped (too large)" % name)
        else:
            text = read_text(p)
            item["chars"] = len(text)
            item["head"] = text[:500]
            item["sha256"] = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
            item["skipped"] = False
        mats.append(item)
    return mats, warns


def main():
    ap = argparse.ArgumentParser(description="Normalize runtime input into input_summary.json")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args()

    os.makedirs(a.work, exist_ok=True)
    warnings, errors = [], []

    case_obj, case_err = load_json_tolerant(a.case)
    if case_err:
        errors.append("case.json: %s" % case_err)
    if not isinstance(case_obj, dict):
        case_obj = {}

    case_id = None
    for key in ("case_id", "caseId", "id"):
        v = case_obj.get(key)
        if isinstance(v, str) and v:
            case_id = v
            break
    if not case_id:
        case_id = os.path.basename(a.case).replace(".json", "") or "unknown-case"
        warnings.append("case_id not found in case.json; using %s" % case_id)

    contract = find_output_contract(case_obj)
    fields = contract_fields(contract)
    if contract is None:
        warnings.append("output_contract not declared in case.json; using defaults "
                        + ", ".join(sorted(DEFAULT_CONTRACT)))

    task_text = read_text(a.task)
    if not task_text:
        warnings.append("task.md empty or missing at %s" % a.task)
    tfig, tstat, tref = collect_clues(task_text)

    materials, mwarn = scan_materials(
        a.input_dir,
        exclude=(os.path.basename(a.case), os.path.basename(a.task)),
    )
    warnings.extend(mwarn)

    style_hints = {}
    for key in ("audience", "style", "format", "genre", "language", "venue", "word_limit"):
        v = case_obj.get(key)
        if isinstance(v, (str, int)):
            style_hints[key] = v

    summary = {
        "case_id": case_id,
        "case_path": a.case,
        "task_path": a.task,
        "task_chars": len(task_text),
        "task_head": task_text[:3000],
        "output_contract_fields": list(fields.keys()),
        "output_contract": {k: {"type": v} for k, v in fields.items()},
        "materials": materials,
        "clues": {
            "task_figures_tables": tfig,
            "task_statistics": tstat,
            "task_references": tref,
        },
        "style_hints": style_hints,
        "warnings": warnings,
        "errors": errors,
        "generated_ts": datetime.now(timezone.utc).isoformat(),
    }

    out = os.path.join(a.work, "input_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "output_contract_fields": list(fields.keys()),
        "materials": len(materials),
        "warnings": warnings,
        "errors": errors,
        "written": out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
