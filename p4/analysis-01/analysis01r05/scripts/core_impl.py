#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic helper routines for the sci-data-cleaning-contract skill.

Design contract
---------------
* Standard library only; no network; no randomness; no pandas/numpy.
* Every function degrades gracefully and returns a structured result instead
  of raising, so the CLI can always write an honest partial deliverable and
  exit with code 0.
* This module implements only *deterministic, verifiable* work: discovery,
  robust text/JSON/CSV reading, missing-value classification, exact-duplicate
  detection, conservative table profiling, and result assembly.  Intelligent
  judgement (dictionary interpretation, rule-conflict resolution, primary-key
  selection, unit conversion with molar mass, outlier retain/drop decisions)
  is the Agent's job and must be recorded in result.json with locator + quote.
"""

import codecs
import csv
import datetime
import hashlib
import io
import json
import os
import re
import unicodedata

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per text file, mirrors skill budget

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat", ".text")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema",
              "data_dictionary", "数据字典", "字段", "字典")
RULES_HINTS = ("rule", "qc", "unit", "quality", "limit", "range", "bound",
               "规则", "质控", "单位", "上下限", "检出限")
WORKORDER_HINTS = ("workorder", "work_order", "order", "instruction", "step",
                   "task", "工单", "步骤", "清洗")

ENVELOPE_FIELDS = ("case_id", "status", "sources", "uncertainty", "change_log",
                   "errors", "degradations", "pending_human", "runtime",
                   "artifacts", "contract_audit")

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# Lower-cased sentinel values commonly used for "entry missing" in aggregate
# transcriptions.  Conservative default; the Agent may override with a locator.
MISSING_SENTINELS = {
    "", "na", "n/a", "n.a.", "n.a", "null", "none", "nan", ".", "-", "--", "—",
    "missing", "无", "空白", "缺", "-999", "-9999", "-99999", "-99", "-999.0",
    "9999", "999", "99999", "999.0", "888", "-888", "8888", "-8888",
    "#n/a", "#null!", "#div/0!", "#value!", "#ref!",
}

# Full-cell tokens for "not detected" (keep as-is; never impute).
NOT_DETECTED_TOKENS = {"nd", "n.d.", "n.d", "nd(n)", "n.d. (not detected)"}
NOT_DETECTED_PHRASES = (
    "not detected", "not_detected", "undetected", "no peak", "未检出",
    "未检测", "未测出", "未检到", "无峰",
)

# Sub-phrases / prefix rule for "below limit of detection" (left-censored).
BELOW_LOD_PHRASES = (
    "below lod", "below_lod", "below detection", "below limit",
    "below quant", "below the limit", "低于检出限", "低于检测限",
    "检出限以下", "检测限以下", "lod以下", "loq以下", "bql", "lloq", "lld",
    "< lod", "<lod", "<loq", "<dl", "< lloq",
)

TRUE_ZERO_TOKENS = {"0", "0.0", "0.00", "0.000", "0e0", "0e+00", "zero",
                    "-0", "-0.0", "-0.00"}

_CONTRACT_ARTIFACT_HINTS = (
    (("clean", "table", "data", "csv", "清洗表"), "cleaned_data"),
    (("flag", "missing", "class", "分类", "标记"), "missing_flags"),
    (("report", "quality", "qc", "质量报告"), "quality_report"),
    (("profile", "stat", "summar", "descript", "画像"), "data_profile"),
    (("evidence", "trace", "proof", "map", "溯源", "证据"), "evidence_map"),
    (("snapshot", "raw", "original", "快照", "原文"), "raw_snapshot"),
    (("change", "log", "audit", "变更", "日志"), "change_log_md"),
)


# --------------------------------------------------------------------------
# Time / filesystem
# --------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def list_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _has_any(name, hints):
    low = name.casefold()
    return any(h.casefold() in low for h in hints)


def discover_inputs(input_dir):
    """Classify files by name hints only; contents are verified by the Agent."""
    files = list_files(input_dir)
    manifest = {"case_json": None, "task_md": None, "data": [], "dictionary": [],
                "rules": [], "workorder": [], "other": []}
    for f in files:
        base = os.path.basename(f)
        low = base.casefold()
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
    # A tabular file not otherwise classified is treated as data.
    if not manifest["data"]:
        for f in files:
            if f.lower().endswith(TABULAR_SUFFIXES) and f not in (
                    manifest["dictionary"] + manifest["rules"] + manifest["workorder"]):
                manifest["data"].append(f)
    return manifest


# --------------------------------------------------------------------------
# Robust text / JSON reading
# --------------------------------------------------------------------------
def read_text_robust(path, max_bytes=MAX_FILE_BYTES):
    """Read a file as text with encoding fallback.  Never raises."""
    size = 0
    truncated = False
    try:
        size = os.path.getsize(path)
    except OSError:
        pass
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        return {"text": "", "encoding": None, "degraded": True, "truncated": False,
                "size": size, "error": str(exc)}
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    if size > max_bytes:
        truncated = True

    encoding = None
    text = ""
    degraded = False
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            encoding = enc
            degraded = enc not in ("utf-8-sig", "utf-8")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text == "" and raw:
        text = raw.decode("latin-1", errors="replace")
        encoding = "latin-1-replace"
        degraded = True
    return {"text": text, "encoding": encoding, "degraded": degraded,
            "truncated": truncated, "size": size, "error": None}


def parse_json_robust(text, default):
    """Parse JSON, tolerate a leading BOM / surrounding whitespace."""
    if not isinstance(text, str) or not text.strip():
        return default, "empty"
    cleaned = text.strip()
    if cleaned.startswith("\ufeff"):
        cleaned = cleaned[1:].lstrip()
    try:
        return json.loads(cleaned), "ok"
    except json.JSONDecodeError as exc:
        return default, "invalid_json: {} (line {})".format(exc.msg, exc.lineno)


# --------------------------------------------------------------------------
# case.json / output contract
# --------------------------------------------------------------------------
def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {"case_id": None, "output_contract": {}, "raw": {}, "manifest": manifest,
            "read_error": None}
    path = manifest.get("case_json")
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info.get("text", ""), {})
    if not isinstance(parsed, dict):
        parsed = {}
        status = "not_a_dict"
    case["raw"] = parsed
    case["case_id"] = (parsed.get("case_id") or parsed.get("id")
                       or parsed.get("challenge_id") or os.path.basename(path))
    contract = parsed.get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = contract
    elif isinstance(contract, list):
        case["output_contract"] = {k: None for k in contract}
    if status != "ok":
        case["read_error"] = "case.json parsed with status={}".format(status)
    if info.get("degraded"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding degraded"
    return case


def contract_default(value):
    """Type-aware default: never drop a key, never invent a value."""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    if isinstance(value, str):
        return ""
    return None


def build_skeleton(case, started):
    contract = case.get("output_contract") or {}
    result = {"case_id": case.get("case_id")}
    for key, val in contract.items():
        result[key] = contract_default(val)
    result.setdefault("status", "partial")
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("change_log", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("pending_human", [])
    result["runtime"] = {"started": started, "finished": None, "seconds": None,
                         "deterministic": True,
                         "note": "timestamps are metadata only; data artifacts are deterministic"}
    result["artifacts"] = {}
    result["contract_audit"] = {"n_contract_keys": len(contract),
                                "keys": list(contract.keys()),
                                "filled": [], "unfilled": list(contract.keys()),
                                "source": "case.json#output_contract"}
    if case.get("read_error"):
        result["errors"].append(case["read_error"])
        if "no case.json" in case["read_error"]:
            result["status"] = "input_error"
        else:
            result["status"] = "partial"
    result["change_log"].append({
        "time": started, "step": "bootstrap",
        "action": "write valid result.json skeleton (partial) with every "
                  "output_contract key backfilled to a type-aware default",
        "why": "guarantee a loadable, honest deliverable before analysis begins",
    })
    return result


def refresh_contract_audit(result, contract):
    keys = list(contract.keys())
    filled, unfilled = [], []
    for k in keys:
        v = result.get(k)
        if v is None or v == [] or v == {} or v == "":
            unfilled.append(k)
        else:
            filled.append(k)
    result["contract_audit"] = {"n_contract_keys": len(keys), "keys": keys,
                                "filled": filled, "unfilled": unfilled,
                                "source": "case.json#output_contract"}
    return result


def fill_contract_keys(result, contract, artifacts):
    """Map empty contract slots to produced artifact paths by keyword hints."""
    n = 0
    for key, val in contract.items():
        current = result.get(key)
        empty = current is None or current == [] or current == {} or current == ""
        if not empty:
            continue
        low = key.casefold()
        for hints, art_key in _CONTRACT_ARTIFACT_HINTS:
            if any(h in low for h in hints) and art_key in artifacts:
                result[key] = artifacts[art_key]
                n += 1
                break
    return n


# --------------------------------------------------------------------------
# Value classification (missing / not detected / below LOD / true zero)
# --------------------------------------------------------------------------
def is_numeric(s):
    if not isinstance(s, str):
        s = str(s)
    s = s.strip().replace(",", "").replace(" ", "")
    if s == "":
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def norm_num(s):
    return str(s).strip().replace(",", "").replace(" ", "")


def classify_value(raw):
    """Classify one cell into the four non-mutually-exclusive categories the
    task distinguishes: entry_missing / not_detected / below_lod / true_zero /
    present.  Deterministic token matching; the Agent may override per field
    with an explicit locator + quote recorded in change_log."""
    if raw is None:
        return "entry_missing"
    s = str(raw).strip()
    if s == "":
        return "entry_missing"
    low = s.casefold()
    if low in MISSING_SENTINELS:
        return "entry_missing"
    # Left-censored values take precedence over plain numeric reading.
    if s.startswith(("<", "\u2264", "\u2266")) or any(t in low for t in BELOW_LOD_PHRASES):
        return "below_lod"
    if low in NOT_DETECTED_TOKENS or any(t in low for t in NOT_DETECTED_PHRASES):
        return "not_detected"
    if low in TRUE_ZERO_TOKENS or re.fullmatch(r"[+-]?0+(\.0+)?([eE][+-]?\d+)?", s):
        return "true_zero"
    return "present"


# --------------------------------------------------------------------------
# Table parsing / profiling
# --------------------------------------------------------------------------
def sniff_delimiter(lines):
    counts = {}
    for line in lines[:10]:
        for delim in ("\t", ",", ";", "|"):
            counts[delim] = counts.get(delim, 0) + line.count(delim)
    if not counts:
        return ","
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def parse_table(path):
    info = read_text_robust(path)
    if info.get("error"):
        return {"headers": [], "rows": [], "delimiter": None,
                "encoding": info.get("encoding"), "error": info["error"],
                "n_rows": 0, "degraded": info.get("degraded", True)}
    text = info.get("text", "")
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return {"headers": [], "rows": [], "delimiter": None,
                "encoding": info.get("encoding"), "error": "empty file",
                "n_rows": 0, "degraded": info.get("degraded", False)}
    delim = sniff_delimiter(lines)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        raw_rows = list(reader)
    except csv.Error as exc:
        return {"headers": [], "rows": [], "delimiter": delim,
                "encoding": info.get("encoding"), "error": "csv parse: {}".format(exc),
                "n_rows": 0, "degraded": info.get("degraded", False)}
    rows = [[c.strip() for c in r] for r in raw_rows if any(c.strip() for c in r)]
    if not rows:
        return {"headers": [], "rows": [], "delimiter": delim,
                "encoding": info.get("encoding"), "error": "no data rows",
                "n_rows": 0, "degraded": info.get("degraded", False)}
    headers = rows[0]
    body = rows[1:]
    return {"headers": headers, "rows": body, "delimiter": delim,
            "encoding": info.get("encoding"), "error": None,
            "n_rows": len(body), "degraded": info.get("degraded", False)}


def profile_table(tbl):
    headers = tbl.get("headers", [])
    rows = tbl.get("rows", [])
    n = len(rows)
    profile = {"n_rows": n, "n_columns": len(headers),
               "delimiter": tbl.get("delimiter"), "encoding": tbl.get("encoding"),
               "degraded": tbl.get("degraded", False), "columns": []}
    for j, h in enumerate(headers):
        values = [r[j] if j < len(r) else "" for r in rows]
        classes = [classify_value(v) for v in values]
        counts = {}
        for c in classes:
            counts[c] = counts.get(c, 0) + 1
        numeric = [v for v in values if is_numeric(v)]
        col = {"index": j, "name": h, "counts": counts,
               "n_non_missing": counts.get("present", 0) + counts.get("below_lod", 0)
                                + counts.get("true_zero", 0),
               "n_present": counts.get("present", 0),
               "n_not_detected": counts.get("not_detected", 0),
               "n_below_lod": counts.get("below_lod", 0),
               "n_entry_missing": counts.get("entry_missing", 0),
               "n_true_zero": counts.get("true_zero", 0),
               "numeric_values": len(numeric)}
        if numeric:
            try:
                nums = [float(norm_num(v)) for v in numeric]
                col["min"] = min(nums)
                col["max"] = max(nums)
            except ValueError:
                pass
        profile["columns"].append(col)

    dup = {}
    for i, r in enumerate(rows):
        key = tuple(r)
        dup.setdefault(key, []).append(i)
    dup_groups = [{"indices": idx, "count": len(idx)}
                  for idx in dup.values() if len(idx) > 1]
    profile["exact_duplicate_groups"] = dup_groups
    profile["n_exact_duplicate_groups"] = len(dup_groups)
    profile["n_exact_duplicate_rows"] = sum(g["count"] - 1 for g in dup_groups)

    # Approximate-duplicate candidates: deterministic normalization only.
    cand = {}
    for i, r in enumerate(rows):
        key = normalized_key(r)
        cand.setdefault(key, []).append(i)
    cand_groups = [{"indices": idx, "count": len(idx)}
                   for idx in cand.values() if len(idx) > 1]
    profile["approximate_duplicate_candidate_groups"] = cand_groups
    profile["n_approximate_duplicate_candidates"] = len(cand_groups)
    return profile


def normalized_key(row):
    out = []
    for v in row:
        s = unicodedata.normalize("NFKC", str(v)).strip().casefold()
        s = re.sub(r"\s+", " ", s)
        out.append(s)
    return tuple(out)


def build_cleaned_table(tbl):
    """Faithful, reversible normalization pass: keep original cells (whitespace
    trimmed only), preserve column order; add no imputation, no value edits.
    Returns (headers, cleaned_rows, flag_rows)."""
    headers = list(tbl.get("headers", []))
    rows = tbl.get("rows", [])
    cleaned_rows, flag_rows = [], []
    for r in rows:
        cells = [r[j] if j < len(r) else "" for j in range(len(headers))]
        cleaned_rows.append(cells)
        flag_rows.append([classify_value(c) for c in cells])
    return headers, cleaned_rows, flag_rows


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def write_json(path, obj):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def write_csv(path, headers, rows, delimiter=","):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(headers)
        w.writerows(rows)
    return path


def copy_snapshot(src, dst):
    """Byte-preserving snapshot; truncates at MAX_FILE_BYTES and records it."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        with open(src, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        return {"path": dst, "truncated": False, "bytes": 0, "error": str(exc)}
    truncated = len(data) > MAX_FILE_BYTES
    if truncated:
        data = data[:MAX_FILE_BYTES]
    try:
        with open(dst, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        return {"path": dst, "truncated": truncated, "bytes": len(data),
                "error": str(exc)}
    return {"path": dst, "truncated": truncated, "bytes": len(data), "error": None}


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
def render_quality_report(profile, tbl, src):
    lines = [
        "# 数据质量报告（确定性画像）",
        "",
        "> 本报告所有数字由清洗表实时重算，不手写；智能判断（异常值保留/剔除/盖帽、"
        "近似重复裁决、单位换算与摩尔质量）由 Agent 追加，并附 locator + 逐字 quote。",
        "",
        "- 来源文件：`{}`".format(src),
        "- 分隔符：`{}`；编码：`{}`".format(profile.get("delimiter"), profile.get("encoding")),
        "- 数据行数：{}；列数：{}".format(profile.get("n_rows"), profile.get("n_columns")),
        "- 完全重复行组：{} 组；涉及冗余行：{}".format(
            profile.get("n_exact_duplicate_groups"), profile.get("n_exact_duplicate_rows")),
        "- 近似重复候选组（仅归一化候选，待 Agent 按真实主键裁决）：{}".format(
            profile.get("n_approximate_duplicate_candidates")),
        "",
        "## 字段级缺失分类",
        "",
        "| 字段 | 非缺失 | 未检测 | 低于检出限 | 录入缺失 | 真值0 | 数值个数 | min | max |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for col in profile.get("columns", []):
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            col["name"], col["n_non_missing"], col["n_not_detected"],
            col["n_below_lod"], col["n_entry_missing"], col["n_true_zero"],
            col["numeric_values"], col.get("min", ""), col.get("max", "")))
    lines += [
        "",
        "## 口径说明",
        "",
        "- `非缺失` = present + below_lod + true_zero；below_lod 是左删失，不是缺失、不是 0。",
        "- 完全重复指整行逐格相同；近似重复仅按 NFKC + 大小写 + 空白归一化给出候选，"
        "不代表真实主键，须由 Agent 依据数据字典/工单确定保留哪一条及依据。",
        "- `min`/`max` 仅对可解析数值（present 与 true_zero）计算，不含左删失值。",
        "",
    ]
    return "\n".join(lines) + "\n"


