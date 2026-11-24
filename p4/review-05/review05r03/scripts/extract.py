#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic evidence extraction for the source-bound academic audit skill.

Responsibilities
----------------
* Discover input files under /input without assuming fixed names.
* Parse PDF (via ``pdftotext`` when available) and text-like materials into
  plain text; record degradations instead of guessing content.
* Build a line-level index (file / page / line / locator / text) and a
  normalized full-text blob used for quote verification.
* Extract *candidate observations* (DOI, citations, statistics, PII, ethics,
  COI/funding, license/reuse, data/code markers) with locator + verbatim quote.
  Observations are leads to judge, never findings.

Standard library only. Deterministic: no random seed, no network, no time
dependency in the evidence layer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

TEXT_SUFFIXES = {
    ".md", ".txt", ".tex", ".latex", ".json", ".jsonl", ".csv", ".tsv",
    ".xml", ".html", ".htm", ".rst", ".bib", ".yaml", ".yml", ".log",
}
PDF_SUFFIX = ".pdf"
MAX_FILE_BYTES = 2 * 1024 * 1024  # skip single files larger than 2 MB
DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")

# ---------------------------------------------------------------------------
# Robust IO
# ---------------------------------------------------------------------------

def read_text_robust(path: Path, max_bytes: int = MAX_FILE_BYTES):
    """Read text with BOM/encoding fallback. Returns (text, error)."""
    try:
        if path.stat().st_size > max_bytes:
            return None, f"file larger than {max_bytes} bytes, skipped"
    except OSError as exc:
        return None, f"stat failed: {exc}"
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            raw = path.read_bytes()
            return raw.decode(enc), None
        except (UnicodeDecodeError, OSError) as exc:
            last = exc
    return None, f"decode failed: {last}"


def read_json_robust(path: Path):
    """Read JSON robustly. Returns (obj_or_None, error_or_None)."""
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        # tolerate a UTF-8 BOM that survives decoding
        try:
            return json.loads(text.lstrip("\ufeff")), None
        except json.JSONDecodeError as exc2:
            return None, f"bad JSON: {exc2}"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(inp: Path):
    """Classify files under *inp*. Returns list of dicts."""
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
        elif p.suffix.lower() in TEXT_SUFFIXES or p.suffix.lower() == PDF_SUFFIX:
            role = "material"
        else:
            role = "artifact"
        rel = p.relative_to(inp).as_posix() if p.is_relative_to(inp) else p.as_posix()
        items.append({
            "path": p,
            "relpath": rel,
            "name": p.name,
            "role": role,
            "size": size,
            "suffix": p.suffix.lower(),
        })
    return items


