"""main.py -- thin entry wrapper for the source-bound citation audit skill.

All behavior lives in core_impl.py so this entry file stays small and the same
implementation can be reused by scripts/selftest.py. Both files are runnable.

Usage:
    python -B scripts/main.py --help
    python -B scripts/main.py discover   [--input DIR --output DIR --work DIR]
    python -B scripts/main.py scaffold   [--input DIR --output DIR --work DIR]
    python -B scripts/main.py finalize   [--input DIR --output DIR --work DIR]
    python -B scripts/main.py pipeline   [--input DIR --output DIR --work DIR]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.main())