def build_evidence_map(manifest, data_src, artifacts):
    em = {"data_src": data_src, "files": [], "artifacts": artifacts,
          "approximate_duplicate_candidates": [],
          "source_conflicts": []}
    roles = (("data", manifest.get("data", [])),
             ("dictionary", manifest.get("dictionary", [])),
             ("rules", manifest.get("rules", [])),
             ("workorder", manifest.get("workorder", [])),
             ("case_json", [manifest.get("case_json")]),
             ("task_md", [manifest.get("task_md")]),
             ("other", manifest.get("other", [])))
    for role, files in roles:
        for f in files:
            if not f:
                continue
            info = read_text_robust(f)
            em["files"].append({"role": role, "path": f,
                                "encoding": info.get("encoding"),
                                "degraded": info.get("degraded", False),
                                "size": info.get("size"),
                                "error": info.get("error"),
                                "sha256": sha256_file(f)})
    return em


# --------------------------------------------------------------------------
# Result load / validation
# --------------------------------------------------------------------------
def load_existing_result(output_dir):
    p = os.path.join(output_dir, "result.json")
    if not os.path.exists(p):
        return None
    info = read_text_robust(p)
    obj, _ = parse_json_robust(info.get("text", ""), None)
    return obj if isinstance(obj, dict) else None


