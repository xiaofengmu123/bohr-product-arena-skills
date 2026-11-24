#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 科研文档库修复引擎（确定性、纯标准库）。

职责：
  * 扫描 /input 递归盘点文件、目录与权限（mode），生成稳定 ID；
  * 逐文件 sha256 哈希，按 hash 识别重复/别名，按归一化 stem 识别版本候选；
  * 读取 case.json / task.md 与可读文本材料，提取 output_contract 与元数据提示；
  * 建立关键词检索索引，并把每条结论锚到 locator + 逐字 quote；
  * 装配 result.json 与三个交付产物，保证同输入两次运行除 runtime 外逐字节一致。

本模块不依赖任何第三方库，也不依赖 PDF/OCR/office 外部工具；缺少文本层时
如实降级并记录，不编造。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

SKILL_NAME = "library-doc-repair"
VERSION = "1.1.0"
SCHEMA = 1
TASK_LABEL = "library-05-document-repair"
DELIVERABLE_NAME = "library_index.json"

ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime",
)
LIST_ENVELOPE_KEYS = {
    "claims", "uncertainties", "sources", "change_log",
    "pending_human", "errors", "degradations",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

# 文本读取上限（字符解码前的字节数）：超过则只读前段用于检索并标注 truncated。
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_QUOTE_LEN = 320
MAX_MATERIAL_DUMP_BYTES = 64 * 1024  # show 时单文件打印上限

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml",
    ".yml", ".xml", ".html", ".htm", ".log", ".bib", ".tex", ".rst", ".ini",
    ".cfg", ".conf", ".toml", ".py", ".js", ".ts", ".sh", ".org", ".nfo",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg", ".ico"}
OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf"}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst"}
PDF_EXT = {".pdf"}
EPUB_DJVU = {".epub", ".djvu", ".mobi", ".azw", ".azw3"}

# 用于在 case.json 与文本材料中识别“显式元数据提示”的键名（大小写不敏感）。
QUERY_KEYS = {"queries", "query", "questions", "searches", "search", "retrieval_questions", "questions_retrieval"}
ALIAS_KEYS = {"aliases", "alias", "duplicates", "duplicate"}
VERSION_KEYS = {"versions", "version", "version_notes", "revisions", "revision", "editions"}
META_KEYS = {"metadata", "meta", "notes", "administrative_notes"}
OPERATION_KEYS = {"operations", "operation", "recovery", "index_operations", "workorder", "tasks", "actions"}

# 语义关键词（中文/英文），用于在文本材料中粗筛提示句；只作 show 提示，不做最终裁定。
HINT_PATTERNS = (
    re.compile(r"(alias|别名|副本|重复|duplicate|copy)", re.IGNORECASE),
    re.compile(r"(version|版本|revision|修订|edition|版次|rev\.|v\d)", re.IGNORECASE),
    re.compile(r"(metadata|元数据|author|作者|title|标题|date|日期|year|年)", re.IGNORECASE),
    re.compile(r"(query|检索|question|问题|search|查找|find)", re.IGNORECASE),
    re.compile(r"(recovery|恢复|index|索引|operation|操作|rename|移动|删除)", re.IGNORECASE),
)


# ------------------------------------------------------------------ 工具函数

def _write_json(path: Path, obj) -> None:
    """写 JSON，固定换行为 \\n，ensure_ascii=False，保证跨平台逐字节一致。"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def sha256_file(path: Path) -> str:
    """流式计算 sha256；不可读时返回特殊标记，不抛异常。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "hash_unavailable"


