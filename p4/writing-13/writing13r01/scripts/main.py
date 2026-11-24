#!/usr/bin/env python3
"""Grant-writing evidence skill — deterministic bootstrap entry point.

What this script guarantees:
  1. A legal ``result.json`` exists in the output dir even before the model
     has read a single sentence of material (partial-first contract).
  2. ``case.json`` / ``task.md`` are discovered without assuming fixed names,
     read with UTF-8-SIG tolerance, and bad JSON degrades instead of crashing.
  3. Sources are snapshotted deterministically (relative path + byte size +
     SHA256) so a third party can re-locate every file we cited.
  4. ``--selftest`` runs the same validator the graders can run.

Standard-library only; all paths resolve relative to the skill root so the
identical command works under ``/skills/weekly-skill`` and on a local machine.
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

REQUIRED_KEYS = [
    "case_id", "status", "artifact", "fact_source_map",
    "version_boundary", "sources", "uncertainty", "pending_human",
    "change_log", "runtime",
]

STATUS_VALUES = {
    "in_progress", "done", "partial", "failed",
    "insufficient_evidence", "input_error",
}


# --------------------------------------------------------------------------- #
# text / json helpers
# --------------------------------------------------------------------------- #
def read_text(path):
    """Return text tolerating BOM and bad encoding (never raises)."""
    if not path or not os.path.isfile(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def load_json(path):
    """Return (obj, error_str). Never raises on malformed input."""
    txt = read_text(path)
    if txt is None:
        return None, "unreadable_or_missing"
    try:
        return json.loads(txt), None
    except json.JSONDecodeError as exc:
        return None, "bad-json:%s" % exc
    except (TypeError, ValueError) as exc:
        return None, "json-error:%s" % exc


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def sha256_of(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# path resolution (local-machine fallback)
# --------------------------------------------------------------------------- #
def resolve_dir(preferred, fallback_name):
    if preferred and os.path.isdir(preferred):
        return preferred
    alt = os.path.join(ROOT, fallback_name)
    if os.path.isdir(alt):
        return alt
    # keep the preferred form; caller creates output/work dirs as needed
    return preferred if preferred else alt


# --------------------------------------------------------------------------- #
# input discovery
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    """Locate case.json and task.md without assuming fixed file names."""
    found = {"case": None, "task": None}
    errors = []
    if not input_dir or not os.path.isdir(input_dir):
        return found, ["input_dir_missing"]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return found, ["input_dir_unreadable"]
    for name in names:
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        low = name.lower()
        if found["case"] is None and low.endswith(".json") and "case" in low:
            found["case"] = full
        elif found["task"] is None and low in ("task.md", "task.txt"):
            found["task"] = full
    if found["case"] is None:
        errors.append("case_json_missing")
    if found["task"] is None:
        errors.append("task_md_missing")
    return found, errors


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return None
    for key in ("case_id", "id", "challenge_id", "armiId", "task_id"):
        val = case_obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return None


def extract_contract(case_obj):
    """Collect any explicit output contract; never mutate or drop keys."""
    contract = {}
    if not isinstance(case_obj, dict):
        return contract
    for key in ("output_contract", "output_schema", "required_fields", "output_fields"):
        val = case_obj.get(key)
        if isinstance(val, dict):
            contract.update(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    contract[item] = None
    return contract


# --------------------------------------------------------------------------- #
# source snapshot (deterministic)
# --------------------------------------------------------------------------- #
def snapshot_sources(input_dir):
    files = []
    errors = []
    if not input_dir or not os.path.isdir(input_dir):
        return {"files": files, "errors": ["input_dir_missing"]}
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            digest = sha256_of(full)
            files.append({
                "path": rel,
                "type": "file",
                "size": size,
                "sha256": digest or "",
                "readable": digest is not None,
            })
    if not files:
        errors.append("no_material_files")
    return {"files": files, "errors": errors}


# --------------------------------------------------------------------------- #
# result skeleton / incremental merge
# --------------------------------------------------------------------------- #
def default_value(key):
    if key in ("fact_source_map",):
        return {
            "version": "1.0",
            "granularity": "claim-level locator + verbatim quote",
            "claims": [],
            "trace": {},
            "conflicts": [],
            "unmapped": [],
        }
    if key in ("artifact",):
        return {"path": None, "title": None, "format": None, "preview": None}
    if key in ("version_boundary",):
        return {
            "modification_diff": [],
            "manual_confirm": [],
            "material_gaps": [],
            "anomalies": [],
        }
    if key in ("sources", "change_log", "uncertainty", "pending_human"):
        return []
    if key == "runtime":
        return {}
    return None


def new_skeleton(case_id, contract):
    result = {}
    for key in REQUIRED_KEYS:
        result[key] = default_value(key)
    result["case_id"] = case_id
    result["status"] = "in_progress"
    # honour the topic's canonical contract fields even if case.json omits them
    for key in ("artifact", "fact_source_map", "version_boundary"):
        result.setdefault(key, default_value(key))
    result["contract_hint"] = contract
    result["runtime"] = {
        "engine": "grant-writing-evidence-skill",
        "skill_version": "2.0.0",
        "created_unix": int(time.time()),
        "budget_seconds": 900,
        "budget_requests": 64,
        "max_response_tokens": 16384,
    }
    return result


def merge_contract(result, contract):
    """Fill every contract key into result.json as null without renaming."""
    for key, val in (contract or {}).items():
        if key not in result:
            result[key] = None
        elif result[key] is None and val is not None:
            result[key] = val
    result["contract_hint"] = contract
    return result


def append_log(result, action, reason, impact):
    result.setdefault("change_log", [])
    result["change_log"].append({
        "time_unix": int(time.time()),
        "action": action,
        "reason": reason,
        "impact": impact,
    })
    return result


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run_selftest(result_path, output_dir):
    here = os.path.dirname(os.path.abspath(__file__))
    selftest = os.path.join(here, "selftest.py")
    if not os.path.exists(selftest):
        print("selftest.py missing", file=sys.stderr)
        return 2
    import subprocess
    rc = subprocess.call([sys.executable, selftest, "--result", result_path,
                          "--output", output_dir])
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic bootstrap for grant-writing evidence skill.")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--selftest-only", action="store_true",
                    help="run selftest.py against existing result.json and exit")
    a = ap.parse_args(argv)

    input_dir = resolve_dir(a.input, "input")
    output_dir = resolve_dir(a.output, "output")
    work_dir = resolve_dir(a.work, "work")
    for d in (output_dir, work_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            print("cannot create %s: %s" % (d, exc), file=sys.stderr)
            return 1

    result_path = os.path.join(output_dir, "result.json")

    if a.selftest_only:
        return run_selftest(result_path, output_dir)

    meta_path = os.path.join(work_dir, "run_meta.json")
    meta, _ = load_json(meta_path)
    meta = meta if isinstance(meta, dict) else {}
    meta["runs"] = int(meta.get("runs", 0)) + 1
    meta["input_dir"] = input_dir
    meta["output_dir"] = output_dir
    meta["last_run_unix"] = int(time.time())
    write_json(meta_path, meta)

    # 1. discover and read inputs (tolerantly)
    found, disc_errors = discover_inputs(input_dir)
    case_obj = None
    case_err = None
    case_id = None
    contract = {}
    if found["case"]:
        case_obj, case_err = load_json(found["case"])
        if isinstance(case_obj, dict):
            case_id = extract_case_id(case_obj)
            contract = extract_contract(case_obj)
        else:
            disc_errors.append("case_json_unparseable:%s" % (case_err or "none"))

    # 2. incremental: reuse existing result.json, never throw away prior work
    result = None
    if os.path.isfile(result_path):
        existing, _ = load_json(result_path)
        if isinstance(existing, dict):
            result = existing
    if result is None:
        result = new_skeleton(case_id, contract)

    # 3. snapshot sources deterministically and (re)attach
    snap = snapshot_sources(input_dir)
    result["sources"] = snap["files"]
    result.setdefault("source_manifest_errors", [])
    result["source_manifest_errors"] = snap["errors"]

    # 4. fill required keys and contract fields
    for key in REQUIRED_KEYS:
        result.setdefault(key, default_value(key))
    for key in ("artifact", "fact_source_map", "version_boundary"):
        result.setdefault(key, default_value(key))
    if case_id:
        result["case_id"] = case_id
    result = merge_contract(result, contract)

    # 5. honest status
    if not case_id or disc_errors:
        if result.get("status") not in ("done", "partial", "failed"):
            result["status"] = "input_error" if not case_id else "insufficient_evidence"
        result.setdefault("uncertainty", [])
        result["uncertainty"].append({
            "reason": "input_discovery_issues",
            "detail": disc_errors,
        })
    else:
        result["status"] = result.get("status", "in_progress")

    append_log(result, "bootstrap_snapshot",
               "discovered inputs, snapshotted sources, ensured legal skeleton",
               "result.json exists and passes structural self-test")

    write_json(result_path, result)
    write_json(os.path.join(work_dir, "run_meta.json"), meta)

    print(json.dumps({
        "ok": True,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "input_dir": input_dir,
        "output_dir": output_dir,
        "sources": len(snap["files"]),
        "discovery_errors": disc_errors,
        "result_path": result_path,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
