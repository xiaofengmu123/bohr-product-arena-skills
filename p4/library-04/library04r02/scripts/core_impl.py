#!/usr/bin/env python3
"""core_impl.py — implementation for the research-kb-qa Skill.

Pure Python standard library. Deterministic ordering, SHA256 dedup,
atomic writes, early-skeleton fallback and honest degradation records.

Subcommands (dispatched by scripts/main.py):
    scaffold   write a valid partial result skeleton within the first 180s
    inventory  walk /input, hash + dedup + version-family, extract searchable text
    finalize   merge answers.json, verify quotes verbatim, back-fill output_contract
    mark       append one model-request ledger entry
    selftest   run empty / bad-json / normal synthetic scenarios in temp dirs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
TOTAL_BUDGET_S = 900
TOTAL_BUDGET_REQUESTS = 64
MAX_CONTENT_BYTES = 2 * 1024 * 1024

PHASES = [
    {"id": "scaffold",  "name": "骨架", "budget_s": 180, "budget_requests": 5,  "deadline_abs_s": 180},
    {"id": "inventory", "name": "盘点", "budget_s": 540, "budget_requests": 20, "deadline_abs_s": 720},
    {"id": "qa",        "name": "问答", "budget_s": 120, "budget_requests": 15, "deadline_abs_s": 840},
    {"id": "finalize",  "name": "汇总", "budget_s": 60,  "budget_requests": 0,  "deadline_abs_s": 900},
]

STATUS_SET = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_ALIASES = {
    "answers": "answers", "answer": "answers", "qa": "answers", "results": "answers",
    "sources": "sources", "files": "sources", "materials": "sources", "inventory": "library",
    "library": "library", "metadata": "metadata", "uncertainty": "uncertainty",
    "uncertainties": "uncertainty", "confidence": "uncertainty",
}


# ---------------------------------------------------------------- utilities
def utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def decode_bytes(raw):
    """Return (text, encoding). Tries strict decoders, falls back to replace."""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp" + str(os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp" + str(os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path):
    raw = read_bytes(path)
    text, enc = decode_bytes(raw)
    data = json.loads(text)
    return data, enc


def load_json_soft(path, default):
    try:
        data, _ = load_json(path)
        return data
    except Exception:
        return default


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def read_head(path, n=4096):
    with open(path, "rb") as f:
        return f.read(n)


# ---------------------------------------------------------------- clock / budget
class Clock:
    """Persisted wall-clock so each phase sees one consistent start time."""

    def __init__(self, work_dir):
        self.path = os.path.join(work_dir, "clock.json")
        if os.path.exists(self.path):
            data = load_json_soft(self.path, {})
            self.started_epoch = data.get("started_epoch") or time.time()
            self.started_at = data.get("started_at") or utcnow_iso()
        else:
            self.started_epoch = time.time()
            self.started_at = utcnow_iso()
            self._write()

    def _write(self):
        atomic_write_json(self.path, {
            "started_epoch": self.started_epoch,
            "started_at": self.started_at,
            "total_budget_s": TOTAL_BUDGET_S,
            "total_budget_requests": TOTAL_BUDGET_REQUESTS,
        })

    def elapsed(self):
        return max(0.0, round(time.time() - self.started_epoch, 3))

    def remaining(self):
        return max(0.0, round(TOTAL_BUDGET_S - self.elapsed(), 3))

    def deadline_met(self, phase_id):
        for p in PHASES:
            if p["id"] == phase_id:
                return self.elapsed() <= p["deadline_abs_s"]
        return True

    def snapshot(self):
        return {
            "started_at": self.started_at,
            "elapsed_s": self.elapsed(),
            "remaining_s": self.remaining(),
            "phase_deadlines_met": {p["id"]: self.deadline_met(p["id"]) for p in PHASES},
        }


def read_request_ledger(work_dir):
    path = os.path.join(work_dir, "request_ledger.json")
    data = load_json_soft(path, {"requests": [], "total": 0})
    return data


def write_request_ledger(work_dir, data):
    atomic_write_json(os.path.join(work_dir, "request_ledger.json"), data)


# ---------------------------------------------------------------- file discovery
def discover_files(input_dir):
    entries = []
    if not os.path.isdir(input_dir):
        return entries
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            entries.append((full, rel))
    entries.sort(key=lambda x: x[1])
    return entries


def discover_dirs(input_dir):
    dirs_out = []
    if not os.path.isdir(input_dir):
        return dirs_out
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for d in sorted(dirs):
            full = os.path.join(root, d)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            dirs_out.append({"rel": rel, "mtime": int(os.path.getmtime(full))})
    dirs_out.sort(key=lambda x: x["rel"])
    return dirs_out


def sniff_type(rel, head):
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "archive"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head.startswith(b"GIF8"):
        return "image"
    if b"\x00" in head[:1024]:
        return "binary"
    return "text"


# ---------------------------------------------------------------- metadata
FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)

VERSION_MARKERS = (
    r"v\d+", r"version[-_.]?\d+", r"rev[-_.]?[a-z0-9]+", r"\br\d+\b",
    r"final", r"draft", r"latest", r"old", r"new", r"copy",
    r"\d{8}", r"\d{4}-\d{2}-\d{2}",
)


def extract_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta


def family_key(rel):
    stem = os.path.splitext(os.path.basename(rel))[0].lower()
    key = stem
    for mk in VERSION_MARKERS:
        key = re.sub(r"[-_.\s]*" + mk + r"[-_.\s]*", "-", key, flags=re.IGNORECASE)
    key = re.sub(r"[-_.\s]+", "-", key).strip("-")
    return key or stem


def version_markers_in(rel):
    stem = os.path.splitext(os.path.basename(rel))[0].lower()
    found = []
    for mk in VERSION_MARKERS:
        for m in re.finditer(r"[-_.\s]*(" + mk + r")[-_.\s]*", stem, flags=re.IGNORECASE):
            found.append(m.group(1))
    return found


# ---------------------------------------------------------------- text extraction
def extract_file_text(full, rel, kind, extract_dir, degradations):
    """Return (text_or_none, locator_prefix, meta). Copies decodable text to extract_dir."""
    raw = read_bytes(full)
    if len(raw) > MAX_CONTENT_BYTES:
        degradations.append({"file": rel, "issue": "size_over_2MB", "action": "skip_content_extraction"})
        return None, rel, {"size_over_limit": True}
    if kind == "text":
        text, enc = decode_bytes(raw)
        atomic_write_text(os.path.join(extract_dir, rel + ".txt"), text)
        return text, rel + "#L", {"encoding": enc, "lines": text.count("\n") + 1}
    if kind == "pdf":
        if shutil.which("pdftotext"):
            out = os.path.join(extract_dir, rel + ".txt")
            try:
                r = subprocess.run(
                    ["pdftotext", "-layout", full, out],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                )
                if r.returncode == 0 and os.path.exists(out):
                    text = read_text_file(out)
                    pages = text.count("\f") + 1
                    return text, rel + "#p", {"pages": pages, "encoding": "pdf-extracted"}
            except Exception:
                pass
        degradations.append({"file": rel, "issue": "no_text_layer_or_missing_pdftotext", "action": "needs_ocr"})
        return None, rel, {"unreadable": True, "needs_ocr": True}
    # archive/image/binary: no text layer extraction in this skill
    return None, rel, {"unreadable": True, "kind": kind}


# ---------------------------------------------------------------- scaffold
def scaffold(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    clock = Clock(work_dir)
    errors, degradations = [], []

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case_id, contract, questions = None, {}, None
    case_summary = {}
    if os.path.exists(case_path):
        try:
            case_data, enc = load_json(case_path)
            case_id = case_data.get("case_id") or case_data.get("id") or case_data.get("challenge_id")
            contract = case_data.get("output_contract", {})
            questions = case_data.get("questions")
            case_summary = {
                "case_id": case_id,
                "case_json_encoding": enc,
                "output_contract": contract,
                "output_contract_keys": list(contract.keys()) if isinstance(contract, dict) else None,
                "questions": questions,
            }
        except Exception as e:
            errors.append({"file": "case.json", "issue": "unparseable", "detail": str(e)})
    else:
        errors.append({"file": "case.json", "issue": "missing"})

    task_text = None
    if os.path.exists(task_path):
        task_text, _ = decode_bytes(read_bytes(task_path))

    unparseable_case = any(e.get("file") == "case.json" and e.get("issue") == "unparseable" for e in errors)
    result = {
        "case_id": case_id,
        "status": "input_error" if unparseable_case else "partial",
        "answers": [],
        "library": {"summary": {}, "file_types": {}, "metadata": [], "directory_tree": [],
                    "duplicate_groups": {}, "version_families": {}, "permissions_sources": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [],
        "runtime": {"started_at": clock.started_at, "elapsed_s": clock.elapsed()},
        "budget": {
            "total_seconds": TOTAL_BUDGET_S, "total_requests": TOTAL_BUDGET_REQUESTS,
            "phases": PHASES,
        },
        "reproducible_hash": None,
    }

    # back-fill every output_contract key with an honest default
    filled = fill_contract(contract)
    for k, v in filled.items():
        result[k] = v

    result["change_log"].append({
        "seq": 1, "phase": "scaffold", "at": utcnow_iso(), "action": "scaffold_skeleton_written",
        "status": result["status"], "detail": "valid partial skeleton atomically persisted",
    })

    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    atomic_write_json(os.path.join(work_dir, "case_summary.json"), case_summary)
    atomic_write_json(os.path.join(work_dir, "budget.json"), {
        "total_seconds": TOTAL_BUDGET_S, "total_requests": TOTAL_BUDGET_REQUESTS,
        "started_at": clock.started_at, "phases": PHASES,
    })
    return result


def fill_contract(contract):
    """Honest default for each output_contract key: null/[]/{} by declared type."""
    if isinstance(contract, dict):
        out = {}
        for k, v in contract.items():
            out[k] = contract_default(v)
        return out
    if isinstance(contract, list):
        return {str(i): None for i in range(len(contract))}
    return {}


TYPE_DEFAULT = {
    "string": None, "str": None, "number": None, "integer": None, "int": None,
    "float": None, "boolean": None, "bool": None, "null": None,
    "array": [], "list": [], "object": {}, "dict": {},
}


def contract_default(spec):
    if spec is None:
        return None
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        return None
    if isinstance(spec, str):
        return TYPE_DEFAULT.get(spec.lower(), None)
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        if "type" in spec and isinstance(spec.get("type"), str):
            return TYPE_DEFAULT.get(spec["type"].lower(), {})
        if "default" in spec:
            return spec["default"]
        return {k: contract_default(v) for k, v in spec.items()}
    return None


# ---------------------------------------------------------------- inventory
def run_inventory(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    clock = Clock(work_dir)
    degradations = []
    extract_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    files = []
    by_sha = {}
    empty_files = []
    evidence_index = []
    type_summary = {}
    version_families = {}
    metadata_list = []
    perm_sources = []

    for full, rel in discover_files(input_dir):
        head = read_head(full)
        kind = sniff_type(rel, head)
        size = os.path.getsize(full)
        sha = sha256_file(full)
        st = os.stat(full)
        mode_oct = format(stat.S_IMODE(st.st_mode), "o")
        readable = os.access(full, os.R_OK)
        writable = os.access(full, os.W_OK)

        role = "task_spec" if rel in ("case.json", "task.md") else "material"
        source = "case_json_materials" if (role == "material") else "task_input"

        text, loc_prefix, text_meta = extract_file_text(full, rel, kind, extract_dir, degradations)

        meta = {"size": size, "mtime": int(st.st_mtime)}
        if text_meta.get("encoding"):
            meta["text_encoding"] = text_meta["encoding"]
        if text is not None and kind == "text":
            fm = extract_frontmatter(text)
            for fk in ("title", "author", "date", "tags"):
                if fk in fm:
                    meta["frontmatter_" + fk] = fm[fk]

        entry = {
            "rel": rel, "sha256": sha, "type": kind, "size": size,
            "role": role, "source": source,
            "permissions": {"mode": mode_oct, "readable": readable, "writable": writable,
                            "uid": st.st_uid, "gid": st.st_gid},
            "metadata": meta,
            "version_markers": version_markers_in(rel),
            "readable": readable,
        }
        if text_meta.get("unreadable") or text is None and kind in ("pdf", "image", "archive", "binary"):
            entry["text_status"] = "unreadable" if text is None else "ok"

        files.append(entry)
        by_sha.setdefault(sha, []).append(rel)
        if size == 0:
            empty_files.append(rel)
        if text is not None:
            evidence_index.append({
                "file": rel, "sha256": sha, "kind": kind,
                "locator_prefix": loc_prefix,
                "lines": text_meta.get("lines"), "pages": text_meta.get("pages"),
                "extract": os.path.join("extracted", rel + ".txt"),
            })
        type_summary[kind] = type_summary.get(kind, 0) + 1
        metadata_list.append({"rel": rel, **meta})
        perm_sources.append({"rel": rel, "mode": mode_oct, "readable": readable,
                             "writable": writable, "uid": st.st_uid, "gid": st.st_gid,
                             "role": role, "source": source})

    duplicate_groups = {sha: rels for sha, rels in by_sha.items() if len(rels) > 1}
    for rel in files:
        fk = family_key(rel["rel"])
        if len(version_markers_in(rel["rel"])) > 0:
            version_families.setdefault(fk, []).append(
                {"rel": rel["rel"], "sha256": rel["sha256"], "markers": rel["version_markers"]})
    version_families = {k: v for k, v in version_families.items() if len(v) > 1}

    dirs = discover_dirs(input_dir)
    tree = build_tree([f["rel"] for f in files])

    inventory = {
        "files": files,
        "count": len(files),
        "directory_tree": {"dirs": dirs, "tree": tree},
        "file_types": type_summary,
        "duplicate_groups": duplicate_groups,
        "empty_files": empty_files,
        "version_families": version_families,
        "metadata": metadata_list,
        "permissions_sources": perm_sources,
    }
    atomic_write_json(os.path.join(work_dir, "inventory.json"), inventory)
    atomic_write_json(os.path.join(work_dir, "evidence_index.json"), evidence_index)

    # incrementally upgrade the skeleton result.json (monotonic, never replaces unknown keys)
    result = load_json_soft(os.path.join(output_dir, "result.json"), {"case_id": None})
    result["sources"] = files
    result["library"] = build_library(inventory)
    result["degradations"] = (result.get("degradations") or []) + degradations
    result["change_log"] = (result.get("change_log") or []) + [{
        "seq": len(result.get("change_log") or []) + 1, "phase": "inventory", "at": utcnow_iso(),
        "action": "inventory_written",
        "detail": f"indexed {len(files)} files, {len(duplicate_groups)} duplicate groups, "
                  f"{len(version_families)} version families",
    }]
    result["runtime"] = {**(result.get("runtime") or {}), "inventory_elapsed_s": clock.elapsed()}
    if not clock.deadline_met("inventory"):
        result["degradations"].append({"phase": "inventory", "issue": "deadline_exceeded", "action": "partial"})
    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    return inventory


def build_library(inventory):
    files = inventory.get("files", [])
    dup = inventory.get("duplicate_groups", {})
    ver = inventory.get("version_families", {})
    dirs = inventory.get("directory_tree", {}).get("dirs", [])
    tree = inventory.get("directory_tree", {}).get("tree", {})
    return {
        "summary": {
            "file_count": len(files),
            "file_types": inventory.get("file_types", {}),
            "duplicate_groups": len(dup),
            "version_families": len(ver),
            "empty_files": len(inventory.get("empty_files", [])),
            "directory_count": len(dirs),
            "unreadable_count": sum(1 for f in files if f.get("text_status") == "unreadable"),
        },
        "file_types": inventory.get("file_types", {}),
        "metadata": inventory.get("metadata", []),
        "directory_tree": {"dirs": dirs, "tree": tree},
        "duplicate_groups": dup,
        "version_families": ver,
        "permissions_sources": inventory.get("permissions_sources", []),
    }


def build_tree(rels):
    tree = {}
    for rel in sorted(rels):
        parts = rel.split("/")
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = None
    return tree


# ---------------------------------------------------------------- verification
_SHA_CACHE = {}


def _file_sha(input_dir, rel):
    if rel not in _SHA_CACHE:
        full = os.path.join(input_dir, rel)
        if os.path.exists(full):
            _SHA_CACHE[rel] = sha256_file(full)
        else:
            _SHA_CACHE[rel] = None
    return _SHA_CACHE[rel]


def verify_evidence(input_dir, work_dir, ev):
    out = {"status": "unverified"}
    rel = ev.get("file")
    if not rel:
        out.update({"file_missing": True, "detail": "no file field"})
        return out
    full = os.path.join(input_dir, rel)
    if not os.path.exists(full):
        out.update({"file_exists": False, "detail": "file not found under input dir"})
        return out
    out["file_exists"] = True

    actual_sha = _file_sha(input_dir, rel)
    declared_sha = ev.get("sha256")
    sha_ok = bool(declared_sha) and declared_sha == actual_sha
    out["sha_match"] = sha_ok
    out["actual_sha256"] = actual_sha

    quote = ev.get("quote") or ""
    text = None
    ext_path = os.path.join(work_dir, "extracted", rel + ".txt")
    if os.path.exists(ext_path):
        try:
            text = read_text_file(ext_path)
        except Exception:
            text = None
    if text is None and os.path.exists(full):
        raw = read_bytes(full)
        t, _ = decode_bytes(raw)
        if "\x00" not in raw[:1024]:
            text = t
    if text is None:
        out["quote_match"] = False
        out["quote_checkable"] = False
        out["status"] = "unverifiable" if sha_ok else "mismatch"
        return out

    out["quote_checkable"] = True
    norm_quote = " ".join(quote.split())
    norm_text = " ".join(text.split())
    out["quote_match"] = bool(quote) and (quote in text or norm_quote in norm_text)
    if sha_ok and out["quote_match"]:
        out["status"] = "verified"
    elif sha_ok and not out["quote_match"]:
        out["status"] = "quote_mismatch"
    else:
        out["status"] = "mismatch"
    return out


# ---------------------------------------------------------------- finalize
def run_finalize(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    clock = Clock(work_dir)
    result = load_json_soft(os.path.join(output_dir, "result.json"),
                            {"case_id": None, "change_log": [], "degradations": [], "errors": [],
                             "library": {}, "sources": []})
    case_summary = load_json_soft(os.path.join(work_dir, "case_summary.json"), {})
    inventory = load_json_soft(os.path.join(work_dir, "inventory.json"), {"files": []})
    answers_doc = load_json_soft(os.path.join(work_dir, "answers.json"), None)

    errors = list(result.get("errors") or [])
    degradations = list(result.get("degradations") or [])
    pending = []

    answers = []
    if answers_doc and isinstance(answers_doc, dict):
        for a in answers_doc.get("answers", []) or []:
            a = dict(a)
            evs = []
            for e in a.get("evidence", []) or []:
                e = dict(e)
                e["verification"] = verify_evidence(input_dir, work_dir, e)
                if e["verification"]["status"] != "verified":
                    degradations.append({"item": e.get("file"), "issue": "evidence_" + e["verification"]["status"],
                                         "detail": "quote/sha could not be verified verbatim"})
                evs.append(e)
            a["evidence"] = evs
            a["answer_status"] = "answered" if a.get("answer") else "missing"
            answers.append(a)
    else:
        degradations.append({"issue": "answers_json_missing", "action": "qa_phase_not_run_or_not_written"})

    result["answers"] = answers
    result["sources"] = inventory.get("files", result.get("sources", []))
    if inventory.get("files") is not None:
        result["library"] = build_library(inventory)
    else:
        result["library"] = result.get("library") or {}
    result["uncertainty"] = build_uncertainty(answers_doc, answers)

    # back-fill output_contract keys from computed fields where names align
    contract = case_summary.get("output_contract", {})
    computed = {"answers": answers, "sources": result["sources"], "library": result["library"],
                "metadata": result["library"].get("metadata", []), "uncertainty": result["uncertainty"]}
    filled = fill_contract(contract)
    for k in filled:
        target = CONTRACT_KEY_ALIASES.get(norm_key(k))
        if target and target in computed:
            result[k] = computed[target]
        else:
            result.setdefault(k, filled[k])
            pending.append({"key": k, "reason": "output_contract field not auto-populated", "value": filled[k]})
    result["pending_human"] = list(result.get("pending_human") or []) + pending

    # status decision
    result["errors"] = errors
    result["degradations"] = degradations
    result["status"] = determine_status(result, answers, inventory, errors)

    # budget + reproducibility
    ledger = read_request_ledger(work_dir)
    result["budget"] = {
        "total_seconds": TOTAL_BUDGET_S, "total_requests": TOTAL_BUDGET_REQUESTS,
        "requests_used": ledger.get("total", 0),
        "requests_remaining": max(0, TOTAL_BUDGET_REQUESTS - ledger.get("total", 0)),
        "elapsed_s": clock.elapsed(), "remaining_s": clock.remaining(),
        "phase_deadlines_met": {p["id"]: clock.deadline_met(p["id"]) for p in PHASES},
        "limitations": [d for d in degradations],
    }
    if clock.elapsed() > TOTAL_BUDGET_S or ledger.get("total", 0) > TOTAL_BUDGET_REQUESTS:
        result["status"] = "partial"
        result["degradations"].append({"issue": "budget_exceeded", "action": "partial_deliverable"})

    result["runtime"] = {
        "started_at": clock.started_at,
        "elapsed_s": clock.elapsed(),
        "remaining_s": clock.remaining(),
    }
    result["change_log"] = (result.get("change_log") or []) + [{
        "seq": len(result.get("change_log") or []) + 1, "phase": "finalize", "at": utcnow_iso(),
        "action": "finalize_completed", "status": result["status"],
        "detail": f"answers={len(answers)}, pending_human={len(pending)}, degradations={len(degradations)}",
    }]
    result["reproducible_hash"] = reproducible_hash(result)

    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    atomic_write_json(os.path.join(output_dir, "evidence_map.json"), build_evidence_map(result))
    atomic_write_text(os.path.join(output_dir, "inventory.md"), render_inventory_md(result, inventory))
    return result


def determine_status(result, answers, inventory, errors):
    if any(e.get("file") == "case.json" and e.get("issue") == "unparseable" for e in errors):
        return "input_error"
    files = inventory.get("files", [])
    materials = [f for f in files if f.get("role") == "material"]
    if not materials:
        return "insufficient_evidence"
    usable = [f for f in materials if f.get("text_status") != "unreadable"]
    if not answers:
        return "partial"
    bad_ev = [e for a in answers for e in a.get("evidence", [])
              if e.get("verification", {}).get("status") not in ("verified", "unverifiable")]
    if bad_ev:
        return "partial"
    if not usable:
        return "partial"
    return "ok"


def build_uncertainty(answers_doc, answers):
    out = []
    if answers_doc and isinstance(answers_doc, dict):
        for u in answers_doc.get("uncertainty", []) or []:
            out.append(dict(u))
    for a in answers:
        if a.get("uncertainty") in ("conflict", "insufficient_evidence", "unreadable"):
            out.append({"level": a["uncertainty"], "item": a.get("question"),
                        "reason": a.get("missing") or "see answer uncertainty"})
    return out


def build_evidence_map(result):
    files = result.get("sources", [])
    return {
        "case_id": result.get("case_id"),
        "file_index": {f["rel"]: {"sha256": f["sha256"], "type": f["type"], "size": f["size"]}
                       for f in files if "rel" in f},
        "answers": [{"question": a.get("question"), "evidence": a.get("evidence", [])}
                    for a in result.get("answers", [])],
    }


def render_inventory_md(result, inventory):
    lines = ["# 科研知识库清单", "",
             f"- case_id: `{result.get('case_id')}`",
             f"- status: `{result.get('status')}`",
             f"- files: {inventory.get('count', len(result.get('sources', [])))}", "",
             "## 文件类型", ""]
    for t, n in sorted((inventory.get("file_types") or {}).items()):
        lines.append(f"- {t}: {n}")
    lines += ["", "## 文件清单", ""]
    for f in inventory.get("files", []):
        lines.append(f"- `{f['rel']}`  {f['type']}  {f['size']}B  sha256={f['sha256'][:12]}…  mode={f['permissions']['mode']}")
    dup = inventory.get("duplicate_groups") or {}
    if dup:
        lines += ["", "## 重复文件（按 SHA256）", ""]
        for sha, rels in sorted(dup.items()):
            lines.append(f"- {sha[:12]}…: {', '.join(rels)}")
    ver = inventory.get("version_families") or {}
    if ver:
        lines += ["", "## 版本归族", ""]
        for fam, members in sorted(ver.items()):
            lines.append(f"- `{fam}`: " + "; ".join(f"{m['rel']}({m['sha256'][:12]}…)" for m in members))
    deg = result.get("degradations") or []
    if deg:
        lines += ["", "## 降级与缺口", ""]
        for d in deg:
            lines.append(f"- {d.get('file') or d.get('issue')}: {d.get('action') or d.get('detail') or ''}".rstrip())
    return "\n".join(lines) + "\n"


def reproducible_hash(result):
    lib = result.get("library") or {}
    core = {
        "case_id": result.get("case_id"),
        "contract_keys": sorted(k for k in result if not str(k).startswith("_") and k not in NON_CORE_KEYS),
        "sources": sorted((f["rel"], f["sha256"], f["type"], f["size"])
                          for f in result.get("sources", []) if isinstance(f, dict) and "rel" in f),
        "file_types": sorted((k, v) for k, v in (lib.get("file_types") or {}).items()),
        "duplicate_groups": sorted((sha, sorted(rels))
                                   for sha, rels in (lib.get("duplicate_groups") or {}).items()),
        "version_families": sorted((fam, sorted((m["rel"], m["sha256"]) for m in members))
                                   for fam, members in (lib.get("version_families") or {}).items()),
        "answers": sorted(
            ({"q": a.get("question"), "a": a.get("answer"),
              "e": [(e.get("file"), e.get("sha256"), e.get("locator"), e.get("quote"))
                    for e in a.get("evidence", [])]}
             for a in result.get("answers", [])),
            key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)),
        "uncertainty": sorted(result.get("uncertainty", []),
                              key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)),
    }
    blob = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(blob.encode("utf-8"))


NON_CORE_KEYS = {"status", "change_log", "runtime", "budget", "reproducible_hash",
                 "pending_human", "errors", "degradations"}


# ---------------------------------------------------------------- mark
def run_mark(work_dir, phase, note):
    os.makedirs(work_dir, exist_ok=True)
    ledger = read_request_ledger(work_dir)
    seq = ledger.get("total", 0) + 1
    ledger.setdefault("requests", []).append(
        {"seq": seq, "phase": phase, "at": utcnow_iso(), "note": note or ""})
    ledger["total"] = seq
    write_request_ledger(work_dir, ledger)
    return ledger


# ---------------------------------------------------------------- selftest
def run_selftest(tmp_root=None):
    """Three synthetic scenarios; never touches /input or /app/output."""
    root = tmp_root or tempfile.mkdtemp(prefix="kbqa-selftest-")
    os.makedirs(root, exist_ok=True)
    results = {}

    def scenario(name, build_input):
        base = os.path.join(root, name)
        inp, out, work = (os.path.join(base, "input"), os.path.join(base, "output"), os.path.join(base, "work"))
        os.makedirs(inp, exist_ok=True)
        build_input(inp)
        scaffold(inp, out, work)
        run_inventory(inp, out, work)
        res = run_finalize(inp, out, work)
        results[name] = {"result": res, "out": out}
        return res

    # 1. empty dir
    scenario("empty", lambda inp: None)
    # 2. bad json
    def bad_json(inp):
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
    scenario("bad_json", bad_json)
    # 3. normal: contract + duplicate + version pair + one synthetic answer (derived, not pre-written)
    def normal(inp):
        md = "# 论文A\n\n本方法在数据集 D 上达到 0.92 的准确率。\n"
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "selftest-case", "output_contract": {"answers": "array",
                        "sources": "array", "uncertainty": "array"}}, f, ensure_ascii=False)
        os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
        with open(os.path.join(inp, "materials", "paper.md"), "w", encoding="utf-8") as f:
            f.write(md)
        with open(os.path.join(inp, "materials", "paper_copy.md"), "w", encoding="utf-8") as f:
            f.write(md)  # exact duplicate -> duplicate_groups
        with open(os.path.join(inp, "materials", "paper_v2.md"), "w", encoding="utf-8") as f:
            f.write(md + "\n# v2 增加内容\n")
    res = scenario("normal", normal)
    # write a derived synthetic answers.json, then re-finalize to exercise verification
    base = os.path.join(root, "normal")
    work = os.path.join(base, "work")
    answers = {"answers": [{
        "question": "本方法准确率是多少？",
        "answer": "0.92",
        "evidence": [{"file": "materials/paper.md", "sha256": sha256_file(os.path.join(base, "input", "materials", "paper.md")),
                      "locator": "materials/paper.md#L3", "quote": "本方法在数据集 D 上达到 0.92 的准确率。",
                      "confidence": "certain"}],
        "uncertainty": "certain", "missing": None}]}
    atomic_write_json(os.path.join(work, "answers.json"), answers)
    run_finalize(os.path.join(base, "input"), os.path.join(base, "output"), work)

    # assertions
    failures = []
    for name, info in results.items():
        r = json.load(open(os.path.join(info["out"], "result.json"), encoding="utf-8"))
        if name == "empty":
            if r.get("status") != "insufficient_evidence":
                failures.append(f"{name}: expected insufficient_evidence, got {r.get('status')}")
        if name == "bad_json":
            if r.get("status") != "input_error":
                failures.append(f"{name}: expected input_error, got {r.get('status')}")
        if name == "normal":
            if not r.get("case_id"):
                failures.append(f"{name}: missing case_id")
            if r.get("status") not in ("ok", "partial"):
                failures.append(f"{name}: unexpected status {r.get('status')}")
            if "answers" not in r or "sources" not in r or "uncertainty" not in r:
                failures.append(f"{name}: missing contract key")
        if not r.get("change_log"):
            failures.append(f"{name}: missing change_log")

    # verify normal-case verification passed
    normal_final = json.load(open(os.path.join(results["normal"]["out"], "result.json"), encoding="utf-8"))
    ev = normal_final.get("answers", [{}])[0].get("evidence", [{}])[0]
    if ev.get("verification", {}).get("status") != "verified":
        failures.append("normal: quote verification did not pass")

    if failures:
        for f in failures:
            print("SELFTEST FAIL:", f)
        return 1
    print("SELFTEST OK: empty / bad_json / normal scenarios passed;", root)
    return 0


# ---------------------------------------------------------------- dispatch (used by main.py)
COMMANDS = {}


def _register(name, fn):
    COMMANDS[name] = fn


def run(cmd, args):
    return COMMANDS[cmd](args)


_register("scaffold", lambda a: scaffold(a.input, a.output, a.work) and 0)
_register("inventory", lambda a: run_inventory(a.input, a.output, a.work) and 0)
_register("finalize", lambda a: run_finalize(a.input, a.output, a.work) and 0)
_register("mark", lambda a: run_mark(a.work, a.phase, a.note) and 0)
_register("selftest", lambda a: run_selftest())


def add_common_args(p):
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="research-kb-qa skill pipeline (scaffold/inventory/finalize/mark/selftest)")
    sub = p.add_subparsers(dest="cmd", required=True)
    add_common_args(sub.add_parser("scaffold", help="write early partial skeleton"))
    add_common_args(sub.add_parser("inventory", help="index files, dedup, version families, extract text"))
    add_common_args(sub.add_parser("finalize", help="merge answers, verify quotes, back-fill contract"))
    mp = sub.add_parser("mark", help="append one request-ledger entry")
    mp.add_argument("--work", default=DEFAULT_WORK)
    mp.add_argument("--phase", required=True)
    mp.add_argument("--note", default=None)
    sub.add_parser("selftest", help="run synthetic self-test scenarios")
    return p


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    code = run(args.cmd, args)
    sys.exit(code if isinstance(code, int) else 0)
