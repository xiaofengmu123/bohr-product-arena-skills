#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-contract -- 入口包装器（仅标准库）。

解析命令行并分发到同目录 core_impl.py 的实现。四个子命令：
  init      落盘契约对齐的 result.json 骨架
  profile   生成数据画像 data_profile.json
  validate  校验 result.json 的契约对齐与产物路径
  selftest  运行四类健壮性自检

调用示例（从 Skill 包根执行）：
  python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main(argv=None):
    parser = core_impl.build_parser()
    args = parser.parse_args(argv)
    return core_impl.dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
