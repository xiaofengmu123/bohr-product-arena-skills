#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""libcore.py — 科研文档库修复的确定性引擎与输出契约发现/校验。

设计原则
--------
1. 只依赖 Python 标准库；所有函数为纯函数或只读文件系统访问。
2. 除 runtime 外，结果中不出现时间戳、遍历顺序、随机量等非确定信息，
   保证同一输入两次运行除 runtime 外逐字节一致。
3. 先发现、再假设：不假设 case.json 的固定键名；output_contract 支持
   dict / list（字符串或对象）两种形态，缺失时降级为规范默认契约并标注。
4. 证据溯源：每条结论都要能指回材料中的逐字片段（source_id + locator + quote）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

SKILL_VERSION = "2.0.0"

# ---------------------------------------------------------------- 常量

TEXT_EXTS = {
    ".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml",
    ".html", ".htm", ".py", ".bib", ".ris", ".enw", ".log", ".tex", ".rst",
}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg"}
SCAN_EXTS = {".djvu"}

MAX_TEXT_READ = 2 * 1024 * 1024          # 文本读取上限 2MB
MAX_TEXT_STORE = 64 * 1024               # 存入 library.json 的整段文本上限 64KB
MAX_PREVIEW = 2000                       # show 展示用的文本预览长度
HASH_SKIP_OVERSIZE = 32 * 1024 * 1024    # 超过 32MB 跳过哈希并记录

# output_contract 缺失时的规范默认契约（与真实输入画像对齐）
DEFAULT_CONTRACT_FIELDS = [
    "inventory", "duplicates", "alias_groups", "version_groups", "conflicts",
    "metadata", "search_results", "operations", "recovery_plan",
]

# 信封字段：任何输出契约之外都必须存在的可复核字段
ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime", "contract_audit",
)
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

LIST_ENVELOPE_KEYS = (
    "claims", "uncertainties", "sources", "change_log",
    "pending_human", "errors", "degradations",
)


# ---------------------------------------------------------------- 小工具

def classify_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in SCAN_EXTS:
        return "scan"
    return "binary"


def write_json(path: Path, obj) -> None:
    """确定性写 JSON：ensure_ascii=False、indent=2、末尾换行。"""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def read_json(path, encodings=("utf-8-sig", "utf-8", "gbk")):
    """容错读 JSON。返回 (data, error)。"""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, {"locator": str(p), "error": f"read_failed:{e}"}
    text = None
    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, {
            "locator": str(p),
            "error": f"json_parse:{e.msg}@line{e.lineno}col{e.colno}",
        }


def read_text(path, limit=MAX_TEXT_READ):
    """容错读文本，返回 (text, error)。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, {"locator": str(p), "error": f"stat_failed:{e}"}
    if size > limit:
        return None, {"locator": str(p), "error": "oversize", "size": size}
    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, {"locator": str(p), "error": f"read_failed:{e}"}
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), None


def sha256_file(path, limit=HASH_SKIP_OVERSIZE):
    """返回 (hexdigest, error)。超限/不可读时返回 None + 说明。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, {"locator": str(p), "error": f"stat_failed:{e}", "status": "unreadable"}
    if size > limit:
        return None, {"locator": str(p), "error": "oversize", "size": size,
                      "status": "hash_skipped_oversize"}
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return None, {"locator": str(p), "error": f"read_failed:{e}", "status": "unreadable"}
    return h.hexdigest(), None


# ---------------------------------------------------------------- 盘点

