#!/usr/bin/env python3
"""Entry point for the sci-figure-model skill.

Subcommands:
    scaffold  --input /input --output /app/output --work /app/work
        Discover inputs, echo output_contract keys, write a partial result.json
        skeleton + figure_model.json skeleton + plan.json. Must run first.
    render    --model /app/work/figure_model.json --output /app/output [--work /app/work] [--dpi 300]
        Deterministically render the model into layered SVG + vector PDF (+ PNG).
    validate  --result /app/output/result.json --model /app/work/figure_model.json
              --output /app/output [--input /input] [--strict]
        Run the self-check and report pass/warn/fail.
    selftest  --output /app/work/selftest
        Build a synthetic demo (clearly labeled example) end-to-end to prove the
        package runs; does NOT write any prepared formal answer.

Pure standard library. This file stays thin (< 30 KB); heavy work is in render.py.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render
import selftest

DEFAULT_RESULT_FIELDS = [
    "case_id", "status", "editable_source_files", "export_files", "provenance",
    "sources", "uncertainty", "pending_human", "change_log", "errors",
    "degradations", "runtime", "figure_model", "plan",
]

LIST_FIELDS = {"editable_source_files", "export_files", "provenance", "sources",
               "uncertainty", "pending_human", "change_log", "errors", "degradations"}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception as e:
        return {"__parse_error__": str(e)}


def _read_text(path, limit=200000):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(limit)
    except Exception:
        return None


def discover_inputs(input_dir):
    """Scan input dir; never assume a fixed layout."""
    found = []
    if os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            p = os.path.join(input_dir, name)
            if os.path.isfile(p):
                sz = os.path.getsize(p)
                found.append({"name": name, "path": p, "bytes": sz,
                              "kind": _kind_of(name)})
    return found


def _kind_of(name):
    n = name.lower()
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith((".csv", ".tsv", ".xlsx", ".xls")):
        return "data_table"
    if n.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
        return "image"
    if n.endswith((".md", ".txt", ".json")):
        return "text"
    if n.endswith((".pptx", ".docx")):
        return "office"
    return "other"


def extract_output_contract(case):
    if not isinstance(case, dict) or "__parse_error__" in case:
        return {}
    for key in ("output_contract", "outputContract", "output_spec"):
        v = case.get(key)
        if isinstance(v, dict):
            return v
    return {}


def scaffold(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    files = discover_inputs(input_dir)
    case = _load_json(os.path.join(input_dir, "case.json"))
    task = _read_text(os.path.join(input_dir, "task.md"))
    case_id = "unknown"
    if isinstance(case, dict) and "__parse_error__" not in case:
        case_id = case.get("case_id") or case.get("id") or case.get("caseId") or "unknown"
    oc = extract_output_contract(case)

    result = {"case_id": str(case_id), "status": "partial"}
    for key in DEFAULT_RESULT_FIELDS:
        if key not in result:
            result[key] = [] if key in LIST_FIELDS else None
    result["runtime"] = {"started": _now(), "finished": None,
                         "model_requests_used": 0, "seconds_elapsed": 0}
    result["figure_model"] = "figure_model.json"
    result["plan"] = "plan.json"
    result["change_log"] = [{"time": _now(), "action": "scaffold",
                             "detail": "partial skeleton written; fill figure_model.json next"}]
    # echo every output_contract key verbatim (never rename/delete unknown keys)
    for k, v in oc.items():
        if k not in result:
            result[k] = [] if isinstance(v, list) else (None if v is None else json.loads(json.dumps(v)))

    model = {
        "schema_version": "1.0",
        "figure": {"id": "figure01", "title": "", "kind": "relationship_schematic",
                   "canvas": {"width_px": 2400, "height_px": 1400, "dpi": 300},
                   "layers": [
                       {"id": "background", "label": "背景与分组", "z": 0},
                       {"id": "relations", "label": "关系连线", "z": 1},
                       {"id": "entities", "label": "实体节点", "z": 2},
                       {"id": "annotations", "label": "标注", "z": 3},
                       {"id": "legend", "label": "图例", "z": 4}]},
        "groups": [], "entities": [], "relations": [],
        "legend": {"items": [], "data_mapping": None},
        "annotations": [], "units": [], "panels": [], "chart": None,
        "uncertainty": [], "notes": ["skeleton generated by scaffold; fill entities/relations from actual material"],
    }

    plan = {
        "phase0_skeleton_done_at": _now(),
        "phase1_extract": {"status": "pending", "todo": "read materials; fill entities/relations/groups/legend into figure_model.json"},
        "phase2_render": {"status": "pending", "todo": "run: python scripts/main.py render --model ... --output /app/output"},
        "phase3_validate": {"status": "pending", "todo": "run: python scripts/main.py validate --result ... --model ... --output ..."},
        "discovered_files": files,
        "output_contract_keys": sorted(oc.keys()),
        "has_task_md": bool(task),
        "budget": {"total_seconds": 900, "model_requests": 64, "skeleton_deadline_seconds": 180},
    }

    _write_json(os.path.join(output_dir, "result.json"), result)
    _write_json(os.path.join(work_dir, "figure_model.json"), model)
    _write_json(os.path.join(work_dir, "plan.json"), plan)
    return {"case_id": str(case_id), "output_contract_keys": sorted(oc.keys()),
            "discovered_files": [f["name"] for f in files]}


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def merge_result(result_path, model_path, render_result):
    """Incrementally update result.json with render outputs (never overwrite user notes)."""
    result = _load_json(result_path)
    if not isinstance(result, dict) or "__parse_error__" in result:
        result = {}
    result.setdefault("case_id", "unknown")
    result.setdefault("status", "partial")
    result["export_files"] = render_result.get("exports", [])
    result["editable_source_files"] = [{
        "path": os.path.basename(render_result.get("source_file", "")),
        "kind": "svg", "layers": True, "editable_text": True,
        "note": "generated from figure_model.json; edit model and re-render"}]
    result["figure_model"] = os.path.basename(model_path)
    result["change_log"] = (result.get("change_log") or []) + [{
        "time": _now(), "action": "render", "detail": "SVG/PDF(+PNG) regenerated from figure_model.json"}]
    _write_json(result_path, result)
    return result_path


def run_selftest(output_dir):
    """Synthetic, clearly-labeled demo to prove the package runs end to end."""
    os.makedirs(output_dir, exist_ok=True)
    demo_model = {
        "schema_version": "1.0",
        "figure": {"id": "demo_selftest", "title": "SELFTEST DEMO (synthetic example, not a formal answer)",
                   "kind": "relationship_schematic",
                   "canvas": {"width_px": 1600, "height_px": 900, "dpi": 150},
                   "layers": [{"id": "entities", "label": "实体", "z": 0}]},
        "groups": [{"id": "g1", "label": "示例分组", "contains": ["e1", "e2"]}],
        "entities": [
            {"id": "e1", "label": "Signal", "type": "process", "group": "g1",
             "provenance": {"source_ref": "selftest://demo", "locator": "demo", "quote": "synthetic", "confirmed": False}},
            {"id": "e2", "label": "Target", "type": "gene", "group": "g1",
             "provenance": {"source_ref": "selftest://demo", "locator": "demo", "quote": "synthetic", "confirmed": False}},
            {"id": "e3", "label": "Effect", "type": "phenotype",
             "provenance": {"source_ref": "selftest://demo", "locator": "demo", "quote": "synthetic", "confirmed": False}},
        ],
        "relations": [
            {"id": "r1", "from": "e1", "to": "e2", "kind": "activation", "label": "促进"},
            {"id": "r2", "from": "e2", "to": "e3", "kind": "inhibition", "label": "抑制"},
        ],
        "legend": {"items": [
            {"id": "lg1", "label": "激活", "color": "#1565C0", "marker": "arrow", "maps_to": ["r1"]},
            {"id": "lg2", "label": "抑制", "color": "#B71C1C", "marker": "tee", "maps_to": ["r2"]}],
            "data_mapping": None},
        "annotations": [], "units": [], "panels": [], "chart": None,
        "uncertainty": [{"item": "demo", "reason": "synthetic", "impact": "none", "mitigation": "n/a"}],
        "notes": ["SELFTEST DEMO: synthetic interface example, explicitly not a formal case answer"],
    }
    model_path = os.path.join(output_dir, "figure_model.json")
    _write_json(model_path, demo_model)
    rr = render.render_model(demo_model, output_dir, output_dir, 150)
    result_path = os.path.join(output_dir, "result.json")
    _write_json(result_path,
                {"case_id": "selftest_demo", "status": "partial",
                 "editable_source_files": [{"path": os.path.basename(rr.get("source_file", "")),
                                            "kind": "svg", "layers": True, "editable_text": True,
                                            "note": "synthetic demo"}],
                 "export_files": rr.get("exports", []),
                 "provenance": [{"element": "e1", "source_ref": "selftest://demo", "locator": "demo",
                                 "quote": "synthetic", "confirmed": False}],
                 "sources": [{"ref": "selftest://demo", "path": "demo", "locator": "demo", "used_for": "demo"}],
                 "uncertainty": [{"item": "demo", "reason": "synthetic", "impact": "none", "mitigation": "n/a"}],
                 "pending_human": [], "change_log": [{"time": _now(), "action": "selftest", "detail": "demo"}],
                 "errors": [], "degradations": [], "runtime": {"started": _now()},
                 "figure_model": "figure_model.json", "plan": "plan.json"})
    report = selftest.run_checks(result_path, model_path, output_dir, None)
    print(json.dumps({"selftest": "ok", "demo_dir": output_dir,
                      "artifacts": sorted(os.listdir(output_dir)),
                      "checks": report.get("checks")}, ensure_ascii=False, indent=2))
    return 0


def _cli():
    ap = argparse.ArgumentParser(prog="main.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")

    p = sub.add_parser("render")
    p.add_argument("--model", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--work", default=None)
    p.add_argument("--dpi", type=int, default=300)

    p = sub.add_parser("validate")
    p.add_argument("--result", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--input", default=None)
    p.add_argument("--strict", action="store_true")

    p = sub.add_parser("selftest")
    p.add_argument("--output", default="/app/work/selftest")

    a = ap.parse_args()
    if a.cmd == "scaffold":
        print(json.dumps(scaffold(a.input, a.output, a.work), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "render":
        model = _load_json(a.model)
        if not isinstance(model, dict) or "__parse_error__" in model:
            print(json.dumps({"error": "model unparseable"}, ensure_ascii=False))
            return 2
        res = render.render_model(model, a.output, a.work, a.dpi)
        # update result.json if present
        rp = os.path.join(a.output, "result.json")
        if os.path.exists(rp):
            merge_result(rp, a.model, res)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "validate":
        report = selftest.run_checks(a.result, a.model, a.output, a.input)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get("critical"):
            return 2
        if a.strict and (report.get("errors") or report.get("warnings")):
            return 1
        return 0
    if a.cmd == "selftest":
        return run_selftest(a.output)
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
