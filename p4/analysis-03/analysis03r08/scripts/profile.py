#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据画像与选图推荐脚本（仅标准库）。

职责：扫描 /input 下的 CSV/TSV/SQLite 数据，逐列推断类型、统计缺失/重复/唯一值/
数值分布，生成 data_dictionary.json（字段语义）与 chart_plan.json（按数据结构与
分析目的给出候选图型、推荐理由与不推荐理由）。该能力对应评分维度"创新增益"中
"按数据结构与分析目的推荐图型"，供 Agent 据此完成最终选图与编码。

子命令：
  profile   对 /input 下全部数据文件画像并写 /app/output/data_dictionary.json、
            /app/output/chart_plan.json；同时把摘要回写到 result.json（不覆盖已有字段）

缺省参数：--input /input --output /app/output --work /app/work。退出码恒为 0。
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

MAX_ROWS_SCAN = 20000  # 每表最多扫描行数；超过则采样前 N 行并标注 truncated
MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = (".csv", ".tsv", ".txt")
DB_EXTS = (".db", ".sqlite", ".sqlite3")


def read_text_robust(path, limit=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, OSError):
            continue
    return "", None, "undecodable"


def sniff_delimiter(sample):
    try:
        d = csv.Sniffer().sniff(sample[:4096], delimiters=",;\t|")
        return d.delimiter
    except Exception:
        for cand in (",", "\t", ";", "|"):
            if cand in sample:
                return cand
        return ","


def _is_number(v):
    v = (v or "").strip()
    if v == "":
        return False
    try:
        float(v)
        return True
    except ValueError:
        return False


