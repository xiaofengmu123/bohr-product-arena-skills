"""pipeline.py - deterministic core for the research-kb-qa skill.

Pure Python standard library: no third-party packages, no network access.
The CLI in main.py parses arguments and calls the functions defined here.

Design goals
------------
* Deterministic: every list is sorted by a stable key (relative path), every
  hash is computed once, and timestamps are captured at a single point.
  Re-running on the same input produces byte-identical inventories.
* Honest: a file we cannot read is recorded as such; we never guess content,
  page numbers, DOIs, versions, or permissions from filenames alone.
* Degradable: empty input, bad JSON, bad encoding, missing ``pdftotext``, and
  unreadable files all produce a valid artifact instead of a crash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Constants and small helpers
# --------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

UNCERTAINTY_LEVELS = (
    "certain",
    "likely",
    "conflict",
    "insufficient_evidence",
    "unreadable",
)

# Files that are part of the task interface, not study material.
TASK_BASENAMES = {"case.json", "task.md"}

# Files larger than this are hashed and inventoried but not read into memory.
MAX_TEXT_BYTES = 2 * 1024 * 1024

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".jsonl", ".yaml",
    ".yml", ".xml", ".tex", ".bib", ".tsv", ".log", ".cfg", ".ini", ".toml",
    ".py", ".r", ".m", ".sh", ".html", ".htm",
}

OFFICE_EXTS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".odp", ".rtf",
}

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".svg",
}

ARCHIVE_EXTS = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
}

_VERSION_MARKER_RE = re.compile(
    r"[_.\-\s]*(?:"
    r"(?:v|ver|version|rev|r|revision)[_.\-\s]*\d+(?:[._\-]\d+)*"
    r"|(?:final|draft|old|new|copy|bak|backup|latest|stable)"
    r"|(?:\d{8}|\d{4}[_.\-]\d{2}[_.\-]\d{2})"
    r")$",
    re.IGNORECASE,
)

_DUPLICATE_SUFFIX_RE = re.compile(r"[_\-\s]*(?:copy|copy of|\d+|\(\d+\))$",
                                  re.IGNORECASE)


def now_iso() -> str:
    """Return a UTC ISO-8601 timestamp captured at call time."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_dumps(obj) -> str:
    """Serialize deterministically with stable key order."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def write_json(path: str, obj) -> str:
    """Write *obj* to *path* (creating parents); return the path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json_dumps(obj))
    return path


def load_json(path: str):
    """Load a JSON file tolerantly. Returns (obj, error_or_None).

    Decodes utf-8-sig first, then utf-8, then latin-1 as a last resort.
    A parse failure returns ``(None, message)`` instead of raising.
    """
    raw = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            raw = data.decode(encoding)
            return json.loads(raw), None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # noqa
            last_error = "%s: %s" % (type(exc).__name__, exc)
        except OSError as exc:
            return None, "OSError: %s" % exc
    return None, "unreadable JSON: %s" % last_error


