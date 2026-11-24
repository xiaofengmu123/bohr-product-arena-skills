"""main.py — thin entry wrapper.

The implementation lives in core_impl.py.  Running this file passes all
command-line arguments through unchanged, so every invocation below works:

    python scripts/main.py init
    python scripts/main.py --input /input --output /app/output --work /app/work analyze

The wrapper exists so the Skill has a stable, short entry point whose
behaviour can be checked with ``python scripts/main.py --help``.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <init|survey|inspect|analyze|build|selfcheck|crosscheck> "
          "[--input DIR] [--output DIR] [--work DIR]")
    print("Statistical analysis Skill (stat-analysis-02). "
          "Deterministic, stdlib-only; see SKILL.md for the workflow.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
