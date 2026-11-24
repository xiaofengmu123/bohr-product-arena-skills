"""main.py — thin CLI entry.

The pipeline implementation lives in core.py.  This module only parses the
command line and dispatches to core.main(); it stays intentionally small so the
bulk of the logic (and its tests) has a single home.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR] [--stage NAME] "
          "[--group COL] [--targets C1,C2] [--help]")
    print("stages: scaffold | profile | clean | analyze | render | reconcile (default)")
    print("Deterministic research-data pipeline; writes result.json under the output dir.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    sys.path.insert(0, HERE)
    import core

    raise SystemExit(core.main())
