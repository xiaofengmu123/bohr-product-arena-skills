# -*- coding: utf-8 -*-
"""main.py —— 统一 CLI 入口（薄封装，实现见 core_impl.py）。

用法：
  python3 -B scripts/main.py scan      [--input /input --work /app/work]
  python3 -B scripts/main.py skeleton  [--work /app/work --output /app/output]
  python3 -B scripts/main.py validate  [--work /app/work --output /app/output]
  python3 -B scripts/main.py selftest
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main(argv=None):
    return core_impl.cli(argv)


if __name__ == "__main__":
    sys.exit(main())