def scan_tree(input_dir):
    """遍历目录，返回 (files, directories, errors, degradations)。"""
    root = Path(input_dir)
    files, dirs, errors, degradations = [], [], [], []
    if not root.exists():
        return files, dirs, [{"locator": str(root), "error": "input_missing"}], degradations
    if not root.is_dir():
        return files, dirs, [{"locator": str(root), "error": "input_not_dir"}], degradations
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            dirs.append(rel_dir.replace(os.sep, "/"))
        for name in filenames:
            full = Path(dirpath) / name
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                st = full.stat()
            except OSError as e:
                errors.append({"rel_path": rel, "error": f"stat_failed:{e}"})
                continue
            ext = full.suffix.lower()
            entry = {
                "rel_path": rel,
                "size": st.st_size,
                "mode_octal": oct(stat.S_IMODE(st.st_mode)),
                "readable": os.access(full, os.R_OK),
                "writable": os.access(full, os.W_OK),
                "ext": ext,
                "kind": classify_ext(ext),
                "is_task": rel in ("case.json", "task.md"),
            }
            digest, herr = sha256_file(full)
            if herr:
                entry["sha256"] = None
                entry["file_status"] = herr.get("status", "hash_error")
                errors.append({"rel_path": rel, **herr})
            else:
                entry["sha256"] = digest
                entry["file_status"] = "ok"
            entry["text_layer"] = False
            if entry["kind"] == "text":
                txt, terr = read_text(full)
                if terr:
                    entry["text_error"] = terr.get("error")
                    degradations.append({"rel_path": rel, **terr})
                else:
                    entry["text_layer"] = True
                    entry["text_preview"] = txt[:MAX_PREVIEW]
                    if len(txt) <= MAX_TEXT_STORE:
                        entry["text"] = txt
            else:
                entry["text_layer"] = False
                entry["text_note"] = (
                    "binary/pdf/office/scan: 文本层需外部工具，缺失时如实标注，不编造"
                )
            files.append(entry)
    files.sort(key=lambda f: f["rel_path"])
    for i, f in enumerate(files, 1):
        f["id"] = "F%03d" % i
    return files, dirs, errors, degradations


# ---------------------------------------------------------------- 版本/别名分组

_VERSION_RE = re.compile(
    r"[\s_\-\.]*(v|ver|version|rev|r)\s*[._-]?\s*\d+(\.\d+)*"
    r"|[\s_\-\.]*(final|draft|old|new|latest|update|updated|copy|copie)"
    r"|[\s_\-\.]*(副本|拷贝|复制|复件|修订|定稿|终稿|草稿)",
    re.IGNORECASE,
)
_NUMBRACKET_RE = re.compile(r"[\(\[\（]\s*\d+\s*[\)\]\）]")
_TRAILNUM_RE = re.compile(r"[\s_\-\.]*\d+$")


def normalize_stem(name: str) -> str:
    """归一化文件名主干，用于启发式版本分组。"""
    s = name.lower()
    s = _NUMBRACKET_RE.sub("", s)
    s = _VERSION_RE.sub("", s)
    s = _TRAILNUM_RE.sub("", s)
    s = re.sub(r"[\s_\-\.]+", "_", s).strip("_")
    return s or name.lower()


def group_files(files):
    """生成 alias_groups / version_groups / conflicts（启发式，可被 draft 覆盖）。"""
    by_hash = {}
    for f in files:
        digest = f.get("sha256")
        if not digest:
            continue
        by_hash.setdefault(digest, []).append(f)

    alias_groups = []
    for digest, members in sorted(by_hash.items()):
        if len(members) > 1:
            alias_groups.append({
                "member_ids": [m["id"] for m in sorted(members, key=lambda x: x["id"])],
                "basis": "sha256_equal",
                "sha256": digest,
            })

    by_stem = {}
    for f in files:
        stem = normalize_stem(Path(f["rel_path"]).stem)
        by_stem.setdefault(stem, []).append(f)

    version_groups, conflicts = [], []
    for stem in sorted(by_stem):
        members = by_stem[stem]
        if len(members) < 2:
            continue
        hashes = {m.get("sha256") for m in members}
        if len(hashes) <= 1:
            continue
        member_ids = [m["id"] for m in sorted(members, key=lambda x: x["id"])]
        version_groups.append({
            "group_id": "V%03d" % (len(version_groups) + 1),
            "stem": stem,
            "member_ids": member_ids,
            "basis": "stem_equal_hash_diff",
        })
        conflicts.append({
            "member_ids": member_ids,
            "stem": stem,
            "reason": "same_stem_diff_hash_no_explicit_version",
        })
    return alias_groups, version_groups, conflicts


# ---------------------------------------------------------------- 显式信息发现

