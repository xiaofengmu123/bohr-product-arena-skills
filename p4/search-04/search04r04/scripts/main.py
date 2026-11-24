#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 引文追踪 Skill 的轻量入口（实现见 core_impl.py）。

用法：
    python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
    python3 scripts/main.py record --kind papers --json '[...]' --reason "..."
    python3 scripts/main.py dedupe
    python3 scripts/main.py rank
    python3 scripts/main.py paths --from ID --to ID
    python3 scripts/main.py finalize --output /app/output --work /app/work
    python3 scripts/main.py selftest
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(__doc__.strip())


if __name__ == "__main__":
    if len(sys.argv) == 1 or "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
