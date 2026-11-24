#!/usr/bin/env python3
"""fact-traceable academic writing skill — pipeline entry point.

Subcommands
-----------
init        read /input, build a partial result.json skeleton (contract-aligned)
index       scan materials, build /app/work/material_index.json
log         append one change_log entry (ts/step/note) to work log + result.json
finalize    merge change_log, re-ensure contract keys, set status, stamp runtime
(no cmd)    run init -> index -> finalize

Design invariants
-----------------
* Standard library only; deterministic ordering; atomic writes.
* result.json contains every output_contract key verbatim (missing values as
  null/[]/{}), never renamed or dropped, merged with the standard fields
  case_id / status / sources / uncertainty / pending_human / change_log /
  errors / degradations / runtime.
* No fabricated data: unparsable or oversized materials are recorded as
  degradations, never guessed from filenames or labels.
"""
import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys

VERSION = "2.0.0"
MAX_FILE_BYTES = 2 * 1024 * 1024      # skip larger files, record degradation
PREVIEW_CHARS = 4000                  # text preview kept per material
PDFTEXT_BIN = "pdftotext"

# output_contract fallback: the three deliverable families required by the
# problem statement (target artifact, fact/source map, version & boundaries).
DEFAULT_CONTRACT = {
    "deliverable": None,
    "fact_source_map": None,
    "version_limits": None,
}

# Files that are routing metadata, never scientific material.
META_NAMES = {"case.json", "task.md", "resources.yaml", "result.json"}

_STD_KEYS = (
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
)


# --------------------------------------------------------------------------- #
# I/O helpers (UTF-8 with BOM tolerance, atomic writes)
# --------------------------------------------------------------------------- #
def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read UTF-8 text tolerating BOM and undecodable bytes.

    Returns (text, error) where text is None on failure. Oversized files are
    reported as an error and skipped.
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat failed: %s" % e
    if size > max_bytes:
        return None, "oversized (%d bytes > %d)" % (size, max_bytes)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            return None, "open failed: %s" % e
    return None, "undecodable text"


def read_json_safe(path):
    """Return (object_or_None, error_string)."""
    text, err = read_text(path)
    if err is not None:
        return None, err
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        # last-resort: strip BOM/whitespace and try once more
        try:
            return json.loads(text.lstrip("\ufeff").strip()), None
        except json.JSONDecodeError:
            return None, "bad JSON: %s" % e


def write_text_atomic(path, text):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp-" + os.path.basename(path) + "-%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json_atomic(path, obj):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Input discovery & contract parsing
# --------------------------------------------------------------------------- #
def discover_files(input_dir):
    """Return sorted list of regular file paths under input_dir (recursive)."""
    out = []
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            out.append(os.path.join(root, name))
    return out


def find_case_file(input_dir):
    """Locate case.json (or any *.json that is not a resource manifest)."""
    for p in discover_files(input_dir):
        if os.path.basename(p).lower() == "case.json":
            return p
    for p in discover_files(input_dir):
        if p.lower().endswith(".json"):
            return p
    return None


def case_id_from(case, source):
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id", "task_id"):
            v = case.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    # fallback: case file name stem if source is a file path, else dir name
    base = os.path.basename(str(source).rstrip("/\\")) or "case"
    return os.path.splitext(base)[0] or "case"


def extract_contract(case):
    """Pull output_contract from case.json; tolerate absent / empty / list forms."""
    if not isinstance(case, dict):
        return DEFAULT_CONTRACT
    for key in ("output_contract", "outputContract", "contract"):
        v = case.get(key)
        if isinstance(v, (dict, list)) and v:
            return v
        if isinstance(v, dict):
            return v  # empty dict is still an explicit contract
    return DEFAULT_CONTRACT


