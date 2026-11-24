#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — academic-slides-evidence 的共享实现层（纯 Python 标准库）。

职责：稳健 IO（编码/坏 JSON 容错）、输入发现、证据定位与逐字引用、
pptx 结构检查（zip/XML）、溢出启发式、预览文件检查。main.py 与
selftest.py 复用本模块；不依赖任何第三方包，可在任意 Python 3.8+ 环境运行。

所有函数只接收显式路径参数，不假设固定文件名或固定键名；文本按多种编码
依次尝试读取，坏 JSON 返回错误而非崩溃。证据部分是本 Skill 的核心：
每个结论都必须落回材料中的「locator + 逐字 quote」，不允许只给来源名。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

__all__ = [
    "VERSION", "now_iso", "sha256_file",
    "read_text_robust", "read_json_robust", "write_json",
    "discover_input", "split_pages", "page_of",
    "find_evidence", "build_evidence_map",
    "inspect_pptx", "overflow_heuristic", "looks_pdf", "looks_png",
]

VERSION = "1.0.0"
MAX_SOURCE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记录
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".xml", ".html", ".htm", ".tex",
             ".rst", ".log", ".tsv", ".yml", ".yaml"}
_MEDIA_EXT = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".png", ".jpg", ".jpeg",
              ".gif", ".webp", ".svg", ".xlsx", ".xls"}


def now_iso() -> str:
    """UTC 时间戳，写入 change_log 与 runtime。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: str) -> str | None:
    """文件 SHA-256；失败返回 None。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 稳健 IO
# --------------------------------------------------------------------------- #
def read_text_robust(path: str, max_bytes: int = MAX_SOURCE_BYTES):
    """按多种编码读取文本，返回 (text, error)。二进制/超限/不可解码均返回 (None, reason)。"""
    try:
        if os.path.getsize(path) > max_bytes:
            return None, "oversize %dB > %dB" % (os.path.getsize(path), max_bytes)
        with open(path, "rb") as f:
            head = f.read(1024)
        if b"\x00" in head:
            return None, "binary_file"
        last_err = None
        for enc in _ENCODINGS:
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read(), None
            except UnicodeError as e:
                last_err = "%s: %s" % (enc, e)
        return None, last_err or "undecodable"
    except OSError as e:
        return None, str(e)


def read_json_robust(path: str):
    """读取 JSON，返回 (obj, error)；坏 JSON 返回 (None, reason)，不抛异常。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # JSONDecodeError / OSError / UnicodeError
        return None, "%s: %s" % (type(e).__name__, e)


def write_json(path: str, obj) -> None:
    """原子写盘：先写临时文件再 replace，避免半成品 result.json。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def _kind_of(rel: str) -> str:
    ext = os.path.splitext(rel)[1].lower().lstrip(".")
    table = {
        "md": "markdown", "markdown": "markdown", "txt": "text", "json": "data",
        "yaml": "data", "yml": "data", "csv": "tabular", "tsv": "tabular",
        "pdf": "pdf", "docx": "document", "doc": "document", "pptx": "slides",
        "ppt": "slides", "xlsx": "sheet", "xls": "sheet", "png": "image",
        "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image",
        "svg": "vector", "xml": "xml", "html": "html", "htm": "html",
        "tex": "latex", "rst": "text", "log": "text",
    }
    return table.get(ext, ext or "unknown")


