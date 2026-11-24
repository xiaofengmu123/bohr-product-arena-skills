#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — “实验结果解释与异常分析” Skill 主入口（仅标准库）。

子命令（位置参数，flags 可写在子命令前或后）：
  skeleton   P0：立即落盘合法 partial 骨架（result.json + report.md + run_state.json）
  inspect    P1：扫描 /input，生成 data_profile.json（文件清单 + 数值画像 + 降级）
  validate   校验 result.json 的 schema 与证据完整性（退出码 0=通过）
  finalize   P3：补 runtime / change_log，渲染最终 report.md 并自检
  checkpoint 更新预算台账（阶段 / 请求数），打印剩余额度

默认路径：--input /input，--output /app/output，--work /app/work。
对 validate / finalize，`--input` 语义为 result.json 路径。
所有命令均确定性执行（固定排序、无随机数）。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import (  # noqa: E402
    DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK,
    build_skeleton, inspect_data, validate, finalize, checkpoint,
)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="实验结果解释与异常分析 Skill：骨架/画像/校验/定稿/预算记账")
    p.add_argument("cmd", choices=("skeleton", "inspect", "validate", "finalize",
                                   "checkpoint"))
    p.add_argument("--input", dest="input_path", default=DEFAULT_INPUT,
                   help="输入目录；对 validate/finalize 为 result.json 路径")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    p.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    p.add_argument("--phase", default=None, help="checkpoint 用：当前阶段 P0-P3")
    p.add_argument("--requests-used", type=int, default=None,
                   help="checkpoint 用：已用模型请求数")

    args = p.parse_args(argv)
    cmd = args.cmd
    if cmd == "skeleton":
        build_skeleton(args.input_path, args.output, args.work)
        return 0
    if cmd == "inspect":
        inspect_data(args.input_path, args.output, args.work)
        return 0
    if cmd == "validate":
        ok, _ = validate(args.input_path)
        return 0 if ok else 1
    if cmd == "finalize":
        return 0 if finalize(args.input_path, args.work) else 1
    if cmd == "checkpoint":
        checkpoint(args.work, args.phase, args.requests_used)
        return 0
    p.error("unknown command: %s" % cmd)


if __name__ == "__main__":
    sys.exit(main())
