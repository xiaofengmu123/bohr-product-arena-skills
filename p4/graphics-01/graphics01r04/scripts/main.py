#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-figure-editor entry point.

Discovery -> skeleton (partial, written immediately) -> render spec -> export
-> validate -> finalize. Standard library only. Idempotent: re-running with a
spec file re-renders and re-validates without losing earlier provenance.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="sci-figure-editor",
        description="Scientific figure generation: material -> editable SVG -> PDF/PNG export with provenance.",
    )
    ap.add_argument("--input", default="/input", help="input directory (default /input)")
    ap.add_argument("--case", default=None, help="case.json path (default: <input>/case.json)")
    ap.add_argument("--task", default=None, help="task.md path (default: <input>/task.md)")
    ap.add_argument("--out", default="/app/output", help="output directory (default /app/output)")
    ap.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    ap.add_argument("--spec", default=None, help="figure spec JSON path; defaults to <work>/figure_spec.json")
    ap.add_argument("--no-export", action="store_true", help="skip PNG/PDF export")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    started = time.time()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    case_path = a.case or os.path.join(a.input, "case.json")
    task_path = a.task or os.path.join(a.input, "task.md")
    result_path = os.path.join(a.out, "result.json")

    # ---- Phase 0: skeleton (always written first, even on input failure) ----
    result = core.build_skeleton(case_path, task_path, a.input)
    core.atomic_write_json(result_path, result)

    info = core.discover_input(a.input, case_path)
    if info.get("task") is None and task_path and os.path.exists(task_path):
        info["task"] = task_path
    for m in info.get("materials", []):
        if not any(s.get("path") == m for s in result["sources"]):
            result["sources"].append({
                "ref": os.path.basename(m), "path": m, "locator": "",
                "used_for": "source material (pending extraction)",
            })

    # ---- Load spec ----
    spec_path = a.spec or os.path.join(a.work, "figure_spec.json")
    spec = None
    spec_warnings = []
    if os.path.exists(spec_path):
        spec, spec_warnings = core.load_spec(spec_path)
        if spec is None:
            result["degradations"].extend(spec_warnings)
            result["uncertainty"].append({
                "item": "spec", "reason": "figure_spec.json unreadable: %s" % "; ".join(spec_warnings),
                "impact": "cannot render figure", "mitigation": "fix the spec and re-run",
            })
    else:
        # Also accept entities.json as a minimal fallback input.
        entities_path = os.path.join(a.work, "entities.json")
        if os.path.exists(entities_path):
            ent_data, ent_warn = core.load_json_safe(entities_path)
            if isinstance(ent_data, dict):
                spec = {
                    "figure": ent_data.get("figure", {}),
                    "entities": ent_data.get("entities", []),
                    "relations": ent_data.get("relations", []),
                    "legends": ent_data.get("legends", []),
                    "annotations": ent_data.get("annotations", []),
                    "sources": ent_data.get("sources", []),
                }
            else:
                result["degradations"].extend(ent_warn)

    if spec is None:
        # Honest partial: extraction pending.
        result["status"] = "partial"
        result["uncertainty"].append({
            "item": "extraction", "reason": "no figure_spec.json present in /app/work",
            "impact": "figure not yet rendered", "mitigation": "extract entities from materials and write figure_spec.json, then re-run",
        })
        core.log_change(result, "phase0-done", "skeleton only; extraction pending")
        core.atomic_write_json(result_path, result)
        return 0

    # ---- Phase 2: render SVG ----
    svg_path = os.path.join(a.out, "figure01.svg")
    try:
        core.render_spec_to_svg(spec, svg_path)
        result["editable_source_files"] = [{
            "path": "figure01.svg", "kind": "svg", "layers": True,
            "editable_text": True, "note": "layered: background/relations/entities/labels/legend/annotations",
        }]
        core.log_change(result, "render", "rendered layered SVG from figure_spec.json")
    except Exception as exc:
        result["errors"].append("render failed: %s" % exc)
        result["uncertainty"].append({
            "item": "render", "reason": str(exc), "impact": "no SVG produced",
            "mitigation": "fix figure_spec.json and re-run",
        })

    # ---- Phase 3: export ----
    if not a.no_export and os.path.exists(svg_path):
        try:
            exports, degradations = core.export_figures(spec, svg_path, a.out)
            result["export_files"] = exports
            result["degradations"].extend(degradations)
            core.log_change(result, "export", "exported %d file(s)" % len(exports))
        except Exception as exc:
            result["errors"].append("export failed: %s" % exc)
            result["export_files"] = [{
                "path": "", "format": "none", "width_px": 0, "height_px": 0, "dpi": 0,
                "color_space": "RGB", "font_check": "none", "note": "export failed: %s" % exc,
            }]
    elif a.no_export:
        core.log_change(result, "export-skipped", "--no-export flag set; SVG only")

    # ---- Phase 3b: evidence merge ----
    result["provenance"] = core.build_provenance(spec)
    for s in core.build_sources(spec, a.input):
        merged = False
        for x in result["sources"]:
            if x.get("path") == s.get("path"):
                if not x.get("locator") and s.get("locator"):
                    x["locator"] = s.get("locator")
                if not x.get("used_for") and s.get("used_for"):
                    x["used_for"] = s.get("used_for")
                merged = True
                break
        if not merged:
            result["sources"].append(s)
    for u in core.build_uncertainty_from_spec(spec):
        result["uncertainty"].append(u)

    # ---- Phase 4: validate + finalize ----
    issues = core.validate_result(result, a.out)
    result["validation"] = {"issues": issues, "passed": len(issues) == 0}

    if result.get("errors"):
        result["status"] = "failed"
    elif issues:
        result["status"] = "partial"
        result["uncertainty"].append({
            "item": "validation", "reason": "%d issue(s): %s" % (len(issues), "; ".join(issues[:5])),
            "impact": "deliverable has gaps", "mitigation": "fix the listed issues and re-run",
        })
    else:
        result["status"] = "complete"

    result["runtime"]["finished_at"] = core.now_iso()
    result["runtime"]["elapsed_s"] = round(time.time() - started, 2)
    core.log_change(result, "finalize", "status=%s" % result["status"])
    core.atomic_write_json(result_path, result)

    print(json.dumps({
        "ok": True, "case_id": result["case_id"], "status": result["status"],
        "svg": os.path.basename(svg_path) if os.path.exists(svg_path) else None,
        "export_files": [f.get("path") for f in result["export_files"]],
        "validation_issues": len(issues),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
