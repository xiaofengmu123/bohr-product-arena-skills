#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 主入口（薄 CLI，纯标准库）。

只负责解析参数并把子命令分派给 core_impl.dispatch()。核心逻辑见 core_impl.py。

子命令：run / skeleton / parse / plan / import / evidence / uncertainty / assemble / selfcheck
全局与子命令均接受 --input/--output/--work（缺省 /input、/app/output、/app/work）。
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=core_impl.DEFAULT_INPUT)
    common.add_argument("--output", default=core_impl.DEFAULT_OUTPUT)
    common.add_argument("--work", default=core_impl.DEFAULT_WORK)

    p = argparse.ArgumentParser(
        description="学术文献检索 Skill：查询规划、召回、去重、证据溯源、不确定性显式化",
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd")

    for name in ("run", "skeleton", "parse", "plan", "assemble", "selfcheck"):
        sub.add_parser(name, parents=[common])

    ip = sub.add_parser("import", parents=[common])
    ip.add_argument("--file")
    ip.add_argument("--inline")

    ep = sub.add_parser("evidence", parents=[common])
    ep.add_argument("--inline", required=True)

    up = sub.add_parser("uncertainty", parents=[common])
    up.add_argument("--inline", required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 0
    return core_impl.dispatch(args)


if __name__ == "__main__":
    sys.exit(main())