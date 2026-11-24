#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly-skill entry point: input discovery -> spec -> layered SVG -> result.json.

Design emphasis: robustness. Empty input directories, malformed JSON, wrong
encodings, missing arguments and oversized files must degrade to a *valid*
partial result instead of crashing the agent run.

Subcommands (first positional argument):
  init      (default) bootstrap: write spec.json skeleton if absent, build SVG,
            write a legal partial result.json and record change_log[0].
  finalize  rebuild from spec.json, validate the output contract, set status,
            stamp runtime and append the finalize change_log entry.
  check     validate an existing result.json against the contract + filesystem.

Exit codes: 0 ok, 1 contract/file errors, 2 warnings only, 3 unexpected crash
(an emergency partial result.json is still written when one is missing).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_svg  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_BYTES = 2_000_000  # 2 MB per text file
ALLOWED_STATUS = {"success", "partial", "insufficient_evidence", "input_error", "failed"}
REQUIRED_KEYS = [
    "case_id", "status", "task_type", "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings", "sources", "uncertainty",
    "pending_human", "change_log", "limitations", "errors", "degradations", "runtime",
]

TEXT_EXTS = (".pdf", ".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html",
             ".png", ".jpg", ".jpeg", ".gif")


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# robust I/O
# ---------------------------------------------------------------------------
def read_text_bytes(path, max_bytes=MAX_TEXT_BYTES):
    """Read a file as text tolerantly. Returns (text, error, size)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat failed: %s" % e, 0
    if size > max_bytes:
        return None, "oversized (%d bytes > cap %d)" % (size, max_bytes), size
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return None, "read failed: %s" % e, size
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None, size
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "decoded with replacement", size


def discover_inputs(root):
    out = {"case_json": None, "task_md": None, "materials": [],
           "errors": [], "degradations": []}
    if not root or not os.path.isdir(root):
        out["errors"].append("input directory missing or not a directory: %r" % root)
        return out
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            filenames.sort()
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                low = fn.lower()
                if low == "case.json":
                    out["case_json"] = full
                elif low == "task.md":
                    out["task_md"] = full
                elif low.endswith(TEXT_EXTS) and "/work" not in dirpath and "/output" not in dirpath:
                    try:
                        if os.path.getsize(full) > MAX_TEXT_BYTES:
                            out["degradations"].append(
                                {"scenario": "oversized_file", "file": full,
                                 "action": "skipped (exceeds %d bytes)" % MAX_TEXT_BYTES})
                            continue
                    except OSError:
                        pass
                    out["materials"].append(full)
    except OSError as e:
        out["errors"].append("walk failed: %s" % e)
    return out


def load_case(path):
    if not path:
        return {"case_id": None, "meta": {}, "error": "no case.json found"}
    text, err, _ = read_text_bytes(path)
    if err:
        return {"case_id": None, "meta": {}, "error": "case.json unreadable: %s" % err}
    try:
        obj = json.loads(text)
    except Exception as e:
        return {"case_id": None, "meta": {"raw_head": text[:200]},
                "error": "case.json bad JSON: %s" % e}
    if not isinstance(obj, dict):
        return {"case_id": None, "meta": {}, "error": "case.json is not a JSON object"}
    cid = obj.get("case_id") or obj.get("id") or obj.get("challenge_id") or obj.get("caseId")
    return {"case_id": cid, "meta": obj, "error": None}


def empty_spec():
    return {"title": "", "entities": [], "relations": [], "data_mappings": [], "legend_notes": []}


def load_spec(work_dir):
    p = os.path.join(work_dir, "spec.json")
    if not os.path.isfile(p):
        return empty_spec(), "spec.json absent; used empty skeleton"
    text, err, _ = read_text_bytes(p)
    if err:
        return empty_spec(), "spec.json unreadable: %s" % err
    try:
        obj = json.loads(text)
    except Exception as e:
        return empty_spec(), "spec.json bad JSON: %s" % e
    if not isinstance(obj, dict):
        return empty_spec(), "spec.json is not a JSON object"
    for k, v in empty_spec().items():
        obj.setdefault(k, v)
    return obj, None


# ---------------------------------------------------------------------------
# result.json construction
# ---------------------------------------------------------------------------
def result_template(case_id):
    return {
        "case_id": case_id,
        "task_type": "biological_mechanism_pathway",
        "status": "partial",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": utcnow_iso(), "finished_at": None,
                    "seconds": None, "model_requests": None, "svg_bytes": None},
    }


def aggregate_sources(spec):
    """Collect (element_id, locator, quote) evidence from entities + relations."""
    sources = []
    seen = set()
    for e in spec.get("entities", []):
        for s in (e.get("source") or []):
            if not isinstance(s, dict):
                continue
            key = (e.get("id"), s.get("locator"), s.get("quote"))
            if key in seen:
                continue
            seen.add(key)
            sources.append({"element_id": e.get("id"), "locator": s.get("locator"),
                            "quote": s.get("quote")})
    for r in spec.get("relations", []):
        for s in (r.get("source_meta") or []):
            if not isinstance(s, dict):
                continue
            key = (r.get("id"), s.get("locator"), s.get("quote"))
            if key in seen:
                continue
            seen.add(key)
            sources.append({"element_id": r.get("id"), "locator": s.get("locator"),
                            "quote": s.get("quote")})
    return sources


def build_result(spec, case_id, svg_relpath, svg_bytes, warnings, errors, degradations,
                 status="partial", t0=None):
    result = result_template(case_id)
    result["entities"] = spec.get("entities", [])
    result["relations"] = spec.get("relations", [])
    result["data_mappings"] = spec.get("data_mappings", [])
    result["legend"] = build_svg.legend_entries(spec)
    result["sources"] = aggregate_sources(spec)
    result["status"] = status
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"]["svg_bytes"] = svg_bytes
    if warnings:
        result["limitations"].extend(warnings)
    if svg_relpath:
        result["editable_source_files"] = [{
            "path": svg_relpath,
            "format": "svg",
            "description": "layered, editable mechanism figure (compartments/edges/nodes/legend layers)",
            "layers": ["layer-compartments", "layer-edges", "layer-nodes",
                       "layer-labels", "layer-legend", "layer-message"],
            "editable": True,
        }]
    if result["status"] in ("partial", "insufficient_evidence", "input_error", "failed"):
        if not result["limitations"]:
            result["limitations"].append("status=%s" % result["status"])
    return result


def check_result(result, output_dir):
    errors, warnings = [], []
    if not isinstance(result, dict):
        return {"errors": ["result.json is not a JSON object"], "warnings": []}
    for k in REQUIRED_KEYS:
        if k not in result:
            errors.append("missing required key: %s" % k)
    if result.get("status") not in ALLOWED_STATUS:
        errors.append("invalid status %r" % result.get("status"))
    if not result.get("case_id"):
        warnings.append("case_id is empty (input may lack case metadata)")
    src_files = result.get("editable_source_files") or []
    if result.get("status") in ("success", "partial") and not src_files:
        warnings.append("no editable_source_files while status=%s" % result.get("status"))
    for f in src_files:
        if not isinstance(f, dict) or not f.get("path"):
            errors.append("editable_source_files entry missing path")
            continue
        full = f["path"] if os.path.isabs(f["path"]) else os.path.join(output_dir, f["path"])
        if not os.path.isfile(full):
            errors.append("editable source file missing: %s" % f["path"])
    for f in result.get("export_artifacts") or []:
        if not isinstance(f, dict) or not f.get("path"):
            errors.append("export_artifacts entry missing path")
            continue
        full = f["path"] if os.path.isabs(f["path"]) else os.path.join(output_dir, f["path"])
        if not os.path.isfile(full):
            warnings.append("export artifact missing (render pending?): %s" % f["path"])
    for s in result.get("sources") or []:
        if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
            warnings.append("source entry lacks locator/quote: %r" % (s,))
    if result.get("status") in ("partial", "failed", "insufficient_evidence", "input_error"):
        if not (result.get("limitations") or []):
            warnings.append("non-success status without limitations")
    return {"errors": errors, "warnings": warnings}


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json_tolerant(path):
    text, err, _ = read_text_bytes(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as e:
        return None, "bad JSON: %s" % e


# ---------------------------------------------------------------------------
# flows
# ---------------------------------------------------------------------------
def _append_change(result, action, reason):
    entry = {"at": utcnow_iso(), "action": action, "reason": reason}
    log = result.setdefault("change_log", [])
    if not log or log[-1].get("action") != action:
        log.append(entry)
    return entry


def run_init(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    disc = discover_inputs(args.input)
    case = load_case(disc["case_json"])
    spec, spec_err = load_spec(args.work)
    warnings = []
    if spec_err:
        warnings.append(spec_err)
    spec, norm_warn = build_svg.normalize_spec(spec)
    warnings.extend(norm_warn)

    svg, svg_warn = build_svg.build_svg(spec)
    warnings.extend(svg_warn)
    svg_path = os.path.join(args.output, "mechanism.svg")
    try:
        build_svg._write_svg(svg, svg_path)
        svg_bytes = len(svg.encode("utf-8"))
    except Exception as e:
        warnings.append("SVG write failed: %s" % e)
        svg_bytes = 0

    # determine honest status for the skeleton
    if case["error"] and not case["case_id"]:
        status = "input_error"
    elif not spec.get("entities") and not spec.get("relations"):
        status = "insufficient_evidence"
    else:
        status = "partial"

    errors = list(disc["errors"])
    if case["error"]:
        errors.append(case["error"])
    degradations = []
    if spec_err:
        degradations.append({"scenario": "spec_unreadable", "action": "used empty skeleton"})
    if not disc["materials"] and not disc["case_json"]:
        degradations.append({"scenario": "empty_input", "action": "wrote insufficient_evidence skeleton"})

    result = build_result(spec, case["case_id"], "mechanism.svg", svg_bytes,
                          warnings, errors, degradations, status=status)
    _append_change(result, "skeleton", "first-pass skeleton written within budget guard")

    out_result = os.path.join(args.output, "result.json")
    write_json_atomic(out_result, result)

    summary = {"ok": True, "mode": "init", "status": status,
               "case_id": case["case_id"], "result": out_result,
               "svg_bytes": svg_bytes, "warnings": warnings, "errors": errors,
               "materials_found": len(disc["materials"]),
               "seconds": round(time.time() - t0, 2)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def run_finalize(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    result, err = load_json_tolerant(result_path)
    if result is None or not isinstance(result, dict):
        result = result_template(None)
        _append_change(result, "finalize", "result.json absent/unreadable; rebuilt skeleton")
    case = load_case(discover_inputs(args.input)["case_json"])
    if not result.get("case_id"):
        result["case_id"] = case["case_id"]

    spec, spec_err = load_spec(args.work)
    spec, norm_warn = build_svg.normalize_spec(spec)
    svg, svg_warn = build_svg.build_svg(spec)
    svg_path = os.path.join(args.output, "mechanism.svg")
    try:
        build_svg._write_svg(svg, svg_path)
        result["runtime"]["svg_bytes"] = len(svg.encode("utf-8"))
    except Exception as e:
        svg_warn.append("SVG write failed: %s" % e)

    result["entities"] = spec.get("entities", [])
    result["relations"] = spec.get("relations", [])
    result["data_mappings"] = spec.get("data_mappings", [])
    result["legend"] = build_svg.legend_entries(spec)
    result["sources"] = aggregate_sources(spec)
    if "mechanism.svg" not in [f.get("path") for f in result.get("editable_source_files", []) if isinstance(f, dict)]:
        result.setdefault("editable_source_files", []).append({
            "path": "mechanism.svg", "format": "svg",
            "description": "layered, editable mechanism figure",
            "layers": ["layer-compartments", "layer-edges", "layer-nodes", "layer-legend"],
            "editable": True})

    warnings = norm_warn + svg_warn + ([spec_err] if spec_err else [])
    result.setdefault("limitations", [])
    result["limitations"] = [x for x in result["limitations"] if not str(x).startswith("status=")]

    # decide status and final limitations *before* the contract check so the
    # check sees the finished document and reports no transient warnings.
    has_entities = bool(spec.get("entities")) or bool(spec.get("relations"))
    if not has_entities:
        status = "insufficient_evidence"
    elif result.get("export_artifacts"):
        status = "success"
    else:
        status = "partial"
        result["limitations"].append("PNG/PDF export pending: SVG only")
    if warnings:
        result["limitations"].extend([w for w in warnings if w not in result["limitations"]])
    result["status"] = status

    check = check_result(result, args.output)
    if check["errors"]:
        status = "partial"
        result["status"] = status
    result["errors"] = result.get("errors", []) + [e for e in check["errors"] if e not in result.get("errors", [])]
    result["degradations"] = result.get("degradations", []) + [
        {"scenario": "finalize", "action": "checked contract and rebuilt SVG"}]
    result["runtime"]["finished_at"] = utcnow_iso()
    result["runtime"]["seconds"] = round(time.time() - t0, 2)
    _append_change(result, "finalize", "final contract check; status=%s" % status)

    write_json_atomic(result_path, result)
    summary = {"ok": not check["errors"], "mode": "finalize", "status": status,
               "contract_errors": check["errors"], "contract_warnings": check["warnings"],
               "seconds": round(time.time() - t0, 2)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not check["errors"] else 2


def run_check(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = load_json_tolerant(result_path)
    if result is None:
        print(json.dumps({"ok": False, "errors": [err or "result.json missing"]}, ensure_ascii=False))
        return 1
    check = check_result(result, args.output)
    code = 1 if check["errors"] else (2 if check["warnings"] else 0)
    print(json.dumps({"ok": code != 1, "errors": check["errors"],
                      "warnings": check["warnings"]}, ensure_ascii=False))
    return code


def _emergency_result(args, exc):
    """Last-resort: ensure a legal partial result exists even after a crash."""
    path = os.path.join(args.output, "result.json")
    if os.path.isfile(path):
        return
    result = result_template(None)
    result["status"] = "partial"
    result["errors"] = ["unexpected crash: %s" % exc]
    result["limitations"] = ["emergency partial result after unexpected error"]
    result["change_log"] = [{"at": utcnow_iso(), "action": "emergency",
                             "reason": "unexpected crash; partial result preserved"}]
    try:
        write_json_atomic(path, result)
    except Exception:
        pass


def parse_args(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="weekly-skill biological pathway figure pipeline")
    ap.add_argument("mode", nargs="?", default="init", choices=("init", "finalize", "check"))
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", DEFAULT_INPUT))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", DEFAULT_WORK))
    ap.add_argument("--max-file-mb", type=float, default=2.0)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.mode == "check":
            return run_check(args)
        if args.mode == "finalize":
            return run_finalize(args)
        return run_init(args)
    except Exception as e:  # never let a crash kill the run without a deliverable
        _emergency_result(args, "%s: %s" % (type(e).__name__, e))
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)},
                         ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
