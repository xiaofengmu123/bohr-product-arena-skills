"""main.py - thin entry wrapper for the scientific data-analysis Skill.

The full implementation lives in core_impl.py so that main.py stays small and
reviewable. The wrapper only re-binds argv[0] and delegates execution, keeping
the same exit code as the implementation.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: python3 -B scripts/main.py [--input DIR] [--output DIR] [--work DIR] [--help]")
    print()
    print("Subcommands (see SKILL.md for the full workflow):")
    print("  scaffold   write a minimal partial result.json skeleton immediately")
    print("  profile    discover data sources and write work/profile.json")
    print("  all        run clean -> analyze -> render -> finalize end to end")
    print("  finalize   re-fill the output_contract from existing work files")
    print()
    print("Defaults: --input /input  --output /app/output  --work /app/work")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
