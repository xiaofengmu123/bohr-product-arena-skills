#!/usr/bin/env python3
"""Validate result.json contract and traceability -> verify_report.json."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C

REQUIRED = ["case_id", "status", "sources", "uncertainty", "change_log"]
DEFAULT_KEYS = ["diagnosis", "edit_plan", "evidence_map", "version_boundary",
                "artifacts"]


def main():
    ap = argparse.ArgumentParser(description="Verify result.json.")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--out", default="/app/output")
    args = ap.parse_args()

    paths = C.resolve_paths(args.case, None, None, None, args.out, args.result)
    report_path = os.path.join(paths["out"], "verify_report.json")
    result = C.load_existing_result(paths["result"])

    issues = []
    warns = []

    if not result:
        issues.append({"level": "FAIL", "field": "result.json",
                       "reason": "result.json missing or empty"})
    else:
        for key in REQUIRED:
            if key not in result:
                issues.append({"level": "FAIL", "field": key,
                               "reason": "missing required field"})
        if not result.get("case_id"):
            issues.append({"level": "FAIL", "field": "case_id",
                           "reason": "empty case_id"})
        status = result.get("status")
        if status not in ("complete", "partial", "insufficient_evidence",
                          "input_error", "in_progress"):
            warns.append({"level": "WARN", "field": "status",
                          "reason": f"unexpected status {status!r}"})

        # Output-contract keys must be present.
        case_data = C.read_json(paths["case"])
        for key in C.output_contract_from(case_data):
            if key not in result:
                warns.append({"level": "WARN", "field": key,
                              "reason": "output_contract key missing"})

        # Traceability: every evidence_map entry needs locator + verbatim quote.
        em = result.get("evidence_map")
        if isinstance(em, list):
            for i, item in enumerate(em):
                if not isinstance(item, dict):
                    issues.append({"level": "FAIL", "field": "evidence_map",
                                   "index": i, "reason": "entry not object"})
                    continue
                missing = [k for k in ("claim_id", "locator", "quote")
                           if not item.get(k)]
                if missing:
                    issues.append({"level": "FAIL", "field": "evidence_map",
                                   "index": i,
                                   "reason": f"missing {missing}"})
        elif em is None:
            warns.append({"level": "WARN", "field": "evidence_map",
                          "reason": "evidence_map is null"})
        else:
            issues.append({"level": "FAIL", "field": "evidence_map",
                           "reason": "evidence_map must be a list"})

        # Every source entry needs a source_ref.
        srcs = result.get("sources")
        if isinstance(srcs, list):
            for i, s in enumerate(srcs):
                if not isinstance(s, dict) or not s.get("source_ref"):
                    issues.append({"level": "FAIL", "field": "sources",
                                   "index": i, "reason": "source_ref missing"})
        elif srcs is None:
            warns.append({"level": "WARN", "field": "sources",
                          "reason": "sources is null"})

        # change_log should be non-empty.
        cl = result.get("change_log")
        if isinstance(cl, list) and not cl:
            warns.append({"level": "WARN", "field": "change_log",
                          "reason": "empty change_log"})

    ok = not any(i["level"] == "FAIL" for i in issues)
    report = {
        "case_id": result.get("case_id") if result else None,
        "generated_at": C.utcnow(),
        "ok": ok,
        "issues": issues,
        "warnings": warns,
    }
    C.write_json(report_path, report)
    print(C.utcnow(), "verify", "PASS" if ok else "FAIL",
          f"({len(issues)} issues, {len(warns)} warnings) ->", report_path)
    # Exit 0 so the pipeline can still mark a partial deliverable.
    return 0


if __name__ == "__main__":
    sys.exit(main())
