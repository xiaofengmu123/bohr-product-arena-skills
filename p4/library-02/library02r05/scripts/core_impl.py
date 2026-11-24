# -*- coding: utf-8 -*-
"""core_impl.py — 科研资料库整理与版本感知检索（确定性、契约对齐、可复核）。

设计取舍（相对通用 inventory 脚本的明确升级）：
1. output_contract 是**一等公民**：递归识别多种键名（output_contract/output/
   result_schema/contract/output_schema），逐叶回填全部字段（缺值 null/[]），
   保留未知键、不改名、不删除；与保留键冲突时改存 computed._contract_<字段>，
   并输出 contract_coverage（每字段 backfilled/present/stashed/missing）。
2. change_log 是 **append-only 时间线**：每步写入 result.change_log 的同时
   落盘 operation_log.jsonl；--update 增量合并语义补丁而不重跑确定性盘点。
3. 先落盘、再分析、后增量改进：--skeleton-only 秒级写出合法 partial 骨架，
   任意中途异常都不会留下空产物。
4. 只读源文件；遍历排序、哈希稳定、无随机数，重复运行结果一致（时间戳除外）。
5. 纯 Python 标准库，无第三方依赖、无个人绝对路径。
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
import sys
import time
import unicodedata
from collections import defaultdict, OrderedDict

VERSION = "2.0.0"
ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_CONTENT_BYTES = 2 * 1024 * 1024      # 内容哈希/文本抽取上限
MAX_TEXT_CHARS = 20000                   # 相似度比对用文本上限
MAX_INDEX_TERMS = 5000                   # 索引词条上限（防超大索引）
RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "runtime", "limitations", "pending_human",
    "computed", "artifacts",
})
CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract",
                 "output_schema", "result")
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".tex", ".bib",
            ".ris", ".rst", ".py", ".html", ".htm", ".xml", ".yml", ".yaml",
            ".log", ".tsv", ".ini", ".cfg"}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mono():
    return time.monotonic()


def _log(change_log, step, detail, actor="skill-core"):
    change_log.append({"time": _utcnow(), "actor": actor,
                       "step": step, "detail": detail})


def safe_json_load(path):
    """读 JSON，容错：坏 JSON 返回 None 并附错误文本。"""
    try:
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception as e:  # noqa: BLE001
        return {"__json_error__": "%s: %s" % (type(e).__name__, e)}


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def read_text_sample(path):
    """读取文件文本样本（最多 MAX_CONTENT_BYTES），坏编码降级，失败返回 None。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_CONTENT_BYTES)
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)[:MAX_TEXT_CHARS]
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]


def set_nested(root, path_parts, value):
    cur = root
    for part in path_parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = OrderedDict()
        cur = cur[part]
    cur[path_parts[-1]] = value


def deep_merge(base, patch):
    """递归合并 patch 到 base；累积型列表键扩展，其余替换。返回新对象。"""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = OrderedDict(base)
    accum = {"sources", "uncertainty", "errors", "degradations",
             "change_log", "pending_human"}
    for k, v in patch.items():
        if k in accum and isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + v
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# 输入契约识别
# --------------------------------------------------------------------------- #
def _find_key(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
        for v in obj.values():
            hit = _find_key(v, keys)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for item in obj:
            hit = _find_key(item, keys)
            if hit is not None:
                return hit
    return None


def extract_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "challenge_id", "challengeId", "id",
                  "armiId", "task_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge_id[:\s`]+([A-Za-z0-9\-_]+)", task_text)
        if m:
            return m.group(1)
    return ""


def extract_contract(case):
    """在 case.json 中识别 output_contract（支持多种键名与嵌套位置）。"""
    if isinstance(case, dict) and "__json_error__" in case:
        return None
    if isinstance(case, dict):
        for k in CONTRACT_KEYS:
            if k in case:
                return case[k]
    return _find_key(case, CONTRACT_KEYS)


# --------------------------------------------------------------------------- #
# output_contract 递归回填
# --------------------------------------------------------------------------- #
def _placeholder(node):
    if isinstance(node, dict):
        if "default" in node:
            return node["default"]
        t = str(node.get("type", "")).lower()
        if "list" in t or "array" in t:
            return []
        if "dict" in t or "object" in t:
            return OrderedDict()
        if "bool" in t or "boolean" in t:
            return False
        return None
    if isinstance(node, list):
        return []
    if isinstance(node, str):
        s = node.lower()
        if "list" in s or "array" in s or s.endswith("[]"):
            return []
        if s in ("int", "integer", "number", "float", "double", "string",
                 "str", "text", "bool", "boolean", "null"):
            return None
        return None
    if isinstance(node, bool) or isinstance(node, (int, float)):
        return node
    return None


def flatten_contract(node, prefix=None):
    """把契约规格扁平化为 (路径片段列表, 占位值) 序列。"""
    if prefix is None:
        prefix = []
    if isinstance(node, dict):
        if isinstance(node.get("fields"), dict):
            for k, v in node["fields"].items():
                yield from flatten_contract(v, prefix + [str(k)])
            return
        if isinstance(node.get("properties"), dict):
            for k, v in node["properties"].items():
                yield from flatten_contract(v, prefix + [str(k)])
            return
        if "type" in node or "default" in node:
            if prefix:
                yield (prefix, _placeholder(node))
            return
        if not node and prefix:
            yield (prefix, OrderedDict())
            return
        for k, v in node.items():
            yield from flatten_contract(v, prefix + [str(k)])
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, str):
                yield (prefix + [item], None)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    yield from flatten_contract(item, prefix + [str(name)])
                else:
                    yield from flatten_contract(item, prefix + [str(i)])
            else:
                yield (prefix + [str(i)], _placeholder(item))
        return
    if prefix:
        yield (prefix, _placeholder(node))


