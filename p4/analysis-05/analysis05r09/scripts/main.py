"""main.py - thin CLI entry point. Implementation lives in core_impl.py.

Usage:
  python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py analyze --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py render --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work

Optional overrides: --primary NAME/PATH, --group COLUMN, --targets COL1,COL2
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Research data multi-step analysis (stdlib only)")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "scaffold", "profile", "analyze", "render", "finalize"])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--primary", default=None, help="primary table name or path")
    ap.add_argument("--group", default=None, help="grouping column")
    ap.add_argument("--targets", default=None, help="comma-separated numeric target columns")
    args = ap.parse_args(argv)

    targets = None
    if args.targets:
        targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    if args.command == "run":
        st = core_impl.run_pipeline(args.input, args.output, args.work,
                                    args.primary, args.group, targets)
    else:
        st = core_impl.run_stage(args.command, args.input, args.output, args.work,
                                 args.primary, args.group, targets)

    # Always exit 0 on handled outcomes so the harness can read result.json.
    status = st.get("status", "unknown")
    print(f"skill finished: status={status} case_id={st.get('case_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
