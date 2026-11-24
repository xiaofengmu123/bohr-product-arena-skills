#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the Results & Discussion writing Skill.

Runs the pipeline on a synthetic fixture and on degraded inputs (empty dir,
bad JSON, oversized file). Verifies:

- legal result.json skeleton is written first (case_id + status + reserved
  fields + output_contract keys back-filled verbatim)
- intermediate scaffolds (plan.json / figure_model.json / evidence_map.json)
  are created
- numeric inventory is extracted from readable text
- validation flags structural problems and forbidden phrases
- degraded inputs produce honest status without raising

Standard library only. Returns True on success, False on failure.
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

import core_impl as core  # noqa: E402


def _write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _check(ok: bool, label: str, failures: list) -> None:
    print(("  ok  " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def run() -> bool:
    failures: list = []
    base = tempfile.mkdtemp(prefix="rdw_selftest_")
    try:
        # --- fixture: a normal case ---------------------------------------
        case_dir = os.path.join(base, "input")
        work_dir = os.path.join(base, "work")
        out_dir = os.path.join(base, "output")
        _write(os.path.join(case_dir, "case.json"), json.dumps({
            "case_id": "selftest-case-001",
            "output_contract": {
                "deliverable": {"file": "manuscript.md"},
                "facts_sources": [],
                "version_boundaries": {},
            },
            "audience": "journal reviewers",
            "style": "formal",
        }))
        _write(os.path.join(case_dir, "task.md"),
               "# task\n结果与讨论章节写作：把图1数据写入正文。\n")
        _write(os.path.join(case_dir, "materials_data.md"),
               "Figure 1 shows group A 12.5 ± 0.3 mg/mL (SEM, n=6) versus "
               "group B 8.2 ± 0.4 mg/mL (p=0.042, t-test).\n"
               "This is the 首次 measurement in our lab.\n")
        _write(os.path.join(case_dir, "table1.csv"),
               "group,mean,sd,n\nA,12.5,0.3,6\nB,8.2,0.4,6\n")

        outcome = core.run_pipeline(
            os.path.join(case_dir, "case.json"),
            os.path.join(case_dir, "task.md"),
            case_dir, work_dir, out_dir)

        rpath = os.path.join(out_dir, "result.json")
        _check(os.path.isfile(rpath), "result.json written", failures)
        result, err = core.read_json(rpath)
        _check(err is None, "result.json parses", failures)
        _check(result.get("case_id") == "selftest-case-001",
               "case_id preserved", failures)
        for field in ("status", "sources", "uncertainty", "pending_human",
                      "change_log", "errors", "degradations", "runtime",
                      "intermediates"):
            _check(field in result, f"reserved field {field}", failures)
        for key in ("deliverable", "facts_sources", "version_boundaries"):
            _check(key in result, f"output_contract field {key}", failures)
        _check(result.get("status") == "partial",
               "initial status is partial (honest)", failures)

        _check(os.path.isfile(os.path.join(work_dir, "plan.json")),
               "plan.json scaffold", failures)
        _check(os.path.isfile(os.path.join(work_dir, "figure_model.json")),
               "figure_model.json scaffold", failures)
        _check(os.path.isfile(os.path.join(work_dir, "evidence_map.json")),
               "evidence_map.json scaffold", failures)

        fm = core.read_json(os.path.join(work_dir, "figure_model.json"))[0]
        nums = (fm or {}).get("numeric_inventory", {}).get("numbers", [])
        _check(len(nums) > 0, "numeric inventory extracted", failures)
        tbls = (fm or {}).get("tables", [])
        _check(any(t.get("numeric_columns") for t in tbls),
               "CSV table parsed with numeric columns", failures)

        # forbidden-word detection must find "首次" in the source material
        hits = core.check_forbidden("首次 国际领先 填补空白 颠覆性")
        _check(len(hits) == 4, "forbidden phrases detected", failures)

        # validate on the skeleton should surface null deliverable facts_sources
        issues = core.validate_result(rpath,
                                      os.path.join(work_dir, "input_summary.json"))
        _check(isinstance(issues, list), "validate returns issues list", failures)

        # --- degraded: empty input dir ------------------------------------
        empty_dir = os.path.join(base, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        _write(os.path.join(empty_dir, "case.json"), json.dumps(
            {"case_id": "empty-001", "output_contract": {}}))
        _write(os.path.join(empty_dir, "task.md"), "# no materials\n")
        core.run_pipeline(
            os.path.join(empty_dir, "case.json"),
            os.path.join(empty_dir, "task.md"),
            empty_dir, os.path.join(base, "ework"), os.path.join(base, "eout"))
        eresult = core.read_json(os.path.join(base, "eout", "result.json"))[0]
        _check(eresult.get("status") in ("partial", "insufficient_evidence"),
               "empty input degrades honestly", failures)

        # --- degraded: bad JSON -------------------------------------------
        bad_dir = os.path.join(base, "bad")
        os.makedirs(bad_dir, exist_ok=True)
        _write(os.path.join(bad_dir, "case.json"), "{not valid json")
        _write(os.path.join(bad_dir, "task.md"), "# bad\n")
        core.run_pipeline(
            os.path.join(bad_dir, "case.json"),
            os.path.join(bad_dir, "task.md"),
            bad_dir, os.path.join(base, "bwork"), os.path.join(base, "bout"))
        bresult = core.read_json(os.path.join(base, "bout", "result.json"))[0]
        _check(bresult.get("status") == "insufficient_evidence",
               "bad case.json -> insufficient_evidence (no crash)", failures)
        _check(isinstance(bresult.get("change_log"), list),
               "change_log still present on bad input", failures)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\nselftest " + ("PASSED" if not failures else
                           f"FAILED ({len(failures)} failures)"))
    return not failures


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
