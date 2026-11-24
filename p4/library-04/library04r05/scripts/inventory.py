#!/usr/bin/env python3
"""Stage-1 inventory: six-dimension catalogue, content hashing, duplicate groups,
version families, and a per-file evidence index.

Usage:
  python3 scripts/inventory.py --input /input --work /app/work
Writes <work>/inventory.json. Standard library only.

Determinism: all walks and lists are sorted; JSON is emitted with stable order.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

MAX_CONTENT = 2_000_000  # per-file cap for text extraction; hashing always streams
PREVIEW_CHARS = 240

OFFICE_EXT = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".ods", ".odp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".rar5"}
TEXT_EXT = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".tex", ".bib", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".py", ".log", ".ini",
    ".cfg", ".toml", ".ipynb",
}

_VERSION_TOKEN = re.compile(
    r"(?i)(?:^|[-_.\s])(v\d+(?:\.\d+)*[a-z0-9]*"
    r"|version[-_.]?\d+(?:\.\d+)*[a-z0-9]*|rev[-_.]?\d+(?:\.\d+)*[a-z0-9]*"
    r"|final|draft|beta|alpha|rc\d+|\d{8}|\d{4}[-_]\d{2}[-_]\d{2})(?:$|[-_.\s])"
)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
    return path


# ---------------------------------------------------------------- hashing / io
def sha256_of(path):
    """Streaming SHA-256; returns hex digest or None on failure."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 256), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_header(path, n=8):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def read_text_robust(path, max_bytes=MAX_CONTENT):
    """Return (text, encoding) or (None, reason). Never raises."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return None, "stat_failed:%s" % exc
    if st.st_size > max_bytes:
        return None, "oversized_%d" % st.st_size
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return None, "read_failed:%s" % exc
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1-fallback"


def looks_textual(head):
    if not head:
        return False
    printable = sum(1 for b in head if (9 <= b <= 13) or (32 <= b <= 126))
    return printable / len(head) >= 0.85


def sniff_category(path, head):
    ext = os.path.splitext(path)[1].lower()
    if head[:4] == b"%PDF" or ext == ".pdf":
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        return "office" if ext in OFFICE_EXT else "archive"
    if head[:2] == b"\x1f\x8b":
        return "archive"
    if (
        head[:8] == b"\x89PNG\r\n\x1a\n"
        or head[:3] == b"\xff\xd8\xff"
        or head[:6] in (b"GIF87a", b"GIF89a")
        or ext in IMAGE_EXT
    ):
        return "image"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext == ".json" or head.lstrip()[:1] in (b"{", b"["):
        return "json"
    if ext in {".csv", ".tsv"}:
        return "csv"
    if ext in TEXT_EXT or ext == "" or looks_textual(head):
        return "text" if (ext in TEXT_EXT or looks_textual(head)) else "binary"
    return "unknown"


# ------------------------------------------------------------------ metadata
def parse_frontmatter(text):
    """Parse a minimal YAML frontmatter block (--- ... ---) without a YAML lib."""
    if not text.startswith("---"):
        return {}
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip().strip("'\"")
        if val.startswith("[") and val.endswith("]"):
            val = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        out[key] = val
    return out


def extract_metadata(text, rel, size, mtime_iso):
    meta = {"size_bytes": size, "mtime_iso": mtime_iso}
    if text is None:
        return meta
    ext = os.path.splitext(rel)[1].lower()
    if ext in (".md", ".markdown", ".rst"):
        fm = parse_frontmatter(text)
        for k in ("title", "author", "date", "tags", "version"):
            if k in fm:
                meta[k] = fm[k]
    elif ext == ".json":
        try:
            obj = json.loads(text)
            meta["top_level_keys"] = sorted(obj.keys()) if isinstance(obj, dict) else ["<list>"]
        except Exception:
            meta["top_level_keys"] = ["<unparsable>"]
    return meta


# ------------------------------------------------------------------- versions
def version_family_of(rel):
    """Return (base, [tokens]) for grouping files by version-agnostic stem."""
    stem = os.path.splitext(os.path.basename(rel))[0]
    tokens = []
    base = stem
    for m in _VERSION_TOKEN.finditer(stem):
        tok = m.group(1).strip()
        tokens.append(tok)
        start, end = m.span(1)
        base = base[:start] + base[end:]
    base = re.sub(r"[-_.\s]+", "_", base).strip("_").lower()
    tokens = sorted(set(t for t in tokens if t))
    return base, tokens


# ---------------------------------------------------------------------- walk
def walk_files(root):
    files = []
    dirs_meta = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", ".svn", "__pycache__"))
        try:
            dst = os.stat(dirpath)
            dirs_meta.append({
                "path": os.path.relpath(dirpath, root).replace(os.sep, "/"),
                "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(dst.st_mtime)),
                "mode_octal": oct(dst.st_mode & 0o777),
            })
        except OSError:
            pass
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                files.append(("symlink", full))
            elif os.path.isfile(full):
                files.append(("file", full))
    rel_files = [
        (os.path.relpath(full, root).replace(os.sep, "/"), full)
        for kind, full in files if kind == "file"
    ]
    symlinks = [
        (os.path.relpath(full, root).replace(os.sep, "/"), full)
        for kind, full in files if kind == "symlink"
    ]
    return sorted(rel_files), sorted(symlinks), sorted(dirs_meta, key=lambda d: d["path"])


def build_tree(rel_files):
    root = {"name": ".", "dirs": {}, "files": []}
    for rel, _ in rel_files:
        parts = rel.split("/")
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"name": part, "dirs": {}, "files": []})
        node["files"].append(parts[-1])
    for node in _iter_nodes(root):
        node["file_count"] = len(node["files"])
    return root


def _iter_nodes(node):
    yield node
    for child in node.get("dirs", {}).values():
        yield from _iter_nodes(child)


# --------------------------------------------------------------------- run
def run(input_dir="/input", work_dir="/app/work"):
    input_dir = os.path.abspath(input_dir)
    if not os.path.isdir(input_dir):
        result = {
            "schema": "inventory.v1",
            "generated_at": now_iso(),
            "input_root": input_dir,
            "summary": {"total_files": 0, "error": "input_dir_missing"},
            "files": [],
            "directory_tree": {"name": ".", "dirs": {}, "files": []},
            "directories": [],
            "duplicate_groups": {},
            "version_families": {},
            "empty_files": [],
            "failures_recovery": [{"step": "inventory", "action": "skip_input", "status": "missing", "detail": "input dir not found"}],
            "skipped": [],
        }
        os.makedirs(work_dir, exist_ok=True)
        write_json(os.path.join(work_dir, "inventory.json"), result)
        return result

    rel_files, symlinks, dirs_meta = walk_files(input_dir)
    files = []
    failures = []
    skipped = []
    by_hash = {}
    empty_files = []

    for rel, full in rel_files:
        try:
            st = os.stat(full)
        except OSError as exc:
            failures.append({"step": "inventory", "item": rel, "action": "stat_failed", "status": "skip", "detail": str(exc)})
            continue
        digest = sha256_of(full)
        if digest is None:
            failures.append({"step": "hash", "item": rel, "action": "read_failed", "status": "skip", "detail": "cannot hash"})
            continue
        if st.st_size == 0:
            empty_files.append({"path": rel, "sha256": digest, "note": "empty file"})
            continue
        head = read_header(full)
        category = sniff_category(rel, head)
        mode = st.st_mode
        mtime_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
        text = preview = None
        encoding = None
        unreadable_reason = None
        if category in ("text", "json", "csv", "unknown"):
            text, enc = read_text_robust(full)
            if text is None:
                unreadable_reason = enc  # enc is a reason string when text is None
            else:
                encoding = enc
                preview = text[:PREVIEW_CHARS]
        status = "readable" if unreadable_reason is None else "unreadable"
        entry = {
            "path": rel,
            "size": st.st_size,
            "sha256": digest,
            "mtime_iso": mtime_iso,
            "mtime_epoch": st.st_mtime,
            "type": category,
            "extension": os.path.splitext(rel)[1].lower(),
            "text_encoding": encoding,
            "preview": preview,
            "permissions": {
                "mode_octal": oct(mode & 0o777),
                "readable": os.access(full, os.R_OK),
                "writable": os.access(full, os.W_OK),
                "uid": getattr(st, "st_uid", None),
                "gid": getattr(st, "st_gid", None),
            },
            "source": "task_input",
            "status": status,
            "metadata": extract_metadata(text, rel, st.st_size, mtime_iso),
        }
        entry["metadata"]["encoding"] = encoding
        if unreadable_reason is not None:
            entry["unreadable_reason"] = unreadable_reason
        files.append(entry)
        by_hash.setdefault(digest, []).append(rel)

    file_types = {}
    extensions = {}
    for f in files:
        file_types[f["type"]] = file_types.get(f["type"], 0) + 1
        extensions[f["extension"]] = extensions.get(f["extension"], 0) + 1

    duplicate_groups = {k: sorted(v) for k, v in by_hash.items() if len(v) > 1}

    family = {}
    for f in files:
        base, tokens = version_family_of(f["path"])
        if base:
            family.setdefault(base, []).append({
                "path": f["path"],
                "sha256": f["sha256"],
                "version_tokens": tokens,
                "mtime_iso": f["mtime_iso"],
            })
    version_families = {
        k: sorted(v, key=lambda x: x["path"])
        for k, v in family.items() if len(v) > 1
    }

    unreadable = [f["path"] for f in files if f["status"] == "unreadable"]
    text_index = {
        f["path"]: {"encoding": f["text_encoding"], "type": f["type"], "has_text": f["preview"] is not None}
        for f in files
    }

    summary = {
        "file_types": file_types,
        "extensions": extensions,
        "total_files": len(files) + len(empty_files),
        "total_bytes": sum(f["size"] for f in files),
        "readable_files": len([f for f in files if f["status"] == "readable"]),
        "unreadable_files": unreadable,
        "duplicate_groups": len(duplicate_groups),
        "version_families": len(version_families),
    }
    summary["six_dimensions"] = {
        "file_types": "covered" if file_types else "not_applicable",
        "metadata": "covered" if any(f["metadata"].get("title") or f["metadata"].get("top_level_keys") for f in files) else "partial",
        "directory_tree": "covered" if dirs_meta else "not_applicable",
        "duplicates": "covered" if duplicate_groups else "none_found",
        "versions": "covered" if version_families else "none_found",
        "permissions_and_source": "covered" if files else "not_applicable",
    }

    result = {
        "schema": "inventory.v1",
        "generated_at": now_iso(),
        "input_root": input_dir,
        "summary": summary,
        "files": files,
        "directory_tree": build_tree(rel_files),
        "directories": dirs_meta,
        "duplicate_groups": duplicate_groups,
        "version_families": version_families,
        "empty_files": empty_files,
        "text_index": text_index,
        "failures_recovery": failures,
        "skipped": [{"path": p, "reason": "symlink_not_followed"} for p, _ in symlinks],
    }
    os.makedirs(work_dir, exist_ok=True)
    write_json(os.path.join(work_dir, "inventory.json"), result)
    return result


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="inventory.py", description="Six-dimension file inventory.")
    p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    return p.parse_args(argv)


if __name__ == "__main__":
    a = parse_args()
    run(input_dir=a.input, work_dir=a.work)
    print("inventory written to", os.path.join(a.work, "inventory.json"))
    sys.exit(0)