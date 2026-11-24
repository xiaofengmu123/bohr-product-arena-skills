# -*- coding: utf-8 -*-
"""Command-line entry point for the literature-tracking Skill.

Run ``python3 scripts/main.py --help`` first; every subcommand uses the
standard library only and resolves paths relative to the skill root.

Examples (paths default to /input, /app/output, /app/work on the harness):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py inventory --input /input --work /app/work
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work --findings /app/work/findings.json
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


def _add_io(parser):
    parser.add_argument("--input", default=core.DEFAULT_INPUT)
    parser.add_argument("--output", default=core.DEFAULT_OUTPUT)
    parser.add_argument("--work", default=core.DEFAULT_WORK)


def cmd_scaffold(args):
    code, result = core.scaffold(args.input, args.output, args.work)
    if args.print_result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


def cmd_inventory(args):
    inv, _errors, _degradations = core.build_inventory(args.input, args.work)
    print(json.dumps(inv, ensure_ascii=False, indent=2))
    return 0


def cmd_dedup(args):
    records, errors = core.load_records(args.records)
    if errors:
        print("WARN: %s" % "; ".join(errors), file=sys.stderr)
    dedup = core.dedup_records(records)
    core.write_json(args.out, dedup)
    print(
        "dedup: %d in -> %d clusters, %d duplicates removed"
        % (dedup["records_in"], len(dedup["clusters"]) + len(dedup["orphans"]),
           dedup["duplicates_removed"])
    )
    return 0


def cmd_normalize(args):
    try:
        record = json.loads(args.record)
    except json.JSONDecodeError as exc:
        print("bad --record JSON: %s" % exc, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "canonical_id": core.canonical_id(record),
                "keys": [list(k) for k in core.canonical_keys(record)],
                "doi": core.normalize_doi(record.get("doi")),
                "arxiv": core.normalize_arxiv(record.get("arxiv")),
                "title": core.normalize_title(record.get("title")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_finalize(args):
    code, result = core.finalize(
        args.input, args.output, args.work, args.findings
    )
    if args.print_result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


def cmd_run(args):
    code1, _result1 = core.scaffold(args.input, args.output, args.work)
    _inv, _e, _d = core.build_inventory(args.input, args.work)
    code2, result = core.finalize(args.input, args.output, args.work, args.findings)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return max(code1, code2)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic helpers for the literature-tracking Skill.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scaffold", help="write an early partial result.json skeleton")
    _add_io(p)
    p.add_argument("--print-result", action="store_true")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("inventory", help="deterministic material inventory")
    parser_tmp = p
    parser_tmp.add_argument("--input", default=core.DEFAULT_INPUT)
    parser_tmp.add_argument("--work", default=core.DEFAULT_WORK)
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("dedup", help="dedup + version-chain classification")
    p.add_argument("--records", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("normalize", help="show canonical keys for one record")
    p.add_argument("--record", required=True)
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("finalize", help="merge findings and write result.json + report.md")
    _add_io(p)
    p.add_argument("--findings", default=None)
    p.add_argument("--print-result", action="store_true")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("run", help="scaffold + inventory + finalize")
    _add_io(p)
    p.add_argument("--findings", default=None)
    p.set_defaults(func=cmd_run)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
