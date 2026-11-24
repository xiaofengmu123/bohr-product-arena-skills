# -*- coding: utf-8 -*-
"""selftest.py —— 自检入口（薄封装，合成输入，不生成正式答案）。

用法：
  python3 -B scripts/selftest.py
退出码：0 = 全部通过；1 = 至少一项失败。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main():
    return core_impl.selftest_main()


if __name__ == "__main__":
    sys.exit(main())
