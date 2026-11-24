#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the methods-experiments-writing Skill.

Contract-first design: result.json is assembled by echoing every field of the
case's output_contract with a typed placeholder, then the agent improves it
incrementally. All scripts are deterministic, standard-library only, and never
fabricate scientific data or benchmark scores.

Subcommands:
  skeleton   -> write a legal partial result.json + state.json first (safe early)
  inventory  -> scan /input, classify materials, extract candidate facts
  validate   -> audit result.json against the output_contract and artifact paths
  selftest   -> run empty-input / bad-json / contract-echo self-tests
"""

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FACTS = 2000
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

_TEXT_EXTS = {
    ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".cfg",
    ".conf", ".ini", ".log", ".out", ".err", ".sh", ".py", ".rst", ".tex",
    ".bib", ".csv", ".tsv", ".nb", ".xml", ".properties", ".env",
}
_OFFICE_DOC_EXTS = {".docx", ".doc", ".odt"}
_TABULAR_EXTS = {".xlsx", ".xls", ".ods"}
_OFFICE_SLIDE_EXTS = {".pptx", ".ppt"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".tif", ".tiff", ".gif",
               ".webp", ".bmp"}
_PDF_EXTS = {".pdf"}

_RE_VERSION = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}(?:[a-zA-Z0-9._+-]*)?\b")
_RE_SEED = re.compile(r"\b(?:random_?seed|seed|torch\.manual_seed)\b\s*[=:(]\s*(\d+)\b", re.I)
_RE_KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]{0,63})\s*[:=]\s*(.+?)\s*$")
_RE_DATASET = re.compile(r"\b(?:dataset|data_?set|corpus|benchmark)\b\s*[=:]\s*([A-Za-z0-9_.\-\/]+)", re.I)
_RE_PATH = re.compile(
    r"((?:/(?:[\w.\-]+/)+[\w.\-]+)|\.\.?/[\w./\-]+|"
    r"[\w.\-]+\.(?:yaml|yml|json|jsonl|toml|cfg|conf|ini|py|sh|csv|tsv|txt|log|md))")
_RE_NUM_UNIT = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(%|ms|s\b|min\b|h\b|GB|MB|KB|GiB|MiB|KiB|"
    r"epochs?|iterations?|steps?|layers?|heads?|hidden_?size|"
    r"dim(?:ension)?s?|batch_?size|seed|lr|learning_?rate)\b", re.I)
_RE_CMD = re.compile(
    r"(?:^\s*[$%!]\s*)(.*)$|"
    r"(?:python(?:3)?\s+\S+|pip(?:3)?\s+install\s+\S+|"
    r"conda\s+(?:install|create|env)\s+\S+|bash\s+\S+\.sh|sh\s+\S+\.sh)")


def now_iso():
    return datetime.datetime.utcnow().strftime(ISO_UTC)


def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerantly. Returns (text, encoding, warnings)."""
    warnings = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, None, ["unreadable: %r" % exc]
    if size > max_bytes:
        return None, None, ["too_large"]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, None, ["unreadable: %r" % exc]
    if b"\x00" in raw[:8192]:
        return None, "binary", ["binary_with_nul"]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc, warnings
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    warnings.append("decoded_with_replace")
    return text, "utf-8-replace", warnings


def read_json_safe(path):
    """Parse JSON tolerantly. Returns (object_or_None, info_dict)."""
    text, enc, warns = read_text_safe(path)
    if text is None:
        return None, {"error": enc or "unreadable", "warnings": warns}
    try:
        return json.loads(text.lstrip("\ufeff")), {"encoding": enc, "warnings": warns}
    except (json.JSONDecodeError, ValueError) as exc:
        return None, {"error": "bad_json", "detail": str(exc)[:200], "warnings": warns}


def write_json(path, obj):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def resolve_case_id(case):
    for key in ("case_id", "id", "challenge_id", "challengeId", "challenge"):
        value = case.get(key) if isinstance(case, dict) else None
        if isinstance(value, (str, int)) and str(value).strip() not in ("", "None"):
            return str(value)
    return "unknown"


