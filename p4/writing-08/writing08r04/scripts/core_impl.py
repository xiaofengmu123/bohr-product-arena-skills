#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the Methods & Experiments chapter-writing Skill.

Standard library only. Imported by scripts/main.py and scripts/selftest.py.
Not intended to be run standalone, but has no import side effects.
"""

import json
import os
import re
import shutil
import subprocess
import zipfile

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB per input file
MAX_FACTS = 400

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py", ".toml",
    ".cfg", ".ini", ".sh", ".log", ".csv", ".tsv", ".rst", ".tex", ".bib",
    ".xml", ".out", ".err", ".ipynb", ".nfo",
}
PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".odt", ".ods", ".fodt"}

STANDARD_FIELDS = [
    "status", "artifacts", "sources", "uncertainty", "limitations",
    "pending_human", "change_log", "errors", "degradations", "runtime",
]

SIX_ELEMENTS = {
    "methods_steps": {
        "label": "方法步骤",
        "required": ["input", "operation", "output", "condition"],
        "status": "not_found",
        "evidence": [],
    },
    "experiment_setup": {
        "label": "实验设置",
        "required": [
            "hardware", "software_versions", "environment", "random_seed",
            "repetitions", "train_val_test_separated",
        ],
        "status": "not_found",
        "evidence": [],
    },
    "parameters": {
        "label": "参数与来源",
        "required": ["name", "value", "unit", "object", "source"],
        "status": "not_found",
        "evidence": [],
    },
    "dataset": {
        "label": "数据集与划分",
        "required": [
            "name", "version", "size", "preprocessing", "split_ratios",
            "randomization", "subset_usage",
        ],
        "status": "not_found",
        "evidence": [],
    },
    "comparison": {
        "label": "对照实验",
        "required": [
            "baseline_name", "baseline_version", "same_budget",
            "metrics", "statistics",
        ],
        "status": "not_found",
        "evidence": [],
    },
    "reproducibility_limits": {
        "label": "可复现与限制",
        "required": [
            "code_version", "environment", "commands", "data_access",
            "failure_scenarios", "scope",
        ],
        "status": "not_found",
        "evidence": [],
    },
}


# --------------------------------------------------------------------------- #
# Generic IO helpers
# --------------------------------------------------------------------------- #
def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_text_any(path):
    """Read a UTF-8 text file, tolerating BOM and broken encodings."""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_json_any(path):
    """Load JSON, tolerating BOM / bad encoding / trailing junk."""
    text = read_text_any(path)
    text = text.lstrip("\ufeff \t\r\n")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(text)
            return obj
        except json.JSONDecodeError:
            return None


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def norm_rel(path, base):
    """Return a POSIX-style relative path, or the absolute path if not under base."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def is_text_file(path):
    name = os.path.basename(path).lower()
    if name in ("makefile", "dockerfile"):
        return True
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return True
    if ext == "":
        try:
            with open(path, "rb") as fh:
                head = fh.read(1024)
            if b"\x00" not in head:
                return True
        except OSError:
            return False
    return False


# --------------------------------------------------------------------------- #
# Office / PDF text extraction (standard library only)
# --------------------------------------------------------------------------- #
def _strip_xml(xml_bytes):
    text = xml_bytes.decode("utf-8", errors="replace")
    text = re.sub(r"</w:p>", "\n", text)
    text = re.sub(r"</a:p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def extract_office_text(path):
    """Extract readable text from OOXML (docx/pptx/xlsx) using zipfile."""
    parts = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            for cand in ("word/document.xml", "content.xml"):
                if cand in names:
                    parts.append(_strip_xml(zf.read(cand)))
            slides = sorted(
                (n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"(\d+)", n).group(1)),
            )
            for n in slides:
                parts.append(_strip_xml(zf.read(n)))
            for cand in ("xl/sharedStrings.xml", "content.xml"):
                if cand in names:
                    parts.append(_strip_xml(zf.read(cand)))
    except (zipfile.BadZipFile, OSError):
        return None
    text = "\n".join(p for p in parts if p and p.strip())
    return text if text.strip() else None


def extract_pdf_text(path, work_dir):
    """Extract PDF text via pdftotext if available; return (text, ok, note)."""
    if shutil.which("pdftotext") is None:
        return None, False, "pdftotext not available; use platform pdf-text-tool"
    out_path = os.path.join(work_dir, "extracted", os.path.basename(path) + ".txt")
    ensure_dir(os.path.dirname(out_path))
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, out_path],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, False, "pdftotext failed: %s" % exc
    if proc.returncode != 0 or not os.path.exists(out_path):
        return None, False, "pdftotext exit %s" % proc.returncode
    return read_text_any(out_path), True, None