def merge_contract(result, contract):
    """逐叶回填契约字段；冲突字段改存 computed._contract_<字段>。返回覆盖率。"""
    coverage = []
    if contract is None:
        return coverage
    for path, ph in flatten_contract(contract):
        flat = ".".join(path)
        if path[0] in RESERVED_KEYS:
            result.setdefault("computed", OrderedDict())
            result["computed"].setdefault("_contract_" + path[0], ph)
            coverage.append({"field": flat, "status": "stashed_reserved"})
            continue
        cur = result
        exists = True
        for part in path[:-1]:
            if not isinstance(cur.get(part), dict):
                cur[part] = OrderedDict()
                exists = False
            cur = cur[part]
        if path[-1] not in cur:
            cur[path[-1]] = ph
            coverage.append({"field": flat, "status": "backfilled"})
        else:
            coverage.append({"field": flat, "status": "present"})
    return coverage


def missing_contract_fields(result, contract):
    missing = []
    for path, _ph in flatten_contract(contract):
        cur = result
        ok = True
        for part in path:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok:
            missing.append(".".join(path))
    return missing


# --------------------------------------------------------------------------- #
# 盘点（只读、确定性）
# --------------------------------------------------------------------------- #
def scan_tree(root):
    files = []
    dirs = []
    empty_dirs = []
    hardlink_groups = defaultdict(list)
    degradations = []
    root = os.path.abspath(root)

    def rel(p):
        rp = os.path.relpath(p, root)
        return "." if rp == "." else rp.replace(os.sep, "/")

    if not os.path.isdir(root):
        return {"root": root, "files": [], "dirs": [], "empty_dirs": [],
                "hardlink_groups": {}, "degradations": [
                    {"relpath": ".", "reason": "input_dir_missing",
                     "detail": root}]}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rdir = rel(dirpath)
        if rdir != ".":
            dirs.append({"relpath": rdir, "name": os.path.basename(dirpath)})
        if not dirnames and not filenames and rdir != ".":
            empty_dirs.append(rdir)
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rp = rel(fp)
            entry = {"relpath": rp, "name": fn,
                     "ext": os.path.splitext(fn)[1].lower(), "size": None,
                     "mtime": None, "mode": None, "type": "regular",
                     "symlink_target": None, "inode": None, "nlink": None,
                     "sha256": None, "broken": None, "error": None}
            try:
                st = os.lstat(fp)
            except OSError as e:
                entry["type"] = "unreadable"
                entry["error"] = "lstat_error: %s" % e
                degradations.append({"relpath": rp, "reason": "lstat_error",
                                     "detail": str(e)})
                files.append(entry)
                continue
            entry["mode"] = oct(st.st_mode & 0o7777)
            entry["mtime"] = int(st.st_mtime)
            entry["inode"] = st.st_ino
            entry["nlink"] = st.st_nlink
            if stat.S_ISLNK(st.st_mode):
                entry["type"] = "symlink"
                try:
                    target = os.readlink(fp)
                except OSError as e:
                    target = None
                    entry["error"] = "readlink_error: %s" % e
                entry["symlink_target"] = target
                if target is None:
                    entry["broken"] = True
                else:
                    full = target if os.path.isabs(target) else os.path.join(
                        dirpath, target)
                    entry["broken"] = not os.path.exists(full)
                if entry["broken"]:
                    degradations.append({"relpath": rp,
                                         "reason": "broken_symlink",
                                         "detail": str(target)})
                files.append(entry)
                continue
            if not stat.S_ISREG(st.st_mode):
                entry["type"] = "non_regular"
                entry["error"] = "non_regular(%s)" % stat.S_IFMT(st.st_mode)
                degradations.append({"relpath": rp, "reason": "non_regular",
                                     "detail": entry["error"]})
                files.append(entry)
                continue
            entry["size"] = st.st_size
            if st.st_size > MAX_CONTENT_BYTES:
                entry["error"] = "too_large"
                degradations.append({"relpath": rp, "reason": "too_large",
                                     "detail": "%d bytes > %d" % (
                                         st.st_size, MAX_CONTENT_BYTES)})
                files.append(entry)
                continue
            if st.st_nlink > 1:
                hardlink_groups[(st.st_dev, st.st_ino)].append(rp)
            try:
                with open(fp, "rb") as fh:
                    entry["sha256"] = hashlib.sha256(fh.read()).hexdigest()
            except OSError as e:
                entry["error"] = "unreadable: %s" % e
                degradations.append({"relpath": rp, "reason": "unreadable",
                                     "detail": str(e)})
            files.append(entry)

    counts = {
        "files": len(files),
        "dirs": len(dirs),
        "empty_dirs": len(empty_dirs),
        "symlinks": sum(1 for f in files if f["type"] == "symlink"),
        "broken_symlinks": sum(1 for f in files if f.get("broken")),
        "hardlink_groups": sum(1 for g in hardlink_groups.values()
                               if len(g) > 1),
        "degraded": len(degradations),
    }
    return {"root": root, "files": files, "dirs": dirs,
            "empty_dirs": empty_dirs,
            "hardlink_groups": {str(k): v for k, v in hardlink_groups.items()
                                if len(v) > 1},
            "degradations": degradations, "counts": counts}


