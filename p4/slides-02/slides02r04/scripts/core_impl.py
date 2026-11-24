"""Shared, dependency-free helpers for academic-slide-evidence.

All paths are resolved from INPUT_DIR / WORK_DIR / OUTPUT_DIR environment
variables with POSIX runtime defaults, so callers never depend on the current
working directory. This module uses only the Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# --- status vocabulary ------------------------------------------------------
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
VALID_STATUS = (STATUS_COMPLETE, STATUS_PARTIAL, STATUS_FAILED)

# --- evidence vocabulary ----------------------------------------------------
CLAIM_KINDS = ("fact", "inference", "design_choice")
CONFIDENCE_LEVELS = ("confirmed", "paraphrased", "inferred", "unverified")

# Used only when /input/case.json has no output_contract. Field names are
# overridden by the runtime contract when one exists.
DEFAULT_CONTRACT = {
    "presentation_file": None,
    "preview_files": [],
    "speaker_notes": None,
    "page_plan": [],
    "evidence_map": [],
    "version_records": [],
}

# Reserved top-level result fields, added after echoing the contract.
RESERVED_FIELDS = {
    "status": STATUS_PARTIAL,
    "sources": [],
    "uncertainty": {
        "unresolved": [],
        "limitations": [],
        "missing_artifacts": [],
        "degradations": [],
        "notes": "",
    },
    "pending_human": [],
    "change_log": [],
    "version_records": [],
}

MAX_INPUT_FILE_BYTES = 2 * 1024 * 1024


def now_iso() -> str:
    """Local, timezone-aware ISO-8601 timestamp (second precision)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dir_from_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def input_dir() -> Path:
    return dir_from_env("INPUT_DIR", "/input")


def work_dir() -> Path:
    return dir_from_env("WORK_DIR", "/app/work")


def output_dir() -> Path:
    return dir_from_env("OUTPUT_DIR", "/app/output")


def read_text(path, max_bytes: int = MAX_INPUT_FILE_BYTES):
    """Read UTF-8 text with BOM/bad-encoding tolerance. Returns (text, error)."""
    p = Path(path)
    try:
        if not p.exists():
            return "", "missing: {}".format(p)
        size = p.stat().st_size
        if size > max_bytes:
            return "", "too large ({} bytes > {})".format(size, max_bytes)
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "utf-16"):
            try:
                return raw.decode(enc), None
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), "decode fallback used"
    except OSError as exc:
        return "", "read error: {}".format(exc)


def load_json(path):
    """Load JSON with BOM tolerance; returns (obj_or_None, error_or_None)."""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "bad json: {}".format(exc)


