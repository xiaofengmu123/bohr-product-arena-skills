# -*- coding: utf-8 -*-
"""main.py -- deterministic graphical-abstract pipeline driver.

Usage (relative to /skills/weekly-skill)::

    python scripts/main.py extract    # normalize + persist partial skeleton
    python scripts/main.py build      # spec -> layered SVG + previews
    python scripts/main.py validate   # validate + backfill evidence into result
    python scripts/main.py finalize   # set honest status + fingerprint
    python scripts/main.py run        # extract -> build -> validate -> finalize

Defaults: --input /input --output /app/output --work /app/work.
Exit code 0 means a valid JSON artefact exists; failures are recorded, not fatal.
"""
from __future__ import annotations

import argparse
import json
import os
import re as _re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Graphical abstract deterministic pipeline")
    p.add_argument("command", choices=["extract", "build", "validate", "finalize", "run"],
                   help="pipeline stage or full run")
    p.add_argument("--input", default="/input", help="input directory")
    p.add_argument("--output", default="/app/output", help="output directory")
    p.add_argument("--work", default="/app/work", help="working directory")
    return p.parse_args(argv)


def ensure_dirs(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(core_impl.canonical_json(obj))
    return path


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    text, _deg = core_impl.read_text_any(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def svg_path_for(args, spec):
    cid = (spec or {}).get("case_id", "case")
    return os.path.join(args.output, "fig_%s.svg" % _safe(cid))


def _safe(cid):
    return _re.sub(r"[^0-9a-zA-Z\-_]+", "-", str(cid)).strip("-") or "case"


def cmd_extract(args):
    ensure_dirs(args)
    case, degradations, errors = core_impl.load_case(args.input)
    contract = _first_contract(case)
    result = core_impl.canonical_result(case, contract)
    core_impl.apply_contract_values(result, contract)
    core_impl.new_change_log(result, 1, "extract", "wrote_partial_skeleton",
                             "partial skeleton persisted before scientific extraction")
    result["degradations"] = degradations
    result["errors"] = errors
    no_case = any("no case.json" in str(e) for e in errors)
    if no_case:
        result["status"] = "failed"
        result["limitations"].append("no case.json found; cannot build a case-bound figure")
    elif not case.get("case_id"):
        result["status"] = "failed"
        result["limitations"].append("no case_id derivable; input unusable")

    spec = core_impl.template_figure_spec(case)
    write_json(os.path.join(args.work, "case_normalized.json"), case)
    spec_path = os.path.join(args.work, "figure_spec.json")
    if not os.path.exists(spec_path):
        write_json(spec_path, spec)
    write_json(os.path.join(args.output, "result.json"), result)
    if no_case or not case.get("case_id"):
        return 1
    return 0


def _first_contract(case):
    return core_impl._first_contract_candidate(case)


def cmd_build(args):
    ensure_dirs(args)
    spec = read_json(os.path.join(args.work, "figure_spec.json")) or core_impl.template_figure_spec({"case_id": "case"})
    svg_text, manifest = core_impl.build_svg(spec)
    svg_path = svg_path_for(args, spec)
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    write_json(os.path.join(args.work, "fig_manifest.json"), manifest)
    exports = core_impl.render_previews(svg_path, args.output)
    result = read_json(os.path.join(args.output, "result.json")) or {}
    result["editable_source"] = {
        "format": "svg",
        "path": os.path.basename(svg_path),
        "layers": ["layer-background", "layer-title", "layer-panels", "layer-entities",
                   "layer-relations", "layer-data", "layer-legend", "layer-annotations"],
        "text_editable": True,
    }
    result["exports"] = exports
    core_impl.new_change_log(result, 2, "build", "built_svg_and_previews", "")
    write_json(os.path.join(args.output, "result.json"), result)
    return 0


def cmd_validate(args):
    ensure_dirs(args)
    spec = read_json(os.path.join(args.work, "figure_spec.json")) or core_impl.template_figure_spec({"case_id": "case"})
    svg_path = svg_path_for(args, spec)
    svg_text = ""
    if os.path.exists(svg_path):
        svg_text, _deg = core_impl.read_text_any(svg_path, max_bytes=10 * 1024 * 1024)
    result = read_json(os.path.join(args.output, "result.json")) or {}
    defects, result = core_impl.validate(svg_text, spec, result)
    write_json(os.path.join(args.work, "validation_report.json"),
               {"defects": defects, "case_id": spec.get("case_id")})
    core_impl.new_change_log(result, 3, "validate", "validated_and_backfilled_evidence",
                             "%d defect(s)" % len(defects))
    write_json(os.path.join(args.output, "result.json"), result)
    return 0


def cmd_finalize(args):
    ensure_dirs(args)
    spec = read_json(os.path.join(args.work, "figure_spec.json")) or core_impl.template_figure_spec({"case_id": "case"})
    result = read_json(os.path.join(args.output, "result.json")) or {}
    report = read_json(os.path.join(args.work, "validation_report.json")) or {}
    defects = report.get("defects", [])
    has_critical = any(d.get("level") == "critical" for d in defects)
    has_entities = bool(spec.get("entities"))
    if has_critical:
        result["status"] = "failed"
    elif has_entities and not any(d.get("level") == "error" for d in defects):
        result["status"] = "completed"
    else:
        result["status"] = "partial"

    if not has_entities:
        result["uncertainty"].append({
            "id": "empty-spec",
            "issue": "unpopulated_figure_spec",
            "note": "no entities/relations/data extracted; figure is a layout scaffold only",
        })
        result["limitations"].append("figure_spec not populated with scientific content")

    # Determinism fingerprint over the scientific content only.
    deterministic_core = {
        "case_id": result.get("case_id"),
        "graphical_abstract": result.get("graphical_abstract"),
        "entities": result.get("entities"),
        "relations": result.get("relations"),
        "data_mapping": result.get("data_mapping"),
        "legend": result.get("legend"),
        "sources": result.get("sources"),
    }
    result["determinism"]["fingerprint"] = core_impl.fingerprint(deterministic_core)
    core_impl.new_change_log(result, 4, "finalize", "set_status_and_fingerprint", result["status"])
    write_json(os.path.join(args.output, "result.json"), result)

    # Runtime telemetry lives here, NOT in result.json (keeps result deterministic).
    write_json(os.path.join(args.work, "run_meta.json"), {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "argv": sys.argv,
    })
    return 0


def cmd_run(args):
    code = cmd_extract(args)
    if code != 0:
        return code
    cmd_build(args)
    cmd_validate(args)
    return cmd_finalize(args)


def main(argv=None):
    args = parse_args(argv)
    handlers = {
        "extract": cmd_extract,
        "build": cmd_build,
        "validate": cmd_validate,
        "finalize": cmd_finalize,
        "run": cmd_run,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
