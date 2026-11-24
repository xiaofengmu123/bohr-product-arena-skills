#!/usr/bin/env python3
"""Entry point for the academic-format-adaptation skill.

Modes (run from /skills/weekly-skill root via relative path):
  python scripts/main.py --init     # discover input, build/merge partial skeleton
  python scripts/main.py --verify   # contract + quote-locatability self-check
  python scripts/main.py            # default: init then verify

All arguments are optional and default to the runtime contract locations.
Unknown arguments are ignored (never crash); missing directories degrade to an
empty scan. Exit code semantics:
  0  a valid result.json was produced/verified (even if status=partial)
  2  hard failure: result.json missing/invalid or verify found contract errors
"""
import argparse
import json
import os
import sys

import core_impl as core


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="Academic format adaptation: robust result skeleton + verification.",
        add_help=True,
    )
    ap.add_argument("--input", default=core.DEFAULT_INPUT, help="input dir (default /input)")
    ap.add_argument("--output", default=core.DEFAULT_OUTPUT, help="output dir (default /app/output)")
    ap.add_argument("--work", default=core.DEFAULT_WORK, help="work dir (default /app/work)")
    ap.add_argument("--case", default=None, help="override path to case.json")
    ap.add_argument("--init", action="store_true", help="build/merge result.json skeleton")
    ap.add_argument("--verify", action="store_true", help="verify contract + quote locatability")
    ap.add_argument("--no-merge", action="store_true", help="do not merge with existing result.json")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # When only one flag is given, the other is implied.
    do_init = args.init or not args.verify
    do_verify = args.verify or not args.init

    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass

    result_path = os.path.join(args.output, "result.json")
    report_path = os.path.join(args.output, "verification_report.json")

    degradations = []
    errors = []

    # ---- discover input -------------------------------------------------- #
    files = core.discover_files(args.input)
    case_path = args.case or core.find_named(args.input, "case.json")
    task_path = core.find_named(args.input, "task.md")

    case_data = None
    case_err = None
    if case_path:
        case_data, case_err = core.load_json_safe(case_path)
        if case_err is not None:
            degradations.append(case_err)
    else:
        degradations.append({
            "path": os.path.join(args.input, "case.json"),
            "issue": "missing_case_json",
        })

    case_id = core.extract_case_id(case_data)
    task_text = None
    if task_path:
        task_text, task_err = core.read_text_safe(task_path)
        if task_err is not None:
            degradations.append(task_err)
        if not case_id and task_text:
            case_id = core.extract_case_id_from_text(task_text)
    if not case_id:
        case_id = "unknown_case"
        degradations.append({"issue": "case_id_not_found", "detail": "case.json/task.md 未提供 case_id"})

    contract = core.extract_output_contract(case_data)

    runtime = {
        "started_at": core.utcnow(),
        "input_dir": args.input,
        "materials_found": len(files),
        "python": sys.version.split()[0],
        "skill": "academic-format-adaptation",
    }

    verify_rc = 0

    # ---- init: partial skeleton first ------------------------------------ #
    if do_init:
        # Extract readable materials now so binary/oversized degradations are
        # recorded in result.json (not only in the verification report).
        _, mat_degradations = core.extract_text_materials(args.input, args.work)
        skeleton = core.build_skeleton(case_id, contract)
        skeleton["runtime"] = runtime
        for d in degradations + mat_degradations:
            skeleton.setdefault("degradations", []).append(d)
        core.record_change(skeleton, "init", "main.py --init 落盘/合并 result.json 骨架 (status=partial)")

        existing = None
        if not args.no_merge and os.path.exists(result_path):
            existing, _err = core.load_json_safe(result_path)
        merged = core.merge_result(existing, skeleton, preserve=True) if isinstance(existing, dict) else skeleton
        # Preserve honest status when skeleton adds nothing new beyond a rerun.
        if isinstance(existing, dict) and merged.get("status") == "partial" and existing.get("status") in core._STATUS_VALUES:
            merged["status"] = existing["status"]
        core.record_change(merged, "merge", "main.py --init 增量合并（不覆盖已有内容）")
        werr = core.write_json_safe(result_path, merged)
        if werr is not None:
            errors.append(werr)

    # ---- verify ----------------------------------------------------------- #
    report = None
    if do_verify:
        result_data = None
        if os.path.exists(result_path):
            result_data, rerr = core.load_json_safe(result_path)
            if rerr is not None:
                errors.append(rerr)
        if not isinstance(result_data, dict):
            result_data = core.build_skeleton(case_id, contract)
            errors.append({"path": result_path, "issue": "result_json_invalid_or_missing"})

        material_roots = [args.input, os.path.join(args.work, "extracted")]
        report = core.verify_result(result_data, case_data, material_roots)
        report["degradations"] = degradations
        report["errors_from_runtime"] = errors
        core.write_json_safe(report_path, report)

        # Stamp a verification summary onto result.json (idempotent).
        result_data["verification"] = {
            "ok": report["ok"],
            "quotes_checked": report["quotes"]["quotes_checked"],
            "quotes_located": report["quotes"]["quotes_located"],
        }
        core.write_json_safe(result_path, result_data)

        verify_rc = 2 if not report["ok"] else 0

    summary = {
        "ok": True,
        "case_id": case_id,
        "case_path": case_path,
        "task_path": task_path,
        "materials_found": len(files),
        "degradations": len(degradations),
        "errors": len(errors),
        "result_path": result_path,
        "report_path": report_path,
        "verify_report_ok": report["ok"] if report else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return verify_rc if do_verify else 0


if __name__ == "__main__":
    sys.exit(main())
