#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for the weekly-skill biological mechanism/pathway figure package.

Subcommands (all standard library, deterministic):

  init     --case /input/case.json --task /input/task.md
              --out /app/output --work /app/work
           Write a valid partial result.json + empty spec.json. Run this FIRST,
           within the first 3 minutes, so a timeout still leaves a deliverable.

  build    --spec /app/work/spec.json --out /app/output
              [--render-meta /app/work/render_meta.json]
           Validate+normalize the spec, then render a layered editable SVG
           (mechanism.svg). Fails (exit 1) on structural spec errors.

  check    --result /app/output/result.json --out /app/output
           Audit result.json against the output contract and the files on disk.
           Exit 0 = pass, 1 = blocking errors, 2 = warnings only.

  selftest Run an end-to-end synthetic example (never a formal case) to verify
           the package works in the current environment.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402


def cmd_init(a):
    return core_impl.init_outputs(a.case, a.task, a.out, a.work)


def cmd_build(a):
    meta = getattr(a, "render_meta", None)
    return core_impl.build_svg_file(a.spec, os.path.join(a.out, "mechanism.svg"), meta)


def cmd_check(a):
    errors, warnings = core_impl.check_result(a.result, a.out)
    for w in warnings:
        print("WARNING:", w)
    for e in errors:
        print("ERROR:", e)
    if errors:
        return 1
    if warnings:
        return 2
    print("OK: result.json contract check passed")
    return 0


def cmd_selftest(a):
    return core_impl.run_selftest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write partial result.json + spec.json skeleton early")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("build", help="validate spec + render layered SVG")
    p.add_argument("--spec", default="/app/work/spec.json")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--render-meta", default="/app/work/render_meta.json")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("check", help="audit result.json against output contract")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("selftest", help="end-to-end self-test on a synthetic example")
    p.set_defaults(fn=cmd_selftest)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
