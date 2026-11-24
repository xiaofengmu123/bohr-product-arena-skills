#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 科研文档库修复的确定性引擎（仅 Python 标准库）。

本模块只做纯函数与确定性计算，不写终产物、不依赖网络、不引入随机性。
职责范围：

    1. 输入目录盘点：文件/目录/权限/大小/sha256，坏文件降级记录；
    2. 文件类型与文本层探测（txt/md/json/csv/html/tex/bib/office/pdf）；
    3. 重复（同 sha256）、候选版本（归一化 stem + 版本标记）、来源冲突识别；
    4. 从 case.json 发现显式别名、行政版本备注、元数据、检索问题与恢复/索引操作；
    5. 倒排检索索引（ASCII 词元 + CJK bigram）与查询评分；
    6. 工作稿模板（DRAFT_TEMPLATE）与机械校验（结构 / source_id 存在 / quote 逐字命中）。

设计约束：所有输出集合在写出前排序，排序键稳定；不把环境时间、绝对路径、
进程信息混入内容级事实，从而保证相同输入两次运行的内容级产物逐字节一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
import zipfile
from pathlib import Path

MAX_TEXT_READ = 512 * 1024           # 单文本文件读取上限（字节）
MAX_EXTRACT_TEXT = 2 * 1024 * 1024   # PDF/Office 抽取文本上限
HASH_CHUNK = 1024 * 1024
MAX_INDEX_TERMS_PER_FILE = 20000

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".yaml",
    ".yml", ".xml", ".html", ".htm", ".tex", ".bib", ".rst", ".log", ".ini",
    ".cfg", ".conf", ".py", ".js", ".m", ".mm", ".do", ".r", ".sql", ".sh",
}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXT = {".pdf"}

# ---- 通用工具 -------------------------------------------------------------

def read_text_tolerant(path: Path, max_bytes: int = MAX_TEXT_READ):
    """按 utf-8-sig → utf-8 → latin-1 顺序容错读取，返回 (文本 or None, 方法)。"""
    try:
        size = path.stat().st_size
    except OSError:
        return None, "unreadable"
    if size > max_bytes:
        return None, "oversize_read_cap"
    for enc in ("utf-8-sig", "utf-8", "latin-1", "gb18030"):
        try:
            raw = path.read_bytes()
            if len(raw) > max_bytes:
                return None, "oversize_read_cap"
            text = raw.decode(enc)
            return text, f"decode:{enc}"
        except (UnicodeDecodeError, OSError):
            continue
    return None, "decode_failed"


def norm_rel(rel):
    rel = str(rel).replace("\\", "/")
    rel = re.sub(r"^\.?/+", "", rel)
    return rel


def sha256_file(path: Path):
    h = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size, None
    except OSError as exc:
        return None, None, f"hash_error:{exc.__class__.__name__}"


def stable_json(obj, indent: int = 2) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=True)


# ---- 文件类型与文本层 -----------------------------------------------------

def file_kind(rel_path: str) -> str:
    ext = Path(rel_path).suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in OFFICE_EXT:
        return "office"
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT:
        return "text"
    if ext in (".zip",):
        return "archive"
    return "binary"


def _zip_xml_text(path: Path, cap: int = MAX_EXTRACT_TEXT):
    """从 zip 容器（docx/xlsx/pptx/odt）抽取扁平文本层。"""
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            wanted = [n for n in names if re.search(
                r"(word/document\.xml|xl/sharedStrings\.xml|ppt/slides/slide\d+\.xml|content\.xml|body\.xml)$",
                n, re.I)]
            if not wanted:
                wanted = [n for n in names if n.lower().endswith(".xml")][:20]
            total = 0
            for n in wanted[:60]:
                try:
                    data = z.read(n)
                except Exception:
                    continue
                chunk = data[:cap]
                try:
                    txt = chunk.decode("utf-8", errors="ignore")
                except Exception:
                    continue
                txt = re.sub(r"<[^>]+>", " ", txt)
                txt = re.sub(r"\s+", " ", txt).strip()
                if txt:
                    out.append(txt)
                    total += len(txt)
                    if total >= cap:
                        break
            return (" ".join(out).strip()) or None
    except Exception:
        return None


