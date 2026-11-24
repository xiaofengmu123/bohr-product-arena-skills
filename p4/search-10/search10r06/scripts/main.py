#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- cross-lingual literature search skill orchestrator.

Subcommands
  init       discover /input, extract case_id + output_contract, and atomically
             write a legal partial result.json skeleton plus the work/plan.json
             and work/evidence.json intermediates. Run this FIRST so a valid
             partial deliverable is on disk within the first 3 minutes.
  plan       deep-merge a JSON file into work/plan.json (research question,
             language routing, terminology table, per-language queries), then
             re-project result.json.
  evidence   deep-merge a JSON file into work/evidence.json (hits with
             locator + verbatim quote, dedup, missing/conflicts), then
             re-project result.json.
  patch      deep-merge a JSON file into result.json for the standard fields
             (status, uncertainty, sources, runtime, ...) not owned by an
             intermediate artifact.
  assemble   rebuild result.json deterministically from plan.json +
             evidence.json + state.json (never required, but makes the
             derivation explicit and reproducible).
  validate   verify result.json is legal JSON, carries case_id, all contract
             fields and the standard fields; print a missing-issue list.
  status     print a short progress summary and the change_log tail.

Standard library only. Default paths follow the runtime contract:
  --input  /input    --output /app/output    --work /app/work
