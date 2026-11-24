#!/usr/bin/env python3
"""Contract self-test for academic-translation-bilingual.

Validates /app/output/result.json and its deliverables without fabricating
anything. Exits 0 when the contract is satisfiable (even if status=partial);
exits 1 only for a structural failure (missing/invalid result.json).

Standard library only.
"""
import json
import os
import sys

import core

REQUIRED_FIELDS = (
    "case_id", "status", "deliverables", "fact_source_map",
    "version_boundary", "sources", "uncertainty", "change_log",
)
OPTIONAL_FIELDS = ("pending_human", "errors", "degradations", "runtime")


def check_result(result, out_dir):
    """Return (errors, warnings). errors are structural, warnings are content gaps."""
    errors, warnings = [], []

    if not isinstance(result, dict):
        return ["result.json is not a JSON object"], []

    for f in REQUIRED_FIELDS:
        if f not in result:
            errors.append("missing required field: %s" % f)

    if "case_id" in result and not result.get("case_id"):
        warnings.append("case_id is empty")

    status = result.get("status")
    if status not in ("success", "partial", "failed", "in_progress"):
        warnings.append("unexpected status: %r" % status)

    # deliverables: list of dicts with path/format/title/description.
    for i, d in enumerate(result.get("deliverables") or []):
        if not isinstance(d, dict):
            warnings.append("deliverables[%d] is not an object" % i)
            continue
        if not d.get("path"):
            warnings.append("deliverables[%d].path empty" % i)
            continue
        p = d["path"]
        if not os.path.isabs(p):
            p = os.path.join(out_dir, p)
        if not os.path.exists(p):
            warnings.append("deliverables[%d].path does not exist: %s" % (i, p))

    # fact_source_map: every item needs source (locator + quote where possible).
    for i, m in enumerate(result.get("fact_source_map") or []):
        if not isinstance(m, dict):
            warnings.append("fact_source_map[%d] is not an object" % i)
            continue
        if not m.get("source"):
            warnings.append("fact_source_map[%d].source empty" % i)
        kind = m.get("kind")
        if kind not in ("number", "term", "symbol", "citation", "figure", "claim", None):
            warnings.append("fact_source_map[%d] unknown kind: %r" % (i, kind))

    vb = result.get("version_boundary")
    if vb is not None and not isinstance(vb, dict):
        warnings.append("version_boundary is not an object")
    elif isinstance(vb, dict):
        for k in ("modifications", "human_confirm", "material_gaps", "anomalies"):
            if not isinstance(vb.get(k), list):
                warnings.append("version_boundary.%s is not a list" % k)

    for f in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(f), list):
            errors.append("%s is not a list" % f)

    return errors, warnings


def run(input_dir, out_dir, work_dir):
    result_path = os.path.join(out_dir, "result.json")
    obj, note = core.safe_read_json(result_path)
    if obj is None:
        print("SELFTEST FAIL: cannot read result.json: %s" % note)
        return 1

    errors, warnings = check_result(obj, out_dir)
    report = {
        "result": result_path,
        "read_note": note,
        "errors": errors,
        "warnings": warnings,
        "status": obj.get("status"),
    }
    report_path = os.path.join(work_dir, "selftest_report.json")
    core.write_json_atomic(report_path, report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        print("SELFTEST: %d error(s)" % len(errors), file=sys.stderr)
        return 1
    print("SELFTEST: ok (%d warning(s))" % len(warnings))
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args()
    sys.exit(run(a.input, a.output, a.work))
