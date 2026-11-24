# -*- coding: utf-8 -*-
"""core_impl.py — deterministic extraction and contract validation.

This module contains only mechanical work: it reads inputs, extracts
machine-verifiable candidates (publication years, term frequencies, gap and
conflict candidate sentences, representative-work candidates) and validates the
result.json contract.  It uses only the Python standard library and writes only
to the directories passed as arguments, so it can be re-run for byte-identical
results under a fixed input.

Design invariant: every machine output is labelled ``provenance="machine"`` and
``confidence="unverified"`` (or ``low``).  The agent is responsible for
upgrading candidates with verbatim quotes and reasoning.  Nothing here
fabricates scores, citations, years or source text.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter

VERSION = "1.1.0"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DOCS = 200
MAX_QUOTE_CHARS = 360

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
SUPPORT_ENUM = ("reported", "not_reported", "inferred")
EVIDENCE_STATUS_ENUM = ("reported", "not_reported", "source_conflict", "inferred")
CONFIDENCE_ENUM = ("high", "medium", "low", "unverified")
GAP_TYPE_ENUM = ("open_question", "method_failure", "unresolved_conflict", "coverage_absence")
EVENT_TYPE_ENUM = ("emergence", "merge", "split", "decline", "terminology_drift")
WORK_KIND_ENUM = ("origin", "method", "milestone", "benchmark")
UNCERTAINTY_KIND_ENUM = (
    "coverage_gap", "year_ambiguity", "term_polysemy", "source_conflict",
    "sampling_boundary", "parse_error", "retrieval_omission",
)

_STOPWORDS = set("""
a an the and or of in on for to with without from by as is are was were be been
this that these those it its they their we our you your i he she his her not no
but if then than so such thus also however more most only very can could may
might will would should shall must do does did have has had at into over under
between among during within via et al we our study studies paper papers method
methods result results data based using used use new two one three first second
present propose proposed approach work research title abstract introduction
conclusion conclusions references appendix figure figures table tables author
authors journal volume pages doi keywords review article
""".split())

_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-9]\d)\b")
_META_YEAR_KEYS = ("year", "pub_year", "publication_year", "publish_year",
                   "date_year", "published_year")
_CITE_RE = re.compile(
    r"(?:cited\s+by|citations?|被引|引用)\s*[:：]?\s*(\d{1,6})", re.I)
_GAP_RE = re.compile(
    r"(future\s+work|future\s+research|remains?\s+(unclear|unknown|unresolved|open)|"
    r"open\s+question|remains?\s+to\s+be|little\s+is\s+known|poorly\s+understood|"
    r"understudied|not\s+yet\s+(been|established|addressed|solved|understood)|"
    r"needs?\s+further|further\s+(studies|investigation|research)|unresolved|limitation)", re.I)
_CONFLICT_RE = re.compile(
    r"(however|in\s+contrast|on\s+the\s+other\s+hand|versus\b|discrepan|contradict|"
    r"inconsistent|differs?\s+from|disagree|conflict)", re.I)
_METHOD_RE = re.compile(
    r"(we\s+(propose|present|introduce|develop|design)|novel\s+(method|framework|approach|"
    r"architecture|model)|we\s+introduce)", re.I)
_BENCH_RE = re.compile(r"(benchmark|dataset|data\s*set|evaluation|standard|corpus|suite)", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")


# --------------------------------------------------------------------------- #
# I/O helpers
# --------------------------------------------------------------------------- #
def now_utc():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file with encoding fallbacks.  Returns (text, note)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "missing:{0}".format(exc)
    if size > max_bytes:
        return None, "oversize:{0}".format(size)
    if size == 0:
        return "", "empty"
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), "ok:{0}".format(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, "error:{0}".format(exc)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "error:{0}".format(exc)
    return raw.decode("utf-8", errors="replace"), "ok:utf-8-replace"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def load_json(path):
    """Load JSON; returns (obj, note). Never raises on malformed input."""
    text, note = read_text(path)
    if text is None:
        return None, note
    try:
        return json.loads(text), "ok"
    except ValueError as exc:
        return None, "bad_json:{0}".format(exc)


def discover_files(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            out.append(os.path.join(root, name))
    return out


def relpath(path, base):
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# case.json / task.md
# --------------------------------------------------------------------------- #
def resolve_case_id(case, task_text):
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)):
                return str(val)
    m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9\-_.]+)`?", task_text or "")
    if m:
        return m.group(1)
    return "unknown"


def normalize_contract(case):
    """Echo the original output_contract verbatim; keep unknown keys."""
    if not isinstance(case, dict):
        return {}
    c = case.get("output_contract")
    if isinstance(c, dict):
        return dict(c)
    if isinstance(c, (list, str, int, float, bool)) or c is None:
        return {"_raw": c}
    return {}


# --------------------------------------------------------------------------- #
# Material discovery & text extraction
# --------------------------------------------------------------------------- #
def _pdftotext(pdf_path, out_txt):
    for cmd in (["pdftotext", "-layout", pdf_path, out_txt],
                ["pdftotext", pdf_path, out_txt]):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True, timeout=120)
            if os.path.exists(out_txt) and os.path.getsize(out_txt) > 0:
                return True
        except Exception:
            continue
    return False


def _parsed_pdf_text(path):
    """Read a .parser/<name>.jsonl parsed-PDF file if present and return text."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    txt = obj.get("text") or obj.get("content") or ""
                    page = obj.get("page") or obj.get("page_num")
                    entries.append(("[p{0}] {1}".format(page, txt) if page else txt))
    except OSError:
        return None
    return "\n".join(entries) if entries else None