def extract_file_text(path: Path, kind: str):
    """返回 (text or None, method string)。method 含 no_tool:<x> 前缀表示缺工具。"""
    if kind == "text":
        text, method = read_text_tolerant(path)
        return text, method
    if kind == "office":
        text = _zip_xml_text(path)
        return text, ("office_zip_xml" if text else "office_no_text_layer")
    if kind == "pdf":
        exe = shutil.which("pdftotext")
        if exe is None:
            return None, "no_tool:pdftotext"
        try:
            proc = subprocess.run(
                [exe, "-q", "-layout", str(path), "-"],
                capture_output=True, timeout=90)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return None, f"pdftotext_error:{exc.__class__.__name__}"
        if proc.returncode != 0:
            return None, f"pdftotext_exit:{proc.returncode}"
        txt = proc.stdout.decode("utf-8", errors="ignore")
        return (txt[:MAX_EXTRACT_TEXT] or None), "pdftotext"
    if kind == "image":
        return None, "no_tool:ocr"
    # binary / archive：不猜文本层
    text, _method = read_text_tolerant(path, MAX_TEXT_READ)
    return text, ("binary_utf8" if text else "binary_no_text_layer")


# ---- 归一化 stem 与版本/冲突分组 -------------------------------------------

_COPY_RE = [
    re.compile(r"[\(\（\[【].*?[\)\）\]】]\s*$", re.I),          # (1), (final)
    re.compile(r"(copy|副本|拷贝|备份|backup|dup|复件)[_\-.\s]*\d*\s*$", re.I),
    re.compile(r"(v|ver|version|rev|r)[_\-.\s]?\d+(\.\d+){0,2}\s*$", re.I),
    re.compile(r"[-_ .]*(final|draft|old|new|latest|定稿|初稿|终稿|修改稿|修改版|修订版|第?[-_ .]?\d+\s*版|v?[-_ .]?\d+(\.\d+){0,2})?\s*$", re.I),
]

_VMARK_RE = re.compile(
    r"(v|ver|version|rev|r)[_\-.\s]?\d+(\.\d+){0,2}|(final|draft|old|new|latest|定稿|初稿|终稿|修改稿|修改版|修订版|[\(（]\d+[\)）])",
    re.I)


def normalized_stem(rel_path: str) -> str:
    """把文件名归一化成"文档 stem"，用于跨版本/副本归并（启发式）。"""
    name = Path(str(rel_path)).name
    root, _ext = os.path.splitext(name)
    if not root:
        root = name
    s = unicodedata.normalize("NFKC", root).lower().strip()
    for _ in range(3):
        for rx in _COPY_RE:
            s = rx.sub("", s).strip()
    s = re.sub(r"[\s_\-.#]+", "-", s)
    s = re.sub(r"[-]+$", "", s)
    return s if len(s) >= 2 else unicodedata.normalize("NFKC", root).lower()


def has_version_marker(rel_path: str) -> bool:
    return bool(_VMARK_RE.search(Path(str(rel_path)).name))


# ---- 倒排索引 -------------------------------------------------------------

_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_\-\.]{1,63}", re.I)
_CJK_RUN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]{2,}")


def tokenize(text: str):
    terms = {}
    for m in _ASCII_WORD.finditer(text):
        t = m.group(0).lower()
        terms[t] = terms.get(t, 0) + 1
    for m in _CJK_RUN.finditer(text):
        run = m.group(0)
        if len(run) >= 2:
            # 连续 CJK 整段作为短语位置项
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                terms[bg] = terms.get(bg, 0) + 1
            terms[run] = terms.get(run, 0) + 1
    return terms


def score_document(text: str, query: str) -> int:
    qterms = tokenize(query)
    if not qterms:
        return 0
    dterms = tokenize(text)
    score = 0
    matched = 0
    for t in qterms:
        if t in dterms:
            matched += 1
            score += 1 + min(dterms[t], 10)
    if matched == len(qterms):
        score += 5  # 全词命中加分
    return score


# ---- case.json 特征发现（先发现，再假设） ---------------------------------

QUERY_KEYS = {"queries", "questions", "searches", "retrieval_queries",
              "检索问题", "questions_to_answer"}
