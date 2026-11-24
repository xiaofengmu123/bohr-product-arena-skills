# -*- coding: utf-8 -*-
"""Evidence utilities for the mind-map writing skill.

Standard library only. Responsibilities:
  * encoding-safe text reading (BOM / bad encoding tolerant)
  * material discovery from the input directory
  * verbatim quote checking (locator + quote -> verified status)
  * source-conflict detection across an evidence map

No scientific content is invented here; every function only transforms what
the caller (the harness agent) already extracted from real material.
"""

import json
import os
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per text file

# File extensions that can be read as UTF-8 text with the standard library.
TEXT_EXTS = {".md", ".txt", ".tex", ".bib", ".csv", ".tsv", ".json", ".yaml", ".yml"}

# Files that are input contract rather than scholarly material.
CONTRACT_NAMES = {"case.json", "task.md"}


def norm_ws(s):
    """Normalize whitespace so quote comparison is robust to line breaks."""
    if s is None:
        return None
    return " ".join(str(s).split())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Return (text, meta). meta records encoding / truncation / error.

    Never raises on encoding problems: falls back to latin-1 with replacement.
    """
    meta = {"path": path, "encoding": None, "size": 0, "truncated": False, "error": None}
    try:
        meta["size"] = os.path.getsize(path)
    except OSError as e:
        meta["error"] = "getsize: %s" % e
        return None, meta

    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes)
        truncated = meta["size"] > max_bytes
        meta["truncated"] = truncated
    except OSError as e:
        meta["error"] = "read: %s" % e
        return None, meta

    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            meta["encoding"] = enc
            return text, meta
        except UnicodeDecodeError:
            continue
    # Last resort: latin-1 always succeeds byte-for-byte.
    text = raw.decode("latin-1", errors="replace")
    meta["encoding"] = "latin-1(replace)"
    return text, meta


def discover_inputs(input_dir):
    """Walk input_dir and return a deterministic list of file records.

    Returned order: contract files first, then materials sorted by rel path.
    Each record: {path, rel, size, kind} where kind in
    {case, task, material, text, unsupported}.
    """
    records = []
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            if name == "case.json":
                kind = "case"
            elif name == "task.md":
                kind = "task"
            elif ext in TEXT_EXTS:
                kind = "text"
            elif ext in {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".svg", ".gif"}:
                kind = "unsupported"  # not readable as stdlib text
            else:
                kind = "material"
            records.append({"path": full, "rel": rel, "size": size, "kind": kind})
    # Stable ordering: contract files first, then the rest by rel path.
    order = {"case": 0, "task": 1, "material": 2, "text": 3, "unsupported": 4}
    records.sort(key=lambda r: (order.get(r["kind"], 9), r["rel"]))
    return records


def load_json_safe(path):
    """Load JSON with BOM / bad encoding tolerance. Returns (obj, error)."""
    text, meta = read_text(path)
    if text is None:
        return None, meta.get("error") or "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        # Try stripping a UTF-8 BOM manually (read_text already did via utf-8-sig,
        # but a BOM with invalid utf-8 would have fallen through to latin-1).
        if text.startswith("\ufeff"):
            try:
                return json.loads(text[1:]), None
            except ValueError as e2:
                return None, "bad json: %s" % e2
        return None, "bad json: %s" % e


def check_quote(text, quote):
    """Classify whether quote appears in text.

    Returns one of: verbatim | partial | not_found | empty | no_text.
    """
    quote = norm_ws(quote)
    if quote is None or quote == "":
        return "empty"
    if text is None:
        return "no_text"
    flat = norm_ws(text)
    if quote in flat:
        return "verbatim"
    head = quote[:40]
    if len(head) >= 8 and head in flat:
        return "partial"
    return "not_found"


def verify_evidence(entries, text_index):
    """Add a 'verified' field to each evidence entry using text_index.

    text_index: dict {rel_path: text}. Entries unchanged when no source text
    is available (verified = 'no_text'). Mutates and returns entries.
    """
    for e in entries:
        src = e.get("source") or ""
        quote = e.get("quote")
        text = text_index.get(src)
        e["verified"] = check_quote(text, quote)
    return entries


def detect_conflicts(entries):
    """Flag multiple variants for the same claim as source_conflict.

    A claim is identified by (claim) or fallback (node_id). Only conflicts
    with at least two distinct (source, quote) variants are reported.
    """
    groups = {}
    for e in entries:
        key = e.get("claim") or e.get("node_id")
        if not key:
            continue
        groups.setdefault(key, []).append(e)
    conflicts = []
    for key, items in sorted(groups.items()):
        variants = set()
        for i in items:
            variants.add((i.get("source") or "", norm_ws(i.get("quote")) or ""))
        variants.discard(("", ""))
        if len(variants) > 1:
            conflicts.append({
                "claim": key,
                "kind": "source_conflict",
                "variants": [{"source": s, "quote": q[:120]} for s, q in sorted(variants)],
            })
    return conflicts


if __name__ == "__main__":
    # Small self-test only when executed directly; never part of the skill flow.
    demo = [{"claim": "X", "source": "a.md", "quote": "one"},
            {"claim": "X", "source": "b.md", "quote": "two"}]
    print(json.dumps(detect_conflicts(demo), ensure_ascii=False))
    sys.exit(0)
