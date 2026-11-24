#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验解释 Skill 共享实现：IO / 发现 / 统计 / 契约 / 中间表示。

仅使用 Python 标准库。本模块被 main.py 与 selftest.py 复用，不直接执行。
运行时路径约定（可被 --input/--output/--work 覆盖）：
  input   /input
  output  /app/output
  work    /app/work
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
SCHEMA_VERSION = "1.0"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# result.json 顶层字段（不含 case.json output_contract 动态追加项）
TOP_KEYS = (
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "intermediate_artifacts",
    "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
)
INTERPRETATION_KEYS = (
    "trends", "anomalies", "possible_causes",
    "verification_steps", "followup_experiments",
)
PLAN_KEYS = (
    "hypothesis", "testable_question", "variables", "controls",
    "steps", "parameters", "resources", "interpretation_criteria",
)
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")
EVIDENCE_KEYS = ("evidence", "supporting_evidence", "excluding_evidence")

# 中间表示工件（本 Skill 的核心设计：结论由 model 渲染，逐条可回指）
INTERMEDIATE_FILES = {
    "plan": "plan.json",
    "profile": "data_profile.json",
    "model": "model.json",
    "figure_model": "figure_model.json",
    "evidence_map": "evidence_map.json",
}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """UTF-8(含 BOM)优先，坏编码降级 latin-1；超限/不可读返回 (None, reason)。"""
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
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_%s_%s" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_%s_%s" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


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
    """发现输入目录全部文件，返回相对路径排序后的清单。"""
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
            out.append({
                "path": full,
                "rel": rel,
                "size_bytes": size,
                "ext": os.path.splitext(fn)[1].lower(),
            })
    out.sort(key=lambda x: x["rel"])
    return out


def resolve_case_id(input_dir):
    obj = load_json_safe(os.path.join(input_dir, "case.json"))
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "case"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        content = obj.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge[-_ ]?id[:\s]*`?([A-Za-z0-9_.-]+)`?", content, re.I)
            if m:
                return m.group(1)
    text, _ = read_text(os.path.join(input_dir, "task.md"))
    if text:
        m = re.search(r"challenge[-_ ]?id[:\s]*`?([A-Za-z0-9_.-]+)`?", text, re.I)
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


