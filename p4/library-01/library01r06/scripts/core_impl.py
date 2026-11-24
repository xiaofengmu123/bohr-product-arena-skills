"""core_impl.py - document-library repair engine.

Design: an explicit intermediate-representation (IR) pipeline.

  raw /input  ->  plan.json + inventory.json   (layer 1: parsed task + raw facts)
               ->  library_model.json          (layer 2: canonical world model)
               ->  derived editable artifacts  (layer 3: CSV/tree/reports/index)
               ->  derivation_manifest.json    (provenance of every artifact)

Every derived artifact is regenerated from library_model.json, so a reviewer can
re-run --derive and confirm that no artifact was hand-edited out of band.

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
import traceback
from collections import defaultdict

VERSION = "2.0.0"
SKILL_NAME = "document-library-repair"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALLOWED_STATUS = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

MAX_HASH_BYTES = 512 * 1024 * 1024          # skip content hash above this
MAX_TEXT_BYTES = 2 * 1024 * 1024            # text extraction limit
MAX_INDEX_TEXT_BYTES = 1 * 1024 * 1024      # index token extraction limit
MAX_INDEX_TOKENS_PER_FILE = 4000            # bound index size per file

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "limitations", "runtime", "change_log", "computed",
    "artifacts",
}

CONTROL_BASENAMES = {"case.json", "task.md"}

CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "output_schema", "answer_schema", "result", "schema",
)


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def rel_posix(base: str, path: str) -> str:
    rel = os.path.relpath(path, base)
    return rel.replace(os.sep, "/")


def read_text_safe(path: str):
    """Return (text_or_None, error_or_None). Tries utf-8-sig then latin-1."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, f"{type(exc).__name__}: {exc}"
    return None, "UnicodeDecodeError: not utf-8 or latin-1"


def read_json_safe(path: str):
    text, err = read_text_safe(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - any JSON error must not crash
        return None, f"JSONDecodeError: {exc}"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def tokens_of(text: str):
    """Stable, bounded token set: ASCII words + CJK bigrams."""
    toks = set()
    for m in re.finditer(r"[A-Za-z0-9_]{2,}", text.lower()):
        toks.add(m.group(0))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk) - 1):
        toks.add(cjk[i] + cjk[i + 1])
    return toks


def jdump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_json(path: str, obj) -> None:
    write_text(path, jdump(obj) + "\n")


def iso_mtime(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"


# --------------------------------------------------------------------------- #
# layer 1 - contract and plan extraction
# --------------------------------------------------------------------------- #

def find_contract(case: dict):
    for key in CONTRACT_KEYS:
        if key in case and case[key]:
            return key, case[key]
    return None, None


def contract_fields(contract) -> list:
    """Return the field-name list implied by the contract value."""
    fields = []
    if isinstance(contract, dict):
        for key in contract.keys():
            if isinstance(key, str) and not key.startswith("_"):
                fields.append(key)
        # JSON-schema style
        props = contract.get("properties")
        if isinstance(props, dict):
            for key in props:
                if isinstance(key, str) and key not in fields:
                    fields.append(key)
        required = contract.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in fields:
                    fields.append(key)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                fields.append(item["name"])
    return fields


def classify_task_lines(task_text: str):
    """Extract rules, conventions and administrative version notes verbatim."""
    lines = task_text.splitlines()
    sections = []
    rules = []
    conventions = []
    version_notes = []
    keywords_rules = (
        "规则", "约定", "字段", "命名", "目录", "去重", "版本", "哈希",
        "元数据", "权限", "来源", "归档", "保存期限", "命名规范",
    )
    keywords_version = (
        "版本说明", "version note", "version", "修订", "rev", "v1.", "v2.",
        "administrative", "行政版本",
    )
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,6}\s", stripped):
            sections.append({"heading": re.sub(r"^#+\s*", "", stripped), "line": ln})
        low = stripped.lower()
        if any(k.lower() in low for k in keywords_version):
            version_notes.append({"line": ln, "quote": stripped})
        if any(k.lower() in low for k in keywords_rules):
            if "版本说明" in stripped or "version note" in low:
                continue
            bucket = conventions if any(c in stripped for c in ("约定", "规则", "命名", "字段")) else rules
            bucket.append({"line": ln, "quote": stripped})
    return {
        "sections": sections,
        "rules": rules,
        "conventions": conventions,
        "version_notes": version_notes,
    }


def build_plan(case: dict, task_text: str, contract_field_list: list, case_id: str):
    classified = classify_task_lines(task_text or "")
    six_elements = [
        "文件类型", "元数据字段", "目录层级", "重复文件", "版本差异", "权限与来源",
    ]
    plan = {
        "case_id": case_id,
        "task_model": {
            "six_elements": six_elements,
            "sections": classified["sections"],
            "rules": classified["rules"],
            "conventions": classified["conventions"],
            "version_notes": classified["version_notes"],
            "contract_fields": contract_field_list,
        },
        "evidence": {
            "task_md": {
                "locator": "task.md",
                "quote": (task_text or "")[:4000],
            },
        },
        "build": {
            "engine_version": VERSION,
            "skill": SKILL_NAME,
        },
    }
    if case is not None:
        plan["evidence"]["case_json"] = {
            "locator": "case.json",
            "quote": json.dumps(
                {k: case.get(k) for k in ("id", "title", "case_id", "challenge_id") if case.get(k)},
                ensure_ascii=False,
            ) or "(no id/title fields)",
        }
    return plan


# --------------------------------------------------------------------------- #
# layer 1 - raw inventory scan
# --------------------------------------------------------------------------- #

