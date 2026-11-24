"""main.py — thin entry wrapper for the document-library repair Skill.

All real logic lives in core_impl.py so that this file stays small and
reviewable. Invocation paths are relative to the Skill package root:

    python3 -B scripts/main.py [options]

Options mirror core_impl; run `--help` for the full list.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help() -> None:
    print(
        "usage: python3 -B scripts/main.py [--input DIR] [--output DIR] [--work DIR]\n"
        "       [--case-json FILE] [--task-path FILE] [--result FILE]\n"
        "       [--skeleton-only] [--update PATCH.json] [--check RESULT.json]\n"
        "\n"
        "Deterministic document-library repair pipeline (stdlib only).\n"
        "Defaults: --input /input --output /app/output --work /app/work.\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
