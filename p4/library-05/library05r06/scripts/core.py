#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — 科研文件库修复 Skill 的确定性引擎。

纯标准库实现，功能边界：
  1) 发现（discover）：遍历 /input，记录文件、目录、权限、哈希、类型/编码探测；
  2) 分析（analyze）：哈希去重、归一化 stem 版本候选、无版本标记冲突、别名候选；
  3) 索引（index_term）：对文件名与文本文件内容建检索索引；
  4) 契约/中间表示：output_contract 逐键回填、plan.json / model.json 模板；
  5) 校验（validate_model）：模型结构与证据溯源机械校验；
  6) 交付（build/selfcheck）：合并确定性结果与模型，收口 result.json 及可复核产物。

确定性约定：同输入两次运行，除 runtime 字段外逐字节一致；json 统一
ensure_ascii=False + indent=2 + sort_keys=True；所有定位用 rel_path，不出绝对路径。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

SKILL_VERSION = "1.2.0"
MODEL_VERSION = "1.2.0"

MAX_HASH_BYTES = 32 * 1024 * 1024        # 超过则不再计算完整哈希（防超时/超大文件）
MAX_TEXT_BYTES = 4 * 1024 * 1024         # 文本文件全文读取上限
MAX_PREVIEW_BYTES = 8192                 # 预览/嗅探读取长度

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".tsv",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".py", ".js", ".ts",
    ".r", ".rmd", ".tex", ".bib", ".log", ".ini", ".cfg", ".conf", ".toml",
    ".sql", ".sh", ".bat", ".ps1",
}

BINARY_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svgz",
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".bin",
    ".mov", ".mp4", ".mp3", ".wav", ".avi", ".flac", ".ogg",
}

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_NOTE_KINDS = {"alias", "version", "metadata", "correction", "query_answer",
                      "annotation", "recovery", "index_override", "general"}
ALLOWED_NOTE_CONFIDENCE = {"explicit", "inferred", "heuristic"}
ALLOWED_QUERY_STATUS = {"answered", "no_direct_match", "insufficient_evidence"}
ALLOWED_RELATION_TYPES = {"alias", "version", "conflict", "duplicate", "link", "ref"}

# --------------------------------------------------------------------------- IO


def read_bytes(path: Path, limit: int | None = None):
    """读取原始字节；失败返回 None。"""
    try:
        with open(path, "rb") as fh:
            return fh.read(limit) if limit is not None else fh.read()
    except OSError:
        return None


def decode_text(data: bytes):
    """按 BOM/utf-8/utf-16-*/latin-1 顺序容错解码，返回 (text, encoding)。"""
    if data.startswith(b"\xff\xfe"):
        try:
            return data[2:].decode("utf-16-le"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    if data.startswith(b"\xfe\xff"):
        try:
            return data[2:].decode("utf-16-be"), "utf-16-be"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    try:
        return data.decode("latin-1"), "latin-1"
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), "utf-8(replace)"


def load_json(path: Path, default=None):
    """容错读取 JSON；坏 JSON/坏编码不抛异常。"""
    if default is None:
        default = {}
    data = read_bytes(path)
    if data is None:
        return default, "unreadable"
    text, _ = decode_text(data)
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - 降级不崩溃
        return default, f"invalid_json:{type(exc).__name__}"


