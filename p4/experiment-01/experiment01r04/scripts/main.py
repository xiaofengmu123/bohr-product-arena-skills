"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Usage:
  python3 scripts/main.py probe     --input /input --work /app/work
  python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
  python3 scripts/main.py validate  --input /input --output /app/output --work /app/work
  python3 scripts/main.py --help
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(__doc__.strip())


if __name__ == "__main__":
    if not sys.argv[1:] or "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
