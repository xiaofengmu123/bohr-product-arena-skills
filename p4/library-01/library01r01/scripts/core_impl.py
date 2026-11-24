"""core_impl.py - 科研资料库修复流水线（证据溯源强化版）。

设计目标：
1. 确定性：遍历排序、哈希稳定、无随机数；同一输入重复运行，除时间戳外结果一致。
2. 先落盘、再增量：`skeleton` 立即写出合法的 partial 结果；`run` 完成全量流水线；
   `update` 把 Agent 的语义补丁增量合并；`check` 做提交前校验。
3. 证据溯源：每条结论（去重组、版本候选、别名、对账、目录解析、检索回答）都带
   locator + quote + quote_sha256 + evidence_type；逐字证据标 `verbatim`，测量值标
   `computed_derivation`，正则命中标 `derived_regex`，结构化字段标 `parsed_field`。

只依赖 Python 标准库。
"""

from __future__ import annotations

import argparse
import csv
import datetime
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
import traceback

# ---------------------------------------------------------------------------
# 常量与默认路径
# ---------------------------------------------------------------------------
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_HASH_BYTES = 2 * 1024 * 1024          # 超过 2MB 跳过内容哈希
MAX_TEXT_READ_BYTES = 2 * 1024 * 1024     # 文本全量读取上限
SAMPLE_CHARS = 800                        # 文本预览长度
MAX_CATALOG_RECORDS = 200                 # 目录解析记录上限
MAX_VERSION_NOTES = 60                    # 版本说明提取上限
MAX_KEYWORDS_PER_FILE = 200               # 每文件关键词索引上限
MAX_CLAIMS = 1200                         # evidence_map 条目上限

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "evidence_map", "computed", "errors", "degradations",
    "runtime", "limitations", "artifacts",
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "output_schema", "response_schema",
)

VERSION_TOKEN_PATTERNS = [
    re.compile(r"[-_.\s]?v?\d+(?:\.\d+)+(?:[-_.]?(?:alpha|beta|rc)\d*)?", re.I),
    re.compile(r"[-_.\s]?v?\d+(?![A-Za-z])", re.I),
    re.compile(r"\b(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\b"),
    re.compile(r"\b(?:19|20)\d{2}[-_.](?:0[1-9]|1[0-2])[-_.](?:0[1-9]|[12]\d|3[01])\b"),
    re.compile(r"\(\d+\)$"),
]

QUALITATIVE_VERSION_RE = re.compile(
    r"[-_.\s]?\b(?:draft|final|old|new|rev|revision|copy|backup|bak|"
    r"archived|dated|outdated|superseded)\b", re.I)

NOTE_NAME_RE = re.compile(
    r"(?:version|版本|note|说明|changelog|readme|变更|更新|修订|change|history|日志)",
    re.I,
)

NOTE_LINE_RE = re.compile(
    r"(?:版本|version|v\d+(?:\.\d+)*|替换|覆盖|修订|修订版|旧版|新版|废弃|作废|"
    r"替代|取代|supersede|deprecat|revis|update|change)",
    re.I,
)

PATH_FIELD_NAMES = {"path", "file", "filename", "file_path", "filepath",
                    "relative_path", "location", "locator", "source"}
HASH_FIELD_NAMES = {"hash", "sha256", "sha256sum", "checksum", "md5", "md5sum"}

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8", errors="replace"))


def _read_text_file(path: str):
    """读取文本，返回 (text, encoding, error)。容错编码。"""
    try:
        raw = open(path, "rb").read(MAX_TEXT_READ_BYTES + 1)
    except OSError as exc:
        return None, None, f"read_error: {exc}"
    if len(raw) > MAX_TEXT_READ_BYTES:
        raw = raw[:MAX_TEXT_READ_BYTES]
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    # 最终兜底：逐字节替换，绝不崩溃
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)", "bad_encoding"


def _is_text(data: bytes) -> bool:
    head = data[:4096]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _hash_file(path: str, size: int):
    """返回 (sha256_hex, skip_reason)。"""
    if size > MAX_HASH_BYTES:
        return None, "too_large"
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as exc:
        return None, f"unreadable: {exc}"


def _mode_str(mode: int) -> str:
    return "0o%o" % (mode & 0o777)


