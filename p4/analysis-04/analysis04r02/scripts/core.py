#!/usr/bin/env python3
"""core.py — pure-standard-library helpers for the sci-analysis-checkpoint Skill.

Design constraints:
  * standard library only (no numpy/pandas/matplotlib imports here);
  * every read/write is tolerant and never raises out of the CLI;
  * JSON is always serialized through safe_serialize so NaN/Inf never break output;
  * output_contract is *discovered*, never invented.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

SKILL_NAME = "sci-analysis-checkpoint"
SKILL_VERSION = "1.0.0"

CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "result_schema", "resultSchema")

NA_MARKERS = {"", "na", "n/a", "nan", "null", "none", "missing", "?", ".", "n.d.", "nd", "#n/a", "#na"}

# Suspect sentinel values: flagged, never silently dropped (they might be real data).
SUSPECT_CODES = {"-999", "-9999", "-999.0", "-9999.0", "9999", "9999.0", "999", "999.0"}

ARTIFACT_EXTS = (
    ".md", ".json", ".csv", ".tsv", ".txt", ".html",
    ".svg", ".png", ".pdf", ".jpg", ".jpeg",
    ".docx", ".pptx", ".xlsx",
)

# Phase budget governor: 900 s total, 64 model requests total.
# P0 is the hard early-checkpoint gate (first 3 minutes).
PHASE_BUDGET = {
    "P0": {"name": "落盘骨架", "window_s": [0, 180], "max_req": 6},
    "P1": {"name": "盘点与字典", "window_s": [180, 420], "max_req": 12},
    "P2": {"name": "方案与代码", "window_s": [420, 660], "max_req": 18},
    "P3": {"name": "运行与核对", "window_s": [660, 840], "max_req": 18},
    "P4": {"name": "收尾与复核", "window_s": [840, 900], "max_req": 10},
}


# --------------------------------------------------------------------------
# Time and JSON-safe serialization
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_serialize(obj):
    """Return a JSON-serializable deep copy; NaN/Inf -> None, other -> str."""
    if isinstance(obj, dict):
        return {str(k): safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [safe_serialize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    return str(obj)


# --------------------------------------------------------------------------
# Tolerant text / JSON reading
# --------------------------------------------------------------------------

def read_text(path):
    """Read a file as text tolerantly: utf-8-sig -> utf-8 -> latin-1.

    Returns (text, encoding, note). text is None when the file is too large.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "unreadable", str(exc)
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped_too_large", f"{len(raw)} bytes > {MAX_FILE_BYTES}"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)", "undecodable bytes replaced"


def load_json_tolerant(path):
    """Parse JSON, tolerating BOM/bad encoding and attempting a salvage parse.

    Returns (obj_or_None, err_dict_or_None).
    """
    text, enc, note = read_text(path)
    if text is None:
        return None, {"error": "file_too_large_or_unreadable", "note": note or "unknown"}
    try:
        return json.loads(text), None
    except Exception as first_err:  # noqa: BLE001 - must never propagate
        try:
            s = text.find("{")
            e = text.rfind("}")
            if 0 <= s < e:
                return json.loads(text[s:e + 1]), {
                    "error": "bad_json_partially_salvaged",
                    "note": str(first_err),
                }
        except Exception:  # noqa: BLE001
            pass
        return None, {"error": "bad_json", "note": str(first_err)}


def describe_load_error(err) -> dict:
    if isinstance(err, dict):
        return {"error": err.get("error", "unknown"), "note": err.get("note", "")}
    return {"error": "unknown", "note": str(err)}


def load_json_file(path):
    if not os.path.exists(path):
        return None
    obj, _err = load_json_tolerant(path)
    return obj


# --------------------------------------------------------------------------
# Input discovery
# --------------------------------------------------------------------------

def scan_input(input_dir):
    """Walk input_dir; return (files, scan_errors). Files carry relpath/size/ext."""
    files = []
    errors = []
    if not os.path.isdir(input_dir):
        errors.append(f"input dir missing or not a directory: {input_dir}")
        return files, errors
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError as exc:
                files.append({"path": p, "relpath": os.path.relpath(p, input_dir), "size": -1, "error": str(exc)})
                continue
            rel = os.path.relpath(p, input_dir)
            files.append({
                "path": p,
                "relpath": rel,
                "size": size,
                "ext": os.path.splitext(name)[1].lower(),
            })
    return files, errors


def load_case_json(input_dir):
    files, _ = scan_input(input_dir)
    for f in files:
        if os.path.basename(f["relpath"]).lower() == "case.json":
            obj, _err = load_json_tolerant(f["path"])
            return obj if isinstance(obj, dict) else {}
    return {}


