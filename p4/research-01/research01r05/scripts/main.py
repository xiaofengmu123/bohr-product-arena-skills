#!/usr/bin/env python3
"""Contract-first deep-research runtime helper (standard library only).

Subcommands
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json + /app/work/run_state.json
  contract   print the normalized output_contract extracted from case.json
  merge      deep-merge a payload JSON into result.json (incremental, safe)
  finalize   assemble report.md / evidence_map.json and copy work notes
  selfcheck  validate result.json and report six-point coverage

Common options (defaults match the runtime contract):
  --input  INPUT_DIR    default /input
  --output OUTPUT_DIR   default /app/output
  --work   WORK_DIR     default /app/work

Commands must run from the Skill root directory; scripts locate themselves via
os.path.dirname(__file__) and never depend on the current working directory.
"""

import argparse
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resultlib as R  # noqa: E402


# ---------------------------------------------------------------------------
# Argument / path plumbing
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# run_state helpers
# ---------------------------------------------------------------------------

def load_run_state(args):
    state, _errs, _degs = R.load_json(work_path(args, "run_state.json"))
    return state if isinstance(state, dict) else None


def read_work_texts(args):
    texts = {}
    for name in ("decomposition.md", "search_plan.md", "synthesis.md"):
        text, _errs, _degs = R.read_text(work_path(args, name))
        texts[name] = text or ""
    return texts


def read_cards(args):
    cards, errs, degs = [], [], []
    path = work_path(args, "evidence_cards.jsonl")
    if not os.path.exists(path):
        return cards, errs, degs
    text, e2, d2 = R.read_text(path)
    errs.extend(e2)
    degs.extend(d2)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            errs.append("bad evidence card line in %s: %s" % (path, exc))
            continue
        if isinstance(item, dict):
            cards.append(item)
        elif isinstance(item, list):
            for row in item:
                if isinstance(row, dict):
                    cards.append(row)
                else:
                    errs.append("non-object evidence card in %s: %r" % (path, row))
        else:
            errs.append("non-object evidence card in %s: %r" % (path, item))
    return cards, errs, degs


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_discover(args):
    print(json.dumps(R.discover_input(args.input), ensure_ascii=False, indent=2))


def _load_case(args):
    inv = R.discover_input(args.input)
    case_obj, errs, degs = None, list(inv.get("errors", [])), []
    if inv.get("case_json"):
        case_obj, e2, d2 = R.load_json(inv["case_json"])
        errs.extend(e2)
        degs.extend(d2)
    return inv, case_obj, errs, degs


def cmd_skeleton(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    inv, case_obj, errs, degs = _load_case(args)
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)
    contract["errors"] = list(contract.get("errors", [])) + list(inv.get("errors", []))

    result_path = out_path(args, "result.json")
    summary = {
        "case_id": case_id,
        "contract_key": contract.get("key"),
        "contract_fields": list(contract.get("fields", [])),
        "contract_errors": list(contract.get("errors", [])),
        "wrote": False,
        "already_exists": os.path.exists(result_path),
        "errors": [],
        "degradations": [],
    }

    if os.path.exists(result_path):
        result, e2, d2 = R.load_json(result_path)
        if isinstance(result, dict):
            # Incremental: keep existing content, backfill any missing contract field.
            R._backfill_contract(result, contract)
            result.setdefault("change_log", []).append({
                "ts": R.iso_now(), "action": "skeleton",
                "detail": "re-opened existing result.json; contract fields re-checked",
            })
            summary["errors"].extend(R.write_json(result_path, result))
            summary["wrote"] = True
        else:
            note = "repaired unreadable result.json"
            result = R.new_skeleton(case_id, contract, note=note)
            result["errors"] = list(e2)
            result["degradations"] = list(d2)
            summary["errors"].extend(R.write_json(result_path, result))
            summary["wrote"] = True
    else:
        note = "partial skeleton before research begins"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv.get("exists") or inv.get("total_files", 0) == 0 \
                or not inv.get("case_json") or not inv.get("task_md"):
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "missing case.json/task.md/materials under %s" % args.input)
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["errors"].extend(R.write_json(result_path, result))
        summary["wrote"] = True

    state = {
        "case_id": case_id,
        "contract": {"key": contract.get("key"),
                     "fields": list(contract.get("fields", [])),
                     "specs": contract.get("specs", {})},
        "inventory": inv,
        "started_at": R.epoch_now(),
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
    }
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    summary["errors"].extend(contract.get("errors", []))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_contract(args):
    _inv, case_obj, _errs, _degs = _load_case(args)
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)
    print(json.dumps({
        "case_id": R.extract_case_id(case_obj) if isinstance(case_obj, dict) else "",
        "contract_key": contract.get("key"),
        "fields": list(contract.get("fields", [])),
        "specs": contract.get("specs", {}),
        "errors": contract.get("errors", []),
    }, ensure_ascii=False, indent=2))


