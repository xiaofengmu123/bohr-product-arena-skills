"""main.py — thin entry wrapper for research-question-driven search Skill.

The implementation lives in core_impl.py; this file only locates the package
root and hands control over, so the skill stays easy to audit and the main
entry stays under the size budget.

Run from the skill root with relative paths:

    python3 -B scripts/main.py --help
    python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
    python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
    python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
    python3 -B scripts/main.py set-meta --file /app/work/meta_in.json --work /app/work
    python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <command> [options]   (see core_impl.py --help for details)")
    print("commands: run | set-plan | import | evidence | set-meta | assemble | selfcheck")


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        return 0
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
