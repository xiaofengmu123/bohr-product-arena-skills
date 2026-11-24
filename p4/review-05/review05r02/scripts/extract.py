#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic input discovery, text extraction, and corpus indexing.

Standard library only. Imported by scripts/main.py and scripts/validate.py.

Responsibilities
----------------
1. Discover and classify every file under /input (case.json, task.md,
   materials, PDFs, artifacts) without assuming fixed names.
2. Decode text files through a robust fallback chain (utf-8-sig -> utf-8 ->
   gb18030 -> latin-1), recording encoding/errors instead of crashing.
3. For PDFs, first reuse an existing plain-text extraction under
   ``<work>/parsed/<stem>.txt``; otherwise try the system ``pdftotext``
   binary and report a degradation when it is unavailable or produces no
   text, so the orchestrator/agent can fall back to platform tools or OCR.
4. Build a paragraph/page-level corpus index: every segment carries file,
   locator, line, offset, and verbatim text so findings can be re-verified.
5. Emit deterministic, heuristic *candidate observations* (never verdicts)
   to seed the agent's adjudication step.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SEGMENT_CHARS = 60000

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".bib", ".csv", ".tsv",
    ".xml", ".html", ".htm", ".json", ".yaml", ".yml", ".log", ".py",
}
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded, NFKC-normalized form for matching."""
    s = unicodedata.normalize("NFKC", text or "")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def normalize_loose(text: str) -> str:
    """Even looser form that tolerates hyphenation and punctuation drift."""
    s = normalize(text)
    s = re.sub(r"[-‐‑]\s+", "", s)  # hyphenated line breaks
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s, flags=re.UNICODE)
    return s


def read_text_robust(path: Path):
    """Return (text, encoding, error). Never raises on decode problems."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", None, f"unreadable: {exc}"
    if len(data) > MAX_FILE_BYTES:
        return "", None, f"file exceeds {MAX_FILE_BYTES} bytes; skipped"
    last_err = None
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = f"{enc}: {exc}"
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace", last_err


def read_json_robust(path: Path):
    """Return (obj, error)."""
    text, _enc, err = read_text_robust(path)
    if err and text == "":
        return None, err
    try:
        return json_loads(text), None
    except (ValueError, TypeError) as exc:
        return None, f"invalid JSON: {exc}"


def json_loads(text: str):
    import json
    return json.loads(text)


def discover(inp: Path):
    """Classify files under /input. Returns a deterministic list of dicts."""
    items = []
    if not inp.exists():
        return items
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        low = p.name.lower()
        if low == "case.json" or (low.startswith("case") and low.endswith(".json")):
            role = "case"
        elif low == "task.md" or (low.startswith("task") and low.endswith(".md")):
            role = "task"
        elif p.suffix.lower() == ".pdf":
            role = "pdf"
        elif p.suffix.lower() in TEXT_SUFFIXES:
            role = "material"
        else:
            role = "artifact"
        items.append({"path": p.as_posix(), "name": p.name, "role": role, "size": size})
    return items


