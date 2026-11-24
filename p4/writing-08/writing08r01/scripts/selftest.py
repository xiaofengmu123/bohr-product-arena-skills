"""selftest.py — package self-test for the methods-experiments-writing skill.

Runs the three failure scenarios required by the reliability checklist
(empty input, bad JSON, missing input) and verifies:

  1. SKILL.md has valid YAML frontmatter (name / description / version).
  2. scripts/main.py is runnable and every subcommand exits 0.
  3. skeleton always leaves a legal result.json behind (partial deliverable).
  4. result.json carries case_id, status and standard contract fields.

Exit code is 0 when all checks pass, 1 otherwise. Standard library only.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        with open(path, "r", encoding="latin-1") as fh:
            return fh.read()


def check_frontmatter():
    skill_md = os.path.join(PACKAGE_ROOT, "SKILL.md")
    if not os.path.exists(skill_md):
        print("FAIL: SKILL.md missing")
        return False
    text = read_text(skill_md)
    if not text.startswith("---"):
        print("FAIL: SKILL.md missing YAML frontmatter")
        return False
    head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    ok = all(tok in head for tok in ("name:", "description:", "version:"))
    print("SKILL.md frontmatter (name/description/version):", "OK" if ok else "FAIL")
    return ok


def run_cmd(args, cwd):
    proc = subprocess.run(
        [sys.executable, "-B"] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def scenario_setup(base, name, populate):
    ind = os.path.join(base, name, "input")
    os.makedirs(ind, exist_ok=True)
    populate(ind)
    return ind


def write_bad_json(ind):
    with open(os.path.join(ind, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{broken json")


def write_case_only(ind):
    with open(os.path.join(ind, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-no-material"}, fh)
    with open(os.path.join(ind, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest task, no material\n")


def write_good_case(ind):
    with open(os.path.join(ind, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-case", "output_contract": {"draft": "string"}}, fh)
    with open(os.path.join(ind, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest task\n")
    mat_dir = os.path.join(ind, "materials")
    os.makedirs(mat_dir, exist_ok=True)
    with open(os.path.join(mat_dir, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write("lr: 1e-4\nbatch_size: 32\nseed: 42\n")


def check_contract_fields(result):
    required = ["case_id", "status", "artifacts", "sources", "uncertainty", "change_log"]
    return all(k in result for k in required)


def main():
    tmp = tempfile.mkdtemp(prefix="mwskill_st_")
    failures = []

    if not check_frontmatter():
        failures.append("frontmatter")

    # 1) subcommand --help exits 0
    rc, out, err = run_cmd([MAIN, "--help"], HERE)
    if rc != 0:
        failures.append("main --help")
        print("FAIL: main.py --help rc=%d err=%s" % (rc, err.strip()))

    scenarios = [
        ("empty", lambda ind: None, "input_error"),
        ("badjson", write_bad_json, "input_error"),
        ("nomaterial", write_case_only, "insufficient_evidence"),
        ("missing", write_good_case, "partial"),
    ]

    for name, populate, expect in scenarios:
        ind = scenario_setup(tmp, name, populate)
        outdir = os.path.join(tmp, name, "output")
        workdir = os.path.join(tmp, name, "work")
        rc, stdout, stderr = run_cmd(
            [MAIN, "skeleton", "--input", ind, "--output", outdir, "--work", workdir],
            HERE,
        )
        result_path = os.path.join(outdir, "result.json")
        if rc != 0:
            failures.append(name + ":skeleton-rc")
            print("FAIL %s skeleton rc=%d err=%s" % (name, rc, stderr.strip()))
            continue
        if not os.path.exists(result_path):
            failures.append(name + ":no-result")
            print("FAIL %s no result.json" % name)
            continue
        with open(result_path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        if result.get("status") != expect:
            failures.append(name + ":status")
            print("FAIL %s status=%s expect=%s" % (name, result.get("status"), expect))
        if not check_contract_fields(result):
            failures.append(name + ":contract")
            print("FAIL %s missing contract fields" % name)
        print("%s skeleton: status=%s result.json=%s" % (
            name, result.get("status"), "OK" if os.path.exists(result_path) else "MISSING"))

    # 2) inventory + validate on the good case
    ind = os.path.join(tmp, "missing", "input")
    outdir = os.path.join(tmp, "missing", "output")
    workdir = os.path.join(tmp, "missing", "work")
    rc, _, err = run_cmd(
        [MAIN, "inventory", "--input", ind, "--output", outdir, "--work", workdir], HERE)
    if rc != 0:
        failures.append("inventory-rc")
        print("FAIL inventory rc=%d err=%s" % (rc, err.strip()))
    facts_path = os.path.join(workdir, "extracted_facts.json")
    if os.path.exists(facts_path):
        with open(facts_path, "r", encoding="utf-8") as fh:
            facts = json.load(fh).get("facts", [])
        has_evidence = all(
            f.get("evidence") and f["evidence"][0].get("locator") and f["evidence"][0].get("quote")
            for f in facts
        )
        print("inventory: %d candidate facts, all with locator+quote: %s" % (
            len(facts), "OK" if has_evidence else "FAIL"))
        if not has_evidence:
            failures.append("facts-evidence")
    rc, _, err = run_cmd(
        [MAIN, "validate", "--input", ind, "--output", outdir, "--work", workdir], HERE)
    if rc != 0:
        failures.append("validate-rc")
        print("FAIL validate rc=%d err=%s" % (rc, err.strip()))

    if failures:
        print("\nSELFTEST FAIL: %s" % ", ".join(failures))
        return 1
    print("\nSELFTEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
