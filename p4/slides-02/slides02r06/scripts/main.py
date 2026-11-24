#!/usr/bin/env python3
"""main.py — traceable-slide-builder 命令行入口。

调用路径相对 Skill 根目录（运行时 /skills/weekly-skill/）：
  python3 scripts/main.py scaffold
  python3 scripts/main.py record --kind evidence --data '{...}'
  python3 scripts/main.py check-quotes --source <txt> --evidence <json>
  python3 scripts/main.py validate
  python3 scripts/main.py finalize --status complete|partial|failed

目录默认 /input、/app/work、/app/output，可用 INPUT_DIR/WORK_DIR/OUTPUT_DIR
环境变量覆盖；脚本只依赖 Python 标准库，不依赖当前工作目录。
"""

import argparse
import json
import os
import sys

import core_impl as C


def _dir_arg(flag, default):
    return os.path.abspath(flag) if flag else default


def cmd_scaffold(args):
    input_dir = _dir_arg(args.input, C.DEFAULT_INPUT)
    work_dir = _dir_arg(args.work, C.DEFAULT_WORK)
    output_dir = _dir_arg(args.output, C.DEFAULT_OUTPUT)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case = C.load_json(os.path.join(input_dir, "case.json")) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown"
    contract = case.get("output_contract") or {}
    files = C.discover_input(input_dir)

    result = C.scaffold_result(case_id, contract)
    result.setdefault("change_log", []).append({
        "t": C.now_iso(), "step": "scaffold",
        "action": "写 result.json 骨架与中间产物空骨架",
        "status": "partial", "reason": "先落盘合法 partial 结果",
    })
    C.write_json(os.path.join(output_dir, "result.json"), result)
    C.write_json(os.path.join(work_dir, "case_summary.json"), C.empty_case_summary(case_id, list(contract.keys()), files))
    C.write_json(os.path.join(work_dir, "plan.json"), C.empty_plan())
    C.write_json(os.path.join(work_dir, "figure_model.json"), C.empty_figure_model())
    C.write_json(os.path.join(work_dir, "evidence_map.json"), C.empty_evidence_map())
    C.write_json(os.path.join(work_dir, "version_records.json"), C.empty_version_records())

    print(json.dumps({
        "ok": True, "case_id": str(case_id),
        "contract_keys": list(contract.keys()),
        "input_file_count": len(files),
        "result": os.path.join(output_dir, "result.json"),
    }, ensure_ascii=False))


def cmd_record(args):
    work_dir = _dir_arg(args.work, C.DEFAULT_WORK)
    data = {}
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": "--data 不是合法 JSON: %s" % exc}, ensure_ascii=False))
            sys.exit(2)
    try:
        path = C.record(args.kind, data, work_dir)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps({"ok": True, "kind": args.kind, "wrote": path}, ensure_ascii=False))


def cmd_check_quotes(args):
    work_dir = _dir_arg(args.work, C.DEFAULT_WORK)
    source = C.read_text_file(args.source)
    if source is None:
        print(json.dumps({"ok": False, "error": "无法读取源文件 %s" % args.source}, ensure_ascii=False))
        sys.exit(2)
    em = C.load_json(args.evidence) or {}
    evidence_list = em.get("evidence") or []
    mismatches = C.verify_quotes(source, evidence_list)
    quote_check = {
        "generated": C.now_iso(),
        "source": args.source,
        "total": len(evidence_list),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "passed": len(mismatches) == 0,
    }
    C.write_json(os.path.join(work_dir, "quote_check.json"), quote_check)
    print(json.dumps({"ok": len(mismatches) == 0, "total": len(evidence_list), "mismatch_count": len(mismatches), "mismatches": mismatches}, ensure_ascii=False, indent=2))
    sys.exit(1 if mismatches else 0)


def cmd_validate(args):
    input_dir = _dir_arg(args.input, C.DEFAULT_INPUT)
    work_dir = _dir_arg(args.work, C.DEFAULT_WORK)
    output_dir = _dir_arg(args.output, C.DEFAULT_OUTPUT)
    self_check, passed = C.run_validate(input_dir, work_dir, output_dir)
    print(json.dumps({
        "ok": passed,
        "error_count": self_check["error_count"],
        "warning_count": self_check["warning_count"],
        "self_check": os.path.join(work_dir, "self_check.json"),
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if passed else 1)


def cmd_finalize(args):
    input_dir = _dir_arg(args.input, C.DEFAULT_INPUT)
    work_dir = _dir_arg(args.work, C.DEFAULT_WORK)
    output_dir = _dir_arg(args.output, C.DEFAULT_OUTPUT)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    result = C.load_json(os.path.join(output_dir, "result.json")) or {}
    if not result:
        case = C.load_json(os.path.join(input_dir, "case.json")) or {}
        result = C.scaffold_result(
            case.get("case_id") or case.get("id") or "unknown",
            case.get("output_contract") or {},
        )
    plan = C.load_json(os.path.join(work_dir, "plan.json")) or {}
    fm = C.load_json(os.path.join(work_dir, "figure_model.json")) or {}
    em = C.load_json(os.path.join(work_dir, "evidence_map.json")) or {}
    artifacts = C.detect_artifacts(output_dir)

    result = C.finalize(result, plan, fm, em, artifacts, args.status)
    result.setdefault("change_log", []).append({
        "t": C.now_iso(), "step": "finalize",
        "action": "写终版 status=%s" % args.status,
        "status": args.status, "reason": "终检并合并中间产物",
    })
    if "runtime" not in result or not isinstance(result.get("runtime"), dict):
        result["runtime"] = {}
    result["runtime"].setdefault("finalized_at", C.now_iso())
    C.write_json(os.path.join(output_dir, "result.json"), result)

    self_check, passed = C.run_validate(input_dir, work_dir, output_dir)
    print(json.dumps({
        "ok": passed,
        "status": args.status,
        "error_count": self_check["error_count"],
        "result": os.path.join(output_dir, "result.json"),
        "self_check": os.path.join(work_dir, "self_check.json"),
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if passed else 1)


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="traceable-slide-builder")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="落 result.json 骨架与中间产物空骨架")
    p.add_argument("--input"); p.add_argument("--work"); p.add_argument("--output")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("record", help="增量写入一条中间表示记录")
    p.add_argument("--kind", required=True, choices=["evidence", "figure", "plan"])
    p.add_argument("--data", required=True, help="单条 JSON 记录")
    p.add_argument("--work")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("check-quotes", help="逐字核对 evidence_map 的 quote 是否在源文中")
    p.add_argument("--source", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--work")
    p.set_defaults(func=cmd_check_quotes)

    p = sub.add_parser("validate", help="终检并写 self_check.json，退出码 1=有缺口")
    p.add_argument("--input"); p.add_argument("--work"); p.add_argument("--output")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("finalize", help="合并中间产物写终版 result.json")
    p.add_argument("--status", required=True, choices=["complete", "partial", "failed"])
    p.add_argument("--input"); p.add_argument("--work"); p.add_argument("--output")
    p.set_defaults(func=cmd_finalize)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.input = os.environ.get("INPUT_DIR") or getattr(args, "input", None)
    args.work = os.environ.get("WORK_DIR") or getattr(args, "work", None)
    args.output = os.environ.get("OUTPUT_DIR") or getattr(args, "output", None)
    args.func(args)


if __name__ == "__main__":
    main()
