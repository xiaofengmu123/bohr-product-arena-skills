#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""academic-talk-organizer 入口：bootstrap → 增量 merge → evidence-check → validate。

仅依赖标准库，路径相对本文件所在目录解析。用法：

    python3 scripts/main.py bootstrap  --case /input/case.json --task /input/task.md \
        --work /app/work --output /app/output
    python3 scripts/main.py merge --result /app/output/result.json --work /app/work \
        --set pages='[{"page_index":1,...}]' --note "填充第 1 页"
    python3 scripts/main.py evidence-check --result /app/output/result.json --work /app/work
    python3 scripts/main.py validate --result /app/output/result.json --work /app/work
    python3 scripts/main.py selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def _load_result(result_path, work_dir):
    data, err = core.read_json_safe(result_path)
    if err:
        print("ERROR: %s" % err, file=sys.stderr)
        return None, None
    spec, _ = core.load_contract_spec_file(os.path.join(work_dir, "contract.json"))
    return data, spec


def cmd_bootstrap(args):
    case_data, case_err = core.read_json_safe(args.case)
    if case_err:
        # 缺 case.json 也允许落骨架：status 标 input_error，不崩溃。
        case_data = {}
        print("WARN: %s (continuing with empty case)" % case_err, file=sys.stderr)
    task_text, task_err = core.read_text_safe(args.task)
    if task_err:
        task_text = ""
        print("WARN: %s" % task_err, file=sys.stderr)

    spec, provided = core.build_contract_spec(case_data)
    result = core.bootstrap_result(case_data, task_text, spec, provided)
    if not case_data:
        result["status"] = "input_error"
        result["errors"] = [case_err]

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    out_path = os.path.join(args.output, "result.json")
    core.write_result(out_path, result, spec)
    core.dump_contract(os.path.join(args.work, "contract.json"), spec, provided)

    input_dir = args.input or (os.path.dirname(os.path.abspath(args.case))
                               if os.path.exists(args.case) else core.DEFAULT_INPUT_DIR)
    sources_manifest = {
        "case_json": args.case,
        "task_md": args.task,
        "materials": core.discover_inputs(input_dir)["materials"],
    }
    with open(os.path.join(args.work, "sources.json"), "w", encoding="utf-8") as fh:
        json.dump(sources_manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(json.dumps({"ok": True, "result": out_path,
                      "status": result["status"],
                      "contract_fields": len(spec)}, ensure_ascii=False))
    return 0


def _merge_one(data, key, raw, as_append):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("ERROR: bad JSON for %s: %s" % (key, exc), file=sys.stderr)
        return False
    if as_append:
        if not isinstance(data.get(key), list):
            data[key] = []
        data[key].append(value)
    else:
        data[key] = value
    return True


def cmd_merge(args):
    data, spec = _load_result(args.result, args.work)
    if data is None:
        return 2
    touched = []
    if args.update_file:
        upd, uerr = core.read_json_safe(args.update_file)
        if uerr:
            print("ERROR: %s" % uerr, file=sys.stderr)
            return 2
        core.deep_merge(data, upd)
        touched.extend(list(upd.keys()) if isinstance(upd, dict) else ["update_file"])
    for pair in args.set or []:
        if "=" not in pair:
            print("ERROR: --set expects KEY=JSON_VALUE, got %r" % pair, file=sys.stderr)
            return 2
        key, raw = pair.split("=", 1)
        if not _merge_one(data, key.strip(), raw, False):
            return 2
        touched.append(key.strip())
    for pair in args.append or []:
        if "=" not in pair:
            print("ERROR: --append expects KEY=JSON_VALUE, got %r" % pair, file=sys.stderr)
            return 2
        key, raw = pair.split("=", 1)
        if not _merge_one(data, key.strip(), raw, True):
            return 2
        touched.append(key.strip())
    if args.status:
        data["status"] = args.status
        touched.append("status")
    if touched:
        core.append_change(data, args.actor, "merge", touched, args.note or "")
    core.write_result(args.result, data, spec)
    print(json.dumps({"ok": True, "touched": touched, "status": data.get("status")},
                     ensure_ascii=False))
    return 0


def cmd_evidence_check(args):
    data, _spec = _load_result(args.result, args.work)
    if data is None:
        return 2
    report = core.evidence_check(data, args.work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_validate(args):
    data, spec = _load_result(args.result, args.work)
    if data is None:
        return 2
    report = core.validate(data, spec, strict=args.strict)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_selftest(_args):
    ok, report = core.selftest()
    print(json.dumps({"ok": ok, "report": report}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="academic-talk-organizer 脚本入口（仅标准库）。")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("bootstrap", help="落盘字段齐全的 partial 结果骨架")
    sp.add_argument("--case", default="/input/case.json")
    sp.add_argument("--task", default="/input/task.md")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--input", default=None, help="材料输入目录（默认取 case.json 同目录）")
    sp.set_defaults(func=cmd_bootstrap)

    sp = sub.add_parser("merge", help="增量合并字段并追加 change_log")
    sp.add_argument("--result", default="/app/output/result.json")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--update-file", help="JSON 文件，整体深合并")
    sp.add_argument("--set", action="append", metavar="KEY=JSON", help="设置字段（可重复）")
    sp.add_argument("--append", action="append", metavar="KEY=JSON", help="向数组字段追加元素（可重复）")
    sp.add_argument("--status", help="覆盖 status 字段")
    sp.add_argument("--note", default="", help="本次修改说明")
    sp.add_argument("--actor", default="agent", help="change_log 记录者")
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("evidence-check", help="校验证据 locator + 逐字 quote")
    sp.add_argument("--result", default="/app/output/result.json")
    sp.add_argument("--work", default="/app/work")
    sp.set_defaults(func=cmd_evidence_check)

    sp = sub.add_parser("validate", help="校验 result.json 与 output_contract 字段逐一对齐")
    sp.add_argument("--result", default="/app/output/result.json")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--strict", action="store_true", help="警告也判失败")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("selftest", help="无外部文件的内存端到端自检")
    sp.set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
