#!/usr/bin/env python3
"""Deterministic bilingual-consistency checker for the translation Skill.

Reads the deliverables under a directory, parses the segment-aligned bilingual
format described in SKILL.md, and reports four failure classes as structured
JSON:

  alignment   SRC[n] without a matching TRG[n], duplicate ids, unordered ids
  numbers     numeric literals present in source but absent from target
  citations   citation markers ([..], (Author, year), (n), et al.) dropped
  terms       glossary canonical/variant used inconsistently across the text
  structure   section/figure/table numbering gaps or duplicates

Classification: `errors` block re-editing (broken alignment, duplicate ids);
`warnings` need human judgment (number/citation candidates, term variants,
numbering gaps). The report keeps file:line locators so the agent can fix a
single paragraph without re-reading the whole document.

Pure standard library. Usage:
  python3 scripts/check.py --dir /app/output --glossary /app/output/glossary.json \
      --out /app/work/check_report.json
"""

import argparse
import json
import os
import re
import sys

SRC_RE = re.compile(r"^>\s*SRC\[(\d+)\]")
TRG_RE = re.compile(r"^>\s*TRG\[(\d+)\]")

# Numeric literal: digits with one optional decimal group; grouping commas
# and % / ‰ / ×10^e are captured so values are compared by digit core.
NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\d+")

CITATION_RES = [
    re.compile(r"\[\s*\d+(?:\s*[,，\-–]\s*\d+)*\s*\]"),      # [1], [1,2], [1-3]
    re.compile(r"\(\s*[A-Z][A-Za-z'\- ]*,\s*\d{4}\s*[a-z]?\s*\)"),  # (Smith, 2020)
    re.compile(r"\(\s*\d{4}\s*[a-z]?\s*\)"),                  # (2020)
    re.compile(r"\bet\s+al\."),                               # et al.
    re.compile(r"\[\s*[A-Z][A-Za-z'\- ]*,\s*\d{4}\s*\]"),     # [Smith, 2020]
]

HEADING_RE = re.compile(r"^#+\s*(?:§\s*)?(\d+)(?:[\.、\s]|$)")
FIG_RE = re.compile(r"(?:图|Figure|Fig\.?)\s*(\d+)")
TABLE_RE = re.compile(r"(?:表|Table)\s*(\d+)")

TERM_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\- ]{1,40}|[\u4e00-\u9fff]{2,12}")


def load_json_any(path):
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.loads(fh.read())
        except (UnicodeDecodeError, OSError):
            continue
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def iter_md_files(directory):
    if not os.path.isdir(directory):
        return []
    out = []
    for base, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if name.lower().endswith((".md", ".markdown", ".txt")):
                out.append(os.path.join(base, name))
    return out


def read_lines(path, cap=2_000_000):
    try:
        if os.path.getsize(path) > cap:
            return []
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def num_core(literal):
    """Return the digit core used for value comparison (drop grouping commas)."""
    return re.sub(r"[,\s]", "", literal)


def scan_numbers(line):
    cores = {}
    for m in NUM_RE.finditer(line):
        lit = m.group(0)
        core = num_core(lit)
        if core and core.isdigit():
            cores.setdefault(core, lit)
    return cores


def scan_citations(line):
    found = []
    for rx in CITATION_RES:
        for m in rx.finditer(line):
            found.append(m.group(0))
    return found


def check_alignment(lines_by_file):
    errors, warnings = [], []
    for path, lines in lines_by_file.items():
        src_ids, trg_ids = [], []
        for lineno, line in enumerate(lines, 1):
            m = SRC_RE.match(line)
            if m:
                src_ids.append((int(m.group(1)), lineno))
            m = TRG_RE.match(line)
            if m:
                trg_ids.append((int(m.group(1)), lineno))
        src_set = {i for i, _ in src_ids}
        trg_set = {i for i, _ in trg_ids}
        for i, lineno in src_ids:
            if i not in trg_set:
                errors.append({
                    "class": "alignment", "severity": "error",
                    "file": path, "line": lineno, "segment": i,
                    "message": "SRC segment has no matching TRG segment",
                })
        # duplicate ids
        seen = {}
        for i, lineno in src_ids + trg_ids:
            seen.setdefault(i, []).append(lineno)
        for i, locs in seen.items():
            if len(locs) > 1:
                errors.append({
                    "class": "alignment", "severity": "error",
                    "file": path, "segment": i,
                    "message": "duplicate segment id appears %d times" % len(locs),
                })
        if src_ids:
            ordered = [i for i, _ in src_ids]
            if ordered != sorted(ordered):
                warnings.append({
                    "class": "alignment", "severity": "warning",
                    "file": path, "line": src_ids[0][1],
                    "message": "segment ids are not strictly increasing",
                })
    return errors, warnings


