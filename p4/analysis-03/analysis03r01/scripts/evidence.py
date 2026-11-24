#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence_map 证据溯源核心（仅标准库）。

提供：
  - 证据记录 schema 与自动 id；
  - locator 语法解析与解析（CSV 单元格 / 文本行 / 段落 / SQLite 行）；
  - quote 机械复核：打开 locator 读取实际文本，比对 quote 是否逐字出现；
  - figure_map 与 evidence_map 的一致性校验。

locator 语法（相对 /input）：
  data.csv#L12            第 12 行（1 起，含表头）
  data.csv#r12c3          第 12 个数据行（不含表头）第 3 列（1 起）
  notes.md#p3             第 3 个段落（按空行分段）
  data.db#table=iris&rowid=12   SQLite 表 iris 的 rowid=12 行（序列化为 JSON）
"""
import csv
import io
import json
import os
import re
import sqlite3

MAX_FILE_BYTES = 2 * 1024 * 1024

CONFIDENCE_VALUES = ("certain", "inferred", "unverified")
CLAIM_TYPES = ("value", "statistic", "figure_element", "decision", "annotation", "other")

LOCATOR_FRAGS = (
    ("line", re.compile(r"^L(\d+)$")),
    ("cell", re.compile(r"^r(\d+)c(\d+)$")),
    ("para", re.compile(r"^p(\d+)$")),
    ("dbrow", re.compile(r"^table=([A-Za-z0-9_]+)&rowid=(\d+)$")),
)


def read_text_robust(path, limit=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d)" % size
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


def parse_locator(locator):
    """返回 (path, kind, params, error)。"""
    if not locator or "#" not in locator:
        return None, None, None, "locator 缺 '#' 或路径: %r" % locator
    path, frag = locator.rsplit("#", 1)
    for kind, rx in LOCATOR_FRAGS:
        m = rx.match(frag)
        if m:
            return path, kind, m.groups(), None
    return None, None, None, "无法识别的 locator 片段: %r" % frag


def _resolve_in_input(input_dir, path):
    p = os.path.join(input_dir, path.replace("/", os.sep))
    if os.path.isfile(p):
        return p
    return None


def resolve_locator(input_dir, locator):
    """打开 locator 读取实际文本，返回 (actual_text, error)。actual_text 可能为 None。"""
    path, kind, params, err = parse_locator(locator)
    if err:
        return None, err
    full = _resolve_in_input(input_dir, path)
    if full is None:
        return None, "文件未找到: %s" % path

    if kind == "dbrow":
        table, rowid = params[0], int(params[1])
        try:
            con = sqlite3.connect("file:%s?mode=ro" % full.replace("\\", "/"), uri=True)
        except sqlite3.Error as e:
            return None, "sqlite_open: %s" % e
        try:
            cur = con.execute('SELECT * FROM "%s" WHERE rowid=?' % table, (rowid,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description] if cur.description else []
            con.close()
            if row is None:
                return None, "dbrow 未命中: %s rowid=%s" % (table, rowid)
            return json.dumps(dict(zip(cols, row)), ensure_ascii=False, default=str), None
        except sqlite3.Error as e:
            try:
                con.close()
            except Exception:
                pass
            return None, "sqlite_query: %s" % e

    text, enc, err = read_text_robust(full)
    if err:
        return None, err

    if kind == "line":
        n = int(params[0])
        lines = text.splitlines()
        if 1 <= n <= len(lines):
            return lines[n - 1], None
        return None, "行号越界: L%d > %d" % (n, len(lines))

    if kind == "cell":
        r, c = int(params[0]), int(params[1])
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as e:
            return None, "csv_parse: %s" % e
        data_rows = rows[1:] if rows else []
        if r < 1 or r > len(data_rows):
            return None, "数据行越界: r%d > %d" % (r, len(data_rows))
        row = data_rows[r - 1]
        if c < 1 or c > len(row):
            return None, "列越界: c%d > %d" % (c, len(row))
        return row[c - 1], None

    if kind == "para":
        n = int(params[0])
        parts = re.split(r"\n\s*\n", text)
        if 1 <= n <= len(parts):
            return parts[n - 1].strip(), None
        return None, "段落越界: p%d > %d" % (n, len(parts))

    return None, "未知 locator 类型: %s" % kind


def verify_quote(actual, quote):
    """返回 (verified, observed, exact)。actual 为 None 表示无法定位。"""
    if actual is None:
        return False, None, False
    a = str(actual)
    q = str(quote)
    if q == "":
        return False, a, False
    return (q in a), a, (q == a.strip())


def add_record(records, claim="", claim_type="value", locator="", quote="",
               kind="value", derivation="", rule="", confidence="certain",
               verified=None):
    if claim_type not in CLAIM_TYPES:
        claim_type = "other"
    if confidence not in CONFIDENCE_VALUES:
        confidence = "unverified"
    n = 1
    for r in records:
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            m = re.match(r"^ev-(\d+)$", r["id"])
            if m:
                n = max(n, int(m.group(1)) + 1)
    rec = {
        "id": "ev-%03d" % n,
        "claim": claim,
        "claim_type": claim_type,
        "locator": locator,
        "quote": quote,
        "observed": None,
        "kind": kind,
        "derivation": derivation,
        "rule": rule,
        "confidence": confidence,
        "verified": verified,
        "conflicts": [],
    }
    records.append(rec)
    return rec


def verify_records(records, input_dir):
    """逐条机械复核；就地更新 verified/observed，返回 (records, issues)。"""
    issues = []
    out = []
    for r in records:
        if not isinstance(r, dict):
            out.append(r)
            continue
        loc = r.get("locator")
        quote = r.get("quote")
        if not loc or not quote:
            r["verified"] = False
            r["observed"] = None
            issues.append("%s: locator/quote 缺失" % r.get("id", "?"))
            out.append(r)
            continue
        actual, err = resolve_locator(input_dir, loc)
        if err:
            r["verified"] = False
            r["observed"] = None
            issues.append("%s: %s" % (r.get("id", "?"), err))
            out.append(r)
            continue
        ok, observed, _exact = verify_quote(actual, quote)
        r["verified"] = ok
        r["observed"] = observed
        if not ok:
            issues.append("%s: quote 未命中 locator %s" % (r.get("id", "?"), loc))
        out.append(r)
    return out, issues


def figure_map_check(figure_map, evidence_ids):
    """返回 figure_map 中指向不存在证据的条目。"""
    issues = []
    for i, f in enumerate(figure_map or []):
        if not isinstance(f, dict):
            issues.append("figure_map[%d] 非对象" % i)
            continue
        if f.get("evidence_id") and f["evidence_id"] not in evidence_ids:
            issues.append("figure_map[%d] 指向不存在证据 %s" % (i, f.get("evidence_id")))
    return issues


def locator_grammar():
    return {
        "line": "file.csv#L12  -> 第 12 行（含表头，1 起）",
        "cell": "file.csv#r12c3 -> 第 12 个数据行（不含表头）第 3 列（1 起）",
        "para": "notes.md#p3   -> 第 3 个段落（按空行分段，1 起）",
        "dbrow": "data.db#table=iris&rowid=12 -> 表 iris 的 rowid=12 行",
    }
