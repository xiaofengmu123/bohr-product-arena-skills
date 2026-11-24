#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic evidence extraction for the source-bound-audit skill.

Reads an input directory (default /input), classifies files, parses text and
PDF material into a *source index*, and detects *candidate* patterns such as
citations, personally identifiable information, statistical expressions and
ethics/funding/licence declarations. Every detected item is an observation
that the auditing agent must verify against the original text; none of them
is a final finding. This split is what makes the pipeline suppress false
positives: the script proposes, the agent disposes.

Standard library only. PDF text extraction shells out to ``pdftotext`` when
present and degrades gracefully otherwise (the agent may then use the
platform ``pdf-text-tool`` and call :func:`index_text` on the result).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/input")
MAX_FILE = 2 * 1024 * 1024  # 2 MB single-file cap
TEXT_SUFFIXES = {
    ".md", ".txt", ".tex", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".bib", ".yaml", ".yml",
}
PDF_SUFFIXES = {".pdf"}
OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls", ".odt", ".ods"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg"}

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# ---------------------------------------------------------------------------
# Pattern library. Each rule maps a label to a compiled regex. These are
# *candidate* signals only; they carry locators so the agent can check them.
# ---------------------------------------------------------------------------
CITATION_PATTERNS = {
    "bracket-citation": re.compile(r"\[\s*\d+(?:\s*[-,;，]\s*\d+\s*)*\]"),
    "author-year": re.compile(
        r"\b[A-Z][A-Za-z\u00c0-\u017f'\-]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][A-Za-z\u00c0-\u017f'\-]+)?,?\s+\((?:19|20)\d{2}[a-z]?\)"
    ),
    "figure-table-ref": re.compile(
        r"\b(?:Fig\.?|Figure|Table|Tab\.?)\s+\d+(?:\s*[-,，;]\s*\d+)*\b", re.IGNORECASE
    ),
}

PII_PATTERNS = {
    "email": re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"),
    "phone": re.compile(
        r"(?:\+?\d{1,3}[\-.\s]?)?(?:\(\d{2,4}\)|\d{2,4})[\-.\s]\d{3,4}[\-.\s]\d{3,4}(?!\d)"
    ),
    "cn-id": re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]\b"),
    "dob": re.compile(r"\b(?:19|20)\d{2}[\-/.年](?:0?[1-9]|1[0-2])[\-/.月](?:0?[1-9]|[12]\d|3[01])[日]?"),
}

STATS_PATTERNS = {
    "p-value": re.compile(r"\bp\s*[=<>≤≥<>]\s*0?\.\d+\b", re.IGNORECASE),
    "confidence-interval": re.compile(r"\b(?:95\s*%|99\s*%|90\s*%)\s*(?:CI|confidence interval)\b", re.IGNORECASE),
    "sample-size": re.compile(r"\b[nN]\s*=\s*\d+\b"),
    "mean-sd": re.compile(
        r"\b(?:mean|M|SD|SEM|IQR|median|range|odds ratio|hazard ratio|risk ratio|OR|HR|RR)\b",
        re.IGNORECASE,
    ),
    "statistical-test": re.compile(
        r"\b(?:t[- ]?test|ANOVA|ANCOVA|chi[- ]?square|χ2|χ²|Mann[- ]?Whitney|Wilcoxon|"
        r"Kruskal[- ]?Wallis|Fisher|log[- ]?rank|Kaplan[- ]?Meier|Cox|regression|"
        r"correlation|Pearson|Spearman|Bland[- ]?Altman|exact test|bootstrap)\b",
        re.IGNORECASE,
    ),
    "effect-size": re.compile(
        r"\b(?:power|effect size|Cohen'?s\s+d|η2|η²|partial\s+η2|omega\s+squared)\b", re.IGNORECASE
    ),
}

