#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""facts.py —— 从科研材料提取事实清单与术语表（供改写时“留得住、说得准”）。

产出（写入 /app/work）：
- facts.json：数值/统计量/图表引用/文献引用/缩写/论断强度标记，每条带 quote + locator。
- terms.json：术语表骨架（缩写->全称、候选同义词），供术语锁定。
- 控制台打印统计摘要。

PDF 处理：优先尝试 pdftotext；失败则查找同名的 .txt/.md/.jsonl 已提取文本；再失败记录降级。
只用标准库；默认 --input /input --work /app/work（manifest.json 亦可复用）。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TEXT_EXTS = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv",
             ".yaml", ".yml", ".tex", ".xml", ".html", ".rst"}

NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"\d+(?:\.\d+)?\s*(?:%|％|‰|ppb|ppm|mg|g|kg|ng|μg|ug|mL|L|μL|uL|M|mM|μM|uM|"
    r"°C|℃|K|h|min|s|ms|mm|cm|m|km|Hz|kHz|MHz|GHz|eV|keV|MeV|GeV|Da|kDa|IU|U|"
    r"CFU|×|x)?"
    r"(?:\s*[-–—~至]\s*\d+(?:\.\d+)?)?"
    r")"
)
STAT_RE = re.compile(
    r"\b(n|N)\s*=\s*\d[\d,]*(?:\s*[,，;；]|\s|$)|"
    r"\b(?:样本量|样本数|入组|纳入|受试者|患者|志愿者)\s*[=为:：]?\s*\d[\d,]*|"
    r"\b(?:p|P)\s*[<>=≤≥]\s*0?\.\d+|"
    r"\b(?:CI|置信区间|可信区间)[^。.;；\n]{0,40}|"
    r"\b(?:OR|RR|HR|IRR|RD|SMD|WMD|MD)\s*[=:：]?\s*\d+(?:\.\d+)?|"
    r"\b(?:SD|SE|SEM|IQR|I2)\s*[=:：]?\s*\d+(?:\.\d+)?|"
    r"[±]\s*\d+(?:\.\d+)?"
)
FIGREF_RE = re.compile(
    r"(?:图|表|式)\s*\d+[A-Za-z]?(?:\s*[-–—]\s*\d+[A-Za-z]?)?|"
    r"\b(?:Fig(?:ure)?\.?|Table|Eq(?:uation)?\.?)\s*\d+[A-Za-z]?(?:\s*[-–—]\s*\d+[A-Za-z]?)?"
)
CITE_RE = re.compile(
    r"\[\s*\d+(?:\s*[-–—,，]\s*\d+)*\s*\]|"
    r"[A-Z][A-Za-z\-'’]+(?:\s+(?:et\s+al\.?|等|和|与))?\s*[,，]?\s*\(?\s*(?:19|20)\d{2}[a-z]?\)?"
)
ABBR_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 ··\-/、（）()]{2,60})\s*[（(]\s*"
    r"([A-Za-z][A-Za-z0-9\-]{1,19})\s*[）)]"
)
STRENGTH_CJK = r"初步表明|因果关系|因果|表明|证实|提示|可能|或许|倾向于|相关|关联|推断|预计|有望|尚需|有待|不足|局限|尚未|未证实|外推"
STRENGTH_LATIN = r"suggest|indicate|show|demonstrat\w+|may|might|could|likely|associated|association|correlation|causal|cause"
STRENGTH_RE = re.compile(r"(?:" + STRENGTH_CJK + r"|\b(?:" + STRENGTH_LATIN + r")\b)")


