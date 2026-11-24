#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验结果解释与异常分析 Skill 核心实现（仅标准库）。

包含：
  - 输入目录清点与文本/CSV/TSV 读取（坏编码降级、超限跳过）
  - 数值列统计（n/缺失/均值/中位数/标准差/分位数/IQR/MAD）
  - 离群筛查（z-score、IQR Tukey、MAD modified-z、Grubbs 单离群近似）
  - 曲线/谱图特征（极值、拐点、单调段、平台段）
  - report.md 与 evidence_map.json 生成
  - result.json 契约校验

设计原则：确定性（固定排序、无随机数）、可复算（每条数值结论附带方法/阈值/样本量配方）。
"""

import csv
import io
import json
import math
import os
import statistics

from main import (
    MAX_FILE_BYTES, STATUSES, TOP_KEYS, INTERPRETATION_KEYS,
    PLAN_KEYS, RISK_KEYS, CONFIRM_KEYS,
    discover_files, load_json_safe, read_text, sha256_head,
)

NUMERIC_EXT = {".csv", ".tsv", ".txt", ".tab", ".dat"}
TABLE_EXT = NUMERIC_EXT | {".json", ".jsonl"}
DEFAULT_Z = 3.0          # z-score 阈值
DEFAULT_IQR_K = 1.5      # Tukey 箱线图系数
DEFAULT_MAD_K = 3.5      # modified z 阈值
GRUBBS_ALPHA = 0.05
MAX_SERIES = 300         # 宽表逐列曲线特征上限，避免产物爆炸
MAX_ROWS_FOR_GRUBBS = 20000


# ---------- 基础解析 ----------

def _is_numeric(v):
    if not isinstance(v, str):
        v = str(v)
    v = v.strip()
    if v in ("", "nan", "NaN", "NA", "N/A", "None", "null", "-", "--"):
        return False
    try:
        float(v)
        return True
    except (ValueError, OverflowError):
        return False


def _to_float(v):
    if not isinstance(v, str):
        v = str(v)
    v = v.strip()
    if v in ("", "nan", "NaN", "NA", "N/A", "None", "null", "-", "--"):
        return None
    try:
        f = float(v)
    except (ValueError, OverflowError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def parse_delimited(path):
    """CSV/TSV/空白分隔表解析；自动嗅探分隔符，返回 (headers, rows) 或 None。"""
    text, enc = read_text(path)
    if text is None:
        return None
    sample = text[:8192]
    if not sample.strip():
        return None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;| ")
    except csv.Error:
        dialect = csv.excel
    if getattr(dialect, "delimiter", ",") == " ":
        # 空白分隔常与 csv 嗅探冲突，退化为通用空白切分
        dialect = None
    rows = []
    reader = csv.reader(io.StringIO(text), dialect) if dialect else None
    if reader is not None:
        for row in reader:
            rows.append([c.strip() for c in row])
    else:
        for line in text.splitlines():
            if line.strip():
                rows.append(line.split())
    if not rows:
        return None
    # 首行若全为数值则视为无表头
    first_numeric = all(_is_numeric(c) for c in rows[0] if c != "")
    if first_numeric and len(rows[0]) > 1:
        headers = ["col_%d" % (i + 1) for i in range(len(rows[0]))]
        data = rows
    else:
        headers = rows[0]
        data = rows[1:]
    return headers, data


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = (n - 1) * p
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


# ---------- 离群筛查 ----------

def _norm_inv(p):
    """标准正态分布分位数（Acklam 有理逼近，精度约 1e-9）。"""
    if p <= 0.0:
        return -10.0
    if p >= 1.0:
        return 10.0
    # 下尾分位数
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    return x


def _student_t_inv_two_tail(p, df):
    """Student t 双尾临界值近似（Fisher 展开，df>=4 可用）。"""
    if df < 1:
        return None
    z = _norm_inv(1.0 - p / 2.0)
    if df >= 4:
        z2 = z * z
        t = z * (1.0 + (z2 + 1.0) / (4.0 * df)
                 + (5.0 * z2 * z2 + 16.0 * z2 + 3.0) / (96.0 * df * df)
                 + (3.0 * z2 * z2 * z2 + 19.0 * z2 * z2 + 17.0 * z2 - 15.0) / (384.0 * df * df * df))
        return t
    return z


def column_stats(values):
    """values: 数值 list。返回确定性统计与离群标记。"""
    n = len(values)
    out = {
        "n": n, "mean": None, "median": None, "std": None, "min": None, "max": None,
        "q1": None, "q3": None, "iqr": None, "mad": None,
        "z_outliers": [], "iqr_outliers": [], "mad_outliers": [], "grubbs": None,
    }
    if n == 0:
        return out
    mean = statistics.fmean(values)
    median = statistics.median(values)
    q1 = _percentile(sorted(values), 0.25)
    q3 = _percentile(sorted(values), 0.75)
    iqr = (q3 - q1) if q3 is not None and q1 is not None else None
    std = statistics.pstdev(values) if n > 1 else 0.0
    devs = [abs(v - median) for v in values]
    mad = statistics.median(devs) if devs else 0.0
    out.update({"mean": mean, "median": median, "std": std,
                "min": min(values), "max": max(values),
                "q1": q1, "q3": q3, "iqr": iqr, "mad": mad})

    if std and std > 0:
        out["z_outliers"] = [
            {"index": i + 1, "value": v, "score": round((v - mean) / std, 4)}
            for i, v in enumerate(values) if abs((v - mean) / std) > DEFAULT_Z
        ]
    if iqr and iqr > 0:
        lo = q1 - DEFAULT_IQR_K * iqr
        hi = q3 + DEFAULT_IQR_K * iqr
        out["iqr_outliers"] = [
            {"index": i + 1, "value": v, "bounds": [round(lo, 6), round(hi, 6)]}
            for i, v in enumerate(values) if v < lo or v > hi
        ]
    if mad and mad > 0:
        out["mad_outliers"] = [
            {"index": i + 1, "value": v, "modified_z": round(0.6745 * (v - median) / mad, 4)}
            for i, v in enumerate(values) if abs(0.6745 * (v - median) / mad) > DEFAULT_MAD_K
        ]
    if 3 <= n <= MAX_ROWS_FOR_GRUBBS:
        g = _grubbs(values, n)
        if g is not None:
            out["grubbs"] = g
    return out


def _grubbs(values, n):
    """单离群 Grubbs 筛查（双侧 alpha=0.05，t 临界值近似）。"""
    mean = statistics.fmean(values)
    std = statistics.pstdev(values)
    if std <= 0:
        return None
    gs = [abs(v - mean) / std for v in values]
    gmax = max(gs)
    idx = gs.index(gmax) + 1
    p = GRUBBS_ALPHA / (2.0 * n)          # 双侧 per-comparison
    t = _student_t_inv_two_tail(p * 2.0, max(n - 2, 1))
    if t is None:
        return None
    gcrit = (n - 1.0) / math.sqrt(n) * math.sqrt((t * t) / (n - 2.0 + t * t))
    return {
        "method": "grubbs_single_two_sided",
        "alpha": GRUBBS_ALPHA, "n": n,
        "g_max": round(gmax, 4), "g_crit": round(gcrit, 4),
        "index": idx, "value": values[idx - 1],
        "flagged": gmax > gcrit,
        "note": "t 临界值为 Fisher 展开近似；N 小或接近临界值时请用精确表复核。",
    }


# ---------- 曲线特征 ----------

def curve_features(y_vals, x_vals=None):
    """给定一条数值序列，返回确定性曲线特征（极值/拐点/单调段/平台段）。"""
    ys = [v for v in y_vals if v is not None]
    n = len(ys)
    if n == 0:
        return {"n": 0}
    xs = [v for v in (x_vals or []) if v is not None]
    if len(xs) != len(y_vals):
        xs = list(range(1, len(y_vals) + 1))
    diffs = [ys[i + 1] - ys[i] for i in range(n - 1)]
    ups = sum(1 for d in diffs if d > 0)
    downs = sum(1 for d in diffs if d < 0)
    flats = sum(1 for d in diffs if d == 0)
    sign_changes = 0
    prev_sign = 0
    for d in diffs:
        s = (d > 0) - (d < 0)
        if s != 0:
            if prev_sign != 0 and s != prev_sign:
                sign_changes += 1
            prev_sign = s
    max_idx = ys.index(max(ys))
    min_idx = ys.index(min(ys))
    tol = max((max(ys) - min(ys)) * 1e-6, 1e-12) if max(ys) != min(ys) else 1e-12
    plateau_runs = []
    run_start = 0
    for i in range(1, n):
        if abs(ys[i] - ys[i - 1]) > tol:
            if i - run_start >= 3:
                plateau_runs.append({"start": run_start + 1, "end": i, "length": i - run_start})
            run_start = i
    if n - run_start >= 3:
        plateau_runs.append({"start": run_start + 1, "end": n, "length": n - run_start})
    return {
        "n": n,
        "y_min": min(ys), "y_max": max(ys), "y_range": max(ys) - min(ys),
        "x_at_y_max": xs[max_idx] if max_idx < len(xs) else None,
        "x_at_y_min": xs[min_idx] if min_idx < len(xs) else None,
        "n_up_segments": ups, "n_down_segments": downs, "n_flat": flats,
        "n_sign_changes": sign_changes,
        "first_diff": {"mean": statistics.fmean(diffs) if diffs else None,
                       "max": max(diffs) if diffs else None,
                       "min": min(diffs) if diffs else None},
        "plateaus": plateau_runs,
        "monotonic": "increasing" if ups and not downs else
                     ("decreasing" if downs and not ups else
                      ("flat" if flats and not ups and not downs else "nonmonotonic")),
    }


# ---------- 表形态判定与画像 ----------

def _role_hint(rel):
    low = rel.lower()
    if any(k in low for k in ("bend", "trace", "curve", "force", "displacement", "stress", "strain")):
        return "trace"
    if any(k in low for k in ("iris", "cv", "fold", "split", "accuracy", "f1", "confusion")):
        return "tabular"
    if any(k in low for k in ("sheet", "workbook", "cefixime", "assay", "dissolution")):
        return "workbook_paper"
    if low.endswith(".pdf"):
        return "paper"
    return "generic"


def _detect_data_form(files, tables):
    hints = {_role_hint(f["rel"]) for f in files}
    if "paper" in hints and any(h in hints for h in ("workbook_paper", "tabular", "trace")):
        return "workbook_paper"
    for h in ("trace", "workbook_paper", "tabular"):
        if h in hints:
            return h
    if tables:
        return "tabular"
    return "generic"


def _profile_table(file_info, headers, rows):
    n_rows = len(rows)
    n_cols = len(headers)
    # 补齐不等长行
    width = n_cols
    norm_rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    col_values = [[r[i] for r in norm_rows] for i in range(width)]
    numeric_flags = []
    for i, cells in enumerate(col_values):
        nonempty = [c for c in cells if c != ""]
        num_cnt = sum(1 for c in nonempty if _is_numeric(c))
        numeric_flags.append(num_cnt >= max(1, int(0.8 * len(nonempty))))
    numeric_cols = [i for i, f in enumerate(numeric_flags) if f]
    columns = []
    for i, cells in enumerate(col_values):
        col = {
            "name": headers[i] if i < len(headers) else "col_%d" % (i + 1),
            "index": i + 1, "numeric": numeric_flags[i],
            "missing": sum(1 for c in cells if c == ""),
            "n_unique": len({c for c in cells}),
        }
        if numeric_flags[i]:
            vals = [_to_float(c) for c in cells]
            col["stats"] = column_stats([v for v in vals if v is not None])
        columns.append(col)

    series = []
    n_num = len(numeric_cols)
    num_frac = n_num / max(1, n_cols)
    is_two_col = n_cols == 2 and n_num == 2 and n_rows >= 8
    is_wide = n_num >= 6 and num_frac >= 0.9 and n_rows >= 6
    if is_two_col:
        xs = [_to_float(c) for c in col_values[0]]
        ys = [_to_float(c) for c in col_values[1]]
        series.append({"series": headers[1], "x": headers[0],
                       "features": curve_features(ys, xs)})
    elif is_wide:
        # 宽表：每列是一条曲线/通道/样本，x 用行号
        for i in numeric_cols[:MAX_SERIES]:
            ys = [_to_float(c) for c in col_values[i]]
            series.append({"series": headers[i], "x": "row_index",
                           "features": curve_features(ys)})

    return {
        "file": file_info["rel"], "delimiter": None, "n_rows": n_rows, "n_cols": n_cols,
        "shape": "two_col" if is_two_col else ("wide" if is_wide else "long"),
        "columns": columns,
        "series": series,
    }


def _read_json_table(file_info):
    obj = load_json_safe(file_info["path"])
    if obj is None:
        return None
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        keys = sorted({k for x in obj for k in x.keys()})
        rows = [[str(x.get(k, "")) for k in keys] for x in obj]
        return keys, rows
    if isinstance(obj, dict):
        # 尝试 dict-of-list（如 {series: [..]}）
        keys = sorted(obj.keys())
        lens = {len(v) if isinstance(v, list) else 0 for v in obj.values()}
        if lens and len(lens) == 1 and 0 not in lens:
            rows = [[str(obj[k][i]) for k in keys] for i in range(max(lens))]
            return keys, rows
    return None


def inspect_input(input_dir, output_dir):
    files = discover_files(input_dir)
    out_abs = os.path.abspath(output_dir) + os.sep
    inventory = []
    tables = []
    degradations = []
    for f in files:
        if f["rel"] in ("case.json", "task.md"):
            continue
        # 排除产物目录自身，避免把 result.json/data_profile.json 当材料画像（保证确定性）。
        if os.path.abspath(f["path"]).startswith(out_abs):
            continue
        entry = {"rel": f["rel"], "path": f["path"], "size_bytes": f["size_bytes"], "ext": f["ext"],
                 "role_hint": _role_hint(f["rel"]), "sha256_head": sha256_head(f["path"])}
        if f["size_bytes"] > MAX_FILE_BYTES:
            entry["status"] = "skipped_oversize"
            degradations.append({"file": f["rel"], "issue": "oversize",
                                 "detail": "exceeds %d bytes, skipped" % MAX_FILE_BYTES})
        elif f["size_bytes"] <= 0:
            entry["status"] = "empty"
        else:
            text, enc = read_text(f["path"])
            if text is None:
                entry["status"] = "unreadable"
                degradations.append({"file": f["rel"], "issue": "unreadable", "detail": enc})
            else:
                entry["encoding"] = enc
                entry["status"] = "readable"
                if enc == "latin-1":
                    degradations.append({"file": f["rel"], "issue": "bad_encoding",
                                         "detail": "UTF-8 解码失败，已用 latin-1 兜底"})
                if f["ext"] in (".json", ".jsonl"):
                    jt = _read_json_table(entry)
                    if jt is not None:
                        tables.append(_profile_table(entry, *jt))
                    elif entry.get("size_bytes", 0) > 0:
                        degradations.append({"file": f["rel"], "issue": "bad_json",
                                             "detail": "JSON 解析失败，已记录并继续处理其余材料"})
                elif f["ext"] in NUMERIC_EXT:
                    parsed = parse_delimited(f["path"])
                    if parsed is not None:
                        tables.append(_profile_table(entry, *parsed))
        inventory.append(entry)

    inventory.sort(key=lambda x: x["rel"])
    tables.sort(key=lambda x: x["file"])
    profile = {
        "generated": True,
        "input_dir": input_dir,
        "data_form": _detect_data_form(files, tables),
        "inventory": inventory,
        "tables": tables,
        "degradations": degradations,
        "statistical_recipe": {
            "z_threshold": DEFAULT_Z,
            "iqr_k": DEFAULT_IQR_K,
            "mad_k": DEFAULT_MAD_K,
            "grubbs_alpha": GRUBBS_ALPHA,
            "note": "z 假定近似正态；IQR/MAD 稳健但阈值需与领域经验核对；Grubbs 为单离群近似。",
        },
    }
    return profile


# ---------- report 与 evidence_map ----------

def _fmt_val(v):
    if isinstance(v, float):
        return "%.6g" % v
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _render_items(items, level):
    if not items:
        return "(空)\n"
    lines = []
    for it in items:
        if isinstance(it, dict):
            for k, v in it.items():
                if k == "evidence":
                    continue
                if v in (None, [], {}):
                    continue
                lines.append("%s- **%s**: %s" % ("  " * level, k, _fmt_val(v)))
            ev = it.get("evidence") or []
            if ev:
                for e in ev:
                    if isinstance(e, dict):
                        lines.append("%s  - 证据 `%s`: %s" % ("  " * level,
                                                              e.get("locator", "?"),
                                                              e.get("quote", "")))
        else:
            lines.append("%s- %s" % ("  " * level, str(it)))
    return "\n".join(lines) + "\n"


def build_report_and_map(result):
    evidence_map = []
    lines = []
    lines.append("# 实验结果解释与异常分析 · %s" % result.get("case_id", ""))
    lines.append("")
    lines.append("- 状态：`%s`" % result.get("status"))
    lines.append("- 数据形态：`%s`" % result.get("data_form", "generic"))
    lines.append("- 摘要：%s" % result.get("summary", ""))
    lines.append("")

    interp = result.get("interpretation") or {}
    labels = [
        ("trends", "趋势"),
        ("anomalies", "异常点"),
        ("possible_causes", "可能原因"),
        ("verification_steps", "复核步骤"),
        ("followup_experiments", "后续实验建议"),
    ]
    lines.append("## 五段结论")
    for key, label in labels:
        lines.append("")
        lines.append("### %s" % label)
        items = interp.get(key) or []
        lines.append(_render_items(items, 1))
        for it in items:
            if isinstance(it, dict):
                ev = it.get("evidence") or []
                for e in ev:
                    if isinstance(e, dict) and e.get("locator"):
                        evidence_map.append({"locator": e.get("locator"),
                                             "quote": e.get("quote", ""),
                                             "used_by": "%s/%s" % (key, it.get("object") or it.get("summary") or "")})

    plan = result.get("experiment_plan") or {}
    lines.append("## 实验方案")
    lines.append(_render_items([plan], 1))

    risk = result.get("evidence_and_risk") or {}
    lines.append("## 依据与风险")
    lines.append(_render_items([risk], 1))

    conf = result.get("confirmation_items") or {}
    lines.append("## 确认项")
    lines.append(_render_items([conf], 1))

    for sec, key in [("来源", "sources"), ("不确定性", "uncertainty"),
                     ("局限", "limitations"), ("待人工确认", "pending_human"),
                     ("错误", "errors"), ("降级", "degradations"),
                     ("变更记录", "change_log")]:
        lines.append("")
        lines.append("## %s" % sec)
        lines.append(_render_items(result.get(key) or [], 0))

    runtime = result.get("runtime") or {}
    lines.append("")
    lines.append("## 运行信息")
    lines.append(_render_items([runtime], 1))
    return "\n".join(lines), evidence_map


# ---------- 契约校验 ----------

def _is_evidence_ok(ev):
    return isinstance(ev, dict) and bool(ev.get("locator")) and isinstance(ev.get("quote", ""), str)


def validate_result(result):
    issues, warnings = [], []

    if not isinstance(result, dict):
        return ["result 不是 JSON 对象"], []

    for k in TOP_KEYS:
        if k not in result:
            issues.append("缺少顶层字段：%s" % k)

    cid = result.get("case_id")
    if not (isinstance(cid, str) and cid.strip()):
        issues.append("case_id 缺失或为空")

    status = result.get("status")
    if status not in STATUSES:
        issues.append("status 非法：%s（应为 %s）" % (status, "|".join(STATUSES)))
    elif status == "ok" and result.get("summary") in (None, ""):
        warnings.append("status=ok 但 summary 为空")

    interp = result.get("interpretation")
    if isinstance(interp, dict):
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                issues.append("interpretation 缺少键：%s" % k)
                continue
            v = interp.get(k)
            if not isinstance(v, list):
                issues.append("interpretation.%s 应为数组" % k)
                continue
            if status == "ok" and not v:
                warnings.append("status=ok 但 interpretation.%s 为空" % k)
            for it in v:
                if isinstance(it, dict):
                    ev = it.get("evidence")
                    if ev is None and k != "verification_steps":
                        warnings.append("%s 条目缺少 evidence：%s" % (k, it.get("object") or it.get("summary") or ""))
                    if isinstance(ev, list):
                        for e in ev:
                            if not _is_evidence_ok(e):
                                warnings.append("%s 证据缺 locator 或 quote" % k)
                    if k == "anomalies":
                        if not it.get("threshold") and not it.get("method"):
                            warnings.append("anomalies 未写判定阈值/方法：%s" % (it.get("object") or ""))
                    if k == "verification_steps":
                        if not it.get("target") or not it.get("decision_rule"):
                            warnings.append("verification_steps 缺 target 或 decision_rule")
    else:
        issues.append("interpretation 缺失或非对象")

    for key, expected in (("experiment_plan", PLAN_KEYS), ("evidence_and_risk", RISK_KEYS),
                          ("confirmation_items", CONFIRM_KEYS)):
        obj = result.get(key)
        if not isinstance(obj, dict):
            issues.append("%s 缺失或非对象" % key)
            continue
        for k in expected:
            if k not in obj:
                warnings.append("%s 缺少键：%s" % (key, k))

    sources = result.get("sources")
    if not isinstance(sources, list) or not sources:
        warnings.append("sources 为空；应列出所用材料与哈希")

    runtime = result.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("started"):
        warnings.append("runtime 缺少 started 时间戳")

    change_log = result.get("change_log")
    if not isinstance(change_log, list) or not change_log:
        warnings.append("change_log 为空；应记录骨架→画像→结论→定稿")

    return issues, warnings
