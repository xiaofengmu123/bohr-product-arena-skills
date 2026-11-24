"""main.py — thin entry wrapper for the research-question-driven search Skill.

Delegates to core_impl.py so the harness can invoke:
    python3 -B scripts/main.py <run|set-plan|import|evidence|set-fields|assemble|selfcheck> [options]
    python3 -B scripts/main.py --help
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: python3 -B scripts/main.py <run|set-plan|import|evidence|set-fields|assemble|selfcheck> [options]")
    print("       python3 -B scripts/main.py --help")
    print("")
    print("defaults: --input /input --output /app/output --work /app/work")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
