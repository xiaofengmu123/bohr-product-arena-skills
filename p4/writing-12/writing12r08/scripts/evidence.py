#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence.py — 证据索引与逐字引用（仅标准库）。

- index : 扫描 /input 下文本材料，按相对路径建立逐行索引，写到 source_index.json
- quote : 从指定文件取 [start, end] 行（1-based，含边界）的逐字片段

用途：为 result.json 的 sources 提供 locator + 逐字 quote，做到可逐条回溯。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_impl import index_evidence, quote_evidence, reconfigure_stdio, write_json


def cmd_index(args):
    index = index_evidence(args.root, args.out)
    print(json.dumps({"out": args.out, "file_count": len(index["files"])},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_quote(args):
    text, meta = quote_evidence(args.file, args.start, args.end)
    print(json.dumps({"text": text, "meta": meta}, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="证据索引与逐字引用。")
    sub = parser.add_subparsers(dest="command", required=True)
    p1 = sub.add_parser("index")
    p1.add_argument("--root", required=True)
    p1.add_argument("--out", required=True)
    p1.set_defaults(func=cmd_index)
    p2 = sub.add_parser("quote")
    p2.add_argument("--file", required=True)
    p2.add_argument("--start", type=int, required=True)
    p2.add_argument("--end", type=int, required=True)
    p2.set_defaults(func=cmd_quote)
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(json.dumps({"fatal": str(exc)}, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
