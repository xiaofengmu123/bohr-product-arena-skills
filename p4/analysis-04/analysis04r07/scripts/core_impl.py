#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec-robust 核心实现（纯标准库）。

设计取舍（本变体以“鲁棒性”为第一目标）：
1. 所有入口函数绝不向调用者抛异常；异常一律转成 errors/degradations 记录，退出码恒为 0。
2. 写盘走“临时文件 + os.replace”原子写；失败再降级直写，再失败写最小合法 result.json。
3. 读文本按 utf-8-sig -> utf-8 -> gb18030 -> cp1252 -> latin-1(替换) 依次尝试，坏编码只降级不崩溃。
4. 单文件大小上限、行数上限、SQLite 只读访问均有兜底；超大文件跳过并记录原因。
5. result.json 是唯一事实源：init 先落盘 partial 骨架，之后 profile/align/snapshot/validate/log/set
   都在不破坏既有成果的前提下原子增量更新 change_log，保证任何中断都不丢已得结果。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

SKILL_NAME = "sci-analysis-exec-robust"
VERSION = "2.0.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 文本类文件读取上限；超过则跳过并在 profile/degradations 中记录。
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024
# CSV 完整扫描的行数上限；超过则截断采样并在结果中标注 truncated。
PROFILE_ROW_CAP = 200_000
# 快照复制单文件上限（原样保留用于复核，但避免吞掉超大二进制）。
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024

ALLOWED_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 编码尝试顺序：latin-1 永不出错，作为最终保底。
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1")

MISSING_MARKERS = {
    "", "na", "n/a", "null", "none", "nan", ".", "-", "--", "missing",
    "n.d.", "nd", "n/a/", "<na>", "na/", "void", "blank",
}

