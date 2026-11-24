#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 0 skeleton: write a minimal valid result.json. Never crashes.

Shared robust helpers (also imported by main.py and selftest.py):
  - text reading with encoding fallback and replacement fallback
  - JSON loading that reports bad JSON instead of raising
  - input discovery with SHA-256 and a 2MB size cap
  - safe atomic-ish JSON writing

Standard library only. Run from the skill root:
  python -B scripts/skeleton.py --input /input --out /app/output/result.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB cap for text materials
HASH_CHUNK = 64 * 1024
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# Contract fields for this grant-writing task (override with case.json keys).
DEFAULT_CONTRACT_FIELDS = ["artifact", "fact_source_map", "version_boundary"]


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str):
    """Read text with encoding fallbacks; return (text_or_None, error_or_None)."""
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, "read_failed:%s" % exc
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "replacement_decode"
    except OSError as exc:
        return None, "read_failed:%s" % exc


def load_json(path: str):
    """Return (obj_or_None, error_or_None). Never raises."""
    text, err = read_text(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "bad_json:%s" % exc


def file_fingerprint(path: str):
    """Return (sha256_hex_or_None, size_or_None, skipped_bool)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, None, False
    if size > MAX_FILE_BYTES:
        return None, size, True
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None, size, False
    return h.hexdigest(), size, False


def discover_input(input_dir: str):
    """List files under input_dir with fingerprints; never raises.

    Returns (files, errors, degradations).
    files: [{"path", "rel", "size", "sha256", "skipped"}]
    """
    files = []
    errors = []
    degradations = []
    root = input_dir
    try:
        if not os.path.isdir(root):
            return files, ["missing_input_dir:%s" % root], degradations
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                sha, size, skipped = file_fingerprint(full)
                if skipped:
                    degradations.append("oversized_skipped:%s:%d" % (full, size))
                    files.append({
                        "path": full,
                        "rel": os.path.relpath(full, root),
                        "size": size,
                        "sha256": None,
                        "skipped": True,
                    })
                    continue
                files.append({
                    "path": full,
                    "rel": os.path.relpath(full, root),
                    "size": size,
                    "sha256": sha,
                    "skipped": False,
                })
    except OSError as exc:
        errors.append("walk_failed:%s" % exc)
    return files, errors, degradations


def case_id_from(case_obj) -> str:
    """Extract a case identifier without ever failing."""
    if not isinstance(case_obj, dict):
        return ""
    for key in ("case_id", "id", "caseId", "challenge_id", "challengeId", "armiId"):
        val = case_obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and str(val).strip():
            return str(val)
    return ""


def contract_fields_from(case_obj):
    """Collect output_contract/required_fields keys from case.json if present."""
    if not isinstance(case_obj, dict):
        return list(DEFAULT_CONTRACT_FIELDS)
    keys = []
    for key in ("output_contract", "output_schema", "required_fields"):
        val = case_obj.get(key)
        if isinstance(val, dict):
            keys.extend(k for k in val.keys() if isinstance(k, str))
        elif isinstance(val, list):
            keys.extend(k for k in val if isinstance(k, str))
        elif isinstance(val, str) and val.strip():
            keys.append(val.strip())
    # preserve order, de-duplicate
    seen = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    if not out:
        out = list(DEFAULT_CONTRACT_FIELDS)
    return out


def build_skeleton(case_obj, input_dir: str, started_at: str):
    """Return a minimal valid result dict. Never raises."""
    contract_fields = contract_fields_from(case_obj)
    case_id = case_id_from(case_obj)
    files, errors, degradations = discover_input(input_dir)
    sources = []
    for f in files:
        if not f.get("skipped"):
            sources.append({
                "path": f["rel"],
                "type": _guess_type(f["rel"]),
                "sha256": f.get("sha256"),
            })
    contract_values = {k: _default_contract_value(k) for k in contract_fields}
    result = {
        "case_id": case_id or None,
        "status": "in_progress",
        "sources": sources,
        "uncertainty": [],
        "change_log": [{
            "time": utcnow(),
            "action": "skeleton",
            "reason": "Step 0 first-3-minute partial result",
            "impact": "minimal valid result.json written",
        }],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "runtime": {"started_at": started_at, "finished_at": None, "seconds": 0.0},
    }
    result.update(contract_values)
    if not files and not errors:
        result["uncertainty"].append({
            "item": "empty_input",
            "reason": "input directory contains no files",
        })
    return result


def _guess_type(rel: str) -> str:
    lower = (rel or "").lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".md", ".markdown")):
        return "markdown"
    if lower.endswith((".txt", ".text")):
        return "text"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".tex"):
        return "latex"
    if lower.endswith((".docx", ".doc")):
        return "office"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
        return "image"
    return "other"


def _default_contract_value(key: str):
    if key == "artifact":
        return {"path": None, "type": "markdown", "description": ""}
    if key in ("fact_source_map", "version_boundary"):
        if key == "fact_source_map":
            return []
        return {
            "modification_diff": [],
            "manual_confirm": [],
            "material_gaps": [],
            "anomalies": [],
        }
    return None


def write_json(obj, path: str):
    """Write JSON safely (temp file + replace). Return (ok, error)."""
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
        return True, None
    except OSError as exc:
        return False, "write_failed:%s" % exc


def _default_input() -> str:
    for candidate in ("/input", "input"):
        if os.path.isdir(candidate):
            return candidate
    return "/input"


def _default_out() -> str:
    return "/app/output/result.json"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Write minimal valid result.json skeleton")
    ap.add_argument("--input", default=_default_input())
    ap.add_argument("--out", default=_default_out())
    a = ap.parse_args(argv)
    started = utcnow()
    case_obj, case_err = load_json(os.path.join(a.input, "case.json"))
    result = build_skeleton(case_obj, a.input, started)
    if case_err:
        result["errors"].append("case.json:" + case_err)
        if case_err.startswith("bad_json"):
            result["status"] = "input_error"
            result["uncertainty"].append({
                "item": "bad_case_json",
                "reason": case_err,
            })
    result["runtime"]["finished_at"] = utcnow()
    result["runtime"]["seconds"] = 0.0
    ok, err = write_json(result, a.out)
    if not ok:
        # Last-resort fallback: write to stdout-adjacent path is impossible; report via stderr.
        sys.stderr.write("skeleton write failed: %s\n" % err)
        return 1
    print(json.dumps({"ok": True, "out": a.out, "status": result["status"],
                      "case_id": result.get("case_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
