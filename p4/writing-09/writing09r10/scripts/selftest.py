#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the Results & Discussion writing skill.

Covers four failure surfaces without needing the real fixed cases:
  1. normal material  -> parseable result.json + manuscript.md + auto claims
  2. empty input dir   -> status insufficient_evidence, no crash
  3. bad JSON case     -> degraded summary with a warning, no crash
  4. missing input dir -> warning recorded, no crash

Exit code 0 means all assertions passed. Pure standard library.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def _run_case(root, case_obj, task_text, materials):
    os.makedirs(root, exist_ok=True)
    case_path = os.path.join(root, "case.json")
    with open(case_path, "w", encoding="utf-8") as fh:
        json.dump(case_obj, fh, ensure_ascii=False)
    task_path = os.path.join(root, "task.md")
    with open(task_path, "w", encoding="utf-8") as fh:
        fh.write(task_text)
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    for name, content in materials.items():
        with open(os.path.join(inp, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    result = core.run_pipeline(case_path, task_path, inp, out, work)
    return result, out, work


def main():
    failures = []
    base = tempfile.mkdtemp(prefix="rdw-selftest-")

    # 1) normal material
    contract = {"deliverable": {}, "facts_sources": [], "version_boundaries": {}}
    r1, out1, _ = _run_case(os.path.join(base, "c1"),
                            {"case_id": "selftest-ok", "output_contract": contract},
                            "# task\n", {
        "fig1.txt": "Treatment reduced serum glucose from 12.3 mmol/L to "
                    "8.1 mmol/L (P = 0.02).",
    })
    if r1.get("case_id") != "selftest-ok":
        failures.append("c1 case_id mismatch")
    if r1.get("status") not in ("partial", "complete"):
        failures.append("c1 bad status: %r" % r1.get("status"))
    if not r1.get("facts_sources"):
        failures.append("c1 no facts_sources")
    if not os.path.exists(os.path.join(out1, "result.json")):
        failures.append("c1 result.json missing")
    if not os.path.exists(os.path.join(out1, "manuscript.md")):
        failures.append("c1 manuscript.md missing")

    # 2) empty input dir -> insufficient_evidence
    r2, out2, _ = _run_case(os.path.join(base, "c2"),
                            {"case_id": "selftest-empty"}, "# task\n", {})
    if r2.get("status") != "insufficient_evidence":
        failures.append("c2 expected insufficient_evidence, got %r" % r2.get("status"))
    if not os.path.exists(os.path.join(out2, "result.json")):
        failures.append("c2 result.json missing")

    # 3) bad JSON case.json
    c3 = os.path.join(base, "c3")
    os.makedirs(os.path.join(c3, "input"), exist_ok=True)
    with open(os.path.join(c3, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{this is not valid json")
    with open(os.path.join(c3, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    s3 = core.normalize_input(os.path.join(c3, "case.json"),
                              os.path.join(c3, "task.md"),
                              os.path.join(c3, "input"),
                              os.path.join(c3, "work"))
    if s3.get("case_id") != "unknown-case":
        failures.append("c3 expected unknown-case")
    if not any("unparseable" in w or "empty" in w for w in s3.get("warnings", [])):
        failures.append("c3 missing bad-JSON warning: %r" % s3.get("warnings"))

    # 4) missing input dir
    s4 = core.normalize_input(None, None, os.path.join(base, "no-such-dir"),
                              os.path.join(base, "work4"))
    if not any("input_dir_missing" in w for w in s4.get("warnings", [])):
        failures.append("c4 missing input_dir_missing warning")

    shutil.rmtree(base, ignore_errors=True)
    print(json.dumps({"ok": not failures, "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
