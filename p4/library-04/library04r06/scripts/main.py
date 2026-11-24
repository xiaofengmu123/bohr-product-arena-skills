#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — research-kb-qa Skill 的薄入口（实现见 core_impl.py）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    import core_impl
    return core_impl.run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())