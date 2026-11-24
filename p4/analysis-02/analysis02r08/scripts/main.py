"""main.py - thin entry point for the analysis-02 skill.

Dispatches subcommands to core_impl.py and guarantees a valid result.json even
if the analysis fails. Run from the skill root with relative paths:

    python scripts/main.py init
    python scripts/main.py survey
    python scripts/main.py inspect
    python scripts/main.py analyze
    python scripts/main.py build
    python scripts/main.py selfcheck
    python scripts/main.py selftest
    python scripts/main.py all

Options: --input DIR (default /input), --output DIR (default /app/output),
--work DIR (default /app/work), --plan FILE (default /app/work/plan.json).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402

USAGE = __doc__
COMMANDS = {"init", "survey", "inspect", "analyze", "build", "selfcheck",
            "selftest", "all"}


def _parse_args(argv):
    args = {"input": "/input", "output": "/app/output", "work": "/app/work",
            "plan": "/app/work/plan.json", "cmd": "all"}
    i = 0
    if argv and argv[0] in COMMANDS:
        args["cmd"] = argv[0]
        i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work", "--plan") and i + 1 < len(argv):
            args[a[2:]] = argv[i + 1]
            i += 2
            continue
        i += 1
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h"):
        print(USAGE)
        return 0
    args = _parse_args(argv)
    ctx = {"input": args["input"], "output": args["output"], "work": args["work"],
           "plan": args["plan"]}
    cmd = args["cmd"]
    try:
        if cmd == "all":
            C._cmd_init(ctx)
            C._cmd_survey(ctx)
            C._cmd_inspect(ctx)
            C._cmd_analyze(ctx)
            C._cmd_build(ctx)
            print(json.dumps(C._cmd_selfcheck(ctx), ensure_ascii=False))
        elif cmd == "init":
            print(json.dumps(C._cmd_init(ctx), ensure_ascii=False))
        elif cmd == "survey":
            print(json.dumps(C._cmd_survey(ctx), ensure_ascii=False, default=str))
        elif cmd == "inspect":
            print(json.dumps(C._cmd_inspect(ctx), ensure_ascii=False, default=str))
        elif cmd == "analyze":
            print(json.dumps(C._cmd_analyze(ctx), ensure_ascii=False, default=str))
        elif cmd == "build":
            print(json.dumps(C._cmd_build(ctx), ensure_ascii=False, default=str))
        elif cmd == "selfcheck":
            print(json.dumps(C._cmd_selfcheck(ctx), ensure_ascii=False, default=str))
        elif cmd == "selftest":
            return C._cmd_selftest(ctx)
        return 0
    except Exception as exc:  # noqa: BLE001
        try:
            C._land_fallback(ctx, str(exc))
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
