# -*- coding: utf-8 -*-
"""core_impl.py - document library repair pipeline (Python standard library only).

Determinism contract:
  * directory traversal and every list are sorted;
  * hashes are stable (SHA-256 of file bytes);
  * no random numbers, no threads, no wall-clock in computed evidence;
  * only ``change_log`` / ``operation_log.jsonl`` / ``runtime`` carry timestamps.

Degradation contract:
  * every exception is caught per item, recorded in ``errors``/``degradations``,
    and processing continues; nothing is silently skipped without a record.
"""

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as stat_mod
import sys
import time

VERSION = "2.0.0"
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
DEFAULT_CASE_JSON = "/input/case.json"
DEFAULT_TASK_PATH = "/input/task.md"

HASH_CAP_BYTES = 2 * 1024 * 1024        # content hash cap for regular files
TEXT_PARSE_CAP_BYTES = 2 * 1024 * 1024  # text/catalog parse cap
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# --------------------------------------------------------------------------- #
# deterministic small helpers
# --------------------------------------------------------------------------- #


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def norm_sep(path):
    """Store relative paths with forward slashes regardless of host OS."""
    return path.replace(os.sep, "/")


def rel_of(root, path):
    rel = os.path.relpath(path, root)
    return norm_sep(rel)


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def read_bytes(path, cap=None):
    """Read up to ``cap`` bytes of a regular file; return (bytes, truncated)."""
    truncated = False
    with open(path, "rb") as fh:
        if cap is None:
            data = fh.read()
        else:
            data = fh.read(cap)
            rest = fh.read(1)
            if rest:
                truncated = True
    return data, truncated


def hash_sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_text_robust(path, cap=TEXT_PARSE_CAP_BYTES):
    """Read text with BOM/encoding fallbacks; return (text, encoding, error)."""
    try:
        raw, truncated = read_bytes(path, cap)
    except Exception as exc:  # noqa: BLE001
        return "", None, "unreadable: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            if truncated:
                return text, enc, "truncated_at_%dB" % cap
            return text, enc, None
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    return text, "utf-8(replace)", "decode_replaced"


