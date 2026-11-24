"""main.py - thin entry wrapper.

Run the deterministic library-repair pipeline implemented in core_impl.py.
Usage:
    python3 -B scripts/main.py --input-dir /input --output-dir /app/output \\
        --work-dir /app/work
    python3 -B scripts/main.py --skeleton-only ...
    python3 -B scripts/main.py --update patch.json --result /app/output/result.json
    python3 -B scripts/main.py --check --result /app/output/result.json \\
        --case-json /input/case.json
    python3 -B scripts/main.py --selftest
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(__doc__)


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
