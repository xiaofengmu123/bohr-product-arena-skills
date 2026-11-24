#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — 科研文档库修复的确定性引擎（纯标准库，无第三方依赖）。

职责（全部为只读 I/O 或纯函数，排序固定，保证同输入两次运行除 runtime 外逐字节一致）：

  1. 稳定扫描输入目录，计算 sha256、探测类型/编码/可读性；
  2. 归一化 stem，做去重（同哈希）、版本分组（版本标记）、别名/显式关系、冲突判定；
  3. 读取文本类材料的显式关系（行政版本备注、别名声明），逐条保留 locator + 逐字 quote；
  4. 从 case.json 与 task.md 提取 output_contract、检索问题；
  5. 建立可复跑的关键词索引并实现确定性检索；
  6. 回填 output_contract 全部键，组装 result.json 各信封字段。

设计取舍（本版侧重点：降级与不确定性显式化）：
  - 顶层增加结构化 `limitations` 信封，把「数据缺口 / 工具缺口 / 范围外 / 假设」分类列全；
  - 每条启发式结论都配套一条 `uncertainty`（含具体 basis），不用笼统的"可能不准"；
  - 二进制/无文本层文件只做字节级盘点并显式标注，绝不虚构批注内容或数值。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

HASH_CHUNK = 1 << 20                      # 1 MiB 流式哈希块
TEXT_READ_CAP = 2 * 1024 * 1024           # 文本读取上限 2 MiB
PREVIEW_CHARS = 500                       # 文本预览长度（字符）
ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]{2,}")
VERSION_TOKEN_RE = re.compile(
    r"(?:\b|[_\-.\s])(?:v|ver|version|rev|r)[\s._-]*\d+(?:\.\d+)*"
    r"|(?:19|20)\d{2}[-_.]\d{1,2}[-_.]\d{1,2}", re.IGNORECASE)
COPY_TOKEN_RE = re.compile(
    r"\s*[\(\[\{（【]\s*(?:copy|副本|\d+)\s*[\)\]\}）】]"
    r"|\s+(?:copy|副本)\s*\d*"
    r"|(?:_|-)(?:final|draft|old|new|bak|backup|tmp|temp)(?=_|-|\.|\b)", re.IGNORECASE)

# 元文件：属于任务定义，不属于被修复的资料库本体。
META_NAMES = {"case.json", "task.md", "resources.yaml", "workorder.md", "task.json"}

# 常见文本类型扩展名（用于优先做文本索引）。
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv", ".tsv",
    ".xml", ".html", ".htm", ".bib", ".ris", ".log", ".py", ".r", ".rst",
    ".tex", ".csv", ".ini", ".cfg", ".toml",
}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}
PDF_EXT = {".pdf"}


# --------------------------------------------------------------------------- 基础 I/O

def stable_walk(root: Path) -> list:
    """按相对路径排序返回所有文件（不包含目录），保证确定性。"""
    if not root.exists():
        return []
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath)
        for name in sorted(filenames):
            abs_path = dirpath / name
            if abs_path.is_file():
                try:
                    rel = abs_path.relative_to(root).as_posix()
                except ValueError:
                    rel = str(abs_path)
                out.append((rel, abs_path))
    out.sort(key=lambda t: t[0])
    return out


def sha256_file(path: Path) -> str | None:
    """流式计算 sha256；失败返回 None。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_bytes_head(path: Path, n: int = 4096) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def decode_text(raw: bytes) -> tuple[str, str]:
    """按 utf-8-sig → utf-8 → latin-1 顺序容错解码，返回 (text, encoding)。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


def read_text_limited(path: Path, cap: int = TEXT_READ_CAP) -> tuple[str | None, str | None, bool]:
    """读取文本；超限返回 (None, None, True) 表示 oversize，不假装有文本层。"""
    size = path.stat().st_size
    if size > cap:
        return None, None, True
    raw = read_bytes_head(path, size)
    text, enc = decode_text(raw)
    return text, enc, False


# --------------------------------------------------------------------------- 类型与权限

def detect_type(rel: str, head: bytes) -> str:
    ext = Path(rel).suffix.lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"GIF8"):
        return "gif"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith(b"PK\x03\x04"):
        return "office_zip" if ext in OFFICE_EXT else "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "office_ole"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"\x7fELF"):
        return "binary"
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in OFFICE_EXT:
        return "office"
    if ext in TEXT_EXT:
        return "text"
    # 文本启发：尝试解码，非空且替换率低则视为 text。
    if head:
        text, _enc = decode_text(head)
        if text and "\ufffd" not in text:
            return "text"
    return "binary"


def is_meta(rel: str) -> bool:
    name = Path(rel).name.lower()
    return name in META_NAMES or rel.startswith("meta/") or rel.startswith("task/")


# --------------------------------------------------------------------------- stem 归一化

def _strip_ext(name: str) -> str:
    p = Path(name)
    suffixes = p.suffixes
    if len(suffixes) > 1 and suffixes[-1].lower() == ".gz":
        name = name[: -len("".join(suffixes[-2:]))]
    else:
        name = p.stem
    return name


