#!/usr/bin/env python3
"""CLI for the deep-research evidence-ledger skill (standard library only).

Subcommands:
  discover   print the input inventory as JSON (read-only)
  skeleton   write an early, valid result.json + /app/work/run_state.json
  card       append one evidence card (--card JSON) or a batch (--file PATH)
  finalize   rebuild result.json from the ledger + contract, then write
             report.md, evidence_map.json and copy work notes to /app/output
  selfcheck  validate result.json and print the six-point coverage report

Common options (defaults match the runtime contract):
  --input INPUT_DIR    default /input
  --output OUTPUT_DIR  default /app/output
  --work WORK_DIR      default /app/work
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L  # noqa: E402


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def load_run_state(args):
    obj, _, _ = L.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else {}


def save_run_state(args, **extra):
    state = load_run_state(args)
    state.update(extra)
    return L.write_json(work_path(args, "run_state.json"), state)


def cmd_discover(args):
    inv = L.discover_input(args.input)
    print(json.dumps(inv, ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    L.ensure_dir(args.output)
    L.ensure_dir(args.work)
    inv = L.discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = L.load_json(inv["case_json"])
    case_id = L.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = L.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": list(errs), "degradations": list(degs)}

    input_error_hint = bool(inv.get("case_json")) and not isinstance(case_obj, dict)

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, d2 = L.load_json(result_path)
        summary["errors"].extend(e2)
        summary["degradations"].extend(d2)
        if not isinstance(existing, dict):
            result = L.new_skeleton(case_id, contract,
                                    note="repaired unreadable result.json")
            summary["errors"].extend(L.write_json(result_path, result))
            summary["wrote"] = True
    else:
        if not inv["exists"] or inv["total_files"] == 0:
            note = "empty input directory; no materials found"
        elif not inv.get("case_json"):
            note = "no case.json found"
        else:
            note = "skeleton before research begins"
        result = L.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            result["status"] = L.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif input_error_hint:
            result["status"] = L.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["errors"].extend(L.write_json(result_path, result))
        summary["wrote"] = True

    summary["errors"].extend(save_run_state(
        args, case_id=case_id, contract=contract, inventory=inv,
        started_at=L.epoch_now(), input_error_hint=input_error_hint,
        input_dir=args.input, output_dir=args.output, work_dir=args.work))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _collect_new_cards(args):
    errors = []
    if args.card:
        try:
            value = json.loads(args.card)
        except ValueError as exc:
            print(json.dumps({"ok": False, "errors": ["--card not JSON: %s" % exc],
                              "added": 0, "updated": 0, "skipped": 0},
                             ensure_ascii=False, indent=2))
            return None, [1]
        new_cards = value if isinstance(value, list) else [value]
    elif args.file:
        if args.file.endswith(".jsonl"):
            new_cards, ferrs, _ = L.load_jsonl(args.file)
            errors.extend(ferrs)
        else:
            obj, ferrs, _ = L.load_json(args.file)
            errors.extend(ferrs)
            new_cards = obj if isinstance(obj, list) else ([obj] if isinstance(obj, dict) else [])
    else:
        new_cards = []
        errors.append("provide --card JSON or --file PATH")
    return new_cards, errors


def cmd_card(args):
    new_cards, errors = _collect_new_cards(args)
    if new_cards is None:
        return
    path = work_path(args, "evidence_cards.jsonl")
    existing, lerrs, _ = L.load_jsonl(path)
    errors.extend(lerrs)
    by_id = {}
    for card in existing:
        if isinstance(card, dict) and card.get("id"):
            by_id.setdefault(card["id"], card)
    added, updated, skipped = 0, 0, 0
    for idx, card in enumerate(new_cards):
        verrs = L.validate_card(card, idx)
        if verrs:
            errors.extend(verrs)
            skipped += 1
            continue
        if card["id"] in by_id:
            by_id[card["id"]] = card
            updated += 1
        else:
            by_id[card["id"]] = card
            added += 1
    ordered = [by_id[k] for k in by_id]
    if ordered:
        errors.extend(_write_jsonl(path, ordered))
    print(json.dumps({"ok": not errors, "errors": errors, "added": added,
                      "updated": updated, "skipped": skipped, "total": len(ordered)},
                     ensure_ascii=False, indent=2))


def _write_jsonl(path, items):
    L.ensure_dir(os.path.dirname(path))
    try:
        with open(path, "w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        return []
    except Exception as exc:  # noqa: BLE001
        return ["write_jsonl(%s): %s: %s" % (path, type(exc).__name__, exc)]


def cmd_finalize(args):
    state = load_run_state(args) or {}
    started = state.get("started_at") or L.epoch_now()
    runtime = L.epoch_now() - started
    contract = state.get("contract") or L.build_contract(None)
    case_id = state.get("case_id") or ""
    input_error_hint = bool(state.get("input_error_hint"))

    product, perrs, _ = L.load_json(work_path(args, "product.json"))
    if not isinstance(product, dict):
        product = {}
    conclusions, cerrs, _ = L.load_jsonl(work_path(args, "conclusions.jsonl"))
    cards, eerrs, _ = L.load_jsonl(work_path(args, "evidence_cards.jsonl"))
    uncertainty, uerrs, _ = L.load_json(work_path(args, "uncertainty.json"))
    if not isinstance(uncertainty, dict):
        uncertainty = {}
    decomposition, derrs, _ = L.read_text(work_path(args, "decomposition.md"))
    search_plan, serrs, _ = L.read_text(work_path(args, "search_plan.md"))

    errors, degradations = [], []
    for label, errs in (("product", perrs), ("conclusions", cerrs),
                        ("evidence", eerrs), ("uncertainty", uerrs),
                        ("decomposition", derrs), ("search_plan", serrs)):
        errors.extend(errs)

    # Tier 1: syntactic validation (conclusions are validated here).
    for idx, concl in enumerate(conclusions):
        errors.extend(L.validate_conclusion(concl, idx))
    L.validate_uncertainty(uncertainty, errors)

    # Tier 2: referential integrity.
    sub_ids = L.parse_decomposition_ids(decomposition)
    ref_errors, _warnings = L.check_references(conclusions, cards, sub_ids)

    # Tier 3: deterministic assembly & contract echo. Validate-then-write.
    result = L.assemble(case_id, contract, product, conclusions, cards,
                        uncertainty, runtime, errors + ref_errors, degradations,
                        state.get("limitations", []), [], input_error_hint,
                        ref_errors)
    result["sources"] = state.get("sources", []) or _derive_sources(cards)
    result["change_log"] = result.get("change_log") if isinstance(result.get("change_log"), list) else []
    result["change_log"].append({
        "ts": L.iso_now(), "action": "finalize",
        "detail": "assembled %d conclusions from %d evidence cards" % (
            len(result["research_product"]["conclusions"]), len(result["evidence_matrix"])),
    })
    write_errs = L.write_json(out_path(args, "result.json"), result)
    result.setdefault("errors", []).extend(write_errs)

    # Companion artifacts.
    _write_report(out_path(args, "report.md"), result, decomposition, search_plan)
    _write_evidence_map(out_path(args, "evidence_map.json"), result)
    # Copy work notes into output as plain markdown (readable provenance).
    for name in ("decomposition.md", "search_plan.md"):
        text, _, _ = L.read_text(work_path(args, name))
        if text.strip():
            _write_text(out_path(args, name), text)

    print(json.dumps({
        "ok": not write_errs,
        "status": result["status"],
        "conclusions": len(result["research_product"]["conclusions"]),
        "evidence": len(result["evidence_matrix"]),
        "referential_errors": len(ref_errors),
        "errors": len(errors) + len(ref_errors),
        "runtime_seconds": round(runtime, 3),
    }, ensure_ascii=False, indent=2))


def _derive_sources(cards):
    seen = []
    for card in cards:
        src = card.get("source") if isinstance(card, dict) else None
        if src and src not in seen:
            seen.append(src)
    return seen


def _write_text(path, text):
    try:
        L.ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except Exception as exc:  # noqa: BLE001
        return ["write_text(%s): %s" % (path, exc)]


def _write_report(path, result, decomposition, search_plan):
    product = result.get("research_product") or {}
    lines = ["# 深度研究结论摘要", "",
             "- case_id: `%s`" % result.get("case_id"),
             "- status: `%s`" % result.get("status"),
             "- 研究问题: %s" % (product.get("question") or "（未填写）"),
             "- 产物类型: `%s`" % (product.get("type") or "synthesis"), "",
             "## 综合结论摘要", "",
             (product.get("summary") or "（未填写）").strip(), "",
             "## 结论清单", ""]
    for idx, concl in enumerate(product.get("conclusions") or [], start=1):
        lines.append("%d. **%s**（强度：`%s`；证据：%s）" % (
            idx, concl.get("text"), concl.get("strength"),
            ", ".join(concl.get("evidence_ids") or [])))
    lines += ["", "## 不确定性", ""]
    uncertainty = result.get("uncertainty") or {}
    for section in ("conflicts", "biases", "gaps", "undetermined"):
        lines.append("### %s" % section)
        items = uncertainty.get(section) or []
        if not items:
            lines.append("（空）")
        else:
            for item in items:
                lines.append("- %s" % (json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item))
    lines += ["", "## 缺口与待人工确认", ""]
    for item in result.get("pending_human") or []:
        lines.append("- %s" % item)
    if not (result.get("pending_human") or []):
        lines.append("（空）")
    lines += ["", "## 限制 / 错误 / 降级", ""]
    for section in ("limitations", "errors", "degradations"):
        lines.append("### %s" % section)
        for item in result.get(section) or []:
            lines.append("- %s" % item)
        if not result.get(section):
            lines.append("（空）")
    lines += ["", "## 可复核材料", "",
              "- `decomposition.md`：子问题拆解", "- `search_plan.md`：检索计划",
              "- `evidence_map.json`：逐条证据卡（locator + quote）", ""]
    _write_text(path, "\n".join(lines) + "\n")


def _write_evidence_map(path, result):
    cards = result.get("evidence_matrix") or []
    conclusion_index = {}
    for card in cards:
        for cid in card.get("conclusion_ids") or []:
            conclusion_index.setdefault(cid, []).append(card.get("id"))
    L.write_json(path, {
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "evidence_matrix": cards,
        "conclusion_index": conclusion_index,
    })


def cmd_selfcheck(args):
    result, errs, degs = L.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        print(json.dumps({"ok": False, "fatal": errs or ["result.json missing/unparseable"]},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    decomposition, _, _ = L.read_text(work_path(args, "decomposition.md"))
    search_plan, _, _ = L.read_text(work_path(args, "search_plan.md"))
    checks = L.coverage_report(result, decomposition, search_plan)
    ok = all(okay for _, okay, _ in checks) and not errs
    print(json.dumps({
        "ok": ok,
        "parse_errors": errs,
        "degradations": degs,
        "coverage": [{"item": name, "pass": bool(okay), "detail": detail}
                     for name, okay, detail in checks],
        "status": result.get("status"),
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if (isinstance(result, dict)) else 1)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common)
    add_common(parser)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", parents=[common], help="print input inventory").set_defaults(func=cmd_discover)
    sub.add_parser("skeleton", parents=[common], help="write early valid result.json").set_defaults(func=cmd_skeleton)

    p_card = sub.add_parser("card", parents=[common], help="append validated evidence card(s)")
    p_card.add_argument("--card", help="single card as JSON")
    p_card.add_argument("--file", help="path to JSON array or JSONL batch")
    p_card.set_defaults(func=cmd_card)

    sub.add_parser("finalize", parents=[common], help="assemble result.json + companions").set_defaults(func=cmd_finalize)
    sub.add_parser("selfcheck", parents=[common], help="validate + six-point coverage").set_defaults(func=cmd_selfcheck)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
