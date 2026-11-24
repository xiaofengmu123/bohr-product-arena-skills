#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, standard-library-only core for sci-data-cleaning.

Design contract
---------------
This module performs only mechanical, reproducible work:

* file discovery and encoding-tolerant text reading (never by filename label);
* robust JSON / CSV parsing;
* conservative classification of a raw cell into one of five states:
  `valid`, `true_zero`, `below_lod`, `not_detected`, `entry_missing`;
* column profiling computed from the observed transcript (no population
  inference, no imputation);
* exact and normalized duplicate grouping;
* generation of cleaned_data.csv (original values preserved verbatim plus
  annotation columns), data_profile.json, quality_report.md, evidence_map.json,
  change_log.json and a valid result.json.

It NEVER imputes missing/invalid values, never deletes rows silently, never
converts units without an explicit mapping, never invents data, citations or
scores, and never uses the network or random numbers. All judgement that
requires reading the data dictionary, the per-metric unit/QC rules or the
cleaning workorder is performed by the calling Agent and recorded through
`agent_decisions.json`, `change_log`, `evidence_map`, `sources` and
`pending_human`. The Agent must never present this script's mechanical output
as human judgement.
"""

import csv
import io
import json
import os
import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema", "variable",
              "数据字典", "字段", "变量", "编码")
RULES_HINTS = ("rule", "qc", "unit", "quality", "threshold", "range",
               "规则", "质控", "单位", "阈值", "范围")
WORKORDER_HINTS = ("workorder", "work_order", "order", "task", "step",
                   "instruction", "工单", "步骤", "说明", "instructions")

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

ENTRY_MISSING = {
    "", "na", "n/a", "na.", "null", "none", "missing", "nan", "nil",
    "empty", "blank", "无", "空", "缺失", "无值", "-", "--", "—", "..",
    ".", "n.a.", "无数据", "null值",
}

NOT_DETECTED = {
    "nd", "n.d.", "nd.", "not detected", "not_detected", "notdetected",
    "未检出", "未检测", "未测得", "未检", "none detected", "no detect",
}

BELOW_LOD = {
    "bdl", "<lod", "<dl", "lod", "dl", "mdl", "<mdl",
    "below lod", "below dl", "below detection limit", "below detection",
    "below limit of detection", "低于检出限", "检出限以下", "低于检测限",
    "低于检出限(lod)", "检出限未达", "检测限以下", "未达到检出限",
}

_MISSING_NUMERIC_RE = re.compile(r"^[+-]?9{3,}(?:\.0+)?$")
_ZERO_RE = re.compile(r"^[+-]?0+(?:\.0*)?$")
_LOD_NUMERIC_RE = re.compile(r"^[<≤]\s*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


# ---------------------------------------------------------------------------
# Filesystem / text
# ---------------------------------------------------------------------------

def list_files(root):
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def read_text_robust(path, max_bytes=4_000_000):
    """Read a text file tolerating BOM / UTF-8 / GB18030 fallbacks.

    Returns dict(text, encoding, degraded, size, error)."""
    result = {"text": "", "encoding": None, "degraded": False, "size": 0, "error": None}
    if not os.path.isfile(path):
        result.update(degraded=True, error="file missing")
        return result
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        result.update(degraded=True, error=str(exc))
        return result
    result["size"] = size
    if size > max_bytes:
        result.update(degraded=True, error="file exceeds size limit")
        return result
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        result.update(degraded=True, error=str(exc))
        return result
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            result["text"] = raw.decode(enc)
            result["encoding"] = enc
            return result
        except (UnicodeDecodeError, LookupError):
            continue
    result["text"] = raw.decode("latin-1", errors="replace")
    result.update(encoding="latin-1(replace)", degraded=True)
    return result


def parse_json_robust(text, default):
    if text is None or not text.strip():
        return default, "empty"
    candidate = text.lstrip("\ufeff").strip()
    try:
        return json.loads(candidate), "ok"
    except (json.JSONDecodeError, ValueError):
        return default, "bad_json"


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _has_any(name, hints):
    low = name.lower()
    return any(h in low for h in hints)


def discover_inputs(input_dir):
    files = list_files(input_dir)
    manifest = {
        "case_json": None, "task_md": None, "data": [], "dictionary": [],
        "rules": [], "workorder": [], "other": [],
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
    if not manifest["data"]:
        for f in files:
            if f.lower().endswith(TABULAR_SUFFIXES) and f not in (
                manifest["dictionary"] + manifest["rules"] + manifest["workorder"]):
                manifest["data"].append(f)
    return manifest


def load_case(input_dir, manifest=None):
    manifest = manifest or discover_inputs(input_dir)
    case = {
        "case_id": None, "output_contract": {}, "raw": {},
        "manifest": manifest, "read_error": None,
    }
    path = manifest.get("case_json")
    if path is None:
        for f in manifest.get("other", []):
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info.get("text"), {})
    if isinstance(parsed, dict):
        case["raw"] = parsed
    else:
        case["read_error"] = "case.json is not a JSON object"
    case["case_id"] = (
        case["raw"].get("case_id")
        or case["raw"].get("id")
        or case["raw"].get("challenge_id")
        or os.path.basename(os.path.dirname(path)) or os.path.basename(path)
    )
    contract = case["raw"].get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = contract
    elif isinstance(contract, list):
        case["output_contract"] = {k: None for k in contract if isinstance(k, str)}
    if status != "ok":
        case["read_error"] = (case["read_error"] or "") + f" case.json parse status={status}"
    if info.get("degraded"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding degraded"
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


# ---------------------------------------------------------------------------
# Value classification
# ---------------------------------------------------------------------------

def classify_value(raw):
    """Return (class, raw_verbatim).

    class in {valid, true_zero, below_lod, not_detected, entry_missing}.
    Below-LOD and not-detected are left-censored / zero-equivalent observations,
    NOT missing values and NOT true zeros; they must never be collapsed."""
    if raw is None:
        return "entry_missing", raw
    s = str(raw)
    stripped = s.strip()
    if stripped == "":
        return "entry_missing", raw
    low = stripped.lower()
    if _ZERO_RE.match(stripped):
        return "true_zero", raw
    if low in ENTRY_MISSING:
        return "entry_missing", raw
    if _MISSING_NUMERIC_RE.match(stripped):
        return "entry_missing", raw
    if low in NOT_DETECTED:
        return "not_detected", raw
    if low in BELOW_LOD:
        return "below_lod", raw
    if _LOD_NUMERIC_RE.match(stripped):
        return "below_lod", raw
    if ("检出限" in stripped) or ("检测限" in stripped):
        return "below_lod", raw
    if stripped.startswith("<") and ("lod" in low or "dl" in low or "mdl" in low):
        return "below_lod", raw
    return "valid", raw


def to_number(raw):
    """Best-effort numeric coercion WITHOUT guessing units.

    Returns float or None. Only used for profiling/range reporting; the
    original string is always preserved in the cleaned table."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------

