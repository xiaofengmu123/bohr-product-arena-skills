"""main.py - thin CLI entry for the research-question-driven-search pipeline.

Delegates all logic to core.py so this file stays small and reviewable.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <scan|skeleton|plan|screen|evidence|rank|assemble|selfcheck> [options]")
    print("  --input DIR     input directory (default /input)")
    print("  --output DIR    output directory (default /app/output)")
    print("  --work DIR      work directory (default /app/work)")
    print("  plan/screen/evidence: --file PATH or --inline JSON")
    print("  assemble: --runtime-seconds N --runtime-requests N")
    print("run `python3 scripts/selftest.py` for a case-free self-test.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
