"""main.py - thin entry wrapper (implementation in core_impl.py)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.cmd_main(sys.argv[1:]))
