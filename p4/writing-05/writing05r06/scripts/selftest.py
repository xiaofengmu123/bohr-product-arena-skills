#!/usr/bin/env python3
"""Self-test for the evidence-driven-writing skill.

Run from the Skill root:
    python scripts/selftest.py

Covers: scaffold, extract, verify (positive + negative), finalize,
empty input, bad JSON. Exit code 0 means all checks passed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C

if __name__ == "__main__":
    sys.exit(C.selftest())
