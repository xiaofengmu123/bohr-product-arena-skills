#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - sci-plan-arbiter 薄入口（实现见 core_impl.py）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
