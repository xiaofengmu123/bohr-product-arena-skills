#!/usr/bin/env python3
"""Normalize /input into a deterministic input_summary.json.

Reads case.json and task.md, discovers and fingerprints every material file,
extracts the output_contract and known constraint keys, and records
degradations without crashing on missing files, bad encodings, or bad JSON.

Determinism contract: for identical /input directory contents, this script
emits byte-identical JSON. It sorts all directory listings, sorts JSON keys,
and fingerprints file content with sha256; it never writes wall-clock time
into the summary.

Standard library only. Exit code 0 even when inputs are incomplete; the
resulting summary records gaps under `degradations`/`errors`.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

MAX_TEXT_CHARS = 20000        # characters of extracted text kept per file
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per-file cap before content is skipped

CONSTRAINT_KEYS = (
    "audience", "style", "format", "genre", "length", "language",
    "target_venue", "requirements", "constraints", "output_contract",
)

TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".yaml", ".yml"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_text(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load_json(path: str):
    try:
        raw = read_text(path)
    except OSError as exc:
        return None, ["cannot read %s: %s" % (path, exc)]
    try:
        return json.loads(raw), []
    except ValueError as exc:
        return None, ["bad JSON in %s: %s" % (path, exc)]


def extract_pdf_text(path: str) -> str:
    """Best-effort pdftotext; returns '' when the tool is unavailable."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, timeout=120,
        )
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return ""


def list_files(root: str):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((rel, full))
    out.sort(key=lambda x: x[0])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Normalize /input into deterministic input_summary.json.")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(a.work, exist_ok=True)
    degradations = []
    errors = []

    summary = {
        "case_id": None,
        "run_fingerprint": None,
        "output_contract": {},
        "constraints": {},
        "inputs": [],
        "materials": [],
        "degradations": [],
        "errors": [],
    }

    # Collect every file under /input, deterministically ordered.
    if os.path.isdir(a.input_dir):
        entries = list_files(a.input_dir)
    else:
        entries = []
        degradations.append("input dir missing: %s" % a.input_dir)

    # Fingerprint the whole input set (path + content hash), sorted by relpath.
    hash_pairs = []
    input_meta = []
    for rel, full in entries:
        try:
            size = os.path.getsize(full)
        except OSError as exc:
            errors.append("cannot stat %s: %s" % (rel, exc))
            continue
        digest = None
        if size <= MAX_FILE_BYTES:
            try:
                with open(full, "rb") as fh:
                    digest = sha256_bytes(fh.read())
            except OSError as exc:
                errors.append("cannot hash %s: %s" % (rel, exc))
        else:
            degradations.append("file skipped (too large): %s (%d bytes)" % (rel, size))
        hash_pairs.append("%s %s" % (rel, digest or "-"))
        input_meta.append({"relpath": rel, "size": size, "sha256": digest})

    run_fingerprint = hashlib.sha256(
        "\n".join(hash_pairs).encode("utf-8")
    ).hexdigest()
    summary["run_fingerprint"] = run_fingerprint
    summary["inputs"] = input_meta

    # case.json: case_id + output_contract + known constraints.
    case_data, case_errs = load_json(a.case)
    errors.extend(case_errs)
    if isinstance(case_data, dict):
        summary["case_id"] = case_data.get("case_id")
        oc = case_data.get("output_contract")
        if isinstance(oc, (dict, list)):
            summary["output_contract"] = oc
        elif oc is not None:
            degradations.append(
                "output_contract has unexpected type %s; treated as empty"
                % type(oc).__name__)
        constraints = {}
        for key in CONSTRAINT_KEYS:
            if key in case_data and key != "output_contract":
                constraints[key] = case_data.get(key)
        summary["constraints"] = constraints

    # task.md is part of the fact baseline; record its extracted text.
    if os.path.isfile(a.task):
        try:
            task_text = read_text(a.task)[:MAX_TEXT_CHARS]
        except OSError as exc:
            task_text = None
            errors.append("cannot read task: %s" % exc)
        summary["task_text"] = task_text
    else:
        summary["task_text"] = None
        degradations.append("task file missing: %s" % a.task)

    # Materials: everything under /input except case.json and task.md.
    for rel, full in entries:
        if rel in ("case.json", "task.md"):
            continue
        base = os.path.basename(rel)
        lower = base.lower()
        ext = os.path.splitext(lower)[1]
        size = os.path.getsize(full) if os.path.isfile(full) else 0
        item = {
            "relpath": rel,
            "size": size,
            "sha256": dict((m["relpath"], m["sha256"])
                           for m in input_meta).get(rel),
            "kind": None,
            "text": None,
            "note": None,
        }
        if size > MAX_FILE_BYTES:
            item["kind"] = "other"
            item["note"] = "skipped: exceeds %d bytes" % MAX_FILE_BYTES
            summary["materials"].append(item)
            continue
        if ext in TEXT_SUFFIXES:
            item["kind"] = "text"
            item["text"] = read_text(full)[:MAX_TEXT_CHARS]
        elif ext == ".pdf":
            item["kind"] = "pdf"
            txt = extract_pdf_text(full)
            if txt:
                item["text"] = txt[:MAX_TEXT_CHARS]
                item["note"] = "pdftotext extracted locally"
            else:
                item["note"] = "pdftotext unavailable; use platform pdf-text-tool"
                degradations.append(
                    "PDF text not extracted here: %s" % rel)
        else:
            item["kind"] = "other"
            item["note"] = "binary/non-text; not parsed"
        summary["materials"].append(item)

    summary["degradations"] = degradations
    summary["errors"] = errors

    out_path = os.path.join(a.work, "input_summary.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    print(json.dumps({
        "ok": True,
        "summary": out_path,
        "case_id": summary["case_id"],
        "fingerprint": run_fingerprint,
        "materials": len(summary["materials"]),
        "degradations": len(degradations),
        "errors": len(errors),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