def read_bytes_limited(path: Path, limit: int):
    """读文件前 limit 字节，返回 (bytes, truncated, error)。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(limit + 1)
        truncated = len(raw) > limit
        if truncated:
            raw = raw[:limit]
        return raw, truncated, None
    except OSError as e:
        return b"", False, str(e)


def decode_text(raw: bytes):
    """尝试多种编码解码；返回 (text, encoding)。全部失败则 replace 兜底。"""
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _sniff_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    return False


def detect_kind(suffix: str, raw: bytes) -> str:
    ext = suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in EPUB_DJVU:
        return "ebook"
    if ext == ".json":
        return "json"
    if ext in {".md", ".markdown"}:
        return "markdown"
    if ext in TEXT_EXTS:
        return "text"
    # 未知扩展名：用内容嗅探
    if raw and not _sniff_binary(raw):
        return "text"
    return "binary"


_is_text_kind = {
    "text", "json", "markdown", "csv", "xml", "html",
}


def normalize_stem(name: str) -> str:
    """归一化文件名 stem，用于版本候选分组。确定性、启发式。

    去掉扩展名、常见副本/版本后缀（copy、v1、final、draft、bak、日期位数等），
    统一分隔符为 '-'，小写。返回空时回退到原始小写 stem。
    """
    base = os.path.splitext(name)[0].strip()
    base = base.lower()
    base = re.sub(r"\.[^.]+$", "", base)  # 去掉多段伪扩展（如 .backup）
    base = re.sub(
        r"[\s._\-]+((?:copy|副本|复件)\s*\d*|\(\d+\)|\[\d+\]|-\d+|"
        r"v\.?\s*\d+(?:\.\d+)*[a-z]?|ver\.?\s*\d+[a-z]?|"
        r"(?:final|draft|rev|revision|updated?|edited?|cleaned?|bak|backup|old|new|original)"
        r"[.\-_ ]*\d*)\s*$",
        "", base,
    )
    base = re.sub(r"^[\s._\-]+|[\s._\-]+$", "", base)
    base = re.sub(r"[\s._\-]+", "-", base)
    return base or os.path.splitext(name)[0].lower()


def contains_quote(text: str, quote: str) -> bool:
    """quote 是否在 text 中逐字命中；换行风格差异做一次归一化兜底。"""
    if not quote or not text:
        return False
    if quote in text:
        return True
    q = quote.replace("\r\n", "\n").replace("\r", "\n")
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    return q in t


def truncate_quote(q: str, limit: int = MAX_QUOTE_LEN) -> str:
    q = q.strip()
    if len(q) > limit:
        return q[: limit - 1] + "…"
    return q


# ------------------------------------------------------------------ 扫描与盘点

def _rel_key(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _scan_entries(input_dir: Path):
    """返回 (files, dirs, errors)；稳定排序保证确定性。"""
    files = []
    dirs = []
    errors = []
    try:
        entries = list(input_dir.rglob("*"))
    except OSError as e:
        return [], [], [{"kind": "scan_error", "locator": input_dir.as_posix(), "detail": str(e)}]
    entries.sort(key=lambda p: _rel_key(p, input_dir))
    for p in entries:
        rel = _rel_key(p, input_dir)
        try:
            if p.is_symlink():
                files.append({"rel_path": rel, "kind": "unreadable", "unreadable_reason": "symlink"})
                continue
            if p.is_dir():
                dirs.append(rel + "/")
            else:
                files.append({"rel_path": rel, "kind": "file"})
        except OSError as e:
            errors.append({"kind": "stat_error", "locator": rel, "detail": str(e)})
    return files, dirs, errors


def _process_file(input_dir: Path, rel: str) -> dict:
    """单个文件 → 盘点记录。"""
    path = input_dir / rel
    rec = {
        "id": None,  # 由调用方回填
        "rel_path": rel,
        "name": os.path.basename(rel),
        "ext": os.path.splitext(rel)[1].lower(),
        "bytes": None,
        "sha256": None,
        "mode": None,
        "kind": "unknown",
        "readable": False,
        "text_available": False,
        "text_encoding": None,
        "truncated": None,
        "unreadable_reason": None,
    }
    try:
        st = path.stat()
        rec["bytes"] = st.st_size
        rec["mode"] = oct(st.st_mode & 0o777)[2:].zfill(3)
    except OSError as e:
        rec["unreadable_reason"] = "stat_failed:" + str(e)
        rec["kind"] = "unreadable"
        return rec

    rec["sha256"] = sha256_file(path)
    if rec["sha256"] == "hash_unavailable":
        rec["unreadable_reason"] = "hash_failed"
        rec["kind"] = "unreadable"
        return rec
    rec["readable"] = True

    raw, truncated, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err:
        rec["unreadable_reason"] = "read_failed:" + err
        rec["kind"] = "unreadable"
        return rec
    rec["truncated"] = "truncated" if truncated else None

    rec["kind"] = detect_kind(rec["ext"], raw[:4096] if raw else b"")
    if rec["kind"] in _is_text_kind:
        text, enc = decode_text(raw)
        rec["text_available"] = True
        rec["text_encoding"] = enc
    return rec


def _split_lines(text: str):
    return text.splitlines()


def normalize_tokens(text: str):
    return [t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", text.lower()) if t]


def scan(input_dir: Path) -> dict:
    """对输入目录做确定性盘点。返回 library 字典。"""
    input_dir = Path(input_dir)
    files0, dirs, scan_errors = _scan_entries(input_dir)

    files = []
    errors = list(scan_errors)
    degradations = []

    # 文本文件的内容缓存（用于 quotes 校验）由调用方在读文件时按需获取；
    # 这里只保存定位与可读性，不缓存正文，避免工作文件失控。
    file_records = []
    for i, entry in enumerate(files0, 1):
        rel = entry["rel_path"]
        if entry["kind"] == "unreadable":
            rec = {
                "id": f"F{i:03d}", "rel_path": rel, "name": os.path.basename(rel),
                "ext": os.path.splitext(rel)[1].lower(), "bytes": None,
                "sha256": None, "mode": None, "kind": "unreadable",
                "readable": False, "text_available": False,
                "text_encoding": None, "truncated": None,
                "unreadable_reason": entry.get("unreadable_reason", "unknown"),
            }
        else:
            rec = _process_file(input_dir, rel)
            rec["id"] = f"F{i:03d}"
        file_records.append(rec)

    # case.json / task.md 判定（大小写不敏感，优先根目录同名文件）
    case = {"path": None, "parsed": None, "error": None, "locator": None}
    task_locator = None
    for rec in file_records:
        name_l = rec["name"].lower()
        if name_l == "case.json" and case["path"] is None:
            case["locator"] = rec["rel_path"]
            case["path"] = input_dir / rec["rel_path"]
        elif name_l == "task.md" and task_locator is None:
            task_locator = rec["rel_path"]

    if case["path"] is not None:
        raw, _, err = read_bytes_limited(case["path"], MAX_TEXT_BYTES)
        if err:
            case["error"] = "read_failed:" + err
            errors.append({"kind": "case_unreadable", "locator": case["locator"], "detail": err})
        else:
            text, enc = decode_text(raw)
            try:
                case["parsed"] = json.loads(text)
            except Exception as e:  # noqa: BLE001
                case["error"] = "bad_json:" + str(e)
                errors.append({"kind": "bad_case_json", "locator": case["locator"], "detail": str(e)})

    case_id = derive_case_id(case["parsed"])
    contract_keys = extract_contract_keys(case["parsed"])
    meta_hints = extract_meta_hints(case["parsed"], file_records, input_dir, case["locator"], task_locator)
    questions = collect_questions(case["parsed"], meta_hints)

    # 分组：重复（相同 sha256）、版本（相同归一化 stem）、冲突
    analysis = build_analysis(file_records, meta_hints)

    return {
        "schema": SCHEMA,
        "engine_version": VERSION,
        "task_label": TASK_LABEL,
        "case_id": case_id,
        "case_locator": case["locator"],
        "case_error": case["error"],
        "task_locator": task_locator,
        "contract_keys": contract_keys,
        "directories": dirs,
        "files": file_records,
        "analysis": analysis,
        "meta_hints": meta_hints,
        "questions": questions,
        "errors": errors,
        "degradations": degradations,
    }


def derive_case_id(case) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "case"):
            v = case.get(key)
            if v not in (None, ""):
                return str(v)
    return "unknown"


def extract_contract_keys(case) -> list:
    if not isinstance(case, dict):
        return []
    raw = case.get("output_contract")
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    v = item.get(cand)
                    if isinstance(v, str):
                        keys.append(v)
                        break
    # 保持输入出现顺序，不排序。
    return keys


def _canonical_key(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s.lower())


def _meta_kind_for(key: str) -> str:
    c = _canonical_key(key)
    for kind, keys in (
        ("aliases", ALIAS_KEYS), ("versions", VERSION_KEYS),
        ("metadata", META_KEYS), ("queries", QUERY_KEYS),
        ("operations", OPERATION_KEYS),
    ):
        if any(_canonical_key(k) == c for k in keys):
            return kind
    return None


def extract_meta_hints(case, file_records, input_dir, case_locator, task_locator) -> list:
    """从 case.json 与文本材料中提取显式元数据提示（原始结构透传，不擅自裁定）。"""
    hints = []
    if isinstance(case, dict):
        for key, value in case.items():
            if key == "output_contract":
                continue
            kind = _meta_kind_for(key)
            if kind and value not in (None, "", [], {}):
                hints.append({
                    "kind": kind, "source": "case",
                    "locator": case_locator or "case.json",
                    "data": _json_safe(value),
                })

    # 扫描可读文本材料里含关键词的行，仅作提示。
    for rec in file_records:
        if not rec.get("text_available"):
            continue
        rel = rec["rel_path"]
        if rel in (case_locator, task_locator):
            continue
        path = input_dir / rel
        raw, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
        if err or not raw:
            continue
        text, _ = decode_text(raw)
        for i, line in enumerate(_split_lines(text), 1):
            line_s = line.strip()
            if not line_s or len(line_s) > 600:
                continue
            matched = False
            for pat in HINT_PATTERNS:
                if pat.search(line_s):
                    matched = True
                    break
            if matched:
                hints.append({
                    "kind": "text_hint", "source": rec["id"],
                    "locator": f"{rel}#L{i}", "data": line_s[:240],
                })
    return hints


def collect_questions(case, meta_hints) -> list:
    """从 case 与元数据提示中收集检索/问题条目（去重，保持出现顺序）。"""
    seen = set()
    questions = []

    def _add(qid, text, locator, src):
        if not text or text in seen:
            return
        seen.add(text)
        questions.append({"query_id": qid, "text": str(text), "source": src, "locator": locator})

    if isinstance(case, dict):
        for key in sorted(QUERY_KEYS):
            v = case.get(key) or case.get(key.title()) or case.get(key.upper())
            if v is None:
                continue
            if isinstance(v, str):
                _add(f"Q{len(seen) + 1}", v, "case.json", "case")
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        _add(f"Q{len(seen) + 1}", item, "case.json", "case")
                    elif isinstance(item, dict):
                        for field in ("text", "question", "query", "q"):
                            if isinstance(item.get(field), str):
                                _add(f"Q{len(seen) + 1}", item[field], "case.json", "case")
                                break

    for h in meta_hints:
        if h.get("kind") == "queries":
            data = h.get("data")
            if isinstance(data, str):
                _add(f"Q{len(seen) + 1}", data, h["locator"], h["source"])
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        _add(f"Q{len(seen) + 1}", item, h["locator"], h["source"])
                    elif isinstance(item, dict):
                        for field in ("text", "question", "query", "q"):
                            if isinstance(item.get(field), str):
                                _add(f"Q{len(seen) + 1}", item[field], h["locator"], h["source"])
                                break
    return questions


def _json_safe(value):
    """确保数据可 JSON 序列化；不可序列化的类型转 str。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def build_analysis(file_records, meta_hints) -> dict:
    """由确定性规则生成重复/版本/冲突分组与显式关系线索。"""
    readable = [r for r in file_records if r.get("sha256") and r["sha256"] != "hash_unavailable"]

    # 重复 / 别名候选：相同 sha256
    hash_map = {}
    for r in readable:
        hash_map.setdefault(r["sha256"], []).append(r["id"])
    alias_groups = []
    for h in sorted(hash_map.keys()):
        ids = sorted(hash_map[h])
        if len(ids) > 1:
            alias_groups.append({"member_ids": ids, "sha256": h})

    # 版本候选：归一化 stem 相同
    stem_map = {}
    for r in readable:
        stem = normalize_stem(r["name"])
        stem_map.setdefault(stem, []).append(r)
    version_groups = []
    for stem in sorted(stem_map.keys()):
        recs = sorted(stem_map[stem], key=lambda x: x["id"])
        ids = [r["id"] for r in recs]
        hashes = {r["sha256"] for r in recs}
        if len(ids) > 1 and len(hashes) > 1:
            version_groups.append({
                "stem": stem, "member_ids": ids,
                "sha256_map": {r["id"]: r["sha256"] for r in recs},
            })

    # 冲突：相同 stem、不同 hash、且无显式版本/别名关系
    explicit_targets = set()
    explicit_relations = []
    for h in meta_hints:
        if h["kind"] in ("aliases", "versions"):
            explicit_relations.append({
                "relation": "alias" if h["kind"] == "aliases" else "version",
                "source": h["source"], "locator": h["locator"],
                "data": h.get("data"),
            })
            targets = _collect_target_ids(h.get("data"))
            explicit_targets.update(targets)

    conflicts = []
    for g in version_groups:
        involved = set(g["member_ids"])
        if involved & explicit_targets:
            continue
        conflicts.append({
            "member_ids": g["member_ids"], "stem": g["stem"],
            "reason": "same_stem_diff_sha256_no_explicit_relation",
        })

    return {
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
        "explicit_relations": explicit_relations,
        "hash_counts": {h: len(v) for h, v in hash_map.items() if len(v) > 1},
    }


