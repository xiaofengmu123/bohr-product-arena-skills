# -*- coding: utf-8 -*-
"""Deterministic, dependency-free core for the research-frontier-trend Skill.

Design goal (robustness variant): no function in this module is allowed to
raise across adversarial inputs.  Functions return (value, error) tuples or
dicts carrying their own `error`/`degradation` notes so the orchestrator can
degrade gracefully.  Only the Python standard library is used.

Concurrency / determinism: all directory listings are sorted, iteration order
is fixed, and every write is atomic (temp file + replace).  Re-running on the
same input produces the same artifact content (runtime timestamps excepted).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone

# ---------------------------------------------------------------- constants

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "latin-1")
MAX_TEXT_BYTES = 2 * 1024 * 1024          # 2 MB per text file
RESULT_MAX_BYTES = 64 * 1024 * 1024       # self-check re-read cap for our own result.json
MAX_FILES = 200                            # hard sampling cap per discovery
MAX_QUOTE_LEN = 400                        # verbatim quote length cap
MAX_TERMS = 40                             # top terms kept for the baseline

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
SUPPORT_VALUES = ("reported", "not_reported", "inferred")
EVIDENCE_STATUS_VALUES = ("reported", "not_reported", "source_conflict", "inferred")
EVENT_TYPES = ("emergence", "merge", "split", "decline", "terminology_drift")
WORK_KINDS = ("origin", "method", "milestone", "benchmark")
GAP_TYPES = ("open_question", "method_failure", "unresolved_conflict", "coverage_absence")

SPECIAL_NAMES = ("case.json", "task.md")

YEAR_RE = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-3][0-9])(?!\d)")
ASCII_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9._\-]{2,}")
CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？；;])\s+|\n+")

FUTURE_PATTERNS = re.compile(
    r"(?i)(future work|remains? (open|unclear|unanswered)|open (question|problem)"
    r"|unknown|limitation|further (study|research|investigation)|not yet|"
    r"still lack|remains to be|unresolved|needs? (to be|further))"
)
CONFLICT_PATTERNS = re.compile(
    r"(?i)(however|in contrast|versus|on the other hand|conflict|discrepan|"
    r"inconsistent|disagree|contradict)"
)

# Minimal stopword set: removes metadata labels and common function words so the
# mechanical term baseline highlights domain terms rather than boilerplate.
STOPWORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "into", "have", "has",
    "was", "were", "are", "not", "but", "its", "our", "their", "they", "which",
    "title", "abstract", "year", "author", "authors", "et", "al", "doi", "http",
    "https", "www", "introduction", "method", "methods", "result", "results",
    "conclusion", "discussion", "figure", "fig", "table", "section", "study",
    "studies", "paper", "research", "using", "based", "proposed", "approach",
    "show", "shows", "shown", "however", "therefore", "thus", "also", "can",
    "may", "one", "two", "new", "used", "well", "via", "across", "between",
    "than", "more", "less", "these", "those",
})


def _keep_term(t, max_term_len=80):
    return 2 < len(t) <= max_term_len and t not in STOPWORDS

# ---------------------------------------------------------------- time / io


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(s):
    """Return a filesystem-safe short name derived from `s`."""
    s = re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(s or "unnamed")).strip("_")
    return (s or "unnamed")[:120]


def ensure_dir(path, fallback_rel):
    """Create `path`; on failure fall back to a cwd-relative dir.

    Returns (final_dir, degraded_bool, note).
    """
    try:
        os.makedirs(path, exist_ok=True)
        return path, False, None
    except OSError as exc:
        alt = os.path.join(os.getcwd(), fallback_rel)
        try:
            os.makedirs(alt, exist_ok=True)
            return alt, True, "unable to create %r (%s); using %r" % (path, exc, alt)
        except OSError as exc2:
            return os.getcwd(), True, "unable to create %r (%s) nor %r (%s)" % (
                path, exc, alt, exc2)


def read_bytes_limited(path, max_bytes=MAX_TEXT_BYTES):
    """Read up to max_bytes+1 bytes; report whether the file was truncated."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        return None, 0, "unreadable: %s" % exc
    size = len(data)
    truncated = size > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data, size, ("truncated at %d bytes" % max_bytes) if truncated else None


