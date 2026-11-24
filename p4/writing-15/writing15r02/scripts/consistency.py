#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic consistency checks for long academic documents.

Checks (all heuristic, deterministic, standard-library only):
  1. terminology  — given a term list, detect non-canonical variants.
  2. numbering    — figure/table/section caption numbering continuity.
  3. citations    — bracket citations vs. detected bibliography entries.
  4. crossrefs    — "如图/见表/见第X章" references vs. detected captions.

Standalone usage:
  python3 -B scripts/consistency.py --text draft.md \
      [--termlist termlist.json] [--bibliography bibliography.json] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# caption-like lines: optional leading whitespace, label + number + separator + text
CAPTION_RE = re.compile(
    r"^\s*(?:图|Figure|Fig\.?|表|Table)\s*(\d+)\s*[:：.、\s　]\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# cross-reference mentions
XREF_RE = re.compile(
    r"(?:如|见|参见|see\s+)?(?:图|Figure|Fig\.?|表|Table)\s*[（(]?(\d+)[）)]?",
    re.IGNORECASE,
)
SECTION_CAPTION_RE = re.compile(
    r"^\s*(?:第\s*(\d+)\s*章|Chapter\s+(\d+)|(\d+(?:\.\d+)*)\s+[^\d].{0,80})$",
    re.MULTILINE,
)
SECTION_XREF_RE = re.compile(r"(?:第\s*(\d+)\s*章|见\s*第\s*(\d+)\s*章|Chapter\s+(\d+))", re.IGNORECASE)
CITE_RE = re.compile(r"\[(\d+(?:\s*[,，\-–]\s*\d+)*)\]")
BIB_ENTRY_RE = re.compile(r"^\s*\[?(\d+)\]?[\s\.、:\-]+", re.MULTILINE)


def _read_json(path):
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _read_text(path):
    p = Path(path)
    if not p.exists():
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, ValueError, OSError):
            continue
    return p.read_text(encoding="utf-8", errors="replace")