def _looks_text(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _TEXT_EXT:
        return True
    if ext in _MEDIA_EXT:
        return False
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(1024)
    except OSError:
        return False


def discover_input(input_dir: str) -> dict:
    """扫描输入目录，返回 {case_json, task_md, materials, warnings}。

    - 不假设固定文件名：case.json / task.md 大小写不敏感识别。
    - 超限文件跳过并在 warnings 记录；二进制材料只登记不读。
    """
    out = {"case_json": None, "task_md": None, "materials": [], "warnings": []}
    if not os.path.isdir(input_dir):
        out["warnings"].append("input_dir 不存在: %s" % input_dir)
        return out
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                out["warnings"].append("unreadable: %s" % rel)
                continue
            if size > MAX_SOURCE_BYTES:
                out["warnings"].append("skip oversize %s (%dB)" % (rel, size))
                continue
            low = fn.lower()
            if low == "case.json" and out["case_json"] is None:
                out["case_json"] = p
            elif low == "task.md" and out["task_md"] is None:
                out["task_md"] = p
            elif _looks_text(p):
                out["materials"].append({
                    "path": p, "rel": rel, "size_bytes": size, "kind": _kind_of(rel),
                })
            else:
                out["materials"].append({
                    "path": p, "rel": rel, "size_bytes": size, "kind": _kind_of(rel),
                })
    return out


# --------------------------------------------------------------------------- #
# 证据：locator + 逐字 quote
# --------------------------------------------------------------------------- #
def split_pages(text: str) -> list:
    """按换页符 \\f 分页，返回 [{page_index, line_start, line_end}]；无换页符时视为单页。"""
    pages = []
    if "\f" not in text:
        pages.append({"page_index": 1, "line_start": 1,
                      "line_end": text.count("\n") + 1})
        return pages
    line = 1
    for i, chunk in enumerate(text.split("\f"), 1):
        n = chunk.count("\n") + (1 if chunk else 0)
        pages.append({"page_index": i, "line_start": line, "line_end": line + n - 1})
        line += n
    return pages


def page_of(pages: list, line: int):
    """给定行号，返回所属页码；未命中返回 None。"""
    for p in pages or []:
        if p["line_start"] <= line <= p["line_end"]:
            return p["page_index"]
    return None


def _line_range(text: str, start: int, end: int):
    ls = text[:start].count("\n") + 1
    le = ls + text[start:end].count("\n")
    return ls, le


def _make_entry(source, text, start, end, confidence, matcher, score=None, page_map=None):
    ls, le = _line_range(text, start, end)
    locator = {"source": source, "line_start": ls, "line_end": le}
    pg = page_of(page_map, ls) if page_map else None
    if pg is not None:
        locator["page"] = pg
    return {
        "locator": locator,
        "quote": text[start:end].strip()[:2000],
        "verbatim": True,
        "confidence": confidence,
        "matcher": matcher,
        "score": round(score, 3) if isinstance(score, (int, float)) else None,
    }


def _flex_pattern(claim: str):
    """把空白折叠为 \\s+ 的正则，忽略大小写；用于「容忍排版换行」的精确匹配。"""
    parts = [re.escape(p) for p in re.split(r"\s+", claim.strip()) if p]
    if not parts:
        return None
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def _sentences(text: str):
    """按行再按句末标点切分，返回 [(start, end, sentence)]。"""
    out = []
    for m in re.finditer(r"[^\n]*\S[^\n]*", text):
        line = m.group(0)
        pos = m.start()
        for part in re.split(r"(?<=[。．！？!?.;；])", line):
            s = part.strip()
            if s:
                out.append((pos, pos + len(part), part))
            pos += len(part)
    return out


def _tokens(s: str) -> set:
    low = s.lower()
    return set(re.findall(r"[a-z0-9]+", low)) | set(re.findall(r"[\u4e00-\u9fff]", low))


def _token_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _has_digits(s: str) -> bool:
    return bool(re.search(r"\d", s or ""))


def find_evidence(text: str, source: str, claim: str, max_results: int = 3,
                  page_map=None) -> list:
    """在材料文本中为一条结论定位证据，返回 locator+逐字 quote 列表。

    匹配策略（确定性、可复现）：
    1. 精确匹配（容忍换行/多余空白、忽略大小写）→ confidence=exact；
    2. 失败则句级模糊匹配（词元重叠度 >= 0.5）→ confidence=fuzzy。

    quote 一律取自材料原文（text[start:end]），不是对结论的转述。
    """
    claim_s = (claim or "").strip()
    if not claim_s or not text:
        return []
    results = []
    pat = _flex_pattern(claim_s)
    if pat:
        m = pat.search(text)
        if m:
            results.append(_make_entry(source, text, m.start(), m.end(),
                                       "exact", "flexible_whitespace",
                                       page_map=page_map))
    if not results:
        scored = []
        for s, e, sent in _sentences(text):
            score = _token_overlap(claim_s, sent)
            if score >= 0.5:
                scored.append((score, s, e))
        scored.sort(key=lambda x: -x[0])
        for score, s, e in scored[:max_results]:
            results.append(_make_entry(source, text, s, e, "fuzzy",
                                       "sentence_overlap", score,
                                       page_map=page_map))
    return results[:max_results]


def _plan_pages(page_plan):
    if isinstance(page_plan, dict):
        return page_plan.get("pages")
    if isinstance(page_plan, list):
        return page_plan
    return None


def build_evidence_map(page_plan, texts: dict, page_maps=None) -> dict:
    """把页计划升级为页级证据映射（schema v2）。

    结构：page -> claims[] -> {claim, type, evidence[{locator, quote, verbatim,
    confidence, matcher, score}], status}。多来源数值冲突标记 source_conflict。
    未定位到的结论进入 pending_human，绝不伪造 quote。
    """
    page_maps = page_maps or {}
    pages, pending = [], []
    plan_pages = _plan_pages(page_plan)
    if plan_pages is None:
        return {"schema": "evidence-map/v2", "pages": [],
                "pending_human": ["page_plan 缺失或结构异常"]}
    for pg in plan_pages:
        if not isinstance(pg, dict):
            continue
        page_no = pg.get("index", pg.get("page"))
        claims = pg.get("claims", []) or []
        if isinstance(claims, dict):
            claims = [claims]
        out_claims = []
        for ci, c in enumerate(claims):
            if isinstance(c, str):
                c = {"claim": c}
            ctext = (c.get("claim") or "").strip()
            if not ctext:
                continue
            ctype = c.get("type", "fact")
            per_source = {}
            for sname, stext in texts.items():
                evs = find_evidence(stext, sname, ctext, max_results=1,
                                    page_map=page_maps.get(sname))
                if evs:
                    per_source[sname] = evs[0]
            evs = [per_source[s] for s in sorted(per_source)]
            claim_id = c.get("claim_id") or ("p%s-c%d" % (page_no, ci + 1))
            if not evs:
                status = "pending_human"
                pending.append({"claim_id": claim_id, "claim": ctext,
                                "reason": "未在提取文本中定位到可核对片段"})
            elif len(evs) >= 2 and _has_digits(ctext) and _quotes_conflict(evs):
                status = "source_conflict"
                pending.append({"claim_id": claim_id, "claim": ctext,
                                "reason": "多个来源逐字片段不一致，需人工裁定"})
            else:
                status = "verified" if any(e["confidence"] == "exact" for e in evs) \
                    else "partial"
            out_claims.append({
                "claim_id": claim_id, "claim": ctext, "type": ctype,
                "evidence": evs, "status": status,
                "notes": c.get("notes"),
            })
        pages.append({"page_index": page_no, "page_title": pg.get("title"),
                      "claims": out_claims})
    summary = {
        "pages": len(pages),
        "claims_total": sum(len(p["claims"]) for p in pages),
        "verified": sum(1 for p in pages for c in p["claims"] if c["status"] == "verified"),
        "partial": sum(1 for p in pages for c in p["claims"] if c["status"] == "partial"),
        "source_conflict": sum(1 for p in pages for c in p["claims"]
                               if c["status"] == "source_conflict"),
        "pending_human": len(pending),
    }
    return {"schema": "evidence-map/v2", "pages": pages,
            "pending_human": pending, "summary": summary}


def _quotes_conflict(evs: list) -> bool:
    norms = {re.sub(r"\s+", "", e["quote"]) for e in evs if e.get("quote")}
    return len(norms) > 1


# --------------------------------------------------------------------------- #
# pptx 检查（zip + XML，纯标准库）
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _rel_target(rels_xml: bytes, kind: str):
    try:
        root = ET.fromstring(rels_xml)
        for rel in root:
            if _local(rel.tag) == "Relationship":
                if (rel.get("Type") or "").endswith("/" + kind):
                    return rel.get("Target")
    except Exception:
        return None
    return None


def _extract_runs(xml_bytes: bytes):
    runs, sizes = [], []
    try:
        root = ET.fromstring(xml_bytes)
        for t in root.iter():
            if _local(t.tag) == "t" and t.text:
                runs.append(t.text)
        for rpr in root.iter():
            if _local(rpr.tag) == "rPr":
                sz = rpr.get("sz")
                if sz:
                    try:
                        sizes.append(int(sz) / 100.0)
                    except ValueError:
                        pass
    except Exception:
        pass
    return runs, sizes


def _slide_sort_key(name: str) -> int:
    m = re.search(r"(\d+)", os.path.basename(name))
    return int(m.group(1)) if m else 0


def inspect_pptx(path: str) -> dict:
    """检查 pptx：zip 完整性、页数、母版/版式/备注、每页版式继承、媒体、文本与字号。

    只做结构/静态检查，不做像素级渲染；渲染一致性须由 PDF/PNG 预览人工核对。
    """
    info = {"pptx_zip_ok": False, "path": path, "error": None, "slide_count": 0,
            "masters": [], "layouts": [], "notes_slides": [], "media": [],
            "slide_layout_map": {}, "slides_text": [], "font_sizes": []}
    if not os.path.isfile(path):
        info["error"] = "file_not_found"
        return info
    if not zipfile.is_zipfile(path):
        info["error"] = "not_a_zip"
        return info
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            info["pptx_zip_ok"] = True
            slides = sorted([n for n in names
                             if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)],
                            key=_slide_sort_key)
            info["slide_count"] = len(slides)
            info["masters"] = sorted(
                [n for n in names
                 if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n)])
            info["layouts"] = sorted(
                [n for n in names
                 if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)])
            info["notes_slides"] = sorted(
                [n for n in names
                 if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)])
            info["media"] = sorted([n for n in names if n.startswith("ppt/media/")])
            for s in slides:
                # OOXML 关系部件名是「slideN.xml.rels」，不是 slideN.rels
                relpath = "ppt/slides/_rels/%s.rels" % os.path.basename(s)
                if relpath in names:
                    info["slide_layout_map"][s] = _rel_target(z.read(relpath), "slideLayout")
            for s in slides:
                runs, sizes = _extract_runs(z.read(s))
                info["slides_text"].append({
                    "slide": s, "char_count": sum(len(t) for t in runs),
                    "text_preview": "".join(runs)[:200],
                })
                info["font_sizes"].extend(sizes)
    except Exception as e:  # zip/XML 结构异常
        info["error"] = "%s: %s" % (type(e).__name__, e)
    return info