def extract_explicit(case):
    """从 case.json 泛化发现别名/版本/元数据/检索问题，不假设固定键名。"""
    out = {"relations": [], "metadata": {}, "queries_raw": []}
    if not isinstance(case, dict):
        return out
    for key, val in case.items():
        kl = key.lower()
        if kl in ("aliases", "alias", "alias_map", "duplicate_of"):
            out["relations"].append({"kind": "alias", "source": "case.json." + key, "value": val})
        elif kl in ("versions", "version_map", "version_notes", "revisions", "revs"):
            out["relations"].append({"kind": "version", "source": "case.json." + key, "value": val})
        elif kl in ("relations", "relationships", "relation_map"):
            out["relations"].append({"kind": "relation", "source": "case.json." + key, "value": val})
        elif kl in ("metadata", "meta", "file_metadata"):
            if isinstance(val, dict):
                out["metadata"].update(val)
            else:
                out["metadata"]["raw"] = val
        elif kl in ("queries", "questions", "searches", "search", "asks"):
            out["queries_raw"].append({"source": "case.json." + key, "value": val})
    return out


def normalize_queries(queries_raw):
    queries = []
    for item in queries_raw:
        val, src = item["value"], item["source"]
        candidates = []
        if isinstance(val, str):
            candidates.append(val)
        elif isinstance(val, list):
            for sub in val:
                if isinstance(sub, str):
                    candidates.append(sub)
                elif isinstance(sub, dict):
                    for k in ("query", "question", "text", "q", "ask", "search"):
                        if isinstance(sub.get(k), str):
                            candidates.append(sub[k])
                            break
        elif isinstance(val, dict):
            for k in ("query", "question", "text", "q", "ask", "search"):
                if isinstance(val.get(k), str):
                    candidates.append(val[k])
                    break
        for c in candidates:
            if c.strip():
                queries.append({"text": c, "source": src})
    for i, q in enumerate(queries, 1):
        q["query_id"] = "Q%02d" % i
    return queries


# ---------------------------------------------------------------- 检索索引

def _tokenize(text):
    text = text.lower()
    toks = re.findall(r"[a-z0-9]+", text)
    # 中文：逐字 + 双字 bigram
    for ch in re.findall(r"[\u4e00-\u9fff]", text):
        toks.append(ch)
    bigrams = re.findall(r"(?=([\u4e00-\u9fff]{2}))", text)
    toks.extend(bigrams)
    return toks


def build_index(files, extra_texts):
    """简易倒排索引：token -> [{source_id, rel_path, snippet, count}]。"""
    index = {}
    for f in files:
        txt = f.get("text") or f.get("text_preview") or ""
        doc = txt
        for tok in _tokenize(doc):
            index.setdefault(tok, {}).setdefault(f["id"], {"rel_path": f["rel_path"], "count": 0})
            index[tok][f["id"]]["count"] += 1
    for extra in extra_texts:
        for tok in _tokenize(extra["text"]):
            index.setdefault(tok, {}).setdefault(extra["source_id"], {
                "rel_path": extra.get("rel_path", ""), "count": 0,
            })
            index[tok][extra["source_id"]]["count"] += 1
    return index


def search(index, query, top=10):
    toks = _tokenize(query)
    if not toks:
        return []
    scores = {}
    for tok in toks:
        for src, info in index.get(tok, {}).items():
            scores.setdefault(src, {"rel_path": info["rel_path"], "score": 0})
            scores[src]["score"] += info["count"]
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    return [
        {"source_id": sid, "rel_path": info["rel_path"], "score": info["score"]}
        for sid, info in ranked[:top]
    ]


# ---------------------------------------------------------------- 输出契约

def _parse_spec(v):
    if isinstance(v, dict):
        return {
            "required": bool(v.get("required", True)),
            "type": v.get("type"),
            "default": v.get("default"),
            "description": v.get("description") or v.get("desc"),
        }
    return {"required": True, "type": None, "default": v}


class ContractSpec:
    def __init__(self, fields, source, raw=None):
        self.fields = fields
        self.source = source
        self.raw = raw

    def to_dict(self):
        return {"source": self.source, "fields": self.fields}


def discover_contract(case):
    raw = None
    if isinstance(case, dict):
        raw = case.get("output_contract")
        if raw is None:
            for k in ("contract", "output_schema", "result_schema", "required_fields"):
                if k in case:
                    raw = case[k]
                    break
    if isinstance(raw, dict):
        fields = []
        for k, v in raw.items():
            fields.append({"field": str(k), **_parse_spec(v)})
        return ContractSpec(fields, "dict", raw)
    if isinstance(raw, list):
        fields = []
        for item in raw:
            if isinstance(item, str):
                fields.append({"field": item, "required": True, "type": None, "default": None})
            elif isinstance(item, dict):
                name = None
                for k in ("name", "key", "field", "field_id", "id"):
                    if isinstance(item.get(k), str):
                        name = item[k]
                        break
                if name:
                    fields.append({"field": name, **_parse_spec(item)})
        return ContractSpec(fields, "list", raw)
    fields = [
        {"field": f, "required": True, "type": None, "default": None}
        for f in DEFAULT_CONTRACT_FIELDS
    ]
    return ContractSpec(fields, "fallback", None)


