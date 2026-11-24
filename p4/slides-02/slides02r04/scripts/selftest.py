"""Standalone self-test entry for academic-slide-evidence.

Runs the same deterministic core self-test as `python3 scripts/main.py
selftest`, without touching /input or /app/output. Exit code 0 = pass.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as ci  # noqa: E402


def main():
    lines, failures = ci.run_selftest()
    for line in lines:
        print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
