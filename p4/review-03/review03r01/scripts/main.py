"""main.py — thin entry point for the evidence-bound audit pipeline.

All implementation lives in core_impl.py so that main.py stays small and the
harness/agent can always invoke `python scripts/main.py <command>` from the
Skill root (relative paths).

Commands: discover | scaffold | finalize | pipeline | selftest
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.main())
