#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研问题驱动检索 Skill 的薄入口。

实现位于同目录 core_impl.py；本文件只负责定位脚本目录并把控制权交给它，
使入口保持精简、--help 与退出码行为稳定。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main(argv=None) -> int:
    import core_impl

    return core_impl.main(argv)


if __name__ == "__main__":
    sys.exit(main())
