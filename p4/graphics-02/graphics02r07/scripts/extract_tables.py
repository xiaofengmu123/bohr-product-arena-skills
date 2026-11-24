#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准库表格解析（S2）：CSV/TSV/Markdown/空白分隔 -> 列类型推断 + 缺失/量级检查 + 映射提示。

只依赖标准库。坏编码、空文件、超限文件、无法解析的行都降级为 issue 记录，绝不崩溃。
输出 JSON（确定性排序）供绘图步骤建立“图元 <- 源数据字段/行号”映射。

用法（相对 Skill 根目录）：
    python scripts/extract_tables.py --input /input/data.csv --name fig2a --out /app/work/t_fig2a.json
    python scripts/extract_tables.py --input /input/table.md --name tab1 --out /app/work/t_tab1.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import read_text_safe, write_json_safe, utcnow  # noqa: E402

DEFAULT_LIMIT = 20000  # 最多解析行数，超限截断并记录 issue


def sniff_delimiter(path: str, text: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    if ext == ".tsv":
        return "tsv"
    if ext == ".csv":
        return "csv"
    if ext in (".md", ".markdown"):
        return "markdown"
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if "\t" in first:
        return "tsv"
    if "," in first:
        return "csv"
    return "whitespace"


def parse_rows(path: str, text: str, delim: str, issues: list) -> list:
    lines = [ln.rstrip("\r") for ln in text.splitlines() if ln.strip()]
    if not lines:
        issues.append({"kind": "empty_input", "detail": "无有效行"})
        return []
    if delim == "csv":
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if any(c.strip() for c in r)]
    elif delim == "tsv":
        reader = csv.reader(io.StringIO(text), delimiter="\t")
        rows = [r for r in reader if any(c.strip() for c in r)]
    elif delim == "markdown":
        rows = []
        for ln in lines:
            if re.match(r"^\s*\|.*\|\s*$", ln):
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if cells and all(re.match(r"^:?-{2,}:?$", c) for c in cells):
                    continue  # 分隔行
                rows.append(cells)
    else:
        rows = [re.split(r"\s+", ln.strip()) for ln in lines]
    if not rows:
        issues.append({"kind": "empty_input", "detail": "未解析出数据行"})
    return rows


def _to_num(s: str):
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def infer_column(col_values: list) -> str:
    """整列全为可解析数值（空值除外）视为 numeric，否则 text。"""
    seen = 0
    for v in col_values:
        if v.strip() == "":
            continue
        if _to_num(v) is None:
            return "text"
        seen += 1
    return "numeric" if seen > 0 else "text"


def analyze(rows: list, issues: list) -> dict:
    if not rows:
        return {"columns": [], "issues": issues, "hints": []}
    n_cols = max(len(r) for r in rows)
    # 归一化列宽（缺列补空，保证行号/列号可回溯）
    norm = [list(r) + [""] * (n_cols - len(r)) for r in rows]
    header = norm[0]
    data = norm[1:] if len(norm) > 1 else []
    use_header = any(str(h).strip() for h in header) and not all(
        _to_num(str(h)) is not None for h in header if str(h).strip())

    columns = []
    for j in range(n_cols):
        name = str(header[j]).strip() if use_header else ("col_%d" % (j + 1))
        vals = [str(row[j]) for row in (data if use_header else norm)]
        ctype = infer_column(vals)
        missing = sum(1 for v in vals if v.strip() == "")
        col = {
            "index": j, "name": name, "type": ctype,
            "missing": missing, "n": len(vals),
            "source_rows": "header=row1, data=row%d..row%d" % (
                (2 if use_header else 1), len(norm)),
        }
        if ctype == "numeric":
            nums = [_to_num(v) for v in vals if v.strip() != "" and _to_num(v) is not None]
            if nums:
                col["min"] = min(nums)
                col["max"] = max(nums)
                nonzero = [abs(x) for x in nums if abs(x) > 0]
                if nonzero and max(nonzero) / max(min(nonzero), 1e-300) > 1e4:
                    issues.append({
                        "kind": "magnitude_span", "column": name,
                        "detail": "数值列量级跨度 >1e4，请核对单位（如 ng/mL vs ug/mL、% 与小数）",
                    })
        columns.append(col)

    hints = _mapping_hints(columns, use_header)
    return {"columns": columns, "issues": issues, "hints": hints}


def _mapping_hints(columns: list, use_header: bool) -> list:
    hints = []
    numeric = [c for c in columns if c["type"] == "numeric"]
    text = [c for c in columns if c["type"] == "text"]
    if len(text) >= 1 and len(numeric) >= 1:
        hints.append("categorical+数值列 -> 柱状/条形图（x=%s, y=数值列）" % text[0]["name"])
    if len(numeric) >= 2:
        hints.append(">=2 数值列 -> 折线/散点图候选（检查第一列是否单调递增）")
    if len(text) >= 2 and len(numeric) >= 2:
        hints.append("文本矩阵 -> 热图候选（行=类别, 列=数值系列）")
    if not hints:
        hints.append("未见清晰类别-数值结构，建议人工核对表头与转置")
    return hints


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="extract_tables.py", description="标准库表格解析")
    ap.add_argument("--input", required=True, help="CSV/TSV/Markdown/空白分隔文件路径")
    ap.add_argument("--name", default="table", help="输出名（用于 result 标识）")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = ap.parse_args(argv)

    issues = []
    text = read_text_safe(args.input, args.max_bytes, issues)
    if text is None:
        write_json_safe(args.out, {
            "name": args.name, "source": args.input, "ok": False,
            "issues": issues, "generated_utc": utcnow(),
        }, None)
        print(json.dumps({"ok": False, "issues": issues}, ensure_ascii=False))
        return 0

    delim = sniff_delimiter(args.input, text)
    rows = parse_rows(args.input, text, delim, issues)
    if len(rows) > args.limit:
        issues.append({"kind": "truncated", "detail": "行数 %d 超过 limit %d，截断处理" % (len(rows), args.limit)})
        rows = rows[:args.limit]
    result = analyze(rows, issues)
    result.update({
        "name": args.name, "source": args.input, "delimiter": delim,
        "n_rows": len(rows), "n_cols": len(result["columns"]),
        "ok": True, "generated_utc": utcnow(),
    })
    write_json_safe(args.out, result, None)
    print(json.dumps({
        "ok": True, "name": args.name, "n_rows": result["n_rows"],
        "n_cols": result["n_cols"], "issues": issues, "out": args.out,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
