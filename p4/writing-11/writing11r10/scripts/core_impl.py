#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the bilingual scholarly translator Skill.

Three-stage pipeline over one intermediate representation:

    segment  ->  extract(ledger)  ->  verify
    (split)      (single source)     (check)

The ledger (work/ledger.json) is the single source of truth. It records every
segment with a stable id, locator and verbatim quote, plus extracted
numbers/units, terms with variants, citation markers and semantic-boundary
markers. Translation is produced against this ledger; verification re-reads the
same ledger and flags drift, so no hand-written glossary is required.

Determinism/robustness: pure standard library, no randomness, utf-8-sig reading
with encoding fallback, atomic JSON writes, and explicit degradation instead of
invented data. Nothing here fabricates scientific data, citations or scores.
"""

import json
import os
import re
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".txt", ".md", ".markdown", ".tex", ".rst", ".json", ".csv",
             ".yaml", ".yml", ".html"}
OFFICE_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".odt", ".rtf"}

DEFAULT_CONTRACT_FIELDS = ["deliverables", "fact_source_map", "version_boundary"]

# Segment anchor written by the agent into target deliverables:
#   <!-- seg:s3 src:materials/paper.pdf#p3 -->
ANCHOR_RE = re.compile(r"<!--\s*seg:([A-Za-z0-9_.-]+)\s*-->")

_NUM_TOKEN = re.compile(
    r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_UNIT_RE = re.compile(
    r"\s*(°C|℃|°F|%|‰|ppm|ppb|mg|µg|μg|ng|g\b|kg|mL|L\b|µL|μL|cm|mm|µm|μm|nm|"
    r"m\b|s\b|ms\b|min\b|h\b|Hz|kHz|MHz|GHz|Pa|kPa|MPa|V\b|mV|A\b|mA|W\b|mW|kW|"
    r"mol\b|M\b|mM|µM|μM|nM|Da|kDa|IU|×)\b")
_MAG_RE = re.compile(r"\s*(万亿|亿|万|million|billion|thousand)\b", re.I)

_ABBR1 = re.compile(
    r"\b([A-Z][A-Za-z0-9&+-]{1,15})\s*\(\s*([A-Za-z][A-Za-z0-9 ,&+-]{1,60}?)\s*\)")
_ABBR2 = re.compile(
    r"([A-Z][a-z][a-z0-9 -]{1,60}?)\s*\(\s*([A-Z]{2,8})\s*\)")
_PHRASE = re.compile(r"\b[A-Z][a-z]+(?:\s(?:[A-Z][a-z]+|[a-z]+)){1,4}\b")

_CIT_BRACKET = re.compile(r"\[[0-9]{1,3}(?:\s*[,，\-–]\s*[0-9]{1,3})*\]")
_CIT_PAREN = re.compile(r"\([^()]{0,60}?[12]\d{3}[a-z]?\)")
_CIT_NARRATIVE = re.compile(
    r"[A-Z][A-Za-z]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][A-Za-z]+)?\s*\([12]\d{3}[a-z]?\)")

_STOPWORDS = {
    "the", "and", "of", "for", "with", "that", "this", "from", "are", "was",
    "were", "have", "has", "had", "not", "but", "their", "our", "they", "these",
    "those", "using", "used", "based", "between", "which", "within", "while",
    "after", "before", "into", "under", "over", "via", "per", "each", "both",
    "also", "such", "more", "less", "than", "one", "two", "three", "data",
    "results", "study", "figure", "table", "section"}

SEMANTIC_MARKERS = {
    "negation": ["not", "no", "never", "neither", "nor", "without", "unable",
                 "fail", "none", "absent", "non", "不", "非", "无", "未",
                 "没有", "并非", "否定"],
    "hedge": ["may", "might", "could", "possibly", "likely", "suggest",
              "indicate", "appear", "probably", "perhaps", "tends", "可能",
              "或许", "似乎", "表明", "提示", "倾向于", "大概"],
    "causal": ["because", "due to", "since", "therefore", "thus", "hence",
               "leads to", "caused by", "owing to", "resulting", "thereby",
               "因为", "由于", "因此", "所以", "导致", "因而", "从而"],
    "scope": ["only", "except", "unless", "limited to", "in this study",
              "our study", "within", "仅", "只", "除", "限于", "本研究",
              "文中", "本实验"],
    "degree": ["significantly", "markedly", "slightly", "substantially",
               "dramatically", "显著", "明显", "略微", "大幅度", "急剧"],
}


def _marker_hits(mk, text):
    """Count occurrences of a marker: word-boundary for ASCII, substring for CJK."""
    if not mk:
        return 0
    if re.search(r"[\u4e00-\u9fff]", mk):
        return text.count(mk)
    return len(re.findall(
        r"(?<![A-Za-z0-9])" + re.escape(mk) + r"(?![A-Za-z0-9])", text, re.I))


# --------------------------------------------------------------------------
# Time / IO helpers
# --------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path):
    """Read UTF-8 text with BOM/encoding fallback; skip binary or oversized.

    Returns None when the file is missing, a string otherwise. Oversized files
    return the sentinel string "__OVERSIZED__" so callers can record the limit.
    """
    if not path or not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        return "__OVERSIZED__"
    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw[:1024]:
        return None  # binary
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json_graceful(path):
    """Load JSON with bad-JSON degradation; never raises."""
    if not path or not os.path.exists(path):
        return None
    txt = read_text(path)
    if txt is None or txt == "__OVERSIZED__":
        return None
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Input discovery & case loading
# --------------------------------------------------------------------------
def discover_input(input_dir, work_dir=None):
    result = {"input_dir": input_dir, "case_json": None, "task_md": None,
              "materials": [], "missing": []}
    if not os.path.isdir(input_dir):
        result["missing"].append("input dir missing: %s" % input_dir)
        return result
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        low = name.lower()
        if low == "case.json":
            result["case_json"] = p
        elif low == "task.md":
            result["task_md"] = p
        else:
            ext = os.path.splitext(low)[1]
            if ext in TEXT_EXTS or ext in OFFICE_EXTS:
                result["materials"].append({"path": p, "ext": ext})
    if work_dir and os.path.isdir(work_dir):
        mat_dir = os.path.join(work_dir, "materials")
        if os.path.isdir(mat_dir):
            for name in sorted(os.listdir(mat_dir)):
                p = os.path.join(mat_dir, name)
                if not os.path.isfile(p):
                    continue
                ext = os.path.splitext(name.lower())[1]
                if ext in TEXT_EXTS:
                    result["materials"].append(
                        {"path": p, "ext": ext, "parsed": True})
    if result["case_json"] is None:
        result["missing"].append("case.json")
    if result["task_md"] is None:
        result["missing"].append("task.md")
    return result


def load_case(case_path):
    if not case_path or not os.path.exists(case_path):
        return {"case_id": None, "output_contract": None,
                "error": "case.json missing"}
    data = load_json_graceful(case_path)
    if not isinstance(data, dict):
        return {"case_id": None, "output_contract": None,
                "error": "case.json unreadable or not an object"}
    cid = (data.get("case_id") or data.get("id")
           or data.get("caseId") or data.get("challenge_id") or None)
    oc = data.get("output_contract")
    if isinstance(oc, dict):
        oc = list(oc.keys()) or None
    elif isinstance(oc, list):
        oc = [str(x) for x in oc]
    else:
        oc = None
    return {"case_id": cid, "output_contract": oc, "error": None}


# --------------------------------------------------------------------------
# Result skeleton lifecycle
# --------------------------------------------------------------------------
def _default_for(key):
    if key == "version_boundary":
        return {"modifications": [], "human_confirm": [],
                "material_gaps": [], "anomalies": [], "editable": True}
    return []


def make_skeleton(case_id, contract_fields):
    r = {"case_id": case_id, "status": "in_progress"}
    fields = list(contract_fields) if contract_fields else DEFAULT_CONTRACT_FIELDS
    for k in fields:
        if k not in r:
            r[k] = _default_for(k)
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        if k not in r:
            r[k] = []
    r["change_log"] = [{
        "time": now_iso(), "action": "init", "key": "result.json",
        "detail": "skeleton written; status=in_progress (partial, honest)"}]
    return r


def _touch_change_log(result, action, key, detail):
    logs = result.setdefault("change_log", [])
    if not isinstance(logs, list):
        logs = []
        result["change_log"] = logs
    logs.append({"time": now_iso(), "action": action, "key": key,
                 "detail": detail})


def result_add(result, key, item):
    """Append item to a list field, keeping change_log current."""
    lst = result.setdefault(key, [])
    if not isinstance(lst, list):
        lst = []
        result[key] = lst
    lst.append(item)
    _touch_change_log(result, "add", key,
                      "appended 1 item (%d total)" % len(lst))
    return result


def result_set(result, key, value):
    result[key] = value
    _touch_change_log(result, "set", key, "set field")
    return result


def finalize_result(result, status):
    result["status"] = status
    for k in DEFAULT_CONTRACT_FIELDS:
        if k not in result:
            result[k] = _default_for(k)
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        result.setdefault(k, [])
    _touch_change_log(result, "finalize", "result.json",
                      "finalized with status=%s" % status)
    return result


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------
def segment_text(text, source_loc, base_id, max_segments=4000):
    """Deterministic paragraph -> sentence segmentation with stable ids."""
    segments = []
    if not text or text == "__OVERSIZED__":
        return segments
    paragraphs = re.split(r"\n\s*\n", text)
    idx = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # keep structural blocks (markdown tables / LaTeX environments) whole
        if para.startswith("|") or para.startswith("\\begin{"):
            sents = [para]
        else:
            sents = _split_sentences(para)
        for sent in sents:
            sent = sent.strip()
            if not sent:
                continue
            idx += 1
            if idx > max_segments:
                break
            segments.append({
                "id": "%s-%d" % (base_id, idx),
                "locator": source_loc,
                "kind": _classify(sent),
                "quote": sent,
                "order": idx,
            })
        if idx > max_segments:
            break
    return segments


def _split_sentences(para):
    # split on sentence terminators followed by space+capital or CJK full stop
    parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff])", para)
    return [p for p in parts if p.strip()]


def _classify(sent):
    if sent.startswith("|"):
        return "table"
    if sent.startswith("\\begin{"):
        return "formula"
    if re.match(r"^\[[0-9]+\]", sent):
        return "reference"
    if re.match(r"^(Fig(?:ure)?|Table|Equation|Eq)\.?\s*\d+", sent, re.I):
        return "caption"
    return "paragraph"


# --------------------------------------------------------------------------
# Extraction primitives (heuristic; documented, deterministic)
# --------------------------------------------------------------------------
def _core_digits(s):
    return re.sub(r"[,，+\-\s%‰±]", "", s)


def find_numbers(text):
    out = []
    for m in _NUM_TOKEN.finditer(text):
        raw = m.group(0)
        start, end = m.start(), m.end()
        tail = text[end:end + 24]
        unit = None
        percent = False
        um = _UNIT_RE.match(tail)
        if um:
            unit = um.group(1)
            percent = unit in ("%", "‰")
        magnitude = None
        mm = _MAG_RE.match(tail)
        if mm:
            magnitude = mm.group(1)
        kind = "number"
        if re.match(r"\s*(?:–|—|~|--|-|to|至)\s*"
                    r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", tail):
            kind = "range"
        digits = re.sub(r"[,]", "", raw)
        if re.fullmatch(r"[12]\d{3}", digits):
            kind = "year"
        out.append({"raw": raw, "kind": kind, "unit": unit,
                    "percent": percent, "magnitude": magnitude,
                    "core": _core_digits(raw), "span": [start, end]})
    return out


def find_terms(text):
    terms = {}
    for m in _ABBR1.finditer(text):
        abbr, full = m.group(1), m.group(2).strip()
        terms[abbr] = {"source": abbr, "expansion": full,
                       "variants": [abbr, full], "canonical_target": ""}
    for m in _ABBR2.finditer(text):
        full, abbr = m.group(1).strip(), m.group(2)
        terms[abbr] = {"source": abbr, "expansion": full,
                       "variants": [abbr, full], "canonical_target": ""}
    for m in _PHRASE.finditer(text):
        phrase = m.group(0).strip()
        words = set(phrase.lower().split())
        if len(phrase) < 3 or not (words - _STOPWORDS):
            continue
        key = phrase.lower()
        if key not in terms:
            terms[key] = {"source": phrase, "expansion": None,
                          "variants": [phrase], "canonical_target": ""}
    return list(terms.values())[:80]


def find_citations(text):
    out = []
    for m in _CIT_BRACKET.finditer(text):
        raw = m.group(0)
        key = re.sub(r"[^0-9,，\-–]", "", raw)
        out.append({"raw": raw, "style": "numeric", "key": key})
    for m in _CIT_PAREN.finditer(text):
        raw = m.group(0)
        year = re.search(r"[12]\d{3}", raw).group(0)
        out.append({"raw": raw, "style": "author-year", "key": year})
    for m in _CIT_NARRATIVE.finditer(text):
        raw = m.group(0)
        year = re.search(r"[12]\d{3}", raw).group(0)
        out.append({"raw": raw, "style": "narrative", "key": year})
    return out


def find_semantic(text):
    low = text.lower()
    found = {}
    for typ, markers in SEMANTIC_MARKERS.items():
        hits = [mk for mk in markers if _marker_hits(mk, low) > 0]
        if hits:
            found[typ] = {"count": sum(_marker_hits(mk, low) for mk in hits),
                          "markers": hits}
    return found


# --------------------------------------------------------------------------
# Ledger building (the intermediate representation)
# --------------------------------------------------------------------------
def build_ledger(segments, sources):
    ledger = {
        "generated": now_iso(),
        "segment_count": len(segments),
        "segments": segments,
        "numbers": [],
        "terms": [],
        "citations": [],
        "semantic_markers": [],
    }
    seen_num = {}
    for seg in segments:
        loc = seg["id"]
        for n in find_numbers(seg["quote"]):
            nk = (n["core"], n.get("unit"))
            if nk in seen_num:
                continue
            seen_num[nk] = True
            n["locator"] = loc
            ledger["numbers"].append(n)
        for t in find_terms(seg["quote"]):
            t["locator"] = loc
            ledger["terms"].append(t)
        for c in find_citations(seg["quote"]):
            c["locator"] = loc
            ledger["citations"].append(c)
        for typ, info in find_semantic(seg["quote"]).items():
            ledger["semantic_markers"].append(
                {"type": typ, "locator": loc, "count": info["count"],
                 "markers": info["markers"]})
    ledger["sources"] = sources
    return ledger


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
def _extract_anchors(targets):
    anchors = {}
    for fname, text in targets.items():
        for m in ANCHOR_RE.finditer(text):
            anchors.setdefault(m.group(1), []).append(fname)
    return anchors


def verify(ledger, targets):
    """Check a translation corpus against the ledger.

    Returns a report dict with status 'pass'|'review'|'fail' plus
    errors/warnings/checks. Heuristic checks are warnings (human review), while
    missing numbers/citations/segments are errors.
    """
    corpus = "\n".join(targets.values())
    anchors = _extract_anchors(targets)
    report = {"time": now_iso(), "errors": [], "warnings": [],
              "checks": []}

    def add(level, check, locator, quote, detail):
        key = "errors" if level in ("error", "errors") else "warnings"
        report[key].append({"check": check, "locator": locator,
                            "quote": quote, "detail": detail})

    # 1. numbers fidelity
    num_missing = 0
    for n in ledger.get("numbers", []):
        core = n.get("core", "")
        if core and core in corpus:
            continue
        add("error", "numbers", n.get("locator", "?"), n.get("raw", ""),
            "number dropped or altered in target")
        num_missing += 1
    report["checks"].append({"name": "numbers", "total": len(ledger.get("numbers", [])),
                             "missing": num_missing})

    # 2. terminology consistency
    term_missing = 0
    for t in ledger.get("terms", []):
        canon = (t.get("canonical_target") or "").strip()
        if not canon:
            continue
        variants = [canon] + [v for v in t.get("variants", []) if v != canon]
        if any(v and v in corpus for v in variants):
            continue
        add("error", "terms", t.get("locator", "?"), t.get("source", ""),
            "canonical target term absent: %s" % canon)
        term_missing += 1
    report["checks"].append({"name": "terms", "checked": term_missing,
                             "total": len(ledger.get("terms", []))})

    # 3. citation retention
    cit_missing = 0
    for c in ledger.get("citations", []):
        key = c.get("key", "")
        if c.get("style") == "numeric" and key:
            if any(d in corpus for d in key.replace(",", " ").split()):
                continue
        elif key and key in corpus:
            continue
        add("error", "citations", c.get("locator", "?"), c.get("raw", ""),
            "citation marker not found in target")
        cit_missing += 1
    report["checks"].append({"name": "citations",
                             "total": len(ledger.get("citations", [])),
                             "missing": cit_missing})

    # 4. segment alignment (requires anchors in deliverables)
    seg_missing = []
    for seg in ledger.get("segments", []):
        if seg["id"] not in anchors:
            seg_missing.append(seg["id"])
    for sid in seg_missing:
        add("error", "alignment", sid, "",
            "segment has no translated anchor; add <!-- seg:%s -->" % sid)
    report["checks"].append({"name": "alignment",
                             "total": len(ledger.get("segments", [])),
                             "missing": len(seg_missing)})

    # 5. semantic boundaries (heuristic -> warning)
    for sm in ledger.get("semantic_markers", []):
        typ = sm.get("type")
        markers = SEMANTIC_MARKERS.get(typ, [])
        low = corpus.lower()
        tgt_hits = sum(_marker_hits(mk, low) for mk in markers)
        if sm.get("count", 0) > 0 and tgt_hits == 0:
            add("warning", "semantic_boundary", sm.get("locator", "?"), typ,
                "source has %s marker but target shows none; confirm not dropped"
                % typ)
    report["checks"].append({"name": "semantic_boundary",
                             "total": len(ledger.get("semantic_markers", []))})

    if report["errors"]:
        report["status"] = "fail"
    elif report["warnings"]:
        report["status"] = "review"
    else:
        report["status"] = "pass"
    return report
