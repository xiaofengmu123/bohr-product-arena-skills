"""main.py - thin entry wrapper (implementation lives in core_impl.py)."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _usage():
    print("usage: python scripts/main.py [--input DIR] [--output DIR] [--work DIR] "
          "{inspect|assemble|validate|demo}")
    print("       validate: python scripts/main.py validate /path/to/result.json")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _usage()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
