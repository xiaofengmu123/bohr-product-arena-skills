#!/usr/bin/env python3
"""Strict schema validation of /app/output/result.json.

Checks that every output-contract field is present and correctly shaped, that
status is honest, and that the file is valid UTF-8 JSON. Exit code 0 = valid,
1 = invalid (a machine-readable report is printed to stdout; optional --report
saves it). Stdlib only; fail-soft on missing files.
"""
import argparse
import json
import os
import sys

ALLOWED_STATUS = {"in_progress", "complete", "partial", "insufficient_evidence", "input_error", "failed"}
ALLOWED_FSM_STATUS = {"traced", "assumption", "unverified", "source_conflict"}

REQUIRED_TOP = ["case_id", "status", "deliverable", "fact_source_mapping",
                "version_boundary", "sources", "uncertainty", "change_log"]
DELIVERABLE_KEYS = ["path", "format", "title", "components", "previews", "status"]
VERSION_KEYS = ["changes", "pending_human", "material_gaps", "anomalies"]


def issue(problems, level, path, msg):
    problems.append({"level": level, "path": path, "message": msg})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)

    problems = []

    if not os.path.exists(a.result):
        issue(problems, "error", a.result, "result.json not found")
    else:
        try:
            with open(a.result, "r", encoding="utf-8-sig") as fh:
                obj = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            issue(problems, "error", a.result, "invalid JSON: %s" % e)
            obj = None

        if isinstance(obj, dict):
            for key in REQUIRED_TOP:
                if key not in obj:
                    issue(problems, "error", key, "missing required field")
            if obj.get("status") not in ALLOWED_STATUS:
                issue(problems, "error", "status", "status %r not in %s" % (obj.get("status"), sorted(ALLOWED_STATUS)))
            if not obj.get("case_id"):
                issue(problems, "error", "case_id", "case_id missing or empty")

            dv = obj.get("deliverable")
            if isinstance(dv, dict):
                for key in DELIVERABLE_KEYS:
                    if key not in dv:
                        issue(problems, "warn", "deliverable.%s" % key, "recommended key missing")
                if dv.get("path") and not isinstance(dv.get("path"), str):
                    issue(problems, "error", "deliverable.path", "must be a string path")
            else:
                issue(problems, "error", "deliverable", "must be an object")

            fsm = obj.get("fact_source_mapping")
            if not isinstance(fsm, list):
                issue(problems, "error", "fact_source_mapping", "must be a list")
            else:
                for i, item in enumerate(fsm):
                    p = "fact_source_mapping[%d]" % i
                    if not isinstance(item, dict):
                        issue(problems, "error", p, "entry must be an object")
                        continue
                    if not item.get("claim"):
                        issue(problems, "warn", p, "missing claim")
                    if not item.get("source_ref"):
                        issue(problems, "warn", p, "missing source_ref")
                    if item.get("status") not in ALLOWED_FSM_STATUS:
                        issue(problems, "error", p + ".status",
                              "status %r not in %s" % (item.get("status"), sorted(ALLOWED_FSM_STATUS)))

            vb = obj.get("version_boundary")
            if not isinstance(vb, dict):
                issue(problems, "error", "version_boundary", "must be an object")
            else:
                for key in VERSION_KEYS:
                    if key not in vb:
                        issue(problems, "warn", "version_boundary.%s" % key, "recommended key missing")
                    elif not isinstance(vb[key], list):
                        issue(problems, "error", "version_boundary.%s" % key, "must be a list")

            for key in ("sources", "uncertainty", "change_log"):
                if key in obj and not isinstance(obj[key], list):
                    issue(problems, "error", key, "must be a list")
        else:
            issue(problems, "error", a.result, "top-level JSON value must be an object")

    errors = [p for p in problems if p["level"] == "error"]
    report = {"valid": len(errors) == 0, "errors": len(errors), "warnings": len(problems) - len(errors),
              "problems": problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.report:
        try:
            with open(a.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except OSError as e:
            print("report write failed: %s" % e, file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
