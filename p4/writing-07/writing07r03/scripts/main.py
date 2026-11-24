#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-intro-writing Skill runner — pure standard library, deterministic.

Subcommands
-----------
scan       Discover /input, extract output_contract -> work/input_manifest.json
skeleton   Emit a valid partial result.json -> output/result.json (fail-safe seed)
validate   Check result.json against contract + evidence map -> work/validation_report.json
finalize   Deterministically re-serialize and validate result.json / evidence / change_log
selftest   Run scripts/selftest.py (empty-input / bad-json / determinism / roundtrip)
run        scan + skeleton safety chain (used before heavy writing work)

Determinism contract: every JSON this script writes uses sorted keys and no
wall-clock timestamps, so identical input produces byte-identical output.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA_VERSION = "1.1.0"
ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
STANDARD_FIELDS = (
    "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "artifacts", "runtime",
)
# Contract lookup priority (first hit wins, case-insensitive key match).
CONTRACT_KEYS = (
    "output_contract", "outputContract", "output", "expected_output",
    "expectedOutput", "fields", "required_fields", "requiredFields",
)
CASE_ID_KEYS = (
    "case_id", "caseId", "challenge_id", "challengeId", "id",
)
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per-material cap

SECTION_MARKERS = {
    "background": ("研究背景", "背景", "研究脉络", "Background", "Introduction"),
    "related_work": ("相关工作", "相关工作组织", "Related Work", "Related work", "Related Work and"),
    "gap": ("研究空白", "研究缺口", "研究差距", "未解决问题", "Research Gap", "Gap", "Open Problems"),
    "contribution": ("贡献", "本文贡献", "主要贡献", "贡献表述", "Contributions", "Contribution"),
}


# --------------------------------------------------------------------------- #
# Deterministic I/O helpers
# --------------------------------------------------------------------------- #

def dumps(obj):
    """Serialize JSON deterministically (sorted keys, no ASCII escaping)."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path, obj):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(obj))


def read_text(path):
    """Read text with BOM / bad-encoding tolerance. Never raises."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decode_replaced"


