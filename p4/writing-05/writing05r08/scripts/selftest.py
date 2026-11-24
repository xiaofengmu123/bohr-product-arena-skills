#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — run the pipeline against synthetic inputs and assert invariants.

Standard library only. Exits 0 only when every check passes. Never fabricates
benchmark results: these are structural self-checks of the Skill package itself.
"""
import json
import os
import sys
import tempfile

from scaffold import (
    build_skeleton,
    cmd_scaffold,
    contract_keys,
    extract_case_id,
    extract_contract,
    load_json,
)
from main import cmd_all, cmd_assemble, cmd_validate

PASS = []


def check(name, condition, detail=""):
    PASS.append((name, bool(condition), detail))


def run_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        summary = cmd_scaffold(os.path.join(tmp, "missing", "case.json"), out, work)
        result = load_json(os.path.join(out, "result.json"))
        check("empty input writes legal result", result is not None)
        check("empty input status input_error",
              result is not None and result.get("status") == "input_error")
        check("empty input lists errors",
              result is not None and isinstance(result.get("errors"), list)
              and len(result["errors"]) > 0)


def run_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        case = os.path.join(tmp, "case.json")
        with open(case, "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "x", "output_contract": {"deliverable": ')
        summary = cmd_scaffold(case, out, work)
        result = load_json(os.path.join(out, "result.json"))
        check("bad json salvaged (result exists)", result is not None)
        check("bad json not crash",
              result is not None and result.get("status") in ("input_error", "in_progress"))


def run_normal_scaffold():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        case = os.path.join(tmp, "case.json")
        with open(case, "w", encoding="utf-8") as fh:
            json.dump({
                "case_id": "demo-1",
                "output_contract": {"deliverable": None, "sources": [], "change_log": []},
            }, fh)
        summary = cmd_scaffold(case, out, work)
        result = load_json(os.path.join(out, "result.json"))
        check("scaffold case_id", result.get("case_id") == "demo-1")
        check("scaffold status in_progress", result.get("status") == "in_progress")
        check("scaffold echoes contract keys",
              all(k in result for k in ("deliverable", "sources", "change_log")))
        check("scaffold adds required fields",
              all(k in result for k in ("uncertainty", "pending_human", "limitations")))
        # idempotent: second run must not overwrite a non-in_progress result
        result["status"] = "partial"
        from scaffold import write_json
        write_json(os.path.join(out, "result.json"), result)
        summary2 = cmd_scaffold(case, out, work)
        check("scaffold preserves non-in_progress", summary2.get("skipped") is True)


def run_assemble_and_validate():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(out, exist_ok=True)
        os.makedirs(work, exist_ok=True)
        case = os.path.join(tmp, "case.json")
        with open(case, "w", encoding="utf-8") as fh:
            json.dump({
                "case_id": "demo-2",
                "output_contract": {"deliverable": None, "sources": [], "change_log": []},
            }, fh)
        cmd_scaffold(case, out, work)
        with open(os.path.join(work, "claims.json"), "w", encoding="utf-8") as fh:
            json.dump([{
                "claim_id": "C1",
                "text": "干预组结局优于对照组。",
                "claim_type": "interpretation",
                "evidence_strength": "moderate",
                "strength_reason": "RCT 但样本量小",
                "uncertainty": "应限定为'本研究样本中'",
                "sources": ["S1"],
                "needs_human": False,
            }], fh, ensure_ascii=False)
        with open(os.path.join(work, "evidence.json"), "w", encoding="utf-8") as fh:
            json.dump([{
                "source_id": "S1",
                "source_doc": "paper.pdf",
                "source_type": "original_study",
                "metadata": {"title": "Demo study"},
                "locator": "S3.P2.s1",
                "quote": "The intervention group improved significantly.",
                "claim_ids": ["C1"],
                "support": "supports",
                "verification": "verified",
            }], fh, ensure_ascii=False)
        with open(os.path.join(work, "deliverable.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo\n\nIntervention improved [C1].\n")
        summary = cmd_assemble(case, work, out)
        result = load_json(os.path.join(out, "result.json"))
        check("assemble status complete", result.get("status") == "complete")
        check("assemble deliverable materialized",
              os.path.exists(os.path.join(out, "deliverable.md")))
        validation = cmd_validate(out)
        check("validate passes on complete result", validation.get("ok") is True,
              detail=str(validation.get("issues")))

        # missing material -> partial/insufficient, not complete
        with tempfile.TemporaryDirectory() as tmp2:
            out2 = os.path.join(tmp2, "output")
            work2 = os.path.join(tmp2, "work")
            os.makedirs(out2, exist_ok=True)
            os.makedirs(work2, exist_ok=True)
            cmd_scaffold(case, out2, work2)
            cmd_assemble(case, work2, out2)
            result2 = load_json(os.path.join(out2, "result.json"))
            check("no material not complete", result2.get("status") != "complete",
                  detail="status=%s" % result2.get("status"))


def run_helpers():
    data = {"caseId": "abc", "outputContract": {"properties": {"deliverable": {}}}}
    check("extract_case_id priority", extract_case_id(data) == "abc")
    check("extract_contract nested", isinstance(extract_contract(data), dict))
    check("contract_keys schema", "deliverable" in contract_keys(extract_contract(data)))
    skeleton = build_skeleton("x", ["foo"], status="in_progress")
    check("build_skeleton unknown key null", skeleton.get("foo") is None)
    check("build_skeleton required fields",
          all(k in skeleton for k in ("sources", "uncertainty", "change_log", "limitations")))


def main():
    run_empty_input()
    run_bad_json()
    run_normal_scaffold()
    run_assemble_and_validate()
    run_helpers()

    failures = [item for item in PASS if not item[1]]
    for name, ok, detail in PASS:
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("---")
    print("%d checks, %d failed" % (len(PASS), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
