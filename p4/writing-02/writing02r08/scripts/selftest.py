# -*- coding: utf-8 -*-
"""Self-test for the academic-polish-02 Skill package.

Runs the failure-mode scenarios required by the rubric's reliability
dimension, then a synthetic happy path, then checks that SKILL.md carries the
budget + first-3-minutes rule and that the rubric maps all five scoring
dimensions.  Standard library only; deterministic; exits non-zero on any
failure so it can gate a release.

Usage:
    python3 -B scripts/selftest.py [--skill-root /path/to/skill]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import checks  # noqa: E402


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )


def _read_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def case_empty(root):
    inp = os.path.join(root, "empty_in")
    out = os.path.join(root, "empty_out")
    work = os.path.join(root, "empty_work")
    os.makedirs(inp)
    r = _run(["scaffold", "--input", inp, "--output", out, "--work", work])
    res = _read_json(os.path.join(out, "result.json"))
    ok = (r.returncode == 0 and res.get("status")
          in ("insufficient_evidence", "input_error", "partial")
          and res.get("case_id"))
    return "empty input", ok, res.get("status")


def case_missing(root):
    inp = os.path.join(root, "no_such_dir")
    out = os.path.join(root, "missing_out")
    work = os.path.join(root, "missing_work")
    r = _run(["scaffold", "--input", inp, "--output", out, "--work", work])
    res = _read_json(os.path.join(out, "result.json"))
    ok = (r.returncode == 0 and res.get("status") == "input_error"
          and res.get("case_id"))
    return "missing input", ok, res.get("status")


def case_bad_json(root):
    inp = os.path.join(root, "bad_in")
    out = os.path.join(root, "bad_out")
    work = os.path.join(root, "bad_work")
    os.makedirs(inp)
    _write(os.path.join(inp, "case.json"), "{ not valid json")
    _write(os.path.join(inp, "task.md"), "# task\n")
    r = _run(["scaffold", "--input", inp, "--output", out, "--work", work])
    res = _read_json(os.path.join(out, "result.json"))
    ok = (r.returncode == 0 and res.get("status") in
          ("input_error", "partial") and res.get("case_id"))
    return "bad JSON", ok, res.get("status")


def case_happy(root):
    inp = os.path.join(root, "happy_in")
    out = os.path.join(root, "happy_out")
    work = os.path.join(root, "happy_work")
    os.makedirs(inp)
    case = {
        "id": "case-selftest-happy",
        "output_contract": {
            "deliverable": None,
            "fact_source_map": [],
            "version_boundary": [],
            "custom_extra": "kept",  # unknown key must survive
        },
    }
    _write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"),
           "# task\n润色以下学术文本，保持事实与限定强度。\n")

    original = ("Patients with heart failure may show elevated BNP levels of "
                "400 pg/mL. This finding suggests that BNP could indicate "
                "disease severity.")
    revised = ("Patients with heart failure may exhibit elevated BNP levels of "
               "400 pg/mL. This finding suggests that BNP could indicate "
               "disease severity.")

    r = _run(["scaffold", "--input", inp, "--output", out, "--work", work])
    if r.returncode != 0:
        return "happy scaffold", False, r.stderr[:200]

    edits = _read_json(os.path.join(work, "edits.json"))
    edits["target_lang"] = "en"
    edits["genre"] = "journal"
    edits["units"] = [{
        "id": "u0001",
        "locator": "materials/selftest.md#p1",
        "original": original,
        "revised": revised,
        "edit_type": "register",
        "reason": "替换 show->exhibit 贴合期刊语域，语义与限定词保持不变",
        "hedge_note": "may/suggests/could 三类弱限定词全部保留",
        "evidence": [{
            "fact": "BNP 水平 400 pg/mL",
            "locator": "materials/selftest.md#p1",
            "quote": "BNP levels of 400 pg/mL",
        }],
    }]
    _write(os.path.join(work, "edits.json"),
           json.dumps(edits, ensure_ascii=False))
    glossary = _read_json(os.path.join(work, "glossary.json"))
    glossary["terms"] = [{
        "concept": "脑钠肽", "canonical": "BNP",
        "variants": ["B-type natriuretic peptide", "B 型利钠肽"],
        "abbr": "BNP", "first_use_locator": "materials/selftest.md#p1",
    }]
    _write(os.path.join(work, "glossary.json"),
           json.dumps(glossary, ensure_ascii=False))

    r = _run(["verify", "--input", inp, "--output", out, "--work", work])
    report = _read_json(os.path.join(work, "check_report.json"))
    if report["counts"]["error"] != 0:
        return "happy verify", False, json.dumps(report["findings"][:3],
                                                  ensure_ascii=False)

    r = _run(["finalize", "--input", inp, "--output", out, "--work", work])
    res = _read_json(os.path.join(out, "result.json"))
    if res.get("status") != "ok":
        return "happy finalize", False, res.get("status")
    if res.get("custom_extra") != "kept":
        return "happy contract", False, "unknown contract key lost"

    r = _run(["selfcheck", "--input", inp, "--output", out, "--work", work])
    if r.returncode != 0:
        return "happy selfcheck", False, r.stdout + r.stderr
    return "happy path", True, res.get("status")


def case_bad_edit(root):
    """A dropped number/hedge must be caught by verify."""
    inp = os.path.join(root, "badedit_in")
    out = os.path.join(root, "badedit_out")
    work = os.path.join(root, "badedit_work")
    os.makedirs(inp)
    _write(os.path.join(inp, "case.json"),
           json.dumps({"id": "case-selftest-bad"}, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"), "# task\n")
    _run(["scaffold", "--input", inp, "--output", out, "--work", work])
    edits = _read_json(os.path.join(work, "edits.json"))
    edits["units"] = [{
        "id": "u0001", "locator": "x#p1",
        "original": "The dose was 400 pg/mL and this may suggest toxicity.",
        "revised": "The dose was measured and this demonstrates toxicity.",
        "edit_type": "grammar", "reason": "", "hedge_note": "",
        "evidence": [],
    }]
    _write(os.path.join(work, "edits.json"),
           json.dumps(edits, ensure_ascii=False))
    _run(["verify", "--input", inp, "--output", out, "--work", work])
    report = _read_json(os.path.join(work, "check_report.json"))
    has_num_err = any(f["check"] == "number_unit_conservation"
                      and f["level"] == "error" for f in report["findings"])
    has_hedge = any(f["check"] == "hedge_conservation"
                    and f["level"] == "warning" for f in report["findings"])
    ok = has_num_err and has_hedge
    return "bad edit caught", ok, ("num_err=%s hedge=%s" % (has_num_err, has_hedge))


def case_skill_md(root):
    md = os.path.join(root, "SKILL.md")
    if not os.path.isfile(md):
        return "SKILL.md", False, "missing"
    text = open(md, "r", encoding="utf-8").read()
    has_name = "\nname:" in text
    has_desc = "\ndescription:" in text
    has_ver = "\nversion:" in text
    has_budget = ("900" in text and "64" in text)
    has_3min = ("3" in text and ("分钟内" in text or "3 分钟" in text or "3 分" in text))
    ok = has_name and has_desc and has_ver and has_budget and has_3min
    return "SKILL.md frontmatter+budget", ok, (
        f"name={has_name} desc={has_desc} ver={has_ver} "
        f"budget={has_budget} 3min={has_3min}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-root", default=os.path.dirname(HERE))
    args = ap.parse_args()
    root = args.skill_root

    failures = []
    tmp = tempfile.mkdtemp(prefix="polish02_selftest_")
    try:
        for fn in (case_empty, case_missing, case_bad_json, case_happy,
                   case_bad_edit):
            name, ok, detail = fn(tmp)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
            if not ok:
                failures.append(name)
        name, ok, detail = case_skill_md(root)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failures.append(name)

        # rubric alignment must cover all five dimensions
        rubric = checks.rubric_alignment_report()
        dims = [r["dimension"] for r in checks.RUBRIC]
        if len(dims) == 5 and not any(f["level"] == "error" for f in rubric):
            print("[PASS] rubric alignment: 5/5 dimensions mapped")
        else:
            print("[FAIL] rubric alignment incomplete")
            failures.append("rubric alignment")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("\nSELFTEST FAIL:", ", ".join(failures))
        return 1
    print("\nSELFTEST OK (6 scenarios)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