def _relpath(path: str, root: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = path
    return rel.replace("\\", "/")


def _stat_entry(entry_path: str, root: str):
    """对单个目录项做只读盘点。"""
    rel = _relpath(entry_path, root)
    name = os.path.basename(entry_path)
    entry = {
        "path": rel,
        "abs_path": os.path.abspath(entry_path),
        "name": name,
        "type": "other",
        "size": None,
        "mode": None,
        "mtime_iso": None,
        "inode": None,
        "nlink": None,
        "dev": None,
        "link_target": None,
        "sha256": None,
        "hash_skipped_reason": None,
        "text_detected": None,
        "encoding": None,
        "line_count": None,
        "sample": None,
        "read_error": None,
    }

    # 符号链接（含断链）优先判断
    if os.path.islink(entry_path):
        try:
            target = os.readlink(entry_path)
            entry["link_target"] = target
        except OSError as exc:
            entry["read_error"] = f"readlink_error: {exc}"
        entry["type"] = "symlink" if os.path.exists(entry_path) else "broken_symlink"
        try:
            st = os.lstat(entry_path)
            entry["mode"] = _mode_str(st.st_mode)
            entry["mtime_iso"] = _mtime_iso(st)
            entry["inode"] = st.st_ino
            entry["nlink"] = st.st_nlink
            entry["dev"] = st.st_dev
        except OSError as exc:
            entry["read_error"] = (entry["read_error"] or "") + f" lstat_error: {exc}"
        return entry

    if os.path.isdir(entry_path):
        entry["type"] = "dir"
        try:
            st = os.lstat(entry_path)
            entry["mode"] = _mode_str(st.st_mode)
            entry["mtime_iso"] = _mtime_iso(st)
        except OSError as exc:
            entry["read_error"] = f"stat_error: {exc}"
        return entry

    if not os.path.isfile(entry_path):
        entry["type"] = "non_regular"
        entry["read_error"] = "non_regular_file"
        return entry

    entry["type"] = "file"
    try:
        st = os.stat(entry_path)
        entry["size"] = st.st_size
        entry["mode"] = _mode_str(st.st_mode)
        entry["mtime_iso"] = _mtime_iso(st)
        entry["inode"] = st.st_ino
        entry["nlink"] = st.st_nlink
        entry["dev"] = st.st_dev
    except OSError as exc:
        entry["read_error"] = f"stat_error: {exc}"
        return entry

    entry["sha256"], skip_reason = _hash_file(entry_path, entry["size"])
    entry["hash_skipped_reason"] = skip_reason

    text, enc, read_err = _read_text_file(entry_path)
    entry["read_error"] = read_err or (entry.get("read_error") or None)
    if text is not None:
        entry["text_detected"] = True
        entry["encoding"] = enc
        entry["line_count"] = text.count("\n") + (1 if text else 0)
        entry["sample"] = text[:SAMPLE_CHARS]
    else:
        # 未能按文本读取，改用二进制探测
        try:
            head = open(entry_path, "rb").read(4096)
            entry["text_detected"] = _is_text(head)
        except OSError as exc:
            entry["read_error"] = (entry.get("read_error") or "") + f" probe_error: {exc}"
    return entry


def _mtime_iso(st) -> str:
    try:
        return datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat(
            timespec="seconds")
    except (OSError, ValueError, OverflowError):
        return None


def discover(root: str):
    """确定性遍历，返回排序后的目录项绝对路径列表。"""
    found = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            filenames.sort()
            for d in dirnames:
                found.append(os.path.join(dirpath, d))
            for f in filenames:
                found.append(os.path.join(dirpath, f))
    except OSError as exc:
        return [], [f"walk_error: {exc}"]
    found.sort(key=lambda p: os.path.relpath(p, root).replace("\\", "/"))
    return found, []


# ---------------------------------------------------------------------------
# 证据收集器（本版核心差异点）
# ---------------------------------------------------------------------------
class EvidenceCollector:
    """把所有结论的可复核证据收集为结构化 evidence_map。"""

    def __init__(self) -> None:
        self.claims = []
        self._seq = 0

    def _next_id(self, kind: str) -> str:
        self._seq += 1
        return f"{kind}-{self._seq:04d}"

    @staticmethod
    def _source(locator: str, quote: str, evidence_type: str, role: str = "support") -> dict:
        q = "" if quote is None else str(quote)
        return {
            "locator": locator,
            "quote": q,
            "quote_sha256": sha256_text(q),
            "evidence_type": evidence_type,
            "role": role,
        }

    def add(self, claim_type: str, statement: str, conclusion_ref: str,
            sources, basis=None, confidence: str = "high", heuristic: bool = False,
            conflicts=None, extra=None) -> str:
        if len(self.claims) >= MAX_CLAIMS:
            return None
        cid = self._next_id(claim_type)
        claim = {
            "claim_id": cid,
            "claim_type": claim_type,
            "statement": statement,
            "conclusion_ref": conclusion_ref,
            "confidence": confidence,
            "heuristic": heuristic,
            "basis": basis or [],
            "sources": sources,
            "source_conflicts": conflicts or [],
        }
        if extra:
            claim.update(extra)
        self.claims.append(claim)
        return cid

    def verbatim(self, locator: str, quote: str, role: str = "support") -> dict:
        return self._source(locator, quote, "verbatim", role)

    def computed(self, locator: str, value: str, role: str = "support") -> dict:
        return self._source(locator, value, "computed_derivation", role)

    def derived(self, locator: str, quote: str, role: str = "support") -> dict:
        return self._source(locator, quote, "derived_regex", role)

    def parsed(self, locator: str, quote: str, role: str = "support") -> dict:
        return self._source(locator, quote, "parsed_field", role)

    def map(self) -> dict:
        return {
            "version": "1.1",
            "count": len(self.claims),
            "claims": self.claims,
        }


# ---------------------------------------------------------------------------
# 去重 / 版本 / 别名
# ---------------------------------------------------------------------------
def group_duplicates(entries, ev: EvidenceCollector):
    """按内容 SHA-256 分组（只统计有哈希的普通文件）。"""
    buckets = {}
    for e in entries:
        if e["type"] == "file" and e.get("sha256"):
            buckets.setdefault(e["sha256"], []).append(e)
    groups = []
    for h in sorted(buckets):
        members = sorted(buckets[h], key=lambda e: e["path"])
        if len(members) < 2:
            continue
        grp = {
            "sha256": h,
            "count": len(members),
            "members": [m["path"] for m in members],
            "evidence_ids": [],
        }
        srcs = [ev.computed(m["path"], f"sha256:{h}", "subject") for m in members]
        cid = ev.add(
            "duplicate_group",
            f"{len(members)} 个文件内容 SHA-256 相同：{', '.join(grp['members'])}",
            f"computed.duplicate_groups[{len(groups)}]",
            srcs,
            basis=["content_sha256"],
            confidence="high",
            heuristic=False,
        )
        if cid:
            grp["evidence_ids"] = [cid]
        groups.append(grp)
    return groups


def _collapse_stem(raw: str) -> str:
    stem = os.path.splitext(raw)[0]
    stem = re.sub(r"[-_.,;:()\[\]{}\s]+", " ", stem)
    return stem.strip().lower()


def normalize_stem(name: str) -> str:
    """归一化主干：先剥离版本号/日期，再剥离定性词（避免空主干时回退）。"""
    base = name
    for pat in VERSION_TOKEN_PATTERNS:
        base = pat.sub("", base)
    numeric_only = _collapse_stem(base)
    qualitative = QUALITATIVE_VERSION_RE.sub("", base)
    with_qualitative = _collapse_stem(qualitative)
    if len(with_qualitative) >= 2:
        return with_qualitative
    return numeric_only


def _diff_summary(text_a, text_b):
    if text_a is None or text_b is None:
        return {"method": "byte_size", "size_delta": (len(text_b or "") - len(text_a or ""))}
    sm = difflib.SequenceMatcher(None, text_a, text_b, autojunk=False)
    inserted = deleted = replaced = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            inserted += j2 - j1
        elif tag == "delete":
            deleted += i2 - i1
        elif tag == "replace":
            replaced += max(i2 - i1, j2 - j1)
    return {
        "method": "sequence_matcher",
        "ratio": round(sm.ratio(), 4),
        "inserted_chars": inserted,
        "deleted_chars": deleted,
        "replaced_chars": replaced,
    }


def version_candidates(entries, ev: EvidenceCollector):
    """按归一化主干分组，组内按 mtime 排序并给差异摘要（启发式）。"""
    stem_buckets = {}
    for e in entries:
        if e["type"] != "file":
            continue
        stem = normalize_stem(e["name"])
        if not stem:
            continue
        stem_buckets.setdefault(stem, []).append(e)
    out = []
    for stem in sorted(stem_buckets):
        members = stem_buckets[stem]
        if len(members) < 2:
            continue
        members.sort(key=lambda e: (e.get("mtime_iso") or "", e["path"]))
        chain = []
        for m in members:
            prev = chain[-1] if chain else None
            diff = None
            if prev is not None:
                diff = _diff_summary(prev.get("sample"), m.get("sample"))
            node = {
                "path": m["path"],
                "sha256": m.get("sha256"),
                "mtime_iso": m.get("mtime_iso"),
                "size": m.get("size"),
                "diff_from_previous": diff,
            }
            chain.append(node)
        srcs = []
        for m in members:
            srcs.append(ev.verbatim(m["path"], m["name"], "subject"))
            srcs.append(ev.computed(m["path"], f"sha256:{m.get('sha256')};mtime:{m.get('mtime_iso')}", "subject"))
        cid = ev.add(
            "version_candidate",
            f"归一化主干 {stem!r} 下 {len(members)} 个候选版本（启发式）",
            f"computed.version_candidates[{len(out)}]",
            srcs,
            basis=["stem_normalization", "mtime_order", "content_diff"],
            confidence="medium",
            heuristic=True,
        )
        evidence_ids = [cid] if cid else []
        out.append({
            "stem": stem,
            "count": len(members),
            "chain": chain,
            "heuristic": True,
            "evidence_ids": evidence_ids,
        })
    return out


def detect_aliases(entries, ev: EvidenceCollector):
    """符号链接（含断链）、硬链接组、内容别名。"""
    symlinks = [e for e in entries if e["type"] in ("symlink", "broken_symlink")]
    hardlink_groups = []
    ino_buckets = {}
    for e in entries:
        if e["type"] == "file" and e.get("inode") is not None and e.get("dev") is not None:
            key = (e["dev"], e["inode"])
            ino_buckets.setdefault(key, []).append(e)
    for key in sorted(ino_buckets, key=str):
        members = sorted(ino_buckets[key], key=lambda e: e["path"])
        if len(members) < 2:
            continue
        hardlink_groups.append({
            "dev": key[0],
            "inode": key[1],
            "members": [m["path"] for m in members],
        })
    content_aliases = []
    by_hash = {}
    for e in entries:
        if e["type"] == "file" and e.get("sha256"):
            by_hash.setdefault(e["sha256"], []).append(e)
    for h in sorted(by_hash):
        members = sorted(by_hash[h], key=lambda e: e["path"])
        names = {m["name"] for m in members}
        if len(names) > 1:
            content_aliases.append({
                "sha256": h,
                "distinct_names": sorted(names),
                "paths": [m["path"] for m in members],
            })

    result = {
        "symlinks": [{"path": e["path"], "link_target": e.get("link_target"),
                      "broken": e["type"] == "broken_symlink"} for e in symlinks],
        "hardlink_groups": hardlink_groups,
        "content_aliases": content_aliases,
        "evidence_ids": [],
    }
    for sl in symlinks:
        src = ev.verbatim(sl["path"], sl.get("link_target") or "", "subject")
        cid = ev.add(
            "alias",
            f"符号链接别名：{sl['path']} -> {sl.get('link_target')}（"
            f"{'断链' if sl['type'] == 'broken_symlink' else '有效'}）",
            "computed.aliases.symlinks",
            [src],
            basis=["symlink_target"],
            confidence="high",
        )
        if cid:
            result["evidence_ids"].append(cid)
    for grp in hardlink_groups:
        srcs = [ev.computed(m, f"dev:{grp['dev']};inode:{grp['inode']}") for m in grp["members"]]
        cid = ev.add(
            "alias",
            f"硬链接组（同 dev/inode）：{', '.join(grp['members'])}",
            "computed.aliases.hardlink_groups",
            srcs,
            basis=["stat_dev_inode"],
            confidence="high",
        )
        if cid:
            result["evidence_ids"].append(cid)
    for ca in content_aliases:
        srcs = [ev.computed(p, f"sha256:{ca['sha256']}") for p in ca["paths"]]
        cid = ev.add(
            "alias",
            f"内容别名（同名内容不同文件名）：{', '.join(sorted(ca['distinct_names']))}",
            "computed.aliases.content_aliases",
            srcs,
            basis=["content_sha256", "filename_diff"],
            confidence="high",
        )
        if cid:
            result["evidence_ids"].append(cid)
    return result


# ---------------------------------------------------------------------------
# 库目录解析：JSON / CSV / BibTeX / RIS / SQLite
# ---------------------------------------------------------------------------
def _as_records(obj):
    """把任意 JSON 结构折叠为扁平记录列表（尽力而为）。"""
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        for v in obj.values():
            recs = _as_records(v)
            if recs:
                return recs
        return [obj]
    return []


def parse_json_catalog(path, text, locator_base):
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"bad_json: {exc}"]
    records = _as_records(obj)
    out = []
    for i, rec in enumerate(records[:MAX_CATALOG_RECORDS]):
        out.append({
            "format": "json",
            "source_file": locator_base,
            "record_number": i,
            "locator": f"{locator_base}#records[{i}]",
            "fields": {k: v for k, v in rec.items() if isinstance(v, (str, int, float, bool))},
        })
    return out, []


