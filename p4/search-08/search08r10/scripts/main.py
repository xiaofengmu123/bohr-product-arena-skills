"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Kept intentionally small so the real logic can be reviewed and reused in
isolation.  See core_impl.py for the stage machine and the reproducible
ledger helpers.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_STAGES = "discover contract ingest dedup screen audit finalize selftest"


def _help():
    print("usage: main.py <stage> [--input DIR] [--output DIR] [--work DIR]")
    print(f"stages: {_STAGES}")
    print("defaults: --input /input --output /app/output --work /app/work")


if __name__ == "__main__":
    if not sys.argv[1:] or "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
