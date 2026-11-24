#!/usr/bin/env python3
"""Shared robust helpers for manuscript-structure-review.

Design goal: every entry point in this package must degrade gracefully on
empty input, malformed JSON, wrong encodings, missing files, oversized files
and unavailable external tools (pdftotext / OCR). No function here raises
when the failure is in the *input*; only programming errors should propagate.

Only the Python standard library is used.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MAX_FILE_BYTES = 2 * 1024 * 1024          # per-file cap before truncation/skip
MAX_TEXT_READ = 2 * 1024 * 1024           # bytes actually decoded per file
MAX_CANDIDATE_NUMBERS = 250               # bound on number candidates
MAX_CANDIDATE_CLAIMS = 400                # bound on claim candidates
MAX_CANDIDATE_SECTIONS = 200              # bound on section candidates
MAX_QUOTE_CHARS = 400                     # verbatim quote cap

DEFAULT_CONTRACT = ["diagnosis", "source_map", "version_boundary", "artifacts"]

TEXT_EXTS = {".md", ".txt", ".tex", ".json", ".csv", ".rst", ".tsv", ".xml",
             ".html", ".htm", ".log", ".yaml", ".yml"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
              ".bmp", ".svg", ".pdf"}  # PDF reclassified below
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt",
               ".pages", ".numbers"}

SECTION_RE = re.compile(
    r"^(?:#{1,6}\s+.+|\d+(?:\.\d+){0,4}\s*[A-Za-z\u4e00-\u9fff][^\n]{0,120}|"
    r"[A-Z][A-Za-z0-9 \u4e00-\u9fff-]{2,60}\n[=\-]{3,})$",
    re.MULTILINE)
NUMBER_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?%?")
CLAIM_RE = re.compile(
    r"[^。.\n]{0,240}(?:因此|表明|证实|说明|所以|可见|提示|提示了|"
    r"thus|therefore|indicate|indicates|suggest|suggests|demonstrate|"
    r"demonstrates|show|shows|confirm|confirms)[^。.\n]{0,240}[。.]",
    re.IGNORECASE)
CITE_RE = re.compile(
    r"\[(?:\d{1,3}(?:[-,]\s*\d{1,3})*)\]|"
    r"\([A-Z][A-Za-z\u00c0-\u017f]+(?:\s+(?:et\s+al\.?|and|[A-Z][A-Za-z]+))?\s*,\s*\d{4}\)",
    re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #

def now_iso():
    """Local ISO-8601 timestamp with seconds precision."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Safe JSON / text reading
# --------------------------------------------------------------------------- #

def read_json_safe(path):
    """Read a JSON file robustly.

    Returns (data_or_None, error_or_None). Never raises on input problems.
    """
    try:
        if not os.path.exists(path):
            return None, "missing"
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return None, "oversized(%d bytes > %d)" % (size, MAX_FILE_BYTES)
        data = None
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with open(path, "r", encoding=enc, errors="replace") as fh:
                    raw = fh.read()
                data = json.loads(raw)
                break
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_err = "%s" % exc
                continue
        if data is None:
            return None, "bad_json: %s" % last_err
        return data, None
    except Exception as exc:  # noqa: BLE001 - report any unexpected I/O error
        return None, "error: %s" % exc


def read_text_safe(path, max_bytes=MAX_TEXT_READ):
    """Decode a text file defensively.

    Returns (text_or_None, meta_dict). meta always has keys: path, bytes,
    truncated, oversized, encoding, error, note. Text is truncated to
    ``max_bytes`` when the file is larger; the truncation is reported, never
    hidden.
    """
    meta = {"path": path, "bytes": None, "truncated": False, "oversized": False,
            "encoding": None, "error": None, "note": None}
    try:
        size = os.path.getsize(path)
        meta["bytes"] = size
        if size > max_bytes:
            meta["oversized"] = True
            meta["truncated"] = True
            meta["note"] = "truncated to first %d bytes of %d" % (max_bytes, size)
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes)
        if b"\x00" in raw[:4096]:
            meta["error"] = "binary/null-bytes, not treated as text"
            return None, meta
        text = None
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(enc)
                meta["encoding"] = enc
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")
            meta["encoding"] = "utf-8(replace)"
            meta["error"] = "undecodable with utf-8-sig/utf-8/gb18030"
        return text, meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = "%s" % exc
        return None, meta


# --------------------------------------------------------------------------- #
# File discovery / classification
# --------------------------------------------------------------------------- #