def normalize_stem(name: str) -> str:
    """去除版本号、日期、副本后缀，折叠空白与下划线，得到候选版本组的 stem。"""
    s = _strip_ext(name)
    s = COPY_TOKEN_RE.sub(" ", s)
    s = VERSION_TOKEN_RE.sub(" ", s)
    s = re.sub(r"[\s_\-\.（）()\[\]{}+]+", " ", s)
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def version_tokens(name: str) -> list:
    return [m.group(0).strip(" \t._-") for m in VERSION_TOKEN_RE.finditer(_strip_ext(name))]


def has_version_marker(name: str) -> bool:
    return len(version_tokens(name)) > 0


# --------------------------------------------------------------------------- 契约提取

def derive_case_id(case) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "case", "caseId"):
            val = case.get(key)
            if val not in (None, ""):
                return val if isinstance(val, str) else str(val)
    return "unknown"


def extract_contract(case) -> list:
    """支持 output_contract 的 dict 与 list 两种形态；返回字段键名列表。"""
    if not isinstance(case, dict):
        return []
    raw = case.get("output_contract")
    keys: list = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id", "id"):
                    if isinstance(item.get(cand), str):
                        keys.append(item[cand])
                        break
    # 去重保序
    seen, ordered = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def extract_queries(case, task_md_text: str | None) -> list:
    """从 case.json 多个可能键名与 task.md 的问句行提取检索问题。"""
    queries: list = []
    qid = 0

    def add(text):
        nonlocal qid
        text = (text or "").strip()
        if not text or len(text) < 2:
            return
        qid += 1
        queries.append({"query_id": f"Q{qid}", "question": text, "source": "auto"})

    if isinstance(case, dict):
        for key in ("queries", "questions", "searches", "检索问题", "检索", "asks"):
            val = case.get(key)
            if isinstance(val, list):
                for it in val:
                    if isinstance(it, str):
                        add(it)
                    elif isinstance(it, dict):
                        add(it.get("question") or it.get("query") or it.get("text") or it.get("q"))
            elif isinstance(val, str):
                add(val)
    if task_md_text:
        # 取以问号结尾、或含"检索/查找/找到/请回答"的行。
        for line in task_md_text.splitlines():
            ls = line.strip()
            if ls.endswith("?") or ls.endswith("？"):
                add(ls.lstrip("#*- "))
            elif re.match(r"^(检索|查找|找到|列出|回答|请找到)", ls):
                add(ls.lstrip("#*- "))
    return queries


# --------------------------------------------------------------------------- 盘点

def build_file_records(root: Path) -> tuple[list, list]:
    """返回 (files, errors)。files 排序固定。"""
    files, errors = [], []
    for idx, (rel, abs_path) in enumerate(stable_walk(root), 1):
        fid = f"F{idx:03d}"
        try:
            st = abs_path.stat()
        except OSError as exc:
            errors.append({"file": rel, "error": f"stat:{exc}", "handled": True})
            continue
        readable = os.access(abs_path, os.R_OK)
        head = read_bytes_head(abs_path)
        ftype = detect_type(rel, head)
        digest = sha256_file(abs_path) if readable else None
        text, enc, oversize = None, None, False
        text_chars = 0
        if readable and ftype in ("text", "json", "markdown", "csv", "html", "log", "xml", "bib", "ris"):
            text, enc, oversize = read_text_limited(abs_path)
            if text is not None:
                text_chars = len(text)
        rec = {
            "id": fid,
            "rel_path": rel,
            "sha256": digest,
            "size": int(st.st_size),
            "mtime": int(st.st_mtime),
            "readable": readable,
            "type": ftype,
            "encoding": enc,
            "is_meta": is_meta(rel),
            "text_chars": text_chars,
            "text_skipped_oversize": oversize,
        }
        files.append(rec)
    return files, errors


# --------------------------------------------------------------------------- 分析：去重 / 版本 / 别名 / 冲突

def _group_by_hash(files: list) -> list:
    by_hash: dict = {}
    for f in files:
        d = f.get("sha256")
        if d:
            by_hash.setdefault(d, []).append(f)
    groups = []
    gid = 0
    for d, members in sorted(by_hash.items(), key=lambda kv: kv[0]):
        if len(members) > 1:
            gid += 1
            groups.append({
                "group_id": f"alias-{gid:02d}",
                "sha256": d,
                "member_ids": sorted(m["id"] for m in members),
                "rel_paths": sorted(m["rel_path"] for m in members),
                "basis": "identical_sha256",
                "confidence": "fact",
            })
    return groups