REL_KEYS = {"aliases", "alias", "version_notes", "versions", "relations",
            "administrative_notes", "admin_notes", "administrative_version_notes"}
META_KEYS = {"metadata", "meta", "file_metadata"}
OPS_KEYS = {"operations", "tasks", "workorder", "recovery_ops", "index_ops",
            "recovery", "index_operations", "actions"}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return [value]


def _norm_item(item):
    """把任意 item 提升成 dict；字符串当作 {'note': ...}。"""
    if isinstance(item, dict):
        return item
    return {"note": item}


def _looks_like_rel_path(value: str) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or v.startswith(("http://", "https://", "/", "\\", "{")):
        return False
    if len(v) > 512:
        return False
    return bool(re.search(r"[A-Za-z0-9_\u4e00-\u9fff]", v))


def _find_path_fields(item, rel_paths: set):
    """递归收集 dict/list 中疑似相对路径字符串，用于事后与真实文件对齐。"""
    if isinstance(item, dict):
        for k, v in item.items():
            if k in {"path", "file", "filename", "file_path", "rel_path",
                     "target", "source", "src", "location", "locator"} and _looks_like_rel_path(v):
                rel_paths.add(norm_rel(v))
            else:
                _find_path_fields(v, rel_paths)
    elif isinstance(item, list):
        for v in item:
            _find_path_fields(v, rel_paths)
    elif isinstance(item, str) and _looks_like_rel_path(item):
        rel_paths.add(norm_rel(item))


def discover_case_features(case):
    """从 case.json 发现显式关系/元数据/查询/操作，返回四项列表。"""
    aliases, version_notes, metadata, queries, operations = [], [], [], [], []
    if not isinstance(case, dict):
        return aliases, version_notes, metadata, queries, operations
    top = case

    def lk(d, keys):
        for k in keys:
            if k in d:
                return d[k]
        return None

    # 别名
    for item in _as_list(lk(top, REL_KEYS)):
        aliases.append(_norm_item(item))
    # 元数据
    meta_raw = lk(top, META_KEYS)
    if isinstance(meta_raw, dict):
        metadata.extend(_norm_item(v) for v in meta_raw.values())
    else:
        metadata.extend(_norm_item(i) for i in _as_list(meta_raw))
    # 查询
    qid = 0
    for item in _as_list(lk(top, QUERY_KEYS)):
        d = _norm_item(item)
        text = d.get("query") or d.get("question") or d.get("text") or d.get("note") or str(item)
        qid += 1
        queries.append({
            "query_id": d.get("query_id") or d.get("id") or f"Q{qid}",
            "text": str(text),
            "source": "case.json",
        })
    # 操作
    for item in _as_list(lk(top, OPS_KEYS)):
        operations.append(_norm_item(item))
    return aliases, version_notes, metadata, queries, operations


# ---- 盘点 ---------------------------------------------------------------

def scan_inventory(input_dir: Path, case):
    """产出内容级事实：files / dirs / errors / degradations。"""
    files, dirs, errors, degradations = [], [], [], []
    if not input_dir.exists():
        errors.append({"kind": "missing_input_dir", "detail": str(input_dir)})
        return files, dirs, errors, degradations

    # 收集所有文件与目录，路径排序保证确定性
    all_files, all_dirs = [], []
    for root, dnames, fnames in os.walk(input_dir):
        dnames[:] = sorted(dnames)
        for d in dnames:
            all_dirs.append(norm_rel(os.path.relpath(os.path.join(root, d), input_dir)))
        for fn in sorted(fnames):
            all_files.append(norm_rel(os.path.relpath(os.path.join(root, fn), input_dir)))
    all_files.sort()
    all_dirs.sort()

    for rel in all_files:
        abs_p = input_dir / rel
        item = {
            "id": None, "rel_path": rel, "size": None, "sha256": None,
            "type": file_kind(rel), "text_layer": None, "text_method": None,
            "status": "ok",
        }
        try:
            st = abs_p.stat()
            item["size"] = st.st_size
            item["mode"] = oct(st.st_mode & 0o777)
            digest, _sz, err = sha256_file(abs_p)
            if digest is None:
                item["status"] = "unreadable"
                item["sha256"] = None
                errors.append({"kind": "hash_error", "file": rel, "detail": err})
            else:
                item["sha256"] = digest
        except OSError as exc:
            item["status"] = "unreadable"
            errors.append({"kind": "stat_error", "file": rel,
                           "detail": f"{exc.__class__.__name__}"})

        if item["status"] == "ok":
            text, method = extract_file_text(abs_p, item["type"])
            if text is None:
                item["text_layer"] = False
                item["text_method"] = method
                if method.startswith("no_tool:"):
                    degradations.append({"kind": method.split(":", 1)[1],
                                         "file": rel, "detail": "no_tool:" + method.split(":", 1)[1]})
            else:
                item["text_layer"] = True
                item["text_method"] = method
                item["text_len"] = len(text)
        files.append(item)

    # 分配稳定 id：按 rel_path 排序
    files_sorted = sorted(files, key=lambda f: f["rel_path"])
    for i, f in enumerate(files_sorted, 1):
        f["id"] = f"F{i:03d}"

    for d in all_dirs:
        dirs.append({"rel_path": d})
    return files_sorted, dirs, errors, degradations


