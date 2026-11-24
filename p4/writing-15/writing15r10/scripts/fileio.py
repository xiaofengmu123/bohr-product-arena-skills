# -*- coding: utf-8 -*-
"""Robust input discovery and output writing for the long-document writing skill.

Stdlib only. Every function degrades gracefully: bad encodings, bad JSON,
oversized files, and missing directories never raise; they return a record
describing what happened so the caller can report it honestly.
"""
import json
import os
import re
import shutil
import subprocess
import time

# Per-file size cap (bytes). Larger files are recorded but skipped for parsing.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Encoding candidates, most specific first. gb18030 covers Chinese Windows
# files; latin-1 always succeeds and is the last resort.
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

MATERIAL_EXT = {".md", ".txt", ".tex", ".rst", ".csv", ".tsv", ".json"}
BINARY_EXT = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".odt", ".html"}


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Return {"ok": bool, "text": str|None, "error": str|None, "bytes": int,
    "encoding": str|None, "skipped": bool}. Never raises."""
    rec = {"ok": False, "text": None, "error": None, "bytes": 0,
           "encoding": None, "skipped": False, "path": path}
    try:
        if not os.path.isfile(path):
            rec["error"] = "not_a_file"
            return rec
        size = os.path.getsize(path)
        rec["bytes"] = size
        if size > max_bytes:
            rec["skipped"] = True
            rec["error"] = "oversize(%d>%d)" % (size, max_bytes)
            return rec
        raw = None
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ENCODINGS:
            try:
                text = raw.decode(enc)
                rec["ok"] = True
                rec["text"] = text
                rec["encoding"] = enc
                return rec
            except (UnicodeDecodeError, ValueError):
                continue
        rec["error"] = "undecodable"
        return rec
    except OSError as exc:  # pragma: no cover - environment dependent
        rec["error"] = "io_error:%s" % exc
        return rec


def read_json(path):
    """Tolerant JSON read. Returns (data, error). On a top-level decode error,
    tries to extract the first balanced {...} block from the raw text."""
    rec = read_text(path)
    if not rec["ok"] or rec["text"] is None:
        return None, rec["error"] or "unreadable"
    text = rec["text"]
    try:
        return json.loads(text), None
    except ValueError:
        pass
    # Best-effort: locate the first balanced object.
    start = text.find("{")
    if start < 0:
        return None, "invalid_json_no_object"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1]), None
                except ValueError:
                    return None, "invalid_json_unbalanced"
    return None, "invalid_json_unbalanced"


def write_json(path, data):
    """Write JSON to path atomically (temp file + os.replace). Returns (ok, err)."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return True, None
    except OSError as exc:
        return False, "write_error:%s" % exc


def write_text(path, text):
    """Write text file, creating parents. Returns (ok, err)."""
    path = os.path.abspath(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return True, None
    except OSError as exc:
        return False, "write_error:%s" % exc


def discover_inputs(input_dir):
    """Walk an input directory and classify files. Returns a dict:
    {"case_json": path|None, "task_md": path|None, "workorder": path|None,
     "materials": [paths], "binary": [paths], "other": [paths], "files": [paths]}.
    Never raises; a missing directory yields an empty classification."""
    out = {"case_json": None, "task_md": None, "workorder": None,
           "materials": [], "binary": [], "other": [], "files": []}
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            out["files"].append(full)
            base = os.path.basename(full).lower()
            ext = os.path.splitext(base)[1].lower()
            if base == "case.json":
                out["case_json"] = full
            elif base == "task.md":
                out["task_md"] = full
            elif "workorder" in base and ext == ".json":
                out["workorder"] = full
            elif ext in BINARY_EXT:
                out["binary"].append(full)
            elif ext in MATERIAL_EXT:
                out["materials"].append(full)
            else:
                out["other"].append(full)
    return out


def extract_pdf_text(path, timeout=20):
    """Best-effort PDF text extraction via the preinstalled pdftotext tool.
    Returns (text, error). The harness also provides a dedicated pdf-text-tool;
    this is a convenience fallback and never crashes."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", errors="replace"), None
        return None, "pdftotext_exit_%d" % proc.returncode
    except FileNotFoundError:
        return None, "pdftotext_missing"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, "pdftotext_error:%s" % exc


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def parse_paragraphs(text):
    """Split text into paragraphs (blank-line separated, with line continuations
    joined). Returns list of paragraph strings. Deterministic."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for b in blocks:
        joined = " ".join(b.split())
        if joined:
            out.append(joined)
    return out


def safe_copy(src, dst):
    """Copy a file into the work area. Returns (ok, err)."""
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True, None
    except OSError as exc:
        return False, "copy_error:%s" % exc
