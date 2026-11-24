"""sci-intro-writing runtime helper entry dispatcher.

Usage:
    python3 -B scripts/main.py scan       [--input /input --work /app/work]
    python3 -B scripts/main.py skeleton   [--work /app/work --output /app/output]
    python3 -B scripts/main.py checkpoint [--work /app/work --phase P1_skeleton --requests 2]
    python3 -B scripts/main.py budget     [--work /app/work]
    python3 -B scripts/main.py validate   [--work /app/work --output /app/output]
    python3 -B scripts/main.py selftest

Standard library only. Defaults: /input, /app/output, /app/work.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl


def main(argv=None):
    return core_impl.main(list(argv) if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