def extract_materials(input_dir, work_dir):
    """Copy/parse research materials into work_dir/materials/mat_NNN.txt.

    Returns (docs, degradations).  ``docs`` is a list of dicts:
      doc_id, source (relative path), kind, note, text, text_note
    ``degradations`` lists files skipped or partially parsed.
    """
    os.makedirs(os.path.join(work_dir, "materials"), exist_ok=True)
    files = discover_files(input_dir)
    degradations = []
    docs = []

    for path in files:
        base = os.path.basename(path).lower()
        if base in ("case.json", "task.md"):
            continue  # task description / contract, not a research document
        rel = relpath(path, input_dir)
        ext = os.path.splitext(base)[1].lower()
        text = None
        note = ""
        kind = "unknown"

        if ext == ".pdf":
            kind = "pdf"
            # 1) prefer pre-parsed layer, 2) try pdftotext
            txt_file = os.path.splitext(path)[0] + ".txt"
            parsed_text = None
            if os.path.exists(txt_file):
                parsed_text, note = read_text(txt_file)
            parser_jsonl = os.path.join(
                os.path.dirname(path), ".parser", os.path.basename(path) + ".jsonl")
            if parsed_text is None and os.path.exists(parser_jsonl):
                parsed_text = _parsed_pdf_text(parser_jsonl)
                note = "ok:parser-jsonl" if parsed_text else note
            if parsed_text is None:
                out_txt = os.path.join(work_dir, "materials",
                                       "_pdftotext_{0}.tmp".format(len(docs) + 1))
                if _pdftotext(path, out_txt):
                    parsed_text, note = read_text(out_txt)
                    os.remove(out_txt)
                else:
                    degradations.append(
                        {"trigger": "pdf_unreadable", "file": rel,
                         "action": "skipped; no text layer", "status": "degraded"})
                    continue
            text = parsed_text
        elif ext in (".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html"):
            kind = ext.lstrip(".")
            text, note = read_text(path)
            if text is None:
                degradations.append(
                    {"trigger": "unreadable_file", "file": rel,
                     "action": "skipped ({0})".format(note), "status": "degraded"})
                continue
        else:
            degradations.append(
                {"trigger": "unsupported_type", "file": rel,
                 "action": "skipped, unsupported extension", "status": "ignored"})
            continue

        if len(docs) >= MAX_DOCS:
            degradations.append(
                {"trigger": "corpus_overflow", "file": rel,
                 "action": "skipped, MAX_DOCS={0} reached".format(MAX_DOCS),
                 "status": "sampled"})
            continue

        doc_id = "mat_{0:03d}".format(len(docs) + 1)
        out_path = os.path.join(work_dir, "materials", doc_id + ".txt")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text if text is not None else "")
        docs.append({"doc_id": doc_id, "source": rel, "kind": kind,
                     "note": note, "text": text or "", "text_note": note})
    return docs, degradations


