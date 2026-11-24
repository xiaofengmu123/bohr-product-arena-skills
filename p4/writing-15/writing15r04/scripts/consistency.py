#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨章节一致性助手：扫描材料，抽取章节/图表/公式编号与术语候选，
写入 registry JSON，并做确定性检查（重复编号、缺号、同缩写异定义）。

用法：
  python -B scripts/consistency.py scan --input /input --work /app/work
  python -B scripts/consistency.py check --registry <json> [--registry ...]

仅依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

TEXT_EXTS = (".md", ".txt", ".tex", ".markdown", ".rst", ".csv")
FILE_SIZE_LIMIT = 2 * 1024 * 1024

SECTION_RES = [
    re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$", re.MULTILINE),
    re.compile(r"^\s*(\d+(?:\.\d+){0,4})[、.．]\s*(\S.{0,80}?)\s*$", re.MULTILINE),
    re.compile(r"^\s*第([一二三四五六七八九十百千〇零]+)\s*[章节部分]\s*([^\n]{0,80})?$", re.MULTILINE),
]

FIG_RE = re.compile(r"图\s*([0-9]+(?:[.\-][0-9]+){0,2})")
TAB_RE = re.compile(r"表\s*([0-9]+(?:[.\-][0-9]+){0,2})")
EQ_RE = re.compile(r"式\s*[（(]?\s*([0-9]+(?:\.[0-9]+){0,2})\s*[)）]?")
FIG_EN_RE = re.compile(r"\b(?:Figure|Fig\.?)\s*([0-9]+(?:\.[0-9]+){0,2})", re.IGNORECASE)
TAB_EN_RE = re.compile(r"\bTable\s*([0-9]+(?:\.[0-9]+){0,2})", re.IGNORECASE)

TERM_ZH_RE = re.compile(
    r"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9·\-]{1,39})\s*[（(]\s*([A-Za-z][A-Za-z0-9\-]{1,19})\s*[)）]")
TERM_EN_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9\-]{2,39})\s*[（(]\s*([A-Za-z][A-Za-z0-9\-]{1,19})\s*[)）]")

TERM_STOPWORDS = {"图", "表", "式", "节", "章", "页", "注", "例", "题", "法", "段", "文"}


def read_text_safe(path: str):
    try:
        size = os.path.getsize(path)
        if size > FILE_SIZE_LIMIT:
            return None, "oversize: %s" % path
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), None
    except Exception as exc:  # noqa: BLE001
        return None, "read_failed: %s: %s" % (path, exc)


