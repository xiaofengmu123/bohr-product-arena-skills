#!/usr/bin/env python3
"""Initialize a schema-valid partial result.json skeleton.

Reads /input/case.json and /input/task.md, discovers material files, and writes
an ``in_progress`` skeleton with all output-contract fields present but empty.
It records only what actually exists on disk; it never fabricates material
content, case content, or benchmark results. Standard library only.
"""
import argparse
import json
import os
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"in_progress", "partial", "complete", "failed", "insufficient_evidence"}
ALLOWED_CLAIM_STATUS = {"traced", "derived", "assumption", "unverified", "source_conflict"}


def read_text(path):
    """Read UTF-8 text, tolerating BOM and common bad encodings. Returns (text, ok)."""
    if not path or not os.path.exists(path):
        return None, False
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None, False
    except OSError:
        return None, False
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), True
        except (UnicodeDecodeError, OSError, LookupError):
            continue
    return None, False


def load_json(path):
    """Tolerant JSON load. Returns (obj, error_string_or_None)."""
    text, ok = read_text(path)
    if not ok or text is None:
        return None, "unreadable or missing: %s" % path
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - must not crash on bad input
        return None, "invalid JSON: %s" % exc


def discover_materials(materials_dir):
    """Walk a materials directory and build a sources inventory."""
    sources = []
    if not materials_dir or not os.path.isdir(materials_dir):
        return sources
    for root, _dirs, files in os.walk(materials_dir):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                sources.append({
                    "id": "mat-skip-%s" % fn, "type": "file", "ref": path,
                    "description": "skipped: file exceeds 2MB limit",
                })
                continue
            ext = os.path.splitext(fn)[1].lower()
            mtype = {
                ".pdf": "pdf", ".md": "markdown", ".txt": "text", ".docx": "docx",
                ".csv": "csv", ".json": "json", ".tex": "latex", ".png": "png",
                ".jpg": "jpg", ".jpeg": "jpg", ".svg": "svg",
            }.get(ext, "other")
            sources.append({"id": "mat-%s" % fn, "type": mtype, "ref": path, "description": fn})
    return sources


def extract_case_id(case_obj):
    """Extract case_id from common key names, preserving the original value."""
    if not isinstance(case_obj, dict):
        return None
    for key in ("case_id", "challenge_id", "id", "armiId"):
        val = case_obj.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, (int, float)):
            return str(val)
    return None


def build_skeleton(case_obj, task_text, sources, case_path, task_path, errors):
    """Build an empty but contract-complete result skeleton."""
    case_id = extract_case_id(case_obj) or ""
    material_gaps = []
    refs = [s["ref"] for s in sources]
    if not any("manuscript" in os.path.basename(r).lower() for r in refs):
        material_gaps.append({"item": "manuscript-baseline not found", "impact": "cannot map revisions to a baseline"})
    if not any("comment" in os.path.basename(r).lower() or "review" in os.path.basename(r).lower() for r in refs):
        material_gaps.append({"item": "reviewer-comments not found", "impact": "cannot build per-comment response ledger"})

    result = {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": {
            "path": None, "format": "markdown", "title": None,
            "version": "v2", "sections": [], "status": "pending",
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [], "pending_confirmation": [], "material_gaps": material_gaps, "anomalies": [],
        },
        "response_letter": {"path": None, "entries": []},
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "degradations": [],
        "change_log": [{
            "ts": int(time.time()),
            "action": "scaffold",
            "detail": "wrote in_progress skeleton from %s and %s" % (case_path, task_path),
        }],
        "runtime": {
            "started_at": int(time.time()),
            "budget_seconds": 900,
            "budget_requests": 64,
        },
        "errors": errors,
    }
    return result


def write_atomic(path, obj):
    """Write JSON atomically: temp file then os.replace."""
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--materials", default="/input/materials")
    ap.add_argument("--out", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    errors = []
    case_obj, case_err = load_json(a.case)
    if case_err:
        errors.append("case: %s" % case_err)
        case_obj = {}
    task_text, task_ok = read_text(a.task)
    if not task_ok:
        errors.append("task unreadable or missing: %s" % a.task)
    sources = discover_materials(a.materials)
    if not sources:
        errors.append("no material files found under %s" % a.materials)

    result = build_skeleton(case_obj, task_text, sources, a.case, a.task, errors)
    try:
        os.makedirs(a.work, exist_ok=True)
        write_atomic(a.out, result)
    except Exception as exc:  # noqa: BLE001
        # Final fallback: ensure the directory exists and report the failure honestly.
        print(json.dumps({
            "ok": False,
            "case_id": result["case_id"],
            "status": result["status"],
            "error": "write failed: %s" % exc,
        }, ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True,
        "case_id": result["case_id"],
        "status": result["status"],
        "sources": len(sources),
        "material_gaps": len(result["version_boundary"]["material_gaps"]),
        "errors": errors,
        "out": a.out,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
