#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""libcore.py — 科研文档库修复引擎（纯标准库、确定性、跨平台）。

本模块只做"确定性"工作：
  * 递归盘点输入目录中的文件并计算 sha256 / 大小 / 权限 / 编码 / 文本层；
  * 哈希去重（alias）、归一化 stem 版本分组（version）、无版本标记的冲突出列；
  * 关键词检索索引与逐字溯源定位（rel_path + 行号 + quote）；
  * 输出契约回填、信封字段组装、机械校验与交付收口。

语义判断（行政版本备注、别名裁定、检索答案）由 Agent 在 draft 中补充；
本模块只负责验证与合并，不替 Agent 编造语义结论。
所有确定性记录用有序字段写入，同一输入 => 除 runtime 外逐字节一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_VERSION = "1.0.0"
ENGINE_ID = "library-doc-repair"
TASK_LABEL = "library-05-document-repair"

# 输入与输出默认路径（评测运行时约定）
DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

# 大小与预算常量
TEXT_READ_CAP = 2_000_000          # 单文件文本层读取上限（约 2MB）
HASH_SKIP_CAP = 512 * 1024 * 1024  # 超过该大小跳过 sha256（512MB）
PREVIEW_CAP = 1600                 # show 时每个文本文件的预览字符数
SHA_PREFIX = 12                    # 展示用哈希前缀长度

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_NOTE_KINDS = {"alias", "version", "metadata", "correction",
                      "query_answer", "conflict", "recovery", "skip"}
ALLOWED_QUERY_STATUS = {"answered", "no_direct_match", "insufficient_evidence"}
ALLOWED_REL_TYPES = {"alias", "version", "conflict"}

# result.json 的信封键（排序即写入顺序）
ENVELOPE_KEYS = (
    "status", "result", "claims", "sources", "uncertainty",
    "pending_human", "change_log", "uncertainties", "errors", "degradations",
)
RUNTIME_KEY = "runtime"

CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
TOKEN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")

# 可能承载元数据 / 行政版本备注 / 关系 / 检索问题清单的文件名模式
SPECIAL_FILE_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"^(metadata|meta|manifest|mapping|relations?|versions?|version[_\- ].*|"
        r"aliases?|notes|index|workorder|task|case)\.(json|md|txt|yaml|yml|csv|tsv)$",
        r"readme(\.md|\.txt)?$",
        r"\.manifest$",
    )
]


# ---------------------------------------------------------------- 基础文件 IO

def _ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def resolve_dirs(input_dir, work_dir, output_dir) -> Tuple[Path, Path, Path]:
    input_dir = Path(input_dir) if input_dir else DEFAULT_INPUT
    work_dir = Path(work_dir) if work_dir else DEFAULT_WORK
    output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT
    _ensure_dir(work_dir)
    _ensure_dir(output_dir)
    return input_dir, work_dir, output_dir


def ensure_output(output_dir: Path, work_dir: Path, skill_root: Path) -> Tuple[Path, Optional[str]]:
    """确认输出目录可写；不可写则依次降级，返回 (实际目录, 降级说明)。"""
    for cand, tag in ((output_dir, None),
                      (work_dir / "output_fallback", "output_fallback"),
                      (skill_root / "skill_output", "skill_output")):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            if tag:
                return cand, f"output_fallback:{cand.name}"
            return cand, None
        except OSError:
            continue
    return output_dir, "output_not_writable"


def write_json(path: Path, obj: Any) -> None:
    """确定性写 JSON：UTF-8 无 BOM、2 空格缩进、保留键序、末尾换行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")


def read_json_tolerant(path: Path) -> Tuple[Any, Optional[str]]:
    """读取 JSON；坏 JSON / 坏编码不抛异常，返回 (数据或 None, 错误说明)。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, f"不可读：{e}"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return None, f"编码错误：{e}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"坏 JSON：{e}"


