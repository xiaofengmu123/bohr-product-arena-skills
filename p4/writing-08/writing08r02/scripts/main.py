#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口包装：把子命令分发到 core_impl。用法见 SKILL.md 第 4/9 节。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import core_impl  # noqa: E402
except Exception as e:  # noqa: BLE001 缺失实现时给可读错误，不静默
    sys.stderr.write("core_impl import failed: {}\n".format(repr(e)))
    sys.exit(1)

if __name__ == "__main__":
    core_impl.run()
