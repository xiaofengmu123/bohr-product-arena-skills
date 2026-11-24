#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the academic-integrity-audit skill.

Re-reads /input material (actual files), loads /app/output/result.json, and
verifies the deliverable against the contract and the source text. Every
confirmed finding's quote must be traceable to the actual extracted text;
fabricated quotes produce a FAIL.

Exit code: 0 when no FAIL, 1 otherwise. WARN items are advisory and do not
fail the run, but should be reviewed before delivery.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import extract
import main as main_mod  # reuse constants and helpers

ALLOWED_STATUS = main_mod.ALLOWED_STATUS
FINDING_TYPES = main_mod.FINDING_TYPES
SEVERITIES = main_mod.SEVERITIES
FINDING_STATUSES = main_mod.FINDING_STATUSES
FINDING_REQUIRED = main_mod.FINDING_REQUIRED


def emit(level, msg):
    print(f"[{level}] {msg}")
    return level == "FAIL"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate audit deliverable.")
    parser.add_argument("--input", type=Path, default=main_mod.DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=main_mod.DEFAULT_OUTPUT)
    parser.add_argument("--work", type=Path, default=main_mod.DEFAULT_WORK)
    args = parser.parse_args(argv)

    fails = 0
    result_path = args.output / "result.json"
    result, err = extract.read_json_robust(result_path)
    if err:
        fails += emit("FAIL", f"result.json unreadable: {err}")
        print("Skipping remaining checks (no parseable result).")
        return 1

    # 1. case_id
    case_id = result.get("case_id")
    if not (isinstance(case_id, str) and case_id.strip()):
        fails += emit("FAIL", "case_id missing or empty")
    else:
        emit("PASS", f"case_id: {case_id}")

    # 2. status
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        fails += emit("FAIL", f"status invalid: {status!r}")
    else:
        emit("PASS", f"status: {status}")

    # 3. output_contract presence
    contract = result.get("output_contract")
    if not isinstance(contract, dict):
        if result.get("contract_missing"):
            emit("PASS", "output_contract missing but contract_missing=true recorded")
        else:
            fails += emit("FAIL", "output_contract not a dict and contract_missing not set")
    else:
        emit("PASS", f"output_contract present with {len(contract)} top-level keys")

    # 4. arrays
    for key in ("sources", "uncertainty", "change_log"):
        val = result.get(key)
        if not isinstance(val, list):
            if status == "insufficient_evidence" and key == "sources":
                emit("PASS", f"{key} empty list accepted for insufficient_evidence")
                continue
            fails += emit("FAIL", f"{key} missing or not a list")
        elif not val:
            if status == "insufficient_evidence" and key == "sources":
                emit("PASS", f"{key} empty accepted for insufficient_evidence")
            else:
                fails += emit("FAIL", f"{key} is empty")
        else:
            emit("PASS", f"{key}: {len(val)} item(s)")

    # 5. report object
    report = result.get("report")
    if not isinstance(report, dict):
        fails += emit("FAIL", "report missing or not a dict")
        report = {}
    findings = report.get("findings")
    if not isinstance(findings, list):
        fails += emit("FAIL", "report.findings missing or not a list")
        findings = []

    # 6. build source index for quote verification
    files = extract.discover(args.input)
    sources, _src_errs, _degr = extract.extract_source_index(files, args.input, args.work)
    src_by_path = {s["path"]: s for s in sources}

    # 7. per-finding checks
    for i, f in enumerate(findings, start=1):
        fid = f.get("id", f"#{i}") if isinstance(f, dict) else f"#{i}"
        if not isinstance(f, dict):
            fails += emit("FAIL", f"finding {fid} is not a dict")
            continue
        missing = [k for k in FINDING_REQUIRED if k not in f]
        if missing:
            fails += emit("FAIL", f"finding {fid} missing keys: {missing}")
        for k, allowed in (("type", FINDING_TYPES), ("severity", SEVERITIES), ("status", FINDING_STATUSES)):
            if f.get(k) not in allowed:
                fails += emit("FAIL", f"finding {fid} bad {k}: {f.get(k)!r}")
        # quote traceability (only confirmed findings must prove their quote)
        if f.get("status") in (None, "confirmed"):
            quote = extract.normalize(str(f.get("quote", "")))
            src = src_by_path.get(f.get("file"))
            if not quote:
                fails += emit("FAIL", f"finding {fid} has empty quote")
            elif src is None or not src.get("extracted"):
                fails += emit("FAIL", f"finding {fid} file not extractable: {f.get('file')!r}")
            elif quote not in src.get("full_norm", ""):
                fails += emit("FAIL", f"finding {fid} quote not found in source text")
            else:
                emit("PASS", f"finding {fid} quote verified in {f.get('file')}")

    # 8. coverage
    checked = report.get("checked_items")
    if not isinstance(checked, list) or not checked:
        if findings:
            fails += emit("FAIL", "checked_items missing despite findings present")
        else:
            emit("WARN", "checked_items empty")
    else:
        emit("PASS", f"checked_items: {len(checked)} item(s)")

    # 9. report.md
    report_md = args.output / "report.md"
    if not report_md.exists():
        fails += emit("FAIL", "report.md missing")
    else:
        emit("PASS", f"report.md exists ({report_md.stat().st_size} bytes)")

    # 10. determinism sanity: result.json must be valid UTF-8 JSON (already loaded)
    emit("PASS", "result.json is valid JSON")

    if fails:
        print(f"\n{ fails } FAIL(s); see above.")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
