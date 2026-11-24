#!/usr/bin/env python3
"""scaffold.py — discover case inputs and write a partial result skeleton.

Reads /input/case.json and /input/task.md, discovers material files, extracts
case_id and the output-contract field names, then immediately writes a legal
partial result.json so no failure mode leaves an empty deliverable.

Standard library only. Exit code 0 even on degraded input: the truthful status
is recorded inside the skeleton, never fabricated.
"""
import argparse
import json
import os
import sys
import time

UTF8_SIG = "utf-8-sig"
MAX_FILE = 2_000_000  # bytes; larger files are skipped and recorded


def read_text(path):
    """Read a UTF-8 file tolerantly; returns (text_or_None, degradation_or_None)."""
    if not os.path.exists(path):
        return None, "missing:" + str(path)
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_failed:%s:%s" % (path, exc)
    if size > MAX_FILE:
        return None, "too_large:" + str(path)
    for enc in (UTF8_SIG, "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except UnicodeError:
            continue
        except OSError as exc:
            return None, "read_failed:%s:%s" % (path, exc)
    return None, "undecodable:" + str(path)


def walk_files(root):
    """Return relative, forward-slash paths of regular files under root."""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append(rel)
    return sorted(out)


def load_json(path):
    text, deg = read_text(path)
    if text is None:
        return None, [deg]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        return None, ["bad_json:%s:%s" % (path, exc)]


def extract_case_id(case):
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId"):
            value = case.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown"


def find_contract_fields(task_text):
    """Heuristically recover the three output-contract field names from task.md."""
    if not task_text:
        return ["deliverable", "fact_source_mapping", "version_boundary"]
    names = []
    pairs = [
        ("deliverable", "目标产物"),
        ("fact_source_mapping", "事实与来源映射"),
        ("version_boundary", "版本与边界"),
    ]
    for key, label in pairs:
        if key in task_text or label in task_text:
            names.append(key)
    return names or ["deliverable", "fact_source_mapping", "version_boundary"]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    ap = argparse.ArgumentParser(description="Write a partial result skeleton.")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--out", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    input_dir = os.path.dirname(os.path.abspath(args.case))
    degradations = []

    case_data, degs = load_json(args.case)
    degradations.extend(degs)
    task_text, deg = read_text(args.task)
    if deg:
        degradations.append(deg)

    case_id = extract_case_id(case_data) if isinstance(case_data, dict) else "unknown"
    contract_fields = find_contract_fields(task_text)
    materials = walk_files(input_dir) if os.path.isdir(input_dir) else []

    out_dir = os.path.dirname(os.path.abspath(args.out))
    for directory in (out_dir, args.work):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            degradations.append("mkdir_failed:%s:%s" % (directory, exc))

    skeleton = {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": {
            "path": None,
            "manuscript_path": None,
            "format": "markdown",
            "title": None,
            "sections": [],
            "status": "pending",
        },
        "fact_source_mapping": [],
        "evidence_map": [],
        "comment_registry": [],
        "version_boundary": {
            "changes": [],
            "pending_confirmation": [],
            "material_gaps": [],
            "anomalies": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {
                "ts": now_iso(),
                "action": "scaffold",
                "detail": "Partial skeleton written; inputs discovered, status=in_progress.",
            }
        ],
        "errors": [],
        "degradations": degradations,
        "runtime": {
            "input_dir": input_dir,
            "materials": materials,
            "output_contract_fields": contract_fields,
            "written_at": now_iso(),
        },
    }

    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(skeleton, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(json.dumps(
            {"ok": False, "error": "write_failed:%s:%s" % (args.out, exc)},
            ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "materials": materials,
        "output_contract_fields": contract_fields,
        "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