def placeholder_for(spec):
    """Return a typed empty placeholder that preserves the contract's shape."""
    if isinstance(spec, dict):
        kind = str(spec.get("type", spec.get("kind", ""))).lower()
        if kind in ("array", "list"):
            items = spec.get("items")
            return [placeholder_for(items)] if items is not None else []
        if kind in ("object", "map", "dict", "table"):
            props = spec.get("properties") or spec.get("fields")
            if isinstance(props, dict):
                return {str(k): placeholder_for(v) for k, v in props.items()}
            if "default" in spec:
                return spec["default"]
            return {}
        if kind in ("string", "str", "text"):
            return spec.get("default", "")
        if kind in ("number", "int", "integer", "float", "double", "bool", "boolean"):
            return spec.get("default", None)
        props = spec.get("properties") or spec.get("fields")
        if isinstance(props, dict):
            return {str(k): placeholder_for(v) for k, v in props.items()}
        if "default" in spec:
            return spec["default"]
        return {}
    if isinstance(spec, list):
        return [placeholder_for(item) for item in spec]
    return None


def contract_to_fields(contract):
    """Normalize output_contract to (fields_dict, ordered_keys, kind)."""
    if contract is None:
        return {}, [], "missing"
    if isinstance(contract, dict):
        fields = {str(k): placeholder_for(v) for k, v in contract.items()}
        return fields, [str(k) for k in contract.keys()], "dict"
    if isinstance(contract, list):
        fields, keys = {}, []
        for idx, item in enumerate(contract):
            if isinstance(item, str):
                name = item
                spec = None
            elif isinstance(item, dict):
                name = str(item.get("name", item.get("field", item.get("key", "field_%d" % idx))))
                spec = item
            else:
                name, spec = "field_%d" % idx, None
            fields[name] = placeholder_for(spec)
            keys.append(name)
        return fields, keys, "list"
    if isinstance(contract, str):
        return {contract: None}, [contract], "scalar"
    return {}, [], "unknown"


def resolve_output_contract(case):
    if not isinstance(case, dict):
        return None
    contract = case.get("output_contract")
    if contract is not None:
        return contract
    for key in ("contract", "output", "outputs", "result_schema"):
        value = case.get(key)
        if isinstance(value, dict) and ("output_contract" in value or "fields" in value
                                        or "properties" in value):
            return value.get("output_contract", value)
    return None


STANDARD_FIELDS = ("status", "artifacts", "sources", "uncertainty", "change_log",
                   "errors", "degradations", "limitations", "pending_human",
                   "runtime")

STANDARD_DEFAULTS = {
    "status": "partial",
    "artifacts": [],
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "errors": [],
    "degradations": [],
    "limitations": [],
    "pending_human": [],
}


def new_runtime():
    return {
        "started_at": now_iso(),
        "budget_seconds": 900,
        "budget_model_requests": 64,
        "budget_tokens_per_response": 16384,
        "updated_at": now_iso(),
    }


def update_runtime(existing):
    runtime = existing if isinstance(existing, dict) else {}
    runtime.setdefault("started_at", now_iso())
    runtime.setdefault("budget_seconds", 900)
    runtime.setdefault("budget_model_requests", 64)
    runtime["updated_at"] = now_iso()
    return runtime


def ensure_standard_fields(base, contract_keys):
    for field, default in STANDARD_DEFAULTS.items():
        if field not in base or base[field] is None:
            base[field] = copy.deepcopy(default)
    if "runtime" not in base or not isinstance(base.get("runtime"), dict):
        base["runtime"] = new_runtime()
    return base


def change_log_next_seq(log):
    if not isinstance(log, list):
        return 0
    return max([int(e.get("seq", -1)) for e in log if isinstance(e, dict)], default=-1) + 1


