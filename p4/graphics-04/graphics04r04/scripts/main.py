#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — scix-evidence-figure-skill 入口。

子命令：
  init      写 result.json 骨架（in_progress，含首条 change_log）
  build     IR -> 分层可编辑 SVG
  check     IR/SVG 自检，写 checks.json
  finalize  写最终状态与结束时间
  run       端到端：发现输入 -> 骨架 -> (若有 ir.json) build+check -> finalize

仅依赖标准库；相对路径从本脚本所在目录解析。缺参数默认指向 /input、/app/output、/app/work。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def cmd_init(args):
    sk = C.make_skeleton(args.case_id, title=args.title, kind=args.kind)
    C.write_json(args.out, sk)
    print(json.dumps({"ok": True, "case_id": args.case_id, "status": sk["status"],
                      "path": args.out}, ensure_ascii=False))
    return 0


def cmd_build(args):
    ir, err = C.load_json(args.ir)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    svg_text, meta = C.build_svg(ir, args.svg)
    print(json.dumps({"ok": True, "svg": args.svg, "meta": meta}, ensure_ascii=False))
    return 0


def cmd_check(args):
    ir, err = C.load_json(args.ir)
    if err:
        checks = [{"severity": "critical", "code": "ir_load_failed", "msg": "IR 读取失败", "detail": str(err)}]
    else:
        checks = C.validate(ir, args.svg)
    summary = C.summarize_checks(checks)
    payload = {"ok": summary["pass"], "summary": summary, "checks": checks}
    C.write_json(args.out, payload)
    print(json.dumps({"ok": summary["pass"], "summary": summary, "out": args.out}, ensure_ascii=False))
    return 0 if summary["pass"] else 2


def cmd_finalize(args):
    cur = C.finalize_result(args.result, args.status)
    print(json.dumps({"ok": True, "status": cur["status"], "path": args.result}, ensure_ascii=False))
    return 0


def cmd_run(args):
    """端到端：发现输入、写骨架、若有 IR 则 build+check、诚实 finalize。"""
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    found, err = C.discover_input(args.input)
    case_id = "unknown"
    title = ""
    kind = None
    degradations = []
    output_contract = None
    if found.get("case_json"):
        case, e = C.load_json(found["case_json"])
        if e:
            degradations.append(e)
        elif isinstance(case, dict):
            case_id = case.get("case_id") or case.get("id") or case.get("challenge_id") or "unknown"
            title = case.get("title") or case.get("content_title") or ""
            kind = case.get("kind")
            output_contract = case.get("output_contract")
    else:
        degradations.append({"error": "case_json_missing", "path": args.input})
    sk = C.make_skeleton(case_id, title=title, kind=kind or "apparatus")
    sk["degradations"] = degradations
    # 原样回填 output_contract 字段（缺值给 null，不改名、不删未知键）
    if isinstance(output_contract, dict):
        for k, v in output_contract.items():
            sk[k] = v if v is not None else None
    srcs = [{"file": f, "role": "material"} for f in found["materials"]]
    if found.get("case_json"):
        srcs.append({"file": found["case_json"], "role": "case_definition"})
    if found.get("task_md"):
        srcs.append({"file": found["task_md"], "role": "task"})
    sk["sources"] = srcs
    C.write_json(result_path, sk)

    ir_path = os.path.join(args.work, "ir.json")
    svg_path = os.path.join(args.work, "figure.svg")
    checks_path = os.path.join(args.work, "checks.json")
    if os.path.exists(ir_path):
        ir, e = C.load_json(ir_path)
        if e:
            sk["errors"].append(e)
        else:
            _, meta = C.build_svg(ir, svg_path)
            checks = C.validate(ir, svg_path)
            summary = C.summarize_checks(checks)
            C.write_json(checks_path, {"summary": summary, "checks": checks})
            sk = C.merge_result(result_path, {
                "entities": ir.get("entities", []),
                "relations": ir.get("relations", []),
                "data_encoding": ir.get("data_encoding", []),
                "checks": checks,
            })
            sk["editable_sources"] = [{"path": svg_path, "format": "svg", "layers": 4,
                                       "notes": "分层可编辑源文件；每图元含 data-source-ref/data-confidence"}]
            sk["artifacts"] = [{"path": svg_path, "role": "editable_svg"}]
            C.write_json(result_path, sk)
    # 诚实判定状态
    if not found["case_json"] and not found["materials"]:
        status = "insufficient_evidence"
        sk["limitations"].append({"code": "empty_input", "msg": "输入目录无 case.json 与材料，无法出图"})
    elif os.path.exists(svg_path):
        status = "partial"  # 未做 PNG/PDF 渲染或未人工复核时保持 partial
    else:
        status = "partial"
    C.finalize_result(result_path, status)
    print(json.dumps({"ok": True, "case_id": case_id, "status": status, "result": result_path},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="证据绑定科研绘图入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="写 result.json 骨架")
    p.add_argument("--case-id", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--kind", default="apparatus")
    p.add_argument("--out", default="/app/output/result.json")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("build", help="IR -> SVG")
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/work/figure.svg")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("check", help="自检")
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/work/figure.svg")
    p.add_argument("--out", default="/app/work/checks.json")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("finalize", help="写最终状态")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--status", default="partial")
    p.set_defaults(fn=cmd_finalize)

    p = sub.add_parser("run", help="端到端")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.set_defaults(fn=cmd_run)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
