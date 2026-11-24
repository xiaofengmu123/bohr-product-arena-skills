"""core_impl.py — document library repair pipeline (Python stdlib only, deterministic).

Design goals
------------
* Read-only over /input: inventory, content SHA-256 dedup, version candidates,
  symlink/hardlink aliases, administrative version notes, library catalogs
  (JSON/CSV/BibTeX/RIS/SQLite), reconciliation, searchable index and Q&A.
* Deterministic: traversal sorted by name, stable hashing, no randomness.
* Degrades gracefully: never crashes on bad JSON / bad encoding / missing
  permission / huge files / broken symlinks / unreadable SQLite.
* Honest status: ok | partial | insufficient_evidence | input_error.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_HASH_BYTES = 2 * 1024 * 1024          # skip content hash above 2 MiB
MAX_TEXT_BYTES = 2 * 1024 * 1024          # read cap for text preview / diff
PREVIEW_CHARS = 240                        # characters kept in text preview
CHUNK = 1 << 20                            # 1 MiB read chunk

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

VERSION_RE = re.compile(
    r"(?:[._\- ]?(?:v|ver|version|rev|r)?[._\-]?\d+[._\-]?\d*"
    r"|[._\- ]?(?:final|draft|old|new|copy|clean|backup|bak|副本|备份|初稿|终稿|新版|旧版)"
    r"|\(\d+\)|\[\d+\]|\s*-\s*\d+)$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(?:20|19)\d{2}[-_.]?\d{0,2}[-_.]?\d{0,2}")

CATALOG_EXT = {".json": "json", ".csv": "csv", ".bib": "bibtex",
               ".ris": "ris", ".db": "sqlite", ".sqlite": "sqlite",
               ".sqlite3": "sqlite"}

NOTE_NAME_RE = re.compile(r"version|change|changelog|history|note|版本|说明|修订|记录",
                          re.IGNORECASE)
QUESTION_NAME_RE = re.compile(r"question|query|ask|search|检索|问题|查询", re.IGNORECASE)

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract",
                 "outputContract", "resultSchema")
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "armiId", "caseId")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _json_default(obj):
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return str(obj)


def json_dump(path, obj):
    """Write JSON with deterministic key order; fall back to ASCII on surrogates."""
    try:
        data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True,
                          default=_json_default)
        data.encode("utf-8")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
    except (UnicodeEncodeError, UnicodeError, OSError):
        data = json.dumps(obj, ensure_ascii=True, indent=2, sort_keys=True,
                          default=_json_default)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)


def json_load(path):
    """Load JSON tolerantly (utf-8-sig then utf-8); return (obj, error)."""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except (json.JSONDecodeError, OSError) as ex:
            return None, "%s: %s" % (type(ex).__name__, ex)
    return None, "unreadable_encoding"


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read text with encoding fallback; return (text, encoding, error)."""
    try:
        raw = open(path, "rb").read(max_bytes)
    except OSError as ex:
        return "", None, "%s: %s" % (type(ex).__name__, ex)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8(replace)", None


def safe(v):
    """Return a JSON-safe str (surrogates replaced)."""
    if v is None:
        return None
    s = str(v)
    try:
        s.encode("utf-8")
        return s
    except UnicodeError:
        return s.encode("utf-8", "replace").decode("utf-8")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def rel_posix(path, root):
    """POSIX-style relative path for cross-platform determinism."""
    try:
        r = os.path.relpath(path, root)
    except ValueError:
        r = path
    return r.replace(os.sep, "/")


def mode_str(mode):
    return "0o%o" % (stat.S_IMODE(mode))


def parse_iso_mtime(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def norm_text(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(s).lower()).strip()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Document library repair: inventory, dedup, versions, "
                    "aliases, reconciliation, index, Q&A and artifacts.")
    p.add_argument("--case-json", default=os.path.join(DEFAULT_INPUT, "case.json"))
    p.add_argument("--task-path", default=os.path.join(DEFAULT_INPUT, "task.md"))
    p.add_argument("--input-root", default=DEFAULT_INPUT)
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    p.add_argument("--work-dir", default=DEFAULT_WORK)
    p.add_argument("--skeleton-only", action="store_true",
                   help="write a partial result skeleton immediately and exit")
    p.add_argument("--update", metavar="PATCH.json",
                   help="deep-merge a patch file into an existing result.json")
    p.add_argument("--result", default=None,
                   help="result.json path for --update / --check "
                        "(default: <output-dir>/result.json)")
    p.add_argument("result_path", nargs="?", default=None,
                   help="positional result.json path (equivalent to --result)")
    p.add_argument("--check", action="store_true",
                   help="validate an existing result.json")
    return p


# ---------------------------------------------------------------------------
# Skeleton (phase 0): always land a valid partial result first
# ---------------------------------------------------------------------------