def _expand_cite_numbers(marker_text):
    """'1,2,4-6' -> [1,2,4,5,6]; returns [] on malformed input."""
    out = []
    for part in re.split(r"[,，]", marker_text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b and b - a <= 200:
                out.extend(range(a, b + 1))
            continue
        if part.isdigit():
            out.append(int(part))
    return out


def check_terminology(text, termlist):
    """termlist: list of {canonical, variants:[...]} or {term, variants:[...]}."""
    findings = []
    if not termlist:
        return findings
    if isinstance(termlist, dict):
        termlist = termlist.get("terms", []) if isinstance(termlist.get("terms"), list) else []
    for entry in termlist:
        if not isinstance(entry, dict):
            continue
        canonical = entry.get("canonical") or entry.get("term")
        if not canonical:
            continue
        variants = entry.get("variants") or []
        for var in variants:
            if not var or var == canonical:
                continue
            if re.search(re.escape(var), text):
                findings.append({
                    "kind": "terminology",
                    "canonical": canonical,
                    "found": var,
                    "advice": f"统一为 {canonical}",
                })
    return findings


def check_numbering(text):
    findings = []
    captions = {}
    for m in CAPTION_RE.finditer(text):
        num = int(m.group(1))
        label = (m.group(0).split(maxsplit=1)[0] if m.group(0).split() else "").strip()
        captions.setdefault(num, 0)
        captions[num] += 1
    nums = sorted(captions)
    if nums:
        if nums[0] != 1:
            findings.append({"kind": "numbering", "issue": "first_caption_not_1", "detail": nums[0]})
        for expected in range(1, max(nums) + 1):
            if expected not in captions:
                findings.append({"kind": "numbering", "issue": "caption_gap", "missing": expected})
        for num, count in captions.items():
            if count > 1:
                findings.append({"kind": "numbering", "issue": "caption_duplicate", "number": num, "count": count})
    # sections
    sec_nums = []
    for m in SECTION_CAPTION_RE.finditer(text):
        val = next((g for g in m.groups() if g is not None), None)
        if val:
            sec_nums.append(val)
    if sec_nums:
        top = [int(s.split(".")[0]) for s in sec_nums if s.split(".")[0].isdigit()]
        if top:
            uniq = sorted(set(top))
            if uniq[0] != 1:
                findings.append({"kind": "numbering", "scope": "section", "issue": "first_not_1", "detail": uniq[0]})
            for expected in range(1, max(uniq) + 1):
                if expected not in uniq:
                    findings.append({"kind": "numbering", "scope": "section", "issue": "gap", "missing": expected})
    return findings


def check_citations(text, bibliography=None):
    findings = []
    bib = set()
    if bibliography:
        if isinstance(bibliography, list):
            bib = {int(e.get("number")) for e in bibliography if isinstance(e, dict) and str(e.get("number", "")).isdigit()}
        elif isinstance(bibliography, str):
            bib = {int(m.group(1)) for m in BIB_ENTRY_RE.finditer(bibliography)}
    else:
        bib = {int(m.group(1)) for m in BIB_ENTRY_RE.finditer(text)}
    cited = set()
    for m in CITE_RE.finditer(text):
        cited.update(_expand_cite_numbers(m.group(1)))
    if not cited and not bib:
        findings.append({"kind": "citation", "issue": "none_detected"})
        return findings
    unresolved = sorted(cited - bib)
    if unresolved:
        findings.append({"kind": "citation", "issue": "cited_but_undefined", "numbers": unresolved})
    unused = sorted(bib - cited)
    if len(unused) > 0:
        findings.append({"kind": "citation", "issue": "defined_but_uncited", "numbers": unused})
    return findings


def check_crossrefs(text):
    findings = []
    caption_nums = {int(m.group(1)) for m in CAPTION_RE.finditer(text)}
    ref_nums = [int(m.group(1)) for m in XREF_RE.finditer(text)]
    unresolved = sorted({n for n in ref_nums if n not in caption_nums})
    if unresolved:
        findings.append({"kind": "crossref", "issue": "target_not_found", "numbers": unresolved})
    # section cross refs
    sec_captions = set()
    for m in SECTION_CAPTION_RE.finditer(text):
        val = next((g for g in m.groups() if g is not None), None)
        if val and val.isdigit():
            sec_captions.add(int(val))
    sec_refs = []
    for m in SECTION_XREF_RE.finditer(text):
        val = next((g for g in m.groups() if g is not None), None)
        if val and val.isdigit():
            sec_refs.append(int(val))
    sec_missing = sorted({n for n in sec_refs if n not in sec_captions})
    if sec_missing:
        findings.append({"kind": "crossref", "scope": "section", "issue": "target_not_found", "numbers": sec_missing})
    return findings


def run_checks(text, termlist=None, bibliography=None):
    findings = {
        "terminology": check_terminology(text, termlist),
        "numbering": check_numbering(text),
        "citation": check_citations(text, bibliography),
        "crossref": check_crossrefs(text),
    }
    total = sum(len(v) for v in findings.values())
    critical = len(findings["citation"]) + len(findings["crossref"])
    return {"findings": findings, "total_count": total, "critical_count": critical}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic consistency checks for long documents.")
    ap.add_argument("--text", help="draft file (Markdown/TeX/plain)")
    ap.add_argument("--termlist", help="JSON term list file")
    ap.add_argument("--bibliography", help="JSON bibliography file or plain reference block")
    ap.add_argument("--out", help="write JSON report to this path")
    args = ap.parse_args(argv)

    text = _read_text(args.text)
    if text is None:
        print(json.dumps({"error": f"cannot read text: {args.text}"}, ensure_ascii=False))
        return 1

    termlist = _read_json(args.termlist) if args.termlist else None
    bibliography = _read_json(args.bibliography) if args.bibliography else None
    if args.bibliography and bibliography is None:
        bibliography = _read_text(args.bibliography)  # plain reference block fallback

    report = run_checks(text, termlist=termlist, bibliography=bibliography)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
