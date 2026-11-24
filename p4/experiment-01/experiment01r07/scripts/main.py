#!/usr/bin/env python3
"""main.py — 薄入口（实现见 core_impl.py）。

用法：
  python3 scripts/main.py probe    --input /input --work /app/work
  python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 scripts/main.py validate --input /input --output /app/output --work /app/work
  python3 scripts/main.py          # 默认 probe -> scaffold
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
