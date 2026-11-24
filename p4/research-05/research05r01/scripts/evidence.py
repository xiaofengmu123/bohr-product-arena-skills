#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 — discover materials, extract PDF text, build locator indexes.

Produces:
    /app/work/material_inventory.json   — one record per source file
    /app/work/evidence_index.jsonl      — grep-able (source_id, locator, text)
    /app/work/text/<source_id>.txt      — normalized text copy of each source
    refreshed sources/errors/degradations/status inside result.json

Evidence model: every conclusion must later point at a locator + verbatim quote.
This script gives the agent the *actual text* and canonical locators it needs to
find quotes without guessing.  PDFs are converted with `pdftotext` (platform
preinstalled); if it is absent the source is marked `text_unavailable` and the
skill keeps processing the remaining readable materials.

Pure Python standard library.  Run:

    python3 -B scripts/evidence.py --input /input --work /app/work --output /app/output
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
             ".yaml", ".yml", ".rst", ".tex", ".bib", ".log", ".nq", ".ttl",
             ".jsonl"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
               ".odt", ".ods", ".odp", ".rtf"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per file

META_NAMES = {"case.json": "meta_case", "task.md": "task_md"}


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text_tolerant(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, False, "unreadable: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), True, None
        except UnicodeDecodeError:
            continue
    return None, False, "undecodable (not utf-8/latin-1)"


def load_json_tolerant(path):
    text, ok, err = read_text_tolerant(path)
    if not ok:
        return None, "unreadable", err
    try:
        return json.loads(text), "ok", None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "bad_json", str(exc)


def norm_rel(path, input_dir):
    """Relative path with forward slashes, used as the canonical locator base."""
    try:
        rel = os.path.relpath(path, input_dir)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def discover_files(input_dir):
    """Recursively list regular files under input_dir (rel paths)."""
    out = []
    for root, dirs, files in os.walk(input_dir):
        # Skip hidden/version-control noise.
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and d not in ("node_modules",)]
        for name in files:
            if name.startswith("."):
                continue
            full = os.path.join(root, name)
            if os.path.isfile(full):
                out.append(full)
    return sorted(out)


def classify(rel, ext):
    """Return (kind, is_meta) for a file."""
    base = os.path.basename(rel).lower()
    if base in META_NAMES:
        return "meta", True
    if ext in PDF_EXTS:
        return "pdf", False
    if ext in TEXT_EXTS:
        return "text", False
    if ext in OFFICE_EXTS:
        return "office", False
    return "other", False


