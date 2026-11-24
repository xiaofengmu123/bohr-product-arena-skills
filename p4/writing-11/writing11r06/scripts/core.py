#!/usr/bin/env python3
"""Shared helpers for academic-translation-bilingual.

Standard library only. Every function is defensive: bad encodings, bad JSON,
missing files, and oversized inputs degrade to recorded errors instead of
crashing the process, so the agent can always emit a legal partial result.
"""
import json
import os
import re
import sys
import tempfile
import time

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB per text file; larger files are skipped.

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# --- time / io ------------------------------------------------------------

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read text with encoding fallback and a size cap.

    Returns (text, note). `note` is None on clean success, otherwise a short
    degradation message. Never raises on ordinary input problems.
    """
    if not os.path.exists(path):
        return None, "missing: %s" % path
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "unreadable: %s" % path
    if size > max_bytes:
        return None, "oversized(%d bytes, cap %d): %s" % (size, max_bytes, path)

    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), None
        except (UnicodeDecodeError, UnicodeError) as err:
            last_err = err
        except OSError as err:
            return None, "io-error(%s): %s" % (err, path)
    # Last resort: replace un-decodable bytes so callers still get content.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "bad-encoding(replaced): %s (%s)" % (path, last_err)
    except OSError as err:
        return None, "io-error(%s): %s" % (err, path)


def safe_read_json(path):
    """Read a JSON file; return (obj, note). Never raises for bad input."""
    text, note = safe_read_text(path)
    if text is None:
        return None, note
    try:
        return json.loads(text), note
    except ValueError as err:
        return None, "bad-json(%s): %s" % (err, path)


def write_json_atomic(path, obj):
    """Write JSON via temp file + rename so a crash never leaves a torn file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", prefix=".result-", dir=os.path.dirname(path) or "."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_change(result, action, key, detail):
    """Append one change_log entry in place (idempotent list)."""
    log = result.setdefault("change_log", [])
    log.append({"time": now_iso(), "action": action, "key": key, "detail": detail})


# --- input discovery ------------------------------------------------------

def discover_inputs(input_dir):
    """Walk an input directory; return a list of {path, size, kind} dicts."""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            items.append({"path": full, "size": size, "ext": ext})
    return items


def read_case(input_dir):
    """Read /input/case.json defensively and return (case_obj, note)."""
    case_path = os.path.join(input_dir, "case.json")
    obj, note = safe_read_json(case_path)
    return obj, note


def case_id_of(case_obj):
    """Extract case identifier from common key names, order of preference."""
    if not isinstance(case_obj, dict):
        return None
    for key in ("case_id", "id", "caseId", "challenge_id", "challengeId"):
        val = case_obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def declared_output_contract(case_obj):
    """Return the output_contract field list declared by the case, else None."""
    if not isinstance(case_obj, dict):
        return None
    oc = case_obj.get("output_contract")
    if isinstance(oc, dict):
        return list(oc.keys())
    if isinstance(oc, list):
        return list(oc)
    return None


# --- result.json skeleton -------------------------------------------------

def default_contract_fields():
    """题目默认三项 output_contract 字段（目标产物/事实与来源映射/版本与边界）。"""
    return ["deliverables", "fact_source_map", "version_boundary"]


def new_result(case_obj):
    """Build the initial legal result.json skeleton (status=in_progress)."""
    case_id = case_id_of(case_obj) or "unknown"
    declared = declared_output_contract(case_obj) or default_contract_fields()
    result = {"case_id": case_id, "status": "in_progress"}

    # Output-contract fields must be present even when empty.
    if "deliverables" in declared:
        result["deliverables"] = []
    if "fact_source_map" in declared:
        result["fact_source_map"] = []
    if "version_boundary" in declared:
        result["version_boundary"] = {
            "modifications": [],
            "human_confirm": [],
            "material_gaps": [],
            "anomalies": [],
            "editable": True,
        }
    # 逐字段回填其它声明键，未知值给 null，不改名、不删未知键。
    for key in declared:
        if key not in result:
            result[key] = None

    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("change_log", [])
    append_change(result, "init", "result.json", "skeleton created")
    return result, declared


# --- text analysis (deterministic, for fact/citation/number checks) --------

NUMBER_RE = re.compile(
    r"[-+]?\d[\d,]{0,15}(?:\.\d+)?\s*"
    r"(?:%|‰|°C|°F|K\b|kg|g|mg|µg|μg|nm|μm|µm|mm|cm|km|mL|ml|L\b|"
    r"s\b|min\b|h\b|year|years|yr|month|day|week|×10[+-]?\d+)?"
)
CITATION_YEAR_RE = re.compile(
    r"\([^()]{0,80}?\b(?:19|20)\d{2}[a-z]?[^()]{0,40}?\)"
)
BRACKET_CITE_RE = re.compile(r"\[\s*\d+(?:\s*[,，;；\-–—]\s*\d+)*\s*\]")


def extract_numbers(text):
    """Extract number-with-optional-unit tokens, deduplicated in order."""
    if not text:
        return []
    seen, out = set(), []
    for m in NUMBER_RE.finditer(text):
        tok = m.group(0).strip()
        key = tok
        if key not in seen:
            seen.add(key)
            out.append(tok)
    return out


def extract_citations(text):
    """Extract author-year and bracketed numeric citation markers."""
    if not text:
        return []
    out = []
    for m in CITATION_YEAR_RE.finditer(text):
        out.append(m.group(0).strip())
    for m in BRACKET_CITE_RE.finditer(text):
        out.append(m.group(0).strip())
    # dedupe, keep first-seen order
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


def split_segments(text, mode="paragraph"):
    """Split source text into translatable segments.

    mode=paragraph: split on blank lines (safe default for alignment).
    mode=sentence: split on sentence terminals after non-blank lines.
    Returns a list of {id, text, chars}.
    """
    if not text:
        return []
    if mode == "sentence":
        parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff])", text)
    else:
        parts = re.split(r"\n\s*\n", text)
    segs = []
    for i, p in enumerate(parts):
        p = p.strip()
        if not p:
            continue
        segs.append({"id": "seg-%03d" % (i + 1), "text": p, "chars": len(p)})
    return segs


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text):
    """Crude language hint: 'zh', 'en', 'mixed', or 'unknown'."""
    if not text:
        return "unknown"
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if cjk == 0 and latin > 0:
        return "en"
    if latin == 0 and cjk > 0:
        return "zh"
    if cjk > 0 and latin > 0:
        return "mixed"
    return "unknown"


# --- glossary / alignment model helpers -----------------------------------

def build_glossary_skeleton():
    """Deterministic, editable terminology model the agent fills in."""
    return {
        "direction": None,
        "domain": None,
        "terms": [],  # {src, tgt, domain, locked, note, occurrences[]}
        "conflicts": [],
    }


def build_alignment_skeleton(source_lang=None, target_lang=None):
    """Deterministic sentence/segment-level alignment model."""
    return {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "units": [],  # {id, src, tgt, position, status}
        "orphan_src": [],
        "orphan_tgt": [],
    }


def build_factcheck_skeleton():
    """Deterministic fact/number/citation verification model."""
    return {
        "numbers": [],   # {locator, quote, tgt_value, status}
        "citations": [], # {locator, quote, kept, status}
        "flags": [],     # {kind, locator, quote, reason}
    }


if __name__ == "__main__":
    print("core.py: importable helper module, not a CLI entry point.")
