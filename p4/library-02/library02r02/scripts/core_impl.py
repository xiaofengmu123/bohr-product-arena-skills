#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 科研资料库修复引擎（仅 Python 标准库，确定性、可复跑）。

机械确定性层职责：
  盘点 → 内容哈希 → 去重 → 版本候选 → 别名(符号/硬链接) → 行政版本说明解析
  → 库目录解析 → 恢复对账 → 检索索引 → 机械问答 → 产物落盘。
语义层（跨文件语义检索/章节定位/证据回链）由调用 Agent 在 result.sources 与
result.answers 上增量补充；本引擎保证索引、候选与证据定位可复核。
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time

VERSION = "1.0.0"

MAX_TEXT_READ = 2_000_000       # 文本类文件读入上限（字节）
MAX_HASH_BYTES = 64_000_000     # 内容哈希上限（字节），超出记录 too_large
MAX_PDF_BYTES = 24_000_000      # 可选 pdftotext 提取上限（字节）
PDF_TIMEOUT = 30                # pdftotext 单文件超时（秒）
INDEX_PREVIEW = 2000            # 索引中每文件文本预览上限（字符）

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".yaml", ".yml", ".bib", ".ris", ".log", ".tex",
    ".ini", ".cfg", ".toml", ".py", ".sh", ".r", ".rmd", ".ipynb",
}

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract",
                 "expected_output", "expected_result")

VERSION_TOKEN = re.compile(
    r"(?i)(v|ver|version|rev|r)?\s*\d+(\.\d+){0,3}"
    r"(final|draft|submitted|accepted|published|old|new|copy|副本|修订|终稿|投稿|扫描|scan)?"
)

NORM_NOISE = re.compile(
    r"(?i)(final|draft|submitted|accepted|published|old|new|copy|副本|修订|终稿|投稿|扫描|scan|"
    r"v\d[\d.]*|ver\d[\d.]*|rev\d[\d.]*|r\d[\d.]*)"
)

CJK = re.compile(r"[\u4e00-\u9fff]")
WORD = re.compile(r"[a-z0-9_]{2,}")
YEAR = re.compile(r"(19|20)\d{2}")
EXT_IN_Q = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|txt|md|csv|tsv|bib|ris|json|db|sqlite)", re.I)
VER_IN_Q = re.compile(r"(?i)v\d[\d.]*|版本[:：]?\s*([A-Za-z0-9.\-_]+)")
PROJ_IN_Q = re.compile(r"(?:项目(?:编号)?|project)[:：]?\s*([A-Za-z0-9\-_]+)")

RESERVED = {
    "case_id", "status", "result", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "limitations", "runtime", "change_log",
    "artifacts", "computed", "contract", "contract_key",
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log_change(change_log, actor, step, detail):
    change_log.append({
        "time": now_iso(), "actor": actor, "step": step, "detail": detail,
    })


def norm_sep(path):
    return path.replace("\\", "/")


# --------------------------------------------------------------------------- #
# 命令行解析
# --------------------------------------------------------------------------- #
def parse_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-json", default=None)
    p.add_argument("--task-path", default=None)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", default=None)
    p.add_argument("--result", default=None)
    p.add_argument("--check", default=None)
    p.add_argument("--help", action="store_true")
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# 读取工具（容错编码、坏 JSON 不崩溃）
# --------------------------------------------------------------------------- #
def read_bytes(path, limit=None):
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if limit is not None and size > limit:
        return None, "too_large", None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return raw, "ok", None
    except OSError as e:
        return None, "unreadable", str(e)


def decode_text(raw):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), "ok", enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "degraded", "latin-1"


def read_text_file(path, limit=MAX_TEXT_READ):
    raw, state, err = read_bytes(path, limit)
    if raw is None:
        return None, state, None, err
    text, dstate, enc = decode_text(raw)
    if dstate == "degraded":
        return text, "degraded", enc, "lossy decode"
    return text, "ok", enc, None


def read_json_file(path):
    raw, state, err = read_bytes(path, MAX_TEXT_READ)
    if raw is None:
        return None, state, err
    try:
        return json.loads(raw.decode("utf-8-sig")), "ok", None
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        # 容错：尝试逐行/宽松解析失败即报错
        return None, "bad_json", str(e)


def find_file(root, name, fallback=None):
    if fallback and os.path.exists(fallback):
        return fallback
    direct = os.path.join(root, name)
    if os.path.exists(direct):
        return direct
    # 浅层扫描两层
    for depth in (1, 2):
        for dirpath, dirnames, filenames in os.walk(root):
            if name in filenames:
                return os.path.join(dirpath, name)
            rel = os.path.relpath(dirpath, root)
            if rel.count(os.sep) >= depth:
                dirnames[:] = []
    return None


# --------------------------------------------------------------------------- #
# 元数据
# --------------------------------------------------------------------------- #
def mode_string(mode):
    def rwx(bits):
        return ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + ("x" if bits & 1 else "-")
    t = "d" if stat.S_ISDIR(mode) else ("l" if stat.S_ISLNK(mode) else "-")
    return t + rwx((mode >> 6) & 7) + rwx((mode >> 3) & 7) + rwx(mode & 7)


def file_kind(path, is_link):
    if is_link:
        return "symlink"
    if os.path.isdir(path):
        return "dir"
    if os.path.isfile(path):
        return "file"
    return "other"


def is_text_like(name):
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_SUFFIXES


def looks_binary(text):
    if not text:
        return False
    probe = text[:4096]
    if "\x00" in probe:
        return True
    controls = sum(1 for ch in probe if ord(ch) < 9 or (13 < ord(ch) < 32))
    return controls / max(1, len(probe)) > 0.02


def extract_years(text, mtime_iso=None):
    years = set()
    if text:
        for m in YEAR.finditer(text):
            years.add(int(m.group(0)))
    if mtime_iso:
        m = re.match(r"(\d{4})", mtime_iso)
        if m:
            years.add(int(m.group(1)))
    return sorted(years)


