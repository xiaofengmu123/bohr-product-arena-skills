# -*- coding: utf-8 -*-
"""main.py — 薄入口：把子命令转发给 core_impl，保持本文件极小以便维护。

用法（在 Skill 包根目录执行）：
  python3 -B scripts/main.py init        --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py profile     --input /input --output /app/output
  python3 -B scripts/main.py seed-plan   --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py render-figure --model /app/work/figure_model.json --output /app/output
  python3 -B scripts/main.py validate    --input /input --output /app/output --repair
  python3 -B scripts/main.py --help
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main():
    return core_impl.dispatch(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
