#!/usr/bin/env python3
"""Self-test for the academic-slide-organizer Skill package.

Checks, in order:
  1. SKILL.md exists with valid name/description/version YAML frontmatter;
  2. every scripts/*.py imports only the standard library;
  3. main.py and evidence_check.py respond to --help with exit code 0;
  4. scaffold builds a legal partial skeleton (case_id, status, run_id);
  5. determinism: two scaffold runs on identical input yield byte-identical result.json;
  6. empty input directory degrades to insufficient_evidence with exit 0;
  7. malformed case.json degrades to input_error with exit 0 (no crash).

Uses only the standard library and temporary directories; it never reads or
writes /input or /app/output. Exit 0 means every check passed.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable


def run(args, cwd=None):
    return subprocess.run([PY] + args, cwd=cwd or ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def stdlib_names():
    try:
        return set(sys.stdlib_module_names)
    except AttributeError:  # Python < 3.10 fallback
        return {
            "argparse", "hashlib", "json", "re", "sys", "subprocess", "tempfile",
            "pathlib", "os", "shutil", "glob", "xml", "collections", "itertools",
            "functools", "typing", "copy", "math", "datetime", "io", "textwrap",
        }


def check_skill_md():
    path = ROOT / "SKILL.md"
    if not path.is_file():
        return False, "SKILL.md missing"
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False, "no YAML frontmatter found"
    for key in ("name", "description", "version"):
        if not re.search(r"^%s:\s*\S" % key, m.group(1), re.M):
            return False, "frontmatter missing %s" % key
    return True, "name/description/version present"


def check_stdlib_only():
    bad = []
    for py in sorted((ROOT / "scripts").glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_.]+))", text, re.M):
            mod = (m.group(1) or m.group(2)).split(".")[0]
            if mod not in stdlib_names():
                bad.append("%s imports %s" % (py.name, mod))
    if bad:
        return False, "; ".join(bad)
    return True, "scripts import standard library only"


def check_help():
    for script in ("main.py", "evidence_check.py"):
        r = run(["scripts/%s" % script, "--help"])
        if r.returncode != 0:
            return False, "%s --help exited %d" % (script, r.returncode)
    return True, "--help exit codes are 0"


def _make_input(base, case_content, extra_files):
    inp = base / "input"
    inp.mkdir()
    (inp / "case.json").write_text(case_content, encoding="utf-8")
    for name, content in extra_files.items():
        (inp / name).write_text(content, encoding="utf-8")
    return inp


def check_scaffold_and_determinism():
    base = Path(tempfile.mkdtemp(prefix="selskill-scaf-"))
    inp = _make_input(
        base,
        json.dumps({"id": "demo-case", "output_contract": {"report_goal": None}}, ensure_ascii=False),
        {"task.md": "challenge_id: demo-case\n# task\n", "paper.md": "# Demo paper\nEvidence sentence."},
    )
    out1, out2 = base / "out1", base / "out2"
    r1 = run(["scripts/main.py", "scaffold", "--input", str(inp), "--output", str(out1)])
    r2 = run(["scripts/main.py", "scaffold", "--input", str(inp), "--output", str(out2)])
    if r1.returncode != 0 or r2.returncode != 0:
        return False, "scaffold exit codes %d/%d" % (r1.returncode, r2.returncode)
    t1 = (out1 / "result.json").read_text(encoding="utf-8")
    t2 = (out2 / "result.json").read_text(encoding="utf-8")
    if t1 != t2:
        return False, "non-deterministic: two scaffold runs differ byte-wise"
    obj = json.loads(t1)
    if obj.get("case_id") != "demo-case":
        return False, "wrong case_id: %r" % obj.get("case_id")
    if obj.get("status") != "partial":
        return False, "wrong status: %r" % obj.get("status")
    if not obj.get("run", {}).get("run_id"):
        return False, "run_id missing"
    if obj.get("report_goal") is not None:
        return False, "provided output_contract field was not preserved"
    return True, "deterministic scaffold + legal skeleton + run_id"


def check_empty_input():
    base = Path(tempfile.mkdtemp(prefix="selskill-empty-"))
    inp = base / "empty"
    inp.mkdir()
    out = base / "out"
    r = run(["scripts/main.py", "scaffold", "--input", str(inp), "--output", str(out)])
    if r.returncode != 0:
        return False, "empty input exited %d" % r.returncode
    obj = json.loads((out / "result.json").read_text(encoding="utf-8"))
    if obj.get("status") != "insufficient_evidence":
        return False, "expected insufficient_evidence, got %r" % obj.get("status")
    return True, "empty input -> insufficient_evidence, exit 0"


def check_bad_json():
    base = Path(tempfile.mkdtemp(prefix="selskill-badjson-"))
    inp = _make_input(base, "{not valid json", {"task.md": "# task\n"})
    out = base / "out"
    r = run(["scripts/main.py", "scaffold", "--input", str(inp), "--output", str(out)])
    if r.returncode != 0:
        return False, "bad JSON exited %d" % r.returncode
    obj = json.loads((out / "result.json").read_text(encoding="utf-8"))
    if obj.get("status") != "input_error":
        return False, "expected input_error, got %r" % obj.get("status")
    if not obj.get("errors"):
        return False, "errors list empty despite malformed case.json"
    return True, "bad JSON -> input_error + errors recorded, exit 0"


CHECKS = [
    ("SKILL.md frontmatter", check_skill_md),
    ("stdlib-only imports", check_stdlib_only),
    ("--help exit codes", check_help),
    ("scaffold + determinism", check_scaffold_and_determinism),
    ("empty input degradation", check_empty_input),
    ("bad JSON degradation", check_bad_json),
]


def main():
    failures = []
    for name, fn in CHECKS:
        ok, detail = fn()
        print("[%s] %s -- %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            failures.append(name)
    print("selftest: %d/%d passed" % (len(CHECKS) - len(failures), len(CHECKS)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
