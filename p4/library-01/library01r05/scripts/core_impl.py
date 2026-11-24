"""core_impl.py - 契约驱动科研资料库修复引擎（仅标准库）。

设计取舍（与同题其他实现相区别）：
1. 以案例 output_contract 为唯一回填目标：先建"契约默认骨架"，再按字段名映射
   机械证据，产出 contract_coverage（total/filled/missing/null/collisions/field_map），
   使"result.json 逐字段对齐 output_contract"可被机器校验。
2. change_log 是一等公民：追加式、带 seq 与 contract_fields_updated，并与
   operation_log.jsonl 同步；--update 只允许 set/merge/append 三种幂等操作。
3. 确定性：os.walk 排序、json sort_keys、无随机数；仅时间戳/日志序号随时间变化，
   并提供 computed.reproducibility 摘要用于复跑核对。
"""
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as _stat
import sys

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "runtime", "limitations", "pending_human",
    "computed", "artifacts",
})
CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "output_schema", "result_contract", "outputContract", "resultSchema",
)
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_HASH_BYTES = 2 * 1024 * 1024  # 2 MiB，超大文件跳过内容哈希
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml",
    ".yml", ".xml", ".html", ".htm", ".bib", ".ris", ".tex", ".log", ".py",
    ".r", ".m", ".c", ".cpp", ".h", ".ipynb", ".rst", ".ini", ".cfg", ".toml",
}
CATALOG_EXTS = {".json", ".csv", ".tsv", ".bib", ".ris", ".sqlite", ".sqlite3", ".db"}
MAGIC = [
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"PK\x03\x04", "zip"),
    (b"SQLite format 3\x00", "sqlite"),
    (b"\x1f\x8b", "gzip"),
    (b"\x7fELF", "elf"),
    (b"ID3", "mp3"),
    (b"\x00\x00\x00\x18ftyp", "mp4"),
]

RE_VERSION_NOTE = re.compile(
    r"版本|version|替代|supersede|修订|rev(ision)?|更新说明|changelog",
    re.I,
)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def now_utc():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def iso_from_epoch(epoch):
    try:
        return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).replace(
            microsecond=0).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def relpath_posix(base, path):
    rel = os.path.relpath(path, base)
    return rel.replace(os.sep, "/") if os.sep != "/" else rel


def read_text_safe(path):
    """返回 (text, encoding, error)。依次尝试 utf-8-sig / utf-8 / latin-1。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", "bad_encoding"


def read_json_safe(path):
    text, _enc, _err = read_text_safe(path)
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, f"bad_json: {exc}"


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as exc:
        return None, f"hash_error: {exc}"


def sniff_format(path, sample):
    for magic, label in MAGIC:
        if sample.startswith(magic):
            return label
    return os.path.splitext(path)[1].lstrip(".").lower() or "unknown"


def normalize_key(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def normalize_stem(name):
    base, ext = os.path.splitext(name.lower())
    base = re.sub(r"[._\-\s]*(?:v|ver|version)[._\-]?\d+(?:\.\d+)*", "", base)
    base = re.sub(r"[._\-\s]*(?:final|draft|copy|copies|backup|bak|old|new|rev|revised|latest)\b", "", base)
    base = re.sub(r"[\[\(]\s*(?:copy|copies|\d+)\s*[\]\)]", "", base)
    base = re.sub(r"[_\-.\s]+", "_", base).strip("_")
    return (base or "unnamed"), (ext.lower() or "")


def first_line_or_binary(path, max_len=160):
    """返回文件首行原文（文本）或二进制魔数说明，用于 sources 的逐字 quote。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    fmt = sniff_format(path, head)
    if b"\x00" in head:
        return f"<binary:{fmt}>"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = head.decode(enc)
            line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            if line:
                return line[:max_len] + ("…" if len(line) > max_len else "")
            return f"<binary:{fmt}>"
        except UnicodeDecodeError:
            continue
    return "<undecodable>"


# --------------------------------------------------------------------------- #
# 契约引擎
# --------------------------------------------------------------------------- #
def find_contract(case_obj, task_text):
    """在 case.json 中识别 output_contract；失败则回退解析 task.md 表格。"""
    if isinstance(case_obj, dict):
        for key in CONTRACT_KEYS:
            if key in case_obj and case_obj[key] is not None:
                return case_obj[key]
        for sub in case_obj.values():
            if isinstance(sub, dict):
                for key in CONTRACT_KEYS:
                    if key in sub and sub[key] is not None:
                        return sub[key]
    return _contract_from_task_md(task_text)


def _contract_from_task_md(text):
    if not text:
        return None
    fields = []
    for m in re.finditer(r"`([A-Za-z_][A-Za-z0-9_.\-]*)`", text):
        name = m.group(1).strip()
        if name and name not in fields and not name.startswith("/"):
            fields.append(name)
    if not fields:
        return None
    return {f: None for f in fields}


def classify(value):
    """把契约节点分类为 container / object_leaf / array_leaf / scalar_leaf。"""
    if isinstance(value, dict):
        if "type" in value and isinstance(value.get("type"), str):
            t = value["type"].lower()
            if t == "object" and isinstance(value.get("properties"), dict):
                return "container", value["properties"]
            if t == "object":
                return "object_leaf", None
            if t == "array":
                return "array_leaf", None
            return "scalar_leaf", None
        if value:
            return "container", value
        return "object_leaf", None
    if isinstance(value, list):
        return "array_leaf", None
    return "scalar_leaf", None


