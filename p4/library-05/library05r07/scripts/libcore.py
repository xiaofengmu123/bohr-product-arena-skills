#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""libcore.py — 确定性文档库盘点/去重/版本/别名/索引引擎（只用标准库）。

设计取舍（鲁棒优先，与"机械提取语义"路线相区别）：
1. 一切失败写入 errors/degradations，不抛到调用方、不靠退出码表达；
2. sha256 按 1 MiB 分块流式计算——超大文件也照常哈希，仅文本预览设体积上限；
3. 编码探测固定顺序 BOM -> utf-8 -> gb18030 -> latin-1，逐级容错，绝不猜错后抛异常；
4. 产物除 runtime 外不含任何时间戳，同一输入两次运行逐字节一致；
5. 每条结论必须带 source_id + locator + 逐字 quote，check 阶段机械验证 quote 命中。
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from pathlib import Path

SKILL_VERSION = "1.1.0"

# 文本预览与整读上限：哈希不受此限制（分块流式）。
MAX_TEXT_PREVIEW_CHARS = 1600
MAX_FULL_TEXT_CHARS = 64 * 1024
MAX_READ_BYTES = 4 * 1024 * 1024
HASH_CHUNK = 1024 * 1024

ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime",
)
ALLOWED_STATUS = frozenset(
    {"ok", "partial", "insufficient_evidence", "input_error"}
)

TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "jsonl",
    "yaml", "yml", "xml", "html", "htm", "log", "bib", "tex",
    "py", "r", "m", "jl", "sh", "css", "js", "rtf", "ini",
    "cfg", "toml", "org", "rst",
}

LIST_LIKE_KEY_HINTS = (
    "list", "items", "array", "quer", "question", "search", "answer",
    "relation", "alias", "version", "note", "annotation",
    "file", "inventor", "entries", "records", "groups", "claims", "sources",
)

# ---------------------------------------------------------------- 基础工具


def natural_key(s: str):
    """自然排序：把数字段当整数，保证 F002 排在 F010 前且跨平台一致。"""
    parts = re.split(r"(\d+)", s)
    out = []
    for p in parts:
        if p == "":
            continue
        out.append((0, int(p)) if p.isdigit() else (1, p.lower()))
    return out


def decode_bytes(data: bytes):
    """确定性多编码探测。返回 (text, encoding, error)。"""
    if not data:
        return "", "", None
    # BOM
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig", None
        except Exception as e:  # noqa: BLE001
            return "", "", f"utf-8-sig decode failed: {e}"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16", None
        except Exception as e:  # noqa: BLE001
            return "", "", f"utf-16 decode failed: {e}"
    # 固定顺序：utf-8 -> gb18030 -> latin-1
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, ValueError):
            continue
    try:
        return data.decode("latin-1"), "latin-1", None
    except Exception as e:  # 理论不可达
        return "", "", f"decode failed: {e}"


def read_text_file(path: Path):
    """读取文本文件。返回 (raw_text, verifiable_text, encoding, error)。"""
    try:
        data = path.read_bytes()
    except OSError as e:
        return "", "", None, f"read failed: {e}"
    if len(data) > MAX_READ_BYTES:
        # 超限：只读头部作为预览，标记为截断/降级，不抛异常。
        head = data[:MAX_READ_BYTES]
        raw, enc, err = decode_bytes(head)
        marker = raw[:MAX_FULL_TEXT_CHARS]
        return (
            marker,
            marker + f"\n[truncated at {MAX_READ_BYTES} bytes]",
            enc,
            f"text truncated: {len(data)} bytes > {MAX_READ_BYTES}",
        )
    raw, enc, err = decode_bytes(data)
    if err:
        return "", "", enc, err
    return raw, raw[:MAX_FULL_TEXT_CHARS], enc, None


