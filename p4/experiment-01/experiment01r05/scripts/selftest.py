"""selftest.py - self-check for the experiment-plan-optimizer package.

Run from anywhere; uses only the Python standard library. Verifies the
SKILL.md frontmatter and exercises probe / scaffold / validate on empty,
malformed and contract-bearing inputs without spawning subprocesses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print("  PASS %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s %s" % (name, detail))


def ns(input_dir, output_dir, work_dir, patch=None):
    return argparse.Namespace(input=input_dir, output=output_dir, work=work_dir, patch=patch)


def normalize_result(obj):
    """Strip the only intentionally non-deterministic fields."""
    out = json.loads(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    for entry in out.get("change_log", []):
        entry.pop("ts", None)
    out.pop("runtime", None)
    out.pop("contract_coverage", None)
    return out


def test_frontmatter():
    print("== frontmatter ==")
    path = os.path.join(ROOT, "SKILL.md")
    check("SKILL.md exists", os.path.isfile(path))
    text = open(path, "r", encoding="utf-8").read()
    check("frontmatter has name", "name:" in text)
    check("frontmatter has description", "description:" in text)
    check("frontmatter has version", "version:" in text)
    for f in ("scripts/main.py", "scripts/core_impl.py"):
        check(f + " exists", os.path.isfile(os.path.join(ROOT, f)))


def test_empty_input():
    print("== empty input ==")
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in"); os.makedirs(inp)
        out = os.path.join(base, "out"); os.makedirs(out)
        work = os.path.join(base, "work"); os.makedirs(work)
        probe = core_impl.scan_input(inp, work)
        check("empty probe case_id unknown", probe["case_id"] == "unknown")
        rc = core_impl.cmd_scaffold(ns(inp, out, work))
        check("scaffold exit 0", rc == 0)
        result = core_impl.load_json(os.path.join(out, "result.json"))
        check("empty status insufficient_evidence", result and result["status"] == "insufficient_evidence")
        rc2 = core_impl.cmd_validate(ns(inp, out, work))
        check("validate usable (0)", rc2 == 0, "rc=%r" % rc2)


def test_bad_json():
    print("== bad json ==")
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in"); os.makedirs(inp)
        out = os.path.join(base, "out"); os.makedirs(out)
        work = os.path.join(base, "work"); os.makedirs(work)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ")
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# t\n- challenge_id: `ch-x`\n")
        probe = core_impl.scan_input(inp, work)
        check("bad json recorded", len(probe["errors"]) >= 1 or len(probe["degradations"]) >= 1)
        rc = core_impl.cmd_scaffold(ns(inp, out, work))
        check("scaffold does not crash", rc in (0, 1))
        result = core_impl.load_json(os.path.join(out, "result.json"))
        check("minimal result present", result is not None and result.get("case_id") == "ch-x")


def test_contract_completeness():
    print("== contract completeness ==")
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in"); os.makedirs(inp)
        out = os.path.join(base, "out"); os.makedirs(out)
        work = os.path.join(base, "work"); os.makedirs(work)
        contract = {
            "plan": {"hypotheses_and_variables": {}, "controls": [], "parameter_ranges": []},
            "evidence_and_risks": {"evidence_map": [], "risks": []},
            "confirmations": [],
        }
        case = {"case_id": "demo-001", "goal": "评估某抑制剂对某细胞系增殖的影响",
                "output_contract": contract}
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        rc = core_impl.cmd_scaffold(ns(inp, out, work))
        check("scaffold exit 0", rc == 0)
        result = core_impl.load_json(os.path.join(out, "result.json"))
        paths = ["plan/hypotheses_and_variables", "plan/controls", "plan/parameter_ranges",
                 "evidence_and_risks/evidence_map", "evidence_and_risks/risks", "confirmations"]
        present = all(_get(result, p) is not None for p in paths)
        check("all contract keys present", present)
        check("coverage map exists", isinstance(result.get("contract_coverage"), dict))
        check("change_log non-empty", isinstance(result.get("change_log"), list) and result["change_log"])

        # patch a nested value, then validate coverage flips to filled
        patch = {"plan": {"hypotheses_and_variables": {"hypothesis": "H1", "iv": "dose", "unit": "uM"}}}
        pfile = os.path.join(work, "patch.json")
        json.dump(patch, open(pfile, "w", encoding="utf-8"), ensure_ascii=False)
        core_impl.cmd_patch(ns(inp, out, work, patch=pfile))
        result = core_impl.load_json(os.path.join(out, "result.json"))
        check("patch merged", result["plan"]["hypotheses_and_variables"].get("hypothesis") == "H1")
        rc2 = core_impl.cmd_validate(ns(inp, out, work))
        check("validate usable (0) with warnings", rc2 == 0, "rc=%r" % rc2)


def test_arithmetic_and_invariants():
    print("== arithmetic and invariants ==")
    check("safe_eval 1+2*3", abs(core_impl.safe_eval("1+2*3") - 7) < 1e-9)
    check("safe_eval (10-2)/2", abs(core_impl.safe_eval("(10-2)/2") - 4.0) < 1e-9)
    payload = {
        "parameter_ranges": [{"name": "temp", "min": 40, "max": 30, "unit": "C"}],
        "arithmetic_checks": [
            {"label": "sum", "expression": "1200+800", "expected": 2000},
            {"label": "bad", "expression": "2*3", "expected": 5},
        ],
    }
    issues = []
    core_impl.check_numeric_invariants(payload, issues)
    err_paths = {i["path"] for i in issues if i["level"] == "error"}
    check("min>max flagged", "parameter_ranges[0]" in err_paths)
    check("bad arithmetic flagged", any(p.startswith("arithmetic_checks[1]") for p in err_paths))
    check("good arithmetic not flagged", not any(p.startswith("arithmetic_checks[0]") for p in err_paths))


def test_reproducibility():
    print("== reproducibility ==")
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in"); os.makedirs(inp)
        work = os.path.join(base, "work"); os.makedirs(work)
        contract = {"plan": {"controls": []}, "confirmations": []}
        case = {"case_id": "rep-001", "output_contract": contract}
        json.dump(case, open(os.path.join(inp, "case.json"), "w", encoding="utf-8"), ensure_ascii=False)
        out1 = os.path.join(base, "out1"); os.makedirs(out1)
        out2 = os.path.join(base, "out2"); os.makedirs(out2)
        core_impl.cmd_scaffold(ns(inp, out1, work))
        core_impl.cmd_scaffold(ns(inp, out2, work))
        r1 = normalize_result(core_impl.load_json(os.path.join(out1, "result.json")))
        r2 = normalize_result(core_impl.load_json(os.path.join(out2, "result.json")))
        check("deterministic projection equal", r1 == r2)


def _get(result, path):
    node = result
    for part in path.split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def main():
    print("experiment-plan-optimizer selftest")
    test_frontmatter()
    test_empty_input()
    test_bad_json()
    test_contract_completeness()
    test_arithmetic_and_invariants()
    test_reproducibility()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
