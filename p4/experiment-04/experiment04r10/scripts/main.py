"""main.py — CLI entry point for the plan-comparison skill.

Subcommands:
  inspect   discover /input, write result.json skeleton + case_pack.json
  assemble  consume evidence_ledger.json + plan_spec.json, write final result
  validate  check /app/output/result.json against the contract
  demo      run a minimal labeled example in a temp directory
  selftest  run the self-check battery (delegates to selftest.py)
"""

import argparse
import os
import sys

import ingest
import compare

HERE = os.path.dirname(os.path.abspath(__file__))


def _defaults():
    return {
        "input": os.environ.get("SKILL_INPUT", ingest.DEFAULT_INPUT),
        "output": os.environ.get("SKILL_OUTPUT", ingest.DEFAULT_OUTPUT),
        "work": os.environ.get("SKILL_WORK", ingest.DEFAULT_WORK),
    }


def _parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Experimental plan comparison and selection skill.",
    )
    p.add_argument("command", nargs="?", default=None,
                   choices=["inspect", "assemble", "validate", "demo", "selftest"])
    p.add_argument("target", nargs="?", default=None, help="validate: result.json path")
    p.add_argument("--input", default=None, help="input dir (default /input)")
    p.add_argument("--output", default=None, help="output dir (default /app/output)")
    p.add_argument("--work", default=None, help="work dir (default /app/work)")
    return p


def cmd_inspect(args):
    cfg = _defaults()
    input_root = args.input or cfg["input"]
    output_dir = args.output or cfg["output"]
    work_dir = args.work or cfg["work"]
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    pack = ingest.build_case_pack(input_root)
    ingest.write_json(os.path.join(work_dir, "case_pack.json"), pack)
    skeleton = ingest.build_skeleton(pack)
    ingest.write_json(os.path.join(output_dir, "result.json"), skeleton)
    print("inspect ok: case_id=%s status=%s candidates=%d contract_fields=%s" % (
        pack["case_id"], skeleton["status"], pack["candidate_count"],
        ",".join(pack["output_contract"]["fields"])))
    return 0


def cmd_assemble(args):
    cfg = _defaults()
    input_root = args.input or cfg["input"]
    output_dir = args.output or cfg["output"]
    work_dir = args.work or cfg["work"]
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    pack, perr = compare.load_work_json(work_dir, "case_pack.json")
    if pack is None:
        pack = ingest.build_case_pack(input_root)
        ingest.write_json(os.path.join(work_dir, "case_pack.json"), pack)
        pack, perr2 = compare.load_work_json(work_dir, "case_pack.json")
        if pack is None:
            print("assemble failed: cannot build case_pack (%s / %s)" % (perr, perr2))
            return 1
    ledger, lerr = compare.load_work_json(work_dir, "evidence_ledger.json")
    spec, serr = compare.load_work_json(work_dir, "plan_spec.json")
    if lerr:
        print("warning: %s" % lerr)
    if serr:
        print("warning: %s" % serr)

    now = ingest.utc_now()
    result = compare.assemble(pack, ledger, spec, output_dir, work_dir, now)
    ingest.write_json(os.path.join(output_dir, "result.json"), result)
    print("assemble ok: status=%s chosen=%s candidates=%d artifacts=%d" % (
        result["status"], result["selection"]["chosen"] if result.get("selection") else None,
        len(result.get("candidates", [])), len(result.get("artifacts", []))))
    return 0


def cmd_validate(args):
    cfg = _defaults()
    output_dir = args.output or cfg["output"]
    work_dir = args.work or cfg["work"]
    input_root = args.input or cfg["input"]
    target = args.target or os.path.join(output_dir, "result.json")
    if not os.path.isfile(target):
        print("validate: result.json not found at %s" % target)
        return 1
    v = compare.validate_result(target, work_dir, input_root)
    print("valid=%s" % ("true" if v["valid"] else "false"))
    for i in v["issues"]:
        print("ISSUE: %s" % i)
    for w in v["warnings"]:
        print("WARN: %s" % w)
    return 0 if v["valid"] else 1


