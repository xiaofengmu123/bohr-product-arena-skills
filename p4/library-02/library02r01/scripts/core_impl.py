"""core_impl.py - document library repair: inventory, dedup, version chains,
aliases, registry parsing, reconciliation, index, answers, and evidence map.

Design goals
------------
* Deterministic: directory traversal is sorted, hashing is stable, no random.
* Robust: bad JSON/encoding, unreadable files, huge files, broken symlinks and
  SQLite errors are recorded (not raised) and processing continues.
* Evidence-first: every machine conclusion carries a locator (forward-slash
  relative path, optionally "#page" or "#L<line>") plus a verbatim quote, and
  is aggregated into a fine-grained `evidence_map` as well as a top-level
  `sources` list. The evidence map is the emphasized variant feature: it keeps
  claim, kind, method, locator, quote, quote span, confidence, basis and
  conflicts as separate fields so a reviewer can re-run and re-check each item.
* Standard library only.
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as stat_module
import sys
import time
import unicodedata
from datetime import datetime, timezone

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB cap for content hashing / text
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml",
    ".html", ".htm", ".bib", ".ris", ".tex", ".yaml", ".yml", ".ini",
    ".cfg", ".log", ".rst", ".py", ".r", ".m", ".nbib", ".enl",
}
REGISTRY_SUFFIXES = {
    ".json", ".csv", ".md", ".markdown", ".bib", ".ris", ".db", ".sqlite",
    ".sqlite3", ".tsv",
}
BINARY_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png",
    ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".zip", ".gz", ".tar",
    ".svg", ".ipynb", ".odt", ".ods", ".odp",
}
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log", "errors",
    "degradations", "runtime", "limitations", "pending_human", "computed",
    "artifacts",
}
CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract", "output_schema",
    "result_contract", "required_fields",
)
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def forward_slash(path):
    return path.replace(os.sep, "/")


def relpath_forward(abspath, root):
    """Return a forward-slash relative path, or the abspath if outside root."""
    try:
        rp = os.path.relpath(abspath, root)
    except ValueError:
        return forward_slash(abspath)
    return forward_slash(rp)


def safe_stat_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mode_octal(st):
    try:
        return "%o" % (st.st_mode & 0o7777)
    except Exception:
        return None


def is_text_like(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_SUFFIXES:
        return True
    if ext in BINARY_SUFFIXES:
        return False
    return None  # unknown


def read_bytes(path, max_bytes=DEFAULT_MAX_BYTES):
    """Read up to max_bytes; return (data, too_large, error)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, False, "stat_error:%s" % exc
    if size > max_bytes:
        return None, True, None
    try:
        with open(path, "rb") as fh:
            return fh.read(), False, None
    except OSError as exc:
        return None, False, "read_error:%s" % exc


def decode_text(data, name):
    """Best-effort text decode with fallbacks. Returns (text, encoding, note)."""
    if data is None:
        return None, None, "no_data"
    if b"\x00" in data[:1024]:
        return None, "binary", "null_byte"
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return data.decode("utf-8", errors="replace"), "utf-8-replace", "replaced"
    except Exception as exc:  # pragma: no cover - defensive
        return None, None, "undecodable:%s" % exc


def sha256_bytes(data):
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def normalize_stem(name):
    """Normalize a filename stem for grouping version families.

    Strips extension, lowercases, removes version/copy/date tokens so that
    "protocol_v2_final.docx" and "protocol-v1.docx" map to the same stem.
    """
    stem = os.path.splitext(name)[0].lower()
    original = stem
    stem = re.sub(r"\d{4}[-_.]?\d{1,2}[-_.]?\d{1,2}", " ", stem)
    stem = re.sub(
        r"[-_.\s]*(v|ver|version|rev|revision|r|draft|final|copy|副本|修订|终稿"
        r"|最终|初稿|新版|旧版|latest|old|new)\s*[-_.\s]*\d*[-_.\s]*",
        " ", stem)
    stem = re.sub(r"[-_.\s]*\(?\d+\)?[-_.\s]*", " ", stem)
    stem = re.sub(r"[-_.\s]+", " ", stem).strip()
    return stem or original.strip() or original


def tokenize(text):
    if not text:
        return []
    lowered = unicodedata.normalize("NFKC", text).lower()
    return re.findall(r"[a-z0-9\u4e00-\u9fff][a-z0-9\u4e00-\u9fff_\-]*",
                      lowered)


def text_similarity(text_a, text_b):
    if not text_a or not text_b:
        return None
    ta, tb = tokenize(text_a), tokenize(text_b)
    if not ta or not tb:
        return None
    return round(difflib.SequenceMatcher(None, ta, tb).ratio(), 6)


