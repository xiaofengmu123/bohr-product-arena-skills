#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test and determinism check for the audit Skill package.

Runs entirely with the Python standard library. Verifies:

1. ``main.py --help`` exits 0.
2. discover + scaffold + finalize on a synthetic fixture yields a valid
   ``result.json`` with case_id, status, sources, uncertainty, change_log.
3. Byte-determinism: ``finalize`` run twice on the same fixture produces
   byte-identical ``result.json`` and ``report.md`` (idempotent machine layer).
4. Empty input → honest ``insufficient_evidence``/``input_error`` status, exit 0.
5. Malformed ``case.json`` → errors recorded, no crash, exit 0.

Exit code is 0 when every check passes, 1 otherwise. This script writes only to
temporary directories; it does not touch the package deliverables.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PATH = os.path.join(HERE, "main.py")

FACETS = [
    "research_design",
    "variable_definition",
    "statistical_methods",
    "sample_size_power",
    "results_reporting",
]


def load_main():
    spec = importlib.util.spec_from_file_location("audit_main", MAIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(mod, argv):
    try:
        code = mod.main(list(argv))
        return code if code is not None else 0
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0


def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_fixture(base):
    inp = os.path.join(base, "input")
    work = os.path.join(base, "work")
    out = os.path.join(base, "output")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    contract = {
        "required_fields": ["case_id", "status", "findings"],
        "task": "research-methods-and-statistical-review",
        "extra_unknown_key": {"keep": ["verbatim", 1, None]},
    }
    write_file(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "selftest-case-001",
        "output_contract": contract,
        "note": "synthetic fixture, not a formal test case",
    }, ensure_ascii=False))
    write_file(os.path.join(inp, "task.md"),
               "# 自检任务\n\n审查研究设计、变量定义、统计方法、样本量与结果报告。\n")
    write_file(os.path.join(inp, "materials", "paper.txt"),
               "## Methods\n\nA randomized trial compared two groups.\n\n"
               "Sample size N = 120 was planned.\n\n"
               "We used a t-test and report p = 0.06.\n\n"
               "## Results\n\n"
               "The difference was significant (p = 0.06).\n\n"
               "The 95% CI was 0.9 to 1.1.\n\n"
               "A total of n = 120 patients were included.\n")
    return inp, work, out


def write_finding(work):
    finding = {
        "id": "F001",
        "facet": "results_reporting",
        "check_class": "statistical_reporting",
        "finding_type": "factual_error",
        "severity": "major",
        "locator": "materials/paper.txt ¶6",
        "quote": "The difference was significant (p = 0.06).",
        "basis": "p=0.06 不小于 0.05，与“significant”措辞矛盾。",
        "suggestion": "改为“未达到统计学显著水平”并复核结论。",
        "confidence": "high",
        "review_status": "confirmed",
    }
    write_file(os.path.join(work, "findings", "F001.json"),
               json.dumps(finding, ensure_ascii=False))


def check(ok, label, detail=""):
    print(("PASS " if ok else "FAIL ") + label + ((" :: " + detail) if detail else ""))
    return ok