def parse_csv_catalog(path, text, locator_base):
    out = []
    errors = []
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            rows = list(csv.reader(io.StringIO(text)))
            if not rows:
                return out, errors
            header = [c.strip() for c in rows[0]]
            for i, row in enumerate(rows[1:MAX_CATALOG_RECORDS + 1], start=1):
                fields = {}
                for j, val in enumerate(row):
                    key = header[j] if j < len(header) else f"col{j}"
                    fields[key] = val
                out.append({
                    "format": "csv",
                    "source_file": locator_base,
                    "record_number": i,
                    "locator": f"{locator_base}#row={i}",
                    "fields": fields,
                })
            return out, errors
        except UnicodeDecodeError:
            continue
    return out, ["csv_decode_error"]


def parse_bibtex(path, text, locator_base):
    out = []
    errors = []
    pattern = re.compile(
        r"@(\w+)\s*\{\s*([^,]+)\s*,(.*?)\n\s*\}", re.S | re.I)
    for m in pattern.finditer(text):
        etype, key, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {"entry_type": etype, "cite_key": key}
        # 花括号包裹字段（允许一层嵌套）
        for fm in re.finditer(r"(\w+)\s*=\s*[{]((?:[^{}]|[{][^{}]*[}])*)[}]", body):
            fields[fm.group(1).lower()] = fm.group(2)
        # 裸值字段（不含花括号，如 year = 2024）
        for fm in re.finditer(r"(\w+)\s*=\s*(\S+)", body):
            if fm.group(1).lower() not in fields:
                fields[fm.group(1).lower()] = fm.group(2)
        out.append({
            "format": "bibtex",
            "source_file": locator_base,
            "record_number": len(out),
            "locator": f"{locator_base}#entry={key}",
            "fields": fields,
        })
        if len(out) >= MAX_CATALOG_RECORDS:
            break
    return out, errors


