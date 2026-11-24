# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill.

Usage (paths relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py plan      --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \
                                      --findings /app/work/findings.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
    python3 scripts/main.py selftest

``scaffold`` is the first-minutes safety net: it writes a valid partial
result.json (case_id + every output_contract field + honest status). ``plan``
writes the auditable intermediate artifact plan.json. ``run`` does
scaffold + plan + finalize with empty findings, so the process always exits
with a legal, honest deliverable even with zero agent judgment. ``finalize``
merges plan + findings + dedup into result.json and report.md.

The scripts never fabricate literature content: missing inputs yield
``insufficient_evidence`` or ``input_error``, never invented records.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}


def _log(event, detail):
    return {"time": core.utcnow_iso(), "event": event, "detail": detail}


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_scaffold(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    status = core.initial_status(data, errors)
    result = core.make_skeleton(case_id, contract, status=status)
    result["sources"] = core._typed_default("sources")
    result["errors"] = list(errors)
    result["runtime"] = {"input_dir": args.input, "output_dir": args.output}
    result["change_log"] = [_log("scaffold_written", "partial skeleton")]
    core.dump_json(result, os.path.join(args.output, "result.json"))
    print("scaffold ok: case_id=%s status=%s contract=%s" %
          (case_id, status, ",".join(contract)))
    return 0


def cmd_plan(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    fresh = core.build_plan(data, case_id, contract)
    plan_path = os.path.join(args.work, "plan.json")
    existing = None
    if os.path.exists(plan_path):
        existing, _ = core.load_json(plan_path)
    plan = core.merge_plan(existing, fresh)
    plan["_path"] = plan_path
    if errors:
        plan.setdefault("gaps", []).extend(
            [{"kind": "input_issue", "detail": e} for e in errors])
    core.dump_json(plan, plan_path)

    # Also refresh the skeleton so output/ always carries a legal result.
    result = core.make_skeleton(case_id, contract,
                                status=core.initial_status(data, errors))
    result["sources"] = []
    result["errors"] = list(errors)
    result["runtime"] = {"input_dir": args.input, "output_dir": args.output}
    result["plan_ref"] = plan_path
    result["change_log"] = [_log("plan_written", plan_path)]
    core.dump_json(result, os.path.join(args.output, "result.json"))
    print("plan ok: %s (%d materials)" % (plan_path, len(plan["materials"])))
    return 0


def cmd_dedup(args):
    records, errs = core.load_json(args.records)
    if records is None:
        core.dump_json({"groups": [], "summary": {"input_count": 0,
                        "group_count": 0, "duplicates_removed": 0},
                        "errors": errs}, args.out)
        print("dedup ok (empty): %s" % args.out)
        return 0
    if isinstance(records, dict):
        records = records.get("records") or records.get("search_results") or []
    if not isinstance(records, list):
        records = []
    out = core.dedup_records(records)
    out["errors"] = errs
    core.dump_json(out, args.out)
    print("dedup ok: %s (%d -> %d groups)" %
          (args.out, out["summary"]["input_count"],
           out["summary"]["group_count"]))
    return 0


def cmd_finalize(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    plan = None
    plan_arg = getattr(args, "plan", None)
    findings_arg = getattr(args, "findings", None)
    dedup_arg = getattr(args, "dedup", None)
    plan_path = plan_arg or os.path.join(args.work, "plan.json")
    if os.path.exists(plan_path):
        plan, _ = core.load_json(plan_path)
    if plan is None:
        plan = core.build_plan(data, case_id, contract)
        plan["_path"] = plan_path
    else:
        plan["_path"] = plan_path

    findings = {}
    if findings_arg and os.path.exists(findings_arg):
        findings, ferrs = core.load_json(findings_arg)
        errors.extend(ferrs)
    if not isinstance(findings, dict):
        findings = {}

    dedup = None
    if dedup_arg and os.path.exists(dedup_arg):
        dedup, derrs = core.load_json(dedup_arg)
        errors.extend(derrs)
    if dedup is None:
        # Derive dedup mechanically from findings.search_results when available.
        records = findings.get("search_results") or []
        if isinstance(records, list) and records:
            dedup = core.dedup_records(records)

    runtime_meta = {
        "input_dir": args.input, "output_dir": args.output,
        "work_dir": args.work, "plan": plan_path,
        "findings": findings_arg, "dedup": dedup_arg,
        "generated_at": core.utcnow_iso(),
    }
    result = core.assemble_result(plan, findings, dedup, data, case_id,
                                  contract, errors, runtime_meta)
    result["change_log"] = list(result.get("change_log") or [])
    result["change_log"].append(_log("finalize", "merged plan+findings+dedup"))
    result["status"] = core.final_status(result)

    # Ensure pending_human is a list and capture honest gaps.
    if not isinstance(result.get("pending_human"), list):
        result["pending_human"] = []
    if result["status"] in ("partial", "insufficient_evidence",
                            "input_error"):
        result["pending_human"].append({
            "kind": "incomplete", "detail":
            "finalize completed without full agent findings; partial result"})

    core.dump_json(result, os.path.join(args.output, "result.json"))
    core.dump_text(core.render_report(result, plan),
                   os.path.join(args.output, "report.md"))
    print("finalize ok: status=%s case_id=%s" % (result["status"], case_id))
    return 0


def cmd_run(args):
    cmd_scaffold(args)
    cmd_plan(args)
    return cmd_finalize(args)


def cmd_normalize(args):
    try:
        rec = __import__("json").loads(args.record)
    except Exception as exc:
        print("bad record json: %s" % exc)
        return 2
    key = core.canonical_key(rec)
    idn = core.record_identifiers(rec)
    print(__import__("json").dumps(
        {"canonical_key": key, "identifiers": idn,
         "record_class": core.classify_record(rec),
         "date_norm": core.record_date(rec)},
        ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_selftest(args):
    import selftest
    return selftest.run(args.input, args.output, args.work)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="literature-tracking Skill: deterministic skeleton/plan/"
                    "dedup/finalize pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    def add_io(sp):
        sp.add_argument("--input", default=DEFAULTS["input"])
        sp.add_argument("--output", default=DEFAULTS["output"])
        sp.add_argument("--work", default=DEFAULTS["work"])

    s = sub.add_parser("scaffold", help="write partial result.json skeleton")
    add_io(s)
    s.set_defaults(func=cmd_scaffold)

    s = sub.add_parser("plan", help="write plan.json intermediate artifact")
    add_io(s)
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("run", help="scaffold + plan + finalize (empty findings)")
    add_io(s)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("dedup", help="cluster records into canonical groups")
    s.add_argument("--records", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_dedup)

    s = sub.add_parser("finalize", help="assemble result.json + report.md")
    add_io(s)
    s.add_argument("--findings", default=None)
    s.add_argument("--dedup", default=None)
    s.add_argument("--plan", default=None)
    s.set_defaults(func=cmd_finalize)

    s = sub.add_parser("normalize", help="print canonical key for one record")
    s.add_argument("--record", required=True)
    s.set_defaults(func=cmd_normalize)

    s = sub.add_parser("selftest", help="run built-in self-checks")
    add_io(s)
    s.set_defaults(func=cmd_selftest)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # last-resort guard: never leave an empty output
        out_dir = getattr(args, "output", DEFAULTS["output"])
        try:
            os.makedirs(out_dir, exist_ok=True)
            core.dump_json({"case_id": "unknown-case", "status": "input_error",
                            "errors": ["uncaught: %s" % exc],
                            "change_log": [_log("crash", str(exc))]},
                           os.path.join(out_dir, "result.json"))
        except Exception:
            pass
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