def decode_text(data):
    """Try several encodings; return (text, encoding) or (None, error)."""
    if data is None:
        return None, "no bytes"
    if b"\x00" in data[:4096]:
        return None, "looks binary (NUL bytes)"
    for enc in TEXT_ENCODINGS:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def read_text_file(path, max_bytes=MAX_TEXT_BYTES):
    """Read a text file robustly.

    Returns dict {ok, text, size, encoding, reason, truncated}.
    """
    data, size, note = read_bytes_limited(path, max_bytes)
    if data is None:
        return {"ok": False, "text": None, "size": 0, "encoding": None,
                "reason": note, "truncated": False}
    text, enc = decode_text(data)
    if text is None:
        return {"ok": False, "text": None, "size": size, "encoding": None,
                "reason": enc, "truncated": False}
    return {"ok": True, "text": text, "size": size, "encoding": enc,
            "reason": note, "truncated": bool(note)}


def load_json(path, max_bytes=MAX_TEXT_BYTES):
    """Load JSON robustly; return (obj, error). Never raises.

    `max_bytes` can be raised for our own (already-written) result.json so
    self-check can re-read large deliverables instead of truncating them.
    """
    try:
        data, size, note = read_bytes_limited(path, max_bytes)
        if data is None:
            return None, note
        text, enc = decode_text(data)
        if text is None:
            return None, "undecodable (%s)" % enc
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        return None, "json load error: %s" % exc


def write_json_atomic(path, obj):
    """Write JSON atomically; return (ok, error). Never raises."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return True, None
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, "write failed (%s)" % exc


def write_text_atomic(path, text):
    """Write plain text atomically; return (ok, error)."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True, None
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, "write failed (%s)" % exc


# ---------------------------------------------------------------- discovery


def discover_inputs(input_dir):
    """Scan a directory tree; return a deterministic manifest dict.

    Returns {exists, files[], case_path, task_path, notes[]}.
    Every entry: {abs, rel, name, ext, size, kind}.
    """
    manifest = {
        "exists": os.path.isdir(input_dir),
        "files": [],
        "case_path": None,
        "task_path": None,
        "notes": [],
    }
    if not manifest["exists"]:
        manifest["notes"].append("input directory %r not found" % input_dir)
        return manifest

    found = []
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fname in sorted(files):
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(fname)[1].lower().lstrip(".")
            kind = ("json" if ext == "json" else
                    "md" if ext in ("md", "markdown", "txt") else
                    "csv" if ext == "csv" else
                    "pdf" if ext == "pdf" else
                    "text" if ext in ("txt", "tex", "bib", "ris", "xml", "yaml", "yml") else
                    "other")
            found.append({
                "abs": full, "rel": rel, "name": fname, "ext": ext,
                "size": size, "kind": kind,
            })

    # Identify case.json / task.md by exact name first, then fallbacks.
    by_name = {f["name"].lower(): f for f in found}
    manifest["case_path"] = (by_name.get("case.json") or {}).get("abs")
    manifest["task_path"] = (by_name.get("task.md") or {}).get("abs")
    if manifest["case_path"] is None:
        jsons = [f for f in found if f["kind"] == "json"]
        for f in jsons:
            obj, err = load_json(f["abs"])
            if err is None and isinstance(obj, dict):
                if any(k in obj for k in ("output_contract", "case_id", "id", "challenge_id")):
                    manifest["case_path"] = f["abs"]
                    break
        if manifest["case_path"] is None and jsons:
            manifest["case_path"] = jsons[0]["abs"]
            manifest["notes"].append("no case.json; using first JSON %s" % jsons[0]["rel"])
    if manifest["task_path"] is None:
        mds = [f for f in found if f["kind"] == "md"]
        if mds:
            manifest["task_path"] = mds[0]["abs"]
            manifest["notes"].append("no task.md; using first markdown %s" % mds[0]["rel"])

    special = {manifest["case_path"], manifest["task_path"]}
    manifest["files"] = [f for f in found if f["abs"] not in special]

    if len(manifest["files"]) > MAX_FILES:
        # Deterministic sampling: keep by sort order already applied.
        dropped = len(manifest["files"]) - MAX_FILES
        manifest["files"] = manifest["files"][:MAX_FILES]
        manifest["notes"].append(
            "material files exceed %d; sampled first %d, dropped %d" % (MAX_FILES, MAX_FILES, dropped))
    return manifest


# ---------------------------------------------------------------- text / pdf


def pdf_to_text(path):
    """Extract text from a PDF using the system pdftotext, if available.

    Returns (text, method, error). Never raises.
    """
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "missing-tool", "pdftotext not on PATH; PDF left unparsed"
    try:
        proc = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", path, "-"],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "pdftotext", "pdftotext failed: %s" % exc
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        return None, "pdftotext", "pdftotext rc=%d %s" % (proc.returncode, err)
    text = (proc.stdout or b"").decode("utf-8", "replace")
    return (text or None), "pdftotext", None


