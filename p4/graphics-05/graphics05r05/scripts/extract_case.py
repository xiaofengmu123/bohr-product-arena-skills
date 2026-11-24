#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract_case.py — normalize /input/* and persist a contract-complete partial skeleton.

Standard library only. This is the FIRST executable step of the skill. It discovers the
case's output_contract, echoes every contract field into result.json (unknown values are
null-filled, never dropped), and writes a status=partial skeleton so that any later failure
still leaves a valid deliverable on disk.

Exit codes:
  0  skeleton written (degraded input still exits 0; degradations are recorded)
  1  /input/case.json missing -> status=failed skeleton written, then exit 1

Writes:
  <work>/case_normalized.json   parsed case + discovered contract + input file scan
  <work>/figure_spec.json       template the Agent fills before build
  <output>/result.json          partial skeleton, contract-complete
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024
STATUSES = ("completed", "partial", "failed")

# Canonical output contract. Insertion order here = order of keys in result.json
# when the case provides no output_contract of its own.
CANONICAL_CONTRACT = {
    "case_id": None,
    "status": "partial",
    "graphical_abstract": {
        "question": None,
        "method": None,
        "results": None,
        "conclusion": None,
    },
    "editable_source": {
        "path": None,
        "format": "svg",
        "layers": [],
        "text_editable": None,
    },
    "exports": [],
    "entities": [],
    "relations": [],
    "data_mapping": [],
    "legend": [],
    "sources": [],
    "uncertainty": [],
    "limitations": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "runtime": {"seconds": None, "model_requests": None},
    "change_log": [],
}

# Keys whose canonical default is a list (used when null-filling an unknown key).
LIST_KEYS = {
    "exports", "entities", "relations", "data_mapping", "legend",
    "sources", "uncertainty", "limitations", "pending_human",
    "errors", "degradations", "change_log",
}

FIGURE_SPEC_TEMPLATE = {
    "case_id": None,
    "title": None,
    "canvas": {"width": 1200, "height": 1600, "unit": "px", "background": "#FFFFFF"},
    "palette": ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9",
                "#F0E442", "#000000"],
    "panels": [],
    "entities": [],
    "relations": [],
    "data": [],
    "legend": [],
    "footnote": None,
}


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clone(value):
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(v) for v in value]
    return value


def canonical_default(key):
    """Return a typed default for a canonical field, else None."""
    if key in CANONICAL_CONTRACT:
        return _clone(CANONICAL_CONTRACT[key])
    if key in LIST_KEYS:
        return []
    return None


def default_from_spec(key, spec):
    """Derive a null/default value from an output_contract field spec."""
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if "list" in t or "array" in t:
            return []
        if "object" in t or "dict" in t or "map" in t:
            return {}
        # string / number / boolean etc. -> null
        return None
    return canonical_default(key)