def _collect_target_ids(data) -> set:
    """从显式关系数据里尽可能收集文件 ID / 文件名字符串。"""
    out = set()
    if isinstance(data, dict):
        for v in data.values():
            out.update(_collect_target_ids(v))
    elif isinstance(data, list):
        for v in data:
            out.update(_collect_target_ids(v))
    elif isinstance(data, str):
        m = re.search(r"\bF\d{3}\b", data)
        if m:
            out.add(m.group(0))
    return out


# ------------------------------------------------------------------ 证据与检索

def _file_text(input_dir, rec):
    if not rec.get("text_available"):
        return None
    path = input_dir / rec["rel_path"]
    raw, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err or not raw:
        return None
    text, _ = decode_text(raw)
    return text


def build_index(input_dir, file_records):
    """词级检索索引：token -> [(file_id, line_no, line)]。"""
    index = {}
    for rec in file_records:
        if not rec.get("text_available"):
            continue
        text = _file_text(input_dir, rec)
        if text is None:
            continue
        for i, line in enumerate(_split_lines(text), 1):
            line_s = line.strip()
            if not line_s:
                continue
            for tok in normalize_tokens(line_s):
                index.setdefault(tok, set()).add((rec["id"], i, line_s[:240]))
    return index


def search_lines(index, query, limit=8):
    """简单雏形检索：任一查询词命中即返回额度内结果，附加分数（命中词数）。"""
    tokens = normalize_tokens(query)
    if not tokens:
        return []
    hits = {}
    for tok in tokens:
        for (fid, ln, line) in index.get(tok, ()):
            key = (fid, ln)
            hits[key] = hits.get(key, 0) + 1
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    out = []
    for (fid, ln), score in ranked[:limit]:
        line = ""
        for t in tokens:
            pass
        # 从 index 里取回行文本
        for (f2, l2, linetxt) in index.get(tokens[0], ()):
            if (f2, l2) == (fid, ln):
                line = linetxt
                break
        out.append({"source_id": fid, "locator": f"{fid}:{ln}", "line": ln, "score": score, "quote": line})
    return out


