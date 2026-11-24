#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 科研资料库修复的确定性主流程实现（入口见 finalize.py）。

流水线（只读源文件、写 /app/output）：
  盘点 -> 内容去重 -> 版本候选 -> 别名(符号/硬链接) -> 行政版本说明 -> 恢复对账
  -> 可检索索引 -> 机械问答 -> 可编辑产物 -> result.json

仅依赖 Python 标准库；遍历排序、无随机数，除时间戳外重复运行结果一致。
"""
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
HASH_MAX_BYTES = 16 * 1024 * 1024        # 超过则跳过内容哈希，记录 too_large
TEXT_MAX_BYTES = 512 * 1024              # 文本预览读取上限
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract", "output_schema")
LIST_HINTS = {
    "files", "items", "entries", "groups", "candidates", "notes", "aliases",
    "answers", "errors", "degradations", "sources", "uncertainty",
    "pending_human", "change_log", "limitations", "questions", "records",
}

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rel(p, root):
    try:
        r = os.path.relpath(p, root)
    except (ValueError, OSError):
        r = p
    return r.replace(os.sep, "/")


def read_text(path, max_bytes=TEXT_MAX_BYTES):
    """读取文本，优先 utf-8-sig；失败降级 latin-1，不崩溃。"""
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError as e:
        return None, "unreadable", str(e)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace"), "latin-1", "decode_fallback"


def sha256_of(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# 输入发现：case.json / task.md / output_contract
# ---------------------------------------------------------------------------
def find_case_json(input_root):
    for name in ("case.json", "case.input.json", "input.json"):
        p = os.path.join(input_root, name)
        if os.path.isfile(p):
            return p
    return None


def detect_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for k in ("case_id", "caseId", "challenge_id", "challengeId", "id", "armiId"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge[-_]?id\s*[:：]\s*`?([^`\s]+)", task_text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"\b(challenge-[0-9a-fA-F]{8})\b", task_text)
        if m:
            return m.group(1)
    return ""


def detect_contract(case_obj):
    """在 case.json 顶层自动识别 output_contract（键名可变）。"""
    if not isinstance(case_obj, dict):
        return None
    for k in CONTRACT_KEYS:
        v = case_obj.get(k)
        if isinstance(v, dict) and v:
            return v
    # 也接受嵌套一层
    for k, v in case_obj.items():
        if isinstance(v, dict) and any(kk in v for kk in CONTRACT_KEYS):
            for kk in CONTRACT_KEYS:
                if isinstance(v.get(kk), dict):
                    return v[kk]
    return None


def detect_questions(case_obj):
    """提取题目或案例中的问题/任务列表，供机械问答对齐。"""
    out = []
    if not isinstance(case_obj, dict):
        return out
    for k, v in case_obj.items():
        kl = k.lower()
        if any(t in kl for t in ("question", "query", "task", "prompt", "instruction")):
            if isinstance(v, str) and v.strip():
                out.append({"item": k, "text": v.strip()})
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, str):
                        out.append({"item": "%s[%d]" % (k, i), "text": item})
                    elif isinstance(item, dict):
                        out.append({"item": "%s[%d]" % (k, i), "text": str(item)})
    return out


# ---------------------------------------------------------------------------
# 1. 盘点
# ---------------------------------------------------------------------------
def inventory(input_root):
    """确定性递归盘点：文件、目录、权限、符号/硬链接、空目录、不可读项。"""
    files, dirs = [], []
    errors = []

    def mode_str(mode):
        if os.name == "nt":
            return "ro" if (mode & 0o222) == 0 else "rw"
        return "%o" % (mode & 0o7777)

    for base, subdirs, names in os.walk(input_root):
        subdirs.sort()
        names.sort()
        for d in subdirs:
            p = os.path.join(base, d)
            try:
                st = os.lstat(p)
            except OSError as e:
                errors.append({"locator": rel(p, input_root), "kind": "stat_error", "detail": str(e)})
                continue
            empty = (len(names) == 0 and len(subdirs) == 0)
            dirs.append({
                "path": rel(p, input_root), "type": "dir", "empty": empty,
                "mode": mode_str(st.st_mode), "mtime": int(st.st_mtime),
            })
        for n in names:
            p = os.path.join(base, n)
            rp = rel(p, input_root)
            entry = {"path": rp, "name": n}
            try:
                st = os.lstat(p)
            except OSError as e:
                entry.update({"type": "unreadable", "note": "stat_error", "error": str(e)})
                files.append(entry)
                errors.append({"locator": rp, "kind": "stat_error", "detail": str(e)})
                continue
            entry.update({"size": st.st_size, "mtime": int(st.st_mtime),
                          "mode": mode_str(st.st_mode), "inode": int(getattr(st, "st_ino", 0) or 0),
                          "device": int(getattr(st, "st_dev", 0) or 0),
                          "nlink": int(getattr(st, "st_nlink", 0) or 0)})
            if os.path.islink(p):
                try:
                    tgt = os.readlink(p)
                except OSError as e:
                    tgt = "<readlink_error: %s>" % e
                entry.update({"type": "symlink", "link_target": tgt,
                              "resolved": rel(os.path.realpath(p), input_root),
                              "broken": not os.path.exists(p)})
            elif os.path.isfile(p):
                entry["type"] = "file"
                if st.st_size > HASH_MAX_BYTES:
                    entry.update({"sha256": None, "note": "too_large"})
                else:
                    h = sha256_of(p)
                    entry["sha256"] = h
                    if h is None:
                        entry["note"] = "hash_error"
                if not entry.get("note"):
                    ext = os.path.splitext(n)[1].lower()
                    if ext in (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".bib", ".ris",
                               ".log", ".xml", ".html", ".tex"):
                        text, enc, err = read_text(p)
                        entry["text_encoding"] = enc
                        entry["text_preview"] = (text[:400] if text is not None else None)
            else:
                entry["type"] = "other"
            files.append(entry)
    files.sort(key=lambda e: e["path"])
    dirs.sort(key=lambda e: e["path"])
    return files, dirs, errors


# ---------------------------------------------------------------------------
# 2. 去重 / 3. 版本候选 / 4. 别名
# ---------------------------------------------------------------------------
def dedup(files):
    groups, by_hash = [], {}
    for e in files:
        if e.get("type") == "file" and e.get("sha256"):
            by_hash.setdefault(e["sha256"], []).append(e["path"])
    for h, paths in sorted(by_hash.items()):
        if len(paths) > 1:
            groups.append({"sha256": h, "count": len(paths), "paths": sorted(paths)})
    groups.sort(key=lambda g: (-g["count"], g["sha256"]))
    return groups


_VERSION_TOKEN = re.compile(
    r"(?:[-_.\s]*(?:v|ver|version|rev|r)[-_.]?\d+(?:\.\d+)*"
    r"|[-_.\s]*(?:final|draft|copy|backup|bak|old|new|latest|clean)"
    r"|[-_.\s]*[\(\[\{]\d+[\)\]\}]"
    r"|[-_.]\d{4}[-_.]?\d{2}[-_.]?\d{2})",
    re.IGNORECASE)


def norm_stem(name):
    base = os.path.splitext(name)[0]
    s = base.lower()
    s = _VERSION_TOKEN.sub("", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", s).strip("_")
    return s or base.lower()


def version_candidates(files):
    """按归一化主干 + 内容哈希/大小差异给候选版本链（启发式）。"""
    by_stem = {}
    for e in files:
        if e.get("type") != "file":
            continue
        stem = norm_stem(e["name"])
        by_stem.setdefault(stem, []).append(e)
    cands = []
    for stem, es in sorted(by_stem.items()):
        if len(es) < 2:
            continue
        hashes = {e.get("sha256") for e in es if e.get("sha256")}
        es_sorted = sorted(es, key=lambda e: (e["mtime"], e["path"]))
        chain = [{
            "path": e["path"], "mtime": e["mtime"], "size": e["size"],
            "sha256": e.get("sha256"),
        } for e in es_sorted]
        diffs = []
        for a, b in zip(es_sorted, es_sorted[1:]):
            diffs.append({
                "from": a["path"], "to": b["path"],
                "size_delta": b["size"] - a["size"],
                "hash_changed": (a.get("sha256") != b.get("sha256")),
            })
        cands.append({
            "stem": stem, "count": len(es), "distinct_hashes": len(hashes),
            "heuristic": True, "chain": chain, "diffs": diffs,
        })
    cands.sort(key=lambda c: (-c["count"], c["stem"]))
    return cands


def aliases(files):
    sym, hard = [], []
    inode_map = {}
    for e in files:
        if e.get("type") == "symlink":
            sym.append({"path": e["path"], "link_target": e.get("link_target"),
                        "resolved": e.get("resolved"), "broken": bool(e.get("broken"))})
        elif e.get("type") == "file" and e.get("inode") and e.get("device"):
            inode_map.setdefault((e["device"], e["inode"]), []).append(e["path"])
    sym.sort(key=lambda x: x["path"])
    for (dev, ino), paths in sorted(inode_map.items()):
        if len(paths) > 1:
            hard.append({"device": dev, "inode": ino, "count": len(paths), "paths": sorted(paths)})
    hard.sort(key=lambda x: (-x["count"], x["device"], x["inode"]))
    return sym, hard


# ---------------------------------------------------------------------------
# 5. 行政版本说明
# ---------------------------------------------------------------------------
VERSION_NOTE_RE = re.compile(
    r"(?i)(version|版本|修订|release|变更|changelog|history|更新记录)[^\n]{0,120}",
)
VERSION_FILE_RE = re.compile(r"(?i)(version|版本|changelog|note|说明|历史|change|release)")


def parse_version_notes(input_root, files, task_text):
    notes = []
    seen = set()
    for e in files:
        if e.get("type") != "file":
            continue
        rp = e["path"]
        name_lc = e["name"].lower()
        if not (VERSION_FILE_RE.search(name_lc) or VERSION_FILE_RE.search(rp)):
            continue
        if rp.endswith("case.json"):
            continue
        p = os.path.join(input_root, *rp.split("/"))
        text, _enc, err = read_text(p)
        if text is None:
            notes.append({"source": rp, "locator": rp, "quote": "",
                          "kind": "unreadable", "detail": err})
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if VERSION_NOTE_RE.search(line):
                key = (rp, ln)
                if key in seen:
                    continue
                seen.add(key)
                notes.append({"source": rp, "locator": "%s#L%d" % (rp, ln),
                              "quote": line.strip()[:200], "kind": "version_note"})
    if task_text:
        for ln, line in enumerate(task_text.splitlines(), 1):
            if "版本说明" in line or "行政版本" in line:
                notes.append({"source": "task.md", "locator": "task.md#L%d" % ln,
                              "quote": line.strip()[:200], "kind": "admin_version_rule"})
    notes.sort(key=lambda x: (x["source"], x["locator"]))
    return notes


# ---------------------------------------------------------------------------
# 6. 库目录解析 + 恢复对账
# ---------------------------------------------------------------------------
def _catalog_key(entry):
    if not isinstance(entry, dict):
        return None
    for k in ("path", "file", "file_path", "filename", "filepath", "name", "key", "id", "doi", "DOI"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def parse_json_catalog(path):
    obj, err = load_json(path)
    if err or obj is None:
        return [], err or "empty"
    if isinstance(obj, list):
        return [e for e in obj if isinstance(e, dict)], None
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                return [e for e in v if isinstance(e, dict)], None
        return [obj], None
    return [], "not_records"


def parse_csv_catalog(path):
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return [r for r in rows if r], None
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        return [], str(e)


def parse_bib_catalog(text):
    entries, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("@"):
            if cur:
                entries.append(cur)
            cur = {"type": line[1:].split("{", 1)[0].lower()}
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip().lower()
            v = v.strip().strip("{}, \t\"")
            cur[k] = v
    if cur:
        entries.append(cur)
    return [e for e in entries if e]


def parse_ris_catalog(text):
    entries, cur = [], {}
    for line in text.splitlines():
        if line.startswith("ER"):
            if cur:
                entries.append(cur)
            cur = {}
        elif len(line) >= 6 and line[2] == "-":
            tag, val = line[:2].strip().upper(), line[6:].strip()
            if tag == "TY":
                cur["type"] = val
            else:
                cur[tag] = val
    if cur:
        entries.append(cur)
    return [e for e in entries if e]


def parse_sqlite_catalog(path):
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as e:
        return [], "sqlite_open_error: %s" % e
    entries = []
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            try:
                cols = [c[1] for c in cur.execute("PRAGMA table_info(%s)" % t).fetchall()]
                path_col = next((c for c in cols if c.lower() in
                                 ("path", "file", "filename", "filepath", "name", "key", "id")), None)
                sel = path_col or cols[0]
                for row in cur.execute("SELECT * FROM %s LIMIT 500" % t).fetchall():
                    d = dict(zip(cols, row))
                    d["_table"] = t
                    entries.append(d)
            except sqlite3.Error as e:
                entries.append({"_table": t, "_error": "sqlite_query_error: %s" % e})
    finally:
        conn.close()
    return entries, None


def parse_catalogs(input_root, files):
    catalogs, raw_entries = [], []
    for e in files:
        rp = e["path"]
        if e.get("type") != "file" or rp.endswith("case.json"):
            continue
        ext = os.path.splitext(e["name"])[1].lower()
        if ext not in (".json", ".csv", ".bib", ".ris", ".db", ".sqlite", ".sqlite3"):
            continue
        p = os.path.join(input_root, *rp.split("/"))
        entries, err = [], None
        if ext == ".json":
            entries, err = parse_json_catalog(p)
        elif ext == ".csv":
            entries, err = parse_csv_catalog(p)
        elif ext == ".bib":
            text, _, rerr = read_text(p)
            if rerr:
                err = rerr
            else:
                entries = parse_bib_catalog(text or "")
        elif ext == ".ris":
            text, _, rerr = read_text(p)
            if rerr:
                err = rerr
            else:
                entries = parse_ris_catalog(text or "")
        else:
            entries, err = parse_sqlite_catalog(p)
        raw_entries.extend({"source": rp, **_normalize_entry(x)} for x in entries)
        catalogs.append({"source": rp, "format": ext.lstrip("."),
                         "entries": len(entries), "error": err})
    catalogs.sort(key=lambda c: c["source"])
    return catalogs, raw_entries


def _normalize_entry(entry):
    d = dict(entry)
    d.pop("text_preview", None)
    d.pop("sha256", None)
    d.pop("mode", None)
    d.pop("mtime", None)
    d.pop("size", None)
    return d


def reconcile(raw_entries, files):
    actual_paths = {e["path"] for e in files}
    actual_names = {e["name"] for e in files}
    expected_keys = set()
    by_key = {}
    for re_ in raw_entries:
        k = _catalog_key(re_)
        if not k:
            continue
        base = k.replace("\\", "/").split("/")[-1]
        expected_keys.add(k)
        expected_keys.add(base)
        by_key.setdefault(k, []).append(re_["source"])
    matches = sorted(p for p in actual_paths if p in expected_keys or os.path.basename(p) in expected_keys)
    missing = sorted(k for k in expected_keys if k not in actual_paths and k not in actual_names)
    orphaned = sorted(p for p in actual_paths
                      if p not in expected_keys and os.path.basename(p) not in expected_keys)
    return {
        "catalog_entry_count": len(raw_entries),
        "matches": matches,
        "mismatches": [],          # 无白板哈希/大小可核对时保持空，由 Agent 语义层补充
        "missing": missing,
        "orphaned": orphaned,
        "matches_count": len(matches),
        "missing_count": len(missing),
        "orphaned_count": len(orphaned),
    }


# ---------------------------------------------------------------------------
# 7. 索引 / 8. 机械问答
# ---------------------------------------------------------------------------
def build_index(files, raw_entries):
    index = {}
    def add(kw, path):
        kw = kw.strip().lower()
        if kw and len(kw) > 1:
            index.setdefault(kw, set()).add(path)
    for e in files:
        p = e["path"]
        for tok in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", e["name"]):
            add(tok, p)
        add(e.get("type", ""), p)
        ext = os.path.splitext(e["name"])[1].lstrip(".").lower()
        if ext:
            add("ext:" + ext, p)
    for re_ in raw_entries:
        for k in ("author", "year", "title", "journal", "DOI", "doi", "type"):
            v = re_.get(k)
            if isinstance(v, str):
                for tok in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", v):
                    add(tok, re_["source"])
    return {k: sorted(v) for k, v in sorted(index.items())}


def mechanical_answers(files, dirs, dup_groups, cands, sym, hard, recon, catalogs):
    ans = []
    def a(question, value, locators):
        ans.append({"question": question, "answer": value, "evidence": locators})
    a("文件总数", len([f for f in files if f.get("type") == "file"]), ["computed.summary"])
    a("目录总数(含空目录)", len(dirs), ["computed.summary"])
    a("空目录数", len([d for d in dirs if d.get("empty")]), ["computed.summary"])
    a("重复组数", len(dup_groups),
      ["dedup_version.json#duplicate_groups" for _ in dup_groups[:1]] or ["computed.duplicate_groups"])
    a("断链符号链接数", len([s for s in sym if s.get("broken")]),
      ["computed.aliases"])
    a("硬链接组数", len(hard), ["computed.aliases"])
    a("版本候选链数", len(cands), ["computed.version_candidates"])
    a("库目录对账: matches", recon["matches_count"], ["recovery_report.json#matches"])
    a("库目录对账: missing", recon["missing_count"], ["recovery_report.json#missing"])
    a("库目录对账: orphaned", recon["orphaned_count"], ["recovery_report.json#orphaned"])
    a("库目录源数量", len(catalogs), ["computed.summary"])
    return ans


# ---------------------------------------------------------------------------
# 产物写盘
# ---------------------------------------------------------------------------
def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_artifacts(output_dir, files, dirs, dup_groups, cands, version_notes, sym, hard,
                    recon, index, answers, catalogs, errors, degradations):
    artifacts = {}
    ensure_dir(output_dir)

    # CSV 清单
    csv_path = os.path.join(output_dir, "library_manifest.csv")
    cols = ["path", "name", "type", "size", "mtime", "mode", "sha256",
            "inode", "nlink", "link_target", "note", "text_encoding"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in files:
            w.writerow({k: ("" if e.get(k) is None else e.get(k)) for k in cols})
    artifacts["library_manifest.csv"] = "library_manifest.csv"

    # 目录树
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    lines = []
    for d in dirs:
        indent = "  " * (d["path"].count("/"))
        lines.append("%s%s/ %s" % (indent, d["path"].split("/")[-1] or ".", "[空目录]" if d["empty"] else ""))
    for e in files:
        indent = "  " * (e["path"].count("/"))
        suffix = ""
        if e.get("type") == "symlink":
            suffix = " -> %s" % (e.get("link_target") or "")
        elif e.get("note"):
            suffix = " [%s]" % e["note"]
        lines.append("%s%s%s" % (indent, e["path"].split("/")[-1], suffix))
    with open(tree_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    artifacts["directory_tree.txt"] = "directory_tree.txt"

    # 去重/版本/别名
    dd_path = os.path.join(output_dir, "dedup_version.json")
    write_json(dd_path, {"duplicate_groups": dup_groups, "version_candidates": cands,
                         "version_notes": version_notes,
                         "aliases": {"symlinks": sym, "hardlinks": hard}})
    artifacts["dedup_version.json"] = "dedup_version.json"

    # 恢复报告
    rr_path = os.path.join(output_dir, "recovery_report.json")
    write_json(rr_path, {"reconciliation": recon, "catalogs": catalogs,
                         "errors": errors, "degradations": degradations})
    artifacts["recovery_report.json"] = "recovery_report.json"

    # 索引
    idx_path = os.path.join(output_dir, "library_index.json")
    write_json(idx_path, {"index": index, "answers": answers})
    artifacts["library_index.json"] = "library_index.json"

    # 交接报告
    md_path = os.path.join(output_dir, "library_manifest.md")
    md = [
        "# 科研资料库交接报告（自动生成）",
        "",
        "- 文件数：%d" % len([f for f in files if f.get("type") == "file"]),
        "- 目录数：%d（空目录 %d）" % (len(dirs), len([d for d in dirs if d.get("empty")])),
        "- 重复组：%d" % len(dup_groups),
        "- 版本候选链：%d" % len(cands),
        "- 断链符号链接：%d" % len([s for s in sym if s.get("broken")]),
        "- 硬链接组：%d" % len(hard),
        "- 对账 matches/missing/orphaned：%d / %d / %d" % (
            recon["matches_count"], recon["missing_count"], recon["orphaned_count"]),
        "",
        "明细见 `library_manifest.csv`、`dedup_version.json`、`recovery_report.json`、`library_index.json`。",
        "本报告为脚本确定性结果，语义层结论（字段映射、版本链确认、检索答案）由 Agent 在 `result.json` 中补 `sources`/`uncertainty`。",
        "",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    artifacts["library_manifest.md"] = "library_manifest.md"

    # 操作日志
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        pass
    artifacts["operation_log.jsonl"] = "operation_log.jsonl"
    return artifacts


# ---------------------------------------------------------------------------
# result.json 组装
# ---------------------------------------------------------------------------
def fill_contract(contract, computed, case_id):
    out = {}
    for k, v in contract.items():
        if k == "case_id":
            out[k] = case_id
        elif k in computed:
            out[k] = computed[k]
        elif k in LIST_HINTS:
            out[k] = []
        else:
            out[k] = None
    return out


def build_result(case_id, status, contract, computed, errors, degradations, change_log,
                 runtime, limitations, questions):
    result = {
        "case_id": case_id,
        "status": status,
    }
    if contract is not None:
        result.update(fill_contract(contract, computed, case_id))
    result.update({
        "computed": computed,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "runtime": runtime,
        "change_log": change_log,
        "_questions_extracted": questions,
    })
    return result


def skeleton_result(case_id, contract):
    computed = {"files": [], "summary": {}, "duplicate_groups": [],
                "version_candidates": [], "version_notes": [], "aliases": {"symlinks": [], "hardlinks": []},
                "reconciliation": {}, "index": {}, "answers": [], "artifacts": {}}
    return build_result(case_id, "partial", contract, computed, [], [],
                        [{"time": now_iso(), "actor": "finalize.py", "step": "skeleton",
                          "detail": "先落盘 partial 骨架，避免中途异常留下空产物"}],
                        {"seconds": None, "note": "skeleton-only"}, [], [])


def merge_patch(result, patch):
    def merge(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            for k, v in b.items():
                a[k] = merge(a.get(k), v) if k in a else v
            return a
        return b
    for k, v in patch.items():
        result[k] = merge(result.get(k), v) if k in result else v
    return result


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_full(input_root, output_dir, work_dir, case_json_path, task_path):
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    start = time.time()
    change_log = []
    def log_step(step, detail):
        change_log.append({"time": now_iso(), "actor": "finalize.py", "step": step, "detail": detail})

    errors, degradations = [], []

    case_obj, case_err = load_json(case_json_path) if case_json_path else (None, "missing_case_json")
    if case_json_path is None:
        case_obj, case_err = None, "missing_case_json"
    task_text = None
    if task_path and os.path.isfile(task_path):
        task_text, _enc, _ = read_text(task_path, TEXT_MAX_BYTES)

    case_id = detect_case_id(case_obj, task_text or "")
    contract = detect_contract(case_obj) if isinstance(case_obj, dict) else None
    questions = detect_questions(case_obj)

    if case_json_path is None or not os.path.isfile(case_json_path):
        status = "input_error"
        errors.append({"locator": "/input/case.json", "kind": "missing_case_json",
                       "detail": "case.json 不存在，无法识别 case_id 与 output_contract"})
    else:
        if case_err:
            errors.append({"locator": rel(case_json_path, input_root), "kind": "bad_json",
                           "detail": case_err})
            degradations.append("case.json 解析失败，按最小元数据继续")
        status = "ok"

    # 先落盘骨架（保证中途失败仍有合法结果）
    skeleton = skeleton_result(case_id or "unknown", contract)
    result_path = os.path.join(output_dir, "result.json")
    try:
        write_json(result_path, skeleton)
        log_step("skeleton", "result.json partial 骨架已落盘")
    except OSError as e:
        errors.append({"locator": result_path, "kind": "write_error", "detail": str(e)})
        print("finalize: skeleton write failed: %s" % e, file=sys.stderr)
        sys.exit(2)

    files, dirs, inv_errors = inventory(input_root)
    for e in inv_errors:
        errors.append(e)

    dup_groups = dedup(files)
    cands = version_candidates(files)
    sym, hard = aliases(files)
    version_notes = parse_version_notes(input_root, files, task_text or "")
    catalogs, raw_entries = parse_catalogs(input_root, files)
    recon = reconcile(raw_entries, files)
    index = build_index(files, raw_entries)
    answers = mechanical_answers(files, dirs, dup_groups, cands, sym, hard, recon, catalogs)

    artifacts = write_artifacts(output_dir, files, dirs, dup_groups, cands, version_notes,
                                sym, hard, recon, index, answers, catalogs, errors, degradations)
    log_step("artifacts", "可编辑产物已写入 /app/output")

    total_size = sum(e.get("size") or 0 for e in files if e.get("type") == "file")
    summary = {
        "files": len([f for f in files if f.get("type") == "file"]),
        "dirs": len(dirs),
        "empty_dirs": len([d for d in dirs if d.get("empty")]),
        "total_size": total_size,
        "hashed": len([f for f in files if f.get("sha256")]),
        "symlinks": len(sym),
        "broken_symlinks": len([s for s in sym if s.get("broken")]),
        "hardlink_groups": len(hard),
        "duplicate_groups": len(dup_groups),
        "version_candidates": len(cands),
        "catalog_sources": len(catalogs),
    }
    computed = {
        "files": files,
        "dirs": dirs,
        "summary": summary,
        "duplicate_groups": dup_groups,
        "version_candidates": cands,
        "version_notes": version_notes,
        "aliases": {"symlinks": sym, "hardlinks": hard},
        "catalogs": catalogs,
        "reconciliation": recon,
        "index": index,
        "answers": answers,
        "artifacts": artifacts,
    }

    if not files and not dirs:
        status = "insufficient_evidence"
        degradations.append("输入目录无任何文件或目录")

    runtime = {"seconds": round(time.time() - start, 3)}
    limitations = [
        "去重按内容 SHA-256，超 %dMB 文件跳过内容哈希并标 too_large" % (HASH_MAX_BYTES // (1024 * 1024)),
        "版本候选为启发式：按归一化主干 + 内容差异，需 Agent 在 sources 中补语义确认",
        "SQLite/JSON/CSV/BibTeX/RIS 库目录仅在存在时解析；解析失败记录到 recovery_report.json",
        "mismatches 需白板哈希/大小才可判定，无白板时保持空，由 Agent 语义层补充",
    ]
    log_step("finalize", "完整 result.json 已写出, status=%s" % status)
    result = build_result(case_id or "unknown", status, contract, computed, errors,
                          degradations, change_log, runtime, limitations, questions)
    try:
        write_json(result_path, result)
    except OSError as e:
        errors.append({"locator": result_path, "kind": "write_error", "detail": str(e)})
        print("finalize: result write failed: %s" % e, file=sys.stderr)
        sys.exit(2)

    with open(os.path.join(output_dir, "operation_log.jsonl"), "a", encoding="utf-8") as f:
        for c in change_log:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print("finalize: %d files, %d dup groups, %d version candidates, %d broken symlinks, "
          "reconcile %d/%d/%d -> %s" % (
              summary["files"], len(dup_groups), len(cands),
              summary["broken_symlinks"], recon["matches_count"],
              recon["missing_count"], recon["orphaned_count"], result_path))
    return 0


def cmd_check(result_path, case_json_path):
    obj, err = load_json(result_path)
    if err or obj is None:
        print("check FAIL: result.json 不可读: %s" % err)
        return 1
    problems = []
    if not obj.get("case_id"):
        problems.append("case_id 为空")
    if obj.get("status") not in ALLOWED_STATUS:
        problems.append("status 非法: %r" % obj.get("status"))
    if case_json_path and os.path.isfile(case_json_path):
        case_obj, cerr = load_json(case_json_path)
        if not cerr and isinstance(case_obj, dict):
            contract = detect_contract(case_obj)
            if contract:
                for k in contract:
                    if k not in obj and k not in obj.get("computed", {}):
                        problems.append("contract 字段缺失: %s" % k)
    for k in ("sources", "uncertainty", "change_log"):
        if k not in obj:
            problems.append("缺少辅助字段: %s" % k)
    if problems:
        for p in problems:
            print("check FAIL: %s" % p)
        return 1
    print("check ok: case_id=%r status=%r" % (obj.get("case_id"), obj.get("status")))
    return 0


def cmd_update(result_path, patch_path):
    obj, err = load_json(result_path)
    if err or obj is None:
        print("update FAIL: 无法读取 result.json: %s" % err, file=sys.stderr)
        return 1
    patch, perr = load_json(patch_path)
    if perr or not isinstance(patch, dict):
        print("update FAIL: patch 不可读: %s" % perr, file=sys.stderr)
        return 1
    merge_patch(obj, patch)
    obj.setdefault("change_log", []).append(
        {"time": now_iso(), "actor": "finalize.py", "step": "update",
         "detail": "增量合并 patch: %s" % os.path.basename(patch_path)})
    write_json(result_path, obj)
    print("update ok -> %s" % result_path)
    return 0


def cmd_skeleton(output_dir, case_json, task_path):
    ensure_dir(output_dir)
    case_obj, _cerr = load_json(case_json) if os.path.isfile(case_json) else (None, "missing")
    contract = detect_contract(case_obj) if isinstance(case_obj, dict) else None
    task_text = None
    if os.path.isfile(task_path):
        task_text, _, _ = read_text(task_path, TEXT_MAX_BYTES)
    cid = detect_case_id(case_obj, task_text or "")
    sk = skeleton_result(cid or "unknown", contract)
    write_json(os.path.join(output_dir, "result.json"), sk)
    print("skeleton ok -> %s" % os.path.join(output_dir, "result.json"))
    return 0
