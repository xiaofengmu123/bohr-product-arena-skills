#!/usr/bin/env python3
"""Scaffold result.json and material_index.json deterministically.

Usage:
    python3 scripts/init_result.py --input-dir /input --output-dir /app/output --work-dir /app/work

Guarantees:
    - Never crashes on missing input, bad JSON, bad encoding, or empty directory.
    - Writes a valid result.json skeleton (status=in_progress / insufficient_evidence / input_error).
    - Same input twice -> byte-identical result.json and material_index.json.
"""
import argparse
import hashlib
import json
import os
import sys

DEFAULT_CONTRACT = ["deliverable", "fact_source_map", "version_limits"]
MAX_FILE_BYTES = 2 * 1024 * 1024
SKIP_BASENAMES = {"case.json", "task.md", "result.json"}
TEXT_KINDS = {"md", "txt", "csv", "json", "tex", "bib", "rst", "yml", "yaml", "tsv", "log"}
PREVIEW_CHARS = 3000


def read_text_loose(path):
    """Read a text file tolerating BOM and bad encodings."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def load_json_loose(path):
    text = read_text_loose(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as exc:
        return None, "bad_json: %s" % exc


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "unreadable"


def discover_materials(input_dir):
    items = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in {"output", "work"})
        for name in sorted(files):
            if name in SKIP_BASENAMES:
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            items.append({
                "rel_path": rel,
                "abs_path": full,
                "kind": os.path.splitext(name)[1].lstrip(".").lower() or "unknown",
                "size": size,
                "oversize": size > MAX_FILE_BYTES,
                "sha256": sha256_file(full),
            })
    items.sort(key=lambda x: x["rel_path"])
    return items


def build_material_index(input_dir):
    index = {
        "case": {"input_dir": input_dir},
        "materials": [],
        "conflicts": [],
        "missing": [],
    }
    for i, it in enumerate(discover_materials(input_dir), 1):
        entry = {
            "id": "m%d" % i,
            "path": it["rel_path"],
            "kind": it["kind"],
            "size": it["size"],
            "sha256": it["sha256"],
            "oversize": it["oversize"],
            "parsed": None,
            "line_count": None,
            "text_preview": None,
            "locators": [],
            "key_claims": [],
            "numbers": [],
            "citations": [],
            "flags": [],
        }
        if it["kind"] in TEXT_KINDS and not it["oversize"]:
            text = read_text_loose(it["abs_path"])
            if text is not None:
                entry["parsed"] = "text"
                entry["line_count"] = text.count("\n") + 1
                entry["text_preview"] = text[:PREVIEW_CHARS]
        elif it["oversize"]:
            entry["flags"].append("oversize: skipped preview, recorded only")
        index["materials"].append(entry)
    return index


def build_skeleton(case_data, contract_fields, degradations):
    case_id = None
    if isinstance(case_data, dict):
        for key in ("id", "case_id", "caseId"):
            val = case_data.get(key)
            if val:
                case_id = str(val)
                break
    if case_id is None:
        case_id = "case.json"

    contract = {}
    for k in contract_fields:
        contract[k] = None

    skeleton = {
        "case_id": case_id,
        "status": "in_progress",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"seq": 1, "stage": "init", "note": "result skeleton written before analysis"}
        ],
        "degradations": list(degradations),
        "runtime": {"budget_seconds": 900, "budget_requests": 64, "budget_tokens": 16384},
    }
    # contract fields are merged after the stable core so they remain visible.
    skeleton.update(contract)
    if degradations:
        skeleton["uncertainty"] = [
            {"item": d, "kind": "degradation", "status": "noted"} for d in degradations
        ]
    return skeleton


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", "--input", default="/input")
    ap.add_argument("--output-dir", "--output", "--out", default="/app/output")
    ap.add_argument("--work-dir", "--work", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(a.output_dir, exist_ok=True)
    os.makedirs(a.work_dir, exist_ok=True)

    degradations = []
    case_data, case_status = load_json_loose(os.path.join(a.input_dir, "case.json"))
    if case_data is None:
        case_data = {}
        degradations.append("case.json %s" % case_status)
    elif not isinstance(case_data, dict):
        case_data = {}
        degradations.append("case.json is not a JSON object")

    task_text = read_text_loose(os.path.join(a.input_dir, "task.md"))
    if task_text is None:
        degradations.append("task.md missing or unreadable")

    material_index = build_material_index(a.input_dir)
    if not material_index["materials"]:
        degradations.append("no material files found under input dir")

    contract_fields = []
    if isinstance(case_data, dict) and isinstance(case_data.get("output_contract"), dict):
        contract_fields = sorted(case_data["output_contract"].keys())
    if not contract_fields:
        contract_fields = list(DEFAULT_CONTRACT)

    skeleton = build_skeleton(case_data, contract_fields, degradations)

    write_json(os.path.join(a.work_dir, "material_index.json"), material_index)
    write_json(os.path.join(a.work_dir, "contract_meta.json"), {
        "case_id": skeleton["case_id"],
        "contract_fields": contract_fields,
        "has_task_md": task_text is not None,
        "material_count": len(material_index["materials"]),
    })
    write_json(os.path.join(a.output_dir, "result.json"), skeleton)

    print(json.dumps({
        "ok": True,
        "case_id": skeleton["case_id"],
        "contract_fields": contract_fields,
        "material_count": len(material_index["materials"]),
        "degradations": degradations,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