def cmd_demo(args):
    import tempfile
    base = tempfile.mkdtemp(prefix="plan-compare-demo-")
    input_dir = os.path.join(base, "input")
    output_dir = os.path.join(base, "output")
    work_dir = os.path.join(base, "work")
    os.makedirs(os.path.join(input_dir, "materials"), exist_ok=True)

    case = {
        "case_id": "demo-example",
        "output_contract": {"experimental_plan": "object", "evidence_and_risks": "object",
                            "confirmation_items": "list"},
        "objective": "DEMO ONLY - not a formal case",
        "hard_constraints": [{"name": "budget", "op": "<=", "value": 50000, "unit": "CNY"}],
        "candidates": [
            {"plan_id": "P1", "name": "Demo route A", "cost": {"total": 38000, "currency": "CNY"}},
            {"plan_id": "P2", "name": "Demo route B", "cost": {"total": 62000, "currency": "CNY"}},
        ],
    }
    ingest.write_json(os.path.join(input_dir, "case.json"), case)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo example\n\nThis is a labeled demo, not a formal case.\n")
    with open(os.path.join(input_dir, "materials", "quote.csv"), "w", encoding="utf-8") as fh:
        fh.write("item,amount,unit\nreagent,1200,CNY\n")

    pack = ingest.build_case_pack(input_dir)
    ingest.write_json(os.path.join(work_dir, "case_pack.json"), pack)
    ledger = {"case_id": "demo-example", "entries": [
        {"id": "E1", "claim": "reagent unit price", "locator": "materials/quote.csv#L2",
         "quote": "reagent,1200,CNY", "kind": "quote", "confidence": "high"}]}
    ingest.write_json(os.path.join(work_dir, "evidence_ledger.json"), ledger)
    spec = {
        "case_id": "demo-example",
        "research_question": {"testable_question": "which route fits the budget?",
                              "variables": {"independent": ["route"], "dependent": ["cost"],
                                            "controlled": []}, "hypothesis": "route A fits"},
        "comparison_frame": {"objectives": ["demo"], "hard_constraints": case["hard_constraints"],
                             "criteria": list(ingest.FIVE_DIMS)},
        "weights": ingest.DEFAULT_WEIGHTS,
        "candidates": [
            {"plan_id": "P1", "name": "Demo route A",
             "seven_fields": {f: "demo %s" % f for f in ingest.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 3},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
             "risks": [], "evidence_refs": ["E1"],
             "cost": {"currency": "CNY", "method": "override", "total": 38000, "lines": []},
             "time": {"unit": "h", "method": "computed", "total": 20,
                      "parts": {"prep": 4, "scheduling": 2, "execution": 10, "characterization": 3, "rework": 1}}},
            {"plan_id": "P2", "name": "Demo route B",
             "seven_fields": {f: "demo %s" % f for f in ingest.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 3, "risk": 3, "cost": 2, "feasibility": 3, "evidence": 3},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
             "risks": [], "evidence_refs": ["E1"],
             "cost": {"currency": "CNY", "method": "override", "total": 62000, "lines": []},
             "time": {"unit": "h", "method": "computed", "total": 24,
                      "parts": {"prep": 4, "scheduling": 3, "execution": 12, "characterization": 4, "rework": 1}}},
        ],
        "selection": {"chosen": "P1", "ranking": ["P1", "P2"], "rationale": "demo: P1 within budget",
                      "rejected": [{"plan_id": "P2", "reason": "exceeds budget"}], "sensitivity_notes": ""},
        "confirmations": [], "uncertainty": [], "change_log": [],
    }
    ingest.write_json(os.path.join(work_dir, "plan_spec.json"), spec)
    result = compare.assemble(pack, ledger, spec, output_dir, work_dir, ingest.utc_now())
    ingest.write_json(os.path.join(output_dir, "result.json"), result)
    v = compare.validate_result(os.path.join(output_dir, "result.json"), work_dir, input_dir)
    print("demo ok: dir=%s status=%s valid=%s artifacts=%d (EXAMPLE ONLY, not a formal case)" % (
        base, result["status"], "true" if v["valid"] else "false", len(result["artifacts"])))
    return 0 if v["valid"] else 1


def cmd_selftest(args):
    import selftest
    return selftest.run()


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.command:
        _parser().print_help()
        return 0
    if args.command == "inspect":
        return cmd_inspect(args)
    if args.command == "assemble":
        return cmd_assemble(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "selftest":
        return cmd_selftest(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