def main():
    mod = load_main()
    results = []

    # 1. --help exits 0.
    code = run(mod, ["--help"])
    results.append(check(code == 0, "--help exits 0", f"code={code}"))

    base = tempfile.mkdtemp(prefix="audit_selftest_")
    try:
        # 2. Full pipeline on fixture.
        inp, work, out = make_fixture(base)
        write_finding(work)
        r1 = run(mod, ["discover", "--input", inp, "--work", work, "--output", out])
        r2 = run(mod, ["scaffold", "--input", inp, "--work", work, "--output", out])
        r3 = run(mod, ["finalize", "--input", inp, "--work", work, "--output", out])
        results.append(check(r1 == r2 == r3 == 0,
                             "pipeline discover/scaffold/finalize exit 0",
                             f"codes={r1},{r2},{r3}"))
        result = read_json(os.path.join(out, "result.json"))
        required = ["case_id", "status", "sources", "uncertainty", "change_log",
                    "summary", "evidence", "pending_human", "limitations"]
        missing = [k for k in required if k not in result]
        results.append(check(not missing, "result.json has required top-level keys",
                             f"missing={missing}"))
        results.append(check(result.get("case_id") == "selftest-case-001",
                             "case_id preserved"))
        contract_ok = (
            result.get("required_fields") == ["case_id", "status", "findings"]
            and result.get("task") == "research-methods-and-statistical-review"
            and result.get("extra_unknown_key") == {"keep": ["verbatim", 1, None]}
        )
        results.append(check(contract_ok, "output_contract fields backfilled verbatim"))
        findings = result.get("review_report", {}).get("findings", [])
        results.append(check(len(findings) == 1, "one finding merged", f"n={len(findings)}"))
        results.append(check(bool(result.get("numeric_checks")),
                             "numeric candidate checks produced"))

        # 3. Byte-determinism: finalize twice.
        first_result = read_bytes(os.path.join(out, "result.json"))
        first_report = read_bytes(os.path.join(out, "report.md"))
        run(mod, ["finalize", "--input", inp, "--work", work, "--output", out])
        second_result = read_bytes(os.path.join(out, "result.json"))
        second_report = read_bytes(os.path.join(out, "report.md"))
        results.append(check(first_result == second_result,
                             "result.json byte-identical across two finalize runs"))
        results.append(check(first_report == second_report,
                             "report.md byte-identical across two finalize runs"))

        # 4. Empty input → honest status, exit 0.
        empty = os.path.join(base, "empty_input")
        empty_work = os.path.join(base, "empty_work")
        empty_out = os.path.join(base, "empty_out")
        os.makedirs(empty, exist_ok=True)
        codes = [
            run(mod, ["discover", "--input", empty, "--work", empty_work, "--output", empty_out]),
            run(mod, ["scaffold", "--input", empty, "--work", empty_work, "--output", empty_out]),
            run(mod, ["finalize", "--input", empty, "--work", empty_work, "--output", empty_out]),
        ]
        empty_result = read_json(os.path.join(empty_out, "result.json"))
        results.append(check(all(c == 0 for c in codes), "empty input exits 0",
                             f"codes={codes}"))
        results.append(check(
            empty_result.get("status") in ("insufficient_evidence", "input_error"),
            "empty input → honest status", f"status={empty_result.get('status')}"))

        # 5. Malformed case.json → errors recorded, no crash.
        bad = os.path.join(base, "bad_input")
        bad_work = os.path.join(base, "bad_work")
        bad_out = os.path.join(base, "bad_out")
        os.makedirs(os.path.join(bad, "materials"), exist_ok=True)
        write_file(os.path.join(bad, "case.json"), "{ not valid json")
        write_file(os.path.join(bad, "task.md"), "# bad case\n")
        write_file(os.path.join(bad, "materials", "m.txt"), "text material\n")
        codes = [
            run(mod, ["discover", "--input", bad, "--work", bad_work, "--output", bad_out]),
            run(mod, ["scaffold", "--input", bad, "--work", bad_work, "--output", bad_out]),
            run(mod, ["finalize", "--input", bad, "--work", bad_work, "--output", bad_out]),
        ]
        bad_result = read_json(os.path.join(bad_out, "result.json"))
        results.append(check(all(c == 0 for c in codes), "malformed case.json exits 0",
                             f"codes={codes}"))
        results.append(check(bool(bad_result.get("errors")),
                             "malformed case.json recorded in errors"))

        # 6. Invalid finding file does not crash finalize.
        invalid_dir = os.path.join(work, "findings")
        write_file(os.path.join(invalid_dir, "Z999.json"),
                   json.dumps({"id": "Z999", "severity": "major"}))
        run(mod, ["finalize", "--input", inp, "--work", work, "--output", out])
        repaired = read_json(os.path.join(out, "result.json"))
        results.append(check(
            len(repaired.get("review_report", {}).get("findings", [])) == 1,
            "invalid finding dropped, valid finding kept"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = sum(0 if ok else 1 for ok in results)
    print(f"\nselftest: {len(results) - failed}/{len(results)} checks passed")
    return 0 if failed == 0 else 1


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


if __name__ == "__main__":
    sys.exit(main())