def merge_output_contract(result, case_obj):
    """若 case.json 含 output_contract，原样回填其全部键（缺值给 null/[]），不改名不删未知键。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    if contract is None:
        return []
    names = []
    if isinstance(contract, dict):
        for key in ("fields", "required", "schema"):
            v = contract.get(key)
            if isinstance(v, list):
                names.extend(x for x in v if isinstance(x, str))
        if not names:
            names = [k for k in contract
                     if isinstance(k, str) and k not in
                     ("fields", "required", "schema", "description", "title")]
    elif isinstance(contract, list):
        names = [x for x in contract if isinstance(x, str)]
    merged = []
    for k in names:
        if k not in result:
            default = None
            if isinstance(contract, dict) and k in contract:
                default = _default_for(contract[k])
            result[k] = default
            merged.append(k)
    return merged


# ---------------------------------------------------------------------------
# 数值统计
# ---------------------------------------------------------------------------

def _numbers(values):
    out = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def describe(values):
    """返回数值列的描述统计；空列返回 None。"""
    nums = _numbers(values)
    if not nums:
        return None
    nums.sort()
    n = len(nums)
    mean = statistics.fmean(nums)
    median = statistics.median(nums)
    stdev = statistics.pstdev(nums) if n > 1 else 0.0
    q1 = _percentile(nums, 25)
    q3 = _percentile(nums, 75)
    iqr = q3 - q1
    mad = statistics.median([abs(x - median) for x in nums]) if n else 0.0
    return {
        "n": n, "mean": round(mean, 6), "median": round(median, 6),
        "stdev": round(stdev, 6), "min": round(nums[0], 6),
        "max": round(nums[-1], 6), "q1": round(q1, 6), "q3": round(q3, 6),
        "iqr": round(iqr, 6), "mad": round(mad, 6),
    }


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def linreg(xs, ys):
    """最小二乘线性拟合，返回 (slope, intercept, r)。空/单点返回 (None, None, None)。"""
    if len(xs) != len(ys) or len(xs) < 2:
        return None, None, None
    xm = statistics.fmean(xs)
    ym = statistics.fmean(ys)
    sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    sxx = sum((x - xm) ** 2 for x in xs)
    syy = sum((y - ym) ** 2 for y in ys)
    if sxx == 0:
        return None, None, None
    slope = sxy / sxx
    intercept = ym - slope * xm
    r = 0.0
    if syy > 0:
        r = sxy / math.sqrt(sxx * syy)
    return slope, intercept, r


def rolling_mean(values, window):
    """滚动均值；窗口对齐到左侧索引。"""
    vals = _numbers(values)
    if window <= 0 or len(vals) < window:
        return []
    out = []
    for i in range(len(vals) - window + 1):
        out.append(statistics.fmean(vals[i:i + window]))
    return out


def zscore_outliers(values, threshold=3.0):
    """z-score 离群：返回 (index, value, z) 列表，|z|>threshold。"""
    nums = _numbers(values)
    if len(nums) < 3:
        return []
    mean = statistics.fmean(nums)
    sd = statistics.pstdev(nums)
    if sd == 0:
        return []
    out = []
    for i, v in enumerate(nums):
        z = (v - mean) / sd
        if abs(z) > threshold:
            out.append({"index": i, "value": round(v, 6), "z": round(z, 4)})
    return out


def iqr_outliers(values, k=1.5):
    """IQR 离群：返回 (index, value, deviation) 列表。"""
    nums = _numbers(values)
    if len(nums) < 4:
        return []
    s = sorted(nums)
    q1 = _percentile(s, 25)
    q3 = _percentile(s, 75)
    iqr = q3 - q1
    if iqr == 0:
        return []
    lo, hi = q1 - k * iqr, q3 + k * iqr
    out = []
    for i, v in enumerate(nums):
        if v < lo:
            out.append({"index": i, "value": round(v, 6),
                        "deviation": round(v - lo, 6)})
        elif v > hi:
            out.append({"index": i, "value": round(v, 6),
                        "deviation": round(v - hi, 6)})
    return out


def mad_outliers(values, k=3.5):
    """MAD 离群（稳健）：|x-median| > k*1.4826*MAD。"""
    nums = _numbers(values)
    if len(nums) < 3:
        return []
    med = statistics.median(nums)
    mad = statistics.median([abs(x - med) for x in nums])
    if mad == 0:
        return []
    scale = k * 1.4826 * mad
    out = []
    for i, v in enumerate(nums):
        if abs(v - med) > scale:
            out.append({"index": i, "value": round(v, 6),
                        "deviation": round(v - med, 6)})
    return out


def trend_direction(values, method="linear"):
    """根据线性斜率给出方向判断；返回 dict。"""
    nums = _numbers(values)
    if len(nums) < 2:
        return {"direction": "insufficient", "method": method, "slope": None}
    xs = [float(i) for i in range(len(nums))]
    slope, _, r = linreg(xs, nums)
    if slope is None:
        return {"direction": "insufficient", "method": method, "slope": None}
    direction = "stable"
    if slope > 1e-9:
        direction = "increasing"
    elif slope < -1e-9:
        direction = "decreasing"
    return {
        "direction": direction, "method": method,
        "slope": round(slope, 8), "r": round(r, 6),
    }


# ---------------------------------------------------------------------------
# 表格解析（CSV/TSV，标准库；XLSX/MAT 由 Agent 用平台工具导出 CSV 后再解析）
# ---------------------------------------------------------------------------

def sniff_table(text, hint_ext=None):
    """尝试用 csv.Sniffer 判定分隔符；失败则按扩展名猜。返回 (delimiter, has_header)。"""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = None
    if delim is None:
        ext = (hint_ext or "").lower()
        delim = "\t" if ext in (".tsv", ".txt") else ","
    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = True
    return delim, has_header


def parse_table(path):
    """解析 CSV/TSV 为 {columns, rows, delimiter, has_header, encoding, error}。

    每个单元格尝试转 float；不可转则保留原字符串。空文件/坏文件返回 error。
    """
    text, enc = read_text(path)
    if text is None:
        return {"columns": [], "rows": [], "delimiter": None,
                "has_header": False, "encoding": None,
                "error": "unreadable file"}
    delim, has_header = sniff_table(text, os.path.splitext(path)[1])
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        raw_rows = list(reader)
    except csv.Error as exc:
        return {"columns": [], "rows": [], "delimiter": delim,
                "has_header": has_header, "encoding": enc,
                "error": "csv parse error: %s" % exc}
    if not raw_rows:
        return {"columns": [], "rows": [], "delimiter": delim,
                "has_header": has_header, "encoding": enc,
                "error": "empty table"}
    ncols = max(len(r) for r in raw_rows)
    rows = [r + [""] * (ncols - len(r)) for r in raw_rows]
    if has_header:
        header = [str(c).strip() or ("col%d" % i) for i, c in enumerate(rows[0])]
        body = rows[1:]
    else:
        header = ["col%d" % i for i in range(ncols)]
        body = rows
    parsed = []
    for r in body:
        out = []
        for c in r:
            out.append(_coerce(c))
        parsed.append(out)
    return {"columns": header, "rows": parsed, "delimiter": delim,
            "has_header": has_header, "encoding": enc, "error": None}


def _coerce(cell):
    s = str(cell).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return s


def column_values(rows, col_index):
    return [r[col_index] if col_index < len(r) else None for r in rows]


def profile_table(path):
    """对单个表格生成画像：每列类型 + 数值列统计 + 离群候选。"""
    parsed = parse_table(path)
    if parsed["error"]:
        return {"path": path, "error": parsed["error"]}
    cols = parsed["columns"]
    profiles = []
    for i, name in enumerate(cols):
        vals = column_values(parsed["rows"], i)
        nums = _numbers(vals)
        entry = {
            "column": name, "index": i, "n": len(vals),
            "numeric_n": len(nums), "numeric_ratio": round(len(nums) / max(len(vals), 1), 3),
            "stats": describe(vals) if len(nums) >= 1 else None,
            "zscore_outliers": zscore_outliers(vals)[:20],
            "iqr_outliers": iqr_outliers(vals)[:20],
            "mad_outliers": mad_outliers(vals)[:20],
        }
        profiles.append(entry)
    return {
        "path": path, "error": None,
        "delimiter": parsed["delimiter"], "has_header": parsed["has_header"],
        "encoding": parsed["encoding"],
        "n_rows": len(parsed["rows"]), "n_columns": len(cols),
        "columns": profiles,
    }


# ---------------------------------------------------------------------------
# 证据与 locator
# ---------------------------------------------------------------------------

def normalize_locator(locator):
    """把 locator 规范化：相对路径 + 可选 #p/行号/L 片段。"""
    if not isinstance(locator, str):
        return ""
    return locator.replace("\\", "/").strip()


