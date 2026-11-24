"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Usage:
    python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py --help
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [command] [--input DIR] [--output DIR] [--work DIR] "
          "[--primary NAME] [--group COL] [--targets a,b,c] [--outcome COL] [--help]")
    print("commands: run (default), scaffold, profile, clean, analyze, render, report, finalize")
    print("Runs the skill implementation and writes result.json plus editable artifacts.")
    print("All scripts use only the Python standard library.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
