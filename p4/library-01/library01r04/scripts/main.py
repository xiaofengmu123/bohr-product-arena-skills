#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the document-library repair engine.

All implementation lives in core_impl.py. Keep this file small so the
evaluator can read the interface at a glance.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help() -> str:
    return (
        "usage: python3 -B scripts/main.py [options]\n"
        "  --case-json PATH      case descriptor (default /input/case.json)\n"
        "  --task-path PATH      task rules (default /input/task.md)\n"
        "  --input-root PATH     source library (default /input)\n"
        "  --output-dir PATH     results dir (default /app/output)\n"
        "  --work-dir PATH       scratch dir (default /app/work)\n"
        "  --skeleton-only       write partial result skeleton, then exit\n"
        "  --update PATCH.json   merge an agent patch into result.json\n"
        "  --check RESULT.json   validate result.json\n"
        "  --selftest            run built-in synthetic-library self-test\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(_help())
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
