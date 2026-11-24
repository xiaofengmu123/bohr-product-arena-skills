"""main.py — thin entry wrapper for document library repair.

Usage (paths default to /input, /app/output, /app/work):
  python3 -B scripts/main.py --skeleton-only
  python3 -B scripts/main.py
  python3 -B scripts/main.py --update patch.json --result /app/output/result.json
  python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json

Implementation lives in core_impl.py (same directory, stdlib only).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.main())