def build_default(value):
    """按分类生成默认骨架（container 递归 / object→{} / array→[] / scalar→None）。"""
    kind, detail = classify(value)
    if kind == "container":
        return {k: build_default(v) for k, v in detail.items()}
    if kind == "object_leaf":
        return {}
    if kind == "array_leaf":
        return []
    return None


def contract_leaves(value, prefix=""):
    """返回叶子字段 [(path, type)]；type 为 object/array/scalar，container 仅递归。"""
    kind, detail = classify(value)
    out = []
    if kind == "container":
        for k, v in sorted(detail.items()):
            child = f"{prefix}.{k}" if prefix else k
            out.extend(contract_leaves(v, child))
    else:
        type_name = {"object_leaf": "object", "array_leaf": "array"}.get(kind, "scalar")
        out.append((prefix or "root", type_name))
    return out


def _is_filled(value, type_name):
    if type_name == "array":
        return isinstance(value, list) and len(value) > 0
    if type_name == "object":
        return isinstance(value, dict) and len(value) > 0
    return value is not None


HINT_RULES = (
    (re.compile(r"manifest|file.?list|inventory"), "manifest_records", "array"),
    (re.compile(r"summary|overview|statistic|stat\b"), "summary", "object"),
    (re.compile(r"duplicat"), "duplicate_groups", "array"),
    (re.compile(r"version"), "version_candidates", "array"),
    (re.compile(r"alias|symlink|hardlink|link"), "aliases", "object"),
    (re.compile(r"reconcil|recover|missing|orphan|mismatch"), "reconciliation", "object"),
    (re.compile(r"index|search"), "index_summary", "object"),
    (re.compile(r"answer|question|qa|quer|ask"), "answers", "array"),
    (re.compile(r"tree|director"), "tree_lines", "array"),
    (re.compile(r"log|timeline|change"), "change_log_snapshot", "array"),
    (re.compile(r"artifact|output"), "artifacts", "object"),
)

# 标量字段名 → summary 键（仅映射含义无歧义的计数/标识字段）
SCALAR_HINTS = {
    "totalfiles": "total_files",
    "filecount": "total_files",
    "files": "total_files",
    "totaldirs": "total_dirs",
    "totalbytes": "total_bytes",
    "duplicatecount": "duplicate_files",
    "versioncount": "version_candidates",
    "caseid": "case_id",
}


def _leaf_location(path):
    name = path.split(".")[0]
    if name in RESERVED_KEYS and name not in ("case_id", "status"):
        return f"computed._contract_{name}"
    return "top." + path


def _fill_leaf(path, type_name, computed_values):
    full_key = normalize_key(path)
    if type_name in ("scalar", "string", "number", "integer", "boolean", "unknown"):
        summary = computed_values.get("summary", {})
        for hint, summary_key in SCALAR_HINTS.items():
            if full_key == hint:
                val = summary.get(summary_key)
                if val is not None:
                    return val
        return None
    for rule, ckey, expected in HINT_RULES:
        if expected != type_name:
            continue
        if rule.search(path):
            val = computed_values.get(ckey)
            if val is not None:
                return val
    return None if type_name != "array" else []


def fill_contract(value, computed_values, prefix="", field_map=None):
    """按契约原值分类回填机械证据。返回 (filled_dict, field_map)。"""
    if field_map is None:
        field_map = []
    kind, detail = classify(value)
    if kind == "container":
        out = {}
        for k, v in sorted(detail.items()):
            path = f"{prefix}.{k}" if prefix else k
            out[k], _ = fill_contract(v, computed_values, path, field_map)
        return out, field_map
    path = prefix or "root"
    if kind == "object_leaf":
        out = _fill_leaf(path, "object", computed_values) or {}
        field_map.append({"path": path, "location": _leaf_location(path), "type": "object"})
        return out, field_map
    if kind == "array_leaf":
        out = _fill_leaf(path, "array", computed_values)
        field_map.append({"path": path, "location": _leaf_location(path), "type": "array"})
        return out, field_map
    out = _fill_leaf(path, "scalar", computed_values)
    field_map.append({"path": path, "location": _leaf_location(path), "type": "scalar"})
    return out, field_map


def _compute_coverage(value, filled):
    if value is None:
        return {"total": 0, "filled": 0, "missing": 0, "null": 0}
    leaves = contract_leaves(value)
    total = len(leaves)
    null_paths = []
    for path, _type in leaves:
        val = _get_path(filled, path)
        if not _is_filled(val, _type):
            null_paths.append(path)
    return {"total": total, "filled": total - len(null_paths),
            "missing": len(null_paths), "null": len(null_paths)}


def _get_path(obj, dotted):
    if dotted in (None, "", "root"):
        return obj
    cur = obj
    for part in dotted.split("."):
        if part == "":
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# --------------------------------------------------------------------------- #
# 输入盘点
# --------------------------------------------------------------------------- #
def discover(root):
    """只读遍历 root，返回 (entries, errors, degradations)。确定性排序。"""
    entries = []
    errors = []
    degradations = []
    if not os.path.isdir(root):
        errors.append(f"input_root_missing: {root}")
        return entries, errors, degradations

    root_abs = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames.sort()
        filenames.sort()
        for name in dirnames:
            full = os.path.join(dirpath, name)
            e, err, deg = _stat_entry(root_abs, full, name, is_dir=True)
            if err:
                errors.append(err)
            if deg:
                degradations.append(deg)
            entries.append(e)
        for name in filenames:
            full = os.path.join(dirpath, name)
            e, err, deg = _stat_entry(root_abs, full, name, is_dir=False)
            if err:
                errors.append(err)
            if deg:
                degradations.append(deg)
            entries.append(e)
    entries.sort(key=lambda x: x["rel_path"])
    return entries, errors, degradations