def line_no(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def extract_sections(text: str, source: str) -> list:
    out = []
    for regex in SECTION_RES:
        for m in regex.finditer(text):
            g = m.groups()
            if regex is SECTION_RES[0]:
                num, title = g[0], g[1].strip()
            elif regex is SECTION_RES[1]:
                num, title = g[0], g[1].strip()
            else:
                num, title = g[0], (g[1] or "").strip()
            out.append({"number": num, "title": title, "locator": "L%d" % line_no(text, m.start()),
                        "source": source})
    return out


def extract_numbers(text: str, source: str, regex, kind: str) -> list:
    return [{"number": m.group(1), "kind": kind,
             "locator": "L%d" % line_no(text, m.start()), "source": source}
            for m in regex.finditer(text)]


def extract_terms(text: str, source: str) -> list:
    out = []
    seen = set()
    for regex in (TERM_ZH_RE, TERM_EN_RE):
        for m in regex.finditer(text):
            term = m.group(1).strip()
            abbr = m.group(2).strip()
            key = (term.lower(), abbr.lower())
            if key in seen:
                continue
            seen.add(key)
            if term in TERM_STOPWORDS or len(term) < 2 or len(abbr) < 2:
                continue
            out.append({"term": term, "abbr": abbr,
                        "locator": "L%d" % line_no(text, m.start()), "source": source})
    return out


def parse_number(number: str):
    parts = re.split(r"[.\-]", number)
    try:
        return tuple(int(p) for p in parts if p.isdigit())
    except (ValueError, TypeError):  # noqa: BLE001
        return None


def check_numbering(numbers):
    """重复编号 + 单级序列缺号检测。numbers 为编号字符串列表。"""
    tuples = [parse_number(n) for n in numbers]
    tuples = [t for t in tuples if t]
    if not tuples:
        return {"duplicates": [], "gaps": []}
    counts = {}
    for t in tuples:
        counts[t] = counts.get(t, 0) + 1
    duplicates = [{"number": ".".join(str(x) for x in t), "count": c}
                  for t, c in sorted(counts.items()) if c > 1]
    gaps = []
    if all(len(t) == 1 for t in tuples):
        vals = sorted({t[0] for t in tuples})
        full = set(range(vals[0], vals[-1] + 1))
        gaps = sorted(full - set(vals))
    return {"duplicates": duplicates, "gaps": [int(g) for g in gaps]}


def check_terms(terms):
    """同一缩写对应多个不同全称 → 定义冲突。"""
    by_abbr = {}
    for t in terms:
        abbr = (t.get("abbr") or "").lower()
        term = t.get("term") or ""
        if abbr and term:
            by_abbr.setdefault(abbr, set()).add(term)
    conflicts = [{"abbr": a, "terms": sorted(ts)}
                 for a, ts in by_abbr.items() if len(ts) > 1]
    conflicts.sort(key=lambda c: c["abbr"])
    return conflicts


def scan_file(path: str) -> dict:
    text, err = read_text_safe(path)
    if err:
        return {"source": path, "error": err,
                "sections": [], "figures": [], "tables": [], "equations": [], "terms": []}
    return {
        "source": path,
        "error": None,
        "sections": extract_sections(text, path),
        "figures": extract_numbers(text, path, FIG_RE, "figure")
        + extract_numbers(text, path, FIG_EN_RE, "figure"),
        "tables": extract_numbers(text, path, TAB_RE, "table")
        + extract_numbers(text, path, TAB_EN_RE, "table"),
        "equations": extract_numbers(text, path, EQ_RE, "equation"),
        "terms": extract_terms(text, path),
    }


def scan(input_dir: str, work_dir: str) -> dict:
    os.makedirs(work_dir, exist_ok=True)
    per_file = []
    all_sections, all_figures, all_tables, all_equations, all_terms = [], [], [], [], []
    if os.path.isdir(input_dir):
        for root, dirs, files in os.walk(input_dir):
            dirs.sort()
            files.sort()
            for name in files:
                if not name.lower().endswith(TEXT_EXTS) or name.lower().startswith("."):
                    continue
                path = os.path.join(root, name)
                rec = scan_file(path)
                per_file.append(rec)
                if rec["error"]:
                    continue
                all_sections += rec["sections"]
                all_figures += rec["figures"]
                all_tables += rec["tables"]
                all_equations += rec["equations"]
                all_terms += rec["terms"]

    checks = {
        "section_duplicates": check_numbering([s["number"] for s in all_sections])["duplicates"],
        "section_gaps": check_numbering([s["number"] for s in all_sections])["gaps"],
        "figure_duplicates": check_numbering([f["number"] for f in all_figures])["duplicates"],
        "table_duplicates": check_numbering([t["number"] for t in all_tables])["duplicates"],
        "equation_duplicates": check_numbering([e["number"] for e in all_equations])["duplicates"],
        "term_conflicts": check_terms(all_terms),
    }

    registry = {
        "input_dir": input_dir,
        "files": per_file,
        "sections": all_sections,
        "figures": all_figures,
        "tables": all_tables,
        "equations": all_equations,
        "terms": all_terms,
        "checks": checks,
    }
    out_path = os.path.join(work_dir, "consistency_registry.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=2)

    print("consistency scan -> %s" % out_path)
    print("  sections=%d figures=%d tables=%d equations=%d terms=%d" % (
        len(all_sections), len(all_figures), len(all_tables),
        len(all_equations), len(all_terms)))
    print("  checks: %s" % json.dumps(checks, ensure_ascii=False))
    return registry


def check(registries) -> dict:
    merged = {"duplicates": [], "gaps": [], "term_conflicts": []}
    for path in registries:
        data, err = read_registry(path)
        if err:
            print("warn: %s" % err, file=sys.stderr)
            continue
        for group, key in (("duplicates", "duplicates"), ("gaps", "gaps")):
            for kind in ("section", "figure", "table", "equation"):
                field = "%s_%s" % (kind, key)
                for item in data.get("checks", {}).get(field, []):
                    merged[key].append({"kind": kind, "item": item, "registry": path})
        for c in data.get("checks", {}).get("term_conflicts", []):
            merged["term_conflicts"].append({"conflict": c, "registry": path})
    return merged


def read_registry(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, "read_registry_failed: %s: %s" % (path, exc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="consistency.py",
                                description="跨章节一致性扫描与检查")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--input", default="/input")
    s.add_argument("--work", default="/app/work")
    c = sub.add_parser("check")
    c.add_argument("--registry", action="append", default=[])
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "scan":
        scan(args.input, args.work)
    elif args.cmd == "check":
        merged = check(args.registry)
        print(json.dumps(merged, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
