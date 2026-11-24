#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init_result.py -- Phase-0 bootstrap: write a complete partial-result skeleton early.

Purpose (budget-guard variant):
  * The first disk artifact is a field-complete /app/output/result.json skeleton
    (all contract keys present with null/[] placeholders), so any later crash
    still leaves a legal deliverable.
  * Re-runs are idempotent: non-null / non-empty values already written are
    never overwritten; only missing fields are back-filled.
  * Records wall-clock timestamps and creates /app/work/phase_ledger.json so
    phase_gate.py can verify the 180s skeleton deadline and the 900s/64-request
    budget.

Standard library only. Exit codes:
  0 ok; 2 degraded (missing/unparsable inputs, skeleton still written);
  3 fatal (output directory could not be created/written).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

BUDGET_S = 900
REQUEST_BUDGET = 64
SKELETON_DEADLINE_S = 180

DEFAULT_PHASES = [
    {"id": "p0_bootstrap", "name": "引导+早落盘", "deadline_s": 180, "hard": True, "req_budget": 4},
    {"id": "p1_extract", "name": "提取", "deadline_s": 400, "hard": False, "req_budget": 12},
    {"id": "p2_pageplan", "name": "页计划", "deadline_s": 520, "hard": False, "req_budget": 8},
    {"id": "p3_build", "name": "制作", "deadline_s": 760, "hard": False, "req_budget": 16},
    {"id": "p4_export", "name": "导出校验", "deadline_s": 850, "hard": False, "req_budget": 8},
    {"id": "p5_close", "name": "收口", "deadline_s": 900, "hard": True, "req_budget": 6},
]
RESERVE_REQ = REQUEST_BUDGET - sum(p["req_budget"] for p in DEFAULT_PHASES)

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFF = "insufficient_evidence"
STATUS_INPUT_ERR = "input_error"
STATUS_FAILED = "failed"
STATUS_IN_PROGRESS = "in_progress"


def now_iso(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "undecodable"


def load_json(path):
    p = Path(path)
    if not p.exists():
        return None, "missing"
    try:
        text, enc_err = read_text(p)
        return json.loads(text), enc_err
    except Exception as exc:  # noqa: BLE001
        return None, "json_error:%s" % type(exc).__name__


def write_json_atomic(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))


