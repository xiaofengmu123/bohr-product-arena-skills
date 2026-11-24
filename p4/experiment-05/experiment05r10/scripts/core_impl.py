#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 实验结果解释 Skill 的纯标准库实现。

设计取舍（相对"结论直写"类包的显式差异）：
  1. 中间表示（IR）：脚本只产出 facts.json（文件清单 + 逐列统计 + 候选异常 + 降级记录），
     绝不产出结论。趋势/异常/原因/复核/后续五段结论由 Agent 在 facts 之上写回 result.json。
  2. 事实与结论分层：result.json 中的任何数值、阈值、定位都必须回指 facts 的 locator + 逐字 quote，
     禁止按文件名/标签臆断来源。
  3. 校验顺序固定：先 schema/契约 → 再证据完整性 → 再确定性，任何一步失败都可定位。
"""

import csv
import hashlib
import io
import json
import math
import os
import re
import statistics
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000
MAX_CELLS = 2_000_000  # 行×列 超限后跳过逐格扫描，只做逐列统计

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

TOP_KEYS = (
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
)
INTERPRETATION_KEYS = ("trends", "anomalies", "possible_causes",
                        "verification_steps", "followup_experiments")
PLAN_KEYS = ("hypothesis", "testable_question", "variables", "controls",
             "steps", "parameters", "resources", "interpretation_criteria")
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")

TEXT_EXTS = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log",
             ".xml", ".html", ".tex", ".bib"}
UNIT_HINT_RE = re.compile(
    r"(?i)\b(mpa|kpa|pa|gpa|n|kn|mm|cm|m|um|nm|s|ms|min|h|hz|khz|"
    r"kg|g|mg|ug|ml|l|ul|mol|mmol|umol|°c|deg|%|ppm|ppb|"
    r"v|mv|a|ma|ohm|w|mw|ph|od|au|rpm|cfu|iu)\b")
NUMERIC_LIKE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# 基础 IO（UTF-8 优先，坏编码降级，原子写盘，确定性排序）                    #
# --------------------------------------------------------------------------- #
def read_text(path, max_bytes=MAX_FILE_BYTES):
    """返回 (text, encoding)；失败返回 (None, reason)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, str(exc)
    if size > max_bytes:
        return None, "file exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    return None, "unreadable encoding"


def load_json_safe(path):
    text, _enc = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _atomic(path, payload, binary=False):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    with open(tmp, mode, **kwargs) as fh:
        fh.write(payload)
    os.replace(tmp, path)


def write_json_atomic(path, obj):
    payload = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic(path, payload)


def write_text_atomic(path, text):
    _atomic(path, text)


def sha256_head(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def discover_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append({"path": full, "rel": rel, "size_bytes": size,
                        "ext": os.path.splitext(fn)[1].lower(),
                        "name": fn})
    out.sort(key=lambda x: x["rel"])
    return out


# --------------------------------------------------------------------------- #
# case_id 解析与 output_contract 回填                                         #
# --------------------------------------------------------------------------- #
def resolve_case_id(input_dir):
    obj = load_json_safe(os.path.join(input_dir, "case.json"))
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "case"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        content = obj.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", content, re.I)
            if m:
                return m.group(1)
    text, _enc = read_text(os.path.join(input_dir, "task.md"))
    if text:
        m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_ ]([0-9a-fA-F-]{8,})", text)
        if m:
            return m.group(0)
    base = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown"
    return base


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def normalize_output_contract(contract):
    """把 output_contract 归一为字段名列表（容忍 dict/list/str 多形态）。"""
    names = []
    if isinstance(contract, dict):
        for key in ("fields", "required", "schema"):
            v = contract.get(key)
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, str):
                        names.append(x)
                    elif isinstance(x, dict):
                        names.extend(k for k in x if isinstance(k, str))
            elif isinstance(v, dict):
                names.extend(k for k in v if isinstance(k, str))
        if not names:
            names = [k for k in contract if isinstance(k, str)
                     and k not in ("fields", "required", "schema",
                                   "description", "title", "type")]
    elif isinstance(contract, list):
        for x in contract:
            if isinstance(x, str):
                names.append(x)
            elif isinstance(x, dict):
                names.extend(k for k in x if isinstance(k, str))
    elif isinstance(contract, str):
        names.append(contract)
    return names


