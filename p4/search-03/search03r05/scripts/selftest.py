# -*- coding: utf-8 -*-
"""selftest.py - dedicated self-test entry for the skill package.

Runs the full pipeline (discover -> screen -> finalize) against a synthetic
in-memory fixture, plus empty-input and broken-JSON robustness checks.
Exits 0 when every assertion passes, 1 otherwise. Stdlib only.

Usage:
    python3 -B scripts/selftest.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl._selftest_impl(None))