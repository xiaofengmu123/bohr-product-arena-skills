"""main.py — thin entry wrapper（实现位于 core_impl.py）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only          # 先落盘 partial 骨架
  python3 -B scripts/main.py --update patch.json --result /app/output/result.json
  python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json

缺省路径：/input、/app/output、/app/work。仅依赖 Python 标准库。
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR] "
          "[--skeleton-only] [--check RESULT] [--update PATCH] [--help]")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
