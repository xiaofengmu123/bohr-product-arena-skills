#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scix-figure orchestrator: S0 bootstrap + stage-budget run book + selftest.

Standard library only. Reads /input/case.json (plus /input/task.md when present),
discovers the output contract, and immediately writes a valid ``partial`` result
skeleton to /app/output/result.json. Every later stage only mutates that
skeleton, so an interruption can never leave an invalid deliverable.

Exit codes: 0 for every handled run, including empty/broken/missing input (which
still yields a valid partial/insufficient-evidence result). A non-zero exit is
reserved for an unhandled crash, which must not happen under normal use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log",
    "pending_human", "errors", "degradations", "runtime", "evidence",
    "mapping", "output_contract", "contract_fields",
}

# Contract schema used only when the input case carries no output_contract.
DEFAULT_CONTRACT = {
    "editable_source": None,
    "render_png": None,
    "render_pdf": None,
    "data_mapping": None,
    "annotation_report": None,
}

STAGES = [
    {"id": "S0", "name": "bootstrap",      "time_s": 170, "requests": 6,  "writes": ["result.json", "contract.json", "state.json", "reproduce.json"]},
    {"id": "S1", "name": "read_material",  "time_s": 120, "requests": 8,  "writes": ["work/notes.md"]},
    {"id": "S2", "name": "parse_data",     "time_s": 120, "requests": 8,  "writes": ["work/t_*.json"]},
    {"id": "S3", "name": "draw",           "time_s": 200, "requests": 14, "writes": ["editable svg + png/pdf"]},
    {"id": "S4", "name": "validate",       "time_s": 120, "requests": 8,  "writes": ["mapping.json", "annotation checks"]},
    {"id": "S5", "name": "selftest",       "time_s": 60,  "requests": 4,  "writes": ["work/check_report.json"]},
    {"id": "S6", "name": "finalize",       "time_s": 60,  "requests": 4,  "writes": ["final result.json"]},
]

ALLOCATED_S = sum(s["time_s"] for s in STAGES)   # 850
ALLOCATED_R = sum(s["requests"] for s in STAGES)  # 52


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str, max_bytes: int = 2_000_000):
    """Read a text file defensively. Returns (text, status)."""
    if not os.path.exists(path):
        return None, "missing"
    size = os.path.getsize(path)
    if size > max_bytes:
        return None, "too_large"
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw:
        return "", "empty"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), "ok"
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "replaced"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_case_file(input_dir: str):
    """Locate the case JSON without assuming a fixed filename."""
    if os.path.isfile(input_dir):
        return input_dir
    if not os.path.isdir(input_dir):
        return None
    for name in ("case.json", "input.json", "task_input.json"):
        p = os.path.join(input_dir, name)
        if os.path.exists(p):
            return p
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return None
    for name in names:
        if name.lower().endswith(".json") and "case" in name.lower():
            return os.path.join(input_dir, name)
    return None


def load_case(input_dir: str):
    case_path = discover_case_file(input_dir)
    errors = []
    degradations = []
    data = {}
    if case_path is None:
        errors.append({"kind": "missing_input", "detail": "no case.json found in input dir", "locator": input_dir})
    else:
        text, status = read_text(case_path)
        if status == "too_large":
            errors.append({"kind": "too_large", "detail": "case file > 2MB, skipped", "locator": case_path})
        elif status == "missing":
            errors.append({"kind": "missing", "detail": "case file disappeared", "locator": case_path})
        else:
            try:
                data = json.loads(text)
                if not isinstance(data, dict):
                    data = {}
                    degradations.append({"kind": "bad_shape", "detail": "case JSON is not an object", "locator": case_path})
            except json.JSONDecodeError as exc:
                errors.append({"kind": "bad_json", "detail": str(exc), "locator": case_path})
                data = {}

    case_id = data.get("case_id") or data.get("id") or ""
    if not case_id:
        degradations.append({"kind": "missing_case_id", "detail": "case_id/id absent in case.json"})

    contract = data.get("output_contract") or data.get("contract") or data.get("outputs") or data.get("deliverables")
    if contract is not None and not isinstance(contract, dict):
        degradations.append({"kind": "bad_contract", "detail": "output_contract is not an object; using default"})
        contract = None

    task_text = ""
    if os.path.isdir(input_dir):
        task_path = os.path.join(input_dir, "task.md")
        if os.path.exists(task_path):
            task_text, _ = read_text(task_path)

    return {
        "case_id": case_id,
        "contract": contract,
        "case_path": case_path,
        "task_text": task_text,
        "errors": errors,
        "degradations": degradations,
        "raw": data,
    }


def make_budget_plan():
    return {
        "summary": {
            "total_s": 900,
            "total_requests": 64,
            "tokens_per_response": 16384,
            "allocated_s": ALLOCATED_S,
            "allocated_requests": ALLOCATED_R,
            "headroom_s": 900 - ALLOCATED_S,
            "headroom_requests": 64 - ALLOCATED_R,
        },
        "plan": STAGES,
    }