# --------------------------------------------------------------------------- #
# Per-document metadata (heuristic, must be agent-verified)
# --------------------------------------------------------------------------- #
def _years_from_json(text):
    years = []
    try:
        obj = json.loads(text)
    except ValueError:
        return years
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, val in cur.items():
                if isinstance(key, str) and key.lower() in _META_YEAR_KEYS:
                    if isinstance(val, int) and 1950 <= val <= 2028:
                        years.append(val)
                    elif isinstance(val, str):
                        m = re.search(r"(19[5-9]\d|20[0-9]\d)", val)
                        if m:
                            years.append(int(m.group(1)))
                stack.append(val)
        elif isinstance(cur, list):
            stack.extend(cur)
    return years


def _pick_year(years):
    c = Counter(years)
    most = max(c.values())
    return min(y for y, n in c.items() if n == most)


def extract_year(text, fname):
    if fname.endswith((".json", ".jsonl")):
        years = _years_from_json(text)
        if years:
            return _pick_year(years), "metadata"
    for key in _META_YEAR_KEYS:
        m = re.search(r'["\']?' + re.escape(key) + r'["\']?\s*[:=]\s*["\']?(\d{4})',
                      text, re.I)
        if m:
            y = int(m.group(1))
            if 1950 <= y <= 2028:
                return y, "keyvalue"
    matches = [int(x) for x in _YEAR_RE.findall(text) if 1950 <= int(x) <= 2028]
    if matches:
        return _pick_year(matches), "heuristic"
    return None, "missing"


def extract_title(text, fname):
    if fname.endswith((".json", ".jsonl")):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for key in ("title", "Title", "paper_title"):
                if isinstance(obj.get(key), str) and obj[key].strip():
                    return obj[key].strip()[:200]
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    for key in ("title", "Title"):
                        if isinstance(item.get(key), str) and item[key].strip():
                            return item[key].strip()[:200]
    for line in text.splitlines():
        line = line.strip()
        if 12 <= len(line) <= 200 and re.search(r"[A-Za-z]", line) \
                and not line.startswith(("#", "//", "*", "-", "=")):
            if len(re.findall(r"[A-Za-z]", line)) >= 4:
                return line[:200]
    return None


def extract_citations(text):
    m = _CITE_RE.search(text)
    return int(m.group(1)) if m else None


def extract_terms(text):
    toks = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text)
    low = [t.lower() for t in toks if t.lower() not in _STOPWORDS]
    uni = Counter(low)
    bi = Counter(" ".join(p) for p in zip(low, low[1:]))
    return uni, bi