def _group_by_stem(files: list, meta_ids: set) -> tuple[list, list]:
    """按归一化 stem 分组。返回 (version_groups, conflicts)。

    冲突 = 同 stem、不同哈希、且无显式版本标记 → 标 source_conflict，进 pending_human。
    版本 = 同 stem、不同哈希、至少一个带版本标记 → 候选版本组。
    """
    by_stem: dict = {}
    for f in files:
        if f["id"] in meta_ids:
            continue
        stem = normalize_stem(f["rel_path"])
        if stem:
            by_stem.setdefault(stem, []).append(f)

    version_groups, conflicts = [], []
    vid = 0
    for stem in sorted(by_stem.keys()):
        members = sorted(by_stem[stem], key=lambda f: f["rel_path"])
        if len(members) < 2:
            continue
        hashes = {f.get("sha256") for f in members if f.get("sha256")}
        if len(hashes) <= 1:
            continue  # 同哈希已在去重组
        marked = any(has_version_marker(f["rel_path"]) for f in members)
        if marked:
            vid += 1
            version_groups.append({
                "group_id": f"version-{vid:02d}",
                "stem": stem,
                "member_ids": [m["id"] for m in members],
                "rel_paths": [m["rel_path"] for m in members],
                "markers": {m["rel_path"]: version_tokens(m["rel_path"]) for m in members},
                "basis": "same_stem_version_markers",
                "confidence": "heuristic",
            })
        else:
            vid += 1
            conflicts.append({
                "group_id": f"conflict-{vid:02d}",
                "stem": stem,
                "member_ids": [m["id"] for m in members],
                "rel_paths": [m["rel_path"] for m in members],
                "basis": "same_stem_diff_hash_no_version_marker",
                "confidence": "heuristic",
            })
    return version_groups, conflicts


# --------------------------------------------------------------------------- 显式关系提取

RELATION_PATTERNS = [
    (re.compile(r"(alias|别名|同文件|同一文件|also[ -]?known[ -]?as)", re.I), "alias"),
    (re.compile(r"(version|版本|修订|revision|supersede[sd]?|取代|替换)", re.I), "version"),
    (re.compile(r"(rename[d]?|改名|重命名|move[d]?|移动)", re.I), "rename"),
    (re.compile(r"(duplicate|重复|副本|copy[ -]?of)", re.I), "duplicate"),
]


def extract_explicit_relations(files: list, root: Path) -> list:
    """从文本类材料中提取显式关系声明（行政版本备注/别名）。

    只保留整行原文作 quote，relation 判定依据是模式命中，不作为事实强判；
    confidence 标 `explicit` 时说明材料原文明写，否则 `heuristic`。
    """
    relations = []
    rid = 0
    for f in files:
        if f.get("is_meta") or f.get("text_chars", 0) == 0:
            continue
        abs_path = root / f["rel_path"]
        if not abs_path.exists():
            continue
        text, _enc, _ov = read_text_limited(abs_path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            ls = line.strip()
            if not ls or len(ls) < 3:
                continue
            for pat, kind in RELATION_PATTERNS:
                if pat.search(ls):
                    rid += 1
                    relations.append({
                        "relation_id": f"rel-{rid:02d}",
                        "kind": kind,
                        "source_id": f["id"],
                        "locator": f"{f['rel_path']}#L{lineno}",
                        "quote": ls,
                        "confidence": "explicit",
                    })
                    break
    return relations


# --------------------------------------------------------------------------- 检索索引

def tokenize(text: str) -> set:
    terms = set()
    for m in ASCII_TOKEN_RE.finditer(text):
        terms.add(m.group(0).lower())
    for m in CJK_RUN_RE.finditer(text):
        run = m.group(0)
        terms.add(run)
        # 中文双字/三字滑动窗口，提高召回。
        for i in range(len(run) - 1):
            terms.add(run[i:i + 2])
    return terms


def build_index(files: list, relations: list, root: Path) -> dict:
    corpus = []
    for f in files:
        entry = {
            "source_id": f["id"],
            "kind": "filename",
            "locator": f["rel_path"],
            "text": f["rel_path"],
            "terms": sorted(tokenize(f["rel_path"])),
        }
        corpus.append(entry)
        if f.get("text_chars", 0) > 0:
            abs_path = root / f["rel_path"]
            if abs_path.exists():
                text, _enc, _ov = read_text_limited(abs_path)
                if text is not None:
                    corpus.append({
                        "source_id": f["id"],
                        "kind": "text",
                        "locator": f["rel_path"],
                        "text": text,
                        "terms": sorted(tokenize(text)),
                    })
    for rel in relations:
        corpus.append({
            "source_id": rel["source_id"],
            "kind": "relation",
            "locator": rel["locator"],
            "text": rel["quote"],
            "terms": sorted(tokenize(rel["quote"])),
        })
    freq: dict = {}
    for c in corpus:
        for t in c["terms"]:
            freq[t] = freq.get(t, 0) + 1
    sample_terms = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:80]
    return {
        "corpus": corpus,
        "term_count": len(freq),
        "doc_count": len(corpus),
        "sample_terms": [{"term": t, "freq": fr} for t, fr in sample_terms],
    }


