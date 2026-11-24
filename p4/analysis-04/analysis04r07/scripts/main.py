#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec-robust 命令行入口（纯标准库）。

用法（在 Skill 包根 /skills/weekly-skill 下执行，路径用相对路径）：
    python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py align  --dict /app/work/data_dictionary.json --input /input --output /app/output
    python3 -B scripts/main.py snapshot --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py validate --input /input --output /app/output
    python3 -B scripts/main.py log   --output /app/output --step clean --action "去重后 150 行"
    python3 -B scripts/main.py set   --output /app/output --key cleaned_table --value result_clean.csv
    python3 -B scripts/main.py run   --input /input --output /app/output --work /app/work

设计约定：缺参数/未知参数/坏数据一律不崩溃，退出码恒为 0；异常全部记录到
result.json 的 errors/degradations/change_log，而不是抛给 shell。
"""
from __future__ import annotations

import argparse
import sys

import core_impl as C


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=None, help="输入目录，缺省取 $SKILL_INPUT_DIR 或 /input")
    common.add_argument("--output", default=None, help="产物目录，缺省取 $SKILL_OUTPUT_DIR 或 /app/output")
    common.add_argument("--work", default=None, help="工作目录，缺省取 $SKILL_WORK_DIR 或 /app/work")
    common.add_argument("--case", default=None, help="显式指定 case.json 路径（缺省自动发现）")

    parser = argparse.ArgumentParser(
        prog="scripts/main.py",
        parents=[common],
        description="科研数据分析鲁棒执行链：init/profile/align/snapshot/validate/log/set/run",
    )
    parser.add_argument("--version", action="store_true", help="打印版本后退出")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("init", parents=[common], help="落盘 partial 结果骨架（前 3 分钟必做）")
    sub.add_parser("profile", parents=[common], help="扫描材料并输出 data_profile.json")
    sub.add_parser("snapshot", parents=[common], help="复制原始材料到 work/snapshot/ 并记录 sha256")
    sub.add_parser("run", parents=[common], help="一键 init -> profile -> validate")

    valp = sub.add_parser("validate", parents=[common], help="校验 result.json 并输出 validation_report.json")
    valp.add_argument("--result", default=None, help="显式 result.json 路径（缺省 <output>/result.json）")

    alignp = sub.add_parser("align", parents=[common], help="按数据字典做字段对齐，输出 alignment.json/.md")
    alignp.add_argument("--dict", default=None, help="数据字典 JSON 路径（含 fields[]）")

    logp = sub.add_parser("log", parents=[common], help="向 change_log 追加一条记录（增量改进不手改 JSON）")
    logp.add_argument("--step", default=None)
    logp.add_argument("--action", default=None)
    logp.add_argument("--status", default=None, choices=C.ALLOWED_STATUSES)
    logp.add_argument("--note", default=None)

    setp = sub.add_parser("set", parents=[common], help="以类型安全方式设置 result.json 的某个字段")
    setp.add_argument("--key", default=None)
    setp.add_argument("--value", default=None)
    setp.add_argument("--kind", default="str", choices=["str", "int", "float", "bool", "json"])

    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    parser = build_parser()
    try:
        args, _unknown = parser.parse_known_args(argv)
    except SystemExit:
        # --help 与非法子命令：打印用法后按鲁棒约定返回 0。
        return 0

    if getattr(args, "version", False):
        print(C.VERSION)
        return 0

    inp, out, work = C.resolve_paths(args)
    C.ensure_dirs(out, work)

    cmd = args.command or "help"
    try:
        if cmd == "init":
            C.run_init(inp, out, work, getattr(args, "case", None))
            return 0
        if cmd == "profile":
            C.run_profile(inp, out, work)
            return 0
        if cmd == "snapshot":
            C.run_snapshot(inp, work, out)
            return 0
        if cmd == "validate":
            C.run_validate(inp, out, getattr(args, "result", None))
            return 0
        if cmd == "align":
            dict_path = getattr(args, "dict", None)
            if not dict_path:
                sys.stderr.write("align 需要 --dict <dictionary.json>；未提供则跳过（退出码 0）。\n")
                C.trace(out, "align", "align_skipped_missing_dict")
                return 0
            C.run_align(dict_path, inp, out)
            return 0
        if cmd == "log":
            C.run_log(out, getattr(args, "step", None), getattr(args, "action", None),
                      getattr(args, "status", None), getattr(args, "note", None))
            return 0
        if cmd == "set":
            C.run_set(out, getattr(args, "key", None), getattr(args, "value", None),
                      getattr(args, "kind", "str"))
            return 0
        if cmd == "run":
            C.run_all(inp, out, work, getattr(args, "case", None))
            return 0
        parser.print_help()
        return 0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("unexpected_error:%s\n" % e)
        # 最终兜底：无论如何都尝试写一份最小合法 result.json。
        try:
            C.ensure_minimal_result(out, work)
        except Exception:  # noqa: BLE001
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
