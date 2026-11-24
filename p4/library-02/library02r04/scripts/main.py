# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the document-library repair skill.

All logic lives in core_impl.py; this file only forwards to it so that the
entry point stays small and reviewable.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: python3 -B scripts/main.py [options]\n"
        "  --input DIR        input root (default /input)\n"
        "  --output DIR       output dir (default /app/output)\n"
        "  --work DIR         work dir (default /app/work)\n"
        "  --case-json PATH   case descriptor (default <input>/case.json)\n"
        "  --task-path PATH   task text (default <input>/task.md)\n"
        "  --skeleton-only    write a partial result skeleton and exit\n"
        "  --update PATCH     merge patch.json into result.json\n"
        "  --result PATH      result.json to update (with --update)\n"
        "  --check PATH       validate result.json against the contract\n"
        "  --selftest         run the package self-test\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