# --------------------------------------------------------------------------- #
# 盘点
# --------------------------------------------------------------------------- #
def inventory(root, errors, degradations):
    root = os.path.abspath(root)
    files = []
    dirs = []
    seen_inodes = {}

    def lstat_rec(abs_path, rel, name):
        try:
            return os.lstat(abs_path)
        except OSError as e:
            errors.append({"type": "stat_error", "locator": rel, "detail": str(e)})
            return None

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = norm_sep(os.path.relpath(dirpath, root))

        # 当前目录本身（根目录除外）——只记录一次，避免空目录重复
        if rel_dir != ".":
            abs_d = dirpath
            st = lstat_rec(abs_d, rel_dir, os.path.basename(dirpath))
            if st is not None:
                dirs.append({
                    "path": rel_dir, "abs_path": norm_sep(abs_d),
                    "name": os.path.basename(dirpath) or ".",
                    "ext": None, "size": None, "mtime": None,
                    "mode": mode_string(st.st_mode), "st_mode": st.st_mode,
                    "st_dev": st.st_dev, "st_ino": st.st_ino, "nlink": st.st_nlink,
                    "kind": "dir", "symlink_target": None, "symlink_broken": None,
                    "role": "material", "empty": (not dirnames and not filenames),
                    "hash_sha256": None, "hash_state": "non_regular",
                    "text_state": None, "text_preview": None, "detected_encoding": None,
                })

        # 符号链接目录：os.walk(followlinks=False) 不进入，这里单独记录
        for d in dirnames:
            abs_d = os.path.join(dirpath, d)
            if not os.path.islink(abs_d):
                continue
            rel = norm_sep(os.path.relpath(abs_d, root))
            st = lstat_rec(abs_d, rel, d)
            if st is None:
                continue
            target = None
            try:
                target = os.readlink(abs_d)
            except OSError as e:
                errors.append({"type": "readlink_error", "locator": rel, "detail": str(e)})
            dirs.append({
                "path": rel, "abs_path": norm_sep(abs_d), "name": d,
                "ext": None, "size": None, "mtime": None,
                "mode": mode_string(st.st_mode), "st_mode": st.st_mode,
                "st_dev": st.st_dev, "st_ino": st.st_ino, "nlink": st.st_nlink,
                "kind": "symlink", "symlink_target": target,
                "symlink_broken": not os.path.exists(os.path.join(dirpath, target)) if target else None,
                "role": "material", "empty": False,
                "hash_sha256": None, "hash_state": "non_regular",
                "text_state": None, "text_preview": None, "detected_encoding": None,
            })

        # 文件（含符号链接文件）
        for f in filenames:
            abs_f = os.path.join(dirpath, f)
            rel = norm_sep(os.path.relpath(abs_f, root))
            is_link = os.path.islink(abs_f)
            st = lstat_rec(abs_f, rel, f)
            if st is None:
                continue
            rec = {
                "path": rel, "abs_path": norm_sep(abs_f), "name": f,
                "ext": os.path.splitext(f)[1].lower() or None,
                "size": st.st_size,
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
                "mode": mode_string(st.st_mode), "st_mode": st.st_mode,
                "st_dev": st.st_dev, "st_ino": st.st_ino, "nlink": st.st_nlink,
                "kind": file_kind(abs_f, is_link), "symlink_target": None,
                "symlink_broken": None, "role": "material",
                "hash_sha256": None, "hash_state": "pending",
                "text_state": None, "text_preview": None, "detected_encoding": None,
            }
            if is_link:
                try:
                    target = os.readlink(abs_f)
                except OSError as e:
                    target = None
                    errors.append({"type": "readlink_error", "locator": rel, "detail": str(e)})
                rec["symlink_target"] = target
                rec["symlink_broken"] = not os.path.exists(os.path.join(dirpath, target)) if target else None
                rec["kind"] = "symlink"
                rec["hash_state"] = "symlink"
            files.append(rec)
            if st.st_nlink > 1 and not is_link:
                seen_inodes.setdefault((st.st_dev, st.st_ino), []).append(rel)

    # 标记控制文件（case.json / task.md）
    for rec in files:
        base = os.path.basename(rec["path"])
        if base in ("case.json", "task.md"):
            rec["role"] = "control"

    return {
        "files": files, "dirs": dirs,
        "hardlink_inodes": {f"{d}:{i}": sorted(set(v)) for (d, i), v in seen_inodes.items() if len(v) > 1},
    }


# --------------------------------------------------------------------------- #
# 哈希与去重
# --------------------------------------------------------------------------- #
def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_hashes(files, root, errors, degradations):
    hashes = {}
    for rec in files:
        if rec["kind"] != "file":
            rec["hash_state"] = "non_regular"
            continue
        abs_f = rec["abs_path"]
        size = rec["size"] or 0
        if size > MAX_HASH_BYTES:
            rec["hash_state"] = "too_large"
            degradations.append({"type": "too_large", "locator": rec["path"], "detail": f"{size} bytes > {MAX_HASH_BYTES}"})
            continue
        try:
            digest = hash_file(abs_f)
        except OSError as e:
            rec["hash_state"] = "unreadable"
            errors.append({"type": "hash_error", "locator": rec["path"], "detail": str(e)})
            continue
        rec["hash_sha256"] = digest
        rec["hash_state"] = "ok"
        hashes.setdefault(digest, []).append(rec["path"])
    return hashes


def group_duplicates(hashes):
    groups = []
    gid = 0
    for digest in sorted(hashes):
        paths = sorted(hashes[digest])
        if len(paths) > 1:
            gid += 1
            groups.append({"group_id": f"dup{gid:03d}", "sha256": digest, "paths": paths})
    return groups


