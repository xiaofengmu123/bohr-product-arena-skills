#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI for the bilingual scholarly translator Skill.

Commands:
  init       discover input, load case, write result.json skeleton + case brief
  extract    segment parsed materials, build work/ledger.json + segments.jsonl
  verify     re-read ledger, check output deliverables, write verification report
  add <key> <json>     append one item to a list field in result.json
  set <key> <json>     set a field in result.json
  finalize   --status <success|partial|failed>   finalize result.json
  selftest   run the built-in self-test

Defaults: --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(args):
    inv = core.discover_input(args.input, args.work)
    case = core.load_case(inv.get("case_json"))
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    brief = {"case_id": case.get("case_id"), "case_json": inv.get("case_json"),
             "task_md": inv.get("task_md"), "materials": inv.get("materials"),
             "missing": inv.get("missing"), "output_contract": case.get("output_contract"),
             "case_error": case.get("error")}
    core.atomic_write_json(os.path.join(args.work, "case_brief.json"), brief)
    skeleton = core.make_skeleton(case.get("case_id"), case.get("output_contract"))
    result_path = os.path.join(args.output, "result.json")
    core.atomic_write_json(result_path, skeleton)
    return {"ok": True, "case_id": case.get("case_id"),
            "result": result_path, "materials": len(inv.get("materials", [])),
            "missing": inv.get("missing")}


def cmd_extract(args):
    inv = core.discover_input(args.input, args.work)
    segments = []
    sources = []
    os.makedirs(args.work, exist_ok=True)
    for m in inv.get("materials", []):
        txt = core.read_text(m["path"])
        if txt is None:
            continue
        if txt == "__OVERSIZED__":
            sources.append({"path": m["path"], "type": m.get("ext"),
                            "role": "skipped-oversized"})
            continue
        base_id = "src" + str(len(sources) + 1)
        segs = core.segment_text(txt, m["path"], base_id)
        if segs:
            segments.extend(segs)
            sources.append({"path": m["path"], "type": m.get("ext"),
                            "role": "material", "segments": len(segs)})
    ledger = core.build_ledger(segments, sources)
    core.atomic_write_json(os.path.join(args.work, "segments.jsonl"),
                           segments)  # list-of-objects, JSONL-compatible
    core.atomic_write_json(os.path.join(args.work, "ledger.json"), ledger)
    return {"ok": True, "segments": len(segments),
            "numbers": len(ledger["numbers"]), "terms": len(ledger["terms"]),
            "citations": len(ledger["citations"]),
            "semantic_markers": len(ledger["semantic_markers"]),
            "ledger": os.path.join(args.work, "ledger.json")}


def _collect_targets(output_dir):
    targets = {}
    for name in sorted(os.listdir(output_dir)):
        if name == "result.json":
            continue
        p = os.path.join(output_dir, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in core.TEXT_EXTS:
            txt = core.read_text(p)
            if txt and txt != "__OVERSIZED__":
                targets[p] = txt
    return targets


def cmd_verify(args):
    ledger_path = os.path.join(args.work, "ledger.json")
    ledger = core.load_json_graceful(ledger_path)
    if not isinstance(ledger, dict) or "segments" not in ledger:
        report = {"time": core.now_iso(), "status": "fail",
                  "errors": [{"check": "ledger", "locator": ledger_path,
                              "quote": "", "detail": "ledger missing; run "
                              "scripts/main.py extract first"}],
                  "warnings": [], "checks": []}
        core.atomic_write_json(os.path.join(args.work, "verification_report.json"),
                               report)
        return {"ok": False, "status": "fail",
                "detail": "ledger missing, run extract first"}
    targets = _collect_targets(args.output)
    report = core.verify(ledger, targets)
    core.atomic_write_json(os.path.join(args.work, "verification_report.json"),
                           report)
    return {"ok": True, "status": report["status"],
            "errors": len(report["errors"]), "warnings": len(report["warnings"]),
            "targets": len(targets),
            "report": os.path.join(args.work, "verification_report.json")}


def _load_result(output_dir):
    p = os.path.join(output_dir, "result.json")
    r = core.load_json_graceful(p)
    if not isinstance(r, dict):
        r = core.make_skeleton(None, None)
    return p, r


def cmd_add(args):
    path, r = _load_result(args.output)
    key = args.args[0]
    item = json.loads(args.args[1])
    core.result_add(r, key, item)
    core.atomic_write_json(path, r)
    return {"ok": True, "key": key}


def cmd_set(args):
    path, r = _load_result(args.output)
    key = args.args[0]
    value = json.loads(args.args[1])
    core.result_set(r, key, value)
    core.atomic_write_json(path, r)
    return {"ok": True, "key": key}


def cmd_finalize(args):
    path, r = _load_result(args.output)
    status = args.status or "partial"
    if status not in ("success", "partial", "failed"):
        status = "partial"
    core.finalize_result(r, status)
    core.atomic_write_json(path, r)
    return {"ok": True, "status": status, "result": path}


def cmd_selftest(args):
    import selftest
    ok, results = selftest.run_selftest()
    for name, passed, note in results:
        print("%-28s %s  %s" % (name, "PASS" if passed else "FAIL", note))
    return {"ok": ok, "tests": len(results)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--status", default=None,
                    help="status for finalize: success|partial|failed")
    ap.add_argument("command",
                    choices=["init", "extract", "verify", "add", "set",
                             "finalize", "selftest"])
    ap.add_argument("args", nargs="*")
    args = ap.parse_args(argv)
    handlers = {"init": cmd_init, "extract": cmd_extract, "verify": cmd_verify,
                "add": cmd_add, "set": cmd_set, "finalize": cmd_finalize,
                "selftest": cmd_selftest}
    try:
        res = handlers[args.command](args)
    except (IndexError, json.JSONDecodeError, ValueError, OSError) as e:
        res = {"ok": False, "error": str(e)}
    if res is None:
        res = {"ok": True}
    _print(res)
    return 0 if res.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
