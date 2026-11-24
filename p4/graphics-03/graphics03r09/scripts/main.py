#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 生物机制图/通路图 Skill 入口（仅标准库）。

用法（相对 Skill 根目录 /skills/weekly-skill）：
  python3 scripts/main.py validate <spec.json> <report.json> [--norm spec.norm.json]
  python3 scripts/main.py build <spec.json> <out.svg>
  python3 scripts/main.py check <result.json> [--output-dir /app/output]
  python3 scripts/main.py skeleton <result.json> --case-id <id> [--title <t>]

退出码：0=通过；1=阻断错误；2=仅警告（validate/check）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def _dump_json(obj, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def cmd_validate(a):
    spec, err = core.load_json(a.spec)
    if err:
        print("FATAL: %s" % err)
        return 1
    errors, warnings, norm = core.validate_spec(spec)
    report = {"errors": errors, "warnings": warnings,
              "n_entities": len(norm.get("entities", [])),
              "n_relations": len(norm.get("relations", []))}
    _dump_json(report, a.report)
    if a.norm:
        _dump_json(norm, a.norm)
    for e in errors:
        print("ERROR: %s" % e)
    for w in warnings:
        print("WARN: %s" % w)
    if errors:
        return 1
    if warnings:
        return 2
    print("validate OK: %d entities, %d relations"
          % (report["n_entities"], report["n_relations"]))
    return 0


def cmd_build(a):
    spec, err = core.load_json(a.spec)
    if err:
        print("FATAL: %s" % err)
        return 1
    try:
        path = core.build_svg(spec, a.out)
    except ValueError as exc:
        print("FATAL: %s" % exc)
        return 1
    print("build OK: %s" % path)
    return 0


def cmd_check(a):
    result, err = core.load_json(a.result)
    if err:
        print("FATAL: %s" % err)
        return 1
    errors, warnings = core.check_result(result, a.output_dir)
    for e in errors:
        print("ERROR: %s" % e)
    for w in warnings:
        print("WARN: %s" % w)
    if errors:
        return 1
    if warnings:
        return 2
    print("check OK")
    return 0


def cmd_skeleton(a):
    result = core.write_skeleton(a.out, a.case_id, a.title)
    print("skeleton OK: %s (status=%s)" % (a.out, result["status"]))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="生物机制图/通路图 Skill：规格校验 / SVG 构建 / 结果契约校验 / 骨架落盘")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("validate", help="校验并归一化 spec.json")
    sp.add_argument("spec", help="spec.json 路径")
    sp.add_argument("report", help="校验报告输出路径")
    sp.add_argument("--norm", default=None, help="归一化 spec 输出路径（可选）")

    bp = sub.add_parser("build", help="由 spec.json 生成分层 SVG")
    bp.add_argument("spec")
    bp.add_argument("out")

    cp = sub.add_parser("check", help="校验 result.json 输出契约")
    cp.add_argument("result")
    cp.add_argument("--output-dir", default=None,
                    help="相对路径的文件存在性检查基准目录（如 /app/output）")

    sk = sub.add_parser("skeleton", help="落盘 partial 结果骨架")
    sk.add_argument("out", help="result.json 输出路径")
    sk.add_argument("--case-id", required=True)
    sk.add_argument("--title", default=None)

    a = p.parse_args(argv)
    return {
        "validate": cmd_validate,
        "build": cmd_build,
        "check": cmd_check,
        "skeleton": cmd_skeleton,
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