def find_file_by_id(library, source_id):
    for f in library["files"]:
        if f["id"] == source_id:
            return f
    return None


def _locator_path(locator: str) -> str:
    """从 locator（可能带 #L<n> 或 :<n> 片段）中提取文件路径部分。"""
    if not locator:
        return ""
    p = locator.split("#", 1)[0]
    p = re.sub(r":\d+$", "", p)
    return p or locator


def verify_evidence(input_dir, library, evidence) -> dict:
    """校验 single evidence：source_id 存在、locator 存在、quote 逐字命中。"""
    source_id = evidence.get("source_id", "")
    locator = evidence.get("locator", "")
    quote = evidence.get("quote", "")
    if source_id in ("case", "task"):
        base = "case.json" if source_id == "case" else (library.get("task_locator") or "task.md")
        target = _locator_path(locator) or base
        path = input_dir / target if (input_dir / target).exists() else None
        ok_id = True
    else:
        rec = find_file_by_id(library, source_id)
        target = _locator_path(locator) or (rec["rel_path"] if rec else "")
        path = input_dir / target if (input_dir / target).exists() else None
        ok_id = rec is not None
    ok_loc = path is not None and path.exists()
    ok_quote = None
    if path is not None and path.exists():
        file_rec = find_file_by_id(library, source_id) if source_id not in ("case", "task") else None
        if source_id in ("case", "task"):
            text, _ = decode_text(read_bytes_limited(path, MAX_TEXT_BYTES)[0])
            ok_quote = contains_quote(text, quote) if quote else "empty"
        elif file_rec and file_rec.get("text_available"):
            text = _file_text(input_dir, file_rec)
            ok_quote = contains_quote(text or "", quote) if quote else "empty"
        else:
            ok_quote = "no_text_layer"
    verdicts = {
        "source_id": ok_id, "locator": ok_loc, "quote": ok_quote,
    }
    bad = []
    if not ok_id:
        bad.append("unknown_source_id")
    if not ok_loc:
        bad.append("missing_locator")
    if ok_quote is False:
        bad.append("quote_not_found")
    return {"evidence": evidence, "verdicts": verdicts, "bad": bad, "verified": not bad}


