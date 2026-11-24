# -*- coding: utf-8 -*-
"""Self-test for the academic-polish (lean) Skill.

Builds synthetic demo inputs under a temporary directory and runs the real
``scripts/main.py`` pipeline end to end. Three scenarios are exercised:

  A. empty input directory      -> insufficient_evidence, exit code 0
  B. corrupt case.json          -> errors recorded, still exits 0
  C. normal inline draft        -> scaffold -> fill edits -> verify -> finalize
                                   -> selfcheck, all exit 0 and report 0 errors

All demo text is synthetic and clearly labeled; no benchmark scores are
produced or asserted. Standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(cmd, inp, out, work, expect=0):
    proc = subprocess.run(
        [PY, "-B", MAIN, cmd, "--input", inp, "--output", out,
         "--work", work],
        capture_output=True, text=True, encoding="utf-8")
    ok = proc.returncode == expect
    return ok, proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def make_demo(base):
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    return inp, out, work


def scenario_empty(base):
    inp, out, work = make_demo(base)
    ok1, rc1, so1, se1 = run("scaffold", inp, out, work, 0)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    return (ok1 and res.get("status") == "insufficient_evidence"
            and bool(res.get("case_id"))), rc1, so1, se1, res.get("status")


def scenario_bad_json(base):
    inp, out, work = make_demo(base)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\n")
    ok, rc, so, se = run("scaffold", inp, out, work, 0)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    return (ok and isinstance(res.get("errors"), list)), rc, so, se, \
        res.get("status")


def scenario_normal(base):
    inp, out, work = make_demo(base)
    draft = ("The results shows that the treatment improves survival by 37.5% "
             "(p < 0.05, Fig. 1, [1,2]). It may indicate a causal effect, "
             "but the effect was not proven. 温度保持在 25 °C，浓度 3.2 mM。")
    case = {
        "id": "selftest-case-0001",
        "output_contract": {
            "deliverable": None,
            "fact_source_map": [],
            "version_boundary": [],
            "notes": None,
        },
        "text": draft,
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\nPolish preserving facts.\n")

    ok0, rc0, so0, se0 = run("scaffold", inp, out, work, 0)
    edits_path = os.path.join(work, "edits.json")
    edits = json.load(open(edits_path, encoding="utf-8"))
    # Simulate the model editing phase: fix subject-verb agreement only,
    # preserve every number/citation/hedge verbatim.
    for u in edits["units"]:
        rev = u["original"].replace("The results shows", "The results show")
        u["revised"] = rev
        u["edit_type"] = "grammar" if rev != u["original"] else "no_change"
        u["reason"] = "subject-verb agreement" if rev != u["original"] \
            else "already correct"
        if u["facts"]:
            u["evidence"] = [{"locator": u["locator"], "quote": u["original"]}]
    json.dump(edits, open(edits_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    glossary = {"terms": [{"concept": "survival", "canonical": "survival",
                           "variants": ["survival rate"], "first_seen": "L1"}],
                "notes": []}
    json.dump(glossary, open(os.path.join(work, "glossary.json"), "w",
                             encoding="utf-8"), ensure_ascii=False)

    ok1, rc1, so1, se1 = run("verify", inp, out, work, 0)
    report = json.load(open(os.path.join(work, "check_report.json"),
                            encoding="utf-8"))
    ok2, rc2, so2, se2 = run("finalize", inp, out, work, 0)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok3, rc3, so3, se3 = run("selfcheck", inp, out, work, 0)
    contract_keys_ok = all(k in res for k in
                           ("deliverable", "fact_source_map",
                            "version_boundary", "notes"))
    return (ok0 and ok1 and ok2 and ok3
            and report["stats"]["errors"] == 0
            and res.get("status") == "ok" and contract_keys_ok), \
        (rc0, rc1, rc2, rc3), (so0, so1, so2, so3), (se0, se1, se2, se3), \
        report["stats"]


def main():
    base = tempfile.mkdtemp(prefix="polish-selftest-")
    results = []
    try:
        ok_a, rc_a, so_a, se_a, st_a = scenario_empty(base)
        ok_b, rc_b, so_b, se_b, st_b = scenario_bad_json(base)
        ok_c, rc_c, so_c, se_c, st_c = scenario_normal(base)
        results = [("empty-input", ok_a, st_a),
                   ("bad-json", ok_b, st_b),
                   ("normal", ok_c, st_c)]
        all_ok = all(r[1] for r in results)
        for name, ok, detail in results:
            print(f"  {name}: {'PASS' if ok else 'FAIL'} ({detail})")
        print("selftest:", "PASS" if all_ok else "FAIL")
        return 0 if all_ok else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
