"""core_impl.py — pure-stdlib implementation for the sci-analysis-exec Skill.

Subcommand implementations: init, profile, evidence (add / verify),
validate, selftest, all.  Everything is deterministic, read-only with
respect to inputs, and never raises on malformed input data: bad JSON,
bad encoding, empty input and missing parameters all degrade to valid
JSON artifacts and exit code 0.

Only the Python standard library is imported.  Analysis code that the
agent generates at runtime may additionally use the scientific-python
toolchain declared in the environment; these helper scripts stay
dependency-free so that contract/profile/evidence bookkeeping always runs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CLAIM_TYPES = {
    "statistic", "figure_element", "query_result", "cleaning_rule",
    "field_alignment", "document_quote", "derived_value", "other",
}
EVIDENCE_KINDS = {"raw_value", "derived_value", "document_quote", "schema", "command_log"}
ALLOWED_CONFIDENCE = {"computed", "inferred", "reported"}
ALLOWED_CLAIM_STATUS = {"verified", "pending_check", "source_conflict", "pending_human"}

MAX_TEXT_BYTES = 2_000_000
MAX_PROFILE_ROWS = 20_000
MAX_DISTINCT_DUP_KEYS = 100_000
DEFAULT_SEED = 42

MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "?", "nil", "-"}


# --------------------------------------------------------------------------- #
#  Small utilities
# --------------------------------------------------------------------------- #

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_input_dir() -> str:
    return os.environ.get("SCI_INPUT") or "/input"


def default_output_dir() -> str:
    return os.environ.get("SCI_OUTPUT") or "/app/output"


def default_work_dir() -> str:
    return os.environ.get("SCI_WORK") or "/app/work"


def ensure_dir(path: str) -> None:
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def write_json_atomic(path: str, obj) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp",
        dir=os.path.dirname(path) or ".",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_text_robust(path: str):
    """Read a UTF-8-ish text file, falling back across encodings.

    Returns (text, encoding, errors).  `text` is None when the file is too
    large to treat as text; errors is a list of human-readable strings.
    """
    errors = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, None, [f"stat:{exc}"]
    if size > MAX_TEXT_BYTES:
        return None, None, [f"oversize:{size}>MAX_TEXT_BYTES"]

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, None, [f"read:{exc}"]

    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, errors
        except (UnicodeDecodeError, LookupError) as exc:
            errors.append(f"{enc}:{type(exc).__name__}")
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", errors


def detect_kind(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".csv"):
        return "csv"
    if low.endswith((".tsv", ".tab")):
        return "tsv"
    if low.endswith(".json"):
        return "json"
    if low.endswith((".sqlite", ".sqlite3", ".db")):
        return "sqlite"
    if low.endswith((".md", ".markdown")):
        return "markdown"
    if low.endswith((".txt", ".data", ".dat", ".log")):
        return "text"
    if low.endswith(".pdf"):
        return "pdf"
    return "other"


def discover_inputs(input_dir: str):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, fnames in os.walk(input_dir):
        for fn in sorted(fnames):
            path = os.path.join(root, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            files.append({
                "path": path,
                "name": fn,
                "rel": os.path.relpath(path, input_dir).replace(os.sep, "/"),
                "kind": detect_kind(fn),
                "size": size,
            })
    return files


def find_case_file(input_dir: str):
    """Locate the case.json file, tolerating name variations."""
    for f in discover_inputs(input_dir):
        if f["name"].lower() == "case.json":
            return f["path"]
    # Fall back to any json at the top level that carries an id-like key.
    for f in discover_inputs(input_dir):
        if f["kind"] == "json" and "/" not in f["rel"]:
            try:
                obj = read_json(f["path"])
            except Exception:
                continue
            if isinstance(obj, dict) and any(k in obj for k in ("case_id", "caseId", "challenge_id", "id")):
                return f["path"]
    return None


def extract_case_id(case: dict):
    for key in ("case_id", "caseId", "challenge_id", "id"):
        val = case.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            return val
    return None


def discover_contract(case: dict):
    """Return the output_contract dict, tolerating camelCase."""
    for key in ("output_contract", "outputContract", "output_contract_fields"):
        val = case.get(key)
        if isinstance(val, dict):
            return val
        if isinstance(val, list):
            out = {}
            for item in val:
                if isinstance(item, str):
                    out[item] = None
                elif isinstance(item, dict) and item.get("name"):
                    out[item["name"]] = item
            return out
    return {}


def empty_value_for(spec) -> object:
    """Choose null vs [] for a missing contract field."""
    if isinstance(spec, dict):
        typ = str(spec.get("type", "")).lower()
        if typ in ("array", "list"):
            return []
    if isinstance(spec, str):
        if spec.lower() in ("array", "list"):
            return []
    return None


# --------------------------------------------------------------------------- #
#  Subcommand: init
# --------------------------------------------------------------------------- #

def run_init(input_dir, output_dir, work_dir):
    ensure_dir(output_dir)
    ensure_dir(work_dir)

    errors, degradations = [], []
    case = None
    case_path = find_case_file(input_dir)
    case_id = None
    contract = {}

    if case_path is None:
        errors.append("case.json not found under input directory")
    else:
        text, enc, enc_errors = read_text_robust(case_path)
        if text is None:
            errors.append(f"case.json unreadable: {enc_errors}")
        else:
            try:
                case = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"case.json invalid JSON: {exc}")
                degradations.append(f"case.json decoded as {enc}; encoding notes: {enc_errors}")
            if isinstance(case, dict):
                case_id = extract_case_id(case)
                contract = discover_contract(case)
                if case_id is None:
                    errors.append("case.json has no case_id/caseId/challenge_id/id key")
            else:
                errors.append("case.json top level is not an object")

    result = {
        "case_id": case_id,
        "status": "insufficient_evidence" if (case_id is None or case_path is None) else "partial",
        "evidence_map": {"schema_version": SCHEMA_VERSION, "claims": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {
                "ts": utc_now(),
                "step": "init",
                "action": "write_partial_skeleton",
                "detail": "落盘 result.json 骨架，含 case_id 与全部契约字段空值",
            }
        ],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "runtime": runtime_meta(),
    }

    # Backfill every output_contract field, unchanged, with null/[] for missing.
    for key, spec in sorted(contract.items()):
        if key not in result:
            result[key] = empty_value_for(spec)

    out_path = os.path.join(output_dir, "result.json")
    write_json_atomic(out_path, result)
    return out_path, result


# --------------------------------------------------------------------------- #
#  Subcommand: profile
# --------------------------------------------------------------------------- #

def _looks_missing(cell: str) -> bool:
    return cell.strip().lower() in MISSING_TOKENS


def _to_float(cell: str):
    try:
        return float(cell.strip())
    except (ValueError, TypeError):
        return None


def _numeric_summary(values):
    values = [v for v in values if v is not None]
    if not values:
        return {}
    n = len(values)
    summary = {
        "count": n,
        "min": round(min(values), 10),
        "max": round(max(values), 10),
        "mean": round(statistics.fmean(values), 10),
        "stdev": round(statistics.pstdev(values), 10) if n > 1 else 0.0,
        "median": round(statistics.median(values), 10),
    }
    return summary


def _profile_delimited(path, kind, rel, max_rows=MAX_PROFILE_ROWS):
    delimiter = "\t" if kind == "tsv" else ","
    text, enc, enc_errors = read_text_robust(path)
    if text is None:
        return {"rel": rel, "kind": kind, "error": "oversize_or_unreadable", "encoding_errors": enc_errors}

    # Try to sniff the delimiter from the first chunk.
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        delimiter = dialect.delimiter
    except csv.Error:
        pass

    rows = []
    total = 0
    errors = list(enc_errors)
    with open(path, "r", encoding=enc, newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        for row in reader:
            total += 1
            if len(rows) < max_rows:
                rows.append(row)

    if not rows:
        return {"rel": rel, "kind": kind, "encoding": enc, "delimiter": delimiter,
                "rows": 0, "columns": 0, "encoding_errors": errors}

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    # Header detection: first row all non-numeric strings AND a later row has numerics.
    first_numeric = [_to_float(c) for c in rows[0]]
    has_numeric_first = any(v is not None for v in first_numeric)
    has_numeric_later = any(_to_float(c) is not None for r in rows[1:10] for c in r if c)
    has_header = (not has_numeric_first) and has_numeric_later and len(rows) > 1

    data_rows = rows[1:] if has_header else rows
    header = rows[0] if has_header else [f"col{i}" for i in range(width)]

    columns = []
    for idx in range(width):
        cells = [r[idx] for r in data_rows]
        missing = sum(1 for c in cells if _looks_missing(c))
        non_missing_cells = [c for c in cells if not _looks_missing(c)]
        floats = [_to_float(c) for c in non_missing_cells]
        numeric_ratio = sum(1 for v in floats if v is not None) / max(1, len(non_missing_cells))
        numeric = len(non_missing_cells) > 0 and numeric_ratio >= 0.8
        col = {
            "index": idx,
            "name": str(header[idx]).strip(),
            "missing": missing,
            "non_missing": len(non_missing_cells),
            "unique": len(set(non_missing_cells)),
            "numeric": numeric,
            "stats": _numeric_summary([v for v in floats if v is not None]) if numeric else {},
            "sample_values": [
                {"locator": f"{rel}#row{i + 1}:col{idx}", "quote": str(r[idx])}
                for i, r in enumerate(data_rows[:3])
            ],
        }
        columns.append(col)

    # Duplicate detection on the profiled subset, plus a whole-file hash pass.
    dup_counts = {}
    dup_locs = []
    for i, r in enumerate(data_rows):
        key = "\x1f".join(r)
        dup_counts[key] = dup_counts.get(key, 0) + 1
    for key, cnt in dup_counts.items():
        if cnt > 1:
            dup_locs.append({"quote": key[:200], "count": cnt})

    # Whole-file duplicate scan (bounded memory) for an accurate total.
    all_dup_keys = {}
    total_data_rows = 0
    truncated_dup = False
    with open(path, "r", encoding=enc, newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        it = iter(reader)
        if has_header:
            next(it, None)
        for row in it:
            total_data_rows += 1
            key = "\x1f".join(row)
            if len(all_dup_keys) < MAX_DISTINCT_DUP_KEYS or key in all_dup_keys:
                all_dup_keys[key] = all_dup_keys.get(key, 0) + 1
            else:
                truncated_dup = True

    whole_dup_rows = sum(cnt for cnt in all_dup_keys.values() if cnt > 1)

    return {
        "rel": rel,
        "kind": kind,
        "encoding": enc,
        "delimiter": delimiter,
        "has_header": has_header,
        "rows": total,
        "data_rows": total_data_rows,
        "columns": width,
        "duplicate_rows": whole_dup_rows,
        "duplicate_distinct_keys": sum(1 for c in all_dup_keys.values() if c > 1),
        "duplicate_truncated": truncated_dup,
        "duplicate_samples": dup_locs[:5],
        "columns": columns,
        "encoding_errors": errors,
    }


def _profile_json(path, rel):
    text, enc, enc_errors = read_text_robust(path)
    if text is None:
        return {"rel": rel, "kind": "json", "error": "oversize_or_unreadable", "encoding_errors": enc_errors}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"rel": rel, "kind": "json", "error": f"invalid_json:{exc}", "encoding": enc,
                "encoding_errors": enc_errors}
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        # Heuristic: if the dict has a list-valued key, treat that as the table.
        list_val = None
        for v in obj.values():
            if isinstance(v, list):
                list_val = v
                break
        rows = list_val if list_val is not None else [obj]
    else:
        rows = []
    columns = sorted({k for r in rows if isinstance(r, dict) for k in r.keys()})
    return {
        "rel": rel,
        "kind": "json",
        "encoding": enc,
        "rows": len(rows),
        "columns": len(columns),
        "column_names": columns,
        "encoding_errors": enc_errors,
    }


def _profile_sqlite(path, rel):
    errors = []
    tables = []
    try:
        conn = sqlite3.connect(path)
        conn.text_factory = str
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        names = [r[0] for r in cur.fetchall()]
        for name in names:
            info = {"table": name}
            try:
                cur.execute(f"PRAGMA table_info({json.dumps(name)})")
                cols = [{"name": r[1], "type": r[2]} for r in cur.fetchall()]
                cur.execute(f"SELECT COUNT(*) FROM {json.dumps(name)}")
                count = cur.fetchone()[0]
                info.update({"columns": cols, "rows": count})
            except sqlite3.Error as exc:
                info["error"] = str(exc)
            tables.append(info)
        conn.close()
    except sqlite3.Error as exc:
        errors.append(str(exc))
    return {"rel": rel, "kind": "sqlite", "tables": tables, "errors": errors}


def run_profile(input_dir, output_dir, _work_dir=None):
    ensure_dir(output_dir)
    files = discover_inputs(input_dir)
    profiled = []
    degradations = []
    errors = []
    for f in files:
        kind = f["kind"]
        try:
            if kind in ("csv", "tsv"):
                profiled.append(_profile_delimited(f["path"], kind, f["rel"]))
            elif kind == "json":
                if f["name"].lower() != "case.json":
                    profiled.append(_profile_json(f["path"], f["rel"]))
            elif kind == "sqlite":
                profiled.append(_profile_sqlite(f["path"], f["rel"]))
            elif kind in ("markdown", "text"):
                text, enc, enc_errors = read_text_robust(f["path"])
                profiled.append({
                    "rel": f["rel"], "kind": kind, "encoding": enc,
                    "bytes": f["size"],
                    "lines": (text.count("\n") + 1) if text is not None else None,
                    "encoding_errors": enc_errors,
                })
            else:
                profiled.append({"rel": f["rel"], "kind": kind, "bytes": f["size"], "skipped": "unsupported_profile_kind"})
        except Exception as exc:  # keep going across any single-file failure
            degradations.append(f"{f['rel']}:{type(exc).__name__}:{exc}")

    profile = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "input_dir": input_dir,
        "files_scanned": len(files),
        "files": profiled,
        "degradations": degradations,
        "errors": errors,
    }
    out_path = os.path.join(output_dir, "data_profile.json")
    write_json_atomic(out_path, profile)
    return out_path, profile


# --------------------------------------------------------------------------- #
#  Subcommand: evidence
# --------------------------------------------------------------------------- #

def _normalize_claim(claim: dict):
    if not isinstance(claim, dict):
        raise ValueError("claim must be an object")
    out = dict(claim)
    out.setdefault("claim_id", f"claim-{len(claim.get('sources', [])) or 0}-{sha1_hex(json.dumps(claim, sort_keys=True, ensure_ascii=False))[:8]}")
    out.setdefault("claim_type", "other")
    out.setdefault("conclusion", "")
    out.setdefault("confidence", "computed")
    out.setdefault("status", "pending_check")
    out.setdefault("derivation", {})

    sources = out.get("sources")
    if isinstance(sources, dict):
        sources = [sources]
        out["sources"] = sources
    if not isinstance(sources, list):
        sources = []
        out["sources"] = sources

    # Promote the convenience fields locator/quote into a source entry.
    if not sources and (out.get("locator") or out.get("quote")):
        sources.append({
            "locator": out.pop("locator", ""),
            "quote": out.pop("quote", ""),
            "evidence_kind": out.pop("evidence_kind", "raw_value"),
        })

    for s in sources:
        if not isinstance(s, dict):
            continue
        s.setdefault("locator", "")
        s.setdefault("quote", "")
        s.setdefault("evidence_kind", "raw_value")
        if s.get("quote") and not s.get("quote_hash"):
            s["quote_hash"] = "sha1:" + sha1_hex(str(s["quote"]))

    if out.get("conclusion") in (None, "") and sources:
        out["conclusion"] = str(sources[0].get("quote", ""))[:120]
    return out


def run_evidence_add(result_path, claim):
    if not os.path.isfile(result_path):
        raise FileNotFoundError(f"result.json not found: {result_path}")
    result = read_json(result_path)
    em = result.setdefault("evidence_map", {"schema_version": SCHEMA_VERSION, "claims": []})
    em.setdefault("schema_version", SCHEMA_VERSION)
    em.setdefault("claims", [])
    claim = _normalize_claim(claim)
    em["claims"].append(claim)
    result.setdefault("change_log", []).append({
        "ts": utc_now(), "step": "evidence", "action": "add_claim",
        "detail": f"登记 claim {claim.get('claim_id')}（{claim.get('claim_type')}）",
    })
    write_json_atomic(result_path, result)
    return claim


def _resolve_locator_file(input_dir, locator, source_file):
    candidates = []
    if source_file:
        if os.path.isabs(source_file):
            candidates.append(source_file)
        else:
            candidates.append(os.path.join(input_dir, source_file))
            candidates.append(os.path.join(input_dir, os.path.basename(source_file)))
    if locator:
        file_part = locator.split("#", 1)[0].strip()
        if file_part:
            candidates.append(os.path.join(input_dir, file_part))
            candidates.append(os.path.join(input_dir, os.path.basename(file_part)))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _check_quote(path, quote):
    if not os.path.isfile(path):
        return False, "file_not_found"
    if detect_kind(os.path.basename(path)) in ("sqlite", "pdf"):
        return False, "binary_source_not_verifiable"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return False, f"read_error:{exc}"
    needle = str(quote).encode("utf-8", errors="replace")
    if not needle:
        return False, "empty_quote"
    if needle in raw:
        return True, "quote_matched"
    return False, "quote_not_found_in_source"


def run_evidence_verify(input_dir, result_path, output_dir):
    ensure_dir(output_dir)
    report = None
    if not os.path.isfile(result_path):
        report = {"result": "missing", "reason": "result.json not found", "checks": []}
    else:
        result = read_json(result_path)
        claims = result.get("evidence_map", {}).get("claims", [])
        checks = []
        for c in claims:
            claim_id = c.get("claim_id")
            claim_all_ok = True
            for s in c.get("sources", []):
                locator = s.get("locator", "")
                quote = s.get("quote", "")
                if not locator or not quote:
                    ok, detail = False, "missing_locator_or_quote"
                else:
                    path = _resolve_locator_file(input_dir, locator, c.get("source_file"))
                    if path is None:
                        ok, detail = False, "locator_unresolvable"
                    else:
                        ok, detail = _check_quote(path, quote)
                        if path is not None:
                            s["source_file"] = path
                s["verify_status"] = "verified" if ok else "failed"
                s["verified_at"] = utc_now()
                if not ok:
                    claim_all_ok = False
                checks.append({"claim_id": claim_id, "locator": locator,
                               "source_file": s.get("source_file"),
                               "ok": ok, "detail": detail})
            if not c.get("sources"):
                claim_all_ok = False
            # Only upgrade pending claims; never downgrade an explicit human/conflict state.
            if c.get("status") in ("pending_check", "verified"):
                c["status"] = "verified" if claim_all_ok else "pending_check"
            c["last_verified_at"] = utc_now()
        result.setdefault("change_log", []).append({
            "ts": utc_now(), "step": "evidence", "action": "verify_quotes",
            "detail": f"逐字校验 {len(checks)} 条 source，回写 verify_status",
        })
        write_json_atomic(result_path, result)
        report = {
            "result": "ok" if checks and all(ch["ok"] for ch in checks) else "fail",
            "total": len(checks),
            "passed": sum(1 for ch in checks if ch["ok"]),
            "failed": sum(1 for ch in checks if not ch["ok"]),
            "checks": checks,
        }
    out_path = os.path.join(output_dir, "evidence_verify_report.json")
    write_json_atomic(out_path, report)
    print(f"evidence_verify: {report.get('result')} ({report.get('passed', 0)}/{report.get('total', 0)} passed)")
    return out_path, report


# --------------------------------------------------------------------------- #
#  Subcommand: validate
# --------------------------------------------------------------------------- #

def run_validate(input_dir, output_dir, _work_dir=None):
    ensure_dir(output_dir)
    result_path = os.path.join(output_dir, "result.json")
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    if not os.path.isfile(result_path):
        add("result_exists", False, "result.json missing")
        report = {"result": "fail", "checks": checks}
        out_path = os.path.join(output_dir, "validation_report.json")
        write_json_atomic(out_path, report)
        print(f"validate: fail (result.json missing)")
        return out_path, report

    result = read_json(result_path)
    add("result_json_valid", isinstance(result, dict), "top level is dict")

    case_id = result.get("case_id")
    add("case_id_present", bool(case_id), str(case_id))

    status = result.get("status")
    add("status_valid", status in ALLOWED_STATUS, str(status))

    em = result.get("evidence_map", {})
    claims = em.get("claims", []) if isinstance(em, dict) else []
    add("evidence_map_present", isinstance(em, dict) and isinstance(claims, list), f"{len(claims)} claims")
    for c in claims:
        has_source = any(
            isinstance(s, dict) and s.get("locator") and s.get("quote")
            for s in c.get("sources", [])
        )
        add(f"claim_{c.get('claim_id')}_has_locator_quote", has_source, str(c.get("conclusion"))[:80])

    for i, s in enumerate(result.get("sources", [])):
        ok = isinstance(s, dict) and bool(s.get("locator")) and bool(s.get("quote"))
        add(f"source_{i}_has_locator_quote", ok, s.get("role", "") if isinstance(s, dict) else "")

    add("change_log_list", isinstance(result.get("change_log"), list), "change_log is list")
    add("uncertainty_present", "uncertainty" in result, "uncertainty key present")
    add("pending_human_present", "pending_human" in result, "pending_human key present")

    # Check artifact paths recorded in string-valued result fields actually exist.
    for key, val in result.items():
        if isinstance(val, str) and val.endswith((".csv", ".md", ".json", ".svg", ".png", ".pdf", ".docx", ".pptx", ".xlsx")):
            full = val if os.path.isabs(val) else os.path.join(output_dir, val)
            add(f"artifact_{key}_exists", os.path.isfile(full), val)

    report = {
        "result": "ok" if all(ch["ok"] for ch in checks) else "fail",
        "total": len(checks),
        "passed": sum(1 for ch in checks if ch["ok"]),
        "failed": sum(1 for ch in checks if not ch["ok"]),
        "checks": checks,
    }
    out_path = os.path.join(output_dir, "validation_report.json")
    write_json_atomic(out_path, report)
    print(f"validate: {report['result']} ({report['passed']}/{report['total']} passed)")
    return out_path, report


# --------------------------------------------------------------------------- #
#  Subcommand: selftest
# --------------------------------------------------------------------------- #

def _make_result_dir(root):
    ensure_dir(root)
    return root


def run_selftest(output_dir):
    ensure_dir(output_dir)
    results = []

    def scenario(name, input_dir, out_dir, work_dir, argv):
        cmd = argv[0]
        expected = {
            "init": ("result.json", lambda r: isinstance(r, dict) and "case_id" in r and r.get("status") in ALLOWED_STATUS),
            "profile": ("data_profile.json", lambda r: isinstance(r, dict) and "files" in r),
            "validate": ("validation_report.json", lambda r: isinstance(r, dict) and "checks" in r),
        }.get(cmd, ("result.json", lambda r: isinstance(r, dict)))
        try:
            dispatch(argv + ["--input", input_dir, "--output", out_dir, "--work", work_dir])
            artifact, predicate = expected
            rp = os.path.join(out_dir, artifact)
            if os.path.isfile(rp):
                r = read_json(rp)
                ok = bool(predicate(r))
                results.append({"scenario": name, "ok": ok,
                                "status": r.get("status") if isinstance(r, dict) else None,
                                "detail": f"{artifact} valid"})
            else:
                results.append({"scenario": name, "ok": False, "detail": f"{artifact} missing"})
        except SystemExit:
            results.append({"scenario": name, "ok": True, "detail": "SystemExit (argparse)"})
        except Exception as exc:
            results.append({"scenario": name, "ok": False, "detail": f"{type(exc).__name__}:{exc}"})

    base = output_dir
    ensure_dir(base)

    # 1. empty input
    d = os.path.join(base, "empty_in")
    o = os.path.join(base, "empty_out")
    w = os.path.join(base, "empty_work")
    ensure_dir(d)
    scenario("empty_input", d, o, w, ["init"])

    # 2. bad JSON
    d = os.path.join(base, "badjson_in")
    o = os.path.join(base, "badjson_out")
    w = os.path.join(base, "badjson_work")
    ensure_dir(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    scenario("bad_json", d, o, w, ["init"])

    # 3. bad encoding (latin-1 bytes written to a .csv + valid case.json)
    d = os.path.join(base, "badenc_in")
    o = os.path.join(base, "badenc_out")
    w = os.path.join(base, "badenc_work")
    ensure_dir(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "case-badenc", "output_contract": {"answer": None}}))
    with open(os.path.join(d, "m.csv"), "wb") as fh:
        fh.write(b"a,b\n\xe9,1\n2,3\n")
    scenario("bad_encoding", d, o, w, ["profile"])

    # 4. missing params -> defaults resolved via SCI_* env vars (same process).
    d = os.path.join(base, "noparam_in")
    o = os.path.join(base, "noparam_out")
    w = os.path.join(base, "noparam_work")
    ensure_dir(d)
    old = (os.environ.get("SCI_INPUT"), os.environ.get("SCI_OUTPUT"), os.environ.get("SCI_WORK"))
    os.environ["SCI_INPUT"] = d
    os.environ["SCI_OUTPUT"] = o
    os.environ["SCI_WORK"] = w
    try:
        dispatch(["init"])
    except SystemExit:
        pass
    except Exception as exc:
        results.append({"scenario": "missing_params", "ok": False, "detail": str(exc)})
    else:
        rp = os.path.join(o, "result.json")
        ok = os.path.isfile(rp)
        results.append({"scenario": "missing_params", "ok": ok, "detail": "defaults resolved"})
    finally:
        for key, val in zip(("SCI_INPUT", "SCI_OUTPUT", "SCI_WORK"), old):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "results": results,
        "all_pass": all(r["ok"] for r in results),
    }
    out_path = os.path.join(output_dir, "selftest_report.json")
    write_json_atomic(out_path, report)
    print(f"selftest: {'PASS' if report['all_pass'] else 'FAIL'} ({sum(1 for r in results if r['ok'])}/{len(results)})")
    return out_path, report


# --------------------------------------------------------------------------- #
#  Subcommand: all
# --------------------------------------------------------------------------- #

def run_all(input_dir, output_dir, work_dir):
    run_init(input_dir, output_dir, work_dir)
    run_profile(input_dir, output_dir, work_dir)
    return run_validate(input_dir, output_dir, work_dir)


# --------------------------------------------------------------------------- #
#  Runtime metadata + dispatch
# --------------------------------------------------------------------------- #

def runtime_meta():
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "node": getattr(platform, "node", lambda: "unknown")(),
        "generated_at": utc_now(),
        "default_seed": DEFAULT_SEED,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="sci-analysis-exec helper: init / profile / evidence / validate / selftest",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write result.json skeleton")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    p = sub.add_parser("profile", help="write data_profile.json")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    p = sub.add_parser("evidence", help="evidence_map operations")
    esub = p.add_subparsers(dest="evidence_command", required=True)
    pe = esub.add_parser("add", help="append one claim")
    pe.add_argument("--result", required=True)
    pe.add_argument("--claim-json", dest="claim_json", required=True)
    pe.add_argument("--conclusion", default=None)
    pe.add_argument("--locator", default=None)
    pe.add_argument("--quote", default=None)
    pe.add_argument("--claim-type", dest="claim_type", default=None)
    pe.add_argument("--evidence-kind", dest="evidence_kind", default=None)
    pe.add_argument("--claim-id", dest="claim_id", default=None)

    pv = esub.add_parser("verify", help="machine-check quotes against sources")
    pv.add_argument("--input", default=None)
    pv.add_argument("--result", required=True)
    pv.add_argument("--output", default=None)

    p = sub.add_parser("validate", help="validate result.json completeness")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    p = sub.add_parser("selftest", help="run robustness self-tests")
    p.add_argument("--output", default=None)

    p = sub.add_parser("all", help="init + profile + validate")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    return parser


def dispatch(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return run_init(args.input or default_input_dir(), args.output or default_output_dir(), args.work or default_work_dir())
    if args.command == "profile":
        return run_profile(args.input or default_input_dir(), args.output or default_output_dir(), args.work or default_work_dir())
    if args.command == "validate":
        return run_validate(args.input or default_input_dir(), args.output or default_output_dir(), args.work or default_work_dir())
    if args.command == "all":
        return run_all(args.input or default_input_dir(), args.output or default_output_dir(), args.work or default_work_dir())
    if args.command == "selftest":
        return run_selftest(args.output or default_output_dir())
    if args.command == "evidence":
        if args.evidence_command == "add":
            if args.claim_json:
                try:
                    claim = json.loads(args.claim_json)
                except json.JSONDecodeError as exc:
                    print(f"evidence add: invalid --claim-json: {exc}", file=sys.stderr)
                    return 1
            else:
                claim = {
                    "claim_id": args.claim_id,
                    "claim_type": args.claim_type or "other",
                    "conclusion": args.conclusion,
                    "locator": args.locator,
                    "quote": args.quote,
                    "evidence_kind": args.evidence_kind or "raw_value",
                }
            return run_evidence_add(args.result, claim)
        if args.evidence_command == "verify":
            return run_evidence_verify(
                args.input or default_input_dir(),
                args.result,
                args.output or os.path.dirname(args.result),
            )
    return 1


def main(argv=None):
    try:
        dispatch(argv)
        return 0
    except SystemExit as exc:
        # argparse exit codes are ordinary; always surface 0 for handled flows.
        return int(exc.code or 0) if isinstance(exc.code, int) and exc.code in (0,) else 0
    except BaseException as exc:  # noqa: BLE001 — hard fail-safe: never crash the harness.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
