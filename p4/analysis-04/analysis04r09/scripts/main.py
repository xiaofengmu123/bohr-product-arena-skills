#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec 核心脚本：init / profile / validate 三个子命令。

只依赖 Python 标准库。设计目标（与评分维度对应）：
  init     —— 先落盘一版合法 result.json 骨架：case_id + 全部 output_contract
              字段（缺值 null）+ status=partial，任何中断都不丢已得结果。
  profile  —— 扫描 /input，产出 data_profile.json：编码、行列数、字段、缺失、
              重复行、数值摘要、SQLite 表结构。供 Agent 做字段对齐与口径核对。
  validate —— 校验 result.json 的契约完整性与产物存在性（含 SVG/PDF 检查），
              产出 validation.json，供"跑通不等于算对"的最后一道闸门。

任何子命令都不应崩溃：异常一律捕获并记入 errors/degradations，进程退出码 0。
"""
import argparse
import csv
import io
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# 运行时缺省参数（与 Harness 契约一致）
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记录
DATA_EXTS = {".csv", ".tsv", ".txt", ".json", ".sqlite", ".sqlite3", ".db", ".data", ".dat"}
SKIP_NAMES = {"case.json", "task.md", "result.json", "validation.json", "data_profile.json"}
KNOWN_EXTS = (".csv", ".md", ".json", ".svg", ".png", ".pdf", ".docx",
              ".pptx", ".xlsx", ".tsv", ".html", ".txt", ".log", ".db", ".sqlite")
NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "missing", "不适用", "缺失", "-"}


def _safe_stream():
    """Windows 控制台编码容错，避免 print 中文时崩溃。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(errors="replace")
            except Exception:
                pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- I/O 基础

