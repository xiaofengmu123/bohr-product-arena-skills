"""selftest.py - run the full self-check suite without importing a stage name.

Thin wrapper around core_impl.py's `selftest` stage.  Usage:

    python3 -B scripts/selftest.py [--input DIR] [--output DIR] [--work DIR]

It prints one PASS/FAIL line per check and exits 0 after running; a FAIL is
reported but does not crash the harness, so a human can read and repair the
degradation.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    target = os.path.join(HERE, "core_impl.py")
    sys.argv = [target, "selftest"] + sys.argv[1:]
    runpy.run_path(target, run_name="__main__")
