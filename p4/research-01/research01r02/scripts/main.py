#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 深度研究 Skill 的命令行入口（仅标准库）。

所有子命令输出统一为 JSON，且退出码恒为 0（异常被记录而非崩溃），
便于 DeepSeek Harness 中的 Agent 解析与继续增量推进。用法见 SKILL.md。

子命令：
  discover / skeleton / checkpoint / trace / merge / finalize / selfcheck
通用参数：--input（缺省 /input）、--output（缺省 /app/output）、--work（缺省 /app/work）。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                        help="输入目录（默认 /input）")
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                        help="输出目录（默认 /app/output）")
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"),
                        help="工作目录（默认 /app/work）")


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="深度研究 Skill 运行助手")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_d = sub.add_parser("discover", help="列出输入清单与 output_contract 字段")
    add_common(p_d)

    p_s = sub.add_parser("skeleton", help="落盘早期 partial 结果骨架 + run_state")
    add_common(p_s)

    p_c = sub.add_parser("checkpoint", help="阶段记账与预算自检")
    add_common(p_c)
    p_c.add_argument("--phase", choices=sorted(R.PHASE_DEADLINES), default=None,
                     help="当前/即将进入的阶段 P0..P4")
    p_c.add_argument("--requests", type=int, default=None, help="已使用的模型请求数")
    p_c.add_argument("--note", default="", help="阶段说明")

    p_t = sub.add_parser("trace", help="追加一条工具调用记录到 tool_ledger.jsonl")
    add_common(p_t)
    p_t.add_argument("--action", default="", help="工具动作，如 search/fetch/pdf_text")
    p_t.add_argument("--params", default=None, help="JSON 参数或自由文本")
    p_t.add_argument("--outcome", default="", help="ok/error/limited 等")
    p_t.add_argument("--summary", default="", help="返回结果摘要")
    p_t.add_argument("--note", default="", help="补充说明")

    p_m = sub.add_parser("merge", help="增量合并 payload 到 result.json（列表只追加）")
    add_common(p_m)
    p_m.add_argument("--payload", required=True, help="payload JSON 路径")

    p_f = sub.add_parser("finalize", help="汇总并生成 report.md/evidence_map.json")
    add_common(p_f)

    p_ck = sub.add_parser("selfcheck", help="六点覆盖自检")
    add_common(p_ck)

    return parser


def _parse_jsonish(text):
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "discover":
            emit(R.run_discover(args.input))
        elif args.cmd == "skeleton":
            emit(R.run_skeleton(args.input, args.output, args.work))
        elif args.cmd == "checkpoint":
            emit(R.run_checkpoint(args.output, args.work, args.phase,
                                  args.requests, args.note))
        elif args.cmd == "trace":
            emit(R.run_trace(args.work, args.action,
                             _parse_jsonish(args.params), args.outcome,
                             args.summary, args.note))
        elif args.cmd == "merge":
            emit(R.run_merge(args.output, args.work, args.payload))
        elif args.cmd == "finalize":
            emit(R.run_finalize(args.input, args.output, args.work))
        elif args.cmd == "selfcheck":
            emit(R.run_selfcheck(args.input, args.output, args.work))
        else:
            emit({"ok": False, "error": "未知命令：%s" % args.cmd})
    except Exception as exc:  # 保证任何意外都不使进程崩溃
        emit({"ok": False, "fatal": True,
              "error": "%s: %s" % (type(exc).__name__, exc)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
