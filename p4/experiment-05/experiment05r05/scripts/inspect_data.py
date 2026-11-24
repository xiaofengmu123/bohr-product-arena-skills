#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据清点与画像：生成 data_profile.json，供五段结论定位数值与异常。

仅使用 Python 标准库。文本表格（CSV/TSV）与 JSON 列表直接画像；
XLSX/MAT 需先用平台 pandas/openpyxl 导出 CSV 后再画像。

用法：
  python scripts/inspect_data.py --input /input --output /app/output
"""

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
MAX_FILE_BYTES = 2_000_000
MAX_ROWS = 200_000
TOP_OUTLIERS = 20

UNIT_HINTS = {
    "N": r"(?<![a-z])(force|load|F)(?![a-z])|\(N\)|\bN\b",
    "kN": r"\bkN\b",
    "MPa": r"\bMPa\b",
    "GPa": r"\bGPa\b",
    "Pa": r"\bPa\b",
    "mm": r"\bmm\b|displacement|deflection",
    "um": r"\bum\b|\bµm\b|micron",
    "degC": r"temp|°C",
    "degF": r"°F",
    "mg": r"\bmg\b",
    "mL": r"\bmL\b|ml\b|volume",
    "s": r"\b(seconds?|time)\b",
    "min": r"\bmin\b",
    "h": r"\bh\b|hour",
    "pct": r"percent|%",
    "A": r"current|\bA\b",
    "V": r"voltage|\bV\b",
    "Hz": r"\bHz\b|frequency",
    "rpm": r"\brpm\b",
    "g": r"\bg\b|gravity|accel",
    "M": r"molar|\bM\b|conc",
}

GROUP_COL_RE = re.compile(
    r"batch|sample|group|channel|well|fold|run|repeat|replic|estimator|model|subject|"
    r"plate|condition|treatment|arm|dose|conc|strain|sheet|label|id$|_id|^id$|"
    r"time|^t$|date|day|hour|min|^x$|x_|displacement|strain|position|index",
    re.I,
)

NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def now_ts():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > max_bytes:
        return None, "file exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    return None, "unreadable encoding"


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


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
                        "ext": os.path.splitext(fn)[1].lower()})
    return out


def to_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def try_int(v):
    f = to_float(v)
    if f is None:
        return None
    if f == int(f) and abs(f) < 1e15:
        return int(f)
    return f


def is_numeric_list(vals):
    if not vals:
        return False
    hit = sum(1 for v in vals if v is not None)
    return hit >= max(1, int(len(vals) * 0.8))


def zscore_outliers(vals):
    n = len(vals)
    if n < 3:
        return []
    mu = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    if sd == 0:
        return []
    out = []
    for i, v in enumerate(vals):
        z = (v - mu) / sd
        if abs(z) > 3:
            out.append({"index": i, "value": v, "z": round(z, 4)})
    out.sort(key=lambda x: -abs(x["z"]))
    return out[:TOP_OUTLIERS]


def iqr_outliers(vals):
    n = len(vals)
    if n < 4:
        return []
    s = sorted(vals)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return []
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    out = []
    for i, v in enumerate(vals):
        if v < lo or v > hi:
            out.append({"index": i, "value": v, "iqr": round(iqr, 6), "lo": round(lo, 6), "hi": round(hi, 6)})
    out.sort(key=lambda x: -abs(x["value"] - ((q1 + q3) / 2)))
    return out[:TOP_OUTLIERS]


def mad_outliers(vals):
    n = len(vals)
    if n < 5:
        return []
    med = statistics.median(vals)
    devs = [abs(v - med) for v in vals]
    mad = statistics.median(devs)
    if mad == 0:
        return []
    thresh = med + 3 * 1.4826 * mad
    out = []
    for i, v in enumerate(vals):
        if abs(v - med) > 3 * 1.4826 * mad:
            out.append({"index": i, "value": v, "mad": round(mad, 6), "median": round(med, 6)})
    out.sort(key=lambda x: -abs(x["value"] - x["median"]))
    return out[:TOP_OUTLIERS]


def unit_hint(colname):
    for unit, pat in UNIT_HINTS.items():
        if re.search(pat, colname, re.I):
            return unit
    return None


def profile_numeric(vals, colname, offset=0):
    nums = [v for v in vals if v is not None]
    n = len(vals)
    missing = sum(1 for v in vals if v is None)
    prof = {"name": colname, "dtype": "float", "n": len(nums), "missing": missing}
    if not nums:
        prof["mean"] = prof["median"] = prof["std"] = prof["min"] = prof["max"] = None
        return prof
    prof["mean"] = round(statistics.fmean(nums), 6)
    prof["median"] = round(statistics.median(nums), 6)
    prof["std"] = round(statistics.pstdev(nums), 6) if len(nums) > 1 else 0.0
    prof["min"] = min(nums)
    prof["max"] = max(nums)
    prof["z_outliers"] = zscore_outliers(nums)
    prof["iqr_outliers"] = iqr_outliers(nums)
    prof["mad_outliers"] = mad_outliers(nums)
    # 偏移：数组中的 index 对应原表行号（含表头 0）
    for tag in ("z_outliers", "iqr_outliers", "mad_outliers"):
        for o in prof[tag]:
            o["row"] = o["index"] + offset + 1
    # 饱和/超量程
    if len(nums) >= 20:
        mm = (prof["min"], prof["max"])
        frac_min = sum(1 for v in nums if v == mm[0]) / len(nums)
        frac_max = sum(1 for v in nums if v == mm[1]) / len(nums)
        if frac_min >= 0.05 or frac_max >= 0.05:
            prof["saturated"] = True
            prof["saturation_note"] = "min 重复 %.0f%%，max 重复 %.0f%%（可能钳位/饱和）" % (frac_min * 100, frac_max * 100)
        else:
            prof["saturated"] = False
            prof["saturation_note"] = None
    if len(set(nums)) == 1:
        prof["saturated"] = True
        prof["saturation_note"] = "全部取值相同（%.6g）" % nums[0]
    prof["unit_hint"] = unit_hint(colname)
    return prof


def parse_table(path):
    """读取 CSV/TSV 表，返回 (header, rows, delimiter, n_raw_rows, encoding, has_header)。rows 为字符串行列表。"""
    text, enc = read_text(path)
    if text is None:
        return None, None, None, 0, enc, False
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "," if sample.count(",") >= sample.count("\t") else "\t"
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    for r in reader:
        rows.append(r)
        if len(rows) >= MAX_ROWS:
            break
    if not rows:
        return None, [], delim, 0, enc, False
    # 首行是否表头：含非数字单元或含字母
    first = rows[0]
    has_header = any(not NUM_RE.match(c.strip()) for c in first if c.strip()) or any(re.search(r"[a-zA-Z]", c) for c in first)
    if has_header:
        header = [c.strip() or "col%d" % i for i, c in enumerate(first)]
        data = rows[1:]
    else:
        header = ["col%d" % i for i in range(len(first))]
        data = rows
    return header, data, delim, len(rows), enc, has_header


def profile_table(path, rel):
    header, data, delim, n_raw, enc, has_header = parse_table(path)
    if header is None:
        return {"rel": rel, "kind": "table", "error": "unreadable"}, None
    ncols = len(header)
    # 对齐行长
    cols = [[] for _ in range(ncols)]
    for r in data:
        for i in range(ncols):
            v = r[i] if i < len(r) else None
            cols[i].append(v)
    col_profiles = []
    numeric_cols = []
    row_offset = 1 if has_header else 0
    for i, name in enumerate(header):
        raw = cols[i]
        nums = [to_float(v.strip()) if isinstance(v, str) and v.strip() else None for v in raw]
        nonmiss = [v for v in nums if v is not None]
        if nonmiss and len(nonmiss) >= max(1, int(len(raw) * 0.8)):
            p = profile_numeric(nums, name, offset=row_offset)
            col_profiles.append(p)
            numeric_cols.append((i, name, nums))
        else:
            uniq = sorted({(v.strip() if isinstance(v, str) else str(v)) for v in raw if v is not None})[:20]
            col_profiles.append({"name": name, "dtype": "str", "n": len(raw), "missing": sum(1 for v in raw if v is None),
                                 "unique_count": len({v for v in raw if v is not None}), "examples": uniq,
                                 "group_like": bool(GROUP_COL_RE.search(name))})
    prof = {
        "rel": rel, "kind": "table", "delimiter": delim, "encoding": enc,
        "has_header": has_header, "n_rows": len(data), "n_cols": ncols, "header": header,
        "columns": col_profiles,
    }
    # 组列与宽表曲线检测
    group_cols = [p["name"] for p in col_profiles if p.get("group_like")]
    prof["group_columns"] = group_cols
    if len(numeric_cols) >= 10 and len(data) <= 2000:
        prof["trace_structure"] = "wide"
        prof["trace_summaries"] = trace_summaries(numeric_cols, header)
    elif len(numeric_cols) >= 2 and group_cols:
        prof["trace_structure"] = "grouped_long"
    else:
        prof["trace_structure"] = "generic"
    return prof, None


def trace_summaries(numeric_cols, header):
    """宽表：每列视为一条曲线，计算 summary 并标记离群曲线。"""
    summaries = []
    for i, name, nums in numeric_cols:
        vals = [v for v in nums if v is not None]
        if not vals:
            continue
        s = {"column": name, "n": len(vals), "max": max(vals), "min": min(vals),
             "mean": round(statistics.fmean(vals), 6), "std": round(statistics.pstdev(vals), 6) if len(vals) > 1 else 0.0,
             "last": vals[-1], "auc_sum": round(sum(vals), 6)}
        summaries.append(s)
    # 用 max 与 auc_sum 找离群曲线
    if len(summaries) >= 5:
        for key in ("max", "auc_sum"):
            vals = [s[key] for s in summaries if isinstance(s[key], (int, float))]
            if len(vals) < 5:
                continue
            q1 = sorted(vals)[len(vals) // 4]
            q3 = sorted(vals)[(3 * len(vals)) // 4]
            iqr = q3 - q1
            if iqr == 0:
                continue
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for s in summaries:
                v = s[key]
                if isinstance(v, (int, float)) and (v < lo or v > hi):
                    s.setdefault("outlier_flags", {})[key] = {"value": v, "lo": round(lo, 6), "hi": round(hi, 6)}
    return summaries


def profile_json(path, rel):
    text, enc = read_text(path)
    if text is None:
        return {"rel": rel, "kind": "json", "error": "unreadable"}, None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        return {"rel": rel, "kind": "json", "error": "bad json: %s" % exc}, "bad json"
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        keys = []
        for x in obj[:200]:
            for k in x.keys():
                if k not in keys:
                    keys.append(k)
        rows = [[ (x.get(k) if isinstance(x, dict) else None) for x in obj] for k in keys]
        header = keys
        cols = rows
        col_profiles = []
        for i, name in enumerate(header):
            raw = cols[i]
            nums = [to_float(v) if v is not None else None for v in raw]
            nonmiss = [v for v in nums if v is not None]
            if nonmiss and len(nonmiss) >= max(1, int(len(raw) * 0.8)):
                col_profiles.append(profile_numeric(nums, name))
            else:
                col_profiles.append({"name": name, "dtype": "str", "n": len(raw),
                                     "unique_count": len({str(v) for v in raw if v is not None}),
                                     "group_like": bool(GROUP_COL_RE.search(name))})
        return {"rel": rel, "kind": "json_list", "encoding": enc, "n_rows": len(obj),
                "n_cols": len(keys), "header": keys, "columns": col_profiles}, None
    return {"rel": rel, "kind": "json", "encoding": enc,
            "note": "非列表或非对象列表，仅记录顶层类型 %s" % type(obj).__name__}, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据清点与画像")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    files = discover_files(args.input)
    out = {"input_root": args.input, "generated_at": now_ts(), "file_count": len(files), "files": [], "issues": [], "degradations": []}
    for f in files:
        rel = f["rel"]
        if rel in ("case.json", "task.md") or rel.startswith(".tmp_"):
            continue
        if f["size_bytes"] > MAX_FILE_BYTES:
            out["degradations"].append({"rel": rel, "reason": "file exceeds %d bytes" % MAX_FILE_BYTES})
            continue
        ext = f["ext"]
        entry = {"rel": rel, "size_bytes": f["size_bytes"], "ext": ext}
        if ext in (".csv", ".tsv", ".txt", ".dat"):
            prof, err = profile_table(f["path"], rel)
            entry.update(prof)
            if err:
                out["degradations"].append({"rel": rel, "reason": err})
        elif ext == ".json":
            prof, err = profile_json(f["path"], rel)
            entry.update(prof)
            if err:
                out["degradations"].append({"rel": rel, "reason": err})
        elif ext in (".xlsx", ".xls", ".mat", ".npy", ".npz", ".h5", ".hdf5"):
            entry["kind"] = "binary"
            entry["note"] = "二进制格式：需先用平台 pandas/openpyxl 导出 CSV 后重新画像"
            out["issues"].append({"rel": rel, "level": "action", "note": "二进制格式需导出 CSV 后再画像"})
        elif ext == ".pdf":
            entry["kind"] = "pdf"
            entry["note"] = "PDF：用 pdftotext 抽取文本后按原文逐字引用，本脚本不做 PDF 画像"
        else:
            entry["kind"] = "other"
        out["files"].append(entry)

    os.makedirs(args.output, exist_ok=True)
    write_json_atomic(os.path.join(args.output, "data_profile.json"), out)
    print("inspect_data: %d files -> %s" % (len(out["files"]), os.path.join(args.output, "data_profile.json")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
