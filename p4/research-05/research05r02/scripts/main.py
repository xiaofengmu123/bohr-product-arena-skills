# -*- coding: utf-8 -*-
"""科研选题与创新性评估 Skill 的统一入口。

用法（在 /skills/weekly-skill 下，默认路径指向运行时目录）：
    python3 -B scripts/main.py skeleton  [--input /input --output /app/output --work /app/work]
    python3 -B scripts/main.py evidence
    python3 -B scripts/main.py finalize
    python3 -B scripts/main.py bump --work /app/work   # 记录一次模型请求（自报台账）
    python3 -B scripts/main.py selftest                 # 提交前自检

所有命令退出码为 0；契约校验失败只写入 result.json 的 errors 字段，不使进程崩溃。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研选题与创新性评估 Skill 管线（skeleton/evidence/finalize/selftest/bump）",
    )
    p.add_argument(
        "mode", nargs="?",
        choices=["skeleton", "evidence", "finalize", "selftest", "bump"],
        help="要执行的阶段；省略时打印帮助",
    )
    p.add_argument("--input", default="/input", help="输入目录，默认 /input")
    p.add_argument("--output", default="/app/output", help="输出目录，默认 /app/output")
    p.add_argument("--work", default="/app/work", help="工作目录，默认 /app/work")
    p.add_argument("--note", default="", help="bump 模式的请求备注")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mode is None:
        build_parser().print_help()
        return 0
    if args.mode == "selftest":
        import selftest
        return selftest.main()
    if args.mode == "bump":
        C.bump_request(args.work, args.note or "model_request")
        print("requests_used=%d" % C.get_ledger_count(args.work))
        return 0
    return C.run(args.mode, args.input, args.output, args.work)


if __name__ == "__main__":
    sys.exit(main())
