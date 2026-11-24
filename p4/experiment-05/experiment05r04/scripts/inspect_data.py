#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据清点与画像：生成 data_profile.json。

仅使用 Python 标准库。识别文件形态（曲线型 / 表格型 / 交叉验证型 / 二进制待导出），
对文本表格计算每列样本量、缺失、均值/中位数/标准差、z-score/IQR/MAD 离群标记，
并标记单位不一致、超量程/饱和、坏编码。

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (  # noqa: E402
    DEFAULT_INPUT, DEFAULT_OUTPUT, MAX_FILE_BYTES,
    read_text, load_json_safe, write_json_atomic, discover_files,
    material_files, now_ts,
)

TEXT_EXTS = {".csv", ".tsv", ".txt", ".json", ".jsonl", ".md"}
TABULAR_BINARY = {".xlsx", ".xls", ".ods", ".mat", ".npy", ".npz"}
TRACE_KEYS = ("bend", "trace", "curve", "force", "load", "displacement", "stress", "strain", "时间", "位移", "载荷", "力")
CV_KEYS = ("fold", "split", "estimator", "cross", "train", "test", "accuracy", "f1", "auc", "iris")
UNIT_HINTS = ("mm", "cm", "µm", "um", "nm", "N", "kN", "MPa", "GPa", "kg", "g", "mg", "mL", "L", "%", "°C", "s", "min", "h")

Z_THRESHOLD = 3.0
IQR_K = 1.5
MAD_K = 3.0


def detect_delimiter(sample):
    counts = {"comma": sample.count(","), "tab": sample.count("\t"),
              "semicolon": sample.count(";"), "pipe": sample.count("|")}
    if counts["tab"] >= counts["comma"] and counts["tab"] >= counts["semicolon"] and counts["tab"] >= counts["pipe"] and counts["tab"] > 0:
        return "\t"
    if counts["comma"] > 0 and counts["comma"] >= counts["semicolon"] and counts["comma"] >= counts["pipe"]:
        return ","
    if counts["semicolon"] > 0 and counts["semicolon"] >= counts["pipe"]:
        return ";"
    if counts["pipe"] > 0:
        return "|"
    return ","


