#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心实现（仅标准库）：输入发现、契约提取、数据画像、FigureIR、四层校验。

与"直接写图"的参考流水线不同，本实现以声明式中间表示 FigureIR 为中心：
  profile 先把 CSV/SQLite 解析为 data_profile，再按数据结构与分析目的生成
  chart 推荐（推荐理由与"不推荐某图型的原因"都落在 IR 里）；
  check 按 L1 数据契约 -> L2 IR schema -> L3 渲染产物 -> L4 result.json 的顺序校验。
任何命令都不抛未捕获异常，保证 Harness 不被中断。
"""
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import statistics
import struct
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件读取上限
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log",
                 "errors", "degradations", "runtime", "artifacts")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")
CONTRACT_NORM = ("outputcontract", "resultcontract", "contract",
                 "resultschema", "expectedoutput")
FALLBACK_CONTRACT = {"analysis": None, "quality_reproducibility": None, "anomalies": None}
NA_TOKENS = {"", "na", "nan", "null", "none", "n/a", "na/", "missing", "-", "?",
             "not reported", "not_reported"}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级到 gb18030/latin-1；返回 (text, encoding, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d > %d)" % (size, limit)
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = _mkstemp(d, os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


def _mkstemp(dirpath, suffix):
    # 用标准库 tempfile 生成临时文件（避免与 Windows 命名冲突）
    import tempfile
    return tempfile.mkstemp(dir=dirpath, prefix=".tmp_", suffix=suffix)


def load_json_robust(path):
    text, _enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e


def write_minimal_result(output_dir, status, errors):
    try:
        os.makedirs(output_dir, exist_ok=True)
        result = {
            "case_id": "unknown", "status": status, "sources": [], "uncertainty": [],
            "change_log": [{"ts": utcnow_iso(), "seq": 0, "step": "fallback",
                            "action": "降级落盘最小合法 result.json", "files": ["result.json"]}],
            "errors": errors or [], "degradations": [],
            "runtime": {"started_at": utcnow_iso(), "seconds": None, "finished_at": None},
            "artifacts": [],
        }
        atomic_write_json(os.path.join(output_dir, "result.json"), result)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    files = {"case_json": None, "task_md": None, "materials": [], "data": []}
    deg = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            base = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                deg.append("unreadable: %s (%s)" % (rel, e))
                continue
            if size > MAX_FILE_BYTES:
                deg.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
                continue
            if base == "case.json":
                files["case_json"] = p
            elif base == "task.md":
                files["task_md"] = p
            elif base.endswith(DATA_EXTS):
                files["data"].append(p)
            elif base.endswith(TEXT_EXTS):
                files["materials"].append(p)
    return files, deg


# --------------------------------------------------------------------------- #
# 契约提取
# --------------------------------------------------------------------------- #
def _norm_key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def nullify(value):
    """把契约模板的叶值置 null、列表置 []，作为回填骨架。"""
    if isinstance(value, dict):
        return {k: nullify(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def find_contract(obj):
    """在 case.json 中查找 output_contract/contract/schema 语义字段，返回 nullify 后的模板。"""
    if not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if _norm_key(k) in CONTRACT_NORM:
            return nullify(v)
    for k, v in obj.items():
        if isinstance(v, dict):
            found = find_contract(v)
            if found is not None:
                return found
    return None


def _extract_json_after(text, marker):
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[j:k + 1])
                except json.JSONDecodeError:
                    return None
    return None


def extract_contract(case, task_md_path=None):
    if isinstance(case, dict) and case:
        c = find_contract(case)
        if c is not None:
            return c
    if task_md_path:
        text, _enc, err = read_text_robust(task_md_path)
        if not err:
            c = _extract_json_after(text, "output_contract") or _extract_json_after(text, "outputContract")
            if isinstance(c, dict):
                return nullify(c)
    return dict(FALLBACK_CONTRACT)


def build_case_id(case, input_dir):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id"):
            v = case.get(k)
            if v:
                return str(v)
    name = os.path.basename(os.path.abspath(input_dir or "."))
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return "%s-%s" % (name or "case", digest)


def build_result_skeleton(case_id, contract):
    contract = contract or {}
    result = {"case_id": case_id}
    for k, v in contract.items():
        result[k] = v
    now = utcnow_iso()
    result["case_id"] = case_id
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["runtime"] = {"started_at": now, "seconds": None, "finished_at": None}
    result["artifacts"] = []
    collisions = [k for k in contract if k in RESERVED_KEYS]
    if collisions:
        result["uncertainty"].append({"item": "reserved_key_collision",
                                      "detail": "契约与保留键同名，按 Skill 语义覆盖: %s" % ", ".join(collisions)})
    result["change_log"].append({"ts": now, "seq": 0, "step": "init",
                                 "action": "落盘 partial 骨架，契约字段全部回填", "files": ["result.json"]})
    return result


def build_plan(result):
    lines = [
        "# 执行计划", "",
        "case_id: %s" % result.get("case_id"),
        "status: %s" % result.get("status"), "",
        "阶段 A：读输入、提取契约、落盘骨架（已完成）",
        "阶段 B：数据画像 -> 清洗口径 -> 统计 -> 选图 -> 编码 -> 标注 -> FigureIR -> 导出",
        "阶段 C：check 自检 -> 补齐证据/uncertainty -> 设 status -> runtime",
        "",
        "请求预算：A<=4，B<=45，C<=10，保留 5 次给失败恢复。",
        "校验顺序：L1 数据契约 -> L2 IR schema -> L3 渲染产物 -> L4 result.json。",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 数据画像
# --------------------------------------------------------------------------- #
def _is_missing(s):
    return s.strip().lower() in NA_TOKENS


def _is_number(s):
    s = s.strip()
    if s == "":
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _parse_datetime(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _numeric_stats(floats):
    n = len(floats)
    if n == 0:
        return {}
    q = None
    if n >= 4:
        try:
            q = statistics.quantiles(floats, n=4)
        except statistics.StatisticsError:
            q = None
    q1, med, q3 = q if q else (None, statistics.median(floats), None)
    iqr = (q3 - q1) if (q1 is not None and q3 is not None) else None
    lo = (q1 - 1.5 * iqr) if iqr is not None else None
    hi = (q3 + 1.5 * iqr) if iqr is not None else None
    outliers = 0
    if lo is not None and hi is not None:
        outliers = sum(1 for x in floats if x < lo or x > hi)
    return {
        "n": n,
        "mean": round(statistics.fmean(floats), 6),
        "std": round(statistics.stdev(floats), 6) if n >= 2 else 0.0,
        "min": min(floats), "max": max(floats),
        "q1": round(q1, 6) if q1 is not None else None,
        "median": round(med, 6) if med is not None else None,
        "q3": round(q3, 6) if q3 is not None else None,
        "outliers_iqr": outliers,
    }


def _infer_column(nonmissing):
    if not nonmissing:
        return "empty"
    n_num = sum(1 for v in nonmissing if _is_number(v))
    if n_num == len(nonmissing):
        return "numeric"
    n_dt = sum(1 for v in nonmissing if _parse_datetime(v))
    if n_dt == len(nonmissing):
        return "datetime"
    uniq = set(nonmissing)
    if len(uniq) <= 30 and len(uniq) / len(nonmissing) < 0.9:
        return "categorical"
    return "text"


def _profile_column(name, values):
    missing = sum(1 for v in values if _is_missing(v))
    nonmissing = [v.strip() for v in values if not _is_missing(v)]
    dtype = _infer_column(nonmissing)
    out = {"name": name, "dtype": dtype, "missing": missing,
           "unique": len(set(nonmissing))}
    if dtype == "numeric":
        floats = [float(v) for v in nonmissing]
        out["stats"] = _numeric_stats(floats)
        out["min"], out["max"] = min(floats), max(floats)
    elif dtype == "datetime":
        dts = [_parse_datetime(v) for v in nonmissing if _parse_datetime(v)]
        if dts:
            out["min"] = min(dts).isoformat()
            out["max"] = max(dts).isoformat()
    elif dtype == "categorical":
        from collections import Counter
        top = Counter(nonmissing).most_common(10)
        out["n_levels"] = len(set(nonmissing))
        out["levels"] = [{"value": k, "count": c} for k, c in top]
    return out


def _count_duplicates(rows):
    from collections import Counter
    seen = Counter()
    for r in rows:
        seen[tuple(r)] += 1
    dup = sum(v - 1 for v in seen.values() if v > 1)
    return {"duplicate_rows": dup, "unique_rows": len(seen)}


def profile_rows(name, kind, header, data):
    n_cols = len(header)
    columns = []
    for j in range(n_cols):
        values = []
        for r in data:
            if j < len(r):
                cell = r[j]
                values.append(cell if isinstance(cell, str) else ("" if cell is None else str(cell)))
            else:
                values.append("")
        columns.append(_profile_column(header[j], values))
    return {"name": name, "kind": kind, "n_rows": len(data), "n_cols": n_cols,
            "columns": columns, "duplicates": _count_duplicates(data), "header_present": True}


def _sniff_delim(text):
    try:
        return csv.Sniffer().sniff(text[:8192], delimiters=",\t;|").delimiter
    except csv.Error:
        return ","


def _has_header(rows):
    if len(rows) < 2:
        return False

    def allnum(r):
        cells = [c for c in r if c.strip() != ""]
        return bool(cells) and all(_is_number(c) for c in cells)
    return not (allnum(rows[0]) and allnum(rows[1]))


def profile_csv(name, text):
    if not text.strip():
        return {"name": name, "kind": "csv", "error": "empty_file",
                "n_rows": 0, "n_cols": 0, "columns": [], "duplicates": {"duplicate_rows": 0, "unique_rows": 0}}
    delim = _sniff_delim(text)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    except csv.Error as e:
        return {"name": name, "kind": "csv", "error": "csv_parse: %s" % e,
                "n_rows": 0, "n_cols": 0, "columns": []}
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return {"name": name, "kind": "csv", "error": "no_data_rows",
                "n_rows": 0, "n_cols": 0, "columns": [], "duplicates": {"duplicate_rows": 0, "unique_rows": 0}}
    has_header = _has_header(rows)
    if has_header:
        header = [(c or "").strip() or ("col%d" % (i + 1)) for i, c in enumerate(rows[0])]
        data = rows[1:]
    else:
        header = ["col%d" % (i + 1) for i in range(len(rows[0]))]
        data = rows
    n_cols = max(len(r) for r in data) if data else len(header)
    if len(header) < n_cols:
        header = header + ["col%d" % (i + 1) for i in range(len(header), n_cols)]
    header = header[:n_cols]
    return profile_rows(name, "csv", header, data)


def read_sqlite_tables(path):
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as e:
        return [], "sqlite_open_error: %s" % e
    try:
        cur = conn.cursor()
        tabs = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = []
        for t in tabs:
            cols = [r[1] for r in cur.execute('PRAGMA table_info("%s")' % t)]
            rows = cur.execute('SELECT * FROM "%s" LIMIT 200000' % t).fetchall()
            out.append(profile_rows(t, "sqlite_table", cols, rows))
        return out, None
    except sqlite3.Error as e:
        return [], "sqlite_read_error: %s" % e
    finally:
        conn.close()


def build_profile(files):
    tables = []
    deg = []
    for p in files.get("data", []):
        base = os.path.basename(p)
        if base.lower().endswith((".db", ".sqlite", ".sqlite3")):
            ts, err = read_sqlite_tables(p)
            if err:
                deg.append("%s: %s" % (base, err))
            else:
                tables.extend(ts)
        else:
            text, _enc, err = read_text_robust(p)
            if err:
                deg.append("%s: %s" % (base, err))
            else:
                tables.append(profile_csv(base, text))
    for p in files.get("materials", []):
        base = os.path.basename(p)
        if base.lower().endswith((".csv", ".tsv", ".txt")):
            text, _enc, err = read_text_robust(p)
            if err:
                deg.append("%s: %s" % (base, err))
            else:
                if not any(t.get("name") == base for t in tables):
                    tables.append(profile_csv(base, text))
    return {"tables": tables, "degradations": deg}


# --------------------------------------------------------------------------- #
# FigureIR：选图推荐 + 中间表示
# --------------------------------------------------------------------------- #
def build_recommendations(profile):
    recs = []
    for table in profile.get("tables", []):
        cols = table.get("columns", [])
        num = [c for c in cols if c.get("dtype") == "numeric"]
        cat = [c for c in cols if c.get("dtype") == "categorical"]
        for c in num[:3]:
            recs.append({"chart": "histogram", "purpose": "distribution",
                         "fields": [c["name"]],
                         "why": "连续变量分布", "reject": "pie(无序构成才考虑，分布用位置编码)"})
        for g in cat[:1]:
            for c in num[:3]:
                recs.append({"chart": "box", "purpose": "distribution_compare",
                             "fields": [g["name"], c["name"]],
                             "why": "无序分类分组比较连续变量（位置/分位数编码）",
                             "reject": "pie(角度比较失真)、grouped_bar(丢失分布)"})
        if len(num) >= 2:
            recs.append({"chart": "scatter", "purpose": "correlation",
                         "fields": [num[0]["name"], num[1]["name"]],
                         "why": "两个连续变量的相关/趋势",
                         "reject": "grouped_bar(丢失成对关系)"})
        if cat:
            recs.append({"chart": "stacked_bar", "purpose": "composition",
                         "fields": [cat[0]["name"]],
                         "why": "分类构成比例",
                         "reject": "pie(>4 类角度难比较，面积夸大差异)"})
    return recs


def build_ir(case_id, profile, task_hint=None):
    return {
        "case_id": case_id,
        "task_hint": task_hint,
        "data_profile": profile,
        "recommendations": build_recommendations(profile),
        "figures": [],
        "evidence_map": [],
    }


# --------------------------------------------------------------------------- #
# 四层校验
# --------------------------------------------------------------------------- #
def validate_ir(ir_obj):
    errs = []
    if not isinstance(ir_obj, dict):
        return ["ir.json 不是对象"]
    if not ir_obj.get("case_id"):
        errs.append("ir.json 缺少 case_id")
    figures = ir_obj.get("figures")
    if not isinstance(figures, list):
        errs.append("figures 不是列表")
        return errs
    for f in figures:
        fid = f.get("id", "?")
        for k in ("id", "chart", "purpose", "encodings", "annotations", "caption", "export"):
            if k not in f:
                errs.append("%s 缺少字段 %s" % (fid, k))
        encs = f.get("encodings", [])
        if not isinstance(encs, list) or not encs:
            errs.append("%s 缺少 encodings" % fid)
        else:
            for e in encs:
                src = e.get("source")
                if not isinstance(src, dict) or not src.get("locator") or not src.get("quote"):
                    errs.append("%s 的 encoding 缺少 source.locator/quote" % fid)
        ex = f.get("export", {})
        if isinstance(ex, dict):
            for k in ("svg", "pdf", "png", "dpi", "width_in", "font_min_pt"):
                if k not in ex:
                    errs.append("%s.export 缺少 %s" % (fid, k))
    return errs


def png_info(path):
    try:
        with open(path, "rb") as fh:
            sig = fh.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return None
            width = height = None
            dpi = None
            while True:
                chunk = fh.read(8)
                if len(chunk) < 8:
                    break
                length = struct.unpack(">I", chunk[:4])[0]
                ctype = chunk[4:8]
                data = fh.read(length)
                fh.read(4)
                if len(data) < length:
                    break
                if ctype == b"IHDR":
                    width, height = struct.unpack(">II", data[:8])
                elif ctype == b"pHYs" and length >= 9:
                    xppm, _yppm, unit = struct.unpack(">IIB", data[:9])
                    if unit == 1 and xppm:
                        dpi = round(xppm * 0.0254, 1)
            if width is None:
                return None
            return {"width": width, "height": height, "dpi": dpi}
    except OSError:
        return None


def check_artifacts(output_dir):
    import xml.etree.ElementTree as ET
    found = {"svg": [], "pdf": [], "png": []}
    if os.path.isdir(output_dir):
        for name in sorted(os.listdir(output_dir)):
            p = os.path.join(output_dir, name)
            if not os.path.isfile(p):
                continue
            l = name.lower()
            if l.endswith(".svg"):
                found["svg"].append(p)
            elif l.endswith(".pdf"):
                found["pdf"].append(p)
            elif l.endswith(".png"):
                found["png"].append(p)
    details = []
    status = "PASS"
    for p in found["svg"][:20]:
        try:
            ET.parse(p)
            details.append("SVG ok: %s" % os.path.basename(p))
        except Exception as e:
            details.append("SVG 无效: %s (%s)" % (os.path.basename(p), e))
            status = "FAIL"
    for p in found["pdf"][:20]:
        try:
            with open(p, "rb") as fh:
                head = fh.read(5)
            if head == b"%PDF-":
                details.append("PDF ok: %s" % os.path.basename(p))
            else:
                details.append("PDF 头无效: %s" % os.path.basename(p))
                status = "FAIL"
        except OSError as e:
            details.append("PDF 不可读: %s" % e)
            status = "FAIL"
    for p in found["png"][:20]:
        info = png_info(p)
        if info:
            dpi = info.get("dpi")
            if dpi is None or dpi < 300:
                if status == "PASS":
                    status = "WARN"
                dpi_s = ("%.0f dpi" % dpi) if dpi else "dpi 未嵌入(pHYs)"
                details.append("PNG 低于 300dpi: %s (%s, %dx%d)"
                               % (os.path.basename(p), dpi_s, info["width"], info["height"]))
            else:
                details.append("PNG ok: %s (%.0f dpi, %dx%d)"
                               % (os.path.basename(p), dpi, info["width"], info["height"]))
        else:
            details.append("PNG 无效: %s" % os.path.basename(p))
            status = "FAIL"
    if not (found["svg"] or found["pdf"] or found["png"]):
        details.append("output 下未发现 svg/pdf/png 产物")
        if status == "PASS":
            status = "WARN"
    return {"status": status, "detail": "; ".join(details) if details else "无渲染产物"}


def validate_result(result, require_contract=False):
    errs = []
    if not isinstance(result, dict):
        return ["result.json 不是对象"]
    if not result.get("case_id"):
        errs.append("缺少 case_id")
    if result.get("status") not in STATUS_VALUES:
        errs.append("status 非法: %r" % result.get("status"))
    for k in ("sources", "uncertainty", "change_log", "errors", "degradations", "runtime", "artifacts"):
        if k not in result:
            errs.append("缺少保留键 %s" % k)
    if require_contract:
        if not result.get("sources"):
            errs.append("require-contract: sources 为空（缺少证据映射）")
        if not result.get("artifacts"):
            errs.append("require-contract: artifacts 为空（缺少主产物）")
    return errs


def run_checks(result, output_dir, work_dir, require_contract=False):
    checks = []
    prof, _err = load_json_robust(os.path.join(work_dir, "profile.json"))
    if prof:
        tables = prof.get("tables", [])
        ok = bool(tables) and all(t.get("columns") for t in tables)
        checks.append({"id": "L1", "layer": "data_contract",
                       "status": "PASS" if ok else "FAIL",
                       "detail": "%d 张表完成数据画像" % len(tables)})
    else:
        checks.append({"id": "L1", "layer": "data_contract",
                       "status": "WARN", "detail": "profile.json 不存在，未做数据画像"})
    ir_obj, _err = load_json_robust(os.path.join(work_dir, "ir.json"))
    if ir_obj:
        errs = validate_ir(ir_obj)
        checks.append({"id": "L2", "layer": "ir_schema",
                       "status": "PASS" if not errs else "FAIL",
                       "detail": "; ".join(errs) if errs else "IR schema ok"})
    else:
        checks.append({"id": "L2", "layer": "ir_schema",
                       "status": "WARN", "detail": "ir.json 不存在"})
    art = check_artifacts(output_dir)
    checks.append({"id": "L3", "layer": "artifacts",
                   "status": art["status"], "detail": art["detail"]})
    errs = validate_result(result, require_contract=require_contract)
    checks.append({"id": "L4", "layer": "result_json",
                   "status": "PASS" if not errs else "FAIL",
                   "detail": "; ".join(errs) if errs else "result.json ok"})
    overall = "ok" if all(c["status"] != "FAIL" for c in checks) else "partial"
    return {"checks": checks, "status": overall}


def print_report(report):
    for c in report["checks"]:
        print("%s [%s] %s: %s" % (c["status"], c["id"], c["layer"], c["detail"]))
    print("overall: %s" % report["status"])
