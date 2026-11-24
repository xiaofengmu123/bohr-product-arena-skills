#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-veritas 独立自检脚本。

运行：python3 -B scripts/selftest.py
等价于 python3 -B scripts/main.py self-test；覆盖空输入目录 / 坏 JSON /
缺输入目录 / output_contract 回填四种场景，全部通过退出码 0。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main.self_test_cmd(None))
