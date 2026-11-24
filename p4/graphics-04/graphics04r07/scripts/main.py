#!/usr/bin/env python3
"""Entry point for the apparatus/technical-route diagram skill.

Subcommands
    run        discover input -> skeleton -> build -> validate -> render -> result
    init       write result.json skeleton (first-three-minutes hard rule)
    build      /app/work/ir.json -> /app/work/figure.svg
    validate   validate IR + SVG and write checks.json

All paths default to the harness contract (/input, /app/work, /app/output) but
are overridable for local use. The process never raises on bad input; it writes
an honest, valid result.json and exits 0 so the orchestrator can continue.

Usage examples (run from the skill root):
    python scripts/main.py run
    python scripts/main.py run --case ./demo/input --work ./demo/work --out ./demo/output
    python scripts/main.py init --case-id demo-1
    python scripts/main.py build --ir ./demo/work/ir.json --out ./demo/work/figure.svg
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

from core_impl import (
    DEFAULT_CONTRACT,
    STATUSES,
    VERSION,
    append_change,
    auto_extract_ir,
    build_svg,
    discover_case,
    ensure_list,
    json_load_safe,
    load_case,
    merge_contract,
    normalize_ir,
    read_text_safe,
    render_preview,
    skeleton,
    finalize,
    utcnow,
    validate_ir,
    validate_svg,
    write_json,
)


def _exists(p):
    return bool(p) and os.path.exists(p)


def _default_work(args):
    return args.work or "/app/work"


def _default_out(args):
    return args.out or "/app/output"


def cmd_init(args):
    case_path = args.case or "/input/case.json"
    if _exists(case_path):
        meta = load_case(case_path)
        case_id = args.case_id or meta.get("case_id", "unknown_case")
        title = args.title or meta.get("title", "diagram")
        contract = meta.get("contract", DEFAULT_CONTRACT)
    else:
        case_id = args.case_id or "unknown_case"
        title = args.title or "diagram"
        contract = DEFAULT_CONTRACT
    rec = skeleton(case_id, title, contract, status="in_progress")
    append_change(rec, "init", "skeleton written (input=%s)" % case_path)
    out_path = args.out or "/app/output/result.json"
    write_json(out_path, rec)
    print("skeleton -> %s" % out_path)
    return 0


def cmd_build(args):
    ir_path = args.ir or "/app/work/ir.json"
    ir = {}
    if _exists(ir_path):
        ir, err = json_load_safe(ir_path, fallback={})
        if err:
            print("warning: IR %s: %s" % (ir_path, err))
        if not isinstance(ir, dict):
            ir = {}
    ir = normalize_ir(ir)
    svg_text = build_svg(ir)
    out_path = args.out or "/app/work/figure.svg"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    print("svg -> %s (%d bytes)" % (out_path, len(svg_text.encode("utf-8"))))
    return 0


def cmd_validate(args):
    ir_path = args.ir or "/app/work/ir.json"
    svg_path = args.svg or "/app/work/figure.svg"
    ir, err = json_load_safe(ir_path, fallback={})
    if err:
        print("warning: IR %s: %s" % (ir_path, err))
    ir = normalize_ir(ir) if isinstance(ir, dict) else normalize_ir({})
    ir_check = validate_ir(ir)
    svg_text, note = read_text_safe(svg_path) if _exists(svg_path) else ("", "missing_svg")
    svg_check = validate_svg(svg_text, expected_entities=len(ensure_list(ir.get("entities")))) if svg_text else {"parsed": False, "checks": [{"level": "error", "code": "missing_svg", "message": "SVG not found"}]}
    result = {
        "ir_checks": ir_check["checks"],
        "svg_checks": svg_check.get("checks", []),
        "errors": ir_check["errors"] + svg_check.get("errors", 0),
        "warnings": ir_check["warnings"] + svg_check.get("warnings", 0),
        "generated_at": utcnow(),
    }
    out_path = args.out or "/app/work/checks.json"
    write_json(out_path, result)
    print("checks -> %s (errors=%d warnings=%d)" % (out_path, result["errors"], result["warnings"]))
    return 0


def cmd_run(args):
    t0 = utcnow()
    input_dir = args.input or "/input"
    work_dir = _default_work(args)
    out_dir = _default_out(args)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    result_path = os.path.join(out_dir, "result.json")
    degradations = []
    errors = []
    pending = []

    # 1) Discover input without assuming names.
    disc = discover_case(input_dir)
    case_path = args.case or disc.get("case_path")
    task_path = disc.get("task_path")
    for note in disc.get("notes", []):
        degradations.append({"stage": "discover", "note": note})

    # 2) Load case and copy output_contract verbatim.
    meta = load_case(case_path)
    if meta.get("error"):
        degradations.append({"stage": "case_json", "note": meta["error"]})
    case_id = meta.get("case_id", "unknown_case")
    title = meta.get("title", "diagram")
    contract = meta.get("contract", DEFAULT_CONTRACT)

    # 3) Write skeleton first (first-three-minutes hard rule), then improve.
    rec = skeleton(case_id, title, contract, status="in_progress")
    append_change(rec, "run_start", "case=%s task=%s" % (case_path, task_path))
    write_json(result_path, rec)

    task_text, task_note = read_text_safe(task_path) if task_path else ("", "no_task_md")
    if task_note:
        degradations.append({"stage": "task_md", "note": task_note})

    # 4) IR: prefer agent-authored /app/work/ir.json, else an ir.json found in
    #    the input directory; else auto-extract a low-confidence skeleton.
    ir_path = args.ir or os.path.join(work_dir, "ir.json")
    if not _exists(ir_path) and input_dir:
        candidate = os.path.join(input_dir, "ir.json")
        if _exists(candidate):
            ir_path = candidate
    ir = {}
    if _exists(ir_path):
        ir, err = json_load_safe(ir_path, fallback={})
        if err:
            degradations.append({"stage": "ir_json", "note": err})
        if not isinstance(ir, dict):
            ir = {}
            errors.append("ir.json is not a JSON object; using empty IR")
    if not ir.get("entities") and args.auto_extract:
        ir = auto_extract_ir(task_text, title=title, case_id=case_id)
        degradations.append({"stage": "ir", "note": "auto_extract fallback (agent-authored IR absent)"})
    ir = normalize_ir(ir)
    ir["meta"].setdefault("title", title)
    ir["meta"].setdefault("case_id", case_id)
    write_json(ir_path, ir)

    # 5) Build layered SVG.
    svg_text = build_svg(ir)
    svg_path = os.path.join(work_dir, "figure.svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    svg_bytes = len(svg_text.encode("utf-8"))

    # 6) Validate IR + SVG.
    ir_check = validate_ir(ir)
    svg_check = validate_svg(svg_text, expected_entities=len(ensure_list(ir.get("entities"))))
    checks = ir_check["checks"] + svg_check.get("checks", [])
    if ir_check["errors"] or svg_check.get("errors"):
        errors.append("validation errors=%d (IR) + %d (SVG)" % (ir_check["errors"], svg_check.get("errors", 0)))
    for o in ir_check.get("overflow", []):
        pending.append({"kind": "canvas_overflow", "item": o})

    # 7) Optional rendering preview (honest skip).
    exports = []
    if args.no_render:
        exports = [{"path": None, "format": "png", "status": "skipped", "reason": "no_render_requested"}]
        degradations.append({"stage": "render", "note": "rendering disabled by --no-render"})
    else:
        exports = render_preview(svg_path, out_dir)

    # 8) Merge into result.json and finalize.
    rec = merge_contract(rec, ir, svg_path, os.path.basename(svg_path), svg_bytes, exports, checks)
    # Copy figure.svg into /app/output so deliverables are self-contained.
    out_svg = os.path.join(out_dir, "figure.svg")
    try:
        with open(svg_path, "r", encoding="utf-8") as fh:
            _svg_text = fh.read()
        with open(out_svg, "w", encoding="utf-8") as fh:
            fh.write(_svg_text)
        rec["editable_sources"][0]["path"] = out_svg
        for a in rec.get("artifacts", []):
            if a.get("role") == "editable_source":
                a["path"] = out_svg
    except OSError as exc:
        degradations.append({"stage": "copy_svg", "note": exc.__class__.__name__})

    status = args.status
    if status not in STATUSES:
        # Derive an honest status from evidence.
        if not ir.get("entities"):
            status = "insufficient_evidence"
        elif errors:
            status = "partial"
        else:
            status = "complete"
    rec = finalize(rec, status, errors=errors, degradations=degradations, pending=pending)
    append_change(rec, "checks_summary", "errors=%s warnings=%s" % (rec.get("errors") and len(rec["errors"]), len(ensure_list(rec.get("checks")))))
    rec["runtime"]["started"] = t0
    write_json(result_path, rec)
    print("result -> %s (status=%s)" % (result_path, rec["status"]))
    return 0


def build_argparser():
    ap = argparse.ArgumentParser(prog="main.py", description="apparatus/technical-route diagram skill (v%s)" % VERSION)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", default=None, help="case.json path (default /input/case.json)")
    common.add_argument("--input", default=None, help="input directory (default /input)")
    common.add_argument("--work", default=None, help="work directory (default /app/work)")
    common.add_argument("--out", default=None, help="output directory (default /app/output)")
    common.add_argument("--ir", default=None, help="IR JSON path (default /app/work/ir.json)")
    common.add_argument("--svg", default=None, help="SVG path for validate (default /app/work/figure.svg)")
    common.add_argument("--title", default=None, help="diagram title override")
    common.add_argument("--case-id", default=None, help="case id override")
    sub = ap.add_subparsers(dest="command")

    sp_init = sub.add_parser("init", parents=[common], help="write result skeleton")
    sp_init.set_defaults(func=cmd_init)

    sp_build = sub.add_parser("build", parents=[common], help="IR -> SVG")
    sp_build.set_defaults(func=cmd_build)

    sp_val = sub.add_parser("validate", parents=[common], help="validate IR + SVG")
    sp_val.set_defaults(func=cmd_validate)

    sp_run = sub.add_parser("run", parents=[common], help="full pipeline")
    sp_run.add_argument("--status", default=None, choices=STATUSES, help="force result status")
    sp_run.add_argument("--auto-extract", dest="auto_extract", action="store_true", default=True, help="auto-extract IR when absent (default on)")
    sp_run.add_argument("--no-auto-extract", dest="auto_extract", action="store_false", help="disable IR auto-extraction")
    sp_run.add_argument("--no-render", action="store_true", help="skip PNG/PDF rendering")
    sp_run.set_defaults(func=cmd_run)
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_argparser()
    # Graceful default: bare flags or unknown first token behave like 'run'.
    known = {"init", "build", "validate", "run", "-h", "--help"}
    if not argv or argv[0] not in known:
        argv = ["run"] + argv
    args = ap.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except SystemExit:
        raise
    except Exception:
        # Never crash the orchestrator: record and write a fallback result.
        work = _default_work(args)
        out = _default_out(args)
        os.makedirs(work, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        fallback = skeleton(getattr(args, "case_id", None) or "unknown_case", "diagram", DEFAULT_CONTRACT, status="failed")
        fallback["errors"] = ["unhandled exception: %s" % traceback.format_exc(limit=3)]
        fallback["degradations"] = [{"stage": "fatal", "note": "caught and recovered"}]
        finalize(fallback, "failed")
        try:
            write_json(os.path.join(out, "result.json"), fallback)
        except OSError:
            pass
        print("fatal error recovered; result.json status=failed", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
