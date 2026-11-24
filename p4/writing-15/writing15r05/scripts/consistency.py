#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨章节一致性检查脚本（只依赖标准库）。

对“可编辑章节”文本做确定性、带 locator 的检查，帮助 Agent 发现并标注：
  1. 标题/章节编号断裂（gap / duplicate）
  2. 图、表、公式编号断裂
  3. 同一拉丁术语的多种写法（如 "Long-Context" vs "LongContext"）
  4. 同一术语被重复定义且定义不一致
  5. 引用键被引用但未定义，或定义了但从未引用
  6. 同一句“模板”下出现不同数值（疑似结论冲突/笔误）

用法：
    python3 -B scripts/consistency.py --chapters <dir> --out <dir>
    python3 -B scripts/consistency.py --chapters <dir> --out <dir> --json-only

输出 check_report.json 与 check_report.md；退出码恒为 0（发现问题以 severity 标注，
是否阻断由 Agent 判断，脚本不替 Agent 作最终裁决）。
"""

import argparse
import json
import re
import sys
from pathlib import Path

TEXT_EXTS = {".md", ".txt", ".tex", ".rst"}

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$")
CN_CHAPTER_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百]+)\s*[章节部分]")
EN_SECTION_RE = re.compile(r"\b(Section|Chapter)\s+(\d+)\b", re.IGNORECASE)
FIG_RE = re.compile(r"(图|表|公式|式)\s*([0-9]+(?:[\.\-][0-9]+)?)|(Fig(?:ure)?\.?|Table)\s*([0-9]+)", re.IGNORECASE)
CITE_BRACKET_RE = re.compile(r"\[\s*([0-9]+(?:\s*[,\-]\s*[0-9]+)*)\s*\]")
CITE_KEY_RE = re.compile(r"\[@?([A-Za-z][\w:\-]{1,40})\]")
CITE_REFDEF_RE = re.compile(r"^\s*\[([0-9]+)\]\s+", re.MULTILINE)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,}(?:-[A-Za-z0-9]+)*")
DEF_RE = re.compile(r"(?:定义|称(?:为|作)|记为|记作|称为|define[sd]? as|denoted as|is called|is defined as)")
NUM_SENT_RE = re.compile(r"[\u4e00-\u9fffA-Za-z]")


def read_text(path):
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def norm_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def norm_latin(tok):
    return tok.lower().replace("-", "").replace(".", "")


def sentence_split(text):
    parts = re.split(r"(?<=[。！？!?.])\s*|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def collect_lines(path):
    text = read_text(path)
    if text is None:
        return []
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines, 1):
        out.append({"file": path, "line": i, "text": ln, "rel": path.name})
    return out


def check_headings(chapters):
    issues = []
    seen = {}
    for ch in chapters:
        for ln in ch["lines"]:
            h = HEADING_RE.match(ln["text"])
            key = None
            m = CN_CHAPTER_RE.search(ln["text"]) or EN_SECTION_RE.search(ln["text"])
            if m:
                key = "".join(g for g in (m.groups() or ()) if g)
            elif h and (ln["text"].strip().startswith("#")):
                key = None  # 无编号标题不纳入序列
            if key is None:
                continue
            seen.setdefault(key, []).append((ln["file"].name, ln["line"], ln["text"].strip()))
    # 数字序列缺口
    nums = []
    for k in seen:
        m = re.match(r"^\d+$", str(k))
        if m:
            nums.append(int(k))
    if nums:
        nums = sorted(set(nums))
        for a, b in zip(nums, nums[1:]):
            if b - a > 1:
                issues.append({
                    "check": "heading_numbering",
                    "severity": "warning",
                    "message": f"章节编号缺口 {a} → {b}（缺 {a+1}..{b-1}）",
                    "locator": None,
                })
    for k, locs in seen.items():
        if len(locs) > 1:
            issues.append({
                "check": "heading_numbering",
                "severity": "warning",
                "message": f"编号/标题 {k} 出现 {len(locs)} 次",
                "locator": [f"{f}:{ln}" for f, ln, _ in locs],
            })
    return issues


def check_fig_table(chapters):
    issues = []
    buckets = {}
    for ch in chapters:
        for ln in ch["lines"]:
            for m in FIG_RE.finditer(ln["text"]):
                prefix = None
                num = None
                for i, g in enumerate(m.groups()):
                    if g and re.match(r"^[0-9]", g):
                        if prefix is None:
                            prefix = m.groups()[i - 1] if i > 0 else None
                        num = g
                        break
                # 兼容 “图1 / Table2” 两种捕获顺序
                if num is None:
                    continue
                # 提取类标签
                raw = m.group(0)
                if re.match(r"^图", raw):
                    prefix = "图"
                elif re.match(r"^表", raw):
                    prefix = "表"
                elif re.match(r"^公式|^式", raw):
                    prefix = "公式"
                elif re.match(r"(?i)fig", raw):
                    prefix = "Fig"
                elif re.match(r"(?i)table", raw):
                    prefix = "Table"
                key = (prefix or "?", num)
                buckets.setdefault(key, []).append((ch["file"].name, ln["line"], raw))
    for (prefix, num), locs in buckets.items():
        if len(locs) > 1:
            issues.append({
                "check": "figure_table_numbering",
                "severity": "warning",
                "message": f"{prefix}{num} 重复出现 {len(locs)} 次",
                "locator": [f"{f}:{l}" for f, l, _ in locs],
            })
    # 每个类内部的数字缺口（仅数字型）
    for prefix in ("图", "表", "Fig", "Table", "公式"):
        nums = sorted({int(n) for (p, n) in buckets if p == prefix and n.isdigit()})
        for a, b in zip(nums, nums[1:]):
            if b - a > 1:
                issues.append({
                    "check": "figure_table_numbering",
                    "severity": "warning",
                    "message": f"{prefix} 编号缺口 {a} → {b}",
                    "locator": None,
                })
    return issues


def check_term_variants(chapters):
    issues = []
    norm_map = {}
    for ch in chapters:
        for ln in ch["lines"]:
            for tok in LATIN_TOKEN_RE.findall(ln["text"]):
                if len(tok) < 4:
                    continue
                n = norm_latin(tok)
                norm_map.setdefault(n, set()).add(tok)
    for n, variants in norm_map.items():
        if len(variants) > 1:
            issues.append({
                "check": "term_variant",
                "severity": "warning",
                "message": f"术语写法不一致: {sorted(variants)}（归一化 {n}）",
                "locator": None,
            })
    return issues


def check_definitions(chapters):
    issues = []
    defs = {}
    for ch in chapters:
        for ln in ch["lines"]:
            t = ln["text"].strip()
            if len(t) < 8 or not DEF_RE.search(t):
                continue
            term_m = re.search(r"([A-Za-z][\w\-]{1,30}|[\u4e00-\u9fff]{2,12})\s*(?:的)?(?:定义|称为|记为|记作|称为)", t)
            if not term_m:
                continue
            term = term_m.group(1)
            body = norm_ws(re.sub(r"\s+", " ", t))
            defs.setdefault(term, {}).setdefault(body, []).append((ch["file"].name, ln["line"]))
    for term, bodies in defs.items():
        if len(bodies) > 1:
            issues.append({
                "check": "duplicate_definition",
                "severity": "warning",
                "message": f"术语 “{term}” 被多处定义且措辞不一致",
                "locator": [f"{f}:{l}" for locs in bodies.values() for f, l in locs],
            })
    return issues


def check_citations(chapters):
    issues = []
    cited = {}
    defined = {}
    for ch in chapters:
        for ln in ch["lines"]:
            for m in CITE_BRACKET_RE.finditer(ln["text"]):
                for n in re.findall(r"\d+", m.group(1)):
                    cited.setdefault(("bracket", n), []).append((ch["file"].name, ln["line"]))
            for m in CITE_KEY_RE.finditer(ln["text"]):
                cited.setdefault(("key", m.group(1)), []).append((ch["file"].name, ln["line"]))
            for m in CITE_REFDEF_RE.finditer(ln["text"]):
                defined.setdefault(m.group(1), []).append((ch["file"].name, ln["line"]))
    for (kind, k), locs in cited.items():
        if kind == "bracket" and k not in defined:
            issues.append({
                "check": "citation_undefined",
                "severity": "warning",
                "message": f"引用 [{k}] 未在参考文献表中定义",
                "locator": [f"{f}:{l}" for f, l in locs],
            })
    for k, locs in defined.items():
        if ("bracket", k) not in cited:
            issues.append({
                "check": "citation_uncited",
                "severity": "info",
                "message": f"参考文献 [{k}] 从未被正文引用",
                "locator": [f"{f}:{l}" for f, l in locs],
            })
    return issues


def check_numeric_conflicts(chapters):
    issues = []
    stem_map = {}
    for ch in chapters:
        for ln in ch["lines"]:
            for sent in sentence_split(ln["text"]):
                if not NUM_SENT_RE.search(sent):
                    continue
                nums = re.findall(r"\d+(?:\.\d+)?%?", sent)
                if not nums:
                    continue
                stem = norm_ws(re.sub(r"\d+(?:\.\d+)?%?", "#", sent))
                if len(stem) < 6 or "#" not in stem:
                    continue
                stem_map.setdefault(stem, []).append((nums, ch["file"].name, ln["line"], sent))
    for stem, entries in stem_map.items():
        locs = {(f, l) for _, f, l, _ in entries}
        if len(locs) < 2:
            continue
        values = set()
        for nums, f, l, _ in entries:
            values.update(nums)
        if len(values) > 1:
            issues.append({
                "check": "numeric_conflict",
                "severity": "warning",
                "message": f"同句式下不同位置出现不同数值: {sorted(values)}（模板 {stem}）",
                "locator": [f"{f}:{l}" for f, l in sorted(locs)],
            })
    return issues


def main(argv=None):
    ap = argparse.ArgumentParser(description="跨章节一致性检查")
    ap.add_argument("--chapters", required=True, help="章节文件所在目录")
    ap.add_argument("--out", required=True, help="报告输出目录")
    ap.add_argument("--json-only", action="store_true", help="只写 JSON，不写 Markdown")
    args = ap.parse_args(argv)

    root = Path(args.chapters)
    files = sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXTS])
    chapters = [{"file": f, "rel": f.name, "lines": collect_lines(f)} for f in files]

    all_issues = []
    all_issues += check_headings(chapters)
    all_issues += check_fig_table(chapters)
    all_issues += check_term_variants(chapters)
    all_issues += check_definitions(chapters)
    all_issues += check_citations(chapters)
    all_issues += check_numeric_conflicts(chapters)

    by_check = {}
    for it in all_issues:
        by_check.setdefault(it["check"], []).append(it)

    report = {
        "checked_files": [str(f.relative_to(root)) for f in files],
        "issue_count": len(all_issues),
        "issues": all_issues,
        "by_check": by_check,
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.json_only:
        lines = [f"# 一致性检查报告", "",
                 f"- 检查文件: {len(files)}", f"- 问题数: {len(all_issues)}", ""]
        for it in all_issues:
            loc = ", ".join(it["locator"]) if it.get("locator") else "全局"
            lines += [f"- [{it['severity']}] {it['message']}（{loc}）"]
        (out / "check_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "issue_count": len(all_issues)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
