#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 分发入口（仅标准库）。子命令：skeleton / evidence / finalize / run。"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402

COMMANDS = {
    'skeleton': core_impl.run_skeleton,
    'evidence': core_impl.run_evidence,
    'finalize': core_impl.run_finalize,
    'run': core_impl.run_all,
}


def build_parser():
    p = argparse.ArgumentParser(
        prog='main.py',
        description='科研选题与创新性评估 Skill：skeleton / evidence / finalize 三阶段。')
    p.add_argument('command', nargs='?', choices=sorted(COMMANDS),
                   help='skeleton | evidence | finalize | run（省略时打印帮助）')
    p.add_argument('--input', default='/input', help='输入目录（默认 /input）')
    p.add_argument('--output', default='/app/output', help='输出目录（默认 /app/output）')
    p.add_argument('--work', default='/app/work', help='工作目录（默认 /app/work）')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 0
    fn = COMMANDS[args.command]
    try:
        return int(fn(args.input, args.output, args.work) or 0)
    except Exception as exc:  # 最后兜底：不让 harness 崩溃
        sys.stderr.write('unhandled error: %s\n' % exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