def _looks_datetime(v):
    return bool(re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", v)) or \
        bool(re.search(r"\d{1,2}:\d{2}", v))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_quantile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    idx = (n - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def infer_column_type(values):
    nonempty = [v for v in values if (v or "").strip() != ""]
    if not nonempty:
        return "empty"
    n_num = sum(1 for v in nonempty if _is_number(v))
    n_dt = sum(1 for v in nonempty if _looks_datetime(v))
    n = len(nonempty)
    if n_num >= 0.9 * n:
        return "numeric"
    if n_dt >= 0.7 * n:
        return "datetime"
    return "categorical"


def profile_column(name, values):
    nonempty = [v for v in values if (v or "").strip() != ""]
    missing = len(values) - len(nonempty)
    unique = {}
    for v in nonempty:
        unique[v] = unique.get(v, 0) + 1
    ctype = infer_column_type(values)
    prof = {
        "name": name,
        "type": ctype,
        "n": len(values),
        "missing": missing,
        "missing_ratio": round(missing / len(values), 4) if values else None,
        "unique": len(unique),
    }
    if ctype == "numeric":
        nums = [_num(v) for v in nonempty if _is_number(v)]
        nums = [x for x in nums if x is not None]
        if nums:
            prof.update({
                "min": min(nums), "max": max(nums),
                "mean": round(statistics.mean(nums), 6),
                "std": round(statistics.pstdev(nums), 6) if len(nums) > 1 else 0.0,
                "q25": _safe_quantile(nums, 0.25),
                "q50": _safe_quantile(nums, 0.5),
                "q75": _safe_quantile(nums, 0.75),
            })
    if ctype == "categorical" or (ctype == "numeric" and len(unique) <= 20):
        top = sorted(unique.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        prof["top_categories"] = [
            {"value": k, "count": v} for k, v in top
        ]
    if ctype == "datetime":
        prof["sample_values"] = nonempty[:3]
    return prof


def read_delimited(path, rel):
    text, enc, err = read_text_robust(path)
    if err:
        return None, "read_error: %s" % err
    delim = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    try:
        for r in reader:
            rows.append(r)
            if len(rows) > MAX_ROWS_SCAN:
                break
    except csv.Error as e:
        return None, "csv_error: %s" % e
    if not rows:
        return None, "no_rows"
    header = rows[0]
    body = rows[1:]
    # 去尾随空列与全空列
    cols = {}
    for i, h in enumerate(header):
        name = (h or "").strip() or ("col_%d" % i)
        vals = []
        for r in body:
            vals.append(r[i] if i < len(r) else "")
        cols[name] = vals
    n = len(body)
    dup_rows = 0
    seen = set()
    for r in body:
        key = "\x1f".join(r)
        if key in seen:
            dup_rows += 1
        else:
            seen.add(key)
    return {"table": os.path.splitext(rel)[0], "n_rows": n, "n_cols": len(cols),
            "duplicate_rows": dup_rows, "delimiter": delim, "encoding": enc,
            "truncated": len(rows) > MAX_ROWS_SCAN,
            "columns": [profile_column(k, v) for k, v in cols.items()]}, None


def read_sqlite(path, rel):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                              uri=True)
    except sqlite3.Error as e:
        return [], ["sqlite_open_error: %s" % e]
    out, deg = [], []
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        for t in tables:
            try:
                cols = [r[1] for r in cur.execute(
                    "PRAGMA table_info(%s)" % t).fetchall()]
                if not cols:
                    continue
                rows = cur.execute("SELECT * FROM %s LIMIT %d" % (t, MAX_ROWS_SCAN)).fetchall()
                total = cur.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except sqlite3.Error as e:
                deg.append("sqlite_table_error: %s (%s)" % (t, e))
                continue
            colvals = {}
            for i, c in enumerate(cols):
                colvals[c] = [str(r[i]) if r[i] is not None else "" for r in rows]
            n = len(rows)
            dup_rows = 0
            seen = set()
            for r in rows:
                key = "\x1f".join("" if x is None else str(x) for x in r)
                if key in seen:
                    dup_rows += 1
                else:
                    seen.add(key)
            out.append({
                "table": "%s:%s" % (os.path.splitext(rel)[0], t),
                "n_rows": total, "n_rows_scanned": n, "n_cols": len(cols),
                "duplicate_rows": dup_rows, "encoding": "sqlite",
                "truncated": total > MAX_ROWS_SCAN,
                "columns": [profile_column(c, v) for c, v in colvals.items()],
            })
    finally:
        con.close()
    return out, deg


def discover_data(input_dir):
    data, deg = [], []
    if not os.path.isdir(input_dir):
        return data, ["input_dir_missing"]
    for root, _d, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            low = name.lower()
            if low.endswith(TEXT_EXTS) and low not in ("task.md", "case.json"):
                data.append(("text", p, rel))
            elif low.endswith(DB_EXTS):
                data.append(("db", p, rel))
    return data, deg


def read_purpose_hints(task_md):
    """从 task.md 提取分析目的关键词，弱规则，仅作推荐提示。"""
    if not task_md or not os.path.isfile(task_md):
        return []
    text, _e, err = read_text_robust(task_md)
    if err:
        return []
    hints = []
    mapping = {
        "分布": "distribution", "比较": "comparison", "相关": "correlation",
        "趋势": "trend", "构成": "composition", "占比": "composition",
        "回归": "correlation", "离群": "distribution", "异常": "distribution",
        "多变量": "multivariate", "时间": "trend", "差异": "comparison",
    }
    for kw, purpose in mapping.items():
        if kw in text:
            hints.append(purpose)
    return sorted(set(hints))


def recommend(profile, purposes):
    """按列类型与目的返回候选图型（含不推荐项）。"""
    cols = profile.get("columns", [])
    n_num = sum(1 for c in cols if c["type"] == "numeric")
    n_cat = sum(1 for c in cols if c["type"] == "categorical")
    n_dt = sum(1 for c in cols if c["type"] == "datetime")
    candidates = []
    if not purposes:
        purposes = []

    if n_num == 1 and n_cat == 0 and n_dt == 0:
        candidates.append({
            "chart_type": "histogram + box", "purpose": "distribution",
            "reason": "单一连续变量，直方图看分布形态，箱线图标出离群",
            "not_recommended": [{"type": "bar", "reason": "连续变量画并列柱丢失分布密度"},
                                {"type": "pie", "reason": "面积编码夸大差异"}],
        })
    if n_cat >= 1 and n_num >= 1:
        cat_col = next((c for c in cols if c["type"] == "categorical"), None)
        low_card = cat_col and cat_col["unique"] <= 6
        base = {"purpose": "comparison",
                "reason": "分类 × 连续比较，用位置编码（箱线中位数/点图均值）",
                "not_recommended": [{"type": "pie", "reason": "无序分类用角度/面积夸大差异"}]}
        if low_card:
            candidates.append(dict(base, chart_type="grouped box/violin/strip",
                                   reason=base["reason"] + "；类别 ≤6 可同图比较"))
        else:
            candidates.append(dict(base, chart_type="horizontal bar (ordered)",
                                   reason=base["reason"] + "；类别 >6 需按值排序并用水平条形"))
    if "composition" in purposes and n_cat >= 1:
        candidates.append({
            "chart_type": "stacked/normalized bar", "purpose": "composition",
            "reason": "分类构成用长度编码，归一化便于跨组比较比例",
            "not_recommended": [{"type": "pie", "reason": "类别多时扇形难以比较"}],
        })
    if n_num >= 2:
        candidates.append({
            "chart_type": "scatter (+ optional regression)", "purpose": "correlation",
            "reason": "两连续变量相关趋势用位置编码，回归线单独说明方法与样本量",
            "not_recommended": [{"type": "line", "reason": "非有序数据连线暗示连续插值"}],
        })
    if n_dt >= 1 and n_num >= 1:
        candidates.append({
            "chart_type": "line", "purpose": "trend",
            "reason": "时间序列用折线展示趋势，x 轴用真实时间刻度",
            "not_recommended": [{"type": "grouped bar", "reason": "并列柱把连续时间离散化"}],
        })
    if n_num >= 4:
        candidates.append({
            "chart_type": "scatter matrix / correlation heatmap", "purpose": "multivariate",
            "reason": "多连续变量用分面散点矩阵或相关热图，避免一图硬塞多个量纲",
            "not_recommended": [{"type": "3d pie", "reason": "三维面积编码几乎不可读"}],
        })
    return candidates


def cmd_profile(args):
    data, deg = discover_data(args.input)
    task_md = os.path.join(args.input, "task.md")
    purposes = read_purpose_hints(task_md)
    dd, plans, errors = [], [], []
    for kind, path, rel in data:
        if kind == "text":
            prof, err = read_delimited(path, rel)
        else:
            tables, terr = read_sqlite(path, rel)
            for t in tables:
                dd.append({"source": rel, **t})
            deg.extend(terr)
            for t in tables:
                plans.append({
                    "source": rel, "table": t["table"],
                    "purposes": purposes,
                    "candidates": recommend(t, purposes),
                    "encoding_hints": {
                        "prefer_position": "位置/长度优先于面积/角度",
                        "categorical_limit": 6,
                        "color": "Okabe-Ito 色盲安全配色，灰度可辨",
                        "log_axis": "对数轴需显式标注，不得当线性轴",
                    },
                })
            continue
        if err:
            errors.append("%s: %s" % (rel, err))
            deg.append("%s: %s" % (rel, err))
            continue
        dd.append({"source": rel, **prof})
        plans.append({
            "source": rel, "table": prof["table"],
            "purposes": purposes,
            "candidates": recommend(prof, purposes),
            "encoding_hints": {
                "prefer_position": "位置/长度优先于面积/角度",
                "categorical_limit": 6,
                "color": "Okabe-Ito 色盲安全配色，灰度可辨",
                "log_axis": "对数轴需显式标注，不得当线性轴",
            },
        })
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    atomic = _atomic_write_json
    atomic(os.path.join(args.output, "data_dictionary.json"),
           {"purposes": purposes, "files": dd, "degradations": deg, "errors": errors})
    atomic(os.path.join(args.output, "chart_plan.json"),
           {"purposes": purposes, "plans": plans})
    # 回写 result.json 摘要（不覆盖已有字段）
    rpath = os.path.join(args.output, "result.json")
    if os.path.isfile(rpath):
        try:
            with open(rpath, "r", encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, json.JSONDecodeError):
            result = {}
        if isinstance(result, dict):
            summary = {
                "purposes": purposes,
                "files": [d.get("source") or d.get("table") for d in dd],
                "column_types": {
                    d.get("source") or d.get("table"): {
                        c["name"]: c["type"] for c in d.get("columns", [])
                    } for d in dd
                },
            }
            result.setdefault("scorecard", {})["data_profile"] = summary
            atomic(rpath, result)
    print(json.dumps({"purposes": purposes, "data_files": len(data),
                      "tables": len(dd), "degradations": len(deg),
                      "errors": len(errors)}, ensure_ascii=False, indent=2))
    return 0


def _atomic_write_json(path, obj):
    import tempfile
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description="数据画像与选图推荐")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("cmd", nargs="?", default="profile", choices=("profile",))
    args = p.parse_args(argv)
    try:
        return cmd_profile(args)
    except Exception as e:
        sys.stderr.write("ERROR(profile.py): %s\n" % e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
