#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""libcore.py — 确定性盘点与分析引擎（纯标准库）。

本模块只做"可机械复现"的确定性工作：
- 递归扫描输入目录，计算 sha256 / 大小 / 权限 / 修改时间等原始元数据；
- 按归一化 stem 归组候选版本，按同哈希归组重复副本/别名；
- 读取显式关系、元数据与检索问题（来自 case.json 或材料文件）；
- 建立可检索的文本索引（仅对可靠文本编码的文件，超限跳过）；
- 生成带 locator 的盘点结构与机械性 claim。

语义解释（行政版本备注、别名裁定、检索答案、启发式分组修正）由模型
在 draft.json 中补充，每条带 source_id + locator + 逐字 quote。
本模块不执行 OCR、PDF 文本层抽取或语义判断，缺工具时如实降级。
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path

# 单文件文本索引大小上限（字节）；超过则只做字节级盘点。
TEXT_INDEX_MAX = 2_000_000
# 全量文件哈希上限（字节）；超过仍记录大小与状态，但不计算哈希，防止卡死。
HASH_MAX = 512_000_000

# 可安全按文本读取并进入检索索引的后缀（小写）。
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".xml",
    ".html", ".htm", ".yaml", ".yml", ".log", ".bib", ".tex",
    ".rst", ".ini", ".cfg", ".toml", ".py", ".sql",
}

# 归类为 PDF/扫描件的后缀。
PDF_EXTENSIONS = {".pdf"}
# 归类为办公文档的后缀。
OFFICE_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
# 归类为图片/扫描件的后缀。
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp"}
# 归档压缩后缀。
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"}

# 用于从文件名中剥离版本/日期/副本噪声标记的正则（大小写不敏感）。
# 匹配独立分隔符分隔的噪声 token，不吞掉正文；括号内的内容作为整体匹配。
_VERSION_NOISE_RE = re.compile(
    r"(?i)"
    r"(?:[-_.\s]+(?:v|ver|version|rev|revision)[-_.]?\d+(?:\.\d+)*"
    r"|[-_.\s]+(?:final|draft|copy|clean|old|new|bak|backup|tmp|temp)"
    r"|[-_.\s]+(?:副本|终稿|定稿|草稿|最终版|修改版)"
    r"|[-_.\s]+\d{4}[-_.]?\d{2}[-_.]?\d{2}"
    r"|[-_.\s]+\d{8,}"
    r"|\s*\(\d+\)"
    r"|[-_.\s]+copy\s+of)"
)
_MULTISPACE_RE = re.compile(r"[\s_]+")

FILE_IDS = "files"
ANALYSIS_IDS = "analysis"


def _sha256_of(path: Path, max_bytes: int = HASH_MAX) -> tuple:
    """返回 (hex_digest 或 None, 状态)。超限/不可读时状态非 ok。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"stat_error:{exc}"
    if size > max_bytes:
        return None, "hash_skipped_oversize"
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), "ok"
    except OSError as exc:
        return None, f"unreadable:{exc}"
    except Exception as exc:  # 兜底：任何读失败都不应崩溃
        return None, f"read_error:{exc}"


def _read_text(path: Path, max_bytes: int = TEXT_INDEX_MAX) -> tuple:
    """容错读取文本：优先 utf-8-sig，失败回退 latin-1 并标记编码降级。

    返回 (text 或 None, 状态字符串)。
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"stat_error:{exc}"
    if size > max_bytes:
        return None, "text_skipped_oversize"
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            raw = path.read_text(encoding=enc)
            if enc in ("gb18030", "latin-1"):
                return raw, f"decoded_{enc}"
            return raw, "ok"
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, f"unreadable:{exc}"
    return None, "undecodable"


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in OFFICE_EXTENSIONS:
        return "office"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix == "":
        return "text"  # 无扩展名按文本尝试（README、Makefile 等）
    return "other"


def _perms_of(path: Path) -> str:
    try:
        mode = path.stat().st_mode
        return oct(stat.S_IMODE(mode))[2:].zfill(4)
    except OSError:
        return "unknown"


def normalize_stem(name: str) -> str:
    """归一化文件名 stem，用于候选版本分组（确定性、无时间戳）。

    仅移除版本/日期/副本噪声标记并把空白折叠为单空格，不改变大小写内容。
    """
    base = Path(name).stem
    base = _VERSION_NOISE_RE.sub(" ", base)
    base = _MULTISPACE_RE.sub(" ", base)
    return base.strip().lower()


def discover_files(input_dir: Path) -> tuple:
    """扫描输入目录，返回 (files, errors)。

    files 为按 rel_path 排序后的字典列表；id 以 F001 稳定编号，保证重复运行一致。
    case.json / task.md 会被识别为输入元数据而非普通材料（source 字段区分）。
    """
    files = []
    errors = []
    if not input_dir.exists():
        return files, [{"kind": "input_missing", "detail": str(input_dir)}]
    try:
        entries = sorted(
            (p for p in input_dir.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(input_dir).as_posix(),
        )
    except OSError as exc:
        return files, [{"kind": "scan_error", "detail": str(exc)}]

    for idx, path in enumerate(entries, start=1):
        rel = path.relative_to(input_dir).as_posix()
        if rel in ("case.json", "task.md"):
            # 输入元数据仍盘点，便于溯源，但不计入材料分组。
            source = "input_meta"
        else:
            source = "material"
        digest, hash_status = _sha256_of(path)
        text, text_status = None, "not_text_indexed"
        if _file_kind(path) == "text":
            text, text_status = _read_text(path)
        files.append({
            "id": f"F{idx:03d}",
            "rel_path": rel,
            "source": source,
            "kind": _file_kind(path),
            "size": path.stat().st_size if path.exists() else None,
            "sha256": digest,
            "hash_status": hash_status,
            "text_status": text_status,
            "perms": _perms_of(path),
        })
    return files, errors


def _json_any(path: Path) -> dict:
    """容错读取 JSON；失败返回带 error 标记的占位 dict。"""
    if not path.exists():
        return {"error": f"missing:{path.name}"}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError, OSError) as exc:
        return {"error": f"invalid_json:{exc}"}