def parse_table(text):
    """把文本按 CSV/TSV 解析为 (header, rows)；失败返回 (None, reason)。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, "empty"
    delim = detect_delimiter("\n".join(lines[:20]))
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = [r for r in reader if any(c.strip() for c in r)]
    except csv.Error as exc:
        return None, "csv error: %s" % exc
    if not rows:
        return None, "no rows"
    # 首行是否为表头：首行非数值且后续行有数值
    header = rows[0]
    body = rows[1:]
    header_is_text = any(not is_numeric(c) for c in header)
    if header_is_text and body:
        return header, body
    # 无表头：生成列名
    width = max(len(r) for r in rows)
    return ["col%d" % (i + 1) for i in range(width)], rows


def is_numeric(s):
    s = (s or "").strip()
    if not s:
        return False
    try:
        float(s.replace(",", ""))
        return True
    except (ValueError, TypeError):
        return False


def to_num(s):
    try:
        return float((s or "").strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def median(xs):
    if not xs:
        return None
    return statistics.median(xs)


def mad(xs):
    if not xs:
        return None
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def profile_columns(header, body):
    """对每列统计画像，返回列画像列表与文件级单位提示。"""
    width = max([len(header)] + [len(r) for r in body]) if (header or body) else 0
    cols = []
    for j in range(width):
        raw = [r[j] if j < len(r) else "" for r in body]
        vals = []
        missing = 0
        for v in raw:
            if v is None or not str(v).strip():
                missing += 1
            else:
                n = to_num(v)
                if n is None:
                    missing += 1
                else:
                    vals.append(n)
        n = len(vals)
        if n == 0:
            cols.append({"index": j, "name": header[j] if j < len(header) else "col%d" % (j + 1),
                         "numeric": False, "n": 0, "missing": len(raw)})
            continue
        mean = statistics.fmean(vals) if vals else None
        stdev = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        med = median(vals)
        mad_v = mad(vals)
        # z-score
        z_out = []
        if stdev and stdev > 0:
            z_out = [i for i, x in enumerate(vals) if abs(x - mean) / stdev > Z_THRESHOLD]
        # IQR
        iqr_out = []
        if len(vals) >= 4:
            sv = sorted(vals)
            q1 = sv[len(sv) // 4]
            q3 = sv[(3 * len(sv)) // 4]
            iqr = q3 - q1
            if iqr > 0:
                lo, hi = q1 - IQR_K * iqr, q3 + IQR_K * iqr
                iqr_out = [i for i, x in enumerate(vals) if x < lo or x > hi]
        # MAD
        mad_out = []
        if mad_v and mad_v > 0:
            mad_out = [i for i, x in enumerate(vals) if abs(x - med) > MAD_K * 1.4826 * mad_v]
        # 超量程/饱和：恒定值占比或极值重复
        uniq = len(set(round(x, 8) for x in vals))
        saturation = (uniq == 1) or (uniq <= max(2, n // 20))
        cols.append({
            "index": j,
            "name": header[j] if j < len(header) else "col%d" % (j + 1),
            "numeric": True,
            "n": n,
            "missing": missing,
            "mean": round(mean, 6) if mean is not None else None,
            "median": round(med, 6) if med is not None else None,
            "std": round(stdev, 6) if stdev is not None else None,
            "min": round(min(vals), 6) if vals else None,
            "max": round(max(vals), 6) if vals else None,
            "z_outliers": z_out,
            "iqr_outliers": iqr_out,
            "mad_outliers": mad_out,
            "saturation_suspect": bool(saturation),
        })
    return cols


def profile_text_file(full, rel, ext):
    text, enc = read_text(full)
    if text is None:
        return {"rel": rel, "ext": ext, "kind": "text", "status": "skipped",
                "reason": "unreadable_or_oversize", "encoding": None}
    if enc == "latin-1":
        deg = {"file": rel, "issue": "bad_encoding", "detail": "按 latin-1 降级读取"}
    else:
        deg = None
    profile = {"rel": rel, "ext": ext, "kind": "text", "status": "ok",
               "encoding": enc, "bytes": len(text.encode(enc or "utf-8", errors="replace"))}
    if ext in (".json", ".jsonl"):
        obj = load_json_safe(full)
        profile["json_parsed"] = obj is not None
        if obj is None:
            profile["status"] = "bad_json"
        else:
            profile["json_type"] = type(obj).__name__
            profile["top_keys"] = list(obj.keys())[:30] if isinstance(obj, dict) else None
    else:
        header, body = parse_table(text)
        if header is None:
            profile["status"] = "unparsed"
            profile["reason"] = body
        else:
            cols = profile_columns(header, body)
            profile["n_rows"] = len(body)
            profile["n_cols"] = len(header)
            profile["columns"] = cols
            profile["header"] = header
            # 形态分型
            header_text = " ".join(header).lower()
            nrows = len(body)
            many_rows = nrows >= 20
            all_numeric_cols = all(c["numeric"] for c in cols if c["n"] > 0)
            if many_rows and all_numeric_cols and any(k in header_text for k in TRACE_KEYS):
                profile["shape"] = "trace"
            elif any(k in header_text for k in CV_KEYS) and nrows <= 500:
                profile["shape"] = "cross_validation"
            else:
                profile["shape"] = "tabular"
    profile["unit_hints"] = sorted({u for u in UNIT_HINTS if u.lower() in (text[:4000].lower() if text else "")})
    return profile, deg


def profile_binary_file(full, rel, ext):
    return {
        "rel": rel, "ext": ext, "kind": "binary", "status": "needs_export",
        "hint": "请用平台 pandas/openpyxl/scipy 导出为 CSV 后重跑本脚本，以得到数值画像。",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据清点与画像")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    files = discover_files(args.input)
    materials = material_files(files)
    profiles = []
    degradations = []
    for f in materials:
        ext = f["ext"]
        if ext in TEXT_EXTS:
            p, deg = profile_text_file(f["path"], f["rel"], ext)
            profiles.append(p)
            if deg:
                degradations.append(deg)
        elif ext in TABULAR_BINARY:
            profiles.append(profile_binary_file(f["path"], f["rel"], ext))
            degradations.append({"file": f["rel"], "issue": "binary_needs_export",
                                 "detail": "二进制表格/MAT 未在本脚本内解析"})
        else:
            profiles.append({"rel": f["rel"], "ext": ext, "kind": "other",
                             "status": "listed_only", "hint": "非文本表格，由 Agent 按材料类型处理"})

    out = {
        "generated_at": now_ts(),
        "input_dir": args.input,
        "file_count": len(materials),
        "profiles": profiles,
        "degradations": degradations,
        "shape_summary": {
            "trace": sum(1 for p in profiles if p.get("shape") == "trace"),
            "cross_validation": sum(1 for p in profiles if p.get("shape") == "cross_validation"),
            "tabular": sum(1 for p in profiles if p.get("shape") == "tabular"),
            "binary_needs_export": sum(1 for p in profiles if p.get("status") == "needs_export"),
        },
    }
    os.makedirs(args.output, exist_ok=True)
    write_json_atomic(os.path.join(args.output, "data_profile.json"), out)
    print(json.dumps(out["shape_summary"], ensure_ascii=False, indent=2))
    print("degradations=%d" % len(degradations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