def sniff_delimiter(text):
    if not text:
        return ","
    first_line = text.splitlines()[0] if text.splitlines() else ""
    candidates = [",", "\t", ";", "|"]
    best = ","
    best_score = -1
    for d in candidates:
        count = first_line.count(d)
        if count > best_score:
            best_score = count
            best = d
    if best_score <= 0 and "\t" in first_line:
        best = "\t"
    return best


def parse_table(path):
    info = read_text_robust(path)
    if info.get("error") and not info.get("text"):
        return {"error": info.get("error"), "headers": [], "rows": [],
                "encoding": info.get("encoding"), "delimiter": None}
    text = info["text"]
    if not text.strip():
        return {"error": "empty tabular file", "headers": [], "rows": [],
                "encoding": info.get("encoding"), "delimiter": None}
    delim = sniff_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        grid = [row for row in reader]
    except csv.Error as exc:
        return {"error": f"csv parse error: {exc}", "headers": [], "rows": [],
                "encoding": info.get("encoding"), "delimiter": delim}
    grid = [r for r in grid if any((c or "").strip() for c in r)]
    if not grid:
        return {"error": "no non-empty rows", "headers": [], "rows": [],
                "encoding": info.get("encoding"), "delimiter": delim}
    headers = grid[0]
    headers = [h.strip() if h else f"col_{i}" for i, h in enumerate(headers)]
    rows = grid[1:]
    return {"error": None, "headers": headers, "rows": rows,
            "encoding": info.get("encoding"), "delimiter": delim,
            "degraded": info.get("degraded"), "source": path}