def has_evidence(obj):
    """检查一个结论对象是否带有效 evidence 数组（locator+quote 均非空）。"""
    if not isinstance(obj, dict):
        return False
    ev = obj.get("evidence")
    if not isinstance(ev, list) or not ev:
        return False
    for item in ev:
        if not isinstance(item, dict):
            return False
        if not (item.get("locator") and item.get("quote")):
            return False
    return True


def is_nonempty_interpretation(obj):
    """五段解释中一段是否非空且带证据。"""
    if not isinstance(obj, list):
        return False
    if not obj:
        return False
    return any(has_evidence(x) for x in obj if isinstance(x, dict))


def _has_evidence_list(ev):
    """evidence/supporting_evidence 列表中是否至少一条 locator+quote 均非空。"""
    if not isinstance(ev, list) or not ev:
        return False
    for item in ev:
        if isinstance(item, dict) and item.get("locator") and item.get("quote"):
            return True
    return False


def section_satisfied(section, items):
    """按五段各自的证据/结构要求判断该段是否满足。

    trends/anomalies：需 evidence 非空；
    possible_causes：需 supporting_evidence 非空且 excluding_evidence 为列表；
    verification_steps：需 target 或 action 非空（可执行动作）；
    followup_experiments：需 change 或 reason 非空。
    """
    if not isinstance(items, list) or not items:
        return False
    if section in ("trends", "anomalies"):
        return any(has_evidence(x) for x in items if isinstance(x, dict))
    if section == "possible_causes":
        return any(
            isinstance(x, dict)
            and _has_evidence_list(x.get("supporting_evidence"))
            and isinstance(x.get("excluding_evidence"), list)
            for x in items)
    if section == "verification_steps":
        return any(
            isinstance(x, dict) and (x.get("target") or x.get("action"))
            for x in items)
    if section == "followup_experiments":
        return any(
            isinstance(x, dict) and (x.get("change") or x.get("reason"))
            for x in items)
    return False


