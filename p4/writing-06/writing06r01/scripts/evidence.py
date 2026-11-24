#!/usr/bin/env python3
"""Inventory input materials and extract candidate anchors -> evidence.json.

Reads actual file contents only; never infers content from file names.
Output: {case_id, materials, sections, anchors, warnings}.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C


def classify(rel_path):
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in C.BINARY_EXTS:
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff"):
            return "image"
        if ext == ".pdf":
            return "pdf"
        if ext in (".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"):
            return "office"
        if ext in (".ipynb", ".zip", ".gz"):
            return "archive"
        return "binary"
    if ext in C.TEXT_EXTS or ext == "":
        return "text"
    return "unknown"


def extract_lines(rel_path, apath):
    """Return lines with 1-based numbers for a text file."""
    text = C.read_text(apath)
    if not text or text.startswith("[skipped"):
        return [], text
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines, 1):
        out.append((i, line))
    return out, text


def section_of(lines, idx):
    """Return the nearest preceding section heading (or None)."""
    for j in range(idx - 1, -1, -1):
        line = lines[j][1].strip()
        if not line:
            continue
        if C.SECTION_RE.match(line):
            return line[:120]
    return None


def main():
    ap = argparse.ArgumentParser(description="Inventory materials and anchors.")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    paths = C.resolve_paths(args.case, None, args.input, args.work, None, None)
    C.ensure_dirs(paths)

    case_data = C.read_json(paths["case"])
    case_id = C.case_id_from(case_data)

    materials = []
    sections = []
    anchors = []
    warnings = []

    for rel_path, apath in C.iter_files(paths["input"]):
        kind = classify(rel_path)
        entry = {"file": rel_path.replace("\\", "/"), "kind": kind,
                 "size": 0, "readable": kind in ("text", "json")}
        try:
            entry["size"] = os.path.getsize(apath)
        except OSError:
            pass
        if kind in ("pdf", "image", "office", "binary", "archive"):
            entry["readable"] = False
            entry["note"] = ("needs render/OCR/tool" if kind in ("pdf", "image")
                             else "binary/office artifact")
        materials.append(entry)

        if kind == "text":
            lines, text = extract_lines(rel_path, apath)
            if not lines:
                warnings.append({"file": rel_path.replace("\\", "/"),
                                 "reason": "empty or unreadable"})
                continue
            n_lines = len(lines)
            if n_lines > 4000:
                warnings.append({"file": rel_path.replace("\\", "/"),
                                 "reason": "very long text, sample anchors"})
            for i, line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if C.SECTION_RE.match(stripped):
                    sections.append({
                        "file": rel_path.replace("\\", "/"),
                        "line": i,
                        "section": stripped[:120],
                    })
                for m in C.NUM_RE.finditer(stripped):
                    anchors.append({
                        "file": rel_path.replace("\\", "/"),
                        "line": i,
                        "value": m.group(0),
                        "context": stripped[:300],
                        "locator": C.normalize_locator(
                            rel_path, i, section_of(lines, i)),
                    })
                    break  # one anchor per line is enough for candidates
                if len(anchors) > 800:
                    break

    if not materials:
        warnings.append({"reason": "no input files found"})

    report = {
        "case_id": case_id,
        "generated_at": C.utcnow(),
        "materials": materials,
        "sections": sections[:600],
        "anchors": anchors[:800],
        "warnings": warnings,
        "counts": {
            "materials": len(materials),
            "sections": len(sections),
            "anchors": len(anchors),
        },
    }

    out_path = os.path.join(paths["work"], "evidence.json")
    C.write_json(out_path, report)
    print(C.utcnow(), "evidence ok ->", out_path,
          json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
