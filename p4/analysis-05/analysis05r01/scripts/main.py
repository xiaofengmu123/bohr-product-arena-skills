#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper for the evidence-reinforced data-analysis Skill.

Usage:
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py profile  ...
  python3 -B scripts/main.py clean    ...
  python3 -B scripts/main.py analyze  ...
  python3 -B scripts/main.py render   ...
  python3 -B scripts/main.py finalize ...
  python3 -B scripts/main.py full     ...   # scaffold -> profile -> clean -> analyze -> render -> finalize
  python3 -B scripts/main.py --help

All implementation lives in core_impl.py so this file stays small. Only the
Python standard library is required. Every command exits 0 even on failure and
records problems inside result.json errors/degradations rather than crashing.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_COMMANDS = ("scaffold", "profile", "clean", "analyze", "render", "finalize", "full")


def _help() -> None:
    print(__doc__)
    print("Defaults: --input /input --output /app/output --work /app/work")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or "--help" in args or "-h" in args:
        _help()
        return 0

    cmd = args[0]
    if not cmd.startswith("-"):
        args = args[1:]
    else:
        cmd = "full"

    if cmd not in _COMMANDS:
        print("unknown command: %r (choose from %s)" % (cmd, ", ".join(_COMMANDS)))
        _help()
        return 0

    sys.path.insert(0, HERE)
    import core_impl
    return core_impl.run(cmd, args)


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except SystemExit as exc:  # argparse-style exits are normalized to 0
        code = exc.code if isinstance(exc.code, int) else 0
    except Exception:  # never crash the harness; the impl also guards per stage
        import traceback
        traceback.print_exc()
        code = 0
    sys.exit(code if isinstance(code, int) else 0)
