#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据溯源助手：核验引用片段是否出现在源材料中，并生成规范证据记录。

用法：
  python -B scripts/evidence.py verify --source <file> --quote "..."       # 单条核验
  python -B scripts/evidence.py verify-batch --quotes <json> --source <dir>
  python -B scripts/evidence.py make --locator "paper.md#L3-L5" --quote "..."

仅依赖 Python 标准库。逐字命中标 exact；空白归一化命中标 ws-normalized；
未命中标 verified=false（Agent 必须据此标 unverified，不得改结论）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

FILE_SIZE_LIMIT = 2 * 1024 * 1024


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def read_source(path: str):
    try:
        size = os.path.getsize(path)
        if size > FILE_SIZE_LIMIT:
            return None, "oversize: %s" % path
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), None
    except Exception as exc:  # noqa: BLE001
        return None, "read_failed: %s: %s" % (path, exc)


def exact_locator(text: str, quote: str):
    idx = text.find(quote)
    if idx < 0:
        return None
    start = text.count("\n", 0, idx) + 1
    end = start + quote.count("\n")
    return "L%d-L%d" % (start, end)


def ws_locator(text: str, quote: str):
    flat = normalize_ws(text)
    q = normalize_ws(quote)
    idx = flat.find(q)
    if idx < 0:
        return None
    line = flat[:idx].count("\n") + 1
    return "L%d(ws-normalized)" % line


def verify_one(source: str, quote: str) -> dict:
    text, err = read_source(source)
    if err:
        return {"source": source, "quote": quote, "verified": False,
                "mode": None, "locator": None, "error": err}
    loc = exact_locator(text, quote)
    if loc is not None:
        return {"source": source, "quote": quote, "verified": True,
                "mode": "exact", "locator": loc, "error": None}
    loc = ws_locator(text, quote)
    if loc is not None:
        return {"source": source, "quote": quote, "verified": True,
                "mode": "ws-normalized", "locator": loc, "error": None}
    return {"source": source, "quote": quote, "verified": False,
            "mode": None, "locator": None, "error": None}


def resolve_sources(source: str):
    """source 可以是文件或目录；目录则收集其下全部文本文件。"""
    if os.path.isfile(source):
        return [source]
    if os.path.isdir(source):
        exts = (".md", ".txt", ".tex", ".markdown", ".rst", ".csv")
        out = []
        for root, _dirs, files in os.walk(source):
            for name in sorted(files):
                if name.lower().endswith(exts):
                    out.append(os.path.join(root, name))
        return out
    return []


def verify_batch(quotes_file: str, source: str) -> list:
    try:
        with open(quotes_file, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return [{"error": "read_quotes_failed: %s" % exc}]

    items = data if isinstance(data, list) else data.get("quotes", [])
    sources = resolve_sources(source)
    if not sources:
        return [{"error": "no readable sources under: %s" % source}]

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        src = item.get("source") or source
        quote = item.get("quote") or ""
        target = src if os.path.isfile(src) else None
        if target is None:
            # 逐个候选文件查找
            hits = []
            for s in sources:
                rec = verify_one(s, quote)
                if rec["verified"]:
                    hits.append(rec)
            if hits:
                results.append(hits[0])
            else:
                results.append({"source": src, "quote": quote, "verified": False,
                                "mode": None, "locator": None, "error": None})
        else:
            results.append(verify_one(target, quote))
    return results


def make_evidence(locator: str, quote: str) -> dict:
    return {"locator": locator, "quote": (quote or "").strip()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evidence.py",
                                description="证据溯源与引用核验")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify")
    v.add_argument("--source", required=True)
    v.add_argument("--quote", required=True)

    vb = sub.add_parser("verify-batch")
    vb.add_argument("--quotes", required=True)
    vb.add_argument("--source", required=True)

    m = sub.add_parser("make")
    m.add_argument("--locator", required=True)
    m.add_argument("--quote", required=True)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "verify":
        rec = verify_one(args.source, args.quote)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    elif args.cmd == "verify-batch":
        recs = verify_batch(args.quotes, args.source)
        print(json.dumps(recs, ensure_ascii=False, indent=2))
    elif args.cmd == "make":
        print(json.dumps(make_evidence(args.locator, args.quote),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