def cmd_merge(args):
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False,
                          "errors": errs or ["payload is not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result_path = out_path(args, "result.json")
    result, e2, d2 = R.load_json(result_path)
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        contract = {"key": None,
                    "fields": list((state.get("contract") or {}).get("fields", [])),
                    "specs": (state.get("contract") or {}).get("specs", {})}
        case_id = state.get("case_id") or R.extract_case_id(payload)
        result = R.new_skeleton(case_id, contract, note="created during merge")
    state = load_run_state(args) or {}
    contract = {"key": (state.get("contract") or {}).get("key"),
                "fields": list((state.get("contract") or {}).get("fields", [])),
                "specs": (state.get("contract") or {}).get("specs", {})}
    R.deep_merge(result, payload)
    R._backfill_contract(result, contract)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2))


def cmd_finalize(args):
    result_path = out_path(args, "result.json")
    result, errs, degs = R.load_json(result_path)
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        contract = {"key": None,
                    "fields": list((state.get("contract") or {}).get("fields", [])),
                    "specs": (state.get("contract") or {}).get("specs", {})}
        result = R.new_skeleton(state.get("case_id", ""), contract,
                                note="created during finalize")
    state = load_run_state(args) or {}
    contract = {"key": (state.get("contract") or {}).get("key"),
                "fields": list((state.get("contract") or {}).get("fields", [])),
                "specs": (state.get("contract") or {}).get("specs", {})}
    R._backfill_contract(result, contract)

    started = state.get("started_at")
    if isinstance(started, (int, float)):
        result["runtime_seconds"] = max(0.0, round(R.epoch_now() - started, 2))
    else:
        result["runtime_seconds"] = 0.0

    # Contract audit: explicit proof of field alignment (the differentiator).
    present = sorted(contract.get("fields", []))
    extra = sorted(k for k in result.keys()
                   if k not in present and k != "case_id"
                   and k not in R.STANDARD_FIELDS)
    result["contract_audit"] = {
        "contract_key": contract.get("key"),
        "present_contract_fields": present,
        "missing_contract_fields": sorted(
            f for f in present if f not in result),
        "extra_fields": extra,
        "checked_at": R.iso_now(),
    }
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "finalize",
        "detail": "runtime and contract audit written",
    })
    errs.extend(R.write_json(result_path, result))

    work_texts = read_work_texts(args)
    cards, card_errs, card_degs = read_cards(args)
    errs.extend(card_errs)
    degs.extend(card_degs)
    if cards:
        # Evidence cards are authoritative for evidence_map.json when present.
        result["evidence_matrix"] = list(cards)
        R.write_json(result_path, result)

    report = build_report(result, contract, work_texts)
    errs.extend(R.write_text(out_path(args, "report.md"), report))
    errs.extend(R.write_json(out_path(args, "evidence_map.json"), {
        "case_id": result.get("case_id"),
        "research_product": result.get("research_product", {}),
        "evidence_matrix": result.get("evidence_matrix", []),
        "sources": result.get("sources", []),
        "uncertainty": result.get("uncertainty", {}),
        "pending_human": result.get("pending_human", []),
        "decomposition": work_texts.get("decomposition.md", ""),
        "search_plan": work_texts.get("search_plan.md", ""),
    }))
    for name, text in work_texts.items():
        if text:
            errs.extend(R.write_text(out_path(args, name), text))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs},
                     ensure_ascii=False, indent=2))