def dedup_by_hash(files):
    groups = defaultdict(list)
    for f in files:
        if f.get("sha256"):
            groups[f["sha256"]].append(f["relpath"])
    out = []
    for h, members in sorted(groups.items()):
        if len(members) > 1:
            out.append({"sha256": h, "count": len(members),
                        "files": sorted(members)})
    return out


# --------------------------------------------------------------------------- #
# 版本候选（归一化主干 + 内容相似度，启发式）
# --------------------------------------------------------------------------- #
_VERSION_RE = re.compile(
    r"[_\s.\-]*(?:v(?:er|ersion)?[_\s.\-]*\d+(?:\.\d+)*"
    r"|rev(?:ision)?[_\s.\-]*\d*"
    r"|final|draft|copy|副本|修订|终稿|草稿|扫描|scan|old|new|bak|backup|orig"
    r"|original|最新|旧|dated[_\s.\-]*\d{4}[\d\-]*)[_\s.\-]*",
    re.IGNORECASE)


def normalize_stem(name):
    base = os.path.splitext(os.path.basename(name))[0]
    base = unicodedata.normalize("NFKC", base)
    base = base.lower()
    base = re.sub(r"[\s._\-]+", "_", base)
    prev = None
    while prev != base:
        prev = base
        base = _VERSION_RE.sub("_", base)
        base = re.sub(r"_+", "_", base).strip("_")
    return base


def version_hint(name):
    m = _VERSION_RE.search(name)
    return m.group(0).strip("_.- ") if m else None


def build_version_candidates(files, input_root):
    stems = defaultdict(list)
    for f in files:
        if f.get("error"):
            continue
        if f["type"] != "regular":
            continue
        if f["ext"] not in TEXT_EXT and f["ext"] != "":
            # 仍按主干分组，但仅文本类才做内容相似度
            pass
        stems[normalize_stem(f["name"])].append(f)
    out = []
    for stem, members in sorted(stems.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda x: x["relpath"])
        first_text = read_text_sample(
            os.path.join(input_root, members[0]["relpath"]))
        chain = []
        for f in members:
            hint = version_hint(f["name"])
            sim = None
            if f["ext"] in TEXT_EXT and first_text is not None:
                t = read_text_sample(os.path.join(input_root, f["relpath"]))
                if t is not None:
                    sim = round(difflib.SequenceMatcher(
                        None, first_text, t).ratio(), 4)
            chain.append({"relpath": f["relpath"],
                          "size": f.get("size"),
                          "sha256": (f.get("sha256") or "")[:12],
                          "version_hint": hint, "similarity_to_first": sim})
        out.append({"stem": stem, "heuristic": True, "members": chain})
    return out


# --------------------------------------------------------------------------- #
# 别名（符号链接含断链 + 硬链接组）
# --------------------------------------------------------------------------- #
def build_aliases(files, hardlink_groups):
    symlinks = [{"relpath": f["relpath"], "target": f["symlink_target"],
                 "broken": f.get("broken")}
                for f in files if f["type"] == "symlink"]
    hardlinks = [{"inode": key, "files": sorted(members)}
                 for key, members in sorted(hardlink_groups.items())
                 if len(members) > 1]
    return {"symlinks": symlinks, "hardlinks": hardlinks}


# --------------------------------------------------------------------------- #
# 库目录/清单解析
# --------------------------------------------------------------------------- #
def parse_catalogs(files, input_root):
    cats = []
    for f in files:
        if f["type"] != "regular" or f.get("error"):
            continue
        ext = f["ext"]
        fp = os.path.join(input_root, f["relpath"])
        if ext == ".json":
            obj = safe_json_load(fp)
            if isinstance(obj, dict) and "__json_error__" in obj:
                cats.append({"path": f["relpath"], "kind": "json",
                             "error": obj["__json_error__"], "entries": [],
                             "registered_paths": []})
                continue
            cats.append(_parse_json_catalog(f["relpath"], obj))
        elif ext == ".csv":
            cats.append(_parse_csv_catalog(fp, f["relpath"]))
        elif ext in (".md", ".markdown", ".rst", ".txt"):
            cats.append(_parse_md_catalog(fp, f["relpath"]))
        elif ext == ".bib":
            cats.append(_parse_bibtex(fp, f["relpath"]))
        elif ext == ".ris":
            cats.append(_parse_ris(fp, f["relpath"]))
        elif ext in (".db", ".sqlite", ".sqlite3"):
            cats.append(_parse_sqlite(fp, f["relpath"]))
    return cats


def _parse_json_catalog(relpath, obj):
    reg = []
    entries = []
    def walk(o):
        if isinstance(o, dict):
            for k in ("path", "file", "relative_path", "文件", "路径"):
                if k in o and isinstance(o[k], str):
                    reg.append(o[k])
            entries.append({k: o[k] for k in ("id", "title", "name", "path",
                                              "version") if k in o})
        elif isinstance(o, list):
            for it in o:
                walk(it)
    walk(obj)
    return {"path": relpath, "kind": "json", "error": None, "entries": entries,
            "registered_paths": sorted(set(reg))}


