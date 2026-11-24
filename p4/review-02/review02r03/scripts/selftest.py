#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-test for the citation-fact-audit skill package.

Verifies, without any network or non-standard dependency:

1. `main.py --help` exits with code 0.
2. Empty input yields status `insufficient_evidence` with a parseable result.
3. A broken `case.json` degrades gracefully (errors recorded, materials read).
4. Determinism: two identical runs produce byte-identical result.json and
   report.md (no wall-clock, no randomness, stable ordering).
5. The output contract fields and the finding count survive the merge.

Exit code 0 means all checks passed; any failure prints to stderr and exits 1.
"""

import json
import subprocess
import sys
from pathlib import Path

import main


def check_help_exit_zero():
    """`main.py --help` must exit 0 without capturing stdio through pipes."""
    script = Path(__file__).resolve().parent / "main.py"
    run = subprocess.run(
        [sys.executable, "-B", str(script), "--help"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    return run.returncode == 0


def main_selftest():
    failures = []
    if not check_help_exit_zero():
        failures.append("main.py --help did not exit 0")
    ok, inner = main.selftest()
    if not ok:
        failures.extend(inner)

    # Extra schema check on the determinism fixture is already covered by
    # main.selftest(); add a lightweight self-consistency assertion here.
    if failures:
        for f in failures:
            print("FAIL: " + f, file=sys.stderr)
        return 1
    print("selftest: OK (help / empty-input / bad-json / determinism)")
    return 0


if __name__ == "__main__":
    sys.exit(main_selftest())
