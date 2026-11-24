#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — self-check the /app/output bundle against the output contract.

Checks that result.json carries case_id, status and the output-contract fields,
that editable artifacts exist and are well-formed, and that evidence items are
traceable (locator + verbatim quote). Prints a checklist and returns exit 0 for
"ok"/"partial" (so the harness run is not killed by a soft gap), and exit 1 only
when result.json is missing or unreadable.

Usage:
  python3 scripts/validate.py --output /app/output
  python3 scripts/validate.py --output /app/output --contract 'deliverable,fact_source_map'
"""
import argparse
import json
import os
import sys
from xml.etree import ElementTree as ET

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
DEFAULT_CONTRACT = ["deliverable", "fact_source_map", "version_boundaries"]
EXTRA_FIELDS = ["status", "sources", "uncertainty", "change_log"]


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def main(argv):
    ap = argparse.ArgumentParser(description="Self-check output bundle.")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--contract", default=None,
                    help="comma-separated required contract field names")
    args = ap.parse_args(argv)

    result_path = os.path.join(args.output, "result.json")
    if not os.path.isfile(result_path):
        print("FAIL  result.json missing: %s" % result_path)
        return 1

    try:
        result = load_json(result_path)
    except Exception as exc:
        print("FAIL  result.json unreadable: %s" % exc)
        return 1

    contract = DEFAULT_CONTRACT
    if args.contract:
        contract = [k.strip() for k in args.contract.split(",") if k.strip()]

    fails = []
    warns = []

    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        fails.append("case_id missing or empty")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        fails.append("status %r not in %s" % (status, sorted(ALLOWED_STATUS)))

    for field in contract:
        if field not in result:
            fails.append("missing contract field: %s" % field)
    for field in EXTRA_FIELDS:
        if field not in result:
            warns.append("missing recommended field: %s" % field)

    # deliverable files must exist
    deliverable = result.get("deliverable")
    if isinstance(deliverable, dict):
        files = deliverable.get("files", [])
        if not files:
            warns.append("deliverable.files is empty")
        for f in files:
            p = os.path.join(args.output, f)
            if not os.path.isfile(p):
                fails.append("deliverable file missing: %s" % f)
    else:
        warns.append("deliverable is not an object")

    # evidence items must carry locator + quote
    fsmap = result.get("fact_source_map", [])
    if isinstance(fsmap, list):
        for i, item in enumerate(fsmap):
            if not isinstance(item, dict):
                warns.append("fact_source_map[%d] is not an object" % i)
                continue
            if not item.get("locator"):
                warns.append("fact_source_map[%d] missing locator" % i)
            if not item.get("quote") and item.get("evidence_type") in (None, "fact"):
                warns.append("fact_source_map[%d] missing verbatim quote" % i)

    # mind-map artifacts well-formed
    for name in ("mindmap.md", "mindmap.mmd", "mindmap.svg", "mindmap.json"):
        p = os.path.join(args.output, name)
        if os.path.isfile(p):
            if name == "mindmap.svg":
                try:
                    ET.parse(p)
                except Exception as exc:
                    fails.append("mindmap.svg not parseable XML: %s" % exc)
            elif name == "mindmap.json":
                try:
                    load_json(p)
                except Exception as exc:
                    fails.append("mindmap.json not parseable: %s" % exc)
            elif name == "mindmap.md":
                if os.path.getsize(p) == 0:
                    fails.append("mindmap.md empty")
        else:
            warns.append("artifact not found: %s" % name)

    # honest-status sanity: insufficient_evidence must not claim a full map
    if status == "insufficient_evidence" and result.get("deliverable", {}).get("files"):
        warns.append("status=insufficient_evidence but deliverable.files non-empty; reconcile")

    print("== output contract self-check ==")
    print("case_id: %s" % result.get("case_id", "<missing>"))
    print("status: %s" % status)
    for f in fails:
        print("FAIL  %s" % f)
    for w in warns:
        print("WARN  %s" % w)
    if not fails and not warns:
        print("PASS  all checks ok")
    print("summary: %d fail, %d warn" % (len(fails), len(warns)))

    if fails:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
