"""main.py - thin entry wrapper (implementation in core_impl.py).

Run with:  python3 -B scripts/main.py --help
The evaluator installs this Skill under /skills/weekly-skill and invokes the
scripts by their relative paths from the SKILL.md root.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    core_impl.main()