def retrieve(query: str, index: dict, top_n: int = 10) -> list:
    """确定性检索：文件名精确命中 > 词项重合 > 子串命中。"""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    qterms = tokenize(q)
    scored = []
    for c in index.get("corpus", []):
        text = c["text"]
        tl = text.lower()
        score = 0
        if ql in Path(c["locator"]).name.lower():
            score += 40
        if ql in tl:
            score += 15
        overlap = qterms & set(c["terms"])
        score += 3 * len(overlap)
        if score > 0:
            scored.append({
                "source_id": c["source_id"],
                "kind": c["kind"],
                "locator": c["locator"],
                "snippet": text[:200],
                "score": score,
            })
    scored.sort(key=lambda x: (-x["score"], x["locator"], x["kind"]))
    return scored[:top_n]


# --------------------------------------------------------------------------- 契约回填

def resolve_contract_values(keys: list, library: dict, draft: dict | None) -> dict:
    """把已知契约键映射到确定性结果；未知键保持 null，绝不编造。"""
    analysis = library.get("analysis", {})
    mapping = {}
    draft_vals = (draft or {}).get("contract_values") or {}
    for k in keys:
        lk = k.lower()
        if k in draft_vals and draft_vals[k] is not None:
            mapping[k] = draft_vals[k]
        elif any(s in lk for s in ("file", "inventory", "manifest", "盘点", "文件")):
            mapping[k] = {
                "count": len(library.get("files", [])),
                "files": [{"id": f["id"], "rel_path": f["rel_path"], "sha256": f["sha256"],
                           "size": f["size"], "type": f["type"], "readable": f["readable"]}
                          for f in library.get("files", [])],
            }
        elif any(s in lk for s in ("duplicate", "副本", "重复", "alias", "别名")):
            mapping[k] = analysis.get("alias_groups", [])
        elif any(s in lk for s in ("version", "版本")):
            mapping[k] = analysis.get("version_groups", [])
        elif any(s in lk for s in ("conflict", "冲突")):
            mapping[k] = analysis.get("conflicts", [])
        elif any(s in lk for s in ("relation", "关系")):
            mapping[k] = analysis.get("explicit_relations", [])
        elif any(s in lk for s in ("index", "索引", "retriev", "检索", "search", "query", "问题")):
            mapping[k] = {
                "term_count": library.get("index", {}).get("term_count", 0),
                "queries": library.get("queries", []),
            }
        elif any(s in lk for s in ("metadata", "元数据")):
            mapping[k] = analysis.get("metadata", {})
        elif any(s in lk for s in ("change", "log", "操作", "记录", "history")):
            mapping[k] = library.get("change_log", [])
        else:
            mapping[k] = None
    return mapping


# --------------------------------------------------------------------------- 结果组装

ENVELOPE_LIST_KEYS = ("claims", "uncertainties", "sources", "change_log",
                      "pending_human", "errors", "degradations")
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def build_skeleton(case_id, contract_keys: list, status: str) -> dict:
    result = {k: None for k in contract_keys}
    for key in ENVELOPE_LIST_KEYS:
        result[key] = []
    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {}
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    result["limitations"] = {
        "summary": "",
        "data_gaps": [],
        "tool_gaps": [],
        "scope_exclusions": [],
        "assumptions": [],
    }
    result["runtime"] = {}
    return result


def _evidence(files_by_id, *ids):
    out = []
    for i in ids:
        f = files_by_id.get(i)
        if f:
            out.append({"source_id": i, "locator": f["rel_path"], "quote": f["rel_path"]})
    return out


def build_claims(library: dict) -> tuple[list, list]:
    """由确定性分组生成 claims；启发式组同步生成 uncertainty 项。"""
    claims, uncertainties = [], []
    analysis = library.get("analysis", {})
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    n = 0

    def push(cid, kind, confidence, text, evidence):
        claims.append({"claim_id": cid, "kind": kind, "confidence": confidence,
                       "text": text, "evidence": evidence})

    for g in analysis.get("alias_groups", []):
        n += 1
        push(f"C{n:03d}", "directly_read", "fact",
             f"文件 {', '.join(g['member_ids'])} 的 sha256 相同，属同一文件的副本/别名。",
             _evidence(files_by_id, *g["member_ids"]))
    for g in analysis.get("version_groups", []):
        n += 1
        cid = f"C{n:03d}"
        push(cid, "inferred", "heuristic",
             f"文件 {', '.join(g['member_ids'])} 归一化 stem 相同且带版本标记，为同一文档的候选版本。",
             _evidence(files_by_id, *g["member_ids"]))
        uncertainties.append({
            "id": f"U{n:03d}", "about": cid, "severity": "medium",
            "basis": "stem 归一化 + 版本标记为启发式规则，未读取二进制内容语义",
            "disposition": "pending_human 复核或由行政版本备注覆盖",
        })
    for g in analysis.get("conflicts", []):
        n += 1
        cid = f"C{n:03d}"
        push(cid, "inferred", "heuristic",
             f"文件 {', '.join(g['member_ids'])} stem 相同但哈希不同且无版本标记，存在来源冲突，不擅自裁定。",
             _evidence(files_by_id, *g["member_ids"]))
        uncertainties.append({
            "id": f"U{n:03d}", "about": cid, "severity": "high",
            "basis": "同 stem 不同哈希且无显式版本标记，无法区分是版本差异还是独立文件",
            "disposition": "pending_human 裁定",
        })
    for rel in analysis.get("explicit_relations", []):
        n += 1
        push(f"C{n:03d}", "directly_read", "explicit",
             "显式关系声明：" + rel["quote"],
             [{"source_id": rel["source_id"], "locator": rel["locator"], "quote": rel["quote"]}])
    return claims, uncertainties