def analyze(files, aliases, version_notes, metadata):
    """在盘点之上计算重复/版本/冲突分组与显式关系。"""
    dup_groups, version_groups, conflict_groups = [], [], []
    files_by_id = {f["id"]: f for f in files}

    # 重复：同 sha256（忽略未哈希文件）
    by_hash = {}
    for f in files:
        if f["sha256"]:
            by_hash.setdefault(f["sha256"], []).append(f["id"])
    for h, ids in sorted(by_hash.items(), key=lambda kv: kv[0]):
        if len(ids) >= 2:
            dup_groups.append({"sha256": h, "member_ids": sorted(ids),
                               "kind": "duplicate"})

    # 版本/冲突：同归一化 stem、不同 sha256、非空文件
    by_stem = {}
    for f in files:
        if not f["sha256"]:
            continue
        by_stem.setdefault(normalized_stem(f["rel_path"]), []).append(f)
    for stem, members in sorted(by_stem.items()):
        if len(members) < 2:
            continue
        hashes = {m["sha256"] for m in members}
        if len(hashes) < 2:
            continue  # 同名同哈希已计入 duplicate
        marker = any(has_version_marker(m["rel_path"]) for m in members)
        ids = sorted(m["id"] for m in members)
        if marker:
            version_groups.append({"stem": stem, "member_ids": ids,
                                   "kind": "version"})
        else:
            conflict_groups.append({"stem": stem, "member_ids": ids,
                                    "kind": "conflict",
                                    "note": "归一化 stem 相同、哈希不同且无版本标记"})

    # 显式关系：对齐别名/版本备注/元数据中的路径到真实文件
    explicit_relations = []
    known = {norm_rel(f["rel_path"]) for f in files}

    def resolve(rel):
        r = norm_rel(rel)
        if r in known:
            return r
        for f in files:
            if f["rel_path"].endswith(r):
                return f["rel_path"]
        return None

    for al in aliases:
        resolved = []
        for key in ("a", "b", "from", "to", "file", "path", "rel_path",
                    "source", "target", "members"):
            if key in al:
                v = al[key]
                if isinstance(v, list):
                    for x in v:
                        rr = resolve(x)
                        if rr:
                            resolved.append(rr)
                else:
                    rr = resolve(v)
                    if rr:
                        resolved.append(rr)
        resolved = sorted(set(resolved))
        if resolved:
            explicit_relations.append({
                "type": "alias",
                "members": resolved,
                "note": str(al.get("note") or al.get("reason") or ""),
                "source": "case.json",
            })
    for vn in version_notes:
        rel = None
        for key in ("file", "path", "rel_path", "target"):
            if key in vn:
                rel = resolve(vn[key])
                break
        if rel:
            explicit_relations.append({
                "type": "version_note",
                "file": rel,
                "note": str(vn.get("note") or vn.get("text") or vn.get("reason") or ""),
                "source": "case.json",
            })
    for md in metadata:
        rel = None
        for key in ("file", "path", "rel_path", "target"):
            if key in md:
                rel = resolve(md[key])
                break
        if rel:
            explicit_relations.append({
                "type": "metadata",
                "file": rel,
                "data": {k: v for k, v in md.items()
                         if k not in ("file", "path", "rel_path", "target")},
                "source": "case.json",
            })

    return {
        "duplicate_groups": dup_groups,
        "version_groups": version_groups,
        "conflict_groups": conflict_groups,
        "explicit_relations": explicit_relations,
    }


