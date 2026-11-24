"""selftest.py — deterministic self-check for the Skill package.

Builds a generated fixture in a temp dir (no shipped example answers), runs the
full discover/scaffold/finalize pipeline, and asserts the output contract
invariants listed in SKILL.md §5. Exit code 0 means all checks passed.

Run from the Skill root:
    python -B scripts/selftest.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    class Args:
        input = "/input"
        output = "/app/output"
        work = "/app/work"

    outcome = core_impl.run_selftest(Args())
    if not outcome.get("ok"):
        print("[selftest] FAILED", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
