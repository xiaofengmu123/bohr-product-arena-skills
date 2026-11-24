#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨章节一致性与可核验性检查（纯标准库）。

对 /app/output 下可读文本产物执行确定性的机械检查：
  1) 章节编号连续性（重复/跳号/乱序）
  2) 图/表/公式交叉引用完整性（引用了未定义、定义了未引用）
  3) 引用标记 vs 参考文献表（引用编号是否都有条目、条目是否都被引用）
  4) 缩写「以下简称」的一致性（定义了却不沿用、重复定义）
  5) 跨文件逐字重复段落（疑似复制，破坏"改得动"）
产出 work/check_report.json，并把本次检查追加到 work/change_log.json。
所有定位信息均带 file + line + quote，供独立复核。
"""

import argparse
import json
import re
import time
from pathlib import Path

MAX_FILE = 2 * 1024 * 1024
TEXT_EXT = {".md", ".txt"}

SEC_RE = re.compile(r"^\s*(\d{1,2})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?\s+\S")
HEAD_NUM_RE = re.compile(r"^\s*#{1,6}\s+(\d{1,2})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?\s*\S*")
CAPTION_FIG = re.compile(r"^\s*(?:图|Fig(?:ure)?\.?)\s*(\d+(?:[-.]\d+)*)", re.MULTILINE)
CAPTION_TAB = re.compile(r"^\s*(?:表|Table)\s*(\d+(?:[-.]\d+)*)", re.MULTILINE)
CAPTION_EQ = re.compile(r"^\s*(?:公式|Eq(?:uation)?\.?|Eq\.)\s*\(?(\d+(?:[-.]\d+)*)\)?", re.MULTILINE)
REF_FIG = re.compile(r"(?:图|Fig(?:ure)?\.?)\s*(\d+(?:[-.]\d+)*)")
REF_TAB = re.compile(r"(?:表|Table)\s*(\d+(?:[-.]\d+)*)")
REF_EQ = re.compile(r"(?:公式|Eq(?:uation)?\.?|Eq\.)\s*\(?(\d+(?:[-.]\d+)*)\)?")
CITE_NUM = re.compile(r"\[(\d+(?:\s*[,，\-–]\s*\d+)*)\]")
REF_ENTRY = re.compile(r"^\s*\[(\d+)\]\s+\S")
ABBR_DEF = re.compile(r"([A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-]{1,40})\s*[（(]\s*以下简称\s*([^）)]+)\s*[）)]")
SENT_SPLIT = re.compile(r"[。！？.!?]\s*")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(p):
    try:
        if p.stat().st_size > MAX_FILE:
            return None
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    return None


def load_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def numbered_issues(files):
    """files: list of (relpath, text, lines)."""
    issues, seen = [], {}
    for rel, text, _ in files:
        for i, line in enumerate(text.splitlines(), 1):
            m = SEC_RE.match(line) or HEAD_NUM_RE.match(line)
            if not m:
                continue
            parts = [x for x in m.groups() if x is not None]
            num = ".".join(parts)
            if num in seen:
                issues.append({"check": "section-number-duplicate", "severity": "error",
                               "file": rel, "line": i, "quote": line.strip(),
                               "detail": "章节编号 {} 重复（首次出现在 {}:{}）".format(
                                   num, seen[num][0], seen[num][1])})
            else:
                seen[num] = (rel, i)
    # 同层级跳号（示例：1.1 后直接 1.3）
    top = sorted((tuple(int(x) for x in k.split(".")), v) for k, v in seen.items())
    prev, prev_label = None, None
    for tup, loc in top:
        label = ".".join(str(x) for x in tup)
        if prev is not None and len(tup) == len(prev) and tup[:-1] == prev[:-1]:
            if tup[-1] != prev[-1] + 1:
                issues.append({"check": "section-number-gap", "severity": "warning",
                               "file": loc[0], "line": loc[1], "quote": label,
                               "detail": "编号 {} 之后跳至 {}，疑似跳号".format(prev_label, label)})
        prev, prev_label = tup, label
    return issues


def crossref_issues(files):
    issues = []
    cap_fig, cap_tab, cap_eq = set(), set(), set()
    ref_fig, ref_tab, ref_eq = set(), set(), set()
    for rel, text, _ in files:
        cap_fig |= set(CAPTION_FIG.findall(text))
        cap_tab |= set(CAPTION_TAB.findall(text))
        cap_eq |= set(CAPTION_EQ.findall(text))
        ref_fig |= set(REF_FIG.findall(text))
        ref_tab |= set(REF_TAB.findall(text))
        ref_eq |= set(REF_EQ.findall(text))
    for name, refs, caps, label in (("figure", ref_fig, cap_fig, "图/Figure"),
                                    ("table", ref_tab, cap_tab, "表/Table"),
                                    ("equation", ref_eq, cap_eq, "公式/Equation")):
        for n in sorted(refs - caps):
            issues.append({"check": "crossref-" + name + "-undefined", "severity": "warning",
                           "file": None, "line": None, "quote": "{} {}".format(label, n),
                           "detail": "引用了编号 {} 但全文未见对应标题/定义".format(n)})
        for n in sorted(caps - refs):
            issues.append({"check": "crossref-" + name + "-unreferenced", "severity": "info",
                           "file": None, "line": None, "quote": "{} {}".format(label, n),
                           "detail": "定义了编号 {} 但未见正文引用".format(n)})
    return issues


def citation_issues(files):
    issues = []
    cited, entries = set(), set()
    for rel, text, _ in files:
        for m in CITE_NUM.finditer(text):
            body = m.group(1)
            for part in re.split(r"[,，]", body):
                part = part.strip()
                if "-" in part or "–" in part:
                    a, b = re.split(r"[-–]", part)
                    try:
                        cited.update(range(int(a), int(b) + 1))
                    except ValueError:
                        continue
                else:
                    try:
                        cited.add(int(part))
                    except ValueError:
                        continue
        for line in text.splitlines():
            m = REF_ENTRY.match(line)
            if m:
                entries.add(int(m.group(1)))
    for n in sorted(cited - entries):
        issues.append({"check": "citation-missing-entry", "severity": "error",
                       "file": None, "line": None, "quote": "[{}]".format(n),
                       "detail": "正文引用 [{}] 但参考文献表无对应条目".format(n)})
    for n in sorted(entries - cited):
        issues.append({"check": "reference-uncited", "severity": "info",
                       "file": None, "line": None, "quote": "[{}]".format(n),
                       "detail": "参考文献条目 [{}] 未被正文引用".format(n)})
    return issues


def abbreviation_issues(files):
    issues = []
    for rel, text, lines in files:
        defs = {}
        for i, line in enumerate(lines, 1):
            for m in ABBR_DEF.finditer(line):
                full, abbr = m.group(1).strip(), m.group(2).strip()
                key = abbr.lower()
                if key in defs:
                    issues.append({"check": "abbr-redefined", "severity": "warning",
                                   "file": rel, "line": i, "quote": line.strip(),
                                   "detail": "缩写 {} 重复定义（首次 {}:{}）".format(
                                       abbr, defs[key][0], defs[key][1])})
                else:
                    defs[key] = (rel, i, full)
        for key, (f, ln, full) in defs.items():
            # 定义后是否仍频繁使用全称而非缩写（粗略：全称在正文再次出现）
            full_uses = len(re.findall(re.escape(full), text))
            if full_uses > 2:
                issues.append({"check": "abbr-unused", "severity": "info",
                               "file": f, "line": ln, "quote": "{}（以下简称 {}）".format(full, key),
                               "detail": "定义缩写 {} 后全称仍出现 {} 次，建议统一".format(key, full_uses)})
    return issues


def duplicate_sentence_issues(files):
    issues, seen = [], {}
    for rel, text, _ in files:
        sents = [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) > 40]
        for s in sents:
            if s in seen and seen[s][0] != rel:
                issues.append({"check": "duplicate-sentence", "severity": "warning",
                               "file": rel, "line": None, "quote": s[:120],
                               "detail": "与 {}:{} 逐字重复".format(seen[s][0], seen[s][1])})
            else:
                seen.setdefault(s, (rel, None))
    return issues


def run_checks(output_dir):
    root = Path(output_dir)
    files = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.name == "result.json":
                continue
            if p.suffix.lower() not in TEXT_EXT:
                continue
            txt = read_text(p)
            if txt is None:
                continue
            files.append((p.as_posix(), txt, txt.splitlines()))
    issues = []
    issues += numbered_issues(files)
    issues += crossref_issues(files)
    issues += citation_issues(files)
    issues += abbreviation_issues(files)
    issues += duplicate_sentence_issues(files)
    counts = {}
    for iss in issues:
        counts[iss["severity"]] = counts.get(iss["severity"], 0) + 1
    return {"issues": issues, "counts": counts, "files_checked": len(files)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check.py", description="long-doc consistency checks")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)
    report = run_checks(args.output)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / "check_report.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(work / "check_report.json")
    cl_path = work / "change_log.json"
    cl = load_json(cl_path) if cl_path.exists() else None
    if not isinstance(cl, list):
        cl = []
    cl.append({"step": "check", "time": now_iso(), "action": "consistency check",
               "target": "work/check_report.json",
               "note": "{} files, {} issues (error {} / warning {} / info {})".format(
                   report["files_checked"], len(report["issues"]),
                   report["counts"].get("error", 0), report["counts"].get("warning", 0),
                   report["counts"].get("info", 0))})
    tmp2 = work / "change_log.json.tmp"
    tmp2.write_text(json.dumps(cl, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp2.replace(cl_path)
    print(json.dumps({"ok": True, "files_checked": report["files_checked"],
                      "counts": report["counts"],
                      "report": str(work / "check_report.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
