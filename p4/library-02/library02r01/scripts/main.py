"""main.py - thin entry wrapper for the library repair skill.

The real implementation lives in core_impl.py so that main.py stays small and
the entry point is always importable/executable without argument leakage.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: python -B scripts/main.py [--input DIR] [--output DIR] "
        "[--work DIR] [--case-json FILE] [--task-path FILE] "
        "[--skeleton-only] [--update PATCH.json --result RESULT.json] "
        "[--check RESULT.json --case-json FILE] [--help]"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