def read_text(path: Path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None, "undecodable"


def pdf_to_text(path: Path):
    """尝试 pdftotext；返回文本或 None。"""
    for cmd in ("pdftotext",):
        try:
            out = subprocess.run(
                [cmd, "-layout", str(path), "-"],
                capture_output=True, timeout=60)
            if out.returncode == 0 and out.stdout:
                return out.stdout.decode("utf-8", errors="replace")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    # 查找同名已提取文本
    for ext in (".txt", ".md"):
        cand = path.with_suffix(ext)
        if cand.exists():
            txt, _ = read_text(cand)
            if txt:
                return txt
    for cand in sorted(path.parent.glob(path.stem + "*.txt")):
        txt, _ = read_text(cand)
        if txt:
            return txt
    return None


def extract_lines(text, source):
    """按行切分，返回 list[dict]，保留 locator。"""
    facts = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        locator = f"{source}#L{i}"

        for m in NUM_RE.finditer(line):
            q = m.group(0).strip()
            if q:
                facts.append({"kind": "number", "quote": q, "locator": locator})
        for m in STAT_RE.finditer(line):
            q = m.group(0).strip()
            if q:
                facts.append({"kind": "statistic", "quote": q, "locator": locator})
        for m in FIGREF_RE.finditer(line):
            q = m.group(0).strip()
            if q:
                facts.append({"kind": "figure_table_eq", "quote": q, "locator": locator})
        for m in CITE_RE.finditer(line):
            q = m.group(0).strip()
            if q:
                facts.append({"kind": "citation", "quote": q, "locator": locator})
        for m in ABBR_RE.finditer(line):
            full, abbr = m.group(1).strip(), m.group(2).strip()
            if len(abbr) >= 2 and len(full) <= 60:
                facts.append({
                    "kind": "abbreviation", "quote": m.group(0).strip(),
                    "full": full, "abbr": abbr, "locator": locator})
        for m in STRENGTH_RE.finditer(line):
            q = m.group(0).strip()
            if q:
                facts.append({"kind": "claim_strength", "quote": q, "locator": locator})
    return facts


def dedupe(facts):
    seen = set()
    out = []
    for f in facts:
        key = (f["kind"], f["quote"], f["locator"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def build_terms(facts):
    abbrs = {}
    for f in facts:
        if f["kind"] == "abbreviation":
            abbrs.setdefault(f["abbr"], {"full": f["full"], "locators": []})
            if f["locator"] not in abbrs[f["abbr"]]["locators"]:
                abbrs[f["abbr"]]["locators"].append(f["locator"])
    terms = {
        "lock_rule": "同一概念全文只用一个说法；缩写首次出现写 全称(缩写)，之后只用缩写",
        "abbreviations": abbrs,
        "synonym_candidates": [],
    }
    return terms


def main():
    ap = argparse.ArgumentParser(description="提取科研材料事实清单与术语表")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--manifest", default=None, help="复用 scaffold 的 manifest.json（可选）")
    args = ap.parse_args()

    input_dir = Path(args.input)
    work_dir = Path(args.work)
    work_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(args.manifest) if args.manifest else (work_dir / "manifest.json")
    files = []
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = m.get("files", [])
        except (OSError, json.JSONDecodeError):
            files = []

    if not files and input_dir.exists():
        for p in sorted(input_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in TEXT_EXTS | {".pdf"}:
                files.append({"path": str(p), "rel": str(p.relative_to(input_dir)),
                              "kind": "pdf" if p.suffix.lower() == ".pdf" else "text"})

    all_facts, degradations, sources_used = [], [], []
    for f in files:
        p = Path(f["path"])
        if not p.exists():
            degradations.append("missing:" + f.get("rel", str(p)))
            continue
        text, enc = None, None
        if p.suffix.lower() == ".pdf":
            text = pdf_to_text(p)
            if text is None:
                degradations.append("pdf_text_unavailable:" + f.get("rel", str(p)))
                continue
            enc = "pdftotext"
        elif p.suffix.lower() in TEXT_EXTS:
            text, enc = read_text(p)
        else:
            continue
        if text is None:
            degradations.append("undecodable:" + f.get("rel", str(p)))
            continue
        source = f.get("rel", str(p))
        facts = extract_lines(text, source)
        all_facts.extend(facts)
        sources_used.append({"source": source, "encoding": enc, "len": len(text)})

    all_facts = dedupe(all_facts)
    terms = build_terms(all_facts)

    facts_out = {
        "sources_used": sources_used,
        "degradations": degradations,
        "facts": all_facts,
        "counts": {
            k: sum(1 for x in all_facts if x["kind"] == k)
            for k in ("number", "statistic", "figure_table_eq", "citation",
                      "abbreviation", "claim_strength")
        },
    }
    (work_dir / "facts.json").write_text(
        json.dumps(facts_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (work_dir / "terms.json").write_text(
        json.dumps(terms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("facts:", len(all_facts))
    print("counts:", facts_out["counts"])
    print("sources_used:", len(sources_used), "degradations:", len(degradations))
    for d in degradations:
        print("  [degrade]", d)
    print("wrote:", work_dir / "facts.json", work_dir / "terms.json")
    sys.exit(0)


if __name__ == "__main__":
    main()
