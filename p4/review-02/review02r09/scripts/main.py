# -*- coding: utf-8 -*-
"""main.py — 入口（实现见 core_impl.py）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(impl.main(sys.argv[1:]))
