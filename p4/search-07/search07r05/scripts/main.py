#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper.

Delegates to coverage.py so the package keeps a stable, minimal entry point
while the implementation lives in a separate module (mirrors the
main.py + core_impl.py convention for keeping files below review-size limits).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        from coverage import build_parser
        build_parser().print_help()
        raise SystemExit(0)
    sys.path.insert(0, HERE)
    from coverage import main
    raise SystemExit(main())