def _stat_entry(root_abs, full, name, is_dir=False):
    rel = relpath_posix(root_abs, full)
    base = {
        "rel_path": rel, "abs_path": full, "name": name, "kind": "dir" if is_dir else "file",
        "size_bytes": None, "mode_octal": None, "mtime_utc": None, "mtime_epoch": None,
        "nlink": None, "inode": None, "readable": None, "writable": None,
        "symlink_target": None, "hardlink_group": None, "sha256": None,
        "format": None, "encoding": None, "line_count": None, "degradation": None,
        "is_duplicate": False, "canonical_of": None,
    }
    err = deg = None
    try:
        st = os.lstat(full)
    except OSError as exc:
        base["kind"] = "other"
        base["degradation"] = f"lstat_error: {exc}"
        return base, f"lstat_error: {rel}", f"lstat_error: {rel}"
    base["mode_octal"] = oct(st.st_mode)
    base["mtime_epoch"] = st.st_mtime
    base["mtime_utc"] = iso_from_epoch(st.st_mtime)
    base["nlink"] = st.st_nlink
    base["inode"] = st.st_ino
    base["readable"] = os.access(full, os.R_OK)
    base["writable"] = os.access(full, os.W_OK)
    if not is_dir and _stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        base["hardlink_group"] = str(st.st_ino)
    if is_dir:
        return base, None, None
    if os.path.islink(full):
        base["kind"] = "symlink"
        try:
            base["symlink_target"] = os.readlink(full)
        except OSError as exc:
            base["symlink_target"] = f"<unreadable:{exc}>"
        if not os.path.exists(full):
            base["kind"] = "broken_symlink"
            base["degradation"] = "broken_symlink"
            deg = f"broken_symlink: {rel}"
        return base, None, deg
    if not _stat.S_ISREG(st.st_mode):
        base["kind"] = "non_regular"
        base["degradation"] = "non_regular"
        deg = f"non_regular: {rel}"
        return base, None, deg
    try:
        size = st.st_size
    except OSError:
        size = None
    base["size_bytes"] = size
    if size is None:
        base["degradation"] = "size_error"
        return base, None, f"size_error: {rel}"
    if size > MAX_HASH_BYTES:
        base["degradation"] = "too_large"
        deg = f"too_large: {rel} ({size} bytes)"
    else:
        try:
            with open(full, "rb") as fh:
                head = fh.read(512)
            base["format"] = sniff_format(full, head)
            digest, herr = sha256_file(full)
            if herr:
                base["degradation"] = herr
                err = f"hash_error: {rel}"
            else:
                base["sha256"] = digest
        except OSError as exc:
            base["degradation"] = f"unreadable: {exc}"
            err = f"unreadable: {rel}"
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXTS and size <= MAX_HASH_BYTES and base.get("sha256"):
        text, enc, eerr = read_text_safe(full)
        base["encoding"] = enc
        if eerr:
            base["degradation"] = base["degradation"] or eerr
            deg = deg or f"bad_encoding: {rel}"
        else:
            base["line_count"] = len(text.splitlines())
    return base, err, deg


# --------------------------------------------------------------------------- #
# 去重 / 版本 / 别名
# --------------------------------------------------------------------------- #
def build_duplicates(entries):
    by_hash = {}
    for e in entries:
        if e["kind"] == "file" and e.get("sha256"):
            by_hash.setdefault(e["sha256"], []).append(e)
    groups = []
    dup_count = 0
    for digest, members in sorted(by_hash.items()):
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda x: x["rel_path"])
        canonical = members[0]["rel_path"]
        for m in members:
            m["is_duplicate"] = m["rel_path"] != canonical
            m["canonical_of"] = canonical if m["is_duplicate"] else None
        dup_count += len(members) - 1
        groups.append({
            "sha256": digest,
            "count": len(members),
            "canonical": canonical,
            "paths": [m["rel_path"] for m in members],
            "total_bytes": members[0].get("size_bytes"),
        })
    return groups, dup_count


def build_versions(entries):
    stems = {}
    for e in entries:
        if e["kind"] != "file" or not e.get("sha256"):
            continue
        stem, ext = normalize_stem(e["name"])
        stems.setdefault((stem, ext), []).append(e)
    candidates = []
    for (stem, ext), members in sorted(stems.items()):
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda x: (x["mtime_epoch"] or 0, x["rel_path"]))
        chain = []
        for i, m in enumerate(members):
            chain.append({"path": m["rel_path"], "sha256": m["sha256"],
                          "mtime_utc": m["mtime_utc"], "size_bytes": m["size_bytes"],
                          "supersedes": members[i - 1]["rel_path"] if i > 0 else None})
        diffs = []
        for i in range(1, len(members)):
            prev, cur = members[i - 1], members[i]
            if prev.get("size_bytes") is not None and cur.get("size_bytes") is not None:
                diffs.append({
                    "from": prev["rel_path"], "to": cur["rel_path"],
                    "size_delta_bytes": cur["size_bytes"] - prev["size_bytes"],
                    "same_content": prev["sha256"] == cur["sha256"],
                })
        candidates.append({
            "stem": stem, "extension": ext or None, "count": len(members),
            "heuristic": True, "chain": chain, "diffs": diffs,
        })
    return candidates