# ------------------------------------------------------------------ result 装配

def build_skeleton(case_id, contract_keys, status="partial") -> dict:
    result = {}
    for key in contract_keys:
        result[key] = None
    for key in ENVELOPE_KEYS:
        if key in result:
            continue
        result[key] = [] if key in LIST_ENVELOPE_KEYS else None
    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {}
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    return result


def _evidence_entry(source_id, locator, quote):
    return {"source_id": source_id, "locator": locator, "quote": truncate_quote(quote)}


def _evidence_for_ids(library, *ids):
    out = []
    for i in ids:
        f = find_file_by_id(library, i)
        if f:
            out.append(_evidence_entry(i, f["rel_path"], f["rel_path"]))
    return out


def _line_quote(input_dir, library, source_id, line_no):
    rec = find_file_by_id(library, source_id)
    if not rec or not rec.get("text_available"):
        return None, False
    path = input_dir / rec["rel_path"]
    raw, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err or not raw:
        return None, False
    text, _ = decode_text(raw)
    lines = _split_lines(text)
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip(), True
    return None, False


def build_claims(library, input_dir, draft) -> list:
    claims = []
    n = 0
    analysis = library.get("analysis", {})

    for g in analysis.get("alias_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "directly_read", "confidence": "high",
            "text": f"文件 {', '.join(g['member_ids'])} 的 sha256 相同，为同一内容的重复副本/别名",
            "relation": "duplicate",
            "evidence": _evidence_for_ids(library, *g["member_ids"]),
        })
    for g in analysis.get("version_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "inferred", "confidence": "heuristic",
            "text": f"文件 {', '.join(g['member_ids'])} 归一化 stem 相同但哈希不同，为同一文献的候选版本",
            "relation": "candidate_version",
            "evidence": _evidence_for_ids(library, *g["member_ids"]),
        })
    for g in analysis.get("conflicts", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "inferred", "confidence": "heuristic",
            "text": f"文件 {', '.join(g['member_ids'])} stem 相同但哈希不同且无显式版本标记，存在来源冲突，待人工裁定",
            "relation": "source_conflict",
            "evidence": _evidence_for_ids(library, *g["member_ids"]),
        })
    for rel in analysis.get("explicit_relations", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "directly_read", "confidence": "explicit",
            "text": "显式关系：" + json.dumps(rel, ensure_ascii=False),
            "relation": rel.get("relation"),
            "evidence": [{"source_id": "case", "locator": rel.get("locator", "case.json"),
                          "quote": json.dumps(rel, ensure_ascii=False)}],
        })

    # draft 语义裁定（模型写入）
    notes = (draft or {}).get("notes", [])
    for note in notes:
        n += 1
        targets = note.get("target_ids") or note.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        ev = list(note.get("evidence", []))
        if not ev and targets:
            ev = _evidence_for_ids(library, *targets)
        claims.append({
            "claim_id": f"C{n:03d}",
            "kind": note.get("kind", "correction"),
            "confidence": note.get("confidence", "inferred"),
            "text": note.get("text") or note.get("basis") or "",
            "relation": note.get("relation"),
            "evidence": [{"source_id": e.get("source_id"), "locator": e.get("locator"),
                          "quote": truncate_quote(e.get("quote", ""))} for e in ev],
        })

    for q in (draft or {}).get("queries", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}",
            "kind": "query_answer",
            "confidence": q.get("confidence", "inferred"),
            "text": f"[{q.get('query_id', '?')}] {q.get('answer', '')}",
            "relation": "retrieval",
            "evidence": [{"source_id": e.get("source_id"), "locator": e.get("locator"),
                          "quote": truncate_quote(e.get("quote", ""))} for e in q.get("evidence", [])],
        })
    return claims


