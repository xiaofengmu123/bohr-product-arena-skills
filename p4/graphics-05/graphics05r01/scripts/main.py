#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Graphical-abstract Skill orchestrator.

Responsibilities (run order):
  1. Discover /input (case.json + task.md + materials), tolerant decoding.
  2. Normalize the case and persist a valid result.json skeleton (status=partial)
     *immediately* — the first-3-minute safe-state requirement.
  3. Load/merge the agent-edited /app/work/figure_spec.json and evidence_map.json.
  4. Build the layered editable SVG, manifest and honest export status.
  5. Validate and write the final /app/output/result.json with case_id, all
     output_contract fields, sources, uncertainty, pending_human, change_log.

Pure standard library. Deterministic figure structure; only run metadata varies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from xml.etree import ElementTree as ET

try:
    import build_svg  # same directory when run as scripts/main.py
except ImportError:  # pragma: no cover - only if imported from a different cwd
    build_svg = None

CANONICAL_FIELDS = [
    "graphical_abstract",
    "editable_source",
    "exports",
    "entities",
    "relations",
    "data_mapping",
    "legend",
    "evidence_map",
    "sources",
    "uncertainty",
    "pending_human",
    "limitations",
    "errors",
    "degradations",
    "runtime",
    "change_log",
]

STATUSES = ("completed", "partial", "failed")
MAX_SOURCE_BYTES = 2 * 1024 * 1024  # 2 MB per source file cap


def read_text(path):
    """UTF-8-sig first, then latin-1 fallback; never raises on bad encoding."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def load_json(path):
    """Tolerant JSON loader; returns None on parse failure (caller records error)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return None


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def discover_input(input_dir):
    """Scan the input directory without assuming fixed filenames."""
    found = {"case_json": None, "task_md": None, "materials": [], "all": []}
    if not os.path.isdir(input_dir):
        return found
    names = sorted(os.listdir(input_dir))
    for name in names:
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        found["all"].append(name)
        low = name.lower()
        if low in ("case.json", "case_info.json") or (low.endswith(".json") and low not in ("task.json",)):
            if found["case_json"] is None:
                found["case_json"] = full
        elif low in ("task.md", "task.txt", "task.md.txt"):
            found["task_md"] = full
        else:
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            if size <= MAX_SOURCE_BYTES:
                found["materials"].append(full)
    return found


def _default_section(text=""):
    return {"text": text, "evidence_ref": None}