def read_text_tolerant(path: Path) -> Tuple[str, str, bool, Optional[str]]:
    """读取文本。返回 (text, encoding, truncated, error)。坏编码降级为 replace 并标注。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return "", "unreadable", False, f"不可读：{e}"
    truncated = len(raw) > TEXT_READ_CAP
    raw = raw[:TEXT_READ_CAP]
    if b"\x00" in raw[:8192]:
        return "", "binary", False, None
    try:
        text = raw.decode("utf-8-sig")
        return text, "utf-8-sig", truncated, None
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "utf-8-replace", truncated, "bad_encoding"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 输入契约解析

def derive_case_id(case: Any, input_dir: Path) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "case", "caseId"):
            val = case.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                return str(val)
    return "unknown"


def extract_contract_keys(case: Any) -> List[str]:
    """output_contract 可能是 dict 或 list（字符串 / 含 name/key/field 的 dict）。"""
    if not isinstance(case, dict):
        return []
    raw = case.get("output_contract")
    keys: List[str] = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id", "id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand])
                        break
    return keys


def extract_queries(case: Any) -> List[Tuple[str, str]]:
    """从 case.json 常见字段尽力抽取检索问题，返回 [(query_id, text)]。"""
    if not isinstance(case, dict):
        return []
    out: List[Tuple[str, str]] = []
    for key in ("queries", "questions", "query", "asks", "search_queries",
                "retrieval_queries", "search"):
        val = case.get(key)
        if isinstance(val, list):
            for i, item in enumerate(val, 1):
                if isinstance(item, str):
                    out.append((f"{key}_{i}", item))
                elif isinstance(item, dict):
                    for cand in ("query", "question", "q", "text", "ask", "title"):
                        if isinstance(item.get(cand), str) and item[cand].strip():
                            out.append((f"{key}_{i}", item[cand]))
                            break
        elif isinstance(val, str) and val.strip():
            out.append((key, val))
    # 去重保序
    seen, uniq = set(), []
    for qid, text in out:
        if text not in seen:
            seen.add(text)
            uniq.append((qid, text))
    return uniq


# ---------------------------------------------------------------- 文件盘点

def _kind_of(path: Path, size: int) -> str:
    if size == 0:
        return "empty"
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return "unreadable"
    if b"\x00" in head:
        return "binary"
    return "text"


def scan_files(input_dir: Path) -> Dict[str, Any]:
    """递归盘点。返回 {'files': [...], 'errors': [], 'degradations': []}。"""
    files: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    degradations: List[Dict[str, str]] = []

    if not input_dir.exists():
        return {
            "files": files,
            "errors": [{"code": "missing_input_dir", "path": str(input_dir),
                        "detail": "输入目录不存在"}],
            "degradations": degradations,
        }

    try:
        paths = sorted((p for p in input_dir.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(input_dir).as_posix())
    except OSError as e:
        return {"files": files,
                "errors": [{"code": "walk_error", "path": str(input_dir), "detail": str(e)}],
                "degradations": degradations}

    for i, p in enumerate(paths, 1):
        rel = p.relative_to(input_dir).as_posix()
        rec: Dict[str, Any] = {
            "id": f"F{i:03d}", "rel_path": rel, "size": None, "sha256": None,
            "kind": "unknown", "encoding": None, "text": None,
            "text_truncated": False, "mode_oct": None, "readable": None,
            "unreadable": False, "hash_skipped": False, "flags": [],
        }
        try:
            st = p.stat()
            rec["size"] = st.st_size
            rec["mode_oct"] = format(st.st_mode & 0o777, "o").zfill(3)
        except OSError as e:
            rec["unreadable"] = True
            rec["flags"].append("unreadable")
            errors.append({"code": "stat_error", "path": rel, "detail": str(e)})
            files.append(rec)
            continue

        rec["readable"] = bool(os.access(str(p), os.R_OK))

        if st.st_size > HASH_SKIP_CAP:
            rec["hash_skipped"] = True
            rec["flags"].append("hash_skipped_oversize")
            degradations.append({"code": "hash_skipped_oversize", "path": rel,
                                 "detail": f"size={st.st_size}"})
        else:
            try:
                rec["sha256"] = sha256_file(p)
            except OSError as e:
                rec["unreadable"] = True
                rec["flags"].append("hash_error")
                errors.append({"code": "hash_error", "path": rel, "detail": str(e)})

        rec["kind"] = _kind_of(p, st.st_size)
        if rec["kind"] == "text":
            text, enc, truncated, terr = read_text_tolerant(p)
            rec["encoding"] = enc
            rec["text"] = text
            rec["text_truncated"] = truncated
            if terr:
                rec["flags"].append(terr)
                degradations.append({"code": "bad_encoding", "path": rel,
                                     "detail": "非严格 UTF-8，已按 replace 解码"})
        elif rec["kind"] == "binary":
            rec["flags"].append("binary")
        elif rec["kind"] == "empty":
            rec["text"] = ""

        files.append(rec)

    return {"files": files, "errors": errors, "degradations": degradations}


def is_special_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    return any(pat.match(name) for pat in SPECIAL_FILE_PATTERNS)


# ---------------------------------------------------------------- 版本 / 别名归一化

def _strip_ext(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{1,10}$", "", name)


def norm_stem(name: str) -> str:
    """把文件名 stem 归一化用于版本分组；只做保守的可逆形归一，不编造语义。"""
    s = re.sub(r"\.[A-Za-z0-9]{1,10}$", "", name)
    s = s.lower()
    # 重复下载 / 副本标记
    s = re.sub(r"\s*[\(\（]\s*(?:copy|副本|another copy|another|duplicate|dup)\s*[\)\）]\s*$", "", s)
    s = re.sub(r"\s*[\(\（]\s*\d+\s*[\)\）]\s*$", "", s)
    # 版本标记（v2 / ver2 / rev2 / r2 ...）
    s = re.sub(r"[\s\-_.]*(?:v|ver|version|rev|r|release)\s*[\-_.]?\s*\d+(?:[._-]\d+){0,3}\s*$", "", s)
    # 阶段标记
    s = re.sub(r"[\s\-_.]*(?:final|draft|clean|cleanup|merged|updated|old|new)\s*$", "", s)
    # 日期后缀
    s = re.sub(r"[\s\-_.]*\d{4}[-_.]?\d{2}[-_.]?\d{2}\s*$", "", s)
    s = re.sub(r"[\s\-_.]*\d{8}\s*$", "", s)
    s = re.sub(r"[\s\-_.]*\d{4}\s*$", "", s)
    # 折叠分隔符并去除尾部空白
    s = re.sub(r"[\s\-_]+", " ", s).strip().strip("._- ").strip()
    return s


def has_version_marker(name: str) -> bool:
    base = _strip_ext(name)
    patterns = [
        r"\bv(?:er|ersion)?[\-_. ]?\d+(?:[._-]\d+){0,3}\b",
        r"\brev[\-_. ]?\d+\b",
        r"\br\d+\b",
        r"\b(final|draft|cleanup|merged|updated|release)\b",
        r"\d{4}[-_.]\d{2}[-_.]\d{2}",
        r"\b\d{8}\b",
    ]
    return any(re.search(p, base, re.I) for p in patterns)


def analyze(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_hash: Dict[str, List[str]] = defaultdict(list)
    for f in files:
        if f.get("sha256"):
            by_hash[f["sha256"]].append(f["id"])

    alias_groups = sorted(
        [sorted(ids) for ids in by_hash.values() if len(ids) > 1],
        key=lambda ids: ids[0],
    )

    by_stem: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in files:
        by_stem[norm_stem(f["rel_path"])].append(f)

    version_groups: List[List[str]] = []
    conflicts: List[List[str]] = []
    for stem, members in by_stem.items():
        if len(members) < 2:
            continue
        hashes = {m.get("sha256") for m in members}
        if len(hashes) < 2:
            continue  # 全部同哈希 → 归入 alias，不算版本差
        ids = sorted(m["id"] for m in members)
        any_marker = any(has_version_marker(m["rel_path"]) for m in members)
        if any_marker:
            version_groups.append(ids)
        else:
            conflicts.append(ids)

    version_groups.sort(key=lambda ids: ids[0])
    conflicts.sort(key=lambda ids: ids[0])
    return {
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
        "stem_map": {f["id"]: norm_stem(f["rel_path"]) for f in files},
    }


# ---------------------------------------------------------------- 检索索引

def tokenize(query: str) -> List[str]:
    q = TOKEN_RE.sub(" ", query.lower())
    tokens = [t for t in q.split() if t]
    extra: List[str] = []
    for run in CJK_RE.findall(query.lower()):
        extra.extend(run[i:i + 2] for i in range(len(run) - 1))
    return [t for t in (tokens + extra) if len(t) > 1]


def _line_containing(text: str, idx: int) -> str:
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def search(files: List[Dict[str, Any]], query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    terms = tokenize(query)
    if not terms:
        return []
    results: List[Dict[str, Any]] = []
    for f in files:
        rel_low = f["rel_path"].lower()
        score = 0
        for t in terms:
            if t in rel_low:
                score += 3
        context = None
        text = f.get("text")
        if isinstance(text, str) and text:
            low = text.lower()
            first: Optional[Tuple[int, str]] = None
            for t in terms:
                idx = low.find(t)
                if idx >= 0:
                    score += 1
                    if first is None or idx < first[0]:
                        first = (idx, t)
            if first is not None:
                idx, _ = first
                line_no = low.count("\n", 0, idx) + 1
                quote = _line_containing(text, idx)
                context = {"locator": f"{f['rel_path']}#L{line_no}", "quote": quote}
        if score > 0:
            results.append({"file_id": f["id"], "rel_path": f["rel_path"],
                            "score": score, "match": context})
    results.sort(key=lambda r: (-r["score"], r["rel_path"]))
    return results[:top_k]


# ---------------------------------------------------------------- draft 校验

def _load_source_texts(scan: Dict[str, Any]) -> Dict[str, str]:
    texts: Dict[str, str] = {}
    for f in scan.get("files", []):
        if f.get("text") is not None:
            texts[f["id"]] = f["text"]
    for sid, src in scan.get("sources", {}).items():
        texts[sid] = src.get("text", "") or ""
    return texts


def _valid_evidence(ev: Any, texts: Dict[str, str], files: List[Dict[str, Any]],
                    rel_map: Dict[str, str]) -> List[str]:
    if not isinstance(ev, dict):
        return ["evidence 项不是对象"]
    sid = ev.get("source_id")
    locator = ev.get("locator")
    quote = ev.get("quote")
    if not isinstance(sid, str) or not sid:
        return ["evidence 缺少 source_id"]
    if sid not in texts:
        return [f"source_id {sid} 不存在（无文本层）"]
    if not isinstance(quote, str) or not quote:
        return ["evidence 缺少非空 quote"]
    if quote not in texts[sid]:
        return [f"quote 未在 {sid} 中逐字命中"]
    if not isinstance(locator, str) or not locator:
        return ["evidence 缺少 locator"]
    if sid in rel_map:
        base = re.sub(r"#.*$", "", locator).strip()
        if base and base != rel_map[sid]:
            return [f"locator 与 source_id {sid} 不一致：{base} != {rel_map[sid]}"]
    return []


def validate_draft(draft: Any, scan: Dict[str, Any]) -> Dict[str, Any]:
    texts = _load_source_texts(scan)
    files = scan.get("files", [])
    rel_map: Dict[str, str] = {f["id"]: f["rel_path"] for f in files}
    for sid, src in scan.get("sources", {}).items():
        rel_map[sid] = src.get("rel_path", sid)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    counts = {"notes": 0, "queries": 0, "relations": 0}

    if not isinstance(draft, dict):
        return {"ok": False, "errors": [{"code": "draft_not_object", "detail": "draft 不是对象"}],
                "warnings": warnings, "counts": counts}

    for note in draft.get("notes", []):
        counts["notes"] += 1
        if not isinstance(note, dict):
            errors.append({"code": "note_not_object", "detail": "note 不是对象"})
            continue
        if note.get("kind") not in ALLOWED_NOTE_KINDS:
            errors.append({"code": "bad_note_kind", "detail": str(note.get("kind"))})
        if not isinstance(note.get("text"), str) or not note["text"].strip():
            errors.append({"code": "note_no_text", "detail": "note 缺少 text"})
        target = note.get("target")
        if not (isinstance(target, str) and (target in texts or target in {"@case", "@task"})):
            warnings.append({"code": "unknown_target", "detail": str(target)})
        for ev in note.get("evidence", []):
            errors.extend({"code": "bad_evidence", "detail": d}
                          for d in _valid_evidence(ev, texts, files, rel_map))

    for q in draft.get("queries", []):
        counts["queries"] += 1
        if not isinstance(q, dict):
            errors.append({"code": "query_not_object", "detail": "query 不是对象"})
            continue
        if not isinstance(q.get("query_id"), str) or not q["query_id"].strip():
            errors.append({"code": "query_no_id", "detail": "query 缺少 query_id"})
        if q.get("status") not in ALLOWED_QUERY_STATUS:
            errors.append({"code": "bad_query_status", "detail": str(q.get("status"))})
        if not isinstance(q.get("answer"), str):
            errors.append({"code": "query_no_answer", "detail": "query 缺少 answer"})
        for ev in q.get("evidence", []):
            errors.extend({"code": "bad_evidence", "detail": d}
                          for d in _valid_evidence(ev, texts, files, rel_map))

    for rel in draft.get("relations", []):
        counts["relations"] += 1
        if not isinstance(rel, dict):
            errors.append({"code": "relation_not_object", "detail": "relation 不是对象"})
            continue
        if rel.get("type") not in ALLOWED_REL_TYPES:
            errors.append({"code": "bad_relation_type", "detail": str(rel.get("type"))})
        members = rel.get("members")
        if not isinstance(members, list) or len(members) < 2:
            errors.append({"code": "relation_no_members", "detail": "relation members 不足"})
        else:
            known = {f["id"] for f in files} | {"@case", "@task"}
            for m in members:
                if m not in known:
                    warnings.append({"code": "unknown_member", "detail": str(m)})

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "counts": counts}


# ---------------------------------------------------------------- 结果组装

def _default_envelope() -> Dict[str, Any]:
    env: Dict[str, Any] = {}
    env["status"] = "partial"
    env["result"] = {}
    env["claims"] = []
    env["sources"] = []
    env["uncertainty"] = {"summary": "", "count": 0, "items": []}
    env["pending_human"] = []
    env["change_log"] = []
    env["uncertainties"] = []
    env["errors"] = []
    env["degradations"] = []
    return env


CONTRACT_HINTS = {
    "files": "files", "inventory": "files", "file_list": "files",
    "library": "files", "library_index": "files", "file_inventory": "files",
    "duplicates": "alias_groups", "alias": "alias_groups", "aliases": "alias_groups",
    "versions": "version_groups", "version": "version_groups",
    "version_groups": "version_groups", "conflicts": "conflicts",
    "relations": "relations", "relationships": "relations", "mapping": "relations",
    "queries": "queries", "answers": "queries", "questions": "queries",
    "metadata": "metadata", "summary": "summary", "result": "summary",
    "change_log": "change_log", "operations": "change_log", "notes": "notes",
    "index": "index", "search_index": "index", "search": "queries",
}


def _pick(merged: Dict[str, Any], key: str) -> Any:
    hint = CONTRACT_HINTS.get(key.lower())
    if hint and hint in merged:
        return merged[hint]
    if key.lower() in ("case_id", "id"):
        return merged.get("case_id")
    return None


def backfill_contract(case: Any, merged: Dict[str, Any]) -> Dict[str, Any]:
    keys = extract_contract_keys(case)
    out: Dict[str, Any] = {}
    for k in keys:
        val = _pick(merged, k)
        if val is None:
            val = [] if k.lower() in ("files", "file_list", "inventory", "library",
                                      "library_index", "duplicates", "aliases", "versions",
                                      "version_groups", "conflicts", "relations",
                                      "queries", "questions", "answers", "notes", "index") else None
        out[k] = val
    return out


def _file_evidence(files_by_id: Dict[str, Dict[str, Any]], *ids: str) -> List[Dict[str, str]]:
    out = []
    for i in ids:
        f = files_by_id.get(i)
        if f:
            out.append({"source_id": i, "locator": f["rel_path"],
                        "quote": f["rel_path"]})
    return out


def build_claims(analysis: Dict[str, Any], files: List[Dict[str, Any]],
                 draft: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    files_by_id = {f["id"]: f for f in files}
    claims: List[Dict[str, Any]] = []
    n = 0

    def add(kind, confidence, text, evidence):
        nonlocal n
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": kind, "confidence": confidence,
                       "text": text, "evidence": evidence})

    for g in analysis.get("alias_groups", []):
        add("directly_read", "high",
            f"文件 {', '.join(g)} 的 sha256 一致，为同一文件的重复副本/别名",
            _file_evidence(files_by_id, *g))

    explicit_version: List[Tuple[List[str], str]] = []
    explicit_alias: List[List[str]] = []
    if isinstance(draft, dict):
        for rel in draft.get("relations", []) or []:
            if not isinstance(rel, dict) or not isinstance(rel.get("members"), list):
                continue
            members = [m for m in rel["members"] if isinstance(m, str)]
            if rel.get("type") == "version" and rel.get("canonical") in members:
                explicit_version.append((members, rel["canonical"]))
            elif rel.get("type") == "alias" and len(members) >= 2:
                explicit_alias.append(members)

    for g in analysis.get("version_groups", []):
        canonical = None
        for members, cand in explicit_version:
            if all(m in g for m in members):
                canonical = cand
                break
        confidence = "explicit" if canonical else "heuristic"
        add("inferred" if not canonical else "directly_read", confidence,
            f"文件 {', '.join(g)} 归一化 stem 相同但哈希不同，为同一文档的候选版本"
            + (f"，行政备注指定 canonical={canonical}" if canonical else ""),
            _file_evidence(files_by_id, *g))

    for g in analysis.get("conflicts", []):
        add("inferred", "heuristic",
            f"文件 {', '.join(g)} stem 相同、哈希不同且无版本标记，存在来源冲突",
            _file_evidence(files_by_id, *g))

    for members in explicit_alias:
        add("directly_read", "explicit",
            f"显式别名关系（draft 指定）：{', '.join(members)}",
            _file_evidence(files_by_id, *members))

    if isinstance(draft, dict):
        for note in draft.get("notes", []) or []:
            if isinstance(note, dict) and note.get("text"):
                add("semantic", note.get("confidence", "inferred"),
                    f"[{note.get('kind')}] {note['text']}",
                    note.get("evidence", []) or [])
        for q in draft.get("queries", []) or []:
            if isinstance(q, dict) and isinstance(q.get("answer"), str):
                add("query_answer", "explicit" if q.get("status") == "answered" else "inferred",
                    f"[{q.get('query_id')}] {q['answer']}",
                    q.get("evidence", []) or [])

    return claims


def build_sources(scan: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for src_id, src in scan.get("sources", {}).items():
        items.append({"source_id": src_id, "rel_path": src.get("rel_path"),
                      "sha256": src.get("sha256"), "type": "input_manifest"})
    for f in scan.get("files", []):
        items.append({"source_id": f["id"], "rel_path": f["rel_path"],
                      "sha256": f.get("sha256"), "size": f.get("size"),
                      "kind": f.get("kind"), "type": "file"})
    return items


def build_change_log(scan: Dict[str, Any], draft: Optional[Dict[str, Any]],
                     queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    log: List[Dict[str, Any]] = []
    log.append({"op": "scan", "before": None, "after": f"{len(scan.get('files', []))} files",
                "reason": "确定性盘点输入目录", "locator": "/input"})
    for err in scan.get("errors", []):
        log.append({"op": "record", "before": err.get("path"), "after": "error",
                    "reason": str(err.get("detail")), "locator": err.get("path")})
    if isinstance(draft, dict):
        for note in draft.get("notes", []) or []:
            if isinstance(note, dict):
                log.append({"op": "note", "before": None, "after": note.get("text"),
                            "reason": f"kind={note.get('kind')}",
                            "locator": (note.get("evidence") or [{}])[0].get("locator")
                            if note.get("evidence") else None})
        for rel in draft.get("relations", []) or []:
            if isinstance(rel, dict):
                log.append({"op": "relation", "before": None,
                            "after": f"{rel.get('members')} -> canonical={rel.get('canonical')}",
                            "reason": f"type={rel.get('type')}",
                            "locator": None})
    for q in queries:
        log.append({"op": "query", "before": q.get("query_text"),
                    "after": q.get("answer"), "reason": f"status={q.get('status')}",
                    "locator": (q.get("evidence") or [{}])[0].get("locator")
                    if q.get("evidence") else None})
    return log


# ---------------------------------------------------------------- 主流程组合

def build_scan_payload(input_dir: Path) -> Dict[str, Any]:
    """扫描 + 读取 case.json/task.md 特殊来源 + 确定性分析。"""
    result = scan_files(input_dir)
    analysis = analyze(result["files"])
    result["analysis"] = analysis

    sources: Dict[str, Any] = {}
    for sid, rel in (("@case", "case.json"), ("@task", "task.md")):
        p = input_dir / rel
        if p.exists():
            text, _, _, _ = read_text_tolerant(p)
            try:
                sh = sha256_file(p)
            except OSError:
                sh = None
            sources[sid] = {"rel_path": rel, "text": text, "sha256": sh}
        else:
            sources[sid] = {"rel_path": rel, "text": "", "sha256": None}
    result["sources"] = sources

    # 特殊文件（metadata/notes/relations）标记，供 show 突出显示
    special_ids = [f["id"] for f in result["files"] if is_special_file(f["rel_path"])]
    result["special_file_ids"] = special_ids
    return result


def load_case(input_dir: Path) -> Tuple[Any, Optional[str]]:
    case, err = read_json_tolerant(input_dir / "case.json")
    return case, err


def make_result(case: Any, scan: Dict[str, Any], draft: Optional[Dict[str, Any]],
                output_files: Dict[str, str], degrade_notes: List[str],
                errors_extra: List[Dict[str, str]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = _default_envelope()

    files = scan.get("files", [])
    analysis = scan.get("analysis", {})

    # 查询：draft 显式给定优先；否则对 case.json 中的问题做确定性关键词检索
    queries: List[Dict[str, Any]] = []
    if isinstance(draft, dict) and draft.get("queries"):
        queries = list(draft["queries"])
    else:
        for qid, qtext in extract_queries(case):
            hits = search(files, qtext)
            status = "answered" if hits else "no_direct_match"
            answer = (hits[0]["rel_path"] if hits else
                      "未在文件名与文本层中检索到直接匹配")
            queries.append({
                "query_id": qid, "query_text": qtext, "answer": answer,
                "status": status, "method": "keyword",
                "evidence": ([{ "source_id": hits[0]["file_id"],
                               "locator": hits[0]["match"]["locator"],
                               "quote": hits[0]["match"]["quote"]}]
                             if hits and hits[0]["match"] else []),
            })

    merged["result"] = {
        "engine": ENGINE_ID,
        "task_label": TASK_LABEL,
        "file_count": len(files),
        "total_bytes": sum((f.get("size") or 0) for f in files),
        "alias_group_count": len(analysis.get("alias_groups", [])),
        "version_group_count": len(analysis.get("version_groups", [])),
        "conflict_count": len(analysis.get("conflicts", [])),
        "query_count": len(queries),
        "answered_count": sum(1 for q in queries if q.get("status") == "answered"),
        "alias_groups": analysis.get("alias_groups", []),
        "version_groups": analysis.get("version_groups", []),
        "conflicts": analysis.get("conflicts", []),
        "queries": queries,
    }
    lean_files = []
    for f in files:
        fr = {k: v for k, v in f.items() if k != "text"}
        if f.get("text"):
            fr["preview"] = f["text"][:240]
        lean_files.append(fr)
    merged["files"] = lean_files
    merged["alias_groups"] = analysis.get("alias_groups", [])
    merged["version_groups"] = analysis.get("version_groups", [])
    merged["conflicts"] = analysis.get("conflicts", [])
    merged["relations"] = (draft or {}).get("relations", []) if isinstance(draft, dict) else []
    merged["queries"] = queries
    merged["metadata"] = _extract_metadata(scan)
    merged["index"] = {
        "entry_count": len(files),
        "method": "filename+text keyword (deterministic)",
        "special_files": scan.get("special_file_ids", []),
    }
    merged["summary"] = merged["result"]
    merged["notes"] = (draft or {}).get("notes", []) if isinstance(draft, dict) else []
    merged["change_log"] = build_change_log(scan, draft, queries)
    merged["case_id"] = derive_case_id(case, Path(str(DEFAULT_INPUT)))
    merged["sources"] = build_sources(scan)
    merged["claims"] = build_claims(analysis, files, draft)
    merged["errors"] = list(scan.get("errors", [])) + list(errors_extra)
    merged["degradations"] = list(scan.get("degradations", []))
    for d in degrade_notes:
        merged["degradations"].append({"code": "no_draft" if d == "no_draft" else d,
                                       "detail": d})

    # 状态判定：诚实降级
    has_files = bool(scan.get("files"))
    input_dir_missing = any(e.get("code") == "missing_input_dir"
                            for e in scan.get("errors", []))
    draft_semantic = isinstance(draft, dict) and bool(draft.get("notes") or
                                                      draft.get("relations"))
    if input_dir_missing:
        status = "input_error"
    elif not has_files:
        status = "insufficient_evidence"
    elif case is None:
        status = "input_error"
    elif draft_semantic:
        status = "ok"
    else:
        status = "partial"
    merged["status"] = status

    uncertainty_items = []
    for g in analysis.get("version_groups", []):
        uncertainty_items.append({"subject": ", ".join(g),
                                  "reason": "版本分组的转折点依赖行政备注，缺备注时为 heuristic"})
    for g in analysis.get("conflicts", []):
        uncertainty_items.append({"subject": ", ".join(g),
                                  "reason": "同 stem 不同哈希且无版本标记，待人工裁定"})
    merged["uncertainty"] = {"summary":
                             f"{len(uncertainty_items)} 项待确认（版本/冲突分组为启发式）",
                             "count": len(uncertainty_items),
                             "items": uncertainty_items}
    merged["uncertainties"] = uncertainty_items
    for u in uncertainty_items:
        merged["pending_human"].append({"subject": u["subject"], "reason": u["reason"]})

    merged["output_files"] = output_files
    merged["result"]["artifacts"] = output_files

    # 组装 result.json：case_id + status + 契约键 + 信封键 + runtime
    out: Dict[str, Any] = {}
    out["case_id"] = merged["case_id"]
    out["status"] = merged["status"]
    contract = backfill_contract(case, merged)
    for k, v in contract.items():
        out[k] = v
    for k in ENVELOPE_KEYS:
        out[k] = merged.get(k)
    out[RUNTIME_KEY] = {}
    return out


def _extract_metadata(scan: Dict[str, Any]) -> Any:
    """从特殊文件里取第一条合法 JSON 当元数据；无则 None。"""
    for f in scan.get("files", []):
        if f.get("id") in scan.get("special_file_ids", []) and f.get("text"):
            try:
                obj = json.loads(f["text"])
                if isinstance(obj, (dict, list)):
                    return {"from": f["rel_path"], "value": obj}
            except json.JSONDecodeError:
                continue
    return None


# ---------------------------------------------------------------- 版本文本层（供 show）

def summarize_scan(scan: Dict[str, Any]) -> str:
    files = scan.get("files", [])
    lines: List[str] = []
    lines.append(f"files={len(files)} "
                 f"alias_groups={len(scan.get('analysis', {}).get('alias_groups', []))} "
                 f"version_groups={len(scan.get('analysis', {}).get('version_groups', []))} "
                 f"conflicts={len(scan.get('analysis', {}).get('conflicts', []))}")
    return "\n".join(lines)