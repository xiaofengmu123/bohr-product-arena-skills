#!/usr/bin/env python3
"""Discover and normalize runtime inputs into a JSON summary.

Usage:
  python scripts/read_input.py --case /input/case.json --task /input/task.md \
      --input-dir /input --work /app/work

Writes <work>/input_summary.json. Never raises on missing or malformed inputs:
every degradation is recorded so downstream steps can still emit an honest
partial result instead of crashing.

Standard library only; paths work on Linux (runtime) and Windows (development).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024  # per-material-file cap; larger files are skipped and recorded
TEXT_EXT = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".log", ".tex", ".bib"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xls"}
FIG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf", ".svg", ".eps", ".gif"}

# Fallback contract used when /input/case.json declares no output_contract.
DEFAULT_CONTRACT = {
    "deliverable": {"title": "", "files": [], "summary": ""},
    "facts_sources": [],
    "version_boundaries": {
        "diff_summary": "",
        "pending_human": [],
        "material_gaps": [],
        "anomalies": [],
        "limitations": [],
    },
}


def read_text(path):
    """Return (text, encoding, error). Tolerant of BOM and bad encodings."""
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="replace") as fh:
                return fh.read(), enc, None
        except OSError as exc:
            last_err = str(exc)
    return "", "unknown", last_err


def safe_json(path):
    """Parse JSON to (obj, error); never raises."""
    text, _enc, err = read_text(path)
    if err is not None and not text:
        return None, "read failed: %s" % err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - we degrade, never crash
        return None, "json parse failed: %s" % exc


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TABLE_EXT:
        return "table"
    if ext in FIG_EXT:
        return "figure"
    if ext in TEXT_EXT or ext == "":
        return "text"
    return "other"


def scan_materials(input_dir):
    """Return (materials, degradations). Deterministic ordering by relative path."""
    materials, degradations = [], []
    if not os.path.isdir(input_dir):
        degradations.append({"where": "input-dir", "issue": "directory missing: %s" % input_dir})
        return materials, degradations
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(fp)
            except OSError as exc:
                degradations.append({"where": rel, "issue": "stat failed: %s" % exc})
                continue
            if size > MAX_FILE_BYTES:
                degradations.append({"where": rel, "issue": "size %d > cap %d, skipped" % (size, MAX_FILE_BYTES)})
                continue
            kind = classify(fp)
            preview = ""
            if kind in ("text", "table"):
                preview = read_text(fp)[0][:800]
            materials.append({
                "path": fp,
                "rel": rel,
                "kind": kind,
                "size": size,
                "preview": preview,
            })
    return materials, degradations


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    degradations = []

    case_obj, case_err = safe_json(a.case)
    if case_err:
        degradations.append({"where": "case.json", "issue": case_err})
        case_obj = {}
    if not isinstance(case_obj, dict):
        case_obj = {}

    task_text, _enc, task_err = read_text(a.task)
    if task_err is not None and not task_text:
        degradations.append({"where": "task.md", "issue": task_err})

    materials, mat_degradations = scan_materials(a.input_dir)
    degradations.extend(mat_degradations)

    # Preserve the declared contract verbatim; fall back to the default contract.
    output_contract = case_obj.get("output_contract")
    if not isinstance(output_contract, dict) or not output_contract:
        output_contract = DEFAULT_CONTRACT
        degradations.append({
            "where": "output_contract",
            "issue": "not declared in case.json; using default contract fields",
        })

    case_id = ""
    for key in ("case_id", "id", "challenge_id"):
        val = case_obj.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break

    summary = {
        "case_id": case_id,
        "output_contract": output_contract,
        "task": task_text[:12000],
        "task_truncated": len(task_text) > 12000,
        "task_path": a.task,
        "case_path": a.case,
        "input_dir": a.input_dir,
        "materials": materials,
        "material_count": len(materials),
        "degradations": degradations,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    os.makedirs(a.work, exist_ok=True)
    out = os.path.join(a.work, "input_summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "summary": out,
        "case_id": case_id,
        "materials": len(materials),
        "degradations": len(degradations),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
