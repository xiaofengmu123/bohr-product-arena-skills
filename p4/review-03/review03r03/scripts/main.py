#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper.

Implementation lives in core_impl.py so that this entry file stays small.
Run:  python -B scripts/main.py [discover|scaffold|finalize] [--input DIR]
      [--output DIR] [--work DIR]
Defaults: --input /input  --output /app/output  --work /app/work
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core_impl import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
