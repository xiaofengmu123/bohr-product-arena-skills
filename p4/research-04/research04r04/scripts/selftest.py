# -*- coding: utf-8 -*-
"""selftest.py — self-check entry and degradation self-test.

Two modes:

1. Contract self-check (default, no --demo):
       python3 scripts/selftest.py [--output /app/output] [--work /app/work]
   Validates result.json against the output contract and prints every failing
   check.  Exit code 0 means no hard errors; warnings do not fail the run.

2. Degradation self-test (--demo):
       python3 scripts/selftest.py --demo
   Runs four fixtures in a temporary directory (empty input, malformed
   case.json, missing publication years, and a repeatability check) and proves
   the pipeline degrades honestly instead of fabricating data.  This mode
   writes nothing to /input or /app/output.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def _contract_check(output, work):
    result, note = core_impl.load_json(os.path.join(output, "result.json"))
    if result is None:
        return 1, "result.json unreadable: {0}".format(note)
    findings = core_impl.check_contract(
        result, materials_dir=os.path.join(work, "materials"))
    errors = [f for f in findings if f["level"] == "error" and not f["ok"]]
    warns = [f for f in findings if f["level"] == "warn" and not f["ok"]]
    core_impl.write_json(os.path.join(work, "selfcheck.json"),
                         {"ok": not errors, "errors": errors,
                          "warnings": warns, "findings": findings})
    for f in errors + warns:
        print("  [{0}] {1}: {2}".format(f["level"], f["check"], f["message"]))
    print("contract check: errors={0} warnings={1}".format(len(errors), len(warns)))
    return (0 if not errors else 1), ""


def _write_case(root, payload):
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def _demo():
    results = []
    base = tempfile.mkdtemp(prefix="rft-selftest-")
    try:
        # 1) empty input -> insufficient_evidence, no crash
        case_root = os.path.join(base, "empty")
        work = os.path.join(base, "work_empty")
        out = os.path.join(base, "out_empty")
        os.makedirs(case_root, exist_ok=True)
        core_impl.prepare(case_root, work, out)
        r, _ = core_impl.load_json(os.path.join(out, "result.json"))
        ok = bool(r) and r.get("status") == "insufficient_evidence" \
            and r.get("research_gaps", {}).get("gaps")
        results.append(("empty_input_degrades", ok,
                        "status={0}".format(r.get("status") if r else None)))

        # 2) malformed case.json -> recorded in errors, no crash
        case_root = os.path.join(base, "badjson")
        work = os.path.join(base, "work_badjson")
        out = os.path.join(base, "out_badjson")
        os.makedirs(case_root, exist_ok=True)
        with open(os.path.join(case_root, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not-valid-json")
        with open(os.path.join(case_root, "mat_a.txt"), "w", encoding="utf-8") as fh:
            fh.write("A study in 2020.\n")
        core_impl.prepare(case_root, work, out)
        core_impl.analyze(work, out)
        r, _ = core_impl.load_json(os.path.join(out, "result.json"))
        ok = bool(r) and any(e.get("component") == "case.json"
                             for e in r.get("errors", []))
        results.append(("bad_json_recorded", ok, "errors recorded"))

        # 3) missing years -> year null + pending_human, no guessing
        case_root = os.path.join(base, "noyear")
        work = os.path.join(base, "work_noyear")
        out = os.path.join(base, "out_noyear")
        os.makedirs(case_root, exist_ok=True)
        _write_case(case_root, {"case_id": "fixture-noyear", "output_contract": {}})
        with open(os.path.join(case_root, "mat_a.txt"), "w", encoding="utf-8") as fh:
            fh.write("This material has no publication year anywhere.\n")
        with open(os.path.join(case_root, "mat_b.txt"), "w", encoding="utf-8") as fh:
            fh.write("Another material without a year.\n")
        core_impl.prepare(case_root, work, out)
        core_impl.analyze(work, out)
        r, _ = core_impl.load_json(os.path.join(out, "result.json"))
        dist = r.get("time_trend", {}).get("distribution", [])
        pending = [p for p in r.get("pending_human", [])
                   if "年份" in (p.get("item") or "")]
        ok = bool(dist == [] and pending)
        results.append(("missing_year_honest", ok,
                        "distribution={0} pending={1}".format(len(dist), len(pending))))

        # 4) repeatability: same input twice -> identical machine result
        case_root = os.path.join(base, "repeat")
        os.makedirs(case_root, exist_ok=True)
        _write_case(case_root, {"case_id": "fixture-repeat", "output_contract": {}})
        with open(os.path.join(case_root, "mat_a.txt"), "w", encoding="utf-8") as fh:
            fh.write("Title A. 2018. Future work remains.\nHowever a conflict.\n")
        with open(os.path.join(case_root, "mat_b.txt"), "w", encoding="utf-8") as fh:
            fh.write("Title B. 2020. We propose a novel method.\n")
        r1 = _run_pipeline(case_root, os.path.join(base, "work_r1"),
                           os.path.join(base, "out_r1"))
        r2 = _run_pipeline(case_root, os.path.join(base, "work_r2"),
                           os.path.join(base, "out_r2"))
        ok = core_impl.digest(r1) == core_impl.digest(r2) and r1 is not None
        results.append(("repeatable_output", ok, "digest stable"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("=== degradation self-test ===")
    all_ok = True
    for name, ok, note in results:
        all_ok = all_ok and ok
        print("  [{0}] {1}: {2}".format("PASS" if ok else "FAIL", name, note))
    print("selftest {0}".format("passed" if all_ok else "failed"))
    return 0 if all_ok else 1


def _run_pipeline(case_root, work, out):
    core_impl.prepare(case_root, work, out)
    core_impl.analyze(work, out)
    r, _ = core_impl.load_json(os.path.join(out, "result.json"))
    return r


def main(argv=None):
    p = argparse.ArgumentParser(prog="selftest.py")
    p.add_argument("--demo", action="store_true",
                   help="run degradation fixtures in a temp dir")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)

    if args.demo:
        return _demo()
    rc, msg = _contract_check(args.output, args.work)
    if msg:
        print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
