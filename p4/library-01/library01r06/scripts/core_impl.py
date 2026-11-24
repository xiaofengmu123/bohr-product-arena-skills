#!/usr/bin/env python3
"""core_impl.py -- deterministic document-library repair engine (Python stdlib only).

Design principle: *derive, don't duplicate*.

All facts are collected once into a normalized intermediate representation
(library_model.json), all intent is captured in a machine-readable plan
(plan.json), and result.json is rendered by a pure function from those two.
`--verify` re-renders and diffs, so a reviewer can prove the delivered
result.json is exactly the projection of the recorded facts -- not a hand-edited
copy that drifted from its own evidence.

This file is the bulk of the implementation. main.py is a thin wrapper.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
from collections import OrderedDict

SCHEMA_VERSION = "1.0.0"
SCRIPT_NAME = "document-library-repair/core_impl.py"

# --- budgets (documented in SKILL.md; informational here) ---
BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64
SKELETON_SECONDS = 180  # hard rule: a partial skeleton must land first

DEFAULT_MAX_HASH_BYTES = 32 * 1024 * 1024   # larger files: skip content hash, keep metadata
DEFAULT_MAX_TEXT_BYTES = 2 * 1024 * 1024    # larger text: skip catalog/notes parsing

# Candidate key names for the output contract. Resolved at runtime, never assumed.
CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract", "outputs", "result",
)

# These top-level keys are owned by the skill; a contract field that collides is
# re-homed verbatim into computed["_contract_<field>"] and recorded in plan.json.
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human", "change_log",
    "errors", "degradations", "runtime", "limitations", "computed",
}

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}

LIST_HINT = (
    "files", "items", "entries", "records", "list", "members", "tags", "answers",
    "groups", "duplicates", "versions", "aliases", "matches", "mismatches",
    "missing", "orphaned", "sources", "errors", "degradations", "uncertainty",
    "pending_human", "rows", "documents",
)

CATALOG_EXT = {".json", ".csv", ".bib", ".bibtex", ".ris", ".db", ".sqlite", ".sqlite3"}
VERSION_NOTE_NAME_RE = re.compile(
    r"(?i)(version|versions|changelog|release[-_ ]?notes|修订|版本|更新日志|变更记录)"
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def to_fwd(rel: str) -> str:
    return rel.replace(os.sep, "/")


def log_entry(actor: str, step: str, detail: str) -> dict:
    return {"time": now_iso(), "actor": actor, "step": step, "detail": detail}


def read_text_file(path, max_bytes=DEFAULT_MAX_TEXT_BYTES):
    """Return (text | None, encoding_info, size). Never raises on bad encoding."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "unreadable", 0
    if size > max_bytes:
        return None, "too_large_for_text", size
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, "unreadable", size
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, size
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(fallback)", size


def sha256_file(path, max_bytes=DEFAULT_MAX_HASH_BYTES):
    """Return (hexdigest | None, status, size). Content-addressed, never by name."""
    try:
        st = os.stat(path)
    except OSError:
        return None, "unreadable", None
    if st.st_size > max_bytes:
        return None, "too_large", st.st_size
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None, "unreadable", st.st_size
    return h.hexdigest(), "ok", st.st_size


def _read_json_loose(path):
    text, enc, size = read_text_file(path)
    if text is None:
        return None, "unreadable:" + enc
    try:
        return json.loads(text), "ok"
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        return None, "bad_json:" + str(exc)


