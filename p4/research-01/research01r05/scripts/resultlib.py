#!/usr/bin/env python3
"""Contract-first deep-research result library (Python standard library only).

This module owns the "single source of truth" for result.json:
  1. discover the input directory without assuming fixed file names;
  2. locate and normalize the case's output_contract (or output_schema /
     result_schema / result_contract equivalents);
  3. build a valid partial skeleton that round-trips every contract field,
     filling missing values with null / [] / {} instead of dropping them;
  4. merge model payloads incrementally while keeping contract fields intact;
  5. validate the result and run the six-point coverage check.

Design rule: the model writes substance, the script writes structure.
The script must never invent numeric values, DOIs, citations or scores.
"""

import copy
import json
import os
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

MAX_TEXT_BYTES = 2 * 1024 * 1024          # 2 MB per text file
TEXT_ENCODINGS = ("utf-8-sig", "gb18030", "latin-1")
TEXT_EXT = {".pdf", ".md", ".txt", ".csv", ".json", ".jsonl",
            ".xml", ".html", ".htm", ".tex", ".bib"}

CONTRACT_KEYS = ("output_contract", "output_schema", "result_schema",
                 "output_spec", "result_contract")
CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId")

# Standard result fields that are always present in addition to the case's
# own output_contract fields.
STANDARD_FIELDS = {
    "case_id": "",
    "status": STATUS_PARTIAL,
    "research_product": {
        "type": "synthesis",
        "question": "",
        "summary": "",
        "conclusions": [],
    },
    "evidence_matrix": [],
    "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
    "sources": [],
    "change_log": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime_seconds": 0,
}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Filesystem / encoding helpers
# ---------------------------------------------------------------------------

def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def read_text(path):
    """Read a UTF-8 text file with graceful encoding fallback.

    Returns (text, errors, degradations). A file larger than MAX_TEXT_BYTES is
    truncated and the truncation is recorded in degradations.
    """
    errors, degradations = [], []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", ["read_text: stat failed for %s: %s" % (path, exc)], []
    if size == 0:
        return "", [], []
    if size > MAX_TEXT_BYTES:
        degradations.append(
            "truncated text file: %s (%d bytes > %d)" % (path, size, MAX_TEXT_BYTES))
    last_exc = None
    for enc in TEXT_ENCODINGS:
        try:
            with open(path, "rb") as fh:
                raw = fh.read(size if size <= MAX_TEXT_BYTES else MAX_TEXT_BYTES)
            return raw.decode(enc), errors, degradations
        except (UnicodeDecodeError, OSError) as exc:
            last_exc = exc
    errors.append("read_text: decode failed for %s: %s" % (path, last_exc))
    return "", errors, degradations


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["write_text: cannot write %s: %s" % (path, exc)]


def load_json(path):
    """Load a JSON file tolerating BOM, bad encoding and truncation.

    Returns (obj, errors, degradations). obj is None when parsing fails.
    """
    errors, degradations = [], []
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["load_json: cannot read %s: %s" % (path, exc)], []
    if len(raw) > MAX_TEXT_BYTES:
        degradations.append(
            "truncated JSON file: %s (%d bytes > %d)" % (path, len(raw), MAX_TEXT_BYTES))
        raw = raw[:MAX_TEXT_BYTES]
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = None
    for enc in TEXT_ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None, ["load_json: cannot decode %s" % path], degradations
    try:
        return json.loads(text), errors, degradations
    except Exception as exc:  # noqa: BLE001 - report and degrade, never crash
        errors.append("load_json: parse failed for %s: %s" % (path, exc))
        return None, errors, degradations