def capitalized_phrases(text):
    return Counter(p.strip() for p in re.findall(
        r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}\b", text))


def split_sentences(text):
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def line_offsets(text):
    offsets = [0]
    for m in re.finditer(r"\n", text):
        offsets.append(m.end())
    return offsets


def char_to_line(offsets, pos):
    for i in range(len(offsets) - 1):
        if offsets[i] <= pos < offsets[i + 1]:
            return i + 1
    return len(offsets)


def _candidate_entries(text, doc_id, regex, label):
    """Return list of {sentence, locator, quote} for sentences matching regex."""
    entries = []
    offsets = line_offsets(text)
    seen = set()
    for sent in split_sentences(text):
        if not regex.search(sent):
            continue
        quote = sent.strip()
        if not quote or quote in seen:
            continue
        seen.add(quote)
        if len(quote) > MAX_QUOTE_CHARS:
            quote = quote[:MAX_QUOTE_CHARS] + "…"
        pos = text.find(quote[:60])
        if pos < 0:
            pos = text.find(quote.split()[0]) if quote.split() else 0
            if pos < 0:
                continue
        line = char_to_line(offsets, pos)
        end_line = char_to_line(offsets, pos + max(1, len(quote[:120])))
        entries.append({
            "sentence": quote,
            "locator": "materials/{0}.txt#L{1}-L{2}".format(doc_id, line, end_line),
            "quote": quote,
        })
    return entries


# --------------------------------------------------------------------------- #
# prepare: discovery + skeleton
# --------------------------------------------------------------------------- #
def build_skeleton(case, task_text, docs, degradations, started_at):
    case_id = resolve_case_id(case, task_text)
    contract = normalize_contract(case)
    sources = [{"id": "S{0:03d}".format(i + 1), "path": d["source"],
                "type": d["kind"], "note": d["text_note"]}
               for i, d in enumerate(docs)]
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
        "research_question": {
            "question": "",
            "scope": "",
            "sub_questions": [],
            "domain_note": "",
        },
        "protocol": {
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "search_scope": "",
            "date_definition_note": "",
        },
        "time_trend": {
            "unit_note": "",
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
        "conflicts": [],
        "coverage": {
            "evidence_excluded": [],
            "search_omissions": [],
            "sample_boundary": "",
        },
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": started_at,
            "action": "skeleton",
            "reason": "contract requires an early honest partial result before analysis",
            "before": "none",
            "after": "partial skeleton with {0} material(s)".format(len(docs)),
        }],
        "limitations": [],
        "errors": [],
        "degradations": list(degradations),
        "runtime": {
            "seconds": 0,
            "model_requests": 0,
            "completed_steps": ["prepare"],
            "script_version": VERSION,
        },
    }
    if not docs:
        skeleton["status"] = "insufficient_evidence"
        skeleton["research_gaps"] = {
            "gaps": [{
                "id": "G001",
                "type": "coverage_absence",
                "description": "材料缺失：无法区分真实研究空白与检索遗漏。",
                "exclusion_evidence": "输入目录无可解析文本材料，见 sources 与 degradations。",
                "counter_example_check": "未执行（无材料）。",
                "evidence_ids": [],
            }],
            "excluded_not_gaps": [],
        }
        skeleton["uncertainty"].append({
            "id": "U001", "kind": "retrieval_omission", "location": "research_gaps",
            "description": "输入目录未发现可解析的科研材料。",
            "impact": "time_trend/topic_evolution/representative_works 无法建立。",
            "mitigation": "补充材料后重跑 prepare/analyze。",
        })
        skeleton["limitations"].append({
            "id": "L001", "kind": "no_materials",
            "description": "未发现可解析科研材料，四类产物无法支撑。",
            "evidence": "sources 为空。",
        })
    return skeleton


def prepare(input_dir, work_dir, output_dir, started_at=None):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    started_at = started_at or now_utc()

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case, case_note = load_json(case_path)
    task_text, task_note = (read_text(task_path) or ("", "")) if os.path.exists(task_path) else ("", "missing")

    docs, degradations = extract_materials(input_dir, work_dir)
    skeleton = build_skeleton(case, task_text, docs, degradations, started_at)

    if case_note.startswith("bad_json") or case_note.startswith("missing"):
        skeleton["errors"].append({
            "component": "case.json", "message": case_note, "recovered": True,
        })
    if not os.path.exists(task_path):
        skeleton["errors"].append({
            "component": "task.md", "message": "missing", "recovered": True,
        })
    if case_note.startswith("bad_json"):
        skeleton["status"] = "input_error" if not docs else skeleton["status"]

    manifest = {
        "case_id": skeleton["case_id"],
        "case_note": case_note,
        "task_note": task_note,
        "docs": [{k: d[k] for k in ("doc_id", "source", "kind", "text_note")}
                 for d in docs],
        "degradations": degradations,
        "started_at": started_at,
        "script_version": VERSION,
    }
    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    return skeleton, manifest


