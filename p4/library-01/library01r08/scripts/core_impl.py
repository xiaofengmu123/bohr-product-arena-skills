"""core_impl.py — 科研资料库修复（document library repair）确定性流水线。

纯标准库实现。职责：
  * 盘点文件/目录/权限/符号链接/硬链接别名
  * 按内容 SHA-256 去重（不按文件名）
  * 按归一化主干 + 内容差异给版本候选链（启发式，标注 heuristic）
  * 解析库目录（JSON/CSV/BibTeX/RIS/SQLite）并抽取行政版本说明
  * 恢复对账（matches/mismatches/missing/orphaned）
  * 重建可检索索引与常见检索问答
  * 产出可复核清单、目录树、去重/版本记录、恢复报告、索引、操作日志
  * 组装 result.json（含 case_id、status、output_contract 全字段、sources、
    uncertainty、change_log 等）

设计原则：确定性（遍历排序、哈希稳定、无随机数）；先落盘骨架、再增量改进；
坏输入降级不崩溃；不编造文件内容/哈希/元数据。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as statmod
import sys
import time
from collections import defaultdict

VERSION = "1.0.0"
SKILL_NAME = "library-document-repair"

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 内容哈希与文本读取上限（字节）。超限保留大小/时间，哈希标记 truncated。
HASH_MAX_BYTES = 512 * 1024 * 1024      # 512 MiB
TEXT_MAX_BYTES = 2 * 1024 * 1024        # 2 MiB

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract",
                 "output_schema", "expected_output")

ARTIFACT_NAMES = {
    "manifest_csv": "library_manifest.csv",
    "directory_tree": "directory_tree.txt",
    "dedup_version": "dedup_version.json",
    "recovery_report": "recovery_report.json",
    "library_index": "library_index.json",
    "manifest_md": "library_manifest.md",
    "operation_log": "operation_log.jsonl",
}

CATALOG_EXTS = {".json", ".csv", ".bib", ".ris", ".sqlite", ".db", ".sqlite3"}

# 版本令牌：用于归一化文件名主干，只用于启发式候选分组。
_VERSION_TOKEN_RE = re.compile(
    r"(?i)"
    r"(?:[_\-. ]?(?:v|ver|version|rev|r|no\.?)\s*\d+(?:\.\d+)*)"      # v2 / ver3 / r1
    r"|[_\-. ]?(?:final|draft|copy|old|new|latest|旧版|新版|副本|备份|定稿|草稿)"  # 状态词
    r"|\(\d+\)"                                                       # (2)
    r"|[_\-. ]?\d{4}[-_]\d{2}[-_]\d{2}"                              # 尾部日期 2023-05-01
)
_SEP_RE = re.compile(r"[_\-. ]+")


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def logline(actor: str, step: str, detail: str) -> dict:
    return {"time": utc_now_iso(), "actor": actor, "step": step, "detail": detail}


def deep_merge(base: dict, patch: dict) -> dict:
    """确定性深合并：patch 覆盖 base；dict 递归，其余整值替换。"""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_json(path: str):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return json.load(f)


def read_text(path: str, max_bytes: int = TEXT_MAX_BYTES):
    """容错读取文本：BOM/坏编码降级，返回 (text, encoding, truncated)。"""
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
    except OSError:
        return None, None, False
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc, truncated
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)", truncated


def hash_file(path: str, max_bytes: int = HASH_MAX_BYTES):
    """流式 SHA-256；超大文件截断哈希并标记 truncated。"""
    h = hashlib.sha256()
    size = 0
    truncated = False
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                remaining = max_bytes - size
                if remaining <= 0:
                    truncated = True
                    break
                if len(chunk) > remaining:
                    h.update(chunk[:remaining])
                    size += remaining
                    truncated = True
                    break
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size, truncated
    except OSError:
        return None, size, truncated


def fs_meta(path: str):
    """读取 stat 元数据，失败降级为 minimal dict。"""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        st = os.stat(path, follow_symlinks=True)
    except Exception:
        return {"exists": False, "error": "stat_failed"}
    meta = {
        "size": int(getattr(st, "st_size", -1)),
        "mtime_iso": _mtime_iso(st),
        "mtime_epoch": float(getattr(st, "st_mtime", -1.0)),
        "mode_octal": oct(statmod.S_IMODE(getattr(st, "st_mode", 0))),
        "uid": int(getattr(st, "st_uid", -1)),
        "gid": int(getattr(st, "st_gid", -1)),
        "nlink": int(getattr(st, "st_nlink", 1)),
        "inode": int(getattr(st, "st_ino", -1)),
        "device": int(getattr(st, "st_dev", -1)),
    }
    return meta


def _mtime_iso(st) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(getattr(st, "st_mtime", 0)))
    except (ValueError, OSError, OverflowError):
        return ""


def is_readable(path: str) -> bool:
    try:
        return os.access(path, os.R_OK)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 发现输入
# ---------------------------------------------------------------------------

def discover_input(input_root: str):
    """发现 case.json / task.md 及其余材料；返回有序结构。"""
    found = {"case_json": None, "task_md": None, "others": []}
    if not os.path.isdir(input_root):
        return found
    for name in sorted(os.listdir(input_root)):
        p = os.path.join(input_root, name)
        base = os.path.basename(name).lower()
        if base == "case.json" and found["case_json"] is None:
            found["case_json"] = p
        elif base in ("task.md", "task.txt") and found["task_md"] is None:
            found["task_md"] = p
        else:
            found["others"].append(p)
    return found


# ---------------------------------------------------------------------------
# 盘点（inventory）
# ---------------------------------------------------------------------------

def inventory(input_root: str):
    """递归盘点；返回 (files, dirs, symlinks, hardlink_groups, errors)。"""
    files = []
    dirs = []
    symlinks = []
    inode_groups = defaultdict(list)
    errors = []

    if not os.path.isdir(input_root):
        errors.append({"kind": "input_missing", "path": input_root,
                       "detail": "input root does not exist"})
        return files, dirs, symlinks, [], errors

    for dirpath, dirnames, filenames in os.walk(input_root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, input_root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        dir_meta = fs_meta(dirpath)
        dirs.append({
            "path": rel_dir or ".",
            "abspath": dirpath,
            "empty": (len(dirnames) == 0 and len(filenames) == 0),
            "readable": is_readable(dirpath),
            "mode_octal": dir_meta.get("mode_octal"),
        })
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, input_root).replace("\\", "/")
            entry = {
                "path": rel,
                "abspath": p,
                "name": fn,
                "ext": os.path.splitext(fn)[1].lower(),
                "type": "file",
                "readable": is_readable(p),
                "is_symlink": os.path.islink(p),
            }
            meta = fs_meta(p)
            entry.update(meta)
            if os.path.islink(p):
                try:
                    target = os.readlink(p)
                except OSError:
                    target = ""
                broken = not os.path.exists(p)
                symlinks.append({
                    "path": rel, "target": target, "broken": broken,
                    "mode_octal": meta.get("mode_octal"),
                })
                entry["symlink_target"] = target
                entry["broken"] = broken
                entry["type"] = "symlink"
            if os.path.isfile(p) and not os.path.islink(p):
                digest, hashed_size, truncated = hash_file(p)
                entry["sha256"] = digest
                entry["hashed_size"] = hashed_size
                entry["hash_truncated"] = truncated
                if digest is None:
                    entry["hash_error"] = "unreadable"
                    errors.append({"kind": "unreadable_file", "path": rel,
                                   "detail": "cannot hash file"})
                elif truncated:
                    errors.append({"kind": "hash_truncated", "path": rel,
                                   "detail": f"hash limited to first {HASH_MAX_BYTES} bytes"})
                # 硬链接组（POSIX inode；Windows 上为尽力而为）
                key = (meta.get("device"), meta.get("inode"))
                if key != (-1, -1):
                    inode_groups[key].append(rel)
            elif not os.path.islink(p) and not os.path.isdir(p):
                entry["type"] = "non_regular"
                errors.append({"kind": "non_regular", "path": rel,
                               "detail": "not a regular file, directory, or symlink"})
            files.append(entry)

    files.sort(key=lambda e: e["path"])
    dirs.sort(key=lambda e: e["path"])
    symlinks.sort(key=lambda e: e["path"])
    hardlink_groups = [sorted(v) for v in inode_groups.values() if len(v) > 1]
    hardlink_groups.sort()
    return files, dirs, symlinks, hardlink_groups, errors


# ---------------------------------------------------------------------------
# 去重（按内容 SHA-256）
# ---------------------------------------------------------------------------

def dedup(files):
    by_hash = defaultdict(list)
    for e in files:
        if e.get("type") == "file" and e.get("sha256"):
            by_hash[e["sha256"]].append(e["path"])
    groups = []
    for h in sorted(by_hash):
        paths = sorted(by_hash[h])
        if len(paths) > 1:
            sizes = {f["size"] for f in files if f["path"] in paths}
            groups.append({
                "sha256": h,
                "paths": paths,
                "count": len(paths),
                "sizes": sorted(sizes),
            })
    groups.sort(key=lambda g: (g["count"], g["sha256"]))
    return groups


# ---------------------------------------------------------------------------
# 版本候选链（启发式）
# ---------------------------------------------------------------------------

def normalize_stem(name: str) -> str:
    """归一化主干：去扩展名、去版本令牌、折叠分隔符、小写。"""
    stem = os.path.splitext(name)[0]
    stem = _VERSION_TOKEN_RE.sub("", stem)
    stem = _SEP_RE.sub(" ", stem)
    stem = stem.strip(" .-_")
    stem = re.sub(r"\s+", " ", stem).lower()
    return stem


def version_candidates(files):
    """按 (归一化主干, 扩展名) 分组，按 mtime 排序，产出候选链。"""
    buckets = defaultdict(list)
    for e in files:
        if e.get("type") != "file" or not e.get("sha256"):
            continue
        stem = normalize_stem(e["name"])
        if not stem:
            continue
        buckets[(stem, e["ext"])].append(e)
    candidates = []
    for (stem, ext) in sorted(buckets):
        members = sorted(buckets[(stem, ext)],
                         key=lambda x: (x.get("mtime_epoch", 0.0), x["path"]))
        if len(members) > 1 or _has_version_token(members[0]["name"]):
            candidates.append({
                "normalized_stem": stem,
                "ext": ext,
                "count": len(members),
                "heuristic": True,
                "members": [
                    {"path": m["path"], "sha256": m.get("sha256"),
                     "size": m.get("size"), "mtime_iso": m.get("mtime_iso")}
                    for m in members
                ],
                "order": "mtime ascending",
                "note": "candidate version chain; verify by content diff before asserting precedence",
            })
    return candidates


def _has_version_token(name: str) -> bool:
    return bool(_VERSION_TOKEN_RE.search(os.path.splitext(name)[0]))


# ---------------------------------------------------------------------------
# 库目录解析（JSON/CSV/BibTeX/RIS/SQLite）
# ---------------------------------------------------------------------------

def parse_catalog(path: str):
    """尝试按扩展名解析库目录，返回 (records, format, note)；失败返回 ([], fmt, err)。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            return parse_json_catalog(path), "json", ""
        if ext == ".csv":
            return parse_csv_catalog(path), "csv", ""
        if ext == ".bib":
            return parse_bibtex_catalog(path), "bibtex", ""
        if ext == ".ris":
            return parse_ris_catalog(path), "ris", ""
        if ext in (".sqlite", ".db", ".sqlite3"):
            return parse_sqlite_catalog(path), "sqlite", ""
    except Exception as exc:  # noqa: BLE001 — 故意兜底
        return [], ext.lstrip(".") or "unknown", f"{type(exc).__name__}: {exc}"
    return [], ext.lstrip(".") or "unknown", "unsupported catalog format"


