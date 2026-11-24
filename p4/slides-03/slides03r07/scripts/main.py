#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — orchestrator for the academic-deck-organizer skill.

Pipeline (each phase never crashes; failures become diagnostics):

    discover -> load -> scaffold -> [validate + evidence-check] -> save

Modes:
    default        full run: write a partial skeleton, then run checks.
    --scaffold-only  write only the partial result.json (call this FIRST, well
                     inside the 3-minute budget, then improve incrementally).
    --validate-only  read an existing result.json and report checks.
    --patch FILE     deep-merge a JSON patch into the existing result.json
                     (the incremental-improvement primitive).
    --selftest       run the degenerate-input self-tests and print a report.

Exit code is 0 whenever a legal result.json was written (or a report was
produced); non-zero only when no artifact could be produced at all.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _load_inputs(input_dir, case_path, task_path, errors, degradations):
    """Discover and load case/task robustly.  Returns (case_obj, task_text, disc)."""
    disc = C.discover_inputs(input_dir)
    if not disc["exists"]:
        errors.append("input directory does not exist: %s" % input_dir)
        return None, "", disc

    case_file = case_path or (
        C._prefer(disc["case_files"], C.CASE_FILE_NAMES)["path"]
        if disc["case_files"] else None)
    task_file = task_path or (
        C._prefer(disc["task_files"], C.TASK_FILE_NAMES)["path"]
        if disc["task_files"] else None)

    case_obj = None
    if case_file:
        loaded = C.load_json_robust(case_file)
        if loaded["data"] is not None:
            case_obj = loaded["data"]
        else:
            errors.append("case file unreadable: %s (%s)"
                          % (case_file, loaded["error"]))
            if loaded["skipped"]:
                degradations.append("oversized case file skipped: %s" % case_file)
    else:
        degradations.append("no case file found in %s; using task + materials only"
                            % input_dir)

    task_text = ""
    if task_file:
        rd = C.read_text_robust(task_file)
        task_text = rd["text"]
        if rd["error"] and not rd["text"]:
            errors.append("task file unreadable: %s (%s)" % (task_file, rd["error"]))
        if rd["skipped"]:
            degradations.append("oversized task file truncated: %s" % task_file)
    else:
        degradations.append("no task file found in %s" % input_dir)

    return case_obj, task_text, disc


def _scaffold(args, errors, degradations):
    case_obj, task_text, disc = _load_inputs(args.input, args.case, args.task,
                                             errors, degradations)
    contract, _provided = C.extract_contract(case_obj)
    result = C.build_skeleton(case_obj, task_text, disc, contract,
                              errors, degradations)
    # record which inputs we actually read
    if disc.get("case_files"):
        p = C._prefer(disc["case_files"], C.CASE_FILE_NAMES)["path"]
        C.mark_source(result["sources"], p, True, "parsed" if case_obj is not None else "failed")
    if disc.get("task_files"):
        p = C._prefer(disc["task_files"], C.TASK_FILE_NAMES)["path"]
        C.mark_source(result["sources"], p, bool(task_text), "read" if task_text else "failed")
    result_path = os.path.join(args.output, "result.json")
    err = C.save_json(result_path, result)
    if err:
        errors.append(err)
        return None, None
    return result, result_path


def _check_and_report(result, args):
    issues = C.validate_result(result, args.output)
    sources_manifest = None
    if args.sources and os.path.exists(args.sources):
        loaded = C.load_json_robust(args.sources, C.MAX_JSON_BYTES)
        if loaded["data"] is not None:
            sources_manifest = loaded["data"]
    issues += C.evidence_check(result, sources_manifest)
    return issues


def _summarize(result, result_path, issues, errors, degradations):
    n_errors = sum(1 for i in issues if i["level"] == "error")
    n_warn = sum(1 for i in issues if i["level"] == "warning")
    return {
        "ok": bool(result) and n_errors == 0,
        "case_id": result.get("case_id") if result else None,
        "status": result.get("status") if result else None,
        "result_path": result_path,
        "validation_errors": n_errors,
        "validation_warnings": n_warn,
        "input_errors": len(errors),
        "degradations": len(degradations),
    }


