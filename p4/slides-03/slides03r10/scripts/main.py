#!/usr/bin/env python3
"""Entry point: discover -> skeleton -> (optional) assemble plan IR -> validate -> write result.json.

Thin orchestrator. All contract/IR logic lives in ``core_impl.py`` so this
file stays small and auditable. Standard library only.

Behaviour summary:
* With no ``plan.json`` yet (or ``--skeleton-only``): writes a legal partial
  ``result.json`` skeleton so the first three minutes always leave a usable
  deliverable on disk.
* With a valid ``plan.json``: validates the intermediate representation,
  checks evidence locators, assembles ``result.json`` deterministically, and
  records the validation report under ``work/validation_report.json``.
* Always exits 0 and always writes a legal result, even for empty input or
  bad JSON.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _print_summary(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _sources(script_sources, plan_sources):
    return C.merge_sources(script_sources, plan_sources)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Assemble result.json from a plan IR; always writes a legal result.")
    ap.add_argument("--case", default="/input/case.json",
                    help="path to case.json (default /input/case.json)")
    ap.add_argument("--task", default="/input/task.md",
                    help="path to task.md (default /input/task.md)")
    ap.add_argument("--input", default="/input",
                    help="input directory to discover materials (default /input)")
    ap.add_argument("--work", default="/app/work",
                    help="working directory (default /app/work)")
    ap.add_argument("--output", default="/app/output",
                    help="output directory (default /app/output)")
    ap.add_argument("--plan", default=None,
                    help="path to the intermediate representation plan.json "
                         "(default <work>/plan.json)")
    ap.add_argument("--skeleton-only", action="store_true",
                    help="force writing only the partial skeleton, ignoring any plan")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    started = time.time()

    # Discover inputs instead of assuming fixed names: if the requested path
    # does not exist, fall back to <input>/case.json and <input>/task.md.
    case_path = args.case
    if not os.path.isfile(case_path):
        alt = os.path.join(args.input, "case.json")
        if os.path.isfile(alt):
            case_path = alt
    task_path = args.task
    if not os.path.isfile(task_path):
        alt = os.path.join(args.input, "task.md")
        if os.path.isfile(alt):
            task_path = alt

    case, case_errors = C.load_json(case_path)
    case_ok = case_errors == [] and isinstance(case, dict)
    task_text, _task_err = C.load_text(task_path)

    skeleton = C.build_skeleton(case)

    # Sources actually touched by this script plus discovered material files
    # marked read=False (the Agent marks them read=True after extracting).
    script_sources = []
    if case is not None:
        script_sources.append({"path": case_path, "type": "case_json",
                               "read": case_errors == []})
    if task_text is not None:
        script_sources.append({"path": task_path, "type": "task_md", "read": True})
    known = {os.path.abspath(case_path), os.path.abspath(task_path)}
    for path in C.discover_files(args.input):
        if os.path.abspath(path) in known:
            continue
        script_sources.append({"path": path, "type": C.guess_type(path),
                               "read": False})
    skeleton["sources"] = script_sources
    skeleton["runtime"]["elapsed_s"] = round(time.time() - started, 2)

    plan_path = args.plan or os.path.join(args.work, "plan.json")
    plan_exists = os.path.isfile(plan_path)
    plan, plan_errors = (C.load_json(plan_path) if plan_exists else (None, []))
    plan_ok = plan_errors == [] and isinstance(plan, dict)

    out_path = os.path.join(args.output, "result.json")

    # ---- Branch 1: skeleton only (first three minutes, or invalid/missing plan) ----
    if args.skeleton_only or not plan_exists or not plan_ok:
        skeleton["status"] = "partial"
        if plan_exists and not plan_ok:
            C.add_note(skeleton, "uncertainty",
                       "plan.json 存在但无法解析: %s" % ";".join(plan_errors or []))
        elif not plan_exists:
            C.add_note(skeleton, "uncertainty",
                       "骨架已落盘，内容规划尚未写入 plan.json")
        else:
            C.add_note(skeleton, "uncertainty", "--skeleton-only 已强制只写骨架")
        C.write_json(out_path, skeleton)
        _print_summary({"ok": True, "mode": "skeleton", "status": skeleton["status"],
                        "case_id": skeleton["case_id"],
                        "plan_errors": plan_errors or [],
                        "result": out_path})
        return 0

    # ---- Branch 2: full assembly from the intermediate representation ----
    issues = C.validate_plan(plan)
    if isinstance(plan.get("pages"), list):
        issues += C.check_evidence(plan["pages"])
        issues += C.hedge_check(plan["pages"])

    result = C.assemble(skeleton, plan)
    result["sources"] = _sources(script_sources, plan.get("sources"))

    deck = result.get("editable_deck") or {}
    deck_path = deck.get("path") if isinstance(deck, dict) else None
    has_deck = bool(deck_path and os.path.isfile(deck_path))
    rendering = result.get("rendering") or {}
    export_files = rendering.get("export_files") if isinstance(rendering, dict) else []
    has_export = bool(export_files)

    result = C.finalize_status(result, case_ok, issues, has_deck, has_export)
    result["runtime"]["elapsed_s"] = round(time.time() - started, 2)
    result["runtime"]["issues"] = issues

    C.write_json(out_path, result)
    C.write_json(os.path.join(args.work, "validation_report.json"), {
        "issues": issues,
        "case_ok": case_ok,
        "has_deck": has_deck,
        "has_export": has_export,
        "plan_ok": plan_ok,
        "timestamp": C.now_iso(),
    })

    _print_summary({
        "ok": True,
        "mode": "assembled",
        "status": result["status"],
        "case_id": result["case_id"],
        "pages": len(result.get("pages") or []),
        "errors": len([i for i in issues if i.get("level") == "error"]),
        "warnings": len([i for i in issues if i.get("level") == "warning"]),
        "has_deck": has_deck,
        "has_export": has_export,
        "result": out_path,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
