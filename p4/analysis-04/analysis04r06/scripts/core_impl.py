# -*- coding: utf-8 -*-
"""core_impl.py — 科研数据分析 Skill 的执行工具层（仅标准库）。

设计原则：规格先行、中间表示可复核（intermediate representations, IR）。
本工具不替 Agent 做统计判断，而是把"发现输入、落盘骨架、画像数据、渲染图表、
校验契约"这些容易写错、写死路径的环节固化成确定性、可重跑的脚本。

子命令（经 scripts/main.py 或直接 python -B core_impl.py 调用）：
  init           落盘一版合法的 result.json 骨架 + 空的 plan.json（前 3 分钟必做）
  profile        扫描 /input，产出 data_profile.json（编码/行列/缺失/数值/SQLite 结构）
  seed-plan      基于 profile + task.md 生成 plan.json 的字段对齐与步骤脚手架
  render-figure  读取 figure_model.json，确定性渲染 SVG + figure_render.json
  validate       对照 output_contract 校验 result.json，产出 validation_report.json

所有子命令都保证：缺参数、坏 JSON、坏编码、空目录时退出码 0，并写出可解释的
degradation 记录；绝不因单个坏文件崩溃。
"""

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 8 * 1024 * 1024
DICT_RE = re.compile(
    r"(dict|dictionary|schema|field|codebook|data_dict|readme|字典|字段|数据字典|说明)",
    re.I,
)

USAGE = """usage: main.py <command> [options]

commands:
  init          写 result.json 骨架与空 plan.json（先落盘 partial 骨架）
  profile       扫描输入目录，写 data_profile.json
  seed-plan     生成 plan.json 脚手架（字段候选 + task 步骤）
  render-figure 渲染 figure_model.json -> SVG
  validate      校验 result.json，写 validation_report.json

options:
  -i, --input DIR   输入目录（默认 /input）
  -o, --output DIR  输出目录（默认 /app/output）
  -w, --work DIR    工作目录（默认 /app/work）
  -m, --model FILE  figure_model.json 路径（render-figure）
  --repair          validate 时自动补齐缺失契约键（默认只报告）
  -h, --help        帮助
"""

PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def decode_bytes(raw):
    """按 utf-8-sig -> utf-8 -> gb18030 -> latin-1 依次尝试解码。"""
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, ""
        except (UnicodeDecodeError, LookupError) as e:
            last = "%s:%s" % (enc, type(e).__name__)
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", last


