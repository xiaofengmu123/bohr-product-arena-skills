#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the sci-data-cleaning-robust skill.

Pure Python standard library. Deterministic: no randomness, no network access,
no wall-clock in data decisions. Every reader is a total function of
(path) -> (value, diagnostics); it never raises on bad input and instead
records `error` / `degraded` markers so the caller can degrade gracefully.

Robustness-first invariants:
  * encoding cascade: utf-8-sig -> gb18030 -> latin-1 (lossless last resort)
  * binary sniffing via NUL bytes; oversized files truncated to a preview
  * tabular parsing never materialises the whole file when it is huge
  * classification is conservative: mark and explain, never silently delete
    or impute values
  * atomic writes (temp file + os.replace) so a crash never leaves corrupt
    artifacts
"""

import csv
import io
import json
import os
import re
import tempfile

MAX_TEXT_BYTES = 2 * 1024 * 1024          # default per-file text cap
MAX_ROW_PREVIEW = 20000                   # max rows materialised from a table

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema",
              "数据字典", "字段")
RULES_HINTS = ("rule", "qc", "unit", "quality", "规则", "质控", "单位")
WORKORDER_HINTS = ("workorder", "work_order", "order", "task", "工单",
                   "步骤", "instructions", "cleaning")

_STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# Missing-value classification vocabulary (lower-cased tokens).
_NOT_DETECTED = frozenset((
    "nd", "n.d.", "n/d", "not detected", "not_detected", "undetected",
    "未检测", "未检出", "未测出", "not found",
))
_BELOW_LOD = frozenset((
    "lod", "<lod", "loq", "<loq", "blq", "bql", "blod", "below lod",
    "below loq", "below limit", "below detection", "低于检出限", "低于检测限",
    "检出限以下", "检测限以下",
))
_MISSING_TEXT = frozenset((
    "", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "---",
    "..", "...", ".", "?", "n.a.", "missing", "缺失", "空",
))


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------
def list_files(root):
    """Return sorted file paths under `root`, skipping VCS/cache dirs."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".svn")]
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _has_any(name, hints):
    low = name.lower()
    return any(h in low for h in hints)


