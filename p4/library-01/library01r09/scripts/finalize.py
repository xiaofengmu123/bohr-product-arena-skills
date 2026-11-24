#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize.py — 科研资料库修复的薄入口（实现见 core_impl.py）。

用法：
  python3 -B finalize.py [--case-json /input/case.json] [--task-path /input/task.md]
      [--input-root /input] [--output-dir /app/output] [--work-dir /app/work]
  python3 -B finalize.py --skeleton-only           # 先落盘 partial 骨架
  python3 -B finalize.py --update patch.json --result /app/output/result.json
  python3 -B finalize.py --check /app/output/result.json --case-json /input/case.json
"""
import argparse
import os
import sys

import core_impl as ci


def main(argv=None):
    ap = argparse.ArgumentParser(description="科研资料库修复确定性主流程")
    ap.add_argument("--case-json", default=None)
    ap.add_argument("--task-path", default=None)
    ap.add_argument("--input-root", default=ci.DEFAULT_INPUT)
    ap.add_argument("--output-dir", default=ci.DEFAULT_OUTPUT)
    ap.add_argument("--work-dir", default=ci.DEFAULT_WORK)
    ap.add_argument("--skeleton-only", action="store_true")
    ap.add_argument("--update", metavar="PATCH.json")
    ap.add_argument("--result", metavar="result.json")
    ap.add_argument("--check", metavar="result.json")
    args = ap.parse_args(argv)

    if args.check:
        return ci.cmd_check(args.check, args.case_json or
                            os.path.join(args.input_root, "case.json"))

    if args.update and args.result:
        return ci.cmd_update(args.result, args.update)

    case_json = args.case_json or os.path.join(args.input_root, "case.json")
    task_path = args.task_path or os.path.join(args.input_root, "task.md")

    if args.skeleton_only:
        return ci.cmd_skeleton(args.output_dir, case_json, task_path)

    return ci.run_full(args.input_root, args.output_dir, args.work_dir, case_json, task_path)


if __name__ == "__main__":
    sys.exit(main())