def build_aliases(entries):
    symlinks = [e for e in entries if e["kind"] in ("symlink", "broken_symlink")]
    hard = {}
    for e in entries:
        if e["kind"] == "file" and e.get("inode") is not None and e.get("nlink", 0) > 1:
            hard.setdefault(e["inode"], []).append(e["rel_path"])
    hard_groups = []
    for inode, paths in sorted(hard.items()):
        if len(paths) > 1:
            hard_groups.append({"inode": inode, "count": len(paths), "paths": sorted(paths)})
    return {
        "symlinks": [{"path": e["rel_path"], "target": e["symlink_target"],
                       "broken": e["kind"] == "broken_symlink"} for e in symlinks],
        "hardlink_groups": hard_groups,
    }


# --------------------------------------------------------------------------- #
# 库目录解析
# --------------------------------------------------------------------------- #
def parse_catalog_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        obj, err = read_json_safe(path)
        if err:
            return None, "json", err
        return _records_from_json(obj), "json", None
    if ext in (".csv", ".tsv"):
        return _records_from_csv(path), ext.lstrip("."), None
    if ext == ".bib":
        return _records_from_bib(path), "bib", None
    if ext == ".ris":
        return _records_from_ris(path), "ris", None
    if ext in (".sqlite", ".sqlite3", ".db"):
        return _records_from_sqlite(path), "sqlite", None
    return [], None, None


def _records_from_json(obj):
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        for key in ("items", "records", "files", "entries", "data", "documents"):
            v = obj.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        return [obj]
    return []


def _records_from_csv(path):
    text, _enc, err = read_text_safe(path)
    if err:
        return []
    try:
        reader = csv.DictReader(io.StringIO(text))
        return [dict(r) for r in reader]
    except csv.Error:
        return []


def _records_from_bib(path):
    text, _enc, err = read_text_safe(path)
    if err:
        return []
    records = []
    cur = {}
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"@\w+\s*\{", s, re.I):
            if cur:
                records.append(cur)
            cur = {"entry_type": re.match(r"@(\w+)\s*\{", s, re.I).group(1).lower()}
            continue
        m = re.match(r"(\w+)\s*=\s*[{\"](.*)[}\"]\s*,?$", s)
        if m and cur is not None:
            cur[m.group(1).lower()] = m.group(2).strip()
    if cur:
        records.append(cur)
    return [r for r in records if r]


