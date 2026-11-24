"""main.py - thin entry wrapper for experiment-plan-comparison (impl in core_impl.py).

Standard library only. Usage:
    python scripts/main.py --help
    python scripts/main.py inspect
    python scripts/main.py assemble
    python scripts/main.py validate /app/output/result.json
    python scripts/main.py demo
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "experiment-plan-comparison v1.0.0\n"
        "usage: main.py [--input DIR] [--output DIR] [--work DIR] "
        "{inspect|assemble|validate|demo} [result.json]\n"
        "defaults: input=/input output=/app/output work=/app/work"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv or len(sys.argv) == 1:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
