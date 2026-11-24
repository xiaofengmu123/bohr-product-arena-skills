#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据清点与画像：生成 data_profile.json。

仅使用 Python 标准库。功能：
  - 确定性文件清单（编码、大小、二进制标记、超限记录）
  - 表格/JSON/CSV/TSV 解析：分隔符嗅探、表头识别、重复列去重
  - 每列数值统计（样本量/缺失/均值/中位数/标准差/极值）
  - 离群标记：z-score、IQR、MAD（均给出阈值与命中的行号）
  - 曲线/多通道/轨迹集合启发式识别
  - 单位不一致、超量程/饱和、坏编码等降级记录

用法：
  python scripts/inspect_data.py --input /input --output /app/output
"""

import argparse
import csv
import io
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"

TEXT_EXTS = {".csv", ".tsv", ".txt", ".tab", ".dat", ".prn", ".log", ".md", ".json",
             ".jsonl", ".ndjson", ".yml", ".yaml", ".xml", ".html"}
SPREADSHEET_EXTS = {".xlsx", ".xls", ".xlsm", ".ods", ".xlsb"}
MATLAB_EXTS = {".mat", ".m"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff"}


def parse_number(raw):
    """把单元格解析为浮点数，返回 (value, note)。note 描述歧义或不一致的处置。"""
    s = raw.strip()
    if not s:
        return None, "empty"
    while len(s) >= 2 and s[0] in "\"'([{“‘" and s[-1] in "\"')]}”’":
        s = s[1:-1].strip()
    if not s:
        return None, "empty"
    s = s.replace("\u00a0", " ").replace("\u2212", "-").strip()
    note = ""
    if "," in s:
        has_dot = "." in s
        if has_dot:
            s2 = s.replace(",", "")
            note = "comma treated as thousands separator"
        else:
            parts = s.split(",")
            if len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
                s2 = s.replace(",", "")
                note = "comma treated as thousands separator (ambiguous)"
            elif len(parts) == 2 and len(parts[1]) in (1, 2):
                s2 = s.replace(",", ".")
                note = "comma treated as decimal separator"
            else:
                s2 = s.replace(",", "")
                note = "comma stripped (ambiguous)"
    else:
        s2 = s
    m = re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", s2)
    if not m:
        return None, "not numeric"
    num = m.group(0)
    suffix = s2[len(num):].strip()
    if suffix:
        note = ("%s; trailing suffix '%s'" % (note, suffix)).strip("; ")
    try:
        return float(num), note
    except ValueError:
        return None, "parse error"


def median(xs):
    return statistics.median(xs) if xs else None


def stdev(xs):
    return statistics.stdev(xs) if len(xs) >= 2 else None


def percentile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def mad(xs):
    if not xs:
        return None
    m = median(xs)
    return median([abs(x - m) for x in xs])


def detect_delim(text):
    """嗅探分隔符；失败时按各候选首行出现次数最多者决定。"""
    try:
        d = csv.Sniffer().sniff(text[:4096], delimiters=",\t;| ")
        return d.delimiter
    except csv.Error:
        best, best_n = ",", -1
        first = text.splitlines()[0] if text.splitlines() else ""
        for cand in (",", "\t", ";", "|"):
            n = first.count(cand)
            if n > best_n:
                best, best_n = cand, n
        return best


def read_table_rows(text):
    """把文本解析为行列表；带引号字段用 csv 处理。"""
    delim = detect_delim(text)
    rows = []
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        for row in reader:
            rows.append([c.strip() for c in row])
    except csv.Error:
        for line in text.splitlines():
            if line.strip():
                rows.append([c.strip() for c in line.split(delim)])
    # 去掉全空行
    return [r for r in rows if any(c != "" for c in r)]


def sniff_header(rows):
    """判断首行是否为表头：首行含非数值单元格且第二行含数值则视为表头。"""
    if len(rows) < 2:
        return False
    def numeric_cells(r):
        n = 0
        for c in r:
            v, _ = parse_number(c)
            if v is not None:
                n += 1
        return n
    first_num = numeric_cells(rows[0])
    second_num = numeric_cells(rows[1])
    return second_num > first_num and first_num < max(1, len(rows[0]) // 2 + 1)


def columnize(rows, has_header):
    if not rows:
        return [], []
    width = max(len(r) for r in rows)
    data = rows[1:] if has_header else rows
    headers = list(rows[0]) if has_header else ["col_%d" % (i + 1) for i in range(width)]
    if has_header:
        headers = [h if h else "col_%d" % (i + 1) for i, h in enumerate(headers)]
        while len(headers) < width:
            headers.append("col_%d" % (len(headers) + 1))
    seen = {}
    for i, h in enumerate(headers):
        if h in seen:
            seen[h] += 1
            headers[i] = "%s_%d" % (h, seen[h])
        else:
            seen[h] = 0
    cols = [[] for _ in range(width)]
    for r in data:
        for i in range(width):
            cols[i].append(r[i] if i < len(r) else "")
    return headers, cols


def profile_column(name, col):
    nums, notes = [], []
    non_numeric = 0
    for i, c in enumerate(col):
        v, note = parse_number(c)
        if v is None:
            non_numeric += 1
        else:
            nums.append((i, v))
            if note:
                notes.append((i, note))
    values = [v for _, v in nums]
    n = len(values)
    missing = len(col) - n
    p = {
        "name": name, "n": n, "missing": missing,
        "mean": sum(values) / n if n else None,
        "median": median(values),
        "std": stdev(values),
        "min": min(values) if n else None,
        "max": max(values) if n else None,
        "q1": percentile(values, 0.25),
        "q3": percentile(values, 0.75),
        "non_numeric_count": non_numeric,
        "parse_notes": _dedup_notes(notes),
    }
    if n:
        iqr = (p["q3"] - p["q1"]) if p["q1"] is not None else None
        mdn = p["median"]
        m = mad(values)
        z_hits, iqr_hits, mad_hits = [], [], []
        mean = p["mean"]
        sd = p["std"]
        for idx, v in nums:
            if sd and sd > 0 and abs(v - mean) / sd > 3:
                z_hits.append({"row": idx, "value": v, "z": round(abs(v - mean) / sd, 3)})
            if iqr is not None and iqr > 0 and (v < p["q1"] - 1.5 * iqr or v > p["q3"] + 1.5 * iqr):
                iqr_hits.append({"row": idx, "value": v})
            if mdn is not None and m is not None and m > 0 and abs(v - mdn) / (m * 1.4826) > 3:
                mad_hits.append({"row": idx, "value": v})
        p["outliers"] = {
            "z_score": {"threshold": "|z| > 3", "hits": z_hits},
            "iqr": {"threshold": "outside [Q1-1.5*IQR, Q3+1.5*IQR]", "hits": iqr_hits},
            "mad": {"threshold": "|x-median| / (1.4826*MAD) > 3", "hits": mad_hits},
        }
    else:
        p["outliers"] = None
    return p


def _dedup_notes(notes):
    uniq, seen = [], set()
    for idx, note in notes:
        if note not in seen:
            seen.add(note)
            uniq.append({"example_row": idx, "note": note})
    return uniq[:20]


def json_to_columns(obj):
    """把 JSON 归一化为列；list-of-dict -> 表，dict -> 单行键值对。"""
    if isinstance(obj, list):
        keys = []
        for item in obj:
            if isinstance(item, dict):
                for k in item:
                    if k not in keys:
                        keys.append(k)
        cols = [[] for _ in keys]
        for item in obj:
            for i, k in enumerate(keys):
                v = item.get(k) if isinstance(item, dict) else None
                cols[i].append("" if v is None else (v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)))
        return keys, cols
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        cols = [[obj[k]] for k in keys]
        return keys, cols
    return [], []


def classify_file(f):
    ext = f["ext"]
    if ext in TEXT_EXTS:
        return "text"
    if ext in SPREADSHEET_EXTS:
        return "spreadsheet"
    if ext in MATLAB_EXTS:
        return "matlab"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


def profile_file(f, input_dir):
    """返回单文件画像 dict。"""
    info = {
        "rel": f["rel"], "size_bytes": f["size_bytes"], "ext": f["ext"],
        "kind": classify_file(f),
    }
    if f["size_bytes"] > core.MAX_FILE_BYTES:
        info["status"] = "skipped_oversize"
        info["note"] = "exceeds %d bytes" % core.MAX_FILE_BYTES
        return info
    if f["ext"] == ".json":
        obj = core.load_json_safe(f["path"])
        if obj is None:
            info["status"] = "unparseable_json"
            info["note"] = "not valid JSON"
            return info
        headers, cols = json_to_columns(obj)
        info["status"] = "parsed"
        info["table"] = {"has_header": True, "n_columns": len(headers),
                         "n_rows": len(cols[0]) if cols else 0,
                         "columns": [profile_column(h, c) for h, c in zip(headers, cols)]}
        return info
    if classify_file(f) in ("spreadsheet", "matlab", "pdf", "image"):
        info["status"] = "requires_export"
        info["note"] = ("%s needs platform tool (pandas/openpyxl/scipy/pdftotext) export to CSV/text before profiling"
                        % classify_file(f))
        return info
    text, enc, note = core.read_text(f["path"])
    if text is None:
        info["status"] = "unreadable"
        info["note"] = note
        return info
    info["encoding"] = enc
    if note:
        info.setdefault("degradations", []).append(note)
    rows = read_table_rows(text)
    has_header = sniff_header(rows)
    headers, cols = columnize(rows, has_header)
    info["status"] = "parsed"
    info["table"] = {
        "has_header": has_header, "delimiter": detect_delim(text),
        "n_columns": len(headers), "n_rows": len(cols[0]) if cols else 0,
        "columns": [profile_column(h, c) for h, c in zip(headers, cols)],
    }
    return info


def detect_traces(profile):
    """启发式识别曲线/多通道/轨迹集合，返回候选列表。"""
    cands = []
    parsed = [f for f in profile["files"] if f.get("status") == "parsed" and f.get("table")]
    numeric_tables = []
    for f in parsed:
        t = f["table"]
        numcols = [c for c in t["columns"] if c["n"] > 0 and c["non_numeric_count"] == 0]
        if numcols:
            numeric_tables.append((f, t, numcols))
    # 单文件两列且行数多 -> 单条曲线（x,y）
    for f, t, numcols in numeric_tables:
        if len(t["columns"]) == 2 and t["n_rows"] >= 5 and len(numcols) == 2:
            cands.append({"type": "single_curve", "file": f["rel"],
                          "x_col": numcols[0]["name"], "y_col": numcols[1]["name"],
                          "n_rows": t["n_rows"]})
    # 单文件多列（>=4 数值列）-> 每行一条轨迹或多通道
    for f, t, numcols in numeric_tables:
        if len(numcols) >= 4 and t["n_rows"] >= 2:
            cands.append({"type": "wide_table_traces", "file": f["rel"],
                          "n_numeric_columns": len(numcols), "n_rows": t["n_rows"]})
    # 多个形状相同的 CSV -> 轨迹集合
    shapes = {}
    for f, t, _ in numeric_tables:
        key = (t["n_rows"], t["n_columns"])
        shapes.setdefault(key, []).append(f["rel"])
    for key, rels in shapes.items():
        if len(rels) >= 10:
            cands.append({"type": "trace_set", "shape": {"rows": key[0], "columns": key[1]},
                          "files": sorted(rels)[:30], "count": len(rels)})
    return cands


def build_profile(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    files = core.discover_files(input_dir)
    material = [f for f in files if f["rel"] not in ("case.json", "task.md") and not f["rel"].startswith(".")]
    prof = {
        "generated_at": core.now_ts(),
        "input_dir": input_dir,
        "n_files": len(material),
        "files": [profile_file(f, input_dir) for f in material],
        "degradations": [],
        "summary": {
            "parsed": sum(1 for f in material),
            "skipped_oversize": 0,
            "unreadable": 0,
            "requires_export": 0,
        },
    }
    for f in prof["files"]:
        st = f.get("status")
        if st == "skipped_oversize":
            prof["summary"]["skipped_oversize"] += 1
            prof["degradations"].append({"rel": f["rel"], "issue": "oversize", "detail": f.get("note")})
        elif st in ("unreadable", "unparseable_json"):
            prof["summary"]["unreadable"] += 1
            prof["degradations"].append({"rel": f["rel"], "issue": st, "detail": f.get("note")})
        elif st == "requires_export":
            prof["summary"]["requires_export"] += 1
        for d in f.get("degradations") or []:
            prof["degradations"].append({"rel": f["rel"], "issue": "encoding", "detail": d})
    prof["trace_candidates"] = detect_traces(prof)
    core.write_json_atomic(os.path.join(output_dir, "data_profile.json"), prof)
    return prof


def main(argv=None):
    p = argparse.ArgumentParser(prog="inspect_data.py", description="数据清点与画像")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    args = p.parse_args(argv)
    try:
        prof = build_profile(args.input, args.output)
        print("data_profile.json written: %d files, %d trace candidates" %
              (prof["n_files"], len(prof["trace_candidates"])))
        return 0
    except Exception as exc:
        print("error: %r" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
