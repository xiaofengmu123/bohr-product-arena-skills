#!/usr/bin/env python3
"""Deterministic consistency checks for bilingual translation deliverables.

Checks run over /app/output deliverables plus a glossary.json:
  - glossary well-formedness
  - term variant consistency (multiple spellings of one canonical term)
  - number fidelity per aligned segment (heuristic; reported as warning)
  - citation/reference pairing (numeric [n] and author-year)
  - segment alignment continuity (SEG-01.. sequential, src/tgt both present)
  - negation/hedge marker coverage (info-level ratio)

Emits a JSON report. Exit code: 0 = ran (findings allowed), 2 = usage error,
1 = internal error. Findings carry severity error|warning|info.
"""
import argparse
import datetime
import json
import os
import re
import sys

SEG_RE = re.compile(r"^###\s*\[(SEG-\d+)\]", re.MULTILINE)
NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*\s*(?:%|％|mg|mL|nm|μm|µg|kg|s|h|min|V|kV|mV|K|M|G|T|Hz|mm|cm|km)?")
CITE_NUM_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")
CITE_YEAR_RE = re.compile(r"\((?:[A-Z][A-Za-z\-']+(?: et al\.)?[ ,;&]*)+?,? ?(?:19|20)\d{2}[a-z]?\)")
REF_NUM_RE = re.compile(r"^\s*\[(\d+)\]")
NEG_MARKERS = ["not", "no", "none", "never", "without", "unable", "fail",
               "未", "不", "没有", "无", "从未", "无法", "并非"]
HEDGE_MARKERS = ["may", "might", "could", "possibly", "likely", "suggest",
                 "potential", "可能", "或", "似乎", "大概", "也许", "提示", "表明"]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except (UnicodeDecodeError, OSError) as e:
            last = str(e)
    return "", last


def list_md(dirpath):
    out = []
    for fn in sorted(os.listdir(dirpath)):
        p = os.path.join(dirpath, fn)
        if os.path.isfile(p) and fn.lower().endswith((".md", ".markdown", ".txt")):
            out.append(p)
    return out


def parse_segments(text, path):
    """Return ordered list of {id, src, tgt, src_loc, line}."""
    segs = []
    cur = None
    for lineno, line in enumerate(text.splitlines(), 1):
        m = SEG_RE.match(line.strip())
        if m:
            if cur is not None:
                segs.append(cur)
            cur = {"id": m.group(1), "src": "", "tgt": "", "src_loc": "", "file": path, "line": lineno}
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("- src:"):
            cur["src"] = s[len("- src:"):].strip()
        elif s.startswith("- tgt:"):
            cur["tgt"] = s[len("- tgt:"):].strip()
        elif s.startswith("- src_loc:"):
            cur["src_loc"] = s[len("- src_loc:"):].strip()
    if cur is not None:
        segs.append(cur)
    return segs


def number_tokens(text):
    toks = []
    for m in NUM_RE.finditer(text):
        t = m.group(0).replace(",", "").replace("％", "%").strip()
        if t:
            toks.append(t)
    return toks


def expand_range(rng):
    """'1-3' -> {1,2,3}; '1,3' -> {1,3}; '1' -> {1}."""
    nums = set()
    for part in re.split(r",", rng):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a, b = int(a), int(b)
                if a <= b:
                    nums.update(range(a, b + 1))
                continue
            except ValueError:
                pass
        try:
            nums.add(int(part))
        except ValueError:
            pass
    return nums


def check_glossary(gloss_path):
    issues = []
    glossary = None
    data, err = read_text(gloss_path)
    if not data:
        return None, [{"severity": "error", "check": "glossary",
                       "locator": gloss_path, "message": "glossary 缺失或不可读: %s" % err}]
    try:
        glossary = json.loads(data)
    except ValueError as e:
        return None, [{"severity": "error", "check": "glossary",
                       "locator": gloss_path, "message": "glossary 非合法 JSON: %s" % e}]
    terms = glossary.get("terms") if isinstance(glossary, dict) else None
    if not isinstance(terms, list) or not terms:
        issues.append({"severity": "warning", "check": "glossary",
                       "locator": gloss_path,
                       "message": "glossary.terms 为空或缺失；术语一致性检查跳过"})
        return glossary, issues
    for i, t in enumerate(terms):
        if not isinstance(t, dict) or not t.get("canonical"):
            issues.append({"severity": "error", "check": "glossary",
                           "locator": "%s#terms[%d]" % (gloss_path, i),
                           "message": "术语项缺少 canonical"})
    return glossary, issues


def check_terms(glossary, md_files, issues):
    if not glossary:
        return
    terms = glossary.get("terms") or []
    for t in terms:
        if not isinstance(t, dict) or not t.get("canonical"):
            continue
        canonical = t["canonical"]
        variants = [v for v in t.get("variants", []) if v and v != canonical]
        found = {"canonical": [], "variants": {}}
        for p in md_files:
            text, _ = read_text(p)
            if canonical in text:
                found["canonical"].append(p)
            for v in variants:
                if v in text:
                    found["variants"].setdefault(v, []).append(p)
        used = [v for v, locs in found["variants"].items() if locs]
        if not found["canonical"] and used:
            issues.append({"severity": "warning", "check": "term",
                           "locator": canonical,
                           "message": "术语 '%s' 未使用统一译法（只见变体 %s）" % (canonical, ",".join(used))})
        if len(used) > 1:
            issues.append({"severity": "warning", "check": "term",
                           "locator": canonical,
                           "message": "术语 '%s' 出现多种写法: %s" % (canonical, ",".join(used))})


