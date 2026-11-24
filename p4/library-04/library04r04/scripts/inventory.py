"""inventory.py - scan /input, build six-dimension inventory + evidence index.

Pure standard library. Deterministic (sorted paths, fixed SHA256, no rng).
Records uncertainty/limitations seeds for files that are unreadable, oversized,
encoding-failed or lacking OCR text. Never fabricates file content or metadata.

The six dimensions:
  1. file types         (extension + magic sniffing)
  2. metadata fields    (frontmatter + size/mtime/encoding)
  3. directory layers   (nested tree + directory list with mtime)
  4. duplicate files    (SHA256 groups; empty files handled separately)
  5. version differences(v1/v2/final/draft/rev/date family grouping)
  6. permissions/source (octal mode, readable/writable, uid/gid, source label)

Usage:
    python3 scripts/inventory.py --input /input --work /app/work
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import subprocess

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB body parse limit
SNIPPET_CHARS = 1200

TEXT_EXT = {
    ".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".yaml",
    ".yml", ".xml", ".html", ".htm", ".tex", ".bib", ".rst", ".log",
    ".py", ".r", ".ipynb", ".org", ".toml", ".ini", ".cfg", ".sh",
}
IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"GIF87a", "image"),
    (b"GIF89a", "image"),
    (b"BM", "image"),
    (b"II*\x00", "image"),
    (b"MM\x00*", "image"),
]

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso():
    return time.strftime(UTC_FMT, time.gmtime())


def _read_bytes(path, limit=65536):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit)
    except OSError:
        return None


def sniff_kind(path, header):
    """Classify into text/pdf/office/image/archive/unknown using felt magic."""
    if header is None:
        return "unknown"
    if header[:4] == b"%PDF":
        return "pdf"
    if header[:2] == b"PK":
        return "office"  # docx/xlsx/pptx/zip - content sniff weak here
    if header[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "office"  # legacy OLE
    for magic, kind in IMAGE_MAGIC:
        if header[:len(magic)] == magic:
            return kind
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        return "text"
    # fallback: decode test
    try:
        header.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"
    return "unknown"


def _sniff_text_and_encoding(path, header):
    """Return (text_or_None, encoding_label, note). Note is a limitation string."""
    if header is None:
        return None, None, "unreadable: cannot open file"
    if len(header) < 3 and os.path.getsize(path) == 0:
        return "", "utf-8", None
    # UTF-16 / UTF-32 BOM paths
    if header[:2] in (b"\xff\xfe", b"\xfe\xff"):
        enc = "utf-16"
    elif header[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        enc = "utf-32"
    elif header[:3] == b"\xef\xbb\xbf":
        enc = "utf-8-sig"
    else:
        enc = "utf-8"
    full = header
    try:
        full = open(path, "rb").read()
    except OSError as exc:
        return None, None, "unreadable: %s" % exc
    try:
        return full.decode(enc), enc, None
    except (UnicodeDecodeError, LookupError):
        pass
    # latin-1 never fails on bytes, but mark as lossy fallback only for text-ext files
    if os.path.splitext(path)[1].lower() in TEXT_EXT:
        try:
            return full.decode("latin-1"), "latin-1(fallback)", "encoding_fallback"
        except Exception:
            return None, None, "encoding_failed"
    return None, None, None  # not a text target, skip text parsing


def _parse_frontmatter(text):
    """Extract markdown/YAML frontmatter key: value pairs (simple, no deps)."""
    if not text or not text.startswith("---"):
        return {}
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key:
            meta[key] = val
    return meta


def _classify_version(name):
    """Group filenames into version families by stripping version markers.

    Separators (._- and whitespace) are normalized to spaces first so that
    "report_v1.txt" and "report v1.txt" fall into the same family base
    "report". Return (base, markers)."""
    stem = os.path.splitext(name)[0].lower()
    norm = re.sub(r"[_\-\s.]+", " ", stem).strip()
    markers = []
    m = re.search(r"\bv(\d+(?:\.\d+)*)\b", norm)
    if m:
        markers.append(m.group(0))
    for kw in ("final", "draft", "rev", "version", "old", "new", "copy", "backup"):
        if re.search(r"\b" + re.escape(kw) + r"\b", norm):
            markers.append(kw)
    mdate = re.search(r"\d{8}|\d{4}[-_]\d{2}[-_]\d{2}", norm)
    if mdate:
        markers.append(mdate.group(0))
    base = norm
    for mk in sorted(markers, key=len, reverse=True):
        # allow the marker's dashes to match the normalized spaces as well
        token = re.escape(mk).replace(r"\-", r"[- ]")
        base = re.sub(r"\b" + token + r"\b", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return (base or norm), markers


def _relpath(path, root):
    rel = os.path.relpath(path, root)
    return rel.replace(os.sep, "/")


def run_inventory(input_dir, work_dir):
    """Return inventory dict; also write inventory.json + evidence_index.json."""
    start = now_iso()
    os.makedirs(work_dir, exist_ok=True)

    files = []
    errors = []
    degradations = []
    limitations = []
    uncertainty = []
    failures_recovery = []
    directories = []
    tree = {}

    if not os.path.isdir(input_dir):
        limitations.append({
            "limitation": "input directory missing or not a directory",
            "impact": "cannot inventory any files; result will be insufficient_evidence/input_error",
            "files": [str(input_dir)],
            "needs_human": True,
            "blocking": True,
        })
        inv = _assemble(input_dir, files, errors, degradations, limitations,
                        uncertainty, failures_recovery, directories, tree, start)
        _write(inv, work_dir)
        return inv

    # collect files with robust permission handling
    def onerror(err):
        limitations.append({
            "limitation": "directory unreadable/permission denied",
            "impact": "files under it are omitted from inventory",
            "files": [getattr(err, "filename", "")],
            "needs_human": True,
        })

    for dirpath, dirnames, filenames in os.walk(input_dir, onerror=onerror):
        rel_dir = _relpath(dirpath, input_dir)
        try:
            dstat = os.stat(dirpath)
            directories.append({"path": rel_dir or ".", "mtime_utc": _mtime(dstat.st_mtime),
                                "mode": stat.filemode(dstat.st_mode)})
        except OSError:
            pass
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            rel = _relpath(abspath, input_dir)
            entry = _scan_file(abspath, rel, input_dir)
            files.append(entry)
            _graft(tree, rel, entry)
            for lim in entry.pop("_limitations", []):
                if lim not in limitations:
                    limitations.append(lim)
            for u in entry.pop("_uncertainty", []):
                if u not in uncertainty:
                    uncertainty.append(u)
            for fr in entry.pop("_failures", []):
                failures_recovery.append(fr)

    # category summary from extension+sniff
    file_types = {}
    for f in files:
        key = f.get("type", "unknown")
        file_types[key] = file_types.get(key, 0) + 1

    # duplicates by SHA256 (empty files separated)
    dup_groups, empty_files = _group_duplicates(files)

    # version families
    version_families = _group_versions(files)

    # material files = everything except the task scaffolding (case.json/task.md)
    scaffold_names = {"case.json", "task.md"}
    material_files = [f for f in files
                      if os.path.basename(f["path"]).lower() not in scaffold_names]

    summary = {
        "n_files": len(files),
        "n_material_files": len(material_files),
        "n_directories": len(directories),
        "total_size_bytes": sum(f.get("size") or 0 for f in files),
        "file_types": file_types,
        "n_duplicate_groups": len(dup_groups),
        "n_version_families": len(version_families),
        "n_empty_files": len(empty_files),
        "n_unreadable": len([f for f in files if not (f.get("permissions") or {}).get("readable", True)]),
    }

    if not material_files:
        limitations.append({
            "limitation": "empty input directory (no material files besides case.json/task.md)",
            "impact": "no material to answer from; result status=insufficient_evidence",
            "files": [],
            "needs_human": False,
            "blocking": True,
        })

    inv = {
        "case_scan": {"input_dir": input_dir, "scanned_at_utc": start},
        "summary": summary,
        "file_types": file_types,
        "directories": directories,
        "directory_tree": tree,
        "duplicate_groups": dup_groups,
        "version_families": version_families,
        "empty_files": empty_files,
        "files": files,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "uncertainty": uncertainty,
        "failures_recovery": failures_recovery,
        "change_log": [{
            "step": "inventory",
            "start_utc": start,
            "end_utc": now_iso(),
            "action": "six-dimension scan + evidence index seed",
            "status": "ok" if files else "partial",
            "detail": "%d files, %d dirs" % (len(files), len(directories)),
        }],
    }
    _write(inv, work_dir)
    return inv


def _scan_file(abspath, rel, input_root):
    entry = {
        "path": rel,
        "size": None,
        "type": "unknown",
        "sha256": None,
        "text_encoding": None,
        "permissions": {},
        "metadata": {},
        "version": [],
        "version_base": None,
        "source": "task_input",
        "readable": True,
        "_limitations": [],
        "_uncertainty": [],
        "_failures": [],
    }
    try:
        st = os.stat(abspath)
    except OSError as exc:
        entry["readable"] = False
        entry["_limitations"].append({
            "limitation": "stat failed (permission denied or broken link)",
            "impact": "file omitted from content evidence",
            "files": [rel],
            "needs_human": True,
        })
        entry["_uncertainty"].append({
            "level": "unreadable", "item": rel,
            "reason": "stat failed: %s" % exc, "missing": "metadata and content",
        })
        entry["_failures"].append({"file": rel, "action": "stat", "status": "failed",
                                   "reason": str(exc), "recovery": "pending_human"})
        return entry

    entry["size"] = st.st_size
    entry["permissions"] = {
        "mode_oct": oct(st.st_mode & 0o7777),
        "mode_human": stat.filemode(st.st_mode),
        "readable": os.access(abspath, os.R_OK),
        "writable": os.access(abspath, os.W_OK),
        "uid": st.st_uid,
        "gid": st.st_gid,
    }
    entry["metadata"] = {
        "mtime_utc": _mtime(st.st_mtime),
        "ctime_utc": _mtime(st.st_ctime),
        "size_bytes": st.st_size,
    }
    if not os.access(abspath, os.R_OK):
        entry["_limitations"].append({
            "limitation": "permission_denied (no read access)",
            "impact": "content and hash unavailable",
            "files": [rel],
            "needs_human": True,
        })
        entry["_uncertainty"].append({
            "level": "unreadable", "item": rel,
            "reason": "read permission denied", "missing": "content evidence",
        })
        return entry

    # hashing
    try:
        h = hashlib.sha256()
        with open(abspath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        entry["sha256"] = h.hexdigest()
    except OSError as exc:
        entry["_failures"].append({"file": rel, "action": "hash", "status": "failed",
                                   "reason": str(exc), "recovery": "retry_later"})

    header = _read_bytes(abspath)
    entry["type"] = sniff_kind(abspath, header)

    if st.st_size > MAX_TEXT_BYTES:
        entry["_limitations"].append({
            "limitation": "file exceeds 2 MB text-parse cap",
            "impact": "hash/meta kept, body snippet not parsed for evidence",
            "files": [rel],
            "needs_human": False,
        })
    else:
        text, enc, note = _sniff_text_and_encoding(abspath, header)
        if enc:
            entry["text_encoding"] = enc
        if note == "encoding_fallback":
            entry["_limitations"].append({
                "limitation": "encoding_fallback (not strict utf-8)",
                "impact": "quotes may contain mojibake; verify before citing verbatim",
                "files": [rel],
                "needs_human": False,
            })
        elif note and note.startswith("unreadable"):
            entry["_limitations"].append({
                "limitation": note,
                "impact": "no text layer available for this file",
                "files": [rel],
                "needs_human": True,
            })
            entry["_uncertainty"].append({
                "level": "unreadable", "item": rel, "reason": note, "missing": "text content",
            })
        if text is not None and entry["type"] in ("text", "markdown"):
            entry["metadata"].update(_parse_frontmatter(text))
            entry["text_stats"] = {"total_chars": len(text), "n_lines": text.count("\n") + 1 if text else 0}
            entry["_snippet"] = text[:SNIPPET_CHARS]

    if entry["type"] == "pdf" and entry.get("sha256"):
        # PDF text layer is extracted on demand by pdftotext; flag if not possible
        _pdf_check(abspath, rel, entry)

    base, markers = _classify_version(os.path.basename(rel))
    entry["version"] = markers
    entry["version_base"] = base

    return entry


def _mtime(ts):
    return time.strftime(UTC_FMT, time.gmtime(ts))


def _graft(tree, rel, entry):
    parts = rel.split("/")
    node = tree
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = {
        "type": entry.get("type"),
        "sha256": entry.get("sha256"),
        "size": entry.get("size"),
    }


def _group_duplicates(files):
    by_hash = {}
    empty_files = []
    for f in files:
        if f.get("sha256") is None:
            continue
        if f.get("size") == 0:
            empty_files.append(f["path"])
            continue
        by_hash.setdefault(f["sha256"], []).append(f["path"])
    groups = []
    for sha in sorted(by_hash):
        if len(by_hash[sha]) > 1:
            groups.append({"sha256": sha, "paths": sorted(by_hash[sha])})
    return groups, sorted(empty_files)


def _group_versions(files):
    fam = {}
    for f in files:
        base = f.get("version_base")
        if not base:
            continue
        members = fam.setdefault(base, [])
        members.append({
            "path": f["path"],
            "sha256": f.get("sha256"),
            "version": f.get("version", []),
        })
    return {base: sorted(fam[base], key=lambda x: x["path"]) for base in sorted(fam)}


def _pdf_check(abspath, rel, entry):
    """Try pdftotext if available; if unavailable/unreadable, flag honestly."""
    if shutil.which("pdftotext") is None:
        entry["_limitations"].append({
            "limitation": "pdftotext not available",
            "impact": "PDF page text cannot be extracted for locator/quote evidence",
            "files": [rel],
            "needs_human": False,
        })
        entry["_uncertainty"].append({
            "level": "unreadable", "item": rel,
            "reason": "pdftotext missing; PDF text layer not verified",
            "missing": "page-level text / quote",
        })
        return
    try:
        proc = subprocess.run(["pdftotext", "-layout", abspath, "-"], capture_output=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        entry["_limitations"].append({
            "limitation": "pdftotext invocation failed",
            "impact": "PDF text unavailable; treat as unreadable",
            "files": [rel],
            "needs_human": True,
        })
        entry["_uncertainty"].append({
            "level": "unreadable", "item": rel, "reason": "pdftotext failed: %s" % exc,
            "missing": "page-level text",
        })
        return
    raw = proc.stdout
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")
    if text.strip():
        entry["_pdf_text_snippet"] = text[:SNIPPET_CHARS]
        entry["_pdf_text_pages"] = _split_pdf_pages(text)
    else:
        entry["_limitations"].append({
            "limitation": "PDF has no extractable text layer (scan/OCR needed)",
            "impact": "cannot provide verbatim quote; mark unreadable, do not fabricate",
            "files": [rel],
            "needs_human": False,
        })
        entry["_uncertainty"].append({
            "level": "unreadable", "item": rel, "reason": "no text layer from pdftotext",
            "missing": "OCR text",
        })


def _split_pdf_pages(text):
    pages = re.split(r"\f", text)
    return [p.strip() for p in pages]


def _assemble(input_dir, files, errors, degradations, limitations, uncertainty,
              failures_recovery, directories, tree, start):
    return {
        "case_scan": {"input_dir": input_dir, "scanned_at_utc": start},
        "summary": {},
        "file_types": {},
        "directories": directories,
        "directory_tree": tree,
        "duplicate_groups": [],
        "version_families": {},
        "empty_files": [],
        "files": files,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "uncertainty": uncertainty,
        "failures_recovery": failures_recovery,
        "change_log": [],
    }


def _write(inv, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    inv_path = os.path.join(work_dir, "inventory.json")

    # build evidence index: strip private underscore keys and snippets
    evidence = {"files": []}
    for f in inv.get("files", []):
        ev = {
            "path": f.get("path"),
            "sha256": f.get("sha256"),
            "type": f.get("type"),
            "version": f.get("version"),
            "version_base": f.get("version_base"),
            "readable": (f.get("permissions") or {}).get("readable", True),
        }
        for key in ("_snippet", "_pdf_text_snippet"):
            if f.get(key):
                ev["snippet" + key[1:]] = f[key]
        if f.get("_pdf_text_pages"):
            ev["pdf_pages"] = f["_pdf_text_pages"]
        evidence["files"].append(ev)

    # keep inventory.json a clean deliverable: strip private underscore keys
    # from file entries (snippets already live in evidence_index.json).
    clean = dict(inv)
    clean["files"] = [
        {k: v for k, v in f.items() if not k.startswith("_")}
        for f in inv.get("files", [])
    ]
    with open(inv_path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    with open(os.path.join(work_dir, "evidence_index.json"), "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return inv_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Six-dimension inventory + evidence index for the KB QA skill.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)
    inv = run_inventory(args.input, args.work)
    print("inventoried %d files, %d dirs" % (
        len(inv.get("files", [])), len(inv.get("directories", []))))
    return 0


if __name__ == "__main__":
    sys.exit(main())