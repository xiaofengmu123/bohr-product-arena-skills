#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation of the research-frontier-trend skill (standard library only).

Architecture note (deliberately different from a "prepare -> analyze -> selfcheck"
pipeline that mutates result.json in place):

  1. Every deterministic fact lives in an append-only *evidence ledger*
     (/app/work/ledger.jsonl): a `meta` record, one `doc` record per source
     document, and one `change` record per pipeline step.  The ledger is never
     hand-edited by the model; only the pipeline appends to it.

  2. Mechanical features (years, term frequencies, gap/conflict candidates,
     representative-work candidates) are cached in a separate, regenerable
     /app/work/features.json and are always marked machine=true.

  3. Interpretive claims (the model's own analysis) are written by the agent to
     /app/work/claims.json following a fixed schema.  Scripts never invent these.

  4. `compile` is a pure projection: it merges ledger + features + claims into
     result.json / report.md / evidence_map.json / tables/*.csv.  result.json is
     therefore always derived, never hand-assembled.

  5. Verification order is ledger-first: `selftest` validates the ledger and the
     verbatim quotes *before* validating the compiled JSON, so a bad quote or a
     dangling evidence id is caught at the source rather than patched downstream.

Only the Python standard library is imported.  All operations are deterministic:
no random numbers, no wall-clock-dependent decisions (timestamps are recorded as
metadata only), fixed sort orders everywhere.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024        # 2MB per source file
MAX_FILES = 200                          # per-material discovery cap
PDF_TIMEOUT_SECONDS = 60

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
EVIDENCE_STATUS = {"reported", "not_reported", "source_conflict", "inferred"}
CONFIDENCE = {"high", "medium", "low"}
SUPPORT = {"reported", "not_reported", "inferred"}
GAP_TYPES = {"open_question", "method_failure", "unresolved_conflict", "coverage_absence"}
EVENT_TYPES = {"emergence", "merge", "split", "decline", "terminology_drift"}
WORK_KINDS = {"origin", "method", "milestone", "benchmark"}

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html"}
PDF_SUFFIX = ".pdf"

# Gap / conflict marker sets (deterministic candidates only; the model decides).
GAP_MARKERS = re.compile(
    r"\b(future work|further work|remaining (?:question|challenge|issue)|open (?:question|problem)|"
    r"unresolved|not yet (?:known|understood|addressed|solved)|unknown|limitation|"
    r"needs? (?:to be|further) (?:investigated|studied|explored)|remains (?:unclear|open|unknown))\b",
    re.IGNORECASE,
)
CONFLICT_MARKERS = re.compile(
    r"\b(however|nevertheless|nonetheless|although|though|in contrast|by contrast|versus|vs\.?|"
    r"discrepancy|discrepant|contradict|conflicting|inconsistent|disagree|on the other hand)\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were", "have",
    "has", "had", "not", "but", "they", "their", "which", "these", "those", "into",
    "also", "more", "can", "may", "will", "than", "then", "when", "where", "while",
    "been", "its", "his", "her", "our", "your", "their", "such", "other", "each",
    "about", "between", "during", "after", "before", "through", "over", "under",
    "again", "once", "most", "some", "any", "all", "both", "few", "many", "much",
    "same", "different", "own", "only", "very", "just", "could", "would", "should",
}

TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")

# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def read_text_safe(path: Path):
    """Read a text file tolerating BOM / bad encoding.  Returns (text, encoding)."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8(replace)"


def load_json_safe(path: Path):
    """Load JSON without crashing; returns (obj, None) or (None, error)."""
    try:
        text, _ = read_text_safe(path)
    except OSError as exc:
        return None, str(exc)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"JSONDecodeError: {exc}"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def norm_ws(text: str) -> str:
    """Collapse whitespace so verbatim quotes can be matched robustly."""
    return re.sub(r"\s+", " ", text or "").strip()


def subseq_find(haystack: str, needle: str) -> int:
    """Return index of needle in haystack after whitespace normalisation, or -1."""
    return norm_ws(haystack).find(norm_ws(needle))


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #


class Context:
    def __init__(self, input_dir: str, output_dir: str, work_dir: str):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.ledger_path = self.work_dir / "ledger.jsonl"
        self.features_path = self.work_dir / "features.json"
        self.claims_path = self.work_dir / "claims.json"
        self.result_path = self.output_dir / "result.json"
        self.report_path = self.output_dir / "report.md"
        self.evidence_map_path = self.output_dir / "evidence_map.json"
        self.tables_dir = self.output_dir / "tables"
        self.materials_dir = self.work_dir / "materials"
        self.selftest_path = self.work_dir / "selftest.json"
        for d in (self.output_dir, self.work_dir, self.materials_dir, self.tables_dir):
            d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Input discovery & parsing
# --------------------------------------------------------------------------- #


def scan_input(ctx: Context):
    """Discover source files under /input.  Returns (records, degradations)."""
    records = []
    degradations = []
    if not ctx.input_dir.exists():
        return records, [f"input directory missing: {ctx.input_dir}"]
    all_files = sorted(p for p in ctx.input_dir.rglob("*") if p.is_file())
    for p in all_files:
        if p.name == "case.json" or p.name == "task.md":
            continue  # handled separately
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            degradations.append(f"skipped oversized file ({size} bytes): {p}")
            continue
        if len(records) >= MAX_FILES:
            degradations.append(f"file cap {MAX_FILES} reached; remaining files not read")
            break
        suffix = p.suffix.lower()
        kind = "pdf" if suffix == PDF_SUFFIX else ("text" if suffix in TEXT_SUFFIXES else "other")
        records.append({"path": str(p), "name": p.name, "size": size, "kind": kind})
    return records, degradations


def extract_case_info(ctx: Context):
    """Return (case_id, output_contract, errors)."""
    case_path = ctx.input_dir / "case.json"
    if not case_path.exists():
        return None, None, ["/input/case.json missing"]
    obj, err = load_json_safe(case_path)
    if err or not isinstance(obj, dict):
        return None, None, [f"case.json unparsable: {err}"]
    case_id = (
        obj.get("case_id")
        or obj.get("id")
        or obj.get("challenge_id")
        or obj.get("armiId")
    )
    contract = obj.get("output_contract")
    if not isinstance(contract, dict):
        contract = {}
    return case_id, contract, []


def extract_task_id(ctx: Context):
    task_path = ctx.input_dir / "task.md"
    if not task_path.exists():
        return None
    text, _ = read_text_safe(task_path)
    m = re.search(r"challenge_id[:：\s`]+([A-Za-z0-9\-_]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"challenge-([A-Za-z0-9\-_]+)", text)
    return f"challenge-{m.group(1)}" if m else None


def extract_document_metadata(obj):
    """Pull year/title fields from case.json if a documents/sources list exists."""
    out = {}
    if not isinstance(obj, dict):
        return out
    for key in ("documents", "sources", "papers", "materials", "literature"):
        items = obj.get(key)
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                fid = it.get("id") or it.get("doc_id") or it.get("file") or it.get("path")
                if not fid:
                    continue
                year = None
                for yk in ("year", "pub_year", "date", "published_year"):
                    v = it.get(yk)
                    m = YEAR_RE.search(str(v)) if v is not None else None
                    if m:
                        year = int(m.group(1))
                        break
                out[str(fid)] = {"title": it.get("title"), "year": year}
    return out


# --------------------------------------------------------------------------- #
# Material extraction to text
# --------------------------------------------------------------------------- #


def pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def extract_pdf(pdf_path: Path, out_txt: Path) -> str:
    """Extract text from a PDF via pdftotext; returns error string or '' on success."""
    if not pdftotext_available():
        return "pdftotext not found on PATH"
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True,
            timeout=PDF_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return "pdftotext not found on PATH"
    except subprocess.TimeoutExpired:
        return f"pdftotext timed out after {PDF_TIMEOUT_SECONDS}s"
    if proc.returncode != 0:
        return f"pdftotext exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:200]}"
    text = proc.stdout.decode("utf-8", "replace")
    out_txt.write_text(text, encoding="utf-8")
    return ""


def build_materials(ctx: Context, records, metadata, errors, degradations):
    """Copy/convert source files into /app/work/materials/mat_NNN.txt.

    Returns list of doc ledger records.
    """
    docs = []
    for i, rec in enumerate(records, start=1):
        doc_id = f"D{i:03d}"
        mat_txt = ctx.materials_dir / f"mat_{i:03d}.txt"
        orig = Path(rec["path"])
        title = metadata.get(rec["name"], {}).get("title") or metadata.get(str(orig), {}).get("title")
        if not title:
            title = orig.stem
        status = "parsed"
        note = ""
        n_chars = 0
        if rec["kind"] == "pdf":
            err = extract_pdf(orig, mat_txt)
            if err:
                status = "failed"
                note = err
                degradations.append(f"PDF not parsed: {rec['name']}: {err}")
                # still record the doc so the pipeline knows it exists but is unreadable
                if not mat_txt.exists():
                    mat_txt.write_text("", encoding="utf-8")
        elif rec["kind"] == "text":
            try:
                text, _ = read_text_safe(orig)
                mat_txt.write_text(text, encoding="utf-8")
            except OSError as exc:
                status = "failed"
                note = str(exc)
                degradations.append(f"text file unreadable: {rec['name']}: {exc}")
                mat_txt.write_text("", encoding="utf-8")
        else:
            status = "skipped"
            note = f"unsupported file kind: {orig.suffix}"
            degradations.append(f"skipped non-text/non-pdf file: {rec['name']}")
            continue

        extracted_text = mat_txt.read_text(encoding="utf-8", errors="replace")
        n_chars = len(extracted_text)
        docs.append(
            {
                "t": "doc",
                "doc_id": doc_id,
                "source_file": f"materials/mat_{i:03d}.txt",
                "orig_path": str(orig),
                "orig_name": rec["name"],
                "title": title,
                "n_chars": n_chars,
                "hash": sha1_text(extracted_text),
                "kind": rec["kind"],
                "status": status,
                "note": note,
            }
        )
    return docs


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #


def write_ledger(ctx: Context, meta, docs, changes) -> None:
    ctx.ledger_path.write_text("", encoding="utf-8")
    append_jsonl(ctx.ledger_path, meta)
    for d in docs:
        append_jsonl(ctx.ledger_path, d)
    for c in changes:
        append_jsonl(ctx.ledger_path, c)


def read_ledger(ctx: Context):
    meta = {}
    docs = []
    changes = []
    if not ctx.ledger_path.exists():
        return meta, docs, changes
    for line in ctx.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("t")
        if t == "meta":
            meta = obj
        elif t == "doc":
            docs.append(obj)
        elif t == "change":
            changes.append(obj)
    return meta, docs, changes


# --------------------------------------------------------------------------- #
# Feature extraction (deterministic, machine=true)
# --------------------------------------------------------------------------- #


def split_sentences(text: str):
    return re.split(r"(?<=[.!?。！？])\s+|\n+", text)


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def guess_year(text: str, meta_year):
    """Conservative year guess from text header.  Never fabricates: returns None when unclear."""
    if meta_year is not None:
        return meta_year, "metadata"
    head = text[:1200]
    years = [int(m.group(1)) for m in YEAR_RE.finditer(head)]
    if not years:
        return None, "missing"
    from collections import Counter

    counts = Counter(years)
    top, freq = counts.most_common(1)[0]
    if freq >= 2:
        return top, "text_header"
    return None, "missing"


def extract_features(ctx: Context):
    meta, docs, _ = read_ledger(ctx)
    doc_years = {}
    terms = []
    gap_candidates = []
    conflict_candidates = []
    work_candidates = []
    metadata = {}
    case_obj, _ = load_json_safe(ctx.input_dir / "case.json")
    if isinstance(case_obj, dict):
        metadata = extract_document_metadata(case_obj)
    # map orig_name -> metadata year/title (metadata keys may be ids or file names)
    meta_lookup = {}
    for k, v in metadata.items():
        meta_lookup[k] = v
        meta_lookup[Path(k).name] = v

    for d in docs:
        if d.get("status") != "parsed":
            doc_years[d["doc_id"]] = {"year": None, "source": "unparsed", "note": d.get("note", "")}
            continue
        mat_txt = ctx.work_dir / d["source_file"]
        if not mat_txt.exists():
            doc_years[d["doc_id"]] = {"year": None, "source": "missing", "note": "extracted text missing"}
            continue
        text = mat_txt.read_text(encoding="utf-8", errors="replace")
        md = meta_lookup.get(d.get("orig_name"), {})
        meta_year = md.get("year")
        meta_title = md.get("title")
        year, source = guess_year(text, meta_year)
        note = ""
        if source == "text_header":
            note = "year inferred from 4-digit pattern in first 1200 chars; needs human confirmation"
        elif source == "missing":
            note = "no publication-year metadata or header year found"
        doc_years[d["doc_id"]] = {"year": year, "source": source, "note": note}

        title = meta_title or d.get("title")
        work_candidates.append(
            {
                "doc_id": d["doc_id"],
                "title": title,
                "year": year,
                "citations": None,
                "heuristics": ["document-in-corpus"],
            }
        )

        # vocabulary counts (unigrams + bigrams) for term-mapping support.
        # Bigrams are formed from *adjacent* alpha tokens in the original token
        # stream (short words kept for adjacency, then filtered) so they reflect
        # real co-occurrence rather than skipping intervening short words.
        low = text.lower()
        toks = TOKEN_RE.findall(low)
        all_toks = re.findall(r"[a-z]+", low)
        unigram = {}
        bigram = {}
        for t in toks:
            if t in STOPWORDS or len(t) < 3:
                continue
            unigram[t] = unigram.get(t, 0) + 1
        for a, b in zip(all_toks, all_toks[1:]):
            if a in STOPWORDS or b in STOPWORDS or len(a) < 3 or len(b) < 3:
                continue
            bigram[f"{a} {b}"] = bigram.get(f"{a} {b}", 0) + 1
        for term, cnt in sorted(unigram.items(), key=lambda kv: (-kv[1], kv[0]))[:60]:
            first = low.find(term)
            last = low.rfind(term)
            terms.append(
                {
                    "term": term,
                    "doc_id": d["doc_id"],
                    "count": cnt,
                    "first_line": line_of(text, first) if first >= 0 else 1,
                    "last_line": line_of(text, last) if last >= 0 else 1,
                }
            )
        for term, cnt in sorted(bigram.items(), key=lambda kv: (-kv[1], kv[0]))[:40]:
            first = low.find(term)
            last = low.rfind(term)
            terms.append(
                {
                    "term": term,
                    "doc_id": d["doc_id"],
                    "count": cnt,
                    "first_line": line_of(text, first) if first >= 0 else 1,
                    "last_line": line_of(text, last) if last >= 0 else 1,
                }
            )

        # gap / conflict sentence candidates with verbatim quotes
        for sent in split_sentences(text):
            sent = sent.strip()
            if len(sent) < 20 or len(sent) > 600:
                continue
            start = text.find(sent)
            if start < 0:
                continue
            ln = line_of(text, start)
            locator = f"{d['source_file']}#L{ln}"
            if GAP_MARKERS.search(sent):
                gap_candidates.append(
                    {"doc_id": d["doc_id"], "locator": locator, "quote": sent, "pattern": "gap_marker"}
                )
            if CONFLICT_MARKERS.search(sent):
                conflict_candidates.append(
                    {"doc_id": d["doc_id"], "locator": locator, "quote": sent, "pattern": "conflict_marker"}
                )

    features = {
        "schema": 1,
        "generated_at": now_iso(),
        "machine": True,
        "doc_years": doc_years,
        "terms": terms,
        "gap_candidates": gap_candidates,
        "conflict_candidates": conflict_candidates,
        "work_candidates": work_candidates,
    }
    write_json(ctx.features_path, features)
    return features


# --------------------------------------------------------------------------- #
# Claims schema helpers
# --------------------------------------------------------------------------- #


def empty_claims_template():
    return {
        "status_hint": None,
        "research_question": {"question": None, "scope": None, "sub_questions": [], "domain_note": None},
        "protocol": {"inclusion_criteria": [], "exclusion_criteria": [], "search_scope": None, "date_definition_note": None},
        "evidence": [],
        "time_trend": {"unit_note": None, "inflection_notes": [], "claims": []},
        "topic_evolution": {"stages": [], "term_mapping": [], "events": [], "claims": []},
        "representative_works": {"criteria": [], "stages": []},
        "research_gaps": {"gaps": [], "ruled_out": []},
        "conflicts": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
    }


def load_claims(ctx: Context):
    obj, err = load_json_safe(ctx.claims_path)
    if err or not isinstance(obj, dict):
        return empty_claims_template()
    return obj


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #


def project_change(c):
    """Strip the internal ledger marker 't' before exposing a change_log entry."""
    out = {k: v for k, v in c.items() if k != "t"}
    return out


def build_sources(docs):
    out = []
    for d in sorted(docs, key=lambda x: x["doc_id"]):
        out.append(
            {
                "doc_id": d["doc_id"],
                "source_file": d["source_file"],
                "orig_name": d.get("orig_name"),
                "title": d.get("title"),
                "kind": d.get("kind"),
                "status": d.get("status"),
                "n_chars": d.get("n_chars"),
                "note": d.get("note") or None,
            }
        )
    return out


def build_year_distribution(features, docs):
    doc_years = features.get("doc_years", {})
    buckets = {}
    for d in docs:
        y = doc_years.get(d["doc_id"], {}).get("year")
        if y is None:
            continue
        buckets.setdefault(y, []).append(d["doc_id"])
    return [{"year": y, "count": len(ids), "doc_ids": sorted(ids)} for y, ids in sorted(buckets.items())]


def build_time_trend(features, docs, tt_claims):
    unit_note = tt_claims.get("unit_note")
    if not unit_note:
        unit_note = "年份按 protocol.date_definition_note 口径统计；来源为 features.doc_years，缺失年份不计入分布。"
    return {
        "unit_note": unit_note,
        "distribution": build_year_distribution(features, docs),
        "inflection_notes": tt_claims.get("inflection_notes") or [],
        "claims": tt_claims.get("claims") or [],
    }


def normalize_evidence(evidence_list):
    """Assign ids where missing and coerce status/confidence enums."""
    out = []
    seen = set()
    auto = 1
    for e in evidence_list or []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()
        if not eid:
            while f"E{auto:03d}" in seen:
                auto += 1
            eid = f"E{auto:03d}"
        if eid in seen:
            continue
        seen.add(eid)
        status = e.get("status") if e.get("status") in EVIDENCE_STATUS else "reported"
        conf = e.get("confidence") if e.get("confidence") in CONFIDENCE else "medium"
        out.append(
            {
                "id": eid,
                "claim": e.get("claim") or "",
                "locator": e.get("locator") or "",
                "quote": e.get("quote") or "",
                "source_file": e.get("source_file") or "",
                "status": status,
                "confidence": conf,
            }
        )
    return out


def products_complete(result):
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    return bool(
        (tt.get("claims") or tt.get("distribution"))
        and (te.get("stages") or te.get("events"))
        and rw.get("stages")
        and rg.get("gaps")
    )


def resolve_status(result, claims, errors):
    if errors:
        return "input_error"
    if not result.get("sources"):
        return "insufficient_evidence"
    hint = claims.get("status_hint")
    if hint == "insufficient_evidence":
        return "insufficient_evidence"
    if hint == "input_error":
        return "input_error"
    if products_complete(result) and result.get("evidence_map"):
        return "ok"
    return "partial"


def assemble_result(ctx, ledger, features, claims):
    meta = ledger.get("meta", {}) if isinstance(ledger, dict) else {}
    docs = ledger.get("docs", []) if isinstance(ledger, dict) else []
    changes = ledger.get("changes", []) if isinstance(ledger, dict) else []
    errors = list(meta.get("errors") or [])
    degradations = list(meta.get("degradations") or [])
    contract = meta.get("contract") or {}

    result = deepcopy(contract) if isinstance(contract, dict) else {}
    result["case_id"] = meta.get("case_id") or "unknown"
    result["research_question"] = claims.get("research_question") or {
        "question": None, "scope": None, "sub_questions": [], "domain_note": None
    }
    result["protocol"] = claims.get("protocol") or {
        "inclusion_criteria": [], "exclusion_criteria": [], "search_scope": None, "date_definition_note": None
    }
    result["time_trend"] = build_time_trend(features, docs, claims.get("time_trend") or {})
    result["topic_evolution"] = claims.get("topic_evolution") or {
        "stages": [], "term_mapping": [], "events": [], "claims": []
    }
    result["representative_works"] = claims.get("representative_works") or {"criteria": [], "stages": []}
    result["research_gaps"] = claims.get("research_gaps") or {"gaps": [], "ruled_out": []}
    result["evidence_map"] = normalize_evidence(claims.get("evidence") or [])
    result["conflicts"] = claims.get("conflicts") or []
    result["sources"] = build_sources(docs)
    result["uncertainty"] = claims.get("uncertainty") or []
    result["pending_human"] = claims.get("pending_human") or []
    result["change_log"] = [project_change(c) for c in changes]
    result["limitations"] = claims.get("limitations") or []
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = {
        "started_at": meta.get("started_at"),
        "finished_at": now_iso(),
        "documents_seen": len(docs),
        "documents_parsed": sum(1 for d in docs if d.get("status") == "parsed"),
    }
    result["status"] = resolve_status(result, claims, errors)
    return result


def build_report(result):
    lines = []
    lines.append("# 研究前沿与趋势分析结果\n")
    lines.append(f"- case_id: `{result.get('case_id')}`")
    lines.append(f"- status: `{result.get('status')}`")
    lines.append(f"- 生成时间: `{result.get('runtime', {}).get('finished_at')}`\n")

    rq = result.get("research_question") or {}
    lines.append("## 研究问题")
    lines.append(f"- 问题: {rq.get('question') or '（待填）'}")
    lines.append(f"- 范围: {rq.get('scope') or '（待填）'}")
    lines.append(f"- 子问题: {', '.join(rq.get('sub_questions') or []) or '（无）'}\n")

    proto = result.get("protocol") or {}
    lines.append("## 检索与纳排口径")
    lines.append(f"- 检索范围: {proto.get('search_scope') or '（待填）'}")
    lines.append(f"- 年份口径: {proto.get('date_definition_note') or '（待填）'}")
    lines.append(f"- 纳入: {'；'.join(proto.get('inclusion_criteria') or []) or '（无）'}")
    lines.append(f"- 排除: {'；'.join(proto.get('exclusion_criteria') or []) or '（无）'}\n")

    tt = result.get("time_trend") or {}
    lines.append("## 时间趋势")
    lines.append(f"- 口径: {tt.get('unit_note') or '（无）'}")
    dist = tt.get("distribution") or []
    if dist:
        lines.append("| 年份 | 数量 | 文献 |")
        lines.append("|---|---|---|")
        for row in dist:
            lines.append(f"| {row['year']} | {row['count']} | {', '.join(row['doc_ids'])} |")
    else:
        lines.append("- （无年度分布数据）")
    for c in tt.get("claims") or []:
        ev = ", ".join(c.get("evidence_ids") or [])
        lines.append(f"- 结论[{c.get('support')}]: {c.get('claim')}（证据: {ev}）")
    lines.append("")

    te = result.get("topic_evolution") or {}
    lines.append("## 主题演化")
    for s in te.get("stages") or []:
        lines.append(f"- 阶段「{s.get('stage')}」（{s.get('period')}）：{s.get('basis')}；术语: {', '.join(s.get('terms') or [])}")
    for e in te.get("events") or []:
        lines.append(f"- 事件[{e.get('type')}] `{e.get('term')}`（{e.get('period')}）：{e.get('cause')}")
    for c in te.get("claims") or []:
        lines.append(f"- 结论[{c.get('support')}]: {c.get('claim')}（证据: {', '.join(c.get('evidence_ids') or [])}）")
    lines.append("")

    rw = result.get("representative_works") or {}
    lines.append("## 代表性工作")
    lines.append(f"- 选取口径: {'；'.join(rw.get('criteria') or []) or '（待填）'}")
    for s in rw.get("stages") or []:
        lines.append(f"### 阶段「{s.get('stage')}」（{s.get('period')}）")
        for w in s.get("works") or []:
            lines.append(f"- [{w.get('kind')}] {w.get('title')}（{w.get('year')}，{w.get('doc_id')}）：{w.get('rationale')}")
    lines.append("")

    rg = result.get("research_gaps") or {}
    lines.append("## 研究空白")
    for g in rg.get("gaps") or []:
        lines.append(f"- {g.get('id')}[{g.get('type')}]: {g.get('description')}；排除证据: {g.get('exclusion_evidence')}")
    for r in rg.get("ruled_out") or []:
        lines.append(f"- 已排除非空白: {r.get('candidate')}（{r.get('reason')}）")
    lines.append("")

    lines.append("## 证据映射")
    for e in result.get("evidence_map") or []:
        lines.append(f"- {e.get('id')}[{e.get('status')}/{e.get('confidence')}] {e.get('locator')}: {e.get('claim')}")
    lines.append("")

    conflicts = result.get("conflicts") or []
    if conflicts:
        lines.append("## 冲突证据")
        for c in conflicts:
            lines.append(f"- {', '.join(c.get('items') or [])}：{c.get('resolution')}")
        lines.append("")

    unc = result.get("uncertainty") or []
    if unc:
        lines.append("## 不确定性")
        for u in unc:
            lines.append(f"- {u}")
        lines.append("")

    pend = result.get("pending_human") or []
    if pend:
        lines.append("## 待人工核验")
        for p in pend:
            lines.append(f"- {p}")
        lines.append("")

    lims = result.get("limitations") or []
    if lims:
        lines.append("## 边界与限制")
        for l in lims:
            lines.append(f"- {l}")
        lines.append("")
    return "\n".join(lines)


def write_tables(ctx, result):
    docs = result.get("sources") or []
    with (ctx.tables_dir / "documents.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doc_id", "source_file", "orig_name", "title", "kind", "status", "n_chars", "note"])
        for d in docs:
            w.writerow(
                [d["doc_id"], d["source_file"], d.get("orig_name"), d.get("title"), d.get("kind"),
                 d.get("status"), d.get("n_chars"), d.get("note")]
            )
    dist = (result.get("time_trend") or {}).get("distribution") or []
    with (ctx.tables_dir / "year_distribution.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "count", "doc_ids"])
        for row in dist:
            w.writerow([row["year"], row["count"], ";".join(row["doc_ids"])])


def compile_artifacts(ctx, status_prev=None, reason=None):
    meta, docs, changes = read_ledger(ctx)
    features, _ = load_json_safe(ctx.features_path)
    if not isinstance(features, dict):
        features = {"doc_years": {}, "terms": [], "gap_candidates": [], "conflict_candidates": [], "work_candidates": []}
    claims = load_claims(ctx)
    ledger = {"meta": meta, "docs": docs, "changes": changes}
    result = assemble_result(ctx, ledger, features, claims)

    # record this compile step in the ledger before freezing change_log
    change = {
        "t": "change",
        "time": now_iso(),
        "action": "compile",
        "reason": reason or "merge ledger+features+claims into artifacts",
        "before": status_prev,
        "after": result["status"],
    }
    append_jsonl(ctx.ledger_path, change)
    result["change_log"] = [project_change(c) for c in (changes + [change])]

    write_json(ctx.result_path, result)
    ctx.report_path.write_text(build_report(result), encoding="utf-8")
    write_json(ctx.evidence_map_path, {"case_id": result["case_id"], "evidence_map": result["evidence_map"]})
    write_tables(ctx, result)
    return result


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_init(ctx: Context, force: bool) -> int:
    case_id, contract, errors = extract_case_info(ctx)
    task_id = extract_task_id(ctx)
    if not case_id and task_id:
        case_id = task_id
    records, degradations = scan_input(ctx)
    metadata = {}
    case_obj, _ = load_json_safe(ctx.input_dir / "case.json")
    if isinstance(case_obj, dict):
        metadata = extract_document_metadata(case_obj)
    docs = build_materials(ctx, records, metadata, errors, degradations)

    meta = {
        "t": "meta",
        "schema": 1,
        "started_at": now_iso(),
        "case_id": case_id or "unknown",
        "contract": contract or {},
        "errors": errors,
        "degradations": degradations,
    }
    change = {
        "t": "change",
        "time": now_iso(),
        "action": "init",
        "reason": "scan input, build ledger and skeleton",
        "before": None,
        "after": "partial-skeleton",
    }
    write_ledger(ctx, meta, docs, [change])

    # Do not overwrite an agent-edited claims.json.
    if not ctx.claims_path.exists():
        write_json(ctx.claims_path, empty_claims_template())

    features = extract_features(ctx)
    compile_artifacts(ctx, status_prev=None, reason="init skeleton")
    return 0


def cmd_extract(ctx: Context) -> int:
    meta, docs, _ = read_ledger(ctx)
    status_prev = None
    if ctx.result_path.exists():
        obj, _ = load_json_safe(ctx.result_path)
        if isinstance(obj, dict):
            status_prev = obj.get("status")
    features = extract_features(ctx)
    compile_artifacts(ctx, status_prev=status_prev, reason="deterministic feature extraction")
    return 0


def cmd_compile(ctx: Context) -> int:
    status_prev = None
    if ctx.result_path.exists():
        obj, _ = load_json_safe(ctx.result_path)
        if isinstance(obj, dict):
            status_prev = obj.get("status")
    compile_artifacts(ctx, status_prev=status_prev, reason="agent claims merged")
    return 0


def cmd_status(ctx: Context) -> int:
    meta, docs, changes = read_ledger(ctx)
    features, _ = load_json_safe(ctx.features_path)
    claims = load_claims(ctx)
    ledger = {"meta": meta, "docs": docs, "changes": changes}
    result = assemble_result(ctx, ledger, features or {}, claims)
    print(json.dumps(
        {
            "case_id": result.get("case_id"),
            "status": result.get("status"),
            "documents": len(result.get("sources") or []),
            "parsed": (result.get("runtime") or {}).get("documents_parsed"),
            "evidence": len(result.get("evidence_map") or []),
            "products_complete": products_complete(result),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #


def collect_quote_sources(ctx, docs):
    """Map source_file -> normalized text for verbatim quote checking."""
    texts = {}
    for d in docs:
        sf = d.get("source_file")
        if not sf:
            continue
        p = ctx.work_dir / sf
        if p.exists():
            texts[sf] = p.read_text(encoding="utf-8", errors="replace")
    return texts


def run_selftest(ctx: Context):
    checks = []
    hard_fail = False

    def add(level, ok, cid, message):
        nonlocal hard_fail
        if level == "FAIL" and not ok:
            hard_fail = True
        checks.append({"level": level if not ok else "PASS", "id": cid, "ok": ok, "message": message})

    # 1. result.json exists and is valid JSON
    if not ctx.result_path.exists():
        add("FAIL", False, "S01", "result.json missing")
        summary = {"passed": False, "checks": checks, "summary": "result.json missing"}
        write_json(ctx.selftest_path, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    result, err = load_json_safe(ctx.result_path)
    if err or not isinstance(result, dict):
        add("FAIL", False, "S01", f"result.json invalid: {err}")
        write_json(ctx.selftest_path, {"passed": False, "checks": checks, "summary": "invalid result.json"})
        print(json.dumps({"passed": False, "checks": checks}, ensure_ascii=False, indent=2))
        return 1
    add("FAIL", True, "S01", "result.json exists and parses as JSON")

    # 2. case_id present
    add("FAIL", bool(result.get("case_id")), "S02", "case_id present")

    # 3. required fixed fields present with correct types
    required = {
        "status": str,
        "research_question": dict,
        "protocol": dict,
        "time_trend": dict,
        "topic_evolution": dict,
        "representative_works": dict,
        "research_gaps": dict,
        "evidence_map": list,
        "conflicts": list,
        "sources": list,
        "uncertainty": list,
        "pending_human": list,
        "change_log": list,
        "limitations": list,
        "errors": list,
        "degradations": list,
        "runtime": dict,
    }
    missing = [k for k in required if k not in result]
    add("FAIL", not missing, "S03", f"required fixed fields present (missing: {missing or 'none'})")

    # 4. contract fields all present
    meta, docs, _ = read_ledger(ctx)
    contract = meta.get("contract") or {}
    missing_contract = [k for k in contract if k not in result]
    add("FAIL", not missing_contract, "S04", f"output_contract fields backfilled (missing: {missing_contract or 'none'})")

    # 5. enums valid
    bad = []
    if result.get("status") not in STATUS_ENUM:
        bad.append(f"status={result.get('status')}")
    for e in result.get("evidence_map") or []:
        if e.get("status") not in EVIDENCE_STATUS:
            bad.append(f"evidence status={e.get('status')}")
        if e.get("confidence") not in CONFIDENCE:
            bad.append(f"confidence={e.get('confidence')}")
    for tt in (result.get("time_trend") or {}).get("claims") or []:
        if tt.get("support") not in SUPPORT:
            bad.append(f"support={tt.get('support')}")
    for g in (result.get("research_gaps") or {}).get("gaps") or []:
        if g.get("type") not in GAP_TYPES:
            bad.append(f"gap type={g.get('type')}")
    for ev in (result.get("topic_evolution") or {}).get("events") or []:
        if ev.get("type") not in EVENT_TYPES:
            bad.append(f"event type={ev.get('type')}")
    for s in (result.get("representative_works") or {}).get("stages") or []:
        for w in s.get("works") or []:
            if w.get("kind") not in WORK_KINDS:
                bad.append(f"work kind={w.get('kind')}")
    add("FAIL", not bad, "S05", f"enums valid (bad: {bad or 'none'})")

    # 6. evidence ids unique
    ids = [e.get("id") for e in result.get("evidence_map") or [] if e.get("id")]
    add("FAIL", len(ids) == len(set(ids)), "S06", "evidence ids unique")

    # 7. every non-empty quote is verbatim-locatable in its source text
    texts = collect_quote_sources(ctx, docs)
    unlocatable = []
    for e in result.get("evidence_map") or []:
        quote = (e.get("quote") or "").strip()
        if not quote:
            continue
        src = e.get("source_file") or ""
        hay = texts.get(src)
        if hay is None:
            unlocatable.append(f"{e.get('id')} source missing: {src}")
        elif subseq_find(hay, quote) < 0:
            unlocatable.append(f"{e.get('id')} quote not found in {src}")
    add("FAIL", not unlocatable, "S07", f"verbatim quotes locatable (unlocatable: {unlocatable or 'none'})")

    # 8. claim evidence_ids resolve to evidence_map ids
    ev_ids = set(ids)
    dangling = []
    def check_claims(clist):
        for c in clist or []:
            for eid in c.get("evidence_ids") or []:
                if eid not in ev_ids:
                    dangling.append(eid)
    check_claims((result.get("time_trend") or {}).get("claims"))
    check_claims((result.get("topic_evolution") or {}).get("claims"))
    for ev in (result.get("topic_evolution") or {}).get("events") or []:
        for eid in ev.get("evidence_ids") or []:
            if eid not in ev_ids:
                dangling.append(eid)
    for g in (result.get("research_gaps") or {}).get("gaps") or []:
        for eid in g.get("evidence_ids") or []:
            if eid not in ev_ids:
                dangling.append(eid)
    add("FAIL", not dangling, "S08", f"claim evidence_ids resolve (dangling: {dangling or 'none'})")

    # 9. year distribution consistent with sources (soft)
    dist = (result.get("time_trend") or {}).get("distribution") or []
    total = sum(d.get("count", 0) for d in dist)
    parsed = (result.get("runtime") or {}).get("documents_parsed", 0)
    add("WARN", total <= parsed, "S09", f"distribution counts consistent (distribution={total}, parsed={parsed})")

    # 10. if status ok, four products must be complete
    if result.get("status") == "ok":
        add("FAIL", products_complete(result), "S10", "status=ok requires four non-empty products")
    else:
        add("PASS", True, "S10", "status!=ok; four-product completeness not enforced")

    # 11. change_log records init at least
    actions = [c.get("action") for c in result.get("change_log") or []]
    add("FAIL", "init" in actions, "S11", "change_log records init step")

    # 12. no fabricated benchmark score / prepared answer markers
    forbidden = ("benchmark_score", "whiteboard_score", "prepared_answer")
    leaked = [k for k in forbidden if k in result]
    add("FAIL", not leaked, "S12", f"no fabricated score keys (found: {leaked or 'none'})")

    summary = {
        "schema": "selftest",
        "passed": not hard_fail,
        "checks": checks,
        "summary": "all hard checks passed" if not hard_fail else "one or more hard checks failed",
    }
    write_json(ctx.selftest_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not hard_fail else 1


def cmd_selftest(ctx: Context) -> int:
    return run_selftest(ctx)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Research-frontier-trend skill pipeline (evidence ledger + compiler).",
    )
    p.add_argument("cmd", choices=["init", "extract", "compile", "selftest", "status"])
    p.add_argument("--input", default=DEFAULT_INPUT, help="input dir (default /input)")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir (default /app/output)")
    p.add_argument("--work", default=DEFAULT_WORK, help="work dir (default /app/work)")
    p.add_argument("--force", action="store_true", help="force re-init")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Context(args.input, args.output, args.work)
    handlers = {
        "init": lambda: cmd_init(ctx, args.force),
        "extract": lambda: cmd_extract(ctx),
        "compile": lambda: cmd_compile(ctx),
        "selftest": lambda: cmd_selftest(ctx),
        "status": lambda: cmd_status(ctx),
    }
    return handlers[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
