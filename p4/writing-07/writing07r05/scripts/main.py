"""CLI entry point for the introduction-and-related-work skill pipeline.

Standard library only. The agent drives three stages with relative paths:

    python3 -B scripts/main.py scan --input /input --work /app/work
    python3 -B scripts/main.py skeleton --work /app/work --output /app/output
    # ... agent authors manuscript.md / evidence_map.json / change_log.json ...
    python3 -B scripts/main.py validate --work /app/work --output /app/output

With no subcommand the tool runs ``scan`` then ``skeleton`` so a bare call
always leaves a valid partial result.json behind (fail-safe bootstrap).
All subcommands exit 0 on a clean run; validation problems are reported in
work/validation_report.json and on stdout, not as a crash.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as core  # noqa: E402


def _print_manifest_summary(manifest: dict) -> None:
    print(f"case_id        = {manifest.get('case_id')}")
    print(f"contract_key   = {manifest.get('contract_key')}")
    print(f"contract_fields= {manifest.get('contract_fields')}")
    print(f"materials      = {manifest.get('material_count')} usable / "
          f"{len(manifest.get('materials') or [])} found")
    for e in manifest.get("errors") or []:
        print(f"[error]   {e}")
    for d in manifest.get("degradations") or []:
        print(f"[degrade] {d}")


def cmd_scan(args: argparse.Namespace) -> int:
    core.ensure_dir(args.work)
    manifest = core.build_manifest(args.input)
    case, _err = core.load_json(os.path.join(args.input, "case.json"))
    normalized = core.build_case_normalized(manifest, case)

    core.write_json(os.path.join(args.work, "input_manifest.json"), manifest)
    core.write_json(os.path.join(args.work, "case_normalized.json"), normalized)
    _print_manifest_summary(manifest)
    return 0


def cmd_skeleton(args: argparse.Namespace) -> int:
    core.ensure_dir(args.output)
    manifest_path = os.path.join(args.work, "input_manifest.json")
    manifest, err = core.load_json(manifest_path)
    if err or not isinstance(manifest, dict):
        # Degrade to a self-contained discovery if the manifest is missing.
        manifest = core.build_manifest(args.input)
        core.write_json(os.path.join(args.work, "input_manifest.json"), manifest)
    result = core.build_skeleton(manifest)
    core.write_json(os.path.join(args.output, "result.json"), result)
    print(f"skeleton written: case_id={result.get('case_id')} "
          f"status={result.get('status')} "
          f"contract_fields={len(manifest.get('contract_fields') or [])}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    core.ensure_dir(args.work)
    report = core.validate_output(args.output, args.work)
    core.write_json(os.path.join(args.work, "validation_report.json"), report)
    print(f"validation: passed={report['passed']} "
          f"checks={len(report['checks'])} errors={len(report['errors'])}")
    for err in report["errors"]:
        print(f"[fail] {err}")
    # Exit 0 always: the report carries the verdict; the agent fixes and
    # re-runs. A non-zero exit would look like a harness crash.
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Introduction-and-related-work skill pipeline "
                    "(scan / skeleton / validate).")
    parser.add_argument("--input", default=core.DEFAULT_INPUT,
                        help="input directory (default: /input)")
    parser.add_argument("--work", default=core.DEFAULT_WORK,
                        help="work directory (default: /app/work)")
    parser.add_argument("--output", default=core.DEFAULT_OUTPUT,
                        help="output directory (default: /app/output)")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("scan", help="discover inputs and output_contract")
    sub.add_parser("skeleton", help="write the first partial result.json")
    sub.add_parser("validate", help="validate result.json and artifacts")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "skeleton":
        return cmd_skeleton(args)
    if args.command == "validate":
        return cmd_validate(args)
    # No subcommand: bootstrap scan + skeleton so any call leaves a valid
    # partial result behind.
    code = cmd_scan(args)
    if code != 0:
        return code
    return cmd_skeleton(args)


if __name__ == "__main__":
    sys.exit(main())
