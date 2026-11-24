#!/usr/bin/env python3
"""Entry dispatcher for sci-figure-lean (standard library only)."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402


def env(name, default):
    return os.environ.get(name) or default


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="sci-figure-lean toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write partial result.json skeleton (S0)")
    p.add_argument("--input", default=env("INPUT_DIR", "/input"))
    p.add_argument("--output", default=env("OUTPUT_DIR", "/app/output"))
    p.add_argument("--work", default=env("WORK_DIR", "/app/work"))

    p = sub.add_parser("extract", help="parse a table into typed columns + issues")
    p.add_argument("--input", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--work", default=env("WORK_DIR", "/app/work"))

    p = sub.add_parser("check", help="validate result.json + deliverables")
    p.add_argument("--result", default=None)
    p.add_argument("--output", default=env("OUTPUT_DIR", "/app/output"))
    p.add_argument("--work", default=env("WORK_DIR", "/app/work"))

    p = sub.add_parser("svg", help="write a minimal editable SVG fallback")
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="figure")
    p.add_argument("--text", action="append", default=[])

    args = ap.parse_args(argv)

    if args.cmd == "init":
        out = core.bootstrap(args.input, args.output, args.work)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("case_id") else 1

    if args.cmd == "extract":
        out = core.extract_table_file(args.input, args.name, args.work)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if "error" not in out else 1

    if args.cmd == "check":
        result = args.result or os.path.join(args.output, "result.json")
        report = core.check_deliverables(result, args.work, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if args.cmd == "svg":
        path = core.write_minimal_svg(args.out, args.title, args.text)
        print(json.dumps({"ok": True, "path": path}, ensure_ascii=False))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
