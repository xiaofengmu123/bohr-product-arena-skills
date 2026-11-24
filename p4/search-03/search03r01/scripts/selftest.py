# -*- coding: utf-8 -*-
"""selftest.py -- self-check entry point for the literature screening Skill.

Runs the robustness dry-runs (empty input / bad JSON / missing dir) and a
finalize check of the current output, then exits 0 when all checks pass.

Can be invoked standalone or via `main.py selftest`:

    python3 -B scripts/selftest.py --output /app/output --work /app/work
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    sys.exit(core_impl.run_selftest(sys.argv[1:]))