"""

import argparse
import json
import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
import core_impl as ci  # noqa: E402


def _arg_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Cross-lingual literature search skill orchestrator.")
    parser.add_argument("command", choices=[
        "init", "plan", "evidence", "patch", "assemble", "validate", "status"])
    parser.add_argument("--input", default=ci.DEFAULT_INPUT)
    parser.add_argument("--output", default=ci.DEFAULT_OUTPUT)
    parser.add_argument("--work", default=ci.DEFAULT_WORK)
    parser.add_argument("--plan", help="JSON file to merge into work/plan.json")
    parser.add_argument("--evidence", help="JSON file to merge into work/evidence.json")
    parser.add_argument("--patch", help="JSON file to merge into result.json")
    return parser


def _load_or_empty(path):
    obj, err = ci.load_json_if_exists(path)
    if err:
        obj, err = {}, err
    return obj or {}, err


def _merge_file_into(target, patch_path, label):
    """Merge patch_path JSON into target object; returns (target, errors)."""
    patch, err = ci.load_json_if_exists(patch_path)
    if err:
        return target, ["%s patch unreadable: %s" % (label, err)]
    if not isinstance(patch, dict):
        return target, ["%s patch is not a JSON object" % label]
    return ci.deep_merge(target, patch), []


def _refresh_result(out_dir, work_dir, state, plan, evidence, current,
                    runtime_delta=0.0):
    current["runtime"] = round(current.get("runtime", 0) + runtime_delta, 2)
    result = ci.assemble(state, plan, evidence, current)
    ok, err = ci.atomic_write_json(os.path.join(out_dir, "result.json"), result)
    return result, ok, err


def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    found = ci.discover_inputs(args.input)
    case, case_err = {}, None
    task_text = ""
    task_err = None
    if found["case"]:
        case, case_err = ci.safe_json_load(found["case"])
        if case is None:
            case, case_err = {}, case_err
    else:
        case_err = "case.json_not_found"
    if found["task"]:
        task_text, task_err = ci.read_text(found["task"])
        if task_err and task_err not in ("non_utf8_latin1_fallback",):
            task_err = "task.md_read_error:%s" % task_err

    contract = ci.find_contract(case) or ci.find_contract_from_text(task_text)
    case_id = ci.find_case_id(case, task_text)

    errors, degradations = [], []
    status = "partial"
    if not found["case"] and not found["task"]:
        status = "input_error"
        errors.append("case.json_not_found")
        errors.append("task.md_not_found")
    elif not found["case"]:
        status = "input_error"
        errors.append("case.json_not_found")
    if case_err:
        errors.append(case_err)
        if case_err.startswith("bad_json"):
            status = "partial" if found["case"] else status
    if task_err:
        degradations.append(task_err)
    # Materials size/encoding audit.
    for mat in found["materials"]:
        _, merr = ci.read_text(mat)
        if merr:
            degradations.append("material:%s:%s" % (os.path.basename(mat), merr))

    result = ci.build_skeleton(case_id, contract)
    result["status"] = status
    result["errors"] = errors
    result["degradations"] = degradations
    result["change_log"] = [{
        "seq": 1, "op": "init", "summary": "discovered input and wrote skeleton",
        "fields": ["case_id", "status"] + ci.contract_fields(contract)}]
    result["runtime"] = 0
    result["sources"] = [{"path": found["case"] or None, "kind": "case"},
                         {"path": found["task"] or None, "kind": "task"}]

    ok, werr = ci.atomic_write_json(os.path.join(args.output, "result.json"), result)
    state = {
        "case_id": case_id,
        "contract_fields": ci.contract_fields(contract),
        "contract": contract,
        "input": {"case": found["case"], "task": found["task"],
                  "materials": found["materials"]},
        "started_epoch": time.time(),
    }
    ci.atomic_write_json(os.path.join(args.work, "state.json"), state)
    plan, ev = ci.empty_plan(), ci.empty_evidence()
    ci.atomic_write_json(os.path.join(args.work, "plan.json"), plan)
    ci.atomic_write_json(os.path.join(args.work, "evidence.json"), ev)
    print("init ok; case_id=%s status=%s contract_fields=%d" % (
        case_id, status, len(ci.contract_fields(contract))))
    if werr:
        print("warning: %s" % werr, file=sys.stderr)
    return 0


def cmd_plan(args):
    if not args.plan:
        print("error: --plan <file.json> required", file=sys.stderr)
        return 1
    state, _ = _load_or_empty(os.path.join(args.work, "state.json"))
    plan, _ = _load_or_empty(os.path.join(args.work, "plan.json"))
    current, _ = _load_or_empty(os.path.join(args.output, "result.json"))
    ev, _ = _load_or_empty(os.path.join(args.work, "evidence.json"))
    plan, errs = _merge_file_into(plan, args.plan, "plan")
    ci.atomic_write_json(os.path.join(args.work, "plan.json"), plan)
    current["change_log"] = ci._append_change_log(
        current.get("change_log", []), "plan",
        "merged plan artifact", sorted(plan.keys()))
    for e in errs:
        current.setdefault("errors", []).append(e)
    _, ok, werr = _refresh_result(args.output, args.work, state, plan, ev, current)
    print("plan ok; sha256=%s" % ci.sha256_of(plan))
    if not ok:
        print("error: %s" % werr, file=sys.stderr)
        return 1
    return 0


def cmd_evidence(args):
    if not args.evidence:
        print("error: --evidence <file.json> required", file=sys.stderr)
        return 1
    state, _ = _load_or_empty(os.path.join(args.work, "state.json"))
    plan, _ = _load_or_empty(os.path.join(args.work, "plan.json"))
    ev, _ = _load_or_empty(os.path.join(args.work, "evidence.json"))
    current, _ = _load_or_empty(os.path.join(args.output, "result.json"))
    ev, errs = _merge_file_into(ev, args.evidence, "evidence")
    ci.atomic_write_json(os.path.join(args.work, "evidence.json"), ev)
    current["change_log"] = ci._append_change_log(
        current.get("change_log", []), "evidence",
        "merged evidence artifact", sorted(ev.keys()))
    for e in errs:
        current.setdefault("errors", []).append(e)
    _, ok, werr = _refresh_result(args.output, args.work, state, plan, ev, current)
    print("evidence ok; hits=%d sha256=%s" % (len(ev.get("hits", [])), ci.sha256_of(ev)))
    if not ok:
        print("error: %s" % werr, file=sys.stderr)
        return 1
    return 0


def cmd_patch(args):
    if not args.patch:
        print("error: --patch <file.json> required", file=sys.stderr)
        return 1
    patch, perr = ci.load_json_if_exists(args.patch)
    patch_keys = sorted(patch.keys()) if isinstance(patch, dict) else []
    current, _ = _load_or_empty(os.path.join(args.output, "result.json"))
    current, errs = _merge_file_into(current, args.patch, "patch")
    if perr:
        errs.append("patch unreadable: %s" % perr)
    current["change_log"] = ci._append_change_log(
        current.get("change_log", []), "patch", "merged result patch", patch_keys)
    for e in errs:
        current.setdefault("errors", []).append(e)
    ok, werr = ci.atomic_write_json(os.path.join(args.output, "result.json"), current)
    print("patch ok")
    if not ok:
        print("error: %s" % werr, file=sys.stderr)
        return 1
    return 0


def cmd_assemble(args):
    state, _ = _load_or_empty(os.path.join(args.work, "state.json"))
    plan, _ = _load_or_empty(os.path.join(args.work, "plan.json"))
    ev, _ = _load_or_empty(os.path.join(args.work, "evidence.json"))
    current, _ = _load_or_empty(os.path.join(args.output, "result.json"))
    result, ok, werr = _refresh_result(args.output, args.work, state, plan, ev, current)
    print("assemble ok; case_id=%s status=%s results=%d" % (
        result.get("case_id"), result.get("status"), len(result.get("results", []) or [])))
    if not ok:
        print("error: %s" % werr, file=sys.stderr)
        return 1
    return 0


def cmd_validate(args):
    result, err = ci.load_json_if_exists(os.path.join(args.output, "result.json"))
    if err:
        print("result.json unreadable: %s" % err)
        return 0
    state, _ = _load_or_empty(os.path.join(args.work, "state.json"))
    contract = state.get("contract") if isinstance(state.get("contract"), (dict, list)) else None
    if contract is None:
        contract = ci.find_contract(result)
    issues = ci.validate_result(result, contract)
    if issues:
        print("validate: %d issue(s)" % len(issues))
        for issue in issues:
            print("  - %s" % issue)
    else:
        print("validate: ok (no issues)")
    return 0


def cmd_status(args):
    result, err = ci.load_json_if_exists(os.path.join(args.output, "result.json"))
    if err:
        print("status: result.json unreadable (%s)" % err)
        return 0
    print("case_id=%s status=%s" % (result.get("case_id"), result.get("status")))
    print("contract fields: %d; results: %d; evidence_map: %d; dedup: %d" % (
        len([k for k in result if k not in ci.STANDARD_FIELDS and k not in ("case_id", "intermediate")]),
        len(result.get("results", []) or []),
        len(result.get("evidence_map", []) or []),
        len(result.get("dedup", []) or [])))
    for entry in (result.get("change_log") or [])[-5:]:
        print("  [%s] %s" % (entry.get("op"), entry.get("summary")))
    return 0


COMMANDS = {
    "init": cmd_init,
    "plan": cmd_plan,
    "evidence": cmd_evidence,
    "patch": cmd_patch,
    "assemble": cmd_assemble,
    "validate": cmd_validate,
    "status": cmd_status,
}


def main(argv=None):
    args = _arg_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- never let the run crash
        print("fatal: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
