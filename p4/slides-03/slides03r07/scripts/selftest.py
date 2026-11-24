#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — end-to-end degenerate-input self-test for the skill package.

Runs scripts/main.py against five synthetic scenarios (empty input directory,
missing input directory, malformed JSON, oversized JSON, and a valid minimal
case) and asserts that each run exits 0 with a legal result.json whose status
honestly reflects the situation.

Standard library only.  Run from anywhere:

    python3 scripts/selftest.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _reconfigure_stdio():
    """Force UTF-8 output so non-ASCII diagnostics never crash on legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main():
    _reconfigure_stdio()
    main_py = os.path.join(HERE, "main.py")
    if not os.path.exists(main_py):
        print("FAIL: main.py not found at %s" % main_py)
        return 1
    passed, report = C.run_selftests(main_py, sys.executable)
    print("self-test results: %d/%d passed\n" % (passed, len(report)))
    for entry in report:
        mark = "PASS" if entry["ok"] else "FAIL"
        print("[%s] %-32s status=%r expected=%r"
              % (mark, entry["name"], entry.get("status"), entry.get("expected")))
        if not entry["ok"]:
            print("      %s" % entry.get("detail", ""))
    print()
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