def contract_defaults(spec):
    """Recursively mirror a contract's key structure with null/[]/{}/literal defaults."""
    if isinstance(spec, dict):
        # Leaf schema: {"type": "...", "default": ...} — use its default.
        if "default" in spec:
            import copy
            return copy.deepcopy(spec["default"])
        if "type" in spec:
            t = str(spec.get("type", "")).lower()
            if t in ("array", "list"):
                return []
            if t in ("object", "dict", "map"):
                props = spec.get("properties") or spec.get("fields") or spec.get("items") or {}
                return contract_defaults(props) if props else {}
            return None
        # Plain mapping: fields -> sub-spec.
        out = {}
        for k, v in spec.items():
            if isinstance(v, dict):
                out[k] = contract_defaults(v)
            elif isinstance(v, list):
                out[k] = []
            else:
                # string/bool/int/float/None = description or literal hint,
                # never a nested field name; default to null (no fabrication).
                out[k] = None
        return out
    if isinstance(spec, list):
        # A list of field names (strings).
        return {k: None for k in spec if isinstance(k, str)}
    if isinstance(spec, str):
        # A single field name.
        return {spec: None}
    return None


def flatten_paths(obj, prefix=""):
    """Yield (dotted_path, value) for every leaf in a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = "%s.%s" % (prefix, k) if prefix else str(k)
            yield from flatten_paths(v, path)
    elif isinstance(obj, list):
        yield prefix, obj
    else:
        yield prefix, obj


# --------------------------------------------------------------------------- #
# result.json skeleton (contract-aligned + standard fields)
# --------------------------------------------------------------------------- #
def build_skeleton(case, case_id, input_dir, work_dir):
    contract = extract_contract(case)
    defaults = contract_defaults(contract) or {}
    if not isinstance(defaults, dict):
        defaults = {"value": defaults}

    result = {
        "case_id": case_id,
        "status": "in_progress",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "skill_version": VERSION,
            "started_at": now_iso(),
            "ended_at": None,
            "input_dir": os.path.abspath(input_dir),
            "work_dir": os.path.abspath(work_dir),
            "output_contract_source": "case.json.output_contract"
            if isinstance(case, dict) and case.get("output_contract") is not None
            else "default",
        },
    }

    collisions = []
    for key, default in defaults.items():
        if key in result:
            collisions.append(key)
            continue
        result[key] = default

    log = [{
        "ts": now_iso(),
        "step": "init",
        "note": "partial result.json skeleton created (contract-aligned)",
        "level": "info",
    }]
    if collisions:
        log.append({
            "ts": now_iso(), "step": "init", "level": "warn",
            "note": "output_contract keys collide with standard keys, kept standard: %s"
                    % ", ".join(collisions),
        })
    result["change_log"] = log

    contract_meta = {
        "case_id": case_id,
        "output_contract": contract,
        "resolved_defaults": defaults,
        "collisions_with_standard_keys": collisions,
        "generated_at": now_iso(),
    }
    return result, contract_meta


# --------------------------------------------------------------------------- #
# Material index
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|(?<![\w])\d+\.\d+")


def _kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".pdf": "pdf", ".txt": "text", ".md": "text", ".csv": "text",
        ".tsv": "text", ".json": "text", ".jsonl": "text", ".log": "text",
        ".xml": "text", ".html": "text", ".tex": "text", ".bib": "text",
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image",
        ".tiff": "image", ".svg": "image", ".docx": "office", ".xlsx": "office",
        ".pptx": "office", ".doc": "office", ".xls": "office", ".ppt": "office",
    }.get(ext, "other")


def _extract_pdf_text(path):
    """Try pdftotext via subprocess; return (text, error)."""
    try:
        p = subprocess.run(
            [PDFTEXT_BIN, "-enc", "UTF-8", path, "-"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return None, "pdftotext not available"
    except subprocess.TimeoutExpired:
        return None, "pdftotext timeout"
    except Exception as e:  # pragma: no cover
        return None, "pdftotext failed: %s" % e
    if p.returncode != 0:
        return None, "pdftotext rc=%d: %s" % (p.returncode, (p.stderr or "").strip()[:200])
    return p.stdout, None


def index_material(path, rel):
    entry = {
        "id": rel.replace("\\", "/"),
        "path": os.path.abspath(path),
        "rel_path": rel.replace("\\", "/"),
        "kind": _kind_of(path),
        "size_bytes": None,
        "status": "ok",
        "error": None,
        "text_preview": None,
        "detected_numbers": [],
        "key_claims": [],   # Agent fills: [{claim, locator, quote, confidence}]
        "citations": [],    # Agent fills: [{key, author, year, ...}]
    }
    try:
        entry["size_bytes"] = os.path.getsize(path)
    except OSError as e:
        entry["status"], entry["error"] = "unreadable", str(e)
        return entry
    if entry["size_bytes"] > MAX_FILE_BYTES:
        entry["status"], entry["error"] = "oversized", "exceeds %d bytes" % MAX_FILE_BYTES
        return entry

    text = None
    if entry["kind"] == "pdf":
        text, err = _extract_pdf_text(path)
        if err:
            entry["status"], entry["error"] = "unparsable", err
            return entry
    elif entry["kind"] == "text":
        text, err = read_text(path)
        if err:
            entry["status"], entry["error"] = "unparsable", err
            return entry
    else:
        entry["status"], entry["error"] = "unsupported_kind", \
            "binary/non-text material requires OCR/render tool; left for Agent"
        return entry

    if text:
        entry["text_preview"] = text[:PREVIEW_CHARS]
        nums = _NUM_RE.findall(text)
        entry["detected_numbers"] = list(dict.fromkeys(nums))[:200]
    return entry


def cmd_init(args):
    case_path = find_case_file(args.input)
    case, err = (read_json_safe(case_path) if case_path else (None, "no case.json found"))
    case_id = case_id_from(case, case_path or args.input)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    # A fresh init starts a fresh append-only change log so that repeated runs
    # over the same input produce identical skeletons (determinism).
    log_path = os.path.join(args.work, "change_log.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)

    result, contract_meta = build_skeleton(case, case_id, args.input, args.work)
    if err:
        result.setdefault("errors", []).append(
            {"ts": now_iso(), "source": "case.json", "message": err})
        result["status"] = "partial"
    if case_path is None:
        result.setdefault("errors", []).append(
            {"ts": now_iso(), "source": "input", "message": "no case.json in /input"})

    write_json_atomic(os.path.join(args.output, "result.json"), result)
    write_json_atomic(os.path.join(args.work, "contract_meta.json"), contract_meta)
    write_json_atomic(os.path.join(args.work, "material_index.json"),
                      {"version": VERSION, "generated_at": now_iso(), "materials": []})
    print(json.dumps({"ok": True, "case_id": case_id, "case_path": case_path,
                      "contract_keys": list(contract_meta["resolved_defaults"].keys()),
                      "errors": result["errors"]}, ensure_ascii=False))
    return 0


def cmd_index(args):
    files = discover_files(args.input)
    mats = []
    for p in files:
        base = os.path.basename(p).lower()
        if base in META_NAMES:
            continue
        rel = os.path.relpath(p, args.input)
        mats.append(index_material(p, rel))
    index = {"version": VERSION, "generated_at": now_iso(), "materials": mats}
    os.makedirs(args.work, exist_ok=True)
    write_json_atomic(os.path.join(args.work, "material_index.json"), index)
    n_unparsable = sum(1 for m in mats if m["status"] != "ok")
    print(json.dumps({"ok": True, "materials": len(mats),
                      "unparsable": n_unparsable}, ensure_ascii=False))
    return 0


def _result_path(args):
    return os.path.join(args.output, "result.json")


def cmd_log(args):
    entry = {
        "ts": now_iso(),
        "step": args.step or "manual",
        "note": args.note,
        "level": args.level or "info",
    }
    os.makedirs(args.work, exist_ok=True)
    append_jsonl(os.path.join(args.work, "change_log.jsonl"), entry)
    rp = _result_path(args)
    obj, err = read_json_safe(rp)
    if err is None and isinstance(obj, dict):
        obj.setdefault("change_log", []).append(entry)
        write_json_atomic(rp, obj)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    rp = _result_path(args)
    obj, err = read_json_safe(rp)
    if obj is None or not isinstance(obj, dict):
        # rebuild a minimal skeleton rather than crashing
        case_path = find_case_file(args.input)
        case, cerr = (read_json_safe(case_path) if case_path else (None, "no case.json"))
        obj, _ = build_skeleton(case, case_id_from(case, case_path or args.input),
                                args.input, args.work)
        if err or cerr:
            obj.setdefault("errors", []).append(
                {"ts": now_iso(), "source": "finalize", "message": err or cerr})
        obj["status"] = "partial"

    # 1) merge change_log.jsonl (idempotent, dedupe by ts+step+note)
    log_path = os.path.join(args.work, "change_log.jsonl")
    jl = []
    if os.path.exists(log_path):
        log_text, _ = read_text(log_path)
        for line in (log_text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                jl.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    existing = obj.setdefault("change_log", [])
    seen = {(e.get("ts"), e.get("step"), e.get("note")) for e in existing
            if isinstance(e, dict)}
    for e in jl:
        key = (e.get("ts"), e.get("step"), e.get("note"))
        if key not in seen:
            existing.append(e)
            seen.add(key)

    # 2) re-ensure every output_contract key is present (fill defaults)
    case_path = find_case_file(args.input)
    case, _ = (read_json_safe(case_path) if case_path else (None, None))
    defaults = contract_defaults(extract_contract(case)) or {}
    if isinstance(defaults, dict):
        for k, v in defaults.items():
            obj.setdefault(k, v)

    # 3) stamp status and runtime
    if getattr(args, "status", None):
        obj["status"] = args.status
    else:
        # honest default: never silently mark complete
        obj.setdefault("status", "partial")
    rt = obj.setdefault("runtime", {})
    rt["ended_at"] = now_iso()
    rt["skill_version"] = VERSION

    # 4) append a finalize change record
    obj.setdefault("change_log", []).append({
        "ts": now_iso(), "step": "finalize", "level": "info",
        "note": "finalize: merged change_log, re-ensured contract keys, status=%s" % obj["status"],
    })

    write_json_atomic(rp, obj)
    print(json.dumps({"ok": True, "status": obj["status"],
                      "change_log_entries": len(obj["change_log"])}, ensure_ascii=False))
    return 0


def cmd_all(args):
    for fn in (cmd_init, cmd_index, cmd_finalize):
        rc = fn(args)
        if rc != 0:
            return rc
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", default="/app/output")
    common.add_argument("--work", default="/app/work")

    ap = argparse.ArgumentParser(description="fact-traceable academic writing pipeline",
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", parents=[common],
                            help="write contract-aligned partial skeleton")
    p_init.set_defaults(func=cmd_init)

    p_index = sub.add_parser("index", parents=[common], help="build material index")
    p_index.set_defaults(func=cmd_index)

    p_log = sub.add_parser("log", parents=[common], help="append one change_log entry")
    p_log.add_argument("--note", required=True)
    p_log.add_argument("--step", default="manual")
    p_log.add_argument("--level", default="info", choices=["info", "warn", "error"])
    p_log.set_defaults(func=cmd_log)

    p_fin = sub.add_parser("finalize", parents=[common],
                           help="merge log, ensure contract, stamp status")
    p_fin.add_argument("--status", choices=["in_progress", "partial", "complete", "failed"])
    p_fin.set_defaults(func=cmd_finalize)

    args = ap.parse_args(argv)
    if args.cmd is None:
        return cmd_all(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
