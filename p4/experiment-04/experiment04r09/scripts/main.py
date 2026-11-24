#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口包装：真实实现见 core_impl.py（仅标准库）。

用法: python scripts/main.py <inspect|assemble|validate|selftest|demo> [--input DIR] [--output DIR] [--work DIR] [result.json]
本文件保持精简；core_impl.py 可独立运行，便于他人直接复用实现。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402

if __name__ == "__main__":
    sys.exit(core_impl.main(sys.argv[1:]))