def merge_output_contract(result, case_obj):
    """回填 output_contract 全部字段（缺值给 null/[]），不改名不删未知键。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    if contract is None:
        return []
    added = []
    for k in normalize_output_contract(contract):
        if k not in result:
            default = None
            if isinstance(contract, dict) and k in contract:
                default = _default_for(contract[k])
            result[k] = default
            added.append(k)
    return added


# --------------------------------------------------------------------------- #
# 结果模板                                                                    #
# --------------------------------------------------------------------------- #
def empty_template(case_id):
    return {
        "case_id": case_id or "",
        "status": "partial",
        "task_type": "experiment-result-interpretation",
        "summary": "",
        "interpretation": {
            "trends": [],
            "anomalies": [],
            "possible_causes": [],
            "verification_steps": [],
            "followup_experiments": [],
        },
        "experiment_plan": {
            "hypothesis": "",
            "testable_question": "",
            "variables": [],
            "controls": [],
            "steps": [],
            "parameters": [],
            "resources": [],
            "interpretation_criteria": [],
        },
        "evidence_and_risk": {
            "evidence_basis": [],
            "risk_and_anomaly_handling": [],
            "assumptions": [],
        },
        "confirmation_items": {
            "safety": [],
            "ethics": [],
            "equipment_availability": [],
            "expert_confirmation": [],
        },
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_at": None, "finished_at": None, "elapsed_seconds": None},
    }


def _input_has_material(input_dir):
    files = discover_files(input_dir)
    for f in files:
        if f["name"] in ("case.json", "task.md"):
            continue
        return True
    return False


def build_skeleton(input_dir, output_dir):
    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    task_text, _enc = read_text(os.path.join(input_dir, "task.md"))
    case_id = resolve_case_id(input_dir)
    result = empty_template(case_id)
    added = merge_output_contract(result, case_obj)

    has_case = case_obj is not None
    has_task = task_text is not None
    has_material = _input_has_material(input_dir)

    if not has_case and not has_task:
        result["status"] = "insufficient_evidence"
        result["errors"].append({
            "where": "/input",
            "problem": "case.json 与 task.md 均缺失或不可读",
            "action": "停止分析，如实报告材料不足",
        })
    else:
        result["status"] = "partial"

    if added:
        result["change_log"].append({
            "stage": "skeleton", "at": now_ts(),
            "action": "回填 output_contract 字段",
            "detail": ", ".join(added),
        })
    if not has_material:
        result["degradations"].append({
            "where": "/input",
            "problem": "未发现除 case.json/task.md 外的材料文件",
            "action": "如需分析实验数据，请确认材料已挂载到 /input",
        })

    result["change_log"].append({
        "stage": "skeleton", "at": now_ts(),
        "action": "落盘 partial 骨架（含全部顶层字段）",
        "detail": "status=%s" % result["status"],
    })
    result["runtime"]["started_at"] = now_ts()

    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"),
                      _report_skeleton(result))
    return result


def _report_skeleton(result):
    return (
        "# 实验结果解释报告\n\n"
        "- case_id: `%s`\n"
        "- status: `%s`\n"
        "- 说明：本文件为骨架，解释结论由后续阶段增量写入。\n"
        % (result.get("case_id") or "", result.get("status"))
    )


# --------------------------------------------------------------------------- #
# 数值画像（逐列统计 + 候选异常，只产事实不产结论）                          #
# --------------------------------------------------------------------------- #
def _to_float(raw):
    s = (raw or "").strip()
    if not s:
        return None, "missing"
    if NUMERIC_LIKE.match(s):
        try:
            return float(s), "ok"
        except ValueError:
            return None, "unparsed"
    if THOUSANDS.match(s):
        try:
            return float(s.replace(",", "")), "thousands"
        except ValueError:
            return None, "unparsed"
    return None, "unparsed"


def _sniff_delimiter(text):
    sample = text[:16384]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        best, best_score = ",", -1
        for cand in (",", ";", "\t", "|"):
            counts = []
            for line in sample.splitlines()[:5]:
                if line.strip():
                    counts.append(line.count(cand))
            if counts:
                mode = max(set(counts), key=counts.count)
                score = counts.count(mode)
                if score > best_score:
                    best_score, best = score, cand
        return best


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _column_stats(vals):
    n = len(vals)
    stats = {"n": n, "missing": 0, "parse_failures": 0}
    numeric = []
    for v in vals:
        fv, tag = _to_float(v)
        if tag == "missing":
            stats["missing"] += 1
        elif tag in ("ok", "thousands"):
            numeric.append(fv)
        else:
            stats["parse_failures"] += 1
    stats["n_numeric"] = len(numeric)
    if len(numeric) >= 2:
        stats["mean"] = round(statistics.fmean(numeric), 8)
        stats["median"] = round(statistics.median(numeric), 8)
        if len(numeric) >= 2:
            stats["std"] = round(statistics.stdev(numeric), 8)
        stats["min"] = numeric[0]
        stats["max"] = numeric[0]
        for x in numeric:
            if x < stats["min"]:
                stats["min"] = x
            if x > stats["max"]:
                stats["max"] = x
        srt = sorted(numeric)
        stats["q1"] = round(_quantile(srt, 0.25), 8)
        stats["q3"] = round(_quantile(srt, 0.75), 8)
        stats["iqr"] = round(stats["q3"] - stats["q1"], 8)
        med = stats["median"]
        devs = sorted(abs(x - med) for x in numeric)
        stats["mad"] = round(statistics.median(devs), 8) if devs else 0.0
    elif len(numeric) == 1:
        stats["mean"] = numeric[0]
        stats["median"] = numeric[0]
        stats["min"] = numeric[0]
        stats["max"] = numeric[0]
    return stats, numeric


def _outlier_cells(rows, col_index, method):
    """rows: list of (row_idx, value)。返回按偏离降序的离群 cell 列表。"""
    vals = [v for _r, v in rows if v is not None]
    if len(vals) < 4:
        return []
    med = statistics.median(vals)
    out = []
    if method == "z":
        mu = statistics.fmean(vals)
        sd = statistics.stdev(vals)
        if sd == 0:
            return []
        for r, v in rows:
            if v is None:
                continue
            z = (v - mu) / sd
            if abs(z) > 3.0:
                out.append((r, v, round(z, 4), "z", 3.0))
    elif method == "iqr":
        srt = sorted(vals)
        q1 = _quantile(srt, 0.25)
        q3 = _quantile(srt, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            return []
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for r, v in rows:
            if v is not None and (v < lo or v > hi):
                out.append((r, v, round((v - q3) / iqr if v > hi else (q1 - v) / iqr, 4),
                            "iqr", 1.5))
    elif method == "mad":
        devs = sorted(abs(v - med) for _r, v in rows if v is not None)
        mad = statistics.median(devs) if devs else 0.0
        if mad == 0:
            return []
        for r, v in rows:
            if v is None:
                continue
            mz = 0.6745 * (v - med) / mad
            if abs(mz) > 3.5:
                out.append((r, v, round(mz, 4), "mad", 3.5))
    out.sort(key=lambda t: -abs(t[2]))
    return out


def profile_table(path, rel):
    text, enc = read_text(path)
    if text is None:
        return {"file": rel, "kind": "table", "error": enc,
                "degradations": [{"file": rel, "problem": enc}]}
    delim = _sniff_delimiter(text)
    rows = []
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        for r in reader:
            rows.append(r)
    except csv.Error as exc:
        return {"file": rel, "kind": "table", "error": "csv parse: %s" % exc,
                "degradations": [{"file": rel, "problem": "csv parse: %s" % exc}]}
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        return {"file": rel, "kind": "table", "delimiter": delim,
                "has_header": False, "n_rows": 0, "n_cols": 0,
                "degradations": [{"file": rel, "problem": "无有效行"}]}

    n_cols = max(len(r) for r in rows)
    def norm_row(r):
        rr = list(r)
        if len(rr) < n_cols:
            rr += [""] * (n_cols - len(rr))
        return rr
    rows = [norm_row(r) for r in rows]

    header = rows[0]
    # 若首行大部分可解析为数值，则视为无表头
    head_numeric = sum(1 for c in header if _to_float(c)[1] in ("ok", "thousands"))
    has_header = head_numeric < (len(header) / 2) and any(
        not _to_float(c)[1] in ("ok", "thousands", "missing") for c in header)
    data_rows = rows[1:] if has_header else rows
    col_names = []
    for i in range(n_cols):
        name = str(header[i]).strip() if has_header else "col%d" % (i + 1)
        col_names.append(name if name else "col%d" % (i + 1))

    result = {
        "file": rel, "kind": "table", "delimiter": delim,
        "has_header": has_header, "n_rows": len(data_rows),
        "n_cols": n_cols, "columns": [],
    }
    numeric_col_count = 0
    col_vectors = []
    for ci in range(n_cols):
        raw = [r[ci] for r in data_rows]
        stats, numeric = _column_stats(raw)
        col = {
            "index": ci + 1, "name": col_names[ci],
            "type": "numeric" if stats.get("n_numeric", 0) >= max(1, len(raw) * 0.7)
                    else ("mixed" if stats.get("n_numeric", 0) > 0 else "text"),
        }
        col.update(stats)
        m = UNIT_HINT_RE.search(col_names[ci])
        col["unit_hint"] = m.group(1) if m else None
        # 饱和/边界：最小最大被频繁命中
        if stats.get("n_numeric", 0) >= 5 and stats.get("min") != stats.get("max"):
            mn, mx = stats["min"], stats["max"]
            frac_min = sum(1 for x in numeric if x == mn) / len(numeric)
            frac_max = sum(1 for x in numeric if x == mx) / len(numeric)
            col["saturation"] = {
                "min_fraction": round(frac_min, 4),
                "max_fraction": round(frac_max, 4),
                "flag": frac_min >= 0.1 or frac_max >= 0.1,
            }
        result["columns"].append(col)
        if col["type"] == "numeric":
            numeric_col_count += 1
            col_vectors.append((ci, numeric, raw))
        else:
            col_vectors.append((ci, None, raw))

    # 逐格离群扫描（宽表/矩阵形态下把异常定位到 行×列）
    wide = numeric_col_count >= 2 and len(data_rows) >= 3
    result["wide_numeric"] = wide
    result["row_outliers"] = []
    cell_budget = len(data_rows) * n_cols
    if wide and cell_budget <= MAX_CELLS:
        for ci, numeric, raw in col_vectors:
            if numeric is None:
                continue
            cells = [(ri, fv) for ri, fv in enumerate(numeric, start=1) if fv is not None]
            for method in ("z", "iqr", "mad"):
                for r, v, score, mname, thr in _outlier_cells(cells, ci, method):
                    result["row_outliers"].append({
                        "locator": "%s#R%d.C%d" % (rel, r, ci + 1),
                        "row": r, "column": ci + 1, "column_name": col_names[ci],
                        "value": v, "method": mname, "score": score, "threshold": thr,
                    })
    elif wide:
        result["degradations"] = result.get("degradations", []) + [{
            "file": rel,
            "problem": "行×列=%d 超限，跳过逐格离群扫描，仅保留逐列统计" % cell_budget,
        }]
    # 去重并限量
    seen, dedup = set(), []
    for o in sorted(result["row_outliers"], key=lambda x: -abs(x["score"])):
        key = (o["locator"], o["method"])
        if key not in seen:
            seen.add(key)
            dedup.append(o)
    result["row_outliers"] = dedup[:100]
    return result


def _profile_json(path, rel):
    obj = load_json_safe(path)
    if obj is None:
        return {"file": rel, "kind": "json", "error": "json 解析失败",
                "degradations": [{"file": rel, "problem": "json 解析失败"}]}
    top = list(obj.keys()) if isinstance(obj, dict) else []
    return {"file": rel, "kind": "json", "top_level_keys": top[:80],
            "is_list": isinstance(obj, list)}


def profile_file(path, rel):
    text, enc = read_text(path)
    if text is None:
        return {"file": rel, "kind": "binary_or_unreadable", "error": enc,
                "degradations": [{"file": rel, "problem": enc}]}
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        return profile_table(path, rel)
    if ext == ".json":
        return _profile_json(path, rel)
    return {
        "file": rel, "kind": "text", "encoding": enc,
        "size_bytes": os.path.getsize(path),
        "n_lines": text.count("\n") + (0 if text.endswith("\n") else 1),
    }


def profile_input(input_dir, work_dir, output_dir):
    files = discover_files(input_dir)
    if os.path.isdir(work_dir):
        wfiles = discover_files(work_dir)
        seen = {f["path"] for f in files}
        for f in wfiles:
            if f["path"] not in seen:
                files.append(f)
    files.sort(key=lambda x: x["rel"])

    entries, degradations, candidates = [], [], []
    for f in files:
        entry = {
            "path": f["path"], "rel": f["rel"], "size_bytes": f["size_bytes"],
            "ext": f["ext"], "sha256": sha256_head(f["path"]),
        }
        if f["size_bytes"] > MAX_FILE_BYTES:
            entry["skipped"] = "file exceeds %d bytes" % MAX_FILE_BYTES
            degradations.append({"file": f["rel"], "problem": entry["skipped"]})
            entries.append(entry)
            continue
        prof = profile_file(f["path"], f["rel"])
        entry.update(prof)
        for d in prof.pop("degradations", []):
            degradations.append(d)
        entries.append(entry)
        for ro in prof.get("row_outliers", []):
            candidates.append(ro)
    candidates.sort(key=lambda x: -abs(x["score"]))
    facts = {
        "generated_at": now_ts(),
        "input_dir": input_dir, "work_dir": work_dir,
        "totals": {"n_files": len(entries)},
        "files": entries,
        "candidate_anomalies": candidates[:200],
        "degradations": degradations,
    }
    write_json_atomic(os.path.join(output_dir, "facts.json"), facts)
    return facts


# --------------------------------------------------------------------------- #
# 报告与证据映射                                                              #
# --------------------------------------------------------------------------- #
def _fmt_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def _render_interpretation(obj, key, title, out):
    out.append("## %s" % title)
    items = (obj.get("interpretation") or {}).get(key) or []
    if not items:
        out.append("_（空）_\n")
        return
    for i, it in enumerate(items, 1):
        if isinstance(it, dict):
            head = it.get("claim") or it.get("title") or it.get("summary") or ("#%d" % i)
            out.append("### %s" % head)
            for field in ("observation", "direction", "subject", "method",
                          "threshold", "value", "expected", "deviation",
                          "supporting_evidence", "excluding_evidence",
                          "confound_risk", "testability", "target",
                          "expected_if_supported", "decision_rule",
                          "reason", "expected_outcome"):
                if field in it:
                    out.append("- **%s**: %s" % (field, _fmt_value(it[field])))
            ev = it.get("evidence") or []
            if ev:
                out.append("- **evidence**:")
                for e in ev:
                    if isinstance(e, dict):
                        out.append("  - `%s` → “%s”" % (e.get("locator", ""),
                                                          e.get("quote", "")))
        else:
            out.append("- %s" % _fmt_value(it))
    out.append("")


def build_report(result_path, output_dir):
    obj = load_json_safe(result_path)
    if obj is None:
        return None
    out = ["# 实验结果解释报告", "",
           "- case_id: `%s`" % (obj.get("case_id") or ""),
           "- status: `%s`" % (obj.get("status") or ""),
           "- task_type: `%s`" % (obj.get("task_type") or ""), "",
           "## 摘要", "", (obj.get("summary") or "_（空）_"), ""]
    out.append("## 解释结论")
    for key, title in (("trends", "结果趋势"), ("anomalies", "异常点"),
                       ("possible_causes", "可能原因"),
                       ("verification_steps", "复核步骤"),
                       ("followup_experiments", "后续实验建议")):
        _render_interpretation(obj, key, title, out)
    out.append("## 实验方案（依据与风险）")
    plan = obj.get("experiment_plan") or {}
    for k, label in (("hypothesis", "假设"), ("testable_question", "可检验问题"),
                     ("variables", "变量"), ("controls", "对照"),
                     ("steps", "步骤"), ("parameters", "参数"),
                     ("resources", "资源"), ("interpretation_criteria", "判读标准")):
        out.append("- **%s**: %s" % (label, _fmt_value(plan.get(k, []))))
    out.append("")
    out.append("## 确认项")
    conf = obj.get("confirmation_items") or {}
    for k, label in (("safety", "安全"), ("ethics", "伦理"),
                     ("equipment_availability", "设备可用性"),
                     ("expert_confirmation", "专家确认")):
        out.append("- **%s**: %s" % (label, _fmt_value(conf.get(k, []))))
    out.append("")
    out.append("## 不确定性与限制")
    out.append("- uncertainty: %s" % _fmt_value(obj.get("uncertainty", [])))
    out.append("- limitations: %s" % _fmt_value(obj.get("limitations", [])))
    out.append("- pending_human: %s" % _fmt_value(obj.get("pending_human", [])))
    report_md = "\n".join(out) + "\n"
    write_text_atomic(os.path.join(output_dir, "report.md"), report_md)

    # evidence_map.json：把结论证据扁平化，便于逐条复核
    evmap = []
    for key in INTERPRETATION_KEYS:
        for item in (obj.get("interpretation") or {}).get(key) or []:
            if not isinstance(item, dict):
                continue
            claim = item.get("claim") or item.get("title") or item.get("summary")
            for e in item.get("evidence") or []:
                if isinstance(e, dict):
                    evmap.append({
                        "section": key, "claim": claim,
                        "locator": e.get("locator"), "quote": e.get("quote"),
                    })
    write_json_atomic(os.path.join(output_dir, "evidence_map.json"), evmap)
    return report_md


# --------------------------------------------------------------------------- #
# 校验（顺序：schema/契约 → 证据完整性 → 确定性）                            #
# --------------------------------------------------------------------------- #
def _evidence_problems(item, section):
    if not isinstance(item, dict):
        return ["%s: 结论项应为带 evidence 的对象，收到 %s" % (section, type(item).__name__)]
    problems = []
    ev = item.get("evidence")
    if not isinstance(ev, list) or not ev:
        problems.append("%s: 缺少 evidence 数组" % section)
        return problems
    for i, e in enumerate(ev):
        if not isinstance(e, dict):
            problems.append("%s: evidence[%d] 非对象" % (section, i))
            continue
        if not (e.get("locator") or "").strip():
            problems.append("%s: evidence[%d] 缺 locator" % (section, i))
        if not (e.get("quote") or "").strip():
            problems.append("%s: evidence[%d] 缺逐字 quote" % (section, i))
    return problems


def validate_result(path):
    obj = load_json_safe(path)
    if obj is None:
        print("[validate] FATAL: 无法解析 %s（不是合法 JSON）" % path)
        return 1
    problems = []
    if not (obj.get("case_id") or "").strip():
        problems.append("case_id 为空")
    if obj.get("status") not in STATUSES:
        problems.append("status=%r 非法（应为 %s）" % (obj.get("status"), "|".join(STATUSES)))
    for k in TOP_KEYS:
        if k not in obj:
            problems.append("缺少顶层键 %s" % k)
    interp = obj.get("interpretation")
    if not isinstance(interp, dict):
        problems.append("interpretation 应为对象")
    else:
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                problems.append("interpretation 缺少键 %s" % k)
            else:
                for item in (interp.get(k) or []):
                    problems.extend(_evidence_problems(item, "interpretation.%s" % k))
    # causes 额外要求支持/排除证据；verification 要求可执行字段
    for item in (interp or {}).get("possible_causes") or []:
        if isinstance(item, dict) and not item.get("supporting_evidence"):
            problems.append("possible_causes: 条目缺 supporting_evidence")
        if isinstance(item, dict) and not item.get("excluding_evidence"):
            problems.append("possible_causes: 条目缺 excluding_evidence（无排除过程应写 [] 并说明）")
    for item in (interp or {}).get("verification_steps") or []:
        if isinstance(item, dict):
            for f in ("target", "expected_if_supported", "decision_rule"):
                if not item.get(f):
                    problems.append("verification_steps: 条目缺 %s" % f)
    for item in (interp or {}).get("followup_experiments") or []:
        if isinstance(item, dict):
            for f in ("reason", "expected_outcome"):
                if not item.get(f):
                    problems.append("followup_experiments: 条目缺 %s" % f)

    if problems:
        print("[validate] 发现 %d 个问题：" % len(problems))
        for p in problems:
            print("  - " + p)
    else:
        print("[validate] ok: schema/契约与证据完整性通过")
    return 0


# --------------------------------------------------------------------------- #
# 定稿：写入 runtime 与 finalize 变更记录                                     #
# --------------------------------------------------------------------------- #
def finalize_result(path):
    obj = load_json_safe(path)
    if obj is None:
        print("[finalize] FATAL: 无法解析 %s" % path)
        return 1
    runtime = obj.setdefault("runtime", {})
    started = runtime.get("started_at")
    runtime["finished_at"] = now_ts()
    elapsed = None
    if isinstance(started, str):
        try:
            t0 = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
            t1 = time.mktime(time.strptime(runtime["finished_at"], "%Y-%m-%dT%H:%M:%SZ"))
            elapsed = max(0, int(round(t1 - t0)))
        except (ValueError, OverflowError):
            elapsed = None
    runtime["elapsed_seconds"] = elapsed
    obj.setdefault("change_log", []).append({
        "stage": "finalize", "at": now_ts(),
        "action": "定稿：写入 runtime 与变更记录",
        "detail": "elapsed_seconds=%s" % elapsed,
    })
    write_json_atomic(path, obj)
    print("[finalize] ok: runtime.finished_at=%s elapsed=%ss"
          % (runtime["finished_at"], elapsed))
    return 0
