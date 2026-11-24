#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口：按 --cmd 分发到 core_impl。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.main(sys.argv[1:]))
