#!/usr/bin/env python3
"""开题报告与研究计划书 Skill 的命令行入口。

子命令：
  init   读取 case.json → 落盘诚实 partial 骨架（前 3 分钟必须先做）
  load   扫描材料目录 → 生成 sources 骨架清单（不假设 PDF 内容）
  check  校验 result.json（契约 + 七要素 + 证据溯源）
  render 渲染 proposal.md / evidence_map.json / route.mmd / route.svg
  merge  把 --patch JSON 浅层合并进 result.json（增量改进用）
  run    init → load → check → render 全流水线（自测/演示用）

公共参数 --input / --output / --work 缺省指向 /input、/app/output、/app/work，
可出现在子命令之后。所有命令退出码 0 表示“已产出合法产物或已记录降级”，
不因材料缺失崩溃；未预期异常被捕获后返回 2 并打印原因。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import proposal  # noqa: E402


def cmd_init(args):
    case, err = proposal.extract_case(args.case)
    if err:
        # case.json 缺失/坏 JSON：仍落盘 input_error 骨架，绝不崩溃。
        print(json.dumps({"case_error": err}, ensure_ascii=False))
        case = None
    result = proposal.build_skeleton(case, args.case)
    proposal.write_json(args.result, result)
    print(json.dumps({
        "case_id": result["case_id"], "status": result["status"],
        "result": args.result, "note": "partial skeleton saved; improve incrementally",
    }, ensure_ascii=False))
    return 0


def cmd_load(args):
    materials, degradations = proposal.discover_materials(args.input)
    sources = []
    for m in materials:
        sources.append({
            "id": "SRC%03d" % (len(sources) + 1),
            "locator": m["locator"],
            "quote": None,
            "kind": "fact",
            "material_kind": m["kind"],
            "note": m.get("note", ""),
            "size": m.get("size"),
        })
    report = {"materials": materials, "degradations": degradations,
              "sources": sources, "count": len(sources)}
    proposal.write_json(args.sources_out, report)
    print(json.dumps({"count": len(sources), "degradations": len(degradations),
                      "sources_out": args.sources_out}, ensure_ascii=False))
    return 0


def cmd_check(args):
    result, err = proposal.load_result_path(args.result)
    if err:
        print(json.dumps({"fail": ["无法读取 result.json: %s" % err],
                          "warn": [], "ok": False}, ensure_ascii=False))
        return 1
    report = proposal.validate(result)
    proposal.write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def cmd_render(args):
    result, err = proposal.load_result_path(args.result)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    artifacts, degradations = proposal.render(result, args.output)
    result["artifacts"] = artifacts
    if degradations:
        result.setdefault("degradations", []).extend(degradations)
    result.setdefault("change_log", []).append({
        "at": proposal.now_iso(), "action": "render artifacts",
        "reason": "生成可编辑产物与预览", "count": len(artifacts),
    })
    proposal.write_json(args.result, result)
    print(json.dumps({"artifacts": artifacts, "degradations": degradations},
                     ensure_ascii=False))
    return 0


def cmd_merge(args):
    result, err = proposal.load_result_path(args.result)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    patch, perr = proposal.read_json(args.patch)
    if perr:
        print(json.dumps({"ok": False, "error": "patch:%s" % perr}, ensure_ascii=False))
        return 1
    if not isinstance(patch, dict):
        print(json.dumps({"ok": False, "error": "patch 不是对象"}, ensure_ascii=False))
        return 1
    # 浅层合并：信封字段按 key 覆盖；列表字段追加；proposal 内部按元素替换。
    for key, value in patch.items():
        if key == "proposal" and isinstance(value, dict) and isinstance(result.get("proposal"), dict):
            for ek, ev in value.items():
                result["proposal"][ek] = ev
        elif key in ("sources", "uncertainty", "pending_human", "change_log",
                     "errors", "degradations") and isinstance(value, list) \
                and isinstance(result.get(key), list):
            result[key].extend(value)
        else:
            result[key] = value
    result.setdefault("change_log", []).append({
        "at": proposal.now_iso(), "action": "merge patch",
        "reason": "增量回填（不整体重写）", "patch": os.path.basename(args.patch),
    })
    proposal.write_json(args.result, result)
    print(json.dumps({"ok": True, "merged_keys": sorted(patch.keys())},
                     ensure_ascii=False))
    return 0


def cmd_run(args):
    """init → load → check → render 全流水线（自测/演示）。"""
    rc_init = cmd_init(args)
    rc_load = cmd_load(args)
    rc_check = cmd_check(args)
    rc_render = cmd_render(args)
    print(json.dumps({"init": rc_init, "load": rc_load,
                      "check": rc_check, "render": rc_render}, ensure_ascii=False))
    return 0 if (rc_init == 0 and rc_render == 0) else 1


def build_parser():
    ap = argparse.ArgumentParser(
        prog="proposal",
        description="开题报告与研究计划书 Skill：init/load/check/render/merge/run")

    # 公共参数（作为 parent，各子命令均接受，缺省指向运行目录）。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=proposal.DEFAULT_INPUT,
                        help="输入目录（缺省 /input）")
    common.add_argument("--output", default=proposal.DEFAULT_OUTPUT,
                        help="输出目录（缺省 /app/output）")
    common.add_argument("--work", default=proposal.DEFAULT_WORK,
                        help="工作目录（缺省 /app/work）")

    sub = ap.add_subparsers(dest="command", required=True)

    def input_case_default():
        return os.path.join(proposal.DEFAULT_INPUT, "case.json")

    def result_default():
        return os.path.join(proposal.DEFAULT_OUTPUT, "result.json")

    def sources_default():
        return os.path.join(proposal.DEFAULT_WORK, "sources.json")

    def report_default():
        return os.path.join(proposal.DEFAULT_WORK, "check_report.json")

    p_init = sub.add_parser("init", parents=[common],
                            help="落盘 partial 骨架")
    p_init.add_argument("--case", default=input_case_default())
    p_init.add_argument("--result", default=result_default())
    p_init.set_defaults(func=cmd_init)

    p_load = sub.add_parser("load", parents=[common],
                            help="扫描材料生成 sources 骨架")
    p_load.add_argument("--sources-out", default=sources_default())
    p_load.set_defaults(func=cmd_load)

    p_check = sub.add_parser("check", parents=[common],
                             help="校验 result.json")
    p_check.add_argument("--result", default=result_default())
    p_check.add_argument("--report", default=report_default())
    p_check.set_defaults(func=cmd_check)

    p_render = sub.add_parser("render", parents=[common],
                              help="渲染可编辑产物与预览")
    p_render.add_argument("--result", default=result_default())
    p_render.set_defaults(func=cmd_render)

    p_merge = sub.add_parser("merge", parents=[common],
                             help="把 patch JSON 增量合并进 result.json")
    p_merge.add_argument("--patch", required=True)
    p_merge.add_argument("--result", default=result_default())
    p_merge.set_defaults(func=cmd_merge)

    p_run = sub.add_parser("run", parents=[common],
                           help="init→load→check→render 全流水线")
    p_run.add_argument("--case", default=input_case_default())
    p_run.add_argument("--result", default=result_default())
    p_run.add_argument("--sources-out", default=sources_default())
    p_run.add_argument("--report", default=report_default())
    p_run.set_defaults(func=cmd_run)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 —— 未预期异常不让进程崩溃
        print(json.dumps({"ok": False, "unexpected_error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