def _text_frames(xml_bytes: bytes) -> list:
    frames = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return frames
    for sp in root.iter():
        if _local(sp.tag) != "sp":
            continue
        xfrm = None
        text = ""
        for el in sp.iter():
            ln = _local(el.tag)
            if xfrm is None and ln == "xfrm":
                xfrm = el
            elif ln == "t" and el.text:
                text += el.text
        if text.strip():
            frames.append((xfrm, text))
    return frames


def _xfrm_emu(xfrm):
    off = ext = None
    if xfrm is not None:
        for el in xfrm.iter():
            ln = _local(el.tag)
            if ln == "off":
                off = (int(el.get("x", 0)), int(el.get("y", 0)))
            elif ln == "ext":
                ext = (int(el.get("cx", 0)), int(el.get("cy", 0)))
    return off, ext


def _frame_overflow(slide_name, xfrm, text):
    _off, ext = _xfrm_emu(xfrm)
    if not ext or ext[0] <= 0 or ext[1] <= 0:
        return None
    font_pt = 18.0  # 缺 rPr 时的默认字号（启发式）
    width_pt = ext[0] / 12700.0
    height_pt = ext[1] / 12700.0
    chars_per_line = max(1.0, width_pt / (0.55 * font_pt))
    lines = math.ceil(len(text) / chars_per_line)
    needed_pt = lines * 1.25 * font_pt
    if needed_pt > height_pt * 1.05:
        return {
            "slide": slide_name, "text_preview": text[:80],
            "estimated_height_pt": round(needed_pt, 1),
            "frame_height_pt": round(height_pt, 1),
            "ratio": round(needed_pt / height_pt, 2),
        }
    return None


