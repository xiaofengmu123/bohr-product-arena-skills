#!/usr/bin/env python3
"""Orchestrator for the fund-proposal writing Skill.

Thin entry point; all logic lives in evidence.py (core implementation).
This split keeps main.py small (< 30 KB) and makes the IR reusable.

Stages (validate-before-render):
  bootstrap  -> write partial result.json skeleton + workbench IR  (do first, <3 min)
  inventory  -> scan /input, snapshot sources with sha256           (L1)
  record     -> merge evidence/element/consistency entries          (L2/L3)
  check      -> run gate checks, update result.json                 (gate)
  finalize   -> honest status + stamp runtime                       (final)

Exit codes: 0 legal result.json exists; 2 input error (still writes a
minimal result.json when possible).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import evidence as ev

_DEFAULTS = {
    "input": "/input",
    "output": "/app/output",
    "work": "/app/work",
    "case": None,
}


def resolve(paths: dict) -> dict:
    """Resolve runtime paths with a local Windows debug fallback."""
    out = dict(paths)
    # Fall back to relative dirs when the absolute sandbox paths are absent.
    for key, abs_path in (("input", "/input"), ("output", "/app/output"), ("work", "/app/work")):
        if not os.path.isdir(paths[key]):
            rel = key
            if os.path.isdir(rel):
                out[key] = rel
    return out


def load_case(case_path: str):
    """Load case.json tolerantly; returns (case_obj, problems)."""
    if not os.path.exists(case_path):
        return None, [f"case.json not found: {case_path}"]
    obj, problems = ev.load_json(case_path)
    if obj is None:
        return None, problems
    return obj, problems


def case_id_from(obj) -> str:
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "caseId"):
            val = obj.get(key)
            if isinstance(val, str) and val:
                return val
    return "unknown"


def _cid(paths: dict) -> str:
    """Current case id, read from case.json (falls back to 'unknown')."""
    obj, _ = load_case(paths["case"])
    return case_id_from(obj) or "unknown"


def contract_hint_from(obj):
    if not isinstance(obj, dict):
        return None
    for key in ("output_contract", "output_schema", "required_fields"):
        if key in obj and obj[key] is not None:
            return {"name": key, "value": obj[key]}
    return None


def load_workbench(work_dir: str, case_id: str, started_at: str) -> dict:
    path = os.path.join(work_dir, "workbench.json")
    if os.path.exists(path):
        wb, _ = ev.load_json(path)
        if isinstance(wb, dict):
            wb.setdefault("schema_version", "1.0.0")
            wb.setdefault("case_id", case_id)
            wb.setdefault("started_at", started_at)
            return wb
    return ev.new_workbench(case_id, started_at)


def load_result(out_dir: str, case_id: str, contract_hint) -> dict:
    path = os.path.join(out_dir, "result.json")
    if os.path.exists(path):
        res, _ = ev.load_json(path)
        if isinstance(res, dict):
            res.setdefault("case_id", case_id)
            return res
    return ev.new_result(case_id, contract_hint)


def cmd_bootstrap(args, paths):
    started = ev.utcnow()
    case_obj, problems = load_case(paths["case"])
    case_id = case_id_from(case_obj)
    contract = contract_hint_from(case_obj)
    wb = load_workbench(paths["work"], case_id, started)
    wb["stage"] = "bootstrap"
    res = load_result(paths["output"], case_id, contract)
    res["runtime"]["stage"] = "bootstrap"
    for p in problems:
        res.setdefault("errors", []).append(p)
    if problems and not case_obj:
        res["status"] = "partial"
        res["version_boundary"]["material_gaps"].append(
            "case.json 缺失或无法解析，case_id 置为 unknown")
    if contract is not None:
        res.setdefault("contract_hint", contract)
    ev.write_json(os.path.join(paths["work"], "workbench.json"), wb)
    ev.write_json(os.path.join(paths["output"], "result.json"), res)
    print(json.dumps({"stage": "bootstrap", "case_id": case_id,
                      "result": os.path.join(paths["output"], "result.json"),
                      "errors": problems}, ensure_ascii=False))
    return 0


def cmd_inventory(args, paths):
    wb = load_workbench(paths["work"], _cid(paths), ev.utcnow())
    entries = []
    problems = []
    input_dir = paths["input"]
    if not os.path.isdir(input_dir):
        problems.append(f"input dir not found: {input_dir}")
    else:
        for name in sorted(os.listdir(input_dir)):
            full = os.path.join(input_dir, name)
            if os.path.isfile(full):
                entry = ev.new_manifest_entry(full, name)
                entries.append(entry)
                if entry["truncated"]:
                    problems.append(f"truncated: {name} > {ev.MAX_READ_BYTES} bytes")
                if not entry["parsed"]:
                    problems.append(f"unreadable: {name}")
    wb["source_manifest"] = entries
    wb["stage"] = "inventory"
    for p in problems:
        if p not in wb["problems"]:
            wb["problems"].append(p)
    res = load_result(paths["output"], _cid(paths), None)
    res["sources"] = [
        {"path": e["path"], "rel": e["rel"], "type": e["ext"], "sha256": e["sha256"],
         "locator": e["rel"], "size_bytes": e["size_bytes"], "encoding": e["encoding"]}
        for e in entries
    ]
    res["runtime"]["stage"] = "inventory"
    if entries:
        ev.log_change(res, "inventory", f"snapshot {len(entries)} source files")
    ev.write_json(os.path.join(paths["work"], "workbench.json"), wb)
    ev.write_json(os.path.join(paths["output"], "result.json"), res)
    print(json.dumps({"stage": "inventory", "sources": len(entries),
                      "problems": problems}, ensure_ascii=False))
    return 0


def cmd_record(args, paths):
    """Merge a JSON patch file (or inline JSON string) into the workbench.

    Accepts --json-file <path> or --json '<...>'. The patch may contain keys:
    evidence_bank (list append), element_map (dict merge), consistency_matrix
    (list append), gates (dict merge), problems (list append).
    """
    wb = load_workbench(paths["work"], _cid(paths), ev.utcnow())
    patch = None
    if args.json_file:
        patch, _ = ev.load_json(args.json_file)
    elif args.json:
        try:
            patch = json.loads(args.json)
        except json.JSONDecodeError as exc:
            patch = None
            wb["problems"].append(f"bad inline json: {exc}")
    if isinstance(patch, dict):
        for key in ("evidence_bank", "consistency_matrix"):
            if key in patch and isinstance(patch[key], list):
                wb.setdefault(key, []).extend(patch[key])
        if "element_map" in patch and isinstance(patch["element_map"], dict):
            wb.setdefault("element_map", {}).update(patch["element_map"])
        if "gates" in patch and isinstance(patch["gates"], dict):
            wb.setdefault("gates", {}).update(patch["gates"])
        if "problems" in patch and isinstance(patch["problems"], list):
            wb.setdefault("problems", []).extend(patch["problems"])
    else:
        wb["problems"].append("record: no valid patch provided")
    wb["stage"] = "record"
    wb["updated_at"] = ev.utcnow()
    ev.write_json(os.path.join(paths["work"], "workbench.json"), wb)
    # Mirror evidence into result.json fact_source_map.
    res = load_result(paths["output"], _cid(paths), None)
    res["fact_source_map"] = [
        {"claim": x.get("claim"), "kind": x.get("kind"),
         "source_ref": x.get("source_file"),
         "locator": x.get("locator"), "quote": x.get("quote"),
         "status": x.get("status")}
        for x in wb.get("evidence_bank", [])
    ]
    res["runtime"]["stage"] = "record"
    ev.write_json(os.path.join(paths["output"], "result.json"), res)
    print(json.dumps({"stage": "record",
                      "evidence": len(wb.get("evidence_bank", [])),
                      "elements": list(wb.get("element_map", {}).keys())},
                     ensure_ascii=False))
    return 0


def cmd_check(args, paths):
    wb = load_workbench(paths["work"], _cid(paths), ev.utcnow())
    problems = ev.check_workbench(wb)
    gates = ev.evaluate_gates(wb)
    wb["problems"] = [p for p in wb.get("problems", [])]
    wb["stage"] = "check"
    ev.write_json(os.path.join(paths["work"], "workbench.json"), wb)
    res = load_result(paths["output"], _cid(paths), None)
    res["version_boundary"]["anomalies"] = [
        f"{code}: {detail}" for code, detail in problems
    ]
    res["runtime"]["stage"] = "check"
    ev.write_json(os.path.join(paths["output"], "result.json"), res)
    report = {
        "gates": gates,
        "problems": [{"code": c, "detail": d} for c, d in problems],
        "status_suggestion": "partial" if problems else "done",
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not problems else 1


def cmd_finalize(args, paths):
    wb = load_workbench(paths["work"], _cid(paths), ev.utcnow())
    wb["updated_at"] = ev.utcnow()
    status = ev.derive_status(wb, args.status or "done")
    ev.write_json(os.path.join(paths["work"], "workbench.json"), wb)
    res = load_result(paths["output"], _cid(paths), None)
    res["status"] = status
    res["runtime"]["stage"] = "finalize"
    res["runtime"]["updated_at"] = ev.utcnow()
    try:
        start = res["runtime"]["started_at"]
        import datetime as _dt
        t0 = _dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
        res["runtime"]["elapsed_s"] = int(time.time() - t0.replace(tzinfo=_dt.timezone.utc).timestamp())
    except Exception:
        res["runtime"]["elapsed_s"] = -1
    if status == "partial":
        res["version_boundary"]["material_gaps"].append(
            "check 发现缺口或材料不完整，保持 partial")
    if args.note:
        ev.log_change(res, "finalize", args.note)
    ev.write_json(os.path.join(paths["output"], "result.json"), res)
    print(json.dumps({"stage": "finalize", "status": status,
                      "result": os.path.join(paths["output"], "result.json")},
                     ensure_ascii=False))
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", default=_DEFAULTS["case"],
                        help="case.json path (default /input/case.json)")
    common.add_argument("--input", default=_DEFAULTS["input"], help="input dir")
    common.add_argument("--output", default=_DEFAULTS["output"], help="output dir")
    common.add_argument("--work", default=_DEFAULTS["work"], help="work dir")

    ap = argparse.ArgumentParser(
        prog="main.py",
        parents=[common],
        description="Fund-proposal writing Skill orchestrator (three-layer IR).")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", parents=[common],
                   help="write partial skeleton (run first)")
    sub.add_parser("inventory", parents=[common],
                   help="snapshot sources with sha256")
    rec = sub.add_parser("record", parents=[common],
                         help="merge evidence/element/consistency patch")
    rec.add_argument("--json-file", default=None, help="patch JSON file")
    rec.add_argument("--json", default=None, help="inline patch JSON string")
    sub.add_parser("check", parents=[common], help="run gate checks")
    fin = sub.add_parser("finalize", parents=[common],
                         help="honest status + stamp runtime")
    fin.add_argument("--status", default=None, choices=["done", "partial", "failed"])
    fin.add_argument("--note", default=None)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    # Resolve case path when not given explicitly.
    if not args.case:
        args.case = os.path.join(args.input, "case.json")
    paths = resolve({"input": args.input, "output": args.output,
                     "work": args.work, "case": args.case})
    os.makedirs(paths["output"], exist_ok=True)
    os.makedirs(paths["work"], exist_ok=True)

    handlers = {
        "bootstrap": cmd_bootstrap,
        "inventory": cmd_inventory,
        "record": cmd_record,
        "check": cmd_check,
        "finalize": cmd_finalize,
    }
    try:
        return handlers[args.command](args, paths)
    except Exception as exc:  # never crash the harness
        res = load_result(paths["output"], "unknown", None)
        res.setdefault("errors", []).append(f"{args.command} crashed: {exc}")
        res["status"] = "failed"
        res["runtime"]["stage"] = args.command
        ev.write_json(os.path.join(paths["output"], "result.json"), res)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