# --------------------------------------------------------------------------
# Contract / case identity discovery
# --------------------------------------------------------------------------

def discover_case_id(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "id", "armiId"):
        v = case.get(key)
        if v not in (None, ""):
            return v
    return None


def discover_output_contract(case):
    """Find output_contract keys under several spellings and nesting levels.

    Returns (fields_dict, source_label). Never raises.
    """
    if not isinstance(case, dict):
        return {}, "no_case_object"
    for key in CONTRACT_KEYS:
        v = case.get(key)
        if v is None:
            continue
        fields, how = contract_fields_from(v)
        if fields is not None:
            return fields, f"case.{key}.{how}"
    for sub in ("task", "spec", "schema", "requirements", "output"):
        v = case.get(sub)
        if isinstance(v, dict):
            for key in CONTRACT_KEYS:
                if key in v:
                    fields, how = contract_fields_from(v[key])
                    if fields is not None:
                        return fields, f"case.{sub}.{key}.{how}"
    return {}, "output_contract_not_found"


def contract_fields_from(v):
    if isinstance(v, dict):
        if "properties" in v and isinstance(v["properties"], dict):
            return v["properties"], "properties"
        return v, "dict"
    if isinstance(v, list):
        out = {}
        for item in v:
            if isinstance(item, str):
                out[item] = None
            elif isinstance(item, dict):
                for k in item:
                    out[k] = None
        return out, "list"
    if isinstance(v, str):
        return {v: None}, "string"
    return None, "unsupported"


def default_value(spec):
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        t = spec.get("type")
        if t == "array":
            return []
        if t == "object":
            return {}
        if t in ("number", "integer", "float"):
            return None
        if t == "string":
            return ""
        if t == "boolean":
            return False
        return None
    if isinstance(spec, list):
        return []
    return None


def build_skeleton(case, contract, contract_source):
    case = case if isinstance(case, dict) else {}
    sk = {}
    for field, spec in contract.items():
        if field in ("case_id", "status"):
            continue  # identity/status are owned by the Skill, not the contract
        sk[field] = default_value(spec)
    sk["case_id"] = discover_case_id(case)
    sk["status"] = "partial"
    sk.update({
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {"generated_at": now_iso()},
        "_meta": {
            "contract_source": contract_source,
            "skill": SKILL_NAME,
            "version": SKILL_VERSION,
        },
    })
    return sk


# --------------------------------------------------------------------------
# Atomic / fallback JSON writes (never crash the process)
# --------------------------------------------------------------------------

