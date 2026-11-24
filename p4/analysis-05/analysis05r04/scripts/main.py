"""main.py - thin entry wrapper (implementation lives in core_impl.py)."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <command> [--input DIR] [--output DIR] [--work DIR] [--primary SRC]")
    print("commands: scaffold | discover | profile | clean | analyze | render | finalize | run | selftest")
    print("defaults: --input /input  --output /app/output  --work /app/work")


if __name__ == "__main__":
    if not sys.argv[1:] or "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