# --------------------------------------------------------------------------- #
# Candidate fact extraction (candidates only; never asserted)
# --------------------------------------------------------------------------- #
_NUM_UNIT = re.compile(
    r"(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"(?P<unit>%|ms|s|min|h|GB|MB|KB|TB|GHz|MHz|epoch|ep|batch|layers?|"
    r"steps?|iters?|lr|nm|mm|cm|m|px|FPS|fps|k|M|B|n|N|°C|K|dim|d)"
)
_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+.][0-9A-Za-z]+)*\b")
_ASSIGN = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)\s*[:=]\s*"
    r"(?P<value>[^#\n,;]+?)\s*(?:[#,;]|$)"
)
_SEED_KEYS = {"seed", "random_seed", "random_state", "randomseed",
              "torch_seed", "seed_everything"}


def _clean_value(value):
    value = value.strip()
    value = value.strip('"\'').strip()
    if len(value) > 120:
        value = value[:120] + "…"
    return value


def extract_facts_from_text(text, locator_prefix, mode):
    """Return candidate facts. `mode` is 'line' or 'page'."""
    facts = []
    if mode == "page":
        blocks = text.split("\f")
        lines_per_block = [(i + 1, block.splitlines()) for i, block in enumerate(blocks)]
    else:
        lines_per_block = [(None, text.splitlines())]

    for page, lines in lines_per_block:
        for idx, raw_line in enumerate(lines):
            line = raw_line.rstrip("\n")
            loc = "%s#L%d" % (locator_prefix, idx + 1) if mode == "line" \
                else "%s#p%d" % (locator_prefix, page)
            stripped = line.strip()
            if not stripped or len(stripped) > 400:
                continue

            for m in _ASSIGN.finditer(line):
                key = m.group("key")
                value = _clean_value(m.group("value"))
                if not value or value in ("None", "null", "{}", "[]", "()"):
                    continue
                kind = "seed" if key.lower() in _SEED_KEYS else "assignment"
                facts.append({
                    "kind": kind, "name": key, "value": value, "unit": None,
                    "object": None, "locator": loc, "quote": stripped,
                })

            for m in re.finditer(r"\b(seed|random_seed|random_state)\s*[:=]\s*(\d+)",
                                 line, re.I):
                facts.append({
                    "kind": "seed", "name": m.group(1), "value": m.group(2),
                    "unit": None, "object": None, "locator": loc, "quote": stripped,
                })

            for m in _NUM_UNIT.finditer(line):
                facts.append({
                    "kind": "quantity", "name": None,
                    "value": m.group("num"), "unit": m.group("unit"),
                    "object": None, "locator": loc, "quote": stripped,
                })

            for m in _VERSION.finditer(line):
                facts.append({
                    "kind": "version", "name": None, "value": m.group(0),
                    "unit": None, "object": None, "locator": loc, "quote": stripped,
                })

            if re.match(r"^\s*(\$|>)\s*\S", line) or re.match(
                r"^\s*(python|pip|conda|docker|git|bash|sh)\s+\S", line, re.I
            ):
                facts.append({
                    "kind": "command", "name": None, "value": None,
                    "unit": None, "object": None, "locator": loc, "quote": stripped,
                })

    seen = set()
    unique = []
    for f in facts:
        key = (f["locator"], f["quote"], f["kind"], f.get("value"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
        if len(unique) >= MAX_FACTS:
            break
    for i, f in enumerate(unique):
        f["id"] = "F%d" % (i + 1)
    return unique


# --------------------------------------------------------------------------- #
# Output contract discovery
# --------------------------------------------------------------------------- #
def extract_output_contract_keys(case):
    oc = case.get("output_contract") if isinstance(case, dict) else None
    keys = []
    if isinstance(oc, dict):
        keys = list(oc.keys())
    elif isinstance(oc, list):
        for item in oc:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for k in ("name", "key", "field", "id"):
                    if item.get(k):
                        keys.append(str(item[k]))
                        break
    elif isinstance(oc, str) and oc.strip():
        parts = [s.strip() for s in oc.split(",") if s.strip()]
        keys = parts if parts else [oc.strip()]
    out = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def discover_case_id(case):
    if not isinstance(case, dict):
        return "unknown"
    for k in ("case_id", "id", "challenge_id"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return "unknown"
