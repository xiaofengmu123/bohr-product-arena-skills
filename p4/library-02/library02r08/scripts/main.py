#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口：把参数原样转给 core_impl.py。

用法：
    python3 -B scripts/main.py --help
    python3 -B scripts/main.py --case-json /input/case.json --task-path /input/task.md \
        --input-root /input --output-dir /app/output --work-dir /app/work
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: main.py [--case-json PATH] [--task-path PATH] [--input-root DIR]\n"
        "               [--output-dir DIR] [--work-dir DIR] [--skeleton-only]\n"
        "               [--update PATCH.json] [--result PATH] [--check RESULT.json]"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
