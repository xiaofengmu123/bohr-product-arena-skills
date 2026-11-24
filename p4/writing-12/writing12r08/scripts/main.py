#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — LaTeX 写作 Skill 的命令行入口：bootstrap / discover / finalize / selfcheck。

薄壳：具体逻辑在 core_impl.py，本文件只做参数解析与退出码控制。
- bootstrap / discover / finalize 对非致命失败一律退出码 0；
- selfcheck 在发现问题时退出码 1，以便调用方据此决定是否继续。
仅依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from core_impl import (
    build_skeleton, contract_keys, discover_input, finalize_result,
    find_case_id, read_json, reconfigure_stdio, utcnow, validate_result, write_json,
)


def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys = build_skeleton(args.input)
    write_json(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": keys,
        "files": discover_input(args.input),
        "generated_at": utcnow(),
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args):
    files = discover_input(args.input)
    print(json.dumps({"input_root": args.input, "files": files},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.isfile(result_path):
        skeleton, _ = build_skeleton(args.input)
        write_json(result_path, skeleton)
    final_obj, problems = finalize_result(result_path, args.work, args.input)
    print(json.dumps({
        "case_id": final_obj.get("case_id"),
        "status": final_obj.get("status"),
        "problems": problems,
        "result_path": result_path,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    problems = validate_result(result_path, args.work, args.input)
    print(json.dumps({
        "result_path": result_path,
        "problems": problems,
        "ok": not problems,
    }, ensure_ascii=False, indent=2))
    return 1 if problems else 0


COMMANDS = {
    "bootstrap": cmd_bootstrap,
    "discover": cmd_discover,
    "finalize": cmd_finalize,
    "selfcheck": cmd_selfcheck,
}


def build_parser():
    parser = argparse.ArgumentParser(
        description="LaTeX 写作 Skill：引导、发现、收尾与自检。")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    fn = COMMANDS[args.command]
    try:
        return int(fn(args))
    except Exception as exc:  # 非致命失败：落盘最小结果并退出码 0
        if args.command in ("bootstrap", "finalize"):
            os.makedirs(args.output, exist_ok=True)
            result_path = os.path.join(args.output, "result.json")
            if not os.path.isfile(result_path):
                skeleton, _ = build_skeleton(args.input)
                write_json(result_path, skeleton)
        print(json.dumps({"command": args.command, "fatal": str(exc)},
                         ensure_ascii=False, indent=2))
        return 0 if args.command != "selfcheck" else 1


if __name__ == "__main__":
    sys.exit(main())
