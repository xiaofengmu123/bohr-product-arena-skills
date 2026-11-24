"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Usage:
    python scripts/main.py [command] [--input DIR] [--output DIR] [--work DIR]

Commands: init | survey | inspect | analyze | build | selfcheck
Default paths: /input, /app/output, /app/work (overridable for local testing).
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [init|survey|inspect|analyze|build|selfcheck] "
          "[--input DIR] [--output DIR] [--work DIR]")
    print("Deterministic, standard-library-only statistical analysis skill.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