def classify(path):
    """Return one of text|pdf|image|office|other based on extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in TEXT_EXTS:
        return "text"
    return "other"


def list_input_files(input_dir):
    """Walk an input directory without raising; return file records.

    Oversized files are included with ``skipped="oversized"`` so they are
    reported honestly rather than silently dropped. Symlinks are ignored.
    """
    out = []
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    continue
                size = os.path.getsize(path)
                rel = os.path.relpath(path, input_dir)
                rec = {"path": path, "relpath": rel, "bytes": size}
                if size > MAX_FILE_BYTES:
                    rec["skipped"] = "oversized"
                out.append(rec)
            except OSError:
                continue
    return out


# --------------------------------------------------------------------------- #
# PDF extraction
# --------------------------------------------------------------------------- #

def pdf_to_text(path):
    """Extract text from a PDF via pdftotext.

    Returns (text_or_None, error_or_None). Avoids capturing stdout/stderr
    through pipes (compatible with sandboxed executors): output goes to a
    temp file, stderr is discarded.
    """
    tool = shutil.which("pdftotext")
    if not tool:
        return None, "pdftotext not found"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "page.txt")
            proc = subprocess.run(
                [tool, "-layout", "-nopgbrk", path, out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=60)
            if proc.returncode != 0:
                return None, "pdftotext rc=%d" % proc.returncode
            if not os.path.exists(out):
                return None, "pdftotext produced no output"
            with open(out, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read(), None
    except subprocess.TimeoutExpired:
        return None, "pdftotext timeout(60s)"
    except Exception as exc:  # noqa: BLE001
        return None, "pdftotext error: %s" % exc


# --------------------------------------------------------------------------- #
# Candidate extraction (anchor scan, not analysis)
# --------------------------------------------------------------------------- #

def _bounded(seq, limit):
    items = []
    for item in seq:
        if len(items) >= limit:
            break
        items.append(item)
    return items


def scan_text(text):
    """Extract bounded candidate anchors: sections, numbers, claims, cites.

    This is a *candidate* pass for the model to verify; it does not interpret
    findings and never invents anything.
    """
    text = text or ""
    sections = _bounded(
        (m.group(0).strip() for m in SECTION_RE.finditer(text)),
        MAX_CANDIDATE_SECTIONS)
    numbers = _bounded(
        (m.group(0) for m in NUMBER_RE.finditer(text)),
        MAX_CANDIDATE_NUMBERS)
    # de-duplicate numbers, preserve order
    seen, uniq_numbers = set(), []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            uniq_numbers.append(n)
    claims = _bounded(
        (m.group(0).strip() for m in CLAIM_RE.finditer(text)),
        MAX_CANDIDATE_CLAIMS)
    cites = _bounded(
        (m.group(0) for m in CITE_RE.finditer(text)),
        MAX_CANDIDATE_CLAIMS)
    return {
        "char_count": len(text),
        "line_count": text.count("\n") + (1 if text else 0),
        "sections": sections,
        "numbers": uniq_numbers,
        "claims": claims,
        "citations": cites,
    }


# --------------------------------------------------------------------------- #
# Skeleton / contract
# --------------------------------------------------------------------------- #

def extract_contract(case):
    """Return output-contract keys from a case dict, else the default set.

    Explicit ``output_contract`` keys are preserved verbatim (order and
    unknown keys included); nothing is renamed or dropped.
    """
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            return list(oc.keys())
        if isinstance(oc, list) and oc:
            return list(oc)
    return list(DEFAULT_CONTRACT)


def build_skeleton(case, contract_keys, case_path=None):
    """Build a partial result.json skeleton (status=in_progress)."""
    case_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id")
    if not case_id and case_path:
        parent = os.path.basename(os.path.dirname(os.path.abspath(case_path)))
        base = os.path.basename(case_path)
        case_id = "case_%s" % (parent or base or "unknown")
    if not case_id:
        case_id = "unknown"
    out = {"case_id": case_id, "status": "in_progress"}
    for key in contract_keys:
        out.setdefault(key, None)
    out["sources"] = []
    out["uncertainty"] = []
    out["pending_human"] = []
    out["change_log"] = [{
        "time": now_iso(),
        "action": "skeleton_written",
        "status": "in_progress",
    }]
    out["errors"] = []
    out["degradations"] = []
    out["runtime"] = {"started_at": now_iso()}
    return out


# --------------------------------------------------------------------------- #
# Atomic write
# --------------------------------------------------------------------------- #

def write_json_atomic(path, obj):
    """Write JSON atomically (temp file + os.replace). Never leaves a
    half-written result."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def normalize_ws(s):
    """Normalize whitespace for quote-backtracking comparisons."""
    return re.sub(r"\s+", " ", (s or "")).strip()
