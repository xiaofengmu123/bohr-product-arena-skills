#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- standalone self-test runner for the Skill package.

Runs the built-in selftest (determinism + robustness) without needing a real
case. It verifies:
  * `main.py --help` exits 0 and lists all subcommands;
  * a synthetic fixture run twice produces byte-identical result.json;
  * an empty input directory yields status=insufficient_evidence (no crash);
  * a bad JSON file is recorded in inspect errors (no crash).

Usage:  python scripts/selftest.py
Exit code 0 when all checks pass, 1 otherwise.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.cmd_selftest(None))