def json_to_text(obj):
    """Flatten a JSON structure to a deterministic text stream of string values."""
    out = []

    def walk(node):
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for k in sorted(node):
                walk(node[k])
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif node is None:
            pass
        else:
            out.append(str(node))

    walk(obj)
    return "\n".join(out)


def extract_material_text(entry, work_dir):
    """Return {text, method, error, notes[]} for one material file."""
    notes = []
    if entry["kind"] == "pdf":
        text, method, error = pdf_to_text(entry["abs"])
        if text is None:
            notes.append("%s: %s" % (entry["rel"], error or "no text"))
        return {"text": text, "method": method, "error": error, "notes": notes}
    if entry["kind"] == "json":
        obj, err = load_json(entry["abs"])
        if err is not None:
            notes.append("%s: %s" % (entry["rel"], err))
            return {"text": None, "method": None, "error": err, "notes": notes}
        return {"text": json_to_text(obj), "method": "json-flatten", "error": None, "notes": notes}
    res = read_text_file(entry["abs"])
    if not res["ok"]:
        notes.append("%s: %s" % (entry["rel"], res["reason"]))
        return {"text": None, "method": None, "error": res["reason"], "notes": notes}
    if res["truncated"]:
        notes.append("%s: %s" % (entry["rel"], res["reason"]))
    return {"text": res["text"], "method": "text", "error": None, "notes": notes}


# ---------------------------------------------------------------- analysis


def split_sentences(text, max_len=800):
    """Deterministic, coarse sentence splitting for candidate extraction."""
    parts = SENTENCE_SPLIT_RE.split(text or "")
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        out.append(p[:max_len])
    return out


def line_span(text, phrase):
    """Return a locator `L<start>-L<end>` for `phrase` within `text` (1-based)."""
    if not phrase or phrase not in text:
        return None
    idx = text.find(phrase)
    start = text.count("\n", 0, idx) + 1
    end = start + phrase.count("\n")
    if start == end:
        return "L%d" % start
    return "L%d-L%d" % (start, end)


def extract_years(text):
    """Return sorted list of (year, count) from a text body."""
    hits = [int(m.group(1)) for m in YEAR_RE.finditer(text or "")]
    return sorted(Counter(hits).items())


def extract_terms(text, top=MAX_TERMS, max_term_len=80):
    """Return list of {term, count} for ASCII and CJK tokens.

    Pathological tokens (e.g. a 2MB run of one character) are skipped so a
    single oversized material cannot bloat result.json beyond re-readable
    size.
    """
    c = Counter()
    for m in ASCII_TERM_RE.finditer(text or ""):
        t = m.group(0).lower().strip("._-")
        if _keep_term(t, max_term_len):
            c[t] += 1
    for m in CJK_TERM_RE.finditer(text or ""):
        t = m.group(0).strip("._-")
        if _keep_term(t, max_term_len):
            c[t] += 1
    return [{"term": t, "count": n} for t, n in c.most_common(top)]


def find_candidate_sentences(text, patterns, max_hits=40):
    """Return list of {sentence, pattern_hit} matching any compiled pattern."""
    out = []
    for sent in split_sentences(text):
        m = patterns.search(sent)
        if m:
            out.append({"sentence": sent, "pattern_hit": m.group(0)})
            if len(out) >= max_hits:
                break
    return out


def title_guess(entry, text):
    """Weak title heuristic from filename or first non-empty line."""
    stem = os.path.splitext(entry["name"])[0]
    for line in (text or "").splitlines():
        line = line.strip()
        if line and len(line) >= 8:
            return line[:200]
    return stem


# ---------------------------------------------------------------- skeleton


def extract_case_id(case_obj, task_text, manifest):
    """Resolve case_id per the precedence in the contract."""
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)):
                return str(val)
    if task_text:
        m = re.search(r"challenge[-_ ]?id[:：]\s*[`'\"]?([A-Za-z0-9_\-]+)", task_text, re.I)
        if m:
            return m.group(1)
        m2 = re.search(r"challenge[-_ ]?id[:：]?\s*([A-Za-z0-9_\-]+)", task_text, re.I)
        if m2:
            return m2.group(1)
    return "unknown"


