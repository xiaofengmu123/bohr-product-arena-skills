"""main.py - thin entry wrapper for the document library repair skill.

All implementation lives in core_impl.py so this entry stays small (< 30KB
per the authoring checklist).  Usage examples:

    python3 -B scripts/main.py --skeleton-only
    python3 -B scripts/main.py
    python3 -B scripts/main.py --update patch.json --result /app/output/result.json
    python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json
    python3 -B scripts/main.py --budget
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    # Make "scripts/" imports and sibling modules resolvable regardless of cwd.
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import core_impl  # noqa: E402  (import after path setup)

    return core_impl.main(argv)


if __name__ == "__main__":
    sys.exit(main())