def _parse_csv_catalog(fp, relpath):
    reg = []
    header = None
    error = None
    nrows = 0
    try:
        with io.open(fp, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            for i, row in enumerate(reader):
                if not row:
                    continue
                if i == 0:
                    header = row
                    continue
                nrows += 1
                for col in ("path", "file", "relative_path", "文件", "路径"):
                    if header and col in header:
                        idx = header.index(col)
                        if idx < len(row) and row[idx].strip():
                            reg.append(row[idx].strip())
    except Exception as e:  # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, e)
    return {"path": relpath, "kind": "csv", "error": error, "header": header,
            "rows": nrows, "registered_paths": sorted(set(reg))}


def _parse_md_catalog(fp, relpath):
    headings = []
    reg = []
    error = None
    try:
        text = read_text_sample(fp) or ""
        for line in text.splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                headings.append({"level": len(m.group(1)),
                                 "title": m.group(2).strip()})
            for tok in re.findall(r"[\w.\-/\\]+\.(?:pdf|docx?|xlsx?|pptx?|"
                                  r"txt|md|csv|json|bib|ris|png|jpe?g|svg)",
                                  line):
                reg.append(tok)
    except Exception as e:  # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, e)
    return {"path": relpath, "kind": "markdown", "error": error,
            "headings": headings, "registered_paths": sorted(set(reg))}


def _parse_bibtex(fp, relpath):
    entries = []
    error = None
    try:
        text = read_text_sample(fp) or ""
        for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
            entries.append({"type": m.group(1), "key": m.group(2)})
    except Exception as e:  # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, e)
    return {"path": relpath, "kind": "bibtex", "error": error,
            "entries": entries, "registered_paths": []}


def _parse_ris(fp, relpath):
    records = []
    error = None
    try:
        text = read_text_sample(fp) or ""
        cur = {}
        for line in text.splitlines():
            if line.startswith("TY  -"):
                cur = {"type": line[5:].strip()}
            elif line.startswith("ER  -") and cur:
                records.append(cur)
                cur = {}
            elif cur and len(line) >= 6 and line[2:4] == "  ":
                tag = line[:2].strip()
                val = line[6:].strip()
                cur[tag] = (cur.get(tag) + "; " + val) if tag in cur else val
        if cur:
            records.append(cur)
    except Exception as e:  # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, e)
    return {"path": relpath, "kind": "ris", "error": error,
            "records": records, "registered_paths": []}


def _parse_sqlite(fp, relpath):
    tables = []
    error = None
    reg = []
    try:
        conn = sqlite3.connect(fp)
        cur = conn.cursor()
        for (name,) in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"):
            try:
                n = cur.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
                cols = [c[1] for c in cur.execute(
                    'PRAGMA table_info("%s")' % name).fetchall()]
                if any(c in cols for c in ("path", "file", "relative_path")):
                    pcol = next(c for c in ("path", "file", "relative_path")
                                if c in cols)
                    for (p,) in cur.execute(
                            'SELECT "%s" FROM "%s"' % (pcol, name)):
                        if p:
                            reg.append(str(p))
                tables.append({"name": name, "rows": n, "columns": cols})
            except Exception as e:  # noqa: BLE001
                tables.append({"name": name, "error": "%s: %s" % (
                    type(e).__name__, e)})
        conn.close()
    except Exception as e:  # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, e)
    return {"path": relpath, "kind": "sqlite", "error": error,
            "tables": tables, "registered_paths": sorted(set(reg))}


def build_reconciliation(inventory, catalogs):
    inv_paths = set(f["relpath"] for f in inventory["files"])
    registered = set()
    for c in catalogs:
        registered.update(c.get("registered_paths", []))
    matches = sorted(registered & inv_paths)
    missing = sorted(registered - inv_paths)
    orphaned = sorted(inv_paths - registered)
    return {
        "matches": matches, "mismatches": [], "missing": missing,
        "orphaned": orphaned,
        "counts": {"matches": len(matches), "mismatches": 0,
                   "missing": len(missing), "orphaned": len(orphaned)},
        "catalogs": [{"path": c["path"], "kind": c["kind"],
                      "error": c.get("error")} for c in catalogs],
    }


# --------------------------------------------------------------------------- #
# 索引
# --------------------------------------------------------------------------- #
def build_index(inventory, input_root):
    term_map = defaultdict(set)
    for f in inventory["files"]:
        if f["type"] != "regular":
            continue
        rp = f["relpath"]
        for tok in re.split(r"[^A-Za-z0-9_\u4e00-\u9fff]+", f["name"].lower()):
            if len(tok) >= 2:
                term_map[tok].add(rp)
        if f["ext"] in TEXT_EXT and not f.get("error"):
            text = read_text_sample(os.path.join(input_root, rp))
            if text:
                words = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{1,}",
                                   text.lower())
                for w in words:
                    if len(w) >= 2:
                        term_map[w].add(rp)
    index = {}
    for term in sorted(term_map):
        if len(index) >= MAX_INDEX_TERMS:
            break
        index[term] = sorted(term_map[term])
    meta = {"by_ext": defaultdict(list)}
    for f in inventory["files"]:
        if f["type"] == "regular":
            meta["by_ext"][f["ext"] or "(none)"].append(f["relpath"])
    meta["by_ext"] = {k: sorted(v) for k, v in sorted(meta["by_ext"].items())}
    return {"terms": index, "metadata": meta, "truncated":
            len(term_map) > MAX_INDEX_TERMS}


