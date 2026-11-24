"""main.py - thin entry wrapper for the deterministic analysis pipeline.

Implementation lives in core_impl.py; this file only resolves the package root,
adds it to sys.path, and delegates to core_impl.main(). All paths are relative to
this script's directory, so the Skill works regardless of the evaluator cwd.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _help():
    print("usage: main.py [init|survey|inspect|analyze|build|selfcheck|run] "
          "[--input DIR] [--output DIR] [--work DIR] [--help]")
    print("Deterministic statistical analysis (stdlib only). Defaults: "
          "--input /input --output /app/output --work /app/work")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    raise SystemExit(core_impl.main(sys.argv[1:]))