# --------------------------------------------------------------------------- #
# analyze: deterministic baseline
# --------------------------------------------------------------------------- #
def analyze(work_dir, output_dir):
    manifest_path = os.path.join(work_dir, "manifest.json")
    manifest, _ = load_json(manifest_path)
    manifest = manifest or {}
    result_path = os.path.join(output_dir, "result.json")
    result, _ = load_json(result_path)
    result = result if isinstance(result, dict) else {}

    docs_meta = []
    term_doc_freq = Counter()
    term_first = {}
    term_last = {}
    cap_doc_freq = Counter()
    all_years = []

    for d in manifest.get("docs", []):
        doc_id = d["doc_id"]
        text_path = os.path.join(work_dir, "materials", doc_id + ".txt")
        text, note = read_text(text_path)
        if text is None:
            docs_meta.append({"doc_id": doc_id, "source": d.get("source", ""),
                              "title": None, "year": None, "year_source": "unreadable",
                              "citations": None, "note": note})
            continue
        year, year_source = extract_year(text, os.path.basename(d.get("source", "")))
        if year is not None:
            all_years.append(year)
        title = extract_title(text, os.path.basename(d.get("source", "")))
        citations = extract_citations(text)
        uni, bi = extract_terms(text)
        for term, n in uni.most_common(40):
            term_doc_freq[term] += 1
            if year is not None:
                term_first.setdefault(term, year)
                term_last[term] = year
        for term, n in bi.most_common(20):
            term_doc_freq[term] += 1
            if year is not None:
                term_first.setdefault(term, year)
                term_last[term] = year
        for phrase, n in capitalized_phrases(text).most_common(30):
            cap_doc_freq[phrase.lower()] += 1
        docs_meta.append({"doc_id": doc_id, "source": d.get("source", ""),
                          "title": title, "year": year, "year_source": year_source,
                          "citations": citations, "note": note})

    # ---- year distribution ----
    year_counts = Counter(y for y in all_years if y is not None)
    distribution = [{"year": str(y), "count": year_counts[y],
                     "doc_ids": sorted(
                         dm["doc_id"] for dm in docs_meta if dm["year"] == y)}
                    for y in sorted(year_counts)]
    missing_year_docs = [dm["doc_id"] for dm in docs_meta if dm["year"] is None]
    year_ambiguity = [dm for dm in docs_meta if dm.get("year_source") == "heuristic"]

    # ---- term table ----
    top_terms = sorted(
        ((t, f, term_first.get(t), term_last.get(t))
         for t, f in term_doc_freq.items() if f >= 2),
        key=lambda x: (-x[1], x[0]))[:50]
    term_mapping_machine = [{
        "canonical": t, "variants": [], "first_year": str(f) if f else None,
        "last_year": str(l) if l else None, "ambiguity_note": "machine-candidate",
    } for t, _, f, l in top_terms]

    # ---- gap / conflict candidates ----
    gap_entries = []
    conflict_entries = []
    for d in manifest.get("docs", []):
        text_path = os.path.join(work_dir, "materials", d["doc_id"] + ".txt")
        text, _ = read_text(text_path)
        if not text:
            continue
        for e in _candidate_entries(text, d["doc_id"], _GAP_RE, "gap"):
            gap_entries.append(e)
        for e in _candidate_entries(text, d["doc_id"], _CONFLICT_RE, "conflict"):
            conflict_entries.append(e)

    # ---- representative-work candidates ----
    by_kind = {"origin": [], "method": [], "milestone": [], "benchmark": []}
    ranked = sorted(docs_meta, key=lambda x: (x["year"] is None, x["year"] or 0))
    if ranked:
        by_kind["origin"].append(ranked[0])
        if len(ranked) > 1:
            by_kind["origin"].append(ranked[1])
    for dm in docs_meta:
        text_path = os.path.join(work_dir, "materials", dm["doc_id"] + ".txt")
        text, _ = read_text(text_path)
        if not text:
            continue
        if _METHOD_RE.search(text) and len(by_kind["method"]) < 5:
            by_kind["method"].append(dm)
        if _BENCH_RE.search(text) and len(by_kind["benchmark"]) < 5:
            by_kind["benchmark"].append(dm)
    cited = [dm for dm in docs_meta if dm.get("citations") is not None]
    cited.sort(key=lambda x: -x["citations"])
    by_kind["milestone"] = cited[:5]

    # ---- machine evidence map ----
    machine_evidence = []
    eid = 0
    for label, entries in (("gap_candidate", gap_entries),
                           ("conflict_candidate", conflict_entries)):
        for e in entries:
            eid += 1
            machine_evidence.append({
                "id": "M{0:03d}".format(eid),
                "claim": "{0}: {1}".format(label, e["quote"][:120]),
                "locator": e["locator"],
                "quote": e["quote"],
                "source_file": os.path.basename(e["locator"].split("#")[0]),
                "status": "reported",
                "confidence": "unverified",
                "provenance": "machine",
                "kind": label,
            })

    # ---- machine uncertainty / limitations ----
    machine_uncertainty = []
    machine_limitations = []
    if year_ambiguity:
        ids = [d["doc_id"] for d in year_ambiguity]
        machine_uncertainty.append({
            "id": "U-year-heuristic", "kind": "year_ambiguity",
            "location": "time_trend.distribution",
            "description": "以下文档年份为文本启发式推断（非题录元数据）：{0}".format(", ".join(ids)),
            "impact": "年度分布可能偏移；不得据此宣称精确拐点。",
            "mitigation": "以题录/DOI/页眉核实年份后改写 year_source。",
        })
    if missing_year_docs:
        machine_uncertainty.append({
            "id": "U-year-missing", "kind": "year_ambiguity",
            "location": "time_trend.unit_note",
            "description": "以下文档缺年份：{0}".format(", ".join(missing_year_docs)),
            "impact": "这些文档不计入年度分布，可能低估对应时段产出。",
            "mitigation": "补充题录或人工补年份，并记入 pending_human。",
        })
        result.setdefault("pending_human", []).append({
            "item": "补全文档年份：" + ", ".join(missing_year_docs),
            "reason": "文本无年份元数据，不猜测。",
        })
    if not all_years:
        machine_limitations.append({
            "id": "L-no-years", "kind": "missing_year",
            "description": "所有文档均无年份，年度分布为空。",
            "evidence": "year_counts 为空。",
        })
    if manifest.get("degradations"):
        machine_limitations.append({
            "id": "L-degradations", "kind": "degraded_inputs",
            "description": "有 {0} 项输入降级，见 degradations。".format(len(manifest["degradations"])),
            "evidence": "manifest.degradations",
        })
    if not docs_meta:
        machine_limitations.append({
            "id": "L-no-docs", "kind": "no_materials",
            "description": "无可解析文档，四类产物无法建立。", "evidence": "docs 为空。",
        })

    # ---- write machine baseline into result ----
    machine_time = {
        "unit_note": ("年份口径未定：在线发表/见刊/预印本需按材料题录确认"
                      if all_years else "无出版年份元数据，待人工。"),
        "distribution": distribution,
        "windows": [],
        "claims": [],
    }
    machine_topic = {
        "stage_divisions": [],
        "term_mapping": term_mapping_machine,
        "events": [],
        "claims": [],
    }
    machine_repr = {
        "criteria": [],
        "stages": [{
            "stage": "machine-candidates", "period": "unspecified", "basis":
            "kind 启发式候选（origin=最早、method=方法首创词、milestone=被引、benchmark=基准词）",
            "works": [
                {"doc_id": dm["doc_id"], "title": dm.get("title") or "",
                 "year": str(dm["year"]) if dm.get("year") is not None else None,
                 "rationale": "machine-candidate, kind={0}".format(kind),
                 "kind": kind,
                 "citation_evidence": ("cited_by={0}".format(dm["citations"])
                                       if dm.get("citations") is not None else "")}
                for kind, dms in by_kind.items() for dm in dms],
        }],
    }
    machine_gaps = result.get("research_gaps", {"gaps": [], "excluded_not_gaps": []})
    # preserve skeleton gap on no-materials case
    if docs_meta and not machine_gaps.get("gaps"):
        machine_gaps = {
            "gaps": [], "excluded_not_gaps": [],
            "_note": "gap 候选句已写入 evidence_map（M 编号）；请由 Agent 核验后升为正式空白。",
        }

    # preserve agent content where present; overwrite machine-only fields
    result.setdefault("time_trend", machine_time)
    result["time_trend"]["distribution"] = machine_time["distribution"]
    result["time_trend"]["unit_note"] = result["time_trend"].get("unit_note") \
        or machine_time["unit_note"]

    result.setdefault("topic_evolution", machine_topic)
    result["topic_evolution"]["term_mapping"] = machine_topic["term_mapping"]
    result.setdefault("representative_works", machine_repr)
    result["representative_works"]["stages"] = machine_repr["stages"]
    result.setdefault("research_gaps", machine_gaps)

    # merge evidence: keep non-machine entries, replace machine entries
    kept = [e for e in result.get("evidence_map", [])
            if e.get("provenance") != "machine"]
    result["evidence_map"] = kept + machine_evidence

    # merge uncertainty/limitations: keep agent entries, append machine ids once
    for section, entries in (("uncertainty", machine_uncertainty),
                             ("limitations", machine_limitations)):
        existing = result.setdefault(section, [])
        ids = {e.get("id") for e in existing if isinstance(e, dict)}
        for e in entries:
            if e.get("id") not in ids:
                existing.append(e)
                ids.add(e.get("id"))

    result["degradations"] = list(manifest.get("degradations", []))
    result.setdefault("change_log", []).append({
        "time": now_utc(), "action": "analyze",
        "reason": "确定性机械基线（年份/术语/候选句/代表作候选）",
        "before": "skeleton", "after": "machine baseline written",
    })
    rt = result.setdefault("runtime", {})
    rt["completed_steps"] = sorted(set(rt.get("completed_steps", []) + ["analyze"]))
    rt["script_version"] = VERSION

    write_json(result_path, result)

    # ---- auxiliary editable artifacts ----
    doc_rows = [{"doc_id": dm["doc_id"], "source": dm["source"],
                 "title": dm.get("title") or "", "year": dm.get("year") or "",
                 "year_source": dm.get("year_source") or "",
                 "citations": dm.get("citations") or ""}
                for dm in docs_meta]
    _write_csv(os.path.join(output_dir, "tables", "documents.csv"),
               ["doc_id", "source", "title", "year", "year_source", "citations"],
               doc_rows)
    _write_csv(os.path.join(output_dir, "tables", "year_distribution.csv"),
               ["year", "count", "doc_ids"],
               [{"year": d["year"], "count": d["count"],
                 "doc_ids": ";".join(d["doc_ids"])} for d in distribution])
    _write_csv(os.path.join(output_dir, "tables", "term_mapping.csv"),
               ["canonical", "first_year", "last_year"],
               [{"canonical": t["canonical"],
                 "first_year": t["first_year"] or "",
                 "last_year": t["last_year"] or ""} for t in term_mapping_machine])

    evidence_map = {e["id"]: e for e in result["evidence_map"]}
    write_json(os.path.join(output_dir, "evidence_map.json"), evidence_map)
    write_json(os.path.join(output_dir, "report.md"), _report_md(result, docs_meta))
    return result


