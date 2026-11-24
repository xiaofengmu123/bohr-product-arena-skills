#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据清点与画像引擎：识别 trace/tabular/experiment 形态。

输出 data_profile.json（机器可读）与 data_profile.md（人可读）。
用法：python scripts/profile.py --input /input --work /app/work --output /app/output
仅用 Python 标准库；XLSX/MAT/PDF 需先用平台工具导出为 CSV/文本后放在 /app/work/。
"""
import argparse
import csv
import io
import json
import math
import os
import re
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (read_text, MAX_FILE_BYTES, write_json_atomic,
                  write_text_atomic, now_ts, discover_files)

TEXT_EXTS = {".csv", ".tsv", ".txt", ".dat", ".text"}
JSON_EXTS = {".json"}
MD_EXTS = {".md", ".markdown"}
BINARY_HINT_EXTS = {".xlsx", ".xls", ".mat", ".m", ".pdf", ".png",
                    ".jpg", ".jpeg", ".svg", ".tif", ".tiff"}

TRACE_HINTS = ("trace", "curve", "bend", "signal", "wave", "load",
               "displacement", "stress", "strain", "force", "deflection", "raw")
EXPERIMENT_HINTS = ("metric", "split", "fold", "estimator", "model", "accuracy",
                    "f1", "precision", "recall", "auc", "cross_val", "iris",
                    "cv", "evaluation")
INDEX_HINTS = ("time", "t", "index", "x", "step", "cycle", "cycle#", "no", "sample")


def to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("，", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def parse_csv(path):
    text, enc = read_text(path)
    if text is None:
        return None, "unreadable"
    sample = text[:8192]
    dialect = csv.excel
    delim = ","
    try:
        d = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = d.delimiter
        dialect = d
    except Exception:
        counts = {c: sample.count(c) for c in (",", ";", "\t", "|")}
        if counts:
            delim = max(counts, key=counts.get)
            if delim == "\t":
                dialect = csv.excel_tab
    try:
        reader = csv.reader(io.StringIO(text), dialect)
        rows = list(reader)
    except Exception as exc:
        return None, "csv parse error: %s" % exc
    if not rows:
        return {"header": [], "rows": [], "delimiter": delim}, None
    return {"header": rows[0], "rows": rows[1:], "delimiter": delim}, None


def parse_json_doc(path):
    text, enc = read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad json: %s" % exc


def json_leaves(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else k
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((p, float(v)))
            elif isinstance(v, (dict, list)):
                out.extend(json_leaves(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = "%s[%d]" % (prefix, i)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((p, float(v)))
            elif isinstance(v, (dict, list)):
                out.extend(json_leaves(v, p))
    return out


def compute_stats(vals):
    n = len(vals)
    if n == 0:
        return {"n": 0, "missing": 0, "mean": None, "median": None, "std": None,
                "min": None, "max": None, "q1": None, "q3": None,
                "iqr": None, "mad": None}
    mean = statistics.fmean(vals)
    median = statistics.median(vals)
    std = statistics.pstdev(vals) if n > 1 else 0.0
    s = sorted(vals)

    def q(qv):
        pos = (n - 1) * qv
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return s[lo]
        return s[lo] * (hi - pos) + s[hi] * (pos - lo)

    q1 = q(0.25)
    q3 = q(0.75)
    iqr = q3 - q1
    mad = statistics.median([abs(v - median) for v in vals]) if n else 0.0
    return {"n": n, "missing": 0, "mean": round(mean, 8), "median": round(median, 8),
            "std": round(std, 8), "min": min(vals), "max": max(vals),
            "q1": round(q1, 8), "q3": round(q3, 8), "iqr": round(iqr, 8),
            "mad": round(mad, 8)}


def outlier_flags(series, stats):
    n = stats["n"]
    zs, iqs, ms = [], [], []
    if n == 0:
        return {"z": zs, "iqr": iqs, "mad": ms}
    mean = stats["mean"]
    std = stats["std"]
    median = stats["median"]
    q1 = stats["q1"]
    q3 = stats["q3"]
    iqr = stats["iqr"]
    mad = stats["mad"]
    for idx, v in series:
        if v is None:
            continue
        if std and std > 0 and abs(v - mean) / std > 3.0:
            zs.append({"row": idx + 2, "value": v, "z": round((v - mean) / std, 4)})
        if iqr and iqr > 0:
            lo = q1 - 1.5 * iqr
            hi = q3 + 1.5 * iqr
            if v < lo or v > hi:
                iqs.append({"row": idx + 2, "value": v, "lo": round(lo, 6),
                            "hi": round(hi, 6)})
        if mad and mad > 0:
            mz = 0.6745 * (v - median) / mad
            if abs(mz) > 3.5:
                ms.append({"row": idx + 2, "value": v, "modified_z": round(mz, 4)})
    return {"z": zs, "iqr": iqs, "mad": ms}


def trace_features(vals, stats):
    if not vals:
        return {}
    rng = stats["max"] - stats["min"]
    start = vals[0]
    end = vals[-1]
    third = max(1, len(vals) // 3)
    first_mean = statistics.fmean(vals[:third])
    last_mean = statistics.fmean(vals[-third:])
    scale = max(abs(first_mean), 1e-9)
    if last_mean - first_mean > 0.05 * scale:
        direction = "increasing"
    elif first_mean - last_mean > 0.05 * scale:
        direction = "decreasing"
    else:
        direction = "flat_or_nonmonotonic"
    tol = 0.005 * rng if rng and rng > 0 else 0.0
    best = cur = 0
    for a, b in zip(vals, vals[1:]):
        if abs(b - a) <= tol:
            cur += 1
        else:
            cur = 0
        best = max(best, cur)
    return {"start": start, "end": end, "range": rng, "direction": direction,
            "plateau_run": best,
            "plateau_frac": round(best / len(vals), 4) if vals else 0.0}


def extract_unit(cell):
    if not isinstance(cell, str):
        return None
    m = re.search(r"\(([^()]{0,24})\)", cell)
    return m.group(1).strip() if m else None


def saturation_info(vals, maxv):
    if not vals:
        return False
    return (sum(1 for v in vals if v == maxv) / len(vals) >= 0.05
            and sum(1 for v in vals if v == maxv) >= 3)


def range_exceeded(header_cells, maxv):
    for c in header_cells:
        if not isinstance(c, str):
            continue
        m = re.search(r"(?:range|量程|limit|上限)\D*(\d+(?:\.\d+)?)", c, re.I)
        if m:
            try:
                if maxv > float(m.group(1)):
                    return {"limit": float(m.group(1)), "header": c}
            except ValueError:
                pass
    return None


def is_increasing(vals):
    if len(vals) < 2:
        return False
    inc = sum(1 for a, b in zip(vals, vals[1:]) if b >= a)
    return inc >= 0.9 * (len(vals) - 1)


def role_of(ci, header_cell, vals):
    h = (header_cell or "").lower()
    if ci == 0 and (any(k in h for k in INDEX_HINTS) or is_increasing(vals)):
        return "index/time"
    return "dependent"


def classify_shape(rel, num_cols, total_cols, header):
    name = rel.lower()
    hdr = " ".join((h or "") for h in (header or [])).lower()
    if any(h in name for h in TRACE_HINTS) and num_cols >= 5:
        return "trace"
    if any(h in hdr for h in EXPERIMENT_HINTS) and num_cols >= 1:
        return "experiment"
    if num_cols >= 8:
        return "trace"
    return "tabular"


def profile_tabular(rel, header, rows, shape):
    vectors = []
    issues = []
    units = []
    for ci in range(len(header)):
        cells = [r[ci] if ci < len(r) else "" for r in rows]
        series = [(i, to_float(c)) for i, c in enumerate(cells)]
        vals = [v for _, v in series if v is not None]
        nonempty = sum(1 for c in cells if isinstance(c, str) and c.strip())
        if not vals or len(vals) < 0.8 * max(1, nonempty):
            continue
        stats = compute_stats(vals)
        stats["missing"] = len(cells) - len(vals)
        unit = extract_unit(header[ci] if ci < len(header) else None)
        if unit:
            units.append(unit)
        role = "field"
        if shape == "trace":
            role = role_of(ci, header[ci] if ci < len(header) else None, vals)
        vec = {"file": rel,
               "column": (header[ci] if ci < len(header) else "col%d" % ci),
               "role": role, "unit": unit}
        vec.update(stats)
        vec["outliers"] = outlier_flags(series, stats)
        maxv = stats["max"]
        if maxv is not None and saturation_info(vals, maxv):
            vec["saturated"] = True
            issues.append({"kind": "saturation", "locator": "%s#col=%s" % (rel, vec["column"]),
                           "detail": ">=5% 数值等于最大值 %s" % maxv})
        else:
            vec["saturated"] = False
        rr = range_exceeded(header, maxv) if maxv is not None else None
        vec["over_range"] = bool(rr)
        if rr:
            issues.append({"kind": "over_range", "locator": "%s#col=%s" % (rel, vec["column"]),
                           "detail": "max %s 超过声明上限 %s" % (maxv, rr["limit"])})
        if shape == "trace" and role == "dependent":
            vec["trace"] = trace_features(vals, stats)
        vectors.append(vec)
    distinct = {u for u in units if u}
    if len(distinct) > 1:
        issues.append({"kind": "unit_conflict", "locator": rel,
                       "detail": "同一文件多个数值列单位不一致: %s" % sorted(distinct)})
    return vectors, issues


def profile_json(rel, obj):
    vectors = []
    issues = []
    leaves = json_leaves(obj)
    if not leaves:
        return vectors, issues, "unknown"
    groups = {}
    for path, val in leaves:
        leaf = path.split(".")[-1].split("[")[0]
        groups.setdefault(leaf, []).append((path, val))
    for leaf, items in groups.items():
        vals = [v for _, v in items]
        stats = compute_stats(vals)
        vec = {"file": rel, "column": leaf, "role": "metric", "unit": None}
        vec.update(stats)
        if stats.get("mean"):
            vec["cv"] = round(stats["std"] / stats["mean"], 6)
        if len(vals) >= 2:
            vec["paths"] = [p for p, _ in items][:20]
        vectors.append(vec)
    return vectors, issues, "experiment"


def profile_file(full, rel, size, ext):
    if size > MAX_FILE_BYTES:
        meta = {"path": rel, "kind": "skipped", "ext": ext, "size_bytes": size}
        issue = {"kind": "oversize", "locator": rel,
                 "detail": "超过 %d 字节跳过" % MAX_FILE_BYTES}
        return meta, [], [issue], "skipped"
    if ext in JSON_EXTS:
        obj, err = parse_json_doc(full)
        if err:
            meta = {"path": rel, "kind": "json_bad", "ext": ext, "size_bytes": size}
            return meta, [], [{"kind": "bad_json", "locator": rel, "detail": err}], "unknown"
        vecs, issues, shape = profile_json(rel, obj)
        meta = {"path": rel, "kind": "json", "ext": ext, "size_bytes": size}
        return meta, vecs, issues, shape
    if ext in TEXT_EXTS:
        parsed, err = parse_csv(full)
        if err or parsed is None:
            meta = {"path": rel, "kind": "text_unparsed", "ext": ext, "size_bytes": size}
            return meta, [], [{"kind": "parse_error", "locator": rel,
                               "detail": err or "unreadable"}], "unknown"
        header = parsed["header"]
        rows = parsed["rows"]
        num_cols = 0
        for ci in range(len(header)):
            cells = [r[ci] if ci < len(r) else "" for r in rows]
            nv = sum(1 for c in cells if to_float(c) is not None)
            nonempty = sum(1 for c in cells if isinstance(c, str) and c.strip())
            if nv and nv >= 0.8 * max(1, nonempty):
                num_cols += 1
        shape = classify_shape(rel, num_cols, len(header), header)
        vecs, issues = profile_tabular(rel, header, rows, shape)
        meta = {"path": rel, "kind": "text", "ext": ext, "size_bytes": size,
                "rows": len(rows), "columns": len(header), "numeric_columns": num_cols}
        return meta, vecs, issues, shape
    if ext in MD_EXTS:
        text, _ = read_text(full)
        meta = {"path": rel, "kind": "markdown", "ext": ext, "size_bytes": size}
        return meta, [], [], "text"
    if ext in BINARY_HINT_EXTS:
        meta = {"path": rel, "kind": "binary_hint", "ext": ext, "size_bytes": size}
        issue = {"kind": "binary_not_textualized", "locator": rel,
                 "detail": "%s 需先导出为 CSV/文本（PDF→pdftotext，XLSX/MAT→pandas/scipy）再画像" % ext}
        return meta, [], [issue], "unknown"
    text, _ = read_text(full)
    if text is None:
        meta = {"path": rel, "kind": "other_unreadable", "ext": ext, "size_bytes": size}
        return meta, [], [{"kind": "unreadable", "locator": rel,
                           "detail": "无法读取"}], "unknown"
    meta = {"path": rel, "kind": "other_text", "ext": ext, "size_bytes": size,
            "chars": len(text)}
    return meta, [], [], "text"


def render_profile_md(p):
    lines = ["# 数据画像", "",
             "- 数据形态: %s" % p["shape"],
             "- 文件数: %d；数值向量数: %d；问题数: %d" % (
                 len(p["files"]), len(p["vectors"]), len(p["issues"])),
             ""]
    lines.append("## 文件")
    for f in p["files"]:
        extra = []
        for k in ("kind", "ext", "rows", "columns", "numeric_columns"):
            if k in f and f[k] not in (None, ""):
                extra.append("%s=%s" % (k, f[k]))
        lines.append("- %s（%s）" % (f["path"], "，".join(extra) if extra else "—"))
    lines.append("")
    lines.append("## 数值向量（截断）")
    for v in p["vectors"][:80]:
        o = v.get("outliers", {})
        outlier_n = len(o.get("z", [])) + len(o.get("iqr", [])) + len(o.get("mad", []))
        lines.append(
            "- %s | %s | n=%s | mean=%s | std=%s | min=%s | max=%s | z/i/m离群=%d | 饱和=%s | 超量程=%s" % (
                v["file"], v["column"], v["n"], v.get("mean"), v.get("std"),
                v.get("min"), v.get("max"), outlier_n,
                v.get("saturated"), v.get("over_range")))
    if len(p["vectors"]) > 80:
        lines.append("- …（其余 %d 个向量见 data_profile.json）" % (len(p["vectors"]) - 80))
    lines.append("")
    lines.append("## 问题")
    for it in p["issues"][:100]:
        lines.append("- [%s] %s: %s" % (it["kind"], it["locator"], it["detail"]))
    if not p["issues"]:
        lines.append("- （无）")
    lines.append("")
    return "\n".join(lines) + "\n"


def profile_dir(input_dir, work_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    seen = set()
    file_metas = []
    all_vectors = []
    all_issues = []
    shapes = []
    for root in (input_dir, work_dir):
        if not os.path.isdir(root):
            continue
        for f in discover_files(root):
            if f["rel"] in seen:
                continue
            seen.add(f["rel"])
            origin = "input" if root == input_dir else "work"
            meta, vecs, issues, shape = profile_file(f["path"], f["rel"],
                                                     f["size_bytes"], f["ext"])
            meta["origin"] = origin
            file_metas.append(meta)
            all_vectors.extend(vecs)
            all_issues.extend(issues)
            if shape in ("trace", "tabular", "experiment"):
                shapes.append(shape)
    if shapes:
        uniq = set(shapes)
        if len(uniq) == 1:
            global_shape = shapes[0]
        elif "experiment" in uniq:
            global_shape = "mixed"
        elif "trace" in uniq and "tabular" in uniq:
            global_shape = "trace+tabular"
        else:
            global_shape = "mixed"
    else:
        global_shape = "unknown"
    profile = {
        "profile_version": "1.1.0",
        "generated_at": now_ts(),
        "input_dir": input_dir,
        "shape": global_shape,
        "files": file_metas,
        "vectors": all_vectors,
        "issues": all_issues,
    }
    write_json_atomic(os.path.join(output_dir, "data_profile.json"), profile)
    write_text_atomic(os.path.join(output_dir, "data_profile.md"), render_profile_md(profile))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="profile.py", description="数据清点与画像")
    p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")
    args = p.parse_args(argv)
    sys.exit(profile_dir(args.input, args.work, args.output))


if __name__ == "__main__":
    main()
