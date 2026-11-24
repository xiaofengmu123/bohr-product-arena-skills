"""main.py — thin CLI entry for citation-fact-audit-trace.

Usage:
  python -B scripts/main.py discover   [--input DIR] [--work DIR]
  python -B scripts/main.py scaffold   [--input DIR] [--output DIR] [--work DIR]
  python -B scripts/main.py finalize   [--input DIR] [--output DIR] [--work DIR]
  python -B scripts/main.py integrity  [--work DIR] [--output DIR]
  python -B scripts/main.py selftest
  python -B scripts/main.py help | --help | -h

All commands use only the Python 3 standard library. Implementation lives in
core_impl.py in the same directory.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

USAGE = __doc__


def main(argv):
    if not argv or argv[0] in ("help", "--help", "-h"):
        print(USAGE)
        return 0
    cmd = argv[0]
    dirs = core_impl.parse_dirs(argv[1:])

    if cmd == "discover":
        core_impl.discover(dirs["input"], dirs["work"])
        return 0
    if cmd == "scaffold":
        core_impl.scaffold(dirs["input"], dirs["output"], dirs["work"])
        return 0
    if cmd == "finalize":
        core_impl.finalize(dirs["input"], dirs["output"], dirs["work"])
        return 0
    if cmd == "integrity":
        return core_impl.integrity(dirs["work"], dirs["output"])
    if cmd == "selftest":
        root = os.path.dirname(HERE)
        return core_impl.run_selftest(root)

    print(f"unknown command: {cmd}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