def _records_from_ris(path):
    text, _enc, err = read_text_safe(path)
    if err:
        return []
    records = []
    cur = {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                records.append(cur)
                cur = {}
            continue
        if line.startswith("TY"):
            cur = {"entry_type": line[2:].strip().lower()}
        elif len(line) >= 6 and line[2:4] == "  ":
            tag = line[0:2].strip()
            value = line[6:].strip()
            if tag:
                cur[tag.lower()] = value
    if cur:
        records.append(cur)
    return [r for r in records if r]


def _records_from_sqlite(path):
    records = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables[:20]:
            try:
                cur = conn.execute(f'SELECT * FROM "{table}" LIMIT 1000')
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                records.append(dict(zip(cols, row)))
    finally:
        conn.close()
    return records


def _extract_file_refs(record):
    refs = []
    for k, v in record.items():
        if v is None:
            continue
        if isinstance(v, str) and (os.sep in v or "/" in v or os.path.splitext(v)[1]):
            refs.append((k, v))
        elif k.lower() in ("path", "file", "filename", "file_path", "location", "source"):
            refs.append((k, str(v)))
    return refs


# --------------------------------------------------------------------------- #
# 恢复对账
# --------------------------------------------------------------------------- #
def build_reconciliation(entries, catalogs):
    inventory_paths = {e["rel_path"] for e in entries if e["kind"] == "file"}
    inventory_names = {os.path.basename(p) for p in inventory_paths}
    matches, mismatches, missing = [], [], []
    seen_refs = set()
    for cat in catalogs:
        for rec in cat["records"]:
            for field, value in _extract_file_refs(rec):
                base = os.path.basename(value)
                key = (cat["rel_path"], field, value)
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                target = next((e for e in entries if e["kind"] == "file" and
                               (e["rel_path"] == value or os.path.basename(e["rel_path"]) == base)), None)
                if not target:
                    missing.append({"catalog": cat["rel_path"], "field": field, "reference": value})
                elif isinstance(rec.get("sha256"), str) and rec["sha256"] != target["sha256"]:
                    mismatches.append({
                        "catalog": cat["rel_path"], "field": field, "reference": value,
                        "found": target["rel_path"], "catalog_sha256": rec["sha256"],
                        "actual_sha256": target["sha256"]})
                else:
                    matches.append({"catalog": cat["rel_path"], "field": field,
                                    "reference": value, "found": target["rel_path"]})
    referenced = {m["found"] for m in matches} | {m["found"] for m in mismatches}
    referenced_bases = {os.path.basename(p) for p in referenced}
    orphaned = sorted(e["rel_path"] for e in entries
                      if e["kind"] == "file" and e["rel_path"] not in referenced
                      and os.path.basename(e["rel_path"]) not in referenced_bases)
    return {"matches": matches, "mismatches": mismatches,
            "missing": missing, "orphaned": orphaned}


# --------------------------------------------------------------------------- #
# 索引与机械问答
# --------------------------------------------------------------------------- #
def build_index(entries, catalogs):
    idx = {"by_extension": {}, "by_token": {}, "metadata": []}
    for e in entries:
        if e["kind"] == "file":
            ext = os.path.splitext(e["name"])[1].lower() or "(noext)"
            idx["by_extension"][ext] = idx["by_extension"].get(ext, 0) + 1
            for token in normalize_stem(e["name"])[0].split("_"):
                if token:
                    idx["by_token"].setdefault(token, []).append(e["rel_path"])
    for cat in catalogs:
        for rec in cat["records"]:
            idx["metadata"].append({"catalog": cat["rel_path"], "record": rec})
    return idx


def answer_questions(case_obj, task_text, idx, dedup_groups, versions, reconcile, entries):
    qs = []
    if isinstance(case_obj, dict):
        for key in ("questions", "queries", "tasks", "question", "query"):
            v = case_obj.get(key)
            if isinstance(v, list):
                qs.extend([str(x) for x in v])
            elif isinstance(v, str):
                qs.append(v)
    if task_text:
        for sent in re.findall(r"[^。\n]*[?？][^。\n]*", task_text):
            if len(sent) < 200:
                qs.append(sent.strip())
    answers = []
    for q in dict.fromkeys(qs):
        answers.append(_answer_one(q, idx, dedup_groups, versions, reconcile, entries))
    return answers


def _answer_one(q, idx, dedup_groups, versions, reconcile, entries):
    ql = q.lower()
    files = [e for e in entries if e["kind"] == "file"]
    dirs = [e for e in entries if e["kind"] == "dir"]
    if re.search(r"how many|count|number of|多少|数量|几个", ql):
        if "file" in ql or "文件" in q:
            return {"question": q, "type": "count", "answer": {"files": len(files)},
                    "evidence": [f"{len(files)} regular files"]}
        if "dir" in ql or "目录" in q:
            return {"question": q, "type": "count", "answer": {"directories": len(dirs)},
                    "evidence": [f"{len(dirs)} directories"]}
    if re.search(r"duplicat|重复", ql):
        return {"question": q, "type": "duplicates", "answer": dedup_groups,
                "evidence": [f"{len(dedup_groups)} duplicate groups"]}
    if re.search(r"missing|缺失", ql):
        return {"question": q, "type": "missing", "answer": reconcile["missing"],
                "evidence": [f"{len(reconcile['missing'])} missing references"]}
    if re.search(r"broken|dangling|symlink|断链|链接", ql):
        return {"question": q, "type": "links", "answer": reconcile,
                "evidence": ["see reconciliation/aliases"]}
    if re.search(r"version|版本", ql):
        return {"question": q, "type": "versions", "answer": versions,
                "evidence": [f"{len(versions)} version candidates"]}
    if re.search(r"author|作者", ql):
        authors = []
        for meta in idx["metadata"]:
            rec = meta["record"]
            for k in ("author", "authors", "au", "creator"):
                if rec.get(k):
                    authors.append(str(rec[k]))
        return {"question": q, "type": "authors", "answer": sorted(set(authors)),
                "evidence": ["from catalogs"]}
    if re.search(r"year|年份|date", ql):
        years = []
        for meta in idx["metadata"]:
            rec = meta["record"]
            for k in ("year", "date", "py"):
                if rec.get(k):
                    years.append(str(rec[k]))
        return {"question": q, "type": "years", "answer": sorted(set(years)),
                "evidence": ["from catalogs"]}
    if re.search(r"largest|biggest|最大|超大", ql):
        big = sorted([e for e in files if e.get("size_bytes")], key=lambda e: -e["size_bytes"])[:5]
        return {"question": q, "type": "largest",
                "answer": [{"path": e["rel_path"], "size_bytes": e["size_bytes"]} for e in big],
                "evidence": ["top 5 by size"]}
    return {"question": q, "type": "unmatched", "answer": None, "evidence": ["pending_human"]}


# --------------------------------------------------------------------------- #
# 目录树
# --------------------------------------------------------------------------- #
def render_tree(entries):
    tree = {}
    for e in entries:
        parts = [p for p in e["rel_path"].split("/") if p]
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if e["kind"] == "dir":
            node.setdefault(parts[-1], {})
        else:
            node[parts[-1]] = None
    lines = []
    _render_node(tree, "", lines)
    return lines


def _render_node(node, prefix, lines):
    for name in sorted(node):
        child = node[name]
        if child == {}:
            lines.append(f"{prefix}{name}/ [空目录]")
        elif isinstance(child, dict):
            lines.append(f"{prefix}{name}/")
            _render_node(child, prefix + "    ", lines)
        else:
            lines.append(f"{prefix}{name}")


# --------------------------------------------------------------------------- #
# 组装 result
# --------------------------------------------------------------------------- #
def assemble_result(case_id, status, contract_filled, coverage, field_map,
                    sources, uncertainty, change_log, errors, degradations,
                    runtime, limitations, pending_human, computed, artifacts):
    result = {
        "case_id": case_id,
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty,
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "runtime": runtime,
        "limitations": limitations,
        "pending_human": pending_human,
        "computed": computed,
        "artifacts": artifacts,
    }
    satisfied_by_reserved = []
    if isinstance(contract_filled, dict):
        for k, v in contract_filled.items():
            if k in RESERVED_KEYS:
                if k in ("case_id", "status"):
                    satisfied_by_reserved.append(k)
                    continue
                computed[f"_contract_{k}"] = v
                continue
            result[k] = v
    coverage["satisfied_by_reserved"] = satisfied_by_reserved
    computed["contract_coverage"] = {
        "total": coverage["total"], "filled": coverage["filled"],
        "missing": coverage["missing"], "null": coverage["null"],
        "collisions": [], "satisfied_by_reserved": satisfied_by_reserved,
        "field_map": field_map,
    }
    return result


# --------------------------------------------------------------------------- #
# 运行时上下文加载
# --------------------------------------------------------------------------- #
def load_context(case_json_path, task_path):
    case_obj = None
    case_err = None
    if os.path.isfile(case_json_path):
        obj, err = read_json_safe(case_json_path)
        if err:
            case_err = err
        else:
            case_obj = obj
    task_text = None
    if os.path.isfile(task_path):
        task_text, _enc, _err = read_text_safe(task_path)
    case_id = None
    if isinstance(case_obj, dict):
        for key in ("case_id", "challenge_id", "id", "challengeId", "caseId"):
            if case_obj.get(key):
                case_id = str(case_obj[key])
                break
    if not case_id and task_text:
        m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9\-_]+)`?", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = "unknown"
    contract = find_contract(case_obj, task_text)
    return case_obj, task_text, case_id, contract, case_err


def _extract_version_notes(task_text):
    if not task_text:
        return []
    return [ln.strip() for ln in task_text.splitlines()
            if RE_VERSION_NOTE.search(ln) and len(ln.strip()) <= 240]


def build_computed(entries, dedup_groups, dup_count, versions, aliases, catalogs,
                   reconcile, idx, answers, task_text, material_files):
    files = material_files if material_files is not None else [
        e for e in entries if e["kind"] == "file"]
    total_bytes = sum(e.get("size_bytes") or 0 for e in files)
    summary = {
        "total_files": len(files),
        "total_dirs": len([e for e in entries if e["kind"] == "dir"]),
        "total_bytes": total_bytes,
        "symlinks": len(aliases["symlinks"]),
        "broken_symlinks": len([s for s in aliases["symlinks"] if s["broken"]]),
        "hardlink_groups": len(aliases["hardlink_groups"]),
        "duplicate_groups": len(dedup_groups),
        "duplicate_files": dup_count,
        "version_candidates": len(versions),
        "catalogs": len(catalogs),
        "reconcile_matches": len(reconcile["matches"]),
        "reconcile_mismatches": len(reconcile["mismatches"]),
        "reconcile_missing": len(reconcile["missing"]),
        "reconcile_orphaned": len(reconcile["orphaned"]),
        "by_extension": idx["by_extension"],
    }
    manifest_records = [{
        "rel_path": e["rel_path"], "kind": e["kind"], "size_bytes": e["size_bytes"],
        "mode_octal": e["mode_octal"], "mtime_utc": e["mtime_utc"], "sha256": e["sha256"],
        "format": e["format"], "encoding": e["encoding"], "nlink": e["nlink"],
        "inode": e["inode"], "symlink_target": e["symlink_target"],
        "hardlink_group": e["hardlink_group"], "is_duplicate": e["is_duplicate"],
        "canonical_of": e["canonical_of"], "degradation": e["degradation"],
    } for e in entries]
    index_summary = {
        "by_extension": idx["by_extension"],
        "token_count": len(idx["by_token"]),
        "metadata_records": len(idx["metadata"]),
    }
    return {
        "summary": summary,
        "inventory": entries,
        "duplicate_groups": dedup_groups,
        "version_candidates": versions,
        "version_notes": _extract_version_notes(task_text),
        "aliases": aliases,
        "catalogs": catalogs,
        "reconciliation": reconcile,
        "answers": answers,
        "index": index_summary,
        "artifacts": {},
        "manifest_records": manifest_records,
        "tree_lines": [],
        "change_log_snapshot": [],
    }


# --------------------------------------------------------------------------- #
# 写产物
# --------------------------------------------------------------------------- #
def write_artifacts(output_dir, entries, dedup_groups, versions, aliases,
                    reconcile, idx, version_notes, result_summary, operation_log):
    os.makedirs(output_dir, exist_ok=True)
    artifacts = {}
    manifest_path = os.path.join(output_dir, "library_manifest.csv")
    cols = ["rel_path", "kind", "size_bytes", "mode_octal", "mtime_utc", "sha256",
            "format", "encoding", "nlink", "inode", "symlink_target", "hardlink_group",
            "is_duplicate", "canonical_of", "degradation"]
    with open(manifest_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in entries:
            w.writerow(e)
    artifacts["library_manifest.csv"] = "library_manifest.csv"

    tree_lines = render_tree(entries)
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    with open(tree_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(tree_lines) + "\n")
    artifacts["directory_tree.txt"] = "directory_tree.txt"

    dvd = {"duplicate_groups": dedup_groups, "version_candidates": versions,
           "version_notes": version_notes, "aliases": aliases}
    dvd_path = os.path.join(output_dir, "dedup_version.json")
    write_json_atomic(dvd_path, dvd)
    artifacts["dedup_version.json"] = "dedup_version.json"

    rec_path = os.path.join(output_dir, "recovery_report.json")
    write_json_atomic(rec_path, reconcile)
    artifacts["recovery_report.json"] = "recovery_report.json"

    idx_path = os.path.join(output_dir, "library_index.json")
    write_json_atomic(idx_path, idx)
    artifacts["library_index.json"] = "library_index.json"

    md = io.StringIO()
    md.write("# 资料库交接报告\n\n")
    md.write(f"- case_id: {result_summary.get('case_id')}\n")
    md.write(f"- status: {result_summary.get('status')}\n")
    for k, v in result_summary.get("summary", {}).items():
        md.write(f"- {k}: {v}\n")
    md.write("\n## 目录树\n\n```text\n" + "\n".join(tree_lines) + "\n```\n")
    md_path = os.path.join(output_dir, "library_manifest.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md.getvalue())
    artifacts["library_manifest.md"] = "library_manifest.md"

    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with open(log_path, "w", encoding="utf-8") as fh:
        for rec in operation_log:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    artifacts["operation_log.jsonl"] = "operation_log.jsonl"
    return artifacts, tree_lines


# --------------------------------------------------------------------------- #
# 入口：骨架 / 全量 / 更新 / 校验
# --------------------------------------------------------------------------- #
def _init_log(step, detail):
    return [{"seq": 1, "time_utc": now_utc(), "actor": "skill",
             "step": step, "detail": detail, "contract_fields_updated": []}]


def _append_log(log, actor, step, detail, fields):
    log.append({"seq": len(log) + 1, "time_utc": now_utc(), "actor": actor,
                "step": step, "detail": detail, "contract_fields_updated": fields})


def run_skeleton(case_json_path, task_path, input_root, output_dir, work_dir):
    case_obj, task_text, case_id, contract, case_err = load_context(case_json_path, task_path)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    status = "partial"
    if not os.path.isfile(case_json_path):
        status = "input_error"
    elif case_err and not case_obj:
        status = "input_error"
    if contract is not None:
        filled, field_map = fill_contract(contract, {})
        coverage = _compute_coverage(contract, filled)
    else:
        filled = None
        field_map = []
        coverage = {"total": 0, "filled": 0, "missing": 0, "null": 0}
    uncertainty = []
    if not contract:
        uncertainty.append({"item": "output_contract",
                            "reason": "output_contract 缺失，已回退 task.md 字段名或仅元数据",
                            "severity": "medium"})
    change_log = _init_log("skeleton", "partial 骨架落盘")
    result = assemble_result(
        case_id, status, filled, coverage, field_map, [], uncertainty,
        change_log, [case_err] if case_err else [], [], {"started_utc": now_utc()},
        ["skeleton-only 骨架，尚未盘点输入"], [], {"skeleton": True}, {})
    result_path = os.path.join(output_dir, "result.json")
    try:
        write_json_atomic(result_path, result)
    except OSError:
        return 2
    return 0


def run(case_json_path, task_path, input_root, output_dir, work_dir):
    case_obj, task_text, case_id, contract, case_err = load_context(case_json_path, task_path)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    errors = []
    degradations = []
    if case_err:
        errors.append(case_err)
    if not os.path.isfile(case_json_path):
        errors.append(f"case.json_missing: {case_json_path}")

    entries, derr, ddeg = discover(input_root)
    errors.extend(derr)
    degradations.extend(ddeg)

    contract_basenames = {os.path.basename(case_json_path), os.path.basename(task_path)}
    material_files = [e for e in entries if e["kind"] == "file"
                      and os.path.basename(e["rel_path"]) not in contract_basenames]
    has_files = bool(material_files)
    if not os.path.isfile(case_json_path):
        status = "input_error"
    elif case_err and case_obj is None:
        status = "input_error"
    elif not has_files:
        status = "insufficient_evidence"
    else:
        status = "ok"

    dedup_groups, dup_count = build_duplicates(entries)
    versions = build_versions(entries)
    aliases = build_aliases(entries)

    catalogs = []
    for e in entries:
        if e["kind"] == "file" and os.path.splitext(e["name"])[1].lower() in CATALOG_EXTS:
            records, fmt, perr = parse_catalog_file(e["abs_path"])
            catalogs.append({"rel_path": e["rel_path"], "format": fmt,
                             "records": records or [], "parse_error": perr})
            if perr:
                degradations.append(f"catalog_parse_error: {e['rel_path']}: {perr}")

    reconcile = build_reconciliation(entries, catalogs)
    idx = build_index(entries, catalogs)
    answers = answer_questions(case_obj, task_text, idx, dedup_groups, versions, reconcile, entries)

    version_notes = _extract_version_notes(task_text)
    computed = build_computed(entries, dedup_groups, dup_count, versions, aliases,
                              catalogs, reconcile, idx, answers, task_text,
                              material_files)

    uncertainty = []
    if not contract:
        uncertainty.append({"item": "output_contract",
                            "reason": "output_contract 缺失，仅产出元数据字段", "severity": "medium"})
    if dup_count:
        uncertainty.append({"item": "dedup",
                            "reason": "重复组按内容 SHA-256 判定，需人工确认是否删冗余", "severity": "low"})
    if versions:
        uncertainty.append({"item": "versions",
                            "reason": "版本链为启发式候选（归一化主干+mtime），需人工确认替代关系",
                            "severity": "medium"})
    if degradations:
        status = "partial" if status == "ok" else status
        uncertainty.append({"item": "degradations",
                            "reason": f"{len(degradations)} 个文件被降级处理，详见 degradations",
                            "severity": "low"})

    sources = _seed_sources(entries, dedup_groups, versions, reconcile, catalogs)
    change_log = _init_log("run", "全量盘点与契约回填")
    operation_log = _init_log("run", "全量运行")

    runtime = {"started_utc": now_utc(), "finished_utc": now_utc()}
    limitations = []
    if any(e.get("degradation") == "too_large" for e in entries):
        limitations.append("超大文件（>2MiB）未计算内容哈希，仅记录大小与权限")
    if versions:
        limitations.append("版本链为启发式候选，未做内容级语义比对")

    result_summary = {"case_id": case_id, "status": status,
                      "summary": computed.get("summary", {})}
    artifacts, tree_lines = write_artifacts(
        output_dir, entries, dedup_groups, versions, aliases, reconcile, idx,
        version_notes, result_summary, operation_log)
    computed["artifacts"] = artifacts
    computed["tree_lines"] = tree_lines
    computed["change_log_snapshot"] = list(change_log)

    # 契约回填（在 computed 完整后再做一次，确保 tree_lines/artifacts 已可映射）
    if contract is not None:
        filled, field_map = fill_contract(contract, computed)
        coverage = _compute_coverage(contract, filled)
    else:
        filled = None
        field_map = []
        coverage = {"total": 0, "filled": 0, "missing": 0, "null": 0}

    result = assemble_result(
        case_id, status, filled, coverage, field_map, sources, uncertainty,
        change_log, errors, degradations, runtime, limitations, [], computed, artifacts)

    result["computed"]["reproducibility"] = _reproducibility(result)

    result_path = os.path.join(output_dir, "result.json")
    try:
        write_json_atomic(result_path, result)
    except OSError:
        return 2

    _append_log(operation_log, "skill", "run", "result.json 落盘", [])
    with open(os.path.join(output_dir, "operation_log.jsonl"), "w", encoding="utf-8") as fh:
        for rec in operation_log:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str) + "\n")

    print(f"run: {computed['summary']['total_files']} files, "
          f"{len(dedup_groups)} dup groups, {len(versions)} version candidates, "
          f"{len([s for s in aliases['symlinks'] if s['broken']])} broken symlink, "
          f"{len(reconcile['mismatches'])} reconcile mismatch, "
          f"contract {coverage['filled']}/{coverage['total']} filled -> {result_path}")
    return 0


def _seed_sources(entries, dedup_groups, versions, reconcile, catalogs):
    sources = []
    for g in dedup_groups[:20]:
        p = g["paths"][0]
        e = next((x for x in entries if x["rel_path"] == p), None)
        quote = first_line_or_binary(e["abs_path"]) if e else None
        sources.append({"item": "duplicate_group", "locator": p,
                        "quote": quote or f"sha256:{g['sha256'][:12]}…", "kind": "computed"})
    for v in versions[:20]:
        p = v["chain"][0]["path"]
        e = next((x for x in entries if x["rel_path"] == p), None)
        quote = first_line_or_binary(e["abs_path"]) if e else None
        sources.append({"item": "version_candidate", "locator": p,
                        "quote": quote or f"stem:{v['stem']}", "kind": "computed"})
    for cat in catalogs:
        if cat["records"]:
            sources.append({"item": "catalog_record", "locator": cat["rel_path"],
                            "quote": json.dumps(cat["records"][0], ensure_ascii=False, default=str)[:160],
                            "kind": "verbatim"})
    return sources


def _reproducibility(result):
    subset = {
        "case_id": result.get("case_id"),
        "summary": result.get("computed", {}).get("summary"),
        "duplicate_groups": result.get("computed", {}).get("duplicate_groups"),
        "version_candidates": result.get("computed", {}).get("version_candidates"),
        "aliases": result.get("computed", {}).get("aliases"),
        "reconciliation": result.get("computed", {}).get("reconciliation"),
        "inventory_sha256": [
            {"rel_path": e["rel_path"], "sha256": e["sha256"], "size_bytes": e["size_bytes"]}
            for e in result.get("computed", {}).get("inventory", [])],
    }
    blob = json.dumps(subset, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# --update / --check
# --------------------------------------------------------------------------- #
def apply_update(patch_path, result_path, case_json_path=None):
    if not os.path.isfile(result_path):
        print(f"error: result not found: {result_path}", file=sys.stderr)
        return 2
    obj, err = read_json_safe(result_path)
    if err:
        print(f"error: bad result: {err}", file=sys.stderr)
        return 1
    patch, perr = read_json_safe(patch_path)
    if perr:
        print(f"error: bad patch: {perr}", file=sys.stderr)
        return 1
    ops = patch if isinstance(patch, list) else [patch]
    applied = 0
    errors = obj.setdefault("errors", [])
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        path = op.get("path")
        value = op.get("value")
        if kind == "set":
            _set_path(obj, path, value)
            applied += 1
        elif kind == "merge":
            target = _get_path(obj, path) if path else obj
            if isinstance(target, dict) and isinstance(value, dict):
                target.update(value)
                applied += 1
            else:
                errors.append(f"merge_target_not_object: {path}")
        elif kind == "append":
            target = _get_path(obj, path) if path else obj
            if isinstance(target, list):
                target.append(value)
                applied += 1
            else:
                errors.append(f"append_target_not_list: {path}")
        else:
            errors.append(f"unknown_op: {kind}")
    log = obj.setdefault("change_log", [])
    log.append({"seq": len(log) + 1, "time_utc": now_utc(), "actor": "agent",
                "step": "update", "detail": f"{applied} ops applied",
                "contract_fields_updated": [o.get("path") for o in ops if isinstance(o, dict)]})
    write_json_atomic(result_path, obj)
    print(f"update: {applied} ops applied -> {result_path}")
    return 0


def _set_path(obj, path, value):
    if not path:
        return
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if part == "":
            continue
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def run_check(result_path, case_json_path=None):
    obj, err = read_json_safe(result_path)
    if err:
        print(f"check: FAIL bad result json: {err}")
        return 1
    ok = True
    if not obj.get("case_id"):
        print("check: FAIL case_id empty")
        ok = False
    if obj.get("status") not in STATUS_VALUES:
        print(f"check: FAIL status invalid: {obj.get('status')}")
        ok = False
    for key in ("sources", "uncertainty", "change_log"):
        if not isinstance(obj.get(key), list):
            print(f"check: FAIL {key} missing/not list")
            ok = False
    coverage = obj.get("computed", {}).get("contract_coverage", {})
    total = coverage.get("total", 0)
    filled = coverage.get("filled", 0)
    if total and filled != total:
        print(f"check: FAIL contract coverage {filled}/{total}")
        ok = False
    else:
        print(f"check: contract coverage {filled}/{total}")
    print(f"check: {'PASS' if ok else 'FAIL'} {result_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    print("core_impl.py is a library; use main.py", file=sys.stderr)
    sys.exit(2)