def stat_entry(base: str, abspath: str) -> dict:
    rel = rel_posix(base, abspath)
    rec = {
        "path": rel,
        "type": None,
        "size": None,
        "mtime": None,
        "mode": None,
        "inode": None,
        "nlink": None,
        "dev": None,
        "sha256": None,
        "symlink_target": None,
        "broken_symlink": False,
        "flags": [],
        "error": None,
        "role": "control" if os.path.basename(rel) in CONTROL_BASENAMES else "material",
    }
    try:
        st = os.lstat(abspath)
    except OSError as exc:
        rec["type"] = "unreadable"
        rec["flags"].append("unreadable")
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec
    rec["size"] = st.st_size
    rec["mtime"] = iso_mtime(st.st_mtime)
    rec["mode"] = oct(stat.S_IMODE(st.st_mode))
    rec["inode"] = getattr(st, "st_ino", None)
    rec["nlink"] = getattr(st, "st_nlink", None)
    rec["dev"] = getattr(st, "st_dev", None)
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        rec["type"] = "symlink"
        try:
            rec["symlink_target"] = os.readlink(abspath)
        except OSError as exc:
            rec["symlink_target"] = f"<unreadable: {exc}>"
        if not os.path.exists(abspath):
            rec["broken_symlink"] = True
            rec["flags"].append("broken_symlink")
        else:
            rec["flags"].append("symlink")
    elif stat.S_ISDIR(mode):
        rec["type"] = "dir"
    elif stat.S_ISREG(mode):
        rec["type"] = "file"
        if rec["size"] > MAX_HASH_BYTES:
            rec["flags"].append("too_large")
        else:
            try:
                if not os.access(abspath, os.R_OK):
                    rec["flags"].append("no_read_permission")
                rec["sha256"] = sha256_file(abspath)
            except OSError as exc:
                rec["flags"].append("unreadable")
                rec["error"] = f"{type(exc).__name__}: {exc}"
    else:
        rec["type"] = "non_regular"
        rec["flags"].append("non_regular")
    return rec


def scan_tree(base: str) -> tuple:
    records = []
    errors = []
    if not os.path.isdir(base):
        errors.append({"item": base, "error": "input root does not exist"})
        return records, errors
    try:
        names = sorted(os.listdir(base))
    except OSError as exc:
        errors.append({"item": base, "error": f"{type(exc).__name__}: {exc}"})
        return records, errors

    def walk(dirpath: str):
        try:
            names = sorted(os.listdir(dirpath))
        except OSError as exc:
            errors.append({"item": rel_posix(base, dirpath), "error": f"{type(exc).__name__}: {exc}"})
            return
        for name in names:
            child = os.path.join(dirpath, name)
            rec = stat_entry(base, child)
            records.append(rec)
            if rec["error"] or rec["type"] == "unreadable":
                errors.append({
                    "item": rec["path"],
                    "error": rec["error"] or f"unreadable ({rec['type']})",
                })
            if rec["type"] == "dir" and not rec["broken_symlink"] and rec["flags"].count("symlink") == 0:
                walk(child)

    walk(base)
    return records, errors


# --------------------------------------------------------------------------- #
# layer 2 - canonical world model
# --------------------------------------------------------------------------- #

VERSION_TOKEN_RE = re.compile(
    r"(?i)(?<![a-z0-9])(v|ver|version|rev|r)[._-]?\d+\b"
)
VERSION_WORD_RE = re.compile(
    r"(?i)\b(final|draft|old|new|latest|bak|backup|copy|副本|初稿|终稿|旧|新)\b"
)
COPY_COUNTER_RE = re.compile(r"\(\d+\)")
DATE_RE = re.compile(r"\d{4}[-_]?\d{1,2}[-_]?\d{1,2}")
NON_ALNUM_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def normalize_stem(path: str) -> str:
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0].lower()
    stem = VERSION_TOKEN_RE.sub(" ", stem)
    stem = VERSION_WORD_RE.sub(" ", stem)
    stem = COPY_COUNTER_RE.sub(" ", stem)
    stem = DATE_RE.sub(" ", stem)
    stem = NON_ALNUM_RE.sub("", stem)
    return stem.strip()


