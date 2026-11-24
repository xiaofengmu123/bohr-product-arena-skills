"""selftest.py — standalone self-check entry for the audit Skill.

Run it against a produced output dir, e.g.:

  python scripts/selftest.py --output /app/output --work /app/work

It reuses the same validation as ``python scripts/main.py selftest`` so the
self-check invoked by hand and the one documented in SKILL.md stay identical.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(core.main(["selftest"] + sys.argv[1:]))