def _norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def compute_contract_values(library, draft, case_id):
    """把契约字段名映射到确定性计算结果；未知字段给 null 并进入 pending。"""
    files = library.get("files", [])
    analysis = library.get("analysis", {})
    queries = library.get("queries", [])
    query_answers = []
    for q in queries:
        query_answers.append({"query_id": q["query_id"], "text": q["text"],
                              "answer": None, "status": "pending"})
    for qa in (draft or {}).get("queries", []):
        for item in query_answers:
            if item["query_id"] == qa.get("query_id"):
                item["answer"] = qa.get("answer")
                item["status"] = qa.get("status", "answered")
                item["evidence"] = qa.get("evidence", [])
    answered = [it for it in query_answers if it.get("status") != "pending"]

    canonical = {
        "inventory": {"files": files, "directories": library.get("directories", [])},
        "files": files,
        "file_list": [f["rel_path"] for f in files],
        "duplicates": analysis.get("alias_groups", []),
        "alias_groups": analysis.get("alias_groups", []),
        "aliases": analysis.get("alias_groups", []),
        "version_groups": analysis.get("version_groups", []),
        "versions": analysis.get("version_groups", []),
        "conflicts": analysis.get("conflicts", []),
        "metadata": analysis.get("metadata", {}),
        "queries": query_answers,
        "search_results": answered if answered else None,
        "operations": analysis.get("operations", []),
        "recovery_plan": analysis.get("recovery_plan", []),
        "explicit_relations": analysis.get("explicit_relations", []),
    }
    return canonical


class ChangeLog:
    """确定性操作记录：before/after/reason/locator，无时间戳。"""

    def __init__(self):
        self.entries = []
        self._seq = 0

    def record(self, scope, before, after, reason, locator, status="applied"):
        self._seq += 1
        self.entries.append({
            "seq": self._seq,
            "scope": scope,
            "before": before,
            "after": after,
            "reason": reason,
            "locator": locator,
            "status": status,
        })

    def to_list(self):
        return self.entries


# ---------------------------------------------------------------- draft 校验

def load_source_text(input_dir, source_id, files):
    """按 source_id 找到文件并读文本，返回 (text, rel_path)。case/task 特殊处理。"""
    if source_id in ("case", "case.json"):
        p = Path(input_dir) / "case.json"
        txt, _ = read_text(p)
        return txt or "", "case.json"
    if source_id in ("task", "task.md"):
        p = Path(input_dir) / "task.md"
        txt, _ = read_text(p)
        return txt or "", "task.md"
    for f in files:
        if f.get("id") == source_id:
            p = Path(input_dir) / f["rel_path"]
            txt, _ = read_text(p)
            return txt or "", f["rel_path"]
    return None, None


def verify_evidence(input_dir, evidence, files):
    """逐字校验 quote 是否命中源文本。返回 (ok, note)。"""
    if not isinstance(evidence, dict):
        return False, "evidence_not_dict"
    sid = evidence.get("source_id")
    loc = evidence.get("locator")
    quote = evidence.get("quote")
    if not sid or not quote:
        return False, "missing_source_or_quote"
    # locator 与 source 的 rel_path 一致性
    src_text, rel_path = load_source_text(input_dir, sid, files)
    if src_text is None:
        # 文件不可读/不存在：结构性降级
        return True, "source_unreadable_structural_only"
    if loc and rel_path and loc != rel_path and not loc.endswith(rel_path):
        return False, "locator_mismatch"
    if quote == loc or quote == rel_path:
        return True, "locator_quote"
    if quote in src_text:
        return True, "verbatim_match"
    return False, "quote_not_found"


# ---------------------------------------------------------------- 状态判定