def sha256_file(path: str):
    """Return lowercase hex SHA-256 of a file, or None when unreadable."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def sniff_type(name: str, head: bytes) -> str:
    """Classify a file by magic bytes first, then extension.

    Categories: text / pdf / office / image / archive / unknown.
    """
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:8] == b"\x89PNG\r\n\x1a\n" or head[:2] == b"\xff\xd8":
        return "image"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if head[:4] == b"PK\x03\x04":
        ext = os.path.splitext(name)[1].lower()
        return "office" if ext in OFFICE_EXTS else "archive"

    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext == ".pdf":
        return "pdf"

    try:
        head.decode("utf-8")
        return "text"
    except (UnicodeDecodeError, AttributeError):
        return "unknown"


def probe_pdf_text_layer(path: str):
    """Return True/False when ``pdftotext`` can tell us, else None.

    Only the first page is probed; an empty result means a scanned page with
    no text layer. Missing tool returns None (we do not invent a result).
    """
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return None
    try:
        proc = subprocess.run(
            [pdftotext, "-f", "1", "-l", "1", path, "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if proc.returncode != 0:
            return None
        return bool(proc.stdout.decode("utf-8", "replace").strip())
    except (OSError, subprocess.SubprocessError):
        return None


def file_permissions(path: str) -> dict:
    """Return a permission record; missing fields are honest ``null``."""
    record = {"mode": None, "readable": None, "writable": None,
              "uid": None, "gid": None}
    try:
        st = os.stat(path)
        record["mode"] = oct(st.st_mode & 0o7777)
        record["readable"] = os.access(path, os.R_OK)
        record["writable"] = os.access(path, os.W_OK)
        if os.name == "posix":
            record["uid"] = getattr(st, "st_uid", None)
            record["gid"] = getattr(st, "st_gid", None)
    except OSError:
        pass
    return record


def version_base(name: str) -> str:
    """Strip version/date tokens to a stable family base name.

    ``paper_v2_final.pdf`` and ``paper_v1.pdf`` both map to ``paper.pdf``;
    ``paper_20231124.md`` maps to ``paper.md``. Equal hashes under one base
    name are treated as duplicate copies, unequal hashes as distinct versions.
    """
    stem, ext = os.path.splitext(name)
    prev = None
    while prev != stem:
        prev = stem
        stem = _VERSION_MARKER_RE.sub("", stem)
        stem = _DUPLICATE_SUFFIX_RE.sub("", stem)
    stem = stem.strip(" _.-")
    if not stem:
        stem = name
    return stem + ext


def read_text_meta(path: str, size: int) -> dict:
    """Return encoding and content metadata for a text file.

    We never store full content here; the Agent extracts verbatim quotes from
    the actual file during the QA phase. This keeps the inventory small.
    """
    meta = {"encoding": None, "char_count": None, "has_bom": False,
            "truncated": False, "note": None}
    if size > MAX_TEXT_BYTES:
        meta["truncated"] = True
        meta["note"] = "file exceeds %d-byte read cap" % MAX_TEXT_BYTES
        return meta
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            meta["has_bom"] = True
            text = raw.decode("utf-8-sig")
            meta["encoding"] = "utf-8-sig"
        else:
            text = raw.decode("utf-8")
            meta["encoding"] = "utf-8"
        meta["char_count"] = len(text)
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
            meta["encoding"] = "latin-1"
            meta["char_count"] = len(text)
            meta["note"] = "decoded as latin-1 fallback"
        except Exception as exc:  # pragma: no cover - defensive
            meta["note"] = "undecodable: %s" % exc
    except OSError as exc:
        meta["note"] = "unreadable: %s" % exc
    return meta


# --------------------------------------------------------------------------
# Inventory building
# --------------------------------------------------------------------------

def build_inventory(input_dir: str, work_dir: str, case_summary: dict) -> dict:
    """Walk *input_dir* and produce the six-dimension inventory.

    The six dimensions required by the rubric are: file types, metadata
    fields, directory hierarchy, duplicate files, version differences, and
    permissions/source.
    """
    input_dir = os.path.abspath(input_dir)
    started = now_iso()
    files = []
    errors = []
    file_types = {}

    for root, dirnames, filenames in os.walk(input_dir):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, input_dir).replace(os.sep, "/")
            try:
                st = os.stat(abs_path)
                size = st.st_size
                mtime = datetime.fromtimestamp(st.st_mtime,
                                               tz=timezone.utc).isoformat(
                                                   timespec="seconds")
            except OSError as exc:
                errors.append({"file": rel_path, "error": str(exc),
                               "stage": "stat"})
                continue

            try:
                with open(abs_path, "rb") as fh:
                    head = fh.read(512)
            except OSError as exc:
                head = b""
                errors.append({"file": rel_path, "error": str(exc),
                               "stage": "read_head"})

            ftype = sniff_type(filename, head)
            file_types[ftype] = file_types.get(ftype, 0) + 1

            digest = sha256_file(abs_path)
            if digest is None:
                errors.append({"file": rel_path,
                               "error": "could not hash file",
                               "stage": "sha256"})

            record = {
                "relative_path": rel_path,
                "name": filename,
                "type": ftype,
                "size": size,
                "sha256": digest,
                "mtime": mtime,
                "permissions": file_permissions(abs_path),
                "source": _classify_source(rel_path, case_summary),
            }

            if ftype == "text":
                record["metadata"] = read_text_meta(abs_path, size)
            elif ftype == "pdf":
                record["pdf_text_layer"] = probe_pdf_text_layer(abs_path)
            else:
                record["metadata"] = {}

            files.append(record)

    directory_tree = _build_directory_tree(input_dir)

    duplicates = _group_duplicates(files)
    version_families = _group_versions(files)
    empty_files = [f for f in files if f["size"] == 0]

    inventory = {
        "generated_at": started,
        "input_dir": input_dir,
        "summary": {
            "file_count": len(files),
            "file_types": file_types,
            "duplicate_groups": len(duplicates),
            "version_families": len(version_families),
            "empty_files": len(empty_files),
            "errors": len(errors),
        },
        "files": files,
        "directory_tree": directory_tree,
        "duplicate_groups": duplicates,
        "version_families": version_families,
        "empty_files": empty_files,
        "errors": errors,
    }
    return inventory


def _classify_source(rel_path: str, case_summary: dict) -> str:
    """Label task-interface files vs study material.

    We prefer an explicit material list from case.json when present, and fall
    back to well-known interface basenames otherwise.
    """
    base = os.path.basename(rel_path)
    if base in TASK_BASENAMES:
        return "task_input"
    materials = case_summary.get("material_paths") or []
    norm = rel_path.replace(os.sep, "/")
    for mat in materials:
        if isinstance(mat, str) and norm.startswith(str(mat).replace(os.sep, "/")):
            return "materials"
    return "materials" if materials else "materials_implied"


def _build_directory_tree(input_dir: str) -> list:
    """Return a deterministic directory tree with directory mtimes."""
    tree = []
    for root, dirnames, filenames in os.walk(input_dir):
        dirnames.sort()
        rel_root = os.path.relpath(root, input_dir).replace(os.sep, "/")
        if rel_root == ".":
            rel_root = ""
        try:
            mtime = datetime.fromtimestamp(
                os.stat(root).st_mtime, tz=timezone.utc).isoformat(
                    timespec="seconds")
        except OSError:
            mtime = None
        tree.append({
            "path": rel_root or ".",
            "directories": sorted(dirnames),
            "files": sorted(filenames),
            "mtime": mtime,
        })
    tree.sort(key=lambda d: d["path"])
    return tree


def _group_duplicates(files: list) -> list:
    """Group non-empty files by SHA-256; return every group with >1 path."""
    buckets = {}
    for f in files:
        if not f["sha256"] or f["size"] == 0:
            continue
        buckets.setdefault(f["sha256"], []).append(f["relative_path"])
    groups = []
    for digest in sorted(buckets):
        paths = sorted(buckets[digest])
        if len(paths) > 1:
            groups.append({"sha256": digest, "count": len(paths), "paths": paths})
    return groups


def _group_versions(files: list) -> list:
    """Group files by (directory, version base name).

    A family with one member is only interesting when the name still carried
    an explicit version marker; groups with several members are always kept so
    differing hashes can be compared.
    """
    buckets = {}
    for f in files:
        directory = os.path.dirname(f["relative_path"])
        base = version_base(f["name"])
        key = (directory, base)
        buckets.setdefault(key, []).append(f)

    families = []
    for (directory, base) in sorted(buckets):
        members = sorted(buckets[(directory, base)],
                         key=lambda f: f["relative_path"])
        hashes = {m["sha256"] for m in members if m["sha256"]}
        explicit_marker = version_base(base) != base or _has_marker(
            [m["name"] for m in members])
        if len(members) > 1 or explicit_marker:
            families.append({
                "base_name": base,
                "directory": directory,
                "members": [
                    {"relative_path": m["relative_path"],
                     "sha256": m["sha256"],
                     "mtime": m["mtime"]}
                    for m in members
                ],
                "distinct_hashes": sorted(hashes),
            })
    return families


def _has_marker(names: list) -> bool:
    for name in names:
        stem = os.path.splitext(name)[0]
        if _VERSION_MARKER_RE.search(stem):
            return True
    return False


# --------------------------------------------------------------------------
# Case summary and output-contract handling
# --------------------------------------------------------------------------

def extract_case_id(case: dict) -> str:
    for key in ("case_id", "id", "challenge_id", "armiId"):
        value = case.get(key) if isinstance(case, dict) else None
        if value:
            return str(value)
    return "unknown"


def extract_material_paths(case: dict) -> list:
    if not isinstance(case, dict):
        return []
    for key in ("materials", "material_paths", "files", "sources"):
        value = case.get(key)
        if isinstance(value, list):
            paths = []
            for item in value:
                if isinstance(item, str):
                    paths.append(item)
                elif isinstance(item, dict) and item.get("path"):
                    paths.append(str(item["path"]))
            if paths:
                return paths
    return []


def extract_questions(case: dict) -> list:
    if not isinstance(case, dict):
        return []
    for key in ("questions", "queries", "qa_pairs"):
        value = case.get(key)
        if isinstance(value, list):
            out = []
            for item in value:
                if isinstance(item, str):
                    out.append({"question": item})
                elif isinstance(item, dict) and item.get("question"):
                    out.append(item)
            if out:
                return out
    return []


def default_for_type(value):
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    return None


def build_case_summary(input_dir: str) -> dict:
    """Read /input/case.json and /input/task.md into a small summary.

    Returns a dict (never raises). Missing files are recorded as gaps.
    """
    summary = {
        "case_id": "unknown",
        "output_contract": {},
        "output_contract_keys": [],
        "questions": [],
        "material_paths": [],
        "task_md_present": False,
        "case_json_present": False,
        "input_errors": [],
    }
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    if os.path.isfile(case_path):
        summary["case_json_present"] = True
        case, err = load_json(case_path)
        if err is not None:
            summary["input_errors"].append(
                {"file": "case.json", "error": err})
            case = {}
        summary["case_id"] = extract_case_id(case)
        summary["material_paths"] = extract_material_paths(case)
        summary["questions"] = extract_questions(case)
        contract = case.get("output_contract")
        if isinstance(contract, dict):
            summary["output_contract"] = contract
            summary["output_contract_keys"] = list(contract.keys())
        elif contract is not None:
            summary["input_errors"].append(
                {"file": "case.json",
                 "error": "output_contract is not a dict; ignoring it"})
    else:
        summary["input_errors"].append(
            {"file": "case.json", "error": "missing input file"})

    summary["task_md_present"] = os.path.isfile(task_path)
    if not summary["task_md_present"]:
        summary["input_errors"].append(
            {"file": "task.md", "error": "missing input file"})

    return summary


def read_task_md(input_dir: str) -> str:
    path = os.path.join(input_dir, "task.md")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf")
                          else "utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# --------------------------------------------------------------------------
# Skeleton and finalization
# --------------------------------------------------------------------------

def make_skeleton(case_summary: dict, output_dir: str, work_dir: str,
                  reason: str) -> dict:
    """Write a minimal but valid result.json early (partial-result-first)."""
    started = now_iso()
    result = _base_result(case_summary, started)
    result["status"] = STATUS_PARTIAL
    result["partial_reason"] = reason
    result["change_log"].append({
        "step": "scaffold",
        "time": started,
        "action": "wrote partial result skeleton",
        "status": STATUS_PARTIAL,
        "note": reason,
    })
    result["runtime"]["started_at"] = started
    result["runtime"]["phase"] = "scaffold"

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    write_json(os.path.join(work_dir, "case_summary.json"), case_summary)
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _base_result(case_summary: dict, started: str) -> dict:
    result = {}
    result["case_id"] = case_summary.get("case_id", "unknown")
    # Echo every output_contract key with an honest default. The QA phase
    # replaces these with actual values; unknown keys are never dropped.
    contract = case_summary.get("output_contract") or {}
    for key, value in contract.items():
        result[key] = default_for_type(value)
    result["status"] = STATUS_PARTIAL
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["limitations"] = []
    result["errors"] = list(case_summary.get("input_errors") or [])
    result["degradations"] = []
    result["change_log"] = [{
        "step": "scaffold",
        "time": started,
        "action": "initialized result skeleton",
        "status": STATUS_PARTIAL,
    }]
    result["runtime"] = {
        "started_at": started,
        "finished_at": None,
        "phase": "scaffold",
        "budget_seconds": 900,
        "budget_requests": 64,
    }
    return result


def finalize(case_summary: dict, inventory: dict, answers: dict,
             output_dir: str, work_dir: str) -> dict:
    """Merge case summary + inventory + answers into the final result.json.

    Also emits human-readable ``inventory.md`` and ``evidence_map.json``.
    """
    started = now_iso()

    # Re-load the skeleton to preserve any partial fields we already wrote.
    existing = None
    skeleton_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(skeleton_path):
        existing, _ = load_json(skeleton_path)

    result = {}
    result["case_id"] = case_summary.get("case_id", "unknown")

    contract = case_summary.get("output_contract") or {}
    answers_output = answers.get("output") if isinstance(answers, dict) else {}
    for key, value in contract.items():
        if key in answers_output:
            result[key] = answers_output[key]
        else:
            result[key] = (existing or {}).get(key, default_for_type(value))

    result["status"] = _derive_status(case_summary, inventory, answers,
                                       existing)
    result["sources"] = _build_sources(inventory)
    result["library"] = _library_summary(inventory)
    result["answers"] = answers.get("answers") if isinstance(answers, dict) else []
    result["uncertainty"] = _merge_uncertainty(answers, inventory, case_summary)
    result["pending_human"] = _merge_pending_human(answers)
    result["limitations"] = _collect_limitations(answers, inventory,
                                                 case_summary)
    result["errors"] = _collect_errors(case_summary, inventory)
    result["degradations"] = _collect_degradations(inventory, answers)
    result["change_log"] = _merge_change_log(existing)
    result["change_log"].append({
        "step": "finalize",
        "time": started,
        "action": "merged inventory and answers into final result",
        "status": result["status"],
    })
    result["runtime"] = (existing or {}).get("runtime", {})
    result["runtime"]["finished_at"] = started
    result["runtime"]["phase"] = "finalize"
    result["runtime"]["inventory_file_count"] = len(
        inventory.get("files", []) if isinstance(inventory, dict) else [])

    # Write the three deliverables.
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(output_dir, "evidence_map.json"),
               _build_evidence_map(result))
    write_text(os.path.join(output_dir, "inventory.md"),
               _render_inventory_md(inventory, result))

    # Persist merged working files for re-runnable handoff.
    write_json(os.path.join(work_dir, "final_case_summary.json"), case_summary)
    return result


def _derive_status(case_summary, inventory, answers, existing):
    files = (inventory or {}).get("files", [])
    if not case_summary.get("case_json_present") and not case_summary.get(
            "task_md_present"):
        return STATUS_INSUFFICIENT
    if not files:
        return STATUS_INSUFFICIENT
    answer_list = answers.get("answers") if isinstance(answers, dict) else []
    if not answer_list and not case_summary.get("questions"):
        return STATUS_PARTIAL
    if existing and existing.get("status") == STATUS_INPUT_ERROR:
        return STATUS_PARTIAL
    return STATUS_OK if answer_list else STATUS_PARTIAL


def _build_sources(inventory) -> list:
    if not isinstance(inventory, dict):
        return []
    sources = []
    for f in inventory.get("files", []):
        sources.append({
            "relative_path": f.get("relative_path"),
            "sha256": f.get("sha256"),
            "type": f.get("type"),
            "size": f.get("size"),
            "source": f.get("source"),
            "version_marker": _has_marker([f.get("name", "")]),
        })
    return sources


def _library_summary(inventory) -> dict:
    if not isinstance(inventory, dict):
        return {}
    summary = inventory.get("summary", {})
    return {
        "file_types": summary.get("file_types", {}),
        "directory_tree": inventory.get("directory_tree", []),
        "duplicate_groups": inventory.get("duplicate_groups", []),
        "version_families": inventory.get("version_families", []),
        "permissions_and_source": [
            {"relative_path": f.get("relative_path"),
             "permissions": f.get("permissions"),
             "source": f.get("source")}
            for f in inventory.get("files", [])
        ],
        "metadata_fields": [
            {"relative_path": f.get("relative_path"),
             "metadata": f.get("metadata", {})}
            for f in inventory.get("files", [])
            if f.get("metadata")
        ],
    }


def _merge_uncertainty(answers, inventory, case_summary) -> list:
    out = []
    if isinstance(answers, dict):
        for item in answers.get("uncertainty") or []:
            if isinstance(item, dict):
                out.append(item)
    if isinstance(inventory, dict):
        for f in inventory.get("files", []):
            if f.get("type") == "pdf" and f.get("pdf_text_layer") is False:
                out.append({
                    "level": "unreadable",
                    "item": f.get("relative_path"),
                    "reason": "PDF has no extractable text layer; needs OCR",
                })
            if f.get("type") == "unknown":
                out.append({
                    "level": "unreadable",
                    "item": f.get("relative_path"),
                    "reason": "unrecognized file type",
                })
    return out


def _merge_pending_human(answers) -> list:
    if not isinstance(answers, dict):
        return []
    out = []
    for answer in answers.get("answers") or []:
        if isinstance(answer, dict) and answer.get("missing"):
            out.append({
                "question": answer.get("question"),
                "missing": answer.get("missing"),
            })
    for item in answers.get("pending_human") or []:
        if isinstance(item, dict):
            out.append(item)
    return out


def _collect_limitations(answers, inventory, case_summary) -> list:
    limits = []
    if isinstance(answers, dict):
        for item in answers.get("limitations") or []:
            if isinstance(item, dict):
                limits.append(item)
    if isinstance(inventory, dict):
        errors = inventory.get("errors") or []
        if errors:
            limits.append({
                "item": "inventory_errors",
                "detail": "%d file(s) could not be fully processed" % len(errors),
            })
    return limits


def _collect_errors(case_summary, inventory) -> list:
    out = list(case_summary.get("input_errors") or [])
    if isinstance(inventory, dict):
        out.extend(inventory.get("errors") or [])
    return out


def _collect_degradations(inventory, answers) -> list:
    out = []
    if isinstance(answers, dict):
        out.extend(answers.get("degradations") or [])
    if isinstance(inventory, dict):
        for f in inventory.get("files", []):
            if f.get("type") == "pdf" and f.get("pdf_text_layer") is None:
                out.append({
                    "item": f.get("relative_path"),
                    "reason": "pdftotext unavailable; PDF text layer not probed",
                })
    return out


def _merge_change_log(existing) -> list:
    if isinstance(existing, dict):
        return list(existing.get("change_log") or [])
    return []


def _build_evidence_map(result) -> list:
    """Flatten answer evidence into a re-runnable map of locators + quotes."""
    out = []
    for answer in result.get("answers") or []:
        if not isinstance(answer, dict):
            continue
        for ev in answer.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            out.append({
                "question": answer.get("question"),
                "file": ev.get("file"),
                "sha256": ev.get("sha256"),
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "confidence": ev.get("confidence") or ev.get("uncertainty"),
            })
    out.sort(key=lambda e: (e.get("file") or "", e.get("locator") or ""))
    return out


def _render_inventory_md(inventory, result) -> str:
    if not isinstance(inventory, dict):
        return "# inventory\n\nNo inventory available.\n"
    lines = ["# 资料清单 inventory", "",
             "- case_id: %s" % result.get("case_id"),
             "- 生成时间: %s" % inventory.get("generated_at"),
             "- 文件数: %d" % len(inventory.get("files", [])),
             "- 类型分布: %s" % json.dumps(inventory.get("summary", {}).get(
                 "file_types", {}), ensure_ascii=False),
             "",
             "## 文件清单", ""]
    for f in inventory.get("files", []):
        lines.append("- `%s` | %s | %d bytes | sha256 %s | source=%s" % (
            f.get("relative_path"), f.get("type"), f.get("size"),
            (f.get("sha256") or "unreadable")[:16] + "…",
            f.get("source")))
    lines.append("")
    lines.append("## 重复文件（按 SHA256）")
    for group in inventory.get("duplicate_groups", []):
        lines.append("- %s: %s" % (group.get("sha256")[:16] + "…",
                                   ", ".join(group.get("paths", []))))
    if not inventory.get("duplicate_groups"):
        lines.append("- （无）")
    lines.append("")
    lines.append("## 版本关系")
    for fam in inventory.get("version_families", []):
        lines.append("- 基名 `%s`（%s）:" % (fam.get("base_name"),
                                             fam.get("directory")))
        for m in fam.get("members", []):
            lines.append("  - `%s` sha256 %s" % (
                m.get("relative_path"),
                (m.get("sha256") or "unreadable")[:16] + "…"))
    if not inventory.get("version_families"):
        lines.append("- （无）")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_text(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path
