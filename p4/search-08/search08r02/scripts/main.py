"""main.py — thin CLI entry for the time-budgeted screening pipeline.

Parses arguments and delegates to core_impl.dispatch(). Standard library only;
run as ``python3 -B scripts/main.py <subcommand>``.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv=None):
    args = core_impl.build_parser().parse_args(argv)
    return core_impl.dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