def write_json(path, obj):
    """Atomically write a JSON object (tmp file + os.replace)."""
    try:
        ensure_dir(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return []
    except OSError as exc:
        return ["write_json: cannot write %s: %s" % (path, exc)]


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Walk the input tree and report what actually exists, never assuming names."""
    inv = {
        "exists": os.path.isdir(input_dir),
        "input_dir": input_dir,
        "total_files": 0,
        "files": [],
        "case_json": None,
        "task_md": None,
        "materials": [],
        "errors": [],
    }
    if not inv["exists"]:
        inv["errors"].append("input directory missing: %s" % input_dir)
        return inv
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, input_dir)
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = -1
            inv["files"].append({"path": fp, "rel": rel, "size": size})
            inv["total_files"] += 1
            low = fn.lower()
            if low == "case.json":
                inv["case_json"] = fp
            elif low == "task.md":
                inv["task_md"] = fp
            elif any(low.endswith(ext) for ext in TEXT_EXT):
                inv["materials"].append(fp)
    return inv


# ---------------------------------------------------------------------------
# Case id and output contract
# ---------------------------------------------------------------------------

def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, (int, float)):
            return str(val)
    return ""


def _find_contract_def(node):
    """Depth-first search for a contract-like key in nested dicts."""
    if not isinstance(node, dict):
        return None, None
    for key in CONTRACT_KEYS:
        if key in node:
            return node[key], key
    for key, val in node.items():
        if isinstance(val, dict):
            found, found_key = _find_contract_def(val)
            if found is not None:
                return found, found_key
    return None, None


def _type_name(value):
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "any"


def normalize_contract(raw):
    """Normalize a contract into (ordered_fields, specs, errors).

    Accepted forms:
      * dict of field_name -> spec (spec may be a dict or an example value);
      * list of field names (str);
      * list of spec objects carrying name / field / key / id.
    Field order is preserved as given, which keeps result.json deterministic.
    """
    errors = []
    ordered, specs = [], {}
    if raw is None:
        return ordered, specs, errors
    if isinstance(raw, dict):
        for name, spec in raw.items():
            if name in specs:
                continue
            ordered.append(name)
            if isinstance(spec, dict):
                specs[name] = spec
            else:
                specs[name] = {"type": _type_name(spec)}
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                name, spec = item, {"type": "any"}
            elif isinstance(item, dict):
                name = (item.get("name") or item.get("field")
                        or item.get("key") or item.get("id"))
                spec = dict(item)
                if not name:
                    errors.append("contract list item lacks name/field/key: %r" % (item,))
                    continue
            else:
                errors.append("unexpected contract item: %r" % (item,))
                continue
            if name not in specs:
                ordered.append(name)
                specs[name] = spec
    else:
        errors.append("output_contract must be a dict or list, got %s"
                      % type(raw).__name__)
    return ordered, specs, errors


def build_contract(case_obj):
    """Return {'raw','key','fields','specs','errors'} from a case object."""
    raw, key = _find_contract_def(case_obj)
    fields, specs, errors = normalize_contract(raw)
    return {"raw": raw, "key": key, "fields": fields, "specs": specs,
            "errors": errors}


def default_value(spec):
    """Default fill for a missing contract field, never an invented number."""
    if isinstance(spec, dict):
        if "default" in spec and spec["default"] is not None:
            return copy.deepcopy(spec["default"])
        t = spec.get("type") or spec.get("dataType") or spec.get("schema_type")
        if isinstance(t, list):
            t = t[0] if t else None
        if t:
            t = str(t).lower()
            if t in ("array", "list"):
                return []
            if t in ("object", "dict", "map"):
                return {}
            if t in ("number", "float", "integer", "int", "boolean", "bool"):
                return None
    return None


# ---------------------------------------------------------------------------
# Skeleton / merge
# ---------------------------------------------------------------------------

def new_skeleton(case_id, contract, note=""):
    """Build a valid partial result with every contract field present."""
    result = copy.deepcopy(STANDARD_FIELDS)
    result["case_id"] = case_id or ""
    result["status"] = STATUS_PARTIAL
    result["change_log"] = [{
        "ts": iso_now(),
        "action": "skeleton",
        "detail": note or "partial skeleton initialized",
        "contract_key": contract.get("key"),
    }]
    _backfill_contract(result, contract)
    return result


def _backfill_contract(result, contract):
    """Ensure every contract field exists; fill missing ones with defaults."""
    for name in contract.get("fields", []):
        if name not in result:
            result[name] = default_value(contract.get("specs", {}).get(name))
    return result


def deep_merge(base, patch):
    """Recursively merge patch into base in place; dicts merge, others replace."""
    for key, val in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            deep_merge(base[key], val)
        else:
            base[key] = copy.deepcopy(val)
    return base


# ---------------------------------------------------------------------------
# Validation and six-point coverage
# ---------------------------------------------------------------------------

def validate_result(result, contract):
    """Return a list of structural issues. Empty list means structurally valid."""
    issues = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    for name in contract.get("fields", []):
        if name not in result:
            issues.append("missing contract field: %s" % name)
    if "case_id" not in result:
        issues.append("missing case_id")
    if result.get("status") not in VALID_STATUSES:
        issues.append("invalid status: %r" % (result.get("status"),))
    ev = result.get("evidence_matrix", [])
    if not isinstance(ev, list):
        issues.append("evidence_matrix must be a list")
    else:
        for idx, row in enumerate(ev):
            if not isinstance(row, dict):
                issues.append("evidence_matrix[%d] is not an object" % idx)
                continue
            rid = row.get("id", "?")
            if not row.get("id"):
                issues.append("evidence_matrix[%d] missing id" % idx)
            if not row.get("locator"):
                issues.append("evidence_matrix[%d] (%s) missing locator" % (idx, rid))
            if not row.get("quote"):
                issues.append("evidence_matrix[%d] (%s) missing quote" % (idx, rid))
    rp = result.get("research_product", {})
    conclusions = rp.get("conclusions", []) if isinstance(rp, dict) else []
    if isinstance(conclusions, list):
        ev_ids = {row.get("id") for row in ev if isinstance(row, dict)}
        for idx, concl in enumerate(conclusions):
            if not isinstance(concl, dict):
                continue
            cid = concl.get("id", idx)
            for eid in concl.get("evidence_ids", []):
                if eid not in ev_ids:
                    issues.append(
                        "conclusion %s references missing evidence %s" % (cid, eid))
    return issues


def six_point(result, work_texts=None):
    """Six-point coverage aligned to the scoring dimensions."""
    work_texts = work_texts or {}
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    dec = (work_texts.get("decomposition.md") or "").strip()
    plan = (work_texts.get("search_plan.md") or "").strip()
    rp = result.get("research_product")
    rp = rp if isinstance(rp, dict) else {}
    conclusions = rp.get("conclusions", [])
    conclusions = conclusions if isinstance(conclusions, list) else []
    ev = result.get("evidence_matrix", [])
    ev = ev if isinstance(ev, list) else []
    unc = result.get("uncertainty")
    unc = unc if isinstance(unc, dict) else {}
    conflicts = unc.get("conflicts", [])
    conflicts = conflicts if isinstance(conflicts, list) else []
    gaps = unc.get("gaps", [])
    gaps = gaps if isinstance(gaps, list) else []
    pending = result.get("pending_human", [])
    pending = pending if isinstance(pending, list) else []

    subq_lines = [ln for ln in dec.splitlines()
                  if ln.strip().startswith(("SQ", "- SQ", "* SQ"))]
    ok1 = bool(dec) and len(subq_lines) >= 2
    add("problem_decomposition", ok1,
        "decomposition.md non-empty with >=2 subquestions"
        if ok1 else "need decomposition.md with >=2 subquestions")

    ok2 = bool(plan) and any(tok in plan for tok in
                             ("停止", "stop", "停止条件", "saturation"))
    add("search_plan", ok2,
        "search_plan.md present with stop condition"
        if ok2 else "need search_plan.md with an explicit stop condition")

    ok3 = bool(ev) and all(
        isinstance(r, dict) and r.get("locator") and r.get("quote") for r in ev)
    add("evidence_traceability", ok3,
        "every evidence row carries locator + verbatim quote"
        if ok3 else "every evidence row must carry locator + verbatim quote")

    ok4 = all(isinstance(c, dict)
              and (c.get("a") or c.get("source_a") or c.get("sources_a"))
              and (c.get("b") or c.get("source_b") or c.get("sources_b"))
              for c in conflicts)
    add("conflict_evidence", ok4,
        "each conflict names two opposing sources and a resolution"
        if conflicts else "no conflicts recorded (verify none were found in materials)")

    ok5 = bool(rp.get("question")) and bool(conclusions)
    add("synthesis_conclusions", ok5,
        "research_product has a question and >=1 conclusion"
        if ok5 else "research_product needs question + >=1 conclusion")

    ok6 = bool(gaps) or bool(pending)
    add("research_gaps", ok6,
        "explicit gaps or pending_human items present"
        if ok6 else "uncertainty.gaps / pending_human must state what is missing")

    return checks
