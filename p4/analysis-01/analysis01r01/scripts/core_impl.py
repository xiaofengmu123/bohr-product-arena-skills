#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- deterministic, stdlib-only substrate for the sci-data-cleaning skill.

This module holds no case-specific policy. It provides the mechanical layer the
Agent needs: robust text/encoding/JSON I/O, input discovery, conservative value
classification (未检测 / 低于检出限 / 录入缺失 / 真值0), tabular parsing and
profiling, duplicate/outlier diagnostics, and the structured evidence-map schema.

Invariants: no random, no network, no wall-clock dependence except optional
metadata timestamps; the same input bytes produce the same data results.
"""
import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import unicodedata

DEFAULT_MAX_FILE = 2 * 1024 * 1024  # 2MB
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")
TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema", "数据字典", "字段", "字典")
RULES_HINTS = ("rule", "qc", "unit", "quality", "规则", "质控", "单位", "检出")
WORKORDER_HINTS = ("workorder", "work_order", "order", "工单", "步骤", "instructions", "清洗")

# Reserved status values of result.json.
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALLOWED_STATUS = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# Four missing/zero classes must never be conflated or overwritten.
KIND_VALID = "valid"
KIND_TRUE_ZERO = "true_zero"
KIND_NOT_DETECTED = "not_detected"
KIND_BELOW_LOD = "below_lod"
KIND_ENTERED_MISSING = "entered_missing"
MISSING_KINDS = (KIND_TRUE_ZERO, KIND_NOT_DETECTED, KIND_BELOW_LOD, KIND_ENTERED_MISSING)

MISSING_SENTINELS = {
    "na", "n/a", "n.a.", "null", "none", "nan", "missing", "缺失", "空白", "无",
    "-999", "-9999", "-99999", ".", "-", "--", "—", "..",
    "n.d.", "nd", "n/a", "n.d", "n/a", "n.a", "no data", "not available", "不可用",
}
NOT_DETECTED_TOKENS = {
    "nd", "n.d.", "not detected", "notdetected", "未检出", "未检测", "未检出（nd）",
    "未检测（nd）", "n.d", "no detected",
}
BELOW_LOD_TOKENS = {
    "<lod", "<dl", "below lod", "below detection limit", "below detection",
    "低于检出限", "低于检测限", "检出限以下", "lod", "dl",
}
# Numeric-looking sentinels that mean "not a measurement" rather than zero.
_TRUE_ZERO_EXACT = {"0", "0.0", "0.00", "0.000", "-0", "+0", "0e0", "0.0e0"}

_FW_TABLE = str.maketrans(
    "０１２３４５６７８９．，－＋＜＞％‰",
    "0123456789.,-+<>%‰",
)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Robust I/O
# ---------------------------------------------------------------------------
def list_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__", ".venv", "venv"))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.isfile(full):
                out.append(full)
    return sorted(out)


def read_bytes_robust(path, max_size=DEFAULT_MAX_FILE):
    result = {"path": path, "size": None, "read_error": None, "data": None}
    if not os.path.isfile(path):
        result["read_error"] = "file not found: %s" % path
        return result
    try:
        size = os.path.getsize(path)
        result["size"] = size
        if size > max_size:
            result["read_error"] = "file exceeds %d bytes; skipped" % max_size
            return result
        with open(path, "rb") as f:
            result["data"] = f.read()
    except OSError as exc:
        result["read_error"] = "os error: %s" % exc
    return result


def read_text_robust(path, max_size=DEFAULT_MAX_FILE):
    out = {
        "path": path, "text": "", "encoding": None, "degraded": False,
        "bom": False, "size": None, "read_error": None,
    }
    raw = read_bytes_robust(path, max_size)
    if raw["read_error"]:
        out["read_error"] = raw["read_error"]
        out["size"] = raw["size"]
        return out
    data = raw["data"]
    out["size"] = raw["size"]
    last_err = None
    for enc in ENCODINGS:
        try:
            text = data.decode(enc)
            out["encoding"] = enc
            out["bom"] = enc == "utf-8-sig" and data.startswith(b"\xef\xbb\xbf")
            out["text"] = text.lstrip("\ufeff")
            if enc in ("gb18030", "big5", "latin-1"):
                out["degraded"] = True
            return out
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = exc
    # Last resort: latin-1 never fails on byte input.
    out["text"] = data.decode("latin-1", errors="replace")
    out["encoding"] = "latin-1"
    out["degraded"] = True
    out["read_error"] = "no clean decoding: %s" % last_err
    return out


def parse_json_robust(text, default=None):
    """Return (obj, status). status in {ok, recovered, failed}. Never raises."""
    if text is None:
        return default, "failed"
    try:
        return json.loads(text), "ok"
    except (ValueError, TypeError):
        pass
    # Salvage attempts: strip BOM, try common suffixes, then best-effort object.
    t = text.lstrip("\ufeff").strip()
    for candidate in (t, t[: t.rfind("}") + 1] if "}" in t else t):
        try:
            return json.loads(candidate), "recovered"
        except (ValueError, TypeError):
            continue
    return default, "failed"


def atomic_write_text(path, text, encoding="utf-8"):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def atomic_write_csv(path, header, rows):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Input discovery / case.json
# ---------------------------------------------------------------------------
def _has_any(name, hints):
    low = name.lower()
    return any(h in low for h in hints)


def discover_inputs(input_dir):
    files = list_files(input_dir)
    manifest = {
        "input_dir": input_dir, "case_json": None, "task_md": None, "data": [],
        "dictionary": [], "rules": [], "workorder": [], "other": [], "all": files,
    }
    for f in files:
        base = os.path.basename(f)
        low = base.lower()
        if low == "case.json":
            manifest["case_json"] = f
        elif low == "task.md":
            manifest["task_md"] = f
        elif low.endswith(TABULAR_SUFFIXES):
            if _has_any(low, DICT_HINTS):
                manifest["dictionary"].append(f)
            elif _has_any(low, RULES_HINTS):
                manifest["rules"].append(f)
            elif _has_any(low, WORKORDER_HINTS):
                manifest["workorder"].append(f)
            else:
                manifest["data"].append(f)
        else:
            manifest["other"].append(f)
    classified = set(manifest["dictionary"] + manifest["rules"] + manifest["workorder"])
    if not manifest["data"]:
        for f in files:
            if f.endswith(TABULAR_SUFFIXES) and f not in classified and f != manifest["case_json"]:
                manifest["data"].append(f)
    return manifest


def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {
        "case_id": None, "output_contract": {}, "raw": {}, "manifest": manifest,
        "read_error": None,
    }
    path = manifest["case_json"]
    if path is None:
        for f in manifest["all"]:
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info["text"], {})
    case["raw"] = parsed if isinstance(parsed, dict) else {}
    case["case_id"] = (
        case["raw"].get("case_id")
        or case["raw"].get("id")
        or case["raw"].get("challenge_id")
        or os.path.basename(os.path.dirname(path))
        or os.path.basename(path)
    )
    contract = case["raw"].get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = contract
    if status != "ok":
        case["read_error"] = "case.json parsed with status=%s" % status
    if info.get("degraded"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding degraded"
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


_CONTRACT_ARTIFACT_HINTS = (
    (("clean", "table", "data"), "cleaned_data"),
    (("report", "quality", "qc"), "quality_report"),
    (("profile", "stat", "summar"), "data_profile"),
    (("evidence", "trace", "proof", "map"), "evidence_map"),
    (("snapshot", "raw", "origin"), "source_snapshot"),
)


def fill_contract_keys(result, contract, artifacts):
    """Backfill every output_contract key at top level, never renaming/deleting."""
    for key, val in contract.items():
        current = result.get(key)
        is_empty = current is None or current == [] or current == {}
        if not is_empty:
            continue
        low = str(key).lower()
        for hints, art in _CONTRACT_ARTIFACT_HINTS:
            if any(h in low for h in hints):
                result[key] = artifacts.get(art)
                break
    return result


# ---------------------------------------------------------------------------
# Token / value semantics
# ---------------------------------------------------------------------------
def normalize_token(value):
    if value is None:
        return ""
    t = unicodedata.normalize("NFKC", str(value))
    t = t.translate(_FW_TABLE)
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def parse_number(value):
    """Best-effort float parse for profiling only. Never used to rewrite values."""
    if not isinstance(value, str):
        value = str(value)
    t = value.strip().translate(_FW_TABLE)
    if not t:
        return None
    t = t.replace(" ", "").replace("\u00a0", "").replace("\t", "")
    if not t:
        return None
    if any(ch in t for ch in "%‰°"):
        return None
    if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", t):
        t = t.replace(",", "")
    elif "," in t:
        return None  # ambiguous comma (e.g. 1,5) -- do not guess
    try:
        v = float(t)
    except ValueError:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def is_true_zero(raw):
    return normalize_token(raw) in _TRUE_ZERO_EXACT or parse_number(raw) == 0.0


def classify_value(raw):
    """Conservative 4-class missing/zero classification. Returns a kind string."""
    if raw is None:
        return KIND_ENTERED_MISSING
    s = str(raw)
    if s.strip() == "":
        return KIND_ENTERED_MISSING
    norm = normalize_token(s)
    if norm in _TRUE_ZERO_EXACT:
        return KIND_TRUE_ZERO
    if is_true_zero(s):
        return KIND_TRUE_ZERO
    low = norm
    if low in NOT_DETECTED_TOKENS:
        return KIND_NOT_DETECTED
    if low in BELOW_LOD_TOKENS:
        return KIND_BELOW_LOD
    stripped = s.strip()
    if stripped[:1] in ("<", "＜"):
        return KIND_BELOW_LOD
    if low in MISSING_SENTINELS:
        return KIND_ENTERED_MISSING
    return KIND_VALID


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------
def count_outside_quotes(line, ch):
    n = 0
    inq = False
    q = None
    for c in line:
        if inq:
            if c == q:
                inq = False
        else:
            if c in "\"'":
                inq = True
                q = c
            elif c == ch:
                n += 1
    return n


def sniff_delimiter(text):
    candidates = (",", "\t", ";", "|")
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if not lines:
        return ","
    best = None
    best_key = None
    for d in candidates:
        counts = [count_outside_quotes(ln, d) for ln in lines]
        total = sum(counts)
        nlines = sum(1 for c in counts if c > 0)
        if total == 0:
            continue
        key = (nlines, total)
        if best_key is None or key > best_key:
            best, best_key = d, key
    return best or ","


def _looks_like_header(cells):
    for c in cells:
        if c is None or c.strip() == "":
            continue
        kind = classify_value(c)
        if kind == KIND_VALID and parse_number(c) is None:
            return True
    return False


def read_table_robust(path, max_size=DEFAULT_MAX_FILE):
    out = {
        "path": path, "header": [], "header_original": [], "rows": [], "n_rows": 0,
        "n_cols": 0, "delimiter": None, "encoding": None, "degraded": False,
        "has_header": False, "ragged_rows": [], "read_error": None, "text": "",
        "sha256": None,
    }
    info = read_text_robust(path, max_size)
    if info["read_error"]:
        out["read_error"] = info["read_error"]
        return out
    out["encoding"] = info["encoding"]
    out["degraded"] = info["degraded"]
    out["text"] = info["text"]
    out["sha256"] = sha256_bytes(info["text"])
    text = info["text"]
    delim = sniff_delimiter(text)
    out["delimiter"] = delim
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        raw_rows = [r for r in reader]
    except csv.Error as exc:
        out["read_error"] = "csv parse error: %s" % exc
        return out
    # Drop fully empty trailing rows only (no data loss of content).
    while raw_rows and all((c or "").strip() == "" for c in raw_rows[-1]):
        raw_rows.pop()
    if not raw_rows:
        out["read_error"] = "empty table (no rows)"
        return out
    first = raw_rows[0]
    has_header = _looks_like_header(first)
    out["has_header"] = has_header
    if has_header:
        out["header_original"] = list(first)
        out["header"] = [h.strip() for h in first]
        data = raw_rows[1:]
    else:
        ncols = max((len(r) for r in raw_rows), default=0)
        out["header"] = ["col_%d" % (i + 1) for i in range(ncols)]
        out["header_original"] = []
        data = raw_rows
    ncols = len(out["header"])
    out["n_cols"] = ncols
    rows = []
    for idx, r in enumerate(data):
        if len(r) < ncols:
            out["ragged_rows"].append(idx + 1)
            r = r + [""] * (ncols - len(r))
        elif len(r) > ncols:
            r = r[:ncols]
            out["ragged_rows"].append(idx + 1)
        rows.append(r)
    out["rows"] = rows
    out["n_rows"] = len(rows)
    return out


def col_index(header, name_or_idx):
    if isinstance(name_or_idx, int):
        return name_or_idx if 0 <= name_or_idx < len(header) else None
    low = normalize_token(name_or_idx)
    for i, h in enumerate(header):
        if normalize_token(h) == low:
            return i
    return None


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------
def _dtype_guess(name, kinds, numeric_count, n):
    low = normalize_token(name)
    id_hints = ("id", "no", "code", "sample", "subject", "编号", "样本", "受试者", "标识")
    date_hints = ("date", "time", "日期", "时间", "timestamp")
    if numeric_count > 0 and numeric_count >= 0.8 * max(n, 1):
        return "numeric"
    if any(h in low for h in id_hints):
        return "id"
    if any(h in low for h in date_hints):
        return "date"
    if n == 0:
        return "empty"
    if any(k in MISSING_KINDS for k in kinds) and numeric_count == 0:
        return "text"
    return "categorical"


def profile_table(header, rows):
    n = len(rows)
    profile = {"n_rows": n, "n_cols": len(header), "columns": {}}
    for ci, name in enumerate(header):
        vals = [rows[ri][ci] for ri in range(n)]
        kinds = [classify_value(v) for v in vals]
        # Numeric stats must exclude sentinel/LOD/not-detected cells, otherwise
        # -999 and <LOD pollute the distribution. true_zero is a real measurement.
        nums = [parse_number(v) if classify_value(v) in (KIND_VALID, KIND_TRUE_ZERO) else None
                for v in vals]
        num_present = [v for v in nums if v is not None]
        kind_counts = {}
        for k in kinds:
            kind_counts[k] = kind_counts.get(k, 0) + 1
        n_empty = sum(1 for v in vals if str(v).strip() == "")
        sentinel_tokens = []
        for v in vals:
            if classify_value(v) in MISSING_KINDS and str(v).strip() != "":
                if v not in sentinel_tokens:
                    sentinel_tokens.append(v)
        uniq = set(normalize_token(v) for v in vals)
        col = {
            "index": ci,
            "name": name,
            "n": n,
            "n_empty": n_empty,
            "missing_rate": round(n_empty / n, 6) if n else 0.0,
            "kind_counts": kind_counts,
            "n_numeric": len(num_present),
            "n_text": sum(1 for i, k in enumerate(kinds) if k == KIND_VALID and nums[i] is None),
            "n_unique": len(uniq),
            "unique_ratio": round(len(uniq) / n, 6) if n else 0.0,
            "sentinel_tokens_found": sentinel_tokens[:20],
            "dtype_guess": _dtype_guess(name, kinds, len(num_present), n),
            "_values": nums,
        }
        if num_present:
            s = sorted(num_present)
            q1 = s[int(math.floor((len(s) - 1) * 0.25))]
            q3 = s[int(math.ceil((len(s) - 1) * 0.75))]
            col.update({
                "numeric_min": s[0], "numeric_max": s[-1],
                "numeric_median": s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0,
                "numeric_q1": q1, "numeric_q3": q3,
            })
        profile["columns"][name] = col
    return profile


# ---------------------------------------------------------------------------
# Duplicate / outlier diagnostics (diagnostic only; final actions by Agent)
# ---------------------------------------------------------------------------
def detect_duplicates(header, rows, keys):
    groups = {}
    for ri, row in enumerate(rows):
        parts = []
        for k in keys:
            ci = col_index(header, k)
            parts.append(normalize_token(row[ci]) if ci is not None else "")
        key = "\u001f".join(parts)
        if key.strip("\u001f").strip() == "":
            continue
        groups.setdefault(key, []).append(ri + 1)
    return {k: v for k, v in groups.items() if len(v) > 1}


def outlier_flags(header, rows, bounds=None):
    """bounds: {field: {min, max, action}}. Returns {field: [flags]} with row_id.

    Only real measurements (valid numerics and true zero) enter the distribution;
    entered-missing sentinels and below-LOD / not-detected cells are excluded so a
    -999 sentinel is never reported as an outlier.
    """
    bounds = bounds or {}
    flags = {}
    # Build per-field aligned numeric arrays, None for non-measurements.
    field_vals = {}
    for ci, name in enumerate(header):
        arr = []
        for row in rows:
            kind = classify_value(row[ci]) if ci < len(row) else KIND_ENTERED_MISSING
            if kind in (KIND_VALID, KIND_TRUE_ZERO):
                arr.append(parse_number(row[ci]))
            else:
                arr.append(None)
        field_vals[name] = arr
    for field, arr in field_vals.items():
        num = [v for v in arr if v is not None]
        if len(num) < 2:
            continue
        s = sorted(num)
        q1 = s[int(math.floor((len(s) - 1) * 0.25))]
        q3 = s[int(math.ceil((len(s) - 1) * 0.75))]
        iqr = q3 - q1
        lo = q1 - 3.0 * iqr
        hi = q3 + 3.0 * iqr
        b = bounds.get(field, {})
        bmin = b.get("min")
        bmax = b.get("max")
        action = b.get("action", "keep")
        colflags = []
        for row_id0, v in enumerate(arr):
            if v is None:
                continue
            flag = None
            if bmin is not None and v < bmin:
                flag = "below_min"
            elif bmax is not None and v > bmax:
                flag = "above_max"
            elif iqr > 0 and (v < lo or v > hi):
                flag = "iqr_outlier"
            if flag:
                colflags.append({"row_id": row_id0 + 1, "value": v, "flag": flag, "action": action})
        flags[field] = colflags
    return flags


# ---------------------------------------------------------------------------
# Evidence map (finer-grained provenance schema)
# ---------------------------------------------------------------------------
EVIDENCE_SCHEMA_VERSION = "2.0"


def make_assertion(aid, kind, assertion, locator, quote, material=None,
                   material_sha256=None, field=None, record=None, rule_ref=None,
                   basis=None, confidence="medium", uncertainty=None, change_log_id=None):
    item = {
        "id": aid,
        "kind": kind,
        "assertion": assertion,
        "evidence": {
            "locator": locator,
            "quote": quote,
            "material": material,
            "material_sha256": material_sha256,
        },
        "confidence": confidence,
    }
    if field is not None:
        item["field"] = field
    if record is not None:
        item["record"] = record
    if rule_ref is not None:
        item["rule_ref"] = rule_ref
    if basis is not None:
        item["basis"] = basis
    if uncertainty is not None:
        item["uncertainty"] = uncertainty
    if change_log_id is not None:
        item["change_log_id"] = change_log_id
    return item


def build_evidence_map(case_id, assertions):
    field_index = {}
    counts = {"total": len(assertions), "by_kind": {}, "by_field": {}}
    for a in assertions:
        kind = a.get("kind", "unknown")
        counts["by_kind"][kind] = counts["by_kind"].get(kind, 0) + 1
        field = a.get("field")
        if field:
            field_index.setdefault(field, []).append(a["id"])
            counts["by_field"][field] = counts["by_field"].get(field, 0) + 1
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "case_id": case_id,
        "assertions": assertions,
        "field_index": field_index,
        "counts": counts,
    }


def verify_quote(text, quote):
    """Verbatim substring check after line-ending normalization only."""
    if not quote or not isinstance(quote, str):
        return False
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    q = quote.replace("\r\n", "\n").replace("\r", "\n").strip()
    return q != "" and q in t


# ---------------------------------------------------------------------------
# result.json validation (selfcheck)
# ---------------------------------------------------------------------------
def validate_result(result):
    issues = []
    if not isinstance(result, dict):
        return False, ["result is not a JSON object"]
    if not result.get("case_id"):
        issues.append("missing case_id")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        issues.append("invalid status: %r" % status)
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing field: %s" % key)
        elif not isinstance(result[key], list):
            issues.append("field %s must be a list" % key)
    for i, src in enumerate(result.get("sources", [])):
        if not src.get("locator") or not src.get("quote"):
            issues.append("sources[%d] lacks locator/quote" % i)
    meta = result.get("_meta", {})
    for key in meta.get("contract_keys", []):
        if key not in result:
            issues.append("output_contract key missing from result: %s" % key)
    return (len(issues) == 0), issues
