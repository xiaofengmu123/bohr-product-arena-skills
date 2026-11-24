#!/usr/bin/env python3
"""Command-line entry point for scix-figure-contract-skill.

Subcommands:
  init      write a result.json skeleton (pre-populates every contract field)
  build     build a layered SVG from an IR JSON file
  validate  validate the SVG against the IR and write checks.json
  merge     merge a partial JSON object into result.json
  finalize  set final status and finished timestamp
  run       run the full deterministic pipeline (init -> build -> validate -> merge -> finalize)

Common flags (accepted by every subcommand, before or after it):
  --case /input/case.json   --out /app/output   --work /app/work
  --case-id <id>            --title <title>
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as ci

COMMON = dict(
    case=dict(flag="--case", default="/input/case.json", help="case.json 路径"),
    out=dict(flag="--out", default="/app/output", help="输出目录"),
    work=dict(flag="--work", default="/app/work", help="工作目录"),
    case_id=dict(flag="--case-id", default="", help="覆盖 case_id"),
    title=dict(flag="--title", default="", help="图题"),
)


def add_common(parser):
    for spec in COMMON.values():
        parser.add_argument(spec["flag"], default=spec["default"], help=spec["help"])
    return parser


def _resolve(args):
    out_dir = args.out
    work_dir = args.work
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_path = args.case
    case = ci.read_json(case_path) if os.path.isfile(case_path) else None
    fields = ci.contract_field_names(case)
    cid = ci.case_id_of(case) or args.case_id or os.path.basename(os.path.dirname(os.path.abspath(case_path)))
    result_path = os.path.join(out_dir, "result.json")
    return case, fields, cid, result_path, work_dir, out_dir


def cmd_init(args):
    _, fields, cid, result_path, _, _ = _resolve(args)
    mgr = ci.ResultManager(result_path, case_id=cid, contract_fields=fields, title=args.title)
    mgr.init_skeleton()
    print(json.dumps({"ok": True, "result": result_path, "case_id": cid,
                      "contract_fields": fields}, ensure_ascii=False))
    return result_path


def cmd_build(args):
    ir = ci.read_json(args.ir)
    if not isinstance(ir, dict):
        print(json.dumps({"ok": False, "error": "ir.json 不可解析或非对象"}, ensure_ascii=False))
        return None
    info = ci.build_svg(ir, args.svg)
    print(json.dumps({"ok": True, "svg": info["path"], "bytes": info["bytes"]}, ensure_ascii=False))
    return info


def cmd_validate(args):
    ir = ci.read_json(args.ir)
    checks = ci.validate_svg(ir if isinstance(ir, dict) else {}, args.svg, args.checks)
    print(json.dumps({"ok": checks.get("ok"), "critical": len(checks.get("critical", []))}, ensure_ascii=False))
    return checks


def cmd_merge(args):
    _, fields, cid, result_path, _, _ = _resolve(args)
    partial = ci.read_json(args.partial)
    if not isinstance(partial, dict):
        print(json.dumps({"ok": False, "error": "partial 文件不可解析或非对象"}, ensure_ascii=False))
        return None
    mgr = ci.ResultManager(result_path, case_id=cid, contract_fields=fields)
    obj = mgr.merge(partial)
    print(json.dumps({"ok": True, "result": result_path}, ensure_ascii=False))
    return obj


def cmd_finalize(args):
    _, fields, cid, result_path, _, _ = _resolve(args)
    mgr = ci.ResultManager(result_path, case_id=cid, contract_fields=fields)
    obj = mgr.finalize(args.status)
    print(json.dumps({"ok": True, "status": obj.get("status")}, ensure_ascii=False))
    return obj


def cmd_run(args):
    _, fields, cid, result_path, work_dir, out_dir = _resolve(args)
    mgr = ci.ResultManager(result_path, case_id=cid, contract_fields=fields)
    mgr.init_skeleton()

    ir_path = args.ir or os.path.join(work_dir, "ir.json")
    svg_path = args.svg or os.path.join(out_dir, "figure.svg")
    checks_path = os.path.join(work_dir, "checks.json")

    partial = {}
    status = "partial"
    if os.path.isfile(ir_path):
        ir = ci.read_json(ir_path)
        if isinstance(ir, dict):
            try:
                ci.build_svg(ir, svg_path)
            except Exception as exc:  # noqa: BLE001 - honest degradation
                partial["limitations"] = [{"scope": "build_svg", "detail": str(exc)}]
                mgr.merge(partial)
            checks = ci.validate_svg(ir, svg_path, checks_path)
            checks["contract_fields_expected"] = fields
            partial["title"] = ir.get("title") or args.title
            partial["figure_kind"] = ir.get("figure_kind")
            partial["checks"] = [checks]
            partial["sources"] = ci.build_sources(ir)
            partial["editable_sources"] = [{
                "path": os.path.basename(svg_path), "format": "svg",
                "layers": ["layer-relations", "layer-entities", "layer-annotations", "layer-legend"],
                "notes": "分层可编辑源文件",
            }]
            if isinstance(ir.get("entities"), list):
                partial["entities"] = ir["entities"]
            if isinstance(ir.get("relations"), list):
                partial["relations"] = ir["relations"]
            if isinstance(ir.get("data_encoding"), list):
                partial["data_encoding"] = ir["data_encoding"]
            if isinstance(ir.get("legend_annotations"), list) or isinstance(ir.get("legend"), list):
                partial["legend_annotations"] = ir.get("legend_annotations", ir.get("legend", []))
            partial["exports"] = ci.build_exports_list(svg_path)
            if checks.get("ok"):
                status = "complete"
    else:
        partial["limitations"] = [{"scope": "ir", "detail": "ir.json 缺失，未生成图稿；仅交付骨架"}]

    mgr.merge(partial)
    mgr.finalize(status)

    # final contract alignment check against what was actually written
    written = ci.read_json(result_path)
    cc = ci.validate_contract(written, fields)
    if not cc.get("ok"):
        mgr.merge({"limitations": (written.get("limitations") or []) +
                   [{"scope": "contract", "detail": "缺失字段: " + ",".join(cc.get("missing", []))}]})
    print(json.dumps({"ok": True, "status": status, "result": result_path,
                      "contract_fields": fields, "contract_align": cc}, ensure_ascii=False))
    return result_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="scix-figure-contract-skill 入口")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init")
    add_common(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build")
    add_common(p)
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/output/figure.svg")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("validate")
    add_common(p)
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/output/figure.svg")
    p.add_argument("--checks", default="/app/work/checks.json")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("merge")
    add_common(p)
    p.add_argument("--partial", default="/app/work/partial.json")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("finalize")
    add_common(p)
    p.add_argument("--status", default="partial")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("run")
    add_common(p)
    p.add_argument("--ir", default="")
    p.add_argument("--svg", default="")
    p.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    try:
        args.func(args)
        return 0
    except Exception as exc:  # noqa: BLE001 - never fail silently
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
