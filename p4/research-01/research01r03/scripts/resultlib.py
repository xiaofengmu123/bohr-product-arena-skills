#!/usr/bin/env python3
"""Deterministic runtime helpers for the deep-research skill (standard library only).

Design principle (variant focus: determinism):
  result.json must be byte-identical when the same input files and the same
  model-authored work notes are assembled twice. Wall-clock data is therefore
  never written into result.json; process/host/timing metadata goes to
  /app/work/run_metadata.json instead. JSON is serialized with sort_keys=True
  and lists are normalized into a canonical order during finalize.

All functions are pure standard library and never raise for malformed input;
  failures are collected into `errors` / `degradations` lists.
"""

import hashlib
import json
import os
import sys
import time

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB soft cap for text material

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

# Text material we attempt to decode as UTF-8 text.
TEXT_EXTS = {
    ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".tex", ".bib", ".tsv", ".yaml", ".yml",
}
# Everything we recognize as an input material artifact.
MATERIAL_EXTS = TEXT_EXTS | {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls", ".odt", ".ods", ".odp",
}

# Canonical contract fields used when case.json has no explicit output_contract.
DEFAULT_CONTRACT_FIELDS = [
    "case_id", "status", "research_product", "evidence_matrix", "uncertainty",
    "sources", "change_log", "limitations", "errors", "degradations",
    "pending_human",
]

# Field-name heuristics for choosing an empty value when a contract key has no
# example instance. Unknown keys default to null.
LIST_FIELDS = {
    "conclusions", "evidence_matrix", "sources", "conflicts", "biases", "gaps",
    "undetermined", "limitations", "errors", "degradations", "pending_human",
    "change_log",
}
DICT_FIELDS = {"research_product", "uncertainty"}

CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId")


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def canonical_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path, obj):
    errors = []
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical_json(obj))
    except Exception as exc:  # noqa: BLE001
        errors.append("write %s failed: %s" % (path, exc))
    return errors


def write_text(path, text):
    errors = []
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except Exception as exc:  # noqa: BLE001
        errors.append("write %s failed: %s" % (path, exc))
    return errors


def _decode(raw, path):
    """Return (text, degradations). Tries utf-8-sig, gb18030, latin-1."""
    degradations = []
    try:
        return raw.decode("utf-8-sig"), degradations
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "latin-1"):
        try:
            degradations.append("decoded %s with %s" % (path, enc))
            return raw.decode(enc), degradations
        except UnicodeDecodeError:
            continue
    degradations.append("fallback decoding for %s" % path)
    return raw.decode("latin-1", errors="replace"), degradations


def read_text(path):
    """Read a text file; return (text, errors, degradations). Never raises."""
    text, errors, degradations = "", [], []
    if not os.path.isfile(path):
        return "", ["missing file: %s" % path], []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            raw = fh.read()
        if size > MAX_TEXT_BYTES:
            degradations.append("text truncated: %s > %d bytes" % (path, MAX_TEXT_BYTES))
            raw = raw[:MAX_TEXT_BYTES]
        text, degradations = _decode(raw, path)
    except Exception as exc:  # noqa: BLE001
        errors.append("read %s failed: %s" % (path, exc))
    return text, errors, degradations


def load_json(path):
    """Read and parse a JSON file; return (obj, errors, degradations)."""
    obj, errors, degradations = None, [], []
    if not os.path.isfile(path):
        return None, ["missing file: %s" % path], []
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if len(raw) > MAX_TEXT_BYTES:
            degradations.append("json truncated: %s > %d bytes" % (path, MAX_TEXT_BYTES))
            raw = raw[:MAX_TEXT_BYTES]
        text, degradations = _decode(raw, path)
    except Exception as exc:  # noqa: BLE001
        return None, ["read %s failed: %s" % (path, exc)], []
    try:
        obj = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        errors.append("parse %s failed: %s" % (path, exc))
    return obj, errors, degradations


def discover_input(input_dir):
    """Walk the input tree and return a deterministic inventory dict."""
    inv = {
        "input_dir": input_dir,
        "exists": os.path.isdir(input_dir),
        "total_files": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "errors": [],
        "degradations": [],
    }
    if not inv["exists"]:
        inv["errors"].append("input directory does not exist: %s" % input_dir)
        return inv
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            inv["total_files"] += 1
            ext = os.path.splitext(name)[1].lower()
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                inv["errors"].append("stat %s failed: %s" % (rel, exc))
                size = -1
            base = name.lower()
            if base == "case.json" and inv["case_json"] is None:
                inv["case_json"] = path
            elif base == "task.md" and inv["task_md"] is None:
                inv["task_md"] = path
            elif ext in MATERIAL_EXTS:
                inv["materials"].append({"path": rel, "size": size})
    inv["materials"].sort(key=lambda m: m["path"])
    return inv


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for key in CASE_ID_KEYS:
        value = case_obj.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _contract_fields(contract):
    fields = []
    if isinstance(contract, dict):
        fields = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                fields.extend(item.keys())
    return fields


