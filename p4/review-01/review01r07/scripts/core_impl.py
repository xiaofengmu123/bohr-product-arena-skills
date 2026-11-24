#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — shared, dependency-free helpers for the paper-quality-review skill.

Everything here uses only the Python standard library. The module is intentionally
defensive: every public function returns a usable value (or `(None, problem)`)
instead of raising, so the CLI can always degrade to a legal partial result.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree

# --------------------------------------------------------------------------
# Constants: review facets, verdicts and result-envelope schema.
# --------------------------------------------------------------------------
FACETS = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistics_report",
    "argument_chain",
    "academic_norms",
]
SEVERITIES = ["critical", "major", "minor", "suggestion"]
ISSUE_TYPES = [
    "factual_error",
    "insufficient_evidence",
    "standards_issue",
    "suggestion",
    "source_conflict",
]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = [
    "pass",
    "revision_required",
    "to_review",
    "insufficient_evidence",
    "input_error",
]
ENVELOPE_KEYS = [
    "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "changes", "change_log", "runtime",
]
FINDING_FIELDS = [
    "finding_id", "facet", "severity", "issue_type", "verdict",
    "claim", "location", "quote", "rationale", "revision_suggestion",
    "confidence", "evidence",
]
MAX_TEXT_FILE = 2 * 1024 * 1024  # 2 MB: skip-and-record above this limit

TEXT_KINDS = {
    ".txt", ".md", ".markdown", ".rst", ".json", ".jsonl", ".csv",
    ".tsv", ".xml", ".html", ".htm", ".tex", ".bib", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".toml",
}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp",
}

_W_DOC = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --------------------------------------------------------------------------
# Text reading with an encoding fallback chain (robustness against BOM and
# mislabelled encodings).
# --------------------------------------------------------------------------
def read_text(path):
    """Return `(text_or_None, problem_or_None)`.

    Tries, in order: utf-8-sig, utf-8, gb18030, latin-1. latin-1 never fails,
    so a text value is returned whenever the file can be opened at all.
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > MAX_TEXT_FILE:
        return None, f"file too large ({len(data)} bytes > {MAX_TEXT_FILE})"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", "replace"), "decoded with latin-1 fallback"


def _json_serializable(obj):
    """Make a best-effort JSON-serializable value (guards against exotic types)."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_serializable(v) for v in obj]
    return str(obj)