def read_text_robust(path, degradations):
    """Read text tolerating BOM and common encodings; never crashes."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            degradations.append({"id": f"read_fail_{os.path.basename(path)}",
                                 "item": "read_failed",
                                 "detail": f"{path}: {exc}"})
            return None, None
    degradations.append({"id": f"decode_fail_{os.path.basename(path)}",
                         "item": "undecodable",
                         "detail": f"{path}: no encoding matched"})
    return None, None


def read_json_robust(path, degradations):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        degradations.append({"id": "file_too_large", "item": "skipped",
                             "detail": f"{path} ({size} bytes) exceeds {MAX_FILE_BYTES}"})
        return None
    text, _ = read_text_robust(path, degradations)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Best-effort repair: parse the outermost JSON object.
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        degradations.append({"id": "bad_json", "item": "unparseable",
                             "detail": f"{path}: {exc}"})
        return None


def find_case_id(data, input_dir):
    if isinstance(data, dict):
        for key in ("case_id", "challenge_id", "id", "case", "task_id", "name"):
            if key in data and isinstance(data[key], (str, int)) and data[key] != "":
                return str(data[key])
    # Fallback: filename of case.json's parent marker.
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        return "case"
    return "unknown_case"


def parse_contract(data):
    """Return an ordered list of (key, default) pairs, or None if no contract found."""
    if not isinstance(data, dict):
        return None
    oc = data.get("output_contract")
    if oc is None:
        for alias in ("outputContract", "result_schema", "output_fields", "schema"):
            if alias in data:
                oc = data[alias]
                break
    if oc is None:
        return None
    pairs = []
    if isinstance(oc, dict):
        for key, spec in oc.items():
            pairs.append((str(key), default_from_spec(str(key), spec)))
    elif isinstance(oc, (list, tuple)):
        for item in oc:
            if isinstance(item, dict) and "name" in item:
                key, spec = str(item["name"]), item
            else:
                key, spec = str(item), None
            pairs.append((key, default_from_spec(key, spec)))
    else:
        return None
    return pairs


def scan_inputs(input_dir):
    """List material files under /input with size; mark large files."""
    found = []
    if not os.path.isdir(input_dir):
        return found
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if os.path.isfile(path):
            size = os.path.getsize(path)
            found.append({"name": name, "path": path.replace(os.sep, "/"),
                          "size": size,
                          "skipped": size > MAX_FILE_BYTES,
                          "ext": os.path.splitext(name)[1].lower()})
    return found


def build_skeleton(case_id, contract_pairs, degradations):
    result = {}
    result["case_id"] = case_id
    result["status"] = "partial"
    seen = {"case_id", "status"}

    if contract_pairs is not None:
        for key, default in contract_pairs:
            if key in seen:
                continue
            result[key] = default
            seen.add(key)
        result["contract_source"] = "case_output_contract"
    else:
        result["contract_source"] = "canonical"

    for key in CANONICAL_CONTRACT:
        if key in seen:
            continue
        result[key] = canonical_default(key)
        seen.add(key)

    # Degraded keys recorded alongside the contract, not dropped.
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("uncertainty", [])
    result.setdefault("limitations", [])
    result.setdefault("pending_human", [])
    result.setdefault("sources", [])

    result["change_log"] = [{
        "version": 1,
        "time_utc": utcnow(),
        "what": "result skeleton persisted (status=partial)",
        "why": "save an honest partial deliverable early; improve incrementally",
    }]
    if contract_pairs is None:
        result["degradations"].append({
            "id": "no_output_contract",
            "item": "canonical_schema_used",
            "detail": "/input/case.json has no output_contract; canonical schema applied",
        })
    result["degradations"].extend(degradations)
    return result


def normalize(input_dir=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    degradations = []
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    if not os.path.isfile(case_path):
        case_id = os.path.basename(input_dir.rstrip("/\\")) or "unknown_case"
        result = build_skeleton(case_id, None, [{
            "id": "missing_case_json", "item": "input_error",
            "detail": f"{case_path} not found; cannot resolve output_contract",
        }])
        result["status"] = "failed"
        result["errors"].append({"id": "missing_case_json", "item": "input_error",
                                 "detail": f"{case_path} not found"})
        result["change_log"][0]["what"] = "failed skeleton persisted (case.json missing)"
        _write_json(os.path.join(output_dir, "result.json"), result)
        _write_json(os.path.join(work_dir, "case_normalized.json"), {
            "case_id": case_id, "case_json_found": False, "output_contract": None,
            "input_files": scan_inputs(input_dir),
        })
        _write_json(os.path.join(work_dir, "figure_spec.json"),
                    dict(FIGURE_SPEC_TEMPLATE, case_id=case_id))
        return {"case_id": case_id, "ok": False, "reason": "missing_case_json"}

    data = read_json_robust(case_path, degradations)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        degradations.append({"id": "case_not_object", "item": "input_error",
                             "detail": f"{case_path} parsed to non-object; treated as empty"})
        data = {}

    case_id = find_case_id(data, input_dir)
    contract_pairs = parse_contract(data)
    contract_keys = [k for k, _ in contract_pairs] if contract_pairs is not None else \
        [k for k in CANONICAL_CONTRACT if k not in ("case_id", "status")]

    result = build_skeleton(case_id, contract_pairs, degradations)

    task_text = None
    if os.path.isfile(task_path):
        task_text, _ = read_text_robust(task_path, degradations)

    normalized = {
        "case_id": case_id,
        "case_json_found": True,
        "case_raw": data,
        "output_contract": ({"_form": "dict_or_list", "keys": contract_keys}
                            if contract_pairs is not None else None),
        "contract_keys": contract_keys,
        "contract_source": result.get("contract_source"),
        "task_text": task_text,
        "input_files": scan_inputs(input_dir),
    }

    figure_spec = dict(FIGURE_SPEC_TEMPLATE)
    figure_spec["case_id"] = case_id
    if isinstance(data, dict) and isinstance(data.get("title"), str):
        figure_spec["title"] = data["title"]

    _write_json(os.path.join(work_dir, "case_normalized.json"), normalized)
    _write_json(os.path.join(work_dir, "figure_spec.json"), figure_spec)
    _write_json(os.path.join(output_dir, "result.json"), result)
    return {"case_id": case_id, "ok": True,
            "contract_keys": contract_keys,
            "contract_source": result.get("contract_source")}


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    summary = normalize(args.input, args.output, args.work)
    if not summary["ok"]:
        print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