DECLARATION_PATTERNS = {
    "ethics-approval": re.compile(
        r"\b(?:ethics(?:al)?\s+(?:approval|review)|IRB|Institutional\s+Review\s+Board|"
        r"伦理|道德委员会|伦理审查|审批(?:编号|号)?|EC\s*/\s*\d+)\b",
        re.IGNORECASE,
    ),
    "informed-consent": re.compile(r"\b(?:informed\s+consent|知情同意|consent\s+form|同意书)\b", re.IGNORECASE),
    "funding": re.compile(
        r"\b(?:funding|grant|funded|基金|资助|项目号|课题号|NSFC|NIH|National\s+Science\s+Foundation|"
        r"European\s+Research\s+Council|acknowledg(?:e|ment))\b",
        re.IGNORECASE,
    ),
    "coi": re.compile(r"\b(?:conflict(?:s)?\s+of\s+interest|competing\s+interest|利益冲突|COI|disclosure)\b", re.IGNORECASE),
    "data-availability": re.compile(
        r"\b(?:data\s+availability|data\s+sharing|dataset|repository|数据可得|数据共享|Figshare|Zenodo|Dryad|GEO|ENA)\b",
        re.IGNORECASE,
    ),
    "code-availability": re.compile(r"\b(?:code\s+availability|source\s+code|代码可得|GitHub|GitLab)\b", re.IGNORECASE),
    "license": re.compile(
        r"\b(?:CC[-\s]?BY|CC0|MIT|GPL|Apache|BSD|license|licence|许可|版权|copyright|©)\b",
        re.IGNORECASE,
    ),
    "animal": re.compile(r"\b(?:animal|mice|mouse|rat|primate|动物|ARRIVE|IACUC|humane\s+endpoint)\b", re.IGNORECASE),
    "human-subjects": re.compile(r"\b(?:human\s+subjects|participants|subjects|患者|受试者|volunteer)\b", re.IGNORECASE),
}


def normalize(text: str) -> str:
    """Collapse all whitespace so quotes can be matched across line breaks."""
    return re.sub(r"\s+", " ", text).strip()


def read_text_robust(path: Path):
    """Return (text, encoding_used_or_none, degradation_note_or_empty)."""
    data = path.read_bytes()
    if len(data) > MAX_FILE:
        return None, None, "file exceeds 2MB, skipped"
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc, ""
        except (UnicodeDecodeError, LookupError):
            continue
    return None, None, "undecodable text, skipped"


def read_json_robust(path: Path):
    """Return (obj_or_None, error_or_empty)."""
    text, _enc, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), ""
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"bad JSON: {exc}"


def parse_pdf(path: Path, work_dir: Path):
    """Extract PDF text via pdftotext; return (text, note)."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            timeout=120,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.decode("utf-8", errors="replace"), ""
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return None, f"pdftotext rc={proc.returncode}: {stderr[:200]}"
    except FileNotFoundError:
        return None, "pdftotext not found"
    except Exception as exc:  # noqa: BLE001 - report any failure honestly
        return None, f"pdftotext failed: {exc}"


def split_paragraphs(text: str):
    """Split on blank lines; return list of dicts with locator, offset, text."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n", raw)
    out = []
    pos = 0
    for i, part in enumerate(parts, 1):
        norm = normalize(part)
        if not norm:
            pos += len(part) + 2
            continue
        stripped = part.strip()
        found = raw.find(stripped, pos) if stripped else pos
        out.append({
            "loc": "p%d" % i,
            "offset": found if found >= 0 else pos,
            "chars": len(norm),
            "text": norm,
        })
        pos = (found if found >= 0 else pos) + max(len(part), 1)
    return out


def detect_patterns(text: str, loc: str, relpath: str):
    """Return candidate hits for one paragraph."""
    hits = []
    for group_name, group in (
        ("citation", CITATION_PATTERNS),
        ("pii", PII_PATTERNS),
        ("statistics", STATS_PATTERNS),
        ("declaration", DECLARATION_PATTERNS),
    ):
        for label, rx in group.items():
            for m in rx.finditer(text):
                snippet = normalize(m.group(0))[:160]
                if not snippet:
                    continue
                hits.append({
                    "kind": group_name,
                    "label": label,
                    "loc": loc,
                    "text": snippet,
                })
    return hits


def classify(p: Path, root: Path):
    """Classify a file under the input root."""
    name = p.name.lower()
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    if name == "case.json" or (name.startswith("case") and name.endswith(".json")):
        role = "case"
    elif name == "task.md" or (name.startswith("task") and name.endswith((".md", ".txt"))):
        role = "task"
    elif p.suffix.lower() in TEXT_SUFFIXES:
        role = "material"
    elif p.suffix.lower() in PDF_SUFFIXES:
        role = "material"
    elif p.suffix.lower() in OFFICE_SUFFIXES or p.suffix.lower() in IMAGE_SUFFIXES:
        role = "artifact"
    else:
        role = "artifact"
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        rel = p.name
    return {"abspath": str(p), "path": rel, "name": p.name, "role": role, "size": size}


def discover(root: Path):
    items = []
    if not root.exists():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        items.append(classify(p, root))
    return items