def parse_ris(path, text, locator_base):
    out = []
    rec = None
    for line in text.splitlines():
        if len(line) < 2:
            continue
        tag = line[:2].strip()
        val = line[2:].strip()
        val = re.sub(r"^-\s*", "", val)
        if tag == "TY":
            if rec is not None:
                out.append(rec)
            rec = {"format": "ris", "source_file": locator_base,
                   "record_number": len(out), "locator": None, "fields": {}}
        if rec is None:
            continue
        if tag == "ER":
            rec["fields"]["end_record"] = True
            continue
        if tag == "TY":
            rec["fields"]["type"] = val
        else:
            rec["fields"][tag.lower()] = val
        rec["locator"] = f"{locator_base}#record={len(out) + 1}"
    if rec is not None:
        out.append(rec)
    for i, r in enumerate(out):
        r["record_number"] = i
        r["locator"] = f"{locator_base}#record={i + 1}"
    return out[:MAX_CATALOG_RECORDS], []


def parse_sqlite(path, locator_base):
    out = []
    errors = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return out, [f"sqlite_open_error: {exc}"]
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                count = cur.fetchone()[0]
                out.append({"format": "sqlite_table", "source_file": locator_base,
                            "table": t, "row_count": count,
                            "locator": f"{locator_base}#table={t}"})
            except sqlite3.Error as exc:
                errors.append(f"sqlite_query_error: table={t}: {exc}")
    except sqlite3.Error as exc:
        errors.append(f"sqlite_query_error: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out, errors


def parse_catalogs(entries, ev: EvidenceCollector):
    """按扩展名识别并解析库目录。"""
    catalog_records = []
    errors = []
    ext_handlers = {
        ".json": parse_json_catalog,
        ".csv": parse_csv_catalog,
        ".bib": parse_bibtex,
        ".ris": parse_ris,
        ".db": parse_sqlite,
        ".sqlite": parse_sqlite,
        ".sqlite3": parse_sqlite,
    }
    for e in entries:
        if e["type"] != "file":
            continue
        ext = os.path.splitext(e["name"])[1].lower()
        handler = ext_handlers.get(ext)
        if handler is None:
            continue
        abs_path = e.get("abs_path") or e["path"]
        if ext in (".db", ".sqlite", ".sqlite3"):
            recs, errs = handler(abs_path, e["path"])
        else:
            text, _, _ = _read_text_file(abs_path)
            if text is None:
                recs, errs = [], [f"unreadable_catalog: {e['path']}"]
            else:
                recs, errs = handler(e["path"], text, e["path"])
        catalog_records.extend(recs)
        errors.extend(errs)
        for rec in recs:
            srcs = [ev.parsed(rec["locator"], json.dumps(rec.get("fields", {}),
                        ensure_ascii=False), "subject")]
            ev.add("catalog_record",
                   f"目录记录 {rec.get('locator')}：{_brief(rec.get('fields', {}))}",
                   "computed.reconciliation.catalog_records",
                   srcs,
                   basis=[f"{rec['format']}_parse"],
                   confidence="high",
                   heuristic=False)
    return catalog_records, errors


def _brief(fields: dict, limit: int = 120) -> str:
    s = json.dumps(fields, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


# ---------------------------------------------------------------------------
# 行政版本说明解析
# ---------------------------------------------------------------------------
def parse_version_notes(task_text, entries, ev: EvidenceCollector):
    notes = []
    # task.md 全量
    if task_text:
        for i, line in enumerate(task_text.splitlines(), start=1):
            if NOTE_LINE_RE.search(line):
                notes.append({"source": "task.md", "locator": f"task.md#L{i}",
                              "line": line.strip()})
    # 材料里的版本说明文件
    for e in entries:
        if e["type"] != "file":
            continue
        if not NOTE_NAME_RE.search(e["name"]) and not NOTE_NAME_RE.search(e["path"]):
            # 仍尝试 .md/.txt 内容（仅当文件名或路径含说明关键词才进）
            continue
        text = e.get("sample")
        if text is None:
            text, _, _ = _read_text_file(e.get("abs_path") or e["path"])
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if NOTE_LINE_RE.search(line):
                notes.append({"source": e["path"], "locator": f"{e['path']}#L{i}",
                              "line": line.strip()})
    notes = notes[:MAX_VERSION_NOTES]
    for n in notes:
        ev.add("version_note",
               f"版本说明：{n['line'][:120]}",
               "computed.version_notes",
               [ev.verbatim(n["locator"], n["line"], "subject")],
               basis=["version_note_regex"],
               confidence="high",
               heuristic=False)
    return notes


# ---------------------------------------------------------------------------
# 恢复对账
# ---------------------------------------------------------------------------
def reconcile(entries, catalog_records, ev: EvidenceCollector):
    inv_paths = {e["path"] for e in entries if e["type"] == "file"}
    inv_by_path = {e["path"]: e for e in entries if e["type"] == "file"}
    referenced = set()
    records_out = []
    statuses = {"matches": 0, "mismatches": 0, "missing": 0, "orphaned": 0}

    for rec in catalog_records:
        fields = rec.get("fields", {})
        p = None
        for key in PATH_FIELD_NAMES:
            if key in fields:
                p = str(fields[key])
                break
        h = None
        for key in HASH_FIELD_NAMES:
            if key in fields:
                h = str(fields[key])
                break
        if p is None:
            continue
        p = p.replace("\\", "/").strip()
        referenced.add(p)
        rec_entry = inv_by_path.get(p)
        if rec_entry is None:
            st = "missing"
        elif h and rec_entry.get("sha256") and rec_entry["sha256"].lower() != h.lower():
            st = "mismatch"
        else:
            st = "match"
        statuses[st] = statuses.get(st, 0) + 1
        record = {
            "locator": rec.get("locator"),
            "path": p,
            "expected_hash": h,
            "actual_hash": rec_entry.get("sha256") if rec_entry else None,
            "status": st,
            "evidence_ids": [],
        }
        srcs = [ev.parsed(rec.get("locator"), f"path={p};hash={h}", "subject")]
        if rec_entry:
            srcs.append(ev.computed(p, f"sha256:{rec_entry.get('sha256')}", "subject"))
        cid = ev.add(
            "reconciliation",
            f"对账记录 {p}：{st}",
            f"computed.reconciliation.catalog_records[{len(records_out)}]",
            srcs,
            basis=["catalog_path", "inventory_sha256"],
            confidence="high",
            heuristic=False,
        )
        if cid:
            record["evidence_ids"] = [cid]
        records_out.append(record)

    orphaned = [p for p in sorted(inv_paths) if p not in referenced
                and os.path.basename(p) not in ("case.json", "task.md")]
    statuses["orphaned"] = len(orphaned)
    for p in orphaned:
        e = inv_by_path[p]
        ev.add(
            "reconciliation",
            f"孤儿文件（未被任何目录引用）：{p}",
            "computed.reconciliation.orphaned",
            [ev.computed(p, f"sha256:{e.get('sha256')}", "subject")],
            basis=["inventory_minus_catalog"],
            confidence="high",
            heuristic=False,
        )
    return {
        "catalog_records": records_out,
        "orphaned": orphaned,
        "summary": statuses,
        "evidence_ids": [],
    }


# ---------------------------------------------------------------------------
# 索引重建
# ---------------------------------------------------------------------------
def build_index(entries):
    by_path = {}
    by_sha256 = {}
    by_stem = {}
    by_keyword = {}
    for e in entries:
        if e["type"] != "file":
            continue
        by_path[e["path"]] = {
            "name": e["name"], "size": e.get("size"), "sha256": e.get("sha256"),
            "mtime_iso": e.get("mtime_iso"),
        }
        if e.get("sha256"):
            by_sha256.setdefault(e["sha256"], []).append(e["path"])
        stem = normalize_stem(e["name"])
        if stem:
            by_stem.setdefault(stem, []).append(e["path"])
        sample = e.get("sample")
        if sample:
            tokens = set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", sample.lower()))
            for tok in sorted(tokens)[:MAX_KEYWORDS_PER_FILE]:
                by_keyword.setdefault(tok, []).append(e["path"])
    for k in by_sha256:
        by_sha256[k] = sorted(set(by_sha256[k]))
    for k in by_stem:
        by_stem[k] = sorted(set(by_stem[k]))
    for k in by_keyword:
        by_keyword[k] = sorted(set(by_keyword[k]))
    return {
        "by_path": by_path,
        "by_sha256": by_sha256,
        "by_stem": by_stem,
        "by_keyword": by_keyword,
    }


# ---------------------------------------------------------------------------
# 检索问答（机械层）
# ---------------------------------------------------------------------------
def answer_questions(entries, dup_groups, version_groups, aliases, recon,
                     catalog_records, ev: EvidenceCollector):
    files = [e for e in entries if e["type"] == "file"]
    broken = aliases["symlinks"]
    broken_n = len([b for b in broken if b["broken"]])
    answers = []
    qa = [
        ("inventory_summary",
         "本次盘点共发现多少个普通文件、目录、符号链接？",
         f"普通文件 {len(files)} 个，目录 {len([e for e in entries if e['type']=='dir'])} 个，"
         f"符号链接 {len(broken)} 个（其中断链 {broken_n} 个）。"),
        ("duplicate_summary",
         "按内容哈希判定的重复文件组有哪些？",
         f"共 {len(dup_groups)} 个重复组。"),
        ("version_summary",
         "归一化主干上的候选版本链有哪些？",
         f"共 {len(version_groups)} 条候选版本链（启发式）。"),
        ("reconciliation_summary",
         "目录对账的 matches/mismatches/missing/orphaned 分别是多少？",
         json.dumps(recon["summary"], ensure_ascii=False)),
        ("catalog_summary",
         "解析出多少条目录记录（JSON/CSV/BibTeX/RIS/SQLite）？",
         f"共 {len(catalog_records)} 条。"),
    ]
    for i, (qid, question, ans) in enumerate(qa):
        answers.append({"question": question, "answer": ans, "evidence_ids": []})
    return answers


# ---------------------------------------------------------------------------
# 目录树 / 清单 / 产物
# ---------------------------------------------------------------------------
def directory_tree(entries, root):
    lines = []
    root_name = os.path.basename(os.path.abspath(root)) or "input"
    lines.append(root_name + "/")
    dirs = {}
    files_by_dir = {}
    for e in entries:
        d = os.path.dirname(e["path"])
        if e["type"] == "dir":
            dirs.setdefault(d, []).append(e["path"])
        elif e["type"] in ("file", "symlink", "broken_symlink", "non_regular", "other"):
            files_by_dir.setdefault(d, []).append(e["path"])
    all_dirs = sorted(set(dirs.keys()) | set(files_by_dir.keys()))
    for d in all_dirs:
        depth = d.count("/") + 1 if d else 0
        parts = d.split("/") if d else []
        lines.append("    " * depth + (os.path.basename(d) + "/" if d else ""))
        for sub in sorted(dirs.get(d, [])):
            name = os.path.basename(sub)
            lines.append("    " * (depth + 1) + name + "/")
            if not any(x.startswith(sub + "/") or os.path.dirname(x) == sub
                       for x in (list(dirs.keys()) + list(files_by_dir.keys()))):
                lines.append("    " * (depth + 2) + "[空目录]")
        for f in sorted(files_by_dir.get(d, [])):
            e = next((x for x in entries if x["path"] == f), None)
            marker = {"symlink": "->", "broken_symlink": "->!",
                      "dir": "", "file": "", "non_regular": "?", "other": "?"}.get(
                e["type"] if e else "file", "")
            target = (" " + marker + " " + (e.get("link_target") or "")) if marker else ""
            lines.append("    " * (depth + 1) + os.path.basename(f) + target)
    return "\n".join(lines) + "\n"


def _public_entry(e):
    """序列化时剔除本地绝对路径，只保留相对 locator。"""
    return {k: v for k, v in e.items() if k != "abs_path"}


def manifest_rows(entries):
    rows = []
    for e in entries:
        if e["type"] != "file":
            continue
        rows.append({
            "path": e["path"],
            "name": e["name"],
            "type": e["type"],
            "size": e.get("size"),
            "mode": e.get("mode"),
            "mtime_iso": e.get("mtime_iso"),
            "sha256": e.get("sha256") or "",
            "hash_skipped_reason": e.get("hash_skipped_reason") or "",
            "encoding": e.get("encoding") or "",
            "line_count": e.get("line_count"),
            "read_error": e.get("read_error") or "",
        })
    rows.sort(key=lambda r: r["path"])
    return rows


def write_manifest_csv(rows, out_dir):
    path = os.path.join(out_dir, "library_manifest.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["path"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def append_jsonl(path, rec):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# result 构建与落盘
# ---------------------------------------------------------------------------
def extract_case_id(case_json, task_text):
    if isinstance(case_json, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
            if key in case_json and case_json[key]:
                return str(case_json[key])
    if task_text:
        m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1)
    return None


def extract_contract(case_json):
    if not isinstance(case_json, dict):
        return None
    for key in CONTRACT_KEYS:
        if key in case_json and case_json[key]:
            return case_json[key]
    return None


def build_result(case_id, status, contract, computed, evidence_map, sources,
                 uncertainty, pending_human, change_log, errors, degradations,
                 runtime, limitations):
    result = {
        "case_id": case_id,
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "change_log": change_log,
        "evidence_map": evidence_map,
        "computed": computed,
        "errors": errors,
        "degradations": degradations,
        "runtime": runtime,
        "limitations": limitations,
    }
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k in RESERVED_KEYS:
                computed.setdefault("_contract_" + k, v)
            else:
                result[k] = v
    return result


def _log(change_log, actor, step, detail):
    change_log.append({"time": now_iso(), "actor": actor, "step": step, "detail": detail})


# ---------------------------------------------------------------------------
# 骨架（前 3 分钟必须落盘）
# ---------------------------------------------------------------------------
def skeleton(case_json, task_text, out_dir, work_dir):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_id = extract_case_id(case_json, task_text)
    contract = extract_contract(case_json)
    status = "partial"
    if case_json is None and not task_text:
        status = "input_error"
    result = build_result(
        case_id=case_id,
        status=status,
        contract=contract,
        computed={},
        evidence_map={"version": "1.1", "count": 0, "claims": []},
        sources=[],
        uncertainty=[{"item": "output_contract",
                      "reason": "合同缺失，仅输出元数据字段"}] if contract is None else [],
        pending_human=[],
        change_log=[{"time": now_iso(), "actor": "core_impl", "step": "skeleton",
                     "detail": "写入 partial 结果骨架"}],
        errors=[],
        degradations=[],
        runtime={"started_iso": now_iso(), "elapsed_seconds": 0.0},
        limitations=["骨架阶段仅含 case_id 与合同占位，尚未盘点材料"],
    )
    write_json(os.path.join(out_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------------------
# 全量流水线
# ---------------------------------------------------------------------------
def run_pipeline(input_root, out_dir, work_dir, start_time=None):
    start_time = start_time or time.time()
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    errors = []
    degradations = []
    change_log = []
    change_log.append({"time": now_iso(), "actor": "core_impl", "step": "run.start",
                       "detail": f"input_root={input_root}"})

    case_path = os.path.join(input_root, "case.json")
    task_path = os.path.join(input_root, "task.md")
    case_json = None
    task_text = None

    if os.path.isfile(case_path):
        raw, _, read_err = _read_text_file(case_path)
        if raw is None:
            errors.append(f"case.json unreadable: {read_err}")
        else:
            try:
                case_json = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"case.json invalid JSON: {exc}")
                degradations.append("case.json 无法解析，按 input_error 降级")
    else:
        errors.append("missing case.json")
        degradations.append("缺少 /input/case.json")

    if os.path.isfile(task_path):
        task_text, _, _ = _read_text_file(task_path)
    else:
        degradations.append("missing task.md")

    case_id = extract_case_id(case_json, task_text)
    contract = extract_contract(case_json)

    # 先落骨架，保证中途异常也有合法产物
    skeleton_result = skeleton(case_json, task_text, out_dir, work_dir)

    # 盘点材料（排除 case.json/task.md）
    found, walk_errors = discover(input_root)
    errors.extend(walk_errors)
    entries = []
    for p in found:
        if os.path.basename(p) in ("case.json", "task.md"):
            continue
        entries.append(_stat_entry(p, input_root))
    entries.sort(key=lambda e: e["path"])

    material_entries = [e for e in entries if e["type"] == "file"]
    if not material_entries and not errors:
        status = "insufficient_evidence"
    elif not case_json:
        status = "input_error"
    else:
        status = "ok" if not errors else "partial"

    ev = EvidenceCollector()
    duplicate_groups = group_duplicates(entries, ev)
    version_groups = version_candidates(entries, ev)
    aliases = detect_aliases(entries, ev)
    version_notes = parse_version_notes(task_text, entries, ev)
    catalog_records, catalog_errors = parse_catalogs(entries, ev)
    errors.extend(catalog_errors)
    if catalog_errors:
        degradations.append("部分库目录解析失败，已记录到 errors")
    recon = reconcile(entries, catalog_records, ev)
    index = build_index(entries)
    answers = answer_questions(entries, duplicate_groups, version_groups,
                               aliases, recon, catalog_records, ev)

    summary = {
        "total_entries": len(entries),
        "files": len(material_entries),
        "dirs": len([e for e in entries if e["type"] == "dir"]),
        "symlinks": len([e for e in entries if e["type"] in ("symlink", "broken_symlink")]),
        "broken_symlinks": len([e for e in entries if e["type"] == "broken_symlink"]),
        "duplicate_groups": len(duplicate_groups),
        "version_candidates": len(version_groups),
        "catalog_records": len(catalog_records),
        "version_notes": len(version_notes),
        "hash_skipped": len([e for e in entries if e.get("hash_skipped_reason")]),
        "with_read_error": len([e for e in entries if e.get("read_error")]),
    }

    computed = {
        "files": [_public_entry(e) for e in entries],
        "summary": summary,
        "duplicate_groups": duplicate_groups,
        "version_candidates": version_groups,
        "version_notes": version_notes,
        "aliases": aliases,
        "reconciliation": recon,
        "answers": answers,
        "index": index,
        "artifacts": {},
    }

    # 产物落盘
    rows = manifest_rows(entries)
    write_manifest_csv(rows, out_dir)
    write_text(os.path.join(out_dir, "directory_tree.txt"), directory_tree(entries, input_root))
    write_json(os.path.join(out_dir, "dedup_version.json"), {
        "duplicate_groups": duplicate_groups,
        "version_candidates": version_groups,
        "version_notes": version_notes,
        "aliases": aliases,
    })
    write_json(os.path.join(out_dir, "recovery_report.json"), recon)
    write_json(os.path.join(out_dir, "library_index.json"), index)
    write_json(os.path.join(out_dir, "evidence_map.json"), ev.map())
    write_text(os.path.join(out_dir, "library_manifest.md"), handoff_report(
        case_id, summary, duplicate_groups, version_groups, aliases, recon, errors))
    op_log_path = os.path.join(out_dir, "operation_log.jsonl")
    with open(op_log_path, "w", encoding="utf-8") as fh:
        pass  # 清空，确保可复跑
    for step in change_log:
        append_jsonl(op_log_path, step)
    append_jsonl(op_log_path, {"time": now_iso(), "actor": "core_impl",
                               "step": "run.finish", "detail": "全量流水线完成"})

    artifact_names = [
        "library_manifest.csv", "directory_tree.txt", "dedup_version.json",
        "recovery_report.json", "library_index.json", "library_manifest.md",
        "operation_log.jsonl", "evidence_map.json",
    ]
    for name in artifact_names:
        computed["artifacts"][name] = f"/app/output/{name}"

    # 顶层 sources：机械性证据摘要（详细证据在 evidence_map）
    sources = [
        {"item": "inventory", "locator": "computed.files",
         "quote": f"{summary['files']} 个普通文件完成盘点"},
        {"item": "duplicates", "locator": "computed.duplicate_groups",
         "quote": f"{summary['duplicate_groups']} 个重复组"},
        {"item": "versions", "locator": "computed.version_candidates",
         "quote": f"{summary['version_candidates']} 条候选版本链"},
        {"item": "reconciliation", "locator": "computed.reconciliation.summary",
         "quote": json.dumps(recon["summary"], ensure_ascii=False)},
    ]

    uncertainty = []
    if contract is None:
        uncertainty.append({"item": "output_contract", "reason": "case.json 未提供输出合同",
                            "severity": "medium"})
    if version_groups:
        uncertainty.append({"item": "version_candidates",
                            "reason": "版本链按归一化主干+内容差异启发式判定，需人工确认",
                            "severity": "low"})
    if not catalog_records:
        uncertainty.append({"item": "reconciliation",
                            "reason": "未发现可解析的库目录，对账仅有孤儿文件判定",
                            "severity": "medium"})

    pending_human = []
    for b in [s for s in aliases["symlinks"] if s["broken"]]:
        pending_human.append({"item": f"broken symlink {b['path']}",
                              "reason": f"链接目标 {b['link_target']} 不存在"})
    if not catalog_records and material_entries:
        pending_human.append({"item": "catalog_expected_paths",
                              "reason": "缺少库目录清单，无法核对期望路径"})

    if errors and status == "ok":
        status = "partial"
    if status == "ok":
        change_log.append({"time": now_iso(), "actor": "core_impl",
                           "step": "run.ok", "detail": "流水线完成，无阻断错误"})
    else:
        change_log.append({"time": now_iso(), "actor": "core_impl",
                           "step": "run.degraded", "detail": f"status={status}"})

    runtime = {
        "started_iso": datetime.datetime.fromtimestamp(
            start_time, datetime.timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - start_time, 3),
    }

    limitations = [
        "版本链与字段映射为启发式结果，需人工复核后定稿",
        "内容哈希仅对 2MB 以内可读普通文件计算",
        "逐字 quote 仅针对文本材料；二进制文件以哈希/stat 的 computed_derivation 记录",
    ]

    result = build_result(case_id, status, contract, computed, ev.map(), sources,
                          uncertainty, pending_human, change_log, errors, degradations,
                          runtime, limitations)
    write_json(os.path.join(out_dir, "result.json"), result)
    return result


def handoff_report(case_id, summary, duplicate_groups, version_groups, aliases,
                   recon, errors):
    lines = [
        "# 科研资料库修复交接报告",
        "",
        f"- case_id: `{case_id or 'N/A'}`",
        f"- 状态: 由 result.json 的 status 字段决定",
        f"- 盘点摘要: {json.dumps(summary, ensure_ascii=False)}",
        "",
        "## 重复组（按内容 SHA-256）",
    ]
    if duplicate_groups:
        for g in duplicate_groups:
            lines.append(f"- {g['sha256'][:12]}… × {g['count']}: {', '.join(g['members'])}")
    else:
        lines.append("- 未发现内容重复组")
    lines.append("")
    lines.append("## 候选版本链（启发式）")
    if version_groups:
        for v in version_groups:
            lines.append(f"- `{v['stem']}`: {' -> '.join(n['path'] for n in v['chain'])}")
    else:
        lines.append("- 未发现候选版本链")
    lines.append("")
    lines.append("## 别名")
    lines.append(f"- 符号链接 {len(aliases['symlinks'])} 个；硬链接组 {len(aliases['hardlink_groups'])} 个；"
                 f"内容别名 {len(aliases['content_aliases'])} 个")
    lines.append("")
    lines.append("## 对账摘要")
    lines.append(f"- {json.dumps(recon['summary'], ensure_ascii=False)}")
    if errors:
        lines.append("")
        lines.append("## 错误")
        for e in errors:
            lines.append(f"- {e}")
    lines.append("")
    lines.append("> 本报告由脚本生成；证据定位见 `evidence_map.json`，操作日志见 `operation_log.jsonl`。")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# --update 增量合并
# ---------------------------------------------------------------------------
def deep_merge(base, patch):
    """深度合并：dict 递归合并；list 追加（按 JSON 去重）；其余覆盖。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k in base:
                base[k] = deep_merge(base[k], v)
            else:
                base[k] = v
        return base
    if isinstance(base, list) and isinstance(patch, list):
        seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in base}
        for v in patch:
            key = json.dumps(v, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                base.append(v)
                seen.add(key)
        return base
    return patch


def apply_update(result_path, patch_path, out_dir):
    with open(result_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    with open(patch_path, "r", encoding="utf-8") as fh:
        patch = json.load(fh)
    patch = dict(patch)
    # 允许 Agent 直接指定 status 与顶层结论
    result = deep_merge(result, patch)
    result["change_log"] = result.get("change_log", [])
    result["change_log"].append({
        "time": now_iso(), "actor": "agent", "step": "update",
        "detail": "语义补丁增量合并",
    })
    if os.path.basename(patch_path) and out_dir:
        pass
    write_json(result_path, result)
    return result


# ---------------------------------------------------------------------------
# --check 校验
# ---------------------------------------------------------------------------
def check(result_path, case_json=None, out_dir=None):
    problems = []
    if not os.path.isfile(result_path):
        return False, ["result.json 不存在"]
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"result.json 无法解析: {exc}"]

    if not result.get("case_id"):
        problems.append("case_id 为空")
    if result.get("status") not in STATUSES:
        problems.append(f"status 非法: {result.get('status')}")
    em = result.get("evidence_map") or {}
    claims = em.get("claims") or []
    if not isinstance(claims, list) or not claims:
        problems.append("evidence_map.claims 为空")
    else:
        for i, c in enumerate(claims):
            if not c.get("claim_id") or not c.get("statement"):
                problems.append(f"claim[{i}] 缺 claim_id/statement")
            srcs = c.get("sources") or []
            if not srcs:
                problems.append(f"claim[{c.get('claim_id')}] 无 sources")
            for s in srcs:
                if not s.get("locator"):
                    problems.append(f"claim[{c.get('claim_id')}] source 缺 locator")
                if not s.get("quote") and not s.get("quote_sha256"):
                    problems.append(f"claim[{c.get('claim_id')}] source 缺 quote")
    if case_json is not None and isinstance(case_json, dict):
        contract = extract_contract(case_json)
        if isinstance(contract, dict):
            for k in contract:
                if k not in result and f"_contract_{k}" not in result.get("computed", {}):
                    problems.append(f"合同字段缺失: {k}")
    if out_dir:
        for name in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json", "evidence_map.json",
                     "library_manifest.md", "operation_log.jsonl"):
            if not os.path.isfile(os.path.join(out_dir, name)):
                problems.append(f"产物缺失: {name}")
    return (not problems), problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="科研资料库修复（证据溯源强化版）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("skeleton", help="仅写入 partial 结果骨架")
    _add_io(sp)
    sp.set_defaults(func=cmd_skeleton)

    sp = sub.add_parser("run", help="执行全量流水线")
    _add_io(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("update", help="增量合并语义补丁")
    sp.add_argument("--patch", required=True)
    sp.add_argument("--result", required=True)
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("check", help="校验 result.json")
    sp.add_argument("--result", required=True)
    sp.add_argument("--case-json")
    sp.add_argument("--output-dir")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("selftest", help="运行自检")
    sp.set_defaults(func=lambda a: _run_selftest_entry())
    return p


def _add_io(sp):
    sp.add_argument("--case-json", default=None)
    sp.add_argument("--task-path", default=None)
    sp.add_argument("--input-root", default=DEFAULT_INPUT)
    sp.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    sp.add_argument("--work-dir", default=DEFAULT_WORK)


def _load_case_json(path):
    if path and os.path.isfile(path):
        raw, _, _ = _read_text_file(path)
        try:
            return json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def cmd_skeleton(args):
    case_json = _load_case_json(args.case_json)
    task_text = None
    if args.task_path and os.path.isfile(args.task_path):
        task_text, _, _ = _read_text_file(args.task_path)
    skeleton(case_json, task_text, args.output_dir, args.work_dir)
    print(f"skeleton -> {os.path.join(args.output_dir, 'result.json')}")
    return 0


def cmd_run(args):
    try:
        run_pipeline(args.input_root, args.output_dir, args.work_dir)
    except Exception:
        traceback.print_exc()
        # 保留已落盘骨架，退出码 2
        return 2
    print(f"run -> {os.path.join(args.output_dir, 'result.json')}")
    return 0


def cmd_update(args):
    apply_update(args.result, args.patch, os.path.dirname(args.result))
    print(f"update -> {args.result}")
    return 0


def cmd_check(args):
    case_json = None
    if args.case_json and os.path.isfile(args.case_json):
        raw, _, _ = _read_text_file(args.case_json)
        try:
            case_json = json.loads(raw or "{}")
        except json.JSONDecodeError:
            case_json = None
    ok, problems = check(args.result, case_json, args.output_dir)
    if ok:
        print("check: PASS")
        return 0
    for p in problems:
        print(f"check: FAIL - {p}")
    return 1


def _run_selftest_entry():
    # 交由 scripts/selftest.py 逻辑（避免循环依赖）
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("selftest", os.path.join(here, "selftest.py"))
    if spec is None or spec.loader is None:
        print("selftest.py 不存在")
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return 0 if mod.main() else 1


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
