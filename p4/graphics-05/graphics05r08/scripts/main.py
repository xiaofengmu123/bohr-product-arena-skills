# -*- coding: utf-8 -*-
"""main.py — CLI entry point for the graphical-abstract skill pipeline.

Run with `python scripts/main.py --input /input --output /app/output --work /app/work`.

The pipeline is split into stages so the Agent can interleave its own semantic
extraction (editing /app/work/figure_spec.json) between deterministic stages:

    extract   discover input, write partial result.json + figure_spec template
    build     render deterministic layered SVG + try PNG/PDF previews
    validate  structural + provenance checks, rubric trace, source mapping
    finalize  set honest status and merge manifest into result.json

Only stdlib is imported. All paths are relative to the script invocation cwd
or resolved via the explicit --input/--output/--work arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import core_impl as core


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_spec(work: Path) -> dict:
    spec = core.load_json(work / "figure_spec.json")
    if spec is None:
        sys.stderr.write("error: figure_spec.json missing or unparsable in work dir\n")
        sys.exit(2)
    return spec


def _load_result(out: Path) -> dict:
    result = core.load_json(out / "result.json")
    if result is None:
        result = {"case_id": "unknown-case", "status": "partial"}
    return result


def stage_extract(args) -> int:
    summary = core.normalize_case(args.input, args.work, args.output)
    print(f"[extract] case_id={summary['case_id']} "
          f"materials={len(summary['materials'])} "
          f"case_json={summary['has_case_json']} task_md={summary['has_task_md']}")
    print(f"[extract] partial skeleton -> {summary['skeleton']}")
    return 0


def stage_build(args) -> int:
    work, out = Path(args.work), Path(args.output)
    spec = _load_spec(work)
    case_id = spec.get("case_id") or "unknown-case"
    out.mkdir(parents=True, exist_ok=True)
    svg_path = out / f"fig_{case_id}.svg"
    layout_report = core.build_svg(spec, svg_path)
    preview = core.try_render(svg_path, out, case_id)
    layers = core.list_layers(svg_path)
    manifest = {
        "case_id": case_id,
        "svg": str(svg_path),
        "png": preview.get("png"),
        "pdf": preview.get("pdf"),
        "renderer": preview.get("renderer"),
        "export_status": preview.get("status"),
        "layers": layers,
        "text_editable": core.count_text_and_sources(svg_path)["text"] > 0,
        "entity_count": len([e for e in (spec.get("entities") or []) if isinstance(e, dict)]),
        "relation_count": len([r for r in (spec.get("relations") or []) if isinstance(r, dict)]),
        "data_count": len([d for d in (spec.get("data") or []) if isinstance(d, dict)]),
        "overflow": layout_report.get("overflow"),
        "max_y": layout_report.get("max_y"),
    }
    core.write_json(work / "fig_manifest.json", manifest)
    print(f"[build] svg={svg_path} layers={len(layers)} "
          f"export={preview['status']} renderer={preview['renderer'] or 'none'}")
    if preview["status"] != "rendered":
        print("[build] no renderer found; keeping editable SVG, recording preview_not_rendered")
    return 0


def stage_validate(args) -> int:
    work, out = Path(args.work), Path(args.output)
    spec = _load_spec(work)
    manifest = core.load_json(work / "fig_manifest.json") or {}
    case_id = spec.get("case_id") or "unknown-case"
    svg_path = out / f"fig_{case_id}.svg"
    result = _load_result(out)
    report = core.validate_figure(svg_path, spec, manifest, args.input, args.work)
    trace = core.build_rubric_trace(report, manifest, result)
    core.write_json(work / "validation_report.json", report)
    core.write_json(work / "rubric_trace.json", trace)
    print(f"[validate] ok={report['ok']} text={report['text_count']} "
          f"data-source={report['source_count']} coverage={report['coverage']:.0%} "
          f"critical={len(report['critical'])} warnings={len(report['warnings'])}")
    for c in report["critical"]:
        print(f"[validate] CRITICAL: {c}")
    for w in report["warnings"]:
        print(f"[validate] warn: {w}")
    return 0 if report["ok"] else 3


def _build_editable_source(manifest: dict) -> dict:
    return {
        "svg": manifest.get("svg"),
        "layers": manifest.get("layers") or [],
        "text_editable": bool(manifest.get("text_editable", False)),
    }


def _build_exports(manifest: dict) -> dict:
    return {
        "png": manifest.get("png"),
        "pdf": manifest.get("pdf"),
        "renderer": manifest.get("renderer"),
        "status": manifest.get("export_status", "preview_not_rendered"),
    }


def stage_finalize(args) -> int:
    work, out = Path(args.work), Path(args.output)
    spec = _load_spec(work)
    manifest = core.load_json(work / "fig_manifest.json") or {}
    report = core.load_json(work / "validation_report.json") or {}
    result = _load_result(out)

    # canonical fields only if the case contract already declared them
    if "editable_source" in result:
        result["editable_source"] = _build_editable_source(manifest)
    if "exports" in result:
        result["exports"] = _build_exports(manifest)
    if "graphical_abstract" in result and isinstance(spec.get("graphical_abstract"), dict):
        result["graphical_abstract"] = spec["graphical_abstract"]
    if "entities" in result:
        result["entities"] = spec.get("entities") or []
    if "relations" in result:
        result["relations"] = spec.get("relations") or []
    if "data_mapping" in result:
        result["data_mapping"] = spec.get("data") or []
    if "legend" in result:
        result["legend"] = spec.get("legend") or []

    # provenance + honesty fields (always, but keep user values when present)
    for field in ("sources", "uncertainty", "pending_human", "limitations"):
        if field in result:
            existing = result.get(field)
            if not existing:
                result[field] = spec.get(field) or []

    # status is exactly completed | partial | failed; an empty template is not
    # a completed figure, so require actual content before declaring completion
    has_content = core.spec_has_content(spec)
    if result.get("status") == "failed":
        pass
    elif report.get("ok") and manifest.get("svg") and has_content:
        result["status"] = "completed"
    elif manifest.get("svg"):
        result["status"] = "partial"
    else:
        result["status"] = "failed"

    if not has_content and result["status"] != "failed":
        lims = result.get("limitations") or []
        if isinstance(lims, list):
            note = "figure_spec has no entities/relations/data and empty four panels"
            if note not in [str(x) for x in lims]:
                lims.append(note)
            result["limitations"] = lims

    clog = result.get("change_log") or []
    if isinstance(clog, list):
        clog.append({
            "step": "finalize",
            "at": now_iso(),
            "status": result["status"],
            "export": (manifest.get("export_status") or "preview_not_rendered"),
            "coverage": report.get("coverage"),
        })
        result["change_log"] = clog

    # attach the rubric trace as an auxiliary verifiability artifact
    trace = core.load_json(work / "rubric_trace.json")
    if trace:
        result["rubric_trace"] = trace

    core.write_json(out / "result.json", result)
    print(f"[finalize] status={result['status']} -> {out / 'result.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Graphical abstract skill pipeline (extract/build/validate/finalize).")
    p.add_argument("--input", default="/input", help="runtime input dir (case.json, task.md, materials)")
    p.add_argument("--output", default="/app/output", help="final deliverables dir")
    p.add_argument("--work", default="/app/work", help="scratch/intermediate dir")
    p.add_argument("--stage", default="all",
                   choices=["all", "extract", "build", "validate", "finalize"],
                   help="run a single stage or the whole pipeline")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    stages = ["extract", "build", "validate", "finalize"] if args.stage == "all" else [args.stage]
    rc = 0
    for st in stages:
        if st == "extract":
            rc = stage_extract(args)
        elif st == "build":
            rc = stage_build(args)
        elif st == "validate":
            rc = stage_validate(args)
        elif st == "finalize":
            rc = stage_finalize(args)
        if rc not in (0, 3):
            return rc
    return 0 if args.stage == "all" or rc in (0, 3) else rc


if __name__ == "__main__":
    sys.exit(main())