# --------------------------------------------------------------------------- #
# 版本候选（归一化主干 + 内容相似度，启发式）
# --------------------------------------------------------------------------- #
def normalized_stem(name):
    stem = os.path.splitext(name)[0].lower()
    stem = NORM_NOISE.sub(" ", stem)
    stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", stem).strip()
    return re.sub(r"\s+", " ", stem)


def similarity(a, b):
    if a is None or b is None or not a or not b:
        return None
    return round(difflib.SequenceMatcher(None, a[:4000], b[:4000]).ratio(), 4)


def build_version_candidates(files, root, errors, degradations):
    by_stem = {}
    for rec in files:
        if rec["kind"] != "file":
            continue
        stem = normalized_stem(rec["name"])
        if not stem:
            continue
        by_stem.setdefault(stem, []).append(rec)

    # 读取文本预览用于相似度
    previews = {}
    for rec in files:
        if rec["kind"] != "file" or not is_text_like(rec["name"]):
            previews[rec["path"]] = None
            continue
        text, state, enc, err = read_text_file(rec["abs_path"])
        rec["detected_encoding"] = enc
        if text is not None:
            if looks_binary(text):
                rec["text_state"] = "binary"
                previews[rec["path"]] = None
            else:
                rec["text_state"] = state
                rec["text_preview"] = text[:INDEX_PREVIEW]
                previews[rec["path"]] = text
        else:
            rec["text_state"] = state
            previews[rec["path"]] = None

    groups = []
    gid = 0
    for stem in sorted(by_stem):
        members = sorted(by_stem[stem], key=lambda r: r["path"])
        unique = {}
        for m in members:
            unique.setdefault(m["hash_sha256"], m)
        if len(unique) <= 1:
            continue
        gid += 1
        group = {
            "group_id": f"ver{gid:03d}", "stem": stem, "heuristic": True,
            "note": "normalized-stem + content-diff candidates; human/agent should confirm",
            "members": [],
        }
        for m in members:
            pair = {
                "path": m["path"], "sha256": m["hash_sha256"], "size": m["size"],
                "similarity_to_first": None,
            }
            if members[0]["path"] != m["path"]:
                pair["similarity_to_first"] = similarity(
                    previews.get(members[0]["path"]), previews.get(m["path"]))
            group["members"].append(pair)
        groups.append(group)
    return groups


# --------------------------------------------------------------------------- #
# 别名（符号链接 + 硬链接）
# --------------------------------------------------------------------------- #
def build_aliases(files, hardlink_inodes):
    symlinks = []
    for rec in files:
        if rec["kind"] == "symlink":
            symlinks.append({
                "path": rec["path"], "target": rec["symlink_target"],
                "broken": rec["symlink_broken"],
            })
    hardlinks = []
    hid = 0
    for key in sorted(hardlink_inodes):
        paths = hardlink_inodes[key]
        if len(paths) > 1:
            hid += 1
            hardlinks.append({"group_id": f"hard{hid:03d}", "inode": key, "paths": paths})
    return {"symlinks": symlinks, "hardlink_groups": hardlinks}


