#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Surface-level translation consistency checker for bilingual academic writing.

Standard library only. This checker is deliberately honest: it verifies the
things a deterministic script can verify (structure, citation markers, digit
presence, glossary usage, section numbering) and routes everything that
requires semantic judgement (negation scope, hedge strength, number
reformatting such as 万/亿 vs million/billion) into a `manual_verify` queue.
It never claims a translation is semantically correct on its own; semantic
correctness is confirmed by the Agent and recorded in fact_source_map.

Usage
    check_translation.py --source SRC.txt --target TRG.txt \
        [--glossary glossary.json] [--out report.json]

The source/target files should be plain text or Markdown. For a single
bilingual parallel file, pass the extracted halves, or pass the whole file as
target and note the mode in the report.

Exit code: 0 when there are no `errors`; 1 when errors exist (warnings and
manual_verify items do not change the exit code).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Extraction helpers
# --------------------------------------------------------------------------- #
NUMBER_RE = re.compile(
    r"(?<![\w])"
    r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?:\s*(?:%|‰|°C|°F|K|kg|g|mg|µg|μg|mL|L|mol|mM|M|m|cm|mm|µm|μm|nm|s|ms|h|min|"
    r"Hz|kHz|MHz|GHz|W|kW|V|A|Ω|Pa|kPa|MPa|bar|Da|kDa|×\s*10\s*[+-]?\d+))?"
    r"(?![\w])"
)

CITATION_BRACKET = re.compile(r"\[\s*(?:\d+(?:\s*[,，\-–—]\s*\d+)*\s*)\]")
CITATION_AUTHOR_YEAR = re.compile(r"\([A-Z][A-Za-z&.\- ]+,\s*(?:19|20)\d{2}[a-z]?\)")
CITATION_ETAL = re.compile(r"\bet\s+al\.", re.IGNORECASE)

FIGURE_TABLE_RE = re.compile(
    r"(?:Fig(?:ure)?\.?\s*\d+[a-z]?|Table\s*\d+|图\s*\d+|表\s*\d+|"
    r"公式\s*\(\d+\)|Equation\s*\(\d+\)|Eq\.?\s*\d+)", re.IGNORECASE)
FORMULA_RE = re.compile(r"(?:\\\[|\$[^$]+\$|\\begin\{equation|\\begin\{align)")
FOOTNOTE_RE = re.compile(r"\[\^[^\]]+\]|\\footnote\{")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+|\b(?:\d+(?:\.\d+)*\.?)\s+[A-Z\u4e00-\u9fff]")

# Hedges / negation / modality markers whose semantic preservation must be
# checked by hand. These cannot be auto-verified across languages.
HEDGE_MARKERS = [
    "not", "no ", "never", "only", "merely", "may", "might", "could",
    "suggest", "suggests", "indicates", "appears", "approximately", "about",
    "around", "roughly", "significantly", "likely", "unlikely", "cannot",
    "however", "although", "whereas", "despite", "limited", "potential",
    "probably", "presumably", "不", "未", "仅", "可能", "或许", "约", "大致",
    "显著", "很可能", "未必", "尽管", "然而", "受限", "潜在",
]


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_json(path: str):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return None


def atomic_write_json(path: str, obj) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def line_number(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def blocks(text: str):
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def extract_numbers(text: str):
    found = []
    for m in NUMBER_RE.finditer(text):
        found.append({
            "token": m.group(0).replace(" ", ""),
            "raw": m.group(0),
            "line": line_number(text, m.start()),
        })
    return found


def extract_citations(text: str):
    found = []
    for m in CITATION_BRACKET.finditer(text):
        found.append({"kind": "bracket", "token": m.group(0),
                      "line": line_number(text, m.start())})
    for m in CITATION_AUTHOR_YEAR.finditer(text):
        found.append({"kind": "author_year", "token": m.group(0),
                      "line": line_number(text, m.start())})
    for m in CITATION_ETAL.finditer(text):
        found.append({"kind": "et_al", "token": m.group(0),
                      "line": line_number(text, m.start())})
    return found


def extract_structure(text: str):
    found = []
    for m in FIGURE_TABLE_RE.finditer(text):
        found.append({"kind": "figure_table", "token": m.group(0),
                      "line": line_number(text, m.start())})
    for m in FORMULA_RE.finditer(text):
        found.append({"kind": "formula", "token": m.group(0),
                      "line": line_number(text, m.start())})
    for m in FOOTNOTE_RE.finditer(text):
        found.append({"kind": "footnote", "token": m.group(0),
                      "line": line_number(text, m.start())})
    for m in HEADING_RE.finditer(text, re.MULTILINE):
        found.append({"kind": "heading", "token": m.group(0).strip(),
                      "line": line_number(text, m.start())})
    return found


def load_glossary(path: str):
    if not path or not os.path.isfile(path):
        return []
    data = read_json(path)
    if not isinstance(data, dict):
        return []
    terms = data.get("terms", data)
    if not isinstance(terms, list):
        return []
    return [t for t in terms if isinstance(t, dict) and t.get("canonical")]


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_glossary_terms(glossary, target_text: str):
    rows, manual = [], []
    target_norm = normalize(target_text)
    for term in glossary:
        canonical = term.get("canonical", "")
        variants = term.get("variants") or []
        if not isinstance(variants, list):
            variants = []
        # Include canonical and any variants; search case-insensitively.
        pool = [canonical] + variants
        present = []
        for v in pool:
            if v and normalize(str(v)) in target_norm:
                present.append(v)
        if not present:
            manual.append({"item": canonical, "kind": "term",
                           "detail": "术语未在译文中出现，确认是否遗漏或改用其他译法",
                           "context": term.get("context", "")})
        elif len(set(normalize(p) for p in present)) > 1:
            rows.append({"term": canonical, "found": present,
                         "inconsistent": True,
                         "detail": "同一术语出现多个译法变体，应统一为 canonical"})
        else:
            rows.append({"term": canonical, "found": present, "inconsistent": False})
    return rows, manual


def check_numbers(source_text: str, target_text: str):
    """Numbers whose digit sequences are absent from the target go to manual_verify,
    because a legitimate rewrite (e.g. 10 million -> 一千万) drops the Arabic digits.
    Presence of the digits is a strong positive signal; absence is not proof of error."""
    src = extract_numbers(source_text)
    tgt_norm = normalize(target_text)
    verified, manual = [], []
    for item in src:
        token_norm = normalize(item["token"])
        digits = re.sub(r"\D", "", token_norm)
        if digits and digits in tgt_norm:
            verified.append(item)
        else:
            manual.append({"item": item["raw"], "kind": "number",
                           "source_line": item["line"],
                           "detail": "译文未找到相同数字串，需人工确认是否以汉字数词/单位重写、是否被舍入或遗漏"})
    return {"source_numbers": len(src), "digit_verified": len(verified),
            "manual_verify": len(manual)}, manual


def check_citations(source_text: str, target_text: str):
    src = extract_citations(source_text)
    tgt = extract_citations(target_text)
    src_tokens = [normalize(c["token"]) for c in src]
    tgt_tokens = [normalize(c["token"]) for c in tgt]
    missing = []
    for item in src:
        if normalize(item["token"]) not in tgt_tokens:
            missing.append({"item": item["token"], "kind": "citation",
                            "source_line": item["line"],
                            "detail": "引用标记未在译文中原样出现，需确认是否保留/转换格式"})
    return {"source_citations": len(src), "target_citations": len(tgt),
            "missing_verbatim": len(missing)}, missing


def check_structure(source_text: str, target_text: str):
    src = extract_structure(source_text)
    tgt = extract_structure(target_text)
    src_tokens = sorted(set(normalize(s["token"]) for s in src))
    tgt_tokens = sorted(set(normalize(s["token"]) for s in tgt))
    missing = []
    for tok in src_tokens:
        if tok and tok not in tgt_tokens:
            missing.append({"item": tok, "kind": "structure",
                            "detail": "图表/公式/脚注/标题编号在译文中缺失，需确认原位保留"})
    return {"source_markers": len(src_tokens), "target_markers": len(tgt_tokens),
            "missing": len(missing)}, missing


def check_alignment(source_text: str, target_text: str):
    sb, tb = blocks(source_text), blocks(target_text)
    delta = abs(len(sb) - len(tb))
    if delta == 0:
        level = "ok"
    elif delta <= max(2, len(sb) * 0.05):
        level = "warn"
    else:
        level = "error"
    return {
        "source_blocks": len(sb),
        "target_blocks": len(tb),
        "delta": delta,
        "level": level,
        "note": "句段数不一致可能由对照排版/合并段落引起，需人工确认句段对应关系",
    }


def build_hedge_queue(source_text: str):
    queue = []
    src_lower = source_text.lower()
    for marker in HEDGE_MARKERS:
        count = src_lower.count(marker.lower())
        if count > 0:
            queue.append({
                "item": marker, "kind": "hedge",
                "count": count,
                "detail": "否定/模糊限制/因果标记，需人工确认译文保持原意、范围和强度",
            })
    return queue


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(source_path: str, target_path: str, glossary_path: str, out_path: str):
    source_text = read_text(source_path)
    target_text = read_text(target_path)
    glossary = load_glossary(glossary_path)

    errors, warnings = [], []
    manual_verify = []

    glossary_rows, glossary_manual = check_glossary_terms(glossary, target_text)
    manual_verify.extend(glossary_manual)

    num_stats, num_manual = check_numbers(source_text, target_text)
    manual_verify.extend(num_manual)

    cit_stats, cit_missing = check_citations(source_text, target_text)
    for item in cit_missing:
        errors.append({"kind": "citation", "item": item["item"],
                       "source_line": item["source_line"], "detail": item["detail"]})

    struct_stats, struct_missing = check_structure(source_text, target_text)
    for item in struct_missing:
        warnings.append({"kind": "structure", "item": item["item"], "detail": item["detail"]})

    align = check_alignment(source_text, target_text)
    if align["level"] == "error":
        warnings.append({"kind": "alignment", "item": "block_count",
                         "detail": "源/译文句段数相差 %d（%d vs %d）" % (
                             align["delta"], align["source_blocks"], align["target_blocks"])})

    manual_verify.extend(build_hedge_queue(source_text))

    report = {
        "ok": len(errors) == 0,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "summary": {
            "alignment": align,
            "numbers": num_stats,
            "citations": cit_stats,
            "structure": struct_stats,
            "glossary_terms": len(glossary_rows),
            "glossary_inconsistent": sum(1 for r in glossary_rows if r.get("inconsistent")),
            "errors": len(errors),
            "warnings": len(warnings),
            "manual_verify": len(manual_verify),
        },
        "errors": errors,
        "warnings": warnings,
        "manual_verify": manual_verify,
        "glossary": glossary_rows,
        "note": ("本脚本只做表面核对：结构、引用标记、数字串存在性、术语表命中。"
                 "否定范围、模糊限制语强度、数字改写成汉字数词等语义项全部进入 manual_verify，"
                 "由 Agent 逐条确认或登记 uncertainty，脚本不冒充语义校验。"),
    }
    if out_path:
        atomic_write_json(out_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--glossary", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    for path, label in ((args.source, "source"), (args.target, "target")):
        if not os.path.isfile(path):
            print("fatal: %s file not found: %s" % (label, path), file=sys.stderr)
            return 2
    return run(args.source, args.target, args.glossary, args.out)


if __name__ == "__main__":
    sys.exit(main())