def read_text_file(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return "", "", "OSError: %s" % e
    if raw[:2] == b"\x1f\x8b":
        return "", "", "gzip-not-supported"
    return decode_bytes(raw)


def load_json_file(path):
    text, enc, err = read_text_file(path)
    if not text and err:
        return None, err
    try:
        return json.loads(text), ""
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        return None, "JSONDecodeError: %s" % e


def safe_write_text(path, text):
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return ""
    except OSError as e:
        return "OSError: %s" % e


def safe_write_json(path, obj):
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return ""
    except (OSError, TypeError, ValueError) as e:
        return "%s: %s" % (type(e).__name__, e)


def relative_path(p, base):
    try:
        return os.path.relpath(p, base).replace("\\", "/")
    except ValueError:
        return p.replace("\\", "/")


def find_case_id(data):
    if isinstance(data, dict):
        for k in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
            if k in data and data[k] not in (None, ""):
                return data[k]
    return None


def find_output_contract(data):
    if isinstance(data, dict):
        for k in ("output_contract", "outputContract", "contract"):
            if k in data and isinstance(data[k], (dict, list)):
                return data[k]
    return None


def normalize_contract(contract):
    """把 list 形式契约转成 dict，保持 key 原样；dict 直接复制。"""
    if contract is None:
        return {}
    if isinstance(contract, dict):
        return dict(contract)
    if isinstance(contract, list):
        out = {}
        for i, item in enumerate(contract):
            if isinstance(item, str):
                out[item] = None
            else:
                out["item_%d" % i] = item
        return out
    return {}


def find_case_file(input_dir):
    if not os.path.isdir(input_dir):
        return None
    for name in os.listdir(input_dir):
        if name.lower() == "case.json":
            return os.path.join(input_dir, name)
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            if fn.lower().endswith(".json"):
                p = os.path.join(root, fn)
                data, err = load_json_file(p)
                if not err and isinstance(data, dict) and find_case_id(data) is not None:
                    return p
    return None


def to_float(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "na", "n/a", "null", "none", "-", "nil"):
        return None
    s = s.replace(" ", "")
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return float(s.replace(",", "."))
    except ValueError:
        pass
    if "," in s:
        try:
            return float(s.replace(",", ""))
        except ValueError:
            pass
    return None


def summarize_numeric(values):
    n = len(values)
    if n == 0:
        return {"count": 0}
    return {
        "count": n,
        "min": round(min(values), 8),
        "max": round(max(values), 8),
        "mean": round(statistics.mean(values), 8),
        "std_pop": round(statistics.pstdev(values), 8) if n > 1 else 0.0,
    }


# --------------------------------------------------------------------------
# 子命令：init
# --------------------------------------------------------------------------
def cmd_init(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    for d in (output_dir, work_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass

    result_path = os.path.join(output_dir, "result.json")
    case_path = find_case_file(input_dir)
    case_id = None
    contract = {}
    errors = []

    if case_path:
        data, err = load_json_file(case_path)
        if err:
            errors.append("case.json 解析失败: %s" % err)
        elif isinstance(data, dict):
            case_id = find_case_id(data)
            raw_contract = find_output_contract(data)
            contract = normalize_contract(raw_contract)
            if not contract:
                errors.append("case.json 中未发现 output_contract 字段（将回填空契约）")
    else:
        errors.append("未发现 case.json（case_id 与契约按空处理）")

    existing = {}
    if os.path.isfile(result_path):
        e, err = load_json_file(result_path)
        if not err and isinstance(e, dict):
            existing = e
        else:
            errors.append("已有 result.json 不可解析，将重建骨架")

    result = dict(existing)
    if result.get("case_id") in (None, "") and case_id is not None:
        result["case_id"] = case_id
    result.setdefault("case_id", case_id)
    for k in contract:
        result.setdefault(k, None)
    if not result.get("status") or result.get("status") not in ALLOWED_STATUS:
        result["status"] = "partial"
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("limitations", [])
    if not isinstance(result["errors"], list):
        result["errors"] = []
    for e in errors:
        if e not in result["errors"]:
            result["errors"].append(e)
    if not isinstance(result["change_log"], list):
        result["change_log"] = []
    result["change_log"].append(
        {
            "t": now_iso(),
            "step": "init",
            "action": "写/补齐 result.json 骨架与 plan.json 骨架",
            "contract_key_count": len(contract),
            "case_id": case_id,
            "ok": not errors,
            "errors": errors,
        }
    )

    werr = safe_write_json(result_path, result)
    if werr:
        errors.append("result.json 写入失败: %s" % werr)

    plan_path = os.path.join(work_dir, "plan.json")
    plan_msg = "已存在，不覆盖"
    if not os.path.isfile(plan_path):
        plan = {
            "schema_version": 1,
            "case_id": case_id,
            "assets": [],
            "fields": [],
            "rules": [],
            "steps": [],
            "decisions": [],
            "status": "partial",
        }
        safe_write_json(plan_path, plan)
        plan_msg = "已创建空骨架"

    print(
        json.dumps(
            {
                "ok": not errors,
                "case_id": case_id,
                "contract_key_count": len(contract),
                "result_path": result_path,
                "plan": plan_msg,
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 0


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------
def scan_files(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            out.append(os.path.join(root, fn))
    return out


def classify(path, ext):
    if ext in (".csv", ".tsv", ".tab"):
        return "tabular"
    if ext in (".json", ".jsonl", ".ndjson"):
        return "json"
    if ext in (".sqlite", ".sqlite3", ".db", ".sdb"):
        return "sqlite"
    if ext in (".md", ".markdown", ".txt", ".rst", ".yml", ".yaml", ".toml", ".ini"):
        return "text"
    if ext in (".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"):
        return "binary_doc"
    return "other"


def sniff_delimiter(text):
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    best, best_count = ",", -1
    for d in (",", "\t", ";", "|"):
        counts = [ln.count(d) for ln in lines]
        if counts and min(counts) > 0 and sum(counts) > best_count:
            best, best_count = d, sum(counts)
    return best


def profile_tabular(text):
    delim = sniff_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = [list(r) for r in reader]
    except Exception as e:
        return {"delimiter": delim, "error": "csv parse: %s" % e}
    if not rows:
        return {"delimiter": delim, "n_rows": 0, "n_cols": 0, "error": "empty"}
    header = [str(c).strip() for c in rows[0]]
    body = [r for r in rows[1:] if any((c or "").strip() for c in r)]
    n_cols = len(header)
    norm = []
    for r in body:
        rr = list(r) + [""] * max(0, n_cols - len(r))
        norm.append(rr[:n_cols])
    n_rows = len(norm)

    seen = {}
    dup_count = 0
    for r in norm:
        t = tuple(x.strip() for x in r)
        if t in seen:
            dup_count += 1
        else:
            seen[t] = 1

    columns = []
    for j, h in enumerate(header):
        cells = [norm[i][j] for i in range(n_rows)]
        nonempty = [c for c in cells if c.strip() != ""]
        n_missing = n_rows - len(nonempty)
        nums = []
        nonnumeric = 0
        for c in nonempty:
            f = to_float(c)
            if f is None:
                nonnumeric += 1
            else:
                nums.append(f)
        total_nonempty = len(nonempty)
        numeric_ratio = (len(nums) / total_nonempty) if total_nonempty else 0.0
        if nums and numeric_ratio >= 0.8:
            dtype = "numeric"
        elif total_nonempty:
            dtype = "categorical"
        else:
            dtype = "empty"
        col = {"name": h, "n_missing": n_missing, "dtype": dtype}
        if dtype == "numeric":
            col["numeric"] = summarize_numeric(nums)
            col["numeric"]["missing_excluded"] = True
        else:
            distinct = {}
            for c in nonempty:
                cc = c.strip()
                distinct[cc] = distinct.get(cc, 0) + 1
            top = sorted(distinct.items(), key=lambda kv: -kv[1])[:10]
            col["distinct"] = {"n_unique": len(distinct), "top": top}
        columns.append(col)

    return {
        "delimiter": delim,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "duplicate_rows": dup_count,
        "header": header,
        "columns": columns,
    }


def profile_json(path, text):
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        return {"error": "JSONDecodeError: %s" % e}
    if isinstance(data, list):
        keys = []
        for item in data[:100]:
            if isinstance(item, dict):
                for k in item.keys():
                    if k not in keys:
                        keys.append(k)
        return {"type": "list", "length": len(data), "record_keys": keys}
    if isinstance(data, dict):
        return {"type": "object", "top_keys": list(data.keys())[:200]}
    return {"type": type(data).__name__}


def _safe_table_name(name):
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def profile_sqlite(path):
    tables = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r[0] for r in cur.fetchall()]
        for name in names:
            if not _safe_table_name(name):
                tables.append({"name": name, "skipped": "unsafe name"})
                continue
            entry = {"name": name}
            try:
                cur.execute('SELECT COUNT(*) FROM "%s"' % name)
                entry["n_rows"] = cur.fetchone()[0]
            except Exception as e:
                entry["n_rows"] = None
                entry["count_error"] = "%s" % e
            cols = []
            try:
                cur.execute('PRAGMA table_info("%s")' % name)
                for row in cur.fetchall():
                    cols.append(
                        {
                            "cid": row[0],
                            "name": row[1],
                            "type": row[2],
                            "notnull": bool(row[3]),
                            "pk": row[5],
                        }
                    )
            except Exception as e:
                cols = [{"error": "%s" % e}]
            entry["columns"] = cols
            tables.append(entry)
        con.close()
    except Exception as e:
        return {"tables": [], "error": "sqlite: %s" % e}
    return {"tables": tables}


def profile_text(path, text):
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    return {
        "line_count": len(lines),
        "nonempty_lines": len(nonempty),
        "preview": "\n".join(nonempty[:8])[:400],
        "delimiter_hint": sniff_delimiter(text),
    }


# --------------------------------------------------------------------------
# 子命令：profile
# --------------------------------------------------------------------------
def cmd_profile(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    files = scan_files(input_dir)
    assets = []
    errors = []
    for fp in files:
        rel = relative_path(fp, input_dir)
        try:
            size = os.path.getsize(fp)
        except OSError as e:
            assets.append({"locator": rel, "error": "OSError: %s" % e})
            continue
        ext = os.path.splitext(fp)[1].lower()
        kind = classify(fp, ext)
        entry = {"locator": rel, "size_bytes": size, "kind": kind}
        name = os.path.basename(fp)
        if DICT_RE.search(name):
            entry["role_hint"] = "data_dictionary"
        elif "task" in name.lower():
            entry["role_hint"] = "task"
        elif kind in ("tabular", "sqlite"):
            entry["role_hint"] = "data"
        else:
            entry["role_hint"] = "material"
        if size > MAX_FILE_BYTES:
            entry["skipped"] = "size_limit"
            assets.append(entry)
            continue
        if kind == "tabular":
            text, enc, err = read_text_file(fp)
            entry["encoding"] = enc
            if err:
                entry["error"] = err
            else:
                entry["profile"] = profile_tabular(text)
        elif kind == "json":
            text, enc, err = read_text_file(fp)
            entry["encoding"] = enc
            if err:
                entry["error"] = err
            else:
                entry["profile"] = profile_json(fp, text)
        elif kind == "sqlite":
            entry["profile"] = profile_sqlite(fp)
        elif kind == "text":
            text, enc, err = read_text_file(fp)
            entry["encoding"] = enc
            if err:
                entry["error"] = err
            else:
                entry["profile"] = profile_text(fp, text)
        else:
            entry["profile"] = {"note": "未解析二进制/未知格式，仅记录元数据"}
        assets.append(entry)

    profile = {
        "schema_version": 1,
        "input_dir": input_dir,
        "scanned_at": now_iso(),
        "file_count": len(assets),
        "files": assets,
        "errors": errors,
    }
    werr = safe_write_json(os.path.join(output_dir, "data_profile.json"), profile)
    print(
        json.dumps(
            {
                "ok": not werr,
                "file_count": len(assets),
                "output": os.path.join(output_dir, "data_profile.json"),
                "write_error": werr,
            },
            ensure_ascii=False,
        )
    )
    return 0


# --------------------------------------------------------------------------
# 子命令：seed-plan
# --------------------------------------------------------------------------
def extract_task_items(text):
    items = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^(?:(\d+)[\.、)．]|[-*•])\s+\S", s):
            items.append(s)
    return items


def cmd_seed_plan(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    try:
        os.makedirs(work_dir, exist_ok=True)
    except OSError:
        pass

    profile, perr = load_json_file(os.path.join(output_dir, "data_profile.json"))
    assets = []
    fields = []
    if not perr and isinstance(profile, dict):
        for f in profile.get("files", []):
            a = {
                "locator": f.get("locator"),
                "kind": f.get("kind"),
                "role_hint": f.get("role_hint"),
                "size_bytes": f.get("size_bytes"),
            }
            assets.append(a)
            if f.get("kind") == "tabular":
                pr = f.get("profile") or {}
                for col in pr.get("columns", []):
                    fields.append(
                        {
                            "asset": f.get("locator"),
                            "column": col.get("name"),
                            "canonical": None,
                            "aliases": [col.get("name")] if col.get("name") else [],
                            "unit": None,
                            "role": None,
                            "dtype": col.get("dtype"),
                            "n_missing": col.get("n_missing"),
                            "status": "candidate",
                        }
                    )
            if f.get("kind") == "sqlite":
                pr = f.get("profile") or {}
                for t in pr.get("tables", []):
                    for c in t.get("columns", []):
                        fields.append(
                            {
                                "asset": "%s::table=%s" % (f.get("locator"), t.get("name")),
                                "column": c.get("name"),
                                "canonical": None,
                                "aliases": [c.get("name")] if c.get("name") else [],
                                "unit": None,
                                "role": None,
                                "dtype": c.get("type"),
                                "status": "candidate",
                            }
                        )

    task_path = os.path.join(input_dir, "task.md")
    steps = []
    if os.path.isfile(task_path):
        text, enc, err = read_text_file(task_path)
        if not err:
            for idx, s in enumerate(extract_task_items(text), 1):
                steps.append(
                    {
                        "id": "step-%02d" % idx,
                        "description": s,
                        "status": "todo",
                        "input": [],
                        "command": None,
                        "output": [],
                        "checks": [],
                    }
                )

    plan_path = os.path.join(work_dir, "plan.json")
    existing = {}
    if os.path.isfile(plan_path):
        e, err = load_json_file(plan_path)
        if not err and isinstance(e, dict):
            existing = e

    plan = dict(existing)
    plan.setdefault("schema_version", 1)
    plan["assets"] = assets
    if not plan.get("fields"):
        plan["fields"] = fields
    if not plan.get("steps") and steps:
        plan["steps"] = steps
    plan.setdefault("rules", [])
    plan.setdefault("decisions", [])
    plan.setdefault("case_id", existing.get("case_id"))
    plan["status"] = plan.get("status") or "partial"

    werr = safe_write_json(plan_path, plan)
    print(
        json.dumps(
            {
                "ok": not werr,
                "assets": len(assets),
                "fields": len(plan["fields"]),
                "steps": len(plan["steps"]),
                "output": plan_path,
                "write_error": werr,
            },
            ensure_ascii=False,
        )
    )
    return 0


# --------------------------------------------------------------------------
# 子命令：render-figure
# --------------------------------------------------------------------------
def load_figure_rows(model, input_dir, output_dir, work_dir):
    data = model.get("data") if isinstance(model, dict) else None
    if not isinstance(data, dict):
        return [], {"n": 0}, "figure_model.data 缺失或非对象"
    inline = data.get("rows")
    if isinstance(inline, list) and inline and all(isinstance(r, dict) for r in inline):
        return inline, {"source": "inline", "n": len(inline)}, ""
    source = data.get("source")
    if not source:
        return [], {"n": 0}, "未提供 data.rows 或 data.source"
    candidates = []
    if os.path.isabs(source):
        candidates.append(source)
    else:
        for base in (output_dir, work_dir, input_dir):
            candidates.append(os.path.join(base, source))
    path = next((c for c in candidates if os.path.isfile(c)), None)
    if path is None:
        return [], {"source": source}, "数据源未找到: %s" % source
    text, enc, err = read_text_file(path)
    if err:
        return [], {"source": source}, err
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            data = json.loads(text)
            if isinstance(data, list) and all(isinstance(r, dict) for r in data):
                return data, {"source": source, "n": len(data)}, ""
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                return data["rows"], {"source": source, "n": len(data["rows"])}, ""
            return [], {"source": source}, "JSON 源非对象数组"
        else:
            delim = sniff_delimiter(text)
            reader = csv.DictReader(io.StringIO(text), delimiter=delim)
            rows = [dict(r) for r in reader]
            return rows, {"source": source, "n": len(rows)}, ""
    except Exception as e:
        return [], {"source": source}, "%s: %s" % (type(e).__name__, e)


def linear_ticks(lo, hi, target=6):
    if lo == hi:
        return [lo]
    span = hi - lo
    if span <= 0:
        return [lo]
    step0 = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(step0))
    step = None
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if step0 / mag <= m + 1e-12:
            step = m * mag
            break
    if step is None:
        step = 10 * mag
    start = math.floor(lo / step) * step
    out = []
    v = start
    while v <= hi + 1e-9:
        if v >= lo - 1e-9:
            out.append(round(v, 10))
        v += step
    return out


def fmt_num(x):
    if x is None:
        return ""
    if isinstance(x, float):
        if x == 0:
            return "0"
        if abs(x) >= 1e4 or abs(x) < 1e-4:
            return ("%.4g" % x)
        return ("%g" % x)
    return str(x)


def esc(s):
    return html.escape(str(s), quote=True)


def render_svg(model, rows):
    W = int(model.get("width") or 720)
    H = int(model.get("height") or 480)
    title = model.get("title") or "figure"
    ml, mr, mt, mb = 74, 30, 52, 62
    pw = max(50, W - ml - mr)
    ph = max(50, H - mt - mb)
    px0, py0 = ml, mt
    px1, py1 = ml + pw, mt + ph

    marks = model.get("marks") if isinstance(model.get("marks"), list) else []
    if not marks:
        marks = [model.get("mark")] if isinstance(model.get("mark"), dict) else []
    marks = [m for m in marks if isinstance(m, dict)]

    # 收集全部 (x, y, color, size) 值
    points = []
    skipped_missing = 0
    for m in marks:
        xf = (m.get("x") or {}).get("field")
        yf = (m.get("y") or {}).get("field")
        cf = (m.get("color") or {}).get("field")
        sf = (m.get("size") or {}).get("field")
        mtype = m.get("type") or "scatter"
        for r in rows:
            xv = r.get(xf) if xf else None
            yv = r.get(yf) if yf else None
            yf_num = to_float(yv)
            if yf_num is None:
                skipped_missing += 1
                continue
            points.append(
                {
                    "mark": m,
                    "type": mtype,
                    "x_raw": xv,
                    "y": yf_num,
                    "color": str(r.get(cf)).strip() if cf and r.get(cf) is not None else None,
                    "size": to_float(r.get(sf)) if sf else None,
                }
            )

    stats = {"n_plotted": len(points), "n_skipped_missing": skipped_missing}
    if not points:
        return _svg_no_data(W, H, title, "无可用数据点（y 缺失或空）"), stats

    numeric_x = all(to_float(p["x_raw"]) is not None for p in points)
    if numeric_x:
        xs = [to_float(p["x_raw"]) for p in points]
        xmin, xmax = min(xs), max(xs)
        if xmin == xmax:
            xmin -= 1
            xmax += 1
    else:
        cats = []
        for p in points:
            if p["x_raw"] not in cats:
                cats.append(str(p["x_raw"]))
        xmin, xmax = 0.0, float(len(cats))

    ys = [p["y"] for p in points]
    ymin, ymax = min(ys), max(ys)
    if ymin == ymax:
        ymin -= 1
        ymax += 1
    pad = (ymax - ymin) * 0.05
    ymin -= pad
    ymax += pad

    def sx(x):
        if numeric_x:
            return px0 + (x - xmin) / (xmax - xmin) * pw
        idx = cats.index(str(x)) if str(x) in cats else 0
        return px0 + (idx + 0.5) / len(cats) * pw

    def sy(y):
        return py1 - (y - ymin) / (ymax - ymin) * ph

    # 颜色类别
    color_cats = []
    for p in points:
        if p["color"] is not None and p["color"] not in color_cats:
            color_cats.append(p["color"])
    color_of = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(color_cats)}

    parts = []
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H)
    )
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append(
        '<text x="%d" y="22" font-family="sans-serif" font-size="15" text-anchor="middle" fill="#111">%s</text>'
        % (W // 2, esc(title))
    )
    # 坐标框
    parts.append(
        '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" stroke="#333" stroke-width="1"/>'
        % (px0, py0, pw, ph)
    )
    # 轴标签
    xmeta = (marks[0].get("x") or {})
    ymeta = (marks[0].get("y") or {})
    xlabel = xmeta.get("title") or xmeta.get("field") or "x"
    ylabel = ymeta.get("title") or ymeta.get("field") or "y"
    if xmeta.get("unit"):
        xlabel += " (%s)" % xmeta["unit"]
    if ymeta.get("unit"):
        ylabel += " (%s)" % ymeta["unit"]
    parts.append(
        '<text x="%d" y="%d" font-family="sans-serif" font-size="12" text-anchor="middle" fill="#111">%s</text>'
        % (W // 2, H - 14, esc(xlabel))
    )
    parts.append(
        '<text x="16" y="%d" font-family="sans-serif" font-size="12" text-anchor="middle" fill="#111" transform="rotate(-90 16 %d)">%s</text>'
        % (mt + ph // 2, mt + ph // 2, esc(ylabel))
    )
    # x 轴刻度
    if numeric_x:
        for t in linear_ticks(xmin, xmax):
            x = sx(t)
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#bbb"/>' % (x, py1, x, py1 + 5))
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="sans-serif" font-size="10" text-anchor="middle" fill="#333">%s</text>'
                % (x, py1 + 17, esc(fmt_num(t)))
            )
    else:
        for i, c in enumerate(cats):
            x = sx(c)
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="sans-serif" font-size="10" text-anchor="middle" fill="#333">%s</text>'
                % (x, py1 + 17, esc(c[:16]))
            )
    # y 轴刻度
    for t in linear_ticks(ymin, ymax):
        y = sy(t)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#bbb"/>' % (px0 - 5, y, px0, y))
        parts.append(
            '<text x="%.1f" y="%.1f" font-family="sans-serif" font-size="10" text-anchor="end" fill="#333">%s</text>'
            % (px0 - 8, y + 4, esc(fmt_num(t)))
        )

    # 图例
    if color_cats and model.get("legend", {}).get("show", True) if isinstance(model.get("legend"), dict) else color_cats:
        lx = px1 + 12
        ly = py0
        for i, c in enumerate(color_cats):
            parts.append('<rect x="%d" y="%d" width="10" height="10" fill="%s"/>' % (lx, ly + i * 16, color_of[c]))
            parts.append(
                '<text x="%d" y="%d" font-family="sans-serif" font-size="10" fill="#333">%s</text>'
                % (lx + 14, ly + i * 16 + 9, esc(c[:20]))
            )

    # 绘制 marks
    for p in points:
        fill = color_of.get(p["color"], "#1f77b4")
        if p["type"] == "bar":
            if numeric_x:
                bw = pw / max(len(points), 1) * 0.8
                x = sx(to_float(p["x_raw"])) - bw / 2
            else:
                bw = pw / max(len(cats), 1) * 0.8
                x = sx(p["x_raw"]) - bw / 2
            y0 = sy(0.0) if ymin <= 0 <= ymax else sy(ymin)
            h = abs(sy(p["y"]) - y0)
            top = min(sy(p["y"]), y0)
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="#333" stroke-width="0.5"/>'
                % (x, top, bw, h, fill)
            )
        elif p["type"] == "line":
            # 按 x 排序后连线（在下方统一处理）
            pass
        else:
            r = 3.5
            if p["size"] is not None and p["size"] > 0:
                r = min(10.0, max(2.0, math.sqrt(p["size"]) * 2.0))
            x = sx(to_float(p["x_raw"]) if numeric_x else p["x_raw"])
            y = sy(p["y"])
            parts.append(
                '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="#333" stroke-width="0.4"/>' % (x, y, r, fill)
            )

    # line marks 统一按 x 排序
    line_marks = [m for m in marks if m.get("type") == "line"]
    for m in line_marks:
        xf = (m.get("x") or {}).get("field")
        yf = (m.get("y") or {}).get("field")
        cf = (m.get("color") or {}).get("field")
        seg = {}
        for r in rows:
            yv = to_float(r.get(yf) if yf else None)
            if yv is None:
                continue
            key = str(r.get(cf)).strip() if cf and r.get(cf) is not None else "__all__"
            seg.setdefault(key, []).append((r.get(xf), yv))
        for key, arr in seg.items():
            arr = sorted(arr, key=lambda t: (to_float(t[0]) is None, to_float(t[0])))
            coords = []
            for xv, yv in arr:
                coords.append("%.1f,%.1f" % (sx(to_float(xv) if numeric_x else xv), sy(yv)))
            if coords:
                stroke = color_of.get(key if key != "__all__" else None, "#1f77b4")
                parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.5"/>' % (" ".join(coords), stroke))

    parts.append("</svg>")
    return "\n".join(parts), stats


def _svg_no_data(W, H, title, msg):
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
            '<rect width="%d" height="%d" fill="white"/>' % (W, H),
            '<text x="%d" y="%d" font-family="sans-serif" font-size="14" text-anchor="middle" fill="#333">%s</text>'
            % (W // 2, H // 2 - 8, esc(title)),
            '<text x="%d" y="%d" font-family="sans-serif" font-size="12" text-anchor="middle" fill="#c00">%s</text>'
            % (W // 2, H // 2 + 16, esc(msg)),
            "</svg>",
        ]
    )


def cmd_render_figure(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError:
        pass
    model_path = args.model or os.path.join(work_dir, "figure_model.json")
    model, err = load_json_file(model_path)
    if err or not isinstance(model, dict):
        msg = err or "figure_model 非对象"
        out_name = "figure.svg"
        safe_write_text(os.path.join(output_dir, out_name), _svg_no_data(720, 480, "figure", msg))
        report = {
            "schema_version": 1,
            "rendered_at": now_iso(),
            "status": "error",
            "error": msg,
            "svg": out_name,
        }
        safe_write_json(os.path.join(output_dir, "figure_render.json"), report)
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        return 0

    rows, meta, derr = load_figure_rows(model, input_dir, output_dir, work_dir)
    if derr:
        out_name = "figure.svg"
        safe_write_text(os.path.join(output_dir, out_name), _svg_no_data(720, 480, "figure", derr))
        report = {
            "schema_version": 1,
            "rendered_at": now_iso(),
            "status": "error",
            "error": derr,
            "svg": out_name,
        }
        safe_write_json(os.path.join(output_dir, "figure_render.json"), report)
        print(json.dumps({"ok": False, "error": derr}, ensure_ascii=False))
        return 0

    svg, rstats = render_svg(model, rows)
    out_name = os.path.basename(model.get("output") or "figure.svg")
    if not out_name.lower().endswith(".svg"):
        out_name += ".svg"
    werr = safe_write_text(os.path.join(output_dir, out_name), svg)

    # 证据与确定性哈希
    evidence = model.get("evidence") if isinstance(model.get("evidence"), dict) else {}
    model_hash = hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    data_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    report = {
        "schema_version": 1,
        "rendered_at": now_iso(),
        "status": "ok" if not werr else "error",
        "svg": out_name,
        "data": meta,
        "n_input_rows": len(rows),
        "n_plotted": rstats.get("n_plotted"),
        "n_skipped_missing": rstats.get("n_skipped_missing"),
        "model_sha256": model_hash,
        "data_sha256": data_hash,
        "evidence": evidence,
        "write_error": werr,
    }
    safe_write_json(os.path.join(output_dir, "figure_render.json"), report)
    print(
        json.dumps(
            {
                "ok": not werr,
                "svg": out_name,
                "n_input_rows": len(rows),
                "n_plotted": rstats.get("n_plotted"),
                "n_skipped_missing": rstats.get("n_skipped_missing"),
                "model_sha256": model_hash[:16],
            },
            ensure_ascii=False,
        )
    )
    return 0


# --------------------------------------------------------------------------
# 子命令：validate
# --------------------------------------------------------------------------
def cmd_validate(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    result_path = os.path.join(output_dir, "result.json")
    report = {"schema_version": 1, "validated_at": now_iso(), "checks": [], "errors": 0, "warnings": 0}

    def add(level, check, detail):
        report["checks"].append({"level": level, "check": check, "detail": detail})
        if level == "error":
            report["errors"] += 1
        elif level == "warning":
            report["warnings"] += 1

    result, err = load_json_file(result_path)
    if err or not isinstance(result, dict):
        add("error", "result.json 可解析", err or "非对象")
        safe_write_json(os.path.join(output_dir, "validation_report.json"), report)
        print(json.dumps({"ok": False, "errors": report["errors"]}, ensure_ascii=False))
        return 0

    if result.get("case_id") in (None, ""):
        add("warning", "case_id 存在", "为空或缺失")
    else:
        add("ok", "case_id 存在", "%s" % result["case_id"])

    if result.get("status") in ALLOWED_STATUS:
        add("ok", "status 合法", result["status"])
    else:
        add("error", "status 合法", "当前值: %r" % result.get("status"))

    # 契约字段
    contract = {}
    case_path = find_case_file(input_dir)
    if case_path:
        data, cerr = load_json_file(case_path)
        if not cerr and isinstance(data, dict):
            contract = normalize_contract(find_output_contract(data))
    missing = [k for k in contract if k not in result]
    for k in missing:
        add("error", "契约字段回填: %s" % k, "result.json 缺失，建议回填 null/[]")
    if not missing:
        add("ok", "契约字段回填", "%d 个字段齐全" % len(contract))
    if args.repair and missing:
        for k in missing:
            result[k] = None
        result.setdefault("change_log", []).append(
            {"t": now_iso(), "step": "validate-repair", "action": "补齐缺失契约键", "keys": missing}
        )
        safe_write_json(result_path, result)

    sources = result.get("sources")
    if sources:
        bad = [
            i
            for i, s in enumerate(sources)
            if not isinstance(s, dict) or not s.get("locator") or not s.get("quote")
        ]
        if bad:
            add("error", "sources 逐字溯源", "索引 %s 缺 locator 或 quote" % bad)
        else:
            add("ok", "sources 逐字溯源", "%d 条均含 locator+quote" % len(sources))
    else:
        if result.get("status") in ("ok", "partial"):
            add("warning", "sources 逐字溯源", "为空；关键结论需给出 locator+quote")

    if not isinstance(result.get("change_log"), list):
        add("error", "change_log 为数组", "缺失或非数组")
    else:
        add("ok", "change_log 为数组", "%d 条" % len(result["change_log"]))

    # 引用的产物是否存在
    missing_artifacts = []
    for v in result.values():
        if isinstance(v, str) and v and not v.startswith(("http://", "https://")):
            cand = v if os.path.isabs(v) else os.path.join(output_dir, v)
            if v.endswith((".svg", ".csv", ".md", ".json", ".png", ".pdf", ".docx", ".xlsx", ".pptx")) and not os.path.isfile(cand):
                missing_artifacts.append(v)
    if missing_artifacts:
        add("warning", "主产物存在", "未找到: %s" % missing_artifacts[:5])
    else:
        add("ok", "主产物存在", "引用的产物路径均可解析")

    # SVG 可解析性
    svg_files = []
    for fn in os.listdir(output_dir) if os.path.isdir(output_dir) else []:
        if fn.lower().endswith(".svg"):
            svg_files.append(os.path.join(output_dir, fn))
    for sp in svg_files:
        try:
            ET.parse(sp)
            add("ok", "SVG 可解析", os.path.basename(sp))
        except Exception as e:
            add("error", "SVG 可解析", "%s: %s" % (os.path.basename(sp), e))

    safe_write_json(os.path.join(output_dir, "validation_report.json"), report)
    print(
        json.dumps(
            {"ok": report["errors"] == 0, "errors": report["errors"], "warnings": report["warnings"]},
            ensure_ascii=False,
        )
    )
    return 0


# --------------------------------------------------------------------------
# 分发
# --------------------------------------------------------------------------
def parse_args(argv):
    ns = argparse.Namespace(input=None, output=None, work=None, model=None, repair=False, help=False)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "-i") and i + 1 < len(argv):
            ns.input = argv[i + 1]
            i += 2
        elif a in ("--output", "-o") and i + 1 < len(argv):
            ns.output = argv[i + 1]
            i += 2
        elif a in ("--work", "-w") and i + 1 < len(argv):
            ns.work = argv[i + 1]
            i += 2
        elif a in ("--model", "-m") and i + 1 < len(argv):
            ns.model = argv[i + 1]
            i += 2
        elif a == "--repair":
            ns.repair = True
            i += 1
        elif a in ("--help", "-h"):
            ns.help = True
            i += 1
        else:
            i += 1
    return ns


HANDLERS = {
    "init": cmd_init,
    "profile": cmd_profile,
    "seed-plan": cmd_seed_plan,
    "render-figure": cmd_render_figure,
    "validate": cmd_validate,
}


def dispatch(argv):
    if not argv or argv[0] in ("--help", "-h", "help"):
        print(USAGE)
        return 0
    cmd = argv[0]
    if cmd not in HANDLERS:
        print("unknown command: %s\n%s" % (cmd, USAGE), file=sys.stderr)
        return 2
    args = parse_args(argv[1:])
    if getattr(args, "help", False):
        print(USAGE)
        return 0
    try:
        return HANDLERS[cmd](args)
    except Exception as e:  # 安全网：任何未预期异常都不应让 Harness 崩掉
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(dispatch(sys.argv[1:]))
