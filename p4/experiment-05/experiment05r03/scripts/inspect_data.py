#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验材料画像：确定性扫描 /input，产出 data_profile.json。

覆盖 CSV/TSV/JSON/纯文本；XLSX/XLS/MAT 由平台 pandas/openpyxl 先导出 CSV 后再画像
（本脚本只用标准库，遇到二进制工作簿仅标记 "convert_first"，不臆测内容）。

画像内容（全部确定性：文件排序、列排序、固定舍入、无随机、无墙钟时间戳）：
  - 文件清单：rel/size/sha256/状态
  - 每列：样本量、缺失、均值/中位数/标准差、min/max、z-score/IQR/MAD 离群计数
  - 单位不一致、超量程/饱和、坏编码提示
  - 数据形态判定（curve_trace / tabular / ml_validation / workbook_paper / mixed）

用法：
  python scripts/inspect_data.py --input /input --output /app/output
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import statistics
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
MAX_FILE_BYTES = 2_000_000
PROFILE_VERSION = "1.0.0"

TEXT_EXTS = {".csv", ".tsv", ".txt", ".json", ".jsonl", ".md", ".log", ".dat", ".out"}


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def read_text(path, max_bytes=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > max_bytes:
        return None, "exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    return None, "unreadable encoding"


def _round(x, nd=6):
    if x is None:
        return None
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _is_number(s):
    if s is None:
        return False
    s = str(s).strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def sniff_delimiter(text):
    """确定性分隔符判定：优先制表符、分号、逗号；由 csv.Sniffer 校正。"""
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        counts = {",": 0, "\t": 0, ";": 0, "|": 0}
        for line in text.splitlines()[:50]:
            for ch in counts:
                counts[ch] += line.count(ch)
        best = max(counts, key=lambda k: counts[k])
        return best if counts[best] > 0 else ","


def profile_csv(text):
    delim = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    try:
        for r in reader:
            rows.append([None if c.strip() == "" else c.strip() for c in r])
    except csv.Error:
        return None, "csv parse error"
    if not rows:
        return {"delimiter": delim, "n_rows": 0, "columns": []}, None
    header = rows[0]
    ncol = max(len(r) for r in rows) if rows else 0
    if ncol == 0:
        return {"delimiter": delim, "n_rows": 0, "columns": []}, None
    # 无表头时用 col_0..col_N
    has_header = True
    numeric_first = all(_is_number(c) for c in rows[0] if c is not None)
    if numeric_first:
        has_header = False
        header = ["col_%d" % i for i in range(ncol)]
        body = rows
    else:
        body = rows[1:]
    cols = []
    for j in range(ncol):
        vals = [r[j] if j < len(r) else None for r in body]
        nonnull = [v for v in vals if v is not None]
        numeric = [_num(v) for v in nonnull if _is_number(v)]
        col = {"name": header[j] if j < len(header) else "col_%d" % j,
               "index": j, "n": len(vals), "non_null": len(nonnull),
               "missing": len(vals) - len(nonnull),
               "dtype": "numeric" if len(numeric) >= max(1, 0.8 * len(nonnull)) else "text"}
        if numeric:
            col["n_numeric"] = len(numeric)
            col["mean"] = _round(statistics.fmean(numeric))
            col["median"] = _round(statistics.median(numeric))
            col["std"] = _round(statistics.pstdev(numeric)) if len(numeric) > 1 else 0.0
            col["min"] = _round(min(numeric))
            col["max"] = _round(max(numeric))
            # z-score 离群（阈值 3）
            sd = statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
            mn = statistics.fmean(numeric)
            z_out = sum(1 for v in numeric if sd > 0 and abs(v - mn) / sd > 3)
            # IQR 离群（阈值 1.5*IQR）
            q = statistics.quantiles(numeric, n=4, method="inclusive")
            iqr = q[2] - q[0]
            iqr_out = sum(1 for v in numeric if iqr > 0 and (v < q[0] - 1.5 * iqr or v > q[2] + 1.5 * iqr))
            # MAD 离群（阈值 3.5*MAD，中位数绝对偏差）
            med = statistics.median(numeric)
            mad = statistics.median([abs(v - med) for v in numeric])
            mad_out = sum(1 for v in numeric if mad > 0 and abs(v - med) / mad > 3.5)
            col["outliers"] = {"z_score_gt3": z_out, "iqr_gt1p5": iqr_out, "mad_gt3p5": mad_out,
                               "thresholds": {"z_score": 3.0, "iqr": 1.5, "mad": 3.5}}
        cols.append(col)
    return {"delimiter": delim, "n_rows": len(rows), "n_data_rows": len(body),
            "has_header": has_header, "columns": cols}, None


def profile_json(text):
    try:
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, "json parse error: %s" % exc
    def summarize(o, depth=0):
        if depth > 4:
            return "…"
        if isinstance(o, dict):
            keys = sorted(o.keys())
            return {"_type": "object", "n_keys": len(keys),
                    "keys_sample": keys[:20],
                    "children": {k: summarize(o[k], depth + 1) for k in keys[:20]}}
        if isinstance(o, list):
            n = len(o)
            sample = [summarize(x, depth + 1) for x in o[:5]]
            return {"_type": "array", "n": n, "items_sample": sample}
        if isinstance(o, (int, float)):
            return {"_type": "number", "value": o}
        if isinstance(o, bool):
            return {"_type": "bool", "value": o}
        if o is None:
            return {"_type": "null"}
        s = str(o)
        return {"_type": "string", "len": len(s), "value": s[:120]}
    return {"json_top": summarize(obj)}, None


def detect_mode(files):
    """确定性数据形态判定，只作提示不作结论。"""
    csvs = [f for f in files if f.get("ext") in (".csv", ".tsv", ".txt")]
    xlsx = [f for f in files if f.get("ext") in (".xlsx", ".xls", ".xlsm")]
    mats = [f for f in files if f.get("ext") in (".mat", ".m")]
    pdfs = [f for f in files if f.get("ext") == ".pdf"]
    rationale = []
    mode = "unknown"
    conf = "low"
    if mats and csvs and len(csvs) >= 2:
        mode = "curve_trace"
        conf = "high"
        rationale.append("存在 MATLAB 工件 + 多个数值表格，疑似原始曲线/弯曲迹线批量数据。")
    elif len(csvs) >= 3:
        mode = "curve_trace"
        conf = "medium"
        rationale.append("多个数值表格，疑似批量曲线/迹线；需结合列结构与对照列确认。")
    elif xlsx and pdfs:
        mode = "workbook_paper"
        conf = "high"
        rationale.append("存在多页工作簿 + 论文 PDF，疑似制剂/药学研究；工作簿需先导出 CSV。")
    elif len(csvs) == 1 and len(pdfs) == 0:
        mode = "tabular"
        conf = "medium"
        rationale.append("单一表格，疑似普通表格式实验结果。")
    if not files:
        mode = "unknown"
        conf = "high"
        rationale.append("无可读文件。")
    return {"mode": mode, "confidence": conf, "rationale": rationale}


def unit_inconsistency_hints(files):
    hints = []
    unit_re = re.compile(r"(mg|µg|μg|ng|g|ml|µl|μl|l|mm|µm|μm|nm|cm|s|min|h|%|M|mM|µM|μM|nM|Pa|MPa|GPa|N|Hz|kHz|MHz|°C|K)\b", re.I)
    for f in files:
        if f.get("ext") not in (".md", ".txt", ".csv", ".tsv"):
            continue
        text, enc = read_text(f["path"])
        if not text:
            continue
        units = sorted({m.group(0) for m in unit_re.finditer(text[:20000])})
        if len(units) >= 4:
            hints.append({"rel": f["rel"], "units_seen": units,
                          "note": "同一材料出现多种单位，需保留原始单位并核对是否同一量纲。"})
    return hints


def saturation_hints(files):
    hints = []
    for f in files:
        if f.get("ext") not in (".csv", ".tsv", ".txt"):
            continue
        text, enc = read_text(f["path"])
        if not text:
            continue
        for i, line in enumerate(text.splitlines()[:5000]):
            for token in ("OVER", "OL", "saturated", "satur", "out of range", ">999", "999999", "Inf", "NaN"):
                if token in line:
                    hints.append({"rel": f["rel"], "line": i + 1,
                                  "note": "疑似超量程/饱和/溢出标记：%s" % token})
                    break
    return hints


def profile_input(input_dir):
    files = []
    for dirpath, dirnames, filenames in os.walk(input_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            if rel.startswith(".tmp_"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({"rel": rel, "path": full, "ext": ext, "size_bytes": size,
                          "sha256": sha256_file(full)})

    enc_issues = []
    profiled = []
    for f in files:
        entry = {"rel": f["rel"], "ext": f["ext"], "size_bytes": f["size_bytes"],
                 "sha256": f["sha256"], "status": "skipped", "reason": None, "profile": None}
        if f["size_bytes"] > MAX_FILE_BYTES:
            entry["status"] = "skipped"
            entry["reason"] = "exceeds %d bytes" % MAX_FILE_BYTES
            profiled.append(entry)
            continue
        if f["ext"] in (".xlsx", ".xls", ".xlsm"):
            entry["status"] = "convert_first"
            entry["reason"] = "binary workbook: run pandas.to_csv() first"
            profiled.append(entry)
            continue
        if f["ext"] in (".mat", ".m"):
            entry["status"] = "convert_first"
            entry["reason"] = "MATLAB artifact: export numeric traces to CSV first"
            profiled.append(entry)
            continue
        if f["ext"] == ".pdf":
            entry["status"] = "external"
            entry["reason"] = "use pdftotext / platform PDF tool for text layer"
            profiled.append(entry)
            continue
        if f["ext"] not in TEXT_EXTS:
            entry["status"] = "skipped"
            entry["reason"] = "non-text extension"
            profiled.append(entry)
            continue
        text, enc = read_text(f["path"])
        if text is None:
            entry["status"] = "unreadable"
            entry["reason"] = enc
            profiled.append(entry)
            continue
        if enc == "latin-1":
            enc_issues.append({"rel": f["rel"], "encoding": enc,
                               "note": "非 UTF-8，已 latin-1 降级读取"})
        if f["ext"] in (".csv", ".tsv", ".txt"):
            profile, err = profile_csv(text)
        elif f["ext"] in (".json", ".jsonl"):
            profile, err = profile_json(text)
        else:
            profile = {"n_lines": len(text.splitlines()), "n_chars": len(text)}
            err = None
        if err:
            entry["status"] = "unreadable"
            entry["reason"] = err
        else:
            entry["status"] = "profiled"
            entry["profile"] = profile
        profiled.append(entry)

    mode = detect_mode(files)
    unit_hints = unit_inconsistency_hints(files)
    sat_hints = saturation_hints(files)

    profile = {
        "profile_version": PROFILE_VERSION,
        "input_dir": input_dir,
        "n_files": len(files),
        "data_shape": mode,
        "files": profiled,
        "encoding_issues": enc_issues,
        "unit_inconsistencies": unit_hints,
        "saturation_hints": sat_hints,
        "determinism": {"sorting": "deterministic", "random_seed": None},
    }
    profile["profile_fingerprint"] = hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return profile


def main(argv=None):
    ap = argparse.ArgumentParser(description="实验材料确定性画像")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    profile = profile_input(args.input)
    os.makedirs(args.output, exist_ok=True)
    tmp = os.path.join(args.output, ".tmp_data_profile_%d.json" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, os.path.join(args.output, "data_profile.json"))
    print("inspect_data: wrote %s/data_profile.json (mode=%s, n_files=%d, fingerprint=%s)"
          % (args.output.rstrip("/\\"), profile["data_shape"]["mode"],
             profile["n_files"], profile["profile_fingerprint"][:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
