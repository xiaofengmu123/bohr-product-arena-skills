#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
consistency.py — 长文档跨章节一致性检查（确定性，仅标准库）。

对 Markdown 稿件做可复现的机械检查，输出 consistency_report.json：
  heading_numbering    章节编号是否跳号/重复
  ref_targets          图/表/式 交叉引用是否有对应标题/标签
  citation_targets     文中 [n] / (Author, year) 是否在参考文献列表中
  term_variants        同一术语的多种写法聚类
  repeated_definitions 同一术语是否被重复定义
  duplicate_paragraphs 近乎重复的段落
  section_wordcount    各节字数分配（供章节规划核对）

用法：
  python3 -B scripts/consistency.py --manuscript <file.md> --out <report.json>
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FIG_REF = re.compile(r"图\s*([0-9]+(?:\s*[-–.,、和]\s*[0-9]+)*)", re.I)
TABLE_REF = re.compile(r"表\s*([0-9]+(?:\s*[-–.,、和]\s*[0-9]+)*)", re.I)
EQ_REF = re.compile(r"(?:式|公式|方程)\s*\(?([0-9]+(?:\s*[-–.,、]\s*[0-9]+)*)\)?", re.I)
FIG_CAP = re.compile(r"^图\s*([0-9]+)[\s\.、：:－-]", re.M)
TABLE_CAP = re.compile(r"^表\s*([0-9]+)[\s\.、：:－-]", re.M)
EQ_CAP = re.compile(r"^(?:式|公式|方程)\s*\(?([0-9]+)\)?[\s\.、：:＝=]", re.M)
CITE_NUM = re.compile(r"\[([0-9]+(?:\s*[-–,，]\s*[0-9]+)*)\]")
CITE_AY = re.compile(r"\(([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\'\- ]{1,40}),?\s*(19|20)\d{2}[a-z]?\)")
REF_ENTRY_NUM = re.compile(r"^\s*\[([0-9]+)\][\]\s\.]", re.M)


def read_text(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, ValueError):
                continue
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def norm_num(s):
    return re.sub(r"\s+", "", s)


