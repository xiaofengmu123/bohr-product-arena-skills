#!/usr/bin/env python3
"""selftest.py — standalone robustness self-check for the Skill.

Runs the same checks as ``python scripts/main.py selftest``. Stdlib only.
Exit code 0 means all checks passed; 1 means at least one check failed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main.run_selftest())
