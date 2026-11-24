# -*- coding: utf-8 -*-
"""Entry wrapper for the research-frontier evidence-analysis Skill.

Resolves its own directory so it runs regardless of the caller's cwd, then
delegates to core_impl. Standard library only.

Usage (paths are relative from the Skill package root):
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core_impl  # noqa: E402


def main(argv=None):
    return core_impl.main(argv)


if __name__ == "__main__":
    sys.exit(main())