def load_case(input_dir: Path) -> dict:
    """读取 case.json（可能缺失/坏 JSON），返回 dict。"""
    case = _json_any(input_dir / "case.json")
    if "error" in case and not case.get("case_id"):
        return {"error": case["error"]}
    return case


def extract_contract(case: dict) -> list:
    """从 case.json 提取 output_contract 的字段名，支持 dict / list 两种形态。"""
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand]:
                        keys.append(item[cand])
                        break
    # 去重且保序
    seen = set()
    ordered = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def extract_relations(case: dict) -> list:
    """从 case.json 读取显式关系（alias/version/关系/别名/版本 等键，形态容错）。"""
    out = []
    if not isinstance(case, dict):
        return out
    for key in ("relations", "explicit_relations", "aliases", "alias",
                "versions", "version_notes", "relationships"):
        val = case.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    out.append({"source": "case.json", "key": key, "record": item})
                elif isinstance(item, str):
                    out.append({"source": "case.json", "key": key, "record": item})
        elif isinstance(val, dict):
            out.append({"source": "case.json", "key": key, "record": val})
    return out


def extract_queries(case: dict) -> list:
    """从 case.json 读取检索问题/任务问题，返回带 query_id 的列表。"""
    out = []
    if not isinstance(case, dict):
        return out
    for key in ("queries", "questions", "search_queries", "tasks"):
        val = case.get(key)
        if isinstance(val, list):
            for i, item in enumerate(val, start=1):
                if isinstance(item, str):
                    out.append({"query_id": f"Q{i}", "text": item})
                elif isinstance(item, dict):
                    qid = item.get("id") or item.get("query_id") or f"Q{i}"
                    text = item.get("query") or item.get("text") or item.get("question") or ""
                    out.append({"query_id": str(qid), "text": str(text)})
        elif isinstance(val, str):
            out.append({"query_id": "Q1", "text": val})
    return out


def _build_text_index(files: list, input_dir: Path) -> dict:
    """构建确定性检索索引：rel_path -> {id, tokens, text_excerpt}。"""
    index = {}
    for f in files:
        if f.get("source") != "material":
            continue
        if f.get("kind") != "text":
            continue
        path = input_dir / f["rel_path"]
        text, status = _read_text(path)
        if text is None:
            continue
        tokens = _tokenize(text)
        index[f["rel_path"]] = {
            "id": f["id"],
            "token_count": len(tokens),
            "text_status": status,
            "excerpt": text[:2000],
        }
    return index


_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list:
    return _TOKEN_RE.findall(text.lower())


def search_index(index: dict, query: str) -> list:
    """确定性关键词检索：所有 query token 命中的文件按命中频次降序。

    返回 [{"id","rel_path","matched_terms","score"}]，无命中为空列表。
    """
    q_tokens = _tokenize(query or "")
    if not q_tokens:
        return []
    hits = []
    for rel_path, rec in index.items():
        text = rec.get("excerpt", "")
        matched = [t for t in q_tokens if t in text]
        if not matched:
            continue
        score = sum(text.count(t) for t in matched)
        hits.append({
            "id": rec["id"],
            "rel_path": rel_path,
            "matched_terms": matched,
            "score": score,
        })
    hits.sort(key=lambda h: (-h["score"], h["rel_path"]))
    return hits


def build_analysis(files: list, case: dict) -> dict:
    """基于确定性信号生成机械分析：重复、候选版本、冲突、显式关系、检索问题。"""
    by_hash = {}
    for f in files:
        if f.get("sha256"):
            by_hash.setdefault(f["sha256"], []).append(f["id"])

    alias_groups = [
        {"member_ids": ids, "sha256": h}
        for h, ids in sorted(by_hash.items()) if len(ids) > 1
    ]

    by_stem = {}
    for f in files:
        if f.get("source") != "material":
            continue
        stem = normalize_stem(f["rel_path"])
        by_stem.setdefault(stem, []).append(f["id"])

    version_groups = []
    conflicts = []
    for stem, ids in sorted(by_stem.items()):
        if len(ids) < 2:
            continue
        digests = {f["sha256"] for f in files if f["id"] in ids and f.get("sha256")}
        if len(digests) > 1:
            conflicts.append({"member_ids": ids, "stem": stem})
        else:
            version_groups.append({"member_ids": ids, "stem": stem})

    return {
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
        "explicit_relations": extract_relations(case),
        "queries": extract_queries(case),
        "normalized_stems": {
            f["id"]: normalize_stem(f["rel_path"])
            for f in files if f.get("source") == "material"
        },
    }


def build_library(input_dir: Path, case: dict) -> dict:
    """一次性完成盘点与确定性分析，返回 library 结构。"""
    files, errors = discover_files(input_dir)
    analysis = build_analysis(files, case)
    index = _build_text_index(files, input_dir)
    return {
        "files": files,
        "analysis": analysis,
        "index": index,
        "errors": errors,
        "case": {k: v for k, v in case.items() if k != "error"},
        "case_parse": "ok" if "error" not in case else case.get("error"),
        "case_json_present": (input_dir / "case.json").exists(),
    }
