#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry dispatcher for the sci-intro-writing-robust skill scripts.

Usage:
  python3 -B scripts/main.py scan [--input /input --work /app/work]
  python3 -B scripts/main.py skeleton [--work /app/work --output /app/output]
  python3 -B scripts/main.py validate [--work /app/work --output /app/output]
  python3 -B scripts/main.py selftest

Defaults match the runtime contract (/input, /app/work, /app/output).
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_MODULES = {
    "scan": "scan.py",
    "skeleton": "emit_skeleton.py",
    "validate": "validate.py",
    "selftest": "selftest.py",
}


def _load(name):
    path = os.path.join(HERE, _MODULES[name])
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd not in _MODULES:
        print(f"main.py: unknown command {cmd!r}; expected one of {sorted(_MODULES)}", file=sys.stderr)
        return 2
    mod = _load(cmd)
    fn = getattr(mod, "run", None)
    if not callable(fn):
        print(f"main.py: {cmd} has no run()", file=sys.stderr)
        return 2
    return fn(args[1:])


if __name__ == "__main__":
    sys.exit(main())