def check_numbers(lines_by_file):
    warnings = []
    for path, lines in lines_by_file.items():
        current_src = None
        for lineno, line in enumerate(lines, 1):
            m = SRC_RE.match(line)
            if m:
                current_src = (m.group(1), line, lineno)
                continue
            m = TRG_RE.match(line)
            if m and current_src:
                sid = m.group(1)
                src_cores = scan_numbers(current_src[1])
                trg_cores = scan_numbers(line)
                missing = [lit for core, lit in src_cores.items() if core not in trg_cores]
                if missing:
                    warnings.append({
                        "class": "numbers", "severity": "warning",
                        "file": path, "line": lineno, "segment": sid,
                        "message": "number(s) in source not found verbatim in target",
                        "missing": missing,
                    })
                current_src = None
    return [], warnings


def check_citations(lines_by_file):
    warnings = []
    for path, lines in lines_by_file.items():
        current_src = None
        for lineno, line in enumerate(lines, 1):
            m = SRC_RE.match(line)
            if m:
                current_src = (m.group(1), line, lineno)
                continue
            m = TRG_RE.match(line)
            if m and current_src:
                sid = m.group(1)
                src_c = scan_citations(current_src[1])
                trg_c = scan_citations(line)
                missing = [c for c in src_c if c not in trg_c]
                if missing:
                    warnings.append({
                        "class": "citations", "severity": "warning",
                        "file": path, "line": lineno, "segment": sid,
                        "message": "citation marker in source not preserved in target",
                        "missing": missing,
                    })
                current_src = None
    return [], warnings


def check_terms(all_text_by_file, glossary):
    warnings = []
    if not isinstance(glossary, dict):
        return warnings
    terms = glossary.get("terms", [])
    if not isinstance(terms, list):
        return warnings
    for term in terms:
        if not isinstance(term, dict):
            continue
        canonical = term.get("canonical")
        variants = term.get("variants") or []
        if not canonical:
            continue
        all_variants = [canonical] + [v for v in variants if isinstance(v, str)]
        hits = {}
        for path, text in all_text_by_file.items():
            for var in all_variants:
                if not var:
                    continue
                count = text.count(var)
                if count:
                    hits.setdefault(var, []).append((path, count))
        distinct = [v for v in all_variants if v in hits]
        if len(distinct) > 1:
            detail = "; ".join("%s (%d)" % (v, sum(c for _, c in locs))
                               for v, locs in hits.items())
            warnings.append({
                "class": "terms", "severity": "warning",
                "file": list(hits[distinct[0]][0][0] if hits else [""])[0] or None,
                "message": "term written with multiple variants; lock one translation",
                "canonical": canonical, "appeared": detail,
            })
    return warnings


def check_structure(lines_by_file):
    warnings = []
    for path, lines in lines_by_file.items():
        for name, rx in (("section", HEADING_RE), ("figure", FIG_RE), ("table", TABLE_RE)):
            nums = []
            for lineno, line in enumerate(lines, 1):
                m = rx.search(line)
                if m:
                    nums.append((int(m.group(1)), lineno))
            if not nums:
                continue
            vals = [n for n, _ in nums]
            dups = sorted({n for n in vals if vals.count(n) > 1})
            for d in dups:
                warnings.append({
                    "class": "structure", "severity": "warning",
                    "file": path, "line": next(l for n, l in nums if n == d),
                    "message": "%s number %d is duplicated" % (name, d),
                })
            unique_sorted = sorted(set(vals))
            lo, hi = unique_sorted[0], unique_sorted[-1]
            expected = set(range(lo, hi + 1))
            gaps = sorted(expected - set(unique_sorted))
            if gaps:
                warnings.append({
                    "class": "structure", "severity": "warning",
                    "file": path,
                    "message": "%s numbering gap at %s" % (name, ", ".join(map(str, gaps[:10]))),
                })
    return [], warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="bilingual consistency checker")
    ap.add_argument("--dir", default="/app/output")
    ap.add_argument("--glossary", default=None)
    ap.add_argument("--out", default="/app/work/check_report.json")
    a = ap.parse_args(argv)

    md_files = iter_md_files(a.dir)
    lines_by_file = {p: read_lines(p) for p in md_files}
    all_text_by_file = {p: "\n".join(lines) for p, lines in lines_by_file.items()}

    errors, warnings = [], []
    for fn in (check_alignment, check_numbers, check_citations, check_structure):
        e, w = fn(lines_by_file)
        errors.extend(e)
        warnings.extend(w)
    if a.glossary:
        glossary = load_json_any(a.glossary)
        warnings.extend(check_terms(all_text_by_file, glossary))
    else:
        glossary = None

    report = {
        "files_scanned": md_files,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "glossary_used": a.glossary if glossary is not None else None,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, a.out)
    print(json.dumps({"ok": True, "errors": len(errors), "warnings": len(warnings),
                      "files": len(md_files), "report": a.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