def check_numbers(segs, issues):
    for s in segs:
        if not s["src"] or not s["tgt"]:
            continue
        a = number_tokens(s["src"])
        b = number_tokens(s["tgt"])
        missing = [x for x in a if x not in b]
        extra = [x for x in b if x not in a]
        loc = "%s:%d" % (s["file"], s["line"])
        if missing:
            issues.append({"severity": "warning", "check": "number",
                           "locator": loc,
                           "message": "%s 译文疑似缺少数字 %s（请人工核对）" % (s["id"], ",".join(missing))})
        if extra:
            issues.append({"severity": "warning", "check": "number",
                           "locator": loc,
                           "message": "%s 译文出现原文未见数字 %s（请确认非补写）" % (s["id"], ",".join(extra))})


def check_citations(md_files, issues):
    for p in md_files:
        text, _ = read_text(p)
        cited = set()
        for m in CITE_NUM_RE.finditer(text):
            cited |= expand_range(m.group(1))
        referenced = set()
        for line in text.splitlines():
            m = REF_NUM_RE.match(line.strip())
            if m:
                referenced.add(int(m.group(1)))
        # author-year citations are checked for presence only (no ref parse)
        years = CITE_YEAR_RE.findall(text)
        orphan = sorted(cited - referenced)
        uncited = sorted(referenced - cited)
        if orphan:
            issues.append({"severity": "error", "check": "citation",
                           "locator": p,
                           "message": "正文引用 [%s] 在参考文献中找不到" % ",".join(map(str, orphan))})
        if uncited:
            issues.append({"severity": "warning", "check": "citation",
                           "locator": p,
                           "message": "参考文献 [%s] 未被正文引用" % ",".join(map(str, uncited))})
        if years:
            issues.append({"severity": "info", "check": "citation",
                           "locator": p,
                           "message": "检测到作者-年份引用 %d 处，请逐条在 fact_source_map 登记" % len(years)})


def check_alignment(segs, issues):
    ids = [s["id"] for s in segs]
    if not ids:
        issues.append({"severity": "warning", "check": "alignment",
                       "locator": "(all)",
                       "message": "未检测到任何 [SEG-xx] 对齐块；确认双语对照稿格式符合约定"})
        return
    nums = []
    for i in ids:
        m = re.match(r"SEG-(\d+)", i)
        nums.append(int(m.group(1)) if m else -1)
    if any(n < 0 for n in nums):
        issues.append({"severity": "warning", "check": "alignment",
                       "locator": ",".join(ids),
                       "message": "存在非 SEG-数字 格式的段标识"})
    dups = {n for n in nums if nums.count(n) > 1}
    if dups:
        issues.append({"severity": "error", "check": "alignment",
                       "locator": ",".join("SEG-%02d" % n for n in sorted(dups)),
                       "message": "段编号重复"})
    for s in segs:
        loc = "%s:%d" % (s["file"], s["line"])
        if not s["src"]:
            issues.append({"severity": "error", "check": "alignment",
                           "locator": loc, "message": "%s 缺 src" % s["id"]})
        if not s["tgt"]:
            issues.append({"severity": "warning", "check": "alignment",
                           "locator": loc, "message": "%s 缺 tgt（未译）" % s["id"]})
        if not s["src_loc"]:
            issues.append({"severity": "warning", "check": "alignment",
                           "locator": loc, "message": "%s 缺 src_loc 来源定位" % s["id"]})


def check_hedges(segs, issues):
    src_markers = sum(1 for s in segs for m in NEG_MARKERS + HEDGE_MARKERS if m in s["src"])
    tgt_markers = sum(1 for s in segs for m in NEG_MARKERS + HEDGE_MARKERS if m in s["tgt"])
    if segs:
        issues.append({"severity": "info", "check": "hedge",
                       "locator": "(all)",
                       "message": "否定/限定标记：原文命中 %d 次，译文命中 %d 次（比率仅作人工复核线索）"
                       % (src_markers, tgt_markers)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="/app/output")
    ap.add_argument("--glossary", default="/app/output/glossary.json")
    ap.add_argument("--out", default="/app/work/consistency_report.json")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.dir):
        print("error: --dir not a directory: %s" % args.dir, file=sys.stderr)
        return 2

    issues = []
    md_files = list_md(args.dir)
    glossary, g_issues = check_glossary(args.glossary)
    issues.extend(g_issues)

    segs = []
    for p in md_files:
        text, _ = read_text(p)
        segs.extend(parse_segments(text, p))

    check_terms(glossary, md_files, issues)
    check_numbers(segs, issues)
    check_citations(md_files, issues)
    check_alignment(segs, issues)
    check_hedges(segs, issues)

    summary = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        summary[i["severity"]] = summary.get(i["severity"], 0) + 1

    report = {
        "generated_at": now_iso(),
        "dir": args.dir,
        "glossary": args.glossary,
        "files": md_files,
        "segments": len(segs),
        "summary": summary,
        "issues": issues,
    }
    d = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(d, exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, args.out)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
