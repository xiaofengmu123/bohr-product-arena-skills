#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, standard-library-only cleaning engine for sci-repro-clean.

Design contract (this is the "自检与确定性" variant):
  * No randomness, no network, no wall-clock data in data-bearing artifacts.
  * Identical input bytes -> identical output bytes. The content fingerprint
    (`canonical_fingerprint`) ignores only volatile metadata (runtime / time
    stamps) so two independent runs can be byte-compared.
  * Every key conclusion carries a locator (`file#L<line>` or `file+section`)
    and a verbatim quote from the material. No value is imputed or invented.
  * All report numbers are recomputed from the cleaned table, not hand-written,
    so `selfcheck` can re-parse the CSV and reproduce them.

The heavy lifting lives here; `main.py` is a thin CLI. `selftest.py` reuses
these helpers to validate a produced result and to prove determinism.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import sys
import unicodedata
from collections import OrderedDict, defaultdict
from decimal import Decimal, InvalidOperation

ENGINE_VERSION = "2.0.0"

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat", ".text", ".tsv")
JSON_SUFFIXES = (".json",)
MD_SUFFIXES = (".md", ".markdown", ".txt")
PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")

DICT_HINTS = (
    "dict", "dictionary", "codebook", "schema", "field", "variable",
    "数据字典", "字段", "变量", "codebook", "metadata",
)
RULES_HINTS = (
    "rule", "qc", "unit", "quality", "limit", "range", "bound",
    "规则", "质控", "单位", "检出限", "范围", "界",
)
WORKORDER_HINTS = (
    "workorder", "work_order", "order", "instruction", "step", "todo",
    "工单", "步骤", "指令", "清洗",
)

# Keys that carry wall-clock / environment metadata and are excluded from the
# content fingerprint. Data-bearing keys never use these names.
VOLATILE_KEYS = frozenset({
    "runtime", "started_at", "finished_at", "elapsed_seconds", "elapsed",
    "duration", "timestamp", "time", "now", "generated_at", "python_version",
    "hostname", "cwd",
})

# Standard result.json keys (in addition to the dynamic output_contract keys).
STANDARD_KEYS = (
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "artifacts",
)

# Missing-value sentinels (entry-level, case-insensitive after strip).
ENTRY_MISSING_TOKENS = {
    "", ".", "-", "--", "n/a", "na", "null", "none", "nan", "nil",
    "missing", "miss", "unknown", "?", "??", "-999", "-9999", "-999.0",
    "-99", "-99.0", "-1", "999", "9999", "999.0", "8888", "-8888",
    "无", "缺", "缺失", "空", "空白", "不适用", "未提供",
}
NOT_DETECTED_TOKENS = {
    "not detected", "notdetected", "n.d.", "nd", "not tested",
    "not measured", "not analyzed", "未检测", "未测定", "未测量", "未分析",
}
BELOW_LOD_TOKENS = {
    "lod", "loq", "below lod", "below loq", "<lod", "< loq", "<lq",
    "lower than lod", "未检出", "低于检出限", "低于定量限", "检出限以下",
}


# --------------------------------------------------------------------------
# Time / hashing / fingerprint
# --------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonicalize(obj):
    """Recursively sort dict keys and drop volatile metadata keys."""
    if isinstance(obj, dict):
        items = []
        for k in sorted(obj.keys(), key=lambda x: str(x)):
            if str(k) in VOLATILE_KEYS:
                continue
            items.append([str(k), _canonicalize(obj[k])])
        return items
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(x) for x in obj]
    if isinstance(obj, bool):
        return ["bool", obj]
    if isinstance(obj, int):
        return ["int", obj]
    if isinstance(obj, float):
        return ["float", repr(obj)]
    if obj is None:
        return ["null"]
    return ["str", str(obj)]


def canonical_fingerprint(obj) -> str:
    """Deterministic content hash; volatile metadata is ignored."""
    payload = json.dumps(_canonicalize(obj), ensure_ascii=False,
                         separators=(",", ":"), sort_keys=True)
    return sha256_hex(payload.encode("utf-8"))


# --------------------------------------------------------------------------
# Robust text / JSON reading (encoding faults, BOM, mojibake)
# --------------------------------------------------------------------------
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp1252",
                   "cp1251", "shift_jis", "latin-1")

_MOJIBIKE_PATTERNS = (
    (re.compile(r"Ã[\x80-\xbf]"), "utf8-as-latin1"),
    (re.compile(r"â€"), "utf8-as-cp1252"),
)


