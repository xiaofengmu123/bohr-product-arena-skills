"""Standalone self-test for the research-selection-audit skill.

Runs the full pipeline against *synthetic* inputs only (empty directory,
missing directory, malformed case.json, a tiny material with a verified
quote, a mismatched quote, and a determinism check).  It never reads or
writes any official test case.

    python scripts/selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import run_selftest  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_selftest())
