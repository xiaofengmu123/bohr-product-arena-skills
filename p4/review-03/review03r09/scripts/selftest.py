#!/usr/bin/env python3
"""Standalone self-test for the research-methods-stats-audit skill.

Runs the built-in selftest from core_impl on synthetic input only; it never
reads or writes formal test outputs and never fabricates benchmark scores.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("usage: python -B scripts/selftest.py [--help]")
        return 0
    return core_impl.cmd_selftest(None)


if __name__ == "__main__":
    raise SystemExit(main())
