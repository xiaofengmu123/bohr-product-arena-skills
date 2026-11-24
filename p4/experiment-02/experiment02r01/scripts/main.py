#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""薄入口。核心实现位于 core_impl.py（保持 main.py < 30KB 的要求）。

同一目录下运行：
  python3 scripts/main.py init --input /input --output /app/output --work /app/work
  python3 scripts/main.py show --locator "case.json#/plans/0/steps/1"
  python3 scripts/main.py grep --pattern "4 °C"
  python3 scripts/main.py check --locators "case.json#/plans/0" "task.md#L9"

assemble.py / selfcheck.py 直接 import core_impl as core，本文件仅作为命令行入口，
并把 core_impl 的公开符号一并导出，便于以 import main 方式复用。
"""

from __future__ import annotations

import sys

from core_impl import *  # noqa: F401,F403
import core_impl


if __name__ == "__main__":
    sys.exit(core_impl.main())
