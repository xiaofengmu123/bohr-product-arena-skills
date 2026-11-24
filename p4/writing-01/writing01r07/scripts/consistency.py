#!/usr/bin/env python3
"""Post-draft consistency checks (stdlib only).

Checks manuscript.md for:
  - dangling [S:ref] / [C:key] markers
  - citation keys defined in the reference block but never used (and vice versa)
  - missing required sections
  - Figure/Table numbering gaps or duplicate numbers
  - explicit [缺失]/[待确认]/[冲突待确认]/[无法解析] markers inventory

Writes consistency_report.json to --work and exits 0 (report, never crash).

Usage:
    python3 scripts/consistency.py --draft /app/output/manuscript.md \
        --index /app/work/material_index.json --work /app/work
"""
import argparse
import json
import os
import re
import sys

VERSION = "2.0.0"

DEFAULT_REQUIRED = [
    ("摘要|abstract", "abstract/摘要"),
    ("引言|introduction", "introduction/引言"),
    ("方法|method", "methods/方法"),
    ("结果|result", "results/结果"),
    ("讨论|discussion", "discussion/讨论"),
    ("结论|conclusion", "conclusion/结论"),
    ("参考文献|reference", "references/参考文献"),
]

SRC_RE = re.compile(r"\[S:([A-Za-z0-9_.-]+)\]")
CITE_USE_RE = re.compile(r"\[C:([A-Za-z0-9_.-]+)\]")
CITE_DEF_RE = re.compile(r"^\[C:([A-Za-z0-9_.-]+)\]\s*\S", re.M)
FIG_REF_RE = re.compile(r"(?:图\s*|Figure\s*|Fig\.\s*)(\d+)", re.I)
TABLE_REF_RE = re.compile(r"(?:表\s*|Table\s*)(\d+)", re.I)
PENDING_RE = re.compile(r"\[(缺失|待确认|冲突待确认|无法解析)\]")


def read(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None


def load_index(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _nums(text, pattern):
    found = []
    for m in pattern.finditer(text):
        try:
            found.append(int(m.group(1)))
        except ValueError:
            continue
    return found


def numbering_gaps(nums):
    uniq = sorted(set(nums))
    missing = [n for n in range(1, max(uniq) + 1) if n not in uniq] if uniq else []
    dup = [n for n in set(nums) if nums.count(n) > 1]
    return missing, dup


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--draft", default="/app/output/manuscript.md")
    ap.add_argument("--index", default="/app/work/material_index.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    report = {"ok": True, "issues": [], "checks": {}}
    draft = read(args.draft)
    if draft is None:
        report["ok"] = False
        report["issues"].append("draft_missing:%s" % args.draft)
        report["checks"]["draft_readable"] = False
    else:
        report["checks"]["draft_readable"] = True

    if draft is not None:
        index = load_index(args.index) or {}
        known_srcs = set()
        for entry in index.values():
            if isinstance(entry, dict):
                known_srcs.add(entry.get("ref_id"))
        used_srcs = set(SRC_RE.findall(draft))
        dangling_srcs = sorted(used_srcs - known_srcs)
        report["checks"]["dangling_sources"] = dangling_srcs
        if dangling_srcs:
            report["issues"].append("dangling_sources:%s" % ",".join(dangling_srcs))

        used_cites = set(CITE_USE_RE.findall(draft))
        defined_cites = set(CITE_DEF_RE.findall(draft))
        dangling_cites = sorted(used_cites - defined_cites)
        defined_unused = sorted(defined_cites - used_cites)
        report["checks"]["dangling_citations"] = dangling_cites
        report["checks"]["defined_unused_citations"] = defined_unused
        if dangling_cites:
            report["issues"].append("dangling_citations:%s" % ",".join(dangling_cites))

        missing_sections = []
        for pattern, label in DEFAULT_REQUIRED:
            if not re.search(r"^#{1,3}\s*.*(?:%s)" % pattern, draft, re.I | re.M):
                missing_sections.append(label)
        report["checks"]["missing_sections"] = missing_sections
        if missing_sections:
            report["issues"].append("missing_sections:%s" % ",".join(missing_sections))

        fig_nums = _nums(draft, FIG_REF_RE)
        tab_nums = _nums(draft, TABLE_REF_RE)
        fig_missing, fig_dup = numbering_gaps(fig_nums)
        tab_missing, tab_dup = numbering_gaps(tab_nums)
        report["checks"]["figure_numbering"] = {
            "missing": fig_missing, "duplicate": fig_dup}
        report["checks"]["table_numbering"] = {
            "missing": tab_missing, "duplicate": tab_dup}
        if fig_missing:
            report["issues"].append("figure_numbering_gap:%s" % fig_missing)
        if fig_dup:
            report["issues"].append("figure_numbering_dup:%s" % fig_dup)
        if tab_missing:
            report["issues"].append("table_numbering_gap:%s" % tab_missing)
        if tab_dup:
            report["issues"].append("table_numbering_dup:%s" % tab_dup)

        pending = PENDING_RE.findall(draft)
        report["checks"]["pending_markers"] = pending
        if pending:
            report["issues"].append(
                "pending_markers_found(%d):%s" % (len(pending), ",".join(sorted(set(pending)))))

        report["checks"]["source_count_used"] = len(used_srcs)
        report["checks"]["citation_count_used"] = len(used_cites)

    report["ok"] = not report["issues"]
    report["version"] = VERSION

    os.makedirs(args.work, exist_ok=True)
    out_path = os.path.join(args.work, "consistency_report.json")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        report["issues"].append("report_write_failed:%s" % exc)
        report["ok"] = False

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