def build_index(files, text_cache):
    """构建倒排索引：term -> file_id -> 词频。text_cache 由调用方填充。"""
    index = {}
    for f in files:
        text = text_cache.get(f["id"])
        if not text:
            continue
        terms = tokenize(text)
        terms = dict(sorted(terms.items())[:MAX_INDEX_TERMS_PER_FILE])
        for t, c in terms.items():
            index.setdefault(t, {})[f["id"]] = c
    for t in index:
        index[t] = dict(sorted(index[t].items()))
    index = dict(sorted(index.items()))
    return index


def run_queries(queries, files, text_cache):
    """对 case.json 中提取的检索问题做确定性评分，返回结果列表。"""
    results = []
    for q in queries:
        scored = []
        for f in files:
            text = text_cache.get(f["id"]) or ""
            s = score_document(text, q["text"])
            if s > 0:
                scored.append({"file_id": f["id"], "rel_path": f["rel_path"],
                               "score": s})
        scored.sort(key=lambda x: (-x["score"], x["file_id"]))
        results.append({
            "query_id": q["query_id"],
            "text": q["text"],
            "status": "answered" if scored else "no_direct_match",
            "hits": scored[:20],
        })
    return results


# ---- 工作稿模板与校验 -----------------------------------------------------

DRAFT_TEMPLATE = {
    "case_id": "",
    "notes": [],
    "queries": [],
}

NOTE_KINDS = {"alias", "version", "metadata", "correction", "query_answer",
              "recovery", "index"}
CONFIDENCE_VALUES = {"explicit", "inferred", "heuristic"}
QUERY_STATUS = {"answered", "no_direct_match", "insufficient_evidence"}


def _plain_text_for_file(files, file_id, text_cache):
    for f in files:
        if f["id"] == file_id:
            return text_cache.get(file_id, ""), f
    return "", None


def validate_draft(draft, files, case_text, text_cache):
    """机械校验 draft.json，返回 (ok, report)。"""
    problems = []
    warnings = []

    if not isinstance(draft, dict):
        return False, {"ok": False, "problems": ["draft 不是对象"],
                       "warnings": []}

    if not isinstance(draft.get("notes"), list) or not isinstance(draft.get("queries"), list):
        problems.append("draft 缺少 notes 或 queries 列表")

    files_by_id = {f["id"]: f for f in files}
    known_ids = set(files_by_id) | {"case"}

    for i, note in enumerate(draft.get("notes", []) or []):
        tag = f"notes[{i}]"
        if not isinstance(note, dict):
            problems.append(f"{tag} 不是对象")
            continue
        if note.get("target") not in known_ids:
            problems.append(f"{tag}.target={note.get('target')!r} 不在已知 source_id 中")
        if note.get("kind") not in NOTE_KINDS:
            warnings.append(f"{tag}.kind={note.get('kind')!r} 非标准取值")
        if note.get("confidence") not in CONFIDENCE_VALUES:
            warnings.append(f"{tag}.confidence={note.get('confidence')!r} 非标准取值")
        if not isinstance(note.get("text"), str) or not note["text"].strip():
            problems.append(f"{tag}.text 必须为非空字符串")
        ev = note.get("evidence") or []
        if not isinstance(ev, list):
            problems.append(f"{tag}.evidence 必须是列表")
            continue
        for j, e in enumerate(ev):
            etag = f"{tag}.evidence[{j}]"
            if not isinstance(e, dict):
                problems.append(f"{etag} 不是对象")
                continue
            src = e.get("source_id")
            if src not in known_ids:
                problems.append(f"{etag}.source_id={src!r} 未知")
                continue
            loc = e.get("locator")
            quote = e.get("quote")
            if not isinstance(loc, str) or not loc.strip():
                problems.append(f"{etag}.locator 必须为非空字符串")
            else:
                expect = None
                if src == "case":
                    expect = case_text
                else:
                    matched = [f for f in files if f["id"] == src]
                    if matched:
                        if not Path(loc).exists() and loc != matched[0]["rel_path"]:
                            warnings.append(f"{etag}.locator={loc!r} 与文件相对路径不一致")
            if isinstance(quote, str) and quote:
                hay = case_text if src == "case" else text_cache.get(src, "")
                if hay and quote not in hay:
                    problems.append(f"{etag}.quote 未在 source 文本中逐字命中")
                elif not hay:
                    warnings.append(f"{etag}.quote 无法验证（source 无文本层）")
            elif quote is None:
                warnings.append(f"{etag}.quote 为空，弱化为 not_text_verifiable")

    for i, q in enumerate(draft.get("queries", []) or []):
        tag = f"queries[{i}]"
        if not isinstance(q, dict):
            problems.append(f"{tag} 不是对象")
            continue
        if not isinstance(q.get("query_id"), str) or not q["query_id"].strip():
            problems.append(f"{tag}.query_id 必须为非空字符串")
        if q.get("status") not in QUERY_STATUS:
            warnings.append(f"{tag}.status={q.get('status')!r} 非标准取值")
        ev = q.get("evidence") or []
        for j, e in enumerate(ev or []):
            if isinstance(e, dict) and isinstance(e.get("quote"), str) and e["quote"]:
                src = e.get("source_id")
                hay = case_text if src == "case" else text_cache.get(src, "")
                if hay and e["quote"] not in hay:
                    problems.append(f"{tag}.evidence[{j}].quote 未命中")

    report = {"ok": not problems, "problems": problems, "warnings": warnings,
              "note_count": len(draft.get("notes", []) or []),
              "query_count": len(draft.get("queries", []) or [])}
    return not problems, report


