#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-test for the delivered skill package.

Runs against an output directory (default /app/output) and also checks the
package itself (SKILL.md frontmatter and required scripts). It reports a
pass/fail checklist and exits 0; it never invents scores or data.

Usage:
    python scripts/selftest.py --output /app/output --skill-dir /skills/weekly-skill
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as impl


def check_frontmatter(skill_dir):
    sk = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(sk):
        return {"pass": False, "detail": "SKILL.md missing"}
    text = impl.read_text_robust(sk)["text"]
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {"pass": False, "detail": "no YAML frontmatter"}
    fm = m.group(1)
    name = re.search(r"^name:\s*\S+", fm, re.MULTILINE)
    desc = re.search(r"^description:\s*\S+", fm, re.MULTILINE)
    ver = re.search(r"^version:\s*\S+", fm, re.MULTILINE)
    ok = bool(name and desc and ver)
    return {"pass": ok,
            "detail": f"name={bool(name)} description={bool(desc)} version={bool(ver)}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--skill-dir", default="/skills/weekly-skill")
    args = ap.parse_args()

    checks = []
    ok = True

    def check(name, cond, detail):
        nonlocal ok
        ok = ok and bool(cond)
        checks.append({"item": name, "pass": bool(cond), "detail": detail})

    fm = check_frontmatter(args.skill_dir)
    check("SKILL.md frontmatter", fm["pass"], fm["detail"])

    for script in ("scripts/main.py", "scripts/core_impl.py"):
        p = os.path.join(args.skill_dir, script)
        check(f"script present {script}", os.path.isfile(p), p)

    # Package self-check: python -B main.py --help must exit 0.
    try:
        import subprocess
        r = subprocess.run([sys.executable, "-B",
                            os.path.join(args.skill_dir, "scripts", "main.py"),
                            "--help"],
                           capture_output=True, text=True, timeout=30)
        check("main.py --help exit 0", r.returncode == 0,
              f"rc={r.returncode}")
    except Exception as exc:  # noqa: BLE001
        check("main.py --help exit 0", False, f"{type(exc).__name__}: {exc}")

    # Output validation via the same selfcheck used by main.py.
    sc = impl.run_selfcheck(args.skill_dir, args.output, args.output)
    for c in sc.get("checks", []):
        checks.append(c)
    check("overall selfcheck", sc.get("pass", False), "see items above")

    print(json.dumps({"pass": ok, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