def build_contract(case_obj):
    """Collect all verbatim output_contract fields, always including canonical extras."""
    contract = {}
    if isinstance(case_obj, dict):
        for key in ("output_contract", "output_schema", "result_schema"):
            for field in _contract_fields(case_obj.get(key)):
                contract.setdefault(field, None)
    for field in DEFAULT_CONTRACT_FIELDS:
        contract.setdefault(field, None)
    return contract


def default_value(key):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    if key == "status":
        return STATUS_PARTIAL
    return None


def new_skeleton(case_id, contract, note=""):
    """Build a valid partial result with every contract key present."""
    result = {}
    for key in sorted(contract.keys()):
        result[key] = default_value(key)
    result["case_id"] = case_id
    result["status"] = STATUS_PARTIAL
    result.setdefault(
        "research_product",
        {"type": "synthesis", "question": "", "summary": "", "conclusions": []},
    )
    result.setdefault("evidence_matrix", [])
    result.setdefault(
        "uncertainty", {"conflicts": [], "biases": [], "gaps": [], "undetermined": []}
    )
    result.setdefault("sources", [])
    result.setdefault("change_log", [])
    result.setdefault("limitations", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("pending_human", [])
    result["change_log"] = [{
        "seq": 1,
        "phase": "skeleton",
        "action": "skeleton",
        "detail": note or "skeleton before research begins",
    }]
    return result


def deep_merge(base, payload):
    """Deterministic recursive merge: dicts recurse, everything else replaced."""
    for key, value in payload.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _canonical_sort_key(item):
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item)


def finalize_result(result, extra_note=""):
    """Normalize ids/ordering so the same logical content serializes identically."""
    # 1. Evidence rows: canonical order + stable ids E1..En.
    matrix = result.get("evidence_matrix")
    if not isinstance(matrix, list):
        matrix = []
    matrix = sorted(
        matrix,
        key=lambda r: (
            str(r.get("source", "")) if isinstance(r, dict) else "",
            str(r.get("locator", "")) if isinstance(r, dict) else "",
            str(r.get("quote", "")) if isinstance(r, dict) else "",
        ),
    )
    old_to_new = {}
    for i, row in enumerate(matrix):
        if isinstance(row, dict):
            old_id = row.get("id")
            new_id = "E%d" % (i + 1)
            if old_id is not None:
                old_to_new[str(old_id)] = new_id
            row["id"] = new_id
    # 2. Remap evidence_ids in conclusions.
    rp = result.get("research_product") or {}
    conclusions = rp.get("conclusions") if isinstance(rp, dict) else []
    if isinstance(conclusions, list):
        for c in conclusions:
            if isinstance(c, dict):
                c["evidence_ids"] = [
                    old_to_new.get(str(x), x) for x in (c.get("evidence_ids") or [])
                ]
    result["evidence_matrix"] = matrix
    # 3. Canonical order inside uncertainty and sources.
    u = result.get("uncertainty")
    if isinstance(u, dict):
        for key in ("conflicts", "biases", "gaps", "undetermined"):
            if isinstance(u.get(key), list):
                u[key] = sorted(u[key], key=_canonical_sort_key)
    if isinstance(result.get("sources"), list):
        result["sources"] = sorted(result["sources"], key=_canonical_sort_key)
    # 4. Append a deterministic change_log entry (no wall-clock timestamp).
    change_log = result.get("change_log")
    if isinstance(change_log, list):
        next_seq = 1
        for entry in change_log:
            if isinstance(entry, dict):
                try:
                    next_seq = max(next_seq, int(entry.get("seq", 0)) + 1)
                except (TypeError, ValueError):
                    pass
        change_log.append({
            "seq": next_seq,
            "phase": "finalize",
            "action": "finalize",
            "detail": extra_note or "deterministic finalize",
        })
    return result


