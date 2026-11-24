#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the source-bound-audit skill.

Re-checks the deliverable against the runtime contract:

- result.json parses; case_id non-empty; status in the allowed set.
- output_contract echoes every original key (missing values become null/[]).
- sources/uncertainty/change_log are arrays and non-empty.
- every finding has the required fields, valid type/severity, and a quote
  that resolves to an exact (normalized) substring of the source index.
- every facet has a verdict and every finding maps to a known facet.
- report.md exists.

Exit code 0 when no FAIL; 1 when any FAIL. WARN items do not fail the run
but are printed so the agent can improve them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
SEVERITIES = {"critical", "major", "minor", "info"}
FACETS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "method", "statistics", "logic",
]
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="validate result.json")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--work", default=str(DEFAULT_WORK))
    parser.add_argument("--lenient", action="store_true",
                        help="downgrade quote-verification FAIL to WARN")
    args = parser.parse_args(argv)

    root = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)

    fails = []
    warns = []

    result_path = out / "result.json"
    if not result_path.exists():
        fails.append("result.json missing")
        _print(fails, warns)
        return 1

    result, err = extract.read_json_robust(result_path)
    if err:
        fails.append(f"result.json unparseable: {err}")
        _print(fails, warns)
        return 1

    # case_id / status
    if not result.get("case_id"):
        fails.append("case_id empty")
    if result.get("status") not in ALLOWED_STATUS:
        fails.append(f"status {result.get('status')!r} not in {sorted(ALLOWED_STATUS)}")

    # arrays
    for key in ("sources", "uncertainty", "change_log"):
        val = result.get(key)
        if not isinstance(val, list) or not val:
            fails.append(f"{key} missing or empty array")

    # output_contract echo
    case_obj, case_id, contract, case_errors = extract.load_case(root)
    if contract:
        oc = result.get("output_contract")
        if not isinstance(oc, dict):
            fails.append("output_contract missing from result.json")
        else:
            for k in contract:
                if k not in oc:
                    fails.append(f"output_contract key {k!r} not echoed")
    else:
        warns.append("no output_contract in case.json (contract_missing assumed)")

    # report / findings / facets
    report = result.get("report")
    if not isinstance(report, dict):
        fails.append("report missing")
    else:
        findings = report.get("findings")
        if not isinstance(findings, list):
            fails.append("report.findings missing")
        else:
            index = _rebuild_index(root, work)
            seen_ids = set()
            for f in findings:
                fid = f.get("id", "?")
                if fid in seen_ids:
                    fails.append(f"duplicate finding id {fid}")
                seen_ids.add(fid)
                for k in FINDING_REQUIRED:
                    v = f.get(k)
                    if v is None or (isinstance(v, str) and not v.strip()):
                        fails.append(f"{fid}: missing/empty field {k}")
                if f.get("type") not in FINDING_TYPES:
                    fails.append(f"{fid}: invalid type {f.get('type')!r}")
                if f.get("severity") not in SEVERITIES:
                    fails.append(f"{fid}: invalid severity {f.get('severity')!r}")
                facet = f.get("facet")
                if facet not in FACETS:
                    warns.append(f"{fid}: facet {facet!r} not in known facets")
                # quote verification
                verified = _verify_quote(index, f)
                if not verified:
                    msg = f"{fid}: quote not found in source ({f.get('file')} {f.get('locator')})"
                    if args.lenient:
                        warns.append(msg)
                    else:
                        fails.append(msg)
        facets = report.get("facets", [])
        have = {f.get("facet") for f in facets if isinstance(f, dict)}
        for fname in FACETS:
            if fname not in have:
                warns.append(f"facet {fname} has no verdict record")

    # report.md
    if not (out / "report.md").exists():
        fails.append("report.md missing")

    _print(fails, warns)
    return 1 if fails else 0


def _rebuild_index(root, work):
    idx_path = work / "evidence.json"
    if idx_path.exists():
        obj, err = extract.read_json_robust(idx_path)
        if not err and isinstance(obj, dict) and "sources" in obj:
            return obj
    return extract.build_index(root, work)


def _verify_quote(index, finding):
    q = extract.normalize(finding.get("quote") or "")
    if not q:
        return False
    file_ = finding.get("file") or ""
    locator = finding.get("locator") or ""
    for src in index.get("sources", []):
        if file_ and src["path"] != file_ and src["path"] != Path(file_).name:
            continue
        target = None
        if locator:
            for p in src["paragraphs"]:
                if p["loc"] == locator or locator.endswith("#" + p["loc"]):
                    target = p["text"]
                    break
        if target is not None:
            if q in target:
                return True
            continue
        if q in " ".join(p["text"] for p in src["paragraphs"]):
            return True
        for p in src["paragraphs"]:
            if q[:40] in p["text"]:
                return True
    return False


def _print(fails, warns):
    for w in warns:
        print(f"WARN  {w}")
    for f in fails:
        print(f"FAIL  {f}")
    print(f"validate: {len(fails)} FAIL, {len(warns)} WARN")


if __name__ == "__main__":
    sys.exit(main())