# --------------------------------------------------------------------------- #
# 行政版本说明解析
# --------------------------------------------------------------------------- #
def parse_markdown_table(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    if not rows:
        return []
    header = rows[0]
    out = []
    for r in rows[1:]:
        rec = {}
        for i, h in enumerate(header):
            if h and i < len(r):
                rec[h] = r[i]
        out.append(rec)
    return out


def normalize_note_record(rec):
    out = {}
    for k, v in rec.items():
        kl = k.strip().lower()
        if kl in ("file", "filename", "path", "文件", "文件名", "路径"):
            out["file"] = v
        elif kl in ("version", "ver", "版本"):
            out["version"] = v
        elif kl in ("status", "state", "状态"):
            out["status"] = v
        elif kl in ("date", "时间", "日期"):
            out["date"] = v
        elif kl in ("note", "notes", "说明", "备注"):
            out["note"] = v
        elif kl in ("id", "编号"):
            out["id"] = v
        elif kl in ("project", "项目", "project_id", "项目编号"):
            out["project"] = v
        elif kl in ("batch", "批次", "batch_id"):
            out["batch"] = v
        elif kl in ("permission", "权限", "perm"):
            out["permission"] = v
        else:
            out[k.strip()] = v
    return out


def parse_version_notes(root, task_text, errors, degradations):
    notes = []
    sources = []
    note_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for f in filenames:
            low = f.lower()
            if re.search(r"(version|note|admin|版本|说明|清单)", low) and \
               os.path.splitext(f)[1].lower() in (".json", ".csv", ".md", ".markdown"):
                note_files.append(os.path.join(dirpath, f))
    for path in note_files:
        rel = norm_sep(os.path.relpath(path, root))
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".json":
                data, state, err = read_json_file(path)
                if data is not None:
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get("entries", data.get("records", data.get("versions", data.get("items", []))))
                        if isinstance(items, dict):
                            items = [items]
                    else:
                        items = []
                    for rec in items:
                        if isinstance(rec, dict):
                            notes.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": json.dumps(data, ensure_ascii=False)[:500]})
            elif ext == ".csv":
                text, state, enc, err = read_text_file(path)
                if text:
                    reader = csv.DictReader(io.StringIO(text))
                    for rec in reader:
                        notes.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": text[:500]})
            else:  # markdown
                text, state, enc, err = read_text_file(path)
                if text:
                    rows = parse_markdown_table(text)
                    for rec in rows:
                        notes.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": text[:500]})
        except Exception as e:
            errors.append({"type": "version_note_parse_error", "locator": rel, "detail": str(e)})

    # 也解析 task.md 中的行政版本说明表格
    if task_text:
        for rec in parse_markdown_table(task_text):
            nr = normalize_note_record(rec)
            if nr.get("file") or nr.get("version"):
                notes.append(nr)
        if notes:
            sources.append({"locator": "task.md", "quote": task_text[:500]})
    return notes, sources


# --------------------------------------------------------------------------- #
# 库目录解析（JSON/CSV/Markdown/BibTeX/RIS/SQLite）
# --------------------------------------------------------------------------- #
def parse_bibtex(text):
    records = []
    pattern = re.compile(
        r"@(\w+)\s*\{\s*([^,]+),\s*(.*?)\n\s*\}", re.S)
    for m in pattern.finditer(text):
        etype, key, body = m.group(1), m.group(2).strip(), m.group(3)
        rec = {"bibtype": etype, "key": key}
        for f in re.finditer(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?", body, re.S):
            rec[f.group(1).lower()] = f.group(2)
        records.append(rec)
    return records


def parse_ris(text):
    records = []
    cur = None
    for line in text.splitlines():
        line = line.rstrip()
        if len(line) >= 6 and line[2:4] == "  -":
            tag, val = line[:2], line[6:]
            if tag == "TY":
                if cur:
                    records.append(cur)
                cur = {}
            if cur is not None:
                cur.setdefault(tag, []).append(val)
        elif line.strip() == "ER" and cur is not None:
            records.append(cur)
            cur = None
    if cur:
        records.append(cur)
    return records


def parse_library_dirs(root, errors, degradations):
    records = []
    sources = []
    lib_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for f in filenames:
            low = f.lower()
            ext = os.path.splitext(f)[1].lower()
            if re.search(r"(library|manifest|catalog|index|目录|清单|书目)", low) and \
               ext in (".json", ".csv", ".md", ".markdown", ".bib", ".ris", ".db", ".sqlite", ".sqlite3"):
                lib_files.append(os.path.join(dirpath, f))
    for path in lib_files:
        rel = norm_sep(os.path.relpath(path, root))
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".json":
                data, state, err = read_json_file(path)
                if data is not None:
                    items = data if isinstance(data, list) else \
                        data.get("entries", data.get("records", data.get("files", data.get("items", []))))
                    if isinstance(items, dict):
                        items = [items]
                    for rec in items:
                        if isinstance(rec, dict):
                            records.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": json.dumps(data, ensure_ascii=False)[:500]})
            elif ext == ".csv":
                text, state, enc, err = read_text_file(path)
                if text:
                    reader = csv.DictReader(io.StringIO(text))
                    for rec in reader:
                        records.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": text[:500]})
            elif ext in (".md", ".markdown"):
                text, state, enc, err = read_text_file(path)
                if text:
                    for rec in parse_markdown_table(text):
                        records.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": text[:500]})
            elif ext == ".bib":
                text, state, enc, err = read_text_file(path)
                if text:
                    for rec in parse_bibtex(text):
                        records.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": text[:500]})
            elif ext == ".ris":
                text, state, enc, err = read_text_file(path)
                if text:
                    for rec in parse_ris(text):
                        records.append(normalize_note_record(rec))
                    sources.append({"locator": rel, "quote": text[:500]})
            else:  # sqlite
                try:
                    con = sqlite3.connect(path)
                    cur = con.cursor()
                    tables = [r[0] for r in cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    for t in tables:
                        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({t})").fetchall()]
                        try:
                            for row in cur.execute(f"SELECT * FROM {t} LIMIT 1000").fetchall():
                                rec = dict(zip(cols, row))
                                records.append(normalize_note_record(rec))
                        except sqlite3.Error as e:
                            errors.append({"type": "sqlite_query_error", "locator": rel, "detail": str(e)})
                    sources.append({"locator": rel, "quote": f"sqlite tables: {tables}"})
                    con.close()
                except sqlite3.Error as e:
                    errors.append({"type": "sqlite_open_error", "locator": rel, "detail": str(e)})
        except Exception as e:
            errors.append({"type": "library_parse_error", "locator": rel, "detail": str(e)})
    return records, sources


# --------------------------------------------------------------------------- #
# 恢复对账
# --------------------------------------------------------------------------- #
def reconcile(files, library_records, version_notes):
    by_path = {r["path"]: r for r in files if r["kind"] == "file"}
    by_name = {os.path.basename(r["path"]): r for r in files if r["kind"] == "file"}

    def locate(rec):
        ref = rec.get("file") or rec.get("path") or rec.get("filename") or rec.get("id")
        if not ref:
            return None
        if ref in by_path:
            return by_path[ref], "path"
        if ref in by_name:
            return by_name[ref], "name"
        # 尝试去掉目录前缀匹配
        for p, r in by_path.items():
            if p.endswith("/" + ref) or p == ref:
                return r, "path_suffix"
        return None, "missing"

    matches, mismatches, missing, orphaned = [], [], [], []
    seen_paths = set()
    for i, rec in enumerate(library_records):
        target, how = locate(rec)
        if target is None:
            missing.append({"record": rec, "reason": "no matching file", "index": i})
            continue
        seen_paths.add(target["path"])
        entry = {"record": rec, "path": target["path"], "match_by": how}
        if rec.get("hash_sha256") or rec.get("sha256") or rec.get("hash"):
            expected = (rec.get("hash_sha256") or rec.get("sha256") or rec.get("hash")).lower()
            actual = (target.get("hash_sha256") or "").lower()
            if actual and expected == actual:
                matches.append(entry)
            else:
                entry["expected_hash"] = expected
                entry["actual_hash"] = actual or None
                mismatches.append(entry)
        else:
            matches.append(entry)

    for rec in version_notes:
        target, how = locate(rec)
        if target is not None:
            seen_paths.add(target["path"])

    # orphaned：真实材料文件未被任何库目录/版本说明引用
    for r in files:
        if r["kind"] == "file" and r["path"] not in seen_paths and r["role"] != "control":
            orphaned.append({"path": r["path"], "reason": "not referenced by any manifest/version note"})

    return {
        "matches": matches, "mismatches": mismatches,
        "missing": missing, "orphaned": orphaned,
        "summary": {
            "matches": len(matches), "mismatches": len(mismatches),
            "missing": len(missing), "orphaned": len(orphaned),
        },
    }


# --------------------------------------------------------------------------- #
# 检索索引
# --------------------------------------------------------------------------- #
def tokenize(text):
    toks = []
    if not text:
        return toks
    low = text.lower()
    toks.extend(WORD.findall(low))
    cjk = CJK.findall(low)
    for i in range(len(cjk) - 1):
        toks.append(cjk[i] + cjk[i + 1])
    return toks


def build_index(files, root, errors, degradations):
    from shutil import which
    index = {"tokens": {}, "files": {}, "pdfs": []}
    pdftotext = which("pdftotext")
    for rec in files:
        if rec["kind"] != "file":
            continue
        if rec.get("role") == "control":
            continue  # 控制文件（case.json/task.md）不进可检索索引
        meta = {
            "path": rec["path"], "size": rec["size"], "ext": rec["ext"],
            "mtime": rec["mtime"], "mode": rec["mode"], "hash": rec["hash_sha256"],
            "text_state": rec.get("text_state"), "preview": rec.get("text_preview"),
            "years": extract_years(rec.get("text_preview"), rec["mtime"]),
        }
        text = None
        ext = rec["ext"] or ""
        if ext == ".pdf":
            # 可选：调用系统 pdftotext（存在则提取，失败降级）
            if pdftotext and (rec["size"] or 0) <= MAX_PDF_BYTES and (rec["size"] or 0) > 0:
                try:
                    proc = subprocess.run(
                        [pdftotext, "-layout", rec["abs_path"], "-"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        timeout=PDF_TIMEOUT,
                    )
                    text = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else None
                    rec["text_state"] = "pdf_extracted" if text else "pdf_no_text"
                    rec["text_preview"] = (text or "")[:INDEX_PREVIEW]
                except (subprocess.SubprocessError, OSError) as e:
                    rec["text_state"] = "pdf_extract_failed"
                    degradations.append({"type": "pdf_extract_failed", "locator": rec["path"], "detail": str(e)})
            else:
                rec["text_state"] = rec.get("text_state") or "pdf_pending"
            if rec["path"] not in index["files"]:
                index["files"][rec["path"]] = meta
            if text:
                meta["preview"] = text[:INDEX_PREVIEW]
                meta["text_state"] = "pdf_extracted"
                meta["years"] = extract_years(text, rec["mtime"])
                for tok in tokenize(text):
                    index["tokens"].setdefault(tok, {}).setdefault(rec["path"], 0)
                    index["tokens"][tok][rec["path"]] += 1
            index["pdfs"].append(rec["path"])
            continue
        if rec.get("text_preview"):
            text = rec["text_preview"]
        elif rec.get("text_state") is None and is_text_like(rec["name"]):
            t, state, enc, err = read_text_file(rec["abs_path"])
            if t is not None and not looks_binary(t):
                text = t
                rec["text_state"] = state
                rec["text_preview"] = text[:INDEX_PREVIEW]
            else:
                rec["text_state"] = state or "binary"
        meta["preview"] = (text or "")[:INDEX_PREVIEW] if text else rec.get("text_preview")
        meta["text_state"] = rec.get("text_state")
        meta["years"] = extract_years(text or rec.get("text_preview"), rec["mtime"])
        index["files"][rec["path"]] = meta
        if text:
            for tok in tokenize(text):
                index["tokens"].setdefault(tok, {}).setdefault(rec["path"], 0)
                index["tokens"][tok][rec["path"]] += 1
    return index


def extract_filters(question):
    filters = {}
    years = [int(y) for y in YEAR.findall(question)]
    if years:
        filters["year"] = years
    exts = [e.lower() for e in EXT_IN_Q.findall(question)]
    if exts:
        filters["filetypes"] = exts
    vers = []
    for m in VER_IN_Q.finditer(question):
        if m.group(1):
            vers.append(m.group(1))
        elif m.group(0):
            vers.append(m.group(0))
    if vers:
        filters["versions"] = vers
    projs = PROJ_IN_Q.findall(question)
    if projs:
        filters["projects"] = projs
    if re.search(r"只读|readonly|read-only|权限", question, re.I):
        filters["permission"] = True
    return filters


def search_index(index, question, filters=None, topn=10):
    filters = filters or {}
    qtoks = tokenize(question)
    if not qtoks:
        return [], filters
    scores = {}
    for tok in set(qtoks):
        for path, cnt in index["tokens"].get(tok, {}).items():
            scores[path] = scores.get(path, 0) + min(cnt, 5)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:topn * 3]
    hits = []
    unverified = []
    for path, score in ranked:
        meta = index["files"].get(path, {})
        if filters.get("filetypes"):
            ext = (meta.get("ext") or "").lstrip(".").lower()
            if ext not in filters["filetypes"]:
                continue
        if filters.get("year"):
            ys = meta.get("years") or []
            if not any(y in ys for y in filters["year"]):
                unverified.append({"path": path, "filter": "year", "years": ys})
                continue
        if filters.get("versions"):
            if not any(v.lower() in path.lower() for v in filters["versions"]):
                continue
        if filters.get("projects"):
            if not any(v.lower() in path.lower() for v in filters["projects"]):
                continue
        preview = meta.get("preview") or ""
        snippet = preview[:240]
        hits.append({
            "path": path, "score": score, "ext": meta.get("ext"),
            "years": meta.get("years"), "snippet": snippet,
            "locator": path,
        })
        if len(hits) >= topn:
            break
    return hits, filters, unverified


def find_questions(case, task_text):
    questions = []
    if case:
        for k in ("questions", "queries", "asks", "search_questions", "检索问题"):
            v = case.get(k)
            if isinstance(v, list):
                for q in v:
                    if isinstance(q, str):
                        questions.append(q)
                    elif isinstance(q, dict):
                        questions.append(q.get("q") or q.get("question") or "")
            elif isinstance(v, str):
                questions.append(v)
            elif isinstance(v, dict):
                questions.append(v.get("q") or v.get("question") or "")
    # 从 task.md 中提取「提问/问题」行
    for line in (task_text or "").splitlines():
        m = re.match(r"\s*[-*]\s*(?:问题|Q\d*|提问)[:：]\s*(.+)", line)
        if m:
            questions.append(m.group(1).strip())
    return questions


def answer_mechanical(questions, index, inv, dup_groups, version_candidates, aliases, recon):
    answers = []
    for q in questions:
        filters = extract_filters(q)
        hits, filters, unverified = search_index(index, q, filters)
        answers.append({
            "question": q,
            "filters": filters,
            "hits": hits,
            "filter_unverified": unverified,
            "status": "answered" if hits else ("no_hits" if not unverified else "filter_unsatisfied"),
            "intent": "mechanical; agent may refine to chapter-level evidence",
        })
    return answers


# --------------------------------------------------------------------------- #
# case_id 与 contract
# --------------------------------------------------------------------------- #
def extract_case_id(case, task_text):
    if case:
        for k in ("case_id", "caseId", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v:
                return v
    if task_text:
        m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9\-_]+)`?", task_text)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_]?([A-Za-z0-9\-_]+)", task_text)
        if m:
            return m.group(0)
    return "unknown_case"


def find_contract(case):
    if not isinstance(case, dict):
        return None, None
    for k in CONTRACT_KEYS:
        if k in case and isinstance(case[k], (dict, list)):
            return case[k], k
    return None, None


DEFAULT_CONTRACT_FIELDS = [
    "inventory", "directory_tree", "duplicate_groups", "version_candidates",
    "aliases", "version_notes", "reconciliation", "index", "answers",
    "sources", "uncertainty",
]


def echo_contract(result, contract):
    """把 output_contract 字段原样回填到 result 顶层；冲突改存 _contract_<key>。"""
    echoed = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k in RESERVED:
                result[f"_contract_{k}"] = v
            else:
                result[k] = v
            echoed[k] = v
    elif isinstance(contract, list):
        for item in contract:
            key = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            if key in RESERVED:
                result[f"_contract_{key}"] = None
            else:
                result[key] = None
            echoed[key] = None
    else:
        for key in DEFAULT_CONTRACT_FIELDS:
            result[key] = None
            echoed[key] = None
    return echoed


# --------------------------------------------------------------------------- #
# 产物写入
# --------------------------------------------------------------------------- #
def write_artifacts(output, work, result, inv, hashes, dup_groups, version_candidates,
                    aliases, version_notes, recon, index, answers, sources):
    os.makedirs(output, exist_ok=True)
    artifacts = {}

    # 1. library_manifest.csv
    manifest_path = os.path.join(output, "library_manifest.csv")
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "name", "ext", "kind", "size", "mtime", "mode",
                    "hash_sha256", "hash_state", "symlink_target", "symlink_broken",
                    "role", "text_state"])
        for rec in sorted(inv["files"], key=lambda r: r["path"]):
            w.writerow([rec["path"], rec["name"], rec["ext"] or "", rec["kind"],
                        rec["size"] if rec["size"] is not None else "",
                        rec["mtime"] or "", rec["mode"] or "", rec["hash_sha256"] or "",
                        rec["hash_state"], rec["symlink_target"] or "",
                        "" if rec["symlink_broken"] is None else str(rec["symlink_broken"]),
                        rec["role"], rec["text_state"] or ""])
    artifacts["library_manifest.csv"] = norm_sep(manifest_path)

    # 2. directory_tree.txt
    tree_path = os.path.join(output, "directory_tree.txt")
    with open(tree_path, "w", encoding="utf-8") as f:
        f.write("# directory tree (input root)\n")
        # 以 dirname 为父键，统一用 "" 表示根目录
        children = {}  # parent -> list of (path, line)

        def add_entry(parent, path, line):
            key = "" if parent in (".", "") else parent
            children.setdefault(key, []).append((path, line))

        for d in inv["dirs"]:
            parent = os.path.dirname(d["path"])
            mark = ""
            if d.get("kind") == "symlink" and d.get("symlink_target"):
                mark = f" -> {d['symlink_target']}"
                if d.get("symlink_broken"):
                    mark += " [断链]"
            elif d.get("empty"):
                mark = " [空目录]"
            add_entry(parent, d["path"], d["name"] + "/" + mark)
        for rec in inv["files"]:
            parent = os.path.dirname(rec["path"])
            mark = ""
            if rec["kind"] == "symlink":
                mark = f" -> {rec['symlink_target']}" + (" [断链]" if rec["symlink_broken"] else "")
            add_entry(parent, rec["path"], rec["name"] + mark)

        def render(parent, depth):
            for path, line in sorted(children.get(parent, []), key=lambda x: x[0]):
                f.write("  " * depth + line + "\n")
                if path in children:
                    render(path, depth + 1)
        render("", 0)
    artifacts["directory_tree.txt"] = norm_sep(tree_path)

    # 3. dedup_version.json
    dedup_path = os.path.join(output, "dedup_version.json")
    with open(dedup_path, "w", encoding="utf-8") as f:
        json.dump({
            "duplicate_groups": dup_groups,
            "version_candidates": version_candidates,
            "version_notes": version_notes,
            "aliases": aliases,
        }, f, ensure_ascii=False, indent=2)
    artifacts["dedup_version.json"] = norm_sep(dedup_path)

    # 4. recovery_report.json
    recovery_path = os.path.join(output, "recovery_report.json")
    with open(recovery_path, "w", encoding="utf-8") as f:
        json.dump(recon, f, ensure_ascii=False, indent=2)
    artifacts["recovery_report.json"] = norm_sep(recovery_path)

    # 5. library_index.json
    index_path = os.path.join(output, "library_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({"files": index["files"], "tokens": index["tokens"], "pdfs": index["pdfs"]},
                  f, ensure_ascii=False, indent=2)
    artifacts["library_index.json"] = norm_sep(index_path)

    # 6. library_manifest.md
    md_path = os.path.join(output, "library_manifest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 科研资料库交接报告\n\n")
        summ = result.get("result", {}).get("summary", {})
        f.write(f"- case_id: {result.get('case_id')}\n")
        f.write(f"- status: {result.get('status')}\n")
        f.write(f"- files: {summ.get('files')} / dirs: {summ.get('dirs')}\n")
        f.write(f"- duplicate_groups: {summ.get('duplicate_groups')}\n")
        f.write(f"- version_candidates: {summ.get('version_candidates')}\n")
        f.write(f"- broken_symlinks: {summ.get('broken_symlinks')}\n")
        f.write(f"- hardlink_groups: {summ.get('hardlink_groups')}\n")
        f.write(f"- reconcile: {summ.get('reconcile')}\n\n")
        f.write("## 文件清单\n\n| path | kind | size | hash |\n|-|-|-|-|\n")
        for rec in sorted(inv["files"], key=lambda r: r["path"]):
            f.write(f"| {rec['path']} | {rec['kind']} | {rec['size']} | "
                    f"{rec['hash_sha256'][:12] if rec['hash_sha256'] else ''} |\n")
    artifacts["library_manifest.md"] = norm_sep(md_path)

    # 7. operation_log.jsonl
    log_path = os.path.join(output, "operation_log.jsonl")
    with open(log_path, "w", encoding="utf-8") as f:
        for entry in result.get("change_log", []):
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    artifacts["operation_log.jsonl"] = norm_sep(log_path)

    return artifacts


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def build_summary(inv, dup_groups, version_candidates, aliases, recon, index):
    return {
        "files": len([r for r in inv["files"] if r["kind"] == "file"]),
        "dirs": len(inv["dirs"]),
        "symlinks": len([r for r in inv["files"] if r["kind"] == "symlink"]),
        "duplicate_groups": len(dup_groups),
        "version_candidates": len(version_candidates),
        "broken_symlinks": sum(1 for s in aliases["symlinks"] if s["broken"]),
        "hardlink_groups": len(aliases["hardlink_groups"]),
        "reconcile": recon["summary"],
        "indexed_files": len(index["files"]),
        "total_tokens": len(index["tokens"]),
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_pipeline(args):
    start = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []
    change_log = []

    case_path = find_file(args.input, "case.json", fallback=args.case_json)
    task_path = find_file(args.input, "task.md", fallback=args.task_path)
    case = None
    if case_path:
        case, cstate, cerr = read_json_file(case_path)
        if case is None:
            errors.append({"type": "case_json_error", "locator": norm_sep(case_path), "detail": cstate or cerr})
    task_text = ""
    if task_path:
        t, tstate, enc, terr = read_text_file(task_path)
        if t is not None:
            task_text = t
        else:
            errors.append({"type": "task_read_error", "locator": norm_sep(task_path), "detail": tstate or terr})

    case_id = extract_case_id(case, task_text)
    contract, contract_key = find_contract(case)
    log_change(change_log, "script", "init", f"case_id={case_id}; contract_key={contract_key}")

    # 骨架模式：快速落盘，不跑盘点
    if args.skeleton_only:
        result = {
            "case_id": case_id, "status": "partial", "contract_key": contract_key,
            "contract": {}, "result": {}, "sources": [], "uncertainty": [],
            "pending_human": [], "errors": errors, "degradations": [],
            "limitations": ["skeleton-only: inventory pending"],
            "runtime": {"seconds": round(time.time() - start, 3), "python": sys.version.split()[0]},
            "change_log": change_log, "artifacts": {}, "computed": {},
        }
        result["contract"] = echo_contract(result, contract)
        if contract is None:
            result.setdefault("uncertainty", []).append(
                {"item": "output_contract", "reason": "case.json 无显式 output_contract，使用默认字段占位",
                 "severity": "low"})
        write_result(args.output, result)
        return result

    # 1. 盘点
    inv = inventory(args.input, errors, degradations)
    log_change(change_log, "script", "inventory",
               f"{len(inv['files'])} files, {len(inv['dirs'])} dirs")

    # 2. 哈希 + 去重
    hashes = compute_hashes(inv["files"], args.input, errors, degradations)
    dup_groups = group_duplicates(hashes)
    log_change(change_log, "script", "dedup", f"{len(dup_groups)} duplicate groups")

    # 3. 版本候选
    version_candidates = build_version_candidates(inv["files"], args.input, errors, degradations)
    log_change(change_log, "script", "version", f"{len(version_candidates)} version candidates")

    # 4. 别名
    aliases = build_aliases(inv["files"], inv["hardlink_inodes"])
    log_change(change_log, "script", "alias",
               f"{len(aliases['symlinks'])} symlinks, {len(aliases['hardlink_groups'])} hardlink groups")

    # 5. 版本说明
    version_notes, note_sources = parse_version_notes(args.input, task_text, errors, degradations)
    log_change(change_log, "script", "version_notes", f"{len(version_notes)} notes")

    # 6. 库目录
    library_records, lib_sources = parse_library_dirs(args.input, errors, degradations)
    log_change(change_log, "script", "library_dirs", f"{len(library_records)} records")

    # 7. 对账
    recon = reconcile(inv["files"], library_records, version_notes)
    log_change(change_log, "script", "reconcile", json.dumps(recon["summary"], ensure_ascii=False))

    # 8. 索引
    index = build_index(inv["files"], args.input, errors, degradations)
    log_change(change_log, "script", "index", f"{len(index['files'])} files indexed")

    # 9. 机械问答
    questions = find_questions(case, task_text)
    answers = answer_mechanical(questions, index, inv, dup_groups, version_candidates, aliases, recon)
    log_change(change_log, "script", "answers", f"{len(answers)} questions answered")

    summary = build_summary(inv, dup_groups, version_candidates, aliases, recon, index)

    sources = []
    for s in note_sources + lib_sources:
        sources.append({"item": s.get("locator"), "locator": s.get("locator"),
                        "quote": s.get("quote", "")})

    # 状态判定
    material_files = [r for r in inv["files"] if r["role"] != "control"]
    if not case_path and not task_path:
        status = "input_error"
    elif not material_files and not inv["dirs"]:
        status = "insufficient_evidence"
    elif errors and not any(r["kind"] == "file" for r in inv["files"]):
        status = "partial"
    else:
        status = "ok"
    if errors:
        status = "partial"

    result_obj = {
        "summary": summary,
        "files": sorted(inv["files"], key=lambda r: r["path"]),
        "directory_tree": "",  # 文本见产物
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "aliases": aliases,
        "version_notes": version_notes,
        "reconciliation": recon,
        "index": {"files": index["files"], "pdfs": index["pdfs"], "token_count": len(index["tokens"])},
        "answers": answers,
    }

    result = {
        "case_id": case_id, "status": status, "contract_key": contract_key,
        "contract": {}, "result": result_obj, "sources": sources,
        "uncertainty": [], "pending_human": [], "errors": errors,
        "degradations": degradations,
        "limitations": ["version chain and answers are heuristic; agent must verify chapter-level evidence"],
        "runtime": {"seconds": round(time.time() - start, 3), "python": sys.version.split()[0]},
        "change_log": change_log, "artifacts": {}, "computed": {},
    }
    if version_candidates:
        result["uncertainty"].append(
            {"item": "version_candidates", "reason": "归一化主干+内容相似度为启发式，可能把不同文档误聚为一链",
             "severity": "medium"})
    if contract is None:
        result["uncertainty"].append(
            {"item": "output_contract", "reason": "case.json 无显式 output_contract，使用默认字段",
             "severity": "low"})

    # 产物
    artifacts = write_artifacts(args.output, args.work, result, inv, hashes, dup_groups,
                                version_candidates, aliases, version_notes, recon, index,
                                answers, sources)
    result["artifacts"] = artifacts
    result["computed"] = {
        "summary": summary,
        "artifact_paths": artifacts,
        "counts": {
            "duplicate_groups": len(dup_groups), "version_candidates": len(version_candidates),
            "symlinks": len(aliases["symlinks"]), "hardlink_groups": len(aliases["hardlink_groups"]),
            "version_notes": len(version_notes), "library_records": len(library_records),
            "answers": len(answers),
        },
    }

    # contract 回填
    result["contract"] = echo_contract(result, contract)

    log_change(change_log, "script", "finalize",
               f"status={status}; {summary['files']} files; {summary['duplicate_groups']} dup groups; "
               f"{summary['version_candidates']} version candidates; {summary['broken_symlinks']} broken symlinks; "
               f"{summary['reconcile']['mismatches']} reconcile mismatches")
    write_result(args.output, result)
    return result


def write_result(output, result):
    path = os.path.join(output, "result.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # 写盘失败：尝试工作目录兜底，退出码 2
        fallback = os.path.join(output, "result.json.failed")
        try:
            with open(fallback, "w", encoding="utf-8") as f:
                json.dump({"case_id": result.get("case_id"), "status": "input_error",
                           "errors": [{"type": "write_failure", "detail": str(e)}]},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        sys.stderr.write(f"write_failure: {e}\n")
        raise SystemExit(2)


# --------------------------------------------------------------------------- #
# --update / --check
# --------------------------------------------------------------------------- #
def deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def do_update(patch_path, result_path):
    with open(patch_path, "r", encoding="utf-8-sig") as f:
        patch = json.load(f)
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            result = json.load(f)
    else:
        result = {"case_id": None, "status": "partial"}
    deep_merge(result, patch)
    log_change(result.setdefault("change_log", []), "agent", "update",
               f"merged patch {os.path.basename(patch_path)}")
    write_result(os.path.dirname(result_path) or ".", result)
    print(f"update: merged {len(patch)} top-level keys -> {result_path}")
    return result


def do_check(result_path, case_path):
    problems = []
    if not os.path.exists(result_path):
        problems.append(f"missing result file: {result_path}")
    else:
        with open(result_path, "r", encoding="utf-8") as f:
            result = json.load(f)
        if not result.get("case_id"):
            problems.append("case_id empty")
        if result.get("status") not in STATUSES:
            problems.append(f"invalid status: {result.get('status')}")
        if case_path and os.path.exists(case_path):
            case, state, err = read_json_file(case_path)
            if case is not None:
                contract, ck = find_contract(case)
                if contract is not None:
                    keys = contract if isinstance(contract, list) else list(contract.keys())
                    for k in keys:
                        key = k if isinstance(k, str) else k
                        if key not in result and f"_contract_{key}" not in result:
                            problems.append(f"contract field missing: {key}")
        for artifact in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                         "recovery_report.json", "library_index.json", "library_manifest.md",
                         "operation_log.jsonl"):
            if not os.path.exists(os.path.join(os.path.dirname(result_path), artifact)):
                problems.append(f"artifact missing: {artifact}")
    if problems:
        print("CHECK FAIL")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    print(f"CHECK OK: {result_path}")
    return 0


def main(argv):
    args = parse_args(argv)
    if args.help:
        sys.exit(0)
    if args.check:
        do_check(args.check, args.case_json)
        return
    if args.update:
        do_update(args.update, args.result or os.path.join(args.output, "result.json"))
        return
    run_pipeline(args)


if __name__ == "__main__":
    main(sys.argv[1:])
