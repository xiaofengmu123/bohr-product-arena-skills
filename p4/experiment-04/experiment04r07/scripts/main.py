#!/usr/bin/env python3
"""main.py — thin entry wrapper for the experiment-plan-comparison skill.

The implementation lives in core_impl.py (kept separate so this entry file
stays small). Subcommands: inspect, assemble, validate, extract, demo.

Usage:
  python scripts/main.py --help
  python scripts/main.py inspect
  python scripts/main.py assemble
  python scripts/main.py validate /app/output/result.json
  python scripts/main.py extract
  python scripts/main.py demo
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
