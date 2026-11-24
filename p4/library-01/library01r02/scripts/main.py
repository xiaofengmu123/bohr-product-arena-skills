"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Run with the interpreter the platform provides (python3 on the harness box,
``python`` on this Windows dev box); the wrapper does not touch sys.executable.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: main.py [--input DIR] [--output DIR] [--work DIR]\n"
        "                [--case-json FILE] [--task-path FILE]\n"
        "                [--skeleton-only] [--update PATCH.json --result RESULT.json]\n"
        "                [--check RESULT.json [--case-json FILE]] [--help]\n"
        "\n"
        "defaults: --input /input  --output /app/output  --work /app/work\n"
        "          --case-json /input/case.json  --task-path /input/task.md\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
