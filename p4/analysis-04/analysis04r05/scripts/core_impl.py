#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-contract -- 实现模块（被 main.py 导入）。

仅依赖 Python 标准库（json/csv/sqlite3/argparse/subprocess 等）。包含四类动作：
  init      扫描 /input，先落盘一份契约对齐的合法 result.json 骨架（status=partial）
  profile   生成 /app/output/data_profile.json 数据画像（编码/行列/缺失/重复/数值摘要/SQLite 表结构）
  validate  校验 result.json 与 output_contract 的字段对齐、状态合法性与产物路径
  selftest  在临时目录跑 4 类健壮性场景，输出 selftest_report.json

设计取舍（相对参考包）：output_contract 被提升为一等公民——init 产出
contract_map.json（逐字段的类型/默认值/来源定位账本），validate 用 case.json 重新推导契约
并逐字段核对“缺不漏、名不改、未知键不删”；change_log 为 append-only 动作日志，
每条含 ts/action/detail/delta。所有错误降级为合法 result.json 且退出码恒为 0。

入口由同目录 main.py 提供；本模块通过 dispatch(args) 被调用，不直接执行。
"""

import argparse
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.0.0"
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")
DEFAULT_MAX_TEXT = 2 * 1024 * 1024
TABULAR_MAX_TEXT = 20 * 1024 * 1024
MISSING_TOKENS = {"", "na", "nan", "null", "none", "n/a", "-", "--", "missing"}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _json_default(o):
    if isinstance(o, (datetime,)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def read_text_robust(path, max_bytes=DEFAULT_MAX_TEXT):
    """按多种编码读取文本，返回 (text, info)；失败返回 (None, info)。"""
    p = Path(path)
    if not p.exists():
        return None, {"error": "not_found", "path": str(path)}
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, {"error": "stat_failed", "path": str(path), "detail": str(e)}
    if size > max_bytes:
        return None, {"error": "too_large", "path": str(path), "bytes": size, "limit": max_bytes}
    try:
        data = p.read_bytes()
    except OSError as e:
        return None, {"error": "read_failed", "path": str(path), "detail": str(e)}
    for enc in TEXT_ENCODINGS:
        try:
            return data.decode(enc), {"encoding": enc, "bytes": size}
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), {"encoding": "utf-8-replace", "bytes": size}


def _strip_comments(s):
    """字符串感知地剥离 // 与 /* */ 注释，避免误伤字符串内文本。"""
    out = []
    in_str = False
    escape = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            i += 2
            while i < n and s[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_trailing_commas(s):
    """字符串感知地去掉 } ] 前的尾随逗号。"""
    out = []
    in_str = False
    escape = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i = j
                continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_json_robust(text):
    """解析 JSON，容错 BOM/注释/尾随逗号。返回 (obj, error)。"""
    if text is None:
        return None, "no_text"
    if isinstance(text, (dict, list)):
        return text, None
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", errors="replace")
    s = str(text).lstrip("\ufeff").strip()
    if not s:
        return None, "empty_text"
    try:
        return json.loads(s), None
    except (json.JSONDecodeError, ValueError):
        pass
    cleaned = _strip_trailing_commas(_strip_comments(s))
    if cleaned != s:
        try:
            return json.loads(cleaned), None
        except (json.JSONDecodeError, ValueError):
            pass
    return None, "json_decode_error"


def write_json(path, obj):
    """原子写 JSON；失败时尝试直接写，仍失败返回 False。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        try:
            p.write_text(text, encoding="utf-8")
            return True
        except OSError:
            return False


def append_change_log(log, action, detail=None, delta=None):
    entry = {"ts": now_iso(), "action": action}
    if detail is not None:
        entry["detail"] = detail
    if delta is not None:
        entry["delta"] = delta
    log.append(entry)


def _is_under(path, base):
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def scan_input_files(input_dir, exclude_dirs=()):
    """递归扫描输入目录，跳过缓存/版本控制目录与给定排除目录；返回按路径排序的文件列表。"""
    root = Path(input_dir)
    if not root.exists() or not root.is_dir():
        return []
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".parser")]
        for name in filenames:
            fp = Path(dirpath) / name
            if any(_is_under(fp, Path(ex)) for ex in exclude_dirs if ex):
                continue
            files.append(fp)
    files.sort(key=lambda f: str(f).lower())
    return files


# --------------------------------------------------------------------------- #
# 输出契约发现与对齐（本变体的核心）
# --------------------------------------------------------------------------- #
def find_case_id(obj):
    if isinstance(obj, dict):
        for key in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
            v = obj.get(key)
            if v not in (None, "", []):
                return v
        nested = obj.get("case")
        if isinstance(nested, dict):
            for key in ("case_id", "caseId", "challenge_id", "id"):
                v = nested.get(key)
                if v not in (None, "", []):
                    return v
    return None


def extract_output_contract(case_obj, task_text):
    """返回 (contract, locator, warnings)。contract 可为 dict/list/str 或 None。"""
    warnings = []
    if isinstance(case_obj, dict):
        for key in ("output_contract", "outputContract", "output-contract", "output_contract_spec"):
            if key in case_obj:
                return case_obj[key], "case.json#%s" % key, warnings
        if isinstance(case_obj.get("contract"), (dict, list)):
            return case_obj["contract"], "case.json#contract", warnings
    if isinstance(task_text, str) and task_text.strip():
        m = re.search(r"output_contract[:\s]*(?P<body>\{.*?\}|\S+)", task_text, re.S | re.I)
        if m:
            body = m.group("body")
            if body.startswith("{"):
                obj, err = parse_json_robust(body)
                if err is None and isinstance(obj, (dict, list)):
                    return obj, "task.md#output_contract", warnings
    warnings.append("output_contract not found in case.json or task.md")
    return None, None, warnings


def default_for_field(name, spec):
    """由契约字段名与可选 schema 推断默认值；未知一律 null。"""
    typ = ""
    if isinstance(spec, dict):
        typ = (spec.get("type") or spec.get("data_type") or spec.get("dtype") or "").lower()
    if typ in ("array", "list", "[]", "sequence"):
        return [], "array"
    if typ in ("object", "dict", "{}", "map", "record"):
        return {}, "object"
    if typ in ("number", "int", "integer", "float", "numeric", "double"):
        return None, "number"
    if typ in ("string", "str", "text"):
        return None, "string"
    if typ in ("boolean", "bool"):
        return None, "boolean"
    low = str(name).lower()
    if any(k in low for k in ("path", "url", "file", "artifact", "output", "src", "table")):
        return None, "string"
    if low.endswith(("s",)) and any(k in low for k in ("list", "items", "results", "records", "sources", "tables")):
        return [], "array"
    return None, "unknown"


def normalize_contract(contract):
    """把契约规范化为 {字段名: 默认值}、字段元信息列表与告警列表。"""
    fields, meta, warnings = {}, [], []
    if isinstance(contract, dict):
        for name, spec in contract.items():
            default, typ = default_for_field(name, spec)
            fields[name] = default
            meta.append({"field": name, "type": typ, "default": default, "source": "contract_key"})
    elif isinstance(contract, list):
        for i, item in enumerate(contract):
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key") or item.get("id") or ("field_%d" % i)
                typ = item.get("type")
                default, typ2 = default_for_field(name, {"type": typ} if typ else {})
                fields[name] = default
                meta.append({"field": name, "type": typ or typ2, "default": default, "source": "contract_list[%d]" % i})
            elif isinstance(item, str) and item.strip():
                default, typ = default_for_field(item, {})
                fields[item] = default
                meta.append({"field": item, "type": typ, "default": default, "source": "contract_list[%d]" % i})
            else:
                warnings.append("contract item %d has unsupported type %s" % (i, type(item).__name__))
    elif isinstance(contract, str) and contract.strip():
        seen = set()
        for groups in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`|\"([A-Za-z_][A-Za-z0-9_]*)\"|'([A-Za-z_][A-Za-z0-9_]*)'", contract):
            name = next((g for g in groups if g), None)
            if name and name not in seen:
                seen.add(name)
                default, typ = default_for_field(name, {})
                fields[name] = default
                meta.append({"field": name, "type": typ, "default": default, "source": "contract_text"})
    else:
        warnings.append("contract is empty or of unsupported type %s" % type(contract).__name__)
    return fields, meta, warnings


# --------------------------------------------------------------------------- #
# init：先落盘契约对齐的 result.json 骨架
# --------------------------------------------------------------------------- #
def init_result(args):
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    for d in (output_dir, work_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    result = {
        "case_id": None,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": now_iso(), "script_version": VERSION, "command": "init"},
    }
    log = result["change_log"]
    append_change_log(log, "init_start", {"input": str(input_dir), "output": str(output_dir), "work": str(work_dir)})

    files = scan_input_files(input_dir)
    append_change_log(log, "input_scan", {"files_found": len(files), "names": [f.name for f in files]})

    # 定位 case.json 与 task.md
    case_path, task_path = None, None
    for f in files:
        low = f.name.lower()
        if case_path is None and (low == "case.json" or low.endswith("case.json") or (f.suffix.lower() == ".json" and "case" in low)):
            case_path = f
        if task_path is None and (low in ("task.md", "task.txt") or low.startswith("task")):
            task_path = f

    case_obj, task_text = None, None
    if case_path is not None:
        text, info = read_text_robust(case_path)
        if text is None:
            result["errors"].append({"code": "case_read_failed", "detail": info})
            append_change_log(log, "case_read_failed", info)
        else:
            obj, err = parse_json_robust(text)
            if err:
                result["errors"].append({"code": "case_json_invalid", "path": str(case_path), "detail": err})
                result["degradations"].append("case.json 无法解析，按缺失处理，不阻断后续")
                append_change_log(log, "case_json_invalid", {"path": str(case_path), "error": err})
            else:
                case_obj = obj
                result["case_id"] = find_case_id(case_obj)
                append_change_log(log, "case_parsed", {"path": str(case_path), "case_id": result["case_id"]})
    else:
        append_change_log(log, "case_missing", {"detail": "no case.json found in input"})

    if task_path is not None:
        text, info = read_text_robust(task_path)
        if text is not None:
            task_text = text
            append_change_log(log, "task_parsed", {"path": str(task_path), "bytes": len(text)})
        else:
            result["degradations"].append("task.md 读取失败：%s" % (info,))
    else:
        append_change_log(log, "task_missing", {"detail": "no task.md found in input"})

    # 输出契约发现与对齐
    contract, locator, warnings = extract_output_contract(case_obj, task_text)
    fields, meta, cwarnings = {}, [], []
    if contract is not None:
        fields, meta, cwarnings = normalize_contract(contract)
        warnings.extend(cwarnings)
        result["_output_contract_locator"] = locator
        result["_output_contract_meta"] = meta
        append_change_log(log, "contract_found", {"locator": locator, "field_count": len(fields)})
    else:
        result["errors"].append({"code": "output_contract_missing", "detail": warnings[-1] if warnings else "no contract"})
        result["degradations"].append("output_contract 缺失：契约字段为空，后续 validate 将提示需人工补齐")
        append_change_log(log, "contract_missing", {"detail": "output_contract not found"})

    for name, default in fields.items():
        result[name] = default
    append_change_log(log, "contract_backfilled", {"field_count": len(fields), "fields": list(fields.keys())})

    # 契约字段对齐账本（本变体特色产物）
    contract_map = {
        "generated_at": now_iso(),
        "locator": locator,
        "field_count": len(fields),
        "fields": meta,
        "warnings": warnings,
    }
    write_json(work_dir / "contract_map.json", contract_map)

    # 原始快照（仅小文本，保证可复核输入）
    snapshot_dir = work_dir / "snapshot"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for f in (case_path, task_path):
            if f is not None:
                try:
                    shutil.copy2(f, snapshot_dir / f.name)
                except OSError as e:
                    result["degradations"].append("快照复制失败 %s：%s" % (f.name, e))
        append_change_log(log, "snapshot_saved", {"dir": str(snapshot_dir)})
    except OSError as e:
        result["degradations"].append("快照目录创建失败：%s" % (e,))

    # 状态判断
    if not files:
        result["status"] = "input_error"
        result["errors"].append({"code": "empty_input", "detail": "input directory contains no files"})
        append_change_log(log, "empty_input", {})
    elif result["case_id"] is None:
        result["status"] = "input_error"
        result["errors"].append({"code": "case_id_missing", "detail": "cannot determine case_id from case.json"})
        append_change_log(log, "case_id_missing", {})
    else:
        result["status"] = "partial"
    result["runtime"]["completed_at"] = now_iso()
    append_change_log(log, "init_complete", {"status": result["status"], "contract_fields": len(fields)})
    write_json(output_dir / "result.json", result)
    return result


# --------------------------------------------------------------------------- #
# profile：数据画像
# --------------------------------------------------------------------------- #
def detect_delimiter(line):
    if not line:
        return ","
    best, best_n = ",", 0
    for c in (",", "\t", ";", "|"):
        n = line.count(c)
        if n > best_n:
            best, best_n = c, n
    return best


def profile_tabular(path):
    text, info = read_text_robust(path, max_bytes=TABULAR_MAX_TEXT)
    if text is None:
        return {"path": str(path), "error": info}
    lines = text.splitlines()
    delim = detect_delimiter(lines[0]) if lines else ","
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    except (csv.Error, UnicodeError) as e:
        return {"path": str(path), "error": str(e), "encoding": info.get("encoding")}
    if not rows:
        return {"path": str(path), "error": "empty", "encoding": info.get("encoding")}
    header = rows[0]
    data_rows = rows[1:]
    while data_rows and all((c or "").strip() == "" for c in data_rows[-1]):
        data_rows.pop()
    n_cols = len(header)
    cols = []
    for ci in range(n_cols):
        vals, missing = [], 0
        for r in data_rows:
            v = r[ci].strip() if ci < len(r) else ""
            if (v or "").strip().lower() in MISSING_TOKENS:
                missing += 1
            else:
                vals.append(v)
        col = {
            "index": ci,
            "name": header[ci] if ci < len(header) else "col_%d" % ci,
            "missing": missing,
            "non_missing": len(vals),
        }
        nums = None
        if vals:
            try:
                nums = [float(v) for v in vals]
                col["numeric"] = True
            except (ValueError, OverflowError):
                col["numeric"] = False
        else:
            col["numeric"] = False
        if nums is not None:
            finite = [x for x in nums if x == x and x not in (float("inf"), float("-inf"))]
            col["finite_count"] = len(finite)
            if finite:
                col["min"] = min(finite)
                col["max"] = max(finite)
                col["mean"] = sum(finite) / len(finite)
            col["n_unique"] = len(set(nums))
        else:
            col["n_unique"] = len(set(vals))
        cols.append(col)
    seen, dup = set(), 0
    for r in data_rows:
        key = tuple(r)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    return {
        "path": str(path), "kind": "tabular", "encoding": info.get("encoding"),
        "delimiter": delim, "n_columns": n_cols, "n_rows": len(data_rows),
        "duplicate_rows": dup, "columns": cols,
    }


def profile_sqlite(path):
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except (sqlite3.Error, ValueError) as e:
        return {"path": str(path), "error": "connect failed: %s" % e}
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
        except sqlite3.Error as e:
            return {"path": str(path), "error": "schema read failed: %s" % e}
        tables = []
        for name, typ, sql in cur.fetchall():
            t = {"name": name, "type": typ, "sql": sql}
            qname = '"%s"' % str(name).replace('"', '""')
            try:
                cur.execute("SELECT COUNT(*) FROM %s" % qname)
                t["n_rows"] = cur.fetchone()[0]
            except sqlite3.Error as e:
                t["n_rows"] = None
                t["error"] = str(e)
            try:
                cur.execute("PRAGMA table_info(%s)" % qname)
                t["columns"] = [
                    {"cid": c[0], "name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": c[5]}
                    for c in cur.fetchall()
                ]
            except sqlite3.Error as e:
                t["columns"] = []
                t["columns_error"] = str(e)
            tables.append(t)
        conn.close()
        return {"path": str(path), "kind": "sqlite", "tables": tables}
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return {"path": str(path), "error": str(e)}


def _sample_keys(item, limit=50):
    if isinstance(item, dict):
        return list(item.keys())[:limit]
    return []


def profile_json_data(path):
    text, info = read_text_robust(path)
    if text is None:
        return {"path": str(path), "error": info}
    obj, err = parse_json_robust(text)
    if err:
        return {"path": str(path), "error": err}
    if isinstance(obj, list):
        return {
            "path": str(path), "kind": "json", "type": "list", "length": len(obj),
            "item_keys": _sample_keys(obj[0]) if obj and isinstance(obj[0], dict) else [],
        }
    if isinstance(obj, dict):
        res = {"path": str(path), "kind": "json", "type": "object", "top_keys": list(obj.keys())[:100]}
        arrays = {k: len(v) for k, v in obj.items() if isinstance(v, list)}
        if arrays:
            res["array_keys"] = arrays
        return res
    return {"path": str(path), "kind": "json", "type": type(obj).__name__}


def profile_data(args):
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    for d in (output_dir, work_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    files = scan_input_files(input_dir, exclude_dirs=(output_dir, work_dir))
    profile = {
        "generated_at": now_iso(), "input_dir": str(input_dir),
        "files": [], "datasets": [], "warnings": [], "degradations": [],
    }
    for f in files:
        entry = {"path": str(f), "name": f.name, "suffix": f.suffix.lower()}
        try:
            entry["bytes"] = f.stat().st_size
        except OSError:
            entry["bytes"] = None
        low = f.name.lower()
        if low in ("case.json",) or (f.suffix.lower() == ".json" and "case" in low):
            entry["kind"] = "case_json"
        elif f.suffix.lower() == ".json":
            entry["kind"] = "json"
        elif f.suffix.lower() in (".csv", ".tsv", ".txt", ".dat", ".data"):
            entry["kind"] = "tabular_text"
        elif f.suffix.lower() in (".db", ".sqlite", ".sqlite3", ".db3"):
            entry["kind"] = "sqlite"
        elif f.suffix.lower() in (".md", ".yaml", ".yml", ".pdf", ".docx", ".xlsx", ".pptx"):
            entry["kind"] = "document_or_binary"
        else:
            entry["kind"] = "other"
        profile["files"].append(entry)
    for entry in profile["files"]:
        kind = entry["kind"]
        if kind == "tabular_text":
            ds = profile_tabular(entry["path"])
        elif kind == "sqlite":
            ds = profile_sqlite(entry["path"])
        elif kind == "json":
            ds = profile_json_data(entry["path"])
        else:
            continue
        profile["datasets"].append(ds)
    write_json(output_dir / "data_profile.json", profile)
    return profile


# --------------------------------------------------------------------------- #
# validate：契约字段对齐与产物核对
# --------------------------------------------------------------------------- #
def _load_case_obj(input_dir):
    for f in scan_input_files(input_dir):
        low = f.name.lower()
        if low == "case.json" or (f.suffix.lower() == ".json" and "case" in low):
            text, info = read_text_robust(f)
            if text is None:
                return None
            obj, err = parse_json_robust(text)
            if err is None and isinstance(obj, dict):
                return obj
    return None


def _looks_like_path(v):
    return (v.startswith("/") or "\\" in v or v.startswith("./") or v.startswith("../")
            or re.search(r"\.(md|json|csv|tsv|svg|png|pdf|docx|xlsx|pptx|py|sql|txt)$", v, re.I))


def _check(obj, report, name, ok, **detail):
    entry = {"check": name, "ok": ok}
    entry.update(detail)
    report["checks"].append(entry)


def validate_result(args):
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    result_path = output_dir / "result.json"
    report = {"generated_at": now_iso(), "result_path": str(result_path), "checks": [], "warnings": [], "passed": True}
    if not result_path.exists():
        _check(obj=None, report=report, name="result_exists", ok=False, detail="missing result.json")
        report["passed"] = False
    else:
        text, info = read_text_robust(result_path)
        obj, err = parse_json_robust(text)
        if err:
            _check(obj, report, "result_json_valid", False, detail=err)
            report["passed"] = False
        elif not isinstance(obj, dict):
            _check(obj, report, "result_is_object", False, detail="top level is %s" % type(obj).__name__)
            report["passed"] = False
        else:
            _validate_object(obj, report, input_dir, output_dir)
    write_json(output_dir / "validation_report.json", report)
    return report


def _validate_object(obj, report, input_dir, output_dir):
    cid = obj.get("case_id")
    _check(obj, report, "case_id_present", cid not in (None, ""), value=cid)
    status = obj.get("status")
    _check(obj, report, "status_valid", status in ALLOWED_STATUS, value=status)
    srcs = obj.get("sources")
    ok_src = isinstance(srcs, list) and all(
        isinstance(s, dict) and (s.get("locator") or s.get("path")) and "quote" in s for s in srcs
    )
    _check(obj, report, "sources_shape", ok_src, count=(len(srcs) if isinstance(srcs, list) else None))
    _check(obj, report, "change_log_present", isinstance(obj.get("change_log"), list),
           count=(len(obj.get("change_log")) if isinstance(obj.get("change_log"), list) else None))
    _check(obj, report, "uncertainty_present", "uncertainty" in obj)
    _check(obj, report, "pending_human_present", "pending_human" in obj)

    case_obj = _load_case_obj(input_dir)
    contract, locator, warnings = extract_output_contract(case_obj, None)
    if contract is not None:
        fields, meta, cw = normalize_contract(contract)
        missing = [f for f in fields if f not in obj]
        _check(obj, report, "contract_fields_complete", not missing, field_count=len(fields), missing=missing)
    else:
        _check(obj, report, "contract_fields_complete", False, detail="no output_contract found in case.json")

    artifacts = []
    for k, v in obj.items():
        if isinstance(v, str) and k not in ("status",) and _looks_like_path(v):
            artifacts.append((k, v))
    missing_artifacts = []
    for k, v in artifacts:
        p = Path(v)
        if not p.is_absolute():
            p = output_dir / p
        if not p.exists():
            missing_artifacts.append({"field": k, "path": v})
    _check(obj, report, "artifact_paths_exist", not missing_artifacts,
           total=len(artifacts), missing=missing_artifacts)

    bools = [c.get("ok") for c in report["checks"] if isinstance(c.get("ok"), bool)]
    report["passed"] = all(bools)


# --------------------------------------------------------------------------- #
# selftest：四类健壮性场景（空输入/坏 JSON/坏编码/缺参数）
# --------------------------------------------------------------------------- #
ENTRY_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def _run_subprocess(args_list, timeout=180):
    cmd = [sys.executable, "-B", ENTRY_SCRIPT] + args_list
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"exit_code": proc.returncode, "stdout": (proc.stdout or "")[-2000:], "stderr": (proc.stderr or "")[-2000:]}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "timeout after %ds" % timeout}
    except OSError as e:
        return {"exit_code": -1, "stdout": "", "stderr": "spawn failed: %s" % e}


def _score_scenario(name, run_info, result_path):
    ok_exit = run_info.get("exit_code") == 0
    result_ok, status = False, None
    if ok_exit and Path(result_path).exists():
        text, info = read_text_robust(result_path)
        obj, err = parse_json_robust(text)
        result_ok = err is None and isinstance(obj, dict) and obj.get("status") in ALLOWED_STATUS
        status = obj.get("status") if isinstance(obj, dict) else None
    return {
        "scenario": name, "exit_code": run_info.get("exit_code"), "result_valid": result_ok,
        "status": status, "ok": ok_exit and result_ok, "stderr": (run_info.get("stderr") or "")[:500],
    }


def selftest(args):
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    for d in (output_dir, work_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    base = Path(tempfile.mkdtemp(prefix="selftest_", dir=str(work_dir)))
    scenarios = []

    # 1) 空输入目录
    d1 = base / "empty_input"
    d1.mkdir()
    r1 = _run_subprocess(["init", "--input", str(d1), "--output", str(d1 / "out"), "--work", str(d1 / "work")])
    scenarios.append(_score_scenario("empty_input", r1, d1 / "out" / "result.json"))

    # 2) 坏 JSON（不可解析的 case.json）
    d2 = base / "bad_json"
    d2.mkdir()
    (d2 / "case.json").write_text("{ this is not valid json !!!", encoding="utf-8")
    (d2 / "task.md").write_text("# task\noutput_contract: {\"mean\": {\"type\": \"number\"}}\n", encoding="utf-8")
    r2 = _run_subprocess(["init", "--input", str(d2), "--output", str(d2 / "out"), "--work", str(d2 / "work")])
    scenarios.append(_score_scenario("bad_json", r2, d2 / "out" / "result.json"))

    # 3) 坏编码（GBK 编码且带 BOM/非法字节）
    d3 = base / "bad_encoding"
    d3.mkdir()
    gbk = '{"case_id": "gbk-1", "output_contract": {"mean": {"type": "number"}, "table_path": {"type": "string"}}, "note": "中文注释"}'
    (d3 / "case.json").write_bytes(gbk.encode("gbk"))
    (d3 / "task.md").write_text("# task\n", encoding="utf-8")
    r3 = _run_subprocess(["init", "--input", str(d3), "--output", str(d3 / "out"), "--work", str(d3 / "work")])
    scenarios.append(_score_scenario("bad_encoding", r3, d3 / "out" / "result.json"))

    # 4) 缺参数（使用默认 /input /app/output /app/work；只需退出码 0，写盘失败自动降级）
    r4 = _run_subprocess(["init"])
    scenarios.append({
        "scenario": "missing_params", "exit_code": r4.get("exit_code"), "result_valid": r4.get("exit_code") == 0,
        "status": None, "ok": r4.get("exit_code") == 0, "stderr": (r4.get("stderr") or "")[:500],
    })

    report = {
        "generated_at": now_iso(), "scenarios": scenarios,
        "all_pass": all(s.get("ok") for s in scenarios),
        "work_dir": str(base),
    }
    write_json(output_dir / "selftest_report.json", report)
    return report


# --------------------------------------------------------------------------- #
# 分发入口
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="sci-analysis-contract",
        description="科研数据分析执行 Skill 的辅助脚本（init/profile/validate/selftest），仅标准库。",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--input", default="/input", help="输入目录（默认 /input）")
        sp.add_argument("--output", default="/app/output", help="产物目录（默认 /app/output）")
        sp.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")

    sp = sub.add_parser("init", help="落盘契约对齐的 result.json 骨架")
    add_common(sp)
    sp = sub.add_parser("profile", help="生成数据画像 data_profile.json")
    add_common(sp)
    sp = sub.add_parser("validate", help="校验 result.json 的契约对齐与产物路径")
    add_common(sp)
    sp = sub.add_parser("selftest", help="运行四类健壮性自检并输出报告")
    add_common(sp)
    return p


def dispatch(args):
    """由 main.py 调用：执行子命令并保证永不崩溃（退出码恒 0）。"""
    try:
        if args.command == "init":
            init_result(args)
        elif args.command == "profile":
            profile_data(args)
        elif args.command == "validate":
            validate_result(args)
        elif args.command == "selftest":
            selftest(args)
        else:
            raise ValueError("unknown command: %s" % args.command)
    except Exception:
        tb = traceback.format_exc(limit=8)
        # 永不崩溃：至少给 init 落一份最小合法 result.json，其余命令保留 stderr 现场
        if args.command == "init":
            try:
                out = Path(args.output)
                out.mkdir(parents=True, exist_ok=True)
                minimal = {
                    "case_id": None, "status": "input_error",
                    "sources": [], "uncertainty": [], "pending_human": [],
                    "change_log": [{"ts": now_iso(), "action": "unhandled_exception"}],
                    "errors": [{"code": "unhandled_exception", "detail": tb}],
                }
                (out / "result.json").write_text(json.dumps(minimal, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        sys.stderr.write(tb)
        return 0
    return 0
