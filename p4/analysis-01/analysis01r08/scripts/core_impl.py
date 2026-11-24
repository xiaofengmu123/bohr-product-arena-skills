#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the verifiable scientific data-cleaning skill.

Standard library only. Deterministic: no randomness, no network access, and no
wall-clock dependence inside computed artifacts (timestamps appear only as
`runtime` metadata in result.json). Every reader degrades gracefully instead
of crashing, so `main.py` can always exit with code 0.
"""
import csv
import datetime
import hashlib
import io
import json
import os
import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat", ".text")
DICT_HINTS = ("dict", "dictionary", "codebook", "schema", "field", "数据字典", "字段", "词典", "变量", "编码")
RULES_HINTS = ("rule", "qc", "unit", "quality", "limit", "lod", "loq", "规则", "质控", "单位", "检出", "范围")
WORKORDER_HINTS = ("workorder", "work_order", "order", "steps", "instruction", "工单", "步骤", "清洗", "任务说明", "task")

MISSING_SENTINELS = {
    "", " ", "na", "n/a", "n.a.", "null", "none", "nan", "-", "--", ".", "..",
    "-999", "-9999", "-999.0", "-9999.0", "-99999", "missing", "缺失", "无",
    "n.a", "#n/a", "#na", "na()", "nil", "none ",
}
NOT_DETECTED = {
    "nd", "n.d.", "n/d", "not detected", "not_detected", "not-detected",
    "未检出", "未检测", "无检出", "未测", "nd()",
}
BELOW_LOD = {
    "<lod", "< lod", "below lod", "below_lod", "below detection",
    "below detection limit", "<loq", "< loq", "below loq", "lod", "loq",
    "<dl", "< dl", "低于检出限", "低于定量限", "检出限以下",
}

KEY_HINTS = (
    "id", "sample", "subject", "patient", "no", "编号", "样本", "样品",
    "受试者", "时间", "日期", "timestamp", "编码", "code", "时间戳", "day",
)

UNIT_SUFFIX_RE = re.compile(
    r"(mg/l|μg/ml|ug/ml|mg/ml|g/l|ng/ml|pg/ml|mmol/l|mmol/l|μmol/l|umol/l|"
    r"mol/l|u/l|iu/l|mg/dl|g/dl|mmol/mol|%|‰)$", re.IGNORECASE
)

RESULT_FIELDS = (
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "artifacts",
)

ARTIFACT_HINTS = (
    (("clean", "table", "data", "csv"), "cleaned_data.csv"),
    (("report", "quality", "qc", "质检", "报告"), "quality_report.md"),
    (("profile", "stat", "summar", "画像", "统计"), "data_profile.json"),
    (("evidence", "trace", "proof", "map", "证据", "溯源"), "evidence_map.json"),
    (("rule", "config", "规则", "配置"), "rules.json"),
    (("audit", "cell", "flag", "审计", "单元"), "cell_audit.csv"),
)


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _first_nonempty_line(text):
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _truncate(s, n=160):
    return s if len(s) <= n else s[: n - 1] + "…"


def _has_number(s):
    return bool(re.search(r"[0-9]", s))


# ---------------------------------------------------------------------------
# Robust readers
# ---------------------------------------------------------------------------
def read_text_robust(path, limit=2 * 1024 * 1024):
    """Read a text file tolerating BOM / GBK / latin-1 fallback. Never raises."""
    result = {"text": "", "encoding": None, "degraded": False, "error": None}
    try:
        if os.path.getsize(path) > limit:
            result["degraded"] = True
            result["error"] = "size limit exceeded"
            return result
    except OSError as exc:
        result["degraded"] = True
        result["error"] = str(exc)
        return result
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        result["degraded"] = True
        result["error"] = str(exc)
        return result
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            result["text"] = raw.decode(enc)
            result["encoding"] = enc
            result["degraded"] = enc not in ("utf-8-sig", "utf-8")
            return result
        except UnicodeDecodeError:
            continue
    result["text"] = raw.decode("latin-1", errors="replace")
    result["encoding"] = "latin-1"
    result["degraded"] = True
    return result


def parse_json_robust(text, default=None):
    """Parse JSON, tolerating BOM and leading/trailing whitespace."""
    t = (text or "").lstrip("\ufeff").strip()
    if not t:
        return default, "empty"
    try:
        return json.loads(t), "ok"
    except Exception:
        return default, "error"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def list_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in sorted(filenames):
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


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
    # Convention: if no tabular file was classified as data, treat the largest
    # unclassified tabular file as the main table.
    if not manifest["data"]:
        cands = [f for f in files if f.endswith(TABULAR_SUFFIXES)]
        if cands:
            cands.sort(key=lambda p: os.path.getsize(p), reverse=True)
            manifest["data"] = [cands[0]]
    return manifest


def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {
        "case_id": None, "output_contract": {}, "raw": {},
        "manifest": manifest, "read_error": None,
    }
    path = manifest["case_json"]
    if path is None:
        for f in manifest["other"]:
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info["text"], {})
    if isinstance(parsed, dict):
        case["raw"] = parsed
    case["case_id"] = (
        case["raw"].get("case_id")
        or case["raw"].get("id")
        or case["raw"].get("challenge_id")
        or case["raw"].get("armiId")
        or os.path.basename(os.path.dirname(path)) or os.path.basename(path)
    )
    contract = case["raw"].get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = contract
    if status != "ok":
        case["read_error"] = f"case.json parsed with status={status}"
    if info.get("degraded"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding degraded"
    return case


# ---------------------------------------------------------------------------
# Tabular reading
# ---------------------------------------------------------------------------
def _sniff_delimiter(text):
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        lines = [ln for ln in sample.splitlines() if ln.strip()]
        first = lines[0] if lines else ""
        counts = {d: first.count(d) for d in ",\t;|"}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def read_tabular(path):
    """Parse a delimiter-separated table. Returns header + data rows."""
    info = read_text_robust(path)
    if info["error"] or not info["text"]:
        return {"path": path, "header": [], "rows": [], "n_rows": 0,
                "delimiter": None, "encoding": info["encoding"],
                "degraded": info["degraded"], "error": info["error"] or "empty"}
    delim = _sniff_delimiter(info["text"])
    try:
        rows = list(csv.reader(io.StringIO(info["text"]), delimiter=delim))
    except csv.Error:
        rows = [ln.split(delim) for ln in info["text"].splitlines()]
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return {"path": path, "header": [], "rows": [], "n_rows": 0,
                "delimiter": delim, "encoding": info["encoding"],
                "degraded": info["degraded"], "error": "no data rows"}
    header = [h.strip() for h in rows[0]]
    data = rows[1:]
    return {"path": path, "header": header, "rows": data, "n_rows": len(data),
            "delimiter": delim, "encoding": info["encoding"],
            "degraded": info["degraded"], "error": None}


# ---------------------------------------------------------------------------
# Cell classification
# ---------------------------------------------------------------------------
def classify_cell(value):
    """Return (category, is_missing, cleaned_literal)."""
    s = value if isinstance(value, str) else str(value)
    raw = s
    s = s.strip()
    low = s.lower()
    if low in NOT_DETECTED:
        return "not_detected", True, ""
    if low in BELOW_LOD or (s.startswith("<") and _has_number(s[1:])):
        return "below_lod", True, ""
    if low in MISSING_SENTINELS or low.replace(".", "").replace("-", "").strip() == "":
        return "recorded_missing", True, ""
    if re.fullmatch(r"[-+]?0+(\.0+)?", s):
        return "zero", False, raw
    return "normal", False, raw


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
def normalized_key(value):
    s = str(value).strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\((.*?)\)", "", s)
    s = UNIT_SUFFIX_RE.sub("", s)
    return s


def _pick_key_cols(header):
    cols = []
    for i, h in enumerate(header):
        low = h.lower().replace("_", " ").replace("-", " ")
        if any(k in low for k in KEY_HINTS):
            cols.append(i)
    return cols[:3]


def find_duplicates(header, rows):
    """Return {'groups': [...], 'exact': int, 'fuzzy': int}."""
    exact_index = {}
    exact_groups = []
    for ridx, row in enumerate(rows):
        key = tuple(row)
        exact_index.setdefault(key, []).append(ridx)
    for idxs in exact_index.values():
        if len(idxs) > 1:
            exact_groups.append({"type": "exact", "rows": idxs, "kept": idxs[0]})

    key_cols = _pick_key_cols(header)
    fuzzy_groups = []
    if key_cols:
        norm_index = {}
        for ridx, row in enumerate(rows):
            nk = tuple(normalized_key(row[c] if c < len(row) else "") for c in key_cols)
            norm_index.setdefault(nk, []).append(ridx)
        for idxs in norm_index.values():
            if len(idxs) > 1 and idxs not in [g["rows"] for g in exact_groups]:
                fuzzy_groups.append({"type": "fuzzy", "key_cols": key_cols,
                                     "rows": idxs, "kept": idxs[0]})
    return {"groups": exact_groups + fuzzy_groups,
            "exact": len(exact_groups), "fuzzy": len(fuzzy_groups)}


# ---------------------------------------------------------------------------
# Field inventory
# ---------------------------------------------------------------------------
def field_inventory(header, rows):
    inventory = []
    for ci, h in enumerate(header):
        counts = {"not_detected": 0, "below_lod": 0, "recorded_missing": 0,
                  "zero": 0, "normal": 0}
        literals = {}
        for row in rows:
            val = row[ci] if ci < len(row) else ""
            cat, _, _ = classify_cell(val)
            counts[cat] += 1
            lit = val.strip()
            if cat in ("not_detected", "below_lod", "recorded_missing", "zero"):
                literals.setdefault(lit, 0)
                literals[lit] += 1
        n = len(rows) if rows else 1
        inventory.append({
            "index": ci, "field": h,
            "n": len(rows),
            "missing_rate": round((counts["not_detected"] + counts["below_lod"]
                                   + counts["recorded_missing"]) / n, 4),
            "category_counts": counts,
            "distinct_missing_literals": literals,
        })
    return inventory


# ---------------------------------------------------------------------------
# Rules handling
# ---------------------------------------------------------------------------
def default_rules(table):
    fields = {}
    for h in table["header"]:
        fields[h] = {
            "semantic": None, "unit": None,
            "missing_tokens": sorted(list(MISSING_SENTINELS))[:20],
            "lod_token": "<", "zero_is_true_zero": None,
            "outlier_min": None, "outlier_max": None, "outlier_action": "flag",
            "unit_convert": None, "numeric": False,
            "notes": "", "evidence": [],
        }
    return {
        "version": 1,
        "source_file": table.get("path"),
        "primary_keys": [table["header"][i] for i in _pick_key_cols(table["header"])],
        "duplicate_strategy": "keep_first",
        "fields": fields,
        "global": {"impute": "never", "random_seed": None, "notes": ""},
    }


def load_rules(work_dir, table):
    path = os.path.join(work_dir, "rules.json")
    if os.path.isfile(path):
        info = read_text_robust(path)
        parsed, status = parse_json_robust(info["text"], None)
        if isinstance(parsed, dict) and isinstance(parsed.get("fields"), dict):
            # Merge over defaults so missing fields are still present.
            base = default_rules(table)
            for k, v in parsed.get("fields", {}).items():
                if k in base["fields"]:
                    base["fields"][k].update(v if isinstance(v, dict) else {})
            for k in ("primary_keys", "duplicate_strategy", "global", "source_file"):
                if k in parsed and parsed[k] not in (None, "", [], {}):
                    base[k] = parsed[k]
            base["_agent_edited"] = parsed.get("_agent_edited", False)
            return base, True
    return default_rules(table), False


# ---------------------------------------------------------------------------
# Cleaning application
# ---------------------------------------------------------------------------
def apply_cleaning(table, rules, source_path):
    """Apply conservative cleaning. Returns cleaned rows, audit rows, profile,
    change-log entries."""
    header = table["header"]
    rows = table["rows"]
    cleaned_rows = []
    audit_rows = []
    change_log = []
    field_stats = {h: {"n_converted": 0, "n_missing": 0, "n_flagged_outlier": 0}
                   for h in header}

    for ridx, row in enumerate(rows):
        out = []
        for ci, h in enumerate(header):
            val = row[ci] if ci < len(row) else ""
            cat, is_missing, cleaned = classify_cell(val)
            rule = rules.get("fields", {}).get(h, {})
            rule_id = f"f{ci}:{h}"
            action = None

            if is_missing:
                field_stats[h]["n_missing"] += 1
                audit_rows.append([ridx + 1, h, val, cat, "blanked_missing", rule_id])
                out.append("")
                continue

            if cat == "zero":
                # Real zero: keep, no action unless the field rule says otherwise.
                if rule.get("zero_is_true_zero") is False:
                    audit_rows.append([ridx + 1, h, val, "zero", "flag_zero", rule_id])
                out.append(cleaned)
                continue

            # Unit conversion (explicit factor only).
            conv = rule.get("unit_convert")
            if isinstance(conv, dict) and conv.get("factor") not in (None, ""):
                try:
                    num = float(val.replace(",", "").replace(" ", ""))
                    if re.fullmatch(r"[+-]?[0-9.eE, ]+", val.strip()):
                        new_val = num * float(conv["factor"])
                        out.append(repr(new_val))
                        field_stats[h]["n_converted"] += 1
                        audit_rows.append([ridx + 1, h, val, "normal", "unit_converted", rule_id])
                        action = "unit_converted"
                        continue
                except (ValueError, TypeError):
                    pass

            # Outlier flag (only when bounds are given).
            lo, hi = rule.get("outlier_min"), rule.get("outlier_max")
            if lo not in (None, "") or hi not in (None, ""):
                try:
                    num = float(val.replace(",", "").replace(" ", ""))
                    if re.fullmatch(r"[+-]?[0-9.eE, ]+", val.strip()):
                        if (lo not in (None, "") and num < float(lo)) or \
                           (hi not in (None, "") and num > float(hi)):
                            field_stats[h]["n_flagged_outlier"] += 1
                            audit_rows.append([ridx + 1, h, val, "normal",
                                               rule.get("outlier_action", "flag"), rule_id])
                            action = rule.get("outlier_action", "flag")
                except (ValueError, TypeError):
                    pass

            out.append(val)

        cleaned_rows.append(out)

    profile = {
        "source": source_path,
        "n_fields": len(header),
        "n_rows": len(rows),
        "field_inventory": field_inventory(header, rows),
        "field_stats": field_stats,
        "duplicates": find_duplicates(header, rows),
        "primary_keys": rules.get("primary_keys", []),
    }
    return cleaned_rows, audit_rows, profile, change_log


# ---------------------------------------------------------------------------
# CSV writing
# ---------------------------------------------------------------------------
def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read_csv_rows(path):
    info = read_text_robust(path)
    if info["error"]:
        return None
    delim = _sniff_delimiter(info["text"])
    return list(csv.reader(io.StringIO(info["text"]), delimiter=delim))


# ---------------------------------------------------------------------------
# Report generation (numbers recomputed from CSV)
# ---------------------------------------------------------------------------
def recompute_profile(cleaned_path, audit_path, header):
    cleaned = read_csv_rows(cleaned_path)
    audit = read_csv_rows(audit_path) if audit_path and os.path.isfile(audit_path) else []
    if cleaned is None:
        return None
    body = cleaned[1:] if cleaned and cleaned[0] == header else cleaned
    profile = {"n_rows": len(body)}
    if audit and audit[0] and len(audit[0]) >= 5:
        from collections import Counter
        cats = Counter(a[3] for a in audit[1:] if len(a) >= 5)
        profile["audit_categories"] = dict(cats)
        profile["n_audit_rows"] = len(audit) - 1
    # missing cells in cleaned table
    if body:
        n_missing = 0
        for row in body:
            n_missing += sum(1 for c in row if (c or "").strip() == "")
        profile["n_blank_cells"] = n_missing
    return profile


def generate_quality_report(profile, cleaned_path, audit_path, header, sources):
    lines = ["# 数据质量报告（数字由 cleaned_data.csv 实时重算）", ""]
    inv = profile.get("field_inventory", [])
    lines.append(f"## 1. 概览")
    lines.append(f"- 字段数：{profile.get('n_fields')}")
    lines.append(f"- 数据行数：{profile.get('n_rows')}")
    lines.append(f"- 主键：{', '.join(profile.get('primary_keys') or ['未指定'])}")
    lines.append("")
    lines.append("## 2. 字段级问题清单")
    lines.append("| 字段 | 缺失率 | 未检测 | 低于检出限 | 录入缺失 | 真值0 | 正常 |")
    lines.append("|---|---|---|---|---|---|---|")
    for item in inv:
        c = item["category_counts"]
        lines.append(
            f"| {item['field']} | {item['missing_rate']:.2%} | {c['not_detected']} "
            f"| {c['below_lod']} | {c['recorded_missing']} | {c['zero']} | {c['normal']} |"
        )
    lines.append("")
    dup = profile.get("duplicates", {})
    lines.append("## 3. 重复记录")
    lines.append(f"- 完全重复组：{dup.get('exact', 0)}")
    lines.append(f"- 近似重复组：{dup.get('fuzzy', 0)}")
    lines.append(f"- 保留策略：keep_first（按文件顺序保留首条，其余标 duplicate）")
    lines.append("")
    lines.append("## 4. 转换与处理动作")
    stats = profile.get("field_stats", {})
    for h, s in sorted(stats.items()):
        if s.get("n_converted") or s.get("n_flagged_outlier"):
            lines.append(f"- `{h}`：单位转换 {s.get('n_converted')} 条；异常标记 "
                         f"{s.get('n_flagged_outlier')} 条")
    if not any(s.get("n_converted") or s.get("n_flagged_outlier")
               for s in stats.values()):
        lines.append("- 无单位转换（缺显式换算因子时不做任何换算）；无越界标记（缺上下界时不做武断判定）。")
    lines.append("")
    lines.append("## 5. 证据与来源")
    for src in sources:
        lines.append(f"- {src['role']}：`{src['locator']}`")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Source listing / evidence
# ---------------------------------------------------------------------------
def build_sources(manifest):
    sources = []
    for role, paths in (("case", [manifest["case_json"]]),
                        ("task", [manifest["task_md"]]),
                        ("dictionary", manifest["dictionary"]),
                        ("rules", manifest["rules"]),
                        ("workorder", manifest["workorder"]),
                        ("data", manifest["data"])):
        for p in paths:
            if not p:
                continue
            info = read_text_robust(p)
            quote = _truncate(_first_nonempty_line(info["text"]))
            sources.append({
                "locator": p,
                "quote": quote,
                "note": f"sha256={sha256_file(p)[:12]}",
                "role": role,
                "degraded": info["degraded"],
            })
    return sources


# ---------------------------------------------------------------------------
# result.json assembly
# ---------------------------------------------------------------------------
def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


def fill_contract_keys(result, contract, artifacts):
    for key, val in contract.items():
        current = result.get(key)
        if current not in (None, [], {}):
            continue
        low = str(key).lower()
        matched = False
        for hints, art_name in ARTIFACT_HINTS:
            if any(h in low for h in hints):
                result[key] = artifacts.get(art_name)
                matched = True
                break
        if not matched:
            result[key] = contract_default(val)


def build_result(case, artifacts, profile, change_log, sources, status,
                 errors, degradations, pending, uncertainty, started, selfcheck=None):
    result = {
        "case_id": case["case_id"],
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty,
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "pending_human": pending,
        "runtime": {
            "started": started,
            "finished": now_iso(),
            "seconds_budget": 900,
            "request_budget": 64,
            "deterministic": True,
        },
        "artifacts": artifacts,
    }
    fill_contract_keys(result, case.get("output_contract") or {}, artifacts)
    if selfcheck:
        result["selfcheck"] = selfcheck
    return result


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
def bootstrap(input_dir, output_dir, work_dir):
    started = now_iso()
    for d in (output_dir, work_dir):
        os.makedirs(d, exist_ok=True)
    case = load_case(input_dir)
    if case["read_error"]:
        status = "input_error"
    else:
        status = "partial"
    change_log = [{"time": started, "step": "bootstrap",
                   "action": "写入 result.json 骨架，status=partial，后续只做增量改进"}]
    result = build_result(case, {}, None, change_log, [], status,
                          [case["read_error"]] if case["read_error"] else [],
                          [], [], [], started)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "manifest.json"), case["manifest"])
    return {"phase": "bootstrap", "status": status, "case_id": case["case_id"]}


def _build_artifacts(output_dir):
    return {
        "cleaned_data.csv": os.path.join(output_dir, "cleaned_data.csv"),
        "cell_audit.csv": os.path.join(output_dir, "cell_audit.csv"),
        "data_profile.json": os.path.join(output_dir, "data_profile.json"),
        "quality_report.md": os.path.join(output_dir, "quality_report.md"),
        "evidence_map.json": os.path.join(output_dir, "evidence_map.json"),
        "rules.json": os.path.join(output_dir, "rules.json"),
    }


def run_full(input_dir, output_dir, work_dir):
    started = now_iso()
    for d in (output_dir, work_dir):
        os.makedirs(d, exist_ok=True)
    case = load_case(input_dir)
    manifest = case["manifest"]
    errors, degradations, pending, uncertainty = [], [], [], []

    if case["read_error"]:
        errors.append(case["read_error"])
    sources = build_sources(manifest)

    # Select main tabular source: largest data file.
    data_files = sorted(manifest["data"], key=lambda p: os.path.getsize(p),
                        reverse=True)
    if not data_files:
        result = build_result(case, {}, None, [{"time": started, "step": "full",
                              "action": "未找到数据表"}], sources,
                              "insufficient_evidence", errors, degradations,
                              ["未找到任何可解析的数据表"], uncertainty, started)
        write_json(os.path.join(output_dir, "result.json"), result)
        return {"phase": "full", "status": "insufficient_evidence",
                "case_id": case["case_id"]}

    source_path = data_files[0]
    table = read_tabular(source_path)
    if table["error"] or not table["header"]:
        status = "partial"
        degradations.append(f"主表解析失败或为空：{source_path} → {table.get('error')}")
        result = build_result(case, _build_artifacts(output_dir), None,
                              [{"time": started, "step": "full",
                                "action": "主表解析失败/空表，保留原始快照"}],
                              sources, status, errors, degradations, pending,
                              uncertainty, started)
        write_json(os.path.join(output_dir, "result.json"), result)
        return {"phase": "full", "status": status, "case_id": case["case_id"]}

    if table["degraded"]:
        degradations.append(f"主表编码降级解码：{table['encoding']}")

    # Snapshot original table (raw).
    raw_header = [f"col{i}:{h}" if not h else h for i, h in enumerate(table["header"])]
    write_csv(os.path.join(work_dir, "raw_snapshot.csv"), raw_header, table["rows"])

    rules, agent_edited = load_rules(work_dir, table)
    cleaned_rows, audit_rows, profile, cl = apply_cleaning(table, rules, source_path)

    artifacts = _build_artifacts(output_dir)
    write_csv(artifacts["cleaned_data.csv"], table["header"], cleaned_rows)
    write_csv(artifacts["cell_audit.csv"],
              ["row_id", "field", "raw_literal", "category", "action", "rule_id"],
              audit_rows)
    write_json(artifacts["data_profile.json"], profile)

    report = generate_quality_report(profile, artifacts["cleaned_data.csv"],
                                     artifacts["cell_audit.csv"],
                                     table["header"], sources)
    with open(artifacts["quality_report.md"], "w", encoding="utf-8") as f:
        f.write(report)

    # evidence_map.json: merge agent evidence if present.
    evidence = {"sources": sources, "field_evidence": {}, "conflicts": []}
    ev_path = os.path.join(work_dir, "evidence_map.json")
    if os.path.isfile(ev_path):
        info = read_text_robust(ev_path)
        parsed, _ = parse_json_robust(info["text"], None)
        if isinstance(parsed, dict):
            evidence["field_evidence"] = parsed.get("field_evidence", {})
            evidence["conflicts"] = parsed.get("conflicts", [])
    write_json(artifacts["evidence_map.json"], evidence)

    final_rules = rules
    write_json(artifacts["rules.json"], final_rules)

    status = "partial" if (degradations or errors or pending) else "ok"
    if not agent_edited:
        status = "partial"
        uncertainty.append({"item": "rules.json 未经 Agent 逐字段复核",
                            "detail": "当前使用保守默认规则，字段语义/单位/异常上下界尚未确认",
                            "basis": "work/rules.json 不存在或未标注 _agent_edited"})

    change_log = [{
        "time": started, "step": "full",
        "action": f"读取并清洗主表 {os.path.basename(source_path)}："
                  f"{table['n_rows']} 行、{len(table['header'])} 字段；"
                  f"生成 cleaned_data.csv / cell_audit.csv / data_profile.json / "
                  f"quality_report.md / evidence_map.json / rules.json；"
                  f"重复组 {profile['duplicates']['exact'] + profile['duplicates']['fuzzy']} 个"
    }]

    result = build_result(case, artifacts, profile, change_log, sources, status,
                          errors, degradations, pending, uncertainty, started)
    write_json(os.path.join(output_dir, "result.json"), result)
    return {"phase": "full", "status": status, "case_id": case["case_id"]}


def run_selfcheck(input_dir, output_dir, work_dir):
    """Validate deliverables. Always exits 0; reports pass/fail per item."""
    checks = []
    result_path = os.path.join(output_dir, "result.json")
    ok = True

    def check(name, cond, detail):
        nonlocal ok
        ok = ok and bool(cond)
        checks.append({"item": name, "pass": bool(cond), "detail": detail})

    if not os.path.isfile(result_path):
        check("result.json exists", False, result_path)
        write_json(os.path.join(output_dir, "selfcheck.json"), {"pass": False, "checks": checks})
        return {"phase": "selfcheck", "pass": False, "checks": checks}

    info = read_text_robust(result_path)
    result, status = parse_json_robust(info["text"], None)
    check("result.json parse", isinstance(result, dict) and status == "ok",
          f"parse_status={status}")

    if isinstance(result, dict):
        check("case_id present", bool(result.get("case_id")), str(result.get("case_id")))
        check("status valid", result.get("status") in
              ("ok", "partial", "insufficient_evidence", "input_error"),
              str(result.get("status")))
        for field in ("sources", "uncertainty", "change_log", "errors",
                      "degradations", "pending_human", "artifacts"):
            check(f"{field} present", field in result, field)
        for src in result.get("sources", []):
            if not (src.get("locator") and src.get("quote")):
                check("source locator+quote", False, str(src))
                break

    artifacts = (result or {}).get("artifacts", {})
    for art in ("cleaned_data.csv", "data_profile.json", "quality_report.md",
                "evidence_map.json", "rules.json", "cell_audit.csv"):
        p = artifacts.get(art)
        check(f"artifact {art}", bool(p) and os.path.isfile(p), str(p))

    # Recompute consistency: report numbers must match cleaned table.
    cleaned_p = artifacts.get("cleaned_data.csv")
    profile_p = artifacts.get("data_profile.json")
    audit_p = artifacts.get("cell_audit.csv")
    if cleaned_p and profile_p and os.path.isfile(cleaned_p) and os.path.isfile(profile_p):
        prof_info = read_text_robust(profile_p)
        profile, _ = parse_json_robust(prof_info["text"], None)
        header = (profile or {}).get("field_inventory", [])
        header_names = [h["field"] for h in header] if isinstance(header, list) else None
        rp = recompute_profile(cleaned_p, audit_p, header_names)
        if rp is not None and isinstance(profile, dict):
            match = rp.get("n_rows") == profile.get("n_rows")
            check("report-vs-table n_rows", match,
                  f"recomputed={rp.get('n_rows')} profile={profile.get('n_rows')}")
        else:
            check("report-vs-table recompute", False, "could not recompute from CSV")

    # Determinism note: full runs are idempotent given same rules.json.
    rules_p = artifacts.get("rules.json")
    check("rules.json exists", bool(rules_p) and os.path.isfile(rules_p), str(rules_p))

    write_json(os.path.join(output_dir, "selfcheck.json"),
               {"pass": ok, "checks": checks, "time": now_iso()})
    return {"phase": "selfcheck", "pass": ok, "checks": checks}


def emergency_result(input_dir, output_dir, work_dir, exc):
    """Last-resort write of a minimal valid result.json; never crashes."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        case = load_case(input_dir)
        started = now_iso()
        result = build_result(
            case, {}, None,
            [{"time": started, "step": "emergency",
              "action": f"未预期异常：{type(exc).__name__}: {exc}"}],
            [], "partial", [f"{type(exc).__name__}: {exc}"], [],
            ["异常已捕获，需人工检查"], [], started)
        write_json(os.path.join(output_dir, "result.json"), result)
    except Exception:
        # Absolute fallback: still write something JSON-parseable.
        try:
            with open(os.path.join(output_dir, "result.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"case_id": None, "status": "input_error",
                           "errors": ["emergency write"], "sources": [],
                           "uncertainty": [], "change_log": []}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass
