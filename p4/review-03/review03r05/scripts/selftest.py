"""selftest.py -- package self-check.

Run from the Skill root (or anywhere):
    python -B scripts/selftest.py

It verifies, without any external input or network:
  1. SKILL.md exists with valid name/description/version YAML frontmatter.
  2. All scripts are syntactically valid.
  3. main.py and every subcommand accept --help and exit 0.
  4. A full synthetic run (txt material + output_contract) produces a valid
     result.json that keeps case_id, every output_contract field, sources,
     uncertainty and a non-empty change_log.
  5. An empty input degrades honestly to insufficient_evidence/input_error.
  6. Two fresh runs on identical input produce identical content
     (timestamps excluded).

Exit code 0 = all checks pass; non-zero = a check failed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / "SKILL.md"
MAIN = ROOT / "scripts" / "main.py"

FAILURES = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILURES.append(msg)


def run_py(args, timeout=120):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-B"] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)


def check_frontmatter():
    if not SKILL_MD.exists():
        check(False, "SKILL.md exists")
        return
    text = SKILL_MD.read_text(encoding="utf-8")
    ok = text.startswith("---")
    end = text.find("\n---", 3)
    ok = ok and end != -1
    name = re_search(r"(?m)^name:\s*(\S+)", text)
    desc = has_line(r"(?m)^description:\s*", text)
    ver = re_search(r"(?m)^version:\s*(\S+)", text)
    check(ok, "SKILL.md has YAML frontmatter block")
    check(bool(name), "frontmatter has name")
    check(desc, "frontmatter has description")
    check(bool(ver), "frontmatter has version")
    check("output_contract" in text, "SKILL.md documents output_contract alignment")
    check("change_log" in text, "SKILL.md documents change_log")
    check("900" in text, "SKILL.md states the 900-second budget")


def has_line(pattern, text):
    import re
    return bool(re.search(pattern, text))


def re_search(pattern, text):
    import re
    m = re.search(pattern, text)
    return m.group(1) if m else None


def check_syntax():
    for p in sorted((ROOT / "scripts").glob("*.py")):
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
            check(True, "syntax ok: %s" % p.name)
        except SyntaxError as exc:
            check(False, "syntax error in %s: %s" % (p.name, exc))


def check_help():
    r = run_py([str(MAIN), "--help"])
    check(r.returncode == 0, "main.py --help exits 0")
    for sub in ("discover", "scaffold", "finalize", "run"):
        r = run_py([str(MAIN), sub, "--help"])
        check(r.returncode == 0, "main.py %s --help exits 0" % sub)


def make_synthetic_input(tmp):
    inp = Path(tmp) / "input"
    mat = inp / "materials"
    mat.mkdir(parents=True)
    (inp / "case.json").write_text(json.dumps({
        "case_id": "selftest-case",
        "task": "review methods and statistics",
        "output_contract": {
            "review_summary": "string",
            "audit_items": "array",
            "flagged_for_human": {"type": "boolean"},
        },
    }, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text("selftest task", encoding="utf-8")
    (mat / "paper.txt").write_text(
        "Patients were randomized 1:1. N = 120.\n\n"
        "The primary outcome differed between groups (p = 0.03), 95% CI 0.2 to 0.9.\n\n"
        "A secondary endpoint was not significant (p = 0.31).\n\n"
        "Authors used a two-sample t test for paired pre-post data.\n",
        encoding="utf-8")
    return inp


def strip_volatile(obj):
    obj = json.loads(json.dumps(obj, ensure_ascii=False))
    obj.pop("change_log", None)
    obj.pop("runtime", None)
    return obj


def check_full_run(tmp):
    inp = make_synthetic_input(tmp)
    out = Path(tmp) / "output"
    work = Path(tmp) / "work"
    r = run_py([str(MAIN), "run", "--input", str(inp),
                "--output", str(out), "--work", str(work)])
    check(r.returncode == 0, "full run exits 0")
    if r.returncode != 0:
        print("    stderr:", r.stderr[-500:])
        return
    result_path = out / "result.json"
    if not result_path.exists():
        check(False, "result.json written by full run")
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        check(False, "result.json is valid JSON (%s)" % exc)
        return
    check(result.get("case_id") == "selftest-case", "case_id preserved")
    for field in ("review_summary", "audit_items", "flagged_for_human"):
        check(field in result, "output_contract field at top level: %s" % field)
    check("output_contract" in result, "raw output_contract retained")
    check(isinstance(result.get("sources"), list), "sources is a list")
    check("uncertainty" in result, "uncertainty present")
    check("change_log" in result and len(result["change_log"]) >= 2,
          "change_log non-empty")
    check((out / "report.md").exists(), "report.md written")
    # deterministic re-run: clear output/work, run again, compare content
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    r2 = run_py([str(MAIN), "run", "--input", str(inp),
                 "--output", str(out), "--work", str(work)])
    check(r2.returncode == 0, "determinism re-run exits 0")
    if r2.returncode == 0:
        result2 = json.loads((out / "result.json").read_text(encoding="utf-8"))
        same = strip_volatile(result) == strip_volatile(result2)
        check(same, "identical input -> identical content (timestamps excluded)")


def check_empty_input(tmp):
    inp = Path(tmp) / "empty_input"
    inp.mkdir(parents=True)
    out = Path(tmp) / "empty_output"
    work = Path(tmp) / "empty_work"
    r = run_py([str(MAIN), "run", "--input", str(inp),
                "--output", str(out), "--work", str(work)])
    check(r.returncode == 0, "empty input run exits 0")
    if r.returncode == 0:
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check(result.get("status") in ("insufficient_evidence", "input_error"),
              "empty input -> honest status (%s)" % result.get("status"))
        check("limitations" in result and "change_log" in result,
              "empty input keeps limitations + change_log")


def main():
    print("=== self-test: research-methods-stats-audit ===\n")
    check_frontmatter()
    check_syntax()
    check_help()
    tmp = tempfile.mkdtemp(prefix="stats_audit_selftest_")
    try:
        check_full_run(tmp)
        check_empty_input(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print("SELFTEST FAILED (%d)" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