def canonical_output_contract():
    return {
        "graphical_abstract": {
            "question": _default_section(),
            "method": _default_section(),
            "results": _default_section(),
            "conclusion": _default_section(),
        },
        "editable_source": {
            "path": None, "format": "svg", "layers": [], "text_editable": True, "renderer": "stdlib",
        },
        "exports": {
            "png": {"path": None, "status": "preview_not_rendered", "renderer": None},
            "pdf": {"path": None, "status": "preview_not_rendered", "renderer": None},
        },
        "entities": [],
        "relations": [],
        "data_mapping": [],
        "legend": [],
        "evidence_map": {"claims": [], "conflicts": [], "unresolved": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {},
        "change_log": [],
    }


def normalize_case(case, task_text, input_dir, errors):
    """Return case_id, output_contract (merged), materials, and error-free facts."""
    case = case if isinstance(case, dict) else {}
    case_id = None
    for key in ("case_id", "id", "challenge_id", "armiId"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            case_id = val.strip()
            break
    if not case_id:
        base = os.path.basename(os.path.abspath(input_dir))
        case_id = base or "case"

    oc = canonical_output_contract()
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        # Preserve original keys, filling missing values with None, never renaming.
        for k, v in contract.items():
            oc[k] = v if v is not None else None

    materials = []
    for key in ("materials", "sources", "files"):
        val = case.get(key)
        if isinstance(val, list):
            materials.extend(x for x in val if isinstance(x, (str, dict)))
    if not materials and task_text:
        materials.append({"name": "task.md", "type": "text"})

    return {
        "case_id": case_id,
        "output_contract": oc,
        "task_text": task_text,
        "materials": materials,
        "errors": errors,
    }


def make_skeleton(normalized):
    oc = normalized.get("output_contract") or {}
    canon = canonical_output_contract()
    result = {"case_id": normalized["case_id"], "status": "partial"}
    # Preserve every original output_contract key verbatim (unknown keys included,
    # null values kept as null); never rename or drop.
    for k, v in oc.items():
        result[k] = v
    # Ensure canonical fields always exist with sensible structural defaults.
    for k in CANONICAL_FIELDS:
        if k not in result or result[k] is None:
            result[k] = canon[k]
    ga = dict(canon["graphical_abstract"])
    if isinstance(result.get("graphical_abstract"), dict):
        ga.update(result["graphical_abstract"])
    result["graphical_abstract"] = ga
    ev = dict(canon["evidence_map"])
    if isinstance(result.get("evidence_map"), dict):
        ev.update(result["evidence_map"])
    result["evidence_map"] = ev
    result["sources"] = result["sources"] if isinstance(result["sources"], list) else []
    result["uncertainty"] = result["uncertainty"] if isinstance(result["uncertainty"], list) else []
    result["pending_human"] = result["pending_human"] if isinstance(result["pending_human"], list) else []
    result["limitations"] = result["limitations"] if isinstance(result["limitations"], list) else []
    result["errors"] = result["errors"] if isinstance(result["errors"], list) else []
    result["degradations"] = result["degradations"] if isinstance(result["degradations"], list) else []
    result["change_log"] = result["change_log"] if isinstance(result["change_log"], list) else []
    result["runtime"] = result["runtime"] if isinstance(result["runtime"], dict) else {}
    return result


def load_figure_spec(work_dir, normalized):
    path = os.path.join(work_dir, "figure_spec.json")
    if os.path.isfile(path):
        spec = load_json(path) or {}
    else:
        spec = {}
    spec.setdefault("case_id", normalized["case_id"])
    spec.setdefault("canvas", {"width": 1600, "height": 900, "margin": 40})
    spec.setdefault("reading_order", ["problem", "method", "results", "conclusion"])
    spec.setdefault("panels", [])
    spec.setdefault("entities", [])
    spec.setdefault("relations", [])
    spec.setdefault("data", [])
    spec.setdefault("legend", [])
    spec.setdefault("annotations", [])
    for sec in ("question", "method", "results", "conclusion"):
        spec.setdefault("graphical_abstract", {}).setdefault(sec, {"text": "", "evidence_ref": None})
    return spec


def load_evidence(work_dir):
    path = os.path.join(work_dir, "evidence_map.json")
    if os.path.isfile(path):
        ev = load_json(path) or {}
        return ev
    return {"claims": [], "conflicts": [], "unresolved": []}


def _has_text(sec):
    return bool(sec) and isinstance(sec, dict) and bool(str(sec.get("text", "")).strip())


def _has_evidence(item):
    if not isinstance(item, dict):
        return False
    return bool(item.get("locator")) or bool(item.get("quote")) or item.get("quote") == "not_reported"


def validate_result(result, output_dir):
    issues = []
    if not isinstance(result, dict):
        return ["result.json is not a JSON object"], "failed"
    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in STATUSES:
        issues.append("invalid status")

    ga = result.get("graphical_abstract") or {}
    for sec in ("question", "method", "results", "conclusion"):
        if not _has_text(ga.get(sec)):
            issues.append("graphical_abstract.%s missing text" % sec)

    es = result.get("editable_source") or {}
    svg_rel = es.get("path")
    svg_path = os.path.join(output_dir, svg_rel) if svg_rel else None
    if svg_path and os.path.isfile(svg_path):
        try:
            ET.parse(svg_path)
        except ET.ParseError as exc:
            issues.append("SVG not parseable: %s" % exc)
    else:
        issues.append("editable_source.path missing or file absent")

    for key in ("entities", "relations", "data_mapping", "legend"):
        items = result.get(key) or []
        missing = [it.get("id") or "?" for it in items if not _has_evidence(it)]
        if missing:
            issues.append("%s lacking locator/quote: %s" % (key, missing[:8]))

    ev = result.get("evidence_map") or {}
    claims = ev.get("claims") or []
    if not claims:
        issues.append("evidence_map.claims empty")
    else:
        bad = [c.get("claim_id") or "?" for c in claims if not _has_evidence(c)]
        if bad:
            issues.append("evidence claims lacking locator/quote: %s" % bad[:8])

    status = "completed"
    if not result.get("case_id") or not (svg_path and os.path.isfile(svg_path)):
        status = "failed"
    elif issues:
        status = "partial"
    return issues, status


def _fill_result_from_spec(result, spec, evidence, svg_path, manifest, exports, output_dir, prev_log, runtime):
    result["editable_source"] = {
        "path": os.path.basename(svg_path),
        "format": "svg",
        "layers": manifest.get("layers", []),
        "text_editable": True,
        "renderer": "stdlib",
    }
    result["exports"] = exports
    result["entities"] = spec.get("entities", [])
    result["relations"] = spec.get("relations", [])
    result["data_mapping"] = spec.get("data", [])
    result["legend"] = spec.get("legend", [])
    result["evidence_map"] = {
        "claims": evidence.get("claims", []),
        "conflicts": evidence.get("conflicts", []),
        "unresolved": evidence.get("unresolved", []),
    }
    ga = spec.get("graphical_abstract") or {}
    result["graphical_abstract"] = {
        "question": ga.get("question", _default_section()),
        "method": ga.get("method", _default_section()),
        "results": ga.get("results", _default_section()),
        "conclusion": ga.get("conclusion", _default_section()),
    }
    if spec.get("sources"):
        result["sources"] = spec["sources"]
    result["runtime"] = runtime

    # Append one change_log entry per rewrite.
    log = list(prev_log)
    rev = len([x for x in log if isinstance(x, dict)]) + 1
    log.append({
        "rev": rev,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": "rebuild+validate",
        "summary": "svg=%s entities=%d relations=%d data=%d" % (
            os.path.basename(svg_path), len(result["entities"]), len(result["relations"]), len(result["data_mapping"])),
    })
    result["change_log"] = log
    return result


def run_pipeline(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    runtime = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "input": input_dir}

    found = discover_input(input_dir)
    errors = []
    degradations = []

    case = None
    task_text = ""
    if found["case_json"] and os.path.isfile(found["case_json"]):
        case = load_json(found["case_json"])
        if case is None:
            errors.append("case.json not parseable: %s" % found["case_json"])
    else:
        errors.append("no case.json found under %s" % input_dir)

    if found["task_md"] and os.path.isfile(found["task_md"]):
        task_text = read_text(found["task_md"])
    else:
        task_text = ""

    normalized = normalize_case(case, task_text, input_dir, errors)
    case_id = normalized["case_id"]
    skeleton = make_skeleton(normalized)
    skeleton["errors"] = errors

    # ---- Write valid partial skeleton first (safe state) ----
    if not case_id or errors:
        skeleton["status"] = "failed"
        skeleton.setdefault("limitations", []).append("input discovery/normalization failed; see errors")
        save_json(os.path.join(output_dir, "result.json"), skeleton)
        runtime["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        skeleton["runtime"] = runtime
        save_json(os.path.join(output_dir, "result.json"), skeleton)
        return {"case_id": case_id, "status": "failed", "errors": errors}

    skeleton["runtime"] = runtime
    save_json(os.path.join(output_dir, "result.json"), skeleton)

    # Persist normalized case and a spec template (do not clobber agent edits).
    save_json(os.path.join(work_dir, "case_normalized.json"), normalized)
    spec_path = os.path.join(work_dir, "figure_spec.json")
    if not os.path.isfile(spec_path):
        save_json(spec_path, load_figure_spec(work_dir, normalized))

    spec = load_figure_spec(work_dir, normalized)
    evidence = load_evidence(work_dir)

    # Build SVG.
    svg_path = os.path.join(output_dir, "fig_%s.svg" % case_id)
    if build_svg is not None:
        build_svg.build_svg(spec, svg_path)
        manifest = build_svg.write_manifest(spec, svg_path, os.path.join(output_dir, "fig_manifest.json"))
        exports = build_svg.attempt_render(svg_path, output_dir, case_id)
    else:
        manifest = {"layers": [], "counts": {}}
        exports = {"png": {"path": None, "status": "preview_not_rendered", "renderer": None},
                   "pdf": {"path": None, "status": "preview_not_rendered", "renderer": None}}

    # Preserve change_log history from an earlier run.
    prev_result = load_json(os.path.join(output_dir, "result.json")) or {}
    prev_log = prev_result.get("change_log") if isinstance(prev_result.get("change_log"), list) else []

    runtime["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    final = _fill_result_from_spec(skeleton, spec, evidence, svg_path, manifest, exports, output_dir, prev_log, runtime)

    # Material list becomes the default sources[] when the agent supplied none.
    if not final.get("sources"):
        srcs = []
        for i, m in enumerate(normalized.get("materials") or []):
            if isinstance(m, dict):
                srcs.append({"source_id": "src%d" % (i + 1), "path": m.get("path") or m.get("name"),
                             "type": m.get("type", "material"), "title": m.get("title") or m.get("name")})
            elif isinstance(m, str):
                srcs.append({"source_id": "src%d" % (i + 1), "path": m, "type": "material", "title": m})
        final["sources"] = srcs

    final["errors"] = errors
    final["degradations"] = degradations
    final["uncertainty"] = final["uncertainty"] if isinstance(final.get("uncertainty"), list) else []
    final["pending_human"] = final["pending_human"] if isinstance(final.get("pending_human"), list) else []
    final["limitations"] = final["limitations"] if isinstance(final.get("limitations"), list) else []

    issues, status_hint = validate_result(final, output_dir)
    # Never overstate completion: require all four sections and at least one
    # evidence-bearing claim before marking completed.
    ga_ok = all(_has_text(final["graphical_abstract"].get(k)) for k in ("question", "method", "results", "conclusion"))
    ev_ok = any(_has_evidence(c) for c in (final["evidence_map"].get("claims") or []))
    if status_hint == "failed":
        final["status"] = "failed"
    elif ga_ok and ev_ok and not issues:
        final["status"] = "completed"
    else:
        final["status"] = "partial"

    if issues:
        final.setdefault("degradations", []).append({"validation_issues": issues})
    if not ev_ok:
        final.setdefault("limitations", []).append("evidence_map has no locator/quote-bearing claims yet")

    save_json(os.path.join(output_dir, "result.json"), final)
    return {"case_id": case_id, "status": final["status"], "issues": issues,
            "svg": os.path.basename(svg_path), "errors": errors}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Graphical-abstract Skill orchestrator")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    summary = run_pipeline(args.input, args.output, args.work)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