def write_json(path: Path, obj, logger=None):
    """确定性地写出 JSON（sort_keys + ensure_ascii=False）。失败降级。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return None
    except OSError as exc:
        if logger is not None:
            logger.append({"kind": "write_failed", "locator": str(path),
                           "message": f"{type(exc).__name__}: {exc}"})
        return str(exc)


def sha256_file(path: Path):
    """流式计算 sha256；超过上限或不可读时返回 (None, size, status)。"""
    h = hashlib.sha256()
    size = 0
    status = None
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
                if size > MAX_HASH_BYTES:
                    status = "hash_skipped_oversize"
                    break
    except OSError:
        return None, None, "unreadable"
    if status:
        return None, size, status
    return h.hexdigest(), size, None


def perm_string(st_mode: int) -> str:
    """把 stat 模式转成 rwxrwxrwx 字符串。"""
    m = st_mode & 0o777
    out = []
    for mask in (0o400, 0o200, 0o100, 0o040, 0o020, 0o010, 0o004, 0o002, 0o001):
        letter = "r" if mask in (0o400, 0o040, 0o004) else \
                 "w" if mask in (0o200, 0o020, 0o002) else "x"
        out.append(letter if (m & mask) else "-")
    return "".join(out)


_EXT_MIME = {
    ".md": "text/markdown", ".txt": "text/plain", ".json": "application/json",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".yaml": "text/yaml", ".yml": "text/yaml", ".xml": "text/xml",
    ".html": "text/html", ".htm": "text/html", ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".zip": "application/zip", ".gz": "application/gzip", ".pdf": "application/pdf",
}


def guess_mime(name: str, kind: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    return "text/plain" if kind == "text" else "application/octet-stream"


# --------------------------------------------------------------------------- 发现


def discover(input_dir: Path):
    """遍历输入目录，产出 (dirs, files, errors, degradations)。"""
    dirs, files, errors, degradations = [], [], [], []
    if not input_dir.exists():
        errors.append({"kind": "missing_input_dir", "locator": input_dir.as_posix(),
                       "message": "输入目录不存在"})
        return dirs, files, errors, degradations
    try:
        all_paths = sorted([p for p in input_dir.rglob("*")],
                           key=lambda p: p.relative_to(input_dir).as_posix())
    except OSError as exc:
        errors.append({"kind": "walk_failed", "locator": input_dir.as_posix(),
                       "message": f"{type(exc).__name__}: {exc}"})
        return dirs, files, errors, degradations

    for p in all_paths:
        rel = p.relative_to(input_dir).as_posix()
        # case.json 与 task.md 属任务契约，不纳入待修复资料盘点
        if rel in ("case.json", "task.md"):
            continue
        try:
            if p.is_symlink():
                degradations.append({"kind": "symlink_skipped", "locator": rel,
                                     "message": "软链接跳过，不计入盘点"})
                continue
            if p.is_dir():
                dirs.append({"rel_path": rel, "name": p.name})
                continue
            if p.is_file():
                st = os.stat(p)
                digest, size, hash_status = sha256_file(p)
                preview = read_bytes(p, MAX_PREVIEW_BYTES)
                ext = p.suffix.lower()
                if ext in TEXT_EXTENSIONS:
                    kind = "text"
                    encoding = None
                    if preview is not None:
                        _, encoding = decode_text(preview)
                elif ext in BINARY_EXTENSIONS:
                    kind = "binary"
                    encoding = None
                else:
                    # 未知扩展名：按是否含 NUL 字节嗅探
                    kind = "text"
                    encoding = None
                    if preview is not None and b"\x00" in preview:
                        kind = "binary"
                    elif preview is not None:
                        try:
                            preview.decode("utf-8-sig")
                            _, encoding = decode_text(preview)
                        except UnicodeDecodeError:
                            kind = "binary"
                files.append({
                    "id": None,  # 稍后按 rel_path 排序统一赋值
                    "rel_path": rel,
                    "name": p.name,
                    "dir": p.parent.relative_to(input_dir).as_posix() or ".",
                    "size": size,
                    "sha256": digest,
                    "hash_status": hash_status,
                    "ext": ext,
                    "mime": guess_mime(p.name, kind),
                    "kind": kind,
                    "encoding": encoding,
                    "permissions": perm_string(st.st_mode),
                    "source": "input",
                })
        except OSError as exc:
            errors.append({"kind": "stat_failed", "locator": rel,
                           "message": f"{type(exc).__name__}: {exc}"})

    # 目录：补充 file_count，并赋 id
    file_ids_by_rel = {}
    for i, f in enumerate(sorted(files, key=lambda x: x["rel_path"])):
        f["id"] = "F%04d" % (i + 1)
        file_ids_by_rel[f["rel_path"]] = f["id"]
    dir_id = 0
    for d in sorted(dirs, key=lambda x: x["rel_path"]):
        dir_id += 1
        d["id"] = "D%04d" % dir_id
        prefix = d["rel_path"] + "/"
        d["file_count"] = sum(1 for f in files if f["rel_path"].startswith(prefix))
    return dirs, files, errors, degradations


# --------------------------------------------------------------------------- 分析


_VSTRIP_TAIL = re.compile(
    r"(\s*[\(\[]?(?:copy|副本|拷贝|duplicate|final|draft|v\d+(?:\.\d+)*|"
    r"version\s*\d*|rev\s*\d*|修订|初稿|定稿|backup|old|new)[\)\]]?|\s*\(\d+\)|\s*[-_]\d+)$",
    re.IGNORECASE,
)


def normalize_stem(rel_path: str) -> str:
    """归一化文件 stem，用于版本/重复候选分组。保守剔除复制/版本后缀。"""
    name = Path(rel_path).name
    stem = Path(name).stem
    lower = stem.lower()
    lower = re.sub(r"_+", " ", lower)
    lower = re.sub(r"\s+", " ", lower).strip()
    prev = None
    while lower and lower != prev:
        prev = lower
        lower = _VSTRIP_TAIL.sub("", lower).strip()
    return lower or prev or stem


def has_version_marker(name: str) -> bool:
    lower = name.lower()
    return bool(re.search(
        r"(v\d|version|rev|draft|final|初稿|定稿|修订|副本|copy|\([0-9]\)|[-_][0-9]+)",
        lower,
    ))


def analyze(dirs, files):
    """基于发现结果做机械分组，产出 analysis dict。"""
    files_by_id = {f["id"]: f for f in files}
    by_sha = {}
    for f in files:
        if f["sha256"]:
            by_sha.setdefault(f["sha256"], []).append(f["id"])
    duplicate_groups = [
        {"group_id": "DUP%02d" % (i + 1), "sha256": sha, "member_ids": sorted(ids)}
        for i, (sha, ids) in enumerate(sorted(by_sha.items()))
        if len(ids) > 1
    ]

    alias_groups = []
    for g in duplicate_groups:
        names = {files_by_id[i]["name"] for i in g["member_ids"]}
        if len(names) > 1:
            alias_groups.append({"group_id": "ALI%02d" % (len(alias_groups) + 1),
                                 "sha256": g["sha256"], "member_ids": g["member_ids"]})

    by_stem = {}
    stems = {}
    for f in files:
        stem = normalize_stem(f["rel_path"])
        f["normalized_stem"] = stem
        stems[f["id"]] = stem
        by_stem.setdefault(stem, []).append(f["id"])

    version_groups = []
    conflicts = []
    for stem, ids in sorted(by_stem.items()):
        if len(ids) < 2:
            continue
        digests = {files_by_id[i]["sha256"] for i in ids}
        if len(digests) <= 1:
            continue  # 内容相同的副本已由重复分组覆盖
        marked = any(has_version_marker(files_by_id[i]["name"]) for i in ids)
        if marked:
            version_groups.append({
                "group_id": "VER%02d" % (len(version_groups) + 1),
                "stem": stem, "member_ids": sorted(ids),
                "confidence": "heuristic",
                "note": "stem 相同、哈希不同，文件名含版本标记，属候选版本关系",
            })
        else:
            conflicts.append({
                "group_id": "CFL%02d" % (len(conflicts) + 1),
                "stem": stem, "member_ids": sorted(ids),
                "confidence": "heuristic",
                "reason": "stem 相同、哈希不同且无版本标记，需人工裁定或标 source_conflict",
            })

    return {
        "duplicate_groups": duplicate_groups,
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
    }


# --------------------------------------------------------------------------- 索引/检索

_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str):
    """提取检索词元：ASCII 单词 + CJK 连续串 + CJK 二元组。"""
    terms = set()
    for m in _WORD_RE.finditer(text):
        w = m.group(0).strip(".-_")
        if len(w) >= 2:
            terms.add(w.lower())
    for m in _CJK_RUN_RE.finditer(text):
        run = m.group(0)
        terms.add(run)
        if len(run) > 1:
            for i in range(len(run) - 1):
                terms.add(run[i:i + 2])
    return terms


def build_index(files, input_dir: Path):
    """为文件名+文本文件内容建立 term->file_ids 索引（确定顺序）。"""
    index = {}
    files_by_id = {f["id"]: f for f in files}
    for f in files:
        terms = tokenize(f["rel_path"])
        if f["kind"] == "text":
            data = read_bytes(input_dir / f["rel_path"], MAX_TEXT_BYTES)
            if data is not None:
                text, _ = decode_text(data)
                terms |= tokenize(text[:MAX_TEXT_BYTES])
        for t in terms:
            index.setdefault(t, set()).add(f["id"])
    return {t: sorted(ids) for t, ids in sorted(index.items())}


def search_candidates(index, query):
    """按词元取并集命中文件 id，返回命中文件 id 列表。"""
    terms = tokenize(query or "")
    if not terms:
        return []
    hits = set()
    for t in terms:
        if t in index:
            hits |= set(index[t])
    return sorted(hits)


# --------------------------------------------------------------------------- 契约


def derive_case_id(case) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "case", "caseId"):
            val = case.get(key)
            if val not in (None, ""):
                return str(val)
    return "unknown"


def extract_contract_keys(case):
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id", "id"):
                    val = item.get(cand)
                    if isinstance(val, str) and val:
                        keys.append(val)
                        break
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


_ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime",
)


def build_skeleton(case_id, contract_keys, status="partial"):
    result = {}
    for key in contract_keys:
        result[key] = None
    for key in _ENVELOPE_KEYS:
        result[key] = [] if key in ("claims", "uncertainties", "sources",
                                    "change_log", "pending_human", "errors",
                                    "degradations") else None
    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {}
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    result["runtime"] = {}
    return result


# --------------------------------------------------------------------------- 中间表示模板


def model_template(case_id: str) -> dict:
    """语义中间表示 model.json 的空模板，由 Agent 增量填写。"""
    return {
        "case_id": case_id,
        "model_version": MODEL_VERSION,
        "notes": [],
        "relations": [],
        "annotations": [],
        "query_answers": [],
        "operations": [],
        "pending_human": [],
        "uncertainties": [],
    }


def plan_template(case_id: str, contract_keys, profile, budget_requests=56):
    """执行计划 plan.json 模板（含逐阶段预算分配）。"""
    return {
        "case_id": case_id,
        "input_profile": profile,
        "budget": {
            "total_seconds": 900,
            "model_requests": budget_requests,
            "allocations": [
                {"phase": "init", "seconds": 40, "requests": 1, "goal": "落盘 partial 骨架 + library.json + plan.json"},
                {"phase": "show", "seconds": 20, "requests": 1, "goal": "阅读带 locator 的盘点与原文摘录"},
                {"phase": "model", "seconds": 480, "requests": 32, "goal": "填写 model.json：别名/版本裁定、检索答案、恢复操作"},
                {"phase": "check", "seconds": 20, "requests": 1, "goal": "机械校验模型结构与证据 quote 命中"},
                {"phase": "build", "seconds": 40, "requests": 1, "goal": "收口 result.json 与全部产物"},
                {"phase": "selfcheck", "seconds": 20, "requests": 1, "goal": "复检产物齐全与可解析"},
            ],
        },
        "phases": [
            {"id": "discover", "status": "pending", "outputs": ["work/library.json"]},
            {"id": "model", "status": "pending", "outputs": ["work/model.json"]},
            {"id": "verify", "status": "pending", "outputs": ["work/check_report.json"]},
            {"id": "deliver", "status": "pending", "outputs": ["output/result.json"]},
        ],
        "contract_keys": contract_keys,
    }


# --------------------------------------------------------------------------- 模型校验


def _evidence_ok(ev, files_by_id) -> bool:
    if not isinstance(ev, dict):
        return False
    sid = ev.get("source_id")
    loc = ev.get("locator")
    if sid == "case" and isinstance(loc, str):
        return True
    if not isinstance(sid, str) or not isinstance(loc, str):
        return False
    if sid not in files_by_id:
        return False
    return True


def validate_model(model, library, input_dir: Path):
    """机械校验 model.json：结构 + 引用完整性 + quote 逐字命中。返回 report。"""
    errors, warnings = [], []
    files = library.get("files", [])
    files_by_id = {f["id"]: f for f in files}
    files_by_rel = {f["rel_path"]: f for f in files}

    if not isinstance(model, dict):
        return {"valid": False, "errors": ["model 不是对象"], "warnings": [], "detail": []}

    def check_evidence(evs, path_label):
        ok, bad = True, []
        if evs is None:
            return True, []
        for i, ev in enumerate(evs):
            if not isinstance(ev, dict):
                bad.append(f"{path_label}.evidence[{i}] 不是对象")
                ok = False
                continue
            sid = ev.get("source_id")
            loc = ev.get("locator")
            quote = ev.get("quote")
            if sid == "case":
                continue
            if sid not in files_by_id:
                f2 = files_by_rel.get(loc)
                if f2 and ev.get("source_id") is None:
                    # 允许仅凭 locator 解析到文件
                    continue
                bad.append(f"{path_label}.evidence[{i}] source_id 不存在: {sid}")
                ok = False
                continue
            # quote 逐字命中检查
            if isinstance(quote, str) and quote:
                f = files_by_id.get(sid)
                if f is not None and f.get("kind") == "binary":
                    # 二进制无文本层：quote 只能取 rel_path，无法做内容逐字检查
                    continue
                src = input_dir / loc if loc else None
                if src and src.exists():
                    data = read_bytes(src, MAX_TEXT_BYTES)
                    if data is not None:
                        text, _ = decode_text(data)
                        if quote not in text:
                            bad.append(f"{path_label}.evidence[{i}] quote 未逐字命中: {sid}:{loc}")
                            ok = False
                            warnings.append(f"quote mismatch at {path_label}.evidence[{i}]")
                    else:
                        bad.append(f"{path_label}.evidence[{i}] 源文件不可读: {loc}")
                        ok = False
                else:
                    bad.append(f"{path_label}.evidence[{i}] locator 无法解析: {loc}")
                    ok = False
        return ok, bad

    notes = model.get("notes", [])
    for i, n in enumerate(notes):
        if not isinstance(n, dict):
            errors.append(f"notes[{i}] 不是对象")
            continue
        if n.get("kind") not in ALLOWED_NOTE_KINDS:
            errors.append(f"notes[{i}].kind 非法: {n.get('kind')}")
        if n.get("confidence") not in ALLOWED_NOTE_CONFIDENCE:
            errors.append(f"notes[{i}].confidence 非法: {n.get('confidence')}")
        ok, bad = check_evidence(n.get("evidence"), f"notes[{i}]")
        if not ok:
            errors.extend(bad)

    rels = model.get("relations", [])
    seen_rel_ends = set()
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            errors.append(f"relations[{i}] 不是对象")
            continue
        if r.get("type") not in ALLOWED_RELATION_TYPES:
            errors.append(f"relations[{i}].type 非法: {r.get('type')}")
        for end in ("from", "to"):
            v = r.get(end)
            if isinstance(v, str) and v not in files_by_id and not v.startswith(("G", "E")):
                # 允许引用文件 id 或组/实体 id；具体存在性由 from/to 落在已知集合判定
                pass
        ok, bad = check_evidence(r.get("evidence"), f"relations[{i}]")
        if not ok:
            errors.extend(bad)

    answers = model.get("query_answers", [])
    for i, a in enumerate(answers):
        if not isinstance(a, dict):
            errors.append(f"query_answers[{i}] 不是对象")
            continue
        if a.get("status") not in ALLOWED_QUERY_STATUS:
            errors.append(f"query_answers[{i}].status 非法: {a.get('status')}")
        ok, bad = check_evidence(a.get("evidence"), f"query_answers[{i}]")
        if not ok:
            errors.extend(bad)

    annots = model.get("annotations", [])
    for i, a in enumerate(annots):
        if not isinstance(a, dict):
            errors.append(f"annotations[{i}] 不是对象")
            continue
        ok, bad = check_evidence(a.get("evidence"), f"annotations[{i}]")
        if not ok:
            errors.extend(bad)

    ops = model.get("operations", [])
    for i, o in enumerate(ops):
        if not isinstance(o, dict):
            errors.append(f"operations[{i}] 不是对象")

    detail = {"notes": len(notes), "relations": len(rels),
              "annotations": len(annots), "query_answers": len(answers),
              "operations": len(ops)}
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "detail": detail}


# --------------------------------------------------------------------------- 契约回填映射


_CONTRACT_MAP = [
    (re.compile(r"file|文件|inventory|盘点|清单"), "files"),
    (re.compile(r"annotation|annots|批注|标注"), "annotations"),
    (re.compile(r"dup|alias|副本|别名|重复"), "duplicate_groups"),
    (re.compile(r"conflict|冲突"), "conflicts"),
    (re.compile(r"version|版本"), "version_groups"),
    (re.compile(r"index|索引"), "index"),
    (re.compile(r"search|query|question|retriev|检索|问题|查找"), "search_results"),
    (re.compile(r"recover|operation|action|恢复|操作|change"), "operations"),
    (re.compile(r"meta"), "metadata"),
    (re.compile(r"relation|关系"), "relations"),
    (re.compile(r"note|笔记"), "notes"),
]


def map_contract_key(key: str, sections: dict, result: dict):
    """把单个契约键映射到最相关的 section；无匹配则返回 None。"""
    k = str(key).lower()
    for pattern, name in _CONTRACT_MAP:
        if pattern.search(k):
            return sections.get(name)
    # 无匹配：留 null，调用方记 degradation
    return None


# --------------------------------------------------------------------------- 证据辅助


def evidence_for(files_by_id, *ids):
    out = []
    for i in ids:
        f = files_by_id.get(i)
        if f:
            out.append({"source_id": i, "locator": f["rel_path"], "quote": f["rel_path"]})
    return out


def build_claims(library, model):
    claims = []
    analysis = library.get("analysis", {})
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    n = 0
    for g in analysis.get("duplicate_groups", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "directly_read", "confidence": "high",
            "text": "文件 %s 的 sha256 相同，为同一内容的副本/别名" % ", ".join(g["member_ids"]),
            "evidence": evidence_for(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("version_groups", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "inferred", "confidence": "heuristic",
            "text": "文件 %s 归一化 stem 相同，为同一文档的候选版本（含版本标记）" % ", ".join(g["member_ids"]),
            "evidence": evidence_for(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("conflicts", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "inferred", "confidence": "heuristic",
            "text": "文件 %s stem 相同但哈希不同且无版本标记，存在来源冲突" % ", ".join(g["member_ids"]),
            "evidence": evidence_for(files_by_id, *g["member_ids"]),
        })
    for rel in model.get("relations", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "directly_read", "confidence": rel.get("confidence", "inferred"),
            "text": "关系[%s]: %s -> %s（%s）" % (rel.get("type"), rel.get("from"), rel.get("to"), rel.get("basis", "")),
            "evidence": rel.get("evidence", []),
        })
    for a in model.get("query_answers", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "query_answer", "confidence": "explicit",
            "text": "检索[%s]: %s" % (a.get("query_id"), a.get("answer")),
            "evidence": a.get("evidence", []),
        })
    return claims


def build_sources(library):
    out = []
    for f in library.get("files", []):
        out.append({
            "source_id": f["id"], "locator": f["rel_path"], "name": f["name"],
            "sha256": f.get("sha256"), "size": f.get("size"), "kind": f.get("kind"),
        })
    return out


def build_change_log(library, model):
    log = []
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    # 引擎机械操作
    for g in library.get("analysis", {}).get("duplicate_groups", []):
        log.append({
            "action": "deduplicate", "before": g["member_ids"], "after": "grouped:" + g["group_id"],
            "reason": "sha256 相同", "locator": "sha256:" + g["sha256"],
        })
    for g in library.get("analysis", {}).get("version_groups", []):
        log.append({
            "action": "version_group", "before": g["member_ids"], "after": "grouped:" + g["group_id"],
            "reason": "stem 相同且含版本标记（heuristic）", "locator": "stem:" + g["stem"],
        })
    for g in library.get("analysis", {}).get("conflicts", []):
        log.append({
            "action": "flag_conflict", "before": g["member_ids"], "after": "pending_human",
            "reason": g.get("reason", "来源冲突"), "locator": "stem:" + g["stem"],
        })
    # 模型操作
    for op in model.get("operations", []):
        if isinstance(op, dict):
            log.append(op)
    # 错误/降级并入操作记录
    for e in library.get("errors", []):
        log.append({"action": "error", "before": None, "after": None,
                    "reason": e.get("message", "错误"), "locator": e.get("locator")})
    for d in library.get("degradations", []):
        log.append({"action": "degrade", "before": None, "after": None,
                    "reason": d.get("message", "降级"), "locator": d.get("locator")})
    return log


def compute_status(library, model, check_ok=True):
    files = library.get("files", [])
    has_model_content = bool(model.get("notes") or model.get("relations")
                             or model.get("query_answers") or model.get("operations")
                             or model.get("annotations"))
    if library.get("input_error"):
        return "input_error"
    if library.get("case_missing"):
        return "input_error"
    if not files:
        return "insufficient_evidence"
    if has_model_content and check_ok and not model.get("pending_human"):
        return "ok"
    return "partial"