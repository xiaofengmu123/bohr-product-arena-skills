#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover and normalize the runtime input for the research-writing-transform skill.

Reads /input (or --input), tolerates BOM / bad encodings / bad JSON, and writes
a deterministic manifest plus a facts inventory to /app/work (or --work).

Exit codes:
  0  success (even when no materials are found; manifest records the gap)
  2  bad arguments
 10  /input does not exist or is unreadable
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip larger material files
TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".xml", ".html", ".tex", ".bib"}
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".xlsx", ".odt"}
PDF_EXT = ".pdf"


def log(msg):
    print("[input_scan] %s" % msg, file=sys.stderr)


def read_text(path):
    """Read a text file tolerating BOM and common encoding failures."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        # tolerate BOM / trailing garbage by trying a subset parse
        raise ValueError("invalid JSON: %s" % e)


def find_case_and_task(root):
    """Locate case.json and task.md without assuming fixed names."""
    case_path = None
    task_path = None
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            lower = fn.lower()
            if lower == "case.json" and case_path is None:
                case_path = os.path.join(dirpath, fn)
            elif lower in ("task.md", "task.txt") and task_path is None:
                task_path = os.path.join(dirpath, fn)
    return case_path, task_path


def list_materials(root):
    """Enumerate material files, skipping oversized files and recording skips."""
    out = []
    skipped = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            lower = fn.lower()
            if lower in ("case.json", "task.md", "task.txt", "result.json"):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": p, "size": size, "reason": "oversize"})
                continue
            ext = os.path.splitext(lower)[1]
            out.append({
                "path": p,
                "name": fn,
                "size": size,
                "ext": ext or None,
                "kind": "pdf" if ext == PDF_EXT else
                        ("office" if ext in OFFICE_EXTS else
                         ("text" if ext in TEXT_EXTS else "other")),
            })
    return out, skipped


def extract_pdf_text(path, work_dir):
    """Extract text from a PDF via pdftotext; degrade gracefully if unavailable."""
    out_path = os.path.join(work_dir, "text",
                            os.path.basename(path) + ".txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        try:
            return read_text(out_path)
        except OSError:
            pass
    for exe in ("pdftotext", "pdftotext.exe"):
        try:
            cp = subprocess.run([exe, "-layout", path, out_path],
                                capture_output=True, text=True, timeout=120)
            if cp.returncode == 0 and os.path.exists(out_path):
                return read_text(out_path)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def is_probably_json(text):
    s = text.lstrip()
    return s.startswith("{") or s.startswith("[")


def parse_case(case_path):
    if not case_path:
        return {}
    text = read_text(case_path)
    if not is_probably_json(text):
        return {"_raw_note": "case.json not valid JSON; raw text captured separately"}
    try:
        return load_json(text)
    except ValueError as e:
        return {"_parse_error": str(e)}


def parse_task(task_path):
    if not task_path:
        return ""
    try:
        return read_text(task_path)
    except OSError as e:
        return "(unreadable: %s)" % e


def pick_case_id(case):
    for key in ("case_id", "id", "caseId", "challenge_id", "challengeId", "armiId"):
        v = case.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def extract_constraints(case, task_text):
    """Heuristically pull audience / genre / format / length constraints."""
    blob = task_text
    if isinstance(case, dict):
        content = case.get("content") or case.get("task") or case.get("description")
        if isinstance(content, str):
            blob = blob + "\n" + content
    constraints = {}
    m = re.search(r"(受众|读者)[：:]\s*([^\n。；;]{1,80})", blob)
    if m:
        constraints["audience_hint"] = m.group(2).strip()
    m = re.search(r"(体裁|文体)[：:]\s*([^\n。；;]{1,80})", blob)
    if m:
        constraints["genre_hint"] = m.group(2).strip()
    m = re.search(r"(格式)[：:]\s*([^\n。；;]{1,40})", blob)
    if m:
        constraints["format_hint"] = m.group(2).strip()
    m = re.search(r"(字数|篇幅)[：:]?\s*([0-9０-９]{1,6})\s*字?", blob)
    if m:
        digits = "".join(ch for ch in m.group(2) if ch.isdigit())
        if digits:
            constraints["word_limit"] = int(digits)
    for kw in ("读者", "audience", "体裁", "genre", "格式", "format", "文体"):
        if kw in blob:
            constraints.setdefault("mentions", []).append(kw)
    return constraints


NUMBER_RE = re.compile(
    r"(?<![\w])"
    r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?|(?:\d+\.\d+|\d+))"
    r"(?:\s*(?:[a-zA-Z%°µμ]|km|kg|mg|ng|pg|s|ms|min|h|nm|µm|μm|mm|cm|m|mL|mL|L|g|kDa|Da|Hz|kHz|MHz|GHz|N|Pa|℃|°C))?"
)
CITE_RE = re.compile(r"\[[0-9]{1,3}(?:[,\-\s][0-9]{1,3})*\]|\(\s*(?:19|20)\d{2}[a-z]?\s*\)")
FIGREF_RE = re.compile(r"(?:图|表|Fig\.?|Table)\s*[0-9IVX]{1,4}(?:\.[0-9]{1,2})?", re.IGNORECASE)
ABBREV_RE = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\b")
CLAIM_RE = re.compile(r"(显著|显著相关|表明|证实|可能|提示|初步|相关|因果|导致|因此|从而)")


def build_facts_inventory(text, source_label):
    """Extract numbers, citations, figure refs, abbrevations, claim cues."""
    inv = {"source": source_label, "numbers": [], "citations": [],
           "figure_refs": [], "abbreviations": [], "claim_cues": []}
    seen = set()
    for m in NUMBER_RE.finditer(text):
        tok = m.group(0).strip()
        s = max(0, m.start() - 40)
        e = min(len(text), m.end() + 40)
        ctx = text[s:e].replace("\n", " ")
        if tok not in seen:
            seen.add(tok)
            inv["numbers"].append({"value": tok, "context": ctx})
    for m in CITE_RE.finditer(text):
        inv["citations"].append(m.group(0))
    for m in FIGREF_RE.finditer(text):
        inv["figure_refs"].append(m.group(0))
    for m in ABBREV_RE.finditer(text):
        tok = m.group(0)
        if 2 <= len(tok) <= 12:
            inv["abbreviations"].append(tok)
    for m in CLAIM_RE.finditer(text):
        inv["claim_cues"].append(m.group(0))
    # de-duplicate preserving order
    for k in ("citations", "figure_refs", "abbreviations", "claim_cues"):
        inv[k] = list(dict.fromkeys(inv[k]))
    return inv


def main():
    ap = argparse.ArgumentParser(description="Scan runtime input for writing skill")
    ap.add_argument("--input", default="/input", help="input dir (default /input)")
    ap.add_argument("--work", default="/app/work", help="work dir (default /app/work)")
    args = ap.parse_args()

    root = args.input
    if not os.path.isdir(root):
        log("input dir not found: %s" % root)
        # still write a minimal manifest so downstream can produce insufficient_evidence
        manifest = {"input_root": root, "exists": False, "error": "input_dir_missing"}
        os.makedirs(args.work, exist_ok=True)
        with open(os.path.join(args.work, "input_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0

    case_path, task_path = find_case_and_task(root)
    case = parse_case(case_path) if case_path else {}
    task_text = parse_task(task_path) if task_path else ""
    materials, skipped = list_materials(root)

    # extract text from text-like materials; PDFs via pdftotext
    sources_text = {}
    facts = []
    for mat in materials:
        p = mat["path"]
        if mat["kind"] == "pdf":
            txt = extract_pdf_text(p, args.work)
            if txt is not None:
                sources_text[p] = txt
        elif mat["kind"] == "text":
            try:
                sources_text[p] = read_text(p)
            except OSError:
                continue
        # office files are listed but not text-extracted by this script
    for p, txt in sources_text.items():
        facts.append(build_facts_inventory(txt, p))

    case_id = pick_case_id(case) or (os.path.basename(root) or "unknown")
    manifest = {
        "input_root": root,
        "exists": True,
        "case_id": case_id,
        "case_path": case_path,
        "task_path": task_path,
        "case": case if isinstance(case, dict) else {"_raw": str(case)[:2000]},
        "task_text": task_text,
        "constraints": extract_constraints(case, task_text),
        "materials": materials,
        "skipped": skipped,
        "facts_inventory": facts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(args.work, exist_ok=True)
    manifest_path = os.path.join(args.work, "input_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    facts_path = os.path.join(args.work, "facts_inventory.json")
    with open(facts_path, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "case_id": case_id,
        "manifest": manifest_path,
        "facts_inventory": facts_path,
        "n_materials": len(materials),
        "n_skipped": len(skipped),
        "n_text_sources": len(sources_text),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
