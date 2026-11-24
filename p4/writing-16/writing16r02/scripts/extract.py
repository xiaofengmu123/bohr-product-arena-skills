#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic fact/citation/term extractor for source materials.

Standard library only. Produces a raw evidence ledger (evidence_map.json) that
the writing agent uses to keep numbers, units, citations and figure references
verbatim, and to mark every claim with a source locator + quote. The extractor
is intentionally conservative: it records candidates, never interprets them.

Usage:
    python3 scripts/extract.py --input /input --work /app/work [--files a.md b.pdf.txt]
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from main import read_text, safe_json, scan_input, TEXT_EXTS, MATERIAL_EXTS
except Exception:  # pragma: no cover - fallback keeps the script runnable
    TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".tex", ".xml",
                 ".yaml", ".yml", ".rst", ".bib"}
    MATERIAL_EXTS = TEXT_EXTS | {".pdf", ".docx", ".pptx", ".xlsx", ".html"}

    def read_text(path, cap=2 * 1024 * 1024):
        p = Path(path)
        try:
            raw = p.read_bytes()
        except OSError as e:
            return "", f"read_failed: {e}"
        if len(raw) > cap:
            raw = raw[:cap]
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc), None
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), "lossy_decode"


# ---- patterns -------------------------------------------------------------
NUM_WITH_UNIT = re.compile(
    r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|percent|per\s*cent|ppm|ppb|nM|uM|mM|M|mg|ug|ug/ml|mg/ml|g|kg|mL|ml|L|"
    r"mm|cm|nm|um|m|km|kHz|MHz|Hz|s|min|h|hr|day|days|week|weeks|month|year|yr|"
    r"°C|deg\s*C|K|CI|OR|RR|HR|SD|SE|SEM|IQR|AUC|P\s*=|p\s*=|n\s*=|N\s*=)"
    r"(?![A-Za-z])"
)

CITATION_BRACKET = re.compile(r"\[(?:\d{1,3}(?:\s*[,，\-–]\s*\d{1,3})*)\]")
CITATION_AUTHOR = re.compile(
    r"(?:[A-Z][A-Za-z'’-]+(?:\s+(?:et\s+al\.?|and|[A-Z][A-Za-z'’-]+))?,?\s+"
    r"(?:19|20)\d{2}[a-z]?)"
)
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
ARXIV = re.compile(r"arXiv:\d{4}\.\d{4,5}(?:v\d+)?", re.IGNORECASE)

FIGREF = re.compile(r"(?:Fig(?:ure)?\.?|图)\s*\d+", re.IGNORECASE)
TABREF = re.compile(r"(?:Table|表)\s*\d+", re.IGNORECASE)

ABBREV = re.compile(
    r"\b([A-Z][A-Za-z0-9-]{1,11})\s*\(([^()\n]{1,80})\)"
)


def line_locator(path, lineno, total):
    return "{}#L{}".format(path, lineno)


def scan_text(rel_path, text):
    """Return a list of evidence entries for one source text."""
    entries = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for m in NUM_WITH_UNIT.finditer(line):
            entries.append({
                "type": "number_with_unit",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in CITATION_BRACKET.finditer(line):
            entries.append({
                "type": "citation_bracket",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in CITATION_AUTHOR.finditer(line):
            entries.append({
                "type": "citation_author_year",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in DOI.finditer(line):
            entries.append({
                "type": "doi",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in ARXIV.finditer(line):
            entries.append({
                "type": "arxiv_id",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in FIGREF.finditer(line):
            entries.append({
                "type": "figure_reference",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in TABREF.finditer(line):
            entries.append({
                "type": "table_reference",
                "value": m.group(0).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
        for m in ABBREV.finditer(line):
            entries.append({
                "type": "abbreviation_candidate",
                "value": m.group(1).strip(),
                "expansion": m.group(2).strip(),
                "quote": line.strip(),
                "locator": line_locator(rel_path, i, len(lines)),
            })
    return entries


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract a raw evidence ledger.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--files", nargs="*", default=None,
                    help="optional explicit source files; otherwise auto-scan")
    args = ap.parse_args(argv)

    Path(args.work).mkdir(parents=True, exist_ok=True)

    if args.files:
        targets = [(Path(f), str(f), None) for f in args.files]
    else:
        root, case_files, task_files, materials = scan_input(args.input)
        if root is None:
            materials = []
        targets = []
        for p, rel, size in materials:
            if p.suffix.lower() in TEXT_EXTS:
                targets.append((p, rel, size))

    ledger, degraded = [], []
    for p, rel, size in targets:
        if p.suffix.lower() not in TEXT_EXTS:
            # Non-text (PDF/docx): the agent parses these with dedicated tools;
            # record them so the pipeline can later attach their parsed text.
            ledger.append({"type": "opaque_material",
                           "path": rel, "note": "non-text; parse before extraction"})
            continue
        text, deg = read_text(p)
        if deg:
            degraded.append("{}: {}".format(rel, deg))
        ledger.extend(scan_text(rel, text))

    by_type = {}
    for e in ledger:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    out = {
        "entries": ledger,
        "counts_by_type": by_type,
        "degraded_files": degraded,
        "note": "raw candidate ledger; every value must be re-checked against "
                "its locator before it is used in the deliverable",
    }
    out_path = Path(args.work) / "evidence_map.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("wrote:", out_path)
    print("entries:", len(ledger), "| types:", json.dumps(by_type, ensure_ascii=False))
    if degraded:
        print("degraded files:", degraded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
