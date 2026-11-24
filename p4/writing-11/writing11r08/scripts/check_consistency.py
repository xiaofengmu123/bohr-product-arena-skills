#!/usr/bin/env python3
"""Deterministic verification for academic translation deliverables.

Checks four quality gates from fixed source materials:
  1. 数字实 (number/unit/year fidelity, incl. 万/亿 <-> million/billion);
  2. 术语稳 (term-variant consistency against a glossary);
  3. 对得齐 (segment/paragraph alignment, figure/table numbering continuity,
     cross-reference validity);
  4. 边界清 (citation markers preserved, fact_source_map quotes actually
     present verbatim in the source).

Reads --source (file or directory) and --target (file or directory); honours
--glossary (json). Writes a JSON report to --out. Never crashes on missing or
empty input; exit code is always 0 so callers read the report instead.

Stdlib only.
"""
import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# text collection
# ---------------------------------------------------------------------------
TEXT_EXT = {".txt", ".md", ".tex", ".rst"}


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return ""


def _read_json_texts(path):
    """Extract concatenated text from a .json/.jsonl extracted-segment file."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        data = json.loads(raw)
    except Exception:
        return ""
    parts = []

    def walk(node):
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for k in ("text", "content", "body", "value", "quote"):
                if k in node and isinstance(node[k], str):
                    parts.append(node[k])
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
    walk(data)
    return "\n".join(parts)


def collect_text(path):
    """Return a dict {relpath: text} for one file or a directory tree."""
    out = {}
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in TEXT_EXT:
            out[path] = _read_text(path)
        elif ext in (".json", ".jsonl"):
            out[path] = _read_json_texts(path)
        return out
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                ext = os.path.splitext(fn)[1].lower()
                if ext in TEXT_EXT:
                    out[full] = _read_text(full)
                elif ext in (".json", ".jsonl"):
                    out[full] = _read_json_texts(full)
        return out
    return {}


# ---------------------------------------------------------------------------
# number / unit / year extraction with 万/亿 <-> million/billion normalisation
# ---------------------------------------------------------------------------
MULTIPLIERS = {
    "万": 1e4, "亿": 1e8, "千": 1e3, "百": 1e2, "十": 1e1,
    "million": 1e6, "millions": 1e6, "billion": 1e9, "billions": 1e9,
    "trillion": 1e12, "trillions": 1e12, "thousand": 1e3, "thousands": 1e3,
    "hundred": 1e2, "hundreds": 1e2,
}

# Arabic number core + optional Chinese/English multiplier word (space allowed).
NUM_WORD_RE = re.compile(
    r'(?<![\w.])'
    r'(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)'
    r'\s*(万|亿|千|百|十|million|millions|billion|billions|trillion|trillions|'
    r'thousand|thousands|hundred|hundreds)?'
    r'(?![A-Za-z0-9])'
)

# Arabic number + bare letter suffix (no space): 3k, 5M, 12B, 2T.
NUM_LETTER_RE = re.compile(
    r'(?<![\w.])(\d+(?:\.\d+)?)([kKMBT])(?![A-Za-z])'
)

UNIT_RE = re.compile(
    r'(?<![A-Za-z])('
    r'°C|°F|%|μg|µg|mg|kg|ng|mL|ml|μL|µl|nL|μM|µM|nM|mM|μmol|mmol|mol|'
    r'nm|µm|μm|mm|cm|km|kHz|MHz|GHz|Hz|kPa|MPa|GPa|kW|MW|GW|kJ|MJ|mA|mV|eV|kJ/mol'
    r')(?![A-Za-z])'
)

YEAR_RE = re.compile(r'\b(18|19|20)\d{2}\b')


def _norm(value):
    if value == int(value):
        return str(int(value))
    return ("%.6g" % value)


def extract_numbers(text):
    """Return dict with keys 'numbers' (canonical set) and 'units' (multiset
    list). Percent signs attach to the preceding number as 'P:<value>'."""
    numbers = set()
    units = []
    percent = set()
    for m in NUM_WORD_RE.finditer(text):
        core, mult = m.group(1), m.group(2)
        try:
            v = float(core.replace(",", ""))
        except ValueError:
            continue
        if mult:
            v *= MULTIPLIERS.get(mult.lower(), 1.0)
        canon = _norm(v)
        numbers.add(canon)
    for m in NUM_LETTER_RE.finditer(text):
        core, letter = m.group(1), m.group(2)
        try:
            v = float(core)
        except ValueError:
            continue
        v *= MULTIPLIERS.get(letter, 1.0)
        numbers.add(_norm(v))
    for m in UNIT_RE.finditer(text):
        u = m.group(1)
        if u == "%":
            # attach to closest preceding number phrase for fidelity checks
            before = text[:m.start()]
            pm = list(NUM_WORD_RE.finditer(before)) + list(NUM_LETTER_RE.finditer(before))
            if pm:
                last = pm[-1]
                core = last.group(1)
                try:
                    v = float(core.replace(",", ""))
                    mult = last.group(2) if last.lastindex and last.lastindex >= 2 else None
                    if mult and isinstance(mult, str):
                        v *= MULTIPLIERS.get(mult.lower(), 1.0)
                    percent.add("P:" + _norm(v))
                except (ValueError, AttributeError):
                    pass
            units.append("%")
        else:
            units.append(u)
    return {"numbers": numbers, "units": units, "percent": percent}


def extract_years(text):
    return set(YEAR_RE.findall(text))


# ---------------------------------------------------------------------------
# citation marker extraction
# ---------------------------------------------------------------------------
BRACKET_CITE_RE = re.compile(r'\[(\d{1,3}(?:\s*[,，\-–—]\s*\d{1,3})*)\]')
AUTHOR_YEAR_RE = re.compile(r'\bet al\.?\s*,?\s*(?:\(?\s*)((?:19|20)\d{2})', re.IGNORECASE)


def extract_citations(text):
    bracketed = set()
    for m in BRACKET_CITE_RE.finditer(text):
        inner = m.group(1)
        for part in re.split(r'[,，\-–—]', inner):
            part = part.strip()
            if part.isdigit():
                bracketed.add(part)
    years = set()
    for m in AUTHOR_YEAR_RE.finditer(text):
        years.add(m.group(1))
    # bare parenthetical years "(2020)" as fallback
    for m in re.finditer(r'\(\s*((?:19|20)\d{2})\s*\)', text):
        years.add(m.group(1))
    return {"bracketed": bracketed, "years": years}


# ---------------------------------------------------------------------------
# figure / table numbering and cross-references
# ---------------------------------------------------------------------------
FIG_RE = re.compile(r'\b(?:Figure|Fig\.?|图)\s*\.?\s*(\d{1,3})', re.IGNORECASE)
TAB_RE = re.compile(r'\b(?:Table|表)\s*\.?\s*(\d{1,3})', re.IGNORECASE)
FIG_REF_RE = re.compile(r'(?:see\s+Figure|见图|如图|Figure)\s*\.?\s*(\d{1,3})', re.IGNORECASE)
TAB_REF_RE = re.compile(r'(?:see\s+Table|见表|如表|Table)\s*\.?\s*(\d{1,3})', re.IGNORECASE)


def extract_numbering(text):
    figs = sorted({int(m.group(1)) for m in FIG_RE.finditer(text)})
    tabs = sorted({int(m.group(1)) for m in TAB_RE.finditer(text)})
    fig_refs = sorted({int(m.group(1)) for m in FIG_REF_RE.finditer(text)})
    tab_refs = sorted({int(m.group(1)) for m in TAB_REF_RE.finditer(text)})
    return {"figures": figs, "tables": tabs, "fig_refs": fig_refs, "tab_refs": tab_refs}


def _gaps(seq):
    if len(seq) < 2:
        return []
    missing = []
    for a, b in zip(seq, seq[1:]):
        missing.extend(range(a + 1, b))
    return missing


# ---------------------------------------------------------------------------
# paragraph / segment alignment
# ---------------------------------------------------------------------------
def paragraph_count(text):
    paras = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
    return len(paras)


# ---------------------------------------------------------------------------
# glossary consistency
# ---------------------------------------------------------------------------
def check_terms(target_text, glossary):
    warnings = []
    terms = glossary.get("terms", []) if isinstance(glossary, dict) else []
    if not isinstance(terms, list):
        return warnings
    for t in terms:
        if not isinstance(t, dict):
            continue
        canonical = t.get("canonical")
        variants = t.get("variants") or []
        if not canonical:
            continue
        used = {}
        for cand in [canonical] + list(variants):
            n = target_text.count(cand)
            if n > 0:
                used[cand] = n
        distinct = [c for c in used if c != canonical]
        if canonical in used and distinct:
            warnings.append({
                "type": "term_variant_collision",
                "term": canonical,
                "used_variants": used,
                "detail": "正文同时出现 canonical 与变体，应统一为 canonical 或显式说明",
            })
        elif len(distinct) > 1:
            warnings.append({
                "type": "term_variant_collision",
                "term": canonical,
                "used_variants": used,
                "detail": "多个变体并存（canonical 未出现），应统一",
            })
    return warnings


# ---------------------------------------------------------------------------
# fact_source_map quote verification (anti-hallucination)
# ---------------------------------------------------------------------------
def _norm_ws(s):
    return re.sub(r'\s+', ' ', s or "").strip()


def check_quotes(source_text, facts):
    warnings = []
    src_norm = _norm_ws(source_text)
    if not facts:
        return warnings
    if not isinstance(facts, list):
        return warnings
    for i, item in enumerate(facts):
        if not isinstance(item, dict):
            continue
        quote = item.get("quote")
        if not quote:
            warnings.append({
                "type": "fact_quote_missing",
                "index": i,
                "item": item.get("item"),
                "detail": "fact_source_map 条目缺少逐字 quote",
            })
            continue
        if _norm_ws(quote) not in src_norm:
            warnings.append({
                "type": "fact_quote_not_found",
                "index": i,
                "item": item.get("item"),
                "quote": quote[:120],
                "detail": "quote 未能在原始材料中逐字定位，需复核 locator/quote",
            })
    return warnings


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="/input")
    ap.add_argument("--target", default="/app/output")
    ap.add_argument("--glossary", default=None)
    ap.add_argument("--result", default="/app/output/result.json",
                    help="result.json whose fact_source_map quotes are verified")
    ap.add_argument("--out", default="/app/work/consistency_report.json")
    args = ap.parse_args(argv)

    src_files = collect_text(args.source)
    tgt_files = collect_text(args.target)
    source_text = "\n".join(src_files.values())
    target_text = "\n".join(tgt_files.values())

    glossary = read_json(args.glossary, {})

    src_num = extract_numbers(source_text)
    tgt_num = extract_numbers(target_text)
    src_years = extract_years(source_text)
    tgt_years = extract_years(target_text)
    src_cite = extract_citations(source_text)
    tgt_cite = extract_citations(target_text)
    src_numbr = extract_numbering(source_text)
    tgt_numbr = extract_numbering(target_text)

    errors, warnings = [], []

    # 1) number fidelity
    missing_numbers = sorted(src_num["numbers"] - tgt_num["numbers"],
                             key=lambda x: (len(x), x))
    extra_numbers = sorted(tgt_num["numbers"] - src_num["numbers"],
                           key=lambda x: (len(x), x))
    if missing_numbers:
        errors.append({
            "type": "number_missing",
            "count": len(missing_numbers),
            "values": missing_numbers[:30],
            "detail": "原文数值在译文中未找到（可能被四舍五入/改写/遗漏），需核对",
        })
    if extra_numbers:
        warnings.append({
            "type": "number_extra",
            "count": len(extra_numbers),
            "values": extra_numbers[:30],
            "detail": "译文中出现原文没有的数值（可能误译或换算），需人工确认",
        })
    src_units = sorted(set(src_num["units"]))
    tgt_units = sorted(set(tgt_num["units"]))
    missing_units = [u for u in src_units if u not in tgt_units]
    if missing_units:
        warnings.append({
            "type": "unit_missing",
            "values": missing_units,
            "detail": "原文单位在译文中未出现，可能存在单位丢失或换算",
        })
    missing_percent = sorted(src_num["percent"] - tgt_num["percent"])
    if missing_percent:
        warnings.append({
            "type": "percent_missing",
            "values": missing_percent[:30],
            "detail": "百分比数值在译文中未匹配（注意中文可能用「%」或「百分」）",
        })

    # years
    missing_years = sorted(src_years - tgt_years)
    if missing_years:
        warnings.append({
            "type": "year_missing",
            "values": missing_years,
            "detail": "年份在译文中未找到，需确认是否被改写",
        })

    # 2) citations
    missing_cite = sorted(src_cite["bracketed"] - tgt_cite["bracketed"])
    extra_cite = sorted(tgt_cite["bracketed"] - src_cite["bracketed"])
    if missing_cite:
        warnings.append({
            "type": "citation_missing",
            "values": missing_cite,
            "detail": "方括号编号引用未在译文中保留（允许格式变化，但编号不应丢失）",
        })
    if extra_cite:
        warnings.append({
            "type": "citation_extra",
            "values": extra_cite,
            "detail": "译文中出现原文没有的引用编号，需人工确认是否误加",
        })
    missing_cite_years = sorted(src_cite["years"] - tgt_cite["years"])
    if missing_cite_years:
        warnings.append({
            "type": "citation_year_missing",
            "values": missing_cite_years,
            "detail": "作者-年份引用的年份在译文中缺失",
        })

    # 3) terminology
    term_warnings = check_terms(target_text, glossary)
    warnings.extend(term_warnings)

    # 4) alignment / numbering
    src_p = paragraph_count(source_text)
    tgt_p = paragraph_count(target_text)
    if src_p > 0 and (tgt_p == 0 or tgt_p < src_p * 0.5):
        errors.append({
            "type": "alignment_short",
            "source_paragraphs": src_p,
            "target_paragraphs": tgt_p,
            "detail": "译文段落数显著少于原文，可能存在整段漏译",
        })
    elif src_p > 0 and tgt_p > src_p * 3:
        warnings.append({
            "type": "alignment_long",
            "source_paragraphs": src_p,
            "target_paragraphs": tgt_p,
            "detail": "译文段落数显著多于原文，可能存在拆分或冗余",
        })

    fig_gaps = _gaps(tgt_numbr["figures"])
    tab_gaps = _gaps(tgt_numbr["tables"])
    if fig_gaps:
        warnings.append({"type": "figure_number_gap", "missing": fig_gaps,
                         "detail": "图编号不连续"})
    if tab_gaps:
        warnings.append({"type": "table_number_gap", "missing": tab_gaps,
                         "detail": "表编号不连续"})
    dangling_fig = sorted(set(tgt_numbr["fig_refs"]) - set(tgt_numbr["figures"]))
    dangling_tab = sorted(set(tgt_numbr["tab_refs"]) - set(tgt_numbr["tables"]))
    if dangling_fig:
        warnings.append({"type": "dangling_figure_ref", "refs": dangling_fig,
                         "detail": "交叉引用指向不存在的图"})
    if dangling_tab:
        warnings.append({"type": "dangling_table_ref", "refs": dangling_tab,
                         "detail": "交叉引用指向不存在的表"})

    # 5) fact quote verification
    result = read_json(args.result, {})
    facts = result.get("fact_source_map", []) if isinstance(result, dict) else []
    warnings.extend(check_quotes(source_text, facts))

    stats = {
        "source_chars": len(source_text),
        "target_chars": len(target_text),
        "source_files": len(src_files),
        "target_files": len(tgt_files),
        "source_numbers": len(src_num["numbers"]),
        "target_numbers": len(tgt_num["numbers"]),
        "source_paragraphs": src_p,
        "target_paragraphs": tgt_p,
        "source_years": sorted(src_years),
        "target_years": sorted(tgt_years),
    }
    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }
    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(
        {"ok": report["ok"], "errors": len(errors), "warnings": len(warnings),
         "out": out}, ensure_ascii=False))
    return 0


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


if __name__ == "__main__":
    sys.exit(main())
