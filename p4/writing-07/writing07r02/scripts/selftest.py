"""Self-test entry for the sci-intro-writing Skill scripts.

Runs scan / skeleton / validate against empty input, bad JSON, missing input and
a minimal valid case in a temporary directory, then reports whether every case
produced a parseable result.json. Exits 0 when all pass, 1 otherwise.

Usage:
    python3 -B scripts/selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl

if __name__ == "__main__":
    raise SystemExit(core_impl.run_selftest())