def load_case(case_path, task_path, input_root):
    """Return (case_data, task_text, case_id, contract, errors)."""
    errors = []
    case_data = None
    task_text = ""
    case_id = None
    contract = None

    if os.path.isfile(case_path):
        case_data, err = json_load(case_path)
        if err:
            errors.append({"item": "case.json", "error": err})
            case_data = None
    else:
        errors.append({"item": "case.json", "error": "missing"})

    if os.path.isfile(task_path):
        task_text, _, err = read_text(task_path)
        if err:
            errors.append({"item": "task.md", "error": err})
    else:
        errors.append({"item": "task.md", "error": "missing"})

    # case_id
    if isinstance(case_data, dict):
        for k in CASE_ID_KEYS:
            if case_data.get(k):
                case_id = str(case_data[k])
                break
    if not case_id and task_text:
        m = re.search(r"challenge[-_]?id[:：\s]*[`'\"]?([A-Za-z0-9\-_.]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        m = re.search(r"challenge[-_]?([A-Za-z0-9\-_.]+)", task_text)
        if m:
            case_id = m.group(0)
    if not case_id:
        case_id = os.path.basename(os.path.abspath(input_root).rstrip("/\\")) or "unknown"

    # output contract
    if isinstance(case_data, dict):
        for k in CONTRACT_KEYS:
            if k in case_data and case_data[k] not in (None, "", [], {}):
                contract = case_data[k]
                break
    if contract is None and task_text:
        m = re.search(r'"?(output_contract|result_schema)"?\s*[:=]\s*(\{.*?\})', task_text,
                      re.IGNORECASE | re.DOTALL)
        if m:
            try:
                contract = json.loads(m.group(2))
            except json.JSONDecodeError:
                contract = None

    return case_data, task_text, case_id, contract, errors


def contract_fields(contract):
    """Return an ordered list of contract field names, however it is shaped."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        out = []
        for item in contract:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(item.keys())
        return out
    if isinstance(contract, str) and contract.strip():
        return [contract.strip()]
    return []


def build_skeleton(case_id, contract, errors, note=""):
    result = {
        "case_id": case_id,
        "status": STATUS_PARTIAL,
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "pending_human": [],
        "errors": [{"item": e["item"], "error": e["error"]} for e in errors],
        "degradations": [],
        "limitations": [],
        "runtime": {"seconds": 0.0, "note": note},
        "computed": {},
    }
    for name in contract_fields(contract):
        if name in result:
            result["computed"]["_contract_%s" % name] = None
        else:
            result[name] = None
    result["change_log"].append({
        "time": now_iso(), "actor": "finalize.py", "step": "skeleton",
        "detail": "partial result skeleton written",
    })
    return result


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def scan_tree(input_root, skip_prefixes=()):
    """Recursively scan input_root; returns (entries, dirs, scan_errors).

    skip_prefixes: absolute paths (output/work dirs) that must never be
    inventoried even when they sit inside input_root.
    """
    entries = []
    dirs = []
    errors = []
    root = os.path.abspath(input_root)
    skips = tuple(os.path.abspath(s) for s in skip_prefixes if s)

    def skipped(path):
        ap = os.path.abspath(path)
        for s in skips:
            try:
                if os.path.commonpath([ap, s]) == s:
                    return True
            except ValueError:
                pass
        return False

    def visit(dpath):
        try:
            names = sorted(os.listdir(dpath))
        except OSError as ex:
            errors.append({"locator": rel_posix(dpath, root) or ".",
                           "error": "listdir: %s" % ex})
            return
        for name in names:
            full = os.path.join(dpath, name)
            if skipped(full):
                continue
            rp = rel_posix(full, root)
            try:
                lst = os.lstat(full)
            except OSError as ex:
                errors.append({"locator": rp, "error": "lstat: %s" % ex})
                continue
            if stat.S_ISLNK(lst.st_mode):
                try:
                    target = os.readlink(full)
                except OSError as ex:
                    target = "readlink_error: %s" % ex
                broken = not os.path.exists(full)
                entries.append({
                    "relpath": rp, "type": "symlink", "role": "material",
                    "size": None, "mtime": parse_iso_mtime(lst.st_mtime),
                    "mode": mode_str(lst.st_mode), "uid": getattr(lst, "st_uid", None),
                    "gid": getattr(lst, "st_gid", None),
                    "inode": getattr(lst, "st_ino", None),
                    "nlink": getattr(lst, "st_nlink", None),
                    "symlink_target": safe(target), "broken": broken,
                    "sha256": None, "hash_status": "symlink",
                })
                continue
            if stat.S_ISDIR(lst.st_mode):
                dirs.append({
                    "relpath": rp + "/", "type": "dir",
                    "mode": mode_str(lst.st_mode), "empty": False,
                    "uid": getattr(lst, "st_uid", None),
                    "gid": getattr(lst, "st_gid", None),
                })
                visit(full)
                continue
            if stat.S_ISREG(lst.st_mode):
                base = os.path.basename(full)
                role = "task_input" if base in ("case.json", "task.md") else "material"
                entries.append(_regular_entry(full, rp, lst, role, root))
                continue
            entries.append({
                "relpath": rp, "type": "special", "role": "material",
                "size": None, "mtime": parse_iso_mtime(lst.st_mtime),
                "mode": mode_str(lst.st_mode),
                "inode": getattr(lst, "st_ino", None),
                "sha256": None, "hash_status": "non_regular",
            })

    if not os.path.isdir(root):
        errors.append({"locator": ".", "error": "input_root_missing"})
        return entries, dirs, errors
    visit(root)
    entries.sort(key=lambda e: e["relpath"])
    dirs.sort(key=lambda d: d["relpath"])
    return entries, dirs, errors


def _regular_entry(full, rp, lst, role, root):
    entry = {
        "relpath": rp, "type": "regular", "role": role,
        "size": lst.st_size, "mtime": parse_iso_mtime(lst.st_mtime),
        "mode": mode_str(lst.st_mode),
        "uid": getattr(lst, "st_uid", None),
        "gid": getattr(lst, "st_gid", None),
        "inode": getattr(lst, "st_ino", None),
        "nlink": getattr(lst, "st_nlink", None),
        "dev": getattr(lst, "st_dev", None),
        "symlink_target": None, "broken": False,
        "sha256": None, "hash_status": "ok", "encoding": None,
        "preview": None, "catalog_kind": None, "is_note": False,
    }
    if lst.st_size == 0:
        entry["sha256"] = hashlib.sha256(b"").hexdigest()
        entry["hash_status"] = "empty"
        entry["encoding"] = "empty"
        return entry
    if lst.st_size > MAX_HASH_BYTES:
        entry["hash_status"] = "too_large"
    elif not os.access(full, os.R_OK):
        entry["hash_status"] = "unreadable"
    else:
        h = hashlib.sha256()
        try:
            with open(full, "rb") as fh:
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
            entry["sha256"] = h.hexdigest()
            entry["hash_status"] = "ok"
        except OSError as ex:
            entry["hash_status"] = "unreadable: %s" % ex
    ext = os.path.splitext(rp)[1].lower()
    if ext in CATALOG_EXT:
        entry["catalog_kind"] = CATALOG_EXT[ext]
    base = os.path.basename(rp)
    if NOTE_NAME_RE.search(base):
        entry["is_note"] = True
    if entry["hash_status"] == "ok" and ext in (
            ".txt", ".md", ".json", ".csv", ".bib", ".ris", ".yaml", ".yml",
            ".xml", ".log", ".tsv"):
        text, enc, err = read_text(full)
        entry["encoding"] = enc
        entry["preview"] = safe(text[:PREVIEW_CHARS])
    return entry


# ---------------------------------------------------------------------------
# Dedup / versions / aliases
# ---------------------------------------------------------------------------

def dedup_groups(entries):
    """Group regular files by content sha256."""
    by_hash = {}
    for e in entries:
        if e["type"] == "regular" and e.get("sha256"):
            by_hash.setdefault(e["sha256"], []).append(e["relpath"])
    groups = []
    for h, paths in sorted(by_hash.items(), key=lambda kv: kv[0]):
        if len(paths) > 1:
            groups.append({"sha256": h, "paths": sorted(paths)})
    return groups


def normalize_stem(relpath):
    base = os.path.splitext(os.path.basename(relpath))[0]
    base = DATE_RE.sub("", base)
    base = VERSION_RE.sub("", base)
    return norm_text(base) or norm_text(base)


def version_candidates(entries):
    """Heuristic version grouping by normalized stem + mtime ordering."""
    buckets = {}
    for e in entries:
        if e["type"] != "regular" or e["role"] == "task_input":
            continue
        stem = normalize_stem(e["relpath"])
        buckets.setdefault(stem, []).append(e)
    groups = []
    for stem, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (m.get("mtime") or "", m["relpath"]))
        chain = []
        for i, m in enumerate(members):
            node = {"relpath": m["relpath"], "mtime": m["mtime"],
                    "sha256": m["sha256"], "size": m["size"]}
            if i > 0:
                prev = members[i - 1]
                node["diff"] = _diff_pair(prev, m)
            chain.append(node)
        groups.append({"stem": stem, "candidate": True,
                       "heuristic": "normalized_stem+mtime+content",
                       "chain": chain})
    return groups


def _diff_pair(a, b):
    out = {}
    if a.get("size") is not None and b.get("size") is not None:
        out["size_delta"] = b["size"] - a["size"]
    if a.get("sha256") and b.get("sha256"):
        out["identical"] = a["sha256"] == b["sha256"]
    if out.get("identical"):
        return out
    ta, ea, _ = read_text(a.get("_abs", ""), MAX_TEXT_BYTES) if a.get("_abs") else ("", None, "na")
    tb, eb, _ = read_text(b.get("_abs", ""), MAX_TEXT_BYTES) if b.get("_abs") else ("", None, "na")
    if ta and tb and ea and eb and "\x00" not in ta and "\x00" not in tb:
        try:
            out["similarity"] = round(difflib.SequenceMatcher(None, ta, tb).ratio(), 4)
        except Exception:
            pass
    else:
        out["binary_or_unreadable"] = True
    return out


def alias_groups(entries):
    symlinks = [e for e in entries if e["type"] == "symlink"]
    hard = {}
    for e in entries:
        if e["type"] == "regular" and e.get("dev") is not None and \
                e.get("inode") is not None and (e.get("nlink") or 1) > 1:
            key = (e["dev"], e["inode"])
            hard.setdefault(key, []).append(e["relpath"])
    hard_groups = [sorted(v) for v in hard.values() if len(v) > 1]
    hard_groups.sort()
    return {"symlinks": symlinks, "hardlink_groups": hard_groups,
            "broken_symlinks": [s for s in symlinks if s.get("broken")]}


# ---------------------------------------------------------------------------
# Catalogs: JSON / CSV / BibTeX / RIS / SQLite
# ---------------------------------------------------------------------------

def parse_catalog(path, kind):
    """Return (records, errors). Records are list of dicts with 'file' key added."""
    records, errors = [], []
    try:
        if kind == "json":
            obj, err = json_load(path)
            if err:
                return [], [err]
            if isinstance(obj, list):
                records = [r for r in obj if isinstance(r, dict)]
            elif isinstance(obj, dict):
                for key, val in obj.items():
                    if isinstance(val, list):
                        for r in val:
                            if isinstance(r, dict):
                                records.append(dict(r, _catalog_key=key))
                    elif isinstance(val, dict):
                        records.append(dict(val, _catalog_key=key))
        elif kind == "csv":
            text, enc, err = read_text(path)
            if err:
                return [], [err]
            reader = csv.DictReader(io.StringIO(text))
            records = [dict(r) for r in reader if any(str(v).strip() for v in r.values())]
        elif kind == "bibtex":
            text, enc, err = read_text(path)
            if err:
                return [], [err]
            records = _parse_bibtex(text)
        elif kind == "ris":
            text, enc, err = read_text(path)
            if err:
                return [], [err]
            records = _parse_ris(text)
        elif kind == "sqlite":
            records, errors = _parse_sqlite(path)
    except Exception as ex:  # never let one catalog kill the run
        errors.append("parse_catalog %s: %s" % (kind, ex))
    for r in records:
        _add_file_key(r)
    return records, errors


def _add_file_key(r):
    for k in ("path", "file", "filename", "relpath", "file_path", "relative_path",
              "locator", "url"):
        if k in r and r[k]:
            r["file"] = str(r[k]).strip()
            break
    if "file" not in r:
        for k in ("id", "key", "title"):
            if k in r and r[k]:
                r["file"] = str(r[k]).strip()
                break


def _parse_bibtex(text):
    records = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),\s*(.*?)\}\s*(?=@|\Z)", text,
                         re.IGNORECASE | re.DOTALL):
        etype, key, body = m.groups()
        rec = {"type": etype, "key": key.strip()}
        for fm in re.finditer(r"(\w+)\s*=\s*[\{](.*?)[\}]\s*,?", body, re.DOTALL):
            rec[fm.group(1).lower()] = re.sub(r"\s+", " ", fm.group(2)).strip()
        records.append(rec)
    return records


def _parse_ris(text):
    records, cur = [], {}
    for line in text.splitlines():
        if line.startswith("ER"):
            if cur:
                records.append(cur)
            cur = {}
            continue
        if len(line) >= 6 and line[2:4] == "  ":
            tag, value = line[:2], line[6:].strip()
            key = {"TY": "type", "TI": "title", "AU": "author", "PY": "year",
                   "DO": "doi", "JO": "journal", "VL": "volume", "IS": "issue",
                   "SP": "pages"}.get(tag, tag.lower())
            if key in cur:
                cur[key] = str(cur[key]) + "; " + value
            else:
                cur[key] = value
    if cur:
        records.append(cur)
    return records


def _parse_sqlite(path):
    records, errors = [], []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    except sqlite3.Error as ex:
        return [], ["sqlite_open_error: %s" % ex]
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            try:
                cols = [d[0] for d in con.execute(
                    "PRAGMA table_info(%s)" % t).fetchall()]
                for row in con.execute("SELECT * FROM %s" % t).fetchall():
                    rec = {"_table": t}
                    for c, v in zip(cols, row):
                        if isinstance(v, bytes):
                            v = v.decode("utf-8", "replace")
                        rec[c] = v
                    records.append(rec)
            except sqlite3.Error as ex:
                errors.append("sqlite_query_error[%s]: %s" % (t, ex))
    except sqlite3.Error as ex:
        errors.append("sqlite_query_error: %s" % ex)
    finally:
        try:
            con.close()
        except Exception:
            pass
    return records, errors


def catalog_matches(records):
    """Extract a normalized file locator per catalog record."""
    seen = []
    for r in records:
        f = r.get("file")
        if f:
            seen.append(f)
    return seen


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile(entries, catalogs):
    disk = {e["relpath"]: e for e in entries if e["type"] == "regular" and
            e["role"] != "task_input"}
    disk_basenames = {}
    for rp in disk:
        disk_basenames.setdefault(os.path.basename(rp), []).append(rp)

    catalog_records = []
    for cat in catalogs:
        for r in cat["records"]:
            catalog_records.append({"source": cat["path"], "record": r})

    matches, mismatches, missing, orphaned = [], [], [], []
    referenced = set()
    for cr in catalog_records:
        f = cr["record"].get("file", "")
        rp = _resolve(f, disk, disk_basenames)
        if rp is None:
            missing.append({"file": f, "source": cr["source"],
                            "reason": "not_on_disk"})
            continue
        referenced.add(rp)
        e = disk[rp]
        item = {"file": rp, "source": cr["source"], "record": cr["record"]}
        if "hash" in cr["record"] or "sha256" in cr["record"]:
            expected = str(cr["record"].get("hash") or cr["record"].get("sha256") or "").strip()
            if expected and e.get("sha256") and not expected.lower().startswith(e["sha256"][:12]):
                mismatches.append(dict(item, reason="hash_mismatch",
                                      expected=expected, actual=e["sha256"]))
            else:
                matches.append(item)
        else:
            matches.append(item)
    orphaned = [rp for rp in sorted(disk) if rp not in referenced]
    return {
        "catalogs": [{"path": c["path"], "kind": c["kind"],
                      "record_count": len(c["records"]),
                      "parse_errors": c["errors"]} for c in catalogs],
        "matches": matches, "mismatches": mismatches, "missing": missing,
        "orphaned": orphaned,
    }


def _resolve(f, disk, basenames):
    f = f.strip()
    if not f:
        return None
    if f in disk:
        return f
    f_norm = f.replace("\\", "/").lstrip("./")
    if f_norm in disk:
        return f_norm
    for rp in disk:
        if rp.endswith(f_norm):
            return rp
    base = os.path.basename(f_norm)
    if base in basenames and len(basenames[base]) == 1:
        return basenames[base][0]
    return None


# ---------------------------------------------------------------------------
# Version notes (administrative)
# ---------------------------------------------------------------------------

def extract_version_notes(entries):
    notes = []
    for e in entries:
        if e["type"] != "regular" or not e.get("is_note"):
            continue
        text, enc, err = read_text(_abs_of(e))
        if err or not text:
            continue
        lines = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if re.search(r"v?\d+\.\d+|version|版本|修订|替代|supersede|replaces",
                         ln, re.IGNORECASE):
                lines.append(safe(ln[:400]))
        if lines:
            notes.append({"file": e["relpath"], "encoding": enc,
                          "lines": lines[:200]})
    return notes


def _abs_of(e):
    return os.path.join(e.get("_input_root", DEFAULT_INPUT), e["relpath"])


# ---------------------------------------------------------------------------
# Search index
# ---------------------------------------------------------------------------

def build_index(entries, catalogs):
    index = {}
    fields = {"author": [], "year": [], "title": [], "doi": [], "type": []}

    def add(token, relpath):
        if token:
            index.setdefault(token, set()).add(relpath)

    for e in entries:
        if e["type"] != "regular":
            continue
        rp = e["relpath"]
        for token in norm_text(os.path.basename(rp)).split():
            add(token, rp)
        for token in norm_text(os.path.dirname(rp)).split("/"):
            add(token, rp)
        if e.get("catalog_kind"):
            add(e["catalog_kind"], rp)
        if e.get("encoding") and e["encoding"] not in ("empty", None):
            add("enc:" + e["encoding"], rp)

    for cat in catalogs:
        for r in cat["records"]:
            for k, v in r.items():
                if k.startswith("_"):
                    continue
                lk = k.lower()
                if lk in fields and v not in (None, ""):
                    fields[lk].append({"value": safe(str(v)),
                                       "source": cat["path"]})
                for token in norm_text(str(v)).split():
                    add(token, "catalog:" + cat["path"])

    index = {k: sorted(v) for k, v in sorted(index.items())}
    return {"tokens": index, "fields": fields}


# ---------------------------------------------------------------------------
# Q&A (mechanical answers with evidence locators)
# ---------------------------------------------------------------------------

def find_questions(entries):
    qs = []
    for e in entries:
        if e["type"] != "regular" or e["role"] == "task_input":
            continue
        base = os.path.basename(e["relpath"])
        ext = os.path.splitext(base)[1].lower()
        if not (QUESTION_NAME_RE.search(base) or ext in (".questions", ".queries")):
            continue
        text, enc, err = read_text(_abs_of(e))
        if err or not text:
            continue
        if ext == ".json":
            obj, jerr = json_load(_abs_of(e))
            if not jerr:
                if isinstance(obj, list):
                    qs.extend(str(x) for x in obj)
                elif isinstance(obj, dict):
                    for v in obj.values():
                        if isinstance(v, list):
                            qs.extend(str(x) for x in v)
                        else:
                            qs.append(str(v))
        else:
            for ln in text.splitlines():
                ln = ln.strip().rstrip("?？")
                if ln and not ln.startswith("#"):
                    qs.append(safe(ln[:300]))
    return qs[:100]


def answer_questions(questions, computed, entries):
    answers = []
    if not questions:
        return answers
    for q in questions:
        ql = q.lower()
        item = {"question": q, "answer": None, "sources": []}
        if any(w in ql for w in ("count", "多少", "数量", "files", "文件数", "统计")):
            item["answer"] = computed["summary"]
            item["sources"].append({"locator": ".", "quote": str(computed["summary"])})
        elif any(w in ql for w in ("duplicate", "重复", "去重")):
            item["answer"] = computed["duplicate_groups"]
        elif any(w in ql for w in ("version", "版本")):
            item["answer"] = computed["version_candidates"]
        elif any(w in ql for w in ("missing", "缺失")):
            item["answer"] = computed["reconciliation"]["missing"]
        elif any(w in ql for w in ("orphan", "孤儿", "未引用")):
            item["answer"] = computed["reconciliation"]["orphaned"]
        elif any(w in ql for w in ("broken", "断链", "symlink", "符号链接")):
            item["answer"] = computed["aliases"]["broken_symlinks"]
        elif any(w in ql for w in ("author", "作者", "year", "年份", "title", "标题")):
            item["answer"] = _field_answer(q, computed["index"]["fields"], entries)
        else:
            hits = _token_search(norm_text(q), computed["index"]["tokens"], entries)
            item["answer"] = hits[:20] or "no_token_match"
        if item["answer"]:
            for src in _locators_for(q, entries):
                item["sources"].append(src)
        answers.append(item)
    return answers


def _field_answer(q, fields, entries):
    out = []
    for key in ("author", "year", "title", "doi"):
        if key in q.lower() and fields.get(key):
            out.extend(fields[key][:20])
    return out or None


def _token_search(tokens, index, entries):
    hits = {}
    for t in tokens.split():
        for path in index.get(t, []):
            hits[path] = hits.get(path, 0) + 1
    return [p for p, _ in sorted(hits.items(), key=lambda kv: -kv[1])]


def _locators_for(q, entries):
    locs = []
    ql = q.lower()
    for e in entries:
        if e["type"] != "regular":
            continue
        if norm_text(os.path.basename(e["relpath"]))[:20] and any(
                t in e["relpath"].lower() for t in ql.split()[:6]):
            locs.append({"locator": e["relpath"],
                         "quote": safe(os.path.basename(e["relpath"]))})
    return locs[:5]


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def write_artifacts(output_dir, entries, dirs, computed):
    os.makedirs(output_dir, exist_ok=True)
    artifacts = []

    # library_manifest.csv
    csv_path = os.path.join(output_dir, "library_manifest.csv")
    cols = ["relpath", "type", "role", "size", "mtime", "mode", "uid", "gid",
            "inode", "nlink", "symlink_target", "broken", "sha256",
            "hash_status", "encoding", "catalog_kind"]
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in entries:
            w.writerow({k: safe(e.get(k)) for k in cols})
    artifacts.append({"file": "library_manifest.csv", "rows": len(entries)})

    # directory_tree.txt
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    tree_lines = render_tree(entries, dirs)
    with open(tree_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(tree_lines) + "\n")
    artifacts.append({"file": "directory_tree.txt", "lines": len(tree_lines)})

    # dedup_version.json
    dv = {
        "duplicate_groups": computed["duplicate_groups"],
        "version_candidates": computed["version_candidates"],
        "version_notes": computed["version_notes"],
        "aliases": computed["aliases"],
    }
    dv_path = os.path.join(output_dir, "dedup_version.json")
    json_dump(dv_path, dv)
    artifacts.append({"file": "dedup_version.json",
                      "dup_groups": len(dv["duplicate_groups"]),
                      "version_groups": len(dv["version_candidates"])})

    # recovery_report.json
    rr_path = os.path.join(output_dir, "recovery_report.json")
    json_dump(rr_path, computed["reconciliation"])
    artifacts.append({"file": "recovery_report.json"})

    # library_index.json
    idx_path = os.path.join(output_dir, "library_index.json")
    json_dump(idx_path, computed["index"])
    artifacts.append({"file": "library_index.json",
                      "tokens": len(computed["index"]["tokens"])})

    # library_manifest.md (handoff)
    md_path = os.path.join(output_dir, "library_manifest.md")
    md = render_manifest_md(computed)
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    artifacts.append({"file": "library_manifest.md"})

    # operation_log.jsonl (one run = one fresh log, not appended)
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
        for step in computed["change_log"]:
            fh.write(json.dumps(step, ensure_ascii=False, default=_json_default) + "\n")
    artifacts.append({"file": "operation_log.jsonl"})

    return artifacts


def render_tree(entries, dirs):
    lines = ["# directory tree (input root = .)"]
    file_map = {e["relpath"]: e for e in entries}

    def parent_of(relpath):
        p = os.path.dirname(relpath.rstrip("/"))
        return p if p else "."

    dir_set = set()
    for d in dirs:
        dir_set.add(d["relpath"].rstrip("/"))

    file_parents = {parent_of(e["relpath"]) for e in entries}
    dir_parents = {parent_of(d) for d in dir_set}

    by_parent = {}
    for e in entries:
        by_parent.setdefault(parent_of(e["relpath"]), []).append(
            {"relpath": e["relpath"], "kind": e["type"],
             "broken": e.get("broken")})
    for d in sorted(dir_set):
        empty = (d not in file_parents) and (d not in dir_parents)
        by_parent.setdefault(parent_of(d), []).append(
            {"relpath": d, "kind": "dir", "empty": empty})

    def walk(parent, prefix):
        out = []
        children = by_parent.get(parent, [])
        children.sort(key=lambda c: (c["relpath"], c["kind"]))
        for i, c in enumerate(children):
            last = i == len(children) - 1
            conn = "└── " if last else "├── "
            child_prefix = prefix + ("    " if last else "│   ")
            if c["kind"] == "dir":
                label = os.path.basename(c["relpath"]) + "/"
                if c.get("empty"):
                    label += " [空目录]"
                out.append(prefix + conn + label)
                out.extend(walk(c["relpath"], child_prefix))
            else:
                label = os.path.basename(c["relpath"])
                if c["kind"] == "symlink":
                    target = file_map.get(c["relpath"], {}).get("symlink_target", "")
                    label += " -> " + (target or "?")
                    if c.get("broken"):
                        label += " [断链]"
                out.append(prefix + conn + label)
        return out

    lines.extend(walk(".", ""))
    return lines


def render_manifest_md(computed):
    s = computed["summary"]
    md = []
    md.append("# 科研资料库交接报告\n")
    md.append("## 概览\n")
    md.append("- 文件 %s 个；目录 %s 个；符号链接 %s 个（断链 %s）；硬链接组 %s"
              % (s["files"], s["directories"], s["symlinks"], s["broken_symlinks"],
                 s["hardlink_groups"]))
    md.append("- 重复组 %s（涉及 %s 个文件）；版本候选组 %s"
              % (s["duplicate_groups"], s["duplicate_files"], s["version_groups"]))
    md.append("- 对账：matches %s / mismatches %s / missing %s / orphaned %s"
              % (s["reconciliation"]["matches"], s["reconciliation"]["mismatches"],
                 s["reconciliation"]["missing"], s["reconciliation"]["orphaned"]))
    md.append("- 已哈希 %s；too_large %s；unreadable %s"
              % (s["hashed"], s["too_large"], s["unreadable"]))
    md.append("\n## 待人工确认\n")
    pend = computed.get("pending_human") or []
    if pend:
        for p in pend:
            md.append("- %s" % p)
    else:
        md.append("- 无")
    md.append("\n## 证据\n")
    for src in computed.get("sources", [])[:40]:
        md.append("- `%s` → %s" % (src.get("locator", "?"),
                                   (src.get("quote") or "")[:80]))
    return "\n".join(md) + "\n"


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def build_summary(entries, dirs, dedup, versions, aliases, recon, hashed, too_large, unreadable):
    return {
        "entries": len(entries),
        "files": sum(1 for e in entries if e["type"] == "regular"),
        "directories": len(dirs),
        "symlinks": len(aliases["symlinks"]),
        "broken_symlinks": len(aliases["broken_symlinks"]),
        "hardlink_groups": len(aliases["hardlink_groups"]),
        "duplicate_groups": len(dedup),
        "duplicate_files": sum(len(g["paths"]) for g in dedup),
        "version_groups": len(versions),
        "reconciliation": {
            "matches": len(recon["matches"]),
            "mismatches": len(recon["mismatches"]),
            "missing": len(recon["missing"]),
            "orphaned": len(recon["orphaned"]),
        },
        "hashed": hashed,
        "too_large": too_large,
        "unreadable": unreadable,
    }


def build_result(case_id, contract, status, entries, dirs, dedup, versions,
                 notes, aliases, recon, index, answers, summary, artifacts,
                 sources, uncertainty, errors, degradations, limitations,
                 pending_human, change_log, started):
    computed = {
        "files": entries,
        "summary": summary,
        "duplicate_groups": dedup,
        "version_candidates": versions,
        "version_notes": notes,
        "aliases": aliases,
        "reconciliation": recon,
        "answers": answers,
        "index": index,
        "artifacts": artifacts,
    }
    result = {
        "case_id": case_id,
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty,
        "change_log": change_log,
        "pending_human": pending_human,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "runtime": {
            "seconds": round(time.time() - started, 3),
            "started": now_iso(),
        },
        "computed": computed,
    }
    for name in contract_fields(contract):
        if name in result:
            # collides with a reserved key: preserve the reserved value under
            # computed._contract_<field> so nothing is lost or overwritten
            result["computed"]["_contract_%s" % name] = result.get(name)
        else:
            result[name] = computed.get(name, None)
    return result


# ---------------------------------------------------------------------------
# Patch merge (incremental updates)
# ---------------------------------------------------------------------------

def deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def check_result(result, case_data):
    problems = []
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in STATUSES:
        problems.append("invalid status %r" % result.get("status"))
    contract = None
    if isinstance(case_data, dict):
        for k in CONTRACT_KEYS:
            if k in case_data and case_data[k] not in (None, "", [], {}):
                contract = case_data[k]
                break
    for name in contract_fields(contract):
        if name not in result:
            problems.append("missing contract field %r" % name)
    if not result.get("sources") and result.get("status") == STATUS_OK:
        problems.append("ok status but no sources")
    return problems


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(args, started):
    case_data, task_text, case_id, contract, load_errors = load_case(
        args.case_json, args.task_path, args.input_root)

    errors = list(load_errors)
    degradations = []
    uncertainty = []
    limitations = []
    pending_human = []
    sources = []
    change_log = []

    # Phase 0: land skeleton before anything else (best-effort).
    skeleton = build_skeleton(case_id, contract, errors)
    os.makedirs(args.output_dir, exist_ok=True)
    json_dump(args.result, skeleton)

    entries, dirs, scan_errors = scan_tree(
        args.input_root,
        skip_prefixes=(args.output_dir, args.work_dir))
    for se in scan_errors:
        errors.append({"item": se["locator"], "error": se["error"]})
        degradations.append({"item": se["locator"], "error": se["error"]})
    change_log.append({"time": now_iso(), "actor": "finalize.py",
                       "step": "inventory", "detail": "%d entries, %d dirs, %d scan errors"
                       % (len(entries), len(dirs), len(scan_errors))})

    # attach abs paths for diffing
    for e in entries:
        e["_abs"] = os.path.join(args.input_root, *e["relpath"].split("/")) \
            if e["relpath"] else args.input_root
        e["_input_root"] = args.input_root

    dedup = dedup_groups(entries)
    versions = version_candidates(entries)
    aliases = alias_groups(entries)
    notes = extract_version_notes(entries)

    # catalogs
    catalogs = []
    for e in entries:
        if e["type"] == "regular" and e.get("catalog_kind"):
            path = e["_abs"]
            recs, cerrs = parse_catalog(path, e["catalog_kind"])
            catalogs.append({"path": e["relpath"], "kind": e["catalog_kind"],
                             "records": recs, "errors": cerrs})
            for ce in cerrs:
                degradations.append({"item": e["relpath"], "error": ce})
    recon = reconcile(entries, catalogs)
    index = build_index(entries, catalogs)
    questions = find_questions(entries)

    hashed = sum(1 for e in entries if e["type"] == "regular" and e.get("sha256"))
    too_large = sum(1 for e in entries if e.get("hash_status") == "too_large")
    unreadable = sum(1 for e in entries if str(e.get("hash_status", "")).startswith("unreadable"))

    summary = build_summary(entries, dirs, dedup, versions, aliases, recon,
                            hashed, too_large, unreadable)

    # status
    material_files = [e for e in entries if e["role"] != "task_input" and
                      e["type"] == "regular"]
    if too_large:
        degradations.append({"item": "content_hash",
                             "error": "%d files skipped as too_large" % too_large})
    if unreadable:
        degradations.append({"item": "content_hash",
                             "error": "%d files unreadable" % unreadable})
    has_incomplete = bool(errors) or bool(degradations) or too_large > 0 or unreadable > 0

    if not case_data and not os.path.isfile(args.case_json):
        status = STATUS_INPUT_ERROR
        errors.append({"item": "case.json", "error": "missing"})
    elif not material_files and not dirs:
        status = STATUS_INSUFFICIENT
        uncertainty.append({"item": "no_material", "reason": "no material files found",
                            "severity": "high"})
    elif has_incomplete:
        status = STATUS_PARTIAL
    else:
        status = STATUS_OK

    # sources for key conclusions
    sources.extend(_build_sources(dedup, versions, aliases, recon, entries, summary))

    if not contract:
        uncertainty.append({"item": "output_contract", "reason": "output_contract missing",
                            "severity": "medium"})
    if scan_errors:
        limitations.append("some paths skipped: %d scan errors" % len(scan_errors))
    if too_large:
        limitations.append("%d files > %d bytes: content hash skipped"
                           % (too_large, MAX_HASH_BYTES))

    answers = answer_questions(questions, {
        "summary": summary, "duplicate_groups": dedup,
        "version_candidates": versions, "aliases": aliases,
        "reconciliation": recon, "index": index,
    }, entries)

    artifacts = write_artifacts(args.output_dir, entries, dirs, {
        "duplicate_groups": dedup, "version_candidates": versions,
        "version_notes": notes, "aliases": aliases,
        "reconciliation": recon, "index": index, "summary": summary,
        "pending_human": pending_human, "sources": sources,
        "change_log": change_log,
    })

    result = build_result(case_id, contract, status, entries, dirs, dedup,
                          versions, notes, aliases, recon, index, answers,
                          summary, artifacts, sources, uncertainty, errors,
                          degradations, limitations, pending_human, change_log,
                          started)
    json_dump(args.result, result)
    return result


def _build_sources(dedup, versions, aliases, recon, entries, summary):
    sources = []
    sources.append({"item": "summary", "locator": ".",
                    "quote": json.dumps(summary, ensure_ascii=False, sort_keys=True)})
    for g in dedup[:50]:
        sources.append({"item": "duplicate_group", "locator": g["paths"][0],
                        "quote": g["sha256"] + " -> " + ", ".join(g["paths"])})
    for g in versions[:50]:
        sources.append({"item": "version_candidate", "locator": g["chain"][0]["relpath"],
                        "quote": g["stem"] + " (" + str(len(g["chain"])) + " members)"})
    for s in aliases["symlinks"][:50]:
        sources.append({"item": "alias_symlink", "locator": s["relpath"],
                        "quote": (s.get("symlink_target") or "") +
                                 (" [broken]" if s.get("broken") else "")})
    for g in aliases["hardlink_groups"][:50]:
        sources.append({"item": "alias_hardlink", "locator": g[0],
                        "quote": ", ".join(g)})
    for m in recon["mismatches"][:50]:
        sources.append({"item": "reconcile_mismatch", "locator": m["file"],
                        "quote": "expected %s, actual %s"
                                 % (m.get("expected"), m.get("actual"))})
    return sources


# ---------------------------------------------------------------------------
# Skeleton-only and update entry points
# ---------------------------------------------------------------------------

def cmd_skeleton(args):
    case_data, task_text, case_id, contract, errors = load_case(
        args.case_json, args.task_path, args.input_root)
    skeleton = build_skeleton(case_id, contract, errors)
    os.makedirs(args.output_dir, exist_ok=True)
    json_dump(args.result, skeleton)
    print("skeleton: case_id=%s status=%s contract_fields=%d errors=%d"
          % (case_id, skeleton["status"], len(contract_fields(contract)), len(errors)))
    return 0


def cmd_update(args):
    patch, err = json_load(args.update)
    if err or not isinstance(patch, dict):
        print("update: cannot read patch: %s" % err)
        return 2
    result, err = json_load(args.result)
    if err or not isinstance(result, dict):
        print("update: cannot read result: %s" % err)
        return 2
    deep_merge(result, patch)
    if "change_log" in result and isinstance(result["change_log"], list):
        result["change_log"].append({
            "time": now_iso(), "actor": "finalize.py", "step": "update",
            "detail": "patch merged from %s" % args.update,
        })
    json_dump(args.result, result)
    print("update: merged %d top-level keys into %s"
          % (len(patch), args.result))
    return 0


def cmd_check(args):
    result, err = json_load(args.result)
    if err or not isinstance(result, dict):
        print("check: cannot read result: %s" % err)
        return 2
    case_data = None
    if os.path.isfile(args.case_json):
        case_data, cerr = json_load(args.case_json)
    problems = check_result(result, case_data)
    print("check: case_id=%s status=%s problems=%d"
          % (result.get("case_id"), result.get("status"), len(problems)))
    for p in problems:
        print("  - %s" % p)
    return 0 if not problems else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.result:
        args.result = args.result_path or os.path.join(args.output_dir, "result.json")
    if args.skeleton_only:
        return cmd_skeleton(args)
    if args.update:
        return cmd_update(args)
    if args.check:
        return cmd_check(args)
    started = time.time()
    try:
        result = run_pipeline(args, started)
    except Exception as ex:
        # Never leave an empty artifact: land a partial skeleton on failure.
        try:
            case_data, task_text, case_id, contract, errors = load_case(
                args.case_json, args.task_path, args.input_root)
            skeleton = build_skeleton(case_id, contract, errors,
                                      note="pipeline failed: %s" % ex)
            os.makedirs(args.output_dir, exist_ok=True)
            json_dump(args.result, skeleton)
        except Exception:
            pass
        print("finalize: pipeline failed: %s" % ex, file=sys.stderr)
        return 2
    s = result["computed"]["summary"]
    print("finalize: %d files, %d dup groups, %d version groups, "
          "%d broken symlinks, %d mismatches -> %s"
          % (s["files"], s["duplicate_groups"], s["version_groups"],
             s["broken_symlinks"], s["reconciliation"]["mismatches"],
             args.result))
    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="librepair_selftest_")
    root = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    os.makedirs(root, exist_ok=True)
    try:
        with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "selftest-case",
                       "output_contract": {"summary": "object", "missing_field": "string"}}, fh)
        with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# test\nchallenge_id: selftest-case\n")
        os.makedirs(os.path.join(root, "materials"), exist_ok=True)
        with open(os.path.join(root, "materials", "a_v1.txt"), "w", encoding="utf-8") as fh:
            fh.write("hello\n")
        with open(os.path.join(root, "materials", "a_v2.txt"), "w", encoding="utf-8") as fh:
            fh.write("hello world\n")
        with open(os.path.join(root, "materials", "dup1.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02")
        with open(os.path.join(root, "materials", "dup2.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02")
        symlink_ok = True
        try:
            os.symlink(os.path.join(root, "materials", "missing.txt"),
                       os.path.join(root, "materials", "broken.lnk"))
        except (OSError, NotImplementedError):
            # Windows without developer mode may disallow symlinks; runtime is Linux.
            symlink_ok = False
        with open(os.path.join(root, "materials", "catalog.json"), "w", encoding="utf-8") as fh:
            json.dump([{"file": "a_v1.txt", "hash": "deadbeef"}], fh)
        with open(os.path.join(root, "materials", "questions.txt"), "w", encoding="utf-8") as fh:
            fh.write("重复文件有哪些？\n")

        args = argparse.Namespace(
            case_json=os.path.join(root, "case.json"),
            task_path=os.path.join(root, "task.md"),
            input_root=root, output_dir=out, work_dir=os.path.join(tmp, "work"),
            result=os.path.join(out, "result.json"),
            skeleton_only=False, update=None, check=False)
        started = time.time()
        result = run_pipeline(args, started)

        assert result["case_id"] == "selftest-case"
        assert result["status"] in STATUSES
        assert "summary" in result and "missing_field" in result
        assert result["missing_field"] is None
        assert result["computed"]["summary"]["duplicate_groups"] == 1
        if symlink_ok:
            assert result["computed"]["summary"]["broken_symlinks"] == 1
        assert result["computed"]["summary"]["reconciliation"]["mismatches"] == 1
        assert result["computed"]["summary"]["version_groups"] >= 1
        assert result["computed"]["answers"], "no answers produced"
        for name in ("library_manifest.csv", "directory_tree.txt",
                     "dedup_version.json", "recovery_report.json",
                     "library_index.json", "library_manifest.md",
                     "operation_log.jsonl"):
            assert os.path.isfile(os.path.join(out, name)), name

        # determinism: re-run and compare (ignore runtime/change_log timestamps)
        result2 = run_pipeline(args, time.time())
        for key in ("case_id", "status", "computed"):
            assert json.dumps(result[key], sort_keys=True, default=_json_default) == \
                   json.dumps(result2[key], sort_keys=True, default=_json_default), key
        # check runner
        args.check = True
        assert cmd_check(args) == 0, "check runner failed"
        print("selftest: OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