def build_report(result, contract, work_texts):
    rp = result.get("research_product", {})
    rp = rp if isinstance(rp, dict) else {}
    conclusions = rp.get("conclusions", [])
    conclusions = conclusions if isinstance(conclusions, list) else []
    ev = result.get("evidence_matrix", [])
    ev = ev if isinstance(ev, list) else []
    unc = result.get("uncertainty", {})
    unc = unc if isinstance(unc, dict) else {}
    lines = [
        "# Deep-research report",
        "",
        "- case_id: `%s`" % (result.get("case_id") or "(empty)"),
        "- status: `%s`" % result.get("status"),
        "- research type: `%s`" % rp.get("type", "synthesis"),
        "- contract key: `%s`" % contract.get("key"),
        "- conclusions: %d, evidence rows: %d" % (len(conclusions), len(ev)),
        "- runtime_seconds: %s" % result.get("runtime_seconds"),
        "",
        "## Research question",
        "",
        str(rp.get("question") or "_not recorded_"),
        "",
        "## Summary",
        "",
        str(rp.get("summary") or "_not recorded_"),
        "",
        "## Conclusions",
        "",
    ]
    if conclusions:
        for c in conclusions:
            if not isinstance(c, dict):
                continue
            lines.append("- **[%s]** %s  _(strength=%s, evidence=%s)_"
                         % (c.get("id", "?"), c.get("text", ""),
                            c.get("strength", "?"),
                            ",".join(c.get("evidence_ids", [])) or "none"))
    else:
        lines.append("_no conclusions recorded_")
    lines += ["", "## Uncertainty", ""]
    for section in ("conflicts", "biases", "gaps", "undetermined"):
        items = unc.get(section, [])
        items = items if isinstance(items, list) else []
        lines.append("- **%s**: %d item(s)" % (section, len(items)))
    lines += ["", "## Gaps / pending human", ""]
    ph = result.get("pending_human", [])
    ph = ph if isinstance(ph, list) else []
    if ph:
        for item in ph:
            lines.append("- %s" % item)
    else:
        lines.append("_none recorded_")
    lines += ["", "## Limitations", ""]
    lim = result.get("limitations", [])
    lim = lim if isinstance(lim, list) else []
    if lim:
        for item in lim:
            lines.append("- %s" % item)
    else:
        lines.append("_none recorded_")
    if result.get("errors"):
        lines += ["", "## Errors", ""]
        for item in result["errors"]:
            lines.append("- %s" % item)
    if result.get("degradations"):
        lines += ["", "## Degradations", ""]
        for item in result["degradations"]:
            lines.append("- %s" % item)
    return "\n".join(lines) + "\n"


def cmd_selfcheck(args):
    result_path = out_path(args, "result.json")
    result, load_errs, _degs = R.load_json(result_path)
    state = load_run_state(args) or {}
    contract = {"key": (state.get("contract") or {}).get("key"),
                "fields": list((state.get("contract") or {}).get("fields", [])),
                "specs": (state.get("contract") or {}).get("specs", {})}
    if not isinstance(result, dict):
        report = {"ok": False, "load_errors": load_errs,
                  "validation": ["result.json missing or unparseable"],
                  "coverage": []}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    validation = R.validate_result(result, contract)
    coverage = R.six_point(result, read_work_texts(args))
    ok = not validation and all(c["ok"] for c in coverage)
    print(json.dumps({
        "ok": ok,
        "load_errors": load_errs,
        "validation_issues": validation,
        "coverage": coverage,
        "contract_fields": list(contract.get("fields", [])),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Contract-first deep-research runtime helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="print input inventory")
    add_common(p)

    p = sub.add_parser("skeleton", help="write early valid result.json")
    add_common(p)

    p = sub.add_parser("contract", help="print normalized output_contract")
    add_common(p)

    p = sub.add_parser("merge", help="merge a payload into result.json")
    p.add_argument("--payload", required=True, help="path to payload JSON")
    add_common(p)

    p = sub.add_parser("finalize", help="assemble report and evidence map")
    add_common(p)

    p = sub.add_parser("selfcheck", help="validate result.json + six-point check")
    add_common(p)

    args = parser.parse_args(argv)
    handlers = {
        "discover": cmd_discover,
        "skeleton": cmd_skeleton,
        "contract": cmd_contract,
        "merge": cmd_merge,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
