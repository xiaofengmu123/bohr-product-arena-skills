"""main.py — thin entry wrapper for sci-analysis-exec.

All logic lives in core_impl.py so that main.py stays small and loadable.
Run from the package root with relative script paths:

    python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py profile --input /input --output /app/output
    python3 -B scripts/main.py evidence add --result /app/output/result.json --claim-json '...'
    python3 -B scripts/main.py evidence verify --input /input --result /app/output/result.json
    python3 -B scripts/main.py validate --input /input --output /app/output
    python3 -B scripts/main.py selftest --output /app/work/selftest
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