def decide_status(contract_filled, contract_total, has_draft, has_input_error,
                  has_errors, empty_material):
    if has_input_error:
        return "input_error"
    if empty_material:
        return "insufficient_evidence"
    if contract_filled == contract_total and has_draft and not has_errors:
        return "ok"
    return "partial"


# ---------------------------------------------------------------- claims/sources 组装

def _file_evidence(files_by_id, *ids):
    out = []
    for i in ids:
        f = files_by_id.get(i)
        if f:
            out.append({"source_id": i, "locator": f["rel_path"], "quote": f["rel_path"]})
    return out


def build_claims(library, draft):
    claims = []
    analysis = library.get("analysis", {})
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    n = 0
    for g in analysis.get("alias_groups", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "directly_read", "confidence": "high",
            "text": "文件 %s 的 sha256 相同，为同一文件的副本/别名" % ", ".join(g["member_ids"]),
            "evidence": _file_evidence(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("version_groups", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "inferred", "confidence": "heuristic",
            "text": "文件 %s 归一化 stem 相同，为同一文档的候选版本" % ", ".join(g["member_ids"]),
            "evidence": _file_evidence(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("conflicts", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "inferred", "confidence": "heuristic",
            "text": "文件 %s stem 相同但哈希不同且无版本标记，存在来源冲突" % ", ".join(g["member_ids"]),
            "evidence": _file_evidence(files_by_id, *g["member_ids"]),
        })
    for rel in analysis.get("explicit_relations", []):
        n += 1
        quote = rel.get("quote_key") or rel.get("source", "case.json")
        claims.append({
            "claim_id": "C%03d" % n, "kind": "directly_read", "confidence": "explicit",
            "text": "显式关系(%s)：%s" % (rel.get("kind", "relation"),
                                      json.dumps(rel.get("value") or rel.get("text"),
                                                 ensure_ascii=False)),
            "evidence": [{"source_id": "case", "locator": "case.json", "quote": quote}],
        })
    for qa in (draft or {}).get("queries", []):
        n += 1
        claims.append({
            "claim_id": "C%03d" % n, "kind": "query_answer",
            "confidence": qa.get("status", "answered"),
            "text": "检索问题 %s：%s" % (qa.get("query_id"), qa.get("answer") or "（未回答）"),
            "evidence": qa.get("evidence", []),
        })
    return claims


def build_sources(library):
    sources = [{"source_id": "case", "rel_path": "case.json", "kind": "task_definition"}]
    for f in library.get("files", []):
        sources.append({
            "source_id": f["id"], "rel_path": f["rel_path"], "kind": f.get("kind"),
            "sha256": f.get("sha256"), "size": f.get("size"),
        })
    return sources


def build_pending(library, filled_fields):
    pending = []
    for f in library.get("files", []):
        if not f.get("text_layer") and f.get("kind") in ("pdf", "office", "image", "scan", "binary"):
            pending.append({"target": f["id"], "issue": "no_text_layer",
                            "rel_path": f["rel_path"], "note": "文本层需外部工具提取"})
        if f.get("file_status") not in (None, "ok"):
            pending.append({"target": f["id"], "issue": f.get("file_status"),
                            "rel_path": f["rel_path"]})
    for c in library.get("analysis", {}).get("conflicts", []):
        pending.append({"target": ", ".join(c["member_ids"]), "issue": "source_conflict",
                        "reason": c.get("reason"), "stem": c.get("stem")})
    for field in filled_fields:
        if not field.get("populated"):
            pending.append({"target": field["field"], "issue": "contract_field_null"})
    return pending


def build_degradations(library, contract, draft, output_fallback):
    degrads = list(library.get("degradations", []))
    if not draft:
        degrads.append({"issue": "no_draft", "note": "未读取材料、未写语义 draft，直接 build"})
    if contract.source == "fallback":
        degrads.append({"issue": "contract_fallback",
                        "note": "case.json 无 output_contract，使用默认契约并逐字段标注"})
    if output_fallback:
        degrads.append({"issue": "output_fallback", "note": output_fallback})
    no_text = [f["id"] for f in library.get("files", []) if not f.get("text_layer")
               and f.get("kind") in ("pdf", "office", "image", "scan")]
    if no_text:
        degrads.append({"issue": "no_tool:text_layer",
                        "note": "以下文件无文本层，需 pdftotext/OCR/office 工具：%s" % ", ".join(no_text)})
    return degrads
