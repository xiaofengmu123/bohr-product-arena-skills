#!/usr/bin/env python3
"""Inventory materials and extract candidate anchors (sections, numbers, stats).

Writes /app/work/evidence.json. The extracted candidates are *candidate*
anchors for the model to verify against the source; they are never ground
truth. Only the Python standard library is used.
"""
import json
import os
import re
import shutil
import subprocess
import sys

DEFAULTS = {
    "input_dir": "/input",
    "work_dir": "/app/work",
    "evidence_json": "/app/work/evidence.json",
}

TEXT_EXTS = {".md", ".txt", ".tex", ".bib", ".csv", ".tsv", ".json", ".yaml", ".yml"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff"}
PDF_EXT = ".pdf"
OFFICE_EXTS = {".docx", ".pptx", ".xlsx"}
MAX_FILE = 2_000_000  # 2 MB per file

NUMBER_RE = re.compile(
    r"(?<![\w.])([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*"
    r"(%|±|p\s*[=<>≤≥]|n\s*=|CI|SD|SEM|Hz|nm|μm|mm|cm|m|s|ms|min|h|g|kg|L|mL|"
    r"M|mM|μM|nM|°C|K)?", re.I)
STAT_RE = re.compile(
    r"\b(p|t|F|χ2|χ²|r|R²|OR|HR|RR|CI|n|N|df|SD|SEM)\s*[=<>≤≥]?\s*[-+]?\d+(?:\.\d+)?",
    re.I)
SECTION_RE = re.compile(
    r"^(#{1,6}\s+.+|\\section\{.+\}|\\subsection\{.+\}|\\subsubsection\{.+\}|"
    r"\d+(?:\.\d+)*[\.\s、）)]\s*.+)$", re.M)


def _env(name, default):
    return os.environ.get(name) or default


def _read_text(path, limit=MAX_FILE):
    try:
        if os.path.getsize(path) > limit:
            return None, "too_large"
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(), "ok"
    except OSError as e:
        return None, "unreadable: %s" % e


def _pdf_to_text(path):
    """Try to extract PDF text; never raise."""
    for tool in ("pdftotext",):
        if shutil.which(tool):
            try:
                r = subprocess.run([tool, path, "-"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout, "ok_pdftotext"
                return r.stdout, "pdftotext_empty"
            except Exception as e:  # noqa: BLE001
                return None, "pdftotext_error: %s" % e
    return None, "no_pdftotext"


def _classify(name):
    ext = os.path.splitext(name)[1].lower()
    if ext == PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    if ext in OFFICE_EXTS:
        return "office"
    return "other"


def _extract_candidates(text, kind, rel):
    out = {"path": rel, "kind": kind, "char_count": len(text or ""),
           "sections": [], "numbers": [], "stats": [], "note": ""}
    if not text:
        return out
    lines = text.splitlines()
    out["line_count"] = len(lines)
    for i, line in enumerate(lines, 1):
        if SECTION_RE.match(line.strip()):
            out["sections"].append({"line": i, "text": line.strip()[:200]})
    for m in NUMBER_RE.finditer(text):
        ctx = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
        out["numbers"].append({"value": m.group(0), "context": ctx})
        if len(out["numbers"]) >= 80:
            break
    for m in STAT_RE.finditer(text):
        out["stats"].append({"stat": m.group(0), "line": text.count("\n", 0, m.start()) + 1})
        if len(out["stats"]) >= 80:
            break
    return out


def main():
    input_dir = _env("INPUT_DIR", DEFAULTS["input_dir"])
    work_dir = _env("WORK_DIR", DEFAULTS["work_dir"])
    evidence_json = _env("EVIDENCE_JSON", DEFAULTS["evidence_json"])

    try:
        os.makedirs(work_dir, exist_ok=True)
    except OSError as e:
        print(json.dumps({"ok": False, "error": "cannot create work dir: %s" % e}, ensure_ascii=False))
        return 1

    inventory = []
    degradations = []
    if not os.path.isdir(input_dir):
        degradations.append("input_dir missing: %s" % input_dir)
    else:
        for root, _dirs, files in os.walk(input_dir):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, input_dir)
                kind = _classify(fn)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                item = {"path": rel, "kind": kind, "size": size, "readable": False, "note": ""}
                if kind == "text":
                    text, err = _read_text(full)
                    item["readable"] = err == "ok"
                    item["note"] = err
                    item["candidates"] = _extract_candidates(text, kind, rel)
                elif kind == "pdf":
                    text, err = _pdf_to_text(full)
                    item["readable"] = bool(text and err.startswith("ok"))
                    item["note"] = err
                    item["candidates"] = _extract_candidates(text, kind, rel) if text else None
                elif kind == "image":
                    item["note"] = "needs render/OCR"
                elif kind == "office":
                    item["note"] = "needs office renderer (docx/pptx/xlsx)"
                else:
                    item["note"] = "unsupported"
                if size > MAX_FILE:
                    degradations.append("skipped oversized file: %s (%d bytes)" % (rel, size))
                    item["note"] = "too_large"
                inventory.append(item)

    result = {
        "input_dir": input_dir,
        "inventory": inventory,
        "degradations": degradations,
        "summary": {"total": len(inventory),
                    "readable": sum(1 for i in inventory if i.get("readable")),
                    "by_kind": {}},
    }
    for i in inventory:
        result["summary"]["by_kind"][i["kind"]] = result["summary"]["by_kind"].get(i["kind"], 0) + 1

    try:
        with open(evidence_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True, "evidence_json": evidence_json,
        "summary": result["summary"], "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
