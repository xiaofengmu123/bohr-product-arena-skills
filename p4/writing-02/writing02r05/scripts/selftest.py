# -*- coding: utf-8 -*-
"""Self-test harness for the Skill package.

Exercises the deterministic checks and the full scaffold -> verify ->
finalize -> selfcheck pipeline against synthetic inputs, including the
degradation scenarios required by the runtime contract:

* empty input directory      -> insufficient_evidence, exit code 0
* unreadable case.json       -> degradation recorded, pipeline continues
* no output_contract         -> default contract used
* contract with extra keys   -> all keys mirrored, none dropped
* numbers/citations changed  -> verify reports errors/warnings

Run from anywhere:

    python3 -B scripts/selftest.py

Exit code 0 means all assertions passed; 1 means a failure was found.
Standard library only.
"""

import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks
import main

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def make_input(root, case=None, task="", files=None):
    os.makedirs(root, exist_ok=True)
    if case is not None:
        with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
    if task:
        with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
            f.write(task)
    for rel, content in (files or {}).items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)


def run_cli(*argv):
    return main.main(list(argv))


# ---------------------------------------------------------------------------
# 1. Pure checks
# ---------------------------------------------------------------------------

def test_checks():
    print("[checks] pure functions")
    check("extract_numbers", checks.extract_numbers("3.5 mg and 1,234 x 0.05")
          == ["0.05", "1234", "3.5"], str(checks.extract_numbers("3.5 mg and 1,234 x 0.05")))
    check("extract_citations", checks.extract_citations("see [1, 2] and [3-5]")
          == ["[1,2]", "[3-5]"])
    check("extract_figrefs", checks.extract_figrefs("Fig. 1 and Table 2 and Eq. (3)")
          == ["Eq.(3)", "Fig.1", "Table2"],
          str(checks.extract_figrefs("Fig. 1 and Table 2 and Eq. (3)")))

    cons = checks.check_conservation(
        "The rate was 3.5 mg [1] (Fig. 2).",
        "The rate was 3.5 mg (Fig. 2).")
    check("lost citation flagged", any("citation" in e for e in cons["errors"]),
          str(cons))
    cons2 = checks.check_conservation("rate 3.5 mg", "rate 4.0 mg")
    check("changed number flagged",
          any("number" in e for e in cons2["errors"])
          and any("number" in w for w in cons2["warnings"]), str(cons2))

    hed = checks.check_hedge("may suggest", "definitely proves")
    check("hedge removal flagged", any("weak" in w for w in hed["warnings"]), str(hed))
    check("strong addition flagged", any("strong" in w for w in hed["warnings"]), str(hed))


# ---------------------------------------------------------------------------
# 2. Contract mirroring
# ---------------------------------------------------------------------------

def test_contract():
    print("[checks] contract mirroring")
    contract = {
        "deliverable": "x.md",
        "fact_source_map": [{"fact": "..", "locator": "..", "quote": ".."}],
        "version_boundary": [{"type": "diff"}],
        "extra_custom_field": {"nested": "example"},
    }
    result = main.build_result(contract, "case-1", "partial")
    check("all contract keys present",
          checks.check_contract(result, contract)["errors"] == [],
          str(checks.check_contract(result, contract)))
    check("nested custom key neutralized", result["extra_custom_field"]["nested"] is None)
    check("envelope present", all(k in result for k in main.REQUIRED_ENVELOPE))

    result2 = dict(result)
    del result2["extra_custom_field"]
    errs = checks.check_contract(result2, contract)["errors"]
    check("missing key reported", any("extra_custom_field" in e for e in errs), str(errs))

    main.append_change_log(result2, "scaffold", "status", None, "partial", "test")
    main.append_change_log(result2, "finalize", "status", "partial", "partial", "test")
    check("change log valid", checks.check_change_log(result2)["errors"] == [],
          str(checks.check_change_log(result2)))


# ---------------------------------------------------------------------------
# 3. Pipeline scenarios
# ---------------------------------------------------------------------------

def test_pipeline():
    print("[pipeline] full round trip")
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        case = {
            "id": "case-1",
            "output_contract": {
                "deliverable": "manuscript.md",
                "fact_source_map": [{"fact": "x", "locator": "y", "quote": "z"}],
                "version_boundary": [],
            },
        }
        draft = ("The rate was 3.5 mg [1]. This may suggest a trend.\n"
                 "Fig. 2 shows the result.")
        make_input(inp, case=case, task="polish this", files={"draft.md": draft})

        rc = run_cli("--input", inp, "--output", out, "--work", work, "scaffold")
        check("scaffold exit 0", rc == 0)
        res = main.read_json(os.path.join(out, "result.json"))
        check("partial result exists", isinstance(res, dict) and res.get("status") == "partial")
        check("contract mirrored", "deliverable" in res and "fact_source_map" in res)

        # model fills edits
        edits = {
            "target_lang": "en", "genre": "paper",
            "units": [{"id": 1, "original": draft, "revised": draft.replace("may suggest", "may suggest"),
                       "edit_type": "grammar", "reason": "keep hedge", "evidence": {}}],
        }
        main.write_json(os.path.join(work, "edits.json"), edits)
        rc = run_cli("--input", inp, "--output", out, "--work", work, "verify")
        check("verify exit 0", rc == 0)
        report = main.read_json(os.path.join(work, "check_report.json"))
        check("verify produced report", isinstance(report, dict) and "errors" in report)

        rc = run_cli("--input", inp, "--output", out, "--work", work, "finalize")
        check("finalize exit 0", rc == 0)
        check("manuscript exists", os.path.isfile(os.path.join(out, "manuscript.md")))
        res = main.read_json(os.path.join(out, "result.json"))
        check("deliverable set", res.get("deliverable") == "manuscript.md")
        check("change log has entries", len(res.get("change_log", [])) >= 2)

        rc = run_cli("--input", inp, "--output", out, "--work", work, "selfcheck")
        check("selfcheck exit 0", rc == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_input():
    print("[pipeline] empty input directory")
    tmp = tempfile.mkdtemp(prefix="skill-empty-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        rc = run_cli("--input", inp, "--output", out, "--work", work, "scaffold")
        check("empty input exit 0", rc == 0)
        res = main.read_json(os.path.join(out, "result.json"))
        check("insufficient_evidence", isinstance(res, dict)
              and res.get("status") == "insufficient_evidence", str(res))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    print("[pipeline] unreadable case.json")
    tmp = tempfile.mkdtemp(prefix="skill-badjson-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
        with open(os.path.join(inp, "draft.md"), "w", encoding="utf-8") as f:
            f.write("A draft with 2.5 mg of substance.")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        rc = run_cli("--input", inp, "--output", out, "--work", work, "scaffold")
        check("bad json exit 0", rc == 0)
        res = main.read_json(os.path.join(out, "result.json"))
        check("degradation recorded", isinstance(res, dict)
              and any("unreadable" in d for d in res.get("degradations", [])), str(res))
        check("draft still found", isinstance(res, dict) and res.get("status") == "partial")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main_selftest():
    print("=== selftest start ===")
    try:
        test_checks()
        test_contract()
        test_pipeline()
        test_empty_input()
        test_bad_json()
    except Exception:
        traceback.print_exc()
        global FAIL
        FAIL += 1
    print(f"=== selftest done: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_selftest())