def parse_pdf_via_pdftotext(pdf_path: Path, out_path: Path):
    """Return (out_path, error). Writes extracted text directly to out_path."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not found on PATH"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [exe, "-enc", "UTF-8", "-layout", str(pdf_path), str(out_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"pdftotext failed: {exc}"
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        return None, "pdftotext returned no text (possibly a scanned/image PDF)"
    return out_path, None


def split_segments(text: str, file: str, kind: str, start_id: int):
    """Split text into locatable segments. Returns (segments, next_id)."""
    segments = []
    sid = start_id

    if kind == "pdf":
        pages = text.split("\f")
        for pno, page in enumerate(pages, 1):
            if not page.strip():
                continue
            paras = re.split(r"\n\s*\n", page)
            para_no = 0
            line = 1
            for para in paras:
                content = para.strip()
                if not content:
                    line += para.count("\n") + 2
                    continue
                para_no += 1
                locator = f"page {pno} · para {para_no}"
                if len(content) <= MAX_SEGMENT_CHARS:
                    segments.append(_seg(sid, file, locator, line, content))
                    sid += 1
                    line += para.count("\n") + 2
                else:
                    for ln in para.split("\n"):
                        chunk = ln.strip()
                        if not chunk:
                            line += 1
                            continue
                        segments.append(_seg(sid, file, f"{locator} · L{line}", line, chunk))
                        sid += 1
                        line += 1
                    line += 1
    else:
        paras = re.split(r"\n\s*\n", text)
        line = 1
        for i, para in enumerate(paras, 1):
            content = para.strip()
            if not content:
                line += para.count("\n") + 2
                continue
            locator = f"para {i}"
            if len(content) <= MAX_SEGMENT_CHARS:
                segments.append(_seg(sid, file, locator, line, content))
                sid += 1
                line += para.count("\n") + 2
            else:
                for ln in para.split("\n"):
                    chunk = ln.strip()
                    if not chunk:
                        line += 1
                        continue
                    segments.append(_seg(sid, file, f"{locator} · L{line}", line, chunk))
                    sid += 1
                    line += 1
                line += 1
    return segments, sid


def _seg(sid, file, locator, line, text):
    return {
        "id": f"S{sid:06d}",
        "file": file,
        "locator": locator,
        "line": line,
        "text": text,
    }


def build_corpus(inp: Path, work: Path):
    """Discover, extract, and index. Returns (corpus, degradations)."""
    items = discover(inp)
    parsed_dir = work / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    degradations = []

    # First pass: make sure every PDF has an extracted plain-text copy.
    for it in items:
        if it["role"] != "pdf":
            continue
        pdf = Path(it["path"])
        parsed = parsed_dir / (pdf.stem + ".txt")
        if parsed.exists() and parsed.stat().st_size > 0:
            continue
        _out, err = parse_pdf_via_pdftotext(pdf, parsed)
        if err:
            degradations.append({
                "file": str(pdf),
                "stage": "pdf_parse",
                "detail": err,
                "action": "use platform pdf-text-tool or ocr-tool, then save text to " + str(parsed),
            })

    files_meta = []
    segments = []
    seg_id = 1
    for it in items:
        meta = {
            "path": it["path"],
            "name": it["name"],
            "role": it["role"],
            "size": it["size"],
            "kind": it["role"],
            "chars": 0,
            "encoding": None,
            "error": None,
        }
        if it["role"] == "material":
            text, enc, err = read_text_robust(Path(it["path"]))
            meta["encoding"] = enc
            meta["chars"] = len(text)
            meta["error"] = err
            if err and text == "":
                degradations.append({"file": it["path"], "stage": "text_read", "detail": err})
            if text:
                segs, seg_id = split_segments(text, it["path"], "text", seg_id)
                segments.extend(segs)
        elif it["role"] == "pdf":
            parsed = parsed_dir / (Path(it["name"]).stem + ".txt")
            if parsed.exists() and parsed.stat().st_size > 0:
                text, enc, err = read_text_robust(parsed)
                meta["encoding"] = enc
                meta["chars"] = len(text)
                meta["text_path"] = str(parsed)
                meta["error"] = err
                if text:
                    segs, seg_id = split_segments(text, it["path"], "pdf", seg_id)
                    segments.extend(segs)
            else:
                meta["error"] = "no extracted text available"
        files_meta.append(meta)

    corpus = {
        "generated_at": utcnow(),
        "input_root": str(inp),
        "files": files_meta,
        "segments": segments,
    }
    return corpus, degradations


CANDIDATE_RULES = [
    {"id": "citation_marker", "category": "citation", "priority": "high",
     "pattern": r"\[\s*\d+(?:\s*[,;]?\s*\d+)*\s*\]|\([A-Z][A-Za-z'’\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'’\-]+)?,\s*(?:19|20)\d{2}[a-z]?\)",
     "note": "引用标记；需核对文献真实存在、元数据一致、正文论断与所引文献对应"},
    {"id": "ethics_signal", "category": "ethics", "priority": "high",
     "pattern": r"(?i)informed consent|institutional review board|IRB|ethics (?:committee|approval)|伦理|知情同意|动物实验|animal experiment|approval number|批件|伦理委员会",
     "note": "涉及人或动物的研究信号；需核对知情同意、批件编号与适用范围是否写明"},
    {"id": "privacy_signal", "category": "privacy", "priority": "high",
     "pattern": r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+|\b1[3-9]\d{9}\b|\b\d{15}(?:\d{2}[\dXx])?\b|\b(?:subject|participant|patient)\s+(?:ID|id|No\.?)\b|personally identifiable|PII|隐私|患者信息|姓名.*出生",
     "note": "可识别个人信息/受限数据信号；需判断是否脱敏、是否具备授权或审批依据"},
    {"id": "funding_signal", "category": "academic_integrity", "priority": "medium",
     "pattern": r"(?i)fund|grant|sponsor|acknowledg|基金|资助|致谢|financ|supported by",
     "note": "基金/致谢信号；需核对致谢、基金编号、作者单位之间是否一致"},
    {"id": "conflict_signal", "category": "academic_integrity", "priority": "high",
     "pattern": r"(?i)conflict of interest|competing interest|利益冲突|声明无",
     "note": "利益冲突声明信号；需核对是否缺失、是否与实际资助关系一致"},
    {"id": "statistics_signal", "category": "statistics", "priority": "medium",
     "pattern": r"(?i)\bp\s*[<>=]\s*0?\.\d+|\bn\s*=\s*\d+|\bstandard deviation\b|\bstandard error\b|\beffect size\b|confidence interval|置信|显著性|标准差|p\s*值",
     "note": "统计量信号；需核对检验方法、样本量、效应量、显著性表述是否交代清楚"},
    {"id": "figure_caption", "category": "copyright", "priority": "medium",
     "pattern": r"(?im)^\s*(图\s*\d+|fig(?:ure)?\.?\s*\d+|table\s*\d+|scheme\s*\d+)\b",
     "note": "图/表题注；需核对第三方图片/表格的来源标注与许可类型"},
    {"id": "license_signal", "category": "copyright", "priority": "medium",
     "pattern": r"(?i)license|licence|许可|CC[ -]?BY|GPL|MIT license|BSD|copyright|版权|©|creative commons",
     "note": "许可/版权信号；需核对代码、数据、图片的许可类型与复用条件"},
    {"id": "data_availability", "category": "method", "priority": "medium",
     "pattern": r"(?i)data availability|data access|数据可用|available (?:at|from|in)|repository|accession",
     "note": "数据可用性声明；需核对数据集来源、获取条件与是否可复核"},
    {"id": "reproducibility_signal", "category": "method", "priority": "low",
     "pattern": r"(?i)reproduc|code availability|代码可用|supplementary material|补充材料|protocols?\.io",
     "note": "可复现性信号；需核对方法/参数/代码是否足以复现"},
]

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
_CATEGORY_RANK = {
    "academic_integrity": 0, "citation": 1, "privacy": 2, "copyright": 3,
    "ethics": 4, "submission": 5, "quality": 6, "statistics": 7,
    "method": 8, "logic": 9,
}
_MAX_PER_CATEGORY = 30
_MAX_TOTAL = 250


def detect_candidates(corpus):
    """Return deterministic candidate observations for agent adjudication."""
    observations = []
    seen = set()
    per_category = {}
    compiled = []
    for rule in CANDIDATE_RULES:
        try:
            rx = re.compile(rule["pattern"])
        except re.error:
            continue
        compiled.append((rule, rx))

    for seg in corpus.get("segments", []):
        text = seg["text"]
        for rule, rx in compiled:
            cat = rule["category"]
            key = (cat, seg["file"], seg["locator"])
            if key in seen:
                continue
            if per_category.get(cat, 0) >= _MAX_PER_CATEGORY:
                continue
            m = rx.search(text)
            if not m:
                continue
            seen.add(key)
            per_category[cat] = per_category.get(cat, 0) + 1
            observations.append({
                "id": f"C{len(observations) + 1:05d}",
                "category": cat,
                "signal": rule["id"],
                "priority": rule["priority"],
                "file": seg["file"],
                "locator": seg["locator"],
                "quote": _window(text, m),
                "note": rule["note"] + "；此为脚本候选观察，须由 Agent 判定后决定成立/排除/降级",
            })
            if len(observations) >= _MAX_TOTAL:
                break
        if len(observations) >= _MAX_TOTAL:
            break

    observations.sort(key=lambda o: (
        _PRIORITY_RANK.get(o["priority"], 9),
        _CATEGORY_RANK.get(o["category"], 99),
        o["file"],
        o["locator"],
    ))
    return observations


def _window(text, m, width=260):
    s = max(0, m.start() - 40)
    e = min(len(text), m.end() + width)
    snippet = text[s:e].strip()
    if s > 0:
        snippet = "…" + snippet
    if e < len(text):
        snippet = snippet + "…"
    return snippet
