"""selftest.py — standalone self-check for the produced result.json.

Runs the same checks documented in SKILL.md §自检清单 and exits 0 when every
check passes. Call after ``finalize``:

    python -B scripts/selftest.py --output /app/output
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def _main() -> int:
    import argparse

    p = argparse.ArgumentParser(prog="selftest.py")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args()
    return core.cmd_selftest(args.output, args.work)


if __name__ == "__main__":
    sys.exit(_main())
