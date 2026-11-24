#!/usr/bin/env python3
"""Fact-traceability consistency checks for academic drafts.

Checks (heuristics are intentionally conservative and documented):
  1. dangling [S:...] source locators (not in material index)
  2. dangling [C:...] citations (used but not defined; defined but unused)
  3. figure / table numbering (referenced vs captioned)
  4. required section coverage
  5. unresolved markers inventory ([缺失]/[待确认]/[冲突待确认]/[无法解析]/[未报告])

Pure stdlib. Deterministic. Exit 0 by default; --strict exits 1 on findings.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MARKER_KINDS = {
    "[缺失]": "missing",
    "[待确认]": "to_confirm",
    "[冲突待确认]": "conflict",
    "[无法解析]": "unparseable",
    "[未报告]": "not_reported",
}

RE_SOURCE = re.compile(r"\[S:([^\]]+)\]")
RE_CITE = re.compile(r"\[C:([^\]]+)\]")
RE_FIG_REF = re.compile(r"(?:Fig(?:ure)?\.?|图)\s*(\d+)", re.IGNORECASE)
RE_TAB_REF = re.compile(r"(?:Table|表)\s*(\d+)", re.IGNORECASE)
RE_FIG_CAP = re.compile(r"^\s*(?:图|Fig(?:ure)?\.?)\s*(\d+)\s*[.:：、]", re.IGNORECASE)
RE_TAB_CAP = re.compile(r"^\s*(?:表|Table)\s*(\d+)\s*[.:：、]", re.IGNORECASE)
RE_CITE_DEF = re.compile(r"^\s*(?:[-*]\s*)?\[C:([^\]]+)\]\s+\S")
RE_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")

SECTION_PATTERNS = {
    "abstract": re.compile(r"^\s*#+\s*(摘要|abstract)", re.IGNORECASE | re.MULTILINE),
    "introduction": re.compile(r"^\s*#+\s*(引言|introduction)", re.IGNORECASE | re.MULTILINE),
    "methods": re.compile(r"^\s*#+\s*(方法|methods|materials and methods)", re.IGNORECASE | re.MULTILINE),
    "results": re.compile(r"^\s*#+\s*(结果|results)", re.IGNORECASE | re.MULTILINE),
    "discussion": re.compile(r"^\s*#+\s*(讨论|discussion)", re.IGNORECASE | re.MULTILINE),
    "conclusion": re.compile(r"^\s*#+\s*(结论|conclusion)", re.IGNORECASE | re.MULTILINE),
    "references": re.compile(r"^\s*#+\s*(参考文献|references|bibliography)", re.IGNORECASE | re.MULTILINE),
    "data_availability": re.compile(r"^\s*#+\s*(数据可得性|数据可用|data availability)", re.IGNORECASE | re.MULTILINE),
}


def load_json(path: Path, default=None):
    try:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception:
        return default


def read_text(path: Path):
    try:
        return path.read_bytes().decode("utf-8-sig", errors="replace")
    except Exception:
        return ""


def unique(items):
    return sorted(set(items))


def extract(draft_text: str):
    sources = RE_SOURCE.findall(draft_text)
    cites = RE_CITE.findall(draft_text)
    fig_refs = set()
    for m in RE_FIG_REF.findall(draft_text):
        fig_refs.add(int(m))
    tab_refs = set()
    for m in RE_TAB_REF.findall(draft_text):
        tab_refs.add(int(m))
    fig_caps = set()
    tab_caps = set()
    for line in draft_text.splitlines():
        m = RE_FIG_CAP.match(line)
        if m:
            fig_caps.add(int(m.group(1)))
        m = RE_TAB_CAP.match(line)
        if m:
            tab_caps.add(int(m.group(1)))
    # markdown images contribute a figure caption even if unnumbered
    md_imgs = RE_MD_IMG.findall(draft_text)
    cite_defs = set()
    for line in draft_text.splitlines():
        m = RE_CITE_DEF.match(line)
        if m:
            cite_defs.add(m.group(1))
    markers = {}
    for token, kind in MARKER_KINDS.items():
        found = draft_text.count(token)
        if found:
            markers[kind] = found
    return {
        "sources": unique(sources),
        "cites": unique(cites),
        "cite_defs": unique(cite_defs),
        "fig_refs": sorted(fig_refs),
        "tab_refs": sorted(tab_refs),
        "fig_caps": sorted(fig_caps),
        "tab_caps": sorted(tab_caps),
        "md_images": len(md_imgs),
        "markers": markers,
    }


def check_sources(extracted, index):
    findings = []
    known = set()
    if index and isinstance(index, dict):
        for m in index.get("materials", []) or []:
            known.add(m.get("ref_id"))
        for lit in index.get("literature", []) or []:
            known.add(lit.get("cite_key"))
    for s in extracted["sources"]:
        if s not in known:
            findings.append({"check": "dangling_source", "key": s,
                             "message": f"[S:{s}] not in material index"})
    return findings


def check_citations(extracted, index):
    findings = []
    lit_keys = set()
    if index and isinstance(index, dict):
        for lit in index.get("literature", []) or []:
            k = lit.get("cite_key")
            if k:
                lit_keys.add(k)
    defined = set(extracted["cite_defs"]) | lit_keys
    used = set(extracted["cites"])
    for c in sorted(used - defined):
        findings.append({"check": "undefined_citation", "key": c,
                         "message": f"[C:{c}] used but not defined"})
    for c in sorted(defined - used):
        findings.append({"check": "unused_citation", "key": c,
                         "message": f"[C:{c}] defined but never used (suspicious)",
                         "severity": "warn"})
    return findings


def check_numbering(extracted):
    findings = []
    fig_orphan_refs = sorted(set(extracted["fig_refs"]) - set(extracted["fig_caps"]))
    fig_orphan_caps = sorted(set(extracted["fig_caps"]) - set(extracted["fig_refs"]))
    tab_orphan_refs = sorted(set(extracted["tab_refs"]) - set(extracted["tab_caps"]))
    tab_orphan_caps = sorted(set(extracted["tab_caps"]) - set(extracted["tab_refs"]))
    for n in fig_orphan_refs:
        findings.append({"check": "figure_ref_without_caption", "n": n,
                         "message": f"Figure/图 {n} referenced but no caption found"})
    for n in tab_orphan_refs:
        findings.append({"check": "table_ref_without_caption", "n": n,
                         "message": f"Table/表 {n} referenced but no caption found"})
    for n in fig_orphan_caps:
        findings.append({"check": "figure_caption_without_ref", "n": n,
                         "message": f"Figure/图 {n} captioned but never referenced",
                         "severity": "warn"})
    for n in tab_orphan_caps:
        findings.append({"check": "table_caption_without_ref", "n": n,
                         "message": f"Table/表 {n} captioned but never referenced",
                         "severity": "warn"})
    return findings


def check_sections(draft_text, required):
    findings = []
    missing = []
    for sec, pat in SECTION_PATTERNS.items():
        if required is not None and sec not in required:
            continue
        if not pat.search(draft_text):
            missing.append(sec)
    if missing:
        findings.append({"check": "missing_sections", "sections": missing,
                         "message": "required sections absent: " + ", ".join(missing)})
    return findings


def check_evidence(evidence, index):
    findings = []
    known = set()
    if index and isinstance(index, dict):
        for m in index.get("materials", []) or []:
            known.add(m.get("ref_id"))
    for e in evidence or []:
        eid = e.get("id", "?")
        ref = e.get("ref_id")
        if ref and ref not in known:
            findings.append({"check": "evidence_bad_ref", "id": eid, "ref": ref,
                             "message": f"evidence {eid} ref_id {ref} not in index"})
        if not e.get("quote"):
            findings.append({"check": "evidence_no_quote", "id": eid,
                             "message": f"evidence {eid} missing verbatim quote",
                             "severity": "warn"})
        if not e.get("locator"):
            findings.append({"check": "evidence_no_locator", "id": eid,
                             "message": f"evidence {eid} missing locator",
                             "severity": "warn"})
    return findings


def run_all(draft: Path, index: Path, evidence: Path, required=None):
    draft_text = read_text(draft) if draft and Path(draft).is_file() else ""
    index_data = load_json(index) if index and Path(index).is_file() else None
    evidence_data = load_json(evidence) if evidence and Path(evidence).is_file() else None
    entries = (evidence_data or {}).get("entries", []) if isinstance(evidence_data, dict) else []

    extracted = extract(draft_text)
    findings = []
    findings += check_sources(extracted, index_data)
    findings += check_citations(extracted, index_data)
    findings += check_numbering(extracted)
    findings += check_sections(draft_text, required)
    findings += check_evidence(entries, index_data)

    hard = [f for f in findings if f.get("severity") != "warn"]
    report = {
        "draft_found": bool(draft_text),
        "markers": extracted["markers"],
        "sources_used": extracted["sources"],
        "citations_used": extracted["cites"],
        "citations_defined": extracted["cite_defs"],
        "figures": {"refs": extracted["fig_refs"],
                    "captions": extracted["fig_caps"],
                    "md_images": extracted["md_images"]},
        "tables": {"refs": extracted["tab_refs"],
                   "captions": extracted["tab_caps"]},
        "findings": findings,
        "hard_findings": hard,
        "pass": len(hard) == 0,
        "warnings": [f for f in findings if f.get("severity") == "warn"],
    }
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Consistency checks for drafts")
    ap.add_argument("--draft", type=Path, required=True)
    ap.add_argument("--index", type=Path, default=None)
    ap.add_argument("--evidence", type=Path, default=None)
    ap.add_argument("--contract", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    required = None
    if args.contract and args.contract.is_file():
        contract = load_json(args.contract, default={}) or {}
        if isinstance(contract.get("required_sections"), list):
            required = set(contract["required_sections"])

    report = run_all(args.draft, args.index, args.evidence, required)
    if args.out:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                           sort_keys=True), encoding="utf-8")
        except Exception:
            pass
    print(json.dumps({"pass": report["pass"],
                      "hard_findings": len(report["hard_findings"]),
                      "warnings": len(report["warnings"]),
                      "markers": report["markers"]}, ensure_ascii=False))
    if args.strict and report["hard_findings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
