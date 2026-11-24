#!/usr/bin/env python3
"""Systematic literature review runtime helper (standard library only).

Subcommands
-----------
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json + /app/work/run_state.json
  merge      deep-merge a payload JSON into result.json (safe incremental update)
  finalize   assemble result.json + review.md + screening_log.json + evidence_map.json
  selfcheck  validate result.json and report coverage + screening + PRISMA checks

Common options (defaults match the runtime contract):
  --input  INPUT_DIR   default /input
  --output OUTPUT_DIR  default /app/output
  --work   WORK_DIR    default /app/work

Robustness contract: every command exits 0 even on empty input, bad JSON, bad
encoding, missing parameters, oversized files or write failures. Problems are
reported inside the returned JSON (errors/degradations) instead of crashing.
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
    obj, _, _ = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else None


def load_result(args):
    """Load current result.json + state; repair to a skeleton if unreadable."""
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    state = load_run_state(args) or {}
    contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
    if not isinstance(result, dict):
        result = R.new_skeleton(state.get("case_id", ""), contract,
                                note="repaired unreadable result.json")
        result["status"] = R.STATUS_INSUFFICIENT
        errs.append("result.json missing or not a dict; skeleton created")
    return result, state, contract, errs, degs


def cmd_discover(args):
    inv = R.discover_input(args.input)
    print(json.dumps(inv, ensure_ascii=False, indent=2, sort_keys=True))
    return inv


def cmd_skeleton(args):
    errors = R.ensure_dir(args.output) + R.ensure_dir(args.work)
    inv = R.discover_input(args.input)
    errors.extend(inv.get("errors", []))
    degradations = list(inv.get("degradations", []))

    case_obj, e2, d2 = (None, [], [])
    if inv.get("case_json"):
        case_obj, e2, d2 = R.load_json(inv["case_json"])
    errors.extend(e2)
    degradations.extend(d2)

    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)
    result_path = out_path(args, "result.json")

    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": [], "degradations": list(degradations)}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e3, d3 = R.load_json(result_path)
        summary["errors"].extend(e3)
        summary["degradations"].extend(d3)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, contract, note="repaired unreadable result.json")
            werr = R.write_json(result_path, result)
            summary["wrote"] = not werr
            summary["errors"].extend(werr)
    else:
        note = "skeleton before systematic review begins"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errors)
        result["degradations"] = list(degradations)
        if not inv.get("exists") or inv.get("total_files", 0) == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = R.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; review_question taken from task.md if readable")
        elif not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        werr = R.write_json(result_path, result)
        summary["wrote"] = not werr
        summary["errors"].extend(werr)

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_at": R.epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
        "last_phase": "skeleton",
    }
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def cmd_merge(args):
    result_path = out_path(args, "result.json")
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False,
                          "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result, _, _, e2, d2 = load_result(args)
    result = R.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(e2)
    werr = R.write_json(result_path, result)
    errs.extend(werr)
    print(json.dumps({"ok": not werr, "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2, sort_keys=True))


def _read_work(args):
    """Read structured work files written by the agent; never fatal.

    Missing files are recorded as degradations (a not-yet-written step), not as
    errors, so a partial workflow stays honest without spurious error noise.
    """
    errors, degradations, missing = [], [], []

    def load_work_json(name):
        path = work_path(args, name)
        if not os.path.exists(path):
            missing.append(name)
            return None, [], []
        return R.load_json(path)

    def load_work_jsonl(name):
        path = work_path(args, name)
        if not os.path.exists(path):
            missing.append(name)
            return [], [], []
        return R.read_jsonl(path)

    protocol, e, d = load_work_json("protocol.json")
    errors += e
    degradations += d
    synthesis, e, d = load_work_json("synthesis.json")
    errors += e
    degradations += d
    screening, e, d = load_work_jsonl("screening.jsonl")
    errors += e
    degradations += d
    quality, e, d = load_work_jsonl("quality_appraisal.jsonl")
    errors += e
    degradations += d
    cards, e, d = load_work_jsonl("evidence_cards.jsonl")
    errors += e
    degradations += d
    if missing:
        degradations.append("work files not yet written: %s" % ", ".join(sorted(missing)))
    return protocol, synthesis, screening, quality, cards, errors, degradations


def cmd_finalize(args):
    errors = R.ensure_dir(args.output) + R.ensure_dir(args.work)
    result, state, _, e0, d0 = load_result(args)
    errors += e0
    degradations = list(d0)

    inventory = state.get("inventory")
    if not isinstance(inventory, dict):
        inventory = R.discover_input(args.input)
    protocol, synthesis, screening, quality, cards, e1, d1 = _read_work(args)
    errors += e1
    degradations += d1

    payload = {}
    if isinstance(protocol, dict):
        for key in ("review_question", "subquestions", "search_strategy",
                    "inclusion_criteria", "exclusion_criteria"):
            if key in protocol:
                payload[key] = protocol[key]
    payload["screening_records"] = screening
    payload["quality_appraisal"] = quality
    if isinstance(synthesis, dict) and synthesis:
        payload["synthesis"] = {
            "conclusions": synthesis.get("conclusions", []),
            "conflicts": synthesis.get("conflicts", []),
            "gaps": synthesis.get("gaps", []),
        }
        payload["uncertainty"] = {
            "conflicts": synthesis.get("conflicts", []),
            "bias": synthesis.get("bias", []),
            "gaps": synthesis.get("gaps", []),
            "undetermined": synthesis.get("undetermined", []),
        }
    payload["evidence_map"] = R.build_evidence_map(cards)
    payload["prisma_flow"] = R.compute_prisma(screening)

    result = R.deep_merge(result, payload)
    case_obj = None
    case_path = os.path.join(args.input, "case.json")
    if os.path.exists(case_path):
        case_obj, _, _ = R.load_json(case_path)
    case_id = state.get("case_id") or R.extract_case_id(case_obj) \
        or result.get("case_id") or "unknown"
    result["case_id"] = case_id
    result["sources"] = R.build_sources(inventory)
    result["status"] = R.determine_status(result)
    result["runtime_seconds"] = max(0, R.epoch_now() - int(state.get("started_at", R.epoch_now())))
    result["errors"] = R.dedupe(list(result.get("errors", [])) + errors)
    result["degradations"] = R.dedupe(list(result.get("degradations", [])) + degradations)
    result.setdefault("limitations", [])

    errs_out = R.write_json(out_path(args, "result.json"), result)
    errs_out += R.write_json(out_path(args, "screening_log.json"), screening)
    errs_out += R.write_json(out_path(args, "evidence_map.json"), R.build_evidence_map(cards))
    errs_out += R.write_text(out_path(args, "review.md"), render_review(result))
    errors += errs_out
    print(json.dumps({"ok": not errs_out, "status": result["status"],
                      "errors": errors, "degradations": degradations},
                     ensure_ascii=False, indent=2, sort_keys=True))


def render_review(result):
    lines = ["# 系统性文献综述结果", ""]
    lines.append("- case_id: `%s`" % (result.get("case_id") or "unknown"))
    lines.append("- status: `%s`" % (result.get("status") or "unknown"))
    lines.append("- runtime_seconds: %s" % result.get("runtime_seconds"))
    lines.append("")
    if result.get("review_question"):
        lines.append("## 研究问题")
        lines.append("")
        lines.append(str(result["review_question"]))
        lines.append("")
    subs = result.get("subquestions") or []
    if subs:
        lines.append("## 子问题")
        lines.append("")
        for item in subs:
            if isinstance(item, dict):
                lines.append("- %s: %s" % (item.get("id", "?"), item.get("question", "")))
            else:
                lines.append("- %s" % item)
        lines.append("")
    prisma = result.get("prisma_flow") or {}
    if prisma:
        lines.append("## PRISMA 计数")
        lines.append("")
        for key, value in prisma.items():
            lines.append("- %s: %s" % (key, value))
        lines.append("")
    synth = result.get("synthesis") or {}
    if synth.get("conclusions"):
        lines.append("## 综合结论")
        lines.append("")
        for i, concl in enumerate(synth["conclusions"], 1):
            if isinstance(concl, dict):
                lines.append("%d. %s" % (i, concl.get("statement", "")))
            else:
                lines.append("%d. %s" % (i, concl))
        lines.append("")
    if synth.get("conflicts"):
        lines.append("## 冲突证据")
        lines.append("")
        for item in synth["conflicts"]:
            if isinstance(item, dict):
                lines.append("- %s" % item.get("description", ""))
            else:
                lines.append("- %s" % item)
        lines.append("")
    if synth.get("gaps"):
        lines.append("## 研究空白")
        lines.append("")
        for item in synth["gaps"]:
            if isinstance(item, dict):
                lines.append("- %s" % item.get("gap", ""))
            else:
                lines.append("- %s" % item)
        lines.append("")
    if result.get("pending_human"):
        lines.append("## 待人工确认")
        lines.append("")
        for item in result["pending_human"]:
            lines.append("- %s" % item)
        lines.append("")
    if result.get("limitations"):
        lines.append("## 局限")
        lines.append("")
        for item in result["limitations"]:
            lines.append("- %s" % item)
        lines.append("")
    return "\n".join(lines)


def cmd_selfcheck(args):
    result, state, contract, errors, degradations = load_result(args)
    checks = []
    checks.append({"id": "result_parse", "pass": isinstance(result, dict),
                   "detail": "result.json loads as a JSON object"})

    has_case = bool(result.get("case_id") and str(result.get("case_id")) != "unknown")
    checks.append({"id": "case_id", "pass": has_case,
                   "detail": "case_id present and not unknown" if has_case else "case_id missing/unknown"})

    valid_status = result.get("status") in R.VALID_STATUSES
    checks.append({"id": "status", "pass": valid_status,
                   "detail": "status=%s" % result.get("status")})

    missing_contract = [k for k in contract if k not in result]
    checks.append({"id": "contract_fields", "pass": not missing_contract,
                   "detail": "missing contract fields: %s" % (missing_contract or "none")})

    emap = {x.get("id") for x in result.get("evidence_map", []) if isinstance(x, dict)}
    unresolved = []
    for concl in (result.get("synthesis") or {}).get("conclusions", []):
        if not isinstance(concl, dict):
            continue
        for eid in concl.get("evidence_ids", []):
            if eid not in emap:
                unresolved.append(eid)
    checks.append({"id": "evidence_backlink", "pass": not unresolved,
                   "detail": "unresolved evidence_ids: %s" % (unresolved or "none")})

    bad_excl = [r.get("record_id") for r in result.get("screening_records", [])
                if isinstance(r, dict) and r.get("decision") == "exclude"
                and not (r.get("reason") and r.get("criterion_id"))]
    checks.append({"id": "screening_audit", "pass": not bad_excl,
                   "detail": "excludes missing reason/criterion_id: %s" % (bad_excl or "none")})

    recomputed = R.compute_prisma(result.get("screening_records", []))
    prisma_ok = (result.get("prisma_flow") == recomputed)
    checks.append({"id": "prisma_recompute", "pass": prisma_ok,
                   "detail": "prisma_flow matches recomputed counts" if prisma_ok
                   else "prisma_flow=%s recomputed=%s" % (result.get("prisma_flow"), recomputed)})

    empty_quote = [x.get("id") for x in result.get("evidence_map", [])
                   if isinstance(x, dict) and not (x.get("quote") or "").strip()]
    checks.append({"id": "quote_present", "pass": not empty_quote,
                   "detail": "evidence entries without quote: %s" % (empty_quote or "none")})

    ok = all(c["pass"] for c in checks)
    print(json.dumps({"ok": ok, "checks": checks,
                      "errors": errors, "degradations": degradations},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return ok


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Systematic literature review runtime helper (standard library only).")
    add_common(parser)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("discover", help="print the input inventory")
    sub.add_parser("skeleton", help="write an early valid result.json + run_state.json")
    merge_p = sub.add_parser("merge", help="deep-merge a payload JSON into result.json")
    merge_p.add_argument("--payload", required=True, help="path to payload JSON")
    sub.add_parser("finalize", help="assemble result.json + companion artifacts")
    sub.add_parser("selfcheck", help="validate result.json and report checks")
    return parser


def _dispatch(argv):
    parser = build_parser()
    try:
        args, unknown = parser.parse_known_args(argv)
    except SystemExit as exc:
        # --help/--version exit 0; argument errors degrade instead of crashing.
        if getattr(exc, "code", 0) == 0:
            return 0
        sys.stderr.write("argument parse error; continuing with safe defaults\n")
        return 0
    if unknown:
        sys.stderr.write("warning: ignored unknown arguments: %s\n" % " ".join(unknown))
    if not getattr(args, "cmd", None):
        parser.print_help(sys.stderr)
        return 0
    handlers = {
        "discover": cmd_discover,
        "skeleton": cmd_skeleton,
        "merge": cmd_merge,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    handler = handlers.get(args.cmd)
    if handler:
        handler(args)
    return 0


def main(argv=None):
    try:
        return _dispatch(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - last-resort guard, never crash the harness
        try:
            R.ensure_dir(os.environ.get("OUTPUT_DIR", "/app/output"))
            R.write_json(os.path.join(os.environ.get("OUTPUT_DIR", "/app/output"),
                                      "result.json"),
                         {"case_id": "unknown", "status": R.STATUS_INPUT_ERROR,
                          "errors": ["fatal: %s" % exc],
                          "limitations": ["unhandled error; see errors"],
                          "sources": [], "uncertainty": {}, "change_log": [],
                          "pending_human": [], "degradations": [],
                          "runtime_seconds": 0})
        except Exception:
            pass
        sys.stderr.write("fatal: %s\n" % exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