def build_skeleton(info, budget_plan):
    result = {
        "case_id": info["case_id"],
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": info["errors"],
        "degradations": info["degradations"],
        "runtime": {
            "started_utc": now_utc(),
            "stage": "S0",
            "budget": budget_plan["summary"],
        },
        "change_log": [],
    }
    contract = info["contract"]
    if isinstance(contract, dict) and contract:
        result["contract_fields"] = sorted(contract.keys())
        for key, val in contract.items():
            if key in RESERVED_KEYS or key in result:
                result.setdefault("output_contract", {})[key] = val
                result["degradations"].append({
                    "kind": "reserved_collision",
                    "detail": "contract field '%s' collides with a reserved key; kept under output_contract" % key,
                })
            else:
                result[key] = None
    else:
        result["contract_fields"] = sorted(DEFAULT_CONTRACT.keys())
        for key in DEFAULT_CONTRACT:
            result[key] = None
    result["change_log"].append({
        "step": "S0",
        "time_utc": now_utc(),
        "status": "partial",
        "note": "skeleton written; contract fields nulled; pending incremental fill",
    })
    return result


def build_state(case_id, budget_plan):
    return {
        "case_id": case_id,
        "created_utc": now_utc(),
        "stage": "S0",
        "plan": budget_plan["plan"],
        "completed": {},
    }


def build_reproduce(case_id, input_dir, case_path, budget_plan):
    inputs = []
    if case_path:
        try:
            inputs.append({
                "path": os.path.basename(case_path),
                "sha256": sha256(case_path),
                "bytes": os.path.getsize(case_path),
            })
        except OSError:
            pass
    if os.path.isdir(input_dir):
        try:
            for name in sorted(os.listdir(input_dir)):
                p = os.path.join(input_dir, name)
                if os.path.isfile(p) and name not in ("case.json", "task.md") and not name.lower().endswith(".json"):
                    try:
                        inputs.append({"path": name, "sha256": sha256(p), "bytes": os.path.getsize(p)})
                    except OSError:
                        continue
        except OSError:
            pass
    return {
        "case_id": case_id or "",
        "seed": 0,
        "input_files": inputs,
        "commands": [],
        "python": sys.version.split()[0],
        "plan": budget_plan["plan"],
    }


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def run_bootstrap(input_dir, output_dir, work_dir):
    info = load_case(input_dir)
    budget_plan = make_budget_plan()
    result = build_skeleton(info, budget_plan)
    state = build_state(info["case_id"], budget_plan)
    repro = build_reproduce(info["case_id"], input_dir, info["case_path"], budget_plan)

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)
    write_json(
        os.path.join(work_dir, "contract.json"),
        {
            "case_id": info["case_id"],
            "output_contract": info["contract"] if isinstance(info["contract"], dict) else DEFAULT_CONTRACT,
            "contract_fields": result.get("contract_fields", []),
        },
    )
    write_json(os.path.join(work_dir, "state.json"), state)
    write_json(os.path.join(work_dir, "reproduce.json"), repro)

    return {
        "result_path": result_path,
        "status": result["status"],
        "case_id": info["case_id"],
        "errors": info["errors"],
        "degradations": info["degradations"],
    }


def selftest():
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        # 1. empty input dir -> valid partial skeleton
        ind = os.path.join(tmp, "empty_in")
        os.makedirs(ind)
        r1 = run_bootstrap(ind, os.path.join(tmp, "empty_out"), os.path.join(tmp, "empty_work"))
        r1_exists = os.path.exists(os.path.join(tmp, "empty_out", "result.json"))
        checks.append({"case": "empty_input", "ok": r1_exists and r1["status"] == "partial" and not r1["case_id"], "detail": r1})

        # 2. bad JSON -> error recorded, skeleton still valid
        ind2 = os.path.join(tmp, "bad_in")
        os.makedirs(ind2)
        with open(os.path.join(ind2, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        r2 = run_bootstrap(ind2, os.path.join(tmp, "bad_out"), os.path.join(tmp, "bad_work"))
        bad_recorded = any(e.get("kind") == "bad_json" for e in r2["errors"])
        checks.append({"case": "bad_json", "ok": os.path.exists(os.path.join(tmp, "bad_out", "result.json")) and bad_recorded, "detail": r2})

        # 3. missing input dir -> partial skeleton, no crash
        r3 = run_bootstrap(os.path.join(tmp, "nope"), os.path.join(tmp, "miss_out"), os.path.join(tmp, "miss_work"))
        checks.append({"case": "missing_input", "ok": os.path.exists(os.path.join(tmp, "miss_out", "result.json")) and r3["status"] == "partial", "detail": r3})

        # 4. proper contract: flatten + collision handling
        ind4 = os.path.join(tmp, "ok_in")
        os.makedirs(ind4)
        case4 = {
            "case_id": "case-demo",
            "output_contract": {
                "editable_source": "figures/fig.svg",
                "render_png": "figures/fig.png",
                "status": "reserved-collision-demo",
            },
        }
        with open(os.path.join(ind4, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case4, fh)
        r4 = run_bootstrap(ind4, os.path.join(tmp, "ok_out"), os.path.join(tmp, "ok_work"))
        res4 = json.load(open(os.path.join(tmp, "ok_out", "result.json"), encoding="utf-8"))
        ok4 = (
            res4.get("case_id") == "case-demo"
            and res4.get("editable_source") is None
            and "output_contract" in res4
            and res4["output_contract"].get("status") == "reserved-collision-demo"
            and "contract_fields" in res4
        )
        checks.append({"case": "contract_flatten_and_collision", "ok": ok4, "detail": r4})

    report = {"selftest": checks, "all_ok": all(c["ok"] for c in checks)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--selftest", action="store_true", help="run package self-test, no real case needed")
    ap.add_argument("--budget", action="store_true", help="print the stage budget plan as JSON")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.budget:
        print(json.dumps(make_budget_plan(), ensure_ascii=False, indent=2))
        return 0

    r = run_bootstrap(a.input, a.output, a.work)
    print(json.dumps({"ok": True, "result": r}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
