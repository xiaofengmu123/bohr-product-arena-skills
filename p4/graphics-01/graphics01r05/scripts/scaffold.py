#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — bootstrap a contract-complete, honest partial result.json before analysis.

Standard library only. Run from the Skill root:

    python scripts/scaffold.py --input-dir /input --output-dir /app/output --work-dir /app/work

Guarantees:
  * /app/output/result.json exists and is valid JSON even when /input is missing,
    unreadable, or contains bad JSON/encoding;
  * every field of the result contract (RESULT_CONTRACT below) is present with an
    explicit default — fields are never silently omitted;
  * any unknown key declared by the case's original ``output_contract`` is echoed
    back as null/[] (never renamed, never dropped);
  * status starts as ``partial`` (or ``input_error`` when inputs fail), and a
    change_log entry records the skeleton write.
"""
import argparse
import copy
import datetime
import json
import os
import re
import sys

STATUS_ENUM = ["complete", "partial", "failed", "insufficient_evidence", "input_error"]

# Single source of truth for the result.json contract.  references/contract.md
# documents the same fields; keep the two in sync.
RESULT_CONTRACT = {
    "case_id": {"type": "str", "required": True, "default": "unknown",
                "desc": "Case identifier extracted from case.json."},
    "status": {"type": "enum", "required": True, "default": "partial", "enum": STATUS_ENUM,
               "desc": "Honest run status; never overclaim completion."},
    "editable_source_files": {"type": "list", "required": False, "default": [],
        "desc": "Layered editable source artifacts (SVG with <g>/<text>, data files, office docs)."},
    "export_files": {"type": "list", "required": False, "default": [],
        "desc": "Rendered deliverables (PNG/PDF) with size/dpi/font/color checks."},
    "provenance": {"type": "list", "required": False, "default": [],
        "desc": "Each visual element -> source locator + verbatim quote + mapping + version."},
    "sources": {"type": "list", "required": False, "default": [],
        "desc": "Materials actually read, with path and locator."},
    "uncertainty": {"type": "list", "required": False, "default": [],
        "desc": "Unresolved or inferred items with reason, impact, mitigation."},
    "pending_human": {"type": "list", "required": False, "default": [],
        "desc": "Decisions or verifications that require a human."},
    "change_log": {"type": "list", "required": False, "default": [],
        "desc": "Time-ordered action log; the skeleton write is the first entry."},
    "degradations": {"type": "list", "required": False, "default": [],
        "desc": "Software/data degradations applied (OCR, font replacement, skipped files)."},
    "errors": {"type": "list", "required": False, "default": [],
        "desc": "Recoverable errors encountered; never left empty when something failed."},
    "runtime": {"type": "object", "required": False, "default": {},
        "desc": "Run metadata (tool versions, budgets, timings); the platform records ground truth."},
}

# Optional per-item keys used by finalize.py to warn (never to reject).
ITEM_HINTS = {
    "editable_source_files": ["path", "kind", "layers", "editable_text", "note"],
    "export_files": ["path", "format", "width_px", "height_px", "dpi",
                     "color_space", "font_check", "note"],
    "provenance": ["element", "source_ref", "quote", "mapping", "version", "confirmed"],
    "sources": ["ref", "path", "locator", "used_for"],
    "uncertainty": ["item", "reason", "impact", "mitigation"],
    "pending_human": ["item", "reason", "suggested_action"],
    "change_log": ["time", "action", "detail"],
    "degradations": ["item", "reason", "fallback"],
    "errors": ["item", "reason", "handling"],
}

CONTRACT_KEYS = ("output_contract", "outputContract", "output_schema",
                 "result_schema", "result_contract")

MAX_TEXT_BYTES = 2 * 1024 * 1024


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """Read a file tolerantly. Returns (text_or_None, encoding, note)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, None, "stat failed: %s" % e
    if size > MAX_TEXT_BYTES:
        return None, None, "skipped (%.2f MB > %.1f MB limit)" % (
            size / 1048576.0, MAX_TEXT_BYTES / 1048576.0)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, None, "read failed: %s" % e
    if b"\x00" in raw[:8000]:
        return None, "binary", "binary payload (assume no text layer)"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc, ""
        except UnicodeDecodeError:
            continue
    return None, "bytes", "undecodable text"