def make_skeleton(case_id, contract=None, input_dir=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT):
    """Build the full result skeleton with all fixed fields.

    Contract fields are echoed verbatim first (unknown keys preserved); fixed
    fields are then merged.  `case_id` and `status` are always set to our
    honest computed values.
    """
    result = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            result[k] = v
    fixed = {
        "case_id": case_id,
        "status": "partial",
        "research_question": {
            "question": None,
            "scope": None,
            "sub_questions": [],
            "domain_note": None,
        },
        "protocol": {
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "search_scope": None,
            "date_definition_note": None,
        },
        "time_trend": {
            "unit_note": None,
            "distribution": [],
            "windows": [],
            "claims": [],
        },
        "topic_evolution": {
            "stage_divisions": [],
            "term_mapping": [],
            "events": [],
            "claims": [],
        },
        "representative_works": {
            "criteria": [],
            "stages": [],
        },
        "research_gaps": {
            "gaps": [],
            "excluded_not_gaps": [],
        },
        "evidence_map": [],
        "conflicts": {"items": [], "resolution": None},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": utcnow_iso(),
            "input_dir": input_dir,
            "output_dir": output_dir,
            "phases": {},
        },
        "artifacts": {
            "report_md": "report.md",
            "evidence_map_json": "evidence_map.json",
            "documents_csv": "tables/documents.csv",
            "year_distribution_csv": "tables/year_distribution.csv",
        },
    }
    for key, val in fixed.items():
        result[key] = val
    return result


def log_change(result, action, reason, before=None, after=None):
    entry = {
        "time": utcnow_iso(),
        "action": action,
        "reason": reason,
        "before": before,
        "after": after,
    }
    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    result["change_log"].append(entry)
    return result


# ---------------------------------------------------------------- validation


def _has(kind, obj, path):
    return path in obj and isinstance(obj[path], kind) and bool(obj[path])


def validate_result(result):
    """Check contract fields, enums, and four artifact families.

    Returns list of {level, check, ok, detail}.  Never raises.
    """
    checks = []

    def add(level, check, ok, detail=""):
        checks.append({"level": level, "check": check, "ok": bool(ok), "detail": detail})

    if not isinstance(result, dict):
        add("FAIL", "result is object", False, "not a dict")
        return checks
    add("FAIL", "case_id present", bool(result.get("case_id")))
    add("FAIL", "status in enum", result.get("status") in STATUSES,
        str(result.get("status")))
    add("FAIL", "change_log is list", isinstance(result.get("change_log"), list))
    add("FAIL", "sources is list", isinstance(result.get("sources"), list))
    add("FAIL", "uncertainty is list", isinstance(result.get("uncertainty"), list))
    add("FAIL", "pending_human is list", isinstance(result.get("pending_human"), list))
    add("FAIL", "limitations is list", isinstance(result.get("limitations"), list))
    add("FAIL", "errors is list", isinstance(result.get("errors"), list))
    add("FAIL", "degradations is list", isinstance(result.get("degradations"), list))

    tt = result.get("time_trend") or {}
    add("WARN", "time_trend.distribution non-empty", _has(list, tt, "distribution"))
    add("WARN", "time_trend.unit_note set", bool(tt.get("unit_note")))
    te = result.get("topic_evolution") or {}
    add("WARN", "topic_evolution non-empty",
        bool(te.get("stage_divisions") or te.get("term_mapping") or te.get("events")))
    rw = result.get("representative_works") or {}
    add("WARN", "representative_works.criteria set", bool(rw.get("criteria")))
    rg = result.get("research_gaps") or {}
    add("WARN", "research_gaps non-empty", bool(rg.get("gaps") or rg.get("excluded_not_gaps")))
    add("WARN", "evidence_map non-empty", bool(result.get("evidence_map")))

    # Enum validation for candidate machine entries.
    for i, ev in enumerate(result.get("evidence_map") or []):
        if ev.get("status") not in EVIDENCE_STATUS_VALUES:
            add("FAIL", "evidence_map[%d].status enum" % i, False, str(ev.get("status")))
    for i, item in enumerate((result.get("conflicts") or {}).get("items") or []):
        if not isinstance(item, dict) or not item:
            add("WARN", "conflicts.items[%d] non-empty object" % i, False, str(item)[:80])

    add("INFO", "checks run", True, "%d checks" % len(checks))
    return checks


def hard_failures(checks):
    return [c for c in checks if c["level"] == "FAIL" and not c["ok"]]


# ---------------------------------------------------------------- tables


def write_csv_table(path, header, rows):
    """Write a CSV atomically; returns (ok, error)."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for row in rows:
                writer.writerow(row)
        os.replace(tmp, path)
        return True, None
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, "csv write failed (%s)" % exc
