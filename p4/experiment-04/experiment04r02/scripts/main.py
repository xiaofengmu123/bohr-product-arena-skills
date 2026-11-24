"""main.py - thin CLI entry wrapper for the plan-comparison skill.

All logic lives in core_impl.py so this file stays under 30 KB.
Run from the skill package root:

    python scripts/main.py inspect            # step 1: flush a valid partial skeleton
    python scripts/main.py assemble           # step 4: final result.json + artifacts
    python scripts/main.py validate /app/output/result.json
    python scripts/main.py demo               # self-check with clearly marked EXAMPLE data
    python scripts/main.py progress           # print the 900s / 64-request stage ledger

Standard library only.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMPL = os.path.join(HERE, "core_impl.py")


def _help():
    print(
        "usage: python scripts/main.py COMMAND [options]\n"
        "\n"
        "commands:\n"
        "  inspect   scan /input, write /app/output/result.json skeleton + /app/work/case_pack.json\n"
        "  assemble  merge /app/work/analysis.json into final result.json + matrix artifacts\n"
        "  validate  check structural validity of RESULT (exit 0 = valid)\n"
        "  demo      run a clearly marked EXAMPLE end-to-end (not a real case)\n"
        "  progress  print the stage budget ledger (900 s / 64 requests)\n"
        "\n"
        "options:\n"
        "  --input DIR   input directory   (default /input)\n"
        "  --output DIR  output directory  (default /app/output)\n"
        "  --work DIR    work directory    (default /app/work)\n"
        "  --help, -h    show this help\n"
    )


def main():
    if "--help" in sys.argv or "-h" in sys.argv or len(sys.argv) == 1:
        _help()
        raise SystemExit(0)
    sys.argv[0] = IMPL
    runpy.run_path(IMPL, run_name="__main__")


if __name__ == "__main__":
    main()
