#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec 核心实现（仅 Python 标准库）。

设计取向：结果骨架先行、字段对齐显式化、降级与不确定性显式登记。
任何异常都尽力落盘最小合法 result.json，退出码保持 0。

子命令：
  init     写 /app/output/result.json 骨架（case_id + 全部契约字段 + status=partial）
  profile  扫描 /input 材料，写 /app/output/data_profile.json（编码/行列/缺失/重复/数值摘要/SQLite 结构 + 字段对齐）
  validate 核对 result.json 与 output_contract、sources 证据、limitations/uncertainty 显式性
"""

import argparse
import csv
import io
import json
import math
import os
import re
import sqlite3
import sys
import time

__version__ = "1.1.0"

MAX_FILE_BYTES = 2 * 1024 * 1024      # 单文件扫描上限
MAX_ROW_PROFILE = 200_000             # 数值摘要最大扫描行
MAX_DISTINCT = 5000                   # 类别列 distinct 采样上限
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
_NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "missing", ".", "-", "nil"}

# ---------------------------------------------------------------------------
# 通用 IO
# ---------------------------------------------------------------------------


def utc_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_text_auto(path):
    """按候选编码顺序解码，全部失败则用 utf-8 + replace 兜底，绝不抛异常。"""
    raw = read_bytes(path)
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def read_json_auto(path):
    text, _enc = read_text_auto(path)
    return json.loads(text)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
    os.replace(tmp, path)


def normalize_column(name):
    """字段对齐：去单位括号、小写、非字母数字折叠为下划线。"""
    s = str(name)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = s.lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", s)
    s = s.strip("_")
    return re.sub(r"_+", "_", s)


def parse_number(token):
    if token is None:
        return None
    t = str(token).strip()
    if t.lower() in _NULL_TOKENS:
        return None
    try:
        v = float(t)
    except (ValueError, TypeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def safe_mean(values):
    return sum(values) / len(values) if values else None


def safe_std(values, mean):
    if len(values) < 2:
        return None
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(var)


# ---------------------------------------------------------------------------
# 发现工具
# ---------------------------------------------------------------------------


def get_case_id(case):
    for k in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def get_contract(case):
    for k in ("output_contract", "outputContract", "output_schema", "outputSchema"):
        v = case.get(k)
        if isinstance(v, dict) and v:
            return v
    return {}


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    case_path = os.path.join(input_dir, "case.json")

    status = "partial"
    case_id = None
    contract = {}
    errors = []
    degradations = []
    limitations = []
    change_log = []

    if not os.path.isfile(case_path):
        status = "input_error"
        errors.append("缺失 /input/case.json，无法确定 case_id 与 output_contract")
        limitations.append({"item": "输入缺失", "effect": "无法回填契约字段", "fallback": "空骨架交付并等待补料"})
        change_log.append({"t": utc_ts(), "step": "init", "action": "发现 case.json 缺失", "result": "input_error"})
    else:
        try:
            case = read_json_auto(case_path)
            case_id = get_case_id(case)
            contract = get_contract(case)
            if not contract:
                degradations.append("case.json 中未发现 output_contract/outputContract 键，将回填空契约")
                limitations.append({"item": "output_contract 缺失", "effect": "无法预填题目要求字段", "fallback": "仅交付通用字段，待 task.md 补全"})
            if not case_id:
                limitations.append({"item": "case_id 缺失", "effect": "结果缺少输入标识", "fallback": "case_id 记 null，待人工补"})
            change_log.append({"t": utc_ts(), "step": "init", "action": "读取 case.json 并解析契约", "case_id": case_id, "contract_keys": sorted(contract.keys())})
        except Exception as e:  # noqa: BLE001 —— 坏 JSON 不得阻断落盘
            status = "input_error"
            errors.append("init 解析 case.json 失败: %s" % e)
            degradations.append("case.json 无法解析，按最小骨架继续")
            change_log.append({"t": utc_ts(), "step": "init", "action": "case.json 解析失败", "error": str(e), "result": "input_error"})

    skeleton = {"case_id": case_id, "status": status}
    for k, v in contract.items():
        skeleton[k] = contract_default(v)
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["limitations"] = limitations
    skeleton["pending_human"] = []
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations
    skeleton["change_log"] = change_log
    skeleton["runtime"] = {"engine": "sci-analysis-exec", "version": __version__, "init_at": utc_ts()}

    write_json(result_path, skeleton)
    return skeleton


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


def list_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.isfile(p):
                found.append(p)
    return sorted(found)


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return "csv"
    if ext in (".tsv", ".tab"):
        return "tsv"
    if ext == ".json":
        return "json"
    if ext in (".jsonl", ".ndjson"):
        return "jsonl"
    if ext in (".sqlite", ".sqlite3", ".db"):
        return "sqlite"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in (".txt", ".log"):
        return "text"
    if ext in (".xlsx", ".xls"):
        return "xlsx"
    return "other"


def sniff_delimiter(sample):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:  # noqa: BLE001
        return None


def profile_delimited(path, kind):
    text, enc = read_text_auto(path)
    sample = text[:8192]
    delim = sniff_delimiter(sample) or ("\t" if kind == "tsv" else ",")
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        return {"file": path, "kind": kind, "encoding": enc, "error": "空文件或无表头"}
    header = [str(h).strip() for h in header]
    data = []
    for r in reader:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        data.append(r)
    n_rows = len(data)
    n_cols = len(header)

    col_stats = []
    for j, name in enumerate(header):
        vals = []
        missing = 0
        numeric = []
        for r in data[:MAX_ROW_PROFILE]:
            v = r[j] if j < len(r) else None
            if v is None or (isinstance(v, str) and v.strip().lower() in _NULL_TOKENS):
                missing += 1
                continue
            vals.append(v)
            num = parse_number(v)
            if num is not None:
                numeric.append(num)
        stats = {"column": name, "normalized": normalize_column(name), "n": len(vals), "missing": missing}
        if numeric and len(numeric) >= 0.5 * max(len(vals), 1):
            m = safe_mean(numeric)
            stats["numeric"] = True
            stats["count"] = len(numeric)
            stats["min"] = min(numeric)
            stats["max"] = max(numeric)
            stats["mean"] = m
            stats["std"] = safe_std(numeric, m)
        else:
            stats["numeric"] = False
            stats["distinct_approx"] = len({str(v) for v in vals[:MAX_DISTINCT]})
        col_stats.append(stats)

    seen = set()
    dup = 0
    for r in data:
        key = tuple(str(c).strip() if c is not None else "" for c in r[:n_cols])
        if key in seen:
            dup += 1
        else:
            seen.add(key)

    return {
        "file": path, "kind": kind, "encoding": enc, "delimiter": delim,
        "rows": n_rows, "columns": n_cols, "duplicate_rows": dup,
        "header": header, "column_stats": col_stats,
    }


def profile_json(path):
    text, enc = read_text_auto(path)
    obj = json.loads(text)
    if isinstance(obj, list):
        keys = sorted({k for row in obj if isinstance(row, dict) for k in row})
        return {"file": path, "kind": "json", "encoding": enc, "rows": len(obj), "columns": len(keys), "keys": keys, "shape": "list_of_objects"}
    if isinstance(obj, dict):
        return {"file": path, "kind": "json", "encoding": enc, "keys": sorted(obj.keys()), "shape": "object"}
    return {"file": path, "kind": "json", "encoding": enc, "shape": "scalar", "value_type": type(obj).__name__}


def profile_sqlite(path):
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        out = []
        for t in tables:
            if not re.fullmatch(r"[A-Za-z0-9_]+", t):
                continue
            cols = con.execute('PRAGMA table_info("%s")' % t).fetchall()
            n = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            out.append({"table": t, "rows": n, "columns": [{"name": c[1], "type": c[2]} for c in cols]})
        return {"file": path, "kind": "sqlite", "tables": out}
    finally:
        con.close()


def cmd_profile(input_dir, output_dir, work_dir=None):
    os.makedirs(output_dir, exist_ok=True)
    report = {"generated_at": utc_ts(), "input_dir": input_dir, "files": [], "errors": [], "degradations": []}
    if not os.path.isdir(input_dir):
        report["errors"].append("输入目录不存在: %s" % input_dir)
        write_json(os.path.join(output_dir, "data_profile.json"), report)
        return report

    for p in list_files(input_dir):
        try:
            sz = os.path.getsize(p)
            if sz > MAX_FILE_BYTES:
                report["degradations"].append({"file": p, "issue": "超过 2MB 上限，跳过"})
                continue
            kind = classify(p)
            if kind in ("csv", "tsv"):
                entry = profile_delimited(p, kind)
            elif kind == "json":
                entry = profile_json(p)
            elif kind == "jsonl":
                entry = {"file": p, "kind": "jsonl", "note": "逐行 JSON，需按行解析"}
            elif kind == "sqlite":
                entry = profile_sqlite(p)
            elif kind in ("md", "text"):
                text, enc = read_text_auto(p)
                entry = {"file": p, "kind": kind, "encoding": enc, "chars": len(text), "lines": text.count("\n") + 1}
            elif kind == "xlsx":
                entry = {"file": p, "kind": "xlsx", "note": "标准库无法读取 xlsx，需 openpyxl/pandas；无则转 pending_human"}
                report["degradations"].append({"file": p, "issue": "xlsx 需第三方库"})
            else:
                entry = {"file": p, "kind": kind, "note": "未识别类型，跳过"}
            entry["bytes"] = sz
            report["files"].append(entry)
        except Exception as e:  # noqa: BLE001
            report["errors"].append({"file": p, "error": str(e)})

    align = {}
    for f in report["files"]:
        for cs in f.get("column_stats", []):
            align.setdefault(cs["normalized"], []).append({"file": os.path.basename(f["file"]), "column": cs["column"]})
        if f.get("shape") == "list_of_objects":
            for k in f.get("keys", []):
                align.setdefault(normalize_column(k), []).append({"file": os.path.basename(f["file"]), "column": k})
        for tb in f.get("tables", []):
            for c in tb.get("columns", []):
                align.setdefault(normalize_column(c["name"]), []).append({"file": os.path.basename(f["file"]), "table": tb["table"], "column": c["name"]})
    report["field_alignment"] = align

    write_json(os.path.join(output_dir, "data_profile.json"), report)
    return report


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(input_dir, output_dir, work_dir=None):
    os.makedirs(output_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    report = {"passed": True, "checks": [], "issues": []}

    def check(name, cond, detail):
        report["checks"].append({"check": name, "ok": bool(cond), "detail": detail})
        if not cond:
            report["passed"] = False
            report["issues"].append(detail)

    if not os.path.isfile(result_path):
        check("exists", False, "result.json 不存在")
        write_json(os.path.join(output_dir, "validation.json"), report)
        return report

    try:
        r = read_json_auto(result_path)
    except Exception as e:  # noqa: BLE001
        check("parse", False, "result.json 解析失败: %s" % e)
        write_json(os.path.join(output_dir, "validation.json"), report)
        return report

    check("case_id", bool(r.get("case_id")), "case_id 非空")
    check("status_valid", r.get("status") in VALID_STATUS, "status ∈ %s" % sorted(VALID_STATUS))

    contract = {}
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        try:
            contract = get_contract(read_json_auto(case_path))
        except Exception:  # noqa: BLE001
            contract = {}
    missing_keys = [k for k in contract if k not in r]
    check("contract_fields", not missing_keys, "缺失契约字段: %s" % (missing_keys or "无"))

    srcs = r.get("sources") or []
    bad_srcs = [s for s in srcs if not (isinstance(s, dict) and s.get("locator") and s.get("quote"))]
    check("sources_evidence", not bad_srcs, "sources 每项须含 locator+quote，异常 %d 项" % len(bad_srcs))

    check("change_log", isinstance(r.get("change_log"), list) and len(r.get("change_log")) > 0, "change_log 非空")
    check("limitations_explicit", "limitations" in r, "limitations 字段存在（降级显式化）")
    check("uncertainty_explicit", "uncertainty" in r, "uncertainty 字段存在（不确定显式化）")

    write_json(os.path.join(output_dir, "validation.json"), report)
    return report


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="sci-analysis-exec",
        description="科研数据分析执行：init/profile/validate（--input/--output/--work 可置于子命令前后）",
    )
    p.add_argument("command", nargs="?", default="init",
                   help="子命令：init | profile | validate（缺省 init）")
    p.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    p.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    p.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def main(argv=None):
    parser = build_parser()
    args, _unknown = parser.parse_known_args(argv)
    cmd = args.command
    try:
        if cmd == "init":
            cmd_init(args.input, args.output, args.work)
        elif cmd == "profile":
            cmd_profile(args.input, args.output, args.work)
        elif cmd == "validate":
            cmd_validate(args.input, args.output, args.work)
        else:
            parser.print_help()
            cmd_init(args.input, args.output, args.work)  # 未知子命令降级为 init，仍退出 0
    except Exception as e:  # noqa: BLE001 —— 兜底：任何异常落盘最小合法产物，退出码 0
        try:
            minimal = {
                "case_id": None,
                "status": "input_error",
                "sources": [],
                "uncertainty": [],
                "limitations": [{"item": "脚本异常", "effect": str(e), "fallback": "最小合法产物"}],
                "pending_human": [],
                "errors": [str(e)],
                "change_log": [{"t": utc_ts(), "step": cmd, "error": str(e)}],
            }
            os.makedirs(args.output, exist_ok=True)
            write_json(os.path.join(args.output, "result.json"), minimal)
        except Exception:  # noqa: BLE001
            pass
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
