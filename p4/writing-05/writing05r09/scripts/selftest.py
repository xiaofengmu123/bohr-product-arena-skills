#!/usr/bin/env python3
"""Self-check for the evidence-grounded-writing package.

Runs three scenarios (normal / empty input / bad JSON) in temp dirs and
asserts the main.py helpers behave deterministically without fabricating data.
Exits 0 when all checks pass. Standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")
SKILL = os.path.join(ROOT, "SKILL.md")

CHECKS = []


def record(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def run(args):
    return subprocess.run([sys.executable, MAIN] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def scenario_empty(tmp):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp)
    for d in (out, work):
        os.makedirs(d)
    r = run(["scaffold", "--case", os.path.join(inp, "case.json"),
             "--input", inp, "--out", out, "--work", work])
    record("empty: scaffold rc=0", r.returncode == 0, r.stdout.strip()[:120])
    res = load_json(os.path.join(out, "result.json"))
    record("empty: skeleton written", res.get("status") == "in_progress")
    r2 = run(["finalize", "--case", os.path.join(inp, "case.json"),
              "--work", work, "--out", out])
    res2 = load_json(os.path.join(out, "result.json"))
    record("empty: finalize yields non-complete", r2.returncode == 0 and res2.get("status") != "complete",
           "status=%s" % res2.get("status"))
    record("empty: result.md written", os.path.exists(os.path.join(out, "result.md")))


def scenario_bad_json(tmp):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp)
    for d in (out, work):
        os.makedirs(d)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    r = run(["scaffold", "--case", os.path.join(inp, "case.json"),
             "--input", inp, "--out", out, "--work", work])
    record("bad-json: scaffold rc=0 (tolerant)", r.returncode == 0, r.stdout.strip()[:120])
    res = load_json(os.path.join(out, "result.json"))
    record("bad-json: error recorded", bool(res.get("errors")), str(res.get("errors"))[:120])


def scenario_normal(tmp):
    inp = os.path.join(tmp, "input")
    mat = os.path.join(inp, "materials")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(mat)
    for d in (out, work):
        os.makedirs(d)
    case = {"case_id": "demo-1", "output_contract": {"deliverable": None, "sources": None, "change_log": None}}
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\n")
    with open(os.path.join(mat, "a.md"), "w", encoding="utf-8") as f:
        f.write("研究甲（n=120）观察到 A 与 B 正相关（r=0.42）。\n")

    r = run(["scaffold", "--case", os.path.join(inp, "case.json"),
             "--input", inp, "--out", out, "--work", work])
    record("normal: scaffold rc=0", r.returncode == 0)
    res = load_json(os.path.join(out, "result.json"))
    record("normal: case_id echoed", res.get("case_id") == "demo-1")
    record("normal: contract keys echoed",
           set(case["output_contract"]).issubset(set(res.keys())), str(sorted(res.keys())))

    r = run(["extract", "--input", inp, "--work", work])
    idx = load_json(os.path.join(work, "extract_index.json"))
    record("normal: extract material", r.returncode == 0 and any(m.get("extracted") for m in idx["materials"]))

    # idempotence: scaffold again must preserve non-in_progress result
    res["status"] = "partial"
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    run(["scaffold", "--case", os.path.join(inp, "case.json"),
         "--input", inp, "--out", out, "--work", work])
    res2 = load_json(os.path.join(out, "result.json"))
    record("normal: scaffold preserves improved result", res2.get("status") == "partial")

    r = run(["finalize", "--case", os.path.join(inp, "case.json"),
             "--work", work, "--out", out])
    res3 = load_json(os.path.join(out, "result.json"))
    record("normal: finalize rc=0", r.returncode == 0)
    for k in ("case_id", "status", "sources", "uncertainty", "pending_human", "change_log"):
        record("normal: field %s present" % k, k in res3)
    r = run(["check", "--out", out])
    record("normal: check rc=0", r.returncode == 0, r.stdout.strip()[:120])


def frontmatter_ok():
    try:
        with open(SKILL, "r", encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        return False, "SKILL.md missing"
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", txt, re.DOTALL)
    if not m:
        return False, "no YAML frontmatter"
    fm = m.group(1)
    for key in ("name", "description", "version"):
        if not re.search(r"^%s:\s*\S" % key, fm, re.MULTILINE):
            return False, "missing frontmatter key: %s" % key
    body = txt[m.end():]
    n_lines = len([ln for ln in body.splitlines() if ln.strip()])
    return True, "body non-empty lines=%d" % n_lines


def check_real_output(out_dir):
    result_path = os.path.join(out_dir, "result.json")
    if not os.path.exists(result_path):
        record("real-output: result.json present", False, result_path)
        return
    record("real-output: result.json present", True)
    r = subprocess.run([sys.executable, MAIN, "check", "--out", out_dir],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    record("real-output: main.py check rc=0", r.returncode == 0, r.stdout.strip()[:160])


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Self-check for evidence-grounded-writing package.")
    ap.add_argument("--input", help="optional real input dir (accepted for symmetry)")
    ap.add_argument("--output", help="optional real output dir to run main.py check against")
    a = ap.parse_args()

    record("SKILL.md frontmatter", *frontmatter_ok())
    with tempfile.TemporaryDirectory(prefix="egw-test-") as tmp:
        scenario_empty(tmp)
    with tempfile.TemporaryDirectory(prefix="egw-test-") as tmp:
        scenario_bad_json(tmp)
    with tempfile.TemporaryDirectory(prefix="egw-test-") as tmp:
        scenario_normal(tmp)
    if a.output:
        check_real_output(a.output)
    failed = [c for c in CHECKS if not c[1]]
    print("\n%d/%d checks passed" % (len(CHECKS) - len(failed), len(CHECKS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
