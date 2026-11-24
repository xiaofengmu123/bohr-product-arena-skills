"""main.py - thin CLI entry for the stat-analysis-02 skill.

All real logic lives in core_impl.py and statlib.py so this file stays small
and easy to audit. Run from anywhere; paths are resolved relative to this file.

Examples (defaults match the evaluation runtime):
    python scripts/main.py init        # partial skeleton within first 3 minutes
    python scripts/main.py survey
    python scripts/main.py inspect
    python scripts/main.py analyze
    python scripts/main.py build
    python scripts/main.py selfcheck
    python scripts/main.py selftest
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Robust statistical analysis skill (stat-analysis-02).")
    p.add_argument("command", nargs="?", default="init",
                   choices=sorted(core_impl.COMMANDS.keys()),
                   help="pipeline stage to run")
    p.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                   help="input directory (default: /input)")
    p.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                   help="output directory (default: /app/output)")
    p.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"),
                   help="work directory (default: /app/work)")
    return p


def main(argv=None):
    parser = build_parser()
    # Unknown arguments are ignored (robust to stray harness flags).
    args, _unknown = parser.parse_known_args(argv)
    return core_impl.run(args.input, args.output, args.work, args.command)


if __name__ == "__main__":
    raise SystemExit(main())
