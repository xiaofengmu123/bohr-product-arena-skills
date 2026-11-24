#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper for the document-library repair Skill.

The real implementation lives in core_impl.py.  This wrapper only resolves the
path and delegates, so `python3 -B scripts/main.py ...` works from anywhere
without sys.path tricks.  All third-party-free, standard library only.

Modes (see SKILL.md for the full contract):
  --skeleton-only   write a valid partial result.json immediately (fast)
  (default)         full inventory/dedup/version/alias/reconcile/index pipeline
  --update patch.json --result out/result.json   merge a semantic patch
  --check out/result.json --case-json in/case.json  validate a result
  --help            usage
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help() -> None:
    print(
        "usage: python3 -B scripts/main.py [options]\n"
        "  --input DIR         input root (default /input)\n"
        "  --output DIR        output dir (default /app/output)\n"
        "  --work DIR          work dir (default /app/work)\n"
        "  --case-json PATH    case file (default /input/case.json)\n"
        "  --task-path PATH    task file (default /input/task.md)\n"
        "  --skeleton-only     write partial skeleton and exit\n"
        "  --update PATCH.json merge a patch into an existing result\n"
        "  --result PATH       result.json path for --update/--check\n"
        "  --check PATH        validate a result.json, exit non-zero on fail\n"
        "  --help              this message"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    if not os.path.isfile(target):
        sys.stderr.write("missing %s\n" % target)
        raise SystemExit(1)
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
