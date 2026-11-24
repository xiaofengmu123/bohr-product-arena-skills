# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper for the coverage-analysis Skill.

Delegates all subcommands (scaffold / inspect / run / merge) to core_impl.py
so the entry file stays small (<30KB) while the implementation lives in one
auditable module.

Usage examples (paths default to the Harness runtime):
    python scripts/main.py --help
    python scripts/main.py scaffold --input /input --output /app/output --work /app/work
    python scripts/main.py inspect  --input /input --output /app/output --work /app/work
    python scripts/main.py run      --input /input --output /app/output --work /app/work
    python scripts/main.py merge    --output /app/output --work /app/work --updates updates.json
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv=None):
    return core_impl.main(argv)


if __name__ == "__main__":
    sys.exit(main())
