#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口封装，实际实现见 pipeline.py。

用法：
  python3 -B scripts/main.py [--input DIR] [--output DIR] [--work DIR] [--help]
默认：--input /input --output /app/output --work /app/work
等价于调用 scripts/pipeline.py 的对应子命令；缺省子命令为 selfcheck。
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    target = os.path.join(HERE, "pipeline.py")
    sys.argv = [target] + argv
    runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
