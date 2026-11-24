#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""libcore.py — 确定性盘点引擎（纯标准库）。

职责：扫描输入目录、哈希去重、版本/别名/冲突分组、显式关系读取、
检索语料与查询匹配、恢复/索引操作的确定性执行记录。
不写 result.json（由 main.py 组装），只产出 lib 字典供后续合并。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from rules import nfkc, normalize_stem, version_tokens, stable_dump

TEXT_READ_LIMIT = 2 * 1024 * 1024          # 文本层逐字证据读取上限
HASH_CAP = 64 * 1024 * 1024                # 超过则 hash_skipped_oversize
PDF_TEXT_LIMIT = 512 * 1024               # 外部 PDF 文本层读取上限

_EXT_TEXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".yaml",
             ".yml", ".log", ".tex", ".bib", ".py", ".html", ".htm", ".rst",
             ".org", ".nfo"}
_EXT_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}


def sha256_of(path: Path) -> tuple:
    """返回 (hexdigest_or_None, size)。超大或不可读返回 (None, size)。"""
    h = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > HASH_CAP:
                    return None, size
                h.update(chunk)
    except OSError:
        return None, -1
    return h.hexdigest(), size


def probe_text(p: Path) -> dict:
    """文本层探测。返回 {type, encoding, text_ok, text}。"""
    ext = p.suffix.lower()
    if ext in _EXT_IMAGE:
        return {"type": "image", "encoding": None, "text_ok": False, "text": ""}
    try:
        size = p.stat().st_size
    except OSError:
        return {"type": "unreadable", "encoding": None, "text_ok": False, "text": ""}
    if size > TEXT_READ_LIMIT:
        return {"type": ext.lstrip(".") + "_large" if ext else "binary_large",
                "encoding": None, "text_ok": False, "text": ""}
    try:
        raw = p.read_bytes()
    except OSError:
        return {"type": "unreadable", "encoding": None, "text_ok": False, "text": ""}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
        return {"type": ext.lstrip(".") or "text", "encoding": "latin-1-fallback",
                "text_ok": False, "text": text}
    text_ok = ("\ufffd" not in text) and ("\x00" not in text)
    return {"type": ext.lstrip(".") or "text", "encoding": "utf-8",
            "text_ok": text_ok, "text": text}


