"""main.py -- thin CLI entry for the coverage & omission analysis Skill.

Usage:
    python scripts/main.py scaffold --input /input --output /app/output --work /app/work
    python scripts/main.py inspect  --input /input --output /app/output --work /app/work
    python scripts/main.py run      --input /input --output /app/output --work /app/work [--refs GLOB] [--results GLOB]
    python scripts/main.py merge    --output /app/output --work /app/work --updates updates.json
    python scripts/main.py finalize --output /app/output --work /app/work
    python scripts/main.py selftest
    python scripts/main.py --help

Defaults: --input /input, --output /app/output, --work /app/work.
Standard library only. Implementation lives in core_impl.py.
"""

import argparse
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic literature-coverage and omission analysis.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_io(sp):
        sp.add_argument("--input", default="/input", help="input dir (default /input)")
        sp.add_argument("--output", default="/app/output", help="output dir (default /app/output)")
        sp.add_argument("--work", default="/app/work", help="work dir (default /app/work)")

    add_io(sub.add_parser("scaffold", help="write a legal partial result skeleton"))

    add_io(sub.add_parser("inspect", help="inventory input files and candidate records"))

    sp_run = sub.add_parser("run", help="freeze reference set, match, compute coverage")
    add_io(sp_run)
    sp_run.add_argument("--refs", nargs="*", default=None, help="glob(s) for reference-set files")
    sp_run.add_argument("--results", nargs="*", default=None, help="glob(s) for search-result files")

    sp_merge = sub.add_parser("merge", help="apply agent adjudications/attribution from updates JSON")
    add_io(sp_merge)
    sp_merge.add_argument("--updates", required=True, help="path to updates JSON")

    add_io(sub.add_parser("finalize", help="write report/evidence map, set honest status"))

    sub.add_parser("selftest", help="run built-in self-checks (no network, no external input)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "selftest":
        target = os.path.join(HERE, "selftest.py")
        sys.argv = [target]
        runpy.run_path(target, run_name="__main__")
        return

    import core_impl  # noqa: E402

    result = core_impl.run_subcommand(args)
    if isinstance(result, dict) and args.command in ("inspect",):
        print("inspect wrote", os.path.join(args.work, "inspect.json"))
        print("candidate_records:", result.get("candidate_total"))
    elif isinstance(result, dict):
        cov = result.get("coverage")
        line = f"status={result.get('status')}"
        if cov:
            line += f" coverage={cov.get('coverage_rate')} ({cov.get('numerator')}/{cov.get('denominator')})"
        print(line)


if __name__ == "__main__":
    main()
