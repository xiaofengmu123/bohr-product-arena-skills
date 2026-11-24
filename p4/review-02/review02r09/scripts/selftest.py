# -*- coding: utf-8 -*-
"""selftest.py — 独立自检：校验 result.json 契约与逐字 quote（仅标准库）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as impl  # noqa: E402


def _opt(args, name):
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    output = _opt(args, "--output") or impl.DEFAULT_OUTPUT
    work = _opt(args, "--work") or impl.DEFAULT_WORK
    raise SystemExit(impl.cmd_selftest(output, work))