def save_json(path, obj):
    """Atomic write: write a temp file then os.replace, never half-written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def load_result(out_dir=None):
    out = Path(out_dir) if out_dir else output_dir()
    return load_json(out / "result.json")


# --- contract handling ------------------------------------------------------
def extract_contract(case):
    """Return output_contract as a dict, or the default when absent/invalid."""
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            return dict(oc)
    return dict(DEFAULT_CONTRACT)


def find_case_id(case):
    """Locate the case id under common key spellings; 'unknown' if missing."""
    if not isinstance(case, dict):
        return "unknown"
    for key in ("case_id", "caseId", "id", "challenge_id"):
        value = case.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        return str(value)
    return "unknown"


def build_skeleton(case, errors=None):
    """Build a legal partial result skeleton that echoes the whole contract."""
    errors = list(errors or [])
    case = case if isinstance(case, dict) else {}
    case_id = find_case_id(case)
    if case_id == "unknown":
        errors.append("case_id not found in case.json; used 'unknown'")

    contract = extract_contract(case)
    skeleton = {"case_id": case_id}
    # Echo every contract field verbatim at top level.
    for key, value in contract.items():
        if key != "case_id":
            skeleton[key] = value

    # Reserved fields fill only where not already supplied by the contract.
    for key, value in RESERVED_FIELDS.items():
        if key not in skeleton:
            skeleton[key] = _clone(value)
    skeleton["status"] = STATUS_PARTIAL  # controlled field
    skeleton["runtime"] = {
        "started_at": now_iso(),
        "ended_at": None,
        "budget_seconds": 900,
        "budget_requests": 64,
        "tokens_per_response": 16384,
        "contract_keys": sorted(contract.keys()),
    }
    skeleton["self_check"] = None
    if errors:
        notes = skeleton["uncertainty"].get("notes", "")
        skeleton["uncertainty"]["notes"] = _join_notes(notes, errors)
    append_change_log(
        skeleton, "scaffold", "init_partial_skeleton", STATUS_PARTIAL,
        "落盘合法 partial 骨架，后续仅做增量改进",
    )
    return skeleton


def _clone(value):
    return json.loads(json.dumps(value))


def _join_notes(existing, new_items):
    parts = [existing.strip()] if existing and existing.strip() else []
    parts.extend(str(x).strip() for x in new_items if str(x).strip())
    return "; ".join(parts)


def append_change_log(result, step, action, status, reason):
    entries = result.setdefault("change_log", [])
    if not isinstance(entries, list):
        entries = []
        result["change_log"] = entries
    entries.append({
        "t": now_iso(),
        "step": step,
        "action": action,
        "status": status,
        "reason": reason,
    })


def append_degradation(result, what, evidence, attempted, failure, fallback):
    unc = result.setdefault("uncertainty", {})
    deg = unc.setdefault("degradations", [])
    if not isinstance(deg, list):
        deg = []
        unc["degradations"] = deg
    deg.append({
        "t": now_iso(),
        "what": what,
        "evidence": evidence,
        "attempted": attempted,
        "failure": failure,
        "fallback": fallback,
    })


# --- evidence validation ----------------------------------------------------
def make_source(ref, loc, kind, quote=None, used_in=None):
    return {
        "ref": ref,
        "loc": loc,
        "kind": kind,
        "quote": quote or None,
        "used_in": list(used_in or []),
    }


def default_confidence(kind, quote):
    if kind == "fact":
        return "confirmed" if (quote or "").strip() else "unverified"
    if kind == "inference":
        return "confirmed" if (quote or "").strip() else "inferred"
    return None  # design_choice carries no source-confidence


def make_evidence(page, role, claim, refs=None, loc=None, quote=None,
                  kind="fact", confidence=None, reason=None):
    kind = kind if kind in CLAIM_KINDS else "fact"
    if confidence not in CONFIDENCE_LEVELS:
        confidence = default_confidence(kind, quote)
    item = {
        "page": page,
        "role": role,
        "claim": claim,
        "claim_kind": kind,
        "confidence": confidence,
        "refs": list(refs or []),
        "locator": loc or None,
        "quote": (quote or None),
        "reason": reason or None,
    }
    return item, validate_evidence(item)


def validate_evidence(item):
    """Return a list of problems; empty list means the item may enter a page."""
    problems = []
    if not (item.get("claim") or "").strip():
        problems.append("empty claim")
    kind = item.get("claim_kind", "fact")
    quote = (item.get("quote") or "").strip()
    if kind == "fact" and not quote:
        problems.append("fact claim requires a verbatim quote")
    if kind == "inference" and not item.get("refs"):
        problems.append("inference requires refs")
    if kind == "inference" and not quote and not item.get("locator"):
        problems.append("inference requires quote or locator")
    if item.get("confidence") == "confirmed" and not quote:
        problems.append("confidence=confirmed requires a quote")
    if item.get("confidence") == "unverified":
        problems.append("unverified must go to pending_human, not a page claim")
    return problems


# --- file inventory & version hashes ---------------------------------------
def scan_input(root):
    root = Path(root)
    if not root.exists():
        return [], ["input dir missing: {}".format(root)]
    items = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            size = p.stat().st_size
            rel = str(p.relative_to(root))
            status = "ok" if size <= MAX_INPUT_FILE_BYTES else "skipped_too_large"
            items.append({"path": rel, "bytes": size, "status": status})
    return items, []


def file_digest(path):
    """Return {path, bytes, sha256, mtime} for reproducibility records."""
    p = Path(path)
    try:
        if not p.is_file():
            return {"path": str(p), "exists": False}
        data = p.read_bytes()
        return {
            "path": str(p),
            "exists": True,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mtime": p.stat().st_mtime,
        }
    except OSError as exc:
        return {"path": str(p), "exists": False, "error": str(exc)}


# --- self test --------------------------------------------------------------
def run_selftest():
    """Deterministic self-test of the core helpers, no external input needed."""
    lines = []
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        if not ok:
            failures += 1
        lines.append("{} {} {}".format("PASS" if ok else "FAIL", name, detail))

    # 1. contract echo keeps unknown keys and overrides nothing unexpected.
    case = {
        "case_id": "c1",
        "output_contract": {"presentation_file": None, "custom_field": [1, 2]},
    }
    sk = build_skeleton(case)
    check("contract echoes unknown key", sk.get("custom_field") == [1, 2])
    check("contract keys recorded", sk["runtime"]["contract_keys"] ==
          ["custom_field", "presentation_file"])
    check("case_id echoed", sk["case_id"] == "c1")
    check("status starts partial", sk["status"] == STATUS_PARTIAL)

    # 2. missing contract falls back to defaults.
    sk2 = build_skeleton({"case_id": "c2"})
    check("default contract applied", sk2.get("evidence_map") == [] and
          sk2.get("presentation_file") is None)

    # 3. missing case id degrades, not crashes.
    sk3 = build_skeleton({})
    check("missing case id degrades", sk3["case_id"] == "unknown" and
          sk3["uncertainty"]["notes"])

    # 4. bad JSON returns an error, does not raise.
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        obj, err = load_json(bad)
        check("bad json handled", obj is None and "bad json" in err)

    # 5. evidence validation enforces quotes for facts.
    item, problems = make_evidence(
        1, "result", "n=42", refs=["Fig 1"], loc="p.3", kind="fact")
    check("fact without quote rejected", problems and any(
        "quote" in p for p in problems), str(problems))
    item2, problems2 = make_evidence(
        1, "result", "n=42", refs=["Fig 1"], loc="p.3",
        quote="n = 42 (p.3)", kind="fact")
    check("fact with quote accepted", not problems2)
    item3, problems3 = make_evidence(
        2, "design", "图3先于图4", kind="design_choice", reason="时序因果")
    check("design_choice needs no quote", not problems3)
    unverified_problems = validate_evidence(
        make_evidence(3, "result", "x", quote="", kind="fact")[0])
    check("unverified flagged", any(
        "unverified must go" in p for p in unverified_problems),
        str(unverified_problems))

    # 6. idempotence: same skeleton modulo runtime.started_at.
    a = build_skeleton({"case_id": "c1", "output_contract": {"x": 1}})
    b = build_skeleton({"case_id": "c1", "output_contract": {"x": 1}})
    a.pop("runtime"); b.pop("runtime")
    check("skeleton idempotent", a == b)

    # 7. atomic save/load roundtrip.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "result.json"
        save_json(p, {"case_id": "c1", "status": "partial"})
        obj, err = load_json(p)
        check("save/load roundtrip", err is None and obj["case_id"] == "c1")

    # 8. empty input dir reports honestly.
    with tempfile.TemporaryDirectory() as td:
        items, errs = scan_input(Path(td) / "nope")
        check("missing input dir reported", items == [] and bool(errs))

    lines.append("selftest {} ({} failures)".format(
        "passed" if failures == 0 else "FAILED", failures))
    return lines, failures
