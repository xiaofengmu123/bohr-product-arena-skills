#!/usr/bin/env python3
"""Self-check for a produced figure deliverable.

Checks result.json contract, figure_model.json consistency, and artifact validity.
Pure standard library; exit code: 0 = pass, 1 = warnings (partial), 2 = critical.

Usage:
    python selftest.py --result /app/output/result.json \
                       --model /app/work/figure_model.json \
                       --output /app/output [--input /input] [--strict]
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

ALLOWED_STATUS = {"complete", "partial", "insufficient_evidence", "input_error", "failed"}


def _load_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception as e:
        return {"__parse_error__": str(e)}


def _file_head(path, n=8):
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except Exception:
        return None


def check_model(model):
    """Return (errors, warnings)."""
    errors, warns = [], []
    if not isinstance(model, dict):
        return ["figure_model.json is not a JSON object"], []
    if model.get("schema_version") != "1.0":
        warns.append(f"schema_version={model.get('schema_version')!r}, expected '1.0'")
    ents = model.get("entities") or []
    rels = model.get("relations") or []
    ids = [e.get("id") for e in ents]
    if len(ids) != len(set(ids)):
        errors.append("duplicate entity ids")
    eids = set(ids)
    for r in rels:
        if r.get("from") not in eids:
            errors.append(f"relation {r.get('id')!r} from={r.get('from')!r} not in entities")
        if r.get("to") not in eids:
            errors.append(f"relation {r.get('id')!r} to={r.get('to')!r} not in entities")
        if r.get("kind") == "containment":
            warns.append(f"relation {r.get('id')!r} uses 'containment'; prefer groups[].contains")
    for g in model.get("groups") or []:
        for m in g.get("contains") or []:
            if m not in eids:
                errors.append(f"group {g.get('id')!r} contains unknown entity {m!r}")
    legend = model.get("legend") or {}
    dm = legend.get("data_mapping")
    if dm:
        cats = dm.get("categories") or []
        colors = dm.get("colors") or {}
        markers = dm.get("markers") or {}
        if len(set(cats)) != len(cats):
            warns.append("data_mapping.categories contains duplicates")
        if set(colors.keys()) != set(cats):
            errors.append("data_mapping.colors keys != categories")
        if markers and set(markers.keys()) != set(cats):
            errors.append("data_mapping.markers keys != categories")
    for it in legend.get("items") or []:
        for ref in it.get("maps_to") or []:
            if ref not in eids and ref not in [r.get("id") for r in rels]:
                warns.append(f"legend item {it.get('id')!r} maps_to unknown id {ref!r}")
    return errors, warns


def _check_provenance(model, result):
    """Cross-check provenance coverage. Returns list of warnings."""
    warns = []
    prov = result.get("provenance") or []
    prov_elements = {p.get("element") for p in prov if isinstance(p, dict)}
    ents = [e.get("id") for e in (model.get("entities") or [])]
    rels = [r.get("id") for r in (model.get("relations") or [])]
    missing = [e for e in ents + rels if e not in prov_elements]
    if missing:
        warns.append(f"provenance missing for elements: {missing[:8]}" + ("..." if len(missing) > 8 else ""))
    for p in prov:
        if isinstance(p, dict) and not p.get("source_ref"):
            warns.append(f"provenance element {p.get('element')!r} lacks source_ref")
    return warns


def _check_artifacts(output_dir):
    """Check editable_source_files/export_files on disk."""
    errors, warns = [], []
    svg_count = 0
    for root, _dirs, files in os.walk(output_dir):
        for f in files:
            p = os.path.join(root, f)
            if f.endswith(".svg"):
                svg_count += 1
                try:
                    ET.parse(p)
                except Exception as e:
                    errors.append(f"SVG {f} unparseable: {e}")
                else:
                    data = ET.parse(p).getroot()
                    tag = data.tag.split("}")[-1]
                    if tag != "svg":
                        errors.append(f"{f} root is not <svg>")
                    else:
                        has_g = any(ch.tag.split('}')[-1] == 'g' for ch in data.iter())
                        has_t = any(ch.tag.split('}')[-1] == 'text' for ch in data.iter())
                        if not has_g:
                            warns.append(f"{f} has no <g> layers")
                        if not has_t:
                            warns.append(f"{f} has no <text> elements")
            elif f.endswith(".pdf"):
                head = _file_head(p, 5)
                if head != b"%PDF-":
                    errors.append(f"PDF {f} bad header")
            elif f.endswith(".png"):
                head = _file_head(p, 8)
                if head != b"\x89PNG\r\n\x1a\n":
                    errors.append(f"PNG {f} bad header")
    if svg_count == 0:
        warns.append("no SVG editable source file found")
    return errors, warns


def run_checks(result_path, model_path, output_dir, input_dir=None):
    result = _load_json(result_path)
    model = _load_json(model_path)
    checks = []
    if result is None:
        return {"critical": True, "errors": ["result.json missing"], "warnings": [],
                "checks": [{"id": "result_json", "status": "fail", "detail": "missing"}]}
    if isinstance(result, dict) and "__parse_error__" in result:
        return {"critical": True, "errors": [f"result.json parse error: {result['__parse_error__']}"],
                "warnings": [], "checks": []}
    if model is None or (isinstance(model, dict) and "__parse_error__" in model):
        return {"critical": True, "errors": ["figure_model.json missing or unparseable"],
                "warnings": [], "checks": []}

    errors, warns = [], []

    def add(check_id, ok, detail):
        checks.append({"id": check_id, "status": "pass" if ok else "fail", "detail": detail})
        if not ok:
            errors.append(detail)

    add("case_id", bool(result.get("case_id")), f"case_id={result.get('case_id')!r}")
    add("status_valid", result.get("status") in ALLOWED_STATUS, f"status={result.get('status')!r}")

    # echo output_contract keys from case.json
    echoed = []
    if input_dir:
        case_path = os.path.join(input_dir, "case.json")
        case = _load_json(case_path)
        if case and isinstance(case, dict) and "__parse_error__" not in case:
            oc = case.get("output_contract") or case.get("outputContract") or {}
            if isinstance(oc, dict):
                echoed = [k for k in oc.keys() if k in result]
                missing = [k for k in oc.keys() if k not in result]
                add("output_contract_echo", not missing,
                    f"echoed={echoed or 'none'} missing={missing or 'none'}")
            else:
                checks.append({"id": "output_contract_echo", "status": "warn",
                               "detail": "case.json has no dict output_contract"})
                warns.append("case.json has no dict output_contract")

    me, mw = check_model(model)
    errors.extend(me)
    warns.extend(mw)
    checks.append({"id": "model_consistency", "status": "pass" if not me else "fail",
                   "detail": f"{len(me)} errors, {len(mw)} warnings"})

    pw = _check_provenance(model, result)
    warns.extend(pw)
    checks.append({"id": "provenance_coverage", "status": "pass" if not pw else "warn",
                   "detail": f"{len(pw)} gaps"})

    ae, aw = _check_artifacts(output_dir)
    errors.extend(ae)
    warns.extend(aw)
    checks.append({"id": "artifacts", "status": "pass" if not ae else "fail",
                   "detail": f"{len(ae)} errors, {len(aw)} warnings"})

    for key in ("sources", "uncertainty", "change_log"):
        v = result.get(key)
        ok = isinstance(v, list)
        add(f"field_{key}", ok, f"{key} is {'list' if ok else type(v).__name__}")

    critical = bool(errors) and result.get("status") == "complete"
    report = {
        "critical": critical,
        "errors": errors,
        "warnings": warns,
        "checks": checks,
        "status": result.get("status"),
    }
    return report


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--input", default=None)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    report = run_checks(a.result, a.model, a.output, a.input)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("critical"):
        sys.exit(2)
    if a.strict and (report.get("errors") or report.get("warnings")):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    _cli()
