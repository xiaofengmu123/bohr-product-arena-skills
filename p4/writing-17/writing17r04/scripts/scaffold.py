#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — discover inputs and write a legal partial result.json skeleton.

Standard library only. Deterministic. It never fabricates content: the analysis
fields start empty and are filled incrementally by the calling Agent, while the
skeleton is already valid JSON so a mid-run crash still leaves a loadable
partial deliverable.

Usage (paths are relative to the Skill root when called from SKILL.md):
  python3 scripts/scaffold.py --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import re
import sys
import time

MAX_SINGLE_FILE = 2 * 1024 * 1024  # skip single files larger than 2 MB
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """Read a text file tolerating BOM and common encodings; return None on failure."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, ValueError, OSError):
            continue
    return None


def discover(input_dir):
    """Walk the input directory; never assume fixed filenames."""
    out = {"files": [], "degradations": [], "errors": []}
    if not os.path.isdir(input_dir):
        out["errors"].append("input directory missing: %s" % input_dir)
        return out
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                out["errors"].append("stat failed: %s (%s)" % (rel, exc))
                continue
            if size > MAX_SINGLE_FILE:
                out["degradations"].append("skipped oversized (%dB): %s" % (size, rel))
                continue
            out["files"].append(rel)
    return out


def extract_case_id(input_dir, files, degradations):
    """Look for a case identifier in case.json or task.md; fall back to unknown.

    Parse failures of case.json are appended to `degradations` in place so the
    robustness contract ("bad JSON is recorded, not fatal") is honored.
    """
    for rel in files:
        if os.path.basename(rel).lower() == "case.json":
            text = read_text(os.path.join(input_dir, rel))
            if text is None:
                degradations.append("case.json unreadable (bad encoding): %s" % rel)
                continue
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    for key in ("case_id", "id", "challenge_id"):
                        val = data.get(key)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
            except (ValueError, TypeError):
                degradations.append("case.json is not valid JSON; case_id set to unknown")
    for rel in files:
        if os.path.basename(rel).lower() == "task.md":
            text = read_text(os.path.join(input_dir, rel))
            if text:
                m = re.search(r"challenge_id[:\s`]*([A-Za-z0-9._-]+)", text)
                if m:
                    return m.group(1)
    return None


def infer_kind(rel):
    base = os.path.basename(rel).lower()
    if base == "case.json":
        return "case_definition"
    if base == "task.md":
        return "task"
    if base.endswith(".pdf"):
        return "pdf_material"
    if base.endswith((".md", ".txt")):
        return "text_material"
    if base.endswith(".json"):
        return "json_material"
    if base.endswith((".csv", ".tsv")):
        return "tabular_material"
    if base.endswith((".bib",)):
        return "bibliography"
    if base.endswith((".tex",)):
        return "latex_material"
    return "other"


def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv):
    ap = argparse.ArgumentParser(description="Scaffold a legal partial result.json.")
    ap.add_argument("--input", default="/input", help="input directory")
    ap.add_argument("--output", default="/app/output", help="output directory")
    ap.add_argument("--work", default="/app/work", help="work directory")
    args = ap.parse_args(argv)

    t0 = time.time()
    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            print("warning: cannot create %s: %s" % (d, exc), file=sys.stderr)

    disc = discover(args.input)
    case_id = extract_case_id(args.input, disc["files"], disc["degradations"]) or "unknown"

    task_header = ""
    for rel in disc["files"]:
        if os.path.basename(rel).lower() == "task.md":
            text = read_text(os.path.join(args.input, rel))
            if text:
                task_header = text[:4000]
            break

    if disc["errors"]:
        status = "input_error"
    elif not disc["files"]:
        status = "insufficient_evidence"
    else:
        status = "partial"

    skeleton = {
        "case_id": case_id,
        "status": status,
        "deliverable": {
            "description": "",
            "files": [],
        },
        "fact_source_map": [],
        "version_boundaries": {
            "changes": [],
            "pending_human": [],
            "material_gaps": [],
            "conflicts": [],
            "errors": disc["errors"],
            "degradations": disc["degradations"],
        },
        "sources": [
            {"path": rel, "kind": infer_kind(rel), "note": ""}
            for rel in disc["files"]
        ],
        "uncertainty": {"notes": [], "unverified_items": []},
        "change_log": [
            {
                "ts": now_ts(),
                "action": "scaffold",
                "detail": "wrote legal partial skeleton; %d input files discovered"
                % len(disc["files"]),
            }
        ],
        "runtime": {
            "started_at": now_ts(),
            "seconds_elapsed": round(time.time() - t0, 2),
        },
    }

    result_path = os.path.join(args.output, "result.json")
    write_json(skeleton, result_path)

    manifest = {
        "input_dir": args.input,
        "files": disc["files"],
        "errors": disc["errors"],
        "degradations": disc["degradations"],
        "case_id": case_id,
        "task_header": task_header,
    }
    write_json(manifest, os.path.join(args.work, "inputs_manifest.json"))

    print(
        json.dumps(
            {
                "status": status,
                "case_id": case_id,
                "result": result_path,
                "files_discovered": len(disc["files"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