# ---------------------------------------------------------------------------
# 骨架与中间表示模板
# ---------------------------------------------------------------------------

def build_skeleton(case_id):
    """构造带全部顶层字段的 result.json 骨架（status=partial）。"""
    return {
        "case_id": case_id,
        "status": "partial",
        "task_type": "experiment_result_interpretation",
        "summary": "",
        "interpretation": {
            "trends": [],
            "anomalies": [],
            "possible_causes": [],
            "verification_steps": [],
            "followup_experiments": [],
        },
        "experiment_plan": {
            "hypothesis": None,
            "testable_question": None,
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
        "intermediate_artifacts": {
            "plan": None,
            "profile": None,
            "model": None,
            "figure_model": None,
            "evidence_map": None,
        },
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {
                "ts": now_ts(), "phase": "skeleton",
                "action": "partial skeleton written",
            }
        ],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_at": now_ts(), "finished_at": None, "seconds": None},
    }


def build_model_template(case_id, profile):
    """构造 model.json 模板：预填 profile 的数值事实，留空解释槽。"""
    series_models = []
    anomaly_candidates = []
    for pf in profile.get("tables", []):
        for col in pf.get("columns", []):
            stats = col.get("stats")
            if not stats:
                continue
            series_models.append({
                "id": "S_%s_col%d" % (_safe_id(pf["path"]), col["index"]),
                "source": {"locator": "%s#col:%s" % (pf["path"], col["column"])},
                "n": stats["n"], "units": None,
                "stats": stats,
                "trend": None,  # 由 Agent 依据具体列/对照写方向、区间与拟合方式
                "inflection_points": [],
            })
            for cand in col.get("mad_outliers", []):
                anomaly_candidates.append({
                    "id": "A_cand_%s_col%d_i%d" % (_safe_id(pf["path"]), col["index"], cand["index"]),
                    "subject": {"dimension": "row", "locator": "%s#row:%d" % (pf["path"], cand["index"] + 1)},
                    "observed": cand["value"],
                    "expected": None,
                    "deviation": None,
                    "method": "mad", "threshold": 3.5,
                    "relation_to_controls": None,
                    "auto_flag": True,
                })
    return {
        "case_id": case_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": now_ts(),
        "series_models": series_models,
        "anomaly_model": anomaly_candidates,
        "cause_model": [],
        "verification_model": [],
        "followup_model": [],
        "experiment_plan_model": {},
        "uncertainty_model": [],
    }


def _safe_id(path):
    return re.sub(r"[^A-Za-z0-9_]+", "_", path).strip("_") or "table"


def build_figure_template(model):
    """从 model 的 series_models 生成 figure_model.json 声明式骨架。"""
    figures = []
    for s in model.get("series_models", []):
        figures.append({
            "id": "fig_" + s["id"],
            "title": None,
            "series": [{"source": s["source"], "style": "line"}],
            "axes": {"x": None, "y": None},
            "annotations": [],
            "generated": False,
        })
    return {"schema_version": SCHEMA_VERSION, "figures": figures}