def build_sources(library: dict) -> list:
    return [
        {
            "source_id": f["id"], "locator": f["rel_path"], "kind": f["type"],
            "sha256": f["sha256"], "size": f["size"], "readable": f["readable"],
        }
        for f in library.get("files", [])
    ]


def determine_status(library: dict, draft: dict | None) -> str:
    # 空输入优先判 insufficient_evidence（即使 case.json 也缺失）。
    if not library.get("files"):
        return "insufficient_evidence"
    if library.get("input_error"):
        return "input_error"
    if library.get("scan_errors"):
        return "partial"
    if not draft:
        return "partial"
    conflicts = library.get("analysis", {}).get("conflicts", [])
    draft_queries = (draft or {}).get("queries") or []
    unanswered = [q for q in draft_queries if q.get("status") in (None, "insufficient_evidence", "no_direct_match")]
    if conflicts or unanswered:
        return "partial"
    return "ok"


# --------------------------------------------------------------------------- 写盘

def to_json(obj, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_json(path: Path) -> object:
    raw = path.read_bytes()
    text, _enc = decode_text(raw)
    return json.loads(text)


# --------------------------------------------------------------------------- 结果组装与草稿校验（供 main.py 调用）

ALLOWED_NOTE_KINDS = {"alias", "version", "metadata", "correction", "query_answer"}
ALLOWED_QUERY_STATUS = {"answered", "no_direct_match", "insufficient_evidence"}


def _uncertainty_summary(items: list) -> str:
    if not items:
        return "无启发式结论，全部结论由字节级/显式证据直接支持。"
    hi = sum(1 for it in items if it.get("severity") == "high")
    return f"{len(items)} 条启发式结论存在不确定性（其中 {hi} 条 severity=high，需人工裁定）。"


def _degradations(library: dict) -> list:
    out = []
    caps = library["capabilities"]["external_tools"]
    for name, ok in caps.items():
        if not ok and name in ("pdftotext", "pdftoppm", "tesseract"):
            out.append({"kind": f"no_tool:{name}",
                        "detail": f"未检测到 {name}；PDF/扫描件文本层与 OCR 属外部工具职责，本次仅做字节级盘点"})
    binary = [f["rel_path"] for f in library["files"]
              if f["type"] in ("pdf", "binary", "office", "office_zip", "office_ole", "image", "zip", "gzip")
              and not f["is_meta"]]
    if binary:
        out.append({"kind": "binary_without_text_layer", "count": len(binary),
                    "detail": "二进制/无文本层文件只计算哈希并记录，不虚构文本内容"})
    oversize = [f["rel_path"] for f in library["files"] if f.get("text_skipped_oversize")]
    if oversize:
        out.append({"kind": "text_skipped_oversize", "files": oversize})
    if library["case_missing"]:
        out.append({"kind": "case_missing", "detail": "case.json 缺失，case_id 记为 unknown"})
    return out


def _limitations(library: dict) -> dict:
    data_gaps = []
    for f in library["files"]:
        if not f["readable"]:
            data_gaps.append({"locator": f["rel_path"], "gap": "unreadable (权限/损坏)"})
        elif f.get("text_skipped_oversize"):
            data_gaps.append({"locator": f["rel_path"], "gap": "文本超限未读取"})
        elif f["type"] not in ("text",) and not f["is_meta"]:
            data_gaps.append({"locator": f["rel_path"], "gap": f"type={f['type']} 无文本层，未做内容抽取"})
    tool_gaps = [f"{k} 不可用" for k, v in library["capabilities"]["external_tools"].items()
                 if not v and k in ("pdftotext", "pdftoppm", "tesseract")]
    assumptions = [
        "版本/别名分组基于文件名 stem 归一化与模式匹配，未读取二进制内容语义；仅显式声明可覆盖为事实",
        "文件 mtime/size 为观察到的输入元数据，不用于排序键",
    ]
    scope = [
        "不做文献内容判断、学术写作、OCR 引擎开发",
        "不负责目录治理的长期归档制度",
    ]
    summary = f"数据缺口 {len(data_gaps)} 项、工具缺口 {len(tool_gaps)} 项；所有启发式结论均已标注 uncertainty，缺料不编造。"
    return {"summary": summary, "data_gaps": data_gaps, "tool_gaps": tool_gaps,
            "scope_exclusions": scope, "assumptions": assumptions}


def _pending(library: dict) -> list:
    out = []
    for g in library["analysis"]["conflicts"]:
        out.append({"id": f"P{len(out)+1:03d}", "item": g["group_id"],
                    "question": "该组文件是同文档的不同版本还是独立文件？请人工裁定或提供行政版本备注",
                    "why": "stem 相同但哈希不同且无版本标记"})
    for f in library["files"]:
        if not f["readable"]:
            out.append({"id": f"P{len(out)+1:03d}", "item": f["rel_path"],
                        "question": "该文件不可读，请确认权限或从备份恢复后再盘点",
                        "why": "os.access(R_OK) 失败，sha256 未计算"})
    return out


def fill_deterministic(library: dict, result: dict) -> None:
    """把确定性盘点写进 result.json（init 阶段即可得）。"""
    claims, uncertainties = build_claims(library)
    result["result"] = {
        "summary": {
            "file_count": len(library["files"]),
            "alias_count": len(library["analysis"]["alias_groups"]),
            "version_count": len(library["analysis"]["version_groups"]),
            "conflict_count": len(library["analysis"]["conflicts"]),
            "relation_count": len(library["analysis"]["explicit_relations"]),
            "query_count": len(library["queries"]),
        },
        "files": [
            {"id": f["id"], "rel_path": f["rel_path"], "sha256": f["sha256"],
             "size": f["size"], "type": f["type"], "readable": f["readable"],
             "is_meta": f["is_meta"]}
            for f in library["files"]
        ],
        "alias_groups": library["analysis"]["alias_groups"],
        "version_groups": library["analysis"]["version_groups"],
        "conflicts": library["analysis"]["conflicts"],
        "explicit_relations": library["analysis"]["explicit_relations"],
        "queries": [
            {"query_id": q["query_id"], "question": q["question"],
             "answer": None, "status": "insufficient_evidence", "evidence": [],
             "candidates": library.get("query_candidates", {}).get(q["query_id"], [])}
            for q in library["queries"]
        ],
        "index": library["index"],
        "capabilities": library["capabilities"],
        "metadata": library["metadata"],
    }
    result["claims"] = claims
    result["uncertainties"] = uncertainties
    result["uncertainty"] = {
        "summary": _uncertainty_summary(uncertainties),
        "count": len(uncertainties),
        "items": uncertainties,
    }
    result["sources"] = build_sources(library)
    result["change_log"] = list(library["change_log"])
    result["errors"] = [
        {"id": f"E{i:03d}", "file": e.get("file", ""), "error": e["error"], "handled": True}
        for i, e in enumerate(library["scan_errors"], 1)
    ]
    if library["case_error"]:
        result["errors"].append({"id": f"E{len(result['errors'])+1:03d}", "file": "case.json",
                                 "error": library["case_error"], "handled": True})
    result["degradations"] = _degradations(library)
    result["limitations"] = _limitations(library)
    result["pending_human"] = _pending(library)
    for k, v in resolve_contract_values(library["contract_keys"], library, None).items():
        result[k] = v


def apply_draft(library: dict, draft: dict | None) -> dict:
    """把模型语义判断并入 library，返回更新后的浅拷贝。"""
    lib = dict(library)
    if not draft:
        return lib
    queries = []
    for q in lib["queries"]:
        match = None
        for dq in (draft.get("queries") or []):
            if dq.get("query_id") == q["query_id"] or dq.get("question") == q["question"]:
                match = dq
                break
        if match:
            queries.append({
                "query_id": q["query_id"], "question": q["question"],
                "answer": match.get("answer"), "status": match.get("status", "answered"),
                "evidence": match.get("evidence") or [],
            })
        else:
            queries.append({
                "query_id": q["query_id"], "question": q["question"],
                "answer": None, "status": "insufficient_evidence", "evidence": [],
            })
    lib["queries"] = queries
    lib["draft_notes"] = draft.get("notes") or []
    admin_relations = []
    for n in lib["draft_notes"]:
        if n.get("kind") in ("alias", "version", "correction"):
            admin_relations.append({
                "relation_id": f"admin-{n.get('target', '?')}",
                "kind": n.get("kind"),
                "source_id": n.get("target"),
                "locator": (n.get("evidence") or [{}])[0].get("locator", "draft"),
                "quote": (n.get("evidence") or [{}])[0].get("quote", n.get("text", "")),
                "confidence": "explicit" if n.get("confidence") == "explicit" else "model",
                "text": n.get("text"),
            })
    lib["analysis"]["admin_overrides"] = admin_relations
    lib["contract_values"] = (draft.get("contract_values") or {})
    return lib


def build_result(library: dict, draft: dict | None, runtime: dict) -> dict:
    lib = apply_draft(library, draft)
    status = determine_status(lib, draft)
    if lib["input_error"]:
        status = "input_error"
    result = build_skeleton(lib["case_id"], lib["contract_keys"], status)
    fill_deterministic(lib, result)
    cands = lib.get("query_candidates", {})
    final_queries = []
    for q in lib["queries"]:
        qq = dict(q)
        qq["candidates"] = cands.get(q["query_id"], [])
        final_queries.append(qq)
    result["result"]["queries"] = final_queries
    for i, n in enumerate(lib.get("draft_notes", []), 1):
        result["claims"].append({
            "claim_id": f"D{i:03d}", "kind": "model_note",
            "confidence": n.get("confidence", "inferred"),
            "text": n.get("text", ""),
            "evidence": n.get("evidence") or [],
        })
    result["pending_human"] = _pending(lib)
    for k, v in resolve_contract_values(lib["contract_keys"], lib, draft).items():
        result[k] = v
    result["runtime"] = runtime
    result["limitations"] = _limitations(lib)
    return result


def _load_source_text(source_id: str, files_by_id: dict, root: Path,
                      case_text: str | None, task_text: str | None) -> str | None:
    if source_id in ("case", "case.json"):
        return case_text
    if source_id in ("task", "task.md"):
        return task_text
    f = files_by_id.get(source_id)
    if not f or f["type"] != "text":
        return None
    text, _enc, _ov = read_text_limited(root / f["rel_path"])
    return text


def verify_quote(source_id: str, locator: str, quote: str, source_text: str | None,
                 files_by_id: dict) -> tuple:
    quote = quote or ""
    locator = locator or ""
    if source_text is not None and quote in source_text:
        return True, "verbatim_hit"
    f = files_by_id.get(source_id)
    if f:
        if quote == f["rel_path"] or quote in f["rel_path"] or quote == Path(f["rel_path"]).name:
            return True, "locator_match"
        if f["type"] != "text":
            return False, "binary_no_text_layer"
    return False, "verbatim_miss"


def check_draft(draft: dict, library: dict, input_dir: Path,
                case_text: str | None, task_text: str | None) -> dict:
    report = {"ok": True, "items": [], "summary": {}}
    files_by_id = {f["id"]: f for f in library["files"]}
    if not isinstance(draft, dict):
        return {"ok": False, "items": [{"id": "draft", "ok": False, "problem": "draft not a dict"}],
                "summary": {"error": "draft not a dict"}}
    notes = draft.get("notes") or []
    queries = draft.get("queries") or []
    for i, n in enumerate(notes, 1):
        if not isinstance(n, dict):
            report["items"].append({"id": f"note-{i}", "ok": False, "problem": "note not dict"})
            report["ok"] = False
            continue
        target = n.get("target")
        if target and target not in files_by_id and target not in ("case", "task"):
            report["items"].append({"id": f"note-{i}", "ok": False, "problem": f"target unknown: {target}"})
            report["ok"] = False
            continue
        if n.get("kind") not in ALLOWED_NOTE_KINDS:
            report["items"].append({"id": f"note-{i}", "ok": False, "problem": f"bad kind: {n.get('kind')}"})
            report["ok"] = False
        ev_ok = True
        for e in (n.get("evidence") or []):
            src = e.get("source_id")
            stext = _load_source_text(src, files_by_id, input_dir, case_text, task_text)
            hit, why = verify_quote(src, e.get("locator", ""), e.get("quote", ""), stext, files_by_id)
            if not hit:
                ev_ok = False
                report["items"].append({"id": f"note-{i}", "ok": False,
                                        "problem": f"quote miss in {src}: {why}", "quote": e.get("quote")})
        if ev_ok:
            report["items"].append({"id": f"note-{i}", "ok": True})
    for i, q in enumerate(queries, 1):
        if not isinstance(q, dict):
            report["items"].append({"id": f"query-{i}", "ok": False, "problem": "query not dict"})
            report["ok"] = False
            continue
        if q.get("status") not in ALLOWED_QUERY_STATUS:
            report["items"].append({"id": f"query-{i}", "ok": False,
                                    "problem": f"bad status: {q.get('status')}"})
            report["ok"] = False
        ev_ok = True
        for e in (q.get("evidence") or []):
            src = e.get("source_id")
            stext = _load_source_text(src, files_by_id, input_dir, case_text, task_text)
            hit, why = verify_quote(src, e.get("locator", ""), e.get("quote", ""), stext, files_by_id)
            if not hit:
                ev_ok = False
                report["items"].append({"id": f"query-{i}", "ok": False,
                                        "problem": f"quote miss in {src}: {why}", "quote": e.get("quote")})
        if ev_ok:
            report["items"].append({"id": f"query-{i}", "ok": True})
    n_bad = sum(1 for it in report["items"] if not it["ok"])
    report["summary"] = {"notes": len(notes), "queries": len(queries),
                         "problems": n_bad, "ok": n_bad == 0}
    report["ok"] = n_bad == 0
    return report


def write_evidence_map(result: dict, files_by_id: dict, root: Path,
                       case_text: str | None, task_text: str | None) -> dict:
    emap = {"claims_by_id": {}, "unverified": []}
    for c in result.get("claims", []):
        evs = c.get("evidence") or []
        checked = []
        all_hit = True
        for e in evs:
            src = e.get("source_id")
            stext = _load_source_text(src, files_by_id, root, case_text, task_text)
            hit, why = verify_quote(src, e.get("locator", ""), e.get("quote", ""), stext, files_by_id)
            if not hit:
                all_hit = False
            checked.append({"source_id": src, "locator": e.get("locator"),
                            "quote": e.get("quote"), "verbatim_hit": hit, "check": why})
        emap["claims_by_id"][c["claim_id"]] = {"text": c["text"], "evidence": checked}
        if not all_hit:
            emap["unverified"].append(c["claim_id"])
    return emap


def write_inventory_md(library: dict) -> str:
    L = []
    L.append(f"# 科研文档库盘点交接清单 — case {library['case_id']}\n")
    L.append("> 本清单由确定性引擎生成，定位统一使用 `rel_path + sha256`，无时间戳，可复跑核对。\n")
    L.append("## 摘要\n")
    s = library["analysis"]
    L.append(f"- 文件 {len(library['files'])} 个；别名/副本 {len(s['alias_groups'])} 组；"
             f"候选版本 {len(s['version_groups'])} 组；冲突 {len(s['conflicts'])} 组；"
             f"显式关系 {len(s['explicit_relations'])} 条。")
    L.append(f"- 检索问题 {len(library['queries'])} 条；索引词项 {library['index']['term_count']} 个。\n")
    L.append("## 文件清单\n")
    L.append("| id | rel_path | type | size | sha256(前12) | readable |")
    L.append("|---|---|---|---|---|---|")
    for f in library["files"]:
        d = (f["sha256"] or "—")[:12]
        L.append(f"| {f['id']} | {f['rel_path']} | {f['type']} | {f['size']} | {d} | "
                 f"{'是' if f['readable'] else '否'} |")
    L.append("")
    if s["alias_groups"]:
        L.append("## 别名/副本（同 sha256）\n")
        for g in s["alias_groups"]:
            L.append(f"- {g['group_id']}: {', '.join(g['rel_paths'])}")
    if s["version_groups"]:
        L.append("\n## 候选版本（同 stem + 版本标记，待复核）\n")
        for g in s["version_groups"]:
            L.append(f"- {g['group_id']} `{g['stem']}`: {', '.join(g['rel_paths'])}")
    if s["conflicts"]:
        L.append("\n## 冲突（待人工裁定）\n")
        for g in s["conflicts"]:
            L.append(f"- {g['group_id']} `{g['stem']}`: {', '.join(g['rel_paths'])}")
    if s["explicit_relations"]:
        L.append("\n## 显式关系声明\n")
        for r in s["explicit_relations"]:
            L.append(f"- [{r['kind']}] `{r['locator']}` → {r['quote']}")
    L.append("\n## 缺口与待办\n")
    L.append(f"- errors {len(library['scan_errors'])} 条；详见 result.json 的 `errors`/`degradations`/`limitations`。")
    return "\n".join(L) + "\n"


def render_show(library: dict) -> str:
    L = []
    L.append(f"case_id: {library['case_id']}")
    L.append(f"files: {len(library['files'])} | aliases: {len(library['analysis']['alias_groups'])} "
             f"| versions: {len(library['analysis']['version_groups'])} "
             f"| conflicts: {len(library['analysis']['conflicts'])} "
             f"| relations: {len(library['analysis']['explicit_relations'])}")
    if library["case_error"]:
        L.append(f"CASE WARNING: {library['case_error']}")
    L.append("")
    L.append("## FILES (id | rel_path | type | size | readable | sha256[:12] | text_chars)")
    for f in library["files"]:
        d = f["sha256"][:12] if f["sha256"] else "NO_HASH"
        L.append(f"{f['id']} | {f['rel_path']} | {f['type']} | {f['size']} | "
                 f"{'R' if f['readable'] else 'X'} | {d} | {f['text_chars']}")
    L.append("")
    L.append("## ALIAS (identical sha256)")
    for g in library["analysis"]["alias_groups"]:
        L.append(f"{g['group_id']}: {', '.join(g['rel_paths'])}")
    L.append("")
    L.append("## VERSION (same stem + markers)")
    for g in library["analysis"]["version_groups"]:
        L.append(f"{g['group_id']} stem={g['stem']!r}: {', '.join(g['rel_paths'])}")
    L.append("")
    L.append("## CONFLICTS (same stem, diff hash, no marker) -> pending_human")
    for g in library["analysis"]["conflicts"]:
        L.append(f"{g['group_id']} stem={g['stem']!r}: {', '.join(g['rel_paths'])}")
    L.append("")
    L.append("## EXPLICIT RELATIONS")
    for r in library["analysis"]["explicit_relations"]:
        L.append(f"{r['relation_id']} [{r['kind']}] {r['locator']} :: {r['quote']}")
    L.append("")
    L.append("## QUERIES (with deterministic retrieval candidates)")
    for q in library["queries"]:
        L.append(f"{q['query_id']}: {q['question']}")
        cands = library.get("query_candidates", {}).get(q["query_id"], [])
        for c in cands[:5]:
            L.append(f"    candidate score={c['score']} {c['kind']} {c['locator']} :: {c['snippet'][:80]!r}")
    if library["scan_errors"]:
        L.append("")
        L.append("## SCAN ERRORS")
        for e in library["scan_errors"]:
            L.append(f"{e['file']}: {e['error']}")
    return "\n".join(L)

