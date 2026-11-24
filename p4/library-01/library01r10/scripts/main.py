#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - thin CLI entry for the library repair pipeline.

All implementation lives in core_impl.py. This file only parses arguments,
resolves defaults, and dispatches. It is intentionally small so that the
skill's behavior is easy to audit.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

USAGE = """usage: main.py <command> [options]

commands:
  run            scan /input, build the intermediate representation (IR),
                 run deterministic analysis stages, and emit editable
                 artifacts + /app/output/result.json. This is the default.
  skeleton       write a minimal, valid partial result.json immediately
                 (case_id + contract placeholders + status=partial) without
                 scanning the library. Use this first to satisfy the
                 early-partial-deliverable requirement.
  check          validate an existing result.json against the input contract
                 and the on-disk artifacts (schema + completeness + determinism
                 hints). Exit code 0 on pass, 1 on failure.

options:
  --case-json PATH   input case file        (default: /input/case.json)
  --task-path PATH   task markdown          (default: /input/task.md)
  --input-root DIR   read-only source tree  (default: /input)
  --output-dir DIR   editable artifacts     (default: /app/output)
  --work-dir DIR     intermediate IR        (default: /app/work)
  --decisions PATH   optional semantic overlay (agent-authored JSON)
  --result PATH      result.json to check   (check command; default:
                     /app/output/result.json)
  --help, -h         show this help

exit codes:
  0  success / check passed
  1  check failed or unknown argument
  2  output-directory write failure after a skeleton was preserved
"""


def _help() -> None:
    print(USAGE)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv or not argv:
        _help()
        return 0

    command = argv[0]
    if command not in ("run", "skeleton", "check"):
        _help()
        return 1

    # Thin dispatch: hand the real work to core_impl, which lives beside this
    # file and is located by absolute path so invocation never depends on the
    # caller's current working directory.
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    ns = runpy.run_path(target, run_name="__main__")
    entry = ns.get("cli_main")
    if entry is None:
        print("core_impl.py did not define cli_main()", file=sys.stderr)
        return 1
    return entry(command, argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