def cmd_skeleton(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    state_path = os.path.join(work_dir, "state.json")

    case_path = os.path.join(input_dir, "case.json")
    case = None
    case_status = "ok"
    case_info = {}
    if os.path.isfile(case_path):
        case, case_info = read_json_safe(case_path)
        if case is None:
            case_status = case_info.get("error", "bad_json")
    else:
        case_status = "missing"

    task_present = os.path.isfile(os.path.join(input_dir, "task.md"))
    case_id = resolve_case_id(case) if isinstance(case, dict) else "unknown"
    contract = resolve_output_contract(case) if isinstance(case, dict) else None
    fields, keys, contract_kind = contract_to_fields(contract)

    ts = now_iso()
    entry = {
        "seq": 0,
        "ts": ts,
        "phase": "skeleton",
        "action": "contract-first skeleton",
        "detail": "echoed %d output_contract fields with typed placeholders" % len(keys),
        "contract_kind": contract_kind,
        "contract_fields": len(keys),
        "evidence_count": 0,
        "status": "partial",
    }

    existing = None
    if os.path.isfile(result_path):
        existing, _ = read_json_safe(result_path)

    if isinstance(existing, dict):
        base = dict(existing)
        for key, value in fields.items():
            if key not in base or base[key] is None:
                base[key] = value
        base = ensure_standard_fields(base, keys)
        base["case_id"] = case_id
        log = base.get("change_log")
        if not isinstance(log, list):
            log = []
        entry["seq"] = change_log_next_seq(log)
        entry["action"] = "skeleton re-run (incremental, no clobber)"
        entry["detail"] = "re-echoed %d contract fields; preserved prior partial content" % len(keys)
        log.append(entry)
        base["change_log"] = log
        base["runtime"] = update_runtime(base.get("runtime"))
    else:
        base = {"case_id": case_id}
        for key, value in fields.items():
            base[key] = value
        base = ensure_standard_fields(base, keys)
        base["change_log"] = [entry]
        base["runtime"] = new_runtime()
        base["status"] = "partial"
        base["errors"] = base.get("errors", [])
        if case_status != "ok":
            base["errors"].append({
                "ts": ts, "phase": "skeleton", "code": case_status,
                "detail": "case.json unreadable or missing",
            })
        if contract_kind == "missing":
            base["degradations"].append({
                "ts": ts, "phase": "skeleton", "code": "no_output_contract",
                "detail": "case.json carries no output_contract; standard fields only",
            })

    write_json(result_path, base)

    state = {
        "case_id": case_id,
        "output_contract_kind": contract_kind,
        "output_contract_keys": keys,
        "output_contract_raw": contract if isinstance(contract, (dict, list)) else None,
        "phase": "skeleton",
        "started_at": ts,
        "updated_at": ts,
        "case_json_status": case_status,
        "task_present": task_present,
        "fact_count": 0,
        "material_count": 0,
    }
    write_json(state_path, state)
    return 0, "skeleton ok: case_id=%s contract_fields=%d contract_kind=%s" % (
        case_id, len(keys), contract_kind)


def classify_file(ext):
    ext = (ext or "").lower()
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in _OFFICE_DOC_EXTS:
        return "office_doc"
    if ext in _TABULAR_EXTS:
        return "tabular"
    if ext in _OFFICE_SLIDE_EXTS:
        return "office_slide"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS:
        return "text"
    return "other"


def make_locator(rel_path, ref):
    return "materials/%s#%s" % (rel_path.replace("\\", "/"), ref)


def append_fact(facts, seen, fact_id, kind, value, quote, locator):
    key = (locator, kind, str(value))
    if key in seen:
        return facts, seen
    seen.add(key)
    facts.append({
        "fact_id": fact_id,
        "kind": kind,
        "value": str(value),
        "quote": (quote or "").strip()[:300],
        "locator": locator,
        "confidence": "candidate",
    })
    return facts, seen


def extract_facts_from_text(text, rel_path, page_prefix=None):
    facts, seen = [], set()
    counter = [0]

    def next_id():
        counter[0] += 1
        return "F%04d" % counter[0]

    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        if len(facts) >= MAX_FACTS:
            break
        loc_ref = "L%d" % lineno
        if page_prefix is not None:
            loc_ref = "%s#L%d" % (page_prefix, lineno)
        locator = make_locator(rel_path, loc_ref)
        stripped = line.strip()
        if not stripped:
            continue
        for match in _RE_VERSION.finditer(line):
            facts, seen = append_fact(facts, seen, next_id(), "version",
                                      match.group(0), stripped, locator)
        for match in _RE_SEED.finditer(line):
            facts, seen = append_fact(facts, seen, next_id(), "seed",
                                      match.group(1), stripped, locator)
        for match in _RE_NUM_UNIT.finditer(line):
            facts, seen = append_fact(facts, seen, next_id(), "number_unit",
                                      match.group(0), stripped, locator)
        for match in _RE_DATASET.finditer(line):
            facts, seen = append_fact(facts, seen, next_id(), "dataset",
                                      match.group(1), stripped, locator)
        for match in _RE_PATH.finditer(line):
            facts, seen = append_fact(facts, seen, next_id(), "path",
                                      match.group(0), stripped, locator)
        for match in _RE_CMD.finditer(line):
            value = match.group(1) or match.group(0)
            facts, seen = append_fact(facts, seen, next_id(), "command",
                                      value.strip(), stripped, locator)
        kv = _RE_KV.match(stripped)
        if kv:
            facts, seen = append_fact(facts, seen, next_id(), "key_value",
                                      "%s = %s" % (kv.group(1), kv.group(2)),
                                      stripped, locator)
    return facts


def try_pdftotext(path):
    """Best-effort pdftotext; returns (pages, error) where pages is a list."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, timeout=60,
        )
    except FileNotFoundError:
        return None, "pdftotext_not_found"
    except subprocess.TimeoutExpired:
        return None, "pdftotext_timeout"
    except OSError as exc:
        return None, "pdftotext_error:%r" % exc
    if proc.returncode != 0:
        return None, "pdftotext_rc_%d" % proc.returncode
    text = proc.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    return [(idx + 1, page) for idx, page in enumerate(pages) if page.strip()], None


def cmd_inventory(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    materials = []
    facts = []
    degradations = []
    total_bytes = 0

    if not os.path.isdir(input_dir):
        write_json(os.path.join(work_dir, "materials_inventory.json"),
                   {"generated_at": now_iso(), "input_missing": True, "total_files": 0,
                    "total_bytes": 0, "files": []})
        write_json(os.path.join(work_dir, "extracted_facts.json"),
                   {"generated_at": now_iso(), "fact_count": 0, "facts": []})
        _update_state(work_dir, phase="inventory", fact_count=0, material_count=0)
        return 0, "inventory: input dir missing (%s)" % input_dir

    for root, dirs, files in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
        for name in sorted(files):
            full = os.path.join(root, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            total_bytes += size
            ext = os.path.splitext(name)[1]
            kind = classify_file(ext)
            status = "indexed"
            if size > MAX_FILE_BYTES:
                status = "skipped_too_large"
            materials.append({
                "path": full, "rel_path": rel, "size": size, "ext": ext,
                "kind": kind, "status": status,
            })

    for item in materials:
        if item["status"] == "skipped_too_large":
            degradations.append({
                "ts": now_iso(), "phase": "inventory", "code": "skipped_too_large",
                "detail": "skipped %s (%d bytes)" % (item["rel_path"], item["size"]),
            })
            continue
        full = item["path"]
        rel = item["rel_path"]
        if item["kind"] == "text":
            text, enc, warns = read_text_safe(full)
            if text is None:
                degradations.append({
                    "ts": now_iso(), "phase": "inventory", "code": "unreadable",
                    "detail": "%s: %s" % (rel, enc),
                })
                continue
            for fact in extract_facts_from_text(text, rel):
                if len(facts) < MAX_FACTS:
                    facts.append(fact)
            if warns:
                degradations.append({
                    "ts": now_iso(), "phase": "inventory", "code": "encoding_warn",
                    "detail": "%s: %s" % (rel, ",".join(warns)),
                })
        elif item["kind"] == "pdf":
            pages, err = try_pdftotext(full)
            if pages:
                for page_no, page_text in pages:
                    for fact in extract_facts_from_text(
                            page_text, rel, page_prefix="p%d" % page_no):
                        if len(facts) < MAX_FACTS:
                            facts.append(fact)
            else:
                degradations.append({
                    "ts": now_iso(), "phase": "inventory", "code": "pdf_not_extracted",
                    "detail": "%s: %s (use pdf-text-tool)" % (rel, err),
                })
        else:
            degradations.append({
                "ts": now_iso(), "phase": "inventory", "code": "not_text_extracted",
                "detail": "%s kind=%s (use office-renderer/ocr-tool)" % (rel, item["kind"]),
            })

    write_json(os.path.join(work_dir, "materials_inventory.json"), {
        "generated_at": now_iso(), "input_dir": input_dir,
        "total_files": len(materials), "total_bytes": total_bytes,
        "files": materials,
    })
    write_json(os.path.join(work_dir, "extracted_facts.json"), {
        "generated_at": now_iso(), "fact_count": len(facts), "facts": facts,
    })
    _update_state(work_dir, phase="inventory", fact_count=len(facts),
                  material_count=len(materials), degradations=degradations)
    return 0, "inventory: %d files, %d candidate facts, %d degradations" % (
        len(materials), len(facts), len(degradations))


def _update_state(work_dir, phase, fact_count=None, material_count=None,
                  degradations=None):
    state_path = os.path.join(work_dir, "state.json")
    state, _ = read_json_safe(state_path) if os.path.isfile(state_path) else (None, {})
    if not isinstance(state, dict):
        state = {}
    state["phase"] = phase
    state["updated_at"] = now_iso()
    if fact_count is not None:
        state["fact_count"] = fact_count
    if material_count is not None:
        state["material_count"] = material_count
    if degradations is not None and degradations:
        existing = state.get("degradations")
        state["degradations"] = (existing if isinstance(existing, list) else []) + degradations
    write_json(state_path, state)


def change_log_monotonic(log):
    if not isinstance(log, list):
        return False
    seqs = [int(e.get("seq", -1)) for e in log if isinstance(e, dict) and "seq" in e]
    if not seqs:
        return len(log) > 0
    return seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def cmd_validate(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    result_path = os.path.join(output_dir, "result.json")
    state_path = os.path.join(work_dir, "state.json")
    result, info = read_json_safe(result_path)
    if result is None or not isinstance(result, dict):
        write_json(os.path.join(work_dir, "validation_report.json"), {
            "generated_at": now_iso(), "summary": "fail", "passed": 0, "failed": 1,
            "checks": [{"name": "result.json valid", "ok": False,
                        "detail": info.get("error", "missing")}],
        })
        return 0, "validate: result.json missing or invalid"

    state, _ = read_json_safe(state_path) if os.path.isfile(state_path) else (None, {})
    contract_keys = (state or {}).get("output_contract_keys", [])

    check("case_id present", bool(result.get("case_id")))
    check("status present", "status" in result)
    check("status valid", result.get("status") in STATUSES,
          "status=%r" % result.get("status"))

    missing_keys = [k for k in contract_keys if k not in result]
    check("output_contract all fields echoed", not missing_keys,
          "missing=%s" % (missing_keys if missing_keys else "[]"))
    for key in contract_keys:
        check("contract field non-empty-or-placeholder: %s" % key,
              True, "present")

    for field in STANDARD_FIELDS:
        check("standard field present: %s" % field, field in result)

    log = result.get("change_log")
    check("change_log non-empty", isinstance(log, list) and len(log) > 0)
    check("change_log append-only seq", change_log_monotonic(log))

    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        missing_art = []
        for artifact in artifacts:
            path = artifact if isinstance(artifact, str) else (
                artifact.get("path") if isinstance(artifact, dict) else None)
            if path:
                full = path if os.path.isabs(path) else os.path.join(output_dir, path)
                if not os.path.isfile(full):
                    missing_art.append(path)
        check("artifact paths exist", not missing_art,
              "missing=%s" % (missing_art if missing_art else "[]"))
    else:
        check("artifact paths exist", False, "artifacts is not a list")

    evidence_path = os.path.join(output_dir, "evidence_map.json")
    if os.path.isfile(evidence_path):
        evidence, _ = read_json_safe(evidence_path)
        entries = evidence.get("evidence", evidence) if isinstance(evidence, dict) else evidence
        if isinstance(entries, list):
            nonempty = sum(1 for e in entries if isinstance(e, dict)
                           and e.get("locator") and e.get("quote"))
        elif isinstance(entries, dict):
            nonempty = sum(1 for e in entries.values() if isinstance(e, dict)
                           and e.get("locator") and e.get("quote"))
        else:
            nonempty = 0
        check("evidence_map present with locator+quote", nonempty > 0,
              "nonempty=%d" % nonempty)
    else:
        check("evidence_map present", False, "not found (allowed in early skeleton)")

    passed = sum(1 for c in checks if c["ok"])
    failed = len(checks) - passed
    write_json(os.path.join(work_dir, "validation_report.json"), {
        "generated_at": now_iso(), "summary": "pass" if failed == 0 else "fail",
        "passed": passed, "failed": failed, "checks": checks,
    })
    return 0, "validate: %s (%d pass / %d fail)" % (
        "pass" if failed == 0 else "fail", passed, failed)


def cmd_selftest(selftest_dir=None):
    base = selftest_dir or tempfile.mkdtemp(prefix="skill_selftest_")
    os.makedirs(base, exist_ok=True)
    results = []

    def run_case(name, setup):
        case_dir = os.path.join(base, name)
        if os.path.isdir(case_dir):
            shutil.rmtree(case_dir, ignore_errors=True)
        inp = os.path.join(case_dir, "input")
        out = os.path.join(case_dir, "output")
        wrk = os.path.join(case_dir, "work")
        os.makedirs(inp, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        os.makedirs(wrk, exist_ok=True)
        setup(inp)
        record = {}
        try:
            cmd_skeleton(inp, out, wrk)
            cmd_inventory(inp, out, wrk)
            cmd_validate(inp, out, wrk)
            res, _ = read_json_safe(os.path.join(out, "result.json"))
            record["pass"] = bool(isinstance(res, dict) and res.get("case_id")
                                  and "status" in res)
            record["detail"] = "status=%r" % (res or {}).get("status")
        except Exception as exc:  # noqa: BLE001 - selftest must never crash
            record["pass"] = False
            record["detail"] = "exception: %r" % exc
        results.append((name, record))

    run_case("empty_input", lambda inp: None)

    def bad_json(inp):
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{this is not valid json")
    run_case("bad_json", bad_json)

    def contract_echo(inp):
        case = {
            "case_id": "demo-1",
            "output_contract": {
                "abstract": {"type": "string"},
                "parameters": {"type": "array", "items": {"type": "object"}},
                "references": {"type": "object"},
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n")
        with open(os.path.join(inp, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("lr: 0.001\nseed: 42\nversion: 1.2.3\n")
    run_case("contract_echo", contract_echo)

    all_ok = all(record["pass"] for _, record in results)
    print("selftest base: %s" % base)
    for name, record in results:
        print("  [%s] %s -> %s" % ("PASS" if record["pass"] else "FAIL", name,
                                   record["detail"]))
    print("selftest summary: %s" % ("PASS" if all_ok else "FAIL"))
    return (0 if all_ok else 1), "selftest %s" % ("pass" if all_ok else "fail")


def run(argv):
    parser = argparse.ArgumentParser(
        prog="methods-experiments-writing",
        description="Methods/Experiments chapter writing skill (contract-first).")
    parser.add_argument("cmd", nargs="?", default="help",
                        choices=["skeleton", "inventory", "validate", "selftest", "help"])
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--selftest-dir", default=None)
    args = parser.parse_args(argv)

    if args.cmd == "skeleton":
        code, msg = cmd_skeleton(args.input, args.output, args.work)
    elif args.cmd == "inventory":
        code, msg = cmd_inventory(args.input, args.output, args.work)
    elif args.cmd == "validate":
        code, msg = cmd_validate(args.input, args.output, args.work)
    elif args.cmd == "selftest":
        code, msg = cmd_selftest(args.selftest_dir)
    else:
        parser.print_help()
        return
    print(msg)
    sys.exit(code)


if __name__ == "__main__":
    run(sys.argv[1:])
