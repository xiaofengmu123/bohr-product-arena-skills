#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — orchestration entry point for the evidence-first figure Skill.

Subcommands (run with `python scripts/main.py <cmd> ...`):
  init      write a legal partial result.json skeleton immediately
  extract   deterministic fallback IR extraction -> work/ir.json
  build     ir.json -> layered SVG (with per-element evidence attributes)
  validate  structural self-check -> work/checks.json
  render    best-effort PNG/PDF probe (honest: skips if no renderer)
  merge     assemble result.json from ir.json + checks + render
  finalize  set final status and finish time
  run       init -> extract -> build -> validate -> render -> merge -> finalize

All paths default to the runtime contract (/input, /app/output, /app/work) but
can be overridden for local self-tests. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core_impl as core  # noqa: E402


def _wj(path: str, obj: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _rj(path: str) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _load_ir(work_dir: str) -> Dict[str, Any]:
    p = os.path.join(work_dir, "ir.json")
    if os.path.exists(p):
        return _rj(p)
    return {}


def _save_ir(work_dir: str, ir: Dict[str, Any]) -> str:
    p = os.path.join(work_dir, "ir.json")
    _wj(p, ir)
    return p


def _initial_change_log(action: str) -> List[Dict[str, Any]]:
    return [{"ts": core.now_iso(), "action": action}]


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #


def cmd_init(args) -> int:
    case, _ = core.load_case_json(args.input)
    case_id = core.extract_case_id(case, args.input)
    out = os.path.join(args.output, "result.json")
    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": "in_progress",
        "title": "",
        "entities": [], "relations": [], "data_encoding": [],
        "legend_annotations": [], "editable_sources": [], "exports": [],
        "sources": [], "evidence_map": {}, "uncertainty": [],
        "pending_human": [], "change_log": _initial_change_log(
            "init: partial result skeleton written (before extraction)"),
        "checks": [], "limitations": [], "artifacts": [],
        "runtime": {"started_at": core.now_iso(), "finished_at": None},
    }
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        for key in oc.keys():
            result[key] = None
    _wj(out, result)
    print(json.dumps({"ok": True, "case_id": case_id,
                      "path": out, "status": "in_progress"}, ensure_ascii=False))
    return 0


def cmd_extract(args) -> int:
    files = core.discover_input_files(args.input)
    base = args.input
    case, _ = core.load_case_json(args.input)
    kind = getattr(args, "kind", "") or ""
    if not kind:
        # prefer case-declared kind, else default apparatus
        kind = (case.get("kind") or case.get("figure_kind") or "apparatus")
    ir = core.extract_ir(files, base, kind=kind)
    ir["title"] = (getattr(args, "title", "") or ""
                   ) or (case.get("title") or ir["title"])
    ir_path = _save_ir(args.work, ir)
    print(json.dumps({"ok": True, "ir": ir_path,
                      "entities": len(ir.get("entities") or []),
                      "relations": len(ir.get("relations") or []),
                      "steps": len(ir.get("steps") or [])}, ensure_ascii=False))
    return 0


