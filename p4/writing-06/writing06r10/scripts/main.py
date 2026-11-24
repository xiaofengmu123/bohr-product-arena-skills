#!/usr/bin/env python3
"""Orchestrator for the paper-structure-optimizer Skill.

Pipeline (all deterministic, pure stdlib):
  1. read /input/case.json (+ /input/task.md) -> case_id and output_contract
  2. scaffold an honest partial result.json immediately (status=in_progress)
  3. build the document model (scripts/parse.py) -> /app/work/document_model.json
  4. run self-test (scripts/selftest.py) -> /app/output/verify_report.json

This script prepares structure and evidence; it never fabricates a diagnosis.
The reasoning model fills diagnosis / edit_plan / argument_map / sources
incrementally by reviewing the document model against the source text.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import parse
import selftest

DEFAULT_CONTRACT_KEYS = [
    "diagnosis",
    "edit_plan",
    "argument_map",
    "coverage",
    "version_boundary",
    "artifacts",
]

SIX_DIMS = [
    "research_question",
    "chapter_structure",
    "argument_chain",
    "paragraph_relations",
    "redundancy",
    "conclusion_boundary",
]


def utc_now():
    try:
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return datetime.datetime.utcnow().isoformat(timespec="seconds")


def read_json(path):
    """Read JSON with BOM/bad-encoding tolerance. Returns dict or None."""
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError:
            return None
    return None


def read_text(path):
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "caseId", "challenge_id"):
        if case.get(key):
            return str(case[key])
    return None


def extract_contract_keys(case):
    keys = []
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            keys = [k for k in oc.keys()]
        elif isinstance(oc, list) and oc:
            keys = [str(k) for k in oc]
    return keys or list(DEFAULT_CONTRACT_KEYS)


def default_coverage():
    return {d: {"status": "pending", "findings": 0, "is_primary": None} for d in SIX_DIMS}


def build_skeleton(case, task_md, contract_keys):
    case_id = extract_case_id(case) or "unknown"
    skeleton = {
        "case_id": case_id,
        "status": "in_progress",
        "_output_contract_keys": contract_keys,
    }
    # fill each contract key with its neutral empty value
    neutral = {
        "diagnosis": None,
        "edit_plan": [],
        "argument_map": [],
        "coverage": default_coverage(),
        "version_boundary": {"diff": [], "human_confirm": [], "gaps": []},
        "artifacts": {},
    }
    for key in contract_keys:
        skeleton[key] = neutral.get(key, None)
    skeleton.update({
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utc_now(),
            "action": "scaffold",
            "status": "in_progress",
        }],
        "runtime": {
            "started_at": utc_now(),
            "budget_seconds": 900,
            "budget_requests": 64,
        },
    })
    return skeleton


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Scaffold + parse + self-test for paper structure optimization.")
    ap.add_argument("--case", default=os.environ.get("CASE_JSON", "/input/case.json"))
    ap.add_argument("--task", default=os.environ.get("TASK_MD", "/input/task.md"))
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--scaffold-only", action="store_true", help="only write the partial skeleton")
    args = ap.parse_args()

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    result_path = os.path.join(args.out, "result.json")

    case = read_json(args.case)
    task_md = read_text(args.task)
    contract_keys = extract_contract_keys(case)

    # 1. Scaffold FIRST: an honest partial result is on disk within the first
    #    minute even if every later step fails.
    skeleton = build_skeleton(case, task_md, contract_keys)
    if case is None:
        skeleton["uncertainty"].append({
            "item": "case.json missing or unreadable",
            "reason": "cannot confirm case_id / output_contract",
        })
    write_json(result_path, skeleton)

    if args.scaffold_only:
        print(json.dumps({"ok": True, "scaffold": result_path, "status": skeleton["status"]},
                         ensure_ascii=False))
        return 0

    # 2. Document model (structure + deterministic signals).
    parse_info = {"ok": False}
    try:
        model_path, model = parse.build_document_model(args.input, args.work)
        parse_info = {
            "ok": True,
            "model": model_path,
            "documents": len(model["documents"]),
            "signals": len(model["signals"]),
            "duplicate_candidates": len(model["duplicate_candidates"]),
            "unreadable": len(model["unreadable"]),
        }
    except Exception as exc:  # noqa: BLE001 - keep pipeline alive, report honestly
        parse_info = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        skeleton["uncertainty"].append({
            "item": "document model build failed",
            "reason": parse_info["error"],
        })
        skeleton["status"] = "failed"
        write_json(result_path, skeleton)

    # 3. Self-test on the scaffold (model will enrich result.json afterwards).
    verify_info = {"ok": False}
    try:
        report = selftest.self_check(result_path, args.out)
        report_path = os.path.join(args.out, "verify_report.json")
        os.makedirs(args.out, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        verify_info = {"ok": True, "overall": report["overall"], "report": report_path}
    except Exception as exc:  # noqa: BLE001
        verify_info = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}

    print(json.dumps({
        "ok": True,
        "result": result_path,
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": contract_keys,
        "parse": parse_info,
        "verify": verify_info,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
