#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口（薄壳）。

子命令：
  scaffold   发现输入、回填 output_contract、落盘 partial result.json
  normalize  规范化一个或多个专利号（参数或 JSON 文件）
  extract    从文本/文件抽取专利号候选
  family     读 records JSON，按共用申请号/优先权号做传递闭包归并
  finalize   校验 result.json、补 runtime、生成 report.md
  validate   校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work；
亦可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖。
纯标准库实现；同一输入重复运行，分析字段结果一致。
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import core_impl as P  # noqa: E402


def _env_default(name, fallback):
    return os.environ.get(name) or fallback


def _read_json_arg(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = P.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err["error"]),
              file=sys.stderr)
        return None
    return obj


def cmd_scaffold(args):
    result, out_path = P.scaffold(args.input, args.output, args.work)
    print("scaffold: wrote %s (status=%s)"
          % (out_path, result.get("status")))


def cmd_normalize(args):
    if args.file:
        data = _read_json_arg(args.file)
        if data is None:
            data = []
        items = data if isinstance(data, list) else [data]
        outputs = [P.normalize_patent_number(x) for x in items]
    elif args.numbers:
        outputs = [P.normalize_patent_number(x) for x in args.numbers]
    else:
        outputs = [P.normalize_patent_number(args.text)]
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def cmd_extract(args):
    if args.file:
        text, _enc, err = P.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=False),
                  file=sys.stderr)
            sys.exit(0)
    else:
        text = args.text
    hits = P.extract_numbers(text)
    print(json.dumps(hits, ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json_arg(args.file)
    if records is None:
        records = []
    if not isinstance(records, list):
        records = [records]
    print(json.dumps(P.group_families(records), ensure_ascii=False, indent=2))


def cmd_finalize(args):
    ok, _result, _path, problems = P.finalize(args.output, args.status)
    if not ok:
        for p in problems:
            print("finalize problem: %s" % p, file=sys.stderr)
    print("finalize: status=%s problems=%d"
          % (args.status or "unchanged", len(problems)))


def cmd_validate(args):
    path = os.path.join(args.output, "result.json")
    if not os.path.exists(path):
        print("INVALID: result.json not found")
        sys.exit(1)
    result, err = P.load_json(path)
    if err:
        print("INVALID: %s" % err["error"])
        sys.exit(1)
    ok, problems = P.validate_result(result)
    if ok:
        print("VALID")
    else:
        print("INVALID")
        for p in problems:
            print("  - %s" % p)
        sys.exit(1)


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill CLI")
    # 路径参数挂到每个子命令上，支持 `scaffold --input ...` 的顺序。
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--input", default=_env_default("INPUT_DIR", "/input"))
    parent.add_argument("--output", default=_env_default("OUTPUT_DIR", "/app/output"))
    parent.add_argument("--work", default=_env_default("WORK_DIR", "/app/work"))

    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scaffold", parents=[parent],
                       help="发现输入并落盘 partial 骨架")
    s.set_defaults(func=cmd_scaffold)

    n = sub.add_parser("normalize", parents=[parent], help="规范化专利号")
    n.add_argument("--text", default="")
    n.add_argument("--file", default=None)
    n.add_argument("numbers", nargs="*")
    n.set_defaults(func=cmd_normalize)

    e = sub.add_parser("extract", parents=[parent], help="抽取专利号候选")
    e.add_argument("--text", default="")
    e.add_argument("--file", default=None)
    e.set_defaults(func=cmd_extract)

    f = sub.add_parser("family", parents=[parent], help="同族归并")
    f.add_argument("--file", required=True)
    f.set_defaults(func=cmd_family)

    fin = sub.add_parser("finalize", parents=[parent],
                         help="校验并生成 report.md")
    fin.add_argument("--status", default=None,
                     choices=sorted(P.ALLOWED_STATUS))
    fin.set_defaults(func=cmd_finalize)

    v = sub.add_parser("validate", parents=[parent], help="校验 result.json")
    v.set_defaults(func=cmd_validate)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
