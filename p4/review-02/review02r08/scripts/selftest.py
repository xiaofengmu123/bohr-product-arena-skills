# -*- coding: utf-8 -*-
"""selftest.py -- validate result.json against the contract.

usage: selftest.py [--input DIR] [--output DIR] [--work DIR] [--help]

Checks: JSON parse, case_id, status/verdict enums, output_contract echo,
finding required keys, severity/category enums, counts.total == len(findings),
and verbatim-quote presence in extracted text. Also runs three degradation
scenarios (empty input, bad case.json, missing input) to prove the scaffold
never crashes and always lands an honest insufficient/partial result.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _run_scenario(label, input_dir, output_dir, work_dir):
    try:
        m = core_impl.discover(input_dir, output_dir, work_dir)
        r = core_impl.scaffold(input_dir, output_dir, work_dir)
        core_impl.finalize(input_dir, output_dir, work_dir)
        ok, errs = core_impl.validate_result(
            os.path.join(output_dir, "result.json"), input_dir, work_dir)
        return ok, r.get("status"), r.get("summary", {}).get("verdict"), errs
    except Exception as exc:  # noqa: BLE001 -- must never crash the harness
        return False, "input_error", "unchecked", ["%s crashed: %r" % (label, exc)]


def main(argv):
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(
            "usage: selftest.py [--input DIR] [--output DIR] [--work DIR] [--help]\n")
        return 0

    input_dir, output_dir, work_dir, _ = core_impl.parse_common_args(argv)

    problems = []
    # 1) validate the real run if a result.json exists.
    real_result = os.path.join(output_dir, "result.json")
    if os.path.exists(real_result):
        ok, errs = core_impl.validate_result(real_result, input_dir, work_dir)
        if not ok:
            problems.extend("result: " + e for e in errs)
    else:
        problems.append("result.json not found under %s" % output_dir)

    # 2) degradation scenarios in a throwaway temp dir.
    tmp_root = os.path.join(
        work_dir, "_selftest_" + str(int(core_impl.time.time())))
    scenarios = [
        ("empty_input", os.path.join(tmp_root, "empty")),
        ("bad_case_json", os.path.join(tmp_root, "bad")),
        ("missing_input", os.path.join(tmp_root, "missing")),
    ]
    core_impl.ensure_dir(os.path.join(tmp_root, "empty"))
    bad_dir = os.path.join(tmp_root, "bad")
    core_impl.ensure_dir(bad_dir)
    with open(os.path.join(bad_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")

    report = []
    for label, d in scenarios:
        out = os.path.join(tmp_root, label + "_out")
        wk = os.path.join(tmp_root, label + "_work")
        ok, status, verdict, errs = _run_scenario(label, d, out, wk)
        report.append({"scenario": label, "ok": ok, "status": status,
                       "verdict": verdict, "errors": errs})

    json_path = os.path.join(work_dir, "selftest_report.json")
    core_impl.write_json(json_path, {"real_result_problems": problems,
                                     "scenarios": report})

    all_ok = not problems and all(r["ok"] for r in report)
    print(json.dumps({"ok": all_ok, "real_result_problems": problems,
                      "scenarios": report}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