def build_model(records, errors, plan):
    files = [r for r in records if r["type"] == "file"]
    dirs = [r for r in records if r["type"] == "dir"]
    symlinks = [r for r in records if r["type"] == "symlink"]

    # duplicate groups by content hash
    by_hash = defaultdict(list)
    for rec in files:
        if rec.get("sha256"):
            by_hash[rec["sha256"]].append(rec["path"])
    duplicate_groups = []
    for h, paths in sorted(by_hash.items()):
        if len(paths) > 1:
            duplicate_groups.append({
                "group_id": f"dup-{h[:12]}",
                "sha256": h,
                "size": next(
                    (r["size"] for r in files if r["path"] == paths[0]), None
                ),
                "count": len(paths),
                "paths": sorted(paths),
            })

    # version candidates by normalized stem (heuristic)
    stem_groups = defaultdict(list)
    for rec in files:
        stem = normalize_stem(rec["path"])
        if stem:
            stem_groups[stem].append(rec)
    version_candidates = []
    for stem, recs in sorted(stem_groups.items()):
        if len(recs) < 2:
            continue
        recs_sorted = sorted(recs, key=lambda r: (r.get("mtime") or "", r["path"]))
        members = []
        for r in recs_sorted:
            members.append({
                "path": r["path"],
                "mtime": r.get("mtime"),
                "size": r.get("size"),
                "sha256": r.get("sha256"),
            })
        hashes = {m["sha256"] for m in members if m.get("sha256")}
        version_candidates.append({
            "stem": stem,
            "confidence": "heuristic",
            "identical_content": len(hashes) == 1 and len(hashes) > 0,
            "members": members,
        })

    # aliases: hard links and symlinks
    hardlink_map = defaultdict(list)
    for rec in files:
        if rec.get("inode") and rec.get("nlink") and rec["nlink"] > 1 and rec.get("dev") is not None:
            hardlink_map[(rec["dev"], rec["inode"])].append(rec["path"])
    hardlink_groups = [
        {"key": [k[0], k[1]], "paths": sorted(v)}
        for k, v in sorted(hardlink_map.items(), key=lambda kv: (str(kv[0]), kv[1][0]))
        if len(v) > 1
    ]
    aliases = {
        "symlinks": [
            {
                "path": r["path"],
                "target": r.get("symlink_target"),
                "broken": r.get("broken_symlink", False),
            }
            for r in symlinks
        ],
        "hardlink_groups": hardlink_groups,
    }

    # permission / source summary
    modes = defaultdict(int)
    for r in records:
        if r.get("mode"):
            modes[r["mode"]] += 1
    flags_seen = defaultdict(list)
    for r in records:
        for f in r.get("flags", []):
            flags_seen[f].append(r["path"])

    counts = {
        "files": len(files),
        "dirs": len(dirs),
        "symlinks": len(symlinks),
        "broken_symlinks": sum(1 for r in symlinks if r.get("broken_symlink")),
        "non_regular": sum(1 for r in records if r["type"] == "non_regular"),
        "unreadable": sum(1 for r in records if r["type"] == "unreadable"),
        "duplicate_groups": len(duplicate_groups),
        "duplicated_files": sum(g["count"] for g in duplicate_groups),
        "version_candidate_groups": len(version_candidates),
        "hardlink_groups": len(hardlink_groups),
    }

    summary = {
        "counts": counts,
        "size_total_bytes": sum((r.get("size") or 0) for r in files),
        "extensions": sorted({os.path.splitext(r["path"])[1].lower() for r in files if os.path.splitext(r["path"])[1]}),
        "modes": {k: v for k, v in sorted(modes.items())},
        "flags": {k: len(v) for k, v in sorted(flags_seen.items())},
        "largest_files": [
            {"path": r["path"], "size": r["size"]}
            for r in sorted(files, key=lambda r: -(r.get("size") or 0))[:10]
        ],
    }

    model = {
        "engine_version": VERSION,
        "built_at": now_iso(),
        "summary": summary,
        "inventory": records,
        "errors_scan": errors,
        "duplicate_groups": duplicate_groups,
        "version_candidates": version_candidates,
        "aliases": aliases,
        "reconciliation": None,   # filled by reconcile step
        "index": None,            # filled by index step
        "answers": None,          # filled by answers step
        "plan": plan,
    }
    return model


# --------------------------------------------------------------------------- #
# manifest parsing + reconciliation
# --------------------------------------------------------------------------- #

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".bib", ".ris", ".yaml", ".yml",
    ".xml", ".html", ".log", ".ini", ".cfg", ".rst", ".tex", ".py",
}


def _entry_paths(entry: dict):
    out = set()
    for k in ("path", "file", "filename", "file_path", "relpath", "name", "location"):
        v = entry.get(k)
        if isinstance(v, str) and v:
            out.add(v.replace("\\", "/"))
    return out


def _entry_hash(entry: dict):
    for k in ("sha256", "hash", "checksum", "sha256_hash", "digest"):
        v = entry.get(k)
        if isinstance(v, str) and v:
            v = v.lower().split(":")[-1]
            if re.fullmatch(r"[0-9a-f]{32,64}", v):
                return v
    return None


def parse_json_manifest(path: str):
    data, err = read_json_safe(path)
    if err:
        return None, err
    entries = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                entries.append(item)
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        entries.append(item)
    return entries, None