def pdf_text(p: Path) -> tuple:
    """可选 PDF 文本层：调用 pdftotext（若在 PATH）。返回 (文本, 状态)。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return "", "no_tool:pdftotext"
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.txt"
        try:
            subprocess.run([exe, "-layout", str(p), str(out)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if out.exists():
                text = out.read_text(encoding="utf-8", errors="replace")[:PDF_TEXT_LIMIT]
                return text, "ok" if text.strip() else "empty"
        except Exception:
            return "", "fail"
    return "", "fail"


def _dir_of(rel: str) -> str:
    parts = rel.split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


def scan(input_dir: Path) -> dict:
    """扫描 /input，返回 lib 字典（files 已含稳定 id）。"""
    files, corpus = [], {}
    config_files = {}
    meta = {"dirs": 0, "total_bytes": 0, "unreadable": [], "no_text": [],
            "oversize": [], "files_total": 0, "config_files": []}

    if not input_dir.exists():
        meta["input_missing"] = True
        lib = {"files": files, "analysis": {}, "corpus": corpus,
               "config_files": config_files, "scan_meta": meta,
               "input_dir": str(input_dir)}
        lib["analysis"] = analyze(lib)
        return lib

    for root, dirs, fnames in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for d in dirs:
            meta["dirs"] += 1
        for name in sorted(fnames):
            if name.startswith("."):
                continue
            p = Path(root) / name
            rel = str(p.relative_to(input_dir)).replace("\\", "/")
            if rel in ("case.json", "task.md"):
                try:
                    config_files[rel] = p.read_text(encoding="utf-8-sig",
                                                    errors="replace")[:TEXT_READ_LIMIT]
                except OSError:
                    config_files[rel] = ""
                meta["config_files"].append(rel)
                continue
            prob = probe_text(p)
            rec = {
                "rel_path": rel, "name": p.name, "stem": normalize_stem(p.name),
                "dir": _dir_of(rel), "extension": p.suffix.lower(),
                "size": -1, "sha256": None, "type": prob["type"],
                "encoding": prob["encoding"], "text_ok": prob["text_ok"],
                "permissions": None, "version_tokens": version_tokens(p.name),
            }
            try:
                st = p.stat()
                rec["size"] = st.st_size
                rec["permissions"] = oct(st.st_mode & 0o777)
            except OSError:
                rec["type"] = "unreadable"
            rec["sha256"], rec["size"] = sha256_of(p)
            if rec["extension"] == ".pdf":
                text, state = pdf_text(p)
                rec["pdf_text_state"] = state
                if state == "ok":
                    rec["pdf_text_ok"] = True
                    rec["text_ok"] = True
                    prob["text_ok"] = True
                    prob["text"] = text
                else:
                    rec["pdf_text_ok"] = False
            files.append(rec)
            if prob["text_ok"]:
                corpus[rec.get("rel_path")] = prob["text"]

    # 分配稳定 id
    for i, f in enumerate(files):
        f["id"] = f"F{i + 1:03d}"
    # corpus 以 id 为键（供 evidence source_id 校验）
    corpus = {f["id"]: corpus.get(f["rel_path"], "") for f in files if f.get("rel_path") in corpus}

    for f in files:
        meta["files_total"] += 1
        if f["size"] >= 0:
            meta["total_bytes"] += f["size"]
        if f.get("sha256") is None:
            meta["oversize"].append(f["id"])
        if not f.get("text_ok"):
            meta["no_text"].append(f["id"])
        if f["size"] < 0 or f["type"] == "unreadable":
            meta["unreadable"].append(f["id"])

    lib = {"files": files, "analysis": {}, "corpus": corpus,
           "config_files": config_files, "scan_meta": meta,
           "input_dir": str(input_dir)}
    lib["analysis"] = analyze(lib)
    return lib


def analyze(lib: dict) -> dict:
    files = lib["files"]
    by_hash = {}
    for f in files:
        if f.get("sha256"):
            by_hash.setdefault(f["sha256"], []).append(f["id"])
    alias_groups = sorted((sorted(v) for v in by_hash.values() if len(v) > 1),
                          key=lambda g: g[0])

    by_stem = {}
    for f in files:
        if f["stem"]:
            by_stem.setdefault(f["stem"], []).append(f["id"])
    version_groups, conflicts = [], []
    for stem, ids in by_stem.items():
        if len(ids) < 2:
            continue
        shas = {_by_id(files, i).get("sha256") for i in ids}
        if len(shas) == 1:
            continue
        markers = any(_by_id(files, i).get("version_tokens") for i in ids)
        grp = {"stem": stem, "member_ids": sorted(ids),
               "basis": "heuristic"}
        if markers:
            version_groups.append(grp)
        else:
            conflicts.append(grp)
    version_groups.sort(key=lambda g: g["stem"])
    conflicts.sort(key=lambda g: g["stem"])
    explicit = explicit_relations(lib)
    return {
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
        "explicit_relations": explicit,
        "metadata": {"stem_groups": len(by_stem),
                     "alias_groups": len(alias_groups),
                     "version_groups": len(version_groups),
                     "conflicts": len(conflicts)},
    }


def _by_id(files, f_id):
    for f in files:
        if f.get("id") == f_id:
            return f
    return {}


_ALIAS_KW = re.compile(r"(别名|alias)", re.I)
_VERSION_KW = re.compile(r"(版本|新版本|version|替代|代替|supersed)", re.I)


def explicit_relations(lib: dict) -> list:
    """从 case/task/材料文本中读取显式关系（行政版本备注）。

    规则：一行同时命中关系关键词与 ≥2 个已知文件名，才记为显式关系；
    quote 为该行逐字原文，source 为 config 文件名或材料 id。
    """
    out, seen = [], set()
    id_by_name = {}
    for f in lib.get("files", []):
        id_by_name.setdefault(nfkc(f["name"]).lower(), f["id"])
    id_by_rel = {f["rel_path"]: f["id"] for f in lib.get("files", [])}

    def scan_lines(src, locator, text):
        if not text:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            kw = None
            if _ALIAS_KW.search(line):
                kw = "alias"
            elif _VERSION_KW.search(line):
                kw = "version"
            if not kw:
                continue
            matched = []
            low = nfkc(line).lower()
            for name, fid in id_by_name.items():
                if name and name in low:
                    if fid not in matched:
                        matched.append(fid)
            if len(matched) < 2:
                continue
            key = (kw, line)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kw, "raw": line, "source": src,
                        "locator": locator, "member_ids": sorted(matched),
                        "basis": "explicit"})

    for src, text in lib.get("config_files", {}).items():
        scan_lines(src, src, text)
    corpus = lib.get("corpus", {})
    for f in lib.get("files", []):
        if corpus.get(f["id"]):
            scan_lines(f["id"], f["rel_path"], corpus[f["id"]])
    return out


# ------------------------------------------------------------------ 检索
def build_index(lib: dict) -> dict:
    index = {}
    def add(sid, locator, text):
        for tok in _tokens(text):
            index.setdefault(tok, []).append(
                {"sid": sid, "locator": locator, "snippet": _snippet(text, tok)})
    for f in lib["files"]:
        if lib.get("corpus", {}).get(f["id"]):
            add(f["id"], f["rel_path"], lib["corpus"][f["id"]])
    for src, text in lib.get("config_files", {}).items():
        add(src, src, text)
    return index


def _tokens(text: str):
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", nfkc(text.lower()))
    return [t for t in s.split() if len(t) > 1]


def _snippet(text: str, tok: str, width: int = 120) -> str:
    low = nfkc(text.lower())
    i = low.find(tok)
    if i < 0:
        return ""
    j = max(0, i - width)
    k = min(len(text), i + len(tok) + width)
    s = text[j:k].strip().replace("\n", " ")
    if j > 0:
        s = "…" + s
    if k < len(text):
        s = s + "…"
    return s


def run_queries(queries, index, corpus) -> list:
    out = []
    if not isinstance(queries, list):
        return out
    for qi, q in enumerate(queries):
        qid, text = _parse_query(q, qi)
        toks = _tokens(text)
        hits = {}
        for t in toks:
            for hit in index.get(t, []):
                key = (hit["sid"], hit["locator"])
                if key not in hits:
                    hits[key] = {"source_id": hit["sid"], "locator": hit["locator"],
                                 "quote": hit["snippet"], "score": 1}
                else:
                    hits[key]["score"] += 1
        ranked = sorted(hits.values(), key=lambda h: (-h["score"], h["locator"]))[:8]
        status = "answered" if ranked else "no_direct_match"
        out.append({"query_id": qid, "query": text, "status": status, "hits": ranked})
    return out


def _parse_query(q, idx):
    if isinstance(q, str):
        return f"Q{idx + 1}", q
    if isinstance(q, dict):
        qid = str(q.get("query_id") or q.get("id") or f"Q{idx + 1}")
        text = str(q.get("query") or q.get("question") or q.get("text") or "")
        return qid, text
    return f"Q{idx + 1}", str(q)


# ------------------------------------------------------------------ 稳定序列化
def dump_library(lib: dict) -> str:
    data = {"files": [_strip_runtime(f) for f in lib["files"]],
            "analysis": lib["analysis"],
            "scan_meta": lib.get("scan_meta", {})}
    return stable_dump(data)


def _strip_runtime(f):
    return {k: v for k, v in f.items() if k != "mtime"}