def parse_json_catalog(path: str):
    text, _enc, _trunc = read_text(path)
    if text is None:
        raise ValueError("unreadable JSON catalog")
    data = json.loads(text)
    return normalize_records(data)


def normalize_records(data):
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("items", "entries", "records", "library", "documents", "files"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
        return [data] if data else []
    return []


def parse_csv_catalog(path: str):
    text, _enc, _trunc = read_text(path)
    if text is None:
        raise ValueError("unreadable CSV catalog")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


def parse_bibtex_catalog(path: str):
    text, _enc, _trunc = read_text(path)
    if text is None:
        raise ValueError("unreadable BibTeX catalog")
    records = []
    entry_re = re.compile(r"@(\w+)\s*[{(](.*?)[)}]\s*,", re.S)
    for m in entry_re.finditer(text):
        etype = m.group(1)
        body = m.group(2)
        rec = {"bibtex_type": etype}
        # 第一段为引用键
        key_m = re.match(r"\s*([^,\s]+)\s*,", body)
        if key_m:
            rec["citekey"] = key_m.group(1)
            body = body[key_m.end():]
        field_re = re.compile(r"(\w+)\s*=\s*[{\"]([^{}\"]*)[}\"]\s*,?", re.S)
        for fm in field_re.finditer(body):
            rec[fm.group(1).lower()] = fm.group(2).strip()
        if len(rec) > 1:
            records.append(rec)
    return records


def parse_ris_catalog(path: str):
    text, _enc, _trunc = read_text(path)
    if text is None:
        raise ValueError("unreadable RIS catalog")
    records = []
    cur = {}
    for raw in text.splitlines():
        if len(raw) >= 6 and raw[2:4] == "  ":
            tag = raw[:2].strip()
            val = raw[6:].strip()
            if tag == "TY":
                if cur:
                    records.append(cur)
                cur = {}
            cur.setdefault(tag, []).append(val)
        elif raw.strip() == "ER" and cur:
            records.append(cur)
            cur = {}
    if cur:
        records.append(cur)
    # RIS 多值转标量（单值时）
    out = []
    for r in records:
        nr = {}
        for k, v in r.items():
            nr[k] = v[0] if len(v) == 1 else v
        out.append(nr)
    return out


def parse_sqlite_catalog(path: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        records = []
        for t in tables[:20]:
            try:
                rows = conn.execute(f"SELECT * FROM {_quote_ident(t)} LIMIT 500")
                cols = [d[0] for d in rows.description]
                for row in rows:
                    records.append(dict(zip(cols, row)))
            except sqlite3.Error:
                continue
        return records
    finally:
        conn.close()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


FIELD_ALIASES = {
    "path": ["path", "file", "filename", "filepath", "location", "uri", "url",
             "relpath", "relative_path", "source", "src"],
    "hash": ["sha256", "sha", "hash", "checksum", "md5", "content_hash",
             "fingerprint", "digest"],
    "version": ["version", "ver", "revision", "rev", "edition"],
    "title": ["title", "name", "label", "document"],
    "author": ["author", "authors", "creator", "creators", "by"],
    "year": ["year", "date", "pubdate", "publish_year", "py"],
    "note": ["note", "notes", "version_note", "admin_note", "description",
             "comment", "n2"],
}


def extract_record_fields(rec: dict):
    """把任意键名的记录归一化到标准字段；保留原始键。"""
    norm = {"raw": rec}
    lower = {str(k).lower(): v for k, v in rec.items()}
    for field, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if a in lower and lower[a] not in (None, ""):
                norm[field] = lower[a]
                break
    return norm


# ---------------------------------------------------------------------------
# 恢复对账（reconciliation）
# ---------------------------------------------------------------------------

def reconcile(files, catalog_records):
    by_path = {f["path"]: f for f in files if f.get("type") == "file"}
    by_name = defaultdict(list)
    for f in files:
        if f.get("type") == "file":
            by_name[os.path.basename(f["path"]).lower()].append(f)

    matches, mismatches, missing, orphaned = [], [], [], []
    referenced = set()
    matched_paths = set()

    for rec in catalog_records:
        fields = extract_record_fields(rec)
        ref_path = fields.get("path")
        ref_hash = fields.get("hash")
        target = None
        if isinstance(ref_path, str):
            for cand in (ref_path,
                         ref_path.lstrip("/").replace("input/", "", 1)
                         if ref_path.lstrip("/").startswith("input/") else None):
                if cand and cand in by_path:
                    target = by_path[cand]
                    break
        if target is None and isinstance(ref_path, str):
            base = os.path.basename(ref_path).lower()
            if base in by_name and len(by_name[base]) == 1:
                target = by_name[base][0]

        status = "missing"
        item = {
            "record": {k: _jsonable(v) for k, v in fields["raw"].items()},
            "fields": {k: _jsonable(v) for k, v in fields.items() if k != "raw"},
            "ref_path": ref_path,
        }
        if target is not None:
            referenced.add(target["path"])
            matched_paths.add(target["path"])
            item["actual_path"] = target["path"]
            item["actual_sha256"] = target.get("sha256")
            if ref_hash and target.get("sha256") and \
                    str(ref_hash).lower() != str(target["sha256"]).lower():
                status = "mismatch"
                item["expected_hash"] = str(ref_hash)
                item["source_conflict"] = True
                mismatches.append(item)
            else:
                status = "match"
                matches.append(item)
        else:
            missing.append(item)
        item["status"] = status

    for f in files:
        if f.get("type") == "file" and f["path"] not in matched_paths:
            orphaned.append({"path": f["path"], "sha256": f.get("sha256"),
                             "size": f.get("size")})

    matches.sort(key=lambda x: x.get("actual_path", ""))
    mismatches.sort(key=lambda x: x.get("actual_path", ""))
    missing.sort(key=lambda x: str(x.get("ref_path", "")))
    orphaned.sort(key=lambda x: x["path"])

    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "counts": {
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "orphaned": len(orphaned),
        },
    }


# ---------------------------------------------------------------------------
# 索引与问答
# ---------------------------------------------------------------------------

def build_index(files, dup_groups, version_cands, catalog_records):
    by_ext = defaultdict(list)
    name_tokens = defaultdict(set)
    for f in files:
        if f.get("type") == "file":
            by_ext[f["ext"] or "(none)"].append(f["path"])
            for tok in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", f["name"].lower()):
                if len(tok) > 1:
                    name_tokens[tok].add(f["path"])
    return {
        "files": {f["path"]: _file_index_entry(f) for f in files
                  if f.get("type") == "file"},
        "by_ext": {k: sorted(v) for k, v in sorted(by_ext.items())},
        "name_tokens": {k: sorted(v) for k, v in sorted(name_tokens.items())},
        "duplicate_groups": dup_groups,
        "version_candidates": version_cands,
        "catalog_entries": [_jsonable(r) for r in catalog_records],
    }


def _file_index_entry(f):
    return {
        "name": f["name"], "ext": f["ext"], "size": f.get("size"),
        "sha256": f.get("sha256"), "mtime_iso": f.get("mtime_iso"),
        "readable": f.get("readable"),
    }


def answer_questions(files, dirs, symlinks, dup_groups, version_cands, recon, catalog_records):
    """确定性机械问答：计数/重复/缺失/断链/版本/作者/年份。"""
    authors, years = [], []
    for rec in catalog_records:
        f = extract_record_fields(rec)
        a = f.get("author")
        y = f.get("year")
        if isinstance(a, (list, tuple)):
            authors.extend(str(x) for x in a if x)
        elif a:
            authors.append(str(a))
        if y not in (None, ""):
            years.append(str(y))
    authors = sorted(set(authors))
    years = sorted(set(years))
    broken = [s["path"] for s in symlinks if s.get("broken")]
    non_regular = [f["path"] for f in files if f.get("type") == "non_regular"]
    truncated = [f["path"] for f in files if f.get("hash_truncated")]
    unreadable = [f["path"] for f in files if f.get("hash_error")]

    answers = {
        "file_count": len([f for f in files if f.get("type") == "file"]),
        "directory_count": len(dirs),
        "symlink_count": len(symlinks),
        "broken_symlink_count": len(broken),
        "broken_symlinks": broken,
        "duplicate_group_count": len(dup_groups),
        "duplicate_files_total": sum(g["count"] for g in dup_groups),
        "version_candidate_count": len(version_cands),
        "catalog_entry_count": len(catalog_records),
        "reconciliation": recon["counts"],
        "authors": authors,
        "years": years,
        "non_regular_files": non_regular,
        "hash_truncated_files": truncated,
        "unreadable_files": unreadable,
    }
    return answers


# ---------------------------------------------------------------------------
# 产物
# ---------------------------------------------------------------------------

def write_manifest_csv(path, files):
    fieldnames = ["path", "name", "ext", "type", "size", "sha256",
                  "mtime_iso", "mode_octal", "readable", "symlink_target",
                  "hash_truncated"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for e in files:
            w.writerow({k: e.get(k, "") for k in fieldnames})


def write_directory_tree(path, dirs, files, symlinks):
    lines = []
    for d in dirs:
        marker = " [空目录]" if d.get("empty") else ""
        lines.append(f"{d['path']}/" if d["path"] != "." else "./" + marker)
    for f in files:
        tag = " [断链]" if f.get("broken") else (" [符号链接]" if f.get("is_symlink") else "")
        lines.append(f["path"] + tag)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")


def write_manifest_md(path, summary, artifacts, errors, degradations):
    lines = [
        "# 科研资料库修复交接报告",
        "",
        "> 本文件由 `scripts/core_impl.py` 确定性生成；可与源文件逐项核对。",
        "",
        "## 概览",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## 产物",
    ]
    for name, rel in sorted(artifacts.items()):
        lines.append(f"- {name}: `{rel}`")
    if errors:
        lines.append("")
        lines.append("## errors")
        for e in errors:
            lines.append(f"- {json.dumps(e, ensure_ascii=False, sort_keys=True)}")
    if degradations:
        lines.append("")
        lines.append("## degradations")
        for d in degradations:
            lines.append(f"- {json.dumps(d, ensure_ascii=False, sort_keys=True)}")
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# output_contract 回填
# ---------------------------------------------------------------------------

def _contract_map(computed):
    return {
        "manifest": computed.get("manifest_rows", []),
        "directory_tree": computed.get("directory_tree_text", ""),
        "tree": computed.get("directory_tree_text", ""),
        "dedup": computed.get("duplicate_groups", []),
        "duplicate": computed.get("duplicate_groups", []),
        "duplicates": computed.get("duplicate_groups", []),
        "version": computed.get("version_candidates", []),
        "versions": computed.get("version_candidates", []),
        "version_chain": computed.get("version_candidates", []),
        "recovery": computed.get("reconciliation", {}),
        "reconcile": computed.get("reconciliation", {}),
        "reconciliation": computed.get("reconciliation", {}),
        "index": computed.get("index", {}),
        "answers": computed.get("answers", {}),
        "answer": computed.get("answers", {}),
        "qa": computed.get("answers", {}),
        "inventory": computed.get("files", []),
        "files": computed.get("files", []),
        "summary": computed.get("summary", {}),
        "aliases": computed.get("aliases", {}),
    }


def fill_contract(contract, computed):
    """把 output_contract 全部键原样回填（未知键给 null/[]，不改名不删）。"""
    mapping = _contract_map(computed)
    if contract is None:
        return {}
    if not isinstance(contract, dict):
        return contract

    def fill_value(key, val):
        k = str(key).lower()
        matched = None
        for token, payload in mapping.items():
            if token in k:
                matched = payload
                break
        if matched is not None:
            return matched
        if isinstance(val, dict):
            if val.get("type") in ("array", "list"):
                return []
            if val.get("type") in ("string", "str"):
                return ""
            if val.get("type") in ("integer", "int", "number", "float"):
                return None
            if val.get("type") in ("boolean", "bool"):
                return None
            if val.get("default") is not None:
                return val.get("default")
            return {k2: fill_value(k2, v2) for k2, v2 in val.items()}
        if isinstance(val, list):
            return []
        if isinstance(val, (int, float, bool)):
            return None
        return None

    out = {}
    for key, val in contract.items():
        out[key] = fill_value(key, val)
    return out


def _jsonable(obj):
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    try:
        return str(obj)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 主流水线
# ---------------------------------------------------------------------------

def run_pipeline(input_root, output_dir, work_dir, case_json_path, task_path,
                 skeleton_only=False):
    t0 = time.time()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    change_log = [logline("core_impl", "start",
                          f"input={input_root} output={output_dir}")]

    # --- 读 case.json / task.md ---
    case_id = None
    contract = None
    errors = []
    degradations = []
    uncertainty = []
    limitations = []
    pending_human = []
    sources = []
    case_data = {}

    if case_json_path is None:
        case_json_path = os.path.join(input_root, "case.json")
    if task_path is None:
        for cand in (os.path.join(input_root, "task.md"),
                     os.path.join(input_root, "task.txt")):
            if os.path.isfile(cand):
                task_path = cand
                break

    status = "partial"
    if not os.path.isfile(case_json_path):
        status = "input_error"
        errors.append({"kind": "missing_case_json", "path": case_json_path,
                       "detail": "case.json not found"})
    else:
        try:
            case_data = load_json(case_json_path)
        except Exception as exc:  # noqa: BLE001
            status = "input_error"
            errors.append({"kind": "bad_case_json", "path": case_json_path,
                           "detail": f"{type(exc).__name__}: {exc}"})
            case_data = {}
        case_id = case_data.get("case_id") or case_data.get("id") or \
            case_data.get("challenge_id") or case_data.get("caseId")
        for key in CONTRACT_KEYS:
            if key in case_data and case_data[key] is not None:
                contract = case_data[key]
                break
        if contract is None:
            uncertainty.append({"item": "output_contract", "reason":
                                "case.json 中未提供 output_contract（或键名不同）",
                                "severity": "medium"})

    if not case_id:
        case_id = os.path.basename(input_root.rstrip("/\\")) or "unknown"

    task_text = ""
    if task_path and os.path.isfile(task_path):
        task_text, _enc, _trunc = read_text(task_path)
        if task_text is None:
            degradations.append({"kind": "task_unreadable", "path": task_path,
                                 "detail": "task.md could not be read"})
    else:
        degradations.append({"kind": "missing_task", "path": task_path,
                             "detail": "task.md not found"})

    # --- 盘点 ---
    files, dirs, symlinks, hardlink_groups, inv_errors = inventory(input_root)
    errors.extend(inv_errors)
    if not files and not dirs and not symlinks and not os.path.isdir(input_root):
        status = "insufficient_evidence"
        limitations.append("input directory empty or missing; no materials to process")

    dup_groups = dedup(files)
    version_cands = version_candidates(files)

    # 库目录解析（排除 case.json 自身）
    catalog_records = []
    catalog_sources = []
    for root, dnames, fnames in os.walk(input_root, followlinks=False):
        dnames.sort()
        for fn in sorted(fnames):
            p = os.path.join(root, fn)
            if os.path.basename(p).lower() == "case.json":
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in CATALOG_EXTS:
                records, fmt, note = parse_catalog(p)
                rel = os.path.relpath(p, input_root).replace("\\", "/")
                if note and not records:
                    degradations.append({"kind": "catalog_parse_error",
                                         "path": rel, "detail": note,
                                         "format": fmt})
                elif records:
                    catalog_sources.append(rel)
                    for r in records:
                        r.setdefault("_catalog_source", rel)
                        r.setdefault("_catalog_format", fmt)
                    catalog_records.extend(records)

    recon = reconcile(files, catalog_records)
    index = build_index(files, dup_groups, version_cands, catalog_records)
    answers = answer_questions(files, dirs, symlinks, dup_groups, version_cands,
                               recon, catalog_records)

    aliases = {
        "symlinks": symlinks,
        "hardlink_groups": hardlink_groups,
        "note": "hardlink detection best-effort on this platform",
    }

    # --- 写入产物 ---
    artifacts = {}
    out = output_dir
    write_manifest_csv(os.path.join(out, ARTIFACT_NAMES["manifest_csv"]), files)
    artifacts["manifest_csv"] = ARTIFACT_NAMES["manifest_csv"]
    write_directory_tree(os.path.join(out, ARTIFACT_NAMES["directory_tree"]),
                         dirs, files, symlinks)
    artifacts["directory_tree"] = ARTIFACT_NAMES["directory_tree"]
    dedup_version = {
        "duplicate_groups": dup_groups,
        "version_candidates": version_cands,
        "version_notes": {
            "catalog_sources": catalog_sources,
            "records_with_version": [
                {"fields": extract_record_fields(r)} for r in catalog_records
                if extract_record_fields(r).get("version")
            ],
        },
        "aliases": aliases,
    }
    write_json(os.path.join(out, ARTIFACT_NAMES["dedup_version"]), dedup_version)
    artifacts["dedup_version"] = ARTIFACT_NAMES["dedup_version"]
    write_json(os.path.join(out, ARTIFACT_NAMES["recovery_report"]), recon)
    artifacts["recovery_report"] = ARTIFACT_NAMES["recovery_report"]
    write_json(os.path.join(out, ARTIFACT_NAMES["library_index"]), index)
    artifacts["library_index"] = ARTIFACT_NAMES["library_index"]

    summary = {
        "case_id": case_id,
        "file_count": answers["file_count"],
        "directory_count": answers["directory_count"],
        "symlink_count": answers["symlink_count"],
        "broken_symlink_count": answers["broken_symlink_count"],
        "duplicate_group_count": answers["duplicate_group_count"],
        "version_candidate_count": answers["version_candidate_count"],
        "catalog_entry_count": answers["catalog_entry_count"],
        "reconciliation_counts": recon["counts"],
    }
    write_manifest_md(os.path.join(out, ARTIFACT_NAMES["manifest_md"]),
                      summary, artifacts, errors, degradations)
    artifacts["manifest_md"] = ARTIFACT_NAMES["manifest_md"]

    change_log.append(logline("core_impl", "inventory_done",
                              f"{len(files)} files, {len(dirs)} dirs, "
                              f"{len(symlinks)} symlinks, {len(dup_groups)} dup groups"))
    write_jsonl(os.path.join(out, ARTIFACT_NAMES["operation_log"]), change_log)
    artifacts["operation_log"] = ARTIFACT_NAMES["operation_log"]

    # --- 组装 computed ---
    computed = {
        "files": [_jsonable(f) for f in files],
        "dirs": dirs,
        "symlinks": symlinks,
        "hardlink_groups": hardlink_groups,
        "summary": summary,
        "inventory_summary": {
            "file_count": answers["file_count"],
            "directory_count": answers["directory_count"],
            "symlink_count": answers["symlink_count"],
            "hardlink_group_count": len(hardlink_groups),
            "unreadable_count": len(answers["unreadable_files"]),
            "non_regular_count": len(answers["non_regular_files"]),
        },
        "duplicate_groups": dup_groups,
        "version_candidates": version_cands,
        "version_notes": dedup_version["version_notes"],
        "aliases": aliases,
        "reconciliation": recon,
        "answers": answers,
        "index": index,
        "artifacts": artifacts,
        "manifest_rows": _manifest_rows(files),
        "directory_tree_text": _tree_text(dirs, files, symlinks),
        "catalog_sources": catalog_sources,
        "contract_fill_note": "contract fields filled best-effort; see computed.contract_fill",
    }

    # 记录每个关键结论的证据来源（locator + 逐字 quote）
    sources = _build_sources(files, dirs, symlinks, dup_groups, recon,
                             catalog_records, input_root, task_text)

    # --- 组装 result.json ---
    result = {
        "case_id": case_id,
        "status": status if status == "insufficient_evidence" or status == "input_error" else "partial",
        "output_contract": fill_contract(contract, computed) if contract is not None else None,
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "runtime": {
            "skill": SKILL_NAME,
            "version": VERSION,
            "seconds": round(time.time() - t0, 3),
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "computed": computed,
    }
    # 若 contract 键名非 output_contract，也在结果顶部保留原始键名
    if contract is not None:
        for key in CONTRACT_KEYS:
            if key in case_data and key != "output_contract":
                result.setdefault(key, fill_contract(case_data[key], computed))

    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)
    change_log.append(logline("core_impl", "result_written",
                              f"status={result['status']}"))
    write_jsonl(os.path.join(out, ARTIFACT_NAMES["operation_log"]), change_log)
    return result_path, result


def _manifest_rows(files):
    rows = []
    for e in files:
        rows.append({k: e.get(k, "") for k in
                     ("path", "name", "ext", "type", "size", "sha256",
                      "mtime_iso", "mode_octal", "readable")})
    return rows


def _tree_text(dirs, files, symlinks):
    lines = []
    for d in dirs:
        lines.append((d["path"] + "/") if d["path"] != "." else "./")
    for f in files:
        lines.append(f["path"])
    for s in symlinks:
        lines.append(s["path"] + " -> " + s.get("target", ""))
    return "\n".join(lines)


def _build_sources(files, dirs, symlinks, dup_groups, recon, catalog_records,
                   input_root, task_text):
    """关键结论的证据定位：locator + 逐字 quote（材料文件名本身作证据）。"""
    sources = []
    for d in dirs[:200]:
        sources.append({"item": "directory",
                        "locator": "input/" + (d["path"] or "."),
                        "quote": d["path"] + (" [空目录]" if d.get("empty") else "")})
    for f in files[:500]:
        q = f["path"]
        if f.get("sha256"):
            q += " sha256=" + f["sha256"][:12]
        sources.append({"item": "file", "locator": "input/" + f["path"], "quote": q})
    for g in dup_groups:
        sources.append({"item": "duplicate_group",
                        "locator": "input/" + g["paths"][0],
                        "quote": "same SHA-256 " + g["sha256"][:12] + " for "
                                 + ", ".join(g["paths"])})
    for r in catalog_records[:200]:
        f = extract_record_fields(r)
        sources.append({"item": "catalog_record",
                        "locator": "input/" + str(r.get("_catalog_source", "")),
                        "quote": json.dumps(f.get("raw", {}), ensure_ascii=False)[:300]})
    if task_text:
        snippet = re.sub(r"\s+", " ", task_text)[:300]
        sources.append({"item": "task_rule", "locator": "input/task.md",
                        "quote": snippet})
    return sources


# ---------------------------------------------------------------------------
# 骨架 / 更新 / 检查
# ---------------------------------------------------------------------------

def write_skeleton(output_dir, work_dir, case_json_path):
    """前 3 分钟落盘：仅写 case_id + contract 占位 + status=partial。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_id = "unknown"
    contract = None
    if case_json_path and os.path.isfile(case_json_path):
        try:
            data = load_json(case_json_path)
            case_id = data.get("case_id") or data.get("id") or \
                data.get("challenge_id") or "unknown"
            for key in CONTRACT_KEYS:
                if key in data:
                    contract = data[key]
                    break
        except Exception:  # noqa: BLE001
            case_id = "unknown"
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": fill_contract(contract, {}) if contract else None,
        "sources": [], "uncertainty": [], "pending_human": [],
        "change_log": [logline("core_impl", "skeleton",
                               "partial skeleton written before full inventory")],
        "errors": [], "degradations": [], "limitations": [], "runtime": {
            "skill": SKILL_NAME, "version": VERSION,
            "seconds": 0.0, "python": sys.version.split()[0],
            "platform": sys.platform},
        "computed": {},
    }
    p = os.path.join(output_dir, "result.json")
    write_json(p, skeleton)
    return p


def update_result(result_path, patch_path):
    patch = load_json(patch_path)
    if not os.path.isfile(result_path):
        raise FileNotFoundError(result_path)
    base = load_json(result_path)
    merged = deep_merge(base, patch)
    merged["change_log"] = list(base.get("change_log", [])) + [
        logline("core_impl", "update", f"merged patch {patch_path}")
    ]
    write_json(result_path, merged)
    return result_path


def check_result(result_path, case_json_path=None):
    problems = []
    try:
        r = load_json(result_path)
    except Exception as exc:  # noqa: BLE001
        return False, [f"result.json not parseable: {exc}"]
    if not isinstance(r, dict):
        return False, ["result.json top-level is not a dict"]
    if not r.get("case_id"):
        problems.append("case_id missing or empty")
    if r.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        problems.append(f"invalid status: {r.get('status')!r}")
    for field in ("sources", "uncertainty", "change_log", "errors",
                  "degradations", "limitations", "pending_human"):
        if field not in r:
            problems.append(f"missing field: {field}")
    if case_json_path and os.path.isfile(case_json_path):
        try:
            data = load_json(case_json_path)
        except Exception:
            data = {}
        for key in CONTRACT_KEYS:
            if key in data and data[key]:
                if r.get("output_contract") is None and r.get(key) is None:
                    problems.append(f"output_contract ({key}) not backfilled")
                break
    return (not problems), problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研资料库修复确定性流水线（standard library only）")
    p.add_argument("--input", dest="input", default=None,
                   help=f"input directory (default: {DEFAULT_INPUT})")
    p.add_argument("--output", dest="output", default=None,
                   help=f"output directory (default: {DEFAULT_OUTPUT})")
    p.add_argument("--work", dest="work", default=None,
                   help=f"work directory (default: {DEFAULT_WORK})")
    p.add_argument("--case-json", dest="case_json", default=None,
                   help="path to case.json (default: INPUT/case.json)")
    p.add_argument("--task-path", dest="task_path", default=None,
                   help="path to task.md (default: INPUT/task.md)")
    p.add_argument("--skeleton-only", action="store_true",
                   help="write partial skeleton result.json only")
    p.add_argument("--check", dest="check", metavar="RESULT", default=None,
                   help="validate an existing result.json and exit")
    p.add_argument("--update", dest="update", metavar="PATCH", default=None,
                   help="deep-merge a patch.json into result.json")
    p.add_argument("--result", dest="result", default=None,
                   help="result.json path for --update")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    input_root = args.input or os.environ.get("SKILL_INPUT") or DEFAULT_INPUT
    output_dir = args.output or os.environ.get("SKILL_OUTPUT") or DEFAULT_OUTPUT
    work_dir = args.work or os.environ.get("SKILL_WORK") or DEFAULT_WORK
    case_json = args.case_json or os.path.join(input_root, "case.json")

    if args.check:
        ok, problems = check_result(args.check, case_json)
        if ok:
            print(f"check: ok -> {args.check}")
            return 0
        print("check: FAILED")
        for pr in problems:
            print(f"  - {pr}")
        return 1

    if args.update:
        result_path = args.result or os.path.join(output_dir, "result.json")
        update_result(result_path, args.update)
        print(f"update: merged {args.update} -> {result_path}")
        return 0

    if args.skeleton_only:
        p = write_skeleton(output_dir, work_dir, case_json)
        print(f"skeleton: {p}")
        return 0

    result_path, result = run_pipeline(input_root, output_dir, work_dir,
                                       case_json, args.task_path)
    s = result["computed"].get("summary", {})
    print(f"finalize: {s.get('file_count', 0)} files, "
          f"{s.get('duplicate_group_count', 0)} dup groups, "
          f"{s.get('version_candidate_count', 0)} version candidates, "
          f"{len(result['computed'].get('symlinks', []))} symlinks, "
          f"{result['computed'].get('reconciliation', {}).get('counts', {}).get('mismatches', 0)} reconcile mismatches "
          f"-> {result_path} [status={result['status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
