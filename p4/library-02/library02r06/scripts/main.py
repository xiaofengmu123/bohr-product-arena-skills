"""main.py - thin CLI entry wrapper for the library-retrieval Skill.

All implementation lives in core.py so this file stays small (<30 KB).
Usage:
    python3 -B scripts/main.py --help
    python3 -B scripts/main.py --skeleton-only
    python3 -B scripts/main.py [--case-json ...] [--task-path ...] \
        [--input-root ...] [--output-dir ...] [--work-dir ...]
    python3 -B scripts/main.py --update patch.json --result /app/output/result.json
    python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json
    python3 -B scripts/main.py --query "buffer" --output-dir /app/output
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


if __name__ == "__main__":
    core.main()