def load_case(inp: Path):
    """Return (case_obj, case_id, contract, errors)."""
    errors = []
    items = discover(inp)
    case_item = next((it for it in items if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, ["/input/case.json not found"]
    case, err = read_json_robust(case_item["path"])
    if err:
        return None, "unknown", {}, [f"case.json unreadable: {err}"]
    if not isinstance(case, dict):
        return None, "unknown", {}, ["case.json is not a JSON object"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id", "caseId"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            case_id = val.strip()
            break
    if not case_id:
        case_id = case_item["path"].stem
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        contract = {}
    return case, case_id, contract, errors


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

def find_tool(*names):
    for name in names:
        p = shutil.which(name)
        if p:
            return p
    return None


def run_pdftotext(pdf: Path, out_txt: Path):
    """Extract text from *pdf* into *out_txt*. Returns (ok, error)."""
    tool = find_tool("pdftotext", "pdf-text-tool", "pdftext")
    if tool is None:
        return False, "pdftotext not found on PATH"
    try:
        proc = subprocess.run(
            [tool, str(pdf), str(out_txt)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"pdftotext failed: {exc}"
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()[:300]
        return False, f"pdftotext exit {proc.returncode}: {msg}"
    if not out_txt.exists() or out_txt.stat().st_size == 0:
        return False, "pdftotext produced empty output"
    return True, None


# ---------------------------------------------------------------------------
# Index / normalization / quote verification
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Canonicalize for quote comparison: NFC, strip zero-width, collapse
    whitespace and line breaks, unify quotes/dashes, drop soft hyphens."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    # page/form-feed and CRLF -> single space
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("\f", " ")
    text = text.replace("\u2010", "-").replace("\u2011", "-")
    text = text.replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"[ \t\u00a0]+", " ", text).strip()
    return text


def build_index(relpath: str, text: str):
    """Line-level index for one source. Returns list of entry dicts."""
    entries = []
    pages = text.split("\f")
    if len(pages) == 1:
        pages = [text]
    for page_no, page in enumerate(pages, 1):
        for line_no, line in enumerate(page.split("\n"), 1):
            if line_no == 1 and not line.strip() and page_no > 1:
                # keep structure; empty first line of a split page is harmless
                pass
            entries.append({
                "file": relpath,
                "page": page_no,
                "line": line_no,
                "text": line,
                "locator": f"{relpath}#p{page_no}.L{line_no}",
            })
    return entries


def build_norm_blob(index):
    """Join normalized line texts into one blob with offset map.

    Returns (blob, offsets, locators). offsets[i] is the start position of
    locators[i] in blob (space-joined).
    """
    parts, offsets, locators = [], [], []
    total = 0
    for entry in index:
        n = normalize(entry["text"])
        if not n:
            continue
        parts.append(n)
        offsets.append(total)
        locators.append(entry["locator"])
        total += len(n) + 1
    return " ".join(parts), offsets, locators


def find_locator(blob, offsets, locators, norm_quote):
    pos = blob.find(norm_quote)
    if pos < 0:
        return None
    lo, hi = 0, len(offsets)
    while lo < hi:
        mid = (lo + hi) // 2
        if offsets[mid] <= pos:
            lo = mid + 1
        else:
            hi = mid
    idx = max(0, lo - 1)
    return locators[idx]


def verify_quote(source_text, quote, index=None):
    """Check a quote appears verbatim (normalized) in *source_text*.

    Returns (ok, locator_or_None).
    """
    nq = normalize(quote)
    if not nq:
        return False, None
    if index is not None:
        blob, offsets, locators = build_norm_blob(index)
        loc = find_locator(blob, offsets, locators, nq)
        return loc is not None, loc
    return nq in normalize(source_text), None


# ---------------------------------------------------------------------------
# Candidate observations
# ---------------------------------------------------------------------------

# (kind, category, pattern, note). English patterns match case-insensitively;
# Chinese patterns are self-contained. Patterns run per line.
OBSERVATION_PATTERNS = [
    ("doi", "citation", r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", "DOI 候选，用于核对文献真实性"),
    ("arxiv_id", "citation", r"arXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?", "arXiv 编号候选"),
    ("pmid", "citation", r"PMID:\s*\d{1,9}", "PubMed 编号候选"),
    ("url", "citation", r"https?://[^\s<>\"')\]]+", "外部链接候选，需核对可达性与许可"),
    ("bracket_cite", "citation", r"[\[（]\s*\d{1,3}(?:\s*[-–,，]\s*\d{1,3})*\s*[\]）]", "数字式引用候选"),
    ("authoryear_cite", "citation", r"[A-Z][A-Za-z-]+(?:\s+(?:et\s+al\.?|&?\s*[A-Z][A-Za-z-]+))?,\s*\d{4}[a-z]?", "作者-年份引用候选"),
    ("p_value", "statistics", r"[Pp]\s*(?:[<>=≤≥]\s*)?0?\.\d{1,5}", "显著性数值候选"),
    ("confidence_interval", "statistics", r"(?:95\s*%|95%)?\s*C\.?I\.?\b|\d{1,3}(?:\.\d+)?\s*%?\s*(?:CI|置信区间)", "置信区间候选"),
    ("sample_size", "statistics", r"\bn\s*=\s*\d{1,7}\b", "样本量候选"),
    ("email", "privacy", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "可识别邮箱候选"),
    ("phone", "privacy", r"(?:\+?\d{1,3}[-.\s]?)?(?:\(\d{2,4}\)|\d{2,4})[-.\s]?\d{3,4}[-.\s]?\d{3,4}", "电话/编号候选"),
    ("id_number", "privacy", r"(?:身份证|病例号|病历号|ID|No\.?|编号)\s*[:：]?\s*[A-Za-z0-9-]{4,}", "证件/编号候选"),
    ("ethics_marker", "ethics", r"(?:informed\s+consent|IRB|institutional\s+review\s+board|ethics\s+(?:committee|board|approval|approval\s+number)|伦理|知情同意|伦理批件|伦理委员会|批准编号)", "伦理与知情同意标记"),
    ("animal_marker", "ethics", r"(?:animal\s+(?:care|use|experiment|welfare)|IACUC|ARRIVE|实验动物|动物伦理|动物实验)", "动物实验标记"),
    ("coi_marker", "academic_integrity", r"(?:conflict[s]?\s+of\s+interest|competing\s+interest|利益冲突|作者声明|利益声明)", "利益冲突声明标记"),
    ("funding_marker", "academic_integrity", r"(?:funding|grant\s+(?:number|No\.?)|supported\s+by|基金|资助|项目编号|课题号|致谢)", "基金/资助/致谢标记"),
    ("reuse_marker", "copyright", r"(?:adapted\s+from|reproduced\s+(?:with|from)|with\s+permission|copyright|版权|授权|许可|©)", "第三方材料复用标记"),
    ("license_marker", "copyright", r"(?:Creative\s+Commons|CC-BY|CC\s+BY|MIT\s+License|GPL|Apache\s+License|license|许可协议|开源协议)", "许可类型标记"),
    ("data_availability", "method", r"(?:data\s+availability|数据可用|数据可得|deposited\s+in|Zenodo|figshare|公开数据|数据分享)", "数据可得性标记"),
    ("code_availability", "method", r"(?:code\s+availability|source\s+code|github\.com|GitLab|代码可用|开源|代码地址)", "代码可得性标记"),
    ("figure_ref", "method", r"(?:Figure\s+\d+|图\s*\d+|Fig\.?\s*\d+)", "图引用候选"),
    ("table_ref", "method", r"(?:Table\s+\d+|表\s*\d+)", "表引用候选"),
    ("preregistration", "method", r"(?:preregister(?:ed)?|pre-register(?:ed)?|ClinicalTrials\.gov|OSF|预注册|注册号|注册编号)", "预注册/注册号标记"),
    ("retraction_term", "academic_integrity", r"(?:retract(?:ed|ion)|withdraw[n]?|撤稿|撤回)", "撤稿/撤回相关表述"),
]

COMPILED_PATTERNS = [
    (kind, category, re.compile(pattern, re.IGNORECASE), note)
    for kind, category, pattern, note in OBSERVATION_PATTERNS
]


def _clean_match(match_text):
    m = match_text.strip()
    m = re.sub(r"^[\s,.;:;)\]}\)]+", "", m)
    m = re.sub(r"[\s,.;:;\[\(\{]+$", "", m)
    return m.strip()


def extract_observations(index):
    """Extract candidate observations from a line index.

    Returns (observations, matched_kinds). Deterministic: stable iteration
    order, deduplicated by (kind, locator, normalized quote).
    """
    observations = []
    seen = set()
    for entry in index:
        line = entry["text"]
        if not line.strip():
            continue
        for kind, category, pattern, note in COMPILED_PATTERNS:
            for m in pattern.finditer(line):
                quote = _clean_match(m.group(0))
                if not quote:
                    continue
                key = (kind, entry["locator"], normalize(quote))
                if key in seen:
                    continue
                seen.add(key)
                start = max(0, m.start() - 100)
                end = min(len(line), m.end() + 100)
                observations.append({
                    "kind": kind,
                    "category": category,
                    "file": entry["file"],
                    "locator": entry["locator"],
                    "quote": quote,
                    "context": line[start:end],
                    "note": note,
                })
    kinds = {}
    for obs in observations:
        kinds.setdefault(obs["kind"], 0)
        kinds[obs["kind"]] += 1
    return observations, kinds


# ---------------------------------------------------------------------------
# Contract handling
# ---------------------------------------------------------------------------

def deepcopy_obj(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


def fill_contract(contract, values):
    """Fill every key of *contract* from *values* or a placeholder.

    Preserves unknown keys and their nesting; known core keys receive the
    actual audit value. Missing leaves become null / [].
    """
    out = {}
    for key, spec in contract.items():
        if key in values:
            out[key] = deepcopy_obj(values[key])
        elif isinstance(spec, dict):
            out[key] = fill_contract(spec, values)
        elif isinstance(spec, list):
            out[key] = []
        else:
            out[key] = None
    return out


def flatten_leaves(node, top, core_keys):
    """Flatten scalar/list leaves of *node* to *top* by leaf key name.

    Core keys and already-present top-level keys are never overwritten.
    """
    if isinstance(node, dict):
        for key, val in node.items():
            if isinstance(val, dict):
                flatten_leaves(val, top, core_keys)
            else:
                if key not in core_keys and key not in top:
                    top[key] = deepcopy_obj(val)
    elif isinstance(node, list):
        return


# ---------------------------------------------------------------------------
# CLI: extract
# ---------------------------------------------------------------------------

def run_extract(inp: Path, work: Path):
    """Full extraction pipeline. Returns a result dict."""
    work.mkdir(parents=True, exist_ok=True)
    parsed_dir = work / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    files = discover(inp)
    case, case_id, contract, errors = load_case(inp)
    degradations = []
    sources = []
    index = []
    parsed_by_file = {}

    materials = [it for it in files if it["role"] == "material"]
    if not materials:
        degradations.append("no text-like material files discovered under /input")

    for it in materials:
        if it["size"] > MAX_FILE_BYTES:
            degradations.append(f"{it['relpath']}: size {it['size']} > {MAX_FILE_BYTES}, skipped")
            continue
        rel = it["relpath"]
        if it["suffix"] == PDF_SUFFIX:
            out_txt = parsed_dir / (Path(rel).stem + ".txt")
            ok, err = run_pdftotext(it["path"], out_txt)
            if not ok:
                degradations.append(f"{rel}: PDF text extraction failed: {err}")
                sources.append({"path": rel, "role": "material", "parsed": False, "error": err})
                continue
            text, terr = read_text_robust(out_txt, max_bytes=64 * 1024 * 1024)
            if terr:
                degradations.append(f"{rel}: parsed text unreadable: {terr}")
                sources.append({"path": rel, "role": "material", "parsed": False, "error": terr})
                continue
            sources.append({"path": rel, "role": "material", "parsed": True, "bytes": len(text)})
        else:
            text, terr = read_text_robust(it["path"])
            if terr:
                degradations.append(f"{rel}: {terr}")
                sources.append({"path": rel, "role": "material", "parsed": False, "error": terr})
                continue
            parsed_copy = parsed_dir / (Path(rel).stem + ".txt")
            atomic_write_text(parsed_copy, text)
            sources.append({"path": rel, "role": "material", "parsed": True, "bytes": len(text)})
        entries = build_index(rel, text)
        index.extend(entries)
        parsed_by_file[rel] = {"text": text, "index": entries}

    observations, kinds = extract_observations(index)
    # aggregated per-file normalized blobs for later quote verification
    norm_index = {}
    for rel, info in parsed_by_file.items():
        blob, offsets, locators = build_norm_blob(info["index"])
        norm_index[rel] = {"blob": blob, "offsets": offsets, "locators": locators}

    evidence = {
        "case_id": case_id,
        "contract": contract,
        "errors": errors,
        "degradations": degradations,
        "sources": sources,
        "index_count": len(index),
        "observations": observations,
        "observation_kinds": kinds,
        "parsed_files": sorted(parsed_by_file.keys()),
        "norm_index": norm_index,
    }
    files_json = [
        {**it, "path": it["path"].as_posix()}
        for it in files
    ]
    atomic_write_json(work / "files.json", files_json)
    atomic_write_json(work / "evidence.json", evidence)
    atomic_write_json(work / "index.json", index)
    return evidence


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Extract evidence from /input")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--work", default=str(DEFAULT_WORK))
    args = ap.parse_args(argv)
    evidence = run_extract(Path(args.input), Path(args.work))
    print(f"case_id={evidence['case_id']} "
          f"materials={len(evidence['sources'])} "
          f"index={evidence['index_count']} "
          f"observations={len(evidence['observations'])} "
          f"degradations={len(evidence['degradations'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