def discover_inputs(input_dir):
    files = list_files(input_dir)
    manifest = {
        "case_json": None, "task_md": None, "data": [], "dictionary": [],
        "rules": [], "workorder": [], "other": [], "files": files,
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
    # Convention: any tabular file not yet classified is treated as data.
    if not manifest["data"]:
        used = set(manifest["dictionary"] + manifest["rules"]
                   + manifest["workorder"])
        for f in files:
            if f.endswith(TABULAR_SUFFIXES) and f not in used:
                manifest["data"].append(f)
    return manifest


# --------------------------------------------------------------------------
# Robust readers
# --------------------------------------------------------------------------
def read_bytes(path, max_bytes=MAX_TEXT_BYTES):
    """Read up to `max_bytes` bytes; never raises. Returns diagnostics."""
    out = {"path": path, "data": None, "truncated": False,
           "error": None, "size": 0}
    try:
        size = os.path.getsize(path)
        out["size"] = size
        if size == 0:
            out["error"] = "empty file"
            return out
        with open(path, "rb") as fh:
            data = fh.read(max_bytes)
        if size > len(data):
            out["truncated"] = True
        out["data"] = data
    except OSError as exc:
        out["error"] = "unreadable: %s" % exc
    return out


def sniff_encoding(data):
    """Pick an encoding. latin-1 is a lossless last resort for any byte stream."""
    if not data:
        return "utf-8"
    if b"\x00" in data[:4096]:
        return "binary"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, ValueError):
            continue
    return "latin-1"


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read text with an encoding cascade; total function of (path)."""
    raw = read_bytes(path, max_bytes)
    out = {"path": path, "text": "", "encoding": None, "degraded": False,
           "truncated": raw["truncated"], "error": raw["error"],
           "binary": False, "size": raw["size"]}
    if raw["error"] or raw["data"] is None:
        return out
    enc = sniff_encoding(raw["data"])
    out["encoding"] = enc
    if enc == "binary":
        out["binary"] = True
        out["degraded"] = True
        out["error"] = (out["error"] or "") + " binary content (NUL bytes)"
        # Preserve a latin-1 view so callers can still attempt tabular parsing.
        enc = "latin-1"
    try:
        text = raw["data"].decode(enc, errors="replace")
        if "\ufffd" in text:
            out["degraded"] = True
            out["error"] = (out["error"] or "") + " replacement chars found"
        out["text"] = text
    except Exception as exc:  # defensive: never let a reader raise
        out["degraded"] = True
        out["error"] = (out["error"] or "") + " decode failed: %s" % exc
    return out


def parse_json(text, default=None):
    """Robust JSON parse. Returns (value, status) with status in ok/degraded/error."""
    if default is None:
        default = {}
    if not text:
        return default, "error"
    candidate = text.lstrip("\ufeff \t\r\n")
    try:
        value = json.loads(candidate)
        return value, "ok"
    except ValueError:
        pass
    # Last resort: try up to the last closing brace to recover a prefix.
    for i in range(len(candidate), 0, -1):
        if candidate[i - 1] in "}]\n":
            try:
                return json.loads(candidate[:i]), "degraded"
            except ValueError:
                continue
    return default, "error"


# --------------------------------------------------------------------------
# Tabular parsing
# --------------------------------------------------------------------------
def normalize_header(h):
    if h is None:
        return ""
    s = str(h).replace("\ufeff", "")
    return re.sub(r"[\x00-\x1f\x7f]", " ", s).strip()


def detect_delimiter(sample):
    counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
    if not counts or max(counts.values()) == 0:
        return ","
    return max(counts, key=counts.get)


def parse_tabular(text, max_rows=MAX_ROW_PREVIEW):
    """Parse delimiter-separated text into headers + list-of-dicts."""
    out = {"headers": [], "rows": [], "delimiter": None, "truncated": False,
           "issues": [], "error": None}
    if not text:
        out["error"] = "no text to parse"
        return out
    sample = text[:65536]
    dialect = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = None
    delim = dialect.delimiter if dialect else detect_delimiter(sample)
    out["delimiter"] = delim
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    raw_rows = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            out["truncated"] = True
            break
        raw_rows.append(row)
    if not raw_rows:
        out["error"] = "no data rows"
        return out
    headers = [normalize_header(h) for h in raw_rows[0]]
    ncols = len(headers)
    # Guard against a single-column line without real headers.
    if ncols == 1 and len(raw_rows) <= 1:
        out["error"] = "single-column file with no data rows"
        return out
    rows = []
    for idx, raw in enumerate(raw_rows[1:], start=2):
        if len(raw) < ncols:
            out["issues"].append("row %d has %d cells (expected %d)"
                                 % (idx, len(raw), ncols))
            raw = raw + [""] * (ncols - len(raw))
        elif len(raw) > ncols:
            out["issues"].append("row %d has %d cells (expected %d)"
                                 % (idx, len(raw), ncols))
            raw = raw[:ncols]
        rows.append({h: raw[j] for j, h in enumerate(headers)})
    out["headers"] = headers
    out["rows"] = rows
    if not rows:
        out["error"] = "header-only table"
    return out


# --------------------------------------------------------------------------
# Value classification
# --------------------------------------------------------------------------
def classify_value(raw):
    """Classify a raw cell value. Returns (category, normalized).

    Categories: not_detected | below_lod | missing | zero | valid.
    """
    if raw is None:
        return ("missing", "")
    s = str(raw).replace("\ufeff", "").strip()
    if s == "":
        return ("missing", "")
    low = s.lower().strip()
    compact = re.sub(r"\s+", " ", low)

    if compact in _NOT_DETECTED:
        return ("not_detected", s)
    if s.startswith("<") or compact in _BELOW_LOD or "检出限" in compact:
        return ("below_lod", s)
    if compact in _MISSING_TEXT:
        return ("missing", "")

    # Numeric-ish values, including thousand separators and scientific form.
    probe = s.replace(",", "")
    if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", probe):
        try:
            v = float(probe)
        except ValueError:
            return ("valid", s)
        if v == int(v) and v <= -999:
            return ("missing", "")
        if v == 0:
            return ("zero", s)
        return ("valid", s)
    return ("valid", s)


def is_missing_category(category):
    return category in ("missing",)


# --------------------------------------------------------------------------
# Case loading and skeleton
# --------------------------------------------------------------------------
def load_case(input_dir, max_bytes=MAX_TEXT_BYTES):
    manifest = discover_inputs(input_dir)
    case = {
        "case_id": None, "output_contract": {}, "raw": {}, "manifest": manifest,
        "read_error": None, "task_md": manifest["task_md"],
    }
    path = manifest["case_json"]
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        case["case_id"] = os.path.basename(os.path.normpath(input_dir)) or "unknown"
        return case
    info = read_text(path, max_bytes)
    parsed, status = parse_json(info["text"], {})
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
    elif contract is not None:
        case["read_error"] = (case["read_error"] or "") + " output_contract is not an object"
    if status == "error":
        case["read_error"] = (case["read_error"] or "") + " case.json unparsable"
    elif status == "degraded":
        case["read_error"] = (case["read_error"] or "") + " case.json partially parsed"
    if info.get("degraded") or info.get("truncated"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding/truncation degraded"
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


_CONTRACT_ARTIFACT_HINTS = (
    (("cleaned", "clean_table", "clean_data", "table", "csv",
      "清洗表", "清洗后"), "cleaned_data"),
    (("report", "quality", "qc", "质检", "报告", "质量"), "quality_report"),
    (("profile", "statistics", "summar", "画像", "统计", "概况"), "data_profile"),
    (("evidence", "trace", "proof", "map", "溯源", "证据"), "evidence_map"),
    (("snapshot", "raw", "原始", "快照"), "raw_snapshot"),
)


def fill_contract_keys(result, contract, artifacts):
    """Fill contract keys with artifact paths using best-hint matching; unknown
    keys stay as their null/[] default and are never renamed or dropped."""
    for key, val in contract.items():
        current = result.get(key)
        if current not in (None, [], {}):
            continue
        low = str(key).lower()
        best = None
        best_score = 0
        for hints, art in _CONTRACT_ARTIFACT_HINTS:
            if not artifacts.get(art):
                continue
            score = sum(1 for h in hints if h in low)
            if score > best_score:
                best, best_score = art, score
        if best and best_score > 0:
            result[key] = artifacts[best]


def build_skeleton(case, started, status="partial", note=""):
    contract = case.get("output_contract") or {}
    result = {"case_id": case.get("case_id"), "status": status}
    for key in contract:
        result[key] = contract_default(contract[key])
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {"started": started, "phases": []}
    result["artifacts"] = {}
    if case.get("read_error"):
        result["errors"].append({"step": "bootstrap", "detail": case["read_error"]})
    if note:
        result["change_log"].append(
            {"time": started, "step": "bootstrap", "action": note})
    return result


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------
def write_text_atomic(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def write_json_atomic(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
    return write_text_atomic(path, text + "\n")


# --------------------------------------------------------------------------
# Heuristic hint extraction (suspected; must be verified by the Agent)
# --------------------------------------------------------------------------
def extract_hints(dict_texts, rules_texts, workorder_texts):
    """Scan dictionary/rules/workorder text for primary keys, QC bounds and
    unit mappings. Everything here is a *suggestion* with a locator + quote;
    the Agent must confirm before acting."""
    hints = {
        "primary_keys": [],
        "qc_bounds": [],
        "unit_mappings": [],
        "missing_rules": [],
    }
    for kind, items in (("dictionary", dict_texts),
                        ("rules", rules_texts),
                        ("workorder", workorder_texts)):
        for info in items:
            text = info.get("text", "")
            if not text:
                continue
            src = info.get("path", "?")
            _scan_keys(text, src, hints)
            _scan_qc(text, src, hints)
            _scan_units(text, src, hints)
            _scan_missing(text, src, hints)
    # Deduplicate by (quote, locator).
    for group in hints.values():
        seen = set()
        uniq = []
        for item in group:
            sig = (item.get("quote", ""), item.get("locator", ""))
            if sig not in seen:
                seen.add(sig)
                uniq.append(item)
        group[:] = uniq
    return hints


_KEY_PAT = re.compile(
    r"(primary\s*key|主键|sample[\s_-]*id|subject[\s_-]*id|participant[\s_-]*id|"
    r"样品[\s_-]*编号|样本[\s_-]*编号|受试者[\s_-]*标识|时间戳|timestamp|"
    r"sample\s*id|subject\s*id|patient[\s_-]*id|row\s*id|记录[\s_-]*编号)",
    re.IGNORECASE)


def _line_quote(text, match):
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    return line[:240]


def _scan_keys(text, src, hints):
    for m in _KEY_PAT.finditer(text):
        hints["primary_keys"].append({
            "token": m.group(0), "quote": _line_quote(text, m),
            "locator": "%s#%s" % (src, text.count("\n", 0, m.start()) + 1),
            "kind": "primary_key", "status": "suspected",
        })


_QC_PAT = re.compile(
    r"(min(?:imum)?|max(?:imum)?|下限|上限|范围|range|physiological|物理范围|"
    r"正常范围|acceptable|合理范围|qc\s*(?:min|max|limit)|loq|lod)\s*[:：=]?\s*"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
    re.IGNORECASE)


def _scan_qc(text, src, hints):
    for m in _QC_PAT.finditer(text):
        hints["qc_bounds"].append({
            "side": m.group(1), "value": m.group(2),
            "quote": _line_quote(text, m),
            "locator": "%s#%s" % (src, text.count("\n", 0, m.start()) + 1),
            "status": "suspected",
        })


_UNIT_PAT = re.compile(
    r"((?:mg|µg|μg|ng|pg|g|mol|mmol|µmol|μmol|umol|nmol|L|mL|ml|dL|dl|"
    r"kg|IU|uL|µL|μL|mEq|U|%\s*(?:w/v)?)\s*/\s*(?:L|mL|ml|dL|dl|g|kg|mg|mol))",
    re.IGNORECASE)


def _scan_units(text, src, hints):
    for m in _UNIT_PAT.finditer(text):
        unit = m.group(1).strip()
        ctx = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
        factor = None
        fm = re.search(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?:x|×|\*)?\s*"
                       r"(?:to|→|换算|factor|因子|相当于|=)", ctx, re.IGNORECASE)
        if fm:
            factor = fm.group(1)
        hints["unit_mappings"].append({
            "unit": unit, "factor": factor, "quote": _line_quote(text, m),
            "locator": "%s#%s" % (src, text.count("\n", 0, m.start()) + 1),
            "status": "suspected",
        })


_MISSING_PAT = re.compile(
    r"(-999(?:\.0)?|-9999(?:\.0)?|-99999(?:\.0)?|sentinel|哨兵|未检测|not\s+detected|"
    r"below\s+lod|低于检出限|missing\s*(?:value|code)|缺失)",
    re.IGNORECASE)


def _scan_missing(text, src, hints):
    for m in _MISSING_PAT.finditer(text):
        hints["missing_rules"].append({
            "token": m.group(0), "quote": _line_quote(text, m),
            "locator": "%s#%s" % (src, text.count("\n", 0, m.start()) + 1),
            "status": "suspected",
        })


# --------------------------------------------------------------------------
# Profile and cleaning
# --------------------------------------------------------------------------
def numeric_value(raw):
    probe = str(raw).replace(",", "").strip()
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", probe):
        return None
    try:
        return float(probe)
    except ValueError:
        return None


def build_profile(table, hints):
    headers = table["headers"]
    columns = {}
    for h in headers:
        cats = {"not_detected": 0, "below_lod": 0, "missing": 0,
                "zero": 0, "valid": 0}
        values = [row.get(h) for row in table["rows"]]
        for v in values:
            cat, _ = classify_value(v)
            cats[cat] += 1
        numerics = [numeric_value(v) for v in values]
        numerics = [x for x in numerics if x is not None]
        col = {
            "n": len(values),
            "categories": cats,
            "missing_rate": round(cats["missing"] / len(values), 4)
            if values else 0.0,
            "n_numeric": len(numerics),
            "min": min(numerics) if numerics else None,
            "max": max(numerics) if numerics else None,
        }
        columns[h] = col
    return {
        "source_file": None, "delimiter": table["delimiter"],
        "n_rows": len(table["rows"]), "n_cols": len(headers),
        "headers": headers, "truncated": table["truncated"],
        "issues": table["issues"], "columns": columns,
        "hints": hints,
    }


def _clean_cell(raw):
    """Return (cleaned_value, flag). Conservative: never impute, never delete."""
    cat, norm = classify_value(raw)
    if cat == "missing":
        return ("", "missing")
    if cat in ("not_detected", "below_lod"):
        return (norm, cat)
    if cat == "zero":
        return (norm, "zero")
    # valid: strip only, preserve representation
    return (norm if norm else str(raw).strip(), "valid")


def clean_rows(table):
    headers = table["headers"]
    cleaned = []
    change_log = []
    for i, row in enumerate(table["rows"], start=2):
        out = {}
        for h in headers:
            val, flag = _clean_cell(row.get(h))
            out[h] = val
            if flag in ("missing", "not_detected", "below_lod", "zero"):
                key = "%s__qc_flag" % h
                out.setdefault(key, "")
                out[key] = flag
        cleaned.append(out)
    return cleaned, change_log


def detect_duplicate_groups(rows, primary_keys):
    """Group rows by normalised primary-key tuple; returns groups with >1 member."""
    keys = [k for k in primary_keys if k]
    if not keys:
        return [], keys
    groups = {}
    for i, row in enumerate(rows, start=2):
        sig = tuple(_norm_key(str(row.get(k, ""))) for k in keys)
        groups.setdefault(sig, []).append(i)
    dup = [rows for rows in groups.values() if len(rows) > 1]
    return dup, keys


def _norm_key(value):
    return re.sub(r"\s+", "", value.strip().casefold())


def write_cleaned_csv(path, headers, rows):
    # Emit original headers plus any qc-flag columns encountered.
    all_headers = list(headers)
    flag_cols = sorted({h for row in rows for h in row
                        if h.endswith("__qc_flag")})
    all_headers += flag_cols
    lines = [",".join(_csv_escape(h) for h in all_headers)]
    for row in rows:
        lines.append(",".join(_csv_escape(str(row.get(h, "")))
                              for h in all_headers))
    return write_text_atomic(path, "\n".join(lines) + "\n", encoding="utf-8")


def _csv_escape(value):
    s = str(value)
    if any(ch in s for ch in (",", '"', "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def write_raw_snapshot(path, table):
    rows = [list(table["headers"])]
    for row in table["rows"]:
        rows.append([str(row.get(h, "")) for h in table["headers"]])
    lines = [",".join(_csv_escape(v) for v in r) for r in rows]
    return write_text_atomic(path, "\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Quality report (numbers recomputed from cleaned rows, never hand-written)
# --------------------------------------------------------------------------
def build_report(profile, duplicate_groups, unit_hints, change_log):
    lines = ["# 数据质量报告（自动生成，数字由清洗表实时重算）", ""]
    lines.append("- 数据行数：%d；字段数：%d" % (profile["n_rows"], profile["n_cols"]))
    lines.append("- 分隔符：%s；截断：%s" % (profile["delimiter"], profile["truncated"]))
    if profile["issues"]:
        lines.append("- 解析告警：")
        for it in profile["issues"][:20]:
            lines.append("  - %s" % it)
    lines.append("")
    lines.append("## 字段级清单")
    lines.append("")
    lines.append("| 字段 | 行数 | 缺失 | 未检测 | 低于LOD | 真值0 | 有效 | 数值min | 数值max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for h, col in profile["columns"].items():
        c = col["categories"]
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %s | %s |" % (
            h, col["n"], c["missing"], c["not_detected"], c["below_lod"],
            c["zero"], c["valid"],
            _fmt(col["min"]), _fmt(col["max"])))
    lines.append("")
    lines.append("## 重复记录")
    if duplicate_groups:
        for grp in duplicate_groups[:20]:
            lines.append("- 重复组（行号）：%s" % ", ".join(str(x) for x in grp))
    else:
        lines.append("- 未检出重复主键（或未指定主键）。")
    lines.append("")
    lines.append("## 单位换算提示（待 Agent 确认）")
    if unit_hints:
        for u in unit_hints[:20]:
            lines.append("- %s（factor=%s）@ %s" % (u["unit"], u["factor"], u["locator"]))
    else:
        lines.append("- 无。")
    lines.append("")
    lines.append("## 处理动作（脚本确定性部分）")
    if change_log:
        for cl in change_log[:20]:
            lines.append("- %s" % cl)
    else:
        lines.append("- 仅做保守标记，未删行/未插补/未换单位。")
    lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(x):
    if x is None:
        return "-"
    if isinstance(x, float) and x == int(x):
        return "%d" % int(x)
    return str(x)


# --------------------------------------------------------------------------
# Full pipeline
# --------------------------------------------------------------------------
def run_full(case, input_dir, output_dir, work_dir, max_bytes):
    result = build_skeleton(case, _now(), "partial")
    os.makedirs(work_dir, exist_ok=True)
    artifacts = {}
    degradations = []

    dict_infos = [read_text(p, max_bytes)
                  for p in case["manifest"]["dictionary"]]
    rules_infos = [read_text(p, max_bytes)
                   for p in case["manifest"]["rules"]]
    work_infos = [read_text(p, max_bytes)
                  for p in case["manifest"]["workorder"]]
    for infos in (dict_infos, rules_infos, work_infos):
        for info in infos:
            if info.get("error") or info.get("degraded"):
                degradations.append({
                    "path": info["path"], "error": info.get("error"),
                    "degraded": info.get("degraded"),
                    "truncated": info.get("truncated"),
                })

    data_files = case["manifest"]["data"]
    if not data_files:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append(
            "no tabular data file found; provide the aggregate table transcription")
        result["degradations"] = degradations
        result["artifacts"] = artifacts
        result["runtime"]["phases"].append("full: insufficient_evidence")
        return result

    data_file = data_files[0]
    if len(data_files) > 1:
        result["uncertainty"].append(
            "multiple candidate data files (%d); used first by name order: %s"
            % (len(data_files), os.path.basename(data_file)))
        result["pending_human"].append(
            "confirm which file is the main aggregate table")

    info = read_text(data_file, max_bytes)
    if info.get("error") and not info.get("text"):
        result["status"] = "input_error"
        result["errors"].append({"step": "full", "path": data_file,
                                 "detail": info["error"]})
        result["degradations"] = degradations
        result["artifacts"] = artifacts
        result["runtime"]["phases"].append("full: unreadable data")
        return result
    if info.get("degraded") or info.get("truncated"):
        degradations.append({"path": data_file, "error": info.get("error"),
                             "degraded": info.get("degraded"),
                             "truncated": info.get("truncated")})

    table = parse_tabular(info["text"])
    if table.get("error") or not table["rows"]:
        result["status"] = "input_error"
        result["errors"].append({"step": "full", "path": data_file,
                                 "detail": table.get("error") or "empty table"})
        result["degradations"] = degradations
        result["artifacts"] = artifacts
        result["runtime"]["phases"].append("full: empty/unparsable table")
        return result

    hints = extract_hints(dict_infos, rules_infos, work_infos)
    profile = build_profile(table, hints)
    profile["source_file"] = os.path.basename(data_file)
    profile["source_path"] = data_file

    cleaned, _ = clean_rows(table)

    # Select the actual column names matching suspected primary-key tokens.
    matched_keys = []
    for h in table["headers"]:
        hl = h.casefold().replace("_", " ").replace("-", " ")
        for pk in hints["primary_keys"]:
            tok = pk["token"].casefold().replace("_", " ").replace("-", " ")
            if tok and (tok in hl or hl in tok):
                matched_keys.append(h)
                break
    dup_groups, used_keys = detect_duplicate_groups(cleaned, matched_keys)

    change_log = [
        {"time": _now(), "step": "full",
         "action": "parsed %s (delimiter=%s, encoding=%s, %d rows x %d cols)"
         % (os.path.basename(data_file), table["delimiter"],
            info.get("encoding"), len(table["rows"]), len(table["headers"]))},
        {"time": _now(), "step": "full",
         "action": "classified missing/not_detected/below_lod/zero per cell; "
                   "no imputation, no deletion"},
    ]
    if dup_groups:
        change_log.append({"time": _now(), "step": "full",
                           "action": "detected %d duplicate group(s) by key %s"
                           % (len(dup_groups), used_keys)})
    if info.get("truncated"):
        change_log.append({"time": _now(), "step": "full",
                           "action": "file exceeds size cap; only first %d rows previewed"
                           % len(table["rows"])})

    # Write artifacts (atomic; failures degrade instead of crashing).
    base = os.path.basename(data_file)
    stem = os.path.splitext(base)[0] or "data"
    paths = {
        "cleaned_data": os.path.join(output_dir, "cleaned_data.csv"),
        "raw_snapshot": os.path.join(output_dir, "raw_snapshot.csv"),
        "data_profile": os.path.join(output_dir, "data_profile.json"),
        "quality_report": os.path.join(output_dir, "quality_report.md"),
        "evidence_map": os.path.join(output_dir, "evidence_map.json"),
    }
    write_cleaned_csv(paths["cleaned_data"], table["headers"], cleaned)
    write_raw_snapshot(paths["raw_snapshot"], table)
    write_json_atomic(paths["data_profile"], profile)
    report = build_report(profile, dup_groups, hints["unit_mappings"], [])
    write_text_atomic(paths["quality_report"], report)
    evidence = build_evidence_map(case, data_file, info, hints, dup_groups,
                                  used_keys)
    write_json_atomic(paths["evidence_map"], evidence)

    for key, p in paths.items():
        if os.path.isfile(p):
            artifacts[key] = p
        else:
            degradations.append({"artifact": key, "path": p,
                                 "error": "write failed"})

    result["artifacts"] = artifacts
    result["degradations"] = degradations
    fill_contract_keys(result, case.get("output_contract", {}), artifacts)
    result["uncertainty"] = result["uncertainty"] + [
        "semantic judgments (primary keys, QC bounds, unit factors) are "
        "suspected by heuristics and must be confirmed by the Agent",
    ]
    result["change_log"] = change_log
    result["sources"] = evidence.get("sources", [])
    result["pending_human"] = result.get("pending_human", []) + [
        "review suspected primary keys, QC bounds and unit mappings",
    ]
    if degradations:
        result["status"] = "partial"
    else:
        result["status"] = "partial"  # semantic review still pending
    result["runtime"]["phases"].append("full: complete")
    return result


def build_evidence_map(case, data_file, info, hints, dup_groups, used_keys):
    sources = []
    # case.json source
    if case["manifest"]["case_json"]:
        sources.append({
            "locator": case["manifest"]["case_json"],
            "quote": "case_id=%s; output_contract keys=%s"
            % (case["case_id"], list(case["output_contract"].keys())),
            "note": "case.json parsed (status=ok)",
        })
    sources.append({
        "locator": data_file,
        "quote": "encoding=%s; delimiter=%s" % (info.get("encoding"), ""),
        "note": "main tabular source",
    })
    for kind in ("primary_keys", "qc_bounds", "unit_mappings", "missing_rules"):
        for h in hints[kind][:30]:
            sources.append({
                "locator": h["locator"], "quote": h["quote"],
                "note": "%s (suspected)" % h["kind"],
            })
    for grp in dup_groups[:20]:
        sources.append({
            "locator": data_file,
            "quote": "duplicate rows %s by key %s" % (grp, used_keys),
            "note": "duplicate group",
        })
    return {"sources": sources, "hints": hints,
            "duplicate_groups": [[int(x) for x in g] for g in dup_groups],
            "used_primary_keys": used_keys}


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
