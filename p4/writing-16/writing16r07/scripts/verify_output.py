#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify /app/output/result.json against the output contract and cross-check
the deliverable against the source facts inventory.

This is a best-effort, deterministic lint: it reports problems but never
fabricates numbers. Numbers in the deliverable that do not appear in any
source are flagged as `number_not_in_source` (a warning, because the agent may
have legitimately rounded or reformatted); they are never auto-"fixed".

Exit codes:
  0  pass (warnings may still be printed)
  1  result.json missing or invalid JSON
  2  bad arguments
  3  required contract keys missing
"""
import argparse
import json
import os
import re
import sys

REQUIRED_TOP_KEYS = [
    "case_id", "status", "task", "deliverable", "facts_sources",
    "change_log", "version_boundary", "sources", "uncertainty",
    "errors", "degradations", "runtime",
]
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FACT_REQUIRED = {"fact", "locator", "quote"}
NUMBER_RE = re.compile(r"(?<![\w])(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?|(?:\d+\.\d+|\d+))")
ABBREV_RE = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\b")


def read_json(path):
    if not os.path.exists(path):
        return None
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def collect_deliverable_text(result):
    chunks = []
    d = result.get("deliverable") or {}
    if isinstance(d, str):
        chunks.append(d)
    elif isinstance(d, dict):
        for k in ("text", "content", "body", "note", "title"):
            v = d.get(k)
            if isinstance(v, str):
                chunks.append(v)
    return "\n".join(chunks)


def collect_source_numbers(facts_inventory):
    nums = []
    for src in facts_inventory or []:
        for item in (src.get("numbers") or []):
            nums.append(item.get("value", ""))
    # normalize: remove thousand separators for comparison
    norm = set()
    for n in nums:
        n = str(n).replace(",", "").strip()
        if n:
            norm.add(n)
    return norm


def main():
    ap = argparse.ArgumentParser(description="Verify result.json for writing skill")
    ap.add_argument("--output", default="/app/output", help="output dir (default /app/output)")
    ap.add_argument("--work", default="/app/work", help="work dir (default /app/work)")
    args = ap.parse_args()

    out_path = os.path.join(args.output, "result.json")
    result = read_json(out_path)
    if result is None:
        print("[verify] FAIL: result.json missing or invalid JSON: %s" % out_path)
        return 1
    if not isinstance(result, dict):
        print("[verify] FAIL: result.json top level must be an object")
        return 3

    problems = []
    warnings = []

    # 1. contract keys
    for k in REQUIRED_TOP_KEYS:
        if k not in result:
            problems.append("missing key: %s" % k)

    # 2. status
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        problems.append("invalid status: %r" % status)

    # 3. case_id
    cid = result.get("case_id")
    if not isinstance(cid, str) or not cid:
        problems.append("case_id missing or empty")

    # 4. facts_sources entries
    fs = result.get("facts_sources")
    if not isinstance(fs, list):
        problems.append("facts_sources must be a list")
    else:
        for i, item in enumerate(fs):
            if not isinstance(item, dict):
                problems.append("facts_sources[%d] not an object" % i)
                continue
            for fk in FACT_REQUIRED:
                if fk not in item or item[fk] in (None, ""):
                    problems.append("facts_sources[%d] missing %r" % (i, fk))

    # 5. deliverable paths
    d = result.get("deliverable")
    if isinstance(d, dict) and isinstance(d.get("paths"), list):
        for p in d["paths"]:
            if not isinstance(p, str):
                continue
            abs_p = p if os.path.isabs(p) else os.path.join(args.output, p)
            if not os.path.exists(abs_p):
                warnings.append("deliverable path not found: %s" % p)

    # 6. cross-check numbers in deliverable vs source inventory
    inv_path = os.path.join(args.work, "facts_inventory.json")
    inv = read_json(inv_path) if os.path.exists(inv_path) else None
    src_nums = collect_source_numbers(inv)
    text = collect_deliverable_text(result)
    if text and src_nums:
        for m in NUMBER_RE.finditer(text):
            tok = m.group(0).replace(",", "").strip()
            if tok and tok not in src_nums:
                warnings.append("number in deliverable not found in sources: %r (verify rounding/unit)" % tok)

    # 7. abbreviation first-use expansion heuristic
    if text:
        for m in ABBREV_RE.finditer(text):
            tok = m.group(0)
            if 2 <= len(tok) <= 12 and tok not in ("AI", "URL", "ID", "PDF"):
                warnings.append("abbreviation used, confirm first-use expansion: %r" % tok)

    if problems:
        print("[verify] FAIL (%d problems):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 3

    print("[verify] PASS: %s" % out_path)
    if warnings:
        print("[verify] %d warnings:" % len(warnings))
        for w in warnings:
            print("  - %s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