def build_sources(library) -> list:
    out = []
    for f in library["files"]:
        out.append({
            "source_id": f["id"], "rel_path": f["rel_path"], "name": f["name"],
            "bytes": f["bytes"], "sha256": f["sha256"], "mode": f["mode"],
            "kind": f["kind"], "readable": f["readable"],
            "text_available": f["text_available"],
        })
    if library.get("case_locator"):
        out.append({"source_id": "case", "rel_path": library["case_locator"], "name": "case.json",
                    "kind": "case_input", "readable": True})
    if library.get("task_locator"):
        out.append({"source_id": "task", "rel_path": library["task_locator"], "name": "task.md",
                    "kind": "task_input", "readable": True})
    return out


def build_uncertainty(library, draft) -> dict:
    items = []
    for g in library.get("analysis", {}).get("version_groups", []):
        items.append({"type": "heuristic_version_group", "member_ids": g["member_ids"], "stem": g["stem"]})
    for g in library.get("analysis", {}).get("conflicts", []):
        items.append({"type": "source_conflict", "member_ids": g["member_ids"], "stem": g["stem"]})
    for h in library.get("meta_hints", []):
        if h.get("kind") == "text_hint":
            items.append({"type": "text_hint_unadjudicated", "locator": h["locator"], "quote": h.get("data", "")[:120]})
    summary_parts = []
    if items and library.get("case_error") is None and not draft:
        summary_parts.append("存在未裁定冲突/启发式分组，且未提供 draft")
    return {"summary": "；".join(summary_parts), "count": len(items), "items": items}


