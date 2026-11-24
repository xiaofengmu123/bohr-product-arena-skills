# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper；实现位于 core_impl.py。

仅做命令行转发，保持本文件体积最小（<30KB）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main(sys.argv[1:]))
