#!/usr/bin/env python3
"""CLI for the systematic-review-ledger Skill (standard library only).

The agent writes structured intermediate artifacts under /app/work and calls
these deterministic commands to assemble /app/output/result.json and its
companion artifacts. Commands never overwrite work files; they only read them
and write derived outputs, so the agent's reasoning notes remain auditable.

Subcommands
-----------
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json + /app/work/run_state.json
  plan       validate /app/work/plan.json and merge protocol fields into result.json
  screening  recount PRISMA from /app/work/screening.jsonl and merge records
  evidence   merge /app/work/quality.jsonl + evidence.jsonl into result.json
  merge      deep-merge an arbitrary payload JSON into result.json
  finalize   assemble result.json + review.md + screening_log.json + evidence_map.json
  selfcheck  validate result.json, report coverage and exit nonzero on errors

Common options (defaults match the runtime contract):
  --input  INPUT_DIR   default /input
  --output OUTPUT_DIR  default /app/output
  --work   WORK_DIR    default /app/work
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                        help="input directory (default /input)")
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                        help="output directory (default /app/output)")
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"),
                        help="work directory (default /app/work)")


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def load_state(args):
    obj, _, _ = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else {}


def load_result(args):
    """Return (result, state, contract, errors, degradations)."""
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    state = load_state(args)
    contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
    case_id = state.get("case_id") or "unknown"
    if not isinstance(result, dict):
        result = R.new_skeleton(case_id, contract,
                                note="recreated by command (prior result unreadable)")
        result["status"] = R.STATUS_PARTIAL
    return result, state, contract, errs, degs


def cmd_discover(args):
    inv = R.discover_input(args.input)
    print(json.dumps(inv, ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    werrs = R.ensure_dir(args.output) + R.ensure_dir(args.work)
    inv = R.discover_input(args.input)
    case_obj, errs, degs = (None, [], [])
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": list(errs), "degradations": list(degs)}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            R.write_json(result_path, R.new_skeleton(case_id, contract,
                                                     note="repaired unreadable result.json"))
            summary["wrote"] = True
    else:
        note = "skeleton written before systematic review begins"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no input files found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = R.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; review_question read from task.md if available")
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        werrs.extend(R.write_json(result_path, result))
        summary["wrote"] = not bool(werrs)
        summary["errors"].extend(werrs)

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_at": R.epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
    }
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_plan(args):
    plan, errs, degs = R.load_json(work_path(args, "plan.json"))
    if not isinstance(plan, dict):
        print(json.dumps({"ok": False, "errors": errs or ["plan.json missing or unparseable"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    issues = R.validate_plan(plan)
    result, state, contract, re, rd = load_result(args)
    result["review_question"] = plan.get("review_question")
    result["subquestions"] = plan.get("subquestions") or []
    result["search_strategy"] = plan.get("search_strategy")
    result["inclusion_criteria"] = plan.get("inclusion_criteria") or []
    result["exclusion_criteria"] = plan.get("exclusion_criteria") or []
    result["quality_rubric"] = plan.get("quality_rubric")
    result["protocol_version"] = plan.get("protocol_version")
    result["protocol_frozen_at"] = plan.get("frozen_at")
    result.setdefault("change_log", []).append(
        {"ts": R.iso_now(), "action": "plan", "detail": "merged plan.json protocol"})
    errs.extend(R.write_json(out_path(args, "result.json"), result))
    print(json.dumps({"ok": not errs, "issues": issues,
                      "errors": errs, "degradations": degs + rd},
                     ensure_ascii=False, indent=2))


def cmd_screening(args):
    records, errs, degs = R.read_jsonl(work_path(args, "screening.jsonl"))
    flow = R.recompute_prisma(records)
    result, state, contract, re, rd = load_result(args)
    result["screening_records"] = records
    result["prisma_flow"] = flow
    result["sources"] = R.collect_sources(records, result.get("evidence_map") or [],
                                          result.get("synthesis"))
    result.setdefault("change_log", []).append(
        {"ts": R.iso_now(), "action": "screening",
         "detail": "recounted PRISMA from %d screening records" % len(records)})
    errs.extend(R.write_json(out_path(args, "result.json"), result))
    print(json.dumps({"ok": not errs, "prisma_flow": flow,
                      "record_count": len(records),
                      "errors": errs, "degradations": degs + rd},
                     ensure_ascii=False, indent=2))


def cmd_evidence(args):
    quality, qe, qd = R.read_jsonl(work_path(args, "quality.jsonl"))
    evidence, ee, ed = R.read_jsonl(work_path(args, "evidence.jsonl"))
    result, state, contract, re, rd = load_result(args)
    result["quality_appraisal"] = quality
    result["evidence_map"] = evidence
    result["sources"] = R.collect_sources(result.get("screening_records") or [],
                                          evidence, result.get("synthesis"))
    result.setdefault("change_log", []).append(
        {"ts": R.iso_now(), "action": "evidence",
         "detail": "merged %d appraisals and %d evidence cards" % (len(quality), len(evidence))})
    errs = qe + ee + R.write_json(out_path(args, "result.json"), result)
    print(json.dumps({"ok": not errs, "appraisal_count": len(quality),
                      "evidence_count": len(evidence),
                      "errors": errs, "degradations": qd + ed + rd},
                     ensure_ascii=False, indent=2))


def cmd_merge(args):
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result, state, contract, re, rd = load_result(args)
    R.deep_merge(result, payload)
    result.setdefault("change_log", []).append(
        {"ts": R.iso_now(), "action": "merge",
         "detail": "merged payload %s" % os.path.basename(args.payload)})
    errs.extend(R.write_json(out_path(args, "result.json"), result))
    print(json.dumps({"ok": not errs, "errors": errs,
                      "degradations": degs + rd}, ensure_ascii=False, indent=2))


def render_review_md(result) -> str:
    lines = ["# 系统性文献综述（结构化台账版）", "",
             "- case_id: `%s`" % result.get("case_id"),
             "- status: `%s`" % result.get("status"),
             "- runtime_seconds: %s" % result.get("runtime_seconds"), ""]
    lines.append("## 研究问题")
    lines.append("")
    lines.append(str(result.get("review_question") or "（未填写）"))
    lines.append("")
    subs = result.get("subquestions") or []
    if subs:
        lines.append("### 子问题")
        lines.append("")
        for s in subs:
            if isinstance(s, dict):
                lines.append("- [%s] %s" % (s.get("id"), s.get("question")))
        lines.append("")
    flow = result.get("prisma_flow") or {}
    lines.append("## 筛选漏斗（由记录重算）")
    lines.append("")
    lines.append("- 识别 %s；题录级筛选 %s；全文级筛选 %s；最终纳入 %s" %
                 (flow.get("identified"), flow.get("title_abstract_screened"),
                  flow.get("fulltext_screened"), flow.get("included")))
    lines.append("")
    syn = result.get("synthesis") if isinstance(result.get("synthesis"), dict) else {}
    lines.append("## 综合结论")
    lines.append("")
    for c in (syn.get("conclusions") or []):
        lines.append("- **%s**（evidence: %s）" %
                     (c.get("statement"), ", ".join(c.get("evidence_ids") or [])))
    if not syn.get("conclusions"):
        lines.append("（尚无结论）")
    lines.append("")
    lines.append("## 冲突证据")
    lines.append("")
    for x in (syn.get("conflicts") or []):
        lines.append("- %s：%s vs %s（%s）" %
                     (x.get("claim"), x.get("source_a"), x.get("source_b"),
                      x.get("resolution") or "undetermined"))
    if not syn.get("conflicts"):
        lines.append("（未记录）")
    lines.append("")
    lines.append("## 研究空白")
    lines.append("")
    for g in (syn.get("gaps") or []):
        lines.append("- %s（位置：%s）" % (g.get("description"), g.get("location")))
    if not syn.get("gaps"):
        lines.append("（未记录）")
    lines.append("")
    unc = result.get("uncertainty")
    if isinstance(unc, dict) and unc:
        lines.append("## 不确定性")
        lines.append("")
        lines.append(str(unc))
        lines.append("")
    return "\n".join(lines)


def cmd_finalize(args):
    R.ensure_dir(args.output)
    plan, pe, pd = R.load_json(work_path(args, "plan.json"))
    screening, se, sd = R.read_jsonl(work_path(args, "screening.jsonl"))
    quality, qe, qd = R.read_jsonl(work_path(args, "quality.jsonl"))
    evidence, ee, ed = R.read_jsonl(work_path(args, "evidence.jsonl"))
    synthesis, ye, yd = R.load_json(work_path(args, "synthesis.json"))
    # ledger.jsonl is optional: absent simply means no substantive manual
    # decisions were recorded, so it must not surface as an error.
    ledger, le, ld = [], [], []
    if os.path.exists(work_path(args, "ledger.jsonl")):
        ledger, le, ld = R.read_jsonl(work_path(args, "ledger.jsonl"))

    errors = pe + se + qe + ee + ye + le
    degradations = pd + sd + qd + ed + yd + ld

    result, state, contract, re, rd = load_result(args)
    errors += re
    degradations += rd

    if isinstance(plan, dict):
        result["review_question"] = plan.get("review_question")
        result["subquestions"] = plan.get("subquestions") or []
        result["search_strategy"] = plan.get("search_strategy")
        result["inclusion_criteria"] = plan.get("inclusion_criteria") or []
        result["exclusion_criteria"] = plan.get("exclusion_criteria") or []
        result["quality_rubric"] = plan.get("quality_rubric")
        result["protocol_version"] = plan.get("protocol_version")
        result["protocol_frozen_at"] = plan.get("frozen_at")
    result["screening_records"] = screening
    result["prisma_flow"] = R.recompute_prisma(screening)
    result["quality_appraisal"] = quality
    result["evidence_map"] = evidence
    result["synthesis"] = synthesis if isinstance(synthesis, dict) else None
    if isinstance(synthesis, dict):
        result["uncertainty"] = synthesis.get("uncertainty")
    result["sources"] = R.collect_sources(screening, evidence, synthesis)
    if ledger:
        result["change_log"] = ledger
    result["status"] = R.compute_status(result, screening, evidence, synthesis, errors)
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    started = state.get("started_at") or R.epoch_now()
    result["runtime_seconds"] = round(R.epoch_now() - started, 1)

    werrs = R.write_json(out_path(args, "result.json"), result)
    werrs += R.write_text(out_path(args, "review.md"), render_review_md(result))
    werrs += R.write_json(out_path(args, "screening_log.json"),
                          {"records": screening, "prisma_flow": R.recompute_prisma(screening)})
    werrs += R.write_json(out_path(args, "evidence_map.json"), {"evidence": evidence})
    result["deliverables"] = [
        {"path": "review.md", "role": "human-readable synthesis"},
        {"path": "screening_log.json", "role": "traceable screening records"},
        {"path": "evidence_map.json", "role": "evidence cards with locator+quote"},
    ]
    werrs += R.write_json(out_path(args, "result.json"), result)

    issues = R.validate_result(result, contract, args.work)
    print(json.dumps({"ok": not werrs and not any(s == "error" for s, _ in issues),
                      "status": result["status"],
                      "write_errors": werrs,
                      "validation_issues": issues},
                     ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    state = load_state(args)
    contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
    issues = R.validate_result(result, contract, args.work)
    coverage = R.coverage_report(result) if isinstance(result, dict) else {}
    has_errors = any(sev == "error" for sev, _ in issues)
    print(json.dumps({"ok": not has_errors, "status": result.get("status") if isinstance(result, dict) else None,
                      "coverage": coverage, "issues": issues,
                      "errors": errs, "degradations": degs},
                     ensure_ascii=False, indent=2))
    return 1 if has_errors else 0


COMMANDS = [
    ("discover", cmd_discover, "print the input inventory"),
    ("skeleton", cmd_skeleton, "write an early valid result.json + run_state.json"),
    ("plan", cmd_plan, "validate plan.json and merge protocol fields"),
    ("screening", cmd_screening, "recount PRISMA and merge screening records"),
    ("evidence", cmd_evidence, "merge quality appraisal and evidence cards"),
    ("merge", cmd_merge, "deep-merge an arbitrary payload JSON"),
    ("finalize", cmd_finalize, "assemble result.json + companion artifacts"),
    ("selfcheck", cmd_selfcheck, "validate result.json and report coverage"),
]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, help_text in COMMANDS:
        p = sub.add_parser(name, help=help_text, description=help_text)
        add_common(p)
        if name == "merge":
            p.add_argument("--payload", required=True,
                           help="path to a JSON object to deep-merge")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        fn = dict((n, f) for n, f, _ in COMMANDS)[args.cmd]
        if args.cmd == "selfcheck":
            return fn(args)
        fn(args)
        return 0
    except Exception as exc:  # noqa: BLE001 - degrade instead of crashing
        print(json.dumps({"ok": False, "fatal": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
