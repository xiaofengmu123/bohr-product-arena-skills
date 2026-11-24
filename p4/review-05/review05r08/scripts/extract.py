#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Input discovery, robust text reading, and verbatim evidence-index builder.

Standard library only. Deterministic: the same inputs and parsed text produce
the same evidence.json (files are walked in sorted order and regexes are fixed).

The evidence index is a *checklist hint*, not a verdict: it records where the
material talks about citations, ethics, privacy, copyright, statistics and so
on, so the reviewing agent can cover every facet instead of only the easiest
one. All final judgements must still be made against the actual source text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

DEFAULT_ROOT = Path("/input")
DEFAULT_WORK = Path("/app/work")

TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".tsv", ".tex", ".rst",
                 ".bib", ".yaml", ".yml", ".xml", ".html"}
PDF_SUFFIXES = {".pdf"}
MAX_TEXT_BYTES = 2_000_000
MAX_LINES = 120_000
PARSED_DIR = "parsed"

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def read_text_robust(path: Path, max_bytes: int = MAX_TEXT_BYTES):
    """Return (text, None) on success or (None, error_string)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"read failed: {exc}"
    if len(raw) > max_bytes:
        return None, f"file too large ({len(raw)} bytes > {max_bytes})"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "undecodable text"


def read_json_robust(path: Path):
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"bad json: {exc}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover(root: Path):
    """Classify files under the input root; sorted for determinism."""
    items = []
    root = Path(root)
    if not root.exists():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        if rel.split("/", 1)[0] in {"work", "output", "parsed"}:
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
        elif p.suffix.lower() in PDF_SUFFIXES:
            role = "pdf"
        elif p.suffix.lower() in TEXT_SUFFIXES:
            role = "material"
        else:
            role = "artifact"
        items.append({
            "path": str(p),
            "rel": rel,
            "name": p.name,
            "role": role,
            "suffix": p.suffix.lower(),
            "size": size,
        })
    return items


def split_lines(text: str):
    lines = []
    for i, raw in enumerate(text.splitlines(), 1):
        t = norm(raw)
        if t:
            lines.append({"n": i, "t": t})
    return lines


_SECTION_PATTERNS = [
    ("abstract", re.compile(r"(?im)^\s*(abstract|摘要)\s*[:：]?\s*$")),
    ("introduction", re.compile(r"(?im)^\s*(introduction|background|引言|背景)\s*[:：]?\s*$")),
    ("methods", re.compile(r"(?im)^\s*(methods?|materials?\s+and\s+methods?|方法)\s*[:：]?\s*$")),
    ("results", re.compile(r"(?im)^\s*(results?|结果)\s*[:：]?\s*$")),
    ("discussion", re.compile(r"(?im)^\s*(discussion|conclusions?|discussion\s+and\s+conclusions?|讨论|结论)\s*[:：]?\s*$")),
    ("references", re.compile(r"(?im)^\s*(references?|bibliography|参考文献)\s*[:：]?\s*$")),
    ("ethics", re.compile(r"(?im)^\s*(ethics\s+statement|ethical\s+statement|ethics|伦理|知情同意|informed\s+consent)\s*[:：]?\s*$")),
    ("funding", re.compile(r"(?im)^\s*(funding|acknowledgments?|financial\s+support|基金|资助|致谢)\s*[:：]?\s*$")),
    ("conflict", re.compile(r"(?im)^\s*(conflict\s+of\s+interest|competing\s+interests?|利益冲突)\s*[:：]?\s*$")),
    ("data_availability", re.compile(r"(?im)^\s*(data\s+availability|data\s+sharing|数据可用性|code\s+availability)\s*[:：]?\s*$")),
]

_CITE_MARKERS = [
    re.compile(r"\[\d{1,3}(?:[,\-–]\d{1,3})*\]"),
    re.compile(r"\([A-Z][A-Za-z'’\-]+(?:\s+(?:and|&|et\s+al\.?)\s+[A-Z][A-Za-z'’\-]+)?,?\s*\d{4}[a-z]?\)"),
    re.compile(r"(?i)\b(?:doi|doi\.org|https?://doi)\b"),
]

_MEDIA_MARKERS = [
    re.compile(r"(?i)\b(?:figure|fig\.|table|scheme|chart|graph|image)\s*\d+"),
    re.compile(r"(?i)\b(?:github|gitlab|source\s+code|code\s+availability|repository|doi:|https?://)"),
    re.compile(r"(?i)\b(?:license|licence|cc-by|cc\s+by|creative\s+commons|mit\s+license|gpl|apache-2\.0|bsd|permission|copyright)"),
]

_PII_MARKERS = [
    re.compile(r"(?i)\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    re.compile(r"(?i)\b(?:phone|telephone|手机|电话|身份证|id\s+number|social\s+security|ssn|date\s+of\s+birth|dob|姓名|participant\s+id)"),
]

_STAT_MARKERS = [
    re.compile(r"(?i)\bp\s*[<>=]\s*\.?\d+"),
    re.compile(r"(?i)\b(?:confidence\s+interval|\bci\b)"),
    re.compile(r"(?i)\bn\s*=\s*\d+"),
    re.compile(r"(?i)\b(?:mean|median|standard\s+deviation|std\s+dev|\bsd\b|t-test|anova|chi-square|regression|correlation|cohen'?s\s+d|odds\s+ratio|hazard\s+ratio)"),
]

_ETHICS_MARKERS = [
    re.compile(r"(?i)\b(?:irb|institutional\s+review\s+board|ethics\s+committee|伦理委员会|伦理批件|approval\s+number|consent\s+form|informed\s+consent|waiver)"),
    re.compile(r"(?i)\b(?:animal|mouse|mice|rat|zebrafish|non-human\s+primate|rabbit)"),
]


def _hit_lines(lines, patterns, cap=25):
    hits = []
    for ln in lines:
        if any(p.search(ln["t"]) for p in patterns):
            hits.append(ln["n"])
        if len(hits) >= cap:
            break
    return hits


def scan_signals(text: str, lines):
    return {
        "sections": {name: _hit_lines(lines, [pat], 3) for name, pat in _SECTION_PATTERNS},
        "citation_lines": _hit_lines(lines, _CITE_MARKERS, 40),
        "media_code_lines": _hit_lines(lines, _MEDIA_MARKERS, 40),
        "pii_lines": _hit_lines(lines, _PII_MARKERS, 30),
        "statistics_lines": _hit_lines(lines, _STAT_MARKERS, 40),
        "ethics_lines": _hit_lines(lines, _ETHICS_MARKERS, 40),
    }


def _rel_to_basename(rel: str) -> str:
    return Path(rel).name


def build_index(root: Path, work: Path):
    files = discover(root)
    errors = []
    degradations = []
    index = {}
    signals_by_file = {}
    text_files = []

    # 1) Directly readable text material under /input.
    for it in files:
        if it["role"] in {"material", "task"}:
            path = Path(it["path"])
            text, err = read_text_robust(path)
            if err:
                errors.append(f"{it['rel']}: {err}")
                it["text_available"] = False
                continue
            lines = split_lines(text)
            if len(lines) > MAX_LINES:
                lines = lines[:MAX_LINES]
                degradations.append(f"{it['rel']}: truncated to {MAX_LINES} lines")
            it["text_available"] = True
            it["line_count"] = len(lines)
            index[it["rel"]] = {"lines": lines}
            signals_by_file[it["rel"]] = scan_signals(text, lines)
            text_files.append(it["rel"])
            try:
                it["sha256"] = sha256_hex(Path(it["path"]).read_bytes())
            except OSError:
                pass

    # 2) Parsed PDF text (produced by pdf-text-tool / pdftotext) under work/parsed.
    parsed_dir = Path(work) / PARSED_DIR
    parsed_map = {}
    if parsed_dir.exists():
        for p in sorted(parsed_dir.rglob("*.txt")):
            text, err = read_text_robust(p)
            if err:
                errors.append(f"{PARSED_DIR}/{p.name}: {err}")
                continue
            lines = split_lines(text)
            if len(lines) > MAX_LINES:
                lines = lines[:MAX_LINES]
                degradations.append(f"{PARSED_DIR}/{p.name}: truncated to {MAX_LINES} lines")
            key = f"{PARSED_DIR}/{p.name}"
            index[key] = {"lines": lines}
            signals_by_file[key] = scan_signals(text, lines)
            # Map "parsed/paper.txt" back to original "paper.pdf".
            parsed_map[_rel_to_basename(p.name)] = key

    # 3) Mark PDFs as pending text extraction.
    for it in files:
        if it["role"] == "pdf":
            base = _rel_to_basename(it["name"])
            it["text_available"] = any(base in k for k in parsed_map)
            it["parsed_as"] = parsed_map.get(base)

    summary = {
        "files": files,
        "errors": errors,
        "degradations": degradations,
        "text_files": text_files,
        "parsed_map": parsed_map,
    }
    return {
        "schema_version": 2,
        "summary": summary,
        "index": index,
        "signals": signals_by_file,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="input root (default /input)")
    ap.add_argument("--work", default=str(DEFAULT_WORK), help="work dir (default /app/work)")
    args = ap.parse_args(argv)

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    evidence = build_index(Path(args.root), work)
    out = work / "evidence.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)

    files = evidence["summary"]["files"]
    readable = [f for f in files if f.get("text_available")]
    print(f"evidence.json written: {out}")
    print(f"files={len(files)} readable={len(readable)} "
          f"errors={len(evidence['summary']['errors'])} "
          f"degradations={len(evidence['summary']['degradations'])}")
    for f in files:
        flag = "T" if f.get("text_available") else "-"
        print(f"  [{flag}] {f['role']:8s} {f['rel']} ({f['size']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