def validate_result(result):
    """Return a list of problems; empty list means structurally valid."""
    problems = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    for key in ("case_id", "status", "research_product", "evidence_matrix",
                "uncertainty", "sources", "change_log"):
        if key not in result:
            problems.append("missing required key: %s" % key)
    status = result.get("status")
    if status not in (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
        problems.append("invalid status: %r" % status)
    matrix = result.get("evidence_matrix")
    evidence_ids = set()
    if isinstance(matrix, list):
        for i, row in enumerate(matrix):
            if not isinstance(row, dict):
                problems.append("evidence_matrix[%d] is not an object" % i)
                continue
            if not str(row.get("locator", "")).strip():
                problems.append("evidence_matrix[%d] missing locator" % i)
            if not str(row.get("quote", "")).strip():
                problems.append("evidence_matrix[%d] missing quote" % i)
            eid = str(row.get("id", ""))
            if not eid or eid in evidence_ids:
                problems.append("evidence_matrix[%d] duplicate/missing id" % i)
            evidence_ids.add(eid)
    rp = result.get("research_product") or {}
    conclusions = rp.get("conclusions") if isinstance(rp, dict) else []
    if isinstance(conclusions, list):
        for i, c in enumerate(conclusions):
            if not isinstance(c, dict):
                problems.append("conclusions[%d] is not an object" % i)
                continue
            if not str(c.get("text", "")).strip():
                problems.append("conclusions[%d] missing text" % i)
            for eid in (c.get("evidence_ids") or []):
                if str(eid) not in evidence_ids:
                    problems.append("conclusions[%d] references unknown evidence %r" % (i, eid))
    return problems


def coverage_six(result, work_dir):
    """Six-point coverage corresponding to the scoring dimensions."""
    def work_file(name):
        return os.path.isfile(os.path.join(work_dir, name))

    matrix = result.get("evidence_matrix") or []
    rp = result.get("research_product") or {}
    conclusions = rp.get("conclusions") if isinstance(rp, dict) else []
    u = result.get("uncertainty") or {}
    gaps = (u.get("gaps") or []) if isinstance(u, dict) else []
    ph = result.get("pending_human") or []
    loc_ok = bool(matrix) and all(
        isinstance(r, dict) and str(r.get("locator", "")).strip()
        and str(r.get("quote", "")).strip()
        for r in matrix
    )
    return {
        "decomposition": work_file("decomposition.md"),
        "search_plan": work_file("search_plan.md"),
        "evidence_locator_quote": loc_ok,
        "conflicts_listed": isinstance(u, dict) and len(u.get("conflicts") or []) > 0,
        "conclusions_nonempty": len(conclusions) > 0,
        "gaps_pending_explicit": len(gaps) > 0 or len(ph) > 0,
    }


def render_report(result):
    lines = [
        "# 深度研究结果报告",
        "",
        "- case_id: `%s`" % result.get("case_id", ""),
        "- status: `%s`" % result.get("status", ""),
    ]
    rp = result.get("research_product") or {}
    lines.append("- type: `%s`" % rp.get("type", ""))
    lines.append("- question: %s" % rp.get("question", ""))
    lines.append("")
    lines.append("## 综合结论")
    lines.append("")
    lines.append(str(rp.get("summary", "")).strip() or "(无)")
    for c in (rp.get("conclusions") or []):
        if isinstance(c, dict):
            lines.append("- [%s] %s （证据 %s；强度 %s）" % (
                c.get("id", "?"), c.get("text", ""),
                ", ".join(str(x) for x in (c.get("evidence_ids") or [])),
                c.get("strength", "?"),
            ))
    lines.append("")
    lines.append("## 证据矩阵")
    lines.append("")
    for row in (result.get("evidence_matrix") or []):
        if isinstance(row, dict):
            lines.append("- [%s] %s · %s — 「%s」" % (
                row.get("id", "?"), row.get("source", "?"),
                row.get("locator", "?"), row.get("quote", ""),
            ))
    lines.append("")
    lines.append("## 不确定性与空白")
    lines.append("")
    u = result.get("uncertainty") or {}
    for key in ("conflicts", "biases", "gaps", "undetermined"):
        lines.append("- %s: %d 条" % (key, len((u.get(key) or []) if isinstance(u, dict) else [])))
    lines.append("")
    lines.append("## 待人工确认")
    lines.append("")
    ph = result.get("pending_human") or []
    for item in ph:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 局限与降级")
    lines.append("")
    for item in (result.get("limitations") or []):
        lines.append("- %s" % item)
    for item in (result.get("errors") or []):
        lines.append("- error: %s" % item)
    for item in (result.get("degradations") or []):
        lines.append("- degradation: %s" % item)
    return "\n".join(lines) + "\n"


def render_evidence_map(result):
    return {
        "case_id": result.get("case_id", ""),
        "status": result.get("status", ""),
        "evidence_matrix": result.get("evidence_matrix", []),
        "sources": result.get("sources", []),
        "uncertainty": result.get("uncertainty", {}),
    }


def run_metadata(result):
    """Non-deterministic wall-clock metadata, kept out of result.json."""
    return {
        "case_id": result.get("case_id", ""),
        "status": result.get("status", ""),
        "host": os.environ.get("HOSTNAME", os.environ.get("COMPUTERNAME", "unknown")),
        "python": sys.version.split()[0],
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "unix_seconds": time.time(),
        "note": "wall-clock/process metadata lives here so result.json stays deterministic",
    }


def content_fingerprint(obj):
    """Deterministic sha256 of canonical JSON; used only for diagnostics."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