def main(argv=None):
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(
        description="Organize research material into a verifiable academic talk "
                    "(robust to empty input, bad JSON, missing params, oversized files).")
    ap.add_argument("--input", default="/input", help="input directory (default: /input)")
    ap.add_argument("--work", default="/app/work", help="work directory (default: /app/work)")
    ap.add_argument("--output", default="/app/output", help="output directory (default: /app/output)")
    ap.add_argument("--case", default=None, help="explicit case file path (overrides discovery)")
    ap.add_argument("--task", default=None, help="explicit task file path (overrides discovery)")
    ap.add_argument("--sources", default=None,
                    help="JSON manifest mapping source paths to extracted text, for quote checks")
    ap.add_argument("--scaffold-only", action="store_true",
                    help="write only the partial result.json skeleton and exit")
    ap.add_argument("--validate-only", action="store_true",
                    help="validate an existing result.json and print a report")
    ap.add_argument("--patch", default=None,
                    help="deep-merge this JSON file into the existing result.json")
    ap.add_argument("--selftest", action="store_true",
                    help="run degenerate-input self-tests and print a report")
    args = ap.parse_args(argv)

    errors, degradations = [], []

    if args.selftest:
        main_py = os.path.abspath(__file__)
        passed, report = C.run_selftests(main_py, sys.executable)
        print(json.dumps({"selftest": {"passed": passed, "total": len(report)},
                          "cases": report}, ensure_ascii=False, indent=2))
        return 0 if passed == len(report) else 1

    # Patch mode: incremental improvement without re-scaffolding.
    if args.patch:
        result_path = os.path.join(args.output, "result.json")
        if os.path.exists(result_path):
            loaded = C.load_json_robust(result_path, C.MAX_JSON_BYTES)
            base = loaded["data"] if loaded["data"] is not None else {}
        else:
            base, _ = _scaffold(args, errors, degradations)
        if not isinstance(base, dict):
            base = {}
        patch = C.load_json_robust(args.patch, C.MAX_JSON_BYTES)["data"]
        if patch is None:
            errors.append("patch file unreadable: %s" % args.patch)
        else:
            base = C.deep_merge(base, patch)
            base.setdefault("change_log", []).append({
                "timestamp": C.now_iso(),
                "action": "merge_patch",
                "scope": "incremental update from %s" % os.path.basename(args.patch),
            })
        err = C.save_json(result_path, base)
        if err:
            errors.append(err)
        print(json.dumps({"ok": not err, "result_path": result_path,
                          "errors": errors}, ensure_ascii=False, indent=2))
        return 0 if not err else 1

    # Validate-only mode: no writes.
    if args.validate_only:
        result_path = os.path.join(args.output, "result.json")
        if not os.path.exists(result_path):
            print(json.dumps({"ok": False,
                              "error": "result.json not found: %s" % result_path},
                             ensure_ascii=False, indent=2))
            return 1
        loaded = C.load_json_robust(result_path, C.MAX_JSON_BYTES)
        result = loaded["data"]
        if result is None:
            print(json.dumps({"ok": False, "error": loaded["error"]},
                             ensure_ascii=False, indent=2))
            return 1
        issues = _check_and_report(result, args)
        print(json.dumps(_summarize(result, result_path, issues, [], []),
                         ensure_ascii=False, indent=2))
        print(json.dumps(issues, ensure_ascii=False, indent=2))
        return 0 if not any(i["level"] == "error" for i in issues) else 1

    # Default / scaffold-only: always write a partial skeleton first.
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result, result_path = _scaffold(args, errors, degradations)
    if result is None:
        print(json.dumps({"ok": False, "errors": errors, "degradations": degradations},
                         ensure_ascii=False, indent=2))
        return 1

    if args.scaffold_only:
        print(json.dumps({"ok": True, "mode": "scaffold-only",
                          "case_id": result.get("case_id"),
                          "status": result.get("status"),
                          "result_path": result_path,
                          "errors": errors, "degradations": degradations},
                         ensure_ascii=False, indent=2))
        return 0

    issues = _check_and_report(result, args)
    summary = _summarize(result, result_path, issues, errors, degradations)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