def build_change_log(library, draft) -> list:
    log = []
    notes = (draft or {}).get("notes", [])
    for i, note in enumerate(notes, 1):
        if note.get("kind") in ("correction", "alias", "version"):
            log.append({
                "op": f"adjudicate_{note.get('kind', 'correction')}",
                "before": (library.get("analysis", {}).get("conflicts") and "heuristic_group") or "none",
                "after": note.get("relation") or note.get("text", "")[:160],
                "reason": note.get("basis", "")[:240],
                "locator": (note.get("evidence") or [{}])[0].get("locator", ""),
            })
    return log


def build_pending_human(library, draft) -> list:
    out = []
    for g in library.get("analysis", {}).get("conflicts", []):
        resolved = False
        for note in (draft or {}).get("notes", []):
            t = note.get("target_ids") or note.get("targets") or []
            if set(map(str, t)) & set(g["member_ids"]):
                resolved = True
                break
        if not resolved:
            out.append({"type": "source_conflict", "member_ids": g["member_ids"],
                        "reason": "stem 相同、哈希不同、无显式版本标记", "action": "人工裁定主版本与别名关系"})
    for f in library["files"]:
        if not f.get("readable"):
            out.append({"type": "unreadable_file", "source_id": f["id"],
                        "locator": f["rel_path"], "reason": f.get("unreadable_reason")})
    return out


def build_result_summary(library, draft) -> dict:
    analysis = library.get("analysis", {})
    kind_counts = {}
    for f in library["files"]:
        kind_counts[f["kind"]] = kind_counts.get(f["kind"], 0) + 1
    queries = library.get("questions", [])
    answered = 0
    for q in (draft or {}).get("queries", []):
        if q.get("status") == "answered":
            answered += 1
    return {
        "files_total": len(library["files"]),
        "directories_total": len(library["directories"]),
        "files_by_kind": dict(sorted(kind_counts.items())),
        "duplicate_groups": len(analysis.get("alias_groups", [])),
        "version_groups": len(analysis.get("version_groups", [])),
        "conflicts": len(analysis.get("conflicts", [])),
        "explicit_relations": len(analysis.get("explicit_relations", [])),
        "queries_total": len(queries),
        "queries_answered": answered,
        "unreadable_files": len([f for f in library["files"] if not f.get("readable")]),
        "deliverables": ["library_index.json", "evidence_map.json", "inventory.md"],
    }


def determine_status(library, draft, errors) -> str:
    if not library.get("case_locator") and not library["files"]:
        return "insufficient_evidence"
    if library.get("case_error"):
        return "input_error"
    if library.get("case_locator") is None and library["files"]:
        # 有材料但缺 case.json
        return "input_error"
    if any(e.get("kind") == "missing_input_dir" for e in errors):
        return "insufficient_evidence"
    if not draft:
        return "partial"
    if library["analysis"]["conflicts"] or any(not f.get("readable") for f in library["files"]):
        return "partial"
    return "ok"


# ------------------------------------------------------------------ 主装配

