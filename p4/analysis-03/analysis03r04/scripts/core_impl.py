#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 的核心实现（仅标准库）。

本模块被 main.py 与 selftest.py 复用，只做“可复核”的确定性工作：
输入发现、契约提取、CSV/SQLite 读取、列画像、异常检测、图型推荐、
证据/局限/不确定性记录、结果骨架与自检。**不做**渲染（渲染由 Agent
在运行时用可用的 scientific-python 资源完成），也不在缺数据时编造任何
数值、图型或结论。凡无法回到源材料的结论一律记为 pending_human 缺口。
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
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
SQLITE_EXTS = (".db", ".sqlite", ".sqlite3")
UNSUPPORTED_BIN_EXTS = (".xlsx", ".xls", ".parquet", ".sav", ".dta", ".rds", ".h5")

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
STATUS_RANK = {"input_error": 0, "insufficient_evidence": 1, "partial": 2, "ok": 3}

RESERVED_KEYS = (
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "limitations", "change_log", "errors", "degradations", "runtime",
    "artifacts", "_contract_template",
)

LIMITATION_LEVELS = ("info", "warning", "missing", "critical", "blocked")
EVIDENCE_LEVELS = ("verified", "inferred", "assumed", "missing")


# --------------------------------------------------------------------------- #
# 基础 IO 工具
# --------------------------------------------------------------------------- #
def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级；返回 (text, encoding, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d > %d)" % (size, limit)
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_",
                               suffix=os.path.splitext(path)[1])
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


def load_json_robust(path):
    text, _enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e


def parse_json_value(v):
    v = (v or "").strip()
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """扫描输入目录；不假设固定文件名。返回 (files, degradations)。"""
    files = {"case_json": None, "task_md": None, "data": [], "materials": []}
    deg = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            low = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                deg.append("unreadable: %s (%s)" % (rel, e))
                continue
            if low == "case.json":
                files["case_json"] = p
            elif low == "task.md":
                files["task_md"] = p
            elif low.endswith(SQLITE_EXTS):
                files["data"].append(p)
            elif low.endswith(UNSUPPORTED_BIN_EXTS):
                deg.append("unsupported_binary: %s（仅标准库，跳过）" % rel)
            elif low.endswith(TEXT_EXTS):
                files["materials"].append(p)
            if size > MAX_FILE_BYTES and low.endswith(TEXT_EXTS + SQLITE_EXTS):
                deg.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
    return files, deg


def summarize_files(files):
    return {
        "case_json": bool(files["case_json"]),
        "task_md": bool(files["task_md"]),
        "data": [os.path.basename(p) for p in files["data"]],
        "materials": [os.path.basename(p) for p in files["materials"]],
    }


# --------------------------------------------------------------------------- #
# 契约提取
# --------------------------------------------------------------------------- #
CONTRACT_KEYS = (
    "output_contract", "outputcontract", "result_contract", "resultcontract",
    "contract", "result_schema", "resultschema", "expected_output",
    "expectedoutput",
)


