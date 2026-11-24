#!/usr/bin/env python3
"""Thin orchestrator for figure-model-skill.

Subcommands (run `python scripts/main.py <cmd> --help`):

  init      read /input/case.json, write a valid result.json skeleton (status=in_progress)
  build     FigureModel IR (/app/work/ir.json) -> layered editable SVG
  validate  contract + IR + SVG three-gate self-check -> /app/work/checks.json
  merge     merge IR + checks into result.json
  finalize  set status and artifact list

Standard library only. Paths default to the runtime contract (/input, /app/work,
/app/output) but are all overridable for local self-testing.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as core  # noqa: E402


def cmd_init(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    case_obj = {}
    load_error = None
    try:
        case_obj = core.load_json(args.case)
    except Exception as ex:  # noqa: BLE001
        load_error = "%s: %s" % (type(ex).__name__, ex)
    case_id = case_obj.get("case_id") or case_obj.get("id") or "unknown"
    keys = core.normalize_contract(case_obj)
    title = case_obj.get("title") or ""
    result = core.init_result(case_id, keys, title)
    if load_error:
        result["limitations"].append({"item": "case.json", "detail": load_error})
    out_path = os.path.join(args.out, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "case_id": case_id, "contract_keys": keys,
                      "result": out_path, "load_error": load_error}, ensure_ascii=False))
    return 0


def cmd_build(args):
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    model = core.load_json(args.ir)
    svg = core.build_svg(model, args.width, args.height)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(json.dumps({"ok": True, "svg": args.out,
                      "bytes": len(svg.encode("utf-8"))}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    gates = []
    keys = list(core.DEFAULT_CONTRACT)
    if os.path.exists(args.result):
        result = core.load_json(args.result)
        if isinstance(result.get("_contract_keys"), list):
            keys = result["_contract_keys"]
        gates.append(core.validate_contract(result, keys))
    else:
        gates.append({"gate": "contract", "critical": ["result.json not found: %s" % args.result],
                      "warnings": [], "pass": False})

    model = None
    if os.path.exists(args.ir):
        model = core.load_json(args.ir)
        gates.append(core.validate_ir(model))
    else:
        gates.append({"gate": "ir", "critical": ["ir.json not found: %s" % args.ir],
                      "warnings": [], "pass": False})

    if os.path.exists(args.svg):
        svg_text, _ = core.read_text(args.svg)
        gates.append(core.validate_svg(svg_text, model or {}))
    else:
        gates.append({"gate": "svg", "critical": ["svg not found: %s" % args.svg],
                      "warnings": [], "pass": False})

    summary = {"gates": gates, "pass": all(g["pass"] for g in gates)}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    brief = [{"gate": g["gate"], "critical": len(g["critical"]), "warnings": len(g["warnings"])}
             for g in gates]
    print(json.dumps({"ok": True, "pass": summary["pass"], "gates": brief}, ensure_ascii=False))
    return 0 if summary["pass"] else 2


def cmd_merge(args):
    os.makedirs(args.out, exist_ok=True)
    result_path = os.path.join(args.out, "result.json")
    if os.path.exists(result_path):
        result = core.load_json(result_path)
    else:
        case_obj = {}
        if os.path.exists(args.case):
            try:
                case_obj = core.load_json(args.case)
            except Exception:  # noqa: BLE001
                case_obj = {}
        result = core.init_result(
            case_obj.get("case_id") or case_obj.get("id") or "unknown",
            core.normalize_contract(case_obj), "")

    model = {}
    if os.path.exists(args.ir):
        model = core.load_json(args.ir)

    updates = {}
    for key in core.DEFAULT_CONTRACT:
        if key in model and model[key] is not None:
            updates[key] = model[key]
    for key in ("sources", "uncertainty", "limitations", "pending_human"):
        if model.get(key):
            updates[key] = model[key]

    checks_path = os.path.join(args.work, "checks.json")
    if os.path.exists(checks_path):
        try:
            summary = core.load_json(checks_path)
            updates["checks"] = summary.get("gates") or summary.get("checks") or []
        except Exception:  # noqa: BLE001
            pass

    result = core.merge_result(result, updates)
    result["change_log"] = list(result.get("change_log") or []) + [
        {"time": core.utc_now(), "action": "merge",
         "detail": "merged FigureModel IR and checks into result.json"}
    ]
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "result": result_path}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    result_path = os.path.join(args.out, "result.json")
    if not os.path.exists(result_path):
        print(json.dumps({"ok": False, "error": "result.json not found: %s" % result_path},
                         ensure_ascii=False))
        return 1
    result = core.load_json(result_path)
    artifacts = []
    for name in sorted(os.listdir(args.out)):
        full = os.path.join(args.out, name)
        if os.path.isfile(full):
            artifacts.append({"path": name, "bytes": os.path.getsize(full)})
    result = core.finalize_result(result, args.status, artifacts)
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "status": result["status"],
                      "artifacts": len(artifacts)}, ensure_ascii=False))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="write result.json skeleton")
    pi.add_argument("--case", default="/input/case.json")
    pi.add_argument("--work", default="/app/work")
    pi.add_argument("--out", default="/app/output")
    pi.set_defaults(func=cmd_init)

    pb = sub.add_parser("build", help="IR -> layered SVG")
    pb.add_argument("--ir", default="/app/work/ir.json")
    pb.add_argument("--out", default="/app/work/figure.svg")
    pb.add_argument("--width", type=int, default=1600)
    pb.add_argument("--height", type=int, default=1000)
    pb.set_defaults(func=cmd_build)

    pv = sub.add_parser("validate", help="three-gate self-check")
    pv.add_argument("--ir", default="/app/work/ir.json")
    pv.add_argument("--result", default="/app/output/result.json")
    pv.add_argument("--svg", default="/app/work/figure.svg")
    pv.add_argument("--out", default="/app/work/checks.json")
    pv.set_defaults(func=cmd_validate)

    pm = sub.add_parser("merge", help="merge IR + checks into result.json")
    pm.add_argument("--case", default="/input/case.json")
    pm.add_argument("--ir", default="/app/work/ir.json")
    pm.add_argument("--work", default="/app/work")
    pm.add_argument("--out", default="/app/output")
    pm.set_defaults(func=cmd_merge)

    pf = sub.add_parser("finalize", help="set status and artifact list")
    pf.add_argument("--out", default="/app/output")
    pf.add_argument("--status", choices=["complete", "partial", "failed", "in_progress"],
                    default="partial")
    pf.set_defaults(func=cmd_finalize)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as ex:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
