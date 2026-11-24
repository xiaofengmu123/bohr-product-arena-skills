#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-structure-logic-optimization Skill entry point.

Commands:
  skeleton   write a valid partial result.json (status=in_progress) immediately
  evidence   discover materials and extract deterministic candidate evidence
  verify     validate result.json contract and provenance; write verify_report.json
  finalize   set status, append change_log, then verify

Paths are resolved from environment variables first, then CLI flags, then
defaults: CASE_JSON=/input/case.json TASK_MD=/input/task.md INPUT_DIR=/input
WORK_DIR=/app/work OUTPUT_DIR=/app/output RESULT_JSON=/app/output/result.json.
"""

import argparse
import json
import os
import sys

# Emit UTF-8 on stdout/stderr regardless of the platform locale, so captured
# output is deterministic and never mojibake.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - Python < 3.7 or replaced streams
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402

ALLOWED_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
REQUIRED_TOP = ["case_id", "status", "sources", "uncertainty", "pending_human", "change_log"]


def _resolve(flag, env, default):
    if flag:
        return flag
    return os.environ.get(env) or default


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="paper-structure-logic Skill entry point")
    p.add_argument("command", nargs="?", default="skeleton",
                   choices=["skeleton", "evidence", "verify", "finalize"])
    p.add_argument("--case", default=None, help="case.json path")
    p.add_argument("--task", default=None, dest="task_md", help="task.md path")
    p.add_argument("--input", default=None, dest="input_dir", help="input directory")
    p.add_argument("--work", default=None, dest="work_dir", help="work directory")
    p.add_argument("--output", default=None, dest="output_dir", help="output directory")
    p.add_argument("--result", default=None, dest="result_json", help="result.json path")
    p.add_argument("--status", default=None, choices=sorted(ALLOWED_STATUS),
                   help="final status for finalize")
    return p


def resolve_cfg(args):
    output_dir = _resolve(args.output_dir, "OUTPUT_DIR", "/app/output")
    result_json = _resolve(args.result_json, "RESULT_JSON",
                           os.path.join(output_dir, "result.json"))
    return {
        "case_json": _resolve(args.case, "CASE_JSON", "/input/case.json"),
        "task_md": _resolve(args.task_md, "TASK_MD", "/input/task.md"),
        "input_dir": _resolve(args.input_dir, "INPUT_DIR", "/input"),
        "work_dir": _resolve(args.work_dir, "WORK_DIR", "/app/work"),
        "output_dir": output_dir,
        "result_json": result_json,
    }


def _load_task_text(cfg):
    txt, _ = C.read_text_safe(cfg["task_md"])
    if txt is None:
        # task.md is sometimes actually JSON; fall back to loading it as such
        obj, _ = C.load_json_safe(cfg["task_md"])
        if isinstance(obj, dict):
            return json.dumps(obj, ensure_ascii=False)
        if obj is not None:
            return str(obj)
        return ""
    return txt


def cmd_skeleton(cfg):
    os.makedirs(cfg["output_dir"], exist_ok=True)
    os.makedirs(cfg["work_dir"], exist_ok=True)
    case, case_err = C.load_json_safe(cfg["case_json"])
    task_text = _load_task_text(cfg)
    case_id = C.get_case_id(case)
    contract = C.get_output_contract(case)
    obj = {"case_id": case_id, "status": "in_progress"}
    for k in contract:
        obj[k] = None
    obj.update({
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"time": C.now_iso(), "action": "skeleton_written", "status": "in_progress"}],
        "limitations": [],
        "errors": [case_err] if case_err else [],
        "degradations": [],
        "runtime": {"started_at": C.now_iso(), "steps": []},
    })
    werr = C.write_json_safe(cfg["result_json"], obj)
    if werr:
        print(json.dumps({"ok": False, "error": werr}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "case_id": case_id, "result": cfg["result_json"],
                      "contract_fields": sorted(contract.keys())}, ensure_ascii=False))
    return 0


def cmd_evidence(cfg):
    os.makedirs(cfg["output_dir"], exist_ok=True)
    os.makedirs(cfg["work_dir"], exist_ok=True)
    case, _ = C.load_json_safe(cfg["case_json"])
    task_text = _load_task_text(cfg)
    evidence, manifest, errors = C.extract_evidence(case, task_text, cfg["input_dir"], cfg["work_dir"])
    ev_path = os.path.join(cfg["work_dir"], "evidence.json")
    man_path = os.path.join(cfg["work_dir"], "manifest.json")
    e1 = C.write_json_safe(ev_path, evidence)
    e2 = C.write_json_safe(man_path, manifest)
    summary = {
        "ok": e1 is None and e2 is None,
        "evidence": ev_path,
        "manifest": man_path,
        "materials": len(evidence["materials"]),
        "sections": len(evidence["sections"]),
        "candidate_claims": len(evidence["candidate_claims"]),
        "candidate_values": len(evidence["candidate_values"]),
        "near_duplicates": len(evidence["near_duplicates"]),
        "candidate_boundary": len(evidence["candidate_boundary"]),
        "unreadable": len(evidence["unreadable"]),
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


def _add(checks, name, result, detail=""):
    checks.append({"name": name, "result": result, "detail": detail})


def cmd_verify(cfg):
    report = {"checks": [], "pass": True}
    os.makedirs(cfg["output_dir"], exist_ok=True)
    if not os.path.exists(cfg["result_json"]):
        report["pass"] = False
        _add(report["checks"], "result_json_exists", "FAIL", "missing")
        return _write_report(cfg, report)

    data, err = C.load_json_safe(cfg["result_json"])
    if data is None:
        report["pass"] = False
        _add(report["checks"], "valid_json", "FAIL", err or "unreadable")
        return _write_report(cfg, report)

    _add(report["checks"], "valid_json", "PASS")

    missing = [k for k in REQUIRED_TOP if k not in data]
    if missing:
        report["pass"] = False
        _add(report["checks"], "required_fields", "FAIL", "missing: " + ",".join(missing))
    else:
        _add(report["checks"], "required_fields", "PASS")

    status = data.get("status")
    if status not in ALLOWED_STATUS:
        if not status:
            report["pass"] = False
            _add(report["checks"], "status_value", "FAIL", f"status={status!r}")
        else:
            _add(report["checks"], "status_value", "WARN", f"status={status!r}")
    else:
        _add(report["checks"], "status_value", "PASS", status)

    sources = data.get("sources")
    if isinstance(sources, list):
        bad = [s for s in sources if not (isinstance(s, dict) and s.get("claim_id") and s.get("quote"))]
        if bad:
            _add(report["checks"], "sources_provenance", "WARN",
                 f"{len(bad)}/{len(sources)} sources lack claim_id or verbatim quote")
        else:
            _add(report["checks"], "sources_provenance", "PASS", f"{len(sources)} sources")
        no_loc = [s.get("claim_id") for s in sources if isinstance(s, dict) and not s.get("locator")]
        if no_loc:
            _add(report["checks"], "sources_locator", "WARN", "missing locator: " + ",".join(str(x) for x in no_loc[:5]))
        else:
            _add(report["checks"], "sources_locator", "PASS")
    else:
        report["pass"] = False
        _add(report["checks"], "sources_provenance", "FAIL", "sources is not a list")

    cl = data.get("change_log")
    if isinstance(cl, list) and cl:
        _add(report["checks"], "change_log", "PASS", f"{len(cl)} entries")
    else:
        _add(report["checks"], "change_log", "WARN", "empty or not a list")

    has_product = bool(data.get("diagnosis")) or bool(data.get("edit_plan"))
    if has_product:
        _add(report["checks"], "main_product", "PASS", "diagnosis or edit_plan present")
    elif status in ("in_progress", "partial", "insufficient_evidence"):
        _add(report["checks"], "main_product", "WARN", "not yet written (allowed for this status)")
    else:
        report["pass"] = False
        _add(report["checks"], "main_product", "FAIL", "diagnosis/edit_plan missing")

    report["pass"] = report["pass"] and not any(c["result"] == "FAIL" for c in report["checks"])
    return _write_report(cfg, report)


def _write_report(cfg, report):
    path = os.path.join(cfg["output_dir"], "verify_report.json")
    C.write_json_safe(path, report)
    print(json.dumps({"pass": report["pass"], "report": path, "checks": report["checks"]},
                     ensure_ascii=False))
    return 0 if report["pass"] else 1


def cmd_finalize(cfg, status):
    status = status or "partial"
    data, _ = C.load_json_safe(cfg["result_json"])
    if data is None:
        data = {}
    data["status"] = status
    cl = data.setdefault("change_log", [])
    cl.append({"time": C.now_iso(), "action": f"finalize:{status}", "status": status})
    rt = data.setdefault("runtime", {})
    rt["ended_at"] = C.now_iso()
    C.write_json_safe(cfg["result_json"], data)
    return cmd_verify(cfg)


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = resolve_cfg(args)
    cmd = args.command
    if cmd == "skeleton":
        return cmd_skeleton(cfg)
    if cmd == "evidence":
        return cmd_evidence(cfg)
    if cmd == "verify":
        return cmd_verify(cfg)
    if cmd == "finalize":
        return cmd_finalize(cfg, args.status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
