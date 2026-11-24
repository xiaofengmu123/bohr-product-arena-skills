#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据清点与画像：把输入材料转成可回指的数据画像与离群标记。

仅使用 Python 标准库。对 CSV/TSV/JSON 直接画像；XLSX/MAT/PDF 等二进制格式
在 profile 中标记 needs_export，并建议由平台 pandas/openpyxl 先导出 CSV 再画像。

用法：
  python scripts/inspect_data.py --input /input --output /app/output
产物：
  /app/output/data_profile.json
"""

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
MAX_FILE_BYTES = 2_000_000
MAX_ROWS = 20000

UNIT_HINTS = {
    "n": "N", "kn": "kN", "mn": "mN", "mpa": "MPa", "gpa": "GPa", "kpa": "kPa",
    "mm": "mm", "um": "µm", "nm": "nm", "deg": "°", "c": "°C", "f": "°F",
    "s": "s", "min": "min", "h": "h", "hz": "Hz", "kg": "kg", "g": "g", "mg": "mg",
    "l": "L", "ml": "mL", "m": "m", "v": "V", "mv": "mV", "a": "A", "ma": "mA",
    "ph": "pH", "od": "OD", "rpm": "rpm", "pct": "%", "percent": "%", "acc": "acc",
    "accuracy": "acc", "precision": "prec", "recall": "rec", "f1": "F1",
}


def log(msg):
    sys.stderr.write("[inspect] %s\n" % msg)


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


def sniff_delimiter(sample):
    try:
        d = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return d.delimiter
    except csv.Error:
        return "," if sample.count(",") >= sample.count("\t") else "\t"


def to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def is_numeric(values, min_ratio=0.8):
    nonnull = [v for v in values if v is not None and str(v).strip() != ""]
    if not nonnull:
        return False
    nums = [v for v in nonnull if to_float(v) is not None]
    return len(nums) / len(nonnull) >= min_ratio


def guess_unit(name):
    low = str(name).lower()
    for key in sorted(UNIT_HINTS, key=len, reverse=True):
        if key in low:
            return UNIT_HINTS[key]
    return None


def numeric_stats(nums):
    if not nums:
        return None
    n = len(nums)
    mean = sum(nums) / n
    median = statistics.median(nums)
    stdev = statistics.pstdev(nums) if n > 1 else 0.0
    q1 = _quantile(sorted(nums), 0.25)
    q3 = _quantile(sorted(nums), 0.75)
    iqr = q3 - q1
    mad = statistics.median([abs(x - median) for x in nums]) if n else 0.0
    return {"n": n, "mean": mean, "median": median, "std": stdev,
            "min": min(nums), "max": max(nums), "q1": q1, "q3": q3,
            "iqr": iqr, "mad": mad}


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def flag_outliers(values, nums, stats):
    """返回离群标记列表：z-score / IQR / MAD 三法，标注行号与数值。"""
    if not nums or stats is None:
        return []
    flags = []
    z_thr, iqr_k, mad_k = 3.0, 1.5, 3.5
    for i, raw in enumerate(values):
        v = to_float(raw)
        if v is None:
            continue
        reasons = []
        if stats["std"] > 0 and abs(v - stats["mean"]) / stats["std"] > z_thr:
            reasons.append("zscore|z|>%.1f=%.3f" % (z_thr, abs(v - stats["mean"]) / stats["std"]))
        if stats["iqr"] > 0:
            lo, hi = stats["q1"] - iqr_k * stats["iqr"], stats["q3"] + iqr_k * stats["iqr"]
            if v < lo or v > hi:
                reasons.append("iqr<%s,%s>" % (round(lo, 4), round(hi, 4)))
        if stats["mad"] > 0:
            m = stats["median"] + mad_k * 1.4826 * stats["mad"]
            if abs(v - stats["median"]) > mad_k * 1.4826 * stats["mad"]:
                reasons.append("mad_median±%.3f" % m)
        if reasons:
            flags.append({"row": i + 2, "value": v, "methods": reasons})
    return flags


def profile_delimited(path, rel, encoding):
    try:
        with open(path, "r", encoding=encoding, newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            delim = sniff_delimiter(sample)
            reader = csv.reader(fh, delimiter=delim)
            rows = []
            for i, row in enumerate(reader):
                if i >= MAX_ROWS:
                    break
                rows.append(row)
    except OSError as exc:
        return {"rel": rel, "error": str(exc), "columns": [], "issues": []}

    if not rows:
        return {"rel": rel, "error": "empty file", "columns": [], "issues": []}

    # 尝试首行为表头：表头与后续行的列数一致且表头多为非数值
    header = rows[0]
    body = rows[1:]
    has_header = (len(body) > 0 and len(header) == max(len(r) for r in body)
                  and not is_numeric(header, min_ratio=0.5))
    if has_header:
        colnames = [str(h).strip() or ("col%d" % (j + 1)) for j, h in enumerate(header)]
        data = body
        start_row = 2
    else:
        colnames = ["col%d" % (j + 1) for j in range(len(header))]
        data = rows
        start_row = 1

    ncols = len(colnames)
    columns = []
    for j in range(ncols):
        values = [r[j] if j < len(r) else None for r in data]
        nums = [to_float(v) for v in values]
        nums = [x for x in nums if x is not None]
        col = {
            "index": j,
            "name": colnames[j],
            "n": len(values),
            "missing": sum(1 for v in values if v is None or str(v).strip() == ""),
            "n_numeric": len(nums),
            "unit": guess_unit(colnames[j]) if has_header else None,
        }
        if is_numeric(values):
            stats = numeric_stats(nums)
            col["stats"] = stats
            col["outliers"] = flag_outliers(values, nums, stats)
            col["saturation"] = detect_saturation(nums)
        else:
            col["distinct"] = len(set(str(v) for v in values if v is not None))
            col["stats"] = None
        columns.append(col)

    issues = []
    for c in columns:
        if c.get("outliers"):
            issues.append({"file": rel, "column": c["name"],
                           "kind": "outliers", "count": len(c["outliers"]),
                           "top": c["outliers"][:5]})
        if c.get("saturation"):
            issues.append({"file": rel, "column": c["name"], "kind": "saturation",
                           "detail": c["saturation"]})
    return {"rel": rel, "delimiter": delim, "header": has_header,
            "n_rows": len(data), "n_columns": ncols,
            "encoding": encoding, "columns": columns, "issues": issues}


def detect_saturation(nums):
    if len(nums) < 5:
        return None
    mx = max(nums)
    mn = min(nums)
    n_max = sum(1 for x in nums if x == mx)
    n_min = sum(1 for x in nums if x == mn)
    out = {}
    if n_max / len(nums) >= 0.1:
        out["at_max"] = {"value": mx, "count": n_max, "ratio": round(n_max / len(nums), 3)}
    if n_min / len(nums) >= 0.1 and mn != mx:
        out["at_min"] = {"value": mn, "count": n_min, "ratio": round(n_min / len(nums), 3)}
    return out or None


def profile_json(path, rel):
    text, enc = read_text(path)
    if text is None:
        return {"rel": rel, "error": enc or "unreadable", "columns": [], "issues": []}
    try:
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        return {"rel": rel, "error": "bad json: %s" % exc, "columns": [], "issues": []}
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys = []
        for rec in obj[:100]:
            for k in rec:
                if k not in keys:
                    keys.append(k)
        columns = []
        for k in keys:
            values = [rec.get(k) for rec in obj]
            nums = [to_float(v) for v in values]
            nums = [x for x in nums if x is not None]
            col = {"index": len(columns), "name": str(k), "n": len(values),
                   "missing": sum(1 for v in values if v is None),
                   "n_numeric": len(nums), "unit": guess_unit(str(k))}
            if is_numeric(values):
                stats = numeric_stats(nums)
                col["stats"] = stats
                col["outliers"] = flag_outliers(values, nums, stats)
                col["saturation"] = detect_saturation(nums)
            else:
                col["distinct"] = len(set(str(v) for v in values if v is not None))
            columns.append(col)
        issues = [{"file": rel, "column": c["name"], "kind": "outliers", "count": len(c.get("outliers", []))}
                  for c in columns if c.get("outliers")]
        return {"rel": rel, "json_kind": "records", "n_rows": len(obj),
                "n_columns": len(columns), "columns": columns, "issues": issues}
    return {"rel": rel, "json_kind": type(obj).__name__, "columns": [], "issues": [],
            "note": "JSON not in flat list-of-records form; inspect manually"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    files = discover_files(args.input)
    profile = {"case_input": args.input, "files_total": len(files),
               "files": [], "issues": []}
    exported_hint = []
    for f in files:
        ext = f["ext"]
        if ext in (".csv", ".tsv", ".txt", ".dat"):
            text, enc = read_text(f["path"])
            if text is None:
                profile["files"].append({"rel": f["rel"], "error": enc or "unreadable"})
                continue
            rec = profile_delimited(f["path"], f["rel"], enc)
        elif ext == ".json":
            rec = profile_json(f["path"], f["rel"])
        elif ext in (".xlsx", ".xls", ".xlsm", ".mat", ".pdf", ".png", ".jpg", ".jpeg", ".svg"):
            rec = {"rel": f["rel"], "ext": ext, "needs_export": True,
                   "columns": [], "issues": [],
                   "note": "binary/office format; export to CSV first (pandas/openpyxl), then re-run inspect"}
            exported_hint.append(f["rel"])
        else:
            rec = {"rel": f["rel"], "ext": ext, "columns": [], "issues": [],
                   "note": "unsupported or unknown extension; inspect manually"}
        profile["files"].append(rec)
        if rec.get("issues"):
            profile["issues"].extend(rec["issues"])

    profile["summary"] = {
        "files_total": len(files),
        "tabular_files": sum(1 for r in profile["files"] if r.get("columns")),
        "needs_export_files": exported_hint,
        "issue_count": len(profile["issues"]),
    }
    os.makedirs(args.output, exist_ok=True)
    out_path = os.path.join(args.output, "data_profile.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    log("data_profile.json written: %d files, %d issues" % (len(files), len(profile["issues"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
