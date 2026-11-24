#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin CLI for the cross-lingual literature search skill.

Subcommands
  init      discover /input, extract case_id + output_contract, atomically
            write a legal partial result skeleton to /app/output/result.json.
            Run FIRST so a valid partial deliverable is on disk early.
  patch     deep-merge a JSON patch file into result.json (lists replace).
  log       append one change_log entry (incremental workflow record).
  lang      heuristic language detection for a text snippet or file.
  norm      compute a DOI/title dedup key (version merge helper).
  validate  verify result.json is legal and contains case_id, contract fields
            and the standard fields; prints issue list.
  status    print a short progress summary from result.json.
  selftest  run the offline self-check suite (delegates to selftest.py).

Standard library only. Default paths follow the runtime contract:
  --input  /input
  --output /app/output
  --work   /app/work
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as cl  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _result_path(args):
    return os.path.join(args.output, "result.json")


def _add_paths(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)
    return sp


def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    found, case, task_text, case_err = cl.load_case(args.input)
    contract = cl.find_contract(case)
    if contract is None:
        contract = cl.find_contract_from_text(task_text)
    case_id = cl.find_case_id(case, task_text)
    skeleton = cl.build_skeleton(case_id, contract, found, case_err)
    cl.atomic_write_json(_result_path(args), skeleton)
    cl.atomic_write_json(os.path.join(args.work, "state.json"), {
        "initialized": True,
        "case": found["case"],
        "task": found["task"],
        "materials": found["materials"],
        "contract_fields": cl.contract_fields(contract),
    })
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_fields": cl.contract_fields(contract),
        "result": _result_path(args),
    }, ensure_ascii=False))
    return 0


def cmd_patch(args):
    patch, err = cl.safe_json_load(args.patch)
    if patch is None:
        print("error: patch not valid JSON: %s" % err, file=sys.stderr)
        return 1
    result = cl.read_result(args.output)
    if not result:
        print("error: no result.json at %s; run init first" % args.output,
              file=sys.stderr)
        return 1
    merged = cl.deep_merge(result, patch)
    cl.atomic_write_json(_result_path(args), merged)
    print(json.dumps({"status": merged.get("status"), "result": _result_path(args)},
                     ensure_ascii=False))
    return 0


def cmd_log(args):
    result = cl.read_result(args.output)
    if not result:
        print("error: no result.json at %s; run init first" % args.output,
              file=sys.stderr)
        return 1
    entry = {
        "seq": len(result.get("change_log", [])),
        "time": cl.utc_now(),
        "action": args.action,
        "detail": args.detail or "",
    }
    result["change_log"] = result.get("change_log", []) + [entry]
    cl.atomic_write_json(_result_path(args), result)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def cmd_lang(args):
    if args.file:
        text, _enc = cl.read_text(args.file)
    else:
        text = args.text or ""
    info = cl.detect_language(text)
    info["chars"] = len(text)
    print(json.dumps(info, ensure_ascii=False))
    return 0


def cmd_norm(args):
    key = cl.dedup_key(doi=args.doi, title=args.title,
                       first_author=args.author, year=args.year)
    print(json.dumps({
        "doi": cl.normalize_doi(args.doi),
        "title": cl.normalize_title(args.title),
        "dedup_key": key,
    }, ensure_ascii=False))
    return 0


def cmd_validate(args):
    result = cl.read_result(args.output)
    if not result:
        print(json.dumps({"ok": False, "issues": ["result.json_missing"]},
                         ensure_ascii=False))
        return 1
    contract = None
    if args.input:
        _found, case, task_text, _err = cl.load_case(args.input)
        contract = cl.find_contract(case)
        if contract is None:
            contract = cl.find_contract_from_text(task_text)
    issues = cl.validate_result(result, contract)
    print(json.dumps({"ok": not issues, "issues": issues}, ensure_ascii=False))
    return 0 if not issues else 1


def cmd_status(args):
    result = cl.read_result(args.output)
    if not result:
        print(json.dumps({"status": "missing", "result": _result_path(args)},
                         ensure_ascii=False))
        return 0
    print(json.dumps({
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "results": len(result.get("results", [])),
        "works": len(result.get("works", [])),
        "dedup": len(result.get("dedup", [])),
        "change_log": len(result.get("change_log", [])),
        "result": _result_path(args),
    }, ensure_ascii=False))
    return 0


def cmd_selftest(args):  # noqa: ARG001
    import selftest
    return selftest.main()


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="cross-lingual literature search skill")
    sub = parser.add_subparsers(dest="command", required=True)

    p = _add_paths(sub.add_parser("init", help="write partial result skeleton"))
    p.set_defaults(func=cmd_init)

    p = _add_paths(sub.add_parser("patch", help="deep-merge a JSON patch into result.json"))
    p.add_argument("--patch", required=True, help="path to patch JSON")
    p.set_defaults(func=cmd_patch)

    p = _add_paths(sub.add_parser("log", help="append a change_log entry"))
    p.add_argument("--action", required=True, help="step or action name")
    p.add_argument("--detail", default="", help="one-line summary")
    p.set_defaults(func=cmd_log)

    p = _add_paths(sub.add_parser("lang", help="detect language of a text snippet/file"))
    p.add_argument("--text", default=None, help="inline text")
    p.add_argument("--file", default=None, help="text file path")
    p.set_defaults(func=cmd_lang)

    p = _add_paths(sub.add_parser("norm", help="compute DOI/title dedup key"))
    p.add_argument("--doi", default="", help="DOI string")
    p.add_argument("--title", default="", help="title string")
    p.add_argument("--author", default="", help="first author surname")
    p.add_argument("--year", default="", help="publication year")
    p.set_defaults(func=cmd_norm)

    p = _add_paths(sub.add_parser("validate", help="validate result.json"))
    p.set_defaults(func=cmd_validate)

    p = _add_paths(sub.add_parser("status", help="print progress summary"))
    p.set_defaults(func=cmd_status)

    p = _add_paths(sub.add_parser("selftest", help="run offline self-checks"))
    p.set_defaults(func=cmd_selftest)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