def load_json_any(path):
    """Tolerantly load JSON. Returns (obj_or_None, error_note)."""
    text, _enc, note = read_text(path)
    if text is None:
        return None, note or "unreadable"
    try:
        return json.loads(text), ""
    except (json.JSONDecodeError, ValueError) as e:
        return None, "bad JSON: %s" % e


def discover_inputs(input_dir):
    """List files under input_dir with size/encoding notes; never fails hard."""
    manifest = []
    note = ""
    if not os.path.isdir(input_dir):
        return manifest, "input dir not found: %s" % input_dir
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            _text, enc, rnote = read_text(p)
            manifest.append({"rel_path": rel, "abs_path": p, "size_bytes": size,
                             "encoding": enc, "note": rnote})
    return manifest, note


def find_output_contract(case_obj):
    """Return (contract_dict, key_list) declared by the case, if any."""
    if not isinstance(case_obj, dict):
        return {}, []
    for k in CONTRACT_KEYS:
        v = case_obj.get(k)
        if isinstance(v, dict):
            return v, list(v.keys())
        if isinstance(v, list):
            return {}, [str(x) for x in v if isinstance(x, str)]
    for v in case_obj.values():
        if isinstance(v, dict):
            for k in CONTRACT_KEYS:
                if isinstance(v.get(k), dict):
                    return v[k], list(v[k].keys())
    return {}, []


def extract_case_id(case_obj, task_text=""):
    if isinstance(case_obj, dict):
        for k in ("case_id", "caseId", "id"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    m = re.search(r"challenge[-_]?id[:：]\s*`?([A-Za-z0-9._-]+)", task_text or "", re.I)
    if m:
        return m.group(1)
    return "unknown"


def default_for(spec):
    d = spec.get("default")
    return copy.deepcopy(d) if isinstance(d, (list, dict)) else d


def build_skeleton(case_id, original_keys):
    result = {k: default_for(spec) for k, spec in RESULT_CONTRACT.items()}
    for k in original_keys:                      # echo unknown original keys verbatim
        if k not in result:
            result[k] = None
    result["case_id"] = case_id
    result["status"] = "partial"
    result["runtime"] = {"contract": "RESULT_CONTRACT v1",
                         "skeleton_written_at": iso_now()}
    result["change_log"] = [{"time": iso_now(), "action": "skeleton_written",
                             "detail": "Contract-complete partial skeleton written before analysis."}]
    return result


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--work-dir", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(a.output_dir, exist_ok=True)
    os.makedirs(a.work_dir, exist_ok=True)

    manifest, manifest_note = discover_inputs(a.input_dir)
    case_path = os.path.join(a.input_dir, "case.json")
    task_path = os.path.join(a.input_dir, "task.md")

    case_obj, case_err = load_json_any(case_path)
    task_text = None
    if os.path.exists(task_path):
        task_text, _, _ = read_text(task_path)

    problems = []
    if case_obj is None:
        problems.append("case.json unavailable: %s" % (case_err or "not found"))
    if not manifest:
        problems.append("no readable input files" + (": %s" % manifest_note if manifest_note else ""))

    case_id = extract_case_id(case_obj or {}, task_text or "")
    original_contract, original_keys = find_output_contract(case_obj or {})

    result = build_skeleton(case_id, original_keys)
    if problems:
        result["status"] = "input_error"
        result["errors"] = [{"item": p, "reason": "input discovery failed",
                             "handling": "skeleton written; halt or degrade honestly"}
                            for p in problems]
    if not original_keys:
        result["degradations"].append({
            "item": "no output_contract declared in case.json",
            "reason": "case input did not carry an output_contract",
            "fallback": "bundled RESULT_CONTRACT used; unknown keys, if later found, preserved as null"})

    write_json(os.path.join(a.work_dir, "contract.json"), {
        "bundled": RESULT_CONTRACT,
        "original_contract": original_contract,
        "original_keys": original_keys,
        "case_id": case_id,
        "generated_at": iso_now(),
    })
    write_json(os.path.join(a.work_dir, "inputs_manifest.json"), {
        "input_dir": a.input_dir,
        "files": manifest,
        "note": manifest_note,
        "generated_at": iso_now(),
    })
    out = os.path.join(a.output_dir, "result.json")
    write_json(out, result)
    print(json.dumps({"ok": True, "case_id": case_id, "status": result["status"],
                      "input_files": len(manifest), "result": out,
                      "contract_keys": len(RESULT_CONTRACT) + len([k for k in original_keys if k not in RESULT_CONTRACT])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
