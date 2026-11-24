#!/usr/bin/env python3
"""main.py -- thin entry wrapper for the document-library repair Skill.

Implementation lives in core_impl.py (kept separate so this wrapper stays small).
Run: `python3 -B scripts/main.py [--input DIR] [--output DIR] [--work DIR] ...`
"""

import os
import sys


def _main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import core_impl
    sys.argv[0] = os.path.join(here, "core_impl.py")
    return core_impl.main()


if __name__ == "__main__":
    raise SystemExit(_main())