def _norm_key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def find_contract_blocks(obj, path="$"):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = _norm_key(k)
            if nk in CONTRACT_KEYS and (isinstance(v, dict) or isinstance(v, list)):
                out.append((k, v, path + "." + str(k)))
            out.extend(find_contract_blocks(v, path + "." + str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(find_contract_blocks(v, path + "[%d]" % i))
    return out


def _pick_contract(blocks):
    """优先键名精确匹配 output_contract；其次叶子数最多者。"""
    if not blocks:
        return None, None
    for k, v, p in blocks:
        if _norm_key(k) == "outputcontract":
            return v, p
    return max(blocks, key=lambda b: _leaf_count(b[1]))[1], \
        max(blocks, key=lambda b: _leaf_count(b[1]))[2]


def _leaf_count(v):
    if isinstance(v, dict):
        return sum(_leaf_count(x) for x in v.values())
    if isinstance(v, list):
        return 1 + sum(_leaf_count(x) for x in v)
    return 1


def template_from_value(v):
    """保留结构：dict 递归、list -> []、标量 -> None（回填模板）。"""
    if isinstance(v, dict):
        return {str(k): template_from_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def _extract_contract_from_md(text):
    """在 task.md 中找 fenced JSON 块或内联 JSON 片段，含契约语义即采用。"""
    if not text:
        return None, "task.md"
    for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S):
        block = m.group(1).strip()
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        found = find_contract_blocks(obj, "task.md")
        if found:
            v, p = _pick_contract(found)
            if v is not None:
                return v, "task.md#" + p
    # 内联：截取 "output_contract" 后第一个平衡花括号
    for key in ("output_contract", "outputContract", "result_schema", "expected_output"):
        i = text.find('"%s"' % key)
        if i < 0:
            i = text.find("'%s'" % key)
        if i < 0:
            continue
        j = text.find("{", i)
        if j < 0:
            continue
        depth = 0
        for k in range(j, len(text)):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[j:k + 1])
                    except json.JSONDecodeError:
                        break
                    found = find_contract_blocks(obj, "task.md")
                    v, p = _pick_contract(found)
                    return (v, "task.md#" + p) if v is not None else (None, None)
    return None, "task.md"


def extract_contract(case_obj, task_md_text):
    """从 case.json（各可能键名）或 task.md 提取 output_contract。
    返回 (value_or_None, source_path)。"""
    found = find_contract_blocks(case_obj if isinstance(case_obj, dict) else {})
    v, p = _pick_contract(found)
    if v is not None:
        return v, "case.json#" + p
    v2, p2 = _extract_contract_from_md(task_md_text)
    if v2 is not None:
        return v2, p2
    return None, None


def compute_case_id(case_obj, input_dir, files):
    for k in ("case_id", "id", "challenge_id", "challengeId"):
        v = case_obj.get(k) if isinstance(case_obj, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    name = os.path.basename(os.path.abspath(input_dir)) or "case"
    token = name + ("|" + ",".join(os.path.basename(p) for p in files["data"]))
    h = hashlib.sha1(token.encode("utf-8")).hexdigest()[:10]
    return "%s-%s" % (name, h)


# --------------------------------------------------------------------------- #
# 结果骨架与增量维护
# --------------------------------------------------------------------------- #
def build_skeleton(case_id, template, contract_path, files, degradations, case_err):
    now = utcnow_iso()
    has_input = bool(files["case_json"] or files["task_md"] or files["data"]
                     or files["materials"])
    result = {"case_id": case_id}
    # 契约字段原样回填为 null/[]，不改名、不删未知键、不改层级
    for k, v in template.items():
        result[k] = v
    result.update({
        "status": "partial" if has_input else "insufficient_evidence",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": now, "finished_at": None, "seconds": None,
                    "budget_seconds": 900, "budget_requests": 64},
        "artifacts": [],
        "_contract_template": template,
        "_contract_path": contract_path,
    })
    if case_err:
        result["errors"].append({"fatal": True, "message": case_err})
        result["status"] = "input_error"
    for d in degradations:
        result["degradations"].append(d)
    append_change(result, "init", "落盘 partial 结果骨架",
                  [os.path.basename(p) for p in
                   [files["case_json"], files["task_md"]] if p])
    return result


def write_result(output_dir, result):
    os.makedirs(output_dir, exist_ok=True)
    return atomic_write_json(os.path.join(output_dir, "result.json"), result)


def load_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return {}, "missing: %s" % path
    obj, err = load_json_robust(path)
    if err:
        return {}, err
    if not isinstance(obj, dict):
        return {}, "result.json 不是对象"
    return obj, None


def _next_seq(result):
    seqs = [c.get("seq", 0) for c in result.get("change_log", [])
            if isinstance(c, dict)]
    return (max(seqs) + 1) if seqs else 1


def append_change(result, step, action, files=None):
    entry = {"ts": utcnow_iso(), "seq": _next_seq(result),
             "step": step, "action": action, "files": files or []}
    result.setdefault("change_log", []).append(entry)
    return entry


def add_source(result, locator, quote, kind="value", level="verified"):
    entry = {"locator": locator, "quote": quote, "kind": kind, "level": level}
    result.setdefault("sources", []).append(entry)
    return entry


def add_limitation(result, kind, level, detail, pending_human=False,
                   recoverable=True):
    if level not in LIMITATION_LEVELS:
        level = "warning"
    entry = {"kind": kind, "level": level, "detail": detail,
             "pending_human": bool(pending_human), "recoverable": bool(recoverable),
             "ts": utcnow_iso()}
    result.setdefault("limitations", []).append(entry)
    if pending_human and level in ("missing", "critical", "blocked"):
        result.setdefault("pending_human", []).append(
            {"kind": kind, "detail": detail})
    return entry


def add_uncertainty(result, message, reason="", level="info", pending_human=False):
    entry = {"message": message, "reason": reason, "level": level,
             "pending_human": bool(pending_human), "ts": utcnow_iso()}
    result.setdefault("uncertainty", []).append(entry)
    return entry


def add_artifact(result, path, fmt, role, dpi=None):
    entry = {"path": path, "format": fmt, "role": role}
    if dpi is not None:
        entry["dpi"] = dpi
    result.setdefault("artifacts", []).append(entry)
    return entry


# --------------------------------------------------------------------------- #
# 状态诚实性：由 limitations/uncertainty 证据自动给出状态上限
# --------------------------------------------------------------------------- #
def status_ceiling(result):
    """根据记录的证据计算允许的最高 status；防止“没有证据却写 ok”。"""
    errs = result.get("errors") or []
    if any(isinstance(e, dict) and e.get("fatal") for e in errs):
        return "input_error"
    arts = result.get("artifacts") or []
    if not arts:
        return "insufficient_evidence"
    ceiling = "ok"
    for l in result.get("limitations") or []:
        lv = l.get("level")
        if lv == "blocked":
            return "partial" if l.get("recoverable", True) else "insufficient_evidence"
        if lv in ("missing", "critical"):
            ceiling = "partial"
    for u in result.get("uncertainty") or []:
        if u.get("pending_human") is True and u.get("level") in ("missing", "critical"):
            ceiling = "partial"
    return ceiling


# --------------------------------------------------------------------------- #
# 表格读取（CSV/TSV + SQLite，仅标准库）
# --------------------------------------------------------------------------- #
def _looks_numeric(s):
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


def sniff_delimiter(lines):
    counts = {}
    for ch in (",", "\t", ";", "|"):
        counts[ch] = sum(l.count(ch) for l in lines)
    if not counts:
        return ","
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def read_table(path, degs=None):
    """读取 CSV/TSV。返回 (table, degradations)。table.rows 为 dict 列表。"""
    degs = degs if degs is not None else []
    text, enc, err = read_text_robust(path)
    if err:
        return None, [err]
    if enc and enc.lower() not in ("utf-8-sig", "utf-8"):
        degs.append("reencoded:%s=%s（非 UTF-8，已转码）" % (os.path.basename(path), enc))
    sample = [l for l in text.splitlines() if l.strip()][:8]
    if not sample:
        return None, ["empty_table: %s" % os.path.basename(path)]
    delim = sniff_delimiter(sample)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        raw_rows = [r for r in reader if any((c or "").strip() for c in r)]
    except csv.Error as e:
        return None, ["csv_parse_error: %s (%s)" % (os.path.basename(path), e)]
    if not raw_rows:
        return None, ["empty_table: %s" % os.path.basename(path)]
    first = raw_rows[0]
    has_header = not all(_looks_numeric(c) for c in first)
    if has_header:
        columns = [c.strip() if isinstance(c, str) else str(c) for c in first]
        body = raw_rows[1:]
    else:
        columns = ["col_%d" % i for i in range(len(first))]
        body = raw_rows
    if len(set(columns)) != len(columns):
        degs.append("duplicate_columns: %s（列名重名，已按出现顺序保留）" % os.path.basename(path))
    n_cols = len(columns)
    rows = []
    for r in body:
        d = {}
        for i, c in enumerate(columns):
            d[c] = r[i].strip() if i < len(r) and r[i] is not None else ""
        rows.append(d)
    ext = os.path.splitext(path)[1].lower()
    table = {
        "path": path, "name": os.path.basename(path),
        "format": "tsv" if delim == "\t" else "csv",
        "delimiter": delim, "has_header": has_header,
        "encoding": enc, "n_columns": n_cols, "n_rows": len(rows),
        "columns": columns, "rows": rows,
        "raw_first": [raw_rows[i] for i in range(1, min(6, len(raw_rows)))],
    }
    if ext == ".tsv" or delim == "\t":
        table["format"] = "tsv"
    return table, degs


def read_sqlite(path, limit_rows=200):
    """读取 SQLite：列出表与列，返回 (info, degradations)。"""
    degs = []
    try:
        uri = "file:%s?mode=ro" % path.replace("\\", "/")
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return None, ["sqlite_open_error: %s (%s)" % (os.path.basename(path), e)]
    info = {"path": path, "name": os.path.basename(path), "format": "sqlite",
            "tables": []}
    try:
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    except sqlite3.Error as e:
        con.close()
        return None, ["sqlite_query_error: %s (%s)" % (os.path.basename(path), e)]
    for t in tabs:
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            cur = con.execute("SELECT * FROM %s LIMIT %d" % (t, limit_rows))
            rows = [list(r) for r in cur.fetchall()]
        except sqlite3.Error as e:
            degs.append("sqlite_table_error: %s.%s (%s)" % (t, cols if False else "", e))
            info["tables"].append({"table": t, "error": str(e)})
            continue
        info["tables"].append({"table": t, "columns": cols, "n_rows": n,
                               "sample_rows": rows})
    con.close()
    return info, degs


# --------------------------------------------------------------------------- #
# 列画像 / 异常检测（确定性，无随机）
# --------------------------------------------------------------------------- #
def _coerce_numeric(values):
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            pass
    return nums


def _numeric_stats(nums):
    n = len(nums)
    out = {"n_numeric": n}
    if n:
        out["min"] = min(nums)
        out["max"] = max(nums)
        out["mean"] = sum(nums) / n
        s = sorted(nums)
        out["median"] = statistics.median(s)
        if n >= 2:
            out["stdev"] = statistics.stdev(s)
        if n >= 4:
            q1 = statistics.median(s[:n // 2]) if n % 2 == 0 else \
                statistics.median(s[:n // 2 + 1])
            q3 = statistics.median(s[n // 2:])
            iqr = q3 - q1
            out["q1"], out["q3"], out["iqr"] = q1, q3, iqr
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            out["outliers_iqr"] = [x for x in s if x < lo or x > hi]
    return out


def profile_columns(columns, rows):
    prof = {}
    for c in columns:
        vals = [r.get(c) for r in rows]
        nonnull = [v for v in vals
                   if v is not None and str(v).strip() != ""]
        n = len(vals)
        nmiss = n - len(nonnull)
        p = {"name": c, "n": n, "n_missing": nmiss,
             "pct_missing": round(100 * nmiss / n, 2) if n else None,
             "n_unique": len(set(str(v) for v in nonnull))}
        nums = _coerce_numeric(nonnull)
        if nonnull and len(nums) >= 0.8 * len(nonnull):
            p["dtype"] = "continuous"
            p.update(_numeric_stats(nums))
        else:
            p["dtype"] = "categorical"
            top = Counter(str(v) for v in nonnull).most_common(5)
            p["top_values"] = [[k, cnt] for k, cnt in top]
        prof[c] = p
    return prof


def detect_issues(rows, profiles):
    """返回结构化 issue 列表（不删除任何数据）。"""
    issues = []
    seen = {}
    n_dup = 0
    for r in rows:
        key = tuple(sorted((k, str(v)) for k, v in r.items()))
        seen[key] = seen.get(key, 0) + 1
    n_dup = sum(1 for c in seen.values() if c > 1)
    if n_dup:
        issues.append({"kind": "duplicate", "detail": "%d 行完全重复（保留原值，未删除）" % n_dup})
    for name, p in profiles.items():
        if p.get("dtype") == "continuous":
            if p.get("n_missing"):
                issues.append({"kind": "missing", "column": name,
                               "detail": "%d 个缺失（%.2f%%）" % (p["n_missing"], p["pct_missing"])})
            if p.get("outliers_iqr"):
                issues.append({"kind": "outlier", "column": name,
                               "detail": "IQR 法检出 %d 个离群值" % len(p["outliers_iqr"])})
            if p.get("n_unique", 0) <= 1 and p.get("n", 0) > 0:
                issues.append({"kind": "constant", "column": name,
                               "detail": "常量列，不携带比较信息"})
        else:
            if p.get("n_unique", 0) > 8:
                issues.append({"kind": "high_cardinality", "column": name,
                               "detail": "类别过多（%d 类），需聚合或分面" % p["n_unique"]})
    return issues


# --------------------------------------------------------------------------- #
# 图型推荐（规则显式、可复核；不做面积/角度夸大）
# --------------------------------------------------------------------------- #
def recommend_figure(profiles, goal=None, n_rows=0):
    """基于列画像 + 分析目的给出图型、理由、被否选项与编码建议。
    无数据时不编造，返回 degradation=no_data。"""
    if not profiles:
        return {"figure": "none", "goal": goal, "rationale": "无可分析的数据列",
                "alternatives_rejected": [], "encoding": [],
                "degradation": "no_data", "limitation": {
                    "kind": "data_quality", "level": "missing",
                    "detail": "没有可画像的数据列，无法选图", "pending_human": True}}
    cont = [p for p in profiles.values() if p.get("dtype") == "continuous"]
    cat = [p for p in profiles.values() if p.get("dtype") == "categorical"]
    if goal is None:
        if cont and cat:
            goal = "comparison"
        elif len(cont) >= 2:
            goal = "correlation"
        elif len(cont) == 1:
            goal = "distribution"
        else:
            goal = "comparison"
    rejected = []
    enc = []
    rec = {"goal": goal, "reasons": [], "alternatives_rejected": rejected,
           "encoding": enc, "n_rows": n_rows}

    def reject(fig, why):
        rejected.append({"figure": fig, "reason": why})

    if goal == "distribution":
        if cont:
            rec["figure"] = "histogram"
            rec["reasons"].append("连续变量分布用位置/长度编码，首选直方图")
            rec["reasons"].append("n=%d，可叠加核密度或改用箱线图观察偏态" % (cont[0].get("n") or 0))
            reject("pie", "饼图只能表示构成，不能表示连续分布")
            reject("bar", "并列柱无法呈现连续变量的取值密度")
            enc.append({"channel": "x", "variable": cont[0]["name"], "kind": "continuous"})
            enc.append({"channel": "y", "variable": "count/density", "kind": "frequency"})
        else:
            rec["figure"] = "bar"
            rec["reasons"].append("分类变量的频数分布用位置/长度编码")
            reject("pie", "无序分类不宜用角度编码，会夸大差异且灰度不可辨")
            enc.append({"channel": "x", "variable": cat[0]["name"], "kind": "categorical"})
            enc.append({"channel": "y", "variable": "count", "kind": "frequency"})
    elif goal == "comparison":
        if cont and cat:
            rec["figure"] = "grouped_box" if len(cat[0].get("top_values", [])) <= 6 \
                else "violin_or_point"
            rec["reasons"].append("分组比较连续变量，箱线/小提琴用位置与长度编码，保留分布信息")
            if len(cat[0].get("top_values", [])) > 6:
                rec["reasons"].append("组数>6，改用小提琴或抖动点图并排序")
            reject("pie", "饼图无法表达连续变量分组比较")
            reject("parallel_bar", "并列柱只显示均值，丢失分布；且量纲不同时误导")
            enc.append({"channel": "x", "variable": cat[0]["name"], "kind": "categorical"})
            enc.append({"channel": "y", "variable": cont[0]["name"], "kind": "continuous"})
        else:
            rec["figure"] = "horizontal_bar"
            rec["reasons"].append("分类计数比较用横向条形（位置/长度编码），类别多时避免标签重叠")
            reject("pie", "无序分类用角度比较误差大，不推荐")
            enc.append({"channel": "length", "variable": cat[0]["name"] if cat else "category",
                        "kind": "categorical"})
    elif goal == "correlation":
        if len(cont) >= 2:
            rec["figure"] = "scatter"
            rec["reasons"].append("连续×连续用散点（位置编码），可加回归线但须标注方法与置信区间")
            reject("line", "非时间序列不应连线暗示连续性趋势")
            enc.append({"channel": "x", "variable": cont[0]["name"], "kind": "continuous"})
            enc.append({"channel": "y", "variable": cont[1]["name"], "kind": "continuous"})
            if len(cont) > 2:
                rec["reasons"].append("变量>2 时建议散点矩阵或相关热图（分面）")
        else:
            rec["figure"] = "scatter_matrix"
            rec["reasons"].append("多变量相关用散点矩阵/相关热图")
    elif goal == "trend":
        rec["figure"] = "line"
        rec["reasons"].append("时间序列趋势用折线（位置编码），保持时间顺序")
        reject("parallel_bar", "把时间序列画成并列柱会破坏时间顺序，误导趋势")
        enc.append({"channel": "x", "variable": "time", "kind": "datetime_or_ordinal"})
    elif goal == "composition":
        rec["figure"] = "stacked_bar"
        rec["reasons"].append("构成用堆叠/归一化条形（长度编码），避免角度与面积")
        reject("pie", "面积与角度会夸大构成差异，灰度/色盲可读性差")
        enc.append({"channel": "length", "variable": "share", "kind": "proportion"})
    else:
        rec["figure"] = "table"
        rec["reasons"].append("目标不明确，退回数据表，避免错误图型")
    rec["figure_recommendation"] = rec.pop("figure")
    return rec


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
def validate_artifact(path):
    """返回 (ok, message)。SVG/PDF/PNG/CSV/JSON 做轻量格式校验。"""
    if not os.path.isfile(path):
        return False, "missing file"
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError as e:
        return False, "unreadable: %s" % e
    if ext == ".svg":
        try:
            ET.parse(path)
            return True, "svg-ok"
        except ET.ParseError as e:
            return False, "svg_parse_error: %s" % e
    if ext == ".pdf":
        return (head.startswith(b"%PDF-"), "pdf-header-ok" if head.startswith(b"%PDF-") else "pdf-bad-header")
    if ext == ".png":
        return (head.startswith(b"\x89PNG"), "png-ok" if head.startswith(b"\x89PNG") else "png-bad-header")
    if ext in (".csv", ".tsv"):
        t, degs = read_table(path)
        return (t is not None, "csv-ok" if t is not None else ("; ".join(degs) or "csv-empty"))
    if ext == ".json":
        obj, err = load_json_robust(path)
        return (err is None and isinstance(obj, (dict, list)), err or "json-ok")
    return True, "unknown-format(未校验内容)"


def check_result(result, require_contract=True, contract_template=None):
    """返回 [(tag, message)]，tag ∈ {FAIL, WARN, PASS}。"""
    issues = []
    if not isinstance(result, dict):
        return [("FAIL", "result.json 不是 JSON 对象")]
    if not result.get("case_id"):
        issues.append(("FAIL", "缺少 case_id"))
    if result.get("status") not in STATUS_VALUES:
        issues.append(("FAIL", "status 非法: %r" % result.get("status")))
    for key in ("sources", "uncertainty", "pending_human", "limitations",
                "change_log", "errors", "degradations", "artifacts"):
        if key not in result:
            issues.append(("WARN", "缺少保留键 %s" % key))
    if not result.get("change_log"):
        issues.append(("WARN", "change_log 为空，过程不可复核"))
    for s in result.get("sources") or []:
        if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
            issues.append(("WARN", "sources 存在缺 locator/quote 的条目"))
    if require_contract:
        tmpl = contract_template if contract_template is not None \
            else result.get("_contract_template", {})
        for k in tmpl.keys():
            if k not in result:
                issues.append(("FAIL", "契约字段缺失: %s" % k))
    ceiling = status_ceiling(result)
    declared = result.get("status")
    if declared and ceiling and STATUS_RANK.get(declared, 0) > STATUS_RANK.get(ceiling, 0):
        issues.append(("WARN", "status=%s 高于证据允许上限 %s（需降级或补充证据）" % (declared, ceiling)))
    return issues


def build_plan(files, template, contract_path):
    lines = ["# 执行计划（init 生成，随增量更新）", ""]
    lines.append("- case_json: %s" % ("有" if files["case_json"] else "无"))
    lines.append("- task.md: %s" % ("有" if files["task_md"] else "无"))
    lines.append("- 数据文件: %s" % (", ".join(os.path.basename(p) for p in files["data"]) or "无"))
    lines.append("- 材料文件: %s" % (", ".join(os.path.basename(p) for p in files["materials"]) or "无"))
    lines.append("- 契约来源: %s" % (contract_path or "未发现，使用最小兜底字段集"))
    lines.append("- 契约字段: %s" % (", ".join(template.keys()) if template else "（无）"))
    lines.append("")
    lines.append("## 分析步骤（逐条落实并留证据）")
    lines.append("1. 读数据字典/字段语义（引用原文）")
    lines.append("2. 记录清洗口径：缺失/异常/重复先记录，不静默删除或填充")
    lines.append("3. 统计（连续变量给 n/均值/中位/标准差，误差线注明 SD/SEM/CI）")
    lines.append("4. 按类型与目的选图，写明推荐理由与不推荐原因")
    lines.append("5. 编码：优先位置/长度，避免面积/角度夸大差异")
    lines.append("6. 标注与图注：轴=变量(单位)、图例、误差线定义、n、显著性")
    lines.append("7. 导出 SVG+PDF+>=300dpi PNG；中文缺字体改英文并记 degradation")
    lines.append("8. 每个关键数值/图元写 locator+逐字 quote，缺证据记 pending_human")
    return "\n".join(lines) + "\n"
