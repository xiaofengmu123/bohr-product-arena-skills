"""main.py — thin entry wrapper for the evidence-ledger screening pipeline.

Dispatch only; all implementation lives in core.py. Run from the skill root:

    python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py signal   --work /app/work --output /app/output
    # Agent appends one JSON object per candidate to /app/work/decisions.jsonl
    python3 -B scripts/main.py merge    --work /app/work --output /app/output
    python3 -B scripts/main.py finalize --work /app/work --output /app/output
    python3 -B scripts/main.py selftest

`python3` unavailable -> fall back to `python`.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def _add_io(p, with_input=False):
    if with_input:
        p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Evidence-ledger literature screening pipeline (discover/signal/merge/finalize/selftest).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="discover inputs, write partial skeleton + normalized case")
    _add_io(p, with_input=True)
    p = sub.add_parser("signal", help="deterministic lexical signals + dedupe/conflict scaffold")
    _add_io(p)
    p = sub.add_parser("merge", help="validate ledger, derive scores, assemble result.json")
    _add_io(p)
    p = sub.add_parser("finalize", help="contract validation + selfcheck.json")
    _add_io(p)
    sub.add_parser("selftest", help="run self-test on a synthetic (clearly-labelled) fixture")

    args = parser.parse_args(argv)
    if args.cmd == "discover":
        core.discover(args.input, args.work, args.output)
    elif args.cmd == "signal":
        core.signal(args.work, args.output)
    elif args.cmd == "merge":
        core.merge(args.work, args.output)
    elif args.cmd == "finalize":
        core.finalize(args.work, args.output)
    elif args.cmd == "selftest":
        import selftest
        return selftest.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