def parse_num_set(s):
    """把 '1, 2-4' 之类展开成整数集合。"""
    out = set()
    for part in re.split(r"[、,，;；]", s):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b >= a and b - a <= 50:
                out.update(range(a, b + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def split_sections(text):
    """按标题切分，返回 [(heading, body)]；首个块 heading 为 None（前言）。"""
    lines = text.splitlines()
    sections = []
    cur_head = None
    cur_body = []
    for ln in lines:
        m = HEADING_RE.match(ln)
        if m:
            sections.append((cur_head, "\n".join(cur_body)))
            cur_head = ln.strip()
            cur_body = []
        else:
            cur_body.append(ln)
    sections.append((cur_head, "\n".join(cur_body)))
    return sections


def heading_numbers(text):
    """提取形如 '1.2'、'1' 的数字标题，返回 [(lineno, level, num_str, title)]。"""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        m = HEADING_RE.match(ln)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        nm = re.match(r"^(\d+(?:\.\d+)*)[\.、\s]", title)
        if nm:
            out.append((i, level, nm.group(1), title))
    return out


def check_heading_numbering(text):
    items = []
    seen = defaultdict(list)
    for i, level, num, title in heading_numbers(text):
        seen[level].append((num, i, title))
    for level, entries in seen.items():
        tops = [e for e in entries if e[0].count(".") == 0]
        # 顶层同级别序号连续性
        for a, b in zip(tops, tops[1:]):
            na, nb = int(a[0]), int(b[0])
            if nb != na + 1:
                items.append({
                    "line": b[1], "level": level, "issue": "heading_number_gap",
                    "detail": "章节编号从 %s 跳到 %s（%s）" % (a[0], b[0], b[2]),
                })
        # 重复编号
        by_num = defaultdict(list)
        for e in entries:
            by_num[e[0]].append(e)
        for num, es in by_num.items():
            if len(es) > 1:
                items.append({
                    "line": es[1][1], "level": level, "issue": "heading_number_duplicate",
                    "detail": "编号 %s 出现 %d 次：%s" % (num, len(es),
                        "; ".join("L%d %s" % (e[1], e[2]) for e in es)),
                })
    return items


def check_ref_targets(text):
    items = []
    fig_refs = set()
    for m in FIG_REF.finditer(text):
        fig_refs |= parse_num_set(m.group(1))
    fig_caps = set()
    for m in FIG_CAP.finditer(text):
        fig_caps.add(int(m.group(1)))
    for n in sorted(fig_refs - fig_caps):
        items.append({"line": None, "issue": "figure_ref_missing", "detail": "正文引用 图 %d 但无对应图题" % n})

    tab_refs = set()
    for m in TABLE_REF.finditer(text):
        tab_refs |= parse_num_set(m.group(1))
    tab_caps = set()
    for m in TABLE_CAP.finditer(text):
        tab_caps.add(int(m.group(1)))
    for n in sorted(tab_refs - tab_caps):
        items.append({"line": None, "issue": "table_ref_missing", "detail": "正文引用 表 %d 但无对应表题" % n})

    eq_refs = set()
    for m in EQ_REF.finditer(text):
        eq_refs |= parse_num_set(m.group(1))
    eq_caps = set()
    for m in EQ_CAP.finditer(text):
        eq_caps.add(int(m.group(1)))
    for n in sorted(eq_refs - eq_caps):
        items.append({"line": None, "issue": "equation_ref_missing", "detail": "正文引用 式 %d 但无对应公式编号" % n})
    return items


def check_citation_targets(text):
    items = []
    # 编号引用 vs 参考文献条目
    cited = set()
    for m in CITE_NUM.finditer(text):
        cited |= parse_num_set(m.group(1))
    refs = set()
    for m in REF_ENTRY_NUM.finditer(text):
        refs.add(int(m.group(1)))
    for n in sorted(cited - refs):
        items.append({"line": None, "issue": "citation_missing", "detail": "引用 [%d] 无对应参考文献条目" % n})
    # 作者-年份引用 vs 参考文献列表中的作者
    ay = set()
    for m in CITE_AY.finditer(text):
        ay.add(m.group(1).strip())
    body_lower = text.lower()
    for author in sorted(ay):
        surname = author.split(",")[0].split()[0] if author else ""
        if surname and surname.lower() not in body_lower.replace(author.lower(), "", 1):
            # 粗略：该作者-年份只出现一次，可能在正文而非文末参考文献
            items.append({
                "line": None, "issue": "citation_author_year_unresolved",
                "detail": "作者-年份引用 (%s) 未能在参考文献区找到对应条目" % author,
            })
    return items


TERM_RE = re.compile(r"([A-Za-z][A-Za-z0-9\-]{1,24})", re.I)


def check_term_variants(text):
    """聚类疑似同一概念的不同写法（大小写、连字符、中英混写）。"""
    tokens = [m.group(1) for m in TERM_RE.finditer(text)]
    lowered = defaultdict(set)
    for t in tokens:
        lowered[t.lower()].add(t)
    items = []
    for low, forms in lowered.items():
        if len(forms) > 1 and all(re.search(r"[A-Za-z]", f) for f in forms):
            items.append({
                "line": None, "issue": "term_variant",
                "detail": "术语多种写法：%s" % ", ".join(sorted(forms)),
            })
    return items


DEFINE_RE = re.compile(
    r"([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\-· ]{1,40})"
    r"\s*(?:是指|定义为|is defined as|refers to|denoted as|记作)",
    re.I,
)


def check_repeated_definitions(text):
    terms = [m.group(1).strip() for m in DEFINE_RE.finditer(text)]
    counts = Counter(t.lower() for t in terms)
    items = []
    for t, c in counts.items():
        if c > 1:
            items.append({
                "line": None, "issue": "repeated_definition",
                "detail": "术语“%s”被定义 %d 次" % (t, c),
            })
    return items


def token_set(s):
    return frozenset(re.findall(r"[\w\u4e00-\u9fff]+", s.lower()))


def check_duplicate_paragraphs(text):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 40]
    seen = {}
    items = []
    for i, p in enumerate(paras, 1):
        ts = token_set(p)
        if not ts:
            continue
        sig = len(ts)
        for j, other in seen.items():
            overlap = len(ts & other[0])
            if overlap >= 0.8 * sig and overlap >= 8:
                items.append({
                    "line": None, "issue": "duplicate_paragraph",
                    "detail": "段落 %d 与段落 %d 高度重复（重叠词 %d/%d）" % (i, j, overlap, sig),
                })
                break
        seen[i] = (ts, p)
    return items


def section_wordcount(text):
    out = []
    for head, body in split_sections(text):
        label = head if head else "(前言/无标题)"
        words_cn = len(re.findall(r"[\u4e00-\u9fff]", body))
        words_en = len(re.findall(r"\b[A-Za-z0-9\'\-]+\b", body))
        out.append({"section": label, "cn_chars": words_cn, "en_words": words_en})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="跨章节一致性检查")
    ap.add_argument("--manuscript", required=True, help="Markdown 稿件路径")
    ap.add_argument("--out", required=True, help="报告输出 JSON 路径")
    args = ap.parse_args(argv)

    text = read_text(args.manuscript)
    if text is None:
        report = {
            "manuscript": args.manuscript,
            "status": "input_error",
            "summary": {"errors": 1, "warnings": 0, "notes": 0},
            "checks": [{"id": "read", "level": "error", "items": [{"issue": "unreadable_manuscript"}]}],
        }
    else:
        checks = [
            {"id": "heading_numbering", "level": "error", "items": check_heading_numbering(text)},
            {"id": "ref_targets", "level": "error", "items": check_ref_targets(text)},
            {"id": "citation_targets", "level": "warning", "items": check_citation_targets(text)},
            {"id": "term_variants", "level": "warning", "items": check_term_variants(text)},
            {"id": "repeated_definitions", "level": "warning", "items": check_repeated_definitions(text)},
            {"id": "duplicate_paragraphs", "level": "warning", "items": check_duplicate_paragraphs(text)},
            {"id": "section_wordcount", "level": "note", "items": section_wordcount(text)},
        ]
        errs = sum(1 for c in checks if c["level"] == "error" and c["items"])
        warns = sum(1 for c in checks if c["level"] == "warning" and c["items"])
        notes = sum(1 for c in checks if c["level"] == "note" and c["items"])
        report = {
            "manuscript": args.manuscript,
            "status": "ok" if errs == 0 else "issues_found",
            "summary": {"errors": errs, "warnings": warns, "notes": notes},
            "checks": checks,
        }

    d = os.path.dirname(args.out)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, args.out)

    print("consistency summary: status=%s errors=%d warnings=%d notes=%d"
          % (report["status"], report["summary"]["errors"],
             report["summary"]["warnings"], report["summary"]["notes"]))
    for c in report["checks"]:
        if c["items"]:
            print("  [%s] %s: %d items" % (c["level"], c["id"], len(c["items"])))
            for it in c["items"][:5]:
                label = it.get("issue") or it.get("section") or "item"
                detail = it.get("detail") or ""
                print("      - %s: %s" % (label, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