def validate_result(result, contract, artifacts_dir):
    """Return a list of issues; never raises.  Used by the selfcheck phase."""
    issues = []
    if not isinstance(result, dict):
        return ["result.json is not a JSON object"]
    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in VALID_STATUS:
        issues.append("invalid status {!r}".format(result.get("status")))
    for key in contract:
        if key not in result:
            issues.append("output_contract key missing from result.json: {!r}".format(key))
    for field in ("sources", "uncertainty", "change_log"):
        if field not in result or not isinstance(result[field], list):
            issues.append("field {!r} missing or not a list".format(field))
    for i, src in enumerate(result.get("sources", [])):
        if not isinstance(src, dict) or not src.get("locator") or not src.get("quote"):
            issues.append("sources[{}] missing locator/quote".format(i))
    for i, ch in enumerate(result.get("change_log", [])):
        if not isinstance(ch, dict) or not ch.get("step") or not ch.get("action"):
            issues.append("change_log[{}] missing step/action".format(i))
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        for key, val in artifacts.items():
            if not isinstance(val, str):
                continue
            p = val if os.path.isabs(val) else os.path.join(artifacts_dir, val)
            if not os.path.exists(p):
                issues.append("artifact not found: {} -> {}".format(key, val))
    audit = result.get("contract_audit")
    if isinstance(audit, dict):
        if audit.get("n_contract_keys") != len(contract):
            issues.append("contract_audit.n_contract_keys mismatch")
    return issues
