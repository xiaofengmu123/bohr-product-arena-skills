#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract_engine：本变体核心。仅依赖 Python 标准库。

职责：
1. 容错 IO（坏编码 / 坏 JSON / BOM / 超限文件不崩溃）。
2. 输入发现与 CSV/TSV/Markdown/空白表格解析、列型推断。
3. output_contract 归一化与逐叶子节点对齐（对齐审计 + 逐字段 change_log）。
4. 确定性可编辑 SVG 渲染（文字 <text>、图元带 data-* 溯源属性）。
5. 产物格式校验（魔数 / SVG 可解析与可编辑性）。

所有对外函数不抛出未捕获异常：失败返回 (None, errors) 或空结果，由调用方降级。
"""
from __future__ import annotations

import copy
import csv
import datetime as _dt
import io
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def utcnow_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

MAX_FILE_BYTES = 2_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"

CJK_FONT_STACK = (
    "'Noto Sans CJK SC','Noto Sans SC','Source Han Sans SC',"
    "'Microsoft YaHei','PingFang SC','SimSun','Arial Unicode MS',sans-serif"
)

# result.json 顶层保留键。契约字段若与保留键同名，只在 output_contract 内保留，
# 不平铺到顶层，避免覆盖 status/sources/change_log 等元数据。
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "limitations", "errors", "degradations", "change_log", "evidence",
    "mapping", "artifacts", "runtime", "contract_alignment",
    "output_contract", "determinism", "self_check", "contract_source",
    "contract_fields", "contract_declined", "evidence_map",
}

# 题目「Skill 运行后的输出要求」三类交付字段，仅在 input 缺 output_contract 时兜底。
DEFAULT_CONTRACT = {
    "editable_source": None,
    "exports": None,
    "source_and_change": None,
}


# --------------------------------------------------------------------------- #
# 容错 IO
# --------------------------------------------------------------------------- #
def read_bytes(path, max_bytes=MAX_FILE_BYTES):
    if not os.path.exists(path):
        return None, ["missing file: %s" % path]
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, ["stat failed: %s" % e]
    if size > max_bytes:
        return None, ["file too large (%dB > %dB): %s" % (size, max_bytes, path)]
    try:
        with open(path, "rb") as f:
            return f.read(), []
    except OSError as e:
        return None, ["read failed: %s" % e]


def decode_text(raw):
    if raw is None:
        return None
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    raw, errs = read_bytes(path, max_bytes)
    if errs:
        return None, errs
    return decode_text(raw), []


def load_json_tolerant(path, max_bytes=MAX_FILE_BYTES):
    raw, errs = read_bytes(path, max_bytes)
    if errs:
        return None, errs
    if raw is None or not raw.strip():
        return None, ["empty file: %s" % path]
    text = decode_text(raw)
    try:
        return json.loads(text), []
    except Exception as e:  # noqa: BLE001
        return None, ["invalid json: %s" % e]


def write_json(path, obj):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return []
    except OSError as e:
        return ["write failed %s: %s" % (path, e)]


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    materials, issues = [], []
    if not os.path.isdir(input_dir):
        return materials, ["input dir missing: %s" % input_dir]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        return materials, ["listdir failed: %s" % e]
    for name in names:
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        kind = {
            ".json": "json", ".md": "markdown", ".txt": "text",
            ".csv": "csv", ".tsv": "tsv", ".tab": "tsv",
            ".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg",
            ".svg": "svg", ".pptx": "pptx", ".xlsx": "xlsx", ".xls": "xls",
            ".docx": "docx", ".eps": "eps", ".tif": "tif", ".tiff": "tif",
        }.get(ext, "other")
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        materials.append({
            "path": p, "basename": name, "ext": ext or "", "kind": kind,
            "size": size,
            "parseable_here": kind in ("json", "csv", "tsv", "markdown", "text"),
        })
    return materials, issues


# --------------------------------------------------------------------------- #
# 表格解析
# --------------------------------------------------------------------------- #
def _guess_delimiter(text):
    first_line = ""
    for line in text.splitlines():
        if line.strip():
            first_line = line
            break
    if "|" in first_line and first_line.strip().lstrip("|").count("|") >= 1:
        return "markdown"
    counts = {d: first_line.count(d) for d in (",", "\t", ";")}
    best = max(counts, key=counts.get)
    if counts[best] >= 1:
        return {",": "csv", "\t": "tsv", ";": "semicolon"}[best]
    return "whitespace"


def _split_markdown_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _try_float(s):
    s = str(s).strip()
    if s == "":
        return None
    s2 = s.replace(",", "").replace("±", "").strip()
    try:
        return float(s2)
    except ValueError:
        return None


def parse_table(text, name="table"):
    issues = []
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return None, ["no non-empty lines in %s" % name]
    dialect = _guess_delimiter(text)
    rows = []
    if dialect == "markdown":
        for ln in lines:
            if re.match(r"^\s*\|?\s*:?-{2,}", ln):
                continue
            rows.append(_split_markdown_row(ln))
    elif dialect == "csv":
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as e:
            return None, ["csv parse failed: %s" % e]
    elif dialect == "tsv":
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        except csv.Error as e:
            return None, ["tsv parse failed: %s" % e]
    elif dialect == "semicolon":
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        except csv.Error as e:
            return None, ["csv parse failed: %s" % e]
    else:
        rows = [ln.split() for ln in lines]

    rows = [r for r in rows if any(c.strip() != "" for c in r)]
    if not rows:
        return None, ["no data rows in %s" % name]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    header, data = None, rows
    if _looks_like_header(rows[0]):
        header = rows[0]
        data = rows[1:]
        if len(header) != len(set(header)):
            issues.append("duplicate header names in %s" % name)
    return {
        "name": name, "dialect": dialect, "header": header, "rows": data,
        "n_rows": len(data), "n_cols": width,
    }, issues


def _looks_like_header(row):
    if not row:
        return False
    return all(_try_float(c) is None for c in row)


def infer_columns(table):
    if not table:
        return [], ["no table to infer"]
    header = table["header"] or ["col%d" % (i + 1) for i in range(table["n_cols"])]
    rows = table["rows"]
    cols, issues = [], []
    for j in range(table["n_cols"]):
        vals = [r[j] if j < len(r) else "" for r in rows]
        numeric_vals, missing, non_numeric = [], 0, 0
        for v in vals:
            if v.strip() == "":
                missing += 1
                continue
            f = _try_float(v)
            if f is None:
                non_numeric += 1
            else:
                numeric_vals.append(f)
        total = len(vals)
        kind = "numeric" if (non_numeric == 0 and numeric_vals) else (
            "categorical" if non_numeric > 0 else "empty")
        col = {
            "name": header[j] if j < len(header) else "col%d" % (j + 1),
            "index": j, "kind": kind, "missing": missing,
            "non_numeric": non_numeric, "n": total - missing,
        }
        if numeric_vals:
            col["min"] = min(numeric_vals)
            col["max"] = max(numeric_vals)
            col["mean"] = sum(numeric_vals) / len(numeric_vals)
            col["has_negative"] = any(x < 0 for x in numeric_vals)
            if col["min"] > 0 and col["max"] > 0:
                span = math.log10(col["max"] / col["min"])
                col["orders_of_magnitude"] = round(span, 2)
                if span > 3:
                    issues.append(
                        "col %r spans %.1f orders of magnitude; unit/magnitude suspect"
                        % (col["name"], span))
        cols.append(col)
    if table["header"] is None:
        issues.append("no header row detected in %s; using col1..colN" % table["name"])
    return cols, issues


# --------------------------------------------------------------------------- #
# output_contract 归一化 + 对齐（本变体核心）
# --------------------------------------------------------------------------- #
def normalize_contract(case):
    """从 case.json 抽取 output_contract，返回 (contract, source, issues)。

    缺失时用题目三类交付字段兜底；list-of-strings 转为 {field: None} 骨架。
    """
    issues = []
    if not isinstance(case, dict):
        return copy.deepcopy(DEFAULT_CONTRACT), "missing", ["case.json 不是对象，使用默认契约"]
    oc = case.get("output_contract")
    if oc is None:
        return copy.deepcopy(DEFAULT_CONTRACT), "default", [
            "case.json 未提供 output_contract，使用题目默认三类交付字段"]
    if isinstance(oc, dict):
        return copy.deepcopy(oc), "input:output_contract", []
    if isinstance(oc, list):
        if oc and all(isinstance(x, str) for x in oc):
            return {x: None for x in oc}, "input:output_contract(list)", []
        return copy.deepcopy(oc), "input:output_contract(list)", [
            "output_contract 为非字符串列表，按原结构对齐"]
    return copy.deepcopy(DEFAULT_CONTRACT), "invalid", [
        "output_contract 类型未知 %s，使用默认契约" % type(oc).__name__]


def iter_leaves(obj, path=()):
    """深度优先遍历契约，产出叶子节点描述。空 dict/list 视为叶子。"""
    if isinstance(obj, dict):
        if not obj:
            yield {"path": path, "key": "_empty", "raw_value": {}}
            return
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                yield from iter_leaves(v, path + (k,))
            else:
                yield {"path": path + (k,), "key": k, "raw_value": v}
    elif isinstance(obj, list):
        if not obj:
            yield {"path": path, "key": "_empty", "raw_value": []}
            return
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)) and v:
                yield from iter_leaves(v, path + ("[%d]" % i,))
            else:
                yield {"path": path + ("[%d]" % i,), "key": str(i), "raw_value": v}
    else:
        yield {"path": path, "key": str(path[-1]) if path else "value",
               "raw_value": obj}


def _set_path(obj, path, value):
    cur = obj
    for seg in path[:-1]:
        if isinstance(cur, dict):
            cur = cur.setdefault(seg, {})
        elif isinstance(cur, list) and seg.startswith("[") and seg.endswith("]"):
            idx = int(seg[1:-1])
            while len(cur) <= idx:
                cur.append({})
            cur = cur[idx]
        else:
            return
    last = path[-1]
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list) and last.startswith("[") and last.endswith("]"):
        idx = int(last[1:-1])
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value


def _resolve_kind(key):
    k = str(key).lower().replace("-", "_").replace(" ", "_")
    rules = (
        (r"change[_ ]?log|changelog|history|revision|修改记录|变更", "change_log"),
        (r"source[_ ]?and[_ ]?change|source[_ ]?change|来源与修改", "source_and_change"),
        (r"evidence|trace|provenance|溯源|证据", "evidence"),
        (r"mapping|映射", "mapping"),
        (r"pptx|ppt|slides|演示文稿", "pptx"),
        (r"eps", "eps"),
        (r"png|bitmap|raster|位图", "png"),
        (r"pdf", "pdf"),
        (r"svg|editable[_ ]?source|source[_ ]?file|vector|可编辑源|矢量", "svg"),
        (r"exports?|render|preview|导出", "exports"),
        (r"table|data|csv|series|source[_ ]?data|数据", "table_data"),
        (r"sources?$|来源", "sources"),
        (r"case[_ ]?id", "case_id"),
        (r"status", "status"),
    )
    for pat, kind in rules:
        if re.search(pat, k):
            return kind
    return "unknown"


def _is_placeholder(v):
    return v is None or v == "" or v == [] or v == {}


def _first_artifact(artifacts, kind):
    for a in artifacts:
        if a.get("kind") == kind:
            return a.get("path")
    return None


def resolve_leaf(kind, leaf, ctx):
    """返回 (value, fill_status, reason)。fill_status ∈ filled|null|object|preserved。"""
    raw = leaf["raw_value"]
    if not _is_placeholder(raw):
        return raw, "preserved", "保留输入契约中的非空原始值"

    artifacts = ctx.get("artifacts") or []
    change_log = ctx.get("change_log") or []
    if kind == "svg":
        v = _first_artifact(artifacts, "svg")
        return v, ("filled" if v else "null"), (
            "填充可编辑 SVG" if v else "未产出可编辑 SVG，需人工确认")
    if kind == "png":
        v = _first_artifact(artifacts, "png")
        return v, ("filled" if v else "null"), (
            "填充 PNG 导出件" if v else "未真实渲染 PNG（不伪造位图预览）")
    if kind == "pdf":
        v = _first_artifact(artifacts, "pdf")
        return v, ("filled" if v else "null"), (
            "填充 PDF 导出件" if v else "未真实渲染 PDF（不伪造预览）")
    if kind == "pptx":
        v = _first_artifact(artifacts, "office")
        return v, ("filled" if v else "null"), (
            "填充 PPTX/Office 源文件" if v else "未产出 PPTX，SVG 可作为可编辑源")
    if kind == "eps":
        return None, "null", "未产出 EPS（可编辑 SVG + PDF 已覆盖投稿需求）"
    if kind == "exports":
        obj = {}
        for k in ("svg", "png", "pdf", "pptx"):
            p = _first_artifact(artifacts, "svg" if k == "svg" else (
                "office" if k == "pptx" else k))
            if p:
                obj[k] = p
        if obj:
            return obj, "object", "按已有产物组装 exports 对象"
        return None, "null", "无任何导出产物，exports 置 null"
    if kind == "change_log":
        return {
            "ref": "result.change_log", "count": len(change_log),
            "steps": [e.get("step") for e in change_log],
        }, "object", "引用顶层 change_log，避免重复存储"
    if kind == "source_and_change":
        return {
            "tables": [{
                "name": t["table"]["name"], "n_rows": t["table"]["n_rows"],
                "n_cols": t["table"]["n_cols"],
            } for t in (ctx.get("tables") or [])],
            "sources": ctx.get("sources") or [],
            "change_log_ref": "result.change_log",
        }, "object", "组装来源与修改记录对象"
    if kind == "evidence":
        return copy.deepcopy(ctx.get("evidence") or []), "object", "填充证据溯源列表"
    if kind == "mapping":
        return copy.deepcopy(ctx.get("mapping") or {}), "object", "填充图元↔数据双向映射"
    if kind == "table_data":
        return {
            "tables": [{
                "name": t["table"]["name"], "n_rows": t["table"]["n_rows"],
                "n_cols": t["table"]["n_cols"],
                "numeric_cols": [c["name"] for c in t["columns"] if c["kind"] == "numeric"],
            } for t in (ctx.get("tables") or [])],
        }, "object", "填充解析出的表格摘要"
    if kind == "sources":
        return copy.deepcopy(ctx.get("sources") or []), "object", "填充来源清单"
    if kind == "case_id":
        return ctx.get("case_id"), "filled", "填充 case_id"
    if kind == "status":
        return None, "null", "status 由顶层 result.status 统一管理，不在此重复"
    return None, "null", "无法解析的契约键（%s），已记录 pending_human" % leaf["key"]


def align_contract(contract, ctx):
    """逐叶子节点对齐契约。返回 (filled, alignment, declined)。

    每个叶子写一条 change_log（ctx['change_log'] 就地追加），并在 alignment
    记录 change_log_index，使 result.json 与 output_contract 的字段一一可核验。
    """
    filled = copy.deepcopy(contract)
    alignment, declined = [], []
    change_log = ctx.setdefault("change_log", [])
    for leaf in iter_leaves(contract):
        kind = _resolve_kind(leaf["key"])
        value, status, reason = resolve_leaf(kind, leaf, ctx)
        _set_path(filled, leaf["path"], value)
        rec = {
            "path": "output_contract." + ".".join(leaf["path"]),
            "leaf_key": leaf["key"],
            "raw_value": leaf["raw_value"],
            "resolved_kind": kind,
            "fill_status": status,
            "value": value if status != "null" else None,
            "reason": reason,
        }
        idx = None
        if status in ("null", "preserved") or True:
            idx = len(change_log)
            change_log.append({
                "step": "S5_contract_align",
                "time_utc": utcnow_iso(),
                "status": "ok" if status in ("filled", "object", "preserved") else "partial",
                "note": "%s -> %s: %s" % (rec["path"], status, reason),
            })
        rec["change_log_index"] = idx
        alignment.append(rec)
        if status == "null":
            declined.append(rec)
    return filled, alignment, declined


def flat_mirror(filled):
    """仅当契约是单层 dict 时，把不与保留键冲突的字段平铺到 result 顶层。"""
    if not isinstance(filled, dict):
        return {}
    out = {}
    for k, v in filled.items():
        if k not in RESERVED_KEYS and not isinstance(v, (dict, list)):
            out[k] = v
    return out


def contract_leaf_keys(contract):
    return ["output_contract." + ".".join(l["path"]) for l in iter_leaves(contract)]


# --------------------------------------------------------------------------- #
# 确定性 SVG 渲染
# --------------------------------------------------------------------------- #
_FMT_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd",
    "#ff7f0e", "#8c564b", "#e377c2", "#7f7f7f",
]


def _nice_ticks(vmin, vmax, n=5):
    if vmax <= vmin:
        vmax = vmin + 1.0
    span = vmax - vmin
    step0 = span / max(1, n)
    mag = 10 ** math.floor(math.log10(step0 + 1e-300))
    step = mag
    for m in (1.0, 2.0, 5.0, 10.0):
        if step0 / mag <= m + 1e-12:
            step = m * mag
            break
    start = math.ceil(vmin / step - 1e-9) * step
    end = math.floor(vmax / step + 1e-9) * step
    ticks = []
    v = start
    while v <= end + step * 1e-6:
        ticks.append(round(v, 10))
        v += step
    return ticks, step


def _fmt_num(v):
    if v is None:
        return ""
    if abs(v) < 1e-12:
        return "0"
    if abs(v) >= 1e6 or abs(v) < 1e-3:
        return "%.3g" % v
    s = ("%.6f" % v).rstrip("0").rstrip(".")
    return s if s else "0"


def _esc(s):
    return escape(str(s) if s is not None else "")


def _qattr(s):
    return escape(str(s) if s is not None else "", {'"': "&quot;"})


def auto_figure_spec(table, cols, chart_type="bar"):
    if not table or not cols:
        return None, ["cannot build spec: no table"]
    issues = []
    cats = [c for c in cols if c["kind"] == "categorical"]
    nums = [c for c in cols if c["kind"] == "numeric"]
    if not nums:
        return None, ["no numeric column found; cannot encode values"]
    x_col = cats[0] if cats else None
    categories = []
    if x_col is not None:
        j = x_col["index"]
        categories = [r[j] if j < len(r) else "" for r in table["rows"]]
        categories = [c if c.strip() else "row%d" % (i + 1)
                      for i, c in enumerate(categories)]
    else:
        categories = ["row%d" % (i + 1) for i in range(table["n_rows"])]

    err_cols = {c["index"]: c for c in nums
                if re.search(r"(?i)(err|sd|sem|se|ci|±|error|_se$|_ci$)", c["name"])}
    value_cols = [c for c in nums if c["index"] not in err_cols] or nums
    series = []
    for c in value_cols:
        j = c["index"]
        vals = [_try_float(r[j] if j < len(r) else "") for r in table["rows"]]
        s = {
            "name": c["name"], "col": j, "values": vals,
            "errors": [None] * len(vals), "error_type": "SD", "n": None,
            "source_map": [{
                "series": c["name"], "category": categories[i],
                "source": table["name"], "row": i + 1, "col": j, "value": vals[i],
            } for i in range(len(vals))],
        }
        base = re.split(r"[._]", c["name"])[0].lower()
        for ei, ec in err_cols.items():
            if base and base in ec["name"].lower():
                s["errors"] = [_try_float(r[ei] if ei < len(r) else "")
                               for r in table["rows"]]
                s["error_type"] = ("SD" if "sd" in ec["name"].lower()
                                   else "SEM" if "sem" in ec["name"].lower() else "CI")
                break
        if all(e is None for e in s["errors"]):
            s["errors"] = [None] * len(vals)
        series.append(s)
        if any(v is None for v in vals):
            issues.append("series %r has missing values" % c["name"])

    return {
        "chart_type": chart_type, "title": table["name"],
        "x_label": x_col["name"] if x_col else "Row",
        "y_label": "Value", "categories": categories, "series": series,
        "significance": [], "width": 840, "height": 560,
        "dpi_note": "SVG is vector; PNG/PDF must be rendered separately",
    }, issues


def build_svg(spec):
    if not spec:
        return None, ["empty spec"]
    issues = []
    chart = spec.get("chart_type", "bar")
    W = float(spec.get("width", 840))
    H = float(spec.get("height", 560))
    ml, mr, mt, mb = 84, 30, 48, 66
    if spec.get("legend", True):
        mr = 150
    px, py = ml, mt
    pw = W - ml - mr
    ph = H - mt - mb

    cats = spec.get("categories") or ["c%d" % (i + 1) for i in range(1)]
    series = spec.get("series") or []
    if not series:
        return None, ["spec has no series"]
    n, m = len(cats), len(series)

    all_vals = [v for s in series for v in (s.get("values") or []) if v is not None]
    all_err = [e for s in series for e in (s.get("errors") or []) if e is not None]
    if not all_vals:
        return None, ["no numeric values to render"]
    ymax = max(all_vals) or 0
    if all_err:
        ymax = max(ymax, max((v + (e or 0)) for s in series
                             for v, e in zip(s.get("values") or [], s.get("errors") or [])
                             if v is not None))
    ymin = min(0.0, min(all_vals) if all_vals else 0.0)
    if ymax <= ymin:
        ymax = ymin + 1.0
    ymax = ymax * 1.12 + (ymax - ymin) * 0.02

    def sx(x):
        return px + (float(x) / (n + 1)) * pw if n > 0 else px

    def sy(y):
        return py + ph - (float(y) - ymin) / (ymax - ymin) * ph

    ticks, _ = _nice_ticks(ymin, ymax)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="%s">'
        % (int(W), int(H), int(W), int(H), CJK_FONT_STACK),
        '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (int(W), int(H)),
    ]
    if spec.get("title"):
        parts.append('<text x="%d" y="30" text-anchor="middle" font-size="16" '
                     'font-weight="bold">%s</text>' % (int(W / 2), _esc(spec["title"])))
    parts.append('<g id="plot-area" data-xmin="%s" data-xmax="%s" data-ymin="%s" '
                 'data-ymax="%s">' % (_fmt_num(0), _fmt_num(n + 1),
                                     _fmt_num(ymin), _fmt_num(ymax)))
    parts.append('<rect x="%s" y="%s" width="%s" height="%s" fill="none" '
                 'stroke="#333333" stroke-width="1"/>' % (px, py, pw, ph))
    for t in ticks:
        yy = sy(t)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#cccccc" '
                     'stroke-width="1"/>' % (px, yy, px + pw, yy))
        parts.append('<text x="%s" y="%s" text-anchor="end" font-size="12" '
                     'fill="#000000">%s</text>' % (px - 8, yy + 4, _esc(_fmt_num(t))))
    for i, c in enumerate(cats):
        cx = sx(i + 1)
        label = c if len(str(c)) <= 14 else str(c)[:13] + "…"
        parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="12" '
                     'fill="#000000" transform="rotate(-30 %s %s)">%s</text>'
                     % (cx, py + ph + 18, cx, py + ph + 18, _esc(label)))
    if spec.get("y_label"):
        parts.append('<text x="18" y="%s" text-anchor="middle" font-size="13" '
                     'transform="rotate(-90 18 %s)">%s</text>'
                     % (py + ph / 2, py + ph / 2, _esc(spec["y_label"])))
    if spec.get("x_label"):
        parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="13">%s</text>'
                     % (px + pw / 2, py + ph + 44, _esc(spec["x_label"])))

    bar_w = pw / (n * (m + 1)) if chart == "bar" else 0
    for si, s in enumerate(series):
        color = _FMT_COLORS[si % len(_FMT_COLORS)]
        sname = s.get("name", "series%d" % (si + 1))
        vals = s.get("values") or []
        errs = s.get("errors") or [None] * len(vals)
        smaps = s.get("source_map") or [{}] * len(vals)
        if chart == "bar":
            for i, v in enumerate(vals):
                if v is None:
                    continue
                x0 = px + (i + 1) * pw / (n + 1) - bar_w * (m / 2.0) + si * bar_w
                y0 = sy(v)
                ybase = sy(0) if ymin <= 0 <= ymax else sy(ymin)
                sm = smaps[i] if i < len(smaps) else {}
                parts.append(
                    '<rect class="bar" data-series="%s" data-category="%s" '
                    'data-source="%s" data-row="%s" data-col="%s" data-value="%s" '
                    'x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="#000000" '
                    'stroke-width="0.5"/>'
                    % (_qattr(sname), _qattr(cats[i] if i < len(cats) else i),
                       _qattr(sm.get("source", "")), _qattr(sm.get("row", "")),
                       _qattr(sm.get("col", "")), _qattr(_fmt_num(v)),
                       _qattr(round(x0, 2)), _qattr(round(y0, 2)),
                       _qattr(round(bar_w * 0.85, 2)), _qattr(round(abs(ybase - y0), 2)),
                       color))
            for i, (v, e) in enumerate(zip(vals, errs)):
                if v is None or e is None:
                    continue
                cx = px + (i + 1) * pw / (n + 1) - bar_w * (m / 2.0) + (si + 0.5) * bar_w
                ytop, ybot = sy(v + e), sy(v - e)
                parts.append('<line class="errorbar" data-series="%s" '
                             'data-error-type="%s" x1="%s" y1="%s" x2="%s" y2="%s" '
                             'stroke="#000000" stroke-width="1"/>'
                             % (_qattr(sname), _qattr(s.get("error_type", "")),
                                _qattr(round(cx, 2)), _qattr(round(ytop, 2)),
                                _qattr(round(cx, 2)), _qattr(round(ybot, 2))))
                parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" '
                             'stroke-width="1"/>' % (cx - 3, ytop, cx + 3, ytop))
                parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" '
                             'stroke-width="1"/>' % (cx - 3, ybot, cx + 3, ybot))
        elif chart in ("line", "scatter"):
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                cx, cy = sx(i + 1), sy(v)
                pts.append((cx, cy))
                sm = smaps[i] if i < len(smaps) else {}
                if chart == "scatter":
                    parts.append(
                        '<circle class="point" data-series="%s" data-category="%s" '
                        'data-source="%s" data-row="%s" data-col="%s" data-value="%s" '
                        'cx="%s" cy="%s" r="4" fill="%s" stroke="#000000" '
                        'stroke-width="0.5"/>'
                        % (_qattr(sname), _qattr(cats[i] if i < len(cats) else i),
                           _qattr(sm.get("source", "")), _qattr(sm.get("row", "")),
                           _qattr(sm.get("col", "")), _qattr(_fmt_num(v)),
                           _qattr(round(cx, 2)), _qattr(round(cy, 2)), color))
                if e is not None:
                    parts.append('<line class="errorbar" data-series="%s" '
                                 'data-error-type="%s" x1="%s" y1="%s" x2="%s" y2="%s" '
                                 'stroke="#000000" stroke-width="1"/>'
                                 % (_qattr(sname), _qattr(s.get("error_type", "")),
                                    _qattr(round(cx, 2)), _qattr(round(sy(v + e), 2)),
                                    _qattr(round(cx, 2)), _qattr(round(sy(v - e), 2))))
            if chart == "line" and pts:
                d = "M" + " L".join("%s %s" % (round(x, 2), round(y, 2)) for x, y in pts)
                parts.append('<path class="line" data-series="%s" d="%s" fill="none" '
                             'stroke="%s" stroke-width="2"/>' % (_qattr(sname), d, color))
                for cx, cy in pts:
                    parts.append('<circle cx="%s" cy="%s" r="3.5" fill="%s" '
                                 'stroke="#ffffff" stroke-width="1"/>'
                                 % (round(cx, 2), round(cy, 2), color))
        elif chart == "heatmap":
            values = s.get("values") or []
            vmax = max((v for v in values if v is not None), default=1.0)
            vmin = min((v for v in values if v is not None), default=0.0)
            for i, v in enumerate(values):
                if v is None:
                    continue
                t = 0.0 if vmax == vmin else (v - vmin) / (vmax - vmin)
                r, g = int(255 * (1 - t)), int(255 * (1 - t * 0.6))
                parts.append(
                    '<rect class="cell" data-series="%s" data-category="%s" '
                    'data-value="%s" x="%s" y="%s" width="%s" height="%s" '
                    'fill="#%02x%02x%02x" stroke="#ffffff"/>'
                    % (_qattr(sname), _qattr(cats[i] if i < len(cats) else i),
                       _qattr(_fmt_num(v)), _qattr(round(sx(i + 1) - 12, 2)),
                       _qattr(round(py, 2)), _qattr(round(24, 2)),
                       _qattr(round(ph, 2)), r, g, 255))
        if spec.get("legend", True):
            lx = px + pw + 20
            parts.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#ffffff" '
                         'stroke="#999999" stroke-width="0.5"/>'
                         % (lx - 10, py, mr - 10, max(24, 20 * m + 8)))
            for si, s in enumerate(series):
                color = _FMT_COLORS[si % len(_FMT_COLORS)]
                parts.append('<rect x="%s" y="%s" width="12" height="12" fill="%s"/>'
                             % (lx, py + 8 + si * 20, color))
                parts.append('<text x="%s" y="%s" font-size="12">%s</text>'
                             % (lx + 18, py + 19 + si * 20,
                                _esc(s.get("name", "series%d" % (si + 1)))))
    for sig in spec.get("significance") or []:
        i, j = sig.get("i"), sig.get("j")
        if i is None or j is None:
            continue
        yy = sy(sig.get("y", ymax * 0.95))
        x1, x2 = sx(i + 1), sx(j + 1)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" '
                     'stroke-width="1"/>' % (x1, yy, x2, yy))
        parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="13" '
                     'data-p="%s">%s</text>'
                     % ((x1 + x2) / 2, yy - 4, _qattr(sig.get("p", "")),
                        _esc(sig.get("mark", "*"))))
    parts.append('</g></svg>')
    return "\n".join(parts), issues


# --------------------------------------------------------------------------- #
# 产物校验
# --------------------------------------------------------------------------- #
def svg_has_text(svg_text):
    return "<text" in (svg_text or "")


def svg_parseable(svg_text):
    try:
        ET.fromstring(svg_text)
        return True
    except ET.ParseError:
        return False


def check_magic(path):
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError as e:
        return "unknown", False, "read failed: %s" % e
    if not head:
        return "unknown", False, "empty file"
    if head.startswith(PNG_MAGIC):
        return "png", True, "PNG magic ok"
    if head.startswith(PDF_MAGIC):
        return "pdf", True, "PDF magic ok"
    if head.startswith(ZIP_MAGIC):
        return "office", True, "ZIP/Office magic ok"
    text = head.decode("latin-1", errors="replace").lstrip()
    if text.lower().startswith("<?xml") or text.lower().startswith("<svg"):
        try:
            with open(path, "rb") as f:
                body = f.read(10_000_000)
        except OSError as e:
            return "svg", False, "read failed: %s" % e
        try:
            ET.fromstring(body.decode("utf-8", errors="replace"))
            return "svg", b"<text" in body, (
                "SVG parseable; text editable" if b"<text" in body
                else "SVG parseable but no <text>")
        except ET.ParseError as e:
            return "svg", False, "SVG parse failed: %s" % e
    return "unknown", False, "unrecognized magic"