def read_json_tolerant(path):
    """Return (obj, error). Never raises."""
    text, err = read_text(path)
    if err and text is None:
        return None, err
    if text is None:
        return None, "empty"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "json_decode_error: %s" % exc


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def stable_walk(root):
    """List regular files sorted by normalized relative path (deterministic)."""
    root = os.path.abspath(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((rel, full))
    out.sort(key=lambda x: x[0])
    return out


def file_kind(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    kinds = {
        "json": "json", "md": "markdown", "markdown": "markdown", "txt": "text",
        "csv": "csv", "bib": "bibtex", "tex": "latex", "yaml": "yaml", "yml": "yaml",
        "pdf": "pdf", "docx": "office", "pptx": "office", "xlsx": "office",
        "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    }
    return kinds.get(ext, "other")


def casefold_dict_keys(d):
    """Return mapping lowercased-key -> (original_key, value)."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        out.setdefault(str(k).lower(), (k, v))
    return out


# --------------------------------------------------------------------------- #
# Contract discovery
# --------------------------------------------------------------------------- #

def find_contract(case):
    """Locate output_contract by priority; return (source_key, raw, normalized)."""
    if not isinstance(case, dict):
        return None, None, ([], {})
    folded = casefold_dict_keys(case)
    for want in CONTRACT_KEYS:
        if want.lower() in folded:
            src_key, raw = folded[want.lower()]
            fields, schema = normalize_contract(raw)
            return src_key, raw, (fields, schema)
    # Fall back: shallow nested scan (depth <= 2) for any key matching.
    for key, value in case.items():
        if isinstance(value, dict):
            sub = casefold_dict_keys(value)
            for want in CONTRACT_KEYS:
                if want.lower() in sub:
                    src_key, raw = sub[want.lower()]
                    fields, schema = normalize_contract(raw)
                    return src_key, raw, (fields, schema)
    return None, None, ([], {})


def normalize_contract(raw):
    """Normalize contract to (sorted_field_list, schema_dict)."""
    if isinstance(raw, dict):
        fields = sorted(str(k) for k in raw.keys())
        schema = {k: raw[k] for k in fields}
        return fields, schema
    if isinstance(raw, list):
        fields = []
        schema = {}
        for item in raw:
            if isinstance(item, str):
                fields.append(item)
                schema.setdefault(item, None)
            elif isinstance(item, dict) and "name" in item:
                name = str(item["name"])
                fields.append(name)
                schema.setdefault(name, item.get("type"))
        fields = sorted(set(fields))
        return fields, schema
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()], {raw.strip(): None}
    return [], {}


def extract_case_id(case):
    if not isinstance(case, dict):
        return None, None
    folded = casefold_dict_keys(case)
    for want in CASE_ID_KEYS:
        if want.lower() in folded:
            return folded[want.lower()][0], folded[want.lower()][1]
    return None, None


def default_for_schema(spec):
    """Map a contract schema value to its empty/unknown backfill."""
    if isinstance(spec, dict):
        return {}
    if isinstance(spec, list):
        return []
    if isinstance(spec, str):
        low = spec.lower()
        if low in ("array", "list"):
            return []
        if low in ("object", "dict", "map"):
            return {}
    return None


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #

def cmd_scan(args):
    input_dir = args.input
    work_dir = args.work
    os.makedirs(work_dir, exist_ok=True)

    files_meta = []
    degradations = []
    errors = []
    case_obj = None
    task_text = None
    contract_source = None
    contract_fields = []
    contract_schema = {}

    if not os.path.isdir(input_dir):
        errors.append("input_dir_missing: %s" % input_dir)
    else:
        for rel, full in stable_walk(input_dir):
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                errors.append("stat_error: %s %s" % (rel, exc))
                continue
            if size > MAX_FILE_BYTES:
                degradations.append({
                    "item": rel, "reason": "size_over_2MB_skipped", "bytes": size,
                })
                files_meta.append({
                    "path": rel, "size": size, "kind": file_kind(rel),
                    "parse_status": "skipped_large",
                })
                continue
            try:
                with open(full, "rb") as fh:
                    raw = fh.read()
            except OSError as exc:
                errors.append("read_error: %s %s" % (rel, exc))
                files_meta.append({
                    "path": rel, "size": size, "kind": file_kind(rel),
                    "parse_status": "read_error",
                })
                continue
            digest = sha256_bytes(raw)
            meta = {
                "path": rel, "size": size, "kind": file_kind(rel),
                "sha256": digest, "parse_status": "ok",
            }
            if rel.lower().endswith("case.json") or rel == "case.json":
                obj, err = read_json_tolerant(full)
                if err:
                    meta["parse_status"] = "bad_json"
                    errors.append("case.json_parse: %s" % err)
                else:
                    case_obj = obj
            if rel.lower().endswith("task.md") or rel == "task.md":
                text, _ = read_text(full)
                task_text = text
            files_meta.append(meta)

    if case_obj is not None:
        case_id_key, case_id_val = extract_case_id(case_obj)
        contract_source, _, (contract_fields, contract_schema) = find_contract(case_obj)
        if contract_source is None:
            degradations.append({
                "item": "case.json", "reason": "no_output_contract_found",
                "detail": "contract fields empty; standard fields still required",
            })
    else:
        case_id_key, case_id_val = None, None
        errors.append("case.json_not_parsed")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id_val,
        "case_id_key": case_id_key,
        "contract_source_key": contract_source,
        "contract_fields": contract_fields,
        "contract_schema": contract_schema,
        "files": files_meta,
        "degradations": degradations,
        "errors": errors,
    }
    write_json(os.path.join(work_dir, "input_manifest.json"), manifest)

    normalized = {
        "case_id": case_id_val,
        "case_id_key": case_id_key,
        "contract_source_key": contract_source,
        "contract_fields": contract_fields,
        "contract_schema": contract_schema,
        "case_raw": case_obj if isinstance(case_obj, dict) else None,
        "task_md_preview": (task_text[:4000] if isinstance(task_text, str) else None),
        "degradations": degradations,
        "errors": errors,
    }
    write_json(os.path.join(work_dir, "case_normalized.json"), normalized)

    print(json.dumps({
        "case_id": case_id_val,
        "contract_fields": contract_fields,
        "files": len(files_meta),
        "degradations": len(degradations),
        "errors": len(errors),
    }, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------- #
# skeleton
# --------------------------------------------------------------------------- #

def cmd_skeleton(args):
    work_dir = args.work
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    manifest, _ = read_json_tolerant(os.path.join(work_dir, "input_manifest.json"))
    if not isinstance(manifest, dict):
        manifest = {}
    case_id = manifest.get("case_id")
    contract_fields = manifest.get("contract_fields") or []
    contract_schema = manifest.get("contract_schema") or {}
    files = manifest.get("files") or []

    # Honest seed status: empty input -> insufficient_evidence; unparseable
    # case.json -> input_error; otherwise partial (Agent upgrades later).
    if not files:
        seed_status = "insufficient_evidence"
    elif any(f.get("path") == "case.json" and f.get("parse_status") == "bad_json"
             for f in files):
        seed_status = "input_error"
    else:
        seed_status = "partial"

    result = {}
    for field in contract_fields:
        result[field] = default_for_schema(contract_schema.get(field))

    standard = {
        "status": seed_status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": list(manifest.get("errors") or []),
        "degradations": list(manifest.get("degradations") or []),
        "artifacts": {},
        "runtime": {
            "budget_seconds": 900,
            "budget_requests": 64,
            "tokens_per_response": 16384,
            "determinism": "sorted_keys_no_wall_clock",
        },
    }
    # Contract field names win over standard fields on collision.
    for key, value in standard.items():
        result.setdefault(key, value)
    if "case_id" not in result:
        result["case_id"] = case_id

    write_json(os.path.join(output_dir, "result.json"), result)

    # Deterministic manifest of produced files (skeleton stage).
    with open(os.path.join(output_dir, "result.json"), "rb") as fh:
        digest = sha256_bytes(fh.read())
        size = os.path.getsize(os.path.join(output_dir, "result.json"))
    run_manifest = {
        "stage": "skeleton",
        "files": [{"path": "result.json", "sha256": digest, "bytes": size}],
    }
    write_json(os.path.join(output_dir, "run.json"), run_manifest)

    print(json.dumps({
        "status": seed_status, "case_id": case_id,
        "contract_fields_backfilled": contract_fields,
    }, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #

def _has_sections(text):
    if not isinstance(text, str) or not text.strip():
        return {}
    found = {}
    for name, markers in SECTION_MARKERS.items():
        found[name] = any(m in text for m in markers)
    return found


def _parse_evidence_map(path):
    obj, err = read_json_tolerant(path)
    if err or not isinstance(obj, dict):
        return None
    return obj


def cmd_validate(args):
    work_dir = args.work
    output_dir = args.output
    os.makedirs(work_dir, exist_ok=True)

    checks = []

    def add(cid, passed, level, message):
        checks.append({"id": cid, "pass": passed, "level": level, "message": message})

    result_path = os.path.join(output_dir, "result.json")
    result, err = read_json_tolerant(result_path)

    # 1. JSON parseability
    if err or not isinstance(result, dict):
        add("result_json_parse", False, "error",
            "result.json unreadable: %s" % (err or "not an object"))
        write_json(os.path.join(work_dir, "validation_report.json"), {
            "schema_version": SCHEMA_VERSION,
            "pass": False,
            "suggested_status": "input_error",
            "checks": checks,
        })
        return

    # 2. case_id
    cid = result.get("case_id")
    if cid is None or cid == "":
        add("case_id_present", False, "warn",
            "case_id missing or null; set to null and record in errors if input lacks it")
    else:
        add("case_id_present", True, "info", "case_id present")

    # 3. status
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        add("status_valid", False, "error",
            "status %r not in %s" % (status, list(ALLOWED_STATUS)))
    else:
        add("status_valid", True, "info", "status=%s" % status)

    # 4. contract fields backfilled
    manifest, _ = read_json_tolerant(os.path.join(work_dir, "input_manifest.json"))
    contract_fields = (manifest or {}).get("contract_fields") or []
    missing_contract = [f for f in contract_fields if f not in result]
    if missing_contract:
        add("contract_fields_backfilled", False, "error",
            "missing contract fields: %s" % ", ".join(sorted(missing_contract)))
    else:
        add("contract_fields_backfilled", True, "info",
            "contract fields backfilled (%d)" % len(contract_fields))

    # 5. standard fields present
    missing_std = [f for f in STANDARD_FIELDS if f not in result]
    if missing_std:
        add("standard_fields_present", False, "warn",
            "missing standard fields: %s" % ", ".join(missing_std))
    else:
        add("standard_fields_present", True, "info", "standard fields present")

    # 6. sources structure
    sources = result.get("sources")
    if not isinstance(sources, list):
        add("sources_list", False, "error", "sources must be a list")
    else:
        bad = 0
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                bad += 1
                continue
            missing = [k for k in ("key", "locator", "quote", "kind")
                       if k not in s or s[k] in (None, "")]
            if missing:
                bad += 1
        if bad:
            add("sources_structure", False, "error",
                "%d source item(s) missing key/locator/quote/kind" % bad)
        else:
            add("sources_structure", True, "info", "sources structure ok (%d items)" % len(sources))

    # 7. change_log structure
    change_log = result.get("change_log")
    if not isinstance(change_log, list):
        add("change_log_list", False, "error", "change_log must be a list")
    else:
        bad = 0
        for i, c in enumerate(change_log):
            if not isinstance(c, dict):
                bad += 1
                continue
            if not any(k in c for k in ("at", "target", "before", "after", "reason")):
                bad += 1
        if bad:
            add("change_log_structure", False, "warn",
                "%d change_log item(s) lack expected keys" % bad)
        else:
            add("change_log_structure", True, "info", "change_log structure ok")

    # 8. manuscript four sections
    manuscript_path = os.path.join(output_dir, "manuscript.md")
    if os.path.isfile(manuscript_path):
        text, _ = read_text(manuscript_path)
        if not text or not text.strip():
            add("manuscript_nonempty", False, "error", "manuscript.md is empty")
        else:
            add("manuscript_nonempty", True, "info", "manuscript.md non-empty")
            found = _has_sections(text)
            missing_sections = [n for n, ok in found.items() if not ok]
            if missing_sections:
                add("four_sections", False, "warn",
                    "sections possibly missing markers for: %s" % ", ".join(missing_sections))
            else:
                add("four_sections", True, "info",
                    "four section markers found (background/related_work/gap/contribution)")
    else:
        add("manuscript_nonempty", False, "warn", "manuscript.md not produced yet")

    # 9. evidence map coverage vs [S#] markers
    evidence_path = os.path.join(output_dir, "evidence_map.json")
    if os.path.isfile(manuscript_path) and os.path.isfile(evidence_path):
        text, _ = read_text(manuscript_path)
        markers = sorted(set(re.findall(r"\[S(\d+)\]", text or "")), key=lambda x: int(x))
        ev = _parse_evidence_map(evidence_path)
        if ev is None:
            add("evidence_map_parse", False, "error", "evidence_map.json unreadable")
        else:
            missing_ev = [m for m in markers if ("S%s" % m) not in ev and m not in ev]
            if missing_ev:
                add("evidence_map_coverage", False, "error",
                    "manuscript markers missing evidence entries: S%s" % ",".join(missing_ev))
            else:
                add("evidence_map_coverage", True, "info",
                    "evidence map covers %d manuscript marker(s)" % len(markers))
    elif os.path.isfile(manuscript_path):
        add("evidence_map_coverage", False, "warn", "evidence_map.json not produced")

    # 10. determinism hygiene: no wall-clock fields
    runtime = result.get("runtime")
    if isinstance(runtime, dict):
        volatile = [k for k in runtime if k in ("wall_seconds", "elapsed_seconds",
                                                 "finished_at", "started_at", "timestamp")]
        if volatile:
            add("runtime_deterministic", False, "warn",
                "volatile runtime fields detected (breaks determinism): %s" % ", ".join(volatile))
        else:
            add("runtime_deterministic", True, "info", "runtime fields deterministic")
    else:
        add("runtime_deterministic", False, "warn", "runtime missing or not an object")

    errors_count = sum(1 for c in checks if c["level"] == "error")
    warned = any(not c["pass"] for c in checks)
    # Suggested status: never upgrade over an explicit insufficient/input error.
    suggested = status if status in ("insufficient_evidence", "input_error") else \
        ("ok" if not errors_count and not warned and os.path.isfile(manuscript_path)
         else ("partial" if os.path.isfile(manuscript_path) else status))

    report = {
        "schema_version": SCHEMA_VERSION,
        "pass": errors_count == 0,
        "suggested_status": suggested,
        "error_count": errors_count,
        "checks": checks,
    }
    write_json(os.path.join(work_dir, "validation_report.json"), report)
    print(json.dumps({
        "pass": report["pass"], "suggested_status": suggested,
        "error_count": errors_count, "checks": len(checks),
    }, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------- #
# finalize (deterministic re-serialization)
# --------------------------------------------------------------------------- #

def cmd_finalize(args):
    output_dir = args.output
    work_dir = args.work
    os.makedirs(work_dir, exist_ok=True)

    touched = []
    for name in ("result.json", "evidence_map.json", "change_log.json",
                 "preserved_facts.json"):
        path = os.path.join(output_dir, name)
        if not os.path.isfile(path):
            continue
        obj, err = read_json_tolerant(path)
        if err:
            print("skip %s: %s" % (name, err))
            continue
        write_json(path, obj)  # re-serialize sorted, deterministic
        touched.append(name)
    # Re-validate after normalization.
    cmd_validate(args)
    print(json.dumps({"finalized": sorted(touched)}, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------- #
# selftest / run
# --------------------------------------------------------------------------- #

def cmd_selftest(args):
    here = os.path.dirname(os.path.abspath(__file__))
    selftest_path = os.path.join(here, "selftest.py")
    if not os.path.isfile(selftest_path):
        print("selftest.py not found next to main.py", file=sys.stderr)
        return 1
    code = subprocess.call([sys.executable, "-B", selftest_path])
    return code


def cmd_run(args):
    cmd_scan(args)
    cmd_skeleton(args)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py", description="sci-intro-writing Skill runner (deterministic)")
    sub = p.add_subparsers(dest="command", required=True)

    # Path args live on each subcommand, so `main.py scan --input X` works
    # and bare `main.py scan` falls back to the runtime defaults below.
    path_args = argparse.ArgumentParser(add_help=False)
    path_args.add_argument("--input", default="/input")
    path_args.add_argument("--output", default="/app/output")
    path_args.add_argument("--work", default="/app/work")

    sub.add_parser("scan", parents=[path_args], help="discover inputs and extract output_contract")
    sub.add_parser("skeleton", parents=[path_args], help="emit valid partial result.json")
    sub.add_parser("validate", parents=[path_args], help="validate result.json against contract and evidence")
    sub.add_parser("finalize", parents=[path_args], help="deterministically re-serialize and validate outputs")
    sub.add_parser("selftest", parents=[path_args], help="run scripts/selftest.py")
    sub.add_parser("run", parents=[path_args], help="scan + skeleton safety chain")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "scan":
        return cmd_scan(args) or 0
    if cmd == "skeleton":
        return cmd_skeleton(args) or 0
    if cmd == "validate":
        return cmd_validate(args) or 0
    if cmd == "finalize":
        return cmd_finalize(args) or 0
    if cmd == "selftest":
        return cmd_selftest(args)
    if cmd == "run":
        return cmd_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
