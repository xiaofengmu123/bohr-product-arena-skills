#!/usr/bin/env python3
"""Core implementation for scholar-bilingual-translator.

Standard library only. Provides deterministic bookkeeping and consistency
checks that a base agent cannot do reliably: tolerant input discovery, atomic
result.json management, evidence-map validation (locator + verbatim quote),
and post-hoc consistency checks (term variants, number drift, dangling
citations, segment alignment).
"""
import json
import os
import re
from datetime import datetime, timezone

DEFAULT_OUTPUT_CONTRACT = ["deliverables", "fact_source_map", "version_boundary"]
EVIDENCE_KINDS = ("segments", "terms", "numbers", "citations", "alignment")
KIND_TO_FACT = {
    "segments": "segment",
    "terms": "term",
    "numbers": "number",
    "citations": "citation",
    "alignment": "alignment",
}
FACT_KINDS = {"term", "number", "citation", "figure", "claim", "segment", "table", "formula"}
TEXT_EXTS = {".pdf", ".md", ".txt", ".tex", ".csv", ".json", ".yaml", ".yml", ".html", ".htm", ".docx", ".doc", ".xlsx", ".pptx"}
MAX_FILE_BYTES = 2_000_000
REQUIRED_KEYS = [
    "case_id", "status", "deliverables", "fact_source_map", "version_boundary",
    "evidence_map", "sources", "uncertainty", "pending_human", "change_log",
]

_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\d{1,3}(?:,\d{3})+|\d{1,})(?:\.\d+)?(?:[eE][+-]?\d+)?\s?(?:%|\u2030|ppm|ppb)?"
)
_SCALE_RE = re.compile(r"(\u4e07|\u4ebf|\u767e\u4e07|\u5343\u4e07|\u5341\u4ebf|\u4e07\u4ebf|million|billion|thousand)", re.IGNORECASE)
_BRACKET_CIT_RE = re.compile(r"\[[^\]\[]*\d[^\]\[]*\]")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerating BOM and bad encodings. Returns (text, err)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, f"cannot stat {path}: {exc}"
    if size > max_bytes:
        return None, f"file exceeds {max_bytes} bytes; skipped"
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, ValueError) as exc:
            last_err = exc
    return None, f"could not decode as text: {last_err}"