def cmd_build(args) -> int:
    ir = _load_ir(args.work)
    if not ir:
        print(json.dumps({"ok": False, "error": "ir.json missing; run extract first"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    svg_path = getattr(args, "svg", "") or os.path.join(args.work, "figure.svg")
    info = core.build_svg(ir, svg_path)
    print(json.dumps({"ok": True, "svg": svg_path, **info}, ensure_ascii=False))
    return 0


def cmd_validate(args) -> int:
    ir = _load_ir(args.work)
    svg_path = getattr(args, "svg", "") or os.path.join(args.work, "figure.svg")
    if not os.path.exists(svg_path):
        print(json.dumps({"ok": False, "error": "svg missing"}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    checks = core.validate_svg(ir, svg_path)
    out = os.path.join(args.work, "checks.json")
    _wj(out, checks)
    failed = [c for c in checks if c["level"] == "critical" and not c["passed"]]
    print(json.dumps({"ok": not failed, "checks": out,
                      "failed": [c["id"] for c in failed]}, ensure_ascii=False))
    return 0 if not failed else 3


def cmd_render(args) -> int:
    svg_path = getattr(args, "svg", "") or os.path.join(args.work, "figure.svg")
    if not os.path.exists(svg_path):
        print(json.dumps({"ok": False, "error": "svg missing"}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    render = core.render_svg(svg_path, args.output)
    _wj(os.path.join(args.work, "render.json"), render)
    print(json.dumps(render, ensure_ascii=False))
    return 0


def cmd_merge(args) -> int:
    case, _ = core.load_case_json(args.input)
    case_id = core.extract_case_id(case, args.input)
    files = core.discover_input_files(args.input)
    sources = core.source_registry_from_files(files, args.input)
    ir = _load_ir(args.work)
    if not ir:
        ir = core.extract_ir(files, args.input, kind="apparatus")
    evidence_map = core.build_evidence_map(ir)
    svg_path = getattr(args, "svg", "") or os.path.join(args.work, "figure.svg")
    editable_sources = core.build_editable_sources(svg_path, args.work)
    render = _rj(os.path.join(args.work, "render.json"))
    if not render:
        render = {"render_status": "skipped", "exports": [], "note": "not attempted"}
    checks = _rj(os.path.join(args.work, "checks.json"))
    if not checks:
        checks = core.validate_svg(ir, svg_path) if os.path.exists(svg_path) else []
    started = core.now_iso()
    # preserve start time from existing result if present
    prev = _rj(os.path.join(args.output, "result.json"))
    if prev.get("runtime", {}).get("started_at"):
        started = prev["runtime"]["started_at"]
    change_log = list(prev.get("change_log") or [])
    change_log.append({"ts": core.now_iso(), "action": "merge: assembled result.json"})
    status = getattr(args, "status", "") or "partial"
    result = core.build_result(
        case_id=case_id, case=case, ir=ir, evidence_map=evidence_map,
        sources=sources, editable_sources=editable_sources,
        exports=render.get("exports") or [], checks=checks, status=status,
        change_log=change_log, render=render, work_dir=args.work, started_at=started)
    _wj(os.path.join(args.output, "result.json"), result)
    # also persist evidence map standalone for independent review
    _wj(os.path.join(args.work, "evidence_map.json"), evidence_map)
    print(json.dumps({"ok": True, "status": status,
                      "quote_coverage": evidence_map["coverage"]["quote_coverage"],
                      "result": os.path.join(args.output, "result.json")},
                     ensure_ascii=False))
    return 0


def cmd_finalize(args) -> int:
    out = os.path.join(args.output, "result.json")
    result = _rj(out)
    if not result:
        result = {"case_id": core.extract_case_id({}, args.input), "change_log": []}
    result["status"] = getattr(args, "status", "complete")
    if "runtime" not in result:
        result["runtime"] = {}
    result["runtime"]["finished_at"] = core.now_iso()
    result.setdefault("change_log", []).append(
        {"ts": core.now_iso(), "action": "finalize: status=%s" % result["status"]})
    _wj(out, result)
    print(json.dumps({"ok": True, "status": result["status"], "path": out}, ensure_ascii=False))
    return 0


def cmd_run(args) -> int:
    codes = []
    codes.append(cmd_init(args))
    codes.append(cmd_extract(args))
    codes.append(cmd_build(args))
    codes.append(cmd_validate(args))
    codes.append(cmd_render(args))
    codes.append(cmd_merge(args))
    # final status: complete only if svg built and no critical check failed
    checks = _rj(os.path.join(args.work, "checks.json"))
    critical_failed = [c for c in checks if c.get("level") == "critical" and not c.get("passed")]
    svg_default = os.path.join(args.work, "figure.svg")
    final_status = "complete" if (not critical_failed and os.path.exists(
        getattr(args, "svg", "") or svg_default)) else "partial"
    codes.append(cmd_finalize(argparse.Namespace(status=final_status, **{
        "output": args.output, "input": args.input, "work": args.work})))
    return max(codes)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    common.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    common.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))

    p = argparse.ArgumentParser(
        prog="main.py", parents=[common],
        description="evidence-first scientific figure pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[common], help="write partial result skeleton")
    ex = sub.add_parser("extract", parents=[common],
                        help="deterministic fallback IR extraction")
    ex.add_argument("--kind", default="")
    ex.add_argument("--title", default="")
    b = sub.add_parser("build", parents=[common], help="IR -> layered SVG")
    b.add_argument("--svg", default="")
    v = sub.add_parser("validate", parents=[common], help="self-check SVG vs IR")
    v.add_argument("--svg", default="")
    r = sub.add_parser("render", parents=[common], help="best-effort PNG/PDF probe")
    r.add_argument("--svg", default="")
    m = sub.add_parser("merge", parents=[common], help="assemble result.json")
    m.add_argument("--svg", default="")
    m.add_argument("--status", default="")
    fz = sub.add_parser("finalize", parents=[common], help="set final status")
    fz.add_argument("--status", default="complete")
    sub.add_parser("run", parents=[common], help="full pipeline in one pass")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    dispatch = {
        "init": cmd_init, "extract": cmd_extract, "build": cmd_build,
        "validate": cmd_validate, "render": cmd_render, "merge": cmd_merge,
        "finalize": cmd_finalize, "run": cmd_run,
    }
    fn = dispatch[args.cmd]
    try:
        return fn(args)
    except Exception as exc:  # noqa: BLE001
        # never leave the caller without a legal artifact
        try:
            out = os.path.join(args.output, "result.json")
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            if not os.path.exists(out):
                _wj(out, {"case_id": "unknown", "status": "failed",
                          "change_log": [{"ts": core.now_iso(),
                                          "action": "crash: %s" % exc}],
                          "limitations": [str(exc)],
                          "entities": [], "relations": [], "data_encoding": [],
                          "legend_annotations": [], "editable_sources": [], "exports": [],
                          "sources": [], "uncertainty": [], "pending_human": [],
                          "checks": [], "artifacts": [], "runtime": {}})
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