def load_json_file(path):
    """Load a JSON file written by this skill or by a harness, tolerating BOM and
    any size (no text-size cap, unlike catalog parsing)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f, object_pairs_hook=OrderedDict)


# --------------------------------------------------------------------------- #
# Input discovery: case_id, output contract, rules
# --------------------------------------------------------------------------- #

def discover_case(case_json_path, task_path):
    errors, degradations, uncertainty = [], [], []
    case_id = None
    case_obj = None
    case_load_status = None
    task_text, task_enc, _ = (None, None, 0)

    if task_path and os.path.exists(task_path):
        task_text, task_enc, _ = read_text_file(task_path)
        if task_text is None:
            degradations.append({"item": "task.md", "detail": "unreadable/missing: " + task_enc})
    else:
        degradations.append({"item": "task.md", "detail": "not provided"})

    if os.path.exists(case_json_path):
        case_obj, case_load_status = _read_json_loose(case_json_path)
        if case_obj is None:
            errors.append({"item": "case.json", "detail": case_load_status})
        elif not isinstance(case_obj, dict):
            errors.append({"item": "case.json", "detail": "not a JSON object"})
            case_obj = None
    else:
        errors.append({"item": "case.json", "detail": "missing"})

    if isinstance(case_obj, dict):
        for k in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            if case_obj.get(k):
                case_id = str(case_obj[k])
                break
    if not case_id and task_text:
        m = re.search(r"challenge_id[:：]\s*[`\"]?([A-Za-z0-9_.-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = "unknown"
        uncertainty.append({
            "item": "case_id",
            "reason": "not found in case.json or task.md; using 'unknown'",
            "severity": "medium",
        })

    contract, contract_source = {}, "not_reported"
    if isinstance(case_obj, dict):
        for k in CONTRACT_KEYS:
            if k in case_obj and case_obj[k] not in (None, {}):
                contract = case_obj[k]
                contract_source = "case.json#" + k
                break

    fields, values = normalize_contract(contract)
    if not fields and contract_source == "not_reported":
        uncertainty.append({
            "item": "output_contract",
            "reason": "no output_contract/output/result_schema/contract key found in case.json",
            "severity": "low",
        })

    rules = extract_rules(task_text)
    return {
        "case_id": case_id,
        "contract_fields": fields,
        "contract_values": values,
        "contract_source": contract_source,
        "contract_clashes": [f for f in fields if f in RESERVED_KEYS],
        "rules": rules,
        "errors": errors,
        "degradations": degradations,
        "uncertainty": uncertainty,
        "case_obj": case_obj,
        "task_text": task_text,
    }


def normalize_contract(contract):
    """Normalize any contract shape to (field_names_in_order, name->default)."""
    fields, values = [], {}
    if isinstance(contract, dict):
        # Unwrap common envelope shapes.
        for env in ("fields", "schema", "properties", "result", "result_schema"):
            if env in contract and isinstance(contract[env], (dict, list)):
                contract = contract[env]
                break
        if isinstance(contract, dict):
            for k, v in contract.items():
                fields.append(k)
                if isinstance(v, dict):
                    if v.get("default") is not None:
                        values[k] = v.get("default")
                    elif str(v.get("type", "")).lower() in ("array", "list"):
                        values[k] = []
                    elif str(v.get("type", "")).lower() in ("object", "dict", "map"):
                        values[k] = {}
                    else:
                        values[k] = None
                elif isinstance(v, list):
                    values[k] = v
                else:
                    values[k] = None
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    fields.append(str(name))
                    values[str(name)] = item.get("default")
            elif isinstance(item, str):
                fields.append(item)
                values[item] = None
    # Fill list-hinted fields with [] instead of null (safer default, recorded).
    for f in fields:
        if values.get(f) is None and f.lower() in LIST_HINT:
            values[f] = []
    return fields, values


def extract_rules(task_text):
    """Pull organizational rules out of task.md into plan.json (heuristic, verifiable)."""
    rules = []
    if not task_text:
        return rules
    kw = {
        "directory": r"目录|folder|directory|归位|组织",
        "naming": r"命名|rename|命名规范",
        "metadata": r"元数据|metadata|字段|field",
        "version": r"版本|version|版本链",
        "dedup": r"去重|重复|duplicate|hash|哈希",
        "permission": r"权限|permission",
        "provenance": r"来源|source|出处",
        "archive": r"归档|archive",
        "recovery": r"对账|reconcil|缺失|断链|恢复",
    }
    for i, line in enumerate(task_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if not (stripped.startswith(("-", "*", "#", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"))
                or "：" in stripped or ":" in stripped):
            continue
        for typ, pat in kw.items():
            if re.search(pat, stripped):
                rules.append({
                    "id": "r%d" % (len(rules) + 1),
                    "type": typ,
                    "text": stripped[:400],
                    "locator": "task.md#L%d" % i,
                })
                break
    return rules


# --------------------------------------------------------------------------- #
# Filesystem scan
# --------------------------------------------------------------------------- #

def scan_tree(root, exclude_paths=None):
    files, dirs, scan_errors, scan_degrad = [], [], [], []
    root = os.path.abspath(root)
    exclude_paths = {os.path.abspath(p) for p in (exclude_paths or []) if p}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir in (".", "") else rel_dir
        if rel_dir:
            dirs.append({"rel": to_fwd(rel_dir), "path": dirpath, "empty": None})
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.abspath(full) in exclude_paths:
                continue  # control files (case.json / task.md) are not material files
            rec = stat_entry(full, root, name)
            files.append(rec)
    # Determine which directories are truly empty (no entries at all).
    for d in dirs:
        try:
            d["empty"] = len(os.listdir(d["path"])) == 0
        except OSError as exc:
            d["empty"] = None
            scan_degrad.append({"item": to_fwd(d["rel"]), "detail": "cannot list dir: " + str(exc)})
    return files, dirs, scan_errors, scan_degrad


def stat_entry(full, root, name):
    rel = os.path.relpath(full, root)
    rec = OrderedDict()
    rec["rel"] = to_fwd(rel)
    rec["path"] = full
    is_link = os.path.islink(full)
    try:
        st = os.lstat(full)
    except OSError as exc:
        rec["type"] = "unknown"
        rec["status"] = "unreadable"
        rec["error"] = str(exc)
        rec["size"] = None
        rec["mode"] = None
        rec["mtime"] = None
        rec["mtime_ts"] = None
        rec["nlink"] = None
        rec["ext"] = ""
        rec["stem"] = ""
        return rec
    rec["mode"] = oct(st.st_mode & 0o7777)
    rec["size"] = st.st_size
    rec["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(st.st_mtime))
    rec["mtime_ts"] = st.st_mtime
    rec["nlink"] = st.st_nlink
    rec["inode"] = [st.st_dev, st.st_ino]
    rec["ext"] = os.path.splitext(name)[1].lower()
    rec["stem"] = os.path.splitext(name)[0]
    if is_link:
        rec["type"] = "symlink"
        try:
            target = os.readlink(full)
        except OSError:
            target = None
        rec["link_target"] = target
        rec["link_broken"] = not os.path.exists(full)
        rec["status"] = "symlink_broken" if rec["link_broken"] else "symlink"
        rec["sha256"] = None
    elif os.path.isfile(full):
        rec["type"] = "file"
        digest, status, _ = sha256_file(full)
        rec["sha256"] = digest
        rec["status"] = status
    else:
        rec["type"] = "non_regular"
        rec["status"] = "non_regular"
        rec["sha256"] = None
    return rec


def group_hardlinks(files):
    groups = OrderedDict()
    for rec in files:
        if rec.get("type") == "file" and rec.get("nlink", 0) > 1:
            key = (rec["inode"][0], rec["inode"][1])
            groups.setdefault(key, []).append(rec["rel"])
    return [{"inode": [k[0], k[1]], "members": sorted(v)}
            for k, v in sorted(groups.items(), key=lambda kv: kv[1][0]) if len(v) > 1]


# --------------------------------------------------------------------------- #
# Dedup (by content hash) and version candidates (normalized stem heuristic)
# --------------------------------------------------------------------------- #

def dedup(files):
    by_hash = OrderedDict()
    for rec in files:
        if rec.get("sha256"):
            by_hash.setdefault(rec["sha256"], []).append(rec["rel"])
    groups = []
    for h, members in by_hash.items():
        members = sorted(set(members))
        if len(members) > 1:
            groups.append({"sha256": h, "members": members, "count": len(members)})
    groups.sort(key=lambda g: g["members"][0])
    return groups


_TOKEN_RE = re.compile(
    r"(?i)(?:^|[\s._-])"
    r"(?:v(?:er(?:sion)?)?[\s._-]?\d+(?:\.\d+)*|rev[\s._-]?\d+|r\d+|"
    r"final|draft|old|new|latest|bak(?:ckup)?|copy(?:[\s._-]?\d+)?|"
    r"\d{8}|\d{4}[-_]\d{2}[-_]\d{2})$"
)


def normalize_stem(stem):
    s = re.sub(r"[\(\)\[\]]", " ", stem)
    s = re.sub(r"[-_.]+", " ", s)
    s = _TOKEN_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def version_candidates(files):
    groups = OrderedDict()
    for rec in files:
        if rec.get("type") != "file":
            continue
        stem = rec.get("stem", "")
        norm = normalize_stem(stem)
        if not norm or norm in {"", " ", "report", "document"}:
            continue
        groups.setdefault(norm, []).append(rec)
    out = []
    for norm, members in groups.items():
        members = sorted(members, key=lambda r: (r.get("mtime_ts") or 0, r["rel"]))
        if len(members) < 2:
            continue
        distinct_hash = {m.get("sha256") for m in members if m.get("sha256")}
        out.append({
            "stem_norm": norm,
            "members": [m["rel"] for m in members],
            "order_by": "mtime_asc",
            "heuristic": True,
            "distinct_content_hashes": len(distinct_hash),
            "note": "candidate chain from normalized stem; verify against content diff",
        })
    out.sort(key=lambda g: g["stem_norm"])
    return out


# --------------------------------------------------------------------------- #
# Aliases (symlinks + hardlinks)
# --------------------------------------------------------------------------- #

def build_aliases(files, hardlink_groups):
    aliases = []
    for rec in files:
        if rec.get("type") == "symlink":
            aliases.append({
                "path": rec["rel"],
                "kind": "symlink",
                "target": rec.get("link_target"),
                "broken": rec.get("link_broken", False),
            })
    for g in hardlink_groups:
        aliases.append({"kind": "hardlink_group", "members": g["members"]})
    aliases.sort(key=lambda a: (a.get("kind", ""), a.get("path") or a.get("members", [""])[0]))
    return aliases


# --------------------------------------------------------------------------- #
# Catalog parsing: JSON / CSV / BibTeX / RIS / SQLite
# --------------------------------------------------------------------------- #

def parse_catalogs(root, files):
    catalogs = []
    parse_issues = []
    for rec in files:
        if rec.get("type") != "file":
            continue
        if rec.get("ext") not in CATALOG_EXT:
            continue
        if rec.get("status") == "too_large":
            parse_issues.append({"item": rec["rel"], "detail": "too_large_for_catalog"})
            continue
        parsed = parse_one_catalog(rec)
        catalogs.append(parsed)
        if parsed.get("status") != "ok":
            parse_issues.append({"item": rec["rel"], "detail": parsed.get("status")})
    return catalogs, parse_issues


def parse_one_catalog(rec):
    path = rec["path"]
    ext = rec["ext"]
    try:
        if ext == ".json":
            return _parse_json_catalog(path, rec)
        if ext == ".csv":
            return _parse_csv_catalog(path, rec)
        if ext in (".bib", ".bibtex"):
            return _parse_bibtex_catalog(path, rec)
        if ext == ".ris":
            return _parse_ris_catalog(path, rec)
        if ext in (".db", ".sqlite", ".sqlite3"):
            return _parse_sqlite_catalog(path, rec)
        return {"source": rec["rel"], "format": ext, "status": "unsupported", "rows": []}
    except Exception as exc:  # noqa: BLE001
        return {"source": rec["rel"], "format": ext, "status": "parse_error:" + str(exc), "rows": []}


def _rows_to_entries(rows):
    entries = []
    for r in rows:
        if isinstance(r, dict):
            entries.append(r)
        elif isinstance(r, (list, tuple)):
            entries.append({"value": r})
        else:
            entries.append({"value": r})
    return entries


def _parse_json_catalog(path, rec):
    obj, status = _read_json_loose(path)
    if obj is None:
        return {"source": rec["rel"], "format": "json", "status": status, "rows": []}
    rows = []
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        for k in ("files", "items", "entries", "records", "rows", "documents", "data"):
            if isinstance(obj.get(k), list):
                rows = obj[k]
                break
        if not rows:
            rows = [obj]
    return {"source": rec["rel"], "format": "json", "status": "ok", "rows": _rows_to_entries(rows)}


def _parse_csv_catalog(path, rec):
    text, enc, _ = read_text_file(path)
    if text is None:
        return {"source": rec["rel"], "format": "csv", "status": "unreadable", "rows": []}
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [r for r in reader]
        return {"source": rec["rel"], "format": "csv", "status": "ok", "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"source": rec["rel"], "format": "csv", "status": "csv_error:" + str(exc), "rows": []}


def _parse_bibtex_catalog(path, rec):
    text, enc, _ = read_text_file(path)
    if text is None:
        return {"source": rec["rel"], "format": "bibtex", "status": "unreadable", "rows": []}
    rows = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+)\s*,", text, re.S):
        head = m.group(2).strip()
        body_start = m.end()
        depth = 0
        end = body_start
        for i in range(body_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        body = text[body_start:end]
        row = {"type": m.group(1), "key": head}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*[,}]", body, re.S):
            row[fm.group(1).lower()] = fm.group(2).strip()
        rows.append(row)
    return {"source": rec["rel"], "format": "bibtex", "status": "ok", "rows": rows}


def _parse_ris_catalog(path, rec):
    text, enc, _ = read_text_file(path)
    if text is None:
        return {"source": rec["rel"], "format": "ris", "status": "unreadable", "rows": []}
    rows = []
    cur = None
    for line in text.splitlines():
        if len(line) >= 6 and line[2:4].strip() == "-":
            tag, val = line[:2].strip(), line[6:].strip()
        elif len(line) >= 2 and line[:2].strip() == "-":
            tag, val = line[:2].strip(), line[2:].strip()
        else:
            tag, val = "XX", line.strip()
        if tag == "TY":
            cur = {"type": val}
            rows.append(cur)
        elif cur is not None:
            key = tag.lower()
            cur[key] = (cur.get(key) + "; " if cur.get(key) else "") + val
    return {"source": rec["rel"], "format": "ris", "status": "ok", "rows": rows}


def _parse_sqlite_catalog(path, rec):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    except Exception as exc:  # noqa: BLE001
        return {"source": rec["rel"], "format": "sqlite", "status": "sqlite_open_error:" + str(exc), "rows": []}
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        rows = []
        for t in tables:
            try:
                cols = [d[1] for d in cur.execute("PRAGMA table_info(%s)" % _q(t)).fetchall()]
                for r in cur.execute("SELECT * FROM %s" % _q(t)).fetchmany(1000):
                    rows.append({"table": t, "row": list(r)} | (dict(zip(cols, r)) if cols else {}))
            except Exception as exc:  # noqa: BLE001
                rows.append({"table": t, "error": "sqlite_query_error:" + str(exc)})
        con.close()
        return {"source": rec["rel"], "format": "sqlite", "status": "ok", "rows": rows}
    except Exception as exc:  # noqa: BLE001
        con.close()
        return {"source": rec["rel"], "format": "sqlite", "status": "sqlite_query_error:" + str(exc), "rows": []}


def _q(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


# --------------------------------------------------------------------------- #
# Administrative version notes
# --------------------------------------------------------------------------- #

def parse_version_notes(root, files):
    notes = []
    for rec in files:
        if rec.get("type") != "file":
            continue
        name = os.path.basename(rec["path"])
        if not VERSION_NOTE_NAME_RE.search(name) and rec.get("ext") not in (".json", ".csv"):
            continue
        text, enc, _ = read_text_file(rec["path"])
        if text is None:
            notes.append({"source": rec["rel"], "status": "unreadable", "entries": []})
            continue
        entries = []
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"(?i)^\s*([^#:=|]+?)\s*[:=]\s*(v?\d+(?:\.\d+)*)\s*$", line)
            if m:
                entries.append({"file": m.group(1).strip(), "version": m.group(2)})
        if entries:
            notes.append({"source": rec["rel"], "status": "parsed", "entries": entries})
    return notes


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def reconcile(files, catalogs):
    file_index = {}
    for rec in files:
        if rec.get("type") != "file":
            continue
        keys = {rec["rel"], os.path.basename(rec["rel"]), rec.get("stem", ""), rec.get("sha256") or ""}
        keys = {k for k in keys if k}
        for k in keys:
            file_index.setdefault(k, []).append(rec["rel"])
        if rec.get("sha256"):
            file_index.setdefault("sha:" + rec["sha256"], []).append(rec["rel"])

    matches, mismatches, missing, seen = [], [], [], set()
    for cat in catalogs:
        for row in cat["rows"]:
            ident = None
            for k in ("path", "file", "filename", "rel", "rel_path"):
                if row.get(k):
                    ident = str(row[k])
                    break
            if ident is None:
                for k in ("sha256", "hash"):
                    if row.get(k):
                        ident = "sha:" + str(row[k])
                        break
            if ident is None:
                for k in ("key", "id", "doi", "title"):
                    if row.get(k):
                        ident = str(row[k])
                        break
            if ident is None:
                continue
            found = file_index.get(ident) or file_index.get(os.path.basename(ident))
            if found:
                target = found[0]
                seen.add(target)
                conflict = _row_conflict(row, target, files)
                if conflict:
                    mismatches.append({"entry": row, "file": target, "conflict": conflict, "source": cat["source"]})
                else:
                    matches.append({"entry": row, "file": target, "source": cat["source"]})
            else:
                missing.append({"entry": row, "source": cat["source"]})

    all_files = {rec["rel"] for rec in files if rec.get("type") == "file"}
    orphaned = sorted(all_files - seen)
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "method": "identifier match on path/filename/hash/id/key; heuristic",
    }


def _row_conflict(row, rel, files):
    for rec in files:
        if rec["rel"] == rel:
            if row.get("size") not in (None, "") and str(row["size"]) != str(rec.get("size")):
                return "size mismatch (catalog %s vs file %s)" % (row["size"], rec.get("size"))
            if row.get("sha256") and row.get("hash") and rec.get("sha256") and str(row.get("sha256")) != rec.get("sha256"):
                return "hash mismatch"
    return None


# --------------------------------------------------------------------------- #
# Index and mechanical Q&A
# --------------------------------------------------------------------------- #

def build_index(files, duplicate_groups, version_candidates, aliases):
    index = {
        "by_path": OrderedDict(),
        "by_hash": OrderedDict(),
        "by_stem_norm": OrderedDict(),
        "version_groups": [g["stem_norm"] for g in version_candidates],
        "alias_groups": aliases,
    }
    for rec in files:
        index["by_path"][rec["rel"]] = {
            "type": rec.get("type"),
            "size": rec.get("size"),
            "sha256": rec.get("sha256"),
            "status": rec.get("status"),
            "mtime": rec.get("mtime"),
        }
        if rec.get("sha256"):
            index["by_hash"].setdefault(rec["sha256"], []).append(rec["rel"])
    return index


_QA_HANDLERS = (
    (r"count|数量|多少个|total|一共|几", "summary_counts"),
    (r"duplicate|重复|相同内容|相同文件", "duplicates"),
    (r"missing|缺失|找不到|缺", "missing"),
    (r"broken|断链|失效|死链|symlink", "broken_links"),
    (r"version|版本|版本链", "versions"),
    (r"author|作者", "authors"),
    (r"year|年份|date|日期", "years"),
    (r"orphan|孤儿|未登记|不在", "orphaned"),
)


def collect_questions(case_obj, task_text):
    qs = []
    if isinstance(case_obj, dict):
        for k in ("questions", "queries", "tasks", "questions_to_answer"):
            v = case_obj.get(k)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        qs.append(item.get("question") or item.get("q") or item.get("text"))
                    elif isinstance(item, str):
                        qs.append(item)
            elif isinstance(v, str):
                qs.append(v)
    if task_text:
        for line in task_text.splitlines():
            s = line.strip()
            if re.match(r"^\s*(?:[0-9]+[.、)]|[-*])\s*.+[?？]\s*$", s):
                qs.append(s)
            elif re.match(r"^\s*(?:Q|问题)[:：]\s*", s, re.I):
                qs.append(s)
    return [q for q in qs if q][:20]


def mechanical_answers(case_obj, task_text, summary, duplicate_groups, version_candidates,
                       recon, aliases, catalogs):
    questions = collect_questions(case_obj, task_text)
    answers = []
    for q in questions:
        method, payload = None, None
        for pat, handler in _QA_HANDLERS:
            if re.search(pat, q, re.I):
                method = handler
                break
        if method == "summary_counts":
            payload = summary
        elif method == "duplicates":
            payload = duplicate_groups
        elif method == "missing":
            payload = recon["missing"]
        elif method == "broken_links":
            payload = [a for a in aliases if a.get("broken")]
        elif method == "versions":
            payload = version_candidates
        elif method == "orphaned":
            payload = recon["orphaned"]
        elif method == "authors":
            payload = _aggregate_field(catalogs, "author")
        elif method == "years":
            payload = _aggregate_field(catalogs, "year")
        answers.append({
            "question": q,
            "answer": payload,
            "method": "mechanical" if method else "agent",
            "handler": method,
        })
    return answers


def _aggregate_field(catalogs, field):
    found = []
    for cat in catalogs:
        for row in cat["rows"]:
            v = row.get(field) or row.get(field + "s")
            if v:
                found.append({"source": cat["source"], field: v})
    return found


# --------------------------------------------------------------------------- #
# Mechanical evidence (locator + machine fact, never invented text)
# --------------------------------------------------------------------------- #

def mechanical_evidence(files, duplicate_groups, version_candidates, aliases, recon, version_notes):
    ev = []
    n = 0

    def add(item, locator, quote, status="confirmed"):
        nonlocal n
        n += 1
        ev.append({
            "id": "e%d" % n,
            "item": item,
            "locator": locator,
            "quote": quote,
            "quote_kind": "machine",
            "origin": "mechanical",
            "status": status,
            "key": True,
        })

    for g in duplicate_groups:
        add("duplicate group by SHA-256", "library_model.json#duplicate_groups",
            "sha256=%s members=%s" % (g["sha256"][:16], ",".join(g["members"][:8])))
    for g in version_candidates:
        add("version candidate (heuristic)", "library_model.json#version_candidates",
            "stem_norm=%s members=%s" % (g["stem_norm"], ",".join(g["members"][:8])))
    for a in aliases:
        if a.get("broken"):
            add("broken symlink", a.get("path", ""), "symlink -> %s (target missing)" % a.get("target"))
    for m in recon["mismatches"]:
        add("reconciliation mismatch", m.get("file", ""), m.get("conflict", ""))
    for m in recon["missing"]:
        add("catalog entry missing on disk", m.get("source", ""), json.dumps(m.get("entry", {}), ensure_ascii=False)[:300])
    return ev


# --------------------------------------------------------------------------- #
# Model assembly
# --------------------------------------------------------------------------- #

def build_summary(files, dirs, dup_groups, ver_cands, aliases, recon, catalogs, hardlink_groups):
    symlinks = [r for r in files if r.get("type") == "symlink"]
    broken = [r for r in symlinks if r.get("link_broken")]
    hashed = [r for r in files if r.get("sha256")]
    too_large = [r for r in files if r.get("status") == "too_large"]
    unreadable = [r for r in files if r.get("status") in ("unreadable",)]
    return OrderedDict([
        ("files", len([r for r in files if r.get("type") == "file"])),
        ("directories", len(dirs)),
        ("empty_directories", len([d for d in dirs if d.get("empty")])),
        ("symlinks", len(symlinks)),
        ("broken_symlinks", len(broken)),
        ("non_regular", len([r for r in files if r.get("type") == "non_regular"])),
        ("hardlink_groups", len(hardlink_groups)),
        ("duplicate_groups", len(dup_groups)),
        ("duplicate_extra_files", sum(g["count"] - 1 for g in dup_groups)),
        ("version_candidate_groups", len(ver_cands)),
        ("catalog_files", len(catalogs)),
        ("files_hashed", len(hashed)),
        ("files_too_large_for_hash", len(too_large)),
        ("files_unreadable", len(unreadable)),
        ("bytes_total", sum(r.get("size") or 0 for r in files)),
        ("reconciliation", {
            "matches": len(recon["matches"]),
            "mismatches": len(recon["mismatches"]),
            "missing": len(recon["missing"]),
            "orphaned": len(recon["orphaned"]),
        }),
    ])


def new_model(case_id):
    return OrderedDict([
        ("schema_version", SCHEMA_VERSION),
        ("case_id", case_id),
        ("input_root", None),
        ("files", []),
        ("directories", []),
        ("summary", {}),
        ("duplicate_groups", []),
        ("version_candidates", []),
        ("version_notes", []),
        ("aliases", []),
        ("hardlink_groups", []),
        ("catalogs", []),
        ("catalog_parse_issues", []),
        ("reconciliation", {}),
        ("index", {}),
        ("answers", []),
        ("evidence", []),
        ("uncertainty", []),
        ("pending_human", []),
        ("errors", []),
        ("degradations", []),
        ("change_log", []),
        ("artifacts", []),
    ])


# --------------------------------------------------------------------------- #
# Rendering (pure projection of plan + model)
# --------------------------------------------------------------------------- #

def render_result(plan, model, runtime):
    computed = OrderedDict([
        ("schema_version", SCHEMA_VERSION),
        ("plan", "plan.json"),
        ("model", "library_model.json"),
        ("summary", model["summary"]),
        ("files", model["files"]),
        ("directories", model["directories"]),
        ("duplicate_groups", model["duplicate_groups"]),
        ("version_candidates", model["version_candidates"]),
        ("version_notes", model["version_notes"]),
        ("aliases", model["aliases"]),
        ("hardlink_groups", model["hardlink_groups"]),
        ("catalogs", model["catalogs"]),
        ("catalog_parse_issues", model["catalog_parse_issues"]),
        ("reconciliation", model["reconciliation"]),
        ("index", model["index"]),
        ("answers", model["answers"]),
        ("artifacts", model["artifacts"]),
    ])
    for f in plan.get("contract_clashes", []):
        computed["_contract_" + f] = plan["contract_values"].get(f)

    result = OrderedDict()
    result["case_id"] = plan["case_id"]
    for f in plan.get("contract_fields", []):
        if f not in RESERVED_KEYS:
            result[f] = plan["contract_values"].get(f)
    result["status"] = plan.get("status", "partial")
    result["sources"] = [e for e in model["evidence"] if e.get("key")]
    result["uncertainty"] = model["uncertainty"]
    result["pending_human"] = model["pending_human"]
    result["change_log"] = model["change_log"]
    result["errors"] = model["errors"]
    result["degradations"] = model["degradations"]
    result["runtime"] = runtime
    result["limitations"] = plan.get("limitations", [])
    result["computed"] = computed
    return result


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #

def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_manifest_csv(path, files):
    cols = ["rel", "type", "size", "mode", "mtime", "sha256", "status",
            "link_target", "link_broken", "nlink", "ext", "stem"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for rec in sorted(files, key=lambda r: r["rel"]):
            w.writerow([rec.get(c, "") for c in cols])


def build_tree_text(files, dirs):
    nodes = {}
    for d in dirs:
        nodes[d["rel"]] = ("dir", d.get("empty"))
    for r in files:
        nodes[r["rel"]] = ("file", r)
    roots = OrderedDict()
    for rel in sorted(nodes):
        parts = rel.split("/")
        cur = roots
        for i, p in enumerate(parts):
            is_last = i == len(parts) - 1
            if is_last:
                cur[p] = {"__self__": nodes[rel]}
            else:
                cur = cur.setdefault(p, {})
    lines = []

    def walk(node, depth):
        for name in sorted(node):
            child = node[name]
            if "__self__" in child:
                kind, payload = child["__self__"]
                prefix = "  " * depth
                if kind == "dir":
                    mark = " [空目录]" if payload else ""
                    lines.append(prefix + name + "/" + mark)
                else:
                    tag = ""
                    if payload.get("type") == "symlink":
                        tag = " -> " + (payload.get("link_target") or "")
                        if payload.get("link_broken"):
                            tag += " [断链]"
                    elif payload.get("status") == "too_large":
                        tag = " [超大未哈希]"
                    elif payload.get("status") == "unreadable":
                        tag = " [不可读]"
                    elif payload.get("type") == "non_regular":
                        tag = " [非常规文件]"
                    lines.append(prefix + name + tag)
            else:
                lines.append("  " * depth + name + "/")
                walk(child, depth + 1)

    walk(roots, 0)
    return "\n".join(lines) + "\n"


def write_manifest_md(case_id, summary, artifacts, plan, recon):
    lines = [
        "# 科研资料库交接报告",
        "",
        "- case_id: `%s`" % case_id,
        "- schema_version: `%s`" % SCHEMA_VERSION,
        "- 生成时间: %s" % now_iso(),
        "",
        "## 摘要",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 对账",
        "",
        "```json",
        json.dumps(recon, ensure_ascii=False, indent=2)[:6000],
        "```",
        "",
        "## 中间表示（可复核）",
        "",
        "- `plan.json`：输入契约、规则、执行步骤与限制（意图层）。",
        "- `library_model.json`：文件、去重、版本、别名、对账、索引与证据（事实层）。",
        "- `result.json` 由上述两者确定性渲染；`--verify` 可证明二者一致。",
        "",
        "## 产物清单",
        "",
    ]
    for a in artifacts:
        lines.append("- `%s`：%s" % (a["path"], a["purpose"]))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def write_outputs(plan, model, runtime, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    artifacts = [
        {"path": "result.json", "purpose": "主结果（contract 全字段 + status/sources/uncertainty/change_log）"},
        {"path": "plan.json", "purpose": "中间表示·意图层：契约、规则、步骤、限制"},
        {"path": "library_model.json", "purpose": "中间表示·事实层：文件/去重/版本/别名/对账/索引/证据"},
        {"path": "library_manifest.csv", "purpose": "文件清单（每行一个源文件，含 SHA-256）"},
        {"path": "directory_tree.txt", "purpose": "目录树（空目录/断链/超大文件标注）"},
        {"path": "dedup_version.json", "purpose": "去重组、版本候选、版本说明、别名"},
        {"path": "recovery_report.json", "purpose": "恢复对账 matches/mismatches/missing/orphaned"},
        {"path": "library_index.json", "purpose": "可检索索引（by_path/by_hash/by_stem_norm）"},
        {"path": "library_manifest.md", "purpose": "交接报告"},
        {"path": "operation_log.jsonl", "purpose": "操作时间线（time/actor/step/detail）"},
    ]
    # Set artifacts *before* rendering so result.json is an exact projection of the
    # model written to disk (this is what `--verify` later re-derives and diffs).
    model["artifacts"] = artifacts
    result = render_result(plan, model, runtime)

    # dedup_version.json (self-contained sub-view)
    dedup_version = OrderedDict([
        ("duplicate_groups", model["duplicate_groups"]),
        ("version_candidates", model["version_candidates"]),
        ("version_notes", model["version_notes"]),
        ("aliases", model["aliases"]),
    ])
    recovery = OrderedDict([
        ("reconciliation", model["reconciliation"]),
        ("catalogs", model["catalogs"]),
        ("catalog_parse_issues", model["catalog_parse_issues"]),
    ])

    write_json(os.path.join(output_dir, "plan.json"), plan)
    write_json(os.path.join(output_dir, "library_model.json"), model)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(output_dir, "dedup_version.json"), dedup_version)
    write_json(os.path.join(output_dir, "recovery_report.json"), recovery)
    write_json(os.path.join(output_dir, "library_index.json"), model["index"])
    write_manifest_csv(os.path.join(output_dir, "library_manifest.csv"), model["files"])
    with open(os.path.join(output_dir, "directory_tree.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_tree_text(model["files"], model["directories"]))
    with open(os.path.join(output_dir, "library_manifest.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(write_manifest_md(plan["case_id"], model["summary"], artifacts, plan, model["reconciliation"]))
    with open(os.path.join(output_dir, "operation_log.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for entry in model["change_log"]:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Working copies in /app/work so the model can inspect/append without touching output.
    write_json(os.path.join(work_dir, "plan.json"), plan)
    write_json(os.path.join(work_dir, "library_model.json"), model)
    with open(os.path.join(work_dir, "operation_log.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for entry in model["change_log"]:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return result


def run_pipeline(args, skeleton_only=False):
    t0 = time.monotonic()
    d = discover_case(args.case_json, args.task_path)
    plan = build_plan(d)
    model = new_model(d["case_id"])
    model["input_root"] = os.path.abspath(args.input)
    model["errors"] = d["errors"]
    model["degradations"] = d["degradations"]
    model["uncertainty"] = d["uncertainty"]
    # Honest status for missing/empty inputs, before any artifact is written.
    if any(e.get("item") == "case.json" and e.get("detail") == "missing" for e in d["errors"]):
        plan["status"] = "input_error"
    model["change_log"].append(log_entry(
        "script", "discover",
        "case_id=%s contract_source=%s fields=%s status=%s" % (
            d["case_id"], d["contract_source"], ",".join(d["contract_fields"]) or "(none)",
            plan["status"])))
    plan["steps"].append({"id": "s0", "name": "skeleton", "status": "done"})

    if skeleton_only:
        runtime = {
            "seconds": round(time.monotonic() - t0, 3),
            "skeleton_only": True,
            "schema_version": SCHEMA_VERSION,
            "deterministic": True,
            "budget_seconds": BUDGET_SECONDS,
            "budget_requests": BUDGET_REQUESTS,
        }
        write_outputs(plan, model, runtime, args.output, args.work)
        return 0

    files, dirs, scan_errors, scan_degrad = scan_tree(
        args.input, exclude_paths=[args.case_json, args.task_path])
    model["files"] = files
    model["directories"] = dirs
    model["errors"].extend(scan_errors)
    model["degradations"].extend(scan_degrad)
    model["change_log"].append(log_entry(
        "script", "scan", "%d files, %d dirs" % (len(files), len(dirs))))
    # No material files and no catalog files -> honest insufficient evidence.
    if not files and not dirs and plan["status"] != "input_error":
        plan["status"] = "insufficient_evidence"
        plan.setdefault("limitations", []).append(
            "input contains no material files or directories to organize")

    hardlink_groups = group_hardlinks(files)
    dup_groups = dedup(files)
    ver_cands = version_candidates(files)
    aliases = build_aliases(files, hardlink_groups)
    catalogs, cat_issues = parse_catalogs(args.input, files)
    version_notes = parse_version_notes(args.input, files)
    recon = reconcile(files, catalogs)
    index = build_index(files, dup_groups, ver_cands, aliases)
    summary = build_summary(files, dirs, dup_groups, ver_cands, aliases, recon, catalogs, hardlink_groups)
    answers = mechanical_answers(d["case_obj"], d["task_text"], summary, dup_groups,
                                 ver_cands, recon, aliases, catalogs)
    evidence = mechanical_evidence(files, dup_groups, ver_cands, aliases, recon, version_notes)

    model["hardlink_groups"] = hardlink_groups
    model["duplicate_groups"] = dup_groups
    model["version_candidates"] = ver_cands
    model["aliases"] = aliases
    model["catalogs"] = catalogs
    model["catalog_parse_issues"] = cat_issues
    model["degradations"].extend(cat_issues)
    model["version_notes"] = version_notes
    model["reconciliation"] = recon
    model["index"] = index
    model["summary"] = summary
    model["answers"] = answers
    model["evidence"] = evidence
    model["change_log"].append(log_entry(
        "script", "analyze",
        "dup=%d ver=%d aliases=%d catalogs=%d" % (
            len(dup_groups), len(ver_cands), len(aliases), len(catalogs))))
    plan["steps"].append({"id": "s1", "name": "inventory_analyze", "status": "done"})

    runtime = {
        "seconds": round(time.monotonic() - t0, 3),
        "skeleton_only": False,
        "schema_version": SCHEMA_VERSION,
        "deterministic": True,
        "budget_seconds": BUDGET_SECONDS,
        "budget_requests": BUDGET_REQUESTS,
    }
    write_outputs(plan, model, runtime, args.output, args.work)
    _print_summary(model, runtime)
    return 0


def build_plan(d):
    plan = OrderedDict([
        ("schema_version", SCHEMA_VERSION),
        ("case_id", d["case_id"]),
        ("status", "partial"),
        ("contract_source", d["contract_source"]),
        ("contract_fields", d["contract_fields"]),
        ("contract_values", d["contract_values"]),
        ("contract_clashes", d["contract_clashes"]),
        ("reserved_keys", sorted(RESERVED_KEYS)),
        ("rules", d["rules"]),
        ("steps", []),
        ("limitations", []),
        ("created_at", now_iso()),
        ("budget_seconds", BUDGET_SECONDS),
        ("budget_requests", BUDGET_REQUESTS),
        ("skeleton_seconds", SKELETON_SECONDS),
    ])
    return plan


def _print_summary(model, runtime):
    s = model["summary"]
    print("finalize: %d files, %d dup groups, %d version candidates, %d broken symlinks, "
          "reconcile(m=%d/x=%d/miss=%d/orph=%d) -> %s" % (
              s.get("files", 0), s.get("duplicate_groups", 0), s.get("version_candidate_groups", 0),
              s.get("broken_symlinks", 0),
              s["reconciliation"]["matches"], s["reconciliation"]["mismatches"],
              s["reconciliation"]["missing"], s["reconciliation"]["orphaned"],
              "result.json"))


# --------------------------------------------------------------------------- #
# Update (incremental patch from the Agent)
# --------------------------------------------------------------------------- #

def merge_patch(patch, plan, model):
    model["change_log"].append(log_entry("agent", "update", str(patch.get("notes", ""))[:300]))
    if patch.get("status") in STATUS_ENUM:
        plan["status"] = patch["status"]
    if isinstance(patch.get("contract_values"), dict):
        for k, v in patch["contract_values"].items():
            if k in plan["contract_fields"]:
                plan["contract_values"][k] = v
    if isinstance(patch.get("limitations"), list):
        plan.setdefault("limitations", []).extend(patch["limitations"])
    if isinstance(patch.get("rules"), list):
        plan.setdefault("rules", []).extend(patch["rules"])
    if isinstance(patch.get("sources"), list):
        for e in patch["sources"]:
            if not isinstance(e, dict):
                continue
            e = dict(e)
            e.setdefault("id", "e%d" % (len(model["evidence"]) + 1))
            e.setdefault("origin", "agent")
            e.setdefault("quote_kind", "text")
            e.setdefault("key", True)
            e.setdefault("status", "unverified")
            model["evidence"].append(e)
    for key in ("uncertainty", "pending_human"):
        if isinstance(patch.get(key), list):
            model[key].extend(patch[key])
    if isinstance(patch.get("answers"), list):
        model["answers"] = patch["answers"]  # Agent's authoritative answer set
    if isinstance(patch.get("version_notes_align"), list):
        for va in patch["version_notes_align"]:
            for note in model.get("version_notes", []):
                if va.get("source") and note.get("source") == va["source"]:
                    note["aligned_to"] = va.get("aligned_to")
                    note["status"] = va.get("status", "linked")
    return plan, model


def run_update(args):
    plan_path = os.path.join(args.output, "plan.json")
    model_path = os.path.join(args.output, "library_model.json")
    if not (os.path.exists(plan_path) and os.path.exists(model_path)):
        # No base model yet: build it first, then apply the patch.
        run_pipeline(args, skeleton_only=False)
    plan = load_json_file(plan_path)
    model = load_json_file(model_path)
    try:
        patch = load_json_file(args.patch)
    except Exception as exc:  # noqa: BLE001
        model["errors"].append({"item": args.patch, "detail": "bad patch json: " + str(exc)})
        patch = {}
    merge_patch(patch, plan, model)
    runtime = {
        "seconds": 0.0,
        "schema_version": SCHEMA_VERSION,
        "deterministic": True,
        "updated": True,
        "budget_seconds": BUDGET_SECONDS,
        "budget_requests": BUDGET_REQUESTS,
    }
    write_outputs(plan, model, runtime, args.output, args.work)
    return 0


# --------------------------------------------------------------------------- #
# Check / verify
# --------------------------------------------------------------------------- #

def run_check(result_path, case_json_path):
    if not os.path.exists(result_path):
        print("FAIL: result.json not found: " + result_path)
        return 1
    try:
        result = load_json_file(result_path)
    except Exception as exc:  # noqa: BLE001
        print("FAIL: result.json not valid JSON: " + str(exc))
        return 1
    ok = True
    if not result.get("case_id"):
        print("FAIL: case_id empty")
        ok = False
    if result.get("status") not in STATUS_ENUM:
        print("FAIL: status invalid: %r" % result.get("status"))
        ok = False
    for key in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(key), list):
            print("FAIL: %s missing or not a list" % key)
            ok = False
    # Contract coverage: every field in case.json's contract must be present.
    if os.path.exists(case_json_path):
        case_obj, _ = _read_json_loose(case_json_path)
        if isinstance(case_obj, dict):
            contract = {}
            for k in CONTRACT_KEYS:
                if k in case_obj and case_obj[k] not in (None, {}):
                    contract = case_obj[k]
                    break
            fields, _ = normalize_contract(contract)
            for f in fields:
                if f in RESERVED_KEYS:
                    if "_contract_" + f not in result.get("computed", {}):
                        print("FAIL: contract field %s not backfilled into computed" % f)
                        ok = False
                elif f not in result:
                    print("FAIL: contract field %s missing from result.json" % f)
                    ok = False
    print("PASS" if ok else "FAIL", result_path)
    return 0 if ok else 1


def _canonicalize(result):
    r = json.loads(json.dumps(result, ensure_ascii=False))
    r.pop("runtime", None)
    for e in r.get("change_log", []):
        e.pop("time", None)
    return r


def run_verify(result_path, work_dir):
    base = os.path.dirname(os.path.abspath(result_path))
    plan_path = os.path.join(base, "plan.json")
    model_path = os.path.join(base, "library_model.json")
    if not (os.path.exists(plan_path) and os.path.exists(model_path)):
        print("FAIL: plan.json/library_model.json not found next to result.json")
        return 1
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f, object_pairs_hook=OrderedDict)
    with open(model_path, "r", encoding="utf-8") as f:
        model = json.load(f, object_pairs_hook=OrderedDict)
    with open(result_path, "r", encoding="utf-8") as f:
        stored = json.load(f, object_pairs_hook=OrderedDict)
    runtime = {"seconds": 0.0, "schema_version": SCHEMA_VERSION, "deterministic": True}
    expected = render_result(plan, model, runtime)
    a, b = _canonicalize(stored), _canonicalize(expected)
    if a == b:
        print("PASS: result.json is the exact projection of plan.json + library_model.json")
        return 0
    # Diff a few top-level keys for a useful message.
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            print("DRIFT at key: %s" % k)
    print("FAIL: result.json drifted from its intermediate representation")
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="document-library repair: inventory, dedup, version chains, aliases, "
                    "reconciliation, index, verifiable intermediate representation.")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-json", default="/input/case.json")
    p.add_argument("--task-path", default="/input/task.md")
    p.add_argument("--skeleton-only", action="store_true",
                   help="write plan.json + partial result.json only (first 3 minutes)")
    p.add_argument("--update", metavar="PATCH_JSON",
                   help="merge an incremental semantic patch and re-render")
    p.add_argument("--check", metavar="RESULT_JSON", help="validate result.json against the contract")
    p.add_argument("--verify", metavar="RESULT_JSON", help="re-render from plan/model and diff")
    p.add_argument("--version", action="store_true")
    args = p.parse_args(argv)

    if args.version:
        print(SCHEMA_VERSION)
        return 0
    if args.check:
        return run_check(args.check, args.case_json)
    if args.verify:
        return run_verify(args.verify, args.work)
    if args.update:
        args.patch = args.update
        return run_update(args)
    return run_pipeline(args, skeleton_only=args.skeleton_only)


if __name__ == "__main__":
    raise SystemExit(main())
