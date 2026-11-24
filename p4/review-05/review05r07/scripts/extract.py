#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic extraction and source indexing for the audit skill.

Standard library only. Responsibilities:
- robust text/JSON reading with encoding fallback and size limits
- PDF text extraction via pdftotext (or reuse of previously parsed text)
- file discovery and role classification under an input root
- paragraph-level source index with normalized text for quote verification
- deterministic candidate-observation detection (signals, never verdicts)
- output_contract defaulting and leaf flattening

Nothing here emits a verdict. It only produces candidates the reviewing agent
must confirm, exclude, or mark pending.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt", ".md", ".rst", ".tex", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".py", ".r", ".bib", ".yaml", ".yml",
}
MAX_BYTES = 2 * 1024 * 1024  # skip single files larger than 2 MB
MAX_OBS_PER_SIGNAL = 15      # cap candidate observations per signal

# Case-level keys that may carry the concrete review facets.
FACET_KEYS = (
    "review_facets", "facets", "checklist", "check_items", "review_items",
    "evidence_requirements", "finding_types", "required_findings", "criteria",
)


def normalize(text: str) -> str:
    """Collapse any whitespace run to a single space and strip ends."""
    return re.sub(r"\s+", " ", text).strip()


def read_text_robust(path: Path, max_bytes: int = MAX_BYTES):
    """Return (text, error). Never raises on bad encoding or oversize files."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", f"cannot stat: {exc}"
    if size > max_bytes:
        return "", f"file too large ({size} bytes > {max_bytes})"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return "", f"cannot read: {exc}"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), ""
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "undecodable; replaced"


def read_json_robust(path: Path):
    """Return (obj, error). obj is None on failure."""
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def discover(inp: Path):
    """Classify files under inp. Returns list of dicts (deterministic order)."""
    items = []
    if not inp.exists():
        return items
    for p in sorted(inp.rglob("*"), key=lambda x: x.as_posix().lower()):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        name = p.name.lower()
        if name == "case.json" or (name.startswith("case") and name.endswith(".json")):
            role = "case"
        elif name == "task.md" or (name.startswith("task") and name.endswith(".md")):
            role = "task"
        elif p.suffix.lower() in TEXT_SUFFIXES:
            role = "material"
        else:
            role = "artifact"
        items.append({
            "path": p,
            "abs": p.as_posix(),
            "rel": p.relative_to(inp).as_posix() if inp.exists() else p.as_posix(),
            "name": p.name,
            "role": role,
            "size": size,
        })
    return items


def pdf_to_text(pdf: Path, work: Path):
    """Extract PDF text via pdftotext into work/parsed/<stem>.txt.

    Returns (txt_path, text, error). Falls back to an already-parsed file.
    """
    parsed_dir = work / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    out = parsed_dir / (pdf.stem + ".txt")
    if out.exists() and out.stat().st_size > 0:
        text, err = read_text_robust(out)
        if not err:
            return out, text, ""
    if shutil.which("pdftotext") is None:
        return out, "", "pdftotext not found on PATH"
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(pdf), str(out)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return out, "", f"pdftotext failed: {exc}"
    if proc.returncode != 0 or not out.exists():
        msg = (proc.stderr or "").strip()[:300]
        return out, "", f"pdftotext exit {proc.returncode}: {msg}"
    text, err = read_text_robust(out)
    if err:
        return out, "", err
    return out, text, ""


def build_paragraphs(text: str):
    """Split text into paragraphs/lines. Each item has n, text, norm."""
    paragraphs = []
    buf = []
    for line in text.split("\n"):
        if line.strip():
            buf.append(line)
        elif buf:
            joined = "\n".join(buf)
            paragraphs.append({"n": len(paragraphs) + 1, "text": joined, "norm": normalize(joined)})
            buf = []
    if buf:
        joined = "\n".join(buf)
        paragraphs.append({"n": len(paragraphs) + 1, "text": joined, "norm": normalize(joined)})
    return paragraphs


def extract_source_index(files, inp: Path, work: Path):
    """Return (sources, errors, degradations). sources carry paragraph text."""
    sources = []
    errors = []
    degradations = []
    for it in files:
        if it["role"] == "case" or it["role"] == "task":
            continue
        entry = {
            "path": it["rel"],
            "abs": it["abs"],
            "role": it["role"],
            "size": it["size"],
            "extracted": False,
            "paragraphs": [],
            "full_norm": "",
            "error": None,
        }
        text = ""
        err = ""
        if it["path"].suffix.lower() == ".pdf":
            _txt_path, text, err = pdf_to_text(it["path"], work)
            if err:
                degradations.append({"path": it["rel"], "issue": err})
        elif it["role"] == "material":
            text, err = read_text_robust(it["path"])
            if err:
                entry["error"] = err
                errors.append({"path": it["rel"], "issue": err})
        # artifacts (non-text, non-PDF) are not parsed; noted but not errors.
        if text and not err:
            paragraphs = build_paragraphs(text)
            entry["extracted"] = True
            entry["paragraphs"] = paragraphs
            entry["full_norm"] = " ".join(p["norm"] for p in paragraphs if p["norm"])
        if entry["error"] is None and it["role"] == "material" and not text and not err:
            entry["error"] = "no text extracted"
        sources.append(entry)
    return sources, errors, degradations


# ---------------------------------------------------------------------------
# Candidate observations. These are deterministic signals, not verdicts.
# ---------------------------------------------------------------------------

_PATTERNS = [
    ("citation_marker", "citation",
     r"(\[[0-9]{1,3}(?:\s*[,，\-–]\s*[0-9]{1,3})*\]|\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.?)?,\s*(?:19|20)\d{2}[a-z]?\))"),
    ("pii_email", "privacy",
     r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"),
    ("pii_phone", "privacy",
     r"((?:\+?\d[\d\s\-()]{6,}\d))"),
    ("pii_id", "privacy",
     r"(\b\d{17}[\dXx]\b|\b\d{15,18}\b)"),
    ("ethics_keyword", "ethics",
     r"(informed\s+consent|IRB|ethics?\s+(?:approval|committee|review)|approval\s+(?:number|no\.?)|知情同意|伦理审查|伦理批件|伦理委员会|批准号)"),
    ("statistics_marker", "statistics",
     r"(\bp\s*[<>=]\s*0?\.\d+\b|\bn\s*=\s*\d+\b|confidence\s+interval|\bCI\b|standard\s+deviation|\bSD\b|±|\bstatistically\s+significant\b|significance\s+level)"),
    ("license_keyword", "copyright",
     r"(adapted\s+from|reproduced\s+with|with\s+permission|copyright|©|\ball\s+rights\s+reserved|\bCC\s?BY\b|creative\s+commons|license|许可|授权|版权所有)"),
    ("funding_keyword", "academic_integrity",
     r"(funding|grant\s+(?:number|no\.?)|supported\s+by|conflict\s+of\s+interest|competing\s+interests|acknowledg|基金|资助|致谢|利益冲突)"),
    ("figure_reference", "quality",
     r"(\b(?:Figure|Fig\.?|Table)\s+\d+|\b[图表]\d+|\b(?:Supplementary|Supporting)\s+(?:Figure|Table|Information)\s*\d*)"),
    ("data_availability", "quality",
     r"(data\s+availability|code\s+availability|deposited|repository|\bDOI\s*[:：]?\s*10\.\d{4,9}/|GEO|SRA|Zenodo|Figshare)"),
]


def detect_observations(sources):
    """Return list of candidate observations across all extracted sources."""
    observations = []
    per_signal = {name: 0 for name, _cat, _re in _PATTERNS}
    for src in sources:
        if not src["extracted"]:
            continue
        for para in src["paragraphs"]:
            norm = para["norm"]
            if not norm:
                continue
            for signal, category, pattern in _PATTERNS:
                if per_signal[signal] >= MAX_OBS_PER_SIGNAL:
                    continue
                for m in re.finditer(pattern, norm, flags=re.IGNORECASE):
                    if per_signal[signal] >= MAX_OBS_PER_SIGNAL:
                        break
                    matched = m.group(1) if m.lastindex else m.group(0)
                    matched = matched.strip()
                    if not matched:
                        continue
                    start = max(0, m.start() - 120)
                    end = min(len(norm), m.end() + 120)
                    context = norm[start:end]
                    observations.append({
                        "id": f"obs-{signal}-{per_signal[signal] + 1}",
                        "signal": signal,
                        "category": category,
                        "file": src["path"],
                        "locator": f"p{para['n']}",
                        "quote": matched[:300],
                        "context": context,
                    })
                    per_signal[signal] += 1
    return observations


def default_for(value):
    """Structural default: dict keeps keys, list -> [], scalar -> None."""
    if isinstance(value, dict):
        return {k: default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def flatten_contract(contract):
    """Map each leaf key of the contract to its structural default."""
    out = {}

    def walk(obj, path):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, path + [k])
        elif isinstance(obj, list):
            if path:
                out[path[-1]] = []
        else:
            if path:
                out[path[-1]] = None

    walk(contract, [])
    return out


def load_facets(case):
    """Extract the concrete review facets declared by a case, if any."""
    facets = []
    for key in FACET_KEYS:
        val = case.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    facets.append(item.strip())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("facet") or item.get("id") or item.get("title")
                    if isinstance(name, str) and name.strip():
                        facets.append(name.strip())
        elif isinstance(val, str) and val.strip():
            facets.append(val.strip())
    # De-duplicate while preserving order.
    seen = set()
    out = []
    for f in facets:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
