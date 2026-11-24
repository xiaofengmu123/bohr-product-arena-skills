"""main.py — thin CLI entry point for the research-methods & statistics audit skill.

All implementation lives in core.py so this file stays small. Usage:

    python -B scripts/main.py scaffold
    python -B scripts/main.py probe
    python -B scripts/main.py commit
    python -B scripts/main.py finalize
    python -B scripts/main.py selftest

Optional flags: --input DIR, --output DIR, --work DIR (defaults /input, /app/output,
/app/work). ``--help`` prints usage and exits 0.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


if __name__ == "__main__":
    sys.exit(core.dispatch())
