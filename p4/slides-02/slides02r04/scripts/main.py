"""Command-line entry for academic-slide-evidence.

Subcommands
-----------
scaffold        Write a legal partial result.json skeleton early.
record          Incremental update: source/evidence/version/uncertainty/artifact.
check           Validate the result, write self_check.json; exit 1 on gaps.
finalize        Set final status; refuses 'complete' when check reports errors.
selftest        Run the deterministic self-test of the core helpers.

Usage is relative to the Skill root and independent of the current working
directory. Override dirs with INPUT_DIR / WORK_DIR / OUTPUT_DIR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import core_impl as ci


def _load_existing_result(out_dir):
    result, err = ci.load_result(out_dir)
    if err is not None:
        print("error: cannot load result.json: {}".format(err), file=sys.stderr)
        sys.exit(1)
    if not isinstance(result, dict):
        print("error: result.json is not a JSON object", file=sys.stderr)
        sys.exit(1)
    return result


def _write_result(result, out_dir):
    ci.save_json(out_dir / "result.json", result)
    print("updated {}".format(out_dir / "result.json"))


# --- scaffold ---------------------------------------------------------------
def cmd_scaffold(args):
    out = ci.output_dir()
    work = ci.work_dir()
    work.mkdir(parents=True, exist_ok=True)

    existing, err = ci.load_result(out)
    if err is None and isinstance(existing, dict):
        if existing.get("status") == ci.STATUS_COMPLETE and not args.force:
            print("refusing to clobber a complete result; use --force", file=sys.stderr)
            sys.exit(1)

    case, case_err = ci.load_json(ci.input_dir() / "case.json")
    task_text, task_err = ci.read_text(ci.input_dir() / "task.md")
    errors = [e for e in (case_err, task_err) if e]
    if case is None:
        case = {}

    result = ci.build_skeleton(case, errors=errors)
    ci.save_json(out / "result.json", result)

    items, scan_errs = ci.scan_input(ci.input_dir())
    summary = {
        "case_id": result["case_id"],
        "contract_keys": result["runtime"]["contract_keys"],
        "input_inventory": items,
        "scaffold_errors": errors + scan_errs,
    }
    ci.save_json(work / "case_summary.json", summary)
    print("scaffold written: {}".format(out / "result.json"))
    print("summary written:  {}".format(work / "case_summary.json"))
    for e in errors:
        print("  degraded: {}".format(e), file=sys.stderr)


# --- record ---------------------------------------------------------------
def cmd_record(args):
    out = ci.output_dir()
    result = _load_existing_result(out)
    step = args.what
    reason = getattr(args, "reason", "") or ""

    if step == "source":
        if not (args.ref and args.loc):
            print("error: source requires --ref and --loc", file=sys.stderr)
            sys.exit(2)
        src = ci.make_source(args.ref, args.loc, args.kind, args.quote,
                             _split_list(args.used_in))
        result.setdefault("sources", []).append(src)
        ci.append_change_log(result, "record", "add_source", result["status"],
                             "source {} ({})".format(args.ref, args.loc))
    elif step == "evidence":
        if args.page is None or not (args.role and args.claim):
            print("error: evidence requires --page, --role and --claim",
                  file=sys.stderr)
            sys.exit(2)
        item, problems = ci.make_evidence(
            args.page, args.role, args.claim,
            refs=_split_list(args.refs), loc=args.loc, quote=args.quote,
            kind=args.kind, confidence=args.confidence, reason=args.reason)
        if problems:
            for p in problems:
                print("rejected: {}".format(p), file=sys.stderr)
            print("evidence NOT written; go back to the source or use "
                  "'record uncertainty' / pending_human instead", file=sys.stderr)
            sys.exit(2)
        result.setdefault("evidence_map", []).append(item)
        ci.append_change_log(result, "record", "add_evidence", result["status"],
                             "page {} role={} kind={} conf={}".format(
                                 args.page, args.role, item["claim_kind"],
                                 item["confidence"]))
    elif step == "version":
        if not args.name:
            print("error: version requires --name", file=sys.stderr)
            sys.exit(2)
        files = [ci.file_digest(f) for f in _split_list(args.files)]
        rec = {"name": args.name, "note": args.note, "t": ci.now_iso(), "files": files}
        result.setdefault("version_records", []).append(rec)
        ci.append_change_log(result, "record", "snapshot_version", result["status"],
                             "version {}".format(args.name))
    elif step == "uncertainty":
        kind = args.kind
        text = args.text
        unc = result.setdefault("uncertainty", {})
        if kind == "degradation":
            ci.append_degradation(result, args.text, args.evidence,
                                  args.attempted, args.failure, args.fallback)
        elif kind == "pending_human":
            result.setdefault("pending_human", []).append({
                "t": ci.now_iso(), "text": text,
            })
        else:
            bucket = {
                "limitation": "limitations",
                "unresolved": "unresolved",
                "missing_artifact": "missing_artifacts",
            }.get(kind, "unresolved")
            unc.setdefault(bucket, []).append({
                "t": ci.now_iso(), "text": text,
            })
        ci.append_change_log(result, "record", "add_{}".format(kind),
                             result["status"], text)
    elif step == "artifact":
        if args.field in ("case_id", "status"):
            print("error: {} is a managed field, not an artifact".format(args.field),
                  file=sys.stderr)
            sys.exit(2)
        result[args.field] = json.loads(args.value) if args.json else args.value
        ci.append_change_log(result, "record", "set_artifact", result["status"],
                             "{} = {}".format(args.field, args.value))
    elif step == "change":
        ci.append_change_log(result, args.step_name, args.action, result["status"],
                             reason)
    else:
        print("unknown record kind: {}".format(step), file=sys.stderr)
        sys.exit(2)

    _write_result(result, out)


def _split_list(value):
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


# --- check ---------------------------------------------------------------
def cmd_check(args):
    out = ci.output_dir()
    result, err = ci.load_result(out)
    checks = []

    if err is not None or not isinstance(result, dict):
        checks.append(_item("result_json", "error", "result.json missing or invalid",
                            "{}".format(err)))
        _write_self_check(out, checks)
        print("check failed: 1 error")
        sys.exit(1)

    def add(cid, level, dimension, message):
        checks.append(_item(cid, level, dimension, message))

    # case_id
    if result.get("case_id") in (None, "", "unknown"):
        add("case_id", "warning", "integrity", "case_id missing or 'unknown'")

    # contract fields present
    contract_keys = result.get("runtime", {}).get("contract_keys", [])
    if not contract_keys:
        add("contract_keys", "warning", "integrity",
            "runtime.contract_keys empty; was scaffold run?")
    for key in contract_keys:
        if key not in result:
            add("contract.{}".format(key), "error", "completeness",
                "contract field missing: {}".format(key))
        elif result[key] is None and key in ("presentation_file", "speaker_notes"):
            add("contract.{}".format(key), "warning", "completeness",
                "{} is null (placeholder)".format(key))

    # evidence validity
    evmap = result.get("evidence_map", [])
    if not isinstance(evmap, list):
        add("evidence_map", "error", "verifiability", "evidence_map is not a list")
        evmap = []
    for i, ev in enumerate(evmap):
        for p in ci.validate_evidence(ev):
            add("evidence.{}".format(i), "error", "verifiability",
                "evidence[{}]: {}".format(i, p))

    # evidence refs resolve to sources
    source_refs = {s.get("ref") for s in result.get("sources", [])
                   if isinstance(s, dict)}
    for i, ev in enumerate(evmap):
        for ref in ev.get("refs", []):
            if ref not in source_refs:
                add("evidence.{}".format(i), "warning", "verifiability",
                    "evidence[{}] references missing source '{}'".format(i, ref))

    # complete status requires artifacts and a finalize entry
    if result.get("status") == ci.STATUS_COMPLETE:
        if not result.get("presentation_file"):
            add("presentation_file", "error", "completeness",
                "status=complete but presentation_file empty")
        if not result.get("preview_files"):
            add("preview_files", "error", "completeness",
                "status=complete but preview_files empty")
        if not any(c.get("step") == "finalize" for c in result.get("change_log", [])):
            add("change_log", "warning", "verifiability",
                "status=complete but no finalize step recorded")

    errors = [c for c in checks if c["level"] == "error"]
    warnings = [c for c in checks if c["level"] == "warning"]
    _write_self_check(out, checks)
    print("check: {} error(s), {} warning(s), {} item(s)".format(
        len(errors), len(warnings), len(checks)))
    for c in checks:
        print("  [{}] {}: {}".format(c["level"], c["id"], c["message"]))
    sys.exit(1 if errors else 0)


def _item(cid, level, dimension, message):
    return {"id": cid, "level": level, "dimension": dimension, "message": message}


def _write_self_check(out, checks):
    doc = {
        "t": ci.now_iso(),
        "summary": {
            "errors": sum(1 for c in checks if c["level"] == "error"),
            "warnings": sum(1 for c in checks if c["level"] == "warning"),
        },
        "items": checks,
    }
    ci.save_json(out / "self_check.json", doc)


# --- finalize ---------------------------------------------------------------
def cmd_finalize(args):
    out = ci.output_dir()
    result = _load_existing_result(out)

    # Evaluate completeness against the REQUESTED status, not the current one.
    requested = args.status
    result["status"] = requested
    checks = _collect_checks(result)
    errors = [c for c in checks if c["level"] == "error"]

    status = requested
    if requested == ci.STATUS_COMPLETE and errors:
        status = ci.STATUS_PARTIAL
        note = (args.note or "") + " 自动降级为 partial：check 存在 {} 个错误。".format(
            len(errors))
        print("downgraded complete -> partial ({} errors)".format(len(errors)),
              file=sys.stderr)
    else:
        note = args.note or ""

    result["status"] = status
    result.setdefault("runtime", {})["ended_at"] = ci.now_iso()
    if note:
        ci.append_change_log(result, "finalize", "set_final_status", status, note)
    else:
        ci.append_change_log(result, "finalize", "set_final_status", status,
                             "终检通过，交付状态 {}".format(status))

    _write_self_check(out, checks)
    result["self_check"] = "self_check.json"
    _write_result(result, out)
    print("final status: {}".format(status))


def _collect_checks(result):
    """Shared check logic used by finalize without exiting."""
    checks = []

    def add(cid, level, dimension, message):
        checks.append(_item(cid, level, dimension, message))

    if result.get("case_id") in (None, "", "unknown"):
        add("case_id", "warning", "integrity", "case_id missing or 'unknown'")

    contract_keys = result.get("runtime", {}).get("contract_keys", [])
    for key in contract_keys:
        if key not in result:
            add("contract.{}".format(key), "error", "completeness",
                "contract field missing: {}".format(key))
        elif result[key] is None and key in ("presentation_file", "speaker_notes"):
            add("contract.{}".format(key), "warning", "completeness",
                "{} is null (placeholder)".format(key))

    evmap = result.get("evidence_map", [])
    if not isinstance(evmap, list):
        add("evidence_map", "error", "verifiability", "evidence_map is not a list")
        evmap = []
    for i, ev in enumerate(evmap):
        for p in ci.validate_evidence(ev):
            add("evidence.{}".format(i), "error", "verifiability",
                "evidence[{}]: {}".format(i, p))

    source_refs = {s.get("ref") for s in result.get("sources", [])
                   if isinstance(s, dict)}
    for i, ev in enumerate(evmap):
        for ref in ev.get("refs", []):
            if ref not in source_refs:
                add("evidence.{}".format(i), "warning", "verifiability",
                    "evidence[{}] references missing source '{}'".format(i, ref))

    if result.get("status") == ci.STATUS_COMPLETE:
        if not result.get("presentation_file"):
            add("presentation_file", "error", "completeness",
                "status=complete but presentation_file empty")
        if not result.get("preview_files"):
            add("preview_files", "error", "completeness",
                "status=complete but preview_files empty")
    return checks


# --- selftest ---------------------------------------------------------------
def cmd_selftest(args):
    lines, failures = ci.run_selftest()
    for line in lines:
        print(line)
    sys.exit(1 if failures else 0)


def selftest_main():
    return _run(["selftest"])


# --- argument parsing -------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scaffold", help="write partial result skeleton")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_scaffold)

    r = sub.add_parser("record", help="incremental record")
    r.add_argument("what", choices=[
        "source", "evidence", "version", "uncertainty", "artifact", "change"])
    r.add_argument("--ref"); r.add_argument("--loc"); r.add_argument("--kind")
    r.add_argument("--quote"); r.add_argument("--used-in"); r.add_argument("--refs")
    r.add_argument("--page", type=int); r.add_argument("--role")
    r.add_argument("--claim"); r.add_argument("--confidence")
    r.add_argument("--reason"); r.add_argument("--name"); r.add_argument("--note")
    r.add_argument("--files"); r.add_argument("--text"); r.add_argument("--evidence")
    r.add_argument("--attempted"); r.add_argument("--failure"); r.add_argument("--fallback")
    r.add_argument("--field"); r.add_argument("--value"); r.add_argument("--json",
                                                                        action="store_true")
    r.add_argument("--step-name"); r.add_argument("--action")
    r.set_defaults(func=cmd_record)

    c = sub.add_parser("check", help="validate result; exit 1 on errors")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("finalize", help="set final status")
    f.add_argument("--status", required=True, choices=ci.VALID_STATUS)
    f.add_argument("--note", default="")
    f.set_defaults(func=cmd_finalize)

    t = sub.add_parser("selftest", help="run core self-test")
    t.set_defaults(func=cmd_selftest)

    return p


def _run(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


def main():
    return _run()


if __name__ == "__main__":
    sys.exit(main())
