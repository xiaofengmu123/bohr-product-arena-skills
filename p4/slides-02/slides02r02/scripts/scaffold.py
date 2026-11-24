#!/usr/bin/env python3
"""scaffold.py — persist an honest partial result skeleton as early as possible.

Design thesis (variant: time budget + early落盘): the single most important
runtime guarantee of this Skill is that, within the first three minutes, the
Agent has already written /app/output/result.json containing case_id, every
output_contract field (null/empty where not yet known) and status="partial".
Everything after that is incremental improvement, never a fresh rewrite.

This script is deterministic, stdlib-only, and independent of the current
working directory.  Input/output locations come from --input/--work/--output
or the environment variables INPUT_DIR/WORK_DIR/OUTPUT_DIR
(defaults: /input, /app/work, /app/output).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 900s / 64 model requests, split into stage deadlines.  The first stage is
# the hard early落盘 deadline: a legal partial result must exist by then.
STAGE_PLAN = [
    # name, deadline_s, request_budget, deliverable
    ("scaffold",  60,  2,  "result.json partial skeleton + work/plan.json"),
    ("inventory", 180, 4,  "material inventory, extracted text, figure list"),
    ("plan",      300, 8,  "page_plan with per-page narrative role"),
    ("draft",     540, 20, "editable deck + speaker notes + evidence_map"),
    ("render",    720, 18, "PDF/PNG previews + overflow/font fixes"),
    ("finalize",  900, 12, "self_check + final honest status"),
]

TOTAL_BUDGET_S = 900
TOTAL_BUDGET_REQUESTS = 64
EARLY_DROP_DEADLINE_S = 180

# Fallback contract when /input/case.json carries no output_contract key.
DEFAULT_CONTRACT_FIELDS = [
    "presentation_file", "preview_files", "speaker_notes",
    "page_plan", "evidence_map", "version_records",
]

# Sensible empty values for the common academic-slide fields.
EMPTY_HINTS = {
    "presentation_file": None,
    "preview_files": [],
    "speaker_notes": None,
    "page_plan": {},
    "evidence_map": [],
    "version_records": [],
    "sources": [],
    "pending_human": [],
    "change_log": [],
    "errors": [],
    "degradations": [],
}

RESERVED_FIELDS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "runtime", "errors", "degradations", "self_check",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def load_json_text(path: Path):
    """Return (object, errors).  Never raises: bad encoding/JSON degrades."""
    errors = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, [f"read failed: {exc}"]
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None, ["undecodable bytes"]
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, [f"bad json: {exc}"]


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def contract_fields_from(case: dict):
    """Extract output_contract field names, preserving order and names."""
    oc = None
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if oc is None:
            oc = case.get("outputContract")
    if oc is None:
        return list(DEFAULT_CONTRACT_FIELDS), "default"
    if isinstance(oc, list):
        fields = [str(f) for f in oc if isinstance(f, str)]
        return fields or list(DEFAULT_CONTRACT_FIELDS), "list"
    if isinstance(oc, dict):
        if isinstance(oc.get("fields"), list):
            fields = [str(f) for f in oc["fields"] if isinstance(f, str)]
            return fields or list(DEFAULT_CONTRACT_FIELDS), "dict.fields"
        fields = [str(k) for k in oc.keys() if str(k) not in ("required", "description")]
        return fields or list(DEFAULT_CONTRACT_FIELDS), "dict"
    return list(DEFAULT_CONTRACT_FIELDS), "fallback"


def build_result(case_id: str, fields) -> dict:
    result = {}
    for f in fields:
        result[f] = EMPTY_HINTS.get(f)
    result["case_id"] = case_id
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = {"unresolved": [], "missing_artifacts": [], "notes": ""}
    result["pending_human"] = []
    result["change_log"] = [{
        "t": now_iso(),
        "step": "scaffold",
        "action": "write_partial_skeleton",
        "status": "ok",
        "reason": "early落盘: honest partial result persisted before any analysis",
    }]
    result["runtime"] = {
        "elapsed_s": 0,
        "requests_used": 0,
        "budget_s": TOTAL_BUDGET_S,
        "budget_requests": TOTAL_BUDGET_REQUESTS,
        "early_drop_deadline_s": EARLY_DROP_DEADLINE_S,
        "scaffold_t": now_iso(),
    }
    result["errors"] = []
    result["degradations"] = []
    return result


def list_input_files(input_dir: Path, cap: int = 200):
    if not input_dir.exists():
        return {"count": 0, "files": [], "truncated": False}
    files = []
    try:
        for p in sorted(input_dir.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(input_dir)))
    except OSError:
        pass
    truncated = len(files) > cap
    return {"count": len(files), "files": files[:cap], "truncated": truncated}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default=env("INPUT_DIR", "/input"))
    ap.add_argument("--work", default=env("WORK_DIR", "/app/work"))
    ap.add_argument("--output", default=env("OUTPUT_DIR", "/app/output"))
    args = ap.parse_args(argv)

    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    for d in (work_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)

    errors = []
    degradations = []
    case = {}
    case_path = input_dir / "case.json"
    task_path = input_dir / "task.md"

    if case_path.exists():
        case, errs = load_json_text(case_path)
        if case is None or not isinstance(case, dict):
            case = {}
            errors.extend(errs)
            degradations.append("case.json unreadable or not an object; used empty contract")
    else:
        degradations.append("case.json missing")

    case_id = None
    if isinstance(case, dict):
        cid = case.get("case_id") or case.get("caseId") or case.get("id")
        if isinstance(cid, str):
            case_id = cid
        elif isinstance(cid, (int, float)) and not isinstance(cid, bool):
            case_id = str(cid)
    if not case_id:
        case_id = "unknown"
        degradations.append("case_id missing; set 'unknown'")

    fields, how = contract_fields_from(case)
    result = build_result(case_id, fields)
    result["errors"].extend(errors)
    result["degradations"].extend(degradations)
    result["uncertainty"]["unresolved"].append(
        f"output_contract fields derived via '{how}' (not fabricated)")

    if not case_path.exists() or not isinstance(case, dict) or not case:
        result["status"] = "insufficient_evidence"
        result["uncertainty"]["missing_artifacts"].append("/input/case.json")
    if not task_path.exists():
        result["uncertainty"]["missing_artifacts"].append("/input/task.md")

    write_json_atomic(output_dir / "result.json", result)

    plan = {
        "budget_s": TOTAL_BUDGET_S,
        "budget_requests": TOTAL_BUDGET_REQUESTS,
        "early_drop_deadline_s": EARLY_DROP_DEADLINE_S,
        "rule": "partial result.json MUST exist by early_drop_deadline_s; afterwards only incremental edits",
        "stages": [
            {"name": n, "deadline_s": d, "request_budget": r, "deliverable": v}
            for (n, d, r, v) in STAGE_PLAN
        ],
    }
    write_json_atomic(work_dir / "plan.json", plan)

    inv = list_input_files(input_dir)
    summary = {
        "case_id": case_id,
        "contract_fields": fields,
        "contract_source": how,
        "input_files": inv,
        "errors": errors,
        "degradations": degradations,
        "t": now_iso(),
    }
    write_json_atomic(work_dir / "case_summary.json", summary)

    print(json.dumps({
        "case_id": case_id,
        "status": result["status"],
        "contract_fields": fields,
        "contract_source": how,
        "result": str(output_dir / "result.json"),
        "plan": str(work_dir / "plan.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
