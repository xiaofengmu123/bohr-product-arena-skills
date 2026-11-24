"""main.py - thin CLI entry wrapper. Implementation lives in core_impl.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(core_impl.main())
