"""main.py - thin entry wrapper (implementation in core_impl.py).

Usage:
  python3 -B scripts/main.py [--case-json PATH] [--task-path PATH]
      [--input-root DIR] [--output-dir DIR] [--work-dir DIR]
      [--skeleton-only | --update patch.json | --check result.json
       | --search QUERY]

Defaults target the runtime layout: /input, /app/work, /app/output.
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