# ---------------------------------------------------------------------------
# Profiling / duplicates
# ---------------------------------------------------------------------------

def profile_table(headers, rows):
    n = len(rows)
    profile = {}
    for c, h in enumerate(headers):
        vals = [(r[c] if c < len(r) else None) for r in rows]
        classes = {}
        numeric = []
        distinct_raw = set()
        for v in vals:
            cls, _ = classify_value(v)
            classes[cls] = classes.get(cls, 0) + 1
            distinct_raw.add(str(v).strip())
            if cls in ("valid", "true_zero"):
                num = to_number(v)
                if num is not None:
                    numeric.append(num)
        profile[h] = {
            "column": h,
            "index": c,
            "total": n,
            "missing_classes": classes,
            "distinct": len(distinct_raw),
            "numeric_count": len(numeric),
            "numeric_min": min(numeric) if numeric else None,
            "numeric_max": max(numeric) if numeric else None,
        }
    return profile


def normalize_key(value):
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def find_duplicates(rows, key_indices=None):
    """Group row indices by normalized key. key_indices=None -> all columns.

    This is a conservative proposal; the Agent must determine the true primary
    key from the dictionary/workorder and record it in change_log."""
    groups = {}
    for i, r in enumerate(rows):
        if key_indices is None:
            key = tuple(normalize_key(v) for v in r)
        else:
            key = tuple(normalize_key(r[j]) if j < len(r) else "" for j in key_indices)
        groups.setdefault(key, []).append(i)
    return {k: v for k, v in groups.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Mechanical pipeline phases
# ---------------------------------------------------------------------------

def _iso_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def build_skeleton(case, manifest=None):
    result = {}
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        for key in contract:
            result[key] = contract_default(contract[key])
        result["_contract_keys"] = sorted(contract.keys())
    elif isinstance(contract, list):
        for key in contract:
            if isinstance(key, str):
                result[key] = None
        result["_contract_keys"] = sorted(k for k in contract if isinstance(k, str))
    else:
        result["_contract_keys"] = []
    result["case_id"] = case.get("case_id")
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {"started": _iso_now()}
    result["artifacts"] = {}
    if case.get("read_error"):
        result["errors"].append({"step": "read_case", "detail": case["read_error"]})
    return result


def run_bootstrap(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    started = _iso_now()
    manifest = discover_inputs(input_dir)
    case = load_case(input_dir, manifest)
    result = build_skeleton(case, manifest)
    result["status"] = "partial"
    result["runtime"]["started"] = started
    result["change_log"].append({
        "at": "bootstrap", "step": "skeleton_written",
        "action": "落盘 result.json 骨架（status=partial，回填 output_contract 全部键）",
    })
    write_json(os.path.join(output_dir, "result.json"), result)
    checkpoint = {
        "phase": "bootstrap",
        "started": started,
        "finished": _iso_now(),
        "case_id": case.get("case_id"),
        "manifest": manifest,
        "contract_keys": result["_contract_keys"],
        "steps_done": ["read_case", "discover_inputs", "write_skeleton"],
        "steps_pending": [
            "read_dictionary", "read_rules", "read_workorder",
            "profile_data", "clean_data", "resolve_keys_units_anomalies",
            "report", "finalize"],
    }
    write_json(os.path.join(work_dir, "checkpoint.json"), checkpoint)
    return result, checkpoint


def _material_context(files):
    ctx = []
    for f in files:
        info = read_text_robust(f)
        snippet = info["text"][:2000] if info["text"] else ""
        ctx.append({
            "file": f, "encoding": info["encoding"], "size": info["size"],
            "degraded": info["degraded"], "error": info["error"],
            "preview": snippet,
        })
    return ctx


def run_full(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    started = _iso_now()
    manifest = discover_inputs(input_dir)
    case = load_case(input_dir, manifest)
    result = build_skeleton(case, manifest)

    # Preserve the bootstrap audit trail if a prior result.json already exists,
    # so the final change_log records the full bootstrap -> full -> finalize path.
    prior_path = os.path.join(output_dir, "result.json")
    prior = None
    if os.path.isfile(prior_path):
        pinfo = read_text_robust(prior_path)
        prior, _ = parse_json_robust(pinfo.get("text"), None)
    if isinstance(prior, dict) and set(prior.get("_contract_keys", [])) == set(result.get("_contract_keys", [])):
        change_log = list(prior.get("change_log", []))
        sources = list(prior.get("sources", []))
        errors = list(prior.get("errors", []))
        degradations = list(prior.get("degradations", []))
        pending = list(prior.get("pending_human", []))
        result["runtime"] = dict(prior.get("runtime", {}) or {})
    else:
        change_log, sources, errors, degradations, pending = [], [], [], [], []

    for kind in ("dictionary", "rules", "workorder"):
        for f in manifest.get(kind, []):
            info = read_text_robust(f)
            if info.get("error"):
                errors.append({"step": "read_material", "file": f, "detail": info["error"]})
            elif info.get("degraded"):
                degradations.append({"file": f, "detail": "encoding degraded, fallback used"})
            sources.append({
                "locator": f, "quote": (info["text"][:160].strip() if info["text"] else ""),
                "note": "材料文件头部预览（逐字），完整内容由 Agent 读取并据此裁决",
            })

    write_json(os.path.join(work_dir, "materials_context.json"), {
        "dictionary": _material_context(manifest.get("dictionary", [])),
        "rules": _material_context(manifest.get("rules", [])),
        "workorder": _material_context(manifest.get("workorder", [])),
    })

    data_files = list(manifest.get("data", []))
    if not data_files:
        result["status"] = "insufficient_evidence"
        pending.append("输入目录未发现任何可解析的表格数据源，无法清洗")
        result.update(sources=sources, change_log=change_log, errors=errors,
                      degradations=degradations, pending_human=pending)
        result["runtime"]["finished"] = _iso_now()
        write_json(os.path.join(output_dir, "result.json"), result)
        _write_checkpoint(work_dir, "full", manifest, ["no_data"], [], started)
        return result

    main_data = data_files[0]
    parsed = parse_table(main_data)
    if parsed.get("error"):
        result["status"] = "input_error"
        errors.append({"step": "parse_data", "file": main_data, "detail": parsed["error"]})
        result.update(sources=sources, change_log=change_log, errors=errors,
                      degradations=degradations, pending_human=pending)
        result["runtime"]["finished"] = _iso_now()
        write_json(os.path.join(output_dir, "result.json"), result)
        _write_checkpoint(work_dir, "full", manifest, ["parse_failed"], [], started)
        return result

    headers, rows = parsed["headers"], parsed["rows"]
    profile = profile_table(headers, rows)
    dup_groups = find_duplicates(rows, key_indices=None)

    # Build cleaned rows with verbatim values + annotation columns.
    fieldnames = ["_row_id"]
    fieldnames += headers
    fieldnames += [f"_class::{h}" for h in headers]
    fieldnames += ["_duplicate_group", "_duplicate_keep"]

    dup_assign, keep_flag = {}, {}
    for gidx, (key, idxs) in enumerate(sorted(dup_groups.items(), key=lambda kv: kv[1][0]), start=1):
        for pos, idx in enumerate(idxs):
            dup_assign[idx] = f"D{gidx}"
            keep_flag[idx] = (pos == 0)

    cleaned_rows = []
    for i, r in enumerate(rows):
        out = {"_row_id": i + 1}
        for c, h in enumerate(headers):
            v = r[c] if c < len(r) else ""
            cls, _ = classify_value(v)
            out[h] = v
            out[f"_class::{h}"] = cls
        out["_duplicate_group"] = dup_assign.get(i, "")
        out["_duplicate_keep"] = keep_flag.get(i, True)
        cleaned_rows.append(out)

    cleaned_path = os.path.join(output_dir, "cleaned_data.csv")
    _write_csv(cleaned_path, fieldnames, cleaned_rows)
    profile_path = os.path.join(output_dir, "data_profile.json")
    write_json(profile_path, {
        "source": main_data, "encoding": parsed.get("encoding"),
        "delimiter": parsed.get("delimiter"), "n_rows": len(rows),
        "n_columns": len(headers), "columns": profile,
    })
    report_path = os.path.join(output_dir, "quality_report.md")
    report_text = build_quality_report(main_data, parsed, profile, dup_groups)
    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(report_text)
    evidence_path = os.path.join(output_dir, "evidence_map.json")
    write_json(evidence_path, {
        "materials": {f: {"encoding": read_text_robust(f)["encoding"],
                          "size": read_text_robust(f)["size"]}
                      for f in (manifest["dictionary"] + manifest["rules"] + manifest["workorder"])},
        "entries": [],
        "note": "entries 由 Agent 逐条补充 locator + 逐字 quote；本文件仅提供材料级定位骨架",
    })
    change_path = os.path.join(work_dir, "change_log.json")
    change_log.extend([
        {"at": "full", "step": "discover", "action": f"识别主数据文件 {main_data}（不按文件名臆测内容）"},
        {"at": "full", "step": "parse", "action": f"按 {parsed.get('encoding')} 解码、分隔符 {parsed.get('delimiter')!r} 解析 {len(rows)} 行"},
        {"at": "full", "step": "classify_missing", "action": "四类缺失状态（未检测/低于检出限/录入缺失/真值0）逐格标注，互不覆盖"},
        {"at": "full", "step": "dedup_proposal", "action": f"归一化近似重复 {len(dup_groups)} 组（脚本保守建议，主键待 Agent 依据字典/工单确认）"},
        {"at": "full", "step": "report", "action": "生成字段级质量报告，数字由 cleaned_data.csv 实时重算"},
    ])
    write_json(change_path, change_log)

    artifacts = {
        "cleaned_data": cleaned_path,
        "data_profile": profile_path,
        "quality_report": report_path,
        "evidence_map": evidence_path,
        "change_log": change_path,
    }
    result["status"] = "partial"
    result["sources"] = sources
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = degradations
    result["pending_human"] = pending + [
        "主键判定（去重键）需依据数据字典/工单确认",
        "异常值上下界需依据 QC/单位规则给出，未给定时不得武断删除真实高值",
        "单位换算仅当规则明确给出映射与因子时执行；摩尔浓度等不猜换算",
        "报告数字与 cleaned_data.csv 的最终一致性需复核",
    ]
    result["artifacts"] = artifacts
    result["runtime"]["finished"] = _iso_now()
    _fill_contract_by_hint(result, case["output_contract"], artifacts)
    write_json(os.path.join(output_dir, "result.json"), result)

    _write_checkpoint(work_dir, "full", manifest, [
        "read_materials", "parse_data", "profile", "clean", "report"],
        ["resolve_keys_units_anomalies", "finalize"], started)
    return result


def _write_csv(path, fieldnames, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def build_quality_report(source, parsed, profile, dup_groups):
    lines = [
        "# 数据质量报告",
        "",
        "> 本报告所有数字由脚本从 `cleaned_data.csv` 实时重算，不手写、不与结果表脱节。",
        "",
        f"- 数据文件：`{source}`",
        f"- 解码：{parsed.get('encoding')}；分隔符：{parsed.get('delimiter')!r}",
        f"- 行数：{len(parsed['rows'])}；列数：{len(parsed['headers'])}",
        f"- 近似重复组（归一化全列键，脚本保守建议）：{len(dup_groups)} 组",
        "",
        "## 字段级清单",
        "",
        "| 列 | 行数 | 未检测 | 低于检出限 | 录入缺失 | 真值0 | 有效/其他 | 唯一值 | 数值范围 |",
        "|-|-|-|-|-|-|-|-|-|",
    ]
    for h in parsed["headers"]:
        p = profile.get(h, {})
        mc = p.get("missing_classes", {})
        lo, hi = p.get("numeric_min"), p.get("numeric_max")
        rng = "-"
        if lo is not None and hi is not None:
            rng = f"{lo} ~ {hi}"
        lines.append(
            f"| {h} | {p.get('total', 0)} | {mc.get('not_detected', 0)} | "
            f"{mc.get('below_lod', 0)} | {mc.get('entry_missing', 0)} | "
            f"{mc.get('true_zero', 0)} | {mc.get('valid', 0)} | "
            f"{p.get('distinct', 0)} | {rng} |"
        )
    lines += [
        "",
        "## 重复记录（脚本保守建议，主键待 Agent 依据字典/工单确认）",
        "",
    ]
    if dup_groups:
        for gidx, (key, idxs) in enumerate(sorted(dup_groups.items(), key=lambda kv: kv[1][0]), start=1):
            lines.append(f"- 组 D{gidx}：键 `{ ' | '.join(key) }` → 行 {', '.join(str(i + 1) for i in idxs)}（建议保留首行，理由待 Agent 补写）")
    else:
        lines.append("- 未发现归一化重复组。")
    lines += [
        "",
        "## 处理动作与影响范围",
        "",
        "- 原值逐格保留（cleaned_data.csv 中每列原样存在，附加 `_class::<列>` 标注分类）。",
        "- 未插补、未静默删除、未改单位、未截断精度；单位/主键/异常裁决由 Agent 依据字典与规则完成并写入 change_log。",
        "- 低于检出限与未检测按左删失保留，不折算为 0、不算缺失。",
    ]
    return "\n".join(lines) + "\n"


_CONTRACT_HINTS = (
    (("clean", "table", "data"), "cleaned_data"),
    (("report", "quality", "qc"), "quality_report"),
    (("profile", "stat", "summar"), "data_profile"),
    (("evidence", "trace", "proof", "map"), "evidence_map"),
)


def _fill_contract_by_hint(result, contract, artifacts):
    if not isinstance(contract, dict):
        return
    for key, val in contract.items():
        current = result.get(key)
        if current not in (None, [], {}):
            continue
        low = str(key).lower()
        for hints, art in _CONTRACT_HINTS:
            if any(h in low for h in hints):
                result[key] = artifacts.get(art)
                break


def _write_checkpoint(work_dir, phase, manifest, steps_done, steps_pending, started):
    checkpoint = {
        "phase": phase, "started": started, "finished": _iso_now(),
        "manifest": manifest, "steps_done": steps_done,
        "steps_pending": steps_pending,
    }
    write_json(os.path.join(work_dir, "checkpoint.json"), checkpoint)


def merge_decisions(result, decisions):
    if not isinstance(decisions, dict):
        return result
    if decisions.get("status") in STATUS_VALUES:
        result["status"] = decisions["status"]
    cv = decisions.get("contract_values")
    if isinstance(cv, dict):
        for key, val in cv.items():
            if key in result.get("_contract_keys", []) or key in result:
                result[key] = val
    for key in ("sources", "uncertainty", "pending_human", "errors", "degradations"):
        extra = decisions.get(key)
        if isinstance(extra, list):
            result[key] = list(result.get(key, [])) + list(extra)
    extra_log = decisions.get("change_log")
    if isinstance(extra_log, list):
        result["change_log"] = list(result.get("change_log", [])) + list(extra_log)
    art = decisions.get("artifacts")
    if isinstance(art, dict):
        result.setdefault("artifacts", {}).update(art)
    return result


def run_finalize(input_dir, output_dir, work_dir):
    result_path = os.path.join(output_dir, "result.json")
    info = read_text_robust(result_path)
    result, status = parse_json_robust(info.get("text"), None)
    if not isinstance(result, dict):
        result = build_skeleton(load_case(input_dir))
    decisions_path = os.path.join(work_dir, "agent_decisions.json")
    if os.path.isfile(decisions_path):
        dinfo = read_text_robust(decisions_path)
        decisions, _ = parse_json_robust(dinfo.get("text"), {})
        result = merge_decisions(result, decisions)
    result["runtime"]["finished"] = _iso_now()
    if result.get("status") == "partial":
        result.setdefault("pending_human", [])
        if not any("未完成" in str(x) for x in result["pending_human"]):
            result["pending_human"].append("Agent 尚未给出最终裁决（主键/单位/异常/冲突），状态保持 partial")
    write_json(result_path, result)
    return result


def run_selfcheck(output_dir):
    checks = []
    result_path = os.path.join(output_dir, "result.json")

    def add(item, ok, detail=""):
        checks.append({"item": item, "ok": bool(ok), "detail": detail})

    if not os.path.isfile(result_path):
        add("result.json 存在", False, result_path)
        return {"ok": False, "checks": checks}

    info = read_text_robust(result_path)
    result, status = parse_json_robust(info.get("text"), None)
    add("result.json 是合法 JSON", status == "ok", status)
    if not isinstance(result, dict):
        return {"ok": False, "checks": checks}

    add("case_id 非空", bool(result.get("case_id")), str(result.get("case_id")))
    add("status 取值合法", result.get("status") in STATUS_VALUES, str(result.get("status")))
    contract_keys = result.get("_contract_keys", [])
    missing_keys = [k for k in contract_keys if k not in result]
    add("output_contract 全部键已回填", not missing_keys, ", ".join(missing_keys) if missing_keys else "ok")
    for key in ("sources", "uncertainty", "change_log", "pending_human", "errors", "degradations"):
        add(f"{key} 为列表", isinstance(result.get(key), list), type(result.get(key)).__name__)

    artifacts = result.get("artifacts") or {}
    for name, path in artifacts.items():
        if isinstance(path, str):
            exists = os.path.isfile(path) and os.path.getsize(path) > 0
            add(f"产物 {name} 存在且非空", exists, path)

    # Cross-check cleaned_data.csv against data_profile.json when both exist.
    cleaned = artifacts.get("cleaned_data")
    profile = artifacts.get("data_profile")
    if isinstance(cleaned, str) and os.path.isfile(cleaned) and isinstance(profile, str) and os.path.isfile(profile):
        cp = parse_table(cleaned)
        pinfo = read_text_robust(profile)
        pjson, _ = parse_json_robust(pinfo.get("text"), None)
        if cp.get("error") is None and isinstance(pjson, dict):
            expected_rows = pjson.get("n_rows")
            actual_rows = len(cp["rows"])
            add("cleaned_data.csv 行数与 data_profile.json 一致",
                expected_rows == actual_rows,
                f"profile={expected_rows}, csv={actual_rows}")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
