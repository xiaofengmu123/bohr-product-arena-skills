#!/usr/bin/env python3
"""Thin entry wrapper. Real implementation lives in core_impl.py.

Usage is identical whichever file you call; this wrapper only ensures the
package imports cleanly when invoked as `python3 scripts/main.py` from any
working directory. Pure standard library.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(core_impl.main())