def assemble_result(input_dir, library, draft, contract_keys, extra_errors=None, runtime=None) -> dict:
    """装配最终 result.json（确定性；runtime 字段允许随环境变化）。"""
    case_id = library.get("case_id") or derive_case_id(None)
    result = build_skeleton(case_id, contract_keys, "partial")
    merged_errors = list(library.get("errors", [])) + list(extra_errors or [])
    status = determine_status(library, draft, merged_errors)

    result["case_id"] = case_id
    result["status"] = status
    result["result"] = build_result_summary(library, draft)
    result["claims"] = build_claims(library, input_dir, draft)
    result["uncertainties"] = build_uncertainty(library, draft)["items"]
    result["sources"] = build_sources(library)
    result["uncertainty"] = build_uncertainty(library, draft)
    result["change_log"] = build_change_log(library, draft)
    result["pending_human"] = build_pending_human(library, draft)
    result["errors"] = merged_errors
    result["degradations"] = list(library.get("degradations", []))
    result["runtime"] = runtime or {}
    return result


def build_library_index(library, draft) -> dict:
    """library_index.json：完整盘点 + 分析，不含 runtime，确定性。"""
    return {
        "schema": library["schema"],
        "engine_version": library["engine_version"],
        "task_label": library["task_label"],
        "case_id": library["case_id"],
        "case_locator": library["case_locator"],
        "task_locator": library["task_locator"],
        "directories": list(library["directories"]),
        "files": [dict(f) for f in library["files"]],
        "analysis": library["analysis"],
        "questions": library["questions"],
        "meta_hints": library["meta_hints"],
        "draft_summary": {
            "note_count": len((draft or {}).get("notes", [])),
            "query_count": len((draft or {}).get("queries", [])),
        },
        "errors": library["errors"],
        "degradations": library["degradations"],
    }


def build_evidence_map(library, claims) -> dict:
    out = {}
    for c in claims:
        out[c["claim_id"]] = {
            "claim_id": c["claim_id"],
            "text": c.get("text", ""),
            "evidence": c.get("evidence", []),
        }
    return {"schema": SCHEMA, "engine_version": VERSION, "claims": out}


def build_inventory_md(library, result) -> str:
    lines = []
    lines.append("# 科研文档库盘点交接清单")
    lines.append("")
    lines.append(f"- case_id：`{library.get('case_id')}`")
    lines.append(f"- 状态：`{result.get('status')}`")
    lines.append(f"- 引擎：core_impl v{VERSION}（确定性，无时间戳）")
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| ID | 相对路径 | 类型 | 大小 | 权限 | sha256 前 12 | 可读 | 文本层 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for f in library["files"]:
        h = (f.get("sha256") or "")[:12]
        lines.append(
            f"| {f['id']} | `{f['rel_path']}` | {f['kind']} | "
            f"{'' if f['bytes'] is None else f['bytes']} | {f.get('mode') or '-'} | "
            f"{h or '-'} | {'是' if f.get('readable') else '否'} | "
            f"{'是' if f.get('text_available') else '否'} |"
        )
    lines.append("")
    lines.append("## 分组与关系")
    lines.append("")
    a = library.get("analysis", {})
    for g in a.get("alias_groups", []):
        lines.append(f"- 重复/别名：{', '.join(g['member_ids'])}（sha256={g['sha256'][:12]}…）")
    for g in a.get("version_groups", []):
        lines.append(f"- 候选版本：{', '.join(g['member_ids'])}（stem=`{g['stem']}`）")
    for g in a.get("conflicts", []):
        lines.append(f"- 冲突待裁定：{', '.join(g['member_ids'])}（stem=`{g['stem']}`）")
    for rel in a.get("explicit_relations", []):
        lines.append(f"- 显式关系：{json.dumps(rel, ensure_ascii=False)}")
    lines.append("")
    lines.append("## 检索问题")
    lines.append("")
    for q in library.get("questions", []):
        lines.append(f"- {q['query_id']}：{q['text']}")
    lines.append("")
    lines.append("## 待人工 / 缺口")
    lines.append("")
    for p in result.get("pending_human", []):
        lines.append(f"- {json.dumps(p, ensure_ascii=False)}")
    lines.append("")
    lines.append("## 降级与错误")
    lines.append("")
    for e in result.get("errors", []):
        lines.append(f"- ERROR {json.dumps(e, ensure_ascii=False)}")
    for d in result.get("degradations", []):
        lines.append(f"- DEGRADE {json.dumps(d, ensure_ascii=False)}")
    lines.append("")
    return "\n".join(lines)