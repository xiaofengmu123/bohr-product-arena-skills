#!/usr/bin/env python3
"""Generate an early partial result.json skeleton.

Reads case.json and task.md, discovers input materials, and writes a minimal
but valid result.json as soon as possible so that any later failure never
leaves the output directory empty. Only the Python standard library is used.
"""
import datetime as _dt
import json
import os
import sys

DEFAULTS = {
    "case_json": "/input/case.json",
    "task_md": "/input/task.md",
    "input_dir": "/input",
    "output_dir": "/app/output",
    "result_json": "/app/output/result.json",
}

DEFAULT_CONTRACT_FIELDS = ["diagnosis", "source_map", "version_boundary", "artifacts"]


def _env(name, default):
    return os.environ.get(name) or default


def _read_json(path):
    """Read a JSON file tolerantly (BOM, bad encoding, bad JSON)."""
    if not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        try:
            return json.loads(raw), "ok"
        except json.JSONDecodeError as e:
            return None, "bad_json: %s" % e
    except (OSError, UnicodeDecodeError):
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                raw = f.read()
            return json.loads(raw), "ok"
        except Exception as e:  # noqa: BLE001
            return None, "unreadable: %s" % e


def _read_text(path, limit=2_000_000):
    if not os.path.exists(path):
        return None, "missing"
    try:
        if os.path.getsize(path) > limit:
            return None, "too_large"
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(), "ok"
    except OSError as e:
        return None, "unreadable: %s" % e


def _discover(input_dir):
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, input_dir)
            ext = os.path.splitext(fn)[1].lower()
            kind = {
                ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
                ".gif": "image", ".webp": "image", ".svg": "image", ".tif": "image",
                ".tiff": "image", ".csv": "table", ".tsv": "table", ".xlsx": "office",
                ".docx": "office", ".pptx": "office", ".md": "text", ".txt": "text",
                ".tex": "text", ".bib": "text", ".json": "data", ".yaml": "data",
                ".yml": "data",
            }.get(ext, "other")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            items.append({"path": rel, "ext": ext, "kind": kind, "size": size})
    return items


def _now():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def main():
    case_json = _env("CASE_JSON", DEFAULTS["case_json"])
    task_md = _env("TASK_MD", DEFAULTS["task_md"])
    input_dir = _env("INPUT_DIR", DEFAULTS["input_dir"])
    output_dir = _env("OUTPUT_DIR", DEFAULTS["output_dir"])
    result_json = _env("RESULT_JSON", DEFAULTS["result_json"])

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        print(json.dumps({"ok": False, "error": "cannot create output dir: %s" % e}, ensure_ascii=False))
        return 1

    case, case_err = _read_json(case_json)
    task_text, task_err = _read_text(task_md)
    materials = _discover(input_dir)

    case_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("armiId")
    if not case_id:
        case_id = "unknown"

    contract_fields = DEFAULT_CONTRACT_FIELDS
    contract_source = "default"
    if isinstance(case, dict):
        for key in ("output_contract", "outputContract", "output"):
            oc = case.get(key)
            if isinstance(oc, dict) and oc:
                contract_fields = list(oc.keys())
                contract_source = key
                break

    skeleton = {
        "case_id": case_id,
        "status": "in_progress",
        "runtime": {
            "started_at": _now(),
            "case_json_status": case_err,
            "task_md_status": task_err,
            "material_count": len(materials),
            "contract_source": contract_source,
        },
        "change_log": [
            {"time": _now(), "action": "skeleton written by scripts/skeleton.py", "status": "in_progress"}
        ],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
    }
    for field in contract_fields:
        if field not in skeleton:
            skeleton[field] = None
    # default contract fields get structured empties
    if "diagnosis" in skeleton:
        skeleton["diagnosis"] = {
            "summary": {"manuscript_identity": None, "overall_assessment": None,
                        "issue_counts": {"critical": 0, "major": 0, "minor": 0}},
            "issues": [],
            "claim_evidence_map": [],
            "dimension_coverage": {},
            "edit_plan": [],
        }
    if "source_map" in skeleton:
        skeleton["source_map"] = []
    if "version_boundary" in skeleton:
        skeleton["version_boundary"] = {"diffs": [], "human_confirm": [], "material_gaps": []}
    if "artifacts" in skeleton:
        skeleton["artifacts"] = {}

    try:
        with open(result_json, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "contract_fields": contract_fields,
        "contract_source": contract_source,
        "materials": materials,
        "result_json": result_json,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
