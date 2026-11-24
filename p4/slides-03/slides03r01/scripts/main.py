#!/usr/bin/env python3
"""Entry point for evidence-traced-talk-organizer.

Subcommands (relative to SKILL.md, standard library only):
  scaffold      write a partial result.json skeleton (run within 3 minutes)
  update        merge a JSON patch into result.json (incremental improvement)
  evidence-check  validate fragment-level evidence_map (locator + verbatim quote)
  validate      structural validation of result.json and referenced artifacts

Example:
  python3 scripts/main.py scaffold --case /input/case.json --task /input/task.md --out /app/output
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _emit(label: str, payload):
    print(json.dumps({label: payload}, ensure_ascii=False, indent=2))


def cmd_scaffold(a):
    case, err = C.read_json_safe(a.case)
    if err is not None:
        return _emit("error", err)
    task_text = None
    if a.task and os.path.exists(a.task):
        task_text, _ = C.read_text_safe(a.task)
    os.makedirs(a.out, exist_ok=True)
    out_path = os.path.join(a.out, "result.json")
    result = C.build_skeleton(case, task_text, out_path, work_dir=a.work)
    _emit("ok", {"result": out_path, "case_id": result.get("case_id"), "status": result.get("status")})


def cmd_update(a):
    if not os.path.exists(a.result):
        return _emit("error", "result.json not found: {}".format(a.result))
    if not a.patch and not a.patch_file:
        return _emit("error", "provide --patch or --patch-file")
    if a.patch_file:
        raw, err = C.read_text_safe(a.patch_file)
        if err is not None:
            return _emit("error", "cannot read patch file: {}".format(err))
    else:
        raw = a.patch
    try:
        patch = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _emit("error", "bad patch JSON: {}".format(exc))
    if not isinstance(patch, dict):
        return _emit("error", "patch must be a JSON object")
    result = C.update_result(a.result, patch, action=a.action or "update")
    _emit("ok", {"result": a.result, "updated": list(patch.keys()), "change_log": result.get("change_log", [])[-1]})


def cmd_evidence_check(a):
    if not os.path.exists(a.result):
        return _emit("error", "result.json not found: {}".format(a.result))
    result, err = C.read_json_safe(a.result)
    if err is not None:
        return _emit("error", err)
    issues = C.validate_evidence(result)
    # Optionally cross-check quotes against extracted source text in sources.json.
    sources = None
    if a.sources and os.path.exists(a.sources):
        sources, _ = C.read_json_safe(a.sources)
    if sources and isinstance(sources, dict):
        texts = {}
        for src in sources.get("sources", []) if isinstance(sources.get("sources"), list) else []:
            if isinstance(src, dict) and src.get("text"):
                texts[src.get("path") or src.get("name") or "unnamed"] = src.get("text")
        if texts:
            em = result.get("evidence_map") or []
            for entry in em:
                for item in entry.get("evidence", []):
                    q = item.get("quote", "")
                    loc = item.get("locator") or {}
                    ref = loc.get("file") or item.get("source_ref")
                    body = texts.get(ref)
                    if body is not None and q and C.quote_offset_in(body, q) is None:
                        issues.append({"level": "error", "message": "quote not found in source {}: {!r}".format(ref, q[:60])})
    errors = [i for i in issues if i.get("level") == "error"]
    _emit("ok", {"error_count": len(errors), "issue_count": len(issues), "issues": issues})
    if errors:
        sys.exit(2)


def cmd_validate(a):
    if not os.path.exists(a.result):
        return _emit("error", "result.json not found: {}".format(a.result))
    issues = C.validate_result(a.result, a.out or os.path.dirname(a.result))
    errors = [i for i in issues if i.get("level") == "error"]
    _emit("ok", {"error_count": len(errors), "issue_count": len(issues), "issues": issues})
    if errors:
        sys.exit(2)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scaffold", help="write partial result.json skeleton")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--out", default="/app/output")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("update", help="merge JSON patch into result.json")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--patch", help="inline JSON object to merge (small patches)")
    p.add_argument("--patch-file", help="path to a JSON file to merge (recommended for large patches)")
    p.add_argument("--action", help="change_log action label")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("evidence-check", help="validate evidence_map locator + quote")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--sources", default="/app/work/sources.json")
    p.set_defaults(func=cmd_evidence_check)

    p = sub.add_parser("validate", help="validate result.json + artifacts")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")
    p.set_defaults(func=cmd_validate)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
