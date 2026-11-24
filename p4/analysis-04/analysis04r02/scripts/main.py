#!/usr/bin/env python3
"""main.py — CLI dispatcher for sci-analysis-checkpoint (pure standard library).

Subcommands:
  init        write partial result skeleton + work/state.json (P0, hard gate)
  profile     profile input files -> output/data_profile.json (P1)
  checkpoint  append checkpoint to state.json and result.json change_log
  validate    validate result.json + artifact presence/format
  set         atomically set one result.json field (e.g. an artifact path)
  evidence    atomically append one {locator, quote, role} source
  selfcheck   light internal smoke check

Every subcommand exits 0 even on failure and never leaves an uncheckpointed
partial state. Unknown CLI flags are ignored so a mistyped flag cannot abort.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def add_common(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT, help="input dir (default /input)")
    sp.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir (default /app/output)")
    sp.add_argument("--work", default=DEFAULT_WORK, help="work dir (default /app/work)")


def build_parser():
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="sci-analysis-checkpoint CLI (phase-budgeted, checkpoint-first data analysis)",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("init", help="P0: write partial result skeleton + state.json")
    add_common(sp)

    sp = sub.add_parser("profile", help="P1: profile input files -> data_profile.json")
    add_common(sp)

    sp = sub.add_parser("checkpoint", help="append phase checkpoint to state.json + change_log")
    add_common(sp)
    sp.add_argument("--phase", required=True, choices=list(core.PHASE_BUDGET), help="phase id P0..P4")
    sp.add_argument("--note", default="", help="short human-readable note")
    sp.add_argument("--req", type=int, default=None, help="model requests used so far (optional)")

    sp = sub.add_parser("validate", help="validate result.json + artifacts")
    add_common(sp)

    sp = sub.add_parser("set", help="set one result.json field (value parsed as JSON when possible)")
    add_common(sp)
    sp.add_argument("--field", required=True, help="field name")
    sp.add_argument("--value", required=True, help="value (JSON or literal string)")

    sp = sub.add_parser("evidence", help="append one {locator, quote, role} source")
    add_common(sp)
    sp.add_argument("--locator", required=True, help="file#section/page locator")
    sp.add_argument("--quote", required=True, help="verbatim quote from the material")
    sp.add_argument("--role", default="evidence", help="role of the evidence")

    sp = sub.add_parser("selfcheck", help="light internal smoke check")
    return p


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def cmd_init(args):
    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass

    files, scan_errs = core.scan_input(args.input)
    case = None
    case_err = None
    for f in files:
        base = os.path.basename(f["relpath"]).lower()
        if base == "case.json" and case is None:
            case, case_err = core.load_json_tolerant(f["path"])

    errors = []
    if case_err:
        errors.append(core.describe_load_error(case_err))
    if not isinstance(case, dict):
        case = {}

    contract, contract_source = core.discover_output_contract(case)

    if not files:
        status = "insufficient_evidence"
    elif case_err:
        status = "input_error"
    else:
        status = "partial"

    result = core.build_skeleton(case, contract, contract_source)
    result["status"] = status
    for e in errors:
        result["errors"].append(e)
    result["change_log"].append({
        "at": core.now_iso(),
        "step": "P0_init",
        "action": "落盘 partial 结果骨架（早落盘硬门槛）",
        "files_scanned": len(files),
        "scan_errors": len(scan_errs),
        "contract_source": contract_source,
    })
    if scan_errs:
        result["degradations"].extend(scan_errs)

    state = {
        "started_at": core.now_iso(),
        "phase": "P0",
        "checkpoints": [],
        "input_dir": args.input,
        "scan_errors": scan_errs,
        "skill": core.SKILL_NAME,
        "version": core.SKILL_VERSION,
    }
    core.write_json_atomic(state, os.path.join(args.work, "state.json"))
    result_path = core.write_json_fallback(result, args.output, args.work)
    print(f"init done: status={status} contract_source={contract_source} files={len(files)} result={result_path}")
    return 0


def cmd_profile(args):
    os.makedirs(args.output, exist_ok=True)
    summary = core.profile_input(args.input)
    path = core.write_json_fallback(summary, args.output, args.output, name="data_profile.json")
    tables = [pr for pr in summary["profiles"] if pr.get("kind") == "table"]
    for pr in tables:
        print(
            f"table {pr.get('path')}: n_rows={pr.get('n_rows')} n_cols={pr.get('n_cols')} "
            f"missing={pr.get('total_missing_cells')} duplicates={pr.get('duplicate_rows')} "
            f"delimiter={pr.get('delimiter')}"
        )
    for pr in summary["profiles"]:
        if pr.get("kind") == "sqlite":
            print(f"sqlite {pr.get('path')}: {len(pr.get('tables', []))} tables")
        if pr.get("kind") == "json":
            print(f"json {pr.get('path')}: top_type={pr.get('top_type')}")
    print(f"profile done: files={summary['file_count']} -> {path}")
    return 0


def cmd_checkpoint(args):
    for d in (args.work, args.output):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    state_path = os.path.join(args.work, "state.json")
    state = core.load_json_file(state_path)
    if not isinstance(state, dict):
        state = {"started_at": core.now_iso(), "phase": args.phase, "checkpoints": []}
    state["phase"] = args.phase
    state.setdefault("checkpoints", []).append({
        "at": core.now_iso(),
        "phase": args.phase,
        "note": args.note,
        "requests_used": args.req,
    })
    core.write_json_atomic(state, state_path)

    result = core.load_json_file(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        result = core.build_skeleton({}, {}, "output_contract_not_found")
    result.setdefault("change_log", []).append({
        "at": core.now_iso(),
        "step": f"checkpoint_{args.phase}",
        "action": args.note or f"phase {args.phase} checkpoint",
    })
    core.write_json_fallback(result, args.output, args.work)
    print(core.budget_status(state, args.phase, args.req))
    print(f"checkpoint {args.phase} saved -> state.json + result.json change_log")
    return 0


def cmd_validate(args):
    result_path = os.path.join(args.output, "result.json")
    result = core.load_json_file(result_path)
    if result is None:
        findings = [{"level": "error", "check": "result_exists", "detail": "result.json missing"}]
        contract = {}
    else:
        case = core.load_case_json(args.input)
        contract, src = core.discover_output_contract(case) if isinstance(case, dict) else ({}, "n/a")
        findings = core.validate_result(result, args.output, contract)
    report = {
        "result_path": result_path,
        "findings": findings,
        "errors": [f for f in findings if f.get("level") == "error"],
        "warnings": [f for f in findings if f.get("level") == "warn"],
        "generated_at": core.now_iso(),
    }
    core.write_json_fallback(report, args.output, args.work, name="validation_report.json")
    for f in findings:
        print(f"[{f['level']}] {f['check']}: {f.get('detail', '')}")
    print(f"validate done: {len(report['errors'])} errors, {len(report['warnings'])} warnings")
    return 0


def cmd_set(args):
    result = core.load_json_file(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        result = core.build_skeleton({}, {}, "output_contract_not_found")
    try:
        val = json.loads(args.value)
    except Exception:  # noqa: BLE001
        val = args.value
    result[args.field] = val
    core.write_json_fallback(result, args.output, args.work)
    shown = json.dumps(core.safe_serialize(val), ensure_ascii=False)[:200]
    print(f"set {args.field} = {shown}")
    return 0


def cmd_evidence(args):
    result = core.load_json_file(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        result = core.build_skeleton({}, {}, "output_contract_not_found")
    result.setdefault("sources", []).append({
        "locator": args.locator,
        "quote": args.quote,
        "role": args.role,
    })
    core.write_json_fallback(result, args.output, args.work)
    print(f"evidence appended: {args.locator}")
    return 0


def cmd_selfcheck(_args):
    checks = [
        ("safe_serialize nan->None", core.safe_serialize({"x": float("nan")})["x"] is None),
        ("build_skeleton has case_id", "case_id" in core.build_skeleton({"case_id": "c"}, {}, "test")),
        ("discover_case_id", core.discover_case_id({"case_id": "abc"}) == "abc"),
        ("discover_output_contract", core.discover_output_contract({"output_contract": {"a": {}}})[0] == {"a": {}}),
        ("PHASE_BUDGET sums to 900s", sum(p["window_s"][1] - p["window_s"][0] for p in core.PHASE_BUDGET.values()) == 900),
        ("PHASE_BUDGET requests sum to 64", sum(p["max_req"] for p in core.PHASE_BUDGET.values()) == 64),
    ]
    for name, ok in checks:
        print(("PASS" if ok else "FAIL") + "  " + name)
    print("selfcheck done")
    return 0  # smoke check always exits 0; failures are printed, not fatal


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

def main(argv=None):
    parser = build_parser()
    args, _unknown = parser.parse_known_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        if args.command == "profile":
            return cmd_profile(args)
        if args.command == "checkpoint":
            return cmd_checkpoint(args)
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "set":
            return cmd_set(args)
        if args.command == "evidence":
            return cmd_evidence(args)
        if args.command == "selfcheck":
            return cmd_selfcheck(args)
        parser.print_help()
        return 0
    except Exception as exc:  # noqa: BLE001 - never crash the harness
        try:
            core.write_json_fallback(
                {"case_id": None, "status": "input_error", "errors": [str(exc)]},
                args.output,
                args.work,
            )
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write(f"fatal (degraded, exit 0): {exc}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