def decode_bytes(raw):
    """按 utf-8-sig / utf-8 / gbk 尝试解码，最后 latin-1 兜底（永不失败）。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("latin-1"), "latin-1"


def read_text(path):
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return None, "binary", "检测到 NUL 字节，判定为二进制文件"
    text, enc = decode_bytes(raw)
    return text, enc, None


def read_json_safe(path):
    """返回 (data, error)。任何解析失败都返回 None + 错误描述，不抛异常。"""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, str(e)
    if b"\x00" in raw[:4096]:
        return None, "二进制内容，不是 JSON"
    text, enc = decode_bytes(raw)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败({enc}): {e}"


def write_json(path, obj, fallback=None):
    """写 JSON。失败时降级为最小合法对象，仍返回 False 标记降级。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        minimal = fallback or {
            "case_id": obj.get("case_id") if isinstance(obj, dict) else None,
            "status": "input_error",
            "errors": [f"写盘失败: {e}"],
        }
        try:
            path.write_text(json.dumps(minimal, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return False


def discover_case_json(input_dir):
    """先找 case.json，否则找首个含 id/output_contract 键的 JSON。"""
    root = Path(input_dir)
    direct = root / "case.json"
    if direct.exists():
        return direct
    if root.exists():
        for p in sorted(root.glob("*.json")):
            data, err = read_json_safe(p)
            if err or not isinstance(data, dict):
                continue
            if any(k in data for k in ("case_id", "caseId", "challenge_id", "id", "output_contract", "outputContract")):
                return p
    return None


def extract_case_id(data):
    if not isinstance(data, dict):
        return None
    for k in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_contract(data):
    """从 case.json 发现 output_contract，返回字段名列表。"""
    if not isinstance(data, dict):
        return []
    for k in ("output_contract", "outputContract", "contract"):
        v = data.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            fields = [str(x) for x in v.keys()]
            return fields
        if isinstance(v, list):
            out = []
            for item in v:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    for fk in ("field", "name", "key", "id"):
                        if fk in item and isinstance(item[fk], str):
                            out.append(item[fk])
                            break
            return out
    return []


# ---------------------------------------------------------------- 数据画像

def scan_data_files(input_dir):
    files, skipped = [], []
    root = Path(input_dir)
    if not root.exists():
        return files, ["输入目录不存在"]
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name.startswith(".") or name in SKIP_NAMES:
            continue
        if any(part in ("output", "work") for part in p.relative_to(root).parts):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            skipped.append(f"{p.relative_to(root)} 超限({size}B)")
            continue
        if p.suffix.lower() in DATA_EXTS:
            files.append(p)
    return files, skipped


def _is_na(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in NA_TOKENS)


def parse_delimited(text):
    """嗅探分隔符并解析为行列表；失败返回空列表。"""
    if not text:
        return []
    sample = text[:4096]
    delim = None
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except Exception:
        counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
        if any(counts.values()):
            delim = max(counts, key=counts.get)
    if not delim:
        delim = ","
    try:
        return list(csv.reader(io.StringIO(text), delimiter=delim))
    except Exception:
        return []


def numeric_summary(header, rows):
    """对数值比例足够高的列给 n/missing/mean/median/std/min/max。"""
    if not rows:
        return {}
    ncols = max(len(header), max((len(r) for r in rows), default=0))
    header = list(header) + [f"col{i}" for i in range(len(header), ncols)]
    summary = {}
    for ci in range(ncols):
        colname = header[ci] if ci < len(header) else f"col{ci}"
        vals, missing = [], 0
        for r in rows:
            v = r[ci] if ci < len(r) else None
            if _is_na(v):
                missing += 1
                continue
            s = str(v).strip()
            try:
                vals.append(float(s))
            except (ValueError, TypeError):
                pass
        total = max(len(rows), 1)
        if vals and (len(vals) / total) >= 0.5:
            mean = statistics.fmean(vals)
            median = statistics.median(vals)
            if len(vals) > 1:
                stdev = statistics.stdev(vals)
            else:
                stdev = 0.0
            summary[colname] = {
                "n": len(vals), "missing": missing, "mean": mean,
                "median": median, "std": stdev, "min": min(vals), "max": max(vals),
            }
    return summary


def missing_counts(header, rows):
    if not rows:
        return {}
    ncols = max(len(header), max((len(r) for r in rows), default=0))
    header = list(header) + [f"col{i}" for i in range(len(header), ncols)]
    counts = {}
    for ci in range(ncols):
        colname = header[ci] if ci < len(header) else f"col{ci}"
        counts[colname] = sum(1 for r in rows if ci >= len(r) or _is_na(r[ci]))
    return counts


def _dedup_count(rows):
    seen, dup = set(), 0
    for r in rows:
        key = tuple(r)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    return dup


def profile_sqlite(path):
    out = {"kind": "sqlite", "tables": []}
    uri = "file:{}?mode=ro".format(path.as_posix())
    con = None
    for attempt in (uri, str(path)):
        try:
            con = sqlite3.connect(attempt, uri=(attempt == uri))
            break
        except sqlite3.Error:
            continue
    if con is None:
        out["error"] = "无法打开 SQLite 数据库"
        return out
    try:
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (tname,) in tables:
            q = '"' + tname.replace('"', '""') + '"'
            cols = con.execute("PRAGMA table_info({})".format(q)).fetchall()
            try:
                n = con.execute("SELECT COUNT(*) FROM {}".format(q)).fetchone()[0]
            except sqlite3.Error:
                n = None
            out["tables"].append({
                "name": tname,
                "rows": n,
                "columns": [{"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": c[5]} for c in cols],
            })
    except sqlite3.Error as e:
        out["error"] = "SQLite 查询失败: {}".format(e)
    finally:
        con.close()
    return out


def profile_json(path, text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {"kind": "json", "error": "JSON 解析失败: {}".format(e)}
    rows = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        rows = data
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    if not rows:
        return {"kind": "json", "note": "未识别为表格型 JSON（list/dict-of-list）", "rows": 0}
    header = []
    for r in rows:
        for k in r.keys():
            if k not in header:
                header.append(k)
    as_rows = [[r.get(k) for k in header] for r in rows]
    return {
        "kind": "json", "rows": len(as_rows), "columns": header,
        "missing": missing_counts(header, as_rows),
        "numeric_summary": numeric_summary(header, as_rows),
        "duplicate_rows": _dedup_count(as_rows),
    }


def profile_file(path):
    rel = str(path)
    info = {"locator": rel, "size_bytes": path.stat().st_size, "encoding": None}
    try:
        raw = path.read_bytes()
    except OSError as e:
        info["error"] = str(e)
        return info
    suffix = path.suffix.lower()
    if suffix in (".sqlite", ".sqlite3", ".db"):
        info.update(profile_sqlite(path))
        return info
    if suffix == ".json":
        text, enc, err = read_text(path)
        if err:
            info.update({"kind": "json", "error": err, "encoding": enc})
            return info
        info["encoding"] = enc
        info.update(profile_json(path, text))
        return info
    # csv/tsv/txt/data
    text, enc, err = read_text(path)
    info["encoding"] = enc
    if err:
        info.update({"kind": "text", "error": err})
        return info
    rows = parse_delimited(text)
    if not rows:
        info.update({"kind": "text", "rows": 0, "note": "未解析出表格行"})
        return info
    header = rows[0]
    body = rows[1:]
    info.update({
        "kind": "table",
        "delimiter_detected": True,
        "rows": len(body),
        "columns": list(header),
        "missing": missing_counts(header, body),
        "duplicate_rows": _dedup_count(body),
        "numeric_summary": numeric_summary(header, body),
    })
    return info


# ---------------------------------------------------------------- 子命令

def _result_path(output_dir):
    return Path(output_dir) / "result.json"


def run_init(input_dir, output_dir, work_dir):
    _safe_stream()
    inp, out, work = Path(input_dir), Path(output_dir), Path(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    result = {
        "case_id": None,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
    }
    change = []
    case_json = discover_case_json(inp)
    contract_fields = []

    if case_json is None:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append("未在输入目录找到 case.json 或可识别任务描述文件")
        result["pending_human"].append("请提供案例输入材料（case.json/task.md/数据文件）")
        change.append(now_iso() + " init: 未找到 case.json，status=insufficient_evidence")
    else:
        data, err = read_json_safe(case_json)
        if err:
            result["status"] = "input_error"
            result["errors"].append("case.json 解析失败: {}".format(err))
            result["case_id"] = case_json.stem
            change.append(now_iso() + " init: case.json 解析失败 -> input_error")
        else:
            result["case_id"] = extract_case_id(data) or case_json.stem
            contract_fields = extract_contract(data)
            change.append(
                now_iso() + " init: 发现 case.json，case_id={}，output_contract 字段 {} 个".format(
                    result["case_id"], len(contract_fields)))

    # 契约字段原样回填，缺值 null；不覆盖 case_id/status 等核心键
    for f in contract_fields:
        if f not in result:
            result[f] = None
    result["change_log"] = change

    write_json(_result_path(out), result)
    print("init done: status={} contract_fields={} -> {}".format(
        result["status"], len(contract_fields), _result_path(out)))
    return 0


def run_profile(input_dir, output_dir, work_dir):
    _safe_stream()
    inp, out = Path(input_dir), Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files, skipped = scan_data_files(inp)
    profile = {
        "generated_at": now_iso(),
        "input_dir": str(inp),
        "files": [],
        "errors": [],
        "skipped": skipped,
    }
    for f in files:
        prof = profile_file(f)
        if prof.get("error"):
            profile["errors"].append({"locator": str(f), "error": prof["error"]})
        else:
            profile["files"].append(prof)

    prof_path = out / "data_profile.json"
    write_json(prof_path, profile)

    # 增量：把画像结果追加进 result.json 的 change_log（不覆盖已有内容）
    try:
        rp = _result_path(out)
        data, err = read_json_safe(rp)
        if not err and isinstance(data, dict):
            log = data.setdefault("change_log", [])
            if not isinstance(log, list):
                log = []
            log.append(now_iso() + " profile: 扫描 {} 个文件，{} 个错误，画像 -> data_profile.json".format(
                len(files), len(profile["errors"])))
            data["change_log"] = log
            write_json(rp, data)
    except Exception:
        pass

    print("profile done: files={} errors={} -> {}".format(
        len(files), len(profile["errors"]), prof_path))
    return 0


def _looks_like_path(s):
    if not isinstance(s, str) or not s:
        return False
    if s.startswith(("http://", "https://", "data:")):
        return False
    # 绝对路径/盘符/./ 前缀，或带分隔符，判为路径
    if s.startswith(("/", "\\", "./", "../")) or re.match(r"^[A-Za-z]:[\\/]", s):
        return True
    if "/" in s or "\\" in s:
        return True
    # 纯文件名：无空格、无箭头、短，且以已知扩展名结尾
    if s.endswith(KNOWN_EXTS) and " " not in s and "->" not in s and len(s) < 120:
        return True
    return False


def _collect_paths(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_paths(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_paths(v, acc)
    elif isinstance(obj, str) and _looks_like_path(obj):
        acc.append(obj)


def _check_artifact(path, output_dir):
    p = Path(path)
    if not p.is_absolute():
        p = Path(output_dir) / p
    if not p.exists():
        return {"path": path, "exists": False, "note": "文件不存在"}
    verdict = {"path": path, "exists": True}
    if p.suffix.lower() == ".svg":
        try:
            ET.parse(str(p))
            verdict["svg_parse"] = "ok"
        except Exception as e:
            verdict["svg_parse"] = "fail: {}".format(e)
    elif p.suffix.lower() == ".pdf":
        try:
            head = p.read_bytes()[:5]
            verdict["pdf_magic"] = "ok" if head == b"%PDF-" else "fail: 头部非 %PDF-"
        except Exception as e:
            verdict["pdf_magic"] = "fail: {}".format(e)
    return verdict


def run_validate(input_dir, output_dir, work_dir):
    _safe_stream()
    inp, out = Path(input_dir), Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    checks = []
    warnings = []

    rp = _result_path(out)
    result, err = read_json_safe(rp)
    if err:
        checks.append({"check": "result.json 可解析", "pass": False, "detail": err})
        report = {"generated_at": now_iso(), "result_path": str(rp), "passed": False,
                  "checks": checks, "warnings": warnings}
        write_json(out / "validation.json", report)
        print("validate done: FAIL (result.json 不可解析)")
        return 0
    checks.append({"check": "result.json 可解析", "pass": True, "detail": "ok"})

    def add(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail if not ok else ""})

    add("case_id 非空", isinstance(result.get("case_id"), str) and result["case_id"].strip() != "",
        "case_id={!r}".format(result.get("case_id")))
    add("status 合法", result.get("status") in STATUSES, "status={!r}".format(result.get("status")))

    case_json = discover_case_json(inp)
    contract = []
    if case_json is not None:
        data, _e = read_json_safe(case_json)
        if _e is None:
            contract = extract_contract(data)
    missing_fields = [f for f in contract if f not in result]
    add("output_contract 字段全部回填", not missing_fields, "缺失: {}".format(missing_fields))

    add("sources 为数组", isinstance(result.get("sources"), list))
    src_bad = 0
    for s in (result.get("sources") or []):
        if not (isinstance(s, dict) and str(s.get("locator", "")).strip() and str(s.get("quote", "")).strip()):
            src_bad += 1
    add("sources 每项含 locator + 逐字 quote", src_bad == 0, "{} 项不合格".format(src_bad))

    add("uncertainty 存在", result.get("uncertainty") in (None,) or isinstance(result.get("uncertainty"), (list, str)))
    add("change_log 为数组", isinstance(result.get("change_log"), list))

    # 产物存在性与格式
    paths = []
    _collect_paths(result, paths)
    seen = []
    for s in paths:
        if s not in seen:
            seen.append(s)
    for p in seen:
        v = _check_artifact(p, out)
        if not v.get("exists"):
            warnings.append(v)
        elif "svg_parse" in v and v["svg_parse"] != "ok":
            warnings.append(v)
        elif "pdf_magic" in v and v["pdf_magic"] != "ok":
            warnings.append(v)
    add("引用的主产物文件均存在且格式可解析", not warnings,
        "{} 个文件问题: {}".format(len(warnings), warnings[:3]))

    passed = all(c["pass"] for c in checks)
    report = {"generated_at": now_iso(), "result_path": str(rp), "passed": passed,
              "checks": checks, "warnings": warnings}
    write_json(out / "validation.json", report)
    print("validate done: {} (checks={} warnings={})".format(
        "PASS" if passed else "FAIL", len(checks), len(warnings)))
    return 0


# ---------------------------------------------------------------- 入口

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研数据分析执行：init 落盘骨架 / profile 画像 / validate 校验",
    )
    p.add_argument("subcommand", nargs="?", default="help",
                   choices=["init", "profile", "validate", "help"])
    p.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="产物目录（默认 /app/output）")
    p.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    return p


def main(argv=None):
    _safe_stream()
    args = build_parser().parse_args(argv)
    if args.subcommand in ("help", None):
        build_parser().print_help()
        return 0
    try:
        if args.subcommand == "init":
            return run_init(args.input, args.output, args.work)
        if args.subcommand == "profile":
            return run_profile(args.input, args.output, args.work)
        if args.subcommand == "validate":
            return run_validate(args.input, args.output, args.work)
    except Exception as e:  # 兜底：任何子命令异常都不让进程非零退出
        try:
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            write_json(out / "result.json",
                       {"case_id": None, "status": "input_error",
                        "errors": ["未捕获异常: {}".format(e)],
                        "sources": [], "uncertainty": [], "change_log": []})
        except Exception:
            pass
        print("fatal(fallback): {}".format(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