# --------------------------------------------------------------------------- #
# 机械问答
# --------------------------------------------------------------------------- #
def build_answers(inventory, dup, versions, aliases, recon, index):
    a = []
    a.append({"q": "文件/目录/空目录总数",
              "a": {"files": inventory["counts"]["files"],
                    "dirs": inventory["counts"]["dirs"],
                    "empty_dirs": inventory["counts"]["empty_dirs"]},
              "evidence": {"locator": "inventory",
                           "quote": "counts=%s" % inventory["counts"]}})
    a.append({"q": "重复组", "a": {"groups": len(dup), "detail": dup},
              "evidence": {"locator": "dedup",
                           "quote": "%d duplicate groups" % len(dup)}})
    a.append({"q": "版本候选链", "a": {"chains": len(versions),
                                       "detail": versions},
              "evidence": {"locator": "version_candidates",
                           "quote": "%d heuristic chains" % len(versions)}})
    a.append({"q": "别名（符号链接/硬链接）", "a": aliases,
              "evidence": {"locator": "aliases", "quote": json.dumps(
                  aliases, ensure_ascii=False)}})
    a.append({"q": "对账 matches/mismatches/missing/orphaned",
              "a": recon["counts"],
              "evidence": {"locator": "reconciliation",
                           "quote": json.dumps(recon["counts"],
                                               ensure_ascii=False)}})
    a.append({"q": "断链符号链接", "a": aliases["symlinks"],
              "evidence": {"locator": "aliases.symlinks",
                           "quote": json.dumps(aliases["symlinks"],
                                               ensure_ascii=False)}})
    a.append({"q": "未登记（orphaned）文件", "a": recon["orphaned"],
              "evidence": {"locator": "reconciliation.orphaned",
                           "quote": json.dumps(recon["orphaned"],
                                               ensure_ascii=False)}})
    return a


# --------------------------------------------------------------------------- #
# 目录树与清单文本
# --------------------------------------------------------------------------- #
def render_tree(inventory):
    lines = [inventory["root"] + "/"]
    for d in sorted(inventory["dirs"], key=lambda x: x["relpath"]):
        depth = d["relpath"].count("/")
        lines.append("  " * (depth + 1) + d["name"] + "/")
    for f in sorted(inventory["files"], key=lambda x: x["relpath"]):
        depth = f["relpath"].count("/")
        tag = ""
        if f["type"] == "symlink":
            tag = " -> " + (f["symlink_target"] or "?")
        elif f.get("error"):
            tag = "  [%s]" % f["error"]
        lines.append("  " * (depth + 1) + f["name"] + tag)
    for d in inventory["empty_dirs"]:
        lines.append("  [空目录] " + d)
    return "\n".join(lines) + "\n"


def render_manifest_csv(inventory, output_path):
    header = ["relpath", "type", "size", "mode", "mtime", "sha256",
              "symlink_target", "broken", "error"]
    with io.open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for f in sorted(inventory["files"], key=lambda x: x["relpath"]):
            w.writerow([f["relpath"], f["type"],
                        f.get("size") if f.get("size") is not None else "",
                        f.get("mode") or "", f.get("mtime") or "",
                        f.get("sha256") or "", f.get("symlink_target") or "",
                        "" if f.get("broken") is None else str(f["broken"]),
                        f.get("error") or ""])
    return output_path


def render_manifest_md(inventory, dup, versions, recon, output_path):
    lines = ["# 科研资料库交接报告", "",
             "- case: %s" % os.path.basename(os.path.dirname(
                 os.path.abspath(output_path))),
             "- 文件数: %d；目录数: %d（空目录 %d）" % (
                 inventory["counts"]["files"], inventory["counts"]["dirs"],
                 inventory["counts"]["empty_dirs"]),
             "- 重复组: %d；版本候选链: %d" % (len(dup), len(versions)),
             "- 对账: matches=%d missing=%d orphaned=%d" % (
                 recon["counts"]["matches"], recon["counts"]["missing"],
                 recon["counts"]["orphaned"]),
             "", "## 目录树", "", "```", render_tree(inventory).rstrip(),
             "```", ""]
    if dup:
        lines += ["## 重复组", ""]
        for g in dup:
            lines.append("- %s…（%d 份）: %s" % (
                g["sha256"][:12], g["count"], ", ".join(g["files"])))
    if versions:
        lines += ["", "## 版本候选链（启发式）", ""]
        for v in versions:
            lines.append("- %s: %s" % (v["stem"], "; ".join(
                "%s(v=%s,sim=%s)" % (m["relpath"], m["version_hint"],
                                     m["similarity_to_first"])
                for m in v["members"])))
    lines += ["", "## 完整性声明", "",
              "- 所有源文件只读处理，未移动/覆盖/删除。",
              "- 哈希按内容 SHA-256 计算；超大/不可读/非常规文件在 manifest.csv "
              "error 列标注。",
              "- 版本链为启发式候选，需人工复核后确认。"]
    with io.open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return output_path


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _recount(inv):
    files = inv["files"]
    inv["counts"] = {
        "files": len(files),
        "dirs": len(inv["dirs"]),
        "empty_dirs": len(inv["empty_dirs"]),
        "symlinks": sum(1 for f in files if f["type"] == "symlink"),
        "broken_symlinks": sum(1 for f in files if f.get("broken")),
        "hardlink_groups": sum(1 for g in inv["hardlink_groups"].values()
                               if len(g) > 1),
        "degraded": len(inv["degradations"]),
    }
    return inv