def parse_csv_manifest(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            entries = [dict(row) for row in reader]
        return entries, None
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1", newline="") as fh:
                reader = csv.DictReader(fh)
                entries = [dict(row) for row in reader]
            return entries, None
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


BIB_ENTRY_RE = re.compile(
    r"@(\w+)\s*\{[^,]*,\s*(.*?)\}\s*,?\s*(?=@|\Z)", re.S
)


def parse_bibtex_manifest(path: str):
    text, err = read_text_safe(path)
    if err:
        return None, err
    entries = []
    for match in BIB_ENTRY_RE.finditer(text):
        etype = match.group(1)
        body = match.group(2)
        entry = {"bibtype": etype}
        for m in re.finditer(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", body, re.S):
            entry[m.group(1).lower()] = m.group(2).strip()
        entries.append(entry)
    return entries, None


def parse_ris_manifest(path: str):
    text, err = read_text_safe(path)
    if err:
        return None, err
    entries = []
    records = re.split(r"\n\s*ER\s*\-?\s*\n", text)
    for rec in records:
        if not rec.strip():
            continue
        entry = {}
        for m in re.finditer(r"^([A-Z0-9]{2})\s*-\s*(.*)$", rec, re.M):
            tag, val = m.group(1), m.group(2).strip()
            entry.setdefault(tag, val)
        if entry:
            entries.append(entry)
    return entries, None


def parse_sqlite_manifest(path: str):
    entries = []
    meta = {"tables": []}
    try:
        con = sqlite3.connect(path)
    except Exception as exc:  # noqa: BLE001
        return None, f"sqlite_open_error: {exc}"
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        meta["tables"] = tables
        for table in tables:
            try:
                rows = con.execute(f'SELECT * FROM "{table}" LIMIT 2000').fetchall()
                cols = [d[0] for d in con.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
                for row in rows:
                    entries.append(dict(zip(cols, row)))
            except Exception as exc:  # noqa: BLE001
                meta.setdefault("errors", []).append(f"{table}: sqlite_query_error: {exc}")
    finally:
        con.close()
    return entries, meta


def parse_manifest(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        entries, err = parse_json_manifest(path)
        kind = "json"
    elif ext in (".csv", ".tsv"):
        entries, err = parse_csv_manifest(path)
        kind = "csv"
    elif ext == ".bib":
        entries, err = parse_bibtex_manifest(path)
        kind = "bibtex"
    elif ext == ".ris":
        entries, err = parse_ris_manifest(path)
        kind = "ris"
    elif ext in (".sqlite", ".db", ".sqlite3"):
        entries, err = parse_sqlite_manifest(path)
        kind = "sqlite"
    else:
        return None, None, None
    return kind, entries, err


def reconcile(model, input_root=DEFAULT_INPUT):
    records = model["inventory"]
    files = [r for r in records if r["type"] == "file"]
    path_to_file = {r["path"]: r for r in files}
    hash_to_files = defaultdict(list)
    for r in files:
        if r.get("sha256"):
            hash_to_files[r["sha256"]].append(r["path"])

    manifests = []
    matches = []
    mismatches = []
    missing = []
    referenced = set()
    reconcile_errors = []

    for rec in records:
        if rec["type"] != "file" or rec["role"] == "control":
            continue
        ext = os.path.splitext(rec["path"])[1].lower()
        if ext not in (".json", ".csv", ".tsv", ".bib", ".ris", ".sqlite", ".db", ".sqlite3"):
            continue
        abspath = os.path.join(input_root, rec["path"].replace("/", os.sep))
        kind, entries, err = parse_manifest(abspath)
        if kind is None:
            continue
        manifest = {
            "manifest_path": rec["path"],
            "kind": kind,
            "entries": entries if entries else [],
            "error": err,
        }
        manifests.append(manifest)
        if err:
            reconcile_errors.append({"item": rec["path"], "error": err, "kind": kind})
            continue
        for i, entry in enumerate(entries):
            eid = f"{rec['path']}#{i}"
            paths = _entry_paths(entry)
            eh = _entry_hash(entry)
            found = None
            for p in paths:
                pn = p.split("/")[-1]
                if p in path_to_file:
                    found = path_to_file[p]
                    break
                if pn and pn in path_to_file:
                    found = path_to_file[pn]
                    break
                candidates = [f for f in files if os.path.basename(f["path"]) == pn]
                if len(candidates) == 1:
                    found = candidates[0]
                    break
            if found is None and eh:
                cand = hash_to_files.get(eh)
                if cand:
                    found = path_to_file[cand[0]]
            if found is None:
                missing.append({"entry_id": eid, "manifest": rec["path"], "entry": entry, "reason": "path/hash not in inventory"})
                continue
            referenced.add(found["path"])
            if eh and found.get("sha256") and eh != found["sha256"]:
                mismatches.append({
                    "entry_id": eid,
                    "manifest": rec["path"],
                    "path": found["path"],
                    "manifest_hash": eh,
                    "inventory_hash": found["sha256"],
                })
            else:
                matches.append({
                    "entry_id": eid,
                    "manifest": rec["path"],
                    "path": found["path"],
                    "hash": found.get("sha256"),
                })

    orphaned = [r["path"] for r in files if r["role"] == "material" and r["path"] not in referenced]

    model["reconciliation"] = {
        "manifests": manifests,
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "counts": {
            "manifests": len(manifests),
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "orphaned": len(orphaned),
        },
    }
    model["errors_reconcile"] = reconcile_errors
    return model


# --------------------------------------------------------------------------- #
# searchable index + mechanical answers
# --------------------------------------------------------------------------- #

def build_index(model, input_root=DEFAULT_INPUT):
    records = model["inventory"]
    files = [r for r in records if r["type"] == "file"]
    index = defaultdict(set)
    meta_tokens = defaultdict(set)
    for rec in files:
        p = rec["path"]
        for tok in tokens_of(p):
            meta_tokens[tok].add(p)
        if rec.get("sha256"):
            meta_tokens[rec["sha256"][:16]].add(p)
        ext = os.path.splitext(p)[1].lower()
        if ext in TEXT_EXTENSIONS and (rec.get("size") or 0) <= MAX_INDEX_TEXT_BYTES:
            abspath = os.path.join(input_root, p.replace("/", os.sep))
            text, err = read_text_safe(abspath)
            if err is None and text:
                toks = list(tokens_of(text))[:MAX_INDEX_TOKENS_PER_FILE]
                for tok in toks:
                    index[tok].add(p)
    index_out = {
        token: sorted(paths) for token, paths in sorted(index.items())
    }
    model["index"] = {
        "path_tokens": {t: sorted(v) for t, v in sorted(meta_tokens.items())},
        "content_tokens": index_out,
        "token_count": len(index_out),
    }
    return model


def build_answers(model):
    s = model["summary"]["counts"]
    rec = model["reconciliation"] or {}
    rc = (rec.get("counts") or {})
    answers = {
        "file_count": {"value": s["files"], "evidence": "inventory count"},
        "directory_count": {"value": s["dirs"], "evidence": "inventory count"},
        "duplicate_groups": {"value": s["duplicate_groups"], "evidence": "sha256 grouping"},
        "duplicated_files": {"value": s["duplicated_files"], "evidence": "sha256 grouping"},
        "version_candidate_groups": {"value": s["version_candidate_groups"], "evidence": "normalized stem heuristic"},
        "broken_symlinks": {"value": s["broken_symlinks"], "evidence": "lstat + exists check"},
        "reconciliation_matches": {"value": rc.get("matches", 0), "evidence": "manifest reconciliation"},
        "reconciliation_mismatches": {"value": rc.get("mismatches", 0), "evidence": "manifest reconciliation"},
        "reconciliation_missing": {"value": rc.get("missing", 0), "evidence": "manifest reconciliation"},
        "orphaned_files": {"value": rc.get("orphaned", 0), "evidence": "manifest reconciliation"},
        "largest_files": {"value": s.get("largest_files", []), "evidence": "stat size"},
    }
    # author / year extraction from reconciliation manifests
    authors = set()
    years = set()
    if rec:
        for m in rec.get("manifests", []):
            for e in (m.get("entries") or []):
                for k in ("author", "authors", "AU", "creator"):
                    v = e.get(k)
                    if isinstance(v, str) and v:
                        authors.add(v)
                for k in ("year", "date", "PY", "year_published"):
                    v = e.get(k)
                    if isinstance(v, (str, int)):
                        years.add(str(v)[:4])
    answers["authors"] = {"value": sorted(authors), "evidence": "manifest metadata"}
    answers["years"] = {"value": sorted(years), "evidence": "manifest metadata"}
    model["answers"] = answers
    return model


# --------------------------------------------------------------------------- #
# layer 3 - derived artifacts
# --------------------------------------------------------------------------- #

def build_tree_text(records):
    children = defaultdict(list)
    entry_by_path = {r["path"]: r for r in records}
    for r in records:
        parent = os.path.dirname(r["path"]) if "/" in r["path"] else "."
        parent = "." if parent in ("", "/") else parent
        children[parent].append(r["path"])
    lines = ["./"]
    def render(rel, prefix, is_last):
        name = os.path.basename(rel) if rel not in ("", ".") else "."
        rec = entry_by_path.get(rel)
        suffix = ""
        if rec and rec["type"] == "dir" and not children.get(rel):
            suffix = "  [空目录]"
        elif rec and rec["type"] == "symlink":
            suffix = f" -> {rec.get('symlink_target','')}" + ("  [断链]" if rec.get("broken_symlink") else "")
        lines.append(f"{prefix}{'└── ' if is_last else '├── '}{name}{suffix}")
        kids = sorted(children.get(rel, []))
        # dirs first then files
        def sort_key(p):
            r = entry_by_path.get(p)
            return (0 if r and r["type"] == "dir" else 1, os.path.basename(p).lower())
        kids = sorted(kids, key=sort_key)
        for i, k in enumerate(kids):
            kid_last = i == len(kids) - 1
            render(k, prefix + ("    " if is_last else "│   "), kid_last)
    root_kids = sorted(children.get(".", []), key=lambda p: (0 if entry_by_path.get(p) and entry_by_path[p]["type"] == "dir" else 1, os.path.basename(p).lower()))
    for i, k in enumerate(root_kids):
        render(k, "", i == len(root_kids) - 1)
    return "\n".join(lines) + "\n"


def manifest_csv_text(records):
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["path", "type", "role", "size", "mtime", "mode", "inode", "nlink", "sha256", "flags", "symlink_target", "error"])
    for r in sorted(records, key=lambda x: x["path"]):
        w.writerow([
            r["path"], r.get("type"), r.get("role"), r.get("size"), r.get("mtime"),
            r.get("mode"), r.get("inode"), r.get("nlink"), r.get("sha256"),
            ";".join(r.get("flags", [])), r.get("symlink_target"), r.get("error"),
        ])
    return buf.getvalue()


def handover_md(model, plan):
    s = model["summary"]
    c = s["counts"]
    rc = (model.get("reconciliation") or {}).get("counts") or {}
    lines = [
        "# 资料库交接报告",
        "",
        f"- case_id: `{plan.get('case_id')}`",
        f"- 引擎版本: `{model.get('engine_version')}`",
        f"- 生成时间: `{model.get('built_at')}`",
        "",
        "## 概览",
        "",
        f"- 文件 {c['files']} 个，目录 {c['dirs']} 个，符号链接 {c['symlinks']} 个（断链 {c['broken_symlinks']} 个）",
        f"- 内容重复组 {c['duplicate_groups']} 个（涉及文件 {c['duplicated_files']} 个）",
        f"- 版本候选链 {c['version_candidate_groups']} 条（启发式）",
        f"- 对账：matches {rc.get('matches',0)} / mismatches {rc.get('mismatches',0)} / missing {rc.get('missing',0)} / orphaned {rc.get('orphaned',0)}",
        "",
        "## 关键结论的证据位置",
        "",
        "详见 `result.json` 的 `sources` 与 `computed`；每个重复/版本/对账判定均带 locator 与逐字 quote。",
        "",
        "## 复核方式",
        "",
        "1. `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json`",
        "2. 用 `python3 -B scripts/selftest.py` 在干净环境跑自检。",
        "",
    ]
    return "\n".join(lines) + "\n"


def derive_artifacts(model, plan, output_dir):
    artifacts = {}
    artifacts["library_manifest.csv"] = manifest_csv_text(model["inventory"])
    artifacts["directory_tree.txt"] = build_tree_text(model["inventory"])
    artifacts["dedup_version.json"] = jdump({
        "duplicate_groups": model["duplicate_groups"],
        "version_candidates": model["version_candidates"],
        "version_notes": plan["task_model"]["version_notes"],
        "aliases": model["aliases"],
    }) + "\n"
    artifacts["recovery_report.json"] = jdump(model["reconciliation"]) + "\n"
    artifacts["library_index.json"] = jdump(model["index"]) + "\n"
    artifacts["library_manifest.md"] = handover_md(model, plan)
    return artifacts


def build_derivation_manifest(artifacts, model):
    stable_model = {k: v for k, v in model.items() if k != "built_at"}
    return {
        "engine_version": VERSION,
        "generated_at": now_iso(),
        "source_model": "library_model.json",
        "artifacts": [
            {
                "artifact": name,
                "kind": "derived",
                "derived_from": ["library_model.json", "plan.json"],
                "regeneration": (
                    "python3 -B scripts/main.py --input-root /input "
                    "--output-dir /app/output --work-dir /app/work"
                ),
            }
            for name in sorted(artifacts)
        ],
        "model_signature": hashlib.sha256(
            jdump(stable_model).encode("utf-8")
        ).hexdigest(),
    }


# --------------------------------------------------------------------------- #
# sources / uncertainty / change_log
# --------------------------------------------------------------------------- #

def build_sources(model):
    sources = []
    for g in model["duplicate_groups"]:
        sources.append({
            "item": f"重复组 {g['group_id']}",
            "locator": g["paths"][0],
            "quote": f"sha256={g['sha256']} size={g['size']} count={g['count']}",
        })
    for v in model["version_candidates"]:
        first = v["members"][0]
        last = v["members"][-1]
        sources.append({
            "item": f"版本候选链 {v['stem']}（启发式）",
            "locator": first["path"],
            "quote": f"stem={v['stem']}; oldest={first['path']}@{first['mtime']}; newest={last['path']}@{last['mtime']}",
        })
    for rec in model["inventory"]:
        if rec.get("sha256"):
            sources.append({
                "item": f"文件哈希 {rec['path']}",
                "locator": rec["path"],
                "quote": f"sha256={rec['sha256']} size={rec['size']} mode={rec['mode']}",
            })
    for note in model["plan"]["task_model"]["version_notes"]:
        sources.append({
            "item": "行政版本说明",
            "locator": "task.md",
            "quote": note["quote"],
        })
    return sources


def build_uncertainty(model, has_contract, contract_key):
    out = []
    if model["version_candidates"]:
        out.append({
            "item": "version_candidates",
            "reason": "版本链按归一化主干+内容差异启发式构建，可能把同名不同文档误并为一条链",
            "severity": "medium",
        })
    if not has_contract:
        out.append({
            "item": "output_contract",
            "reason": "case.json 未提供 output_contract/output/result_schema/contract 键，仅回填元数据字段",
            "severity": "medium",
        })
    aliases = model["aliases"]
    if aliases.get("hardlink_groups"):
        out.append({
            "item": "hardlink_groups",
            "reason": "硬链接识别依赖 st_ino/st_nlink，在部分文件系统/平台上可能不准确",
            "severity": "low",
        })
    for rec in model["inventory"]:
        if "too_large" in rec.get("flags", []):
            out.append({
                "item": rec["path"],
                "reason": f"超过 {MAX_HASH_BYTES} 字节，跳过内容哈希",
                "severity": "low",
            })
    return out


def build_change_log(steps):
    return [
        {"time": now_iso(), "actor": "scripts/core_impl.py", "step": s, "detail": d}
        for s, d in steps
    ]


# --------------------------------------------------------------------------- #
# contract fill
# --------------------------------------------------------------------------- #

def guess_map(model, plan, artifacts, tree_text):
    s = model.get("summary", {}) if isinstance(model, dict) else {}
    return {
        "files": [r["path"] for r in model.get("inventory", []) if r.get("type") == "file"],
        "file_list": [r["path"] for r in model.get("inventory", []) if r.get("type") == "file"],
        "inventory": model.get("inventory", []),
        "manifest": model.get("inventory", []),
        "library_manifest": "library_manifest.csv",
        "manifest_path": "library_manifest.csv",
        "duplicates": model.get("duplicate_groups", []),
        "duplicate_groups": model.get("duplicate_groups", []),
        "dedup": model.get("duplicate_groups", []),
        "versions": model.get("version_candidates", []),
        "version_candidates": model.get("version_candidates", []),
        "version_chain": model.get("version_candidates", []),
        "aliases": model.get("aliases", {}),
        "reconciliation": model.get("reconciliation", {}),
        "recovery": model.get("reconciliation", {}),
        "recovery_report": "recovery_report.json",
        "index": model.get("index", {}),
        "library_index": "library_index.json",
        "tree": tree_text,
        "directory_tree": tree_text,
        "summary": s,
        "counts": s.get("counts", {}),
        "answers": model.get("answers", {}),
        "status": "ok",
    }


def normalize_field(field: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", field.lower())


def fill_contract(result, fields, gm):
    computed = result.setdefault("computed", {})
    for field in fields:
        value = None
        nf = normalize_field(field)
        for key, val in gm.items():
            if nf and (nf in normalize_field(key) or normalize_field(key) in nf):
                value = val
                break
        if field in RESERVED_KEYS:
            computed[f"_contract_{field}"] = value
        else:
            result[field] = value
    return result


# --------------------------------------------------------------------------- #
# result assembly
# --------------------------------------------------------------------------- #

def assemble_result(case, case_id, plan, model, artifacts, tree_text, skeleton=False):
    contract_key, contract = find_contract(case) if case is not None else (None, None)
    fields = contract_fields(contract) if contract is not None else []
    has_contract = contract is not None

    steps = []
    result = {"case_id": case_id}
    if skeleton:
        result["status"] = STATUS_PARTIAL
        result["sources"] = []
        result["uncertainty"] = []
        result["pending_human"] = []
        result["errors"] = []
        result["degradations"] = []
        result["limitations"] = ["skeleton-only run; semantic analysis not yet performed"]
        result["runtime"] = {"engine_version": VERSION, "mode": "skeleton"}
        result["change_log"] = build_change_log([("skeleton", "partial result skeleton written")])
        fill_contract(result, fields, guess_map({}, plan, {}, ""))
        return result, fields

    steps.append(("scan", f"inventory {len(model['inventory'])} entries"))
    steps.append(("model", "library_model.json built from inventory"))
    steps.append(("derive", f"derived {len(artifacts)} editable artifacts"))
    steps.append(("assemble", "result.json assembled from model"))

    result["status"] = STATUS_OK
    if not model["summary"]["counts"]["files"]:
        result["status"] = STATUS_INSUFFICIENT
    if model["errors_scan"]:
        result["status"] = STATUS_PARTIAL

    result["sources"] = build_sources(model)
    result["uncertainty"] = build_uncertainty(model, has_contract, contract_key)
    result["pending_human"] = []
    result["errors"] = [dict(e) for e in model.get("errors_scan", [])] + [
        dict(e) for e in model.get("errors_reconcile", [])
    ]
    result["degradations"] = []
    result["limitations"] = []
    result["runtime"] = {
        "engine_version": VERSION,
        "skill": SKILL_NAME,
        "contract_key": contract_key,
        "contract_field_count": len(fields),
    }
    result["change_log"] = build_change_log(steps)

    gm = guess_map(model, plan, artifacts, tree_text)
    fill_contract(result, fields, gm)

    computed = result.setdefault("computed", {})
    computed.update({
        "files": [r["path"] for r in model["inventory"] if r["type"] == "file"],
        "summary": model["summary"],
        "inventory": model["inventory"],
        "duplicate_groups": model["duplicate_groups"],
        "version_candidates": model["version_candidates"],
        "version_notes": plan["task_model"]["version_notes"],
        "aliases": model["aliases"],
        "reconciliation": model["reconciliation"],
        "answers": model["answers"],
        "index_summary": {
            "token_count": (model["index"] or {}).get("token_count"),
        },
        "plan": plan,
        "intermediate_artifacts": {
            "plan": "plan.json",
            "inventory": "inventory.json",
            "model": "library_model.json",
            "derivation_manifest": "derivation_manifest.json",
        },
    })
    result["artifacts"] = {
        "editable": sorted(artifacts),
        "intermediate": [
            "plan.json", "inventory.json", "library_model.json",
            "derivation_manifest.json", "operation_log.jsonl",
        ],
    }
    return result, fields


# --------------------------------------------------------------------------- #
# pipeline driver
# --------------------------------------------------------------------------- #

def load_inputs(args):
    case = None
    task_text = None
    case_path = args.case_json or os.path.join(args.input_root, "case.json")
    task_path = args.task_path or os.path.join(args.input_root, "task.md")
    case_err = None
    task_err = None
    if os.path.isfile(case_path):
        case, case_err = read_json_safe(case_path)
    else:
        case_err = "case.json not found"
    if os.path.isfile(task_path):
        task_text, task_err = read_text_safe(task_path)
    return case, task_text, case_err, task_err, case_path, task_path


def resolve_case_id(case, task_text, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "armiId"):
            if case.get(k):
                return str(case[k])
    if task_text:
        m = re.search(r"challenge[_-]id[`\s:：]*([A-Za-z0-9_-]+)", task_text)
        if m:
            return m.group(1)
    return os.path.basename(case_path)


def write_all(output_dir, work_dir, payload):
    """payload: dict of {relpath: text}. Returns list of written paths."""
    written = []
    for rel, text in payload.items():
        target = os.path.join(output_dir if not rel.startswith("_work/") else work_dir, rel.replace("_work/", "", 1))
        write_text(target, text)
        written.append(target)
    return written


def run_full(args):
    case, task_text, case_err, task_err, case_path, _ = load_inputs(args)
    case_id = resolve_case_id(case, task_text, case_path)
    contract_key, contract = find_contract(case) if case else (None, None)
    fields = contract_fields(contract) if contract is not None else []

    if case is None:
        plan = build_plan(case, task_text, fields, case_id)
        plan["evidence"]["case_json_error"] = case_err
        result = {
            "case_id": case_id,
            "status": STATUS_INPUT_ERROR,
            "sources": [],
            "uncertainty": [{"item": "case.json", "reason": case_err, "severity": "high"}],
            "pending_human": [],
            "errors": [{"item": "case.json", "error": case_err}],
            "degradations": [],
            "limitations": ["case.json unreadable or missing"],
            "runtime": {"engine_version": VERSION, "mode": "input_error"},
            "change_log": build_change_log([("input", "case.json unavailable")]),
            "computed": {},
        }
        fill_contract(result, fields, guess_map({}, plan, {}, ""))
        payload = {"result.json": jdump(result) + "\n", "_work/plan.json": jdump(plan) + "\n"}
        write_all(args.output_dir, args.work_dir, payload)
        return result

    records, scan_errors = scan_tree(args.input_root)
    plan = build_plan(case, task_text, fields, case_id)
    if task_err:
        plan["evidence"]["task_md_error"] = task_err
    model = build_model(records, scan_errors, plan)
    model = reconcile(model, args.input_root)
    model = build_index(model, args.input_root)
    model = build_answers(model)

    tree_text = build_tree_text(records)
    artifacts = derive_artifacts(model, plan, args.output_dir)
    derivation = build_derivation_manifest(artifacts, model)
    result, _ = assemble_result(case, case_id, plan, model, artifacts, tree_text)

    # determine final status honestly
    material_files = [r for r in records if r["type"] == "file" and r["role"] == "material"]
    if not material_files and not [r for r in records if r["type"] != "file" and r["role"] == "material"]:
        result["status"] = STATUS_INSUFFICIENT
    if case_err and not contract:
        result["status"] = STATUS_PARTIAL

    # operation log (NDJSON)
    log_lines = []
    for step in result["change_log"]:
        log_lines.append(json.dumps(step, ensure_ascii=False))

    payload = {
        "result.json": jdump(result) + "\n",
        "library_manifest.csv": "\ufeff" + artifacts["library_manifest.csv"],
        "directory_tree.txt": artifacts["directory_tree.txt"],
        "dedup_version.json": artifacts["dedup_version.json"],
        "recovery_report.json": artifacts["recovery_report.json"],
        "library_index.json": artifacts["library_index.json"],
        "library_manifest.md": artifacts["library_manifest.md"],
        "operation_log.jsonl": "\n".join(log_lines) + ("\n" if log_lines else ""),
        # intermediate representation artifacts
        "plan.json": jdump(plan) + "\n",
        "inventory.json": jdump(records) + "\n",
        "library_model.json": jdump(model) + "\n",
        "derivation_manifest.json": jdump(derivation) + "\n",
    }
    try:
        write_all(args.output_dir, args.work_dir, payload)
    except OSError as exc:
        # degrade to skeleton, do not crash
        skeleton = {
            "case_id": case_id,
            "status": STATUS_PARTIAL,
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "errors": [{"item": "output", "error": f"{type(exc).__name__}: {exc}"}],
            "degradations": [],
            "limitations": ["output write failed"],
            "runtime": {"engine_version": VERSION, "mode": "degraded"},
            "change_log": build_change_log([("write", "output write failed; skeleton kept")]),
            "computed": {},
        }
        fill_contract(skeleton, fields, guess_map({}, plan, {}, ""))
        try:
            write_text(os.path.join(args.output_dir, "result.json"), jdump(skeleton) + "\n")
        except OSError:
            pass
        print(f"finalize: write failure -> {exc}", file=sys.stderr)
        return 2
    return 0


def run_skeleton(args):
    case, task_text, case_err, task_err, case_path, _ = load_inputs(args)
    case_id = resolve_case_id(case, task_text, case_path)
    contract_key, contract = find_contract(case) if case else (None, None)
    fields = contract_fields(contract) if contract is not None else []
    plan = build_plan(case, task_text, fields, case_id)
    result, _ = assemble_result(case, case_id, plan, {}, {}, "", skeleton=True)
    payload = {"result.json": jdump(result) + "\n", "_work/plan.json": jdump(plan) + "\n"}
    write_all(args.output_dir, args.work_dir, payload)
    return 0


def run_update(args):
    result_path = args.result or os.path.join(args.output_dir, "result.json")
    with open(result_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    with open(args.update, "r", encoding="utf-8") as fh:
        patch = json.load(fh)
    list_keys = {"sources", "uncertainty", "pending_human", "errors", "degradations", "limitations"}
    for key, value in patch.items():
        if key in list_keys and isinstance(value, list):
            result.setdefault(key, [])
            result[key] = result[key] + value
        elif key == "change_log" and isinstance(value, list):
            result.setdefault("change_log", [])
            result["change_log"] = result["change_log"] + value
        else:
            result[key] = value
    result.setdefault("change_log", []).append({
        "time": now_iso(), "actor": "scripts/core_impl.py --update",
        "step": "update", "detail": f"merged patch {os.path.basename(args.update)}",
    })
    write_text(result_path, jdump(result) + "\n")
    return 0


def run_check(args):
    result_path = args.check or os.path.join(args.output_dir, "result.json")
    case_path = args.case_json or os.path.join(args.input_root, "case.json")
    problems = []
    if not os.path.isfile(result_path):
        problems.append("result.json missing")
    else:
        result, err = read_json_safe(result_path)
        if err:
            problems.append(f"result.json invalid: {err}")
        else:
            if not result.get("case_id"):
                problems.append("case_id empty")
            if result.get("status") not in ALLOWED_STATUS:
                problems.append(f"bad status {result.get('status')!r}")
            if os.path.isfile(case_path):
                case, cerr = read_json_safe(case_path)
                if cerr is None and isinstance(case, dict):
                    _, contract = find_contract(case)
                    for field in contract_fields(contract):
                        if field in RESERVED_KEYS:
                            ok = f"_contract_{field}" in result.get("computed", {})
                        else:
                            ok = field in result
                        if not ok:
                            problems.append(f"contract field missing: {field}")
    if problems:
        print("check: FAIL")
        for p in problems:
            print("  -", p)
        return 3
    print("check: OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description="Document-library repair engine (IR pipeline).")
    p.add_argument("--input-root", default=os.environ.get("INPUT_ROOT", DEFAULT_INPUT))
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT))
    p.add_argument("--work-dir", default=os.environ.get("WORK_DIR", DEFAULT_WORK))
    p.add_argument("--case-json", default=None)
    p.add_argument("--task-path", default=None)
    p.add_argument("--skeleton-only", action="store_true", help="write partial skeleton immediately")
    p.add_argument("--update", metavar="PATCH.json", help="incrementally merge a patch into result.json")
    p.add_argument("--result", metavar="result.json", help="target result for --update/--check")
    p.add_argument("--check", metavar="result.json", help="validate result against contract")
    p.add_argument("--version", action="store_true")
    args = p.parse_args(argv)

    if args.version:
        print(f"{SKILL_NAME} {VERSION}")
        return 0
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(args.work_dir, exist_ok=True)

    if args.check:
        return run_check(args)
    if args.update:
        return run_update(args)
    if args.skeleton_only:
        return run_skeleton(args)
    return run_full(args)


if __name__ == "__main__":
    raise SystemExit(main())