def deep_equal(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _deepcopy(v):
    if isinstance(v, (list, dict)):
        return json.loads(json.dumps(v, ensure_ascii=False))
    return v


def fill_missing(existing, template):
    """Back-fill only missing/null/empty slots; never overwrite filled values."""
    if isinstance(template, dict):
        out = existing if isinstance(existing, dict) else {}
        for k, v in template.items():
            cur = out.get(k)
            if k not in out or cur is None or (isinstance(v, (list, dict)) and not cur):
                out[k] = _deepcopy(v)
            elif isinstance(v, dict) and isinstance(cur, dict):
                out[k] = fill_missing(cur, v)
        return out
    return existing if existing not in (None, "", [], {}) else template


def default_skeleton():
    return {
        "case_id": None,
        "status": STATUS_IN_PROGRESS,
        "output_contract": None,
        "presentation": {"pptx_path": None, "pdf_path": None, "png_paths": [],
                         "slide_count": None, "page_plan": []},
        "evidence_map": [],
        "render_record": {"source_files": [], "template_file": None, "layouts_used": [],
                          "fonts": [], "exports": [], "integrity": {}, "media": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": None, "skeleton_written_at": None,
                    "started_epoch": None, "skeleton_written_epoch": None,
                    "budget_s": BUDGET_S, "request_budget": REQUEST_BUDGET,
                    "requests_used": 0},
    }


def _placeholder(v):
    if isinstance(v, dict):
        return {}
    if isinstance(v, list):
        return []
    return None


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    name = Path(case_path).name or "case"
    return "unknown-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def build_inventory(input_dir):
    root = Path(input_dir)
    items = []
    if not root.exists() or not root.is_dir():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rec = {"relpath": str(p.relative_to(root)), "size_bytes": p.stat().st_size}
        if p.stat().st_size <= 10 * 1024 * 1024:
            rec["sha1"] = hashlib.sha1(p.read_bytes()).hexdigest()
        else:
            rec["sha1"] = None
        if p.suffix.lower() in (".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".py"):
            _, err = read_text(p)
            rec["encoding_note"] = err or "ok"
        items.append(rec)
    return items


def new_ledger(now_ts):
    phases = []
    for p in DEFAULT_PHASES:
        phases.append(dict(p, started_at=None, started_epoch=None, ended_at=None,
                           ended_epoch=None, actual_s=None, status="planned", req_used=0))
    return {
        "schema": "phase-ledger-v1",
        "started_at": now_iso(now_ts),
        "started_epoch": now_ts,
        "skeleton_written_at": now_iso(now_ts),
        "skeleton_written_epoch": now_ts,
        "skeleton_deadline_s": SKELETON_DEADLINE_S,
        "total_budget_s": BUDGET_S,
        "total_req_budget": REQUEST_BUDGET,
        "req_reserve": RESERVE_REQ,
        "requests_used_total": 0,
        "phases": phases,
        "events": [],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--now", type=float, default=None, help="epoch seconds (test injection)")
    args = ap.parse_args(argv)

    now_ts = time.time() if args.now is None else args.now
    in_dir, out_dir, work_dir = Path(args.input), Path(args.output), Path(args.work)
    errors, degradations = [], []
    exit_code = 0

    case_path = in_dir / "case.json"
    task_path = in_dir / "task.md"
    case, case_err = load_json(case_path)
    task_text = None
    if task_path.exists():
        task_text, _ = read_text(task_path)

    inventory = build_inventory(in_dir)
    if not inventory:
        degradations.append({"code": "empty_input", "detail": "no files under %s" % in_dir})
        case_id = None
        final_status = STATUS_INSUFF
        exit_code = 2
    else:
        case_id = extract_case_id(case, case_path)
        if case is None:
            errors.append({"code": "case_json_unavailable", "detail": case_err or "missing"})
            final_status = STATUS_INPUT_ERR
            exit_code = 2
        elif case_id is None or case_id.startswith("unknown-"):
            errors.append({"code": "case_id_missing", "detail": "case.json 无 case_id"})
            final_status = STATUS_INPUT_ERR
            exit_code = 2
        else:
            final_status = STATUS_IN_PROGRESS
            exit_code = 0

    if case_id is None:
        case_id = "unknown"

    skel = default_skeleton()
    skel["case_id"] = case_id
    skel["status"] = final_status
    if isinstance(case, dict):
        contract = case.get("output_contract")
        skel["output_contract"] = contract
        if isinstance(contract, dict):
            for k, v in contract.items():
                if k not in skel:
                    skel[k] = _placeholder(v)
    skel["sources"] = [{"relpath": it["relpath"], "size_bytes": it["size_bytes"]} for it in inventory]
    skel["errors"] = errors
    skel["degradations"] = degradations

    out_file = out_dir / "result.json"
    existing, _ = load_json(out_file)
    if isinstance(existing, dict):
        merged = fill_missing(existing, skel)
        changed = not deep_equal(existing, merged)
    else:
        merged = skel
        changed = True

    rt = merged.get("runtime")
    if not isinstance(rt, dict):
        rt = {}
        merged["runtime"] = rt
    if not rt.get("started_at"):
        rt["started_at"] = now_iso(now_ts)
        rt["started_epoch"] = now_ts
    if not rt.get("skeleton_written_at"):
        rt["skeleton_written_at"] = now_iso(now_ts)
        rt["skeleton_written_epoch"] = now_ts
    rt.setdefault("budget_s", BUDGET_S)
    rt.setdefault("request_budget", REQUEST_BUDGET)
    rt.setdefault("requests_used", 0)

    if changed:
        cl = merged.setdefault("change_log", [])
        if not isinstance(cl, list):
            cl = []
            merged["change_log"] = cl
        cl.append({"ts": now_iso(now_ts), "event": "skeleton_init",
                   "detail": "early-write skeleton merged (idempotent)" if isinstance(existing, dict)
                   else "early-write skeleton created"})

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_file, merged)
    except OSError as exc:
        print("FATAL: cannot write result skeleton: %s" % exc, file=sys.stderr)
        return 3

    try:
        write_json_atomic(work_dir / "input_inventory.json",
                          {"case_id": case_id, "input_dir": str(in_dir),
                           "generated_at": now_iso(now_ts), "files": inventory})
    except OSError as exc:
        degradations.append({"code": "inventory_write_failed", "detail": str(exc)})
        exit_code = 2

    ledger_file = work_dir / "phase_ledger.json"
    ledger, _ = load_json(ledger_file)
    if isinstance(ledger, dict):
        if not ledger.get("started_epoch"):
            ledger["started_epoch"] = now_ts
            ledger["started_at"] = now_iso(now_ts)
        if not ledger.get("skeleton_written_epoch"):
            ledger["skeleton_written_epoch"] = now_ts
            ledger["skeleton_written_at"] = now_iso(now_ts)
        ledger.setdefault("phases", [])
    else:
        ledger = new_ledger(now_ts)
    try:
        write_json_atomic(ledger_file, ledger)
    except OSError as exc:
        degradations.append({"code": "ledger_write_failed", "detail": str(exc)})
        exit_code = 2

    print(json.dumps({"case_id": case_id, "status": final_status,
                      "result": str(out_file), "exit": exit_code,
                      "skeleton_deadline_s": SKELETON_DEADLINE_S,
                      "changed": changed}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
