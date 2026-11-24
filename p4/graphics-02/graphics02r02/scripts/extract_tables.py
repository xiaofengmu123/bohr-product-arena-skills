#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse delimited / Markdown / whitespace tables into a locator-annotated JSON.

Standard library only. Detects the delimiter, infers column types, and flags
missing cells, mixed types, unit suffixes and suspicious magnitude gaps. Every
row carries its source line number so a figure glyph can be traced back to a
cell (figure -> data) and a cell back to a row (data -> figure).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys


def read_text(path, max_bytes=10_000_000):
    if not os.path.exists(path):
        return None, "missing"
    if os.path.getsize(path) > max_bytes:
        return None, "too_large"
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), "ok"
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "replaced"


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def detect_delim(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "ws"
    first = lines[0]
    if "|" in first:
        # Classic markdown has a |---| separator row; some extracted tables omit
        # it but still keep pipes on every row.
        if any(re.search(r"^\s*\|?[\s:|-]+\|[\s:|-]+\|?", ln) for ln in lines[1:3]):
            return "markdown"
        if len(lines) >= 2 and all("|" in ln for ln in lines[: min(len(lines), 6)]):
            return "markdown"
    counts = {
        "comma": first.count(","),
        "tab": first.count("\t"),
        "semicolon": first.count(";"),
    }
    best = max(counts, key=counts.get)
    if counts[best] >= 1:
        return best
    return "whitespace"


def parse_csv(text, delim):
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows, locs = [], []
    for rec in reader:
        rows.append([c.strip() for c in rec])
        locs.append(reader.line_num)
    return rows, locs


def parse_markdown(text):
    rows, locs = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{1,}:?", c) for c in cells):
            continue  # separator row like |---|---|
        rows.append(cells)
        locs.append(i)
    return rows, locs


def parse_whitespace(text):
    rows, locs = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        cells = re.split(r"\s{2,}|\t+", line.strip())
        if len(cells) <= 1:
            continue
        rows.append([c.strip() for c in cells])
        locs.append(i)
    return rows, locs


def parse_table(text, delim):
    if delim == "markdown":
        return parse_markdown(text)
    if delim in ("comma", "tab", "semicolon"):
        return parse_csv(text, {"comma": ",", "tab": "\t", "semicolon": ";"}[delim])
    return parse_whitespace(text)


def is_number_like(s):
    if s is None:
        return None
    t = s.replace(",", "").strip()
    if re.fullmatch(r"[+-]?\d+", t):
        return "int"
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?", t):
        return "float"
    return None


def build_columns(header, data_rows):
    ncols = max((len(r) for r in data_rows), default=len(header))
    cols = []
    for ci in range(ncols):
        name = header[ci] if ci < len(header) else "col%d" % (ci + 1)
        vals, parsed, units = [], [], set()
        for r in data_rows:
            v = r[ci].strip() if ci < len(r) else ""
            vals.append(v if v else None)
            kind = is_number_like(v)
            if kind is not None:
                parsed.append(int(v.replace(",", "")) if kind == "int" else float(v.replace(",", "")))
            else:
                parsed.append(None)
                m = re.match(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(\S.*)$", v)
                if m:
                    units.add(m.group(2))
        nonempty = [x for x in vals if x is not None]
        kinds = [is_number_like(x) for x in nonempty]
        if not nonempty:
            ctype = "empty"
        elif all(k == "int" for k in kinds):
            ctype = "int"
        elif all(k in ("int", "float") for k in kinds):
            ctype = "float"
        else:
            ctype = "text"
        cols.append({
            "name": name,
            "index": ci,
            "type": ctype,
            "values": vals,
            "parsed": parsed,
            "unit_suffixes": sorted(units),
            "missing_count": sum(1 for x in vals if x is None),
        })
    return cols


def detect_issues(columns):
    issues = []
    for col in columns:
        if col["missing_count"]:
            idxs = [ri for ri, v in enumerate(col["values"]) if v is None]
            issues.append({
                "kind": "missing",
                "column": col["name"],
                "column_index": col["index"],
                "row_indices": idxs[:20],
                "count": col["missing_count"],
            })
        if col["unit_suffixes"]:
            issues.append({
                "kind": "unit_suffix",
                "column": col["name"],
                "column_index": col["index"],
                "units": col["unit_suffixes"],
                "hint": "split numeric value from unit before encoding",
            })
        if col["type"] in ("int", "float"):
            nums = [v for v in col["parsed"] if isinstance(v, (int, float))]
            if nums:
                mx, mn = max(nums), min(nums)
                if mn != 0 and mx / abs(mn) > 1000:
                    issues.append({
                        "kind": "magnitude_gap",
                        "column": col["name"],
                        "column_index": col["index"],
                        "max": mx,
                        "min": mn,
                        "ratio": round(mx / abs(mn), 1),
                        "hint": "check unit/magnitude (e.g. ng/mL vs ug/mL)",
                    })
    return issues


def mapping_hints(columns):
    hints = []
    numeric = [c for c in columns if c["type"] in ("int", "float")]
    text = [c for c in columns if c["type"] == "text"]
    if numeric:
        hints.append({"kind": "value_axis_candidates", "columns": [c["name"] for c in numeric]})
    if text:
        hints.append({"kind": "category_or_group_candidates", "columns": [c["name"] for c in text]})
    for c in columns:
        low = c["name"].lower()
        if re.search(r"\b(n|sample|replicat|count|group)\b", low):
            hints.append({"kind": "sample_size_or_group", "column": c["name"]})
        if re.search(r"(sd|sem|se\b|ci\b|err|std|±)", low):
            hints.append({"kind": "error_column", "column": c["name"]})
    return hints


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--name", default=None, help="logical table name (e.g. fig2a)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--delim", default="auto", choices=["auto", "comma", "tab", "semicolon", "markdown", "whitespace"])
    a = ap.parse_args(argv)

    name = a.name or os.path.splitext(os.path.basename(a.input))[0]
    text, status = read_text(a.input)
    if status != "ok":
        result = {
            "name": name,
            "source": a.input,
            "status": status,
            "rows": [],
            "issues": [{"kind": status, "detail": "could not read input"}],
            "mapping_hints": [],
        }
        write_json(a.out, result)
        print(json.dumps({"ok": True, "out": a.out, "status": status, "issues": 1}, ensure_ascii=False))
        return 0

    delim = detect_delim(text) if a.delim == "auto" else a.delim
    rows, locs = parse_table(text, delim)
    if not rows:
        result = {
            "name": name,
            "source": a.input,
            "status": "empty",
            "delimiter": delim,
            "rows": [],
            "issues": [{"kind": "empty_table", "detail": "no rows parsed"}],
            "mapping_hints": [],
        }
        write_json(a.out, result)
        print(json.dumps({"ok": True, "out": a.out, "n_rows": 0, "issues": 1}, ensure_ascii=False))
        return 0

    header = rows[0]
    data_rows = rows[1:]
    data_locs = locs[1:] if rows else []
    columns = build_columns(header, data_rows)
    issues = detect_issues(columns)
    hints = mapping_hints(columns)

    row_objs = []
    for r, ln in zip(data_rows, data_locs):
        row_objs.append({"line": ln, "cells": r})

    result = {
        "name": name,
        "source": a.input,
        "status": "ok",
        "delimiter": delim,
        "assumes_first_row_is_header": True,
        "n_rows": len(data_rows),
        "n_cols": len(columns),
        "header": header,
        "columns": columns,
        "rows": row_objs,
        "issues": issues,
        "mapping_hints": hints,
    }
    write_json(a.out, result)
    print(json.dumps({
        "ok": True,
        "out": a.out,
        "delimiter": delim,
        "n_rows": len(data_rows),
        "n_cols": len(columns),
        "issues": len(issues),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