def _exclude_self_dirs(inv, input_root, dirs_to_exclude):
    """若 output/work 目录位于 input_root 之内，则从盘点结果剔除自身。"""
    prefixes = []
    for d in dirs_to_exclude:
        if not d:
            continue
        try:
            rp = os.path.relpath(os.path.abspath(d), os.path.abspath(input_root))
        except ValueError:
            continue
        if rp != "." and not rp.startswith("..") and not os.path.isabs(rp):
            prefixes.append(rp.replace(os.sep, "/"))
    if not prefixes:
        return inv

    def drop(relpath):
        return any(relpath == p or relpath.startswith(p + "/")
                   for p in prefixes)

    inv["files"] = [f for f in inv["files"] if not drop(f["relpath"])]
    inv["dirs"] = [d for d in inv["dirs"] if not drop(d["relpath"])]
    inv["empty_dirs"] = [d for d in inv["empty_dirs"] if not drop(d)]
    inv["hardlink_groups"] = {
        k: [p for p in v if not drop(p)]
        for k, v in inv["hardlink_groups"].items()}
    inv["hardlink_groups"] = {k: v for k, v in inv["hardlink_groups"].items()
                              if len(v) > 1}
    inv["degradations"] = [g for g in inv["degradations"]
                           if not drop(g.get("relpath", ""))]
    return _recount(inv)


def build_full(case_id, contract, input_root, output_dir, work_dir,
               change_log, t0, initial_errors=None):
    inv = scan_tree(input_root)
    inv = _exclude_self_dirs(inv, input_root, (output_dir, work_dir))
    material_files = [f for f in inv["files"]
                      if f["relpath"] not in ("case.json", "task.md")]
    if not case_id:
        status = "input_error"
    elif not material_files and not inv["dirs"]:
        status = "insufficient_evidence"
    else:
        status = "partial"
    dup = dedup_by_hash(inv["files"])
    versions = build_version_candidates(inv["files"], input_root)
    aliases = build_aliases(inv["files"], inv["hardlink_groups"])
    catalogs = parse_catalogs(inv["files"], input_root)
    recon = build_reconciliation(inv, catalogs)
    index = build_index(inv, input_root)
    answers = build_answers(inv, dup, versions, aliases, recon, index)

    artifacts = []
    if output_dir and os.path.isdir(output_dir) is False:
        os.makedirs(output_dir, exist_ok=True)
    manifest_csv = os.path.join(output_dir, "library_manifest.csv")
    render_manifest_csv(inv, manifest_csv)
    artifacts.append(manifest_csv)
    tree_txt = os.path.join(output_dir, "directory_tree.txt")
    with io.open(tree_txt, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_tree(inv))
    artifacts.append(tree_txt)
    dedup_json = os.path.join(output_dir, "dedup_version.json")
    write_json(dedup_json, {"duplicate_groups": dup,
                            "version_candidates": versions, "aliases": aliases,
                            "version_notes": [],
                            "heuristic": True})
    artifacts.append(dedup_json)
    recon_json = os.path.join(output_dir, "recovery_report.json")
    write_json(recon_json, {"reconciliation": recon, "catalogs": catalogs})
    artifacts.append(recon_json)
    index_json = os.path.join(output_dir, "library_index.json")
    write_json(index_json, {"index": index, "answers": answers})
    artifacts.append(index_json)
    manifest_md = os.path.join(output_dir, "library_manifest.md")
    render_manifest_md(inv, dup, versions, recon, manifest_md)
    artifacts.append(manifest_md)

    # 证据溯源
    sources = []
    for g in dup:
        sources.append({"item": "duplicate_group %s" % g["sha256"][:12],
                        "locator": g["files"][0],
                        "quote": "sha256=%s files=%s" % (
                            g["sha256"], g["files"])})
    for v in versions:
        sources.append({"item": "version_chain %s" % v["stem"],
                        "locator": v["members"][0]["relpath"],
                        "quote": "members=%s" % [m["relpath"]
                                                 for m in v["members"]]})
    for s in aliases["symlinks"]:
        sources.append({"item": "symlink %s" % s["relpath"],
                        "locator": s["relpath"],
                        "quote": "-> %s (broken=%s)" % (s["target"],
                                                        s["broken"])})
    sources.append({"item": "reconciliation",
                    "locator": "catalogs",
                    "quote": "matches=%d missing=%d orphaned=%d" % (
                        recon["counts"]["matches"], recon["counts"]["missing"],
                        recon["counts"]["orphaned"])})

    uncertainty = []
    if versions:
        uncertainty.append({"item": "version_candidates",
                            "reason": "按归一化主干+内容相似度构建，属启发式候选",
                            "severity": "medium"})
    if recon["counts"]["orphaned"]:
        uncertainty.append({"item": "orphaned",
                            "reason": "未在可解析清单中登记的文件，可能并非缺失，"
                                     "而是清单格式未被识别", "severity": "low"})

    errors = list(initial_errors or [])
    if not case_id:
        errors.append("case_id 缺失，无法唯一定位案例")
    limitations = [
        "内容语义检索由 Agent 在脚本候选之上补充；脚本只做确定性的哈希/主干/"
        "清单对账。",
        "扫描件（无文本层）与加密文件无法抽取文本，按类型/哈希处理并在 "
        "degradations 记录。",
    ]
    pending_human = []
    if versions:
        pending_human.append("复核版本候选链，确认主干分组与顺序后再作为证据使用")

    result = OrderedDict()
    result["case_id"] = case_id
    result["status"] = status
    coverage = merge_contract(result, contract)
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = inv["degradations"]
    result["limitations"] = limitations
    result["pending_human"] = pending_human
    result["runtime"] = {"skill_version": VERSION,
                         "elapsed_seconds": round(time.monotonic() - t0, 3),
                         "input_root": input_root, "output_dir": output_dir}
    result["computed"] = OrderedDict({
        "summary": inv["counts"],
        "files": inv["files"],
        "dirs": inv["dirs"],
        "empty_dirs": inv["empty_dirs"],
        "duplicate_groups": dup,
        "version_candidates": versions,
        "version_notes": [],
        "aliases": aliases,
        "reconciliation": recon,
        "answers": answers,
        "index": index,
        "catalogs": catalogs,
        "contract_coverage": coverage,
        "artifacts": artifacts,
    })
    result["artifacts"] = artifacts
    return result


