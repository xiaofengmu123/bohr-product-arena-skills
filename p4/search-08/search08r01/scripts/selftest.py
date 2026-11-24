# -*- coding: utf-8 -*-
"""selftest.py - dedicated self-test entry for the skill package.

Checks the package layout (SKILL.md frontmatter and script syntax), then runs
the runtime self-check over result.json. Exit code 0 only when everything
passes. Standard library only.

    python3 -B scripts/selftest.py [--input DIR] [--output DIR] [--work DIR]
"""

import argparse
import os
import py_compile
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def package_checks():
    """Static checks over the package itself; returns list of (name, ok, detail)."""
    pkg = os.path.dirname(HERE)
    checks = []
    skill = os.path.join(pkg, "SKILL.md")
    if not os.path.isfile(skill):
        checks.append(("SKILL.md exists", False, "missing %s" % skill))
    else:
        checks.append(("SKILL.md exists", True, ""))
        try:
            text = open(skill, encoding="utf-8-sig").read()
        except Exception as exc:
            checks.append(("SKILL.md readable", False, str(exc)))
            text = ""
        fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not fm:
            checks.append(("SKILL.md frontmatter", False, "no --- block"))
        else:
            checks.append(("SKILL.md frontmatter", True, ""))
            for key in ("name", "description", "version"):
                present = bool(re.search(r"^%s\s*:" % key, fm.group(1), re.M))
                checks.append(("frontmatter.%s" % key, present, "" if present else "missing"))
    for name in ("main.py", "core_impl.py", "selftest.py"):
        p = os.path.join(HERE, name)
        if not os.path.isfile(p):
            checks.append(("%s compiles" % name, False, "missing"))
            continue
        try:
            py_compile.compile(p, doraise=True)
            checks.append(("%s compiles" % name, True, ""))
        except Exception as exc:
            checks.append(("%s compiles" % name, False, str(exc)))
    return checks


def main():
    parser = argparse.ArgumentParser(prog="selftest.py", description="Run package and runtime self-checks.")
    parser.add_argument("--input", default=core_impl.DEFAULT_INPUT)
    parser.add_argument("--output", default=core_impl.DEFAULT_OUTPUT)
    parser.add_argument("--work", default=core_impl.DEFAULT_WORK)
    args = parser.parse_args()

    all_checks = package_checks()
    rc = 0
    for name, ok, detail in all_checks:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))
        if not ok:
            rc = 1

    runtime_rc = core_impl.run_selfcheck(args)
    if runtime_rc != 0:
        rc = 1
    npass = sum(1 for c in all_checks if c[1])
    print("selftest: %d/%d package checks PASS; runtime exit=%d" % (npass, len(all_checks), runtime_rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
