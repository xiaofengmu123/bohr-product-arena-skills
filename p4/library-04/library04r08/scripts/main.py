"""main.py - command-line entry point for the research-kb-qa skill.

Subcommands
-----------
scaffold   Read /input/case.json + task.md and write an early, valid
           partial result skeleton (partial-result-first). Exit 0.
inventory  Walk /input, compute hashes/types/metadata/duplicates/versions,
           and write inventory.json + evidence_index.json. Exit 0.
finalize   Merge the Agent-written answers.json with the inventory and the
           echoed output_contract into /app/output/result.json, plus
           inventory.md and evidence_map.json. Exit 0.

Usage
-----
    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --answers /app/work/answers.json

Only the Python standard library is used. All paths default to the runtime
contract values (/input, /app/output, /app/work) when the flags are absent.
"""

from __future__ import annotations

import argparse
import os
import sys

import pipeline  # same directory as this file

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Research knowledge-base inventory and cited QA pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("scaffold", "inventory", "finalize"):
        p = sub.add_parser(name, help="run the %s phase" % name)
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)

    finalize = sub.choices["finalize"]
    finalize.add_argument("--answers", default=None,
                          help="path to the Agent-written answers.json")

    return parser


def cmd_scaffold(args) -> int:
    case_summary = pipeline.build_case_summary(args.input)
    pipeline.make_skeleton(case_summary, args.output, args.work,
                           reason="scaffold: input contract parsed, "
                                  "analysis pending")
    print("scaffold: wrote %s/result.json and %s/case_summary.json" % (
        args.output, args.work))
    return 0


def cmd_inventory(args) -> int:
    case_summary_path = os.path.join(args.work, "case_summary.json")
    if os.path.isfile(case_summary_path):
        case_summary, err = pipeline.load_json(case_summary_path)
        if err is not None:
            case_summary = {"case_id": "unknown", "material_paths": [],
                            "output_contract": {}}
    else:
        case_summary = pipeline.build_case_summary(args.input)

    inventory = pipeline.build_inventory(args.input, args.work, case_summary)
    pipeline.write_json(os.path.join(args.work, "inventory.json"), inventory)

    evidence_index = _build_evidence_index(inventory)
    pipeline.write_json(os.path.join(args.work, "evidence_index.json"),
                        evidence_index)

    # Refresh the skeleton's library/sources/limitations incrementally.
    existing_path = os.path.join(args.output, "result.json")
    existing, _ = pipeline.load_json(existing_path) if os.path.isfile(
        existing_path) else (None, None)
    if isinstance(existing, dict):
        existing["sources"] = pipeline._build_sources(inventory)
        existing["library"] = pipeline._library_summary(inventory)
        existing["errors"] = pipeline._collect_errors(case_summary, inventory)
        existing["degradations"] = pipeline._collect_degradations(
            inventory, None)
        existing["change_log"].append({
            "step": "inventory",
            "time": pipeline.now_iso(),
            "action": "inventoried %d file(s)" % len(inventory.get("files", [])),
            "status": "partial",
        })
        existing["runtime"]["phase"] = "inventory"
        pipeline.write_json(existing_path, existing)

    print("inventory: %d file(s), %d duplicate group(s), "
          "%d version family/families" % (
              len(inventory.get("files", [])),
              len(inventory.get("duplicate_groups", [])),
              len(inventory.get("version_families", []))))
    return 0


def _build_evidence_index(inventory) -> list:
    """Give the Agent a sorted index of text-like files to quote from."""
    index = []
    for f in inventory.get("files", []):
        if f.get("type") in ("text", "pdf"):
            index.append({
                "relative_path": f.get("relative_path"),
                "type": f.get("type"),
                "sha256": f.get("sha256"),
                "size": f.get("size"),
                "pdf_text_layer": f.get("pdf_text_layer"),
                "locator_hint": _locator_hint(f),
            })
    return index


def _locator_hint(f) -> str:
    if f.get("type") == "text":
        return "%s#L<start>-L<end>" % f.get("relative_path")
    if f.get("type") == "pdf":
        return "%s#p<page>" % f.get("relative_path")
    return "%s" % f.get("relative_path")


def cmd_finalize(args) -> int:
    case_summary_path = os.path.join(args.work, "case_summary.json")
    case_summary, err = pipeline.load_json(case_summary_path)
    if err is not None or not isinstance(case_summary, dict):
        case_summary = pipeline.build_case_summary(args.input)

    inventory, _ = pipeline.load_json(os.path.join(args.work, "inventory.json"))
    if not isinstance(inventory, dict):
        inventory = {}

    answers = {}
    answers_path = args.answers or os.path.join(args.work, "answers.json")
    if os.path.isfile(answers_path):
        answers, err = pipeline.load_json(answers_path)
        if err is not None or not isinstance(answers, dict):
            answers = {"answers": [], "uncertainty": [],
                       "degradations": [{
                           "item": "answers.json",
                           "reason": "unreadable answers file: %s" % err}]}

    result = pipeline.finalize(case_summary, inventory, answers,
                               args.output, args.work)
    print("finalize: status=%s, answers=%d, sources=%d" % (
        result.get("status"),
        len(result.get("answers") or []),
        len(result.get("sources") or [])))
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "scaffold":
            return cmd_scaffold(args)
        if args.command == "inventory":
            return cmd_inventory(args)
        if args.command == "finalize":
            return cmd_finalize(args)
        print("unknown command: %s" % args.command, file=sys.stderr)
        return 2
    except Exception as exc:  # never let a partial artifact crash the run
        print("error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
