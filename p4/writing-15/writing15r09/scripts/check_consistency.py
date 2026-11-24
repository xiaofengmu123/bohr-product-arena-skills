#!/usr/bin/env python3
"""check_consistency.py — 长文档跨章节一致性检查（仅标准库）。

对 Markdown 章节做确定性检查：
  1) 标题编号连续性与层级
  2) 图/表/式编号与交叉引用
  3) 术语/缩写重复定义、缩写先定义后使用
  4) 符号表符号是否在正文出现（若存在符号表）

输出 check_report.json 与 check_report.md。所有检查都不修改原文，
发现的问题以 findings 列表返回，由 Agent 决定是否修改。
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
NUM_HEADING_RE = re.compile(r"(?:第\s*)?(\d+(?:\.\d+)*)(?:\s*章|\s*节)?")
NUM_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)*)")

CAP_RE = re.compile(r"^\s*(图|表|Fig\.?|Figure|Table)\s*[:：.\s]\s*([0-9][0-9.\-]*)")
XREF_RE = re.compile(r"(?:如|见|参见|如|参照)\s*(图|表|式|公式|Fig\.?|Figure|Table|Eq\.?|Equation|节|第)\s*([0-9][0-9.\-]*)|(图|表|式)\s*([0-9][0-9.\-]*)\s*(所示|中|可见)")

DEF_ABBR_RE = re.compile(r"([^\s（(]{1,40}?)[（(]([A-Z][A-Za-z0-9]{1,20})[)）]")
DEF_CN_RE = re.compile(r"(?:以下简称|简称|简写为)\s*[“\"]?([A-Za-z0-9\u4e00-\u9fff]{1,24})")

SYMBOL_HEADER_RE = re.compile(r"(符号|symbol|记号)", re.I)

TEXT_EXT = {".md", ".txt", ".tex"}


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def norm_key(numstr):
    """规范化编号，便于比较顺序。"""
    parts = numstr.replace("-", ".").replace("－", ".").split(".")
    try:
        return tuple(int(p) for p in parts if p.isdigit())
    except ValueError:
        return tuple()


def collect_files(input_dir):
    root = Path(input_dir)
    if not root.exists():
        return []
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_EXT:
            files.append(path)
    return files


def check_headings(lines, src):
    findings = []
    seen = {}
    prev = None
    prev_level = 0
    last_at_level = {}
    for lineno, raw in enumerate(lines, 1):
        m = HEADING_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        nm = NUM_HEADING_RE.search(title)
        if not nm:
            continue
        num = nm.group(1)
        key = norm_key(num)
        if not key:
            continue
        # 深度优先顺序检查：同层递增，跨层回退只允许回到父层
        if prev is not None:
            if key == prev:
                findings.append({"file": src, "line": lineno, "kind": "heading",
                                 "severity": "error",
                                 "msg": "标题编号重复: %s" % num})
            elif level == prev_level and key < prev:
                findings.append({"file": src, "line": lineno, "kind": "heading",
                                 "severity": "warn",
                                 "msg": "标题编号回退: %s 在 %s 之后" % (num, seen.get(prev, "?"))})
        # 同层跳号检查：同一父级下最后一段编号 +1 以上视为跳号
        if level in last_at_level:
            last_key = last_at_level[level]
            if (len(key) == len(last_key) and key[:-1] == last_key[:-1]
                    and key[-1] > last_key[-1] + 1):
                findings.append({"file": src, "line": lineno, "kind": "heading",
                                 "severity": "warn",
                                 "msg": "标题编号跳号: %s（前一段为 %s）" %
                                       (num, ".".join(map(str, last_key)))})
        if key in seen:
            findings.append({"file": src, "line": lineno, "kind": "heading",
                             "severity": "warn",
                             "msg": "编号 %s 出现多次（行 %d 与 %d）" % (num, seen[key], lineno)})
        else:
            seen[key] = lineno
        last_at_level[level] = key
        prev = key
        prev_level = level
    return findings


def check_labels_xref(lines, src):
    findings = []
    defined = defaultdict(set)   # kind -> set of norm keys
    referenced = defaultdict(set)
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        cm = CAP_RE.search(line)
        if cm:
            kind = cm.group(1).lower()
            defined[kind].add(norm_key(cm.group(2)))
        for xm in XREF_RE.finditer(line):
            if xm.group(1) and xm.group(2):
                referenced[xm.group(1).lower()].add(norm_key(xm.group(2)))
            if xm.group(3) and xm.group(4):
                referenced[xm.group(3).lower()].add(norm_key(xm.group(4)))
    for kind, refs in referenced.items():
        defs = defined.get(kind, set())
        for key in sorted(refs):
            if key and key not in defs:
                findings.append({"file": src, "line": 0, "kind": "xref",
                                 "severity": "warn",
                                 "msg": "交叉引用 %s%s 未找到对应编号定义" % (kind, ".".join(map(str, key)))})
    return findings, defined


def check_terms(lines, src):
    findings = []
    abbr_map = {}   # abbr -> (expansion, first_def_line)
    first_use = {}  # abbr -> first occurrence line
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        for m in DEF_ABBR_RE.finditer(line):
            expansion = m.group(1).strip()
            abbr = m.group(2)
            if abbr in abbr_map:
                prev_exp, prev_line = abbr_map[abbr]
                if prev_exp != expansion:
                    findings.append({"file": src, "line": lineno, "kind": "term",
                                     "severity": "error",
                                     "msg": "缩写 %s 重复定义且展开不一致：%s (行 %d) vs %s" %
                                           (abbr, prev_exp, prev_line, expansion)})
            else:
                abbr_map[abbr] = (expansion, lineno)
        for m in DEF_CN_RE.finditer(line):
            abbr = m.group(1)
            # 中文简称定义可出现在“（以下简称 X）”，记录首个定义
            if abbr not in first_use:
                first_use[abbr] = lineno
    # 检查先定义后使用：全词（仅拉丁缩写）
    full_text = "\n".join(lines)
    for abbr, (exp, def_line) in abbr_map.items():
        # 找缩写独立出现的位置（非定义行）
        for lineno, raw in enumerate(lines, 1):
            if lineno == def_line:
                continue
            if re.search(r"\b%s\b" % re.escape(abbr), raw):
                if abbr not in first_use or first_use[abbr] > def_line:
                    first_use[abbr] = lineno
        if first_use.get(abbr) and first_use[abbr] < def_line:
            findings.append({"file": src, "line": first_use[abbr], "kind": "term",
                             "severity": "warn",
                             "msg": "缩写 %s 在第 %d 行先于定义（第 %d 行）使用" %
                                   (abbr, first_use[abbr], def_line)})
    return findings, abbr_map


def is_separator_row(line):
    """表头与数据行之间的分隔线，如 --- | --- 或 |:---|:---|。"""
    return re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", line) is not None


def check_symbols(lines, src):
    """若存在符号表，提取符号并检查是否在正文出现。"""
    findings = []
    symbols = []
    in_symbol_table = False
    for raw in lines:
        line = raw.rstrip("\n")
        if not in_symbol_table:
            if SYMBOL_HEADER_RE.search(line) and "|" in line:
                in_symbol_table = True
            continue
        if "|" not in line:
            in_symbol_table = False
            continue
        if is_separator_row(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        vals = [c for c in cells if c]
        if len(vals) >= 2:
            sym = vals[0]
            if sym and sym not in ("符号", "symbol", "记号", "---"):
                symbols.append(sym)
    if not symbols:
        return findings, []
    full_text = "\n".join(lines)
    for sym in symbols:
        count = full_text.count(sym)
        if count <= 1:
            findings.append({"file": src, "line": 0, "kind": "symbol",
                             "severity": "warn",
                             "msg": "符号 %s 在符号表外几乎未出现（出现 %d 次）" % (sym, count)})
    return findings, symbols


def main(argv=None):
    parser = argparse.ArgumentParser(description="长文档跨章节一致性检查")
    parser.add_argument("--input", default="/app/output",
                        help="含章节 Markdown 的目录（默认 /app/output）")
    parser.add_argument("--output", default="/app/work/checks",
                        help="检查报告输出目录（默认 /app/work/checks）")
    args = parser.parse_args(argv)

    files = collect_files(args.input)
    findings = []
    stats = {"files": len(files), "headings": 0, "terms": 0, "symbols": 0}
    all_abbr = {}
    all_symbols = []

    for path in files:
        text = read_text(path)
        if text is None:
            findings.append({"file": str(path), "line": 0, "kind": "io",
                             "severity": "error", "msg": "无法读取文件"})
            continue
        lines = text.split("\n")
        rel = str(path)
        findings += check_headings(lines, rel)
        xref_findings, _ = check_labels_xref(lines, rel)
        findings += xref_findings
        term_findings, abbrs = check_terms(lines, rel)
        findings += term_findings
        for abbr, val in abbrs.items():
            all_abbr.setdefault(abbr, val)
        sym_findings, syms = check_symbols(lines, rel)
        findings += sym_findings
        all_symbols.extend(syms)

    stats["findings"] = len(findings)
    report = {"stats": stats, "findings": findings,
              "abbreviations": {k: v[0] for k, v in all_abbr.items()},
              "symbols": sorted(set(all_symbols))}

    out_dir = Path(args.output)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "check_report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        md_lines = ["# 一致性检查报告", "",
                    "- 检查文件数：%d" % stats["files"],
                    "- 发现项：%d" % stats["findings"], ""]
        for f in findings:
            md_lines.append("- [%s] %s:%s %s" % (f.get("severity", "info"),
                                                  f.get("file", ""), f.get("line", 0),
                                                  f.get("msg", "")))
        if all_abbr:
            md_lines.append("\n## 缩写表\n")
            for abbr, exp in sorted(all_abbr.items()):
                md_lines.append("- %s：%s" % (abbr, exp[0]))
        with open(out_dir / "check_report.md", "w", encoding="utf-8") as fh:
            fh.write("\n".join(md_lines) + "\n")
    except OSError as exc:
        print(json.dumps({"status": "error", "errors": ["cannot_write_report: %s" % exc]},
                         ensure_ascii=False))
        return 0

    print(json.dumps({"status": "ok", "files": stats["files"],
                      "findings": stats["findings"],
                      "report": str(out_dir / "check_report.json")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
