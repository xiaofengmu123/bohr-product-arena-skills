# -*- coding: utf-8 -*-
"""main.py — 入口包装器，实际实现位于 core_impl.py。

用法：python3 scripts/main.py <scaffold|inventory|finalize|verify|selftest>
所有子命令均支持 --input/--output/--work 参数，缺省指向
/input、/app/output、/app/work。
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <scaffold|inventory|finalize|verify|selftest> "
          "[--input DIR] [--output DIR] [--work DIR] [--answers FILE]")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv or len(sys.argv) < 2:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.path.insert(0, HERE)
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")