def _write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def _report_md(result, docs_meta):
    lines = ["# 机械基线报告（machine-generated）",
             "",
             "本报告由 scripts/main.py analyze 确定性生成，所有候选待 Agent 核验后升格；",
             "未核验项一律 confidence=unverified。",
             "",
             "## 年度分布",
             ""]
    dist = result.get("time_trend", {}).get("distribution", [])
    if dist:
        lines.append("| year | count | doc_ids |")
        lines.append("|-|-|-|")
        for d in dist:
            lines.append("| {0} | {1} | {2} |".format(
                d["year"], d["count"], ", ".join(d["doc_ids"])))
    else:
        lines.append("（无年度分布：见 time_trend.unit_note 与 uncertainty）")
    lines += ["", "## 文档清单", "", "| doc_id | source | title | year | year_source | citations |",
              "|-|-|-|-|-|-|"]
    for dm in docs_meta:
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} |".format(
            dm["doc_id"], dm["source"], (dm.get("title") or "")[:60],
            dm.get("year") or "", dm.get("year_source") or "", dm.get("citations") or ""))
    lines += ["", "## 状态", "", "- status: `{0}`".format(result.get("status")),
              "- sources: {0}".format(len(result.get("sources", []))),
              "- evidence_map: {0}".format(len(result.get("evidence_map", []))),
              "- uncertainty: {0}".format(len(result.get("uncertainty", []))),
              "- limitations: {0}".format(len(result.get("limitations", []))),
              "- degradations: {0}".format(len(result.get("degradations", []))),
              ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# self-check validation
# --------------------------------------------------------------------------- #
def check_contract(result, materials_dir=None):
    """Return a list of findings: {level, check, ok, message}."""
    findings = []
    def add(level, check, ok, message):
        findings.append({"level": level, "check": check, "ok": ok,
                         "message": message})

    if not isinstance(result, dict):
        return [{"level": "error", "check": "result_json", "ok": False,
                 "message": "result.json is not a JSON object"}]

    add("error", "case_id", bool(result.get("case_id")),
        "case_id present" if result.get("case_id") else "case_id missing")
    add("error", "status_enum", result.get("status") in STATUS_ENUM,
        "status valid: {0}".format(result.get("status")))
    add("error", "output_contract", "output_contract" in result,
        "output_contract echoed" if "output_contract" in result
        else "output_contract missing")
    add("error", "sources", isinstance(result.get("sources"), list),
        "sources is a list")
    add("error", "uncertainty", isinstance(result.get("uncertainty"), list),
        "uncertainty is a list")
    add("error", "change_log", isinstance(result.get("change_log"), list)
        and len(result.get("change_log", [])) > 0,
        "change_log non-empty")
    add("error", "limitations", isinstance(result.get("limitations"), list),
        "limitations is a list")

    # four products present
    for field in ("time_trend", "topic_evolution", "representative_works",
                  "research_gaps"):
        ok = isinstance(result.get(field), dict)
        add("error" if ok else "warn", "product_" + field, ok,
            "{0} present as object".format(field))

    # enum checks on evidence_map
    for e in result.get("evidence_map", []):
        if not isinstance(e, dict):
            add("warn", "evidence_shape", False, "evidence entry not an object")
            continue
        if e.get("status") not in EVIDENCE_STATUS_ENUM:
            add("warn", "evidence_status_enum", False,
                "bad status in {0}".format(e.get("id")))
        if e.get("confidence") not in CONFIDENCE_ENUM:
            add("warn", "evidence_confidence_enum", False,
                "bad confidence in {0}".format(e.get("id")))
        if e.get("status") == "reported" and not (e.get("quote") or "").strip():
            add("warn", "evidence_quote", False,
                "reported evidence {0} lacks quote".format(e.get("id")))
        if e.get("status") in ("reported", "source_conflict") and \
                not (e.get("locator") or "").strip():
            add("warn", "evidence_locator", False,
                "evidence {0} lacks locator".format(e.get("id")))

    # quote locatability (only when materials dir is available)
    if materials_dir and os.path.isdir(materials_dir):
        for e in result.get("evidence_map", []):
            if not isinstance(e, dict):
                continue
            quote = (e.get("quote") or "").strip()
            src = (e.get("source_file") or "").strip()
            if not quote or not src:
                continue
            target = os.path.join(materials_dir, src)
            if not os.path.exists(target):
                add("warn", "quote_source_missing", False,
                    "source_file {0} not found for {1}".format(src, e.get("id")))
                continue
            text, _ = read_text(target)
            if text is None:
                add("warn", "quote_source_unreadable", False,
                    "cannot read {0}".format(src))
                continue
            if quote.rstrip("…") not in text:
                add("warn", "quote_not_located", False,
                    "quote of {0} not located in {1}".format(e.get("id"), src))

    # no fabricated scores
    for key in ("benchmark_score", "whiteboard", "白板", "score_total"):
        if key in result:
            add("error", "no_fabricated_score", False,
                "forbidden key present: {0}".format(key))
    rt = result.get("runtime", {})
    for key in ("score", "benchmark", "whiteboard"):
        if key in rt:
            add("error", "no_fabricated_score", False,
                "runtime contains forbidden key: {0}".format(key))

    # completeness by status
    status = result.get("status")
    if status == "ok":
        tt = result.get("time_trend", {})
        te = result.get("topic_evolution", {})
        rw = result.get("representative_works", {})
        rg = result.get("research_gaps", {})
        if not tt.get("distribution"):
            add("warn", "ok_time_trend_empty", False,
                "status=ok but time_trend.distribution empty")
        if not (te.get("stage_divisions") or te.get("term_mapping")):
            add("warn", "ok_topic_empty", False, "status=ok but topic empty")
        if not rw.get("stages"):
            add("warn", "ok_representative_empty", False,
                "status=ok but representative_works empty")
        if not rg.get("gaps"):
            add("warn", "ok_gaps_empty", False, "status=ok but gaps empty")
    return findings


# --------------------------------------------------------------------------- #
# deterministic digest (for repeatability checks)
# --------------------------------------------------------------------------- #
def digest(obj):
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def main(argv=None):
    argv = sys.argv if argv is None else argv
    return argv
