"""main.py - thin entry wrapper for the citation-fact-audit Skill.

The implementation lives in core_impl.py so the entry point stays small and
auditable. Run from the Skill package root (the scripts/ directory is on the
path automatically):

    python -B scripts/main.py --help
    python -B scripts/main.py --input /input --output /app/output --work /app/work
    python -B scripts/main.py --phase finalize
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    sys.path.insert(0, HERE)
    import core_impl  # noqa: E402  (imported after path setup)

    raise SystemExit(core_impl.main(sys.argv[1:]))
