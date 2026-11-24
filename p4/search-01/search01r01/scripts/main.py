#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 学术文献检索 Skill 的命令行入口（纯标准库，相对路径可执行）。

子命令：
  run        骨架流水线：skeleton -> parse -> plan -> assemble（不检索）
  skeleton   写一版合法的 partial 骨架 result.json
  parse      解析 /input -> work/case_summary.json
  plan       由 case_summary 生成查询计划 -> work/plan.json
  import     导入候选文献 -> work/records.json（按 DOI/规范化标题 upsert）
  evidence   追加查询/来源证据 -> work/evidence.json
  assemble   去重+打分+排序，写 output/result.json 与 candidates.md、evidence_map.json
  selfcheck  校验 output/result.json 是否满足输出契约

调用：先 cd 到 Skill 根目录，再用相对路径执行 `python3 scripts/main.py run ...`。
"""
import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as impl

DEFAULT_INPUT = impl.DEFAULT_INPUT
DEFAULT_OUTPUT = impl.DEFAULT_OUTPUT
DEFAULT_WORK = impl.DEFAULT_WORK


def _parse_inline(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as e:
        print("inline JSON 解析失败:", str(e)[:200], file=sys.stderr)
        return None


def build_parser():
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="所有路径均可通过 --input/--output/--work 覆盖，缺省指向 /input、/app/output、/app/work。")
    p.add_argument("--version", action="version", version=impl.VERSION)
    sub = p.add_subparsers(dest="cmd")

    def add_io(sp):
        sp.add_argument("--input", default=DEFAULT_INPUT)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
        sp.add_argument("--work", default=DEFAULT_WORK)
        return sp

    add_io(sub.add_parser("run", help="骨架流水线（不检索）"))
    add_io(sub.add_parser("skeleton", help="写 partial 骨架"))
    add_io(sub.add_parser("parse", help="解析输入"))
    add_io(sub.add_parser("plan", help="生成查询计划"))
    add_io(sub.add_parser("assemble", help="组装结果"))

    sp_imp = sub.add_parser("import", help="导入候选文献")
    sp_imp.add_argument("--file", default=None)
    sp_imp.add_argument("--inline", default=None)
    add_io(sp_imp)

    sp_ev = sub.add_parser("evidence", help="追加证据")
    sp_ev.add_argument("--inline", default=None)
    add_io(sp_ev)

    sp_sc = sub.add_parser("selfcheck", help="自检 result.json")
    sp_sc.add_argument("--strict", action="store_true", help="有失败项则退出码 1")
    add_io(sp_sc)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd is None:
        parser.print_help()
        return 0

    # evidence / import 需要 work 目录（assemble/selfcheck 也用到 work）
    work = getattr(args, "work", DEFAULT_WORK)
    impl.ensure_dir(work)

    if args.cmd == "run":
        impl.run_pipeline(args.input, args.output, args.work)
        print("run 完成 ->", os.path.join(args.output, "result.json"))
        return 0

    if args.cmd == "skeleton":
        impl.write_skeleton(args.input, args.output, args.work)
        return 0

    if args.cmd == "parse":
        summary = impl.parse_case(args.input, args.work)
        print("case_id=%s | contract_fields=%s"
              % (summary.get("case_id"), summary.get("contract_fields")))
        return 0

    if args.cmd == "plan":
        impl.build_plan(args.work)
        return 0

    if args.cmd == "import":
        payload = None
        if args.file:
            payload, deg = impl.read_json(args.file, None)
            if deg:
                print("import 文件读取降级:", deg, file=sys.stderr)
                payload = None
        elif args.inline:
            payload = _parse_inline(args.inline)
        if payload is None:
            print("import 需要 --file 或 --inline 且解析成功", file=sys.stderr)
            return 2
        n = impl.import_records(args.work, payload)
        print("records=%d" % n)
        return 0

    if args.cmd == "evidence":
        payload = _parse_inline(args.inline) if args.inline else None
        if payload is None:
            print("evidence 需要 --inline JSON", file=sys.stderr)
            return 2
        entries = impl.add_evidence(args.work, payload)
        print("evidence=%d" % len(entries))
        return 0

    if args.cmd == "assemble":
        result = impl.assemble(args.input, args.output, args.work)
        print("status=%s | results=%d" % (result.get("status"), len(result.get("results") or [])))
        return 0

    if args.cmd == "selfcheck":
        ok = impl.selfcheck(args.output, args.work, strict=args.strict)
        return 0 if ok else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())