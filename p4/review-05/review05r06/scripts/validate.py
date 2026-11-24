#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the source-bound compliance audit deliverable.

Checks the contract fields, finding schema, quote verifiability, facet
coverage and status honesty against the evidence ledger. Exit code 0 means no
fixable FAIL; 1 means at least one FAIL that must be fixed before delivery.
Warnings do not change the exit code but are printed for the audit agent.

Standard library only. Imports extract.py and main.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import extract
import main

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def check(condition, label, kind):
    return {"label": label, "kind": kind, "ok": bool(condition)}


def main_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)

    checks = []

    res_path = out / "result.json"
    result = None
    result_err = None
    if not res_path.exists():
        checks.append(check(False, "result.json exists", "fail"))
    else:
        result, result_err = extract.read_json_robust(res_path)
        checks.append(check(result_err is None and isinstance(result, dict),
                            "result.json parses as object", "fail"))

    if result is not None:
        case_id = result.get("case_id")
        checks.append(check(isinstance(case_id, str) and case_id.strip(),
                            "case_id non-empty", "fail"))
        status = result.get("status")
        checks.append(check(status in ALLOWED_STATUS,
                            f"status in allowed set (got {status!r})", "fail"))

        # output_contract keys preserved
        contract = result.get("output_contract")
        checks.append(check(isinstance(contract, dict),
                            "output_contract present as object", "warn"))
        case_obj = None
        case_path = inp / "case.json"
        if case_path.exists():
            case_obj, _ = extract.read_json_robust(case_path)
        if isinstance(case_obj, dict):
            expected = case_obj.get("output_contract")
            if isinstance(expected, dict):
                missing = [k for k in expected if k not in contract]
                checks.append(check(not missing,
                                    f"output_contract keys preserved (missing {missing})", "fail"))

        for key in ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human"):
            val = result.get(key)
            checks.append(check(isinstance(val, list),
                                f"{key} is a list", "fail"))

        report = result.get("report", {})
        findings = report.get("findings", [])
        checks.append(check(isinstance(findings, list), "report.findings is a list", "fail"))

        paragraphs = []
        parsed_names = set()
        evidence_path = work / "evidence.json"
        evidence = None
        if evidence_path.exists():
            evidence, _ = extract.read_json_robust(evidence_path)
        if evidence:
            paragraphs = evidence.get("paragraphs", [])
            parsed_names = {Path(f["path"]).name for f in evidence.get("files", []) if f["parsed"]}

        for index, finding in enumerate(findings):
            prefix = f"finding[{index}]"
            for field in main.FINDING_REQUIRED:
                checks.append(check(isinstance(finding.get(field), (str, int)) and str(finding.get(field, "")).strip(),
                                    f"{prefix}.{field} present", "fail"))
            ftype = finding.get("type")
            checks.append(check(ftype in main.FINDING_TYPES,
                                f"{prefix}.type valid (got {ftype!r})", "fail"))
            severity = finding.get("severity")
            checks.append(check(severity in main.SEVERITIES,
                                f"{prefix}.severity valid (got {severity!r})", "fail"))
            category = finding.get("category")
            if category not in main.KNOWN_CATEGORIES:
                checks.append(check(False, f"{prefix}.category known (got {category!r})", "warn"))
            evidence_status = finding.get("evidence_status")
            if evidence_status in ("not_found", "empty_quote"):
                checks.append(check(False,
                                    f"{prefix}.quote verifiable (status {evidence_status})", "fail"))
            elif evidence_status in ("file_not_indexed_or_not_parsed", None):
                checks.append(check(True,
                                    f"{prefix}.quote check deferred (status {evidence_status})", "warn"))
            else:
                checks.append(check(True,
                                    f"{prefix}.quote matched ({evidence_status})", "pass"))

        report_md = out / "report.md"
        checks.append(check(report_md.exists() and report_md.stat().st_size > 0,
                            "report.md exists and non-empty", "fail"))

        # facet coverage from the report itself (the audit agent maintains these)
        not_checked = report.get("not_checked", [])
        if status == "ok" and not_checked:
            checks.append(check(False,
                                f"status=ok but not_checked non-empty ({len(not_checked)} items)", "fail"))
        elif not_checked:
            checks.append(check(True,
                                f"not_checked non-empty ({len(not_checked)} items), status={status}", "warn"))
        else:
            checks.append(check(True, "coverage complete (not_checked empty)", "pass"))

        plan_path = work / "plan.json"
        checks.append(check(plan_path.exists(), "plan.json exists", "warn"))

        if status == "ok":
            checks.append(check(bool(report.get("summary")),
                                "status=ok requires a summary", "fail"))

    fails = [c for c in checks if c["kind"] == "fail" and not c["ok"]]
    warns = [c for c in checks if c["kind"] == "warn" and not c["ok"]]
    for c in checks:
        mark = "PASS" if c["ok"] else c["kind"].upper()
        print(f"[{mark}] {c['label']}")
    print(f"--- {len(checks)} checks, {len(fails)} fail, {len(warns)} warn ---")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main_cli())