def load_json_safe(path):
    """Tolerant JSON loader. Returns (obj, err). Never raises on bad input."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    last_err = None
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            return json.loads(text), None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_err = exc
    return None, f"cannot parse JSON: {last_err}"


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def discover_input(input_dir):
    """Scan the input directory and build a case brief. Never raises."""
    brief = {
        "case_id": None,
        "output_contract": list(DEFAULT_OUTPUT_CONTRACT),
        "materials": [],
        "missing": [],
        "errors": [],
        "files": [],
    }
    if not os.path.isdir(input_dir):
        brief["missing"].append(input_dir)
        return brief
    case_path = None
    task_path = None
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            brief["files"].append({"path": full, "rel": rel, "size": size})
            low = name.lower()
            if low == "case.json":
                case_path = full
            elif low == "task.md":
                task_path = full
    if case_path:
        data, err = load_json_safe(case_path)
        if err:
            brief["errors"].append({"file": case_path, "error": err})
        else:
            brief["case_id"] = data.get("case_id") or data.get("id") or data.get("caseId")
            oc = data.get("output_contract")
            if isinstance(oc, list):
                brief["output_contract"] = [str(x) for x in oc]
            elif isinstance(oc, dict):
                brief["output_contract"] = [str(k) for k in oc.keys()]
            for key in ("materials", "material_paths", "sources", "files"):
                val = data.get(key)
                if isinstance(val, list):
                    for m in val:
                        if isinstance(m, dict):
                            brief["materials"].append(m.get("path") or m.get("file") or str(m))
                        else:
                            brief["materials"].append(str(m))
    else:
        brief["missing"].append("case.json")
    if task_path is None:
        brief["missing"].append("task.md")
    else:
        brief["materials"].append("task.md")
    for f in brief["files"]:
        ext = os.path.splitext(f["rel"])[1].lower()
        if ext in TEXT_EXTS and f["rel"] not in ("case.json", "task.md"):
            brief["materials"].append(f["rel"])
    return brief


def _set_path(obj, path, value):
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _get_path(obj, path, default=None):
    cur = obj
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _locator_str(loc):
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        parts = []
        for k in ("file", "path", "page", "para", "segment", "line"):
            v = loc.get(k)
            if v not in (None, ""):
                parts.append(f"{k}={v}")
        return " ".join(parts)
    return str(loc)


class ResultStore:
    """Manages result.json with atomic writes and an auto-appended change_log."""

    def __init__(self, path):
        self.path = os.path.abspath(path)

    def exists(self):
        return os.path.exists(self.path)

    def load(self):
        if not self.exists():
            return None
        data, err = load_json_safe(self.path)
        return data if err is None else None

    def _write(self, data):
        atomic_write_json(self.path, data)

    def log(self, data, action, key, detail):
        data.setdefault("change_log", []).append(
            {"time": utcnow(), "action": action, "key": key, "detail": detail}
        )

    def init(self, case_id, contract_fields=None):
        contract = contract_fields or list(DEFAULT_OUTPUT_CONTRACT)
        data = {"case_id": case_id, "status": "in_progress"}
        for f in contract:
            if f == "deliverables":
                data[f] = []
            elif f == "fact_source_map":
                data[f] = []
            elif f == "version_boundary":
                data[f] = {
                    "modifications": [], "human_confirm": [],
                    "material_gaps": [], "anomalies": [], "editable": True,
                }
            else:
                data[f] = None
        data["evidence_map"] = {k: [] for k in EVIDENCE_KINDS}
        data["sources"] = []
        data["uncertainty"] = []
        data["pending_human"] = []
        data["change_log"] = [
            {"time": utcnow(), "action": "init", "key": "result", "detail": "skeleton created"}
        ]
        self._write(data)
        return data

    def set(self, key, value):
        data = self.load()
        if data is None:
            return None, "result.json missing; run init first"
        _set_path(data, key, value)
        self.log(data, "set", key, "set field")
        self._write(data)
        return data, None

    def add_list(self, key, item):
        data = self.load()
        if data is None:
            return None, "result.json missing; run init first"
        lst = _get_path(data, key)
        if lst is None:
            lst = []
            _set_path(data, key, lst)
        if not isinstance(lst, list):
            return None, f"{key} is not a list"
        lst.append(item)
        self.log(data, "add_list", key, "appended item")
        self._write(data)
        return data, None

    def add_evidence(self, kind, item):
        if kind not in EVIDENCE_KINDS:
            return None, f"unknown evidence kind {kind!r}; use one of {sorted(EVIDENCE_KINDS)}"
        if not isinstance(item, dict):
            return None, f"{kind} item must be a JSON object"
        loc = item.get("source_locator") or item.get("locator")
        quote = item.get("source_quote") or item.get("quote")
        if not loc or not quote:
            return None, f"{kind} item requires 'source_locator' and verbatim 'source_quote'"
        if isinstance(loc, dict) and not (loc.get("file") or loc.get("path")):
            return None, f"{kind} source_locator requires 'file' or 'path' key"
        data = self.load()
        if data is None:
            return None, "result.json missing; run init first"
        em = data.setdefault("evidence_map", {})
        em.setdefault(kind, []).append(item)
        fsm = data.setdefault("fact_source_map", [])
        fsm.append({
            "item": item.get("term") or item.get("number") or item.get("citation") or item.get("id"),
            "kind": KIND_TO_FACT.get(kind, kind),
            "value": item.get("translation") or item.get("target_text") or item.get("verified"),
            "source": _locator_str(loc),
            "note": item.get("note", ""),
            "locator": loc,
            "quote": quote,
        })
        label = item.get("id") or item.get("term") or item.get("number") or item.get("citation")
        self.log(data, "add_evidence", kind, f"added {kind} item: {label}")
        self._write(data)
        return data, None

    def finalize(self, status):
        data = self.load()
        if data is None:
            return None, "result.json missing; run init first"
        if status not in ("success", "partial", "failed"):
            return None, f"invalid status {status!r}; use success|partial|failed"
        if status == "success":
            problems = []
            if not data.get("deliverables"):
                problems.append("deliverables is empty")
            fsm = data.get("fact_source_map") or []
            em = data.get("evidence_map") or {}
            if not fsm and not any(isinstance(v, list) and v for v in em.values()):
                problems.append("no fact_source_map/evidence_map entries")
            if problems:
                return None, "cannot finalize success: " + "; ".join(problems) + " (use partial/failed)"
        data["status"] = status
        self.log(data, "finalize", "status", f"finalized as {status}")
        self._write(data)
        return data, None


def extract_numbers(text):
    """Extract numeric tokens with value normalization (for drift checking)."""
    if not text:
        return []
    out = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0)
        norm = raw.replace(",", "").strip()
        pure = norm
        unit = None
        for u in ("%", "\u2030", "ppm", "ppb"):
            if pure.endswith(u):
                unit = u
                pure = pure[: -len(u)].strip()
                break
        try:
            val = float(pure)
        except (ValueError, TypeError):
            val = None
        out.append({"raw": raw, "norm": norm, "value": val, "unit": unit})
    return out


def _nums_match(a, b, rel=2e-3):
    if a is None or b is None:
        return False
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def _path_exists(p):
    if os.path.exists(p):
        return True
    if p and not os.path.isabs(p):
        for base in ("/app/output", "/app/work"):
            if os.path.exists(os.path.join(base, p)):
                return True
    return False


def _evidence_nonempty(em):
    return any(isinstance(v, list) and v for v in (em or {}).values())


def check_consistency(result_path, glossary_path, out_path):
    """Deterministic consistency check. Writes a report; returns (report, err)."""
    data, err = load_json_safe(result_path)
    if err:
        report = {"ok": False, "errors": [{"check": "result_unreadable", "detail": err}], "warnings": []}
        report["counts"] = {"errors": len(report["errors"]), "warnings": 0}
        atomic_write_json(out_path, report)
        return report, None
    glossary = {}
    if glossary_path and os.path.exists(glossary_path):
        g, gerr = load_json_safe(glossary_path)
        if not gerr and isinstance(g, dict):
            glossary = g

    errors, warnings = [], []

    # 1. deliverable files exist
    for d in data.get("deliverables", []) or []:
        p = d.get("path") if isinstance(d, dict) else None
        if p and isinstance(p, str) and not _path_exists(p):
            errors.append({"check": "deliverable_missing", "item": p})

    # 2. evidence_map: locator + verbatim quote completeness
    em = data.get("evidence_map") or {}
    for kind in EVIDENCE_KINDS:
        items = em.get(kind) if isinstance(em, dict) else None
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append({"check": "evidence_not_object", "kind": kind, "index": i})
                continue
            if not (item.get("source_locator") or item.get("locator")):
                errors.append({"check": "missing_locator", "kind": kind, "index": i, "item": item.get("id")})
            if not (item.get("source_quote") or item.get("quote")):
                errors.append({"check": "missing_quote", "kind": kind, "index": i, "item": item.get("id")})

    # 3. term variant check (corpus + per-term target_text)
    corpus_parts = []
    for d in data.get("deliverables", []) or []:
        if isinstance(d, dict) and d.get("path"):
            txt, _ = read_text_safe(d["path"], max_bytes=5_000_000)
            if txt:
                corpus_parts.append(txt)
    corpus = "\n".join(corpus_parts)
    for t in glossary.get("terms", []) or []:
        if not isinstance(t, dict):
            continue
        canonical = t.get("canonical") or t.get("translation")
        variants = [v for v in (t.get("variants") or []) if v]
        if not canonical:
            warnings.append({"check": "term_missing_translation", "term": t.get("term")})
            continue
        for v in variants:
            if v and v != canonical and v in corpus:
                warnings.append({
                    "check": "term_variant_used", "term": t.get("term"),
                    "canonical": canonical, "found": v,
                })

    # 4. number drift on segments
    for seg in em.get("segments", []) or []:
        if not isinstance(seg, dict):
            continue
        src = seg.get("source_quote") or ""
        tgt = seg.get("target_text") or ""
        tgt_vals = [n["value"] for n in extract_numbers(tgt) if n["value"] is not None]
        for n in extract_numbers(src):
            if n["value"] is None:
                continue
            if not any(_nums_match(n["value"], tv) for tv in tgt_vals):
                if _SCALE_RE.search(tgt):
                    warnings.append({"check": "number_rescaled_verify", "segment": seg.get("id"), "number": n["raw"], "target": tgt[:120]})
                else:
                    errors.append({"check": "number_missing", "segment": seg.get("id"), "number": n["raw"], "source": src[:120]})

    # 5. bracket citation presence on segments
    for seg in em.get("segments", []) or []:
        if not isinstance(seg, dict):
            continue
        src = seg.get("source_quote") or ""
        tgt = seg.get("target_text") or ""
        for cit in _BRACKET_CIT_RE.findall(src):
            if cit not in tgt:
                warnings.append({"check": "citation_missing", "segment": seg.get("id"), "citation": cit})

    # 6. segment alignment count
    src_count = sum(1 for s in em.get("segments", []) or [] if isinstance(s, dict) and s.get("source_quote"))
    tgt_count = sum(1 for s in em.get("segments", []) or [] if isinstance(s, dict) and s.get("target_text"))
    if src_count != tgt_count:
        warnings.append({"check": "segment_count_mismatch", "source_segments": src_count, "target_segments": tgt_count})

    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {"errors": len(errors), "warnings": len(warnings)},
    }
    atomic_write_json(out_path, report)

    # append to change_log (reload to stay consistent)
    store = ResultStore(result_path)
    data2 = store.load()
    if data2 is not None:
        store.log(data2, "check", "consistency", f"{len(errors)} errors, {len(warnings)} warnings")
        store._write(data2)
    return report, None


def validate_result(data):
    """Return a list of problems; empty means valid structure."""
    problems = []
    if not isinstance(data, dict):
        return ["result is not a dict"]
    for k in REQUIRED_KEYS:
        if k not in data:
            problems.append(f"missing key {k}")
    if data.get("case_id") in (None, ""):
        problems.append("case_id empty")
    if data.get("status") not in ("success", "partial", "failed", "in_progress"):
        problems.append(f"invalid status {data.get('status')!r}")
    return problems