# ---- 结果摘要与契约回填 ---------------------------------------------------

def summary_of(library):
    a = library.get("analysis", {})
    stats = library.get("index_stats", {})
    return {
        "files": stats.get("total_files", len(library.get("files", []))),
        "dirs": len(library.get("dirs", [])),
        "duplicate_groups": len(a.get("duplicate_groups", [])),
        "version_groups": len(a.get("version_groups", [])),
        "conflict_groups": len(a.get("conflict_groups", [])),
        "explicit_relations": len(a.get("explicit_relations", [])),
        "queries": len(library.get("queries", {}).get("raw", [])),
        "operations": len(library.get("operations", [])),
        "index_terms": stats.get("terms", 0),
        "files_with_text": stats.get("files_with_text", 0),
    }


def contract_value(key, default, library, claims, sources, change_log,
                   uncertainty, errors, degradations):
    """按 output_contract 字段名启发式回填真实计算值，缺料回退 default。"""
    kl = key.lower()
    a = library.get("analysis", {})
    files = library.get("files", [])
    q = library.get("queries", {})
    if any(w in kl for w in ("library", "inventory", "file", "manifest", "index")):
        return [{"id": f["id"], "rel_path": f["rel_path"], "size": f.get("size"),
                 "sha256": f.get("sha256"), "type": f.get("type"),
                 "text_layer": f.get("text_layer")} for f in files]
    if any(w in kl for w in ("version", "relation")):
        return a.get("version_groups", []) + a.get("explicit_relations", [])
    if any(w in kl for w in ("alias", "duplicate", "副本", "copy")):
        return a.get("duplicate_groups", [])
    if "conflict" in kl:
        return a.get("conflict_groups", [])
    if any(w in kl for w in ("note", "annotation", "批注", "claim")):
        return claims
    if any(w in kl for w in ("query", "search", "retrieval", "检索")):
        return q.get("results", [])
    if any(w in kl for w in ("metadata", "meta")):
        return [r for r in a.get("explicit_relations", []) if r.get("type") == "metadata"]
    if any(w in kl for w in ("change", "log", "操作", "history")):
        return change_log
    if "source" in kl:
        return sources
    if any(w in kl for w in ("summary", "result", "stat")):
        return summary_of(library)
    if "uncertain" in kl:
        return uncertainty
    if "error" in kl:
        return errors
    if any(w in kl for w in ("degradation", "limit", "限制")):
        return degradations
    return default