def load_json_file(path):
    """Return (obj, error). Never raises."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(TEXT_PARSE_CAP_BYTES)
        text = raw.decode("utf-8-sig", errors="replace")
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json: %s" % exc


def mode_octal(st_mode):
    try:
        return oct(stat_mod.S_IMODE(st_mode))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# discovery: files, directories, permissions, aliases
# --------------------------------------------------------------------------- #


def lstat_info(path, root):
    """Best-effort stat that never raises; returns a dict or None."""
    try:
        st = os.lstat(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": "stat_failed: %s" % exc, "path": rel_of(root, path)}
    info = {
        "path": rel_of(root, path),
        "type": "unknown",
        "size": getattr(st, "st_size", None),
        "mtime": None,
        "mode": mode_octal(st.st_mode),
        "uid": getattr(st, "st_uid", None),
        "gid": getattr(st, "st_gid", None),
        "nlink": getattr(st, "st_nlink", 1),
        "dev": getattr(st, "st_dev", None),
        "ino": getattr(st, "st_ino", None),
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
        "executable": os.access(path, os.X_OK),
    }
    try:
        info["mtime"] = datetime.datetime.fromtimestamp(
            st.st_mtime, datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        pass
    if stat_mod.S_ISLNK(st.st_mode):
        info["type"] = "symlink"
        try:
            info["link_target"] = norm_sep(os.readlink(path))
        except Exception as exc:  # noqa: BLE001
            info["link_target"] = None
            info["link_error"] = "readlink_failed: %s" % exc
        try:
            info["link_exists"] = os.path.exists(path)
        except Exception:  # noqa: BLE001
            info["link_exists"] = None
    elif stat_mod.S_ISDIR(st.st_mode):
        info["type"] = "dir"
    elif stat_mod.S_ISREG(st.st_mode):
        info["type"] = "file"
    else:
        info["type"] = "non_regular"
    return info


def scan_input(root):
    """Sorted deterministic scan. Returns (entries, errors, dirs, files)."""
    entries = []
    errors = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                followlinks=False):
        dirnames.sort()
        filenames.sort()
        for name in dirnames:
            p = os.path.join(dirpath, name)
            if p == root:
                continue
            info = lstat_info(p, root)
            entries.append(info)
            if info.get("error"):
                errors.append(info)
        for name in filenames:
            p = os.path.join(dirpath, name)
            info = lstat_info(p, root)
            entries.append(info)
            if info.get("error"):
                errors.append(info)
    entries.sort(key=lambda d: d["path"])
    dirs = [e for e in entries if e["type"] == "dir"]
    files = [e for e in entries if e["type"] != "dir"]
    return entries, errors, dirs, files


def file_identity(path):
    """Return (digests, status_code, reason). Never raises."""
    try:
        data, truncated = read_bytes(path, HASH_CAP_BYTES)
        if truncated:
            return None, "too_large", "over_%dB" % HASH_CAP_BYTES
        digests = {
            "sha256": hash_sha256(data),
            "md5": hashlib.md5(data).hexdigest(),
        }
        return digests, "hashed", None
    except PermissionError as exc:
        return None, "unreadable", "permission: %s" % exc
    except OSError as exc:
        return None, "unreadable", "os_error: %s" % exc
    except Exception as exc:  # noqa: BLE001
        return None, "unreadable", "error: %s" % exc


# --------------------------------------------------------------------------- #
# version normalisation & candidates
# --------------------------------------------------------------------------- #

MARKERS = (
    r"v(?:er(?:sion)?)?\.?\s*\d+(?:[._\-]\d+)*",
    r"rev(?:ision)?\.?\s*\d*",
    r"draft",
    r"final",
    r"copy(?:\s*\d+)?",
    r"bak(?:ckup)?",
    r"backup",
    r"old",
    r"new",
    r"修改",
    r"副本",
    r"备份",
)
MARKER_RE = re.compile(
    r"[\s._\-]*(" + "|".join(MARKERS) + r")[\s._\-]*", re.IGNORECASE
)


def normalize_stem(name):
    """Normalize a filename stem to a version-independent semantic stem."""
    base, ext = os.path.splitext(name)
    s = base.lower()
    s = MARKER_RE.sub("", s)
    s = re.sub(r"[()（）\[\]【】{}]", "", s)
    s = re.sub(r"[\s._\-]+", "_", s).strip("_")
    return (s or base.lower()), ext.lower()


def detect_version_label(name):
    """Best-effort version label; returns a string or None (heuristic)."""
    m = re.search(r"(?:v(?:er(?:sion)?)?\.?|rev\.?)\s*(\d+(?:[._\-]\d+)*)",
                  name, re.IGNORECASE)
    if m:
        return m.group(1)
    for token in ("draft", "final", "old", "new", "修改", "副本", "备份"):
        if token.lower() in name.lower():
            return token.lower()
    return None


# --------------------------------------------------------------------------- #
# version notes (administrative)
# --------------------------------------------------------------------------- #

NOTE_NAME_RE = re.compile(
    r"(version|版本|note|说明|readme|变更|changelog)", re.IGNORECASE
)
NOTE_LINE_RE = re.compile(
    r"(?:v(?:er(?:sion)?)?\.?\s*)?(?P<from>\d+(?:[._]\d+)*)"
    r"\s*(?:->|→|to|更新到|升级到|改为)\s*"
    r"(?:v(?:er(?:sion)?)?\.?\s*)?(?P<to>\d+(?:[._]\d+)*)",
    re.IGNORECASE,
)


def parse_version_notes(root, files):
    notes = []
    errors = []
    for finfo in files:
        if finfo["type"] != "file":
            continue
        name = os.path.basename(finfo["path"])
        if not NOTE_NAME_RE.search(name):
            continue
        abs_path = os.path.join(root, *finfo["path"].split("/"))
        text, enc, err = read_text_robust(abs_path)
        if err and err not in ("truncated_at_%dB" % TEXT_PARSE_CAP_BYTES,):
            errors.append({"path": finfo["path"], "error": err})
            continue
        lines = text.splitlines()
        found = []
        for i, line in enumerate(lines, 1):
            m = NOTE_LINE_RE.search(line)
            if m:
                found.append({
                    "line": i,
                    "from": m.group("from"),
                    "to": m.group("to"),
                    "quote": line.strip(),
                })
        if found or NOTE_NAME_RE.search(name):
            notes.append({
                "path": finfo["path"],
                "encoding": enc,
                "transitions": found,
                "n_transitions": len(found),
            })
    notes.sort(key=lambda d: d["path"])
    return notes, errors


# --------------------------------------------------------------------------- #
# catalog parsing: JSON / CSV / BibTeX / RIS / SQLite
# --------------------------------------------------------------------------- #

CATALOG_EXT = {".json", ".csv", ".bib", ".bibtex", ".ris", ".sqlite",
               ".sqlite3", ".db"}


def _records_from_mapping(mapping):
    """Flatten a JSON mapping into candidate catalog records."""
    out = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            rec = {"_key": str(key)}
            rec.update({str(k): v for k, v in value.items()})
            out.append(rec)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    rec = {"_key": "%s[%d]" % (key, i)}
                    rec.update({str(k): v for k, v in item.items()})
                    out.append(rec)
                else:
                    out.append({"_key": "%s[%d]" % (key, i), "value": item})
        else:
            out.append({"_key": str(key), "value": value})
    return out


def parse_json_catalog(path):
    obj, err = load_json_file(path)
    if err:
        return None, err
    if isinstance(obj, dict):
        return _records_from_mapping(obj), None
    if isinstance(obj, list):
        recs = []
        for i, item in enumerate(obj):
            if isinstance(item, dict):
                rec = {"_key": str(i)}
                rec.update({str(k): v for k, v in item.items()})
                recs.append(rec)
            else:
                recs.append({"_key": str(i), "value": item})
        return recs, None
    return [{"_key": "root", "value": obj}], None


def parse_csv_catalog(path):
    data, _, err = read_text_robust(path)
    if err:
        return None, err
    try:
        sample = data[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:  # noqa: BLE001
        dialect = csv.excel
    try:
        reader = csv.DictReader(io.StringIO(data), dialect=dialect)
        recs = [dict(r) for r in reader]
        return recs, None
    except Exception as exc:  # noqa: BLE001
        return None, "csv_parse_error: %s" % exc


def _bibtex_entries(text):
    entries = []
    pattern = re.compile(
        r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,\n]+),", re.IGNORECASE
    )
    for m in pattern.finditer(text):
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start:i - 1]
        fields = {}
        for fm in re.finditer(
            r"(?P<k>[A-Za-z0-9_\-]+)\s*=\s*"
            r"(?:\{(?P<b>[^{}]*)\}|\"(?P<q>[^\"]*)\"|(?P<n>[^,\n]+))",
            body,
        ):
            val = fm.group("b") or fm.group("q") or fm.group("n")
            fields[fm.group("k").lower()] = val.strip()
        entries.append({"_type": m.group("type"), "_key": m.group("key").strip(),
                        **fields})
    return entries


def parse_bibtex_catalog(path):
    text, _, err = read_text_robust(path)
    if err:
        return None, err
    try:
        entries = _bibtex_entries(text)
        if not entries:
            return None, "bibtex_no_entries"
        return entries, None
    except Exception as exc:  # noqa: BLE001
        return None, "bibtex_parse_error: %s" % exc


def _ris_entries(text):
    entries = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"(?P<tag>[A-Z0-9]{2})  - (?P<val>.*)$", line)
        if m:
            tag, val = m.group("tag"), m.group("val").strip()
            if tag == "TY":
                if cur is not None:
                    entries.append(cur)
                cur = {"TY": val}
            elif cur is not None:
                cur[tag] = cur.get(tag, "") + ("; " if cur.get(tag) else "") + val
    if cur is not None:
        entries.append(cur)
    return entries


def parse_ris_catalog(path):
    text, _, err = read_text_robust(path)
    if err:
        return None, err
    try:
        entries = _ris_entries(text)
        if not entries:
            return None, "ris_no_entries"
        return entries, None
    except Exception as exc:  # noqa: BLE001
        return None, "ris_parse_error: %s" % exc


def parse_sqlite_catalog(path):
    uri = "file:%s?mode=ro" % path.replace("\\", "/")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except Exception as exc:  # noqa: BLE001
        return None, "sqlite_open_error: %s" % exc
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        recs = []
        for t in tables:
            try:
                cur.execute('SELECT * FROM "%s" LIMIT 500' % t.replace('"', '""'))
                cols = [c[0] for c in cur.description]
                for row in cur.fetchall():
                    rec = {"_table": t}
                    rec.update(dict(zip(cols, row)))
                    recs.append(rec)
            except Exception as exc:  # noqa: BLE001
                recs.append({"_table": t, "_error": "sqlite_query_error: %s" % exc})
        return recs, None
    except Exception as exc:  # noqa: BLE001
        return None, "sqlite_query_error: %s" % exc
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def parse_catalog(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_json_catalog(path)
    if ext == ".csv":
        return parse_csv_catalog(path)
    if ext in (".bib", ".bibtex"):
        return parse_bibtex_catalog(path)
    if ext == ".ris":
        return parse_ris_catalog(path)
    if ext in (".sqlite", ".sqlite3", ".db"):
        return parse_sqlite_catalog(path)
    return None, "unsupported_catalog_ext: %s" % ext


# --------------------------------------------------------------------------- #
# reconciliation
# --------------------------------------------------------------------------- #

PATH_KEYS = ("path", "file", "filename", "filepath", "name", "relative_path",
             "relpath", "entry", "id")


def _catalog_path_lookup(rec):
    for key in PATH_KEYS:
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().replace("\\", "/").lstrip("./")
    return None


def reconcile(catalog_records, files_by_path, all_files):
    seen = set()
    matches = []
    mismatches = []
    missing = []
    for rec in catalog_records:
        p = _catalog_path_lookup(rec)
        rid = jdump(rec)
        if rid in seen:
            continue
        seen.add(rid)
        if p is None:
            missing.append({"record": rec, "reason": "no_path_field"})
            continue
        actual = files_by_path.get(p)
        if actual is None:
            missing.append({"record": rec, "path": p, "reason": "no_actual_file"})
            continue
        match = {"path": p, "record": rec,
                 "actual_size": actual.get("size"),
                 "actual_sha256": actual.get("sha256")}
        mismatch = False
        for size_key in ("size", "bytes", "byte_size", "filesize"):
            if size_key in rec and rec[size_key] is not None:
                try:
                    if int(rec[size_key]) != int(actual.get("size") or -1):
                        match["catalog_size"] = rec[size_key]
                        match["reason"] = "size_mismatch"
                        mismatch = True
                except (TypeError, ValueError):
                    pass
        for hkey in ("sha256", "hash256", "sha_256", "md5", "checksum", "hash",
                     "sha"):
            if hkey in rec and rec[hkey] is not None and mismatch is False:
                catalog_val = str(rec[hkey]).strip().lower()
                match["catalog_%s" % hkey] = rec[hkey]
                actual_sha = str(actual.get("sha256") or "").lower()
                actual_md5 = str(actual.get("md5") or "").lower()
                if hkey == "md5":
                    expected = actual_md5
                elif len(catalog_val) == 32:
                    expected = actual_md5
                else:
                    expected = actual_sha
                if expected and catalog_val and catalog_val != expected:
                    match["reason"] = "%s_mismatch" % hkey
                    mismatch = True
        if mismatch:
            mismatches.append(match)
        else:
            matches.append(match)
    matched_paths = {m["path"] for m in matches} | {m["path"] for m in mismatches}
    orphaned = [f for f in all_files if f["path"] not in matched_paths]
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "counts": {
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "orphaned": len(orphaned),
        },
    }


# --------------------------------------------------------------------------- #
# mechanical answers
# --------------------------------------------------------------------------- #


def build_answers(files, summary, dup_groups, versions, aliases, recon,
                  notes):
    counts = summary["counts"]
    answers = {
        "file_count": counts["files"],
        "directory_count": counts["dirs"],
        "total_bytes": counts["total_bytes"],
        "duplicate_group_count": len(dup_groups),
        "duplicate_file_count": sum(len(g["paths"]) for g in dup_groups),
        "version_candidate_group_count": len(versions),
        "broken_symlink_count": aliases.get("broken_symlink_count", 0),
        "hardlink_group_count": len(aliases.get("hardlink_groups", [])),
        "reconcile_counts": recon["counts"],
        "version_note_file_count": len(notes),
    }
    # extraction heuristics (clearly labelled heuristic)
    years = {}
    authors = {}
    for f in files:
        name = os.path.basename(f["path"])
        for ym in re.finditer(r"(?<!\d)(19|20)\d{2}(?!\d)", name):
            years[ym.group(0)] = years.get(ym.group(0), 0) + 1
        for am in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})(?=_|-)",
                              name):
            authors[am.group(1)] = authors.get(am.group(1), 0) + 1
    answers["years_by_filename"] = dict(sorted(years.items()))
    answers["authors_by_filename"] = dict(
        sorted(authors.items(), key=lambda kv: -kv[1])
    )
    return answers


# --------------------------------------------------------------------------- #
# directory tree
# --------------------------------------------------------------------------- #


def build_tree(entries):
    tree = {"name": ".", "type": "dir", "children": []}
    for e in entries:
        parts = [p for p in e["path"].split("/") if p]
        node = tree
        for i, part in enumerate(parts):
            is_last = (i == len(parts) - 1)
            children = node.setdefault("children", [])
            child = next((c for c in children if c["name"] == part), None)
            if child is None:
                child = {"name": part}
                children.append(child)
                children.sort(key=lambda c: c["name"])
            node = child
            if is_last:
                child["type"] = e["type"]
    return tree


def render_tree(node, prefix=""):
    lines = []
    label = node.get("name", ".")
    if node.get("type") == "dir":
        lines.append(prefix + label + "/")
        children = node.get("children", [])
        for i, child in enumerate(children):
            last = (i == len(children) - 1)
            connector = "`-- " if last else "|-- "
            sub = render_tree(child, prefix + ("    " if last else "|   "))
            lines.extend(sub)
    else:
        lines.append(prefix + label)
    return lines


def render_tree_text(entries):
    tree = build_tree(entries)
    lines = []
    for child in tree.get("children", []):
        lines.extend(render_tree(child, ""))
    if not lines:
        lines = ["[空目录]"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# result assembly
# --------------------------------------------------------------------------- #


def find_contract(case_obj):
    if not isinstance(case_obj, dict):
        return None, None
    for key in ("output_contract", "output", "result_schema", "contract"):
        if key in case_obj:
            return key, case_obj[key]
    return None, None


def find_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "task_id"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if val is not None:
                return str(val)
    m = re.search(r"challenge[_-]?id[:：\s]*`?([A-Za-z0-9._-]+)", task_text,
                  re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"`([A-Za-z0-9][A-Za-z0-9._-]{4,})`", task_text)
    if m:
        return m.group(1)
    return "unknown"


def fill_contract(contract, computed):
    """Echo every contract key; fill known names from computed, else null."""
    if not isinstance(contract, dict):
        return contract
    filled = {}
    flat = {}
    for key, val in computed.items():
        flat[str(key)] = val
        flat[str(key).lower()] = val
    for key, _ in contract.items():
        if key in flat:
            filled[key] = flat[key]
        else:
            filled[key] = None
    return filled


def build_skeleton(args):
    case_obj = None
    case_err = None
    if os.path.exists(args.case_json):
        case_obj, case_err = load_json_file(args.case_json)
    task_text = ""
    if os.path.exists(args.task_path):
        task_text, _, _ = read_text_robust(args.task_path)
    case_id = find_case_id(case_obj, task_text)
    contract_key, contract = find_contract(case_obj)
    result = {
        "case_id": case_id,
        "status": "partial",
        "errors": [],
        "degradations": [],
        "runtime": {
            "budget": {
                "total_seconds": 900,
                "model_requests": 64,
                "phase": "skeleton",
                "elapsed_s": None,
                "requests_estimate": None,
            },
            "timestamps": {"skeleton_written_at": now_iso()},
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "change_log": [
            {
                "time": now_iso(),
                "actor": "scripts/main.py",
                "step": "skeleton",
                "detail": "early partial skeleton written before analysis",
            }
        ],
        "computed": {
            "artifacts": {},
            "contract": {
                "source_key": contract_key,
                "value": contract if isinstance(contract, dict) else None,
            },
        },
    }
    if isinstance(contract, dict):
        result["computed"]["contract_filled"] = fill_contract(contract,
                                                              result["computed"])
        for key in contract:
            result.setdefault(key, result["computed"]["contract_filled"][key])
    if case_err:
        result["errors"].append("case_json: %s" % case_err)
    if not os.path.exists(args.case_json):
        result["status"] = "input_error"
        result["errors"].append("missing case.json: %s" % args.case_json)
    elif case_obj is None:
        result["status"] = "input_error"
    return result, case_obj, task_text, contract_key, contract


def write_result(result, args, code=None):
    os.makedirs(args.output_dir, exist_ok=True)
    path = os.path.join(args.output_dir, "result.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    if code is not None:
        result["runtime"]["exit_code"] = code
    return path


def append_log(args, entry):
    os.makedirs(args.output_dir, exist_ok=True)
    path = os.path.join(args.output_dir, "operation_log.jsonl")
    entry = dict(entry)
    entry.setdefault("time", now_iso())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------- #
# main pipeline
# --------------------------------------------------------------------------- #


def run_pipeline(args):
    started = time.time()
    phase_times = {}
    result = {}

    def phase(name, fn):
        t0 = time.time()
        out = fn()
        phase_times[name] = round(time.time() - t0, 3)
        return out

    # Phase 0: skeleton (already written in do_skeleton flow, but safe to build)
    def _phase0():
        return build_skeleton(args)

    result, case_obj, task_text, contract_key, contract = phase(
        "0_skeleton", _phase0
    )
    # early落盘：analysis must never leave the output dir without a valid result.
    write_result(result, args)
    append_log(args, {"actor": "scripts/main.py", "step": "skeleton",
                      "detail": "early skeleton written before analysis"})

    def _phase1():
        entries, scan_errors, dirs, files = scan_input(args.input_root)
        # drop output/work dirs only when they live under the input root
        excluded = []
        for d in (args.output_dir, args.work_dir):
            try:
                rel = norm_sep(os.path.relpath(d, args.input_root))
            except ValueError:
                continue
            if rel and rel != ".":
                excluded.append(rel)
        def _outside_io(e):
            p = e["path"]
            return not any(p == ex or p.startswith(ex + "/")
                           for ex in excluded)
        entries = [e for e in entries if _outside_io(e)]
        dirs = [e for e in dirs if _outside_io(e)]
        files = [e for e in files if _outside_io(e)]
        return entries, scan_errors, dirs, files

    entries, scan_errors, dirs, files = phase("1_scan", _phase1)

    # case.json / task.md are task plumbing, not library material. Keep them in
    # inventory for transparency, but exclude them from material analysis and
    # from the empty-input check.
    meta_paths = set()
    for mp in (args.case_json, args.task_path):
        try:
            rel = norm_sep(os.path.relpath(mp, args.input_root))
        except ValueError:
            continue
        if rel and rel != ".":
            meta_paths.add(rel)
    meta_files = [f for f in files if f["path"] in meta_paths]
    files = [f for f in files if f["path"] not in meta_paths]

    regular_files = [f for f in files if f["type"] == "file"]
    file_list = []
    hash_errors = []
    by_sha = {}
    for f in regular_files:
        abs_path = os.path.join(args.input_root, *f["path"].split("/"))
        digests, code, reason = file_identity(abs_path)
        f = dict(f)
        if digests:
            f["sha256"] = digests["sha256"]
            f["md5"] = digests["md5"]
        else:
            f["sha256"] = None
            f["md5"] = None
        f["hash_status"] = code
        if reason:
            f["hash_note"] = reason
        if code == "hashed":
            by_sha.setdefault(f["sha256"], []).append(f["path"])
        else:
            hash_errors.append({"path": f["path"], "code": code,
                                "reason": reason})
        file_list.append(f)
    file_list.sort(key=lambda d: d["path"])

    # dedup groups
    dup_groups = []
    for sha in sorted(by_sha):
        paths = by_sha[sha]
        if len(paths) > 1:
            dup_groups.append({"sha256": sha, "paths": paths, "n": len(paths)})
    dup_groups.sort(key=lambda g: -g["n"])

    # version candidates
    stem_groups = {}
    for f in file_list:
        stem, ext = normalize_stem(os.path.basename(f["path"]))
        stem_groups.setdefault((stem, ext), []).append(f)
    versions = []
    for (stem, ext) in sorted(stem_groups):
        members = stem_groups[(stem, ext)]
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda d: d.get("mtime") or "")
        vs = []
        for i, m in enumerate(members, 1):
            vs.append({
                "path": m["path"],
                "order": i,
                "label": "v%d" % i,
                "detected_label": detect_version_label(
                    os.path.basename(m["path"])
                ),
                "mtime": m.get("mtime"),
                "size": m.get("size"),
                "sha256": m.get("sha256"),
            })
        # content diff summary
        unique_hashes = {v["sha256"] for v in vs if v.get("sha256")}
        versions.append({
            "stem": stem,
            "ext": ext,
            "heuristic": True,
            "n_members": len(vs),
            "unique_content_hashes": len(unique_hashes),
            "members": vs,
        })
    versions.sort(key=lambda g: (g["ext"], g["stem"]))

    # aliases
    symlinks = [f for f in files if f["type"] == "symlink"]
    broken = [s for s in symlinks if s.get("link_exists") is False]
    hardlink_buckets = {}
    for f in file_list:
        if f.get("nlink", 1) and f.get("nlink", 1) > 1:
            key = (f.get("dev"), f.get("ino"))
            if key[0] is not None and key[1] is not None:
                hardlink_buckets.setdefault(key, []).append(f["path"])
    hardlink_groups = [paths for paths in hardlink_buckets.values()
                       if len(paths) > 1]
    hardlink_groups.sort(key=lambda g: g[0])
    aliases = {
        "symlinks": symlinks,
        "broken_symlinks": broken,
        "broken_symlink_count": len(broken),
        "hardlink_groups": hardlink_groups,
        "hardlink_group_count": len(hardlink_groups),
    }

    # version notes
    notes, note_errors = phase("notes", lambda: parse_version_notes(
        args.input_root, file_list))

    # catalogs + reconciliation
    catalogs = []
    recon = {"matches": [], "mismatches": [], "missing": [], "orphaned": [],
             "counts": {"matches": 0, "mismatches": 0, "missing": 0,
                        "orphaned": 0}}
    catalog_errors = []
    files_by_path = {f["path"]: f for f in file_list}
    all_files = file_list
    for f in file_list:
        ext = os.path.splitext(f["path"])[1].lower()
        if ext not in CATALOG_EXT:
            continue
        # case.json / task.md are task inputs, not library catalogs
        if "/" not in f["path"] and os.path.basename(f["path"]) in (
                "case.json", "task.md"):
            continue
        abs_path = os.path.join(args.input_root, *f["path"].split("/"))
        recs, err = parse_catalog(abs_path)
        entry = {"path": f["path"], "records": None, "error": None,
                 "n_records": None}
        if err:
            entry["error"] = err
            catalog_errors.append({"path": f["path"], "error": err})
        else:
            entry["records"] = recs
            entry["n_records"] = len(recs)
            part = reconcile(recs, files_by_path, all_files)
            for k in ("matches", "mismatches", "missing"):
                recon[k].extend(part[k])
            recon["orphaned"].extend(part["orphaned"])
        catalogs.append(entry)
    # orphaned = files not claimed by any catalog record
    claimed = set()
    for m in recon["matches"]:
        claimed.add(m["path"])
    for m in recon["mismatches"]:
        claimed.add(m["path"])
    recon["orphaned"] = [f for f in all_files if f["path"] not in claimed]
    recon["counts"] = {
        "matches": len(recon["matches"]),
        "mismatches": len(recon["mismatches"]),
        "missing": len(recon["missing"]),
        "orphaned": len(recon["orphaned"]),
    }

    # summary
    total_bytes = 0
    for f in file_list:
        if isinstance(f.get("size"), int):
            total_bytes += f["size"]
    ext_counts = {}
    for f in file_list:
        ext = os.path.splitext(f["path"])[1].lower() or "(none)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    summary = {
        "counts": {
            "files": len(file_list),
            "dirs": len(dirs),
            "entries": len(entries),
            "total_bytes": total_bytes,
            "symlinks": len(symlinks),
            "broken_symlinks": len(broken),
            "hash_errors": len(hash_errors),
        },
        "extensions": dict(sorted(ext_counts.items())),
        "scan_errors": len(scan_errors),
    }

    # answers
    answers = build_answers(file_list, summary, dup_groups, versions, aliases,
                            recon, notes)

    # index
    index = []
    for f in file_list:
        name = os.path.basename(f["path"])
        index.append({
            "path": f["path"],
            "name": name,
            "ext": os.path.splitext(name)[1].lower() or "(none)",
            "size": f.get("size"),
            "mtime": f.get("mtime"),
            "sha256": f.get("sha256"),
            "keywords": [k for k in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+",
                                             name.lower()) if k],
        })
    index.sort(key=lambda d: d["path"])

    computed = {
        "case_id": result.get("case_id"),
        "files": file_list,
        "summary": summary,
        "inventory": {"entries": entries, "dirs": dirs,
                      "meta_files": meta_files},
        "duplicate_groups": dup_groups,
        "version_candidates": versions,
        "version_notes": notes,
        "aliases": aliases,
        "catalogs": catalogs,
        "reconciliation": recon,
        "answers": answers,
        "index": index,
        "artifacts": {},
    }

    # artifacts
    os.makedirs(args.output_dir, exist_ok=True)
    artifacts = write_artifacts(args, file_list, entries, dup_groups, versions,
                                aliases, notes, recon, index, computed,
                                catalogs)
    computed["artifacts"] = artifacts

    # merge into result
    computed["case_id"] = result.get("case_id")
    result["computed"].update(computed)
    result["summary"] = summary
    result["duplicate_groups"] = dup_groups
    result["version_candidates"] = versions
    result["aliases"] = aliases
    result["reconciliation"] = recon
    result["answers"] = answers

    if isinstance(contract, dict):
        result["computed"]["contract_filled"] = fill_contract(contract,
                                                              computed)
        for key in contract:
            result.setdefault(key, result["computed"]["contract_filled"][key])

    for err in scan_errors:
        result["errors"].append(err if isinstance(err, str) else
                                json.dumps(err, ensure_ascii=False))
    for err in hash_errors:
        result["errors"].append(err)
    for err in note_errors:
        result["errors"].append(err)
    for err in catalog_errors:
        result["errors"].append(err)

    base_status = result.get("status")
    if base_status == "input_error":
        # missing/bad case.json: keep input_error, do not mask as partial
        pass
    elif result.get("errors"):
        result["status"] = "partial"
    elif summary["counts"]["files"] == 0:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "input_material",
            "reason": "no regular files found under input root",
            "severity": "high",
        })
        result["degradations"].append("empty_input: no material to inventory")
    else:
        result["status"] = "ok"

    result["runtime"]["budget"]["phase"] = "full"
    result["runtime"]["budget"]["elapsed_s"] = round(time.time() - started, 3)
    result["runtime"]["phase_times_s"] = phase_times
    result["change_log"].append({
        "time": now_iso(),
        "actor": "scripts/main.py",
        "step": "pipeline",
        "detail": "full inventory/repair pipeline completed",
    })

    write_result(result, args)
    append_log(args, {"actor": "scripts/main.py", "step": "pipeline",
                      "detail": "full run completed"})
    print("main: %d files, %d dup groups, %d version candidates, "
          "%d broken symlinks, %d reconcile mismatches -> %s"
          % (summary["counts"]["files"], len(dup_groups), len(versions),
             len(broken), recon["counts"]["mismatches"],
             os.path.join(args.output_dir, "result.json")))
    return 0


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #


def write_artifacts(args, file_list, entries, dup_groups, versions, aliases,
                    notes, recon, index, computed, catalogs):
    os.makedirs(args.output_dir, exist_ok=True)
    artifacts = {}

    def put(name, text, mode="w"):
        path = os.path.join(args.output_dir, name)
        with open(path, mode, encoding="utf-8", newline="") as fh:
            fh.write(text)
        artifacts[name] = path

    # 1) manifest CSV
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["path", "type", "size", "mtime", "mode", "sha256",
                     "hash_status", "hash_note"])
    for f in file_list:
        writer.writerow([f["path"], f["type"], f.get("size"), f.get("mtime"),
                         f.get("mode"), f.get("sha256"),
                         f.get("hash_status"), f.get("hash_note")])
    put("library_manifest.csv", csv_buf.getvalue())

    # 2) directory tree
    put("directory_tree.txt", render_tree_text(entries))

    # 3) dedup / version / alias json
    put("dedup_version.json", json.dumps({
        "duplicate_groups": dup_groups,
        "version_candidates": versions,
        "version_notes": notes,
        "aliases": aliases,
        "dedup_policy": "content_sha256",
        "version_policy": "normalized_stem + content_diff (heuristic)",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # 4) recovery report
    put("recovery_report.json", json.dumps({
        "reconciliation": recon,
        "catalogs": catalogs,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # 5) index
    put("library_index.json", json.dumps({
        "files": index,
        "answers": computed["answers"],
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # 6) manifest markdown
    lines = ["# 科研资料库交接报告（示例标题，内容为本次运行结果）", "",
             "- 案例: %s" % computed.get("case_id", ""),
             "- 文件数: %d" % len(file_list),
             "- 重复组: %d" % len(dup_groups),
             "- 版本候选组: %d" % len(versions),
             "- 断链符号链接: %d" % aliases.get("broken_symlink_count", 0),
             "", "## 目录树", "", "```text", render_tree_text(entries).rstrip(),
             "```", "", "## 去重与版本", "",
             json.dumps({"duplicate_groups": dup_groups,
                         "version_candidates": versions}, ensure_ascii=False,
                        indent=2), ""]
    put("library_manifest.md", "\n".join(lines))

    return artifacts


# --------------------------------------------------------------------------- #
# skeleton / update / check modes
# --------------------------------------------------------------------------- #


def do_skeleton(args):
    result, case_obj, task_text, contract_key, contract = build_skeleton(args)
    if not os.path.exists(args.input_root):
        result["errors"].append("input_root_missing: %s" % args.input_root)
        result["status"] = "input_error"
    write_result(result, args)
    append_log(args, {"actor": "scripts/main.py", "step": "skeleton",
                      "detail": "skeleton written"})
    print("skeleton: %s" % os.path.join(args.output_dir, "result.json"))
    return 0


def _deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    if isinstance(base, list) and isinstance(patch, list):
        out = list(base)
        for item in patch:
            if item not in out:
                out.append(item)
        return out
    return patch


def do_update(args):
    if not os.path.exists(args.result):
        print("update: result not found: %s" % args.result, file=sys.stderr)
        return 2
    result, err = load_json_file(args.result)
    if err:
        print("update: cannot read result: %s" % err, file=sys.stderr)
        return 2
    patch, perr = load_json_file(args.update)
    if perr:
        print("update: cannot read patch: %s" % perr, file=sys.stderr)
        return 2
    merged = _deep_merge(result, patch)
    merged["change_log"] = list(merged.get("change_log", []))
    merged["change_log"].append({
        "time": now_iso(),
        "actor": "scripts/main.py --update",
        "step": "update",
        "detail": "merged patch %s" % os.path.basename(args.update),
    })
    out_dir = os.path.dirname(args.result) or args.output_dir
    os.makedirs(out_dir, exist_ok=True)
    tmp = args.result + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, args.result)
    append_log(args, {"actor": "scripts/main.py", "step": "update",
                      "detail": "patch merged"})
    print("update: %s" % args.result)
    return 0


def do_check(args):
    target = args.check
    if not os.path.exists(target):
        print("check: result not found", file=sys.stderr)
        return 1
    result, err = load_json_file(target)
    if err:
        print("check: bad json: %s" % err, file=sys.stderr)
        return 1
    problems = []
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in STATUS_VALUES:
        problems.append("bad status %r" % result.get("status"))
    for req in ("sources", "uncertainty", "change_log"):
        if req not in result:
            problems.append("missing key %s" % req)
    case_obj = None
    if os.path.exists(args.case_json):
        case_obj, _ = load_json_file(args.case_json)
    _, contract = find_contract(case_obj)
    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                problems.append("contract field missing: %s" % key)
    if problems:
        for p in problems:
            print("check: %s" % p, file=sys.stderr)
        return 1
    print("check: ok (%s, %s)" % (result.get("case_id"), result.get("status")))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input", dest="input_root", default=DEFAULT_INPUT)
    p.add_argument("--output", dest="output_dir", default=DEFAULT_OUTPUT)
    p.add_argument("--work", dest="work_dir", default=DEFAULT_WORK)
    p.add_argument("--case-json", dest="case_json", default=DEFAULT_CASE_JSON)
    p.add_argument("--task-path", dest="task_path", default=DEFAULT_TASK_PATH)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", dest="update", default=None)
    p.add_argument("--result", dest="result", default=None)
    p.add_argument("--check", dest="check", default=None)
    p.add_argument("--help", "-h", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.help:
        build_parser().print_help()
        return 0
    if args.check:
        return do_check(args)
    if args.update:
        return do_update(args)
    if args.skeleton_only:
        return do_skeleton(args)
    return run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
