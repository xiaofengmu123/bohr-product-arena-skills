# -*- coding: utf-8 -*-
"""main.py — thin CLI entry wrapper.

The implementation lives in core_impl.py so this entry stays tiny (<30 KB),
keeping the package easy to audit. All paths are resolved relative to this
file's directory, so the wrapper works from any working directory.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR] COMMAND [options]")
    print("commands: scaffold | profile | clean | analyze | render | finalize | run | check")
    print("  run  runs the full pipeline end-to-end (also accepts --group/--targets/--intent)")
    print("  check  validates existing outputs (JSON/SVG/contract keys)")
    print("defaults: --input /input --output /app/output --work /app/work")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
