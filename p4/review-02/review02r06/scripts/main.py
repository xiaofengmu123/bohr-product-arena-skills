"""main.py — thin entry wrapper (implementation lives in core.py).

Usage:
  python scripts/main.py --help
  python scripts/main.py discover [--input DIR] [--output DIR] [--work DIR]
  python scripts/main.py scaffold  ...
  python scripts/main.py finalize ...
  python scripts/main.py selftest ...
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def main(argv=None) -> int:
    return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