def load_json(path):
    """Return the parsed object, or None on any failure. Never raises."""
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def atomic_write_json(path, obj):
    """Write JSON atomically (tmp file + os.replace); falls back to direct write."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    payload = json.dumps(_json_serializable(obj), ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        os.replace(tmp, path)
        return str(path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
            return str(path)
        except OSError as exc:
            return f"write failed: {exc}"


def atomic_write_text(path, text):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return str(path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            return str(path)
        except OSError as exc:
            return f"write failed: {exc}"


# --------------------------------------------------------------------------
# File discovery and kind classification.
# --------------------------------------------------------------------------
def classify_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in PDF_KINDS:
        return "pdf"
    if ext in DOCX_KINDS:
        return "docx"
    if ext in IMAGE_KINDS:
        return "image"
    if ext in TEXT_KINDS:
        return "text"
    return "other"


def material_files(input_dir: Path):
    """Discover material files under input_dir, excluding the case/task files."""
    if not input_dir.exists():
        return []
    skip = {"case.json", "task.md", "resources.yaml", "environment.yaml"}
    hidden_part = re.compile(r"(^|[\\/])\.")
    items = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name in skip:
            continue
        rel = p.relative_to(input_dir)
        if hidden_part.search(str(rel)):
            continue
        items.append(p)
    return items


# --------------------------------------------------------------------------
# Paragraph splitting and locators.
# --------------------------------------------------------------------------
def split_paragraphs(text, page=None):
    """Split text into non-empty paragraphs, preserving a stable 1-based index."""
    paras = []
    chunks = re.split(r"\n[ \t]*\n+", text or "")
    idx = 0
    for chunk in chunks:
        chunk = re.sub(r"[ \t]+", " ", chunk).strip()
        if not chunk:
            continue
        idx += 1
        paras.append({"index": idx, "page": page, "text": chunk})
    return paras


def make_locator(page, index):
    if page:
        return f"p{page}#para{index}"
    return f"para{index}"


def normalize_ws(text):
    return re.sub(r"\s+", " ", text or "").strip()


# --------------------------------------------------------------------------
# Minimal, stdlib-only PDF text extraction.
# --------------------------------------------------------------------------
def _pdf_unescape(s):
    s = s.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    s = re.sub(r"\\[0-7]{1,3}", lambda m: chr(int(m.group(0)[1:], 8) & 0xFF), s)
    return s


def _text_from_content(s):
    """Extract text fragments from a decoded PDF content stream."""
    out = []
    # (string) Tj
    for m in re.finditer(r"\(((?:[^()\\]|\\.)*)\)\s*Tj", s):
        out.append(_pdf_unescape(m.group(1)))
    # [ (a) (b) 123 -456 ] TJ
    for m in re.finditer(r"\[(.*?)\]\s*TJ", s, re.DOTALL):
        for sm in re.finditer(r"\(((?:[^()\\]|\\.)*)\)", m.group(1)):
            out.append(_pdf_unescape(sm.group(1)))
    # (string) ' and (string) "
    for m in re.finditer(r"\(((?:[^()\\]|\\.)*)\)\s*['\"]", s):
        out.append(_pdf_unescape(m.group(1)))
    return out


def _stream_bytes(body):
    """Yield raw bytes of every `stream ... endstream` in an object body,
    transparently decompressing FlateDecode streams when possible."""
    out = []
    for m in re.finditer(r"stream(\r?\n)(.*?)endstream", body, re.DOTALL):
        raw = m.group(2).encode("latin-1", "replace")
        # Strip the single trailing EOL required by the PDF spec.
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n") or raw.endswith(b"\r"):
            raw = raw[:-1]
        header = body[max(0, m.start() - 256):m.start()]
        if "FlateDecode" in header:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                pass  # not actually flate, or corrupted; keep raw bytes
        out.append(raw)
    return out


def extract_pdf_text(data):
    """Return `(pages: list[str], problems: list[str])`.

    The extractor handles FlateDecode content streams and a useful subset of
    text-showing operators (Tj/TJ/'/\"). It is deliberately conservative: if it
    cannot recover text it reports a problem instead of guessing content.
    """
    pages, problems = [], []
    if not isinstance(data, (bytes, bytearray)):
        return [], ["pdf data is not bytes"]
    if not data[:5] == b"%PDF-":
        return [], ["not a PDF (missing %PDF- header)"]
    try:
        text = data.decode("latin-1")
    except Exception as exc:
        return [], [f"pdf decode error: {exc}"]

    objects = {}
    for m in re.finditer(r"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", text, re.DOTALL):
        objects[int(m.group(1))] = m.group(3)

    def page_content(body):
        streams = []
        cm = re.search(r"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R|\S+)", body)
        if cm:
            val = cm.group(1).strip()
            if val.startswith("["):
                for num, _gen in re.findall(r"(\d+)\s+(\d+)\s+R", val):
                    if int(num) in objects:
                        streams.extend(_stream_bytes(objects[int(num)]))
            else:
                rm = re.match(r"(\d+)\s+(\d+)\s+R", val)
                if rm and int(rm.group(1)) in objects:
                    streams.extend(_stream_bytes(objects[int(rm.group(1))]))
        if not streams:
            streams = _stream_bytes(body)
        return streams

    page_objs = [
        (num, body) for num, body in objects.items()
        if re.search(r"/Type\s*/Page\b", body)
    ]

    if page_objs:
        for _num, body in page_objs:
            parts = []
            for raw in page_content(body):
                parts.extend(_text_from_content(raw.decode("latin-1", "replace")))
            line = " ".join(parts)
            if line.strip():
                pages.append(line)
    else:
        # Fallback: concatenate every decoded stream and treat it as one page.
        parts = []
        for body in objects.values():
            for raw in _stream_bytes(body):
                parts.extend(_text_from_content(raw.decode("latin-1", "replace")))
        line = " ".join(parts)
        if line.strip():
            pages.append(line)
        problems.append("no /Type /Page objects found; single-page fallback used")

    if not pages:
        problems.append("no extractable text layer (likely a scanned PDF)")
    return pages, problems


# --------------------------------------------------------------------------
# DOCX paragraph extraction.
# --------------------------------------------------------------------------
def extract_docx_paras(data):
    """Return `(lines: list[str], problem_or_None)`."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except Exception as exc:
        return [], f"docx unzip error: {exc}"
    try:
        names = zf.namelist()
    except Exception as exc:
        return [], f"docx namelist error: {exc}"
    if "word/document.xml" not in names:
        return [], "docx missing word/document.xml"
    try:
        root = ElementTree.fromstring(zf.read("word/document.xml"))
    except Exception as exc:
        return [], f"docx xml parse error: {exc}"
    lines = []
    for p in root.iter(_W_DOC + "p"):
        pieces = [t.text or "" for t in p.iter(_W_DOC + "t")]
        line = "".join(pieces).strip()
        if line:
            lines.append(line)
    if not lines:
        return [], "no paragraphs extracted from docx"
    return lines, None


# --------------------------------------------------------------------------
# Contract handling: fill every output_contract key without renaming/removing.
# --------------------------------------------------------------------------
def contract_keys_of(contract):
    """Return the ordered list of field names implied by an output_contract."""
    if contract is None:
        return []
    if isinstance(contract, dict):
        return [str(k) for k in contract.keys()]
    if isinstance(contract, list):
        keys = []
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                key = (item.get("key") or item.get("name")
                       or item.get("field") or item.get("id"))
                if key:
                    keys.append(str(key))
        return keys
    return []


def contract_object(contract):
    """Return a dict containing every contract key, unknown values as null/[]."""
    out = {}
    keys = contract_keys_of(contract)
    for k in keys:
        out[k] = None
    return out


# --------------------------------------------------------------------------
# Finding validation (used by `check` and `build`).
# --------------------------------------------------------------------------
def validate_finding(f):
    problems = []
    if not isinstance(f, dict):
        return ["finding is not an object"]
    for field in FINDING_FIELDS:
        if field == "evidence":
            continue
        if f.get(field) in (None, ""):
            problems.append(f"missing/empty field '{field}'")
    facet = f.get("facet")
    if facet is not None and facet not in FACETS:
        problems.append(f"unknown facet '{facet}'")
    if f.get("severity") not in SEVERITIES:
        problems.append(f"unknown severity '{f.get('severity')}'")
    if f.get("issue_type") not in ISSUE_TYPES:
        problems.append(f"unknown issue_type '{f.get('issue_type')}'")
    if f.get("verdict") not in VERDICTS:
        problems.append(f"unknown verdict '{f.get('verdict')}'")
    if f.get("confidence") not in CONFIDENCES:
        problems.append(f"unknown confidence '{f.get('confidence')}'")
    ev = f.get("evidence")
    if ev is not None and not isinstance(ev, list):
        problems.append("evidence must be a list")
    elif isinstance(ev, list):
        for i, item in enumerate(ev):
            if not isinstance(item, dict):
                problems.append(f"evidence[{i}] not an object")
                continue
            if item.get("source_id") in (None, "") or item.get("quote") in (None, ""):
                problems.append(f"evidence[{i}] missing source_id/quote")
    return problems


def verify_quote(paragraphs, quote, location=None):
    """Check a quote against extracted paragraphs.

    Returns `(ok, best_locator, matched_text)`; `best_locator` is the location of
    the best (closest) match so the Agent can re-copy the verbatim text.
    """
    q = normalize_ws(quote)
    if not q:
        return False, None, ""
    if location:
        for p in paragraphs:
            if p["locator"] == location:
                return q in normalize_ws(p["text"]), location, p["text"]
    best, best_loc, best_text = 0, None, ""
    for p in paragraphs:
        pn = normalize_ws(p["text"])
        if q in pn:
            return True, p["locator"], p["text"]
        # crude similarity for the failure hint: longest common prefix run
        score = 0
        for a, b in zip(q, pn):
            if a == b:
                score += 1
            else:
                break
        if score > best:
            best, best_loc, best_text = score, p["locator"], p["text"]
    return False, best_loc, best_text


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Deterministic minimal-result builders (used on every failure path).
# --------------------------------------------------------------------------
def unchecked_item(facet, reason, detail=""):
    return {"facet": facet, "reason": reason, "detail": detail}


def empty_verdict_summary():
    return {
        "overall_verdict": "insufficient_evidence",
        "counts": {"critical": 0, "major": 0, "minor": 0,
                   "suggestion": 0, "total": 0},
        "coverage": {f: "not_checked" for f in FACETS},
    }


def summary_from_findings(findings, unchecked):
    counts = {"critical": 0, "major": 0, "minor": 0, "suggestion": 0}
    for f in findings:
        if isinstance(f, dict) and f.get("severity") in counts:
            counts[f["severity"]] += 1
    counts["total"] = sum(counts.values())
    coverage = {f: "not_checked" for f in FACETS}
    for f in findings:
        if isinstance(f, dict) and f.get("facet") in coverage:
            coverage[f["facet"]] = "checked"
    for u in unchecked:
        if isinstance(u, dict) and u.get("facet") in coverage:
            if coverage[u["facet"]] == "not_checked":
                coverage[u["facet"]] = "declared_unchecked"
    overall = "revision_required" if counts["total"] > 0 else "pass"
    return {"overall_verdict": overall, "counts": counts, "coverage": coverage}


def minimal_result(case_id, status, errors=None, degradations=None,
                   sources=None, contract=None, unchecked=None, changes=None,
                   runtime_meta=None):
    """Build a legal, JSON-serializable result envelope."""
    out = contract_object(contract)
    out.update({
        "case_id": case_id,
        "status": status,
        "review_report": "review_report.md",
        "sources": sources or [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors or [],
        "degradations": degradations or [],
        "changes": changes or [],
        "change_log": [],
        "runtime": runtime_meta or {},
        "result": {
            "findings": [],
            "unchecked_items": unchecked or [unchecked_item(f, "not reviewed") for f in FACETS],
            "verdict_summary": empty_verdict_summary(),
        },
    })
    return out
