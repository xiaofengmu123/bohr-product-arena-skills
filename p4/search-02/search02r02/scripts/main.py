"""main.py — thin entry wrapper.

Delegates to core_impl.py so that the CLI surface stays small and reviewable.
Run from the skill root:  python3 -B scripts/main.py --help
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: python3 -B scripts/main.py <command> [options]\n"
        "commands:\n"
        "  scaffold  --input DIR --output DIR --work DIR   write partial skeleton\n"
        "  set-plan  --file JSON --work DIR                 merge decomposition/plan/terms/stop\n"
        "  import    --file JSON --work DIR                 replace candidate record ledger\n"
        "  evidence  --file JSON --work DIR                 append evidence log entries\n"
        "  assemble  --input DIR --output DIR --work DIR    write result.json + .md artifacts\n"
        "  selfcheck --output DIR --work DIR                print PASS/FAIL checklist\n"
        "options: --runtime-seconds N --runtime-requests N (assemble)\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
