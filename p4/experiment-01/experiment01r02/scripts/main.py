#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 统一入口：probe | scaffold | validate | all | selftest。

默认 all = input_probe → scaffold_result → validate_result，即最小可用路径
（先早落盘合法 partial，再校验）。纯标准库；通过子进程复用各脚本的既有 CLI，
不产生跨模块导入耦合。stdout/stderr 继承，避免管道捕获。
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(name, argv):
    return subprocess.run(
        [sys.executable, os.path.join(HERE, name)] + argv).returncode


def main():
    ap = argparse.ArgumentParser(description="sci-plan-timeboxed entry point.")
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["probe", "scaffold", "validate", "all", "selftest"])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    base = ["--input", args.input, "--output", args.output, "--work", args.work]

    if args.cmd == "selftest":
        return run("selftest.py", [])
    if args.cmd == "probe":
        return run("input_probe.py", ["--input", args.input, "--work", args.work])
    if args.cmd == "scaffold":
        return run("scaffold_result.py", base)
    if args.cmd == "validate":
        return run("validate_result.py", base)

    # all：先发现，再早落盘，最后校验（前 3 分钟内完成前两步）
    rc = run("input_probe.py", ["--input", args.input, "--work", args.work])
    if rc != 0:
        return rc
    rc = run("scaffold_result.py", base)
    if rc != 0:
        return rc
    return run("validate_result.py", base)


if __name__ == "__main__":
    sys.exit(main())
