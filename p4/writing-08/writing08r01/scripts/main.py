"""main.py — entry dispatcher for the methods-experiments-writing skill.

Usage:
  python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
  python3 scripts/main.py validate  --input /input --output /app/output --work /app/work
  python3 scripts/main.py selftest  [--package <dir>]
  python3 scripts/main.py                    # default: skeleton -> inventory -> validate

Standard library only. Exit code is 0 on handled degradation so a partial
deliverable is never lost.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as impl  # noqa: E402


def _add_common(parser):
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__.splitlines()[0])
    _add_common(parser)
    sub = parser.add_subparsers(dest="command")

    p_sk = sub.add_parser("skeleton", help="write legal partial result.json + state.json")
    _add_common(p_sk)

    p_inv = sub.add_parser("inventory", help="discover materials and emit candidate facts")
    _add_common(p_inv)

    p_val = sub.add_parser("validate", help="validate result.json contract and evidence paths")
    _add_common(p_val)

    p_st = sub.add_parser("selftest", help="run empty/bad-json/missing-input self-tests")
    p_st.add_argument("--package", default=None)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "skeleton":
        return impl.run_skeleton(args)
    if args.command == "inventory":
        return impl.run_inventory(args)
    if args.command == "validate":
        return impl.run_validate(args)
    if args.command == "selftest":
        return impl.run_selftest_cmd(args)
    return impl.run_full(args)


if __name__ == "__main__":
    sys.exit(main())