STANDARD_KEYS = (
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "limitations", "artifacts", "runtime",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_paths(args) -> tuple:
    """参数优先级：显式 --input/--output/--work > 环境变量 > 平台默认值。"""
    inp = getattr(args, "input", None) or os.environ.get("SKILL_INPUT_DIR") or DEFAULT_INPUT
    out = getattr(args, "output", None) or os.environ.get("SKILL_OUTPUT_DIR") or DEFAULT_OUTPUT
    work = getattr(args, "work", None) or os.environ.get("SKILL_WORK_DIR") or DEFAULT_WORK
    return os.path.normpath(inp), os.path.normpath(out), os.path.normpath(work)


def ensure_dirs(out: str, work: str) -> list:
    notes = []
    for d in (out, work):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            notes.append("dir_create_failed:%s:%s" % (d, e))
    return notes


# --------------------------------------------------------------------------- #
# 读取原语（鲁棒性核心）
# --------------------------------------------------------------------------- #

def read_bytes_sized(path: str, max_bytes: int):
    """按大小上限读原始字节；返回 (bytes|None, note)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat_failed:%s" % e
    if size > max_bytes:
        return None, "file_too_large:%d>%d" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            return fh.read(), None
    except Exception as e:  # noqa: BLE001
        return None, "read_failed:%s" % e


def decode_bytes(raw: bytes) -> tuple:
    """按编码优先级解码；返回 (text, encoding_used, note)。"""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", "encoding_fallback_replaced"


def read_text_robust(path: str, max_bytes: int = MAX_TEXT_FILE_BYTES) -> tuple:
    """返回 (text|None, encoding_used|None, note|None)。永不抛异常。"""
    raw, note = read_bytes_sized(path, max_bytes)
    if raw is None:
        return None, None, note
    text, enc, dec_note = decode_bytes(raw)
    return text, enc, note or dec_note


def load_json_robust(path: str, max_bytes: int = MAX_TEXT_FILE_BYTES) -> tuple:
    """返回 (obj|None, note|None)。坏 JSON / 坏编码 / 超大文件均不抛异常。"""
    if not os.path.isfile(path):
        return None, "file_not_found:%s" % path
    text, enc, note = read_text_robust(path, max_bytes)
    if text is None:
        return None, note
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        return None, "json_decode_error:%s" % e
    return obj, ("encoding:%s" % enc if enc in ("gb18030", "cp1252", "latin-1", "utf-8-replace") else None)


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #

def _abs_norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def scan_input(input_dir: str, exclude: tuple = ()) -> tuple:
    """递归列出输入文件（不跟随符号链接），返回 (files, note)。

    exclude 传入需排除的目录（通常是 output/work），避免把产物目录扫成输入、
    造成自引用。仅按绝对规范化路径比较。
    """
    if not os.path.isdir(input_dir):
        return [], "input_dir_missing:%s" % input_dir
    excl = {_abs_norm(p) for p in exclude if p}
    files = []
    for root, dirs, names in os.walk(input_dir, followlinks=False):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".")
                   and _abs_norm(os.path.join(root, d)) not in excl]
        for name in sorted(names):
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            if _abs_norm(path) in excl:
                continue
            if os.path.islink(path):
                continue
            try:
                st = os.stat(path)
            except OSError as e:
                files.append({"path": path, "rel": os.path.relpath(path, input_dir),
                              "name": name, "size": -1, "error": "stat_failed:%s" % e})
                continue
            files.append({
                "path": path,
                "rel": os.path.relpath(path, input_dir),
                "name": name,
                "size": st.st_size,
                "ext": os.path.splitext(name)[1].lower(),
                "mtime": int(st.st_mtime),
            })
    files.sort(key=lambda f: f.get("rel", f.get("name", "")))
    return files, None


def classify_file(f: dict) -> str:
    name = f.get("name", "").lower()
    ext = f.get("ext", "")
    if name == "case.json":
        return "case"
    if ext in (".csv", ".tsv", ".tab", ".txt", ".dat", ".data"):
        return "tabular"
    if ext == ".json":
        return "json"
    if ext in (".sqlite", ".sqlite3", ".db"):
        return "sqlite"
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext in (".yaml", ".yml"):
        return "yaml"
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".tif", ".tiff"):
        return "image"
    return "other"


def find_case(input_dir: str, explicit: str = None, exclude: tuple = ()) -> str:
    if explicit and os.path.isfile(explicit):
        return explicit
    direct = os.path.join(input_dir, "case.json")
    if os.path.isfile(direct):
        return direct
    files, _ = scan_input(input_dir, exclude=exclude)
    for f in files:
        if f.get("name", "").lower() == "case.json":
            return f["path"]
    for f in files:
        if f.get("ext") == ".json" and "case" in f.get("name", "").lower():
            return f["path"]
    return None


# --------------------------------------------------------------------------- #
# 契约发现
# --------------------------------------------------------------------------- #

def discover_case_id(case) -> str:
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "id", "task_id", "taskId"):
        v = case.get(key)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return None


def _normalize_spec(spec):
    if isinstance(spec, str):
        return {"name": spec, "type": "any", "description": ""}
    if isinstance(spec, dict):
        name = spec.get("name") or spec.get("field") or spec.get("key") or spec.get("id")
        return {
            "name": str(name) if name is not None else None,
            "type": str(spec.get("type") or spec.get("dtype") or "any"),
            "description": str(spec.get("description") or spec.get("desc") or ""),
            "required": bool(spec.get("required", False)),
        }
    return {"name": None, "type": "any", "description": ""}


def discover_output_contract(case) -> dict:
    """从 case.json 提取 output_contract；返回 {field_name: spec}。缺省返回 {}。"""
    if not isinstance(case, dict):
        return {}
    raw = None
    for key in ("output_contract", "outputContract", "output_schema", "outputSchema",
                "contract", "result_schema", "resultSchema"):
        if key in case:
            raw = case[key]
            break
    if raw is None:
        return {}
    contract = {}
    if isinstance(raw, dict):
        for name, spec in raw.items():
            contract[str(name)] = _normalize_spec(spec)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                contract[item] = {"name": item, "type": "any", "description": ""}
            elif isinstance(item, dict):
                spec = _normalize_spec(item)
                if spec["name"] is not None:
                    contract[spec["name"]] = spec
    return contract


def default_for_spec(spec: dict):
    t = str(spec.get("type") or "").lower()
    if t in ("list", "array", "[]"):
        return []
    if t in ("object", "dict", "map", "{}"):
        return {}
    return None


# --------------------------------------------------------------------------- #
# result.json 骨架与原子写
# --------------------------------------------------------------------------- #

def minimal_result() -> dict:
    return {
        "case_id": None,
        "status": "input_error",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "artifacts": {
            "cleaned_table": [],
            "stats": [],
            "figures": [],
            "query_results": [],
            "analysis_code": [],
            "previews": [],
            "profile": [],
            "snapshot": [],
        },
        "runtime": {
            "skill": SKILL_NAME,
            "version": VERSION,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "started_at": now_iso(),
        },
    }


def write_json_safe(path: str, obj) -> tuple:
    """原子写 JSON；返回 (ok, note)。任何失败都不抛异常。"""
    tmp = None
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".result-", suffix=".tmp", dir=d or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True, None
    except Exception as e:  # noqa: BLE001
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:  # noqa: BLE001
                pass
        # 降级：直接写
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)
            return True, "direct_write_after_replace_failure:%s" % e
        except Exception as e2:  # noqa: BLE001
            return False, "write_failed:%s" % e2


def load_result(result_path: str) -> tuple:
    if os.path.isfile(result_path):
        obj, note = load_json_robust(result_path)
        if isinstance(obj, dict):
            return obj, note
    return None, "result_missing_or_invalid"


def ensure_standard_keys(obj: dict) -> dict:
    for key in STANDARD_KEYS:
        if key not in obj:
            obj[key] = [] if key in ("sources", "uncertainty", "pending_human",
                                     "change_log", "errors", "degradations", "limitations") else None
    if "artifacts" not in obj or not isinstance(obj.get("artifacts"), dict):
        obj["artifacts"] = minimal_result()["artifacts"]
    if "runtime" not in obj or not isinstance(obj.get("runtime"), dict):
        obj["runtime"] = minimal_result()["runtime"]
    return obj


def trace(output_dir: str, step: str, action: str, status: str = None, note: str = None, **extra) -> None:
    """向 result.json 增量追加一条 change_log（不覆盖既有字段）。"""
    result_path = os.path.join(output_dir, "result.json")
    obj, _ = load_result(result_path)
    if obj is None:
        obj = minimal_result()
        obj["status"] = "partial"
    ensure_standard_keys(obj)
    entry = {"at": now_iso(), "step": step, "action": action}
    if status is not None:
        entry["status"] = status
    if note:
        entry["note"] = note
    for k, v in extra.items():
        entry[k] = v
    obj["change_log"].append(entry)
    write_json_safe(result_path, obj)


# --------------------------------------------------------------------------- #
# Step 0：init —— 前 3 分钟落盘 partial 骨架
# --------------------------------------------------------------------------- #

def run_init(input_dir: str, output_dir: str, work_dir: str, case_path: str = None) -> dict:
    ensure_dirs(output_dir, work_dir)
    result_path = os.path.join(output_dir, "result.json")

    # 重入保护：已有合法骨架时不覆盖已得结果，只追加一条记录。
    existing, existing_note = load_result(result_path)
    if isinstance(existing, dict) and existing.get("case_id"):
        ensure_standard_keys(existing)
        existing["change_log"].append({
            "at": now_iso(), "step": "init", "action": "init_reentry_preserve_existing",
            "note": existing_note,
        })
        write_json_safe(result_path, existing)
        _write_state(work_dir, existing)
        return existing

    files, scan_note = scan_input(input_dir, exclude=(output_dir, work_dir))
    errors, degradations = [], []
    if scan_note:
        errors.append(scan_note)
    if not files:
        errors.append("input_empty")

    case_path = find_case(input_dir, case_path, exclude=(output_dir, work_dir))
    case = None
    case_note = None
    if case_path is None:
        errors.append("case_json_not_found")
    else:
        case, case_note = load_json_robust(case_path)
        if case is None:
            errors.append("case_json_unreadable:%s" % case_note)
            degradations.append(case_note)
        elif case_note:
            degradations.append("case_json_encoding:%s" % case_note)

    case_id = discover_case_id(case) if isinstance(case, dict) else None
    contract = discover_output_contract(case) if isinstance(case, dict) else {}
    if case_id is None:
        errors.append("case_id_not_found")
    if not contract:
        degradations.append("output_contract_missing_or_empty")

    if not files:
        status = "insufficient_evidence"
    elif case is None or case_id is None:
        status = "input_error"
    else:
        status = "partial"

    result = minimal_result()
    result["case_id"] = case_id
    result["status"] = status
    for name, spec in contract.items():
        result[name] = default_for_spec(spec)
    result["errors"] = errors
    result["degradations"] = degradations
    result["input"] = {
        "case_path": case_path,
        "input_dir": input_dir,
        "files": [{"rel": f.get("rel"), "name": f.get("name"), "size": f.get("size"),
                   "kind": classify_file(f)} for f in files],
    }
    result["output_contract_fields"] = sorted(contract.keys())
    result["change_log"].append({
        "at": now_iso(), "step": "init", "action": "落盘 partial 结果骨架",
        "status": status, "errors": list(errors), "degradations": list(degradations),
    })
    write_json_safe(result_path, result)
    _write_state(work_dir, result)
    return result


def _write_state(work_dir: str, result: dict) -> None:
    state = {
        "at": now_iso(),
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "contract_fields": result.get("output_contract_fields", []),
        "next": ["profile", "align(若需)", "analysis(清洗/统计/图/查询)", "validate"],
    }
    write_json_safe(os.path.join(work_dir, "state.json"), state)


# --------------------------------------------------------------------------- #
# Step 1：profile —— 数据画像
# --------------------------------------------------------------------------- #

def _safe_mean_stdev(values: list):
    n = len(values)
    if n == 0:
        return None, None
    try:
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / n
        return mean, math.sqrt(var)
    except (OverflowError, ValueError):
        return None, None


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        pass
    first = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {d: first.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ","


def profile_csv(path: str) -> dict:
    text, enc, note = read_text_robust(path)
    if text is None:
        return {"error": note, "skipped": True}
    if not text.strip():
        return {"rows": 0, "data_rows": 0, "columns": 0, "empty": True, "encoding": enc}

    delim = _sniff_delimiter(text[:4096])
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        return {"rows": 0, "data_rows": 0, "columns": 0, "empty": True, "encoding": enc}
    if header and header[0].startswith("\ufeff"):
        header[0] = header[0][1:]

    ncols = len(header)
    data_rows = []
    ragged = 0
    truncated = False
    for row in reader:
        if len(row) != ncols:
            ragged += 1
        data_rows.append(row)
        if len(data_rows) > PROFILE_ROW_CAP:
            truncated = True
            break

    columns = []
    for ci in range(ncols):
        vals = []
        for r in data_rows:
            vals.append(r[ci] if ci < len(r) else "")
        missing = 0
        numeric = []
        non_numeric = 0
        for v in vals:
            if v.strip().lower() in MISSING_MARKERS:
                missing += 1
                continue
            try:
                numeric.append(float(v.strip().replace(",", "").replace(" ", "")))
            except (ValueError, TypeError):
                non_numeric += 1
        mean, stdev = _safe_mean_stdev(numeric)
        columns.append({
            "header": header[ci],
            "missing": missing,
            "non_missing": len(vals) - missing,
            "numeric_count": len(numeric),
            "non_numeric": non_numeric,
            "mean": round(mean, 6) if mean is not None else None,
            "stdev": round(stdev, 6) if stdev is not None else None,
            "min": round(min(numeric), 6) if numeric else None,
            "max": round(max(numeric), 6) if numeric else None,
        })

    seen = {}
    for r in data_rows:
        key = tuple(v.strip() for v in r)
        seen[key] = seen.get(key, 0) + 1
    dup_value_groups = sum(1 for c in seen.values() if c > 1)

    return {
        "delimiter": delim,
        "encoding": enc,
        "header": header,
        "columns_n": ncols,
        "data_rows": len(data_rows),
        "total_rows": len(data_rows) + 1,
        "ragged_rows": ragged,
        "truncated": truncated,
        "duplicate_row_values": dup_value_groups,
        "columns": columns,
        "note": note,
    }


def profile_json(path: str) -> dict:
    obj, note = load_json_robust(path)
    if obj is None:
        return {"error": note}
    if isinstance(obj, dict):
        return {"top_type": "object", "key_count": len(obj), "keys": list(obj.keys())[:200]}
    if isinstance(obj, list):
        first_type = type(obj[0]).__name__ if obj else None
        return {"top_type": "array", "length": len(obj), "first_item_type": first_type}
    return {"top_type": type(obj).__name__}


def profile_sqlite(path: str) -> dict:
    try:
        con = sqlite3.connect(path)
    except sqlite3.Error as e:
        return {"error": "sqlite_open_failed:%s" % e}
    try:
        cur = con.cursor()
        try:
            cur.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            pass
        rows = cur.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
        tables = []
        for name, typ in rows:
            safe = name.replace('"', '""')
            info = {"name": name, "type": typ, "columns": [], "rows": None, "error": None}
            try:
                cols = cur.execute('PRAGMA table_info("%s")' % safe).fetchall()
                info["columns"] = [
                    {"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])}
                    for c in cols
                ]
            except sqlite3.Error as e:
                info["error"] = "pragma_failed:%s" % e
            try:
                if typ == "table":
                    info["rows"] = cur.execute('SELECT COUNT(*) FROM "%s"' % safe).fetchone()[0]
            except sqlite3.Error as e:
                info["error"] = (info["error"] or "") + " count_failed:%s" % e
            tables.append(info)
        return {"tables": tables}
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def profile_text(path: str) -> dict:
    text, enc, note = read_text_robust(path)
    if text is None:
        return {"error": note, "skipped": True}
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()][:3]
    return {"lines": len(lines), "chars": len(text), "encoding": enc,
            "first_lines": nonempty, "note": note}


def run_profile(input_dir: str, output_dir: str, work_dir: str) -> dict:
    ensure_dirs(output_dir, work_dir)
    files, scan_note = scan_input(input_dir, exclude=(output_dir, work_dir))
    entries = []
    summary = {"files": len(files), "tabular": 0, "json": 0, "sqlite": 0,
               "markdown": 0, "other": 0, "errors": 0, "skipped": 0, "scan_note": scan_note}
    for f in files:
        kind = classify_file(f)
        entry = {"path": f.get("path"), "rel": f.get("rel"), "size": f.get("size"), "kind": kind}
        if f.get("error"):
            entry["profile"] = {"error": f["error"], "skipped": True}
            summary["errors"] += 1
        else:
            try:
                if kind == "tabular":
                    entry["profile"] = profile_csv(f["path"])
                    summary["tabular"] += 1
                elif kind == "sqlite":
                    entry["profile"] = profile_sqlite(f["path"])
                    summary["sqlite"] += 1
                elif kind in ("json", "case"):
                    entry["profile"] = profile_json(f["path"])
                    summary["json"] += 1
                elif kind in ("markdown", "yaml"):
                    entry["profile"] = profile_text(f["path"])
                    summary["markdown"] += 1
                else:
                    entry["profile"] = {"skipped": True, "reason": "unsupported_kind:%s" % kind}
                    summary["other"] += 1
            except Exception as e:  # noqa: BLE001
                entry["profile"] = {"error": "unexpected:%s" % e, "skipped": True}
                summary["errors"] += 1
            if isinstance(entry.get("profile"), dict):
                if entry["profile"].get("skipped"):
                    summary["skipped"] += 1
                elif entry["profile"].get("error"):
                    summary["errors"] += 1
        entries.append(entry)

    profile = {"at": now_iso(), "input_dir": input_dir, "summary": summary, "files": entries}
    write_json_safe(os.path.join(output_dir, "data_profile.json"), profile)
    # 自动同步到 result.json
    result_path = os.path.join(output_dir, "result.json")
    obj, _ = load_result(result_path)
    if obj is None:
        obj = minimal_result()
    ensure_standard_keys(obj)
    obj["artifacts"]["profile"] = ["data_profile.json"]
    obj["change_log"].append({
        "at": now_iso(), "step": "profile", "action": "数据画像完成",
        "note": "files=%d errors=%d skipped=%d" % (summary["files"], summary["errors"], summary["skipped"]),
    })
    write_json_safe(result_path, obj)
    return profile


# --------------------------------------------------------------------------- #
# Step 1b：align —— 字段对齐（口径显式化）
# --------------------------------------------------------------------------- #

def _norm_token(s: str) -> str:
    t = str(s).strip().lower()
    t = t.replace("_", " ").replace("-", " ")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)


def _headers_of(profile: dict):
    headers = profile.get("header") if isinstance(profile, dict) else None
    return headers or []


def run_align(dict_path: str, input_dir: str, output_dir: str) -> dict:
    ensure_dirs(output_dir, "")
    dic, note = load_json_robust(dict_path)
    fields = []
    if isinstance(dic, dict):
        fields = dic.get("fields") if isinstance(dic.get("fields"), list) else []
    elif isinstance(dic, list):
        fields = dic
    norm_fields = []
    for f in fields:
        if isinstance(f, str):
            norm_fields.append({"name": f, "aliases": [f], "unit": None, "description": ""})
        elif isinstance(f, dict):
            nm = f.get("name") or f.get("field") or f.get("key")
            if nm is None:
                continue
            aliases = [nm] + [a for a in (f.get("aliases") or []) if a]
            norm_fields.append({
                "name": str(nm), "aliases": aliases,
                "unit": f.get("unit"), "description": f.get("description") or f.get("desc") or "",
            })

    files, _ = scan_input(input_dir, exclude=(output_dir,))
    per_file = []
    for f in files:
        if classify_file(f) != "tabular":
            continue
        p = profile_csv(f["path"])
        headers = p.get("header") or []
        per_file.append({"rel": f.get("rel"), "headers": headers})

    # 建立 header -> 规范 token 的索引
    matched = []
    for nf in norm_fields:
        target_tokens = {_norm_token(a) for a in nf["aliases"]}
        hits = []
        for pf in per_file:
            for h in pf["headers"]:
                if _norm_token(h) in target_tokens:
                    hits.append({"rel": pf["rel"], "header": h})
        matched.append({
            "field": nf["name"], "aliases": nf["aliases"], "unit": nf["unit"],
            "description": nf["description"], "status": "matched" if hits else "not_found",
            "matches": hits,
        })

    used_headers = set()
    for m in matched:
        for h in m["matches"]:
            used_headers.add((h["rel"], h["header"]))
    unmatched = []
    for pf in per_file:
        for h in pf["headers"]:
            if (pf["rel"], h) not in used_headers:
                unmatched.append({"rel": pf["rel"], "header": h})

    alignment = {
        "at": now_iso(), "dict_path": dict_path, "dict_note": note,
        "fields": matched, "unmatched_headers": unmatched,
        "matched_count": sum(1 for m in matched if m["status"] == "matched"),
        "not_found_count": sum(1 for m in matched if m["status"] != "matched"),
    }
    write_json_safe(os.path.join(output_dir, "alignment.json"), alignment)
    _write_alignment_md(output_dir, alignment)
    trace(output_dir, "align", "字段对齐完成",
          note="matched=%d not_found=%d" % (alignment["matched_count"], alignment["not_found_count"]))
    return alignment


def _write_alignment_md(output_dir: str, alignment: dict) -> None:
    lines = ["# 字段对齐表", "", "| 字典字段 | 状态 | 匹配到的表头 | 单位 |", "|-|-|-|-|"]
    for m in alignment["fields"]:
        hits = "; ".join("%s::%s" % (h["rel"], h["header"]) for h in m["matches"]) or "-"
        unit = m.get("unit") or "-"
        lines.append("| %s | %s | %s | %s |" % (m["field"], m["status"], hits, unit))
    if alignment["unmatched_headers"]:
        lines.append("")
        lines.append("## 未匹配表头")
        for u in alignment["unmatched_headers"]:
            lines.append("- %s :: %s" % (u["rel"], u["header"]))
    try:
        with open(os.path.join(output_dir, "alignment.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Step 1c：snapshot —— 原始快照
# --------------------------------------------------------------------------- #

def run_snapshot(input_dir: str, work_dir: str, output_dir: str) -> dict:
    ensure_dirs(output_dir, work_dir)
    files, note = scan_input(input_dir, exclude=(output_dir, work_dir))
    snap_dir = os.path.join(work_dir, "snapshot")
    manifest = []
    for f in files:
        item = {"rel": f.get("rel"), "size": f.get("size"), "copied": False}
        if f.get("error"):
            item["reason"] = f["error"]
        elif f.get("size", 0) > MAX_SNAPSHOT_BYTES:
            item["reason"] = "too_large_for_snapshot"
        else:
            dest = os.path.join(snap_dir, f["rel"])
            try:
                os.makedirs(os.path.dirname(dest) or snap_dir, exist_ok=True)
                shutil.copy2(f["path"], dest)
                with open(dest, "rb") as fh:
                    item["sha256"] = hashlib.sha256(fh.read()).hexdigest()
                item["copied"] = True
            except Exception as e:  # noqa: BLE001
                item["reason"] = "copy_failed:%s" % e
        manifest.append(item)
    snap = {"at": now_iso(), "snapshot_dir": snap_dir, "scan_note": note, "files": manifest,
            "copied": sum(1 for m in manifest if m["copied"])}
    write_json_safe(os.path.join(work_dir, "snapshot_manifest.json"), snap)
    result_path = os.path.join(output_dir, "result.json")
    obj, _ = load_result(result_path)
    if obj is None:
        obj = minimal_result()
    ensure_standard_keys(obj)
    obj["artifacts"]["snapshot"] = ["work/snapshot_manifest.json", "work/snapshot/"]
    obj["change_log"].append({
        "at": now_iso(), "step": "snapshot", "action": "原始快照完成",
        "note": "copied=%d/%d" % (snap["copied"], len(manifest)),
    })
    write_json_safe(result_path, obj)
    return snap


# --------------------------------------------------------------------------- #
# Step 4：validate —— 结果校验
# --------------------------------------------------------------------------- #

def _artifact_like(key: str) -> bool:
    k = key.lower()
    return any(tok in k for tok in ("path", "file", "artifact", "figure", "table",
                                    "plot", "chart", "query", "preview", "pdf", "png",
                                    "svg", "xlsx", "docx", "pptx", "csv", "json"))


def _looks_like_path(value) -> bool:
    if not isinstance(value, str):
        return False
    return (os.sep in value or "/" in value or "\\" in value
            or value.lower().endswith((".csv", ".json", ".md", ".png", ".svg", ".pdf",
                                       ".xlsx", ".docx", ".pptx", ".sqlite", ".db")))


def run_validate(input_dir: str, output_dir: str, result_path: str = None) -> dict:
    result_path = result_path or os.path.join(output_dir, "result.json")
    issues, warnings = [], []
    obj = None
    if not os.path.isfile(result_path):
        issues.append({"check": "result_exists", "severity": "error", "detail": "result.json missing"})
    else:
        obj, note = load_json_robust(result_path)
        if obj is None:
            issues.append({"check": "result_parse", "severity": "error", "detail": note})
        elif not isinstance(obj, dict):
            issues.append({"check": "result_type", "severity": "error", "detail": "not an object"})
        else:
            if not obj.get("case_id"):
                issues.append({"check": "case_id", "severity": "error", "detail": "case_id 缺失或为空"})
            if obj.get("status") not in ALLOWED_STATUSES:
                issues.append({"check": "status", "severity": "error",
                               "detail": "status=%r 不在 %s" % (obj.get("status"), ALLOWED_STATUSES)})
            for key in ("sources", "uncertainty", "change_log", "pending_human", "errors",
                        "degradations", "limitations"):
                if key in obj and not isinstance(obj[key], (list, str)):
                    issues.append({"check": key, "severity": "error", "detail": "类型应为 list/str"})

            # 契约字段完整性
            case_path = find_case(input_dir, exclude=(output_dir,))
            if case_path:
                case, _ = load_json_robust(case_path)
                contract = discover_output_contract(case) if isinstance(case, dict) else {}
                for name in contract:
                    if name not in obj:
                        issues.append({"check": "contract_field:%s" % name,
                                       "severity": "error", "detail": "缺少 output_contract 字段"})
            else:
                warnings.append({"check": "contract", "detail": "case.json 未找到，跳过契约字段核对"})

            # 主产物路径真实性
            for key, value in obj.items():
                if not _artifact_like(key):
                    continue
                candidates = value if isinstance(value, list) else ([value] if value is not None else [])
                for c in candidates:
                    if not _looks_like_path(c):
                        continue
                    p = c if os.path.isabs(c) else os.path.join(output_dir, c)
                    if not os.path.exists(p):
                        issues.append({"check": "artifact_path:%s" % key,
                                       "severity": "error", "detail": "产物不存在：%s" % c})

            # sources 溯源质量
            sources = obj.get("sources")
            if isinstance(sources, list):
                if not sources:
                    warnings.append({"check": "sources", "detail": "sources 为空，关键结论应逐字溯源"})
                for i, s in enumerate(sources):
                    if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
                        warnings.append({"check": "source:%d" % i,
                                         "detail": "source 需含 locator + quote"})

    valid = not any(i["severity"] == "error" for i in issues)
    report = {"at": now_iso(), "valid": valid, "result_path": result_path,
              "issues": issues, "warnings": warnings}
    write_json_safe(os.path.join(output_dir, "validation_report.json"), report)
    status = "ok" if valid else "partial"
    trace(output_dir, "validate", "结果校验", status=status,
          note="valid=%s errors=%d warnings=%d" % (valid, len(issues), len(warnings)))
    return report


# --------------------------------------------------------------------------- #
# 增量修改：log / set（Agent 不必手改 JSON，避免损坏）
# --------------------------------------------------------------------------- #

def run_log(output_dir: str, step: str, action: str, status: str = None, note: str = None) -> dict:
    step = step or "unknown"
    action = action or "unknown"
    if status and status not in ALLOWED_STATUSES:
        status = None
    trace(output_dir, step, action, status=status, note=note)
    obj, _ = load_result(os.path.join(output_dir, "result.json"))
    return obj or minimal_result()


def run_set(output_dir: str, key: str, value: str, kind: str = "str") -> dict:
    result_path = os.path.join(output_dir, "result.json")
    obj, _ = load_result(result_path)
    if obj is None:
        obj = minimal_result()
        obj["status"] = "partial"
    ensure_standard_keys(obj)
    if not key:
        obj["change_log"].append({"at": now_iso(), "step": "set", "action": "set_skipped_missing_key"})
        write_json_safe(result_path, obj)
        return obj
    if kind == "json":
        try:
            val = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            val = value
    elif kind == "int":
        try:
            val = int(value)
        except (ValueError, TypeError):
            val = value
    elif kind == "float":
        try:
            val = float(value)
        except (ValueError, TypeError):
            val = value
    elif kind == "bool":
        val = str(value).lower() in ("1", "true", "yes", "y")
    else:
        val = value
    obj[key] = val
    obj["change_log"].append({"at": now_iso(), "step": "set", "action": "set %s" % key, "note": "kind=%s" % kind})
    write_json_safe(result_path, obj)
    return obj


def run_all(input_dir: str, output_dir: str, work_dir: str, case_path: str = None) -> dict:
    """一键骨架：init -> profile -> validate（任何一步失败都不影响退出码 0）。"""
    r = run_init(input_dir, output_dir, work_dir, case_path)
    try:
        run_profile(input_dir, output_dir, work_dir)
    except Exception as e:  # noqa: BLE001
        trace(output_dir, "run_all", "profile 失败", note=str(e))
    try:
        run_validate(input_dir, output_dir)
    except Exception as e:  # noqa: BLE001
        trace(output_dir, "run_all", "validate 失败", note=str(e))
    return r


def ensure_minimal_result(output_dir: str, work_dir: str = None) -> dict:
    """最终兜底：确保至少存在一份最小合法 result.json。"""
    ensure_dirs(output_dir, work_dir or output_dir)
    path = os.path.join(output_dir, "result.json")
    obj, _ = load_result(path)
    if obj is None:
        obj = minimal_result()
        obj["errors"].append("unexpected_failure_fallback_minimal_result")
        write_json_safe(path, obj)
    return obj
