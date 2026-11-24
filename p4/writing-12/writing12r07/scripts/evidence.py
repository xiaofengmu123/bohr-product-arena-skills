#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence.py — 事实-来源映射：索引 / 逐字引用 / 定位 / 事实抽取。

三个子命令：
  index  扫描 /input 建立 source_index.json：文件清单 + 文本行号偏移 + sha256，
         供 result.json 的 sources/uncertainty 以 locator（file#L 行）回溯。
  quote  读取指定文件的 N..M 行，输出逐字片段（用于 sources[].quote）。
  locate 在 /input 全部文本文件中检索给定词/正则，返回 locator 列表，
         便于把「关键数值/术语/引用」指回材料原文。

仅依赖 Python 标准库。对缺失文件/坏编码/超大文件优雅降级，退出码 0。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml", ".rst",
    ".cls", ".sty", ".cfg", ".lst",
}


def reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds %d bytes" % MAX_FILE_BYTES
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def sha256_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def iter_text_files(root):
    """确定性遍历 /input 下可读文本文件。"""
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in TEXT_EXTS:
                continue
            text, note = read_text(full)
            if text is None:
                continue
            yield full, text, note


def cmd_index(args):
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    files = []
    for full, text, note in iter_text_files(args.root):
        lines = text.splitlines()
        rel = os.path.relpath(full, args.root).replace("\\", "/")
        files.append({
            "path": full,
            "rel": rel,
            "lines": len(lines),
            "sha256": sha256_of(text.encode("utf-8", errors="replace")),
            "note": note,
            # 供 quote 使用的行边界快照：记录每行首字符偏移（行内 0 基）
            "line_offsets": _line_offsets(text),
        })
    index = {
        "root": os.path.abspath(args.root),
        "file_count": len(files),
        "files": files,
    }
    fd = args.out
    with open(fd, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"source_index": os.path.abspath(fd), "file_count": len(files)},
                     ensure_ascii=False, indent=2))
    return 0


def _line_offsets(text):
    """每行起始字符偏移，用于快速定位（避免整份重读）。"""
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def cmd_quote(args):
    text, note = read_text(args.file)
    if text is None:
        print(json.dumps({"error": note, "quote": None}, ensure_ascii=False, indent=2))
        return 0
    lines = text.splitlines()
    start = max(1, args.start)
    end = min(len(lines), args.end)
    if start > end:
        print(json.dumps({"error": "start>end", "quote": None}, ensure_ascii=False, indent=2))
        return 0
    selected = lines[start - 1:end]
    print(json.dumps({
        "file": os.path.abspath(args.file),
        "locator": "%s#L%d-L%d" % (args.file, start, end),
        "start": start,
        "end": end,
        "quote": "\n".join(selected),
        "note": note,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_locate(args):
    pattern = args.pattern
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        print(json.dumps({"error": "invalid regex: %s" % exc, "matches": []},
                         ensure_ascii=False, indent=2))
        return 0
    matches = []
    for full, text, _note in iter_text_files(args.root):
        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = os.path.relpath(full, args.root).replace("\\", "/")
                matches.append({
                    "locator": "%s#L%d" % (rel, lineno),
                    "snippet": line[:200],
                })
                if args.limit and len(matches) >= args.limit:
                    break
        if args.limit and len(matches) >= args.limit:
            break
    print(json.dumps({"pattern": pattern, "match_count": len(matches),
                      "matches": matches}, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="evidence.py",
        description="事实-来源映射：index / quote / locate",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_idx = sub.add_parser("index", help="扫描 /input 建立 source_index.json")
    p_idx.add_argument("--root", required=True, help="输入目录")
    p_idx.add_argument("--out", required=True, help="source_index.json 输出路径")
    p_idx.set_defaults(func=cmd_index)

    p_quote = sub.add_parser("quote", help="逐字引用文件 N..M 行")
    p_quote.add_argument("--file", required=True)
    p_quote.add_argument("--start", type=int, required=True)
    p_quote.add_argument("--end", type=int, required=True)
    p_quote.set_defaults(func=cmd_quote)

    p_loc = sub.add_parser("locate", help="检索词/正则并返回 locator")
    p_loc.add_argument("--root", required=True)
    p_loc.add_argument("--pattern", required=True)
    p_loc.add_argument("--limit", type=int, default=20)
    p_loc.set_defaults(func=cmd_locate)
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, 2) else exc.code
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
