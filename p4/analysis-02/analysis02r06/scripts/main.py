# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper.

The real implementation lives in core_impl.py. This wrapper exists so the
skill can be invoked from any working directory without importing issues:

    cd /skills/weekly-skill && python scripts/main.py run
    cd /skills/weekly-skill && python scripts/main.py --help
    cd /skills/weekly-skill && python scripts/main.py selfcheck
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [cmd] [--input DIR] [--output DIR] [--work DIR]")
    print("cmds: run | init | survey | inspect | analyze | build | selfcheck")
    print("defaults: --input /input --output /app/output --work /app/work")
    print("Writes result.json and intermediate representations to --output.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