def overflow_heuristic(path: str) -> list:
    """文本框溢出启发式（非像素级真相，只做确定性预警）。

    依据：容器 EMU 宽高 + 文本字符数 + 默认字号估算行数；判定为
    possible_overflow 时需要人工用渲染预览复核，不作为最终结论。
    """
    checks = []
    if not os.path.isfile(path) or not zipfile.is_zipfile(path):
        return [{"name": "overflow_heuristic", "auto": True, "result": "skip",
                 "detail": "pptx 不可读，跳过溢出检查"}]
    try:
        with zipfile.ZipFile(path) as z:
            slides = sorted([n for n in z.namelist()
                             if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)],
                            key=_slide_sort_key)
            hits = []
            for s in slides:
                for xfrm, text in _text_frames(z.read(s)):
                    flag = _frame_overflow(s, xfrm, text)
                    if flag:
                        hits.append(flag)
    except Exception as e:
        return [{"name": "overflow_heuristic", "auto": True, "result": "error",
                 "detail": "%s: %s" % (type(e).__name__, e)}]
    if not hits:
        return [{"name": "overflow_heuristic", "auto": True, "result": "pass",
                 "detail": "未发现估算超高的文本框"}]
    return [{"name": "overflow_heuristic", "auto": True, "result": "warn",
             "detail": "%d 处需人工复核" % len(hits), "hits": hits[:20]}]


# --------------------------------------------------------------------------- #
# 预览文件检查
# --------------------------------------------------------------------------- #
def looks_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def looks_png(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False
