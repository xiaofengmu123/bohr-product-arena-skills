"""selftest.py — standalone package self-check for citation-fact-audit-trace.

Run from anywhere:
  python -B scripts/selftest.py            # skill root = parent of scripts/
  python -B scripts/selftest.py --root DIR # explicit skill root
  python -B scripts/selftest.py --help

Exits 0 when every check passes, 1 otherwise. Only the standard library.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv):
    root = os.path.dirname(HERE)
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 < len(argv):
            root = argv[i + 1]
    elif any(a.startswith("--root=") for a in argv):
        for a in argv:
            if a.startswith("--root="):
                root = a.split("=", 1)[1]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    return core_impl.run_selftest(root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