def extract_pdf(pdf_path, txt_path):
    """Run pdftotext; returns (ok, note)."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return False, "pdftotext not on PATH"
    try:
        proc = subprocess.run(
            [pdftotext, "-enc", "UTF-8", pdf_path, txt_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, "pdftotext failed: %s" % exc
    if proc.returncode != 0:
        return False, "pdftotext rc=%s: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
    return os.path.isfile(txt_path) and os.path.getsize(txt_path) > 0, "ok"


def index_text_lines(text, source_id, locator_base, kind):
    """Yield (locator, text) per non-empty line for line-anchored sources."""
    for idx, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        loc = "%s#L%d" % (locator_base, idx)
        yield {"source_id": source_id, "locator": loc,
               "text": stripped[:2000]}


def index_pdf_pages(text, source_id, locator_base):
    """Yield (locator, text) per page; pdftotext separates pages with \\f."""
    pages = text.split("\f")
    for idx, page in enumerate(pages, 1):
        clean = " ".join(page.split())
        if not clean:
            continue
        loc = "%s#p%d" % (locator_base, idx)
        yield {"source_id": source_id, "locator": loc,
               "text": clean[:2000]}


def write_result_update(result, output_dir):
    result_path = os.path.join(output_dir, "result.json")
    try:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2,
                      allow_nan=False)
    except OSError as exc:
        print("EVIDENCE: could not update result.json: %s" % exc,
              file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Discover materials, extract PDF text, build evidence index.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)

    input_dir = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    work_dir = os.path.abspath(args.work)
    text_dir = os.path.join(work_dir, "text")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(text_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Load current result.json (skeleton); if missing, create a minimal one.
    result_path = os.path.join(output_dir, "result.json")
    result, rstate, rerr = load_json_tolerant(result_path)
    if rstate != "ok" or not isinstance(result, dict):
        result = {"status": "partial", "sources": [], "errors": [],
                  "degradations": [], "change_log": [], "runtime": {},
                  "pending_human": [], "uncertainty": {"gaps": []}}

    errors = list(result.get("errors") or [])
    degradations = list(result.get("degradations") or [])
    sources = []
    inventory = {"generated_at": utcnow(), "input_dir": input_dir,
                 "materials": []}
    index_entries = []
    next_sid = 1

    # case.json may reference material files explicitly; collect those paths.
    case_json = os.path.join(input_dir, "case.json")
    case_refs = []
    if os.path.isfile(case_json):
        cdata, cstate, _ = load_json_tolerant(case_json)
        if cstate == "ok" and isinstance(cdata, dict):
            for key in ("materials", "sources", "files", "documents"):
                val = cdata.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            case_refs.append(item)
                        elif isinstance(item, dict):
                            for pkey in ("path", "file", "source", "url"):
                                if isinstance(item.get(pkey), str):
                                    case_refs.append(item[pkey])

    # Merge: explicit case refs first, then full-directory discovery.
    discovered = discover_files(input_dir)
    ordered = []
    for ref in case_refs:
        cand = os.path.join(input_dir, ref.replace("/", os.sep))
        if os.path.isfile(cand) and cand not in ordered:
            ordered.append(cand)
    for full in discovered:
        if full not in ordered:
            ordered.append(full)

    readable_count = 0
    for full in ordered:
        rel = norm_rel(full, input_dir)
        ext = os.path.splitext(full)[1].lower()
        kind, is_meta = classify(rel, ext)
        size = os.path.getsize(full)

        source_id = "s%d" % next_sid
        next_sid += 1
        rec = {
            "source_id": source_id, "path": rel, "abs_path": full,
            "kind": kind, "size_bytes": size, "status": "unknown",
            "extracted_text": None, "note": None,
        }

        if size > MAX_FILE_BYTES:
            rec["status"] = "skipped_oversize"
            rec["note"] = "size %d > %d bytes" % (size, MAX_FILE_BYTES)
            degradations.append("skipped oversize file: %s" % rel)
        elif kind == "pdf":
            txt_path = os.path.join(text_dir, source_id + ".txt")
            ok, note = extract_pdf(full, txt_path)
            if ok:
                text, tok, terr = read_text_tolerant(txt_path)
                if tok:
                    rec["status"] = "text_extracted"
                    rec["extracted_text"] = "text/%s.txt" % source_id
                    rec["note"] = note
                    readable_count += 1
                    for ent in index_pdf_pages(text, source_id, rel):
                        index_entries.append(ent)
                else:
                    rec["status"] = "text_unavailable"
                    rec["note"] = "extracted but unreadable: %s" % terr
            else:
                rec["status"] = "text_unavailable"
                rec["note"] = note
                degradations.append("PDF text unavailable (%s): %s" % (rel, note))
        elif kind == "text":
            text, tok, terr = read_text_tolerant(full)
            if tok:
                rec["status"] = "readable"
                try:
                    with open(os.path.join(text_dir, source_id + ".txt"),
                              "w", encoding="utf-8") as fh:
                        fh.write(text)
                    rec["extracted_text"] = "text/%s.txt" % source_id
                except OSError as exc:
                    degradations.append("could not copy text %s: %s" % (rel, exc))
                readable_count += 1
                for ent in index_text_lines(text, source_id, rel, kind):
                    index_entries.append(ent)
            else:
                rec["status"] = "unreadable"
                rec["note"] = terr
                errors.append("unreadable text file %s: %s" % (rel, terr))
        elif kind == "office":
            rec["status"] = "text_unavailable"
            rec["note"] = ("binary office format; use office-renderer/ocr if "
                           "available, or treat as non-evidence source")
            degradations.append("office binary not parsed: %s" % rel)
        elif kind == "meta":
            text, tok, _terr = read_text_tolerant(full)
            if tok:
                rec["status"] = "readable"
                try:
                    with open(os.path.join(text_dir, source_id + ".txt"),
                              "w", encoding="utf-8") as fh:
                        fh.write(text)
                    rec["extracted_text"] = "text/%s.txt" % source_id
                except OSError:
                    pass
                if rel.endswith("task.md"):
                    for ent in index_text_lines(text, source_id, rel, "text"):
                        index_entries.append(ent)
            else:
                rec["status"] = "unreadable"
                rec["note"] = "meta file unreadable"
        else:
            rec["status"] = "ignored_unknown_extension"
            rec["note"] = "extension %s not handled" % (ext or "(none)")

        sources.append({
            "source_id": source_id, "path": rel, "kind": kind,
            "status": rec["status"], "locator_base": rel,
        })
        inventory["materials"].append(rec)

    # ---- Write inventory + index ---------------------------------------
    with open(os.path.join(work_dir, "material_inventory.json"), "w",
              encoding="utf-8") as fh:
        json.dump(inventory, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(work_dir, "evidence_index.jsonl"), "w",
              encoding="utf-8") as fh:
        for ent in index_entries:
            fh.write(json.dumps(ent, ensure_ascii=False) + "\n")

    # ---- Refresh result.json --------------------------------------------
    result["sources"] = sources
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = dict(result.get("runtime") or {})
    result["runtime"]["phase1_at"] = utcnow()
    result["runtime"]["material_count"] = len(sources)
    result["runtime"]["readable_count"] = readable_count
    log = list(result.get("change_log") or [])
    log.append({"time": utcnow(), "phase": "phase1",
                "action": "materials_indexed",
                "detail": "%d sources, %d readable" % (len(sources),
                                                       readable_count)})
    result["change_log"] = log

    # Honest status downgrade when nothing readable remains.
    if readable_count == 0 and result.get("status") not in (
            "input_error", "insufficient_evidence"):
        result["status"] = "insufficient_evidence"
        u = result.setdefault("uncertainty", {"gaps": []})
        u.setdefault("gaps", []).append(
            "no readable evidence material found under %s" % input_dir)

    write_result_update(result, output_dir)
    print("EVIDENCE: indexed %d sources (%d readable); status=%s"
          % (len(sources), readable_count, result.get("status")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