def write_result_and_log(result, output_dir):
    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with io.open(log_path, "a", encoding="utf-8", newline="\n") as fh:
        for entry in result.get("change_log", []):
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return result_path, log_path


def run_skeleton(args, change_log, t0):
    input_root = args.input
    case_path = os.path.join(input_root, "case.json")
    task_path = args.task_path or os.path.join(input_root, "task.md")
    case = safe_json_load(case_path) if os.path.exists(case_path) else None
    task_text = ""
    if os.path.exists(task_path):
        task_text = read_text_sample(task_path) or ""
    if isinstance(case, dict) and "__json_error__" in case:
        case_id = ""
        contract = None
        errors = ["case.json 无法解析: %s" % case["__json_error__"]]
    else:
        case_id = extract_case_id(case, task_text)
        contract = extract_contract(case)
        errors = []
    status = "partial" if case_id else "input_error"
    result = OrderedDict()
    result["case_id"] = case_id
    result["status"] = status
    merge_contract(result, contract)
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = []
    result["limitations"] = ["骨架阶段：尚未执行完整盘点，仅有 case_id 与契约占位"]
    result["pending_human"] = []
    result["runtime"] = {"skill_version": VERSION,
                         "elapsed_seconds": round(time.monotonic() - t0, 3),
                         "stage": "skeleton"}
    result["computed"] = OrderedDict({"summary": {}, "artifacts": []})
    result["artifacts"] = []
    if output_dir_exists(args.output):
        pass
    result_path, log_path = write_result_and_log(result, args.output)
    return result_path


def output_dir_exists(output_dir):
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    return True


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def build_argparser():
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="科研资料库整理与版本感知检索（确定性、契约对齐）")
    p.add_argument("--input", dest="input", default="/input")
    p.add_argument("--output", dest="output", default="/app/output")
    p.add_argument("--work", dest="work", default="/app/work")
    p.add_argument("--case-json", dest="case_json", default=None)
    p.add_argument("--task-path", dest="task_path", default=None)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", dest="update", default=None,
                   help="JSON 补丁文件路径，增量合并到 result.json")
    p.add_argument("--result", dest="result", default=None,
                   help="--update/--check 时的 result.json 路径")
    p.add_argument("--check", dest="check", default=None,
                   help="校验 result.json")
    return p


def run_check(args):
    result_path = args.check or os.path.join(args.output, "result.json")
    if not os.path.exists(result_path):
        print("FAIL: result.json 不存在: %s" % result_path)
        return 1
    result = safe_json_load(result_path)
    if isinstance(result, dict) and "__json_error__" in result:
        print("FAIL: result.json 无法解析: %s" % result["__json_error__"])
        return 1
    problems = []
    if not result.get("case_id"):
        problems.append("case_id 为空")
    if result.get("status") not in ALLOWED_STATUS:
        problems.append("status 非法: %r" % result.get("status"))
    if not isinstance(result.get("change_log"), list) or not \
            result.get("change_log"):
        problems.append("change_log 缺失或为空")
    out_dir = os.path.dirname(os.path.abspath(result_path))
    required = ["library_manifest.csv", "directory_tree.txt",
                "dedup_version.json", "recovery_report.json",
                "library_index.json", "library_manifest.md",
                "operation_log.jsonl"]
    for name in required:
        if not os.path.exists(os.path.join(out_dir, name)):
            problems.append("产物缺失: %s" % name)
    for name in ("dedup_version.json", "recovery_report.json",
                 "library_index.json"):
        pth = os.path.join(out_dir, name)
        if os.path.exists(pth):
            obj = safe_json_load(pth)
            if isinstance(obj, dict) and "__json_error__" in obj:
                problems.append("%s 无法解析: %s" % (name,
                                                     obj["__json_error__"]))
    if args.case_json and os.path.exists(args.case_json):
        case = safe_json_load(args.case_json)
        contract = extract_contract(case) if not (
            isinstance(case, dict) and "__json_error__" in case) else None
        if contract is not None:
            missing = missing_contract_fields(result, contract)
            if missing:
                problems.append("契约字段缺失: %s" % ", ".join(missing[:20]))
    if problems:
        print("FAIL: " + " | ".join(problems))
        return 1
    print("OK: case_id=%s status=%s" % (result.get("case_id"),
                                        result.get("status")))
    return 0


