# -*- coding: utf-8 -*-
"""main.py — 引文追踪 Skill 的薄入口（实现见 core_impl.py）。

用法：
  python scripts/main.py bootstrap --input /input --output /app/output --work /app/work
  python scripts/main.py record --kind edge --json '{...}' [--reason ...]
  python scripts/main.py finalize --output /app/output
  python scripts/main.py rank   --output /app/output
  python scripts/main.py path   --source ID --target ID [--reverse] --output /app/output
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _usage() -> str:
    return (
        "usage: main.py <bootstrap|record|finalize|rank|path> [options]\n"
        "  bootstrap  --input DIR --output DIR --work DIR\n"
        "  record     --kind KIND --json JSON|'-' [--reason TEXT] --output DIR --work DIR\n"
        "  finalize   --output DIR\n"
        "  rank       --output DIR\n"
        "  path       --source ID --target ID [--reverse] --output DIR\n"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(_usage())
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")