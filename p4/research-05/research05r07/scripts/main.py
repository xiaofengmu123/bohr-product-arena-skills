"""main.py — 科研选题与创新性评估 Skill 的入口封装。

用法（在 /skills/weekly-skill 下，全部用相对路径调用）：
    python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py selftest

--input/--output/--work 缺省分别指向 /input、/app/output、/app/work。
未知参数只告警不崩溃；缺少子命令时打印帮助并返回 0。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import (  # noqa: E402
    DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK,
    run_skeleton, run_evidence, run_finalize, run_selftest,
)


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="科研选题与创新性评估 Skill：先落盘 partial 骨架，再做证据发现与增量合并。",
    )
    sub = parser.add_subparsers(dest="command")
    specs = {
        "skeleton": "阶段0：先写合法 result.json 骨架（前3分钟内必须完成）",
        "evidence": "阶段1：发现材料、抽取 PDF/文本、刷新 sources",
        "finalize": "阶段4：合并 fragments、校验契约、生成 report.md",
        "selftest": "自检：空输入/坏JSON/缺参数/超大文件/坏编码的降级验证",
    }
    for name, help_text in specs.items():
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--input", default=DEFAULT_INPUT, help="输入目录，缺省 /input")
        sp.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录，缺省 /app/output")
        sp.add_argument("--work", default=DEFAULT_WORK, help="工作目录，缺省 /app/work")
    return parser


def main(argv=None):
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print("[main] warn: ignored unknown args: %s" % " ".join(unknown), file=sys.stderr)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    dispatch = {
        "skeleton": run_skeleton,
        "evidence": run_evidence,
        "finalize": run_finalize,
        "selftest": run_selftest,
    }
    fn = dispatch[args.command]
    if args.command == "selftest":
        return fn()
    return fn(args.input, args.output, args.work)


if __name__ == "__main__":
    sys.exit(main())