def sha256_file(path: Path):
    """流式 sha256，任何大小都可计算。返回 (hex_digest, error)。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as e:
        return None, f"hash failed: {e}"


def permission_info(path: Path):
    try:
        st = path.stat()
        mode = st.st_mode
        return {
            "mode_octal": oct(stat.S_IMODE(mode)),
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
        }
    except OSError as e:
        return {"mode_octal": None, "readable": False, "writable": False,
                "note": f"stat failed: {e}"}


def guess_file_type(name: str):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext:
        return ext
    return "no_extension"


def _cjk_bigrams(text: str):
    out = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def tokenize(text: str):
    if not text:
        return set()
    toks = set(t.lower() for t in re.findall(r"[a-z0-9_]{2,}", text, re.I))
    toks |= _cjk_bigrams(text)
    return toks


# ---------------------------------------------------------------- 文件名归一


_VERSION_PATTERNS = [
    re.compile(r"(?i)[_\s-]?(v|ver|rev|version)[_\s-]?\d+(\.\d+)*[a-z]?$"),
    re.compile(r"(?i)[_\s-]?(final|final\d*|draft|draft\d*|latest|new|old|初稿|终稿|定稿|新版|旧版|最新)$"),
    re.compile(r"\(\d+\)$"),
    re.compile(r"[_\s]\d+(\.\d+)*$"),
]

_VERSION_MARKER_RE = re.compile(
    r"(?i)(v|ver|rev|version)[_\s.-]?\d|^(v\d)|[_\s-](final|draft|latest|new|old)\b"
    r"|[\u521d\u7ec8\u5b9a\u65b0\u65e7][\u7a3f\u7248]|\(\d+\)$",
)


def norm_stem(path_str: str) -> str:
    name = os.path.basename(path_str)
    base, _ext = os.path.splitext(name)
    for pat in _VERSION_PATTERNS:
        base = pat.sub("", base)
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[\s_\-]+", " ", base).strip().lower()
    return base or name.lower()


def has_version_marker(name: str) -> bool:
    base, _ext = os.path.splitext(os.path.basename(name))
    return bool(_VERSION_MARKER_RE.search(base))


# ---------------------------------------------------------------- 盘点扫描


def scan_directory(input_dir: Path, errors: list, degradations: list):
    files = []
    directories = []
    if not input_dir.exists():
        errors.append(f"input dir missing: {input_dir}")
        return {"files": files, "directories": directories}

    all_paths = []
    for root, dirs, fnames in os.walk(input_dir):
        rootp = Path(root)
        try:
            rel_dir = rootp.relative_to(input_dir)
        except ValueError:
            rel_dir = rootp
        dirs.sort(key=lambda d: natural_key(d))
        if str(rel_dir) != ".":
            directories.append({
                "rel_path": str(rel_dir).replace(os.sep, "/"),
                "permissions": permission_info(rootp),
            })
        for fn in sorted(fnames, key=natural_key):
            all_paths.append((rootp / fn, rootp, fn))

    all_paths.sort(key=lambda t: natural_key(str(t[0].relative_to(input_dir))))

    for idx, (fp, rootp, fn) in enumerate(all_paths):
        try:
            rel_path = str(fp.relative_to(input_dir)).replace(os.sep, "/")
        except ValueError:
            rel_path = fn
        entry = {
            "id": f"F{idx + 1:03d}",
            "rel_path": rel_path,
            "name": fn,
            "parent_dir": str(Path(rel_path).parent).replace(os.sep, "/"),
            "size": None,
            "sha256": None,
            "file_type": guess_file_type(fn),
            "encoding": None,
            "permissions": permission_info(fp),
            "status": "ok",
            "verifiable_text": "",
            "text_preview": "",
        }
        # 大小
        try:
            st = fp.stat()
            entry["size"] = st.st_size
        except OSError as e:
            entry["status"] = "unreadable"
            entry["verifiable_text"] = f"{rel_path} {fn} unreadable"
            degradations.append({"file": rel_path, "issue": f"stat failed: {e}"})
            files.append(entry)
            continue
        # 哈希（流式，超大也照常）
        digest, herr = sha256_file(fp)
        if herr:
            entry["status"] = "unreadable" if entry["status"] == "ok" else entry["status"]
            degradations.append({"file": rel_path, "issue": herr})
        entry["sha256"] = digest
        # 文本层
        if entry["file_type"] in TEXT_EXTENSIONS and entry["size"] <= MAX_READ_BYTES:
            raw, vtext, enc, terr = read_text_file(fp)
            entry["encoding"] = enc
            if terr:
                degradations.append({"file": rel_path, "issue": terr})
            if vtext:
                entry["verifiable_text"] = vtext
                entry["text_preview"] = vtext[:MAX_TEXT_PREVIEW_CHARS]
            else:
                entry["verifiable_text"] = _surface(entry)
                entry["text_preview"] = entry["verifiable_text"]
        elif entry["size"] == 0:
            entry["status"] = "empty"
            entry["verifiable_text"] = f"{rel_path} (empty file)"
            entry["text_preview"] = entry["verifiable_text"]
        else:
            # 二进制或超限文本：只提供表面事实，不虚构文本层。
            if entry["file_type"] not in TEXT_EXTENSIONS:
                entry["verifiable_text"] = _surface(entry)
            else:
                raw, vtext, enc, terr = read_text_file(fp)
                entry["encoding"] = enc
                if terr:
                    degradations.append({"file": rel_path, "issue": terr})
                entry["verifiable_text"] = vtext if vtext else _surface(entry)
            entry["text_preview"] = entry["verifiable_text"][:MAX_TEXT_PREVIEW_CHARS]
        files.append(entry)

    return {"files": files, "directories": directories}


def _surface(entry: dict) -> str:
    return " ".join(
        str(x) for x in (
            entry.get("rel_path", ""),
            entry.get("name", ""),
            entry.get("file_type", ""),
            entry.get("size", ""),
            entry.get("sha256", ""),
        ) if x not in (None, "")
    )


# ---------------------------------------------------------------- schema/契约


def build_skeleton(case_id: str, contract_keys: list, status: str) -> dict:
    result = {}
    for key in contract_keys:
        result[key] = _contract_default(key)
    for key in ENVELOPE_KEYS:
        if key in ("claims", "uncertainties", "sources", "change_log",
                   "pending_human", "errors", "degradations"):
            result[key] = []
        elif key == "result":
            result[key] = {}
        elif key == "runtime":
            result[key] = {}
        else:
            result[key] = None
    result["case_id"] = case_id
    result["status"] = status
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    return result


def _contract_default(key: str):
    k = str(key).lower()
    for hint in LIST_LIKE_KEY_HINTS:
        if hint in k:
            return []
    return None


# ---------------------------------------------------------------- 关系分析


def analyze_relations(files: list, explicit_relations: list):
    by_id = {f["id"]: f for f in files}
    by_name = {}
    for f in files:
        by_name[f["name"]] = f["id"]
        by_name[f["rel_path"]] = f["id"]

    def resolve_ids(raw_ids):
        out = []
        for r in raw_ids or []:
            if r in by_id:
                out.append(r)
            elif isinstance(r, str) and r in by_name:
                out.append(by_name[r])
        return sorted(set(out))

    by_sha = {}
    for f in files:
        if f.get("sha256"):
            by_sha.setdefault(f["sha256"], []).append(f["id"])

    duplicate_groups = []
    for sha, ids in by_sha.items():
        if len(ids) > 1:
            duplicate_groups.append({
                "sha256": sha,
                "member_ids": sorted(ids),
                "basis": "identical_bytes",
            })

    by_stem = {}
    for f in files:
        by_stem.setdefault(norm_stem(f["rel_path"]), []).append(f)

    version_groups = []
    conflict_groups = []
    for stem, members in by_stem.items():
        # 按内容（sha256）去重：版本/冲突分析只针对"不同内容"的代表文件，
        # 字节级副本另归 duplicate_groups，避免把副本误当版本。
        reps_by_sha = {}
        for m in sorted(members, key=lambda m: m["id"]):
            reps_by_sha.setdefault(m.get("sha256") or f'unknown:{m["id"]}', m)
        reps = list(reps_by_sha.values())
        if len(reps) < 2:
            continue
        # 行政版本备注 / 显式关系可覆盖启发式冲突
        marked = [m for m in reps if has_version_marker(m["name"])]
        if len(marked) == len(reps) or _explicit_version(explicit_relations, reps, by_name):
            version_groups.append({
                "stem": stem,
                "member_ids": [m["id"] for m in reps],
                "basis": "version_marker" if len(marked) == len(reps) else "explicit_note",
            })
        else:
            conflict_groups.append({
                "stem": stem,
                "member_ids": [m["id"] for m in reps],
                "basis": "same_stem_different_hash_no_marker",
            })

    # 显式别名关系（行政备注中声明 is_alias_of / same_as）
    alias_groups = []
    seen_ids = set()
    for rel in explicit_relations:
        if rel.get("kind") in ("alias", "same_as", "is_alias_of"):
            ids = resolve_ids(rel.get("member_ids") or rel.get("files"))
            if ids and tuple(ids) not in seen_ids:
                alias_groups.append({"member_ids": ids, "basis": rel})
                seen_ids.add(tuple(ids))

    return {
        "duplicate_groups": duplicate_groups,
        "version_groups": version_groups,
        "conflict_groups": conflict_groups,
        "alias_groups": alias_groups,
    }


def _explicit_version(relations, members, by_name):
    ids = {m["id"] for m in members}
    for rel in relations:
        if rel.get("kind") in ("version", "newer_than", "replaces", "supersedes"):
            raw = rel.get("member_ids") or rel.get("files") or []
            rids = set(raw)
            if rids & ids:
                return True
            # 名称引用解析
            for r in raw:
                if isinstance(r, str) and by_name.get(r) in ids:
                    return True
    return False


# ---------------------------------------------------------------- 检索索引


def build_index(files: list, metadata_texts: list):
    term_map = {}
    file_docs = {}
    for f in files:
        text = " ".join([
            f.get("name", ""),
            f.get("rel_path", ""),
            f.get("file_type", ""),
            f.get("verifiable_text", ""),
        ])
        for extra in metadata_texts:
            if isinstance(extra, str):
                text += " " + extra
        toks = tokenize(text)
        file_docs[f["id"]] = {"tokens": sorted(toks), "text": f.get("verifiable_text", ""),
                              "locator": f["rel_path"]}
        for t in toks:
            term_map.setdefault(t, []).append(f["id"])
    for t in term_map:
        term_map[t] = sorted(set(term_map[t]))
    return {
        "terms": {t: term_map[t] for t in sorted(term_map)},
        "file_tokens": {fid: sorted(file_docs[fid]["tokens"]) for fid in sorted(file_docs)},
        "by_file": file_docs,
    }


def answer_queries(queries, files, index):
    answers = []
    by_id = {f["id"]: f for f in files}
    term_map = index["terms"]
    for q in queries:
        qid = q.get("query_id", "Q?")
        text = q.get("text", "")
        toks = tokenize(text)
        hits = {}
        for t in toks:
            for fid in term_map.get(t, []):
                hits[fid] = hits.get(fid, 0) + 1
        ranked = sorted(hits.items(), key=lambda kv: (-kv[1], natural_key(kv[0])))
        top = []
        for fid, score in ranked[:5]:
            doc = index["by_file"].get(fid, {})
            quote = _quote_snippet(doc.get("text", ""), toks) or doc.get("locator", "")
            top.append({"source_id": fid, "locator": doc.get("locator", ""),
                        "score": score, "quote": quote})
        if top:
            answers.append({"query_id": qid, "query": text, "status": "answered",
                            "hits": top})
        elif text.strip():
            answers.append({"query_id": qid, "query": text,
                            "status": "no_direct_match", "hits": []})
        else:
            answers.append({"query_id": qid, "query": text,
                            "status": "insufficient_evidence", "hits": []})
    return answers


def _quote_snippet(text, toks):
    if not text:
        return ""
    low = text.lower()
    for t in sorted(toks, key=len, reverse=True):
        pos = low.find(t.lower())
        if pos >= 0:
            start = max(0, pos - 40)
            end = min(len(text), pos + len(t) + 60)
            snippet = text[start:end].replace("\n", " ")
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet += "…"
            return snippet
    return ""


# ---------------------------------------------------------------- 证据校验


def verify_draft(library: dict, draft: dict, case_raw_text: str, task_raw_text: str):
    """校验 draft 每条例证：source_id 存在 + quote 逐字命中。返回 (verified_draft, issues)。"""
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    surfaces = {}
    for fid, f in files_by_id.items():
        surfaces[fid] = f.get("verifiable_text", "")
    surfaces["case"] = case_raw_text
    surfaces["task"] = task_raw_text

    issues = []
    vs = {"case_id": draft.get("case_id"), "notes": [], "queries": [],
          "contract": draft.get("contract") or {}, "recoveries": draft.get("recoveries") or []}

    def verify_evidence(ev_list, ctx):
        out = []
        for ev in ev_list or []:
            sid = ev.get("source_id", "")
            loc = ev.get("locator", ev.get("desc", ""))
            quote = ev.get("quote", "")
            rec = {"source_id": sid, "locator": loc, "quote": quote}
            if sid not in surfaces:
                rec["verified"] = False
                issues.append({"context": ctx, "source_id": sid,
                               "issue": "source_id_not_found"})
            elif quote and quote not in surfaces[sid]:
                rec["verified"] = False
                issues.append({"context": ctx, "source_id": sid, "locator": loc,
                               "issue": "quote_miss", "quote": quote[:60]})
            else:
                rec["verified"] = True
            out.append(rec)
        return out

    for n in draft.get("notes", []):
        nn = dict(n)
        nn["evidence"] = verify_evidence(n.get("evidence"), f'note:{n.get("target", "?")}')
        vs["notes"].append(nn)
    for q in draft.get("queries", []):
        qn = dict(q)
        qn["evidence"] = verify_evidence(qn.get("evidence"), f'query:{qn.get("query_id", "?")}')
        vs["queries"].append(qn)
    return vs, issues


# ---------------------------------------------------------------- 组装


def auto_fill(key: str, library: dict, summary: dict, index: dict):
    """把 output_contract 键名模糊映射到真实产物（缺省回 null/[]）。"""
    k = str(key).lower()
    groups = library.get("analysis", {})
    if any(t in k for t in ("inventor", "files", "file", "清单", "盘点", "目录")):
        return summary
    if any(t in k for t in ("quer", "question", "search", "answer", "检索", "问题", "答案")):
        return library.get("query_answers", [])
    if any(t in k for t in ("version", "版本")):
        return groups.get("version_groups", [])
    if any(t in k for t in ("alias", "别名", "relation", "关联", "reference")):
        return groups.get("alias_groups", []) + library.get("explicit_relations", [])
    if any(t in k for t in ("duplicate", "重复", "副本")):
        return groups.get("duplicate_groups", [])
    if any(t in k for t in ("conflict", "冲突")):
        return groups.get("conflict_groups", [])
    if any(t in k for t in ("index", "索引", "检索", "ir_")):
        return {"term_count": len(index.get("terms", {})),
                "sample_terms": list(index.get("terms", {}))[:50]}
    if any(t in k for t in ("change", "变更", "操作", "日志", "log")):
        return library.get("change_log", [])
    if any(t in k for t in ("summary", "摘要", "result", "结果")):
        return summary
    return _contract_default(str(key))


def finalize_result(case_id, contract_keys, library, draft, summary, index,
                    runtime, errors, degradations, case_raw_err):
    result = build_skeleton(case_id, contract_keys, "partial")
    result["status"] = _derive_status(library, draft, errors, degradations, case_raw_err)
    result["result"] = {
        "inventory_summary": summary,
        "duplicate_groups": library.get("analysis", {}).get("duplicate_groups", []),
        "version_groups": library.get("analysis", {}).get("version_groups", []),
        "alias_groups": library.get("analysis", {}).get("alias_groups", []),
        "conflict_groups": library.get("analysis", {}).get("conflict_groups", []),
        "explicit_relations": library.get("explicit_relations", []),
        "query_answers": library.get("query_answers", []),
        "index_stats": {"term_count": len(index.get("terms", {})),
                        "file_count": len(library.get("files", []))},
    }
    result["claims"] = _build_claims(library, draft)
    result["sources"] = _build_sources(library)
    result["uncertainties"] = _build_uncertainties(library, draft)
    result["change_log"] = library.get("change_log", [])
    result["pending_human"] = library.get("pending_human", [])
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = runtime
    result["uncertainty"] = {
        "summary": _uncertainty_summary(library),
        "count": len(result["uncertainties"]),
        "items": result["uncertainties"],
    }
    # 契约回填（draft.contract 显式覆盖优先）
    overrides = (draft or {}).get("contract", {}) or {}
    for key in contract_keys:
        if key in overrides and overrides[key] is not None:
            result[key] = overrides[key]
        else:
            result[key] = auto_fill(key, library, summary, index)
    return result


def _derive_status(library, draft, errors, degradations, case_raw_err):
    files = library.get("files", [])
    has_material = bool(files or library.get("directories"))
    if case_raw_err and has_material:
        # 坏 JSON / 缺 case.json 但有材料：仍盘点，但输入契约无法确认
        return "input_error"
    if not has_material:
        return "insufficient_evidence"
    if not draft:
        return "partial"
    if errors or degradations:
        return "partial"
    if library.get("pending_human"):
        return "partial"
    if library.get("analysis", {}).get("conflict_groups"):
        return "partial"
    return "ok"


def _build_claims(library, draft):
    claims = []
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    n = 0
    groups = library.get("analysis", {})

    def ev_for(*ids):
        out = []
        for i in sorted(set(ids)):
            f = files_by_id.get(i)
            if f:
                out.append({"source_id": i, "locator": f["rel_path"],
                            "quote": f["rel_path"], "verified": True})
        return out

    for g in groups.get("duplicate_groups", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": "directly_read",
                       "confidence": "high",
                       "text": "文件 %s 的 sha256 相同，为同一文件的字节级副本（重复下载）。" % ", ".join(g["member_ids"]),
                       "evidence": ev_for(*g["member_ids"])})
    for g in groups.get("version_groups", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": "inferred",
                       "confidence": "explicit" if g["basis"] == "explicit_note" else "heuristic",
                       "text": "文件 %s 属于同一文档的候选版本/改版。" % ", ".join(g["member_ids"]),
                       "evidence": ev_for(*g["member_ids"])})
    for g in groups.get("conflict_groups", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": "inferred",
                       "confidence": "heuristic",
                       "text": "文件 %s 归一化 stem 相同但哈希不同且无版本标记，存在来源冲突，待人工裁定。" % ", ".join(g["member_ids"]),
                       "evidence": ev_for(*g["member_ids"])})
    for g in groups.get("alias_groups", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": "directly_read",
                       "confidence": "explicit",
                       "text": "文件 %s 存在显式别名关系。" % ", ".join(g["member_ids"]),
                       "evidence": ev_for(*g["member_ids"])})
    # 模型补充的 note 也进入 claims
    for note in (draft or {}).get("notes", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": note.get("kind", "correction"),
                       "confidence": note.get("confidence", "inferred"),
                       "text": note.get("text", ""),
                       "evidence": note.get("evidence", [])})
    return claims


def _build_sources(library):
    return [{"source_id": f["id"], "rel_path": f["rel_path"], "sha256": f.get("sha256"),
             "file_type": f.get("file_type")} for f in library.get("files", [])]


def _build_uncertainties(library, draft):
    out = []
    for g in library.get("analysis", {}).get("conflict_groups", []):
        out.append({"target": ", ".join(g["member_ids"]), "kind": "source_conflict",
                    "detail": "同 stem 不同哈希且无版本标记"})
    for note in (draft or {}).get("notes", []):
        if note.get("confidence") == "inferred":
            out.append({"target": note.get("target", "?"), "kind": note.get("kind"),
                        "detail": note.get("text", "")[:120]})
    return out


def _uncertainty_summary(library):
    n_conflict = len(library.get("analysis", {}).get("conflict_groups", []))
    n_pending = len(library.get("pending_human", []))
    bits = []
    if n_conflict:
        bits.append(f"{n_conflict} 个来源冲突")
    if n_pending:
        bits.append(f"{n_pending} 项待人工")
    return "；".join(bits) or "无"