def run_update(args):
    result_path = args.result or os.path.join(args.output, "result.json")
    if not os.path.exists(result_path) or not os.path.exists(args.update):
        print("FAIL: result 或 patch 不存在")
        return 1
    result = safe_json_load(result_path) or OrderedDict()
    patch = safe_json_load(args.update) or {}
    if isinstance(patch, dict) and "__json_error__" in patch:
        print("FAIL: patch 无法解析: %s" % patch["__json_error__"])
        return 1
    change_entry = None
    if isinstance(patch, dict) and "_change" in patch:
        ch = patch.pop("_change")
        change_entry = {"time": _utcnow(), "actor": ch.get("actor", "agent"),
                        "step": ch.get("step", "update"),
                        "detail": ch.get("detail", "")}
    if change_entry:
        result.setdefault("change_log", [])
        result["change_log"] = result["change_log"] + [change_entry]
    result = deep_merge(result, patch)
    write_json(result_path, result)
    log_path = os.path.join(os.path.dirname(os.path.abspath(result_path)),
                            "operation_log.jsonl")
    if change_entry:
        with io.open(log_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(change_entry, ensure_ascii=False) + "\n")
    print("merged patch into %s" % result_path)
    return 0


def main(argv=None):
    args = build_argparser().parse_args(argv)
    t0 = _mono()
    change_log = []
    _log(change_log, "start", "invoke=%s" % " ".join(sys.argv[1:])
         if len(sys.argv) > 1 else "")

    if args.check:
        return run_check(args)
    if args.update:
        return run_update(args)

    output_dir_exists(args.output)
    if args.work and not os.path.isdir(args.work):
        os.makedirs(args.work, exist_ok=True)

    if args.skeleton_only:
        _log(change_log, "skeleton", "写入 partial 骨架", "skill-core")
        run_skeleton(args, change_log, t0)
        return 0

    input_root = args.input
    case_path = args.case_json or os.path.join(input_root, "case.json")
    task_path = args.task_path or os.path.join(input_root, "task.md")
    case = safe_json_load(case_path) if os.path.exists(case_path) else None
    task_text = ""
    if os.path.exists(task_path):
        task_text = read_text_sample(task_path) or ""

    if isinstance(case, dict) and "__json_error__" in case:
        case_id = ""
        contract = None
        errors = ["case.json 无法解析: %s" % case["__json_error__"]]
    else:
        case_id = extract_case_id(case, task_text)
        contract = extract_contract(case)
        errors = []

    if not os.path.isdir(input_root):
        result = OrderedDict()
        result["case_id"] = case_id
        result["status"] = "insufficient_evidence"
        merge_contract(result, contract)
        result["sources"] = []
        result["uncertainty"] = [{"item": "input",
                                  "reason": "输入目录不存在: %s" % input_root,
                                  "severity": "high"}]
        _log(change_log, "finalize",
             "input 目录不存在，insufficient_evidence", "skill-core")
        result["change_log"] = change_log
        result["errors"] = errors + ["input 目录不存在: %s" % input_root]
        result["degradations"] = []
        result["limitations"] = ["无输入材料可盘点"]
        result["pending_human"] = []
        result["runtime"] = {"skill_version": VERSION,
                             "elapsed_seconds": round(time.monotonic() - t0, 3)}
        result["computed"] = OrderedDict({"summary": {}, "artifacts": []})
        result["artifacts"] = []
        write_result_and_log(result, args.output)
        return 0

    try:
        _log(change_log, "inventory", "盘点 /input 全目录", "skill-core")
        result = build_full(case_id, contract, input_root, args.output,
                            args.work, change_log, t0,
                            initial_errors=errors)
        _log(change_log, "finalize", "完整产物已落盘", "skill-core")
        result["change_log"] = change_log
        result["runtime"]["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        write_result_and_log(result, args.output)
        return 0
    except Exception as e:  # noqa: BLE001
        # 保留骨架，退出码 2，不崩溃
        _log(change_log, "error", "%s: %s" % (type(e).__name__, e),
             "skill-core")
        try:
            run_skeleton(args, change_log, t0)
            result_path = os.path.join(args.output, "result.json")
            result = safe_json_load(result_path)
            result.setdefault("errors", []).append(
                "full pipeline 失败: %s: %s" % (type(e).__name__, e))
            result["status"] = "partial"
            write_json(result_path, result)
        except Exception:  # noqa: BLE001
            pass
        print("pipeline error (保留骨架): %s: %s" % (type(e).__name__, e),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
