# -*- coding: utf-8 -*-
"""selftest.py — standalone self-check for the citation/fact audit package.

Run from the package root (or anywhere, using the scripts directory of this
file). It exercises: frontmatter completeness, --help exit code, JSON
parseability, contract echo, finding key completeness, locator presence and
finalize determinism. Exit code 0 means every check passed.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core_impl import DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK, main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("usage: selftest.py [--input DIR] [--output DIR] [--work DIR]")
        sys.exit(0)
    # Reuse main()'s argument parsing by forcing the selftest subcommand.
    sys.argv = [sys.argv[0], "selftest"] + argv
    sys.exit(main())
