#!/usr/bin/env python3
"""Author-facing self-test for the review-response skill package.

Exercises scaffold.run against synthetic inputs (empty directory, malformed
JSON, missing input, minimal valid case), validates a crafted complete
result.json schema, and checks the package layout. Run from the package root:

    python3 -B scripts/selftest.py

Exits 0 only when every check passes. Standard library only; no fabricated
benchmark scores, no network access.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scaffold  # noqa: E402


def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(s)


def _scaffold(root, sub, files=None, case_content=None, task_content="# task"):
    d = os.path.join(root, sub)
    os.makedirs(d, exist_ok=True)
    if case_content is not None:
        write(os.path.join(d, "case.json"), case_content)
    if task_content is not None:
        write(os.path.join(d, "task.md"), task_content)
    for rel, txt in (files or {}).items():
        write(os.path.join(d, rel), txt)
    out = os.path.join(d, "out", "result.json")
    res = scaffold.run(os.path.join(d, "case.json"), os.path.join(d, "task.md"),
                       os.path.join(d, "work"), out)
    return d, out, res


def scenario_empty(root):
    _, _, res = _scaffold(root, "empty")
    ok, errs = scaffold.validate_schema(res)
    return (ok and not errs and bool(res["version_boundary"]["material_gaps"]),
            "empty input: valid skeleton, material_gaps=%s" % res["version_boundary"]["material_gaps"])


def scenario_bad_json(root):
    _, _, res = _scaffold(root, "badjson", case_content="{ not valid json")
    ok, errs = scaffold.validate_schema(res)
    has_deg = any("JSON" in e for e in (res.get("degradations") or []))
    return (ok and not errs and has_deg), "bad JSON recorded as degradation"


def scenario_missing(root):
    _, _, res = _scaffold(root, "missing", case_content=None, task_content=None)
    ok, errs = scaffold.validate_schema(res)
    return (ok and not errs and res.get("case_id") is None), "missing input handled, case_id=None"


def scenario_minimal(root):
    files = {
        "materials/manuscript-baseline.md":
            "# Baseline\n## Abstract\nWe tested 10 samples with method X.\n",
        "materials/reviewer-comments.md":
            "R1: Please clarify the sample size.\nR2: Add the ethics statement.\n",
    }
    d, _, res = _scaffold(root, "minimal", files=files,
                          case_content=json.dumps({"case_id": "demo-1"}))
    with open(os.path.join(d, "work", "comment_ledger.json"), "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    n = len(ledger.get("comments") or [])
    return (res.get("case_id") == "demo-1" and n == 2 and res["status"] == "in_progress",
            "case_id kept, %d comments decomposed" % n)


def scenario_schema(root):
    res = {
        "case_id": "demo-2", "status": "complete",
        "deliverable": {"path": "/tmp/r.md", "format": "markdown", "title": "t",
                        "sections": [], "status": "complete",
                        "revised_manuscript": "/tmp/m.md"},
        "fact_source_mapping": [{"claim": "c", "source_ref": "s", "location": "l",
                                 "quote": "q", "status": "traced"}],
        "version_boundary": {"changes": [], "pending_confirmation": [],
                             "material_gaps": [], "anomalies": []},
        "sources": [], "uncertainty": [], "change_log": [],
    }
    ok, errs = scaffold.validate_schema(res)
    return (ok and not errs), "complete-result schema valid"


def scenario_layout(root):
    skill_md = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")
    return os.path.exists(skill_md), "SKILL.md present at package root"


def main():
    cases = [
        ("empty", scenario_empty),
        ("bad_json", scenario_bad_json),
        ("missing", scenario_missing),
        ("minimal", scenario_minimal),
        ("schema", scenario_schema),
        ("layout", scenario_layout),
    ]
    failures = 0
    tmp_root = tempfile.mkdtemp(prefix="rr_skill_")
    for name, fn in cases:
        try:
            ok, detail = fn(tmp_root)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
        print("  [%s] %-8s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            failures += 1
    print("selftest: %d/%d passed" % (len(cases) - failures, len(cases)))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
