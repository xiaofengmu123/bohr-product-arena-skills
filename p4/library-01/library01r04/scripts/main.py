"""main.py — thin entry point; the implementation lives in core_impl.py.

Kept small on purpose so the CLI surface is stable and the whole engine can
live in a single auditable module. Run with `python3 -B scripts/main.py --help`.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _usage():
    return (
        "usage: python3 -B scripts/main.py\n"
        "         [--case-json PATH] [--task-path PATH] [--input-root DIR]\n"
        "         [--output-dir DIR] [--work-dir DIR] [--max-hash-bytes N]\n"
        "         [--skeleton-only]\n"
        "         [--update PATCH.json [--result RESULT.json]]\n"
        "         [--check RESULT.json [--check-case-json CASE.json]]\n"
        "       defaults: --input-root /input --output-dir /app/output --work-dir /app/work"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(_usage())
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
