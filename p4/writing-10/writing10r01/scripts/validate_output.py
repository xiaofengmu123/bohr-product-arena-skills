#!/usr/bin/env python3
"""validate_output.py — hard schema gate for /app/output/result.json.

Verifies required top-level fields, the three output-contract fields, basic
types, a legal status value, and that referenced deliverable files exist.
Exit 0 when valid, 1 otherwise (report on stdout). Standard library only.
"""
import argparse
import json
import os
import sys

REQUIRED = [
    "case_id", "status", "deliverable", "fact_source_mapping",
    "version_boundary", "sources", "uncertainty", "pending_human", "change_log",
]
CONTRACT = ["deliverable", "fact_source_mapping", "version_boundary"]
STATUS_OK = {
    "complete", "partial", "failed", "insufficient_evidence",
    "input_error", "in_progress",
}
LIST_FIELDS = [
    "fact_source_mapping", "sources", "uncertainty", "pending_human",
    "change_log", "comment_registry", "evidence_map", "errors", "degradations",
]


def main():
    ap = argparse.ArgumentParser(description="Validate result.json schema.")
    ap.add_argument("--result", default="/app/output/result.json")
    args = ap.parse_args()

    if not os.path.isfile(args.result):
        print(json.dumps({"ok": False, "error": "result.json missing"}, ensure_ascii=False))
        return 1
    try:
        with open(args.result, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "read_failed:%s" % exc}, ensure_ascii=False))
        return 1

    problems = []
    if not isinstance(data, dict):
        problems.append("root_not_object")

    for key in REQUIRED:
        if key not in data:
            problems.append("missing_field:" + key)
    for key in CONTRACT:
        if key not in data:
            problems.append("missing_contract_field:" + key)

    if isinstance(data.get("status"), str) and data.get("status") not in STATUS_OK:
        problems.append("bad_status:" + str(data.get("status")))
    if not data.get("case_id"):
        problems.append("empty_case_id")

    for key in LIST_FIELDS:
        if key in data and not isinstance(data[key], list):
            problems.append("field_not_list:" + key)

    if not isinstance(data.get("version_boundary"), dict):
        problems.append("version_boundary_not_object")
    if not isinstance(data.get("deliverable"), dict):
        problems.append("deliverable_not_object")

    # referenced deliverable files exist (accept absolute or basename-in-out-dir)
    out_dir = os.path.dirname(os.path.abspath(args.result))
    deliverable = data.get("deliverable") or {}
    for key in ("path", "manuscript_path"):
        p = deliverable.get(key)
        if not p:
            continue
        if os.path.isfile(p):
            continue
        if os.path.isfile(os.path.join(out_dir, os.path.basename(p))):
            continue
        problems.append("deliverable_file_missing:" + str(p))

    ok = not problems
    print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
