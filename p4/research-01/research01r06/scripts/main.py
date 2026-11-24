#!/usr/bin/env python3
"""Deep-research skill runtime helper (standard library only).

The model writes substantive content into /app/work as *structured
intermediates* (plan.json, figure_model.json, evidence_cards.jsonl,
tool_calls.jsonl, payload.json); this script deterministically assembles and
validates them. Subcommands:

  discover   print the input inventory as JSON
  skeleton   write an early, always-valid partial result.json + run_state.json
  plan       register/validate plan.json and derive decomposition.md + search_plan.md
  figure     register/validate figure_model.json (figure/table inventory)
  evidence   append validated evidence cards (locator + verbatim quote required)
  merge      deep-merge a payload.json into result.json (safe incremental update)
  finalize   assemble result.json, report.md, evidence_map.json and copy audit trail
  selfcheck  validate result.json and print the six-point coverage report

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
import resultlib as R


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


def load_run_state(args):
    obj, _e, _d = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else {}


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# discover / skeleton
# ---------------------------------------------------------------------------
def cmd_discover(args):
    print_json(R.discover_input(args.input))


def cmd_skeleton(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    inv = R.discover_input(args.input)
    case_obj, errs, degs = (None, [], [])
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_file = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": [], "degradations": []}

    if os.path.exists(result_file):
        summary["already_exists"] = True
        existing, e2, _d2 = R.load_json(result_file)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, contract, note="repaired unreadable result.json")
            summary["errors"].extend(R.write_json(result_file, result))
            summary["wrote"] = True
    else:
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            note = "empty or incomplete input; skeleton before research"
        else:
            note = "skeleton before research begins"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif inv.get("case_json") and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["errors"].extend(R.write_json(result_file, result))
        summary["wrote"] = not summary["errors"]

    state = {"case_id": case_id, "contract": contract, "inventory": inv,
             "started_at": R.epoch_now(), "input_dir": args.input,
             "output_dir": args.output, "work_dir": args.work}
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print_json(summary)


# ---------------------------------------------------------------------------
# plan.json: structured decomposition + search plan (single source of truth)
# ---------------------------------------------------------------------------
def _valid_plan(plan):
    errors = []
    if not isinstance(plan, dict):
        return ["plan payload must be a JSON object"]
    if not (plan.get("research_question") or "").strip():
        errors.append("research_question is empty")
    subqs = plan.get("subquestions")
    if not isinstance(subqs, list) or len(subqs) < 2:
        errors.append("subquestions must be a list of >=2")
    else:
        seen = set()
        for i, s in enumerate(subqs):
            if not isinstance(s, dict):
                errors.append("subquestions[%d] not an object" % i)
                continue
            if not s.get("id"):
                errors.append("subquestions[%d] missing id" % i)
            elif s["id"] in seen:
                errors.append("duplicate subquestion id %r" % s["id"])
            else:
                seen.add(s["id"])
            if not (s.get("text") or "").strip():
                errors.append("subquestion %s missing text" % s.get("id"))
            if not isinstance(s.get("depends_on"), list):
                errors.append("subquestion %s depends_on must be a list" % s.get("id"))
            if not isinstance(s.get("search"), dict):
                errors.append("subquestion %s missing search block" % s.get("id"))
            elif not (s["search"].get("query") or "").strip():
                errors.append("subquestion %s search.query empty" % s.get("id"))
    for field in ("search_order", "parallel_groups"):
        if not isinstance(plan.get(field), list):
            errors.append("%s must be a list" % field)
    if not isinstance(plan.get("stop_conditions"), dict):
        errors.append("stop_conditions must be an object")
    return errors


def cmd_plan(args):
    R.ensure_dir(args.work)
    path = work_path(args, "plan.json")
    plan, errs, degs = R.load_json(path) if os.path.exists(path) else ({}, [], [])
    if not isinstance(plan, dict):
        plan = {"schema_version": "1.0", "research_question": "", "task_type": "",
                "subquestions": [], "search_order": [], "parallel_groups": [],
                "stop_conditions": {}}
    if args.payload:
        payload, perrs, pdegs = R.load_json(args.payload)
        errs.extend(perrs)
        degs.extend(pdegs)
        if isinstance(payload, dict):
            plan = R.deep_merge(plan, payload)
    verrs = _valid_plan(plan)
    summary = {"ok": not verrs, "errors": verrs, "degradations": degs + errs,
               "subquestions": len(plan.get("subquestions") or []),
               "wrote": False}
    if not verrs or args.payload:
        summary["errors"].extend(R.write_json(path, plan))
        decom, planmd = R.plan_to_markdown(plan)
        summary["errors"].extend(R.write_text(work_path(args, "decomposition.md"), decom))
        summary["errors"].extend(R.write_text(work_path(args, "search_plan.md"), planmd))
        summary["wrote"] = True
    print_json(summary)


# ---------------------------------------------------------------------------
# figure_model.json: structured inventory of figures/tables/appendices
# ---------------------------------------------------------------------------
def cmd_figure(args):
    R.ensure_dir(args.work)
    path = work_path(args, "figure_model.json")
    model, errs, degs = R.load_json(path) if os.path.exists(path) else ({}, [], [])
    if not isinstance(model, dict):
        model = {"schema_version": "1.0", "figures": []}
    if args.payload:
        payload, perrs, pdegs = R.load_json(args.payload)
        errs.extend(perrs)
        degs.extend(pdegs)
        if isinstance(payload, dict):
            model = R.deep_merge(model, payload)
    verrs = []
    figs = model.get("figures")
    if not isinstance(figs, list):
        verrs.append("figures must be a list")
    else:
        for i, f in enumerate(figs):
            if not isinstance(f, dict):
                verrs.append("figures[%d] not an object" % i)
                continue
            for field in ("id", "source", "locator", "kind", "caption"):
                if not f.get(field):
                    verrs.append("figure %s missing %s" % (f.get("id", i), field))
    summary = {"ok": not verrs, "errors": verrs, "degradations": errs + degs,
               "figures": len(figs) if isinstance(figs, list) else 0, "wrote": False}
    if not verrs or args.payload:
        summary["errors"].extend(R.write_json(path, model))
        summary["wrote"] = True
    print_json(summary)


# ---------------------------------------------------------------------------
# evidence_cards.jsonl: one validated card per line
# ---------------------------------------------------------------------------
CARD_FIELDS = ("id", "subquestion", "source", "locator", "quote", "claim",
               "judgment", "reason", "conclusion_ids", "figure_ids")
VALID_JUDGMENT = ("supports", "partial", "unsupported", "conflicts", "extraction_failed")


def _valid_card(card):
    errors = []
    if not isinstance(card, dict):
        return ["card must be a JSON object"]
    if not card.get("id"):
        errors.append("card missing id")
    for field in ("source", "locator", "quote", "claim"):
        if not (card.get(field) or "").strip():
            errors.append("card missing %s" % field)
    if card.get("judgment") not in VALID_JUDGMENT:
        errors.append("invalid judgment %r" % card.get("judgment"))
    return errors


def cmd_evidence(args):
    R.ensure_dir(args.work)
    path = work_path(args, "evidence_cards.jsonl")
    summary = {"ok": True, "errors": [], "degradations": [],
               "appended": 0, "total": 0, "wrote": False}
    cards = []
    if args.card:
        obj, errs, degs = R.load_json(args.card)
        summary["errors"].extend(errs)
        summary["degradations"].extend(degs)
        if isinstance(obj, dict):
            cards = [obj]
        elif isinstance(obj, list):
            cards = obj
    if args.import_file:
        with open(args.import_file, "rb") as fh:
            for line in fh:
                try:
                    cards.append(json.loads(line.decode("utf-8-sig")))
                except (ValueError, UnicodeDecodeError) as exc:
                    summary["errors"].append("bad card line: %s" % exc)
    for card in cards:
        verrs = _valid_card(card)
        if verrs:
            summary["errors"].append("%s: %s" % (card.get("id", "?"), "; ".join(verrs)))
            summary["ok"] = False
        else:
            summary["appended"] += 1
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(card, ensure_ascii=False) + "\n")
            summary["wrote"] = True
    # Count existing cards (idempotent; duplicate ids are reported by selfcheck).
    total = 0
    if os.path.exists(path):
        with open(path, "rb") as fh:
            for line in fh:
                if line.strip():
                    total += 1
    summary["total"] = total
    print_json(summary)


# ---------------------------------------------------------------------------
# merge / finalize
# ---------------------------------------------------------------------------
def cmd_merge(args):
    result_file = out_path(args, "result.json")
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print_json({"ok": False, "errors": errs or ["payload not a JSON object"],
                    "degradations": degs})
        return
    result, e2, d2 = R.load_json(result_file)
    if not isinstance(result, dict):
        state = load_run_state(args)
        contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
        case_id = state.get("case_id") or R.extract_case_id(payload)
        result = R.new_skeleton(case_id, contract, note="created during merge")
    result = R.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload)})
    errs.extend(R.write_json(result_file, result))
    print_json({"ok": not errs, "errors": errs, "degradations": degs + d2})


def _load_cards(work):
    path = os.path.join(work, "evidence_cards.jsonl")
    cards, errors = [], []
    if not os.path.exists(path):
        return cards, errors
    with open(path, "rb") as fh:
        for i, line in enumerate(fh):
            if not line.strip():
                continue
            try:
                cards.append(json.loads(line.decode("utf-8-sig")))
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append("evidence_cards.jsonl line %d: %s" % (i + 1, exc))
    return cards, errors


def _card_to_row(card):
    row = {"id": card.get("id", ""), "subquestion": card.get("subquestion", ""),
           "source": card.get("source", ""), "locator": card.get("locator", ""),
           "quote": card.get("quote", ""), "claim": card.get("claim", ""),
           "judgment": card.get("judgment", "supports"),
           "reason": card.get("reason", "")}
    if card.get("conclusion_ids"):
        row["conclusion_ids"] = list(card["conclusion_ids"])
    if card.get("figure_ids"):
        row["figure_ids"] = list(card["figure_ids"])
    return row


def cmd_finalize(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    state = load_run_state(args)
    result_file = out_path(args, "result.json")

    result, errs, degs = R.load_json(result_file)
    if not isinstance(result, dict):
        contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
        result = R.new_skeleton(state.get("case_id") or "", contract,
                                note="created during finalize")

    payload, perrs, pdegs = R.load_json(work_path(args, "payload.json"))
    errs.extend(perrs)
    degs.extend(pdegs)
    if isinstance(payload, dict):
        result = R.deep_merge(result, payload)

    cards, card_errs = _load_cards(args.work)
    errs.extend(card_errs)
    if cards and not result.get("evidence_matrix"):
        result["evidence_matrix"] = [_card_to_row(c) for c in cards]

    plan, _pe, _pd = R.load_json(work_path(args, "plan.json"))
    if isinstance(plan, dict):
        rp = result.setdefault("research_product", {})
        if not rp.get("question"):
            rp["question"] = plan.get("research_question", "")
        if not rp.get("type") and plan.get("task_type"):
            rp["type"] = plan.get("task_type")

    started = state.get("started_at") or R.epoch_now()
    result["runtime_seconds"] = max(0, R.epoch_now() - int(started))
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "finalize",
        "detail": "assembled result.json, report.md, evidence_map.json"})
    errs.extend(R.write_json(result_file, result))

    errs.extend(_write_report(args, result, plan))
    errs.extend(_write_evidence_map(args, result, plan, cards))
    errs.extend(_copy_trail(args))

    ok, verrs = R.validate_result(result)
    errs.extend(verrs)
    print_json({"ok": ok, "errors": errs, "degradations": degs,
                "status": result.get("status"),
                "evidence_rows": len(result.get("evidence_matrix") or []),
                "conclusions": len(((result.get("research_product") or {}).get("conclusions") or []))})


def _write_report(args, result, plan):
    rp = result.get("research_product") or {}
    lines = ["# 深度研究报告", "",
             "case_id：`%s`" % (result.get("case_id") or "（缺）"), "",
             "状态：`%s`" % (result.get("status") or "partial"), "",
             "## 研究问题", "", (rp.get("question") or "（未填）"), "",
             "## 综合结论", "", (rp.get("summary") or "（未填）"), ""]
    concls = rp.get("conclusions") or []
    lines.append("| id | 结论 | 强度 | 证据 |")
    lines.append("|---|---|---|---|")
    for c in concls:
        if not isinstance(c, dict):
            continue
        lines.append("| %s | %s | %s | %s |" % (
            c.get("id", ""), c.get("text", ""), c.get("strength", ""),
            "、".join(c.get("evidence_ids") or [])))
    lines.append("")
    matrix = result.get("evidence_matrix") or []
    lines.append("## 证据矩阵")
    lines.append("")
    lines.append(R.md_table(["id", "来源", "定位", "逐字引用", "判定"],
                            [[r.get("id", ""), r.get("source", ""), r.get("locator", ""),
                              r.get("quote", ""), r.get("judgment", "")] for r in matrix]))
    unc = result.get("uncertainty") or {}
    conflicts = unc.get("conflicts") or []
    if conflicts:
        lines += ["", "## 冲突证据", ""]
        for i, c in enumerate(conflicts):
            srcs = c.get("sources") or [c.get("source_a", ""), c.get("source_b", "")]
            srcs = [s for s in srcs if s]
            lines.append("- **冲突 %d**：%s | %s — 解决：%s"
                         % (i + 1, " vs ".join(srcs), c.get("claim", ""), c.get("resolution", "")))
    gaps = unc.get("gaps") or []
    pending = result.get("pending_human") or []
    if gaps or pending:
        lines += ["", "## 研究空白与待办", ""]
        for g in gaps:
            lines.append("- 空白：%s" % g)
        for p in pending:
            lines.append("- 待人工确认：%s" % p)
    lim = result.get("limitations") or []
    if lim:
        lines += ["", "## 局限", ""]
        for l in lim:
            lines.append("- %s" % l)
    lines.append("")
    return R.write_text(out_path(args, "report.md"), "\n".join(lines))


def _write_evidence_map(args, result, plan, cards):
    figure, _fe, _fd = R.load_json(work_path(args, "figure_model.json"))
    obj = {
        "case_id": result.get("case_id", ""),
        "plan": plan if isinstance(plan, dict) else {},
        "figure_model": figure if isinstance(figure, dict) else {"figures": []},
        "evidence_matrix": result.get("evidence_matrix") or [],
        "conclusions": ((result.get("research_product") or {}).get("conclusions") or []),
        "uncertainty": result.get("uncertainty") or {},
        "sources": result.get("sources") or [],
        "raw_cards": cards,
    }
    return R.write_json(out_path(args, "evidence_map.json"), obj)


def _copy_trail(args):
    errors = []
    for name in ("plan.json", "figure_model.json", "decomposition.md",
                 "search_plan.md", "evidence_cards.jsonl", "tool_calls.jsonl"):
        src = work_path(args, name)
        dst = out_path(args, name)
        if not os.path.exists(src):
            continue
        try:
            with open(src, "rb") as fh_in, open(dst, "wb") as fh_out:
                fh_out.write(fh_in.read())
        except OSError as exc:
            errors.append("copy %s failed: %s" % (name, exc))
    return errors


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------
def cmd_selfcheck(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        print_json({"result_parseable": False, "errors": errs or ["result.json missing"],
                    "degradations": degs, "coverage": {"passed": 0, "total": 6}})
        return
    ok, verrs = R.validate_result(result)
    coverage = R.six_point_coverage(result, args.work)
    print_json({"result_parseable": True, "valid": ok, "errors": verrs,
                "degradations": degs, "coverage": coverage})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(description="Deep-research skill runtime helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="print input inventory")
    add_common(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("skeleton", help="write early valid result.json + run_state.json")
    add_common(p)
    p.set_defaults(func=cmd_skeleton)

    p = sub.add_parser("plan", help="register/validate plan.json and derive notes")
    add_common(p)
    p.add_argument("--payload", help="plan JSON to deep-merge")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("figure", help="register/validate figure_model.json")
    add_common(p)
    p.add_argument("--payload", help="figure-model JSON to deep-merge")
    p.set_defaults(func=cmd_figure)

    p = sub.add_parser("evidence", help="append validated evidence cards")
    add_common(p)
    p.add_argument("--card", help="a single card JSON file (object or list)")
    p.add_argument("--import-file", help="a JSONL file of cards to append")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("merge", help="deep-merge payload.json into result.json")
    add_common(p)
    p.add_argument("--payload", required=True, help="payload JSON file")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("finalize", help="assemble result.json + report.md + evidence_map.json")
    add_common(p)
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("selfcheck", help="validate result.json + six-point coverage")
    add_common(p)
    p.set_defaults(func=cmd_selfcheck)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
