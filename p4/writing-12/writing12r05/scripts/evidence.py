#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence.py — 材料索引与逐字取证。

职责：
  1. index：扫描 /input，抽取文本材料的行号定位（path#L行号），产出索引；
  2. quote：按行号取出某文件片段，附带行号（用于 sources 的逐字 quote）；
  3. match：按正则在某文件内定位含关键词的行，方便 Agent 找证据。

仅依赖 Python 标准库。PDF 需先经平台 pdf-text-tool 转出 .txt/.md 后再索引；
本脚本对 .pdf 本身只记录文件名与大小，不做内容猜测。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
             ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst"}


def reconfigure_stdio():
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
        return None, ["read_error: %s" % exc]
    if len(raw) > MAX_FILE_BYTES:
        return None, ["skipped: exceeds 2MiB"]
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ["decoded_with_replacement"]


def rel_of(path, root):
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path


def cmd_index(args):
    entries = []
    notes = []
    if not os.path.isdir(args.root):
        notes.append("input_root_not_found:" + args.root)
    else:
        for dirpath, dirnames, filenames in os.walk(args.root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = rel_of(full, args.root)
                ext = os.path.splitext(name)[1].lower()
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                entry = {"rel": rel, "path": full, "ext": ext, "size": size,
                         "is_text": ext in TEXT_EXTS, "lines": None}
                if ext in TEXT_EXTS:
                    text, errs = read_text(full)
                    notes.extend(["%s: %s" % (rel, e) for e in errs])
                    if text is not None:
                        entry["lines"] = len(text.splitlines())
                entries.append(entry)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"root": args.root, "generated_at": _now(),
                   "notes": notes, "files": entries}, fh,
                  ensure_ascii=False, indent=2)
    print(json.dumps({"files": len(entries), "notes": notes,
                      "index_path": args.out}, ensure_ascii=False, indent=2))
    return 0


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_quote(args):
    text, errs = read_text(args.file)
    if text is None:
        print(json.dumps({"error": errs}, ensure_ascii=False, indent=2))
        return 0
    lines = text.splitlines()
    start = max(1, args.start)
    end = args.end if args.end and args.end > 0 else (start + args.lines - 1)
    end = min(end, len(lines)) if lines else start
    if start > len(lines):
        excerpt = []
        note = "start_beyond_eof"
    else:
        excerpt = [{"line": i, "text": lines[i - 1]} for i in range(start, end + 1)]
        note = None
    print(json.dumps({"file": args.file, "quote": excerpt, "note": note,
                      "total_lines": len(lines), "encoding_errors": errs},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_match(args):
    text, errs = read_text(args.file)
    if text is None:
        print(json.dumps({"error": errs}, ensure_ascii=False, indent=2))
        return 0
    try:
        pattern = re.compile(args.pattern, re.IGNORECASE)
    except re.error as exc:
        print(json.dumps({"error": ["bad_regex: %s" % exc]}, ensure_ascii=False, indent=2))
        return 0
    hits = []
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            hits.append({"line": i, "text": line[:500]})
            if len(hits) >= args.limit:
                break
    print(json.dumps({"file": args.file, "pattern": args.pattern,
                      "hits": hits, "total_hits_capped": len(hits) == args.limit},
                     ensure_ascii=False, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="evidence.py",
        description="材料索引与逐字取证（仅标准库）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("index", help="扫描 /input 并生成带行号的来源索引")
    sp.add_argument("--root", default="/input")
    sp.add_argument("--out", default="/app/work/source_index.json")

    sp = sub.add_parser("quote", help="按行号取出文件片段（逐字）")
    sp.add_argument("--file", required=True)
    sp.add_argument("--start", type=int, required=True)
    sp.add_argument("--end", type=int, default=0)
    sp.add_argument("--lines", type=int, default=1)

    sp = sub.add_parser("match", help="定位含关键词的行")
    sp.add_argument("--file", required=True)
    sp.add_argument("--pattern", required=True)
    sp.add_argument("--limit", type=int, default=20)
    return p


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    return {"index": cmd_index, "quote": cmd_quote, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
