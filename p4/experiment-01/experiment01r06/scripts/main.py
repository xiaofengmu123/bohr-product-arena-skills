#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI entry for experiment-plan-ir.

Usage (relative to skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py probe    [--input /input] [--work /app/work]
    python3 scripts/main.py scaffold [--input /input] [--output /app/output] [--work /app/work]
    python3 scripts/main.py build    [--input /input] [--output /app/output] [--work /app/work] [--ir <file>]
    python3 scripts/main.py validate [--input /input] [--output /app/output] [--work /app/work]
    python3 scripts/main.py todo     [--input /input] [--work /app/work]
    python3 scripts/main.py all      [--input ... --output ... --work ...]

Exit codes: 0 = usable (warnings allowed), 1 = must-fix hard failure, 2 = usage error.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(core_impl.main(sys.argv[1:]))
