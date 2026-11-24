#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the library-document-repair skill.

The actual implementation lives in core_impl.py (same directory). This file
keeps the top-level interface stable and under 30 KB so the harness can call:

    python3 -B scripts/main.py --help
    python3 -B scripts/main.py --skeleton-only
    python3 -B scripts/main.py --input-root /input --output-dir /app/output \
        --work-dir /app/work
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main() -> int:
    return core_impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
