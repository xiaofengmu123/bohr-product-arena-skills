"""selftest.py — end-to-end self-test for the Skill (stdlib only).

Builds a synthetic dataset in a temporary directory, runs the full pipeline
(init -> survey -> inspect -> analyze -> build -> selfcheck), then verifies:

  1. result.json is valid JSON with case_id + status + envelope keys;
  2. output_contract fields are echoed (present, null-filled);
  3. SVG figures parse as XML;
  4. known statistics are correct (ANOVA on separated groups -> small p);
  5. two runs over identical input produce identical bytes (determinism);
  6. fallback on empty input returns status=insufficient_evidence and exit 0.

Usage:  python scripts/selftest.py
Exit code 0 = pass, 1 = fail.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"


def run(cmd, cwd):
    return subprocess.run([sys.executable, str(MAIN)] + cmd, cwd=str(cwd),
                          capture_output=True, text=True)


def make_input(root):
    inp = root / "input"
    inp.mkdir(parents=True)
    # case.json with an explicit output_contract
    case = {
        "case_id": "selftest-case-001",
        "task_instruction": "compare two synthetic groups",
        "output_contract": {
            "result": None, "quality": None, "descriptive": None,
            "inferential": None, "effects": None, "interpretation": None,
            "summary_table": None,
        },
    }
    (inp / "case.json").write_text(json.dumps(case), encoding="utf-8")
    # task.md
    (inp / "task.md").write_text("# synthetic\nCompare groups.", encoding="utf-8")
    # data.csv: two clearly separated groups + a duplicate + a missing + an outlier
    rows = [
        ["group", "value"],
        ["A", "1.0"], ["A", "2.0"], ["A", "3.0"], ["A", "4.0"],
        ["A", "5.0"], ["A", "6.0"], ["A", "7.0"], ["A", "8.0"],
        ["B", "21.0"], ["B", "22.0"], ["B", "23.0"], ["B", "24.0"],
        ["B", "25.0"], ["B", "26.0"], ["B", "27.0"], ["B", "28.0"],
        ["B", "28.0"],          # duplicate of last B row
        ["A", ""],              # missing value
        ["A", "0.0"],           # outlier (still inside A's low region)
    ]
    with open(inp / "data.csv", "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return inp


def main():
    failures = []
    checks_run = [0]
    root = Path(tempfile.mkdtemp(prefix="skill_selftest_"))
    inp = make_input(root)
    work = root / "work"
    out = root / "output"

    def check(name, cond):
        checks_run[0] += 1
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            failures.append(name)

    # pipeline
    for cmd in (["init"], ["survey"], ["inspect"], ["analyze"], ["build"], ["selfcheck"]):
        r = run(cmd + ["--input", str(inp), "--output", str(out), "--work", str(work)], HERE)
        check("command %s exit 0" % cmd[0], r.returncode == 0)
        if r.returncode != 0:
            print(r.stdout, r.stderr)

    result_path = out / "result.json"
    check("result.json exists", result_path.exists())
    result = json.loads(result_path.read_text(encoding="utf-8"))
    check("case_id preserved", result.get("case_id") == "selftest-case-001")
    check("status present", result.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"})
    for k in ("sources", "uncertainty", "change_log"):
        check("envelope key %s" % k, k in result)
    check("output_contract echoed (result key)", "result" in result)
    check("output_contract echoed (summary_table key)", "summary_table" in result)

    # figures parse
    svgs = list((out / "figures").glob("*.svg"))
    check("at least one SVG", len(svgs) > 0)
    for svg in svgs:
        try:
            ET.parse(str(svg))
            check("SVG parses %s" % svg.name, True)
        except Exception as exc:  # noqa: BLE001
            check("SVG parses %s" % svg.name, False)
            print("  ", exc)

    # analysis.json statistics sanity: chosen test should have small p
    analysis = json.loads((work / "analysis.json").read_text(encoding="utf-8"))
    results = analysis.get("results", [])
    check("analysis has results", isinstance(results, list) and len(results) > 0)
    if results:
        r0 = results[0]
        test = r0.get("test") or {}
        check("two-group test p small", test.get("p") is not None and test["p"] < 0.05)

    # determinism: re-run analyze+selfcheck and compare bytes
    out2 = root / "output2"
    for cmd in (["analyze"], ["build"]):
        run(cmd + ["--input", str(inp), "--output", str(out2), "--work", str(work)], HERE)
    b1 = (out / "result.json").read_bytes()
    b2 = (out2 / "result.json").read_bytes()
    check("deterministic result.json", b1 == b2)

    # empty input fallback
    empty = root / "empty"
    empty.mkdir()
    r = run(["init", "--input", str(empty), "--output", str(root / "empty_out"),
             "--work", str(root / "empty_work")], HERE)
    check("empty init exit 0", r.returncode == 0)
    r = run(["analyze", "--input", str(empty), "--output", str(root / "empty_out"),
             "--work", str(root / "empty_work")], HERE)
    check("empty analyze exit 0", r.returncode == 0)
    empty_result = json.loads((root / "empty_out" / "result.json").read_text(encoding="utf-8"))
    check("empty -> insufficient_evidence", empty_result.get("status") == "insufficient_evidence")

    shutil.rmtree(root, ignore_errors=True)
    if failures:
        print("\nselftest FAILED:", failures)
        return 1
    print("\nselftest OK (%d checks)" % checks_run[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
