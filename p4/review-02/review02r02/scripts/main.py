# -*- coding: utf-8 -*-
"""main.py — thin CLI entry for the citation/fact audit toolchain.

Delegates every subcommand (discover/scaffold/checkpoint/finalize/selftest)
to core_impl.py so this file stays small and easy to audit.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core_impl import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
