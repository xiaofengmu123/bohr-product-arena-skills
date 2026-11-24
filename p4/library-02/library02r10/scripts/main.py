# -*- coding: utf-8 -*-
"""main.py — thin CLI entry point for the document-library skill.

All implementation lives in ``core.py``. This wrapper only locates the module
next to itself and forwards the argument vector, so the skill can be invoked
with a relative path ``scripts/main.py`` regardless of the working directory.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def _help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR] "
          "[--case-json F] [--task-path F] [--skeleton-only] "
          "[--update PATCH.json] [--result RESULT.json] [--check]")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    raise SystemExit(core.main(sys.argv[1:]))