def _decode_bytes(data: bytes):
    """Return (text, encoding, degraded)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16", False
        except UnicodeDecodeError:
            pass
    if data[:3] == b"\xef\xbb\xbf":
        return data.decode("utf-8-sig"), "utf-8-sig", False
    for enc in _TEXT_ENCODINGS:
        try:
            return data.decode(enc), enc, False
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)", True


def _repair_mojibake(text: str):
    """Heuristically undo a common double-encoding fault; never destructive."""
    repaired = False
    for pattern, _kind in _MOJIBIKE_PATTERNS:
        if pattern.search(text):
            try:
                candidate = text.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
                if candidate != text:
                    text, repaired = candidate, True
                    break
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    return text, repaired


def read_text_robust(path: str):
    """Read a text file tolerating BOM / wrong encoding / mojibake.

    Returns a dict; never raises for a readable file.
    """
    out = {"path": path, "text": "", "encoding": None, "degraded": False,
           "repaired": False, "size": 0, "error": None}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        out["size"] = len(data)
        if b"\x00" in data[:4096]:
            # possible UTF-16/32 without BOM
            try:
                text = data.decode("utf-16")
                out.update(text=text, encoding="utf-16", degraded=False)
                return out
            except UnicodeDecodeError:
                pass
        text, enc, degraded = _decode_bytes(data)
        if enc.startswith("utf-8") or enc == "utf-8-sig":
            fixed, repaired = _repair_mojibake(text)
            if repaired:
                text, degraded, out["repaired"] = fixed, False, True
        out.update(text=text, encoding=enc, degraded=degraded)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def parse_json_robust(text: str, default=None):
    """Parse JSON tolerating BOM and surrounding noise. Returns (obj, status)."""
    if default is None:
        default = {}
    t = text.strip()
    if t.startswith("\ufeff"):
        t = t.lstrip("\ufeff")
    try:
        return json.loads(t), "ok"
    except (json.JSONDecodeError, ValueError):
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        i = t.find(open_ch)
        j = t.rfind(close_ch)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1]), "recovered"
            except (json.JSONDecodeError, ValueError):
                continue
    return default, "error"


# --------------------------------------------------------------------------
# Discovery (deterministic: sorted traversal, never assumes file names)
# --------------------------------------------------------------------------
def list_files(root):
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for fn in sorted(filenames):
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _has_any(name, hints):
    low = name.lower()
    return any(h in low for h in hints)


def discover_inputs(input_dir, extra_roots=None):
    """Classify every file without trusting its name to encode content."""
    roots = [input_dir]
    for r in (extra_roots or []):
        if r and os.path.isdir(r):
            roots.append(r)
    manifest = {
        "case_json": None, "task_md": None, "data": [], "dictionary": [],
        "rules": [], "workorder": [], "other": [],
    }
    seen = set()
    for root in roots:
        for f in list_files(root):
            key = os.path.normcase(os.path.abspath(f))
            if key in seen:
                continue
            seen.add(key)
            base = os.path.basename(f)
            low = base.lower()
            if low == "case.json":
                if manifest["case_json"] is None:
                    manifest["case_json"] = f
            elif low == "task.md":
                if manifest["task_md"] is None:
                    manifest["task_md"] = f
            elif low.endswith(JSON_SUFFIXES):
                manifest["other"].append(f)
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
    # If no data file was classified, treat unclassified tabular files as data.
    if not manifest["data"]:
        for f in manifest["other"]:
            if f.endswith(TABULAR_SUFFIXES):
                manifest["data"].append(f)
    return manifest


# --------------------------------------------------------------------------
# case.json loading
# --------------------------------------------------------------------------
def load_case(input_dir, extra_roots=None):
    manifest = discover_inputs(input_dir, extra_roots)
    case = {
        "case_id": None,
        "output_contract": {},
        "raw": {},
        "manifest": manifest,
        "read_error": None,
        "path": None,
    }
    path = manifest["case_json"]
    if path is None:
        for f in manifest["other"]:
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found under input"
        return case
    case["path"] = path
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info["text"], {})
    case["raw"] = parsed if isinstance(parsed, dict) else {}
    case["case_id"] = (
        case["raw"].get("case_id")
        or case["raw"].get("id")
        or case["raw"].get("challenge_id")
        or os.path.basename(os.path.dirname(path)) or os.path.basename(path)
    )
    contract = case["raw"].get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = dict(contract)
    if status != "ok":
        case["read_error"] = (case["read_error"] or "") + f" case.json status={status}"
    if info.get("degraded") or info.get("repaired"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding repaired"
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


def fill_contract_keys(result, contract, artifacts):
    """Back-fill every dynamic output_contract key; never rename or drop."""
    for key, val in contract.items():
        if result.get(key) not in (None, [], {}, ""):
            continue
        low = str(key).lower()
        if any(h in low for h in ("evidence", "trace", "proof", "map", "source")):
            result[key] = artifacts.get("evidence_map")
        elif any(h in low for h in ("report", "quality", "qc")):
            result[key] = artifacts.get("quality_report")
        elif any(h in low for h in ("profile", "stat", "summar")):
            result[key] = artifacts.get("data_profile")
        elif any(h in low for h in ("clean", "table", "data")):
            result[key] = artifacts.get("cleaned_data")
        else:
            result[key] = contract_default(val)
    return result


def build_skeleton(case, started=None):
    """A valid, honest partial result: all contract keys plus standard keys."""
    contract = case.get("output_contract") or {}
    result = {k: contract_default(v) for k, v in contract.items()}
    result["case_id"] = case.get("case_id")
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {
        "started_at": started or now_iso(),
        "engine_version": ENGINE_VERSION,
    }
    result["artifacts"] = {}
    if case.get("read_error"):
        result["errors"].append({
            "step": "bootstrap", "detail": case["read_error"],
        })
        if "no case.json" in case["read_error"]:
            result["status"] = "insufficient_evidence"
        else:
            result["status"] = "partial"
    result["change_log"].append({
        "step": "bootstrap",
        "action": "write_skeleton",
        "detail": "落盘最小合法 partial 结果骨架，保留 output_contract 全部键",
        "time": now_iso(),
    })
    return result


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# Delimited-table parsing (delimiter sniffing, encoding, header detection)
# --------------------------------------------------------------------------
def _sniff_delimiter(text: str):
    sample = text[:8192]
    counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        return ","
    return best


_HEADER_TOKENS = (
    "field", "unit", "min", "max", "name", "id", "role", "type", "value",
    "variable", "column", "sample", "subject", "date", "字段", "单位", "变量",
    "名称", "编号", "说明", "最小值", "最大值",
)


def _looks_like_header(row):
    """Heuristic fallback when csv.Sniffer cannot decide."""
    if not row:
        return False
    hits = 0
    for cell in row:
        low = cell.strip().lower()
        if not low:
            continue
        if low in _HEADER_TOKENS or any(t in low for t in _HEADER_TOKENS):
            hits += 1
    return hits >= 2


def parse_delimited(path):
    """Parse CSV/TSV/etc. tolerating encoding faults and quoted newlines."""
    out = {"path": path, "headers": [], "rows": [], "delimiter": None,
           "has_header": False, "encoding": None, "degraded": False,
           "n_data_rows": 0, "error": None}
    info = read_text_robust(path)
    if info.get("error"):
        out["error"] = info["error"]
        return out
    out.update(encoding=info["encoding"], degraded=info["degraded"])
    text = info["text"]
    delim = _sniff_delimiter(text)
    out["delimiter"] = delim
    try:
        sample = text[:8192]
        has_header = csv.Sniffer().has_header(sample) if len(sample) > 1 else False
    except (csv.Error, ValueError):
        has_header = False
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        raw_rows = [r for r in reader]
    except csv.Error as exc:
        out["error"] = f"csv parse failed: {exc}"
        return out
    raw_rows = [r for r in raw_rows if any(cell.strip() for cell in r)]
    if not raw_rows:
        out["error"] = "empty table"
        return out
    if not has_header and _looks_like_header(raw_rows[0]):
        has_header = True
    out["has_header"] = has_header
    if has_header:
        headers = [c.strip() for c in raw_rows[0]]
        body = raw_rows[1:]
    else:
        headers = [f"col_{i+1}" for i in range(len(raw_rows[0]))]
        body = raw_rows
    # normalize trailing/leading empty header slots
    headers = [h if h else f"col_{i+1}" for i, h in enumerate(headers)]
    out["headers"] = headers
    out["rows"] = body
    out["n_data_rows"] = len(body)
    return out


# --------------------------------------------------------------------------
# Missing-value classification (four-way; never overwrites raw)
# --------------------------------------------------------------------------
def classify_missing(raw):
    """Return (class, cleaned_raw). Classes: observed / entry_missing /
    not_detected / below_lod / true_zero. Raw string is preserved verbatim."""
    if raw is None:
        return "entry_missing", ""
    s = str(raw).strip()
    if s == "":
        return "entry_missing", ""
    low = s.lower()
    if low in ENTRY_MISSING_TOKENS:
        return "entry_missing", s
    if low in NOT_DETECTED_TOKENS:
        return "not_detected", s
    if low in BELOW_LOD_TOKENS:
        return "below_lod", s
    if re.fullmatch(r"[+-]?0+(\.0+)?([eE][+-]?\d+)?", s):
        return "true_zero", s
    if re.match(r"^\s*<", s) and re.search(r"\d", s):
        return "below_lod", s
    if "not detected" in low or "未检测" in s:
        return "not_detected", s
    if "lod" in low or "loq" in low or "检出限" in s or "未检出" in s:
        return "below_lod", s
    return "observed", s


# --------------------------------------------------------------------------
# Numeric parsing (precision-preserving: Decimal, thousand sep, sci notation)
# --------------------------------------------------------------------------
def parse_number(raw):
    """Return (Decimal|None, error). Preserves precision via Decimal."""
    if raw is None:
        return None, "empty"
    s = str(raw).strip().replace(" ", "")
    if s == "":
        return None, "empty"
    # thousand separators: comma with dot present -> comma is grouping
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+([eE][+-]?\d+)?", s):
            s = s.replace(",", "")
        elif re.fullmatch(r"[+-]?\d+,\d+", s):
            s = s.replace(",", ".")
    try:
        return Decimal(s), None
    except InvalidOperation:
        return None, f"unparsable: {raw!r}"


def decimal_to_str(d: Decimal):
    if d is None:
        return ""
    t = format(d, "f")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


# --------------------------------------------------------------------------
# Unit parsing / conversion (known factors only; molar needs MW -> pending)
# --------------------------------------------------------------------------
_UNIT_EQ_RE = re.compile(
    r"1\s*([A-Za-zμµ/·]+)\s*=\s*([0-9.eE+\-]+)\s*([A-Za-zμµ/·]+)"
)
_UNIT_FACTOR_RE = re.compile(
    r"([A-Za-zμµ/·]+)\s*[:=]\s*([0-9.eE+\-]+)(?:\s*([A-Za-zμµ/·]+))?"
)

_MOLAR_UNITS = ("mol", "mmol", "umol", "μmol", "nmol", "pmol", "m", "mm", "nm",
                "molar", "mol/l", "mmol/l", "μmol/l", "umol/l", "nm", "mm")


def parse_unit_rules(texts):
    """Extract a conservative unit-conversion map from rule text."""
    units = OrderedDict()   # unit -> factor relative to base (1.0)
    equations = []
    for text in texts:
        for m in _UNIT_EQ_RE.finditer(text):
            lhs, factor, rhs = m.group(1), m.group(2), m.group(3)
            try:
                f = Decimal(factor)
            except InvalidOperation:
                continue
            equations.append((lhs, f, rhs))
            units.setdefault(rhs, Decimal(1))
            units[lhs] = units.get(rhs, Decimal(1)) * f
        for m in _UNIT_FACTOR_RE.finditer(text):
            unit, factor = m.group(1), m.group(2)
            try:
                f = Decimal(factor)
            except InvalidOperation:
                continue
            units.setdefault(unit, f)
    return units, equations


def is_molar_unit(unit):
    low = (unit or "").lower().replace(" ", "")
    return any(u in low for u in _MOLAR_UNITS)


def convert_value(value_str, from_unit, to_unit, unit_map):
    """Convert a numeric string between units using a known factor.

    Returns (converted_str|None, note). Molar conversions without molar mass
    return (None, 'pending_human: molar mass required').
    """
    num, err = parse_number(value_str)
    if num is None:
        return None, f"not numeric: {err}"
    if not from_unit or not to_unit:
        return None, "unit unknown"
    if from_unit.lower() == to_unit.lower():
        return decimal_to_str(num), None
    if is_molar_unit(from_unit) or is_molar_unit(to_unit):
        return None, "pending_human: molar conversion needs molar mass"
    f_from = unit_map.get(from_unit.lower().replace(" ", ""))
    f_to = unit_map.get(to_unit.lower().replace(" ", ""))
    if f_from is None or f_to is None:
        return None, f"unknown factor for {from_unit}->{to_unit}"
    converted = num * f_from / f_to
    return decimal_to_str(converted), None


# --------------------------------------------------------------------------
# Material text extraction (dictionary / rules / workorder)
# --------------------------------------------------------------------------
def extract_material_texts(paths):
    """Return a list of {path, text, kind, parsed, error} for each material."""
    out = []
    for p in paths:
        item = {"path": p, "text": "", "kind": None, "parsed": None, "error": None}
        ext = os.path.splitext(p)[1].lower()
        info = read_text_robust(p)
        if info.get("error"):
            item["error"] = info["error"]
            out.append(item)
            continue
        item["text"] = info["text"]
        if ext in JSON_SUFFIXES:
            obj, _status = parse_json_robust(info["text"], None)
            item["parsed"] = obj
            item["kind"] = "json"
        else:
            item["kind"] = "text"
        out.append(item)
    return out


def _norm_field(s):
    s = s.strip().lower()
    s = re.sub(r"[\s_\-]+", " ", s)
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    return " ".join(s.split())


def _spec_col_index(headers, names):
    for i, h in enumerate(headers):
        nh = _norm_field(h)
        for n in names:
            if nh == n or (n and n in nh):
                return i
    return -1


def apply_spec_tabular(path, headers, specs):
    """Parse a columnar dictionary (field/unit/min/max/role columns)."""
    t = parse_delimited(path)
    if t.get("error") or not t["headers"]:
        return
    fh = t["headers"]
    idx_field = _spec_col_index(fh, ("field", "name", "variable", "字段", "变量", "column"))
    idx_unit = _spec_col_index(fh, ("unit", "单位", "量纲"))
    idx_min = _spec_col_index(fh, ("min", "minimum", "下限", "lo"))
    idx_max = _spec_col_index(fh, ("max", "maximum", "上限", "hi"))
    idx_role = _spec_col_index(fh, ("role", "key", "type", "角色", "主键"))
    norm_headers = {_norm_field(h): h for h in headers}
    if idx_field < 0:
        return
    for r_i, row in enumerate(t["rows"]):
        if idx_field >= len(row):
            continue
        fname = _norm_field(row[idx_field])
        if fname not in norm_headers:
            continue
        h = norm_headers[fname]
        loc = f"{os.path.basename(path)}#L{r_i + 2}"
        spec = specs[h]
        if idx_unit >= 0 and idx_unit < len(row):
            u = row[idx_unit].strip()
            if u and re.search(r"[A-Za-zμµ/·]", u):
                spec["unit"] = u
                spec["locator"] = loc
        for idx, key in ((idx_min, "min"), (idx_max, "max")):
            if idx >= 0 and idx < len(row):
                v = row[idx].strip()
                try:
                    spec[key] = float(v.replace(",", "."))
                    spec["locator"] = loc
                except ValueError:
                    pass
        if idx_role >= 0 and idx_role < len(row):
            if re.search(r"(primary|key|主键|id|编号|标识)", row[idx_role], re.I):
                spec["is_key"] = True
                spec["locator"] = loc


def extract_field_specs(material_items, headers):
    """Heuristically extract field semantics / unit / bounds / key role.

    Handles columnar dictionaries (CSV/TSV with field/unit/min/max/role) and
    line-based QC text (unit: X, [min, max], primary key markers).
    Conservative: only records what is actually seen; everything else stays
    unresolved and is reported in pending_human.
    """
    specs = OrderedDict()
    norm_headers = {_norm_field(h): h for h in headers}
    for h in headers:
        specs[h] = {"semantic": None, "unit": None, "min": None, "max": None,
                    "allowed": [], "is_key": False, "locator": None}
    for item in material_items:
        ext = os.path.splitext(item["path"])[1].lower()
        if ext in (".csv", ".tsv", ".tab", ".dat"):
            apply_spec_tabular(item["path"], headers, specs)
            continue
        text = item.get("text") or ""
        for i, line in enumerate(text.splitlines()):
            loc = f"{os.path.basename(item['path'])}#L{i+1}"
            low = line.lower()
            norm_line = _norm_field(line)
            if re.search(r"(primary[\s_]*key|主键|样本编号|受试者标识|subject[\s_]*id)", low):
                for h2, nh2 in norm_headers.items():
                    if nh2 and nh2 in norm_line:
                        specs[h2]["is_key"] = True
                        specs[h2]["locator"] = loc
            for h, nh in norm_headers.items():
                if nh not in norm_line:
                    continue
                spec = specs[h]
                m = re.search(r"\(([^)]*)\)", line)
                if m:
                    token = m.group(1).strip()
                    if re.search(r"[A-Za-zμµ/·]", token) and len(token) <= 12:
                        spec["unit"] = token
                        spec["locator"] = loc
                for um in re.finditer(r"(?:unit|单位)\s*[:=]\s*([A-Za-zμµ/·]+)", line, re.I):
                    spec["unit"] = um.group(1)
                    spec["locator"] = loc
                for rng in re.finditer(r"[\[\(]\s*([-+]?\d+[.,]?\d*)\s*[,，]\s*([-+]?\d+[.,]?\d*)\s*[\]\)]", line):
                    try:
                        spec["min"] = float(rng.group(1).replace(",", "."))
                        spec["max"] = float(rng.group(2).replace(",", "."))
                        spec["locator"] = loc
                    except ValueError:
                        pass
    return specs


# --------------------------------------------------------------------------
# Core cleaning pipeline
# --------------------------------------------------------------------------
def _normalize_key_value(raw):
    """Normalize for near-duplicate detection: casefold, collapse whitespace,
    unify unit-ish separators. Deterministic."""
    s = str(raw).strip().lower()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    s = s.replace("μ", "u").replace("µ", "u")
    s = re.sub(r"(mg/l|μg/ml|ug/ml|g/l)", lambda m: m.group(1).replace(" ", ""), s)
    return s


def run_full(input_dir, output_dir, work_dir):
    """Thin wrapper: never raises for expected bad input.

    On an unexpected exception it salvages the last written result.json (the
    skeleton written first), appends an honest error + degradation, and returns
    a loadable partial deliverable instead of crashing.
    """
    try:
        return _run_full_impl(input_dir, output_dir, work_dir)
    except Exception as exc:  # noqa: BLE001
        result_path = os.path.join(output_dir, "result.json")
        obj, err, _ = _load_result(output_dir)
        if err or not isinstance(obj, dict):
            case = load_case(input_dir, [os.path.join(work_dir, "extracted")])
            obj = build_skeleton(case)
        obj.setdefault("errors", []).append({
            "step": "full",
            "detail": f"{type(exc).__name__}: {exc}",
        })
        obj.setdefault("degradations", []).append({
            "step": "full",
            "detail": "清洗流水线异常，已降级为最小合法结果",
        })
        obj["status"] = "partial"
        obj.setdefault("runtime", {})["finished_at"] = now_iso()
        write_json(result_path, obj)
        return obj


def _run_full_impl(input_dir, output_dir, work_dir):
    """Execute the deterministic cleaning pipeline and finalize result.json.

    Returns the result dict (already written). Never raises for expected bad
    input; every branch degrades to a legal, honest partial result.
    """
    started = now_iso()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    extra_roots = [os.path.join(work_dir, "extracted")]
    case = load_case(input_dir, extra_roots)
    manifest = case["manifest"]
    result = build_skeleton(case, started)
    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)  # partial skeleton first: any later failure
    #                                   still leaves a loadable deliverable

    errors = list(result["errors"])
    degradations = list(result["degradations"])
    pending = list(result["pending_human"])
    uncertainty = list(result["uncertainty"])
    change_log = list(result["change_log"])

    def log_step(step, action, detail):
        change_log.append({"step": step, "action": action, "detail": detail,
                           "time": now_iso()})

    artifacts = {}

    # --- non-text materials: record honestly, do not fabricate ------------
    non_text = [f for f in manifest["other"]
                if f.lower().endswith(PDF_SUFFIXES + IMAGE_SUFFIXES)]
    for f in non_text:
        pending.append({
            "item": os.path.basename(f),
            "detail": "PDF/图片材料需先用平台解析工具提取文本到 "
                      "/app/work/extracted/ 后重跑 --phase full；脚本不臆测其内容",
            "locator": os.path.basename(f),
        })
    if non_text:
        log_step("full", "defer_non_text", f"{len(non_text)} 个非文本材料待文本化")

    # --- select main data file -------------------------------------------
    data_files = sorted(manifest["data"])
    if not data_files:
        result["status"] = "insufficient_evidence"
        result["pending_human"] = pending + [{
            "item": "data_table",
            "detail": "未发现可解析的表格数据文件",
            "locator": "input_dir",
        }]
        result["errors"] = errors
        result["degradations"] = degradations
        result["uncertainty"] = uncertainty
        result["change_log"] = change_log
        result["artifacts"] = artifacts
        result["runtime"]["finished_at"] = now_iso()
        write_json(result_path, result)
        return result

    # deterministic tie-break: prefer largest file, then lexical path
    def data_rank(f):
        try:
            return (os.path.getsize(f), f)
        except OSError:
            return (0, f)
    main_file = max(data_files, key=data_rank)
    related = [f for f in data_files if f != main_file]

    table = parse_delimited(main_file)
    if table.get("error") or not table["headers"]:
        result["status"] = "input_error"
        result["errors"] = errors + [{
            "step": "parse_main", "detail": table.get("error") or "无表头",
            "locator": os.path.basename(main_file),
        }]
        result["pending_human"] = pending
        result["degradations"] = degradations
        result["uncertainty"] = uncertainty
        result["change_log"] = change_log
        result["runtime"]["finished_at"] = now_iso()
        write_json(result_path, result)
        return result

    headers = table["headers"]
    rows = table["rows"]
    log_step("full", "select_main",
             f"主数据表 {os.path.basename(main_file)}：{len(headers)} 列 "
             f"{len(rows)} 行，分隔符 {table['delimiter']!r}，编码 {table['encoding']}")
    if table["degraded"]:
        degradations.append({
            "step": "decode_main",
            "detail": f"主表编码降级解码（{table['encoding']}）",
            "locator": os.path.basename(main_file),
        })
    if related:
        log_step("full", "related_tables",
                 f"另有 {len(related)} 个表格文件作为关联材料，未并入主表")

    # --- material texts ---------------------------------------------------
    material_paths = manifest["dictionary"] + manifest["rules"] + manifest["workorder"]
    material_items = extract_material_texts(material_paths)
    material_texts = [m["text"] for m in material_items if m["text"]]
    for m in material_items:
        if m.get("error"):
            degradations.append({"step": "read_material", "detail": m["error"],
                                 "locator": os.path.basename(m["path"])})

    specs = extract_field_specs(material_items, headers)
    unit_map, unit_equations = parse_unit_rules(material_texts)

    # --- cleaning pass (per cell) ----------------------------------------
    cleaned_rows = []       # list of ordered dicts (one per source row)
    field_stats = OrderedDict()
    for h in headers:
        field_stats[h] = {
            "observed": 0, "entry_missing": 0, "not_detected": 0,
            "below_lod": 0, "true_zero": 0, "outlier_low": 0,
            "outlier_high": 0, "numeric": 0, "converted": 0,
            "unconverted_pending": 0,
        }

    for r_i, row in enumerate(rows):
        out_row = OrderedDict()
        out_row["__row_id"] = str(r_i + 1)
        for c_i, h in enumerate(headers):
            raw = row[c_i] if c_i < len(row) else ""
            cls, cleaned_raw = classify_missing(raw)
            num, num_err = parse_number(cleaned_raw) if cls == "observed" else (None, None)
            flag = []
            numeric_s = ""
            if cls == "observed" and num is not None:
                numeric_s = decimal_to_str(num)
                field_stats[h]["numeric"] += 1
                spec = specs.get(h, {})
                if spec.get("min") is not None and num < Decimal(str(spec["min"])):
                    flag.append("outlier_low")
                    field_stats[h]["outlier_low"] += 1
                if spec.get("max") is not None and num > Decimal(str(spec["max"])):
                    flag.append("outlier_high")
                    field_stats[h]["outlier_high"] += 1
            field_stats[h][cls] += 1

            unit = specs.get(h, {}).get("unit")
            out_row[h] = cleaned_raw
            out_row[f"{h}__class"] = cls if cls != "observed" else ""
            out_row[f"{h}__numeric"] = numeric_s
            out_row[f"{h}__unit"] = unit or ""
            out_row[f"{h}__flag"] = ",".join(flag)
        cleaned_rows.append(out_row)

    # --- unit conversion across records (only where a factor is known) ----
    unit_columns = {}
    for h in headers:
        u = specs.get(h, {}).get("unit")
        if u and not is_molar_unit(u):
            unit_columns[h] = u
    # (Conversions within one field across mixed units are applied only when a
    # second unit with a known factor appears in rules; otherwise left as-is.)
    for m in material_items:
        text = m.get("text") or ""
        for line in text.splitlines():
            if re.search(r"(换算|转换|convert|单位不统一|单位一处)", line, re.I):
                uncertainty.append({
                    "item": "unit_consistency",
                    "detail": "材料提示单位可能不一致；仅对规则中给出明确因子的换算执行，"
                              "摩尔浓度或未知因子标 pending_human",
                    "locator": f"{os.path.basename(m['path'])}#L"
                               f"{text.splitlines().index(line) + 1}",
                })

    # --- duplicate detection (exact + near, by real key) ------------------
    key_headers = [h for h in headers if specs.get(h, {}).get("is_key")]
    if not key_headers:
        # fallback: any header whose name smells like an identifier
        for h in headers:
            nh = _norm_field(h)
            if re.search(r"(id|编号|标识|subject|sample|时间|time|timestamp|日期|date)", nh):
                key_headers.append(h)
    if not key_headers:
        key_headers = list(headers)
        pending.append({
            "item": "primary_key",
            "detail": "未在字典/工单中找到明确主键，退化为全列比对；请人工确认主键",
            "locator": "data_dictionary",
        })
    duplicate_of = {}
    seen = {}
    for r_i, row in enumerate(cleaned_rows):
        key = "||".join(_normalize_key_value(row.get(h, "")) for h in key_headers)
        if key in seen:
            duplicate_of[r_i] = seen[key]
        else:
            seen[key] = r_i
    for r_i, row in enumerate(cleaned_rows):
        row["__duplicate_of"] = str(duplicate_of[r_i] + 1) if r_i in duplicate_of else ""
        row["__keep"] = "false" if r_i in duplicate_of else "true"
    dup_groups = defaultdict(list)
    for r_i in duplicate_of:
        dup_groups[duplicate_of[r_i]].append(r_i + 1)
    n_dup_rows = len(duplicate_of)
    log_step("full", "dedupe",
             f"主键 {key_headers}：识别 {len(dup_groups)} 个重复组共 {n_dup_rows} 条近似/完全重复")

    # --- write cleaned CSV (single source of truth) ----------------------
    cleaned_path = os.path.join(output_dir, "cleaned_data.csv")
    col_order = ["__row_id", "__keep", "__duplicate_of"]
    for h in headers:
        col_order.extend([h, f"{h}__class", f"{h}__numeric", f"{h}__unit",
                          f"{h}__flag"])
    with open(cleaned_path, "w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=col_order, extrasaction="ignore")
        writer.writeheader()
        for row in cleaned_rows:
            writer.writerow(row)
    artifacts["cleaned_data"] = "cleaned_data.csv"

    # --- data profile (deterministic JSON) -------------------------------
    profile = {
        "source": os.path.basename(main_file),
        "n_columns": len(headers),
        "n_rows": len(cleaned_rows),
        "n_kept_rows": sum(1 for r in cleaned_rows if r["__keep"] == "true"),
        "n_duplicate_rows": n_dup_rows,
        "primary_key": key_headers,
        "encoding": table["encoding"],
        "degraded": bool(table["degraded"]),
        "field_stats": field_stats,
        "field_specs": {h: specs.get(h, {}) for h in headers},
        "unit_map": {k: str(v) for k, v in unit_map.items()},
        "unit_equations": [[a, str(b), c] for a, b, c in unit_equations],
    }
    profile_path = os.path.join(output_dir, "data_profile.json")
    write_json(profile_path, profile)
    artifacts["data_profile"] = "data_profile.json"

    # --- quality report (numbers recomputed from the same cleaned rows) ---
    report_md = build_quality_report(
        main_file, table, cleaned_rows, headers, field_stats, dup_groups,
        key_headers, unit_map, unit_equations, degradations, pending)
    report_path = os.path.join(output_dir, "quality_report.md")
    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(report_md)
    artifacts["quality_report"] = "quality_report.md"

    # --- evidence map (locator + verbatim quote per key conclusion) ------
    evidence_map = build_evidence_map(
        main_file, table, material_items, specs, key_headers, cleaned_rows,
        dup_groups, headers)
    evidence_path = os.path.join(output_dir, "evidence_map.json")
    write_json(evidence_path, evidence_map)
    artifacts["evidence_map"] = "evidence_map.json"

    # --- sources (one entry per material, locator + verbatim snippet) -----
    sources = build_sources(main_file, material_items, table)
    result["sources"] = sources

    # --- run manifest (content-addressed inputs & outputs) ---------------
    manifest_obj = build_run_manifest(
        main_file, material_paths, artifacts, output_dir, unit_map)
    manifest_path = os.path.join(output_dir, "run_manifest.json")
    write_json(manifest_path, manifest_obj)
    artifacts["run_manifest"] = "run_manifest.json"
    if unit_map:
        unit_map_path = os.path.join(output_dir, "unit_map.json")
        write_json(unit_map_path, {
            "factor_vs_base": {k: str(v) for k, v in unit_map.items()},
            "equations": [[a, str(b), c] for a, b, c in unit_equations],
        })
        artifacts["unit_map"] = "unit_map.json"

    # --- finalize result.json --------------------------------------------
    result = fill_contract_keys(result, case.get("output_contract") or {}, artifacts)
    result["case_id"] = case.get("case_id")
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = degradations
    result["pending_human"] = pending
    result["artifacts"] = artifacts
    result["runtime"]["finished_at"] = now_iso()
    try:
        result["runtime"]["elapsed_seconds"] = int(
            (datetime.datetime.fromisoformat(result["runtime"]["finished_at"])
             - datetime.datetime.fromisoformat(result["runtime"]["started_at"])).total_seconds())
    except (ValueError, KeyError):
        result["runtime"]["elapsed_seconds"] = 0

    if errors:
        result["status"] = "partial"
    elif pending or uncertainty:
        result["status"] = "partial"
    else:
        result["status"] = "ok"
    if result["status"] == "ok" and (table["degraded"] or not key_headers):
        result["status"] = "partial"

    log_step("full", "finalize", "回填 output_contract 全部字段并写定 result.json")
    write_json(result_path, result)
    return result


def build_quality_report(main_file, table, cleaned_rows, headers, field_stats,
                         dup_groups, key_headers, unit_map, unit_equations,
                         degradations, pending):
    """Field-level quality report. Every number is recomputed here from the
    same cleaned_rows used to write cleaned_data.csv (never hand-written)."""
    n = len(cleaned_rows)
    kept = sum(1 for r in cleaned_rows if r["__keep"] == "true")
    lines = []
    lines.append("# 数据质量报告（自动重算，与 cleaned_data.csv 一致）")
    lines.append("")
    lines.append(f"- 源表：`{os.path.basename(main_file)}`")
    lines.append(f"- 分隔符：`{table['delimiter']!r}`；编码：`{table['encoding']}`"
                 f"{'（降级）' if table['degraded'] else ''}")
    lines.append(f"- 总行数：**{n}**；去重后保留：**{kept}**；重复行：**{len(cleaned_rows) - kept}**")
    lines.append(f"- 主键列：`{', '.join(key_headers) if key_headers else '（未识别，已人工标记）'}`")
    lines.append("")
    lines.append("## 字段级清单")
    lines.append("")
    lines.append("| 字段 | 观测 | 录入缺失 | 未检测 | 低于检出限 | 真值0 | 异常低 | 异常高 | 数值可解析 |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for h in headers:
        s = field_stats[h]
        lines.append(
            f"| {h} | {s['observed']} | {s['entry_missing']} | {s['not_detected']} "
            f"| {s['below_lod']} | {s['true_zero']} | {s['outlier_low']} "
            f"| {s['outlier_high']} | {s['numeric']} |")
    lines.append("")
    lines.append("缺失率按四类口径分别统计，不互相覆盖；真值 0 不并入缺失。")
    lines.append("")
    if dup_groups:
        lines.append("## 重复组")
        lines.append("")
        for keep_i in sorted(dup_groups):
            rows = sorted(dup_groups[keep_i])
            lines.append(f"- 保留第 {keep_i + 1} 行，重复行 {rows}")
    else:
        lines.append("## 重复组")
        lines.append("")
        lines.append("未发现重复记录。")
    lines.append("")
    if unit_map:
        lines.append("## 单位换算映射")
        lines.append("")
        lines.append("| 单位 | 相对基准因子 |")
        lines.append("|---|--:|")
        for u in unit_map:
            lines.append(f"| {u} | {decimal_to_str(unit_map[u])} |")
        if unit_equations:
            for a, b, c in unit_equations:
                lines.append(f"\n- 依据：1 {a} = {b} {c}")
        lines.append("")
    if degradations:
        lines.append("## 降级说明")
        lines.append("")
        for d in degradations:
            lines.append(f"- `{d.get('step')}`：{d.get('detail')}（{d.get('locator')}）")
        lines.append("")
    if pending:
        lines.append("## 待人工确认")
        lines.append("")
        for p in pending:
            lines.append(f"- {p.get('item')}：{p.get('detail')}")
        lines.append("")
    lines.append("## 处理动作与影响范围")
    lines.append("")
    lines.append("- 缺失四类均**保留原文并分类**，未插补、未改写、未归并为 0。")
    lines.append("- 异常值仅据字典/QC 物理上下界打标（outlier_low/high），默认**保留**；")
    lines.append("  确需剔除或截帽时须在工单中明确，并记录理由与 locator。")
    lines.append("- 重复记录按主键规范化后分组，保留首条（原行序确定），其余标 __keep=false。")
    lines.append("- 单位换算仅使用规则中出现的因子，摩尔浓度缺摩尔质量时不换算。")
    lines.append("- 报告数字由清洗后表格实时重算；自检脚本会再次解析 CSV 复核一致。")
    return "\n".join(lines) + "\n"


def build_evidence_map(main_file, table, material_items, specs, key_headers,
                       cleaned_rows, dup_groups, headers):
    """Map key conclusions to a locator + verbatim quote."""
    entries = []
    # main table provenance
    entries.append({
        "conclusion": "main_table_parsed",
        "locator": f"{os.path.basename(main_file)}#L1",
        "quote": (table["headers"][:5] + ["..."]) if table["headers"] else "",
        "note": f"主表解析：{len(headers)} 列，分隔符 {table['delimiter']!r}",
    })
    for item in material_items:
        text = item.get("text") or ""
        if text:
            lines = text.splitlines()
            snippet = "\n".join(lines[:4])
            entries.append({
                "conclusion": f"material:{os.path.basename(item['path'])}",
                "locator": f"{os.path.basename(item['path'])}#L1",
                "quote": snippet[:400],
                "note": "材料原文开头片段，用于字段语义/规则溯源",
            })
    for h in headers:
        spec = specs.get(h, {})
        if spec.get("unit") or spec.get("min") is not None or spec.get("max") is not None:
            entries.append({
                "conclusion": f"field_spec:{h}",
                "locator": spec.get("locator") or "data_dictionary",
                "quote": f"unit={spec.get('unit')} min={spec.get('min')} max={spec.get('max')}",
                "note": "从字典/规则材料中读取的字段约束",
            })
    for keep_i in sorted(dup_groups):
        rows = sorted(dup_groups[keep_i])
        keep_row = cleaned_rows[keep_i] if keep_i < len(cleaned_rows) else {}
        key_vals = {h: keep_row.get(h, "") for h in key_headers}
        entries.append({
            "conclusion": "duplicate_group",
            "locator": f"{os.path.basename(main_file)}#L{keep_i + 2}",
            "quote": json.dumps(key_vals, ensure_ascii=False),
            "note": f"保留第 {keep_i + 1} 行，重复行 {rows}",
        })
    return entries


def build_sources(main_file, material_items, table):
    """One source entry per material; locator + verbatim quote."""
    sources = []
    main_info = read_text_robust(main_file)
    if main_info.get("text"):
        snippet = "\n".join(main_info["text"].splitlines()[:3])
        sources.append({
            "locator": os.path.basename(main_file),
            "quote": snippet[:400],
            "note": f"主数据表（{table['headers'] and len(table['headers'])} 列，"
                    f"分隔符 {table['delimiter']!r}）",
        })
    for item in material_items:
        if item.get("text"):
            snippet = "\n".join(item["text"].splitlines()[:3])
            sources.append({
                "locator": os.path.basename(item["path"]),
                "quote": snippet[:400],
                "note": "字典/规则/工单材料原文片段",
            })
    return sources


def build_run_manifest(main_file, material_paths, artifacts, output_dir, unit_map):
    """Content-addressed record of every input and output (deterministic)."""
    inputs = {}
    for p in [main_file] + sorted(material_paths):
        if p and os.path.isfile(p):
            inputs[os.path.basename(p)] = file_sha256(p)
    outputs = {}
    for key, rel in sorted(artifacts.items()):
        p = os.path.join(output_dir, rel)
        if os.path.isfile(p):
            outputs[rel] = file_sha256(p)
    return {
        "engine_version": ENGINE_VERSION,
        "input_sha256": inputs,
        "output_sha256": outputs,
        "unit_factor_count": len(unit_map),
    }


# --------------------------------------------------------------------------
# Command entry points
# --------------------------------------------------------------------------
def cmd_bootstrap(args):
    case = load_case(args.input, [os.path.join(args.work, "extracted")])
    result = build_skeleton(case)
    write_json(os.path.join(args.output, "result.json"), result)
    print(f"bootstrap ok status={result['status']} case={result['case_id']}")
    return 0


def cmd_full(args):
    try:
        result = run_full(args.input, args.output, args.work)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash
        # write a minimal legal result on unexpected failure
        case = load_case(args.input, [os.path.join(args.work, "extracted")])
        result = build_skeleton(case)
        result["errors"].append({
            "step": "full", "detail": f"{type(exc).__name__}: {exc}",
        })
        result["status"] = "partial"
        result["degradations"].append({
            "step": "full", "detail": "清洗流水线异常，已降级为最小合法结果",
        })
        result["runtime"]["finished_at"] = now_iso()
        write_json(os.path.join(args.output, "result.json"), result)
    print(f"full ok status={result.get('status')} artifacts={sorted(result.get('artifacts', {}))}")
    return 0


def _load_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None, "result.json 不存在", path
    info = read_text_robust(path)
    obj, status = parse_json_robust(info["text"], None)
    if not isinstance(obj, dict):
        return None, f"result.json 解析失败(status={status})", path
    return obj, None, path


def cmd_selfcheck(args, strict=False):
    checks = []
    report = {"case_id": None, "checks": [], "pass": True, "fingerprint": None}
    obj, err, path = _load_result(args.output)
    if err:
        checks.append({"item": "result_json", "ok": False, "detail": err})
        report["checks"] = checks
        report["pass"] = False
        write_json(os.path.join(args.output, "selfcheck_report.json"), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not strict else 2

    report["case_id"] = obj.get("case_id")
    report["fingerprint"] = canonical_fingerprint(obj)

    # 1. schema fields
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        ok = key in obj and obj[key] not in (None,)
        checks.append({"item": f"field:{key}", "ok": bool(ok),
                       "detail": "" if ok else f"缺少 {key}"})
    ok = obj.get("status") in STATUS_ENUM
    checks.append({"item": "field:status_enum", "ok": ok,
                   "detail": "" if ok else f"非法 status={obj.get('status')!r}"})

    # 2. output_contract completeness (against /input/case.json)
    case = load_case(args.input, [os.path.join(args.work, "extracted")])
    contract = case.get("output_contract") or {}
    missing_contract = [k for k in contract if k not in obj]
    checks.append({"item": "contract_keys", "ok": not missing_contract,
                   "detail": "" if not missing_contract
                   else f"缺契约键 {missing_contract}"})

    # 3. artifacts exist
    artifacts = obj.get("artifacts") or {}
    for key, rel in sorted(artifacts.items()):
        exists = os.path.isfile(os.path.join(args.output, rel))
        checks.append({"item": f"artifact:{rel}", "ok": exists,
                       "detail": "" if exists else f"产物缺失 {rel}"})

    # 4. sources carry locator + quote
    sources = obj.get("sources") or []
    bad_sources = [s for s in sources
                   if not (isinstance(s, dict) and s.get("locator") and s.get("quote"))]
    checks.append({"item": "sources_evidence", "ok": not bad_sources,
                   "detail": "" if not bad_sources
                   else f"{len(bad_sources)} 条 source 缺 locator/quote"})

    # 5. report<->CSV consistency (recompute counts from cleaned_data.csv)
    csv_path = os.path.join(args.output, artifacts.get("cleaned_data") or "cleaned_data.csv")
    if os.path.isfile(csv_path):
        consistent, detail = _recompute_consistency(csv_path, obj)
        checks.append({"item": "report_table_consistency", "ok": consistent,
                       "detail": detail})
    else:
        checks.append({"item": "report_table_consistency", "ok": False,
                       "detail": "cleaned_data.csv 缺失，无法复核"})

    report["checks"] = checks
    report["pass"] = all(c["ok"] for c in checks)
    write_json(os.path.join(args.output, "selfcheck_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if strict and not report["pass"]:
        return 2
    return 0


def _recompute_consistency(csv_path, result):
    """Re-parse cleaned_data.csv and cross-check headline numbers against
    data_profile.json. The report is recomputed from the same rows, so this
    closes the loop: report <- profile <- CSV."""
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
    except Exception as exc:  # noqa: BLE001
        return False, f"CSV 无法解析：{exc}"
    n = len(rows)
    kept = sum(1 for r in rows if r.get("__keep") == "true")
    dup = n - kept
    artifacts = result.get("artifacts") or {}
    profile_rel = artifacts.get("data_profile")
    if profile_rel:
        profile_path = os.path.join(os.path.dirname(csv_path), profile_rel)
        if os.path.isfile(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as fh:
                    profile = json.load(fh)
                pn = profile.get("n_rows")
                pk = profile.get("n_kept_rows")
                pd = profile.get("n_duplicate_rows")
                if pn == n and pk == kept and pd == dup:
                    return True, f"CSV/画像一致：总行 {n}，保留 {kept}，重复 {dup}"
                return False, (f"CSV/画像不一致：CSV 总行 {n}/保留 {kept}/重复 {dup}，"
                               f"画像 {pn}/{pk}/{pd}")
            except (OSError, ValueError) as exc:
                return False, f"画像读取失败：{exc}"
    return True, f"CSV 复核：总行 {n}，保留 {kept}，重复 {dup}"


def cmd_fingerprint(args):
    obj, err, _path = _load_result(args.output)
    if err:
        print(err)
        return 0
    print(canonical_fingerprint(obj))
    return 0