def load_case(root: Path):
    """Return (case_obj, case_id, contract, errors)."""
    errors = []
    items = discover(root)
    case_item = next((it for it in items if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, ["case.json not found under input root"]
    case, err = read_json_robust(Path(case_item["abspath"]))
    if err:
        return None, "unknown", {}, [f"case.json unreadable: {err}"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key) if isinstance(case, dict) else None
        if isinstance(val, str) and val:
            case_id = val
            break
    if not case_id:
        case_id = Path(case_item["path"]).stem
    contract = {}
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        contract = case["output_contract"]
    return case, case_id, contract, errors


def build_index(root: Path, work_dir: Path):
    """Parse every material and produce the deterministic source index.

    Returns a dict ready for ``evidence.json``.
    """
    items = discover(root)
    sources = []
    degradations = []
    src_counter = 1

    for it in items:
        if it["role"] not in ("material", "task", "case"):
            continue
        abspath = Path(it["abspath"])
        suffix = abspath.suffix.lower()
        entry = {
            "id": "src%d" % src_counter,
            "path": it["path"],
            "role": it["role"],
            "size": it["size"],
            "kind": "text" if suffix in TEXT_SUFFIXES else "pdf" if suffix in PDF_SUFFIXES else "unknown",
            "parse": "ok",
            "note": "",
            "paragraphs": [],
            "hits": [],
        }
        src_counter += 1

        if it["size"] > MAX_FILE:
            entry["parse"] = "skipped"
            entry["note"] = "exceeds 2MB, skipped"
            degradations.append(f"{it['path']}: exceeds 2MB, skipped")
            sources.append(entry)
            continue

        if suffix in PDF_SUFFIXES:
            text, note = parse_pdf(abspath, work_dir)
            if text is None:
                entry["parse"] = "failed"
                entry["note"] = note
                degradations.append(f"{it['path']}: PDF parse failed ({note})")
                sources.append(entry)
                continue
            entry["kind"] = "pdf"
        elif suffix in TEXT_SUFFIXES:
            text, _enc, err = read_text_robust(abspath)
            if text is None:
                entry["parse"] = "failed"
                entry["note"] = err
                degradations.append(f"{it['path']}: {err}")
                sources.append(entry)
                continue
        else:
            entry["parse"] = "skipped"
            entry["note"] = f"unsupported suffix {suffix}; agent should use office-renderer/pdf-render-tool"
            degradations.append(f"{it['path']}: unsupported format {suffix}, not parsed by extract")
            sources.append(entry)
            continue

        paragraphs = split_paragraphs(text)
        entry["paragraphs"] = [
            {"loc": p["loc"], "offset": p["offset"], "chars": p["chars"], "text": p["text"]}
            for p in paragraphs
            if p["text"]
        ]
        for p in entry["paragraphs"]:
            entry["hits"].extend(detect_patterns(p["text"], p["loc"], it["path"]))

        sources.append(entry)

    return {"sources": sources, "degradations": list(degradations)}


def default_for(contract):
    """Echo every contract key with a null/[] placeholder, preserving shape."""
    out = {}
    for key, value in contract.items():
        if isinstance(value, dict):
            out[key] = default_for(value)
        elif isinstance(value, list):
            out[key] = []
        elif isinstance(value, bool):
            out[key] = False
        else:
            out[key] = None
    return out


def flatten_leaves(obj, prefix=""):
    """Return leaf key -> value for a nested dict/list structure."""
    leaves = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            leaves.update(flatten_leaves(v, prefix=("%s.%s" % (prefix, k)) if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaves.update(flatten_leaves(v, prefix="%s.%d" % (prefix, i)))
    else:
        leaf_name = prefix.rsplit(".", 1)[-1] if prefix else "value"
        leaves[leaf_name] = obj
    return leaves


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract deterministic evidence index.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--work", default=str(Path("/app/work")))
    parser.add_argument("--json", action="store_true", help="print evidence JSON to stdout")
    args = parser.parse_args(argv)

    root = Path(args.root)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    _case, case_id, contract, errors = load_case(root)
    index = build_index(root, work)
    index["case_id"] = case_id
    index["contract"] = contract
    index["contract_missing"] = not contract
    index["errors"] = errors

    out_path = work / "evidence.json"
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)

    if args.json:
        print(json.dumps(index, ensure_ascii=False, indent=2))
    else:
        n_src = len(index["sources"])
        n_hits = sum(len(s["hits"]) for s in index["sources"])
        print(f"evidence.json written: {n_src} sources, {n_hits} candidate hits, "
              f"{len(index['degradations'])} degradations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