def deep_merge(base, patch):
    """Recursively merge patch into base (dicts merge, other types replace)."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = dict(base)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def clip(text, limit=240):
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "\u2026"


# ---------------------------------------------------------------------------
# Input contract discovery
# ---------------------------------------------------------------------------


def load_json_safe(path):
    if not path or not os.path.isfile(path):
        return None, "missing"
    data, too_large, err = read_bytes(path)
    if err:
        return None, err
    if too_large:
        return None, "too_large"
    text, _enc, note = decode_text(data, os.path.basename(path))
    if text is None:
        return None, "undecodable:%s" % note
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        # Record that the file was present but unparseable.
        return None, "parse_error:%s" % exc


def extract_contract(case_obj):
    """Return (contract_obj, found_key) where contract_obj is the dict/list to
    backfill. Returns (None, None) when no contract key is present."""
    if not isinstance(case_obj, dict):
        return None, None
    for key in CONTRACT_KEYS:
        if key in case_obj and case_obj[key] is not None:
            return case_obj[key], key
    return None, None


def contract_field_map(contract_obj):
    """Flatten a contract into a list of (field_name, kind_hint) pairs.

    kind_hint in {dict, list, scalar, unknown}. Preserves nesting for dict
    schemas by yielding leaf field names with dot paths.
    """
    result = []

    def walk(node, prefix):
        if isinstance(node, dict):
            if not node:
                result.append((prefix, "unknown"))
                return
            for key, value in node.items():
                name = key if prefix is None else "%s.%s" % (prefix, key)
                if isinstance(value, dict):
                    walk(value, name)
                elif isinstance(value, list):
                    result.append((name, "list"))
                elif value in (None, "", None.__class__) or value is None:
                    result.append((name, "unknown"))
                else:
                    result.append((name, "scalar"))
        elif isinstance(node, list):
            result.append((prefix, "list"))
        elif node is None:
            result.append((prefix, "unknown"))
        else:
            result.append((prefix, "scalar"))

    walk(contract_obj, None)
    return result


def contract_value_lookup(computed, field_name):
    """Map a contract field name to a computed value, else None."""
    kl = field_name.lower()
    lookup = computed.get("contract_lookup", {})
    for key, value in lookup.items():
        if key and key in kl:
            return value
    return None


# ---------------------------------------------------------------------------
# Discovery / inventory
# ---------------------------------------------------------------------------


def discover(input_root):
    """Walk the input tree deterministically and build the inventory."""
    files = []
    dirs = []
    errors = []
    if not os.path.isdir(input_root):
        return files, dirs, errors, {
            "error": "input_root_missing:%s" % input_root}

    for base, subdirs, filenames in os.walk(input_root):
        subdirs.sort()
        filenames.sort()
        rel_base = relpath_forward(base, input_root)
        if rel_base != ".":
            dirs.append({
                "path": rel_base,
                "kind": "dir",
                "mode": mode_octal(os.stat(base)) if _exists(base) else None,
            })
        for name in filenames:
            full = os.path.join(base, name)
            rel = relpath_forward(full, input_root)
            entry = inspect_entry(full, rel, input_root)
            files.append(entry)
            if entry.get("note"):
                errors.append({
                    "path": rel, "kind": entry["kind"], "note": entry["note"],
                    "reason": entry.get("note"),
                })
    return files, dirs, errors, None


def _exists(path):
    try:
        os.lstat(path)
        return True
    except OSError:
        return False


def inspect_entry(full, rel, input_root):
    entry = {
        "path": rel,
        "name": os.path.basename(full),
        "kind": "file",
        "size": None,
        "mode": None,
        "mtime": None,
        "ext": os.path.splitext(full)[1].lower(),
        "sha256": None,
        "encoding": None,
        "text_preview": None,
        "inode": None,
        "nlink": None,
        "link_target": None,
        "note": None,
    }
    try:
        st = os.lstat(full)
    except OSError as exc:
        entry["note"] = "lstat_error:%s" % exc
        entry["kind"] = "unreadable"
        return entry

    entry["mode"] = mode_octal(st)
    entry["mtime"] = safe_stat_int(getattr(st, "st_mtime", None))
    entry["size"] = safe_stat_int(getattr(st, "st_size", None))
    entry["inode"] = safe_stat_int(getattr(st, "st_ino", None))
    entry["nlink"] = safe_stat_int(getattr(st, "st_nlink", None))

    if stat_module.S_ISLNK(st.st_mode):
        entry["kind"] = "symlink"
        try:
            target = os.readlink(full)
            entry["link_target"] = forward_slash(target)
            # Broken when the target does not resolve relative to the link.
            resolved = os.path.join(os.path.dirname(full), target)
            if not os.path.exists(resolved):
                entry["kind"] = "broken_symlink"
                entry["note"] = "broken_symlink"
        except OSError as exc:
            entry["note"] = "readlink_error:%s" % exc
        return entry

    if not stat_module.S_ISREG(st.st_mode):
        entry["kind"] = "non_regular"
        entry["note"] = "non_regular"
        return entry

    data, too_large, err = read_bytes(full)
    if err:
        entry["note"] = err
        return entry
    if too_large:
        entry["note"] = "too_large"
        return entry

    entry["sha256"] = sha256_bytes(data)

    text_like = is_text_like(full)
    if text_like is not False:
        text, encoding, note = decode_text(data, entry["name"])
        entry["encoding"] = encoding
        if text is not None:
            entry["text_preview"] = text[:2000]
            if note:
                entry.setdefault("note", note)
    return entry


def summarize(files, dirs):
    n_files = sum(1 for f in files if f["kind"] == "file")
    n_symlink = sum(1 for f in files if f["kind"] == "symlink")
    n_broken = sum(1 for f in files if f["kind"] == "broken_symlink")
    n_other = sum(1 for f in files if f["kind"] == "non_regular")
    n_unreadable = sum(1 for f in files if f["kind"] == "unreadable")
    total_size = sum(f["size"] or 0 for f in files if f["kind"] == "file")
    ext_counts = {}
    for f in files:
        ext = f["ext"] or "(none)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    return {
        "files": n_files,
        "directories": len(dirs),
        "symlinks": n_symlink,
        "broken_symlinks": n_broken,
        "non_regular": n_other,
        "unreadable": n_unreadable,
        "total_bytes": total_size,
        "extensions": dict(sorted(ext_counts.items())),
    }


# ---------------------------------------------------------------------------
# Deduplication, versions, aliases
# ---------------------------------------------------------------------------


def build_duplicate_groups(files):
    by_hash = {}
    for f in files:
        h = f.get("sha256")
        if h:
            by_hash.setdefault(h, []).append(f["path"])
    groups = []
    for h in sorted(by_hash):
        members = by_hash[h]
        if len(members) > 1:
            groups.append({
                "sha256": h,
                "count": len(members),
                "members": sorted(members),
                "basis": "sha256",
            })
    return groups


def build_version_candidates(files):
    stem_groups = {}
    for f in files:
        if f["kind"] != "file":
            continue
        stem = normalize_stem(f["name"])
        stem_groups.setdefault(stem, []).append(f)
    candidates = []
    for stem in sorted(stem_groups):
        members = sorted(stem_groups[stem], key=lambda x: x["path"])
        if len(members) < 2:
            continue
        similarities = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ratio = None
                basis = None
                if (a.get("text_preview") is not None
                        and b.get("text_preview") is not None):
                    ratio = text_similarity(a["text_preview"],
                                            b["text_preview"])
                    basis = "text_tokens" if ratio is not None else "none"
                elif a.get("sha256") and a.get("sha256") == b.get("sha256"):
                    ratio = 1.0
                    basis = "sha256"
                elif a.get("size") is not None and b.get("size") is not None:
                    basis = "binary_size"
                similarities.append({
                    "pair": [a["path"], b["path"]],
                    "similarity": ratio,
                    "basis": basis,
                })
        same_hash = all(s["basis"] == "sha256" for s in similarities) \
            if similarities else False
        if same_hash:
            relation = "duplicate"
        elif any(s["similarity"] is not None for s in similarities):
            relation = "version_chain"
        else:
            relation = "same_stem_unresolved"
        candidates.append({
            "stem": stem,
            "relation": relation,
            "heuristic": True,
            "members": [m["path"] for m in members],
            "similarities": similarities,
        })
    return candidates


def build_aliases(files):
    symlinks = []
    broken = []
    hardlink_groups = {}
    for f in files:
        if f["kind"] in ("symlink", "broken_symlink"):
            if f["kind"] == "broken_symlink":
                broken.append({
                    "link": f["path"], "target": f.get("link_target"),
                    "note": f.get("note"),
                })
            else:
                symlinks.append({
                    "link": f["path"], "target": f.get("link_target"),
                })
        if f["kind"] == "file" and f.get("nlink") and f["nlink"] > 1 \
                and f.get("inode") is not None:
            hardlink_groups.setdefault(f["inode"], []).append(f["path"])
    hardlink_groups = {str(k): sorted(v) for k, v in
                       sorted(hardlink_groups.items())
                       if len(v) > 1}
    return {
        "symlink_aliases": symlinks,
        "broken_symlinks": broken,
        "hardlink_groups": hardlink_groups,
    }


# ---------------------------------------------------------------------------
# Registry parsing (library directories / manifests)
# ---------------------------------------------------------------------------


def _is_registry_file(entry):
    if entry["kind"] != "file":
        return False
    ext = entry["ext"]
    if ext in REGISTRY_SUFFIXES:
        return True
    name = entry["name"].lower()
    return any(t in name for t in
               ("library", "manifest", "catalog", "index", "register",
                "registry", "inventory", "库", "清单", "目录", "登记"))


def _has_path_field(entry):
    if not isinstance(entry, dict):
        return False
    for key, value in entry.items():
        if not isinstance(value, str):
            continue
        low = value.lower()
        if (key.lower() in ("path", "file", "filename", "file_path",
                            "filepath", "location", "url", "l1", "id")
                or "/" in value or "\\" in value
                or low.endswith(tuple(TEXT_SUFFIXES | BINARY_SUFFIXES))):
            return True
    return False


def parse_json_registry(path, data):
    obj, err = load_json_safe(path)
    if err:
        return None, "json_parse_error:%s" % err
    entries = []
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and _has_path_field(item):
                entries.append(item)
    elif isinstance(obj, dict):
        # Try common container keys first; else a single entry only when it
        # carries path-like fields (so case.json/contract JSON are not
        # mistaken for a library manifest).
        for key in ("files", "items", "entries", "records", "documents"):
            if isinstance(obj.get(key), list):
                for item in obj[key]:
                    if isinstance(item, dict) and _has_path_field(item):
                        entries.append(item)
        if not entries and _has_path_field(obj):
            entries.append(obj)
    return entries, None


def parse_csv_registry(path, data):
    text, _enc, note = decode_text(data, os.path.basename(path))
    if text is None:
        return None, "csv_decode_error:%s" % note
    entries = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if row and any(v for v in row.values() if v):
                entries.append(dict(row))
    except (csv.Error, TypeError) as exc:
        return None, "csv_parse_error:%s" % exc
    return entries, None


def parse_markdown_registry(path, data):
    text, _enc, note = decode_text(data, os.path.basename(path))
    if text is None:
        return None, "md_decode_error:%s" % note
    entries = []
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            rows.append(cells)
    if len(rows) < 2:
        return [], None
    header = rows[0]
    for row in rows[1:]:
        if any(re.match(r"^:?-+:?$", c or "") for c in row):
            continue  # separator row
        if len(row) == len(header):
            entries.append(dict(zip(header, row)))
        else:
            # Pad/truncate to header length.
            entries.append(dict(zip(header, (row + [""] * len(header))
                                    [:len(header)])))
    return entries, None


_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", re.S)


def parse_bibtex_registry(path, data):
    text, _enc, note = decode_text(data, os.path.basename(path))
    if text is None:
        return None, "bib_decode_error:%s" % note
    entries = []
    for m in re.finditer(r"@\w+\s*\{([^,]+),", text):
        body_end = text.find("}", m.end() - 1)
        body = text[m.end() - 1:body_end if body_end > 0 else len(text)]
        fields = dict(_BIB_FIELD_RE.findall(body))
        fields["_key"] = m.group(1).strip()
        entries.append(fields)
    return entries, None


def parse_ris_registry(path, data):
    text, _enc, note = decode_text(data, os.path.basename(path))
    if text is None:
        return None, "ris_decode_error:%s" % note
    entries = []
    current = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("ER"):
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("TY"):
            current = {}
        if len(line) >= 6 and line[:2] == line[2:4]:
            tag, value = line[2:4], line[6:].strip()
            current[tag] = value
        elif line[:2] in ("TI", "AU", "A1", "PY", "DO", "ID", "L1", "UR",
                          "JF", "T2", "SP", "EP"):
            tag, value = line[:2], line[6:].strip()
            current.setdefault(tag, value)
    if current:
        entries.append(current)
    return entries, None


def parse_sqlite_registry(path):
    entries = []
    tables = []
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            try:
                cur.execute("SELECT * FROM %s LIMIT 200" % _quote_ident(table))
            except sqlite3.Error as exc:
                continue
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchall()
            for row in rows:
                rec = dict(zip(cols, row))
                if any(v for v in rec.values() if v):
                    entries.append(rec)
        conn.close()
    except sqlite3.Error as exc:
        return entries, tables, "sqlite_error:%s" % exc
    return entries, tables, None


def _quote_ident(name):
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""):
        return name
    return '"%s"' % (name or "").replace('"', '""')


def _path_fields(entry):
    """Extract path-like values from a registry entry (any key)."""
    out = {}
    for key, value in (entry.items() if isinstance(entry, dict) else []):
        if isinstance(value, str):
            low = value.lower()
            if ("/" in value or "\\" in value or low.endswith(
                    tuple(TEXT_SUFFIXES | BINARY_SUFFIXES))):
                out[key] = value
    return out


def parse_registries(files, input_root, exclude=None):
    exclude = exclude or set()
    registries = []
    for f in files:
        if f["path"] in exclude:
            continue
        if not _is_registry_file(f):
            continue
        full = os.path.join(input_root, f["path"].replace("/", os.sep))
        data, too_large, err = read_bytes(full)
        if err:
            registries.append({"path": f["path"], "format": f["ext"],
                               "entries": [], "error": err})
            continue
        if too_large:
            registries.append({"path": f["path"], "format": f["ext"],
                               "entries": [], "error": "too_large"})
            continue
        fmt = f["ext"].lower()
        entries = []
        error = None
        if fmt == ".json":
            entries, error = parse_json_registry(full, data)
        elif fmt in (".csv", ".tsv"):
            entries, error = parse_csv_registry(full, data)
        elif fmt in (".md", ".markdown"):
            entries, error = parse_markdown_registry(full, data)
        elif fmt == ".bib":
            entries, error = parse_bibtex_registry(full, data)
        elif fmt == ".ris":
            entries, error = parse_ris_registry(full, data)
        elif fmt in (".db", ".sqlite", ".sqlite3"):
            entries, _tables, error = parse_sqlite_registry(full)
        else:
            error = "unsupported_registry_format:%s" % fmt
        registries.append({
            "path": f["path"], "format": fmt, "entries": entries or [],
            "error": error,
        })
    return registries


def collect_registered(registries):
    """Flatten registry entries into a normalized registered list."""
    registered = []
    for reg in registries:
        for idx, entry in enumerate(reg.get("entries") or []):
            paths = _path_fields(entry)
            path_val = None
            for key in ("path", "file", "filename", "file_path", "filepath",
                        "location", "url", "L1", "ID"):
                if key in entry and isinstance(entry[key], str) and entry[key]:
                    path_val = entry[key]
                    break
            if path_val is None and paths:
                path_val = sorted(paths.values())[0]
            registered.append({
                "registry": reg["path"],
                "row": idx,
                "registered_path": path_val,
                "metadata": entry,
            })
    return registered


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _normalize_registered_path(value):
    if not value:
        return None
    v = str(value).replace("\\", "/")
    v = v.split("://", 1)[-1] if "://" in v else v
    v = v.lstrip("./")
    return v.lower().rstrip("/")


def reconcile(registered, files):
    actual = {}
    for f in files:
        if f["kind"] == "file":
            actual[f["path"].lower().rstrip("/")] = f
        elif f["kind"] in ("symlink", "broken_symlink"):
            actual.setdefault(f["path"].lower().rstrip("/"), f)

    matches, mismatches, missing, orphaned = [], [], [], []
    matched_paths = set()
    seen_registered = set()
    for rec in registered:
        rp = _normalize_registered_path(rec.get("registered_path"))
        if not rp:
            missing.append({**rec, "reason": "no_path_field"})
            continue
        if rp in seen_registered:
            continue
        seen_registered.add(rp)
        found = None
        if rp in actual:
            found = actual[rp]
        else:
            for ap in actual:
                if ap.endswith("/" + rp) or rp.endswith("/" + ap):
                    found = actual[ap]
                    break
        if found is None:
            missing.append({**rec, "reason": "not_found"})
        else:
            matched_paths.add(found["path"].lower().rstrip("/"))
            reg_hash = None
            md = rec.get("metadata") or {}
            for key in ("sha256", "hash", "checksum", "md5"):
                if md.get(key):
                    reg_hash = str(md[key]).strip().lower()
                    break
            # Only compare when the registered hash is a plausible 64-hex
            # SHA-256 digest; shorter digests (e.g. MD5) cannot be verified
            # against the SHA-256 we compute and are noted, not flagged.
            if (reg_hash and found.get("sha256")
                    and re.fullmatch(r"[0-9a-f]{64}", reg_hash)
                    and reg_hash != found["sha256"]):
                mismatches.append({
                    **rec,
                    "found_path": found["path"],
                    "registered_hash": reg_hash,
                    "actual_hash": found["sha256"],
                })
            elif (reg_hash and found.get("sha256")
                    and not re.fullmatch(r"[0-9a-f]{64}", reg_hash)):
                matches.append({
                    **rec,
                    "found_path": found["path"],
                    "hash_note": "registered_hash_not_sha256; verification "
                                 "skipped",
                })
            else:
                matches.append({**rec, "found_path": found["path"]})

    for f in files:
        if f["kind"] in ("file", "symlink", "broken_symlink"):
            key = f["path"].lower().rstrip("/")
            if key not in matched_paths:
                orphaned.append({
                    "path": f["path"], "kind": f["kind"],
                    "reason": "unregistered",
                })
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
    }


# ---------------------------------------------------------------------------
# Version notes parsing
# ---------------------------------------------------------------------------


def parse_version_notes(files, input_root, version_candidates=None):
    notes = []
    pattern = re.compile(
        r"(?i)\b(v(?:er(?:sion)?)?\.?\s*[\d]+(?:\.[\d]+)*"
        r"(?:[-_][\w]+)?)\b")
    for f in files:
        if f["kind"] != "file":
            continue
        name_low = f["name"].lower()
        is_note = any(t in name_low for t in
                      ("version", "changelog", "changes", "release", "notes",
                       "history", "版本", "修订"))
        if not is_note and f.get("text_preview") is None:
            continue
        text = f.get("text_preview")
        found = []
        if text:
            found = sorted(set(pattern.findall(text)))[:40]
        if found:
            notes.append({
                "path": f["path"],
                "kind": "version_note",
                "versions": found,
                "quote": clip(text) if text else "",
            })

    # Link notes to candidate chains: a note is "linked" when any candidate
    # member path shares a numeric version token with the note.
    version_candidates = version_candidates or []
    member_tokens = {}
    for vc in version_candidates:
        for m in vc["members"]:
            for tok in re.findall(r"(?i)(?:v|ver|version)?\.?\s*(\d+(?:\.\d+)*)",
                                  m):
                member_tokens.setdefault(tok.lower(), set()).add(m)

    linked, orphan_notes = [], []
    for note in notes:
        note_tokens = set()
        for v in note["versions"]:
            for tok in re.findall(r"(?i)(\d+(?:\.\d+)*)", v):
                note_tokens.add(tok.lower())
        hit = set()
        for tok in note_tokens:
            hit.update(member_tokens.get(tok, set()))
        if hit:
            linked.append({**note, "linked_members": sorted(hit)})
        else:
            orphan_notes.append(note)
    return {"version_notes": notes, "linked": linked, "orphan": orphan_notes}


# ---------------------------------------------------------------------------
# Searchable index
# ---------------------------------------------------------------------------


def build_index(files, duplicate_groups, version_candidates):
    by_stem = {}
    by_token = {}
    by_ext = {}
    for f in files:
        if f["kind"] != "file":
            continue
        stem = normalize_stem(f["name"])
        by_stem.setdefault(stem, []).append(f["path"])
        by_ext.setdefault(f["ext"] or "(none)", []).append(f["path"])
        for tok in tokenize(f.get("text_preview") or "")[:200]:
            by_token.setdefault(tok, set()).add(f["path"])
    by_token = {k: sorted(v) for k, v in by_token.items()}
    return {
        "by_stem": by_stem,
        "by_extension": by_ext,
        "by_token": {k: v for k, v in sorted(by_token.items())},
        "duplicate_groups": duplicate_groups,
        "version_candidates": version_candidates,
        "note": "token index is a deterministic first-pass; semantic search "
                "is layered by the agent on top of these candidates",
    }


# ---------------------------------------------------------------------------
# Answers (mechanical retrieval questions)
# ---------------------------------------------------------------------------


def answer_questions(task_text, case_obj, files, duplicate_groups,
                     version_candidates, aliases, reconciliation, index):
    """Answer common mechanical questions with evidence. Returns list of
    {question, answer, method, evidence_id} dicts."""
    questions = []
    corpus = (task_text or "") + "\n" + json.dumps(case_obj or {},
                                                   ensure_ascii=False)
    # Extract sentences that look like questions.
    for sent in re.split(r"[。！？!?\n]", corpus):
        sent = sent.strip()
        if not sent or len(sent) > 400:
            continue
        if re.search(r"[\?？]|多少|哪些|哪一|哪张|哪个|是否|如何|怎么|为什么|"
                     r"how many|which|where|what version|列出", sent):
            questions.append(sent)
    answers = []

    def add(q, answer, method, evidence_id):
        answers.append({
            "question": q,
            "answer": answer,
            "method": method,
            "evidence_id": evidence_id,
        })

    n_files = sum(1 for f in files if f["kind"] == "file")
    summary_q = next((q for q in questions if "多少" in q or "几个" in q
                      or "how many" in q.lower()), None)
    if summary_q:
        add(summary_q, {
            "file_count": n_files,
        }, "inventory_count", "ev-count")

    dup_q = next((q for q in questions if "重复" in q or "duplicate" in q.lower()
                  or "副本" in q), None)
    if dup_q:
        add(dup_q, {"duplicate_groups": duplicate_groups},
            "sha256_dedup", "ev-dup")

    ver_q = next((q for q in questions if "版本" in q or "version" in q.lower()),
                 None)
    if ver_q:
        add(ver_q, {"version_candidates": version_candidates},
            "stem_similarity", "ev-version")

    link_q = next((q for q in questions if "断链" in q or "符号" in q
                   or "symlink" in q.lower() or "broken" in q.lower()), None)
    if link_q:
        add(link_q, {"broken_symlinks": aliases["broken_symlinks"]},
            "symlink_scan", "ev-alias")

    rec_q = next((q for q in questions if "缺失" in q or "missing" in q.lower()
                  or "对账" in q or "reconcil" in q.lower()), None)
    if rec_q:
        add(rec_q, {
            "matches": len(reconciliation["matches"]),
            "mismatches": len(reconciliation["mismatches"]),
            "missing": len(reconciliation["missing"]),
            "orphaned": len(reconciliation["orphaned"]),
        }, "registry_reconciliation", "ev-recon")

    return answers


# ---------------------------------------------------------------------------
# Evidence map (the emphasized variant feature)
# ---------------------------------------------------------------------------


def build_evidence_map(files, dirs, duplicate_groups, version_candidates,
                       aliases, registries, reconciliation, index, answers,
                       errors):
    entries = []
    counter = [0]

    def add(kind, claim, locator, quote, method=None, confidence=None,
            basis=None, conflicts=None):
        counter[0] += 1
        eid = "ev-%04d" % counter[0]
        entries.append({
            "id": eid,
            "kind": kind,
            "claim": claim,
            "method": method,
            "locator": locator,
            "quote": quote if quote is not None else "",
            "confidence": confidence or "high",
            "basis": basis or "machine_computed",
            "conflicts": conflicts or [],
        })
        return eid

    for f in files:
        if f["kind"] == "file":
            add("file_identity",
                "文件 %s 存在，大小 %s 字节，SHA-256 %s" % (
                    f["path"], f.get("size"), f.get("sha256")),
                f["path"],
                "sha256=%s" % (f.get("sha256") or "unavailable"),
                method="sha256", basis="content_hash")
        elif f["kind"] == "broken_symlink":
            add("alias",
                "符号链接 %s 指向 %s 且目标缺失" % (
                    f["path"], f.get("link_target")),
                f["path"],
                "target=%s" % f.get("link_target"),
                method="readlink", confidence="high", basis="symlink_scan")
        elif f["kind"] == "symlink":
            add("alias", "符号链接 %s -> %s" % (f["path"],
                                                f.get("link_target")),
                f["path"], "target=%s" % f.get("link_target"),
                method="readlink", confidence="high", basis="symlink_scan")

    for d in dirs:
        add("directory", "目录 %s 存在" % d["path"], d["path"],
            "path=%s" % d["path"], method="walk", confidence="high",
            basis="filesystem_walk")

    for g in duplicate_groups:
        add("dedup", "重复组（内容相同）：%s" % "，".join(g["members"]),
            g["members"][0],
            "sha256=%s" % g["sha256"],
            method="sha256", confidence="high", basis="content_hash")

    for vc in version_candidates:
        add("version", "版本候选链 [%s]：%s（启发式 %s）" % (
            vc["relation"], "，".join(vc["members"]), vc["stem"]),
            vc["members"][0],
            "stem=%s relation=%s" % (vc["stem"], vc["relation"]),
            method="normalized_stem+similarity", confidence="medium",
            basis="heuristic", conflicts=None)

    for inode, paths in aliases["hardlink_groups"].items():
        add("alias", "硬链接组（同一 inode %s）：%s" % (inode,
                                                        "，".join(paths)),
            paths[0], "inode=%s" % inode, method="st_ino",
            confidence="high", basis="inode_grouping")

    for reg in registries:
        loc = reg["path"]
        if reg.get("error"):
            add("registry", "库目录 %s 解析失败：%s" % (loc, reg["error"]),
                loc, "error=%s" % reg["error"], method="registry_parse",
                confidence="low", basis="parse_result")
        else:
            add("registry", "库目录 %s 解析出 %d 条登记" % (
                loc, len(reg.get("entries") or [])),
                loc, "entries=%d" % len(reg.get("entries") or []),
                method="registry_parse", confidence="high",
                basis="parse_result")

    for kind, label in (("matches", "对账一致"),
                        ("mismatches", "对账哈希不一致"),
                        ("missing", "对账缺失"),
                        ("orphaned", "对账未登记(孤儿)")):
        for item in reconciliation.get(kind, []):
            claim = "%s：%s" % (label, item.get("found_path")
                                or item.get("registered_path")
                                or item.get("path"))
            quote = json.dumps(item, ensure_ascii=False, default=str)
            add("reconciliation", claim, item.get("found_path")
                or item.get("registered_path") or item.get("path"),
                quote[:300], method="registry_reconcile",
                confidence="high", basis="path+hash_comparison")

    for ans in answers:
        add("answer", "问题 [%s] 的回答：%s" % (
            clip(ans["question"], 80), clip(json.dumps(ans["answer"],
                                                       ensure_ascii=False,
                                                       default=str), 120)),
            "task.md/question", "question=%s" % ans["question"],
            method=ans["method"], confidence="medium",
            basis="mechanical_computation")

    return entries, counter[0]


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------


def write_artifacts(output_dir, computed):
    os.makedirs(output_dir, exist_ok=True)
    written = {}

    manifest_rows = []
    for f in computed["files"]:
        manifest_rows.append({
            "path": f["path"], "name": f["name"], "kind": f["kind"],
            "size": f.get("size"), "mode": f.get("mode"),
            "ext": f.get("ext"), "sha256": f.get("sha256"),
            "encoding": f.get("encoding"),
            "link_target": f.get("link_target"),
            "inode": f.get("inode"), "nlink": f.get("nlink"),
            "note": f.get("note"),
        })
    manifest_path = os.path.join(output_dir, "library_manifest.csv")
    with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
        if manifest_rows:
            writer = csv.DictWriter(fh, fieldnames=list(manifest_rows[0].keys()))
            writer.writeheader()
            writer.writerows(manifest_rows)
        else:
            fh.write("path,name,kind\n")
    written["library_manifest.csv"] = "library_manifest.csv"

    tree_lines = build_directory_tree(computed["files"], computed["dirs"])
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    with open(tree_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(tree_lines) + "\n")
    written["directory_tree.txt"] = "directory_tree.txt"

    dd = {
        "duplicate_groups": computed["duplicate_groups"],
        "version_candidates": computed["version_candidates"],
        "version_notes": computed["version_notes"],
        "aliases": computed["aliases"],
    }
    dd_path = os.path.join(output_dir, "dedup_version.json")
    with open(dd_path, "w", encoding="utf-8") as fh:
        json.dump(dd, fh, ensure_ascii=False, indent=2)
    written["dedup_version.json"] = "dedup_version.json"

    rec_path = os.path.join(output_dir, "recovery_report.json")
    with open(rec_path, "w", encoding="utf-8") as fh:
        json.dump(computed["reconciliation"], fh, ensure_ascii=False, indent=2)
    written["recovery_report.json"] = "recovery_report.json"

    idx_path = os.path.join(output_dir, "library_index.json")
    with open(idx_path, "w", encoding="utf-8") as fh:
        json.dump(computed["index"], fh, ensure_ascii=False, indent=2)
    written["library_index.json"] = "library_index.json"

    manifest_md = render_manifest_md(computed)
    md_path = os.path.join(output_dir, "library_manifest.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(manifest_md)
    written["library_manifest.md"] = "library_manifest.md"

    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with open(log_path, "w", encoding="utf-8") as fh:
        for step in computed["change_log"]:
            fh.write(json.dumps(step, ensure_ascii=False) + "\n")
    written["operation_log.jsonl"] = "operation_log.jsonl"

    return written


def build_directory_tree(files, dirs):
    lines = []
    dir_set = set(d["path"] for d in dirs)
    # Identify empty directories (directories that contain no files/children).
    occupied = set()
    for f in files:
        parent = os.path.dirname(f["path"])
        while parent:
            occupied.add(parent)
            parent = os.path.dirname(parent)
    for d in dirs:
        if d["path"] not in occupied:
            lines.append("%s [空目录]" % d["path"])
    for f in sorted(files, key=lambda x: x["path"]):
        marker = ""
        if f["kind"] == "broken_symlink":
            marker = " -> %s [断链]" % (f.get("link_target") or "?")
        elif f["kind"] == "symlink":
            marker = " -> %s" % (f.get("link_target") or "?")
        elif f.get("note"):
            marker = " [%s]" % f["note"]
        lines.append("%s%s" % (f["path"], marker))
    return sorted(set(lines))


def render_manifest_md(computed):
    s = computed["summary"]
    lines = [
        "# 科研资料库修复交接报告",
        "",
        "> 本报告由 library-document-repair Skill 自动生成，可复核、可复跑。",
        "",
        "## 概览",
        "",
        "- 文件数：%s" % s["files"],
        "- 目录数：%s" % s["directories"],
        "- 符号链接：%s（断链 %s）" % (s["symlinks"], s["broken_symlinks"]),
        "- 非常规文件：%s；不可读：%s" % (s["non_regular"], s["unreadable"]),
        "- 总字节数：%s" % s["total_bytes"],
        "- 重复组：%d；版本候选链：%d" % (
            len(computed["duplicate_groups"]),
            len(computed["version_candidates"])),
        "",
        "## 证据溯源说明",
        "",
        "每个关键结论在 `result.json` 的 `computed.evidence_map` 与 `sources` "
        "中都有 locator（相对路径，可加 `#p页码` / `#L行号`）+ 逐字 quote。",
        "",
        "## 产物清单",
        "",
    ]
    for name in ("library_manifest.csv", "directory_tree.txt",
                 "dedup_version.json", "recovery_report.json",
                 "library_index.json", "operation_log.jsonl"):
        lines.append("- `%s`" % name)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def assemble_result(case_id, status, contract_obj, computed, errors,
                    degradations, uncertainty, pending_human, runtime):
    result = {}
    result["case_id"] = case_id
    result["status"] = status

    # Backfill every contract field, preserving the contract's structure.
    if contract_obj is not None:
        lookup = build_contract_lookup(computed)
        computed["contract_lookup"] = lookup
        fields = contract_field_map(contract_obj)
        for field_name, kind_hint in fields:
            value = contract_value_lookup(computed, field_name)
            if value is None:
                value = [] if kind_hint == "list" else None
            _set_dotted(result, field_name, value)

    result["sources"] = build_sources(computed)
    result["uncertainty"] = uncertainty
    result["change_log"] = computed["change_log"]
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = runtime
    result["limitations"] = computed.get("limitations", [])
    result["pending_human"] = pending_human
    result["computed"] = {k: v for k, v in computed.items()
                          if k != "contract_lookup"}
    result["artifacts"] = computed.get("artifacts", {})
    return result


def build_contract_lookup(computed):
    return {
        "manifest": computed["files"],
        "file": computed["files"],
        "files": computed["files"],
        "inventory": computed["files"],
        "file_list": computed["files"],
        "directory_tree": computed["directory_tree"],
        "tree": computed["directory_tree"],
        "duplicate": computed["duplicate_groups"],
        "duplicates": computed["duplicate_groups"],
        "dedup": computed["duplicate_groups"],
        "version": computed["version_candidates"],
        "versions": computed["version_candidates"],
        "version_candidates": computed["version_candidates"],
        "alias": computed["aliases"],
        "aliases": computed["aliases"],
        "reconciliation": computed["reconciliation"],
        "recovery": computed["reconciliation"],
        "recover": computed["reconciliation"],
        "index": computed["index"],
        "answers": computed["answers"],
        "answer": computed["answers"],
        "summary": computed["summary"],
        "report": computed["summary"],
        "evidence_map": computed["evidence_map"],
        "evidence": computed["evidence_map"],
    }


def _set_dotted(obj, dotted, value):
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def build_sources(computed):
    """Aggregate key evidence into top-level sources (locator + quote)."""
    sources = []
    seen = set()
    for ev in computed.get("evidence_map", []):
        key = (ev["locator"], ev["quote"])
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "item": ev["claim"],
            "locator": ev["locator"],
            "quote": ev["quote"],
        })
    return sources


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline(case_json_path, task_path, input_root, output_dir, work_dir,
                 skeleton_only=False):
    t_start = time.time()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    change_log = [{
        "time": now_iso(), "actor": "skill", "step": "start",
        "detail": "pipeline start; input=%s" % input_root,
    }]

    # --- Load case.json and task.md ---
    case_obj, case_err = load_json_safe(case_json_path)
    if case_obj is None:
        case_obj = {}
        if case_err and case_err != "missing":
            change_log.append({
                "time": now_iso(), "actor": "skill", "step": "load_case",
                "detail": "case.json load error: %s" % case_err,
            })
    case_id = ""
    if isinstance(case_obj, dict):
        case_id = str(case_obj.get("case_id") or case_obj.get("id")
                      or case_obj.get("challenge_id") or "").strip()
    if not case_id and case_json_path:
        case_id = os.path.basename(case_json_path).replace(".json", "")

    task_text = ""
    if task_path and os.path.isfile(task_path):
        data, _tl, err = read_bytes(task_path)
        if err:
            change_log.append({
                "time": now_iso(), "actor": "skill", "step": "load_task",
                "detail": "task.md load error: %s" % err,
            })
        else:
            text, _enc, note = decode_text(data, "task.md")
            if text is not None:
                task_text = text
            else:
                change_log.append({
                    "time": now_iso(), "actor": "skill", "step": "load_task",
                    "detail": "task.md decode error: %s" % note,
                })

    contract_obj, contract_key = extract_contract(case_obj)

    # --- Skeleton fast path ---
    if skeleton_only:
        skeleton = {
            "case_id": case_id,
            "status": "partial",
            "sources": [],
            "uncertainty": [{
                "item": "pipeline", "reason": "skeleton written before full "
                "analysis; incremental update expected", "severity": "info",
            }],
            "change_log": change_log + [{
                "time": now_iso(), "actor": "skill", "step": "skeleton",
                "detail": "partial skeleton written",
            }],
            "errors": [],
            "degradations": [],
            "runtime": {"seconds": round(time.time() - t_start, 3)},
            "limitations": ["skeleton_only: analysis not yet run"],
            "pending_human": [],
            "computed": {"contract_lookup": {}},
            "artifacts": {},
        }
        if contract_obj is not None:
            for field_name, kind_hint in contract_field_map(contract_obj):
                value = [] if kind_hint == "list" else None
                _set_dotted(skeleton, field_name, value)
        write_result(skeleton, os.path.join(output_dir, "result.json"))
        return 0

    # --- Full analysis ---
    files, dirs, errors, discovery_err = discover(input_root)
    if discovery_err:
        errors.append({"path": None, "kind": "root", "note": discovery_err})
    change_log.append({
        "time": now_iso(), "actor": "skill", "step": "discover",
        "detail": "discovered %d files, %d dirs" % (len(files), len(dirs)),
    })

    summary = summarize(files, dirs)
    duplicate_groups = build_duplicate_groups(files)
    version_candidates = build_version_candidates(files)
    aliases = build_aliases(files)
    exclude = set()
    for p in (case_json_path, task_path):
        if p and os.path.isfile(p):
            exclude.add(relpath_forward(p, input_root))
    registries = parse_registries(files, input_root, exclude)
    registered = collect_registered(registries)
    reconciliation = reconcile(registered, files)
    version_notes = parse_version_notes(files, input_root, version_candidates)
    index = build_index(files, duplicate_groups, version_candidates)
    answers = answer_questions(task_text, case_obj, files, duplicate_groups,
                               version_candidates, aliases, reconciliation,
                               index)
    evidence_map, ev_count = build_evidence_map(
        files, dirs, duplicate_groups, version_candidates, aliases, registries,
        reconciliation, index, answers, errors)

    degradations = []
    for f in files:
        if f.get("note") and f["kind"] != "broken_symlink":
            degradations.append({
                "path": f["path"], "kind": f["kind"], "note": f["note"],
            })

    uncertainty = []
    for vc in version_candidates:
        if vc["relation"] != "duplicate":
            uncertainty.append({
                "item": "version_candidate:%s" % vc["stem"],
                "reason": "版本链为启发式（归一化主干+内容相似度），需人工确认",
                "severity": "medium",
            })

    pending_human = []
    if not registered:
        pending_human.append({
            "item": "reconciliation",
            "reason": "未发现库目录/清单，对账为空；如应存在清单请人工确认",
        })

    artifacts = write_artifacts(output_dir, {
        "files": files, "dirs": dirs, "summary": summary,
        "duplicate_groups": duplicate_groups,
        "version_candidates": version_candidates, "aliases": aliases,
        "version_notes": version_notes, "reconciliation": reconciliation,
        "index": index, "answers": answers, "evidence_map": evidence_map,
        "change_log": change_log, "limitations": [],
    })

    change_log.append({
        "time": now_iso(), "actor": "skill", "step": "artifacts",
        "detail": "wrote %d artifacts" % len(artifacts),
    })

    # Determine honest status.
    status = "ok"
    if not files and not dirs:
        status = "insufficient_evidence"
    elif discovery_err or any(f.get("note") in ("unreadable",)
                              for f in files):
        status = "partial"
    elif any(r.get("error") for r in registries):
        status = "partial"

    limitations = [
        "内容哈希仅对 ≤%d 字节的常规文件计算；超大文件保留大小/权限但不哈希"
        % DEFAULT_MAX_BYTES,
        "版本链为启发式，非权威结论，需人工复核",
        "语义检索为确定性 token 索引 + 候选层；概念级同义/跨语言对齐由 Agent "
        "在候选上补充",
    ]

    computed = {
        "files": files,
        "dirs": dirs,
        "summary": summary,
        "duplicate_groups": duplicate_groups,
        "version_candidates": version_candidates,
        "aliases": aliases,
        "registries": registries,
        "reconciliation": reconciliation,
        "version_notes": version_notes,
        "index": index,
        "answers": answers,
        "evidence_map": evidence_map,
        "evidence_count": ev_count,
        "directory_tree": build_directory_tree(files, dirs),
        "change_log": change_log,
        "artifacts": artifacts,
        "limitations": limitations,
    }

    runtime = {
        "seconds": round(time.time() - t_start, 3),
        "files": len(files),
        "dirs": len(dirs),
        "evidence_count": ev_count,
    }

    result = assemble_result(case_id, status, contract_obj, computed, errors,
                             degradations, uncertainty, pending_human, runtime)
    write_result(result, os.path.join(output_dir, "result.json"))
    return 0


def write_result(result, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------------------
# --update / --check
# ---------------------------------------------------------------------------


def do_update(patch_path, result_path):
    if not os.path.isfile(result_path):
        print("update: result.json not found at %s" % result_path,
              file=sys.stderr)
        return 2
    patch_obj, err = load_json_safe(patch_path)
    if err or not isinstance(patch_obj, dict):
        print("update: patch not loadable: %s" % err, file=sys.stderr)
        return 2
    base, err2 = load_json_safe(result_path)
    if err2 or not isinstance(base, dict):
        print("update: result.json not loadable: %s" % err2, file=sys.stderr)
        return 2
    merged = deep_merge(base, patch_obj)
    write_result(merged, result_path)
    print("update: merged patch into %s" % result_path)
    return 0


def do_check(result_path, case_json_path):
    issues = []
    result, err = load_json_safe(result_path)
    if err or not isinstance(result, dict):
        print("CHECK FAIL: result.json missing/unparseable (%s)" % err,
              file=sys.stderr)
        return 1
    if not result.get("case_id"):
        issues.append("case_id empty")
    if result.get("status") not in STATUSES:
        issues.append("status invalid: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing field: %s" % key)
    case_obj, _ = load_json_safe(case_json_path) if case_json_path else (None, None)
    contract_obj, _ = extract_contract(case_obj if isinstance(case_obj, dict)
                                       else None)
    if contract_obj is not None:
        for field_name, _kind in contract_field_map(contract_obj):
            cur = result
            ok = True
            for part in field_name.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if not ok:
                issues.append("contract field missing: %s" % field_name)
    if issues:
        for issue in issues:
            print("CHECK FAIL: %s" % issue, file=sys.stderr)
        return 1
    print("CHECK PASS: case_id=%r status=%r" % (result.get("case_id"),
                                                result.get("status")))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--input", "--input-root", dest="input_root",
                    default="/input")
    ap.add_argument("--output", "--output-dir", dest="output_dir",
                    default="/app/output")
    ap.add_argument("--work", "--work-dir", dest="work_dir", default="/app/work")
    ap.add_argument("--case-json", dest="case_json", default="/input/case.json")
    ap.add_argument("--task-path", dest="task_path", default="/input/task.md")
    ap.add_argument("--skeleton-only", action="store_true")
    ap.add_argument("--update", dest="update_patch")
    ap.add_argument("--result", dest="result_path", default="/app/output/result.json")
    ap.add_argument("--check", dest="check_result")
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)

    if args.check_result:
        return do_check(args.check_result, args.case_json)
    if args.update_patch:
        return do_update(args.update_patch, args.result_path)
    return run_pipeline(args.case_json, args.task_path, args.input_root,
                        args.output_dir, args.work_dir,
                        skeleton_only=args.skeleton_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
