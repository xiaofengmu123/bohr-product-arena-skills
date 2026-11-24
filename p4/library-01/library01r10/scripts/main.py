#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin CLI entry for the library-repair skill.

Delegates all implementation to core_impl.py.  Use relative invocation from the
skill root:

    python3 -B scripts/main.py --skeleton-only
    python3 -B scripts/main.py
    python3 -B scripts/main.py --update patch.json --result /app/output/result.json
    python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json

Defaults mirror the runtime contract: input=/input, output=/app/output,
work=/app/work.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic document-library repair: crawl -> analyze -> render.",
    )
    p.add_argument("--case-json", default="/input/case.json",
                   help="path to case.json (default: /input/case.json)")
    p.add_argument("--task-path", default="/input/task.md",
                   help="path to task.md (default: /input/task.md)")
    p.add_argument("--input-root", default="/input",
                   help="input material root (default: /input)")
    p.add_argument("--output-dir", default="/app/output",
                   help="final artifacts directory (default: /app/output)")
    p.add_argument("--work-dir", default="/app/work",
                   help="intermediate work directory (default: /app/work)")
    p.add_argument("--result", default="/app/output/result.json",
                   help="result.json path (default: /app/output/result.json)")
    p.add_argument("--hash-limit", type=int, default=None,
                   help="content-hash size limit in bytes (default: 2097152)")
    p.add_argument("--skeleton-only", action="store_true",
                   help="write only the partial result skeleton (phase 0)")
    p.add_argument("--update", metavar="PATCH",
                   help="deep-merge a JSON patch into result.json")
    p.add_argument("--check", metavar="RESULT",
                   help="validate a result.json against case.json contract")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.check:
        ok, issues = core.check_result(args.check, args.case_json)
        if issues:
            for i in issues:
                print("check: %s" % i)
        print("check: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1

    if args.update:
        ok, msg = core.apply_patch(args.result, args.update)
        print("update: %s" % msg)
        return 0 if ok else 2

    if args.skeleton_only:
        path = core.run_skeleton(args.case_json, args.task_path, args.output_dir, args.work_dir)
        print("skeleton: %s" % path)
        return 0

    path = core.run_pipeline(
        args.case_json,
        args.task_path,
        args.input_root,
        args.output_dir,
        args.work_dir,
        hash_limit=args.hash_limit,
    )
    print("finalize: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
