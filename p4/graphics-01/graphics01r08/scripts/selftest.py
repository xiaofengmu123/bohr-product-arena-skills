#!/usr/bin/env python3
"""Package self-check for sci-figure-workflow.

Run `python scripts/selftest.py` from the skill root before submitting.
Validates frontmatter, script syntax, size constraints, and a synthetic
empty-input end-to-end run that must stay honest (no fabricated data).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MAX_FILES = 14
MAX_TOTAL_BYTES = int(1.2 * 1024 * 1024)
MAIN_MAX_BYTES = 30 * 1024
SKILL_MAX_LINES = 180


def main():
    checks = []
    fails = []

    def chk(cond, msg):
        checks.append(("PASS" if cond else "FAIL", msg))
        if not cond:
            fails.append(msg)

    skill = os.path.join(ROOT, "SKILL.md")
    chk(os.path.isfile(skill), "SKILL.md exists at %s" % skill)
    if os.path.isfile(skill):
        with open(skill, encoding="utf-8") as fh:
            text = fh.read()
        lines = text.splitlines()
        chk(len(lines) <= SKILL_MAX_LINES, "SKILL.md <= %d lines (actual %d)" % (SKILL_MAX_LINES, len(lines)))
        chk(text.lstrip().startswith("---"), "SKILL.md begins with YAML frontmatter")
        parts = text.split("---", 2)
        fm = parts[1] if len(parts) >= 3 else ""
        for key in ("name:", "description:", "version:"):
            chk(key in fm, "frontmatter contains %s" % key)

    scripts_dir = os.path.join(ROOT, "scripts")
    chk(os.path.isdir(scripts_dir), "scripts/ exists")
    py_files = []
    if os.path.isdir(scripts_dir):
        py_files = sorted(fn for fn in os.listdir(scripts_dir) if fn.endswith(".py"))
    for fn in py_files:
        p = os.path.join(scripts_dir, fn)
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        try:
            compile(src, p, "exec")
            chk(True, "compiles: scripts/%s" % fn)
        except SyntaxError as exc:
            chk(False, "syntax error in %s: %s" % (fn, exc))

    main_py = os.path.join(scripts_dir, "main.py")
    chk(os.path.isfile(main_py), "scripts/main.py exists")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if os.path.isfile(main_py):
        size = os.path.getsize(main_py)
        chk(size < MAIN_MAX_BYTES, "main.py < %d KB (actual %d B)" % (MAIN_MAX_BYTES // 1024, size))
        r = subprocess.run([sys.executable, main_py, "--help"], capture_output=True, text=True, env=env)
        chk(r.returncode == 0, "python scripts/main.py --help exits 0")

    # synthetic empty-input end-to-end: must stay honest, no fabricated answer
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        r = subprocess.run(
            [sys.executable, main_py, "scaffold", "--input", inp, "--output", out, "--work", work],
            capture_output=True, text=True, env=env)
        chk(r.returncode == 0, "scaffold on empty input exits 0 (rc=%d)" % r.returncode)
        rp = os.path.join(out, "result.json")
        chk(os.path.isfile(rp), "scaffold wrote result.json")
        if os.path.isfile(rp):
            with open(rp, encoding="utf-8") as fh:
                obj = json.load(fh)
            chk(obj.get("case_id") == "unknown", "empty input -> case_id=unknown")
            chk(obj.get("status") in ("partial", "insufficient_evidence", "failed"),
                "empty input -> honest non-complete status (%r)" % obj.get("status"))
        r = subprocess.run(
            [sys.executable, main_py, "validate", "--output", out, "--input", inp],
            capture_output=True, text=True, env=env)
        chk(r.returncode in (0, 1), "validate runs on scaffold (rc=%d)" % r.returncode)

    # package size constraints
    total, count = 0, 0
    for root, dirs, names in os.walk(ROOT):
        parts = root.split(os.sep)
        if "__pycache__" in parts or ".git" in parts:
            continue
        for name in names:
            count += 1
            total += os.path.getsize(os.path.join(root, name))
    chk(count <= MAX_FILES, "file count <= %d (actual %d)" % (MAX_FILES, count))
    chk(total <= MAX_TOTAL_BYTES, "total size <= 1.2MB (actual %d B)" % total)

    for status, msg in checks:
        print("[%s] %s" % (status, msg))
    print("\n%d/%d checks passed" % (len(checks) - len(fails), len(checks)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