def write_json_atomic(obj, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(safe_serialize(obj), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def write_json_fallback(obj, output_dir, work_dir, name="result.json"):
    """Write to output_dir; on OSError fall back to work_dir, then cwd, then stdout.

    Always returns (or prints) a legal artifact; never raises.
    """
    assert isinstance(obj, dict), "write_json_fallback expects a dict"
    for d in (output_dir, work_dir, os.getcwd()):
        try:
            return write_json_atomic(obj, os.path.join(d, name))
        except OSError as exc:
            obj.setdefault("degradations", []).append(f"write fallback from {d}: {exc}")
    print(json.dumps(safe_serialize(obj), ensure_ascii=False, indent=2))
    return None


# --------------------------------------------------------------------------
# Data profiling (delimiter/JSON/SQLite)
# --------------------------------------------------------------------------

def is_missing(v) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.lower() in NA_MARKERS


def is_int(s) -> bool:
    try:
        int(s)
        return True
    except (TypeError, ValueError):
        return False


def is_float(s) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def infer_type(vals):
    if not vals:
        return "empty"
    kinds = set()
    for v in vals:
        s = str(v).strip()
        if is_int(s):
            kinds.add("integer")
        elif is_float(s):
            kinds.add("float")
        else:
            kinds.add("string")
    if kinds == {"integer"}:
        return "integer"
    if kinds <= {"integer", "float"}:
        return "float"
    if kinds == {"string"}:
        return "string"
    return "mixed"


def numeric_summary(values):
    vals = []
    for v in values:
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        vals.append(f)
    n = len(vals)
    if n == 0:
        return {"n_valid": 0, "n_missing": len(values), "min": None, "max": None, "mean": None, "std": None}
    mean = sum(vals) / n
    if n >= 2:
        var = sum((x - mean) ** 2 for x in vals) / (n - 1)
        std = math.sqrt(var)
    else:
        std = None
    return {
        "n_valid": n,
        "n_missing": len(values) - n,
        "min": min(vals),
        "max": max(vals),
        "mean": mean,
        "std": std,
    }


def detect_delimiter(text):
    best = None
    best_score = (-1, 1)
    for d in (",", "\t", ";", "|"):
        counts = []
        for line in text.splitlines()[:20]:
            if not line.strip():
                continue
            counts.append(line.count(d))
        if not counts:
            continue
        nonzero = [c for c in counts if c > 0]
        if not nonzero:
            continue
        score = (sum(nonzero), -len(set(nonzero)))
        if score > best_score:
            best_score = score
            best = d
    return best


def has_header(header):
    non_num = 0
    for c in header:
        s = str(c).strip()
        if s and not is_float(s):
            non_num += 1
    return non_num > 0


def profile_delimited(path, relpath):
    info = {"path": relpath, "kind": "table"}
    text, enc, note = read_text(path)
    info["encoding"] = enc
    if note:
        info["decode_note"] = note
    if text is None:
        info["error"] = "too_large_or_unreadable"
        return info
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        delim = dialect.delimiter
    except Exception:  # noqa: BLE001
        delim = detect_delimiter(text) or ","
    info["delimiter"] = repr(delim)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        info["n_rows"] = 0
        info["n_cols"] = 0
        return info
    header = [str(c).lstrip("\ufeff") for c in rows[0]]
    data = rows[1:]
    while data and all(not str(c).strip() for c in data[-1]):
        data.pop()
    info["header"] = header
    info["n_cols"] = len(header)
    info["n_rows"] = len(data)
    info["ragged_rows"] = sum(1 for r in data if len(r) != len(header))
    info["header_guess_non_numeric"] = has_header(header)

    col_missing = {}
    col_suspect = {}
    for j, col in enumerate(header):
        col_missing[col] = sum(1 for r in data if j >= len(r) or is_missing(r[j]))
        col_suspect[col] = sum(1 for r in data if j < len(r) and str(r[j]).strip() in SUSPECT_CODES)
    info["missing_by_column"] = col_missing
    info["suspect_sentinel_by_column"] = col_suspect
    info["total_missing_cells"] = sum(col_missing.values())

    tups = [tuple(str(c) for c in r) for r in data]
    counts = Counter(tups)
    info["unique_rows"] = len(counts)
    info["duplicate_rows"] = sum(v - 1 for v in counts.values() if v > 1)

    types = {}
    numsum = {}
    cat_counts = {}
    for j, col in enumerate(header):
        vals = [r[j] for r in data if j < len(r) and not is_missing(r[j])]
        t = infer_type(vals)
        types[col] = t
        if t in ("integer", "float"):
            numsum[col] = numeric_summary(vals)
        clean = [str(r[j]).strip() for r in data if j < len(r) and not is_missing(r[j])]
        if len(set(clean)) <= 20 and t != "float":
            cat_counts[col] = dict(Counter(clean))
    info["column_types"] = types
    info["numeric_summary"] = numsum
    info["categorical_value_counts"] = cat_counts
    return info


def profile_json(path, relpath):
    obj, err = load_json_tolerant(path)
    info = {"path": relpath, "kind": "json"}
    if err:
        info["error"] = err
        return info
    if isinstance(obj, dict):
        info["top_type"] = "object"
        info["keys"] = list(obj.keys())
        info["key_types"] = {str(k): type(v).__name__ for k, v in list(obj.items())[:100]}
    elif isinstance(obj, list):
        info["top_type"] = "array"
        info["length"] = len(obj)
        if obj:
            info["first_item_type"] = type(obj[0]).__name__
            if isinstance(obj[0], dict):
                info["first_item_keys"] = list(obj[0].keys())
    else:
        info["top_type"] = type(obj).__name__
    return info


def profile_sqlite(path, relpath):
    info = {"path": relpath, "kind": "sqlite", "tables": []}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            entry = {"name": t}
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                entry["row_count"] = cur.fetchone()[0]
            except Exception as exc:  # noqa: BLE001
                entry["row_count_error"] = str(exc)
            try:
                cur.execute(f'PRAGMA table_info("{t}")')
                entry["columns"] = [
                    {"cid": r[0], "name": r[1], "type": r[2], "notnull": r[3], "pk": r[5]}
                    for r in cur.fetchall()
                ]
            except Exception as exc:  # noqa: BLE001
                entry["columns_error"] = str(exc)
            info["tables"].append(entry)
        con.close()
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


def profile_input(input_dir):
    files, scan_errs = scan_input(input_dir)
    profiles = []
    for f in files:
        ext = f.get("ext", "")
        if f.get("error"):
            profiles.append({"path": f["relpath"], "kind": "unreadable", "error": f["error"]})
            continue
        try:
            if ext in (".csv", ".tsv", ".txt", ".data"):
                profiles.append(profile_delimited(f["path"], f["relpath"]))
            elif ext == ".json":
                profiles.append(profile_json(f["path"], f["relpath"]))
            elif ext in (".sqlite", ".db", ".sqlite3"):
                profiles.append(profile_sqlite(f["path"], f["relpath"]))
            elif ext == ".jsonl":
                with open(f["path"], encoding="utf-8", errors="replace") as fh:
                    n = sum(1 for _ in fh)
                profiles.append({"path": f["relpath"], "kind": "jsonl", "line_count": n})
            else:
                profiles.append({"path": f["relpath"], "kind": "other", "size": f["size"], "note": "not profiled"})
        except Exception as exc:  # noqa: BLE001
            profiles.append({"path": f["relpath"], "kind": "error", "error": str(exc)})
    return {
        "input_dir": input_dir,
        "file_count": len(files),
        "scan_errors": scan_errs,
        "profiles": profiles,
        "generated_at": now_iso(),
    }


# --------------------------------------------------------------------------
# Result validation
# --------------------------------------------------------------------------

def validate_result(result, output_dir, contract=None):
    findings = []
    if not isinstance(result, dict):
        return [{"level": "error", "check": "result_is_dict", "detail": "result is not a dict"}]
    cid = result.get("case_id")
    if cid in (None, ""):
        findings.append({"level": "warn", "check": "case_id", "detail": "missing or empty"})
    if result.get("status") not in STATUSES:
        findings.append({"level": "error", "check": "status", "detail": f"invalid status {result.get('status')!r}"})
    if contract:
        for k in contract:
            if k not in result:
                findings.append({"level": "warn", "check": "contract_field", "detail": f"missing contract field {k!r}"})
    sources = result.get("sources")
    if isinstance(sources, list) and sources:
        for i, s in enumerate(sources):
            if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
                findings.append({"level": "warn", "check": "source_item", "detail": f"sources[{i}] missing locator/quote"})
    elif not sources:
        findings.append({"level": "info", "check": "sources", "detail": "empty (acceptable for partial/insufficient_evidence)"})

    for key, val in result.items():
        if isinstance(val, str) and val.lower().endswith(ARTIFACT_EXTS):
            p = val if os.path.isabs(val) else os.path.join(output_dir, val)
            if not os.path.exists(p):
                findings.append({"level": "warn", "check": "artifact_exists", "detail": f"{key}: {val} not found"})
                continue
            low = val.lower()
            if low.endswith(".svg"):
                try:
                    ET.parse(p)
                    findings.append({"level": "ok", "check": "svg_parse", "detail": val})
                except Exception as exc:  # noqa: BLE001
                    findings.append({"level": "error", "check": "svg_parse", "detail": f"{val}: {exc}"})
            if low.endswith(".pdf"):
                try:
                    with open(p, "rb") as fh:
                        head = fh.read(5)
                    if head != b"%PDF-":
                        findings.append({"level": "error", "check": "pdf_header", "detail": val})
                except Exception as exc:  # noqa: BLE001
                    findings.append({"level": "error", "check": "pdf_header", "detail": f"{val}: {exc}"})
    try:
        json.dumps(safe_serialize(result), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        findings.append({"level": "error", "check": "json_serializable", "detail": str(exc)})
    return findings


# --------------------------------------------------------------------------
# Budget governor
# --------------------------------------------------------------------------

def budget_status(state, phase, requests=None):
    started = state.get("started_at") if isinstance(state, dict) else None
    elapsed = None
    if started:
        try:
            t0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        except Exception:  # noqa: BLE001
            elapsed = None
    info = PHASE_BUDGET.get(phase, {})
    lo, hi = info.get("window_s", (None, None))
    lines = [
        f"phase={phase} name={info.get('name', '?')} window={lo}-{hi}s max_req={info.get('max_req', '?')}"
    ]
    if elapsed is not None:
        lines.append(f"elapsed_s={elapsed:.0f}")
        if lo is not None and hi is not None:
            if elapsed < lo:
                lines.append("status=ahead_of_phase")
            elif elapsed <= hi * 1.2:
                lines.append("status=within_phase")
            else:
                lines.append("status=OVER_BUDGET -> cut optional scope, keep mandatory deliverables, status=partial")
    if requests is not None:
        lines.append(f"requests_used={requests} requests_left={max(0, 64 - requests)}")
    return "\n".join(lines)
