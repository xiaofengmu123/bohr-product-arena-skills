#!/usr/bin/env python3
"""sci-figure-studio entry point: init -> render -> validate. Standard library only.

Commands:
  init      discover /input, write a legal partial result.json skeleton + work templates
  render    render work/figure_spec.json to a layered SVG (+ best-effort PNG), sync result.json
  validate  check result.json against the output contract and file integrity
  all       init -> render (if spec exists) -> validate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

try:
    from figure import render_svg, export_png
except Exception as exc:  # pragma: no cover
    render_svg = export_png = None
    FIGURE_IMPORT_ERROR = str(exc)
else:
    FIGURE_IMPORT_ERROR = None

MAX_FILE = 2_000_000
ALLOWED_STATUS = {"complete", "partial", "insufficient_evidence", "input_error", "failed"}
REQUIRED_KEYS = ["case_id", "status", "editable_source_files", "export_files", "provenance",
                 "sources", "uncertainty", "pending_human", "change_log"]


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def load_json(path):
    if not os.path.exists(path):
        return None, {"error": f"missing file: {path}", "missing": True}
    raw = read_text(path)
    if raw.strip() == "":
        return None, {"error": f"empty file: {path}", "empty": True}
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, {"error": f"bad json at line {exc.lineno}: {exc.msg}", "raw_head": raw[:160],
                      "bad_json": True}


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def classify(name):
    n = name.lower()
    if n.endswith(".json"):
        return "json"
    if n.endswith((".md", ".markdown")):
        return "markdown"
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".csv"):
        return "csv"
    if n.endswith((".tsv", ".txt", ".text")):
        return "text"
    if n.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".webp")):
        return "image"
    if n.endswith(".svg"):
        return "svg"
    if n.endswith((".xlsx", ".pptx", ".docx", ".xls", ".ppt", ".doc")):
        return "office"
    if n.endswith((".yaml", ".yml")):
        return "yaml"
    if n.endswith((".py", ".sh")):
        return "code"
    return "other"


def discover_inputs(input_dir):
    manifest = []
    if not os.path.isdir(input_dir):
        return manifest, False
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError as exc:
                manifest.append({"path": p, "kind": "other", "size": -1, "skipped": True, "reason": str(exc)})
                continue
            entry = {"path": p, "rel": os.path.relpath(p, input_dir), "kind": classify(f),
                     "size": size, "skipped": False}
            if size > MAX_FILE:
                entry.update({"skipped": True, "reason": f"size {size} > {MAX_FILE} limit"})
            if entry["kind"] == "pdf":
                entry["note"] = "text layer unknown; run pdftotext, fall back to pdftoppm/OCR if empty"
            manifest.append(entry)
    return manifest, True


def build_skeleton(case, case_err, task_text, manifest, input_ok, started, args):
    contract = {}
    case_id = "unknown"
    if isinstance(case, dict):
        case_id = str(case.get("case_id") or case.get("id") or case.get("caseId") or "unknown")
        if isinstance(case.get("output_contract"), dict):
            contract = json.loads(json.dumps(case["output_contract"]))
    result = dict(contract)
    result["case_id"] = case_id
    result.setdefault("status", "partial")
    for key, value in {
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limits": [],
    }.items():
        result.setdefault(key, value)
    result.setdefault("runtime", {})
    result["runtime"].setdefault("started_at", started)
    result["runtime"].setdefault("budget_s", 900)
    result["runtime"].setdefault("request_budget", 64)
    result["runtime"].setdefault("phase", "skeleton")
    result["runtime"].setdefault("notes", "standard library scripts only; PNG export depends on available renderer")

    # status determination (honest, never fabricates completion)
    material_files = [m for m in manifest if not m.get("skipped") and m["kind"] not in ("json", "markdown", "code", "other")]
    if case_err and case_err.get("missing"):
        if material_files:
            status = "input_error"
            result["errors"].append({"item": "case.json", "detail": case_err.get("error")})
            result["degradations"].append("case.json missing but material files present")
        else:
            status = "insufficient_evidence"
            result["pending_human"].append({"item": "case.json", "reason": "case metadata missing"})
    elif case_err:
        status = "input_error"
        result["errors"].append({"item": "case.json", "detail": case_err.get("error")})
        result["degradations"].append("case.json unreadable or invalid; continuing with minimal skeleton")
    elif not manifest:
        status = "insufficient_evidence"
        result["pending_human"].append({"item": "input materials", "reason": "no files found under input directory"})
        result["limits"].append("no source material to extract entities from")
    elif not material_files:
        status = "insufficient_evidence"
        result["pending_human"].append({"item": "materials", "reason": "only control files found; no PDF/table/sketch material"})
        result["limits"].append("no extractable source material")
    else:
        status = "partial"
    result["status"] = status

    # sources: every material file + control files, locator/quote filled during extraction
    case_basename = os.path.basename(args.case).lower()
    task_basename = os.path.basename(args.task).lower()
    for m in manifest:
        if m.get("skipped"):
            continue
        base = os.path.basename(m["path"]).lower()
        if base in (case_basename, "case.json"):
            used_for = "case metadata and output_contract"
        elif base in (task_basename, "task.md"):
            used_for = "task requirements and scoring dimensions"
        else:
            used_for = "material discovery (locator/quote to be filled during extraction)"
        result["sources"].append({
            "ref": m["rel"], "path": m["path"], "locator": "", "quote": "", "used_for": used_for, "kind": m["kind"],
        })
    result["change_log"].append({"time": started, "action": "init",
                                 "detail": f"skeleton written; status={status}; manifest entries={len(manifest)}"})
    return result


def cmd_init(args):
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.out, "result.json")
    if os.path.exists(result_path):
        data, err = load_json(result_path)
        if err is None and isinstance(data, dict):
            return {"ok": True, "already": True, "status": data.get("status"), "case_id": data.get("case_id"),
                    "result": result_path, "work_dir": args.work,
                    "note": "result.json exists; preserved (incremental edit in place)"}
    started = iso_now()
    case, case_err = load_json(args.case)
    task_text = read_text(args.task)
    manifest, input_ok = discover_inputs(args.input)
    result = build_skeleton(case, case_err, task_text, manifest, input_ok, started, args)
    write_json(result_path, result)
    write_json(os.path.join(args.work, "material_manifest.json"),
               {"input_dir": args.input, "found": input_ok, "entries": manifest})
    spec_path = os.path.join(args.work, "figure_spec.json")
    entities_path = os.path.join(args.work, "entities.json")
    if not os.path.exists(spec_path):
        spec_tpl = {
            "figure_id": "figure01", "title": "",
            "canvas": {"width_px": 2400, "height_px": 1400, "dpi": 300},
            "font": {"family": "Arial, Helvetica, sans-serif", "base_px": 28},
            "background": "#ffffff",
            "entities": [], "relations": [], "legend": {"title": "Legend", "items": []},
            "annotations": [], "groups": [],
        }
        write_json(spec_path, spec_tpl)
    if not os.path.exists(entities_path):
        entities_tpl = {"entities": [], "relations": [], "legend": {"items": []}, "annotations": [],
                        "groups": [], "notes": "fill from source material; grammar in references/figure-spec.md"}
        write_json(entities_path, entities_tpl)
    return {"ok": True, "status": result["status"], "case_id": result["case_id"],
            "manifest_count": len(manifest), "result": result_path,
            "work_dir": args.work, "errors": result["errors"]}


def update_result_files(result_path, svg_path, svg_info, png_info, png_path):
    data, err = load_json(result_path)
    if err or not isinstance(data, dict):
        return
    data["editable_source_files"] = [{
        "path": os.path.basename(svg_path), "kind": "svg", "layers": True, "editable_text": True,
        "note": "layered <g>; editable <text>; font-family replaceable; re-run render to redraw",
    }]
    if png_info.get("ok") and png_path and os.path.exists(png_path):
        export = [{
            "path": os.path.basename(png_path), "format": "png",
            "width_px": svg_info["width_px"], "height_px": svg_info["height_px"],
            "dpi": int(png_info.get("dpi", 300)), "color_space": "RGB", "font_check": "embedded",
            "note": "rasterized by " + str(png_info.get("renderer")) + "; text not editable in PNG",
        }]
    else:
        export = [{
            "path": "", "format": "png",
            "width_px": svg_info["width_px"], "height_px": svg_info["height_px"],
            "dpi": int(png_info.get("dpi", 300)), "color_space": "RGB", "font_check": "none",
            "note": "PNG export failed: " + str(png_info.get("reason", "renderer unavailable")),
        }]
    data["export_files"] = export
    data.setdefault("change_log", []).append({
        "time": iso_now(), "action": "render",
        "detail": f"rendered {os.path.basename(svg_path)}; png_ok={bool(png_info.get('ok'))}",
    })
    write_json(result_path, data)


def cmd_render(args):
    if render_svg is None:
        return {"ok": False, "error": "figure.py unavailable: " + FIGURE_IMPORT_ERROR}
    spec, err = load_json(args.spec)
    if err or not isinstance(spec, dict):
        return {"ok": False, "error": "spec unreadable: " + str(err)}
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir or ".", exist_ok=True)
    info = render_svg(spec, args.out)
    png_info = {"ok": False, "reason": "not requested"}
    if args.png:
        dpi = int(num_or((spec.get("canvas") or {}).get("dpi"), 300))
        png_info = export_png(args.out, args.png, width_px=info["width_px"], height_px=info["height_px"], dpi=dpi)
    if args.result:
        update_result_files(args.result, args.out, info, png_info, args.png)
    return {"ok": True, "svg": args.out, "png": png_info, "warnings": info["warnings"]}


def num_or(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_path(out_dir, p):
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.join(out_dir, p)


def file_magic(path, fmt):
    f = (fmt or "").lower()
    if f == "svg":
        try:
            ET.parse(path)
            return True
        except Exception as exc:  # noqa: BLE001
            return str(exc)
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        return str(exc)
    if f == "png":
        return head[:8] == b"\x89PNG\r\n\x1a\n" or "PNG magic mismatch"
    if f == "pdf":
        return head[:5] == b"%PDF-" or "PDF magic mismatch"
    return True


def cmd_validate(args):
    data, err = load_json(args.result)
    if err or not isinstance(data, dict):
        report = {"ok": False, "fatal": "result.json unreadable: " + str(err), "checks": []}
        write_json(os.path.join(args.out, "validation_report.json"), report)
        return report
    checks = []

    def add(level, check, detail=""):
        checks.append({"level": level, "check": check, "detail": detail})

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        add("fail", "required fields missing", ", ".join(missing))
    else:
        add("pass", "required fields present")
    if data.get("status") in ALLOWED_STATUS:
        add("pass", "status valid", str(data.get("status")))
    else:
        add("fail", "status invalid", str(data.get("status")))
    if str(data.get("case_id", "")).strip():
        add("pass", "case_id present", str(data["case_id"]))
    else:
        add("fail", "case_id empty")

    srcs = data.get("editable_source_files", [])
    svg_ok = False
    if not srcs:
        add("fail", "no editable source files")
    for s in srcs:
        p = resolve_path(args.out, s.get("path", ""))
        if not os.path.exists(p):
            add("fail", "source file missing", s.get("path"))
            continue
        if str(s.get("kind", "")).lower() == "svg":
            magic = file_magic(p, "svg")
            if magic is True:
                svg_ok = True
                add("pass", "SVG well-formed", s.get("path"))
            else:
                add("fail", "SVG malformed", s.get("path") + " " + str(magic))
        else:
            add("pass", "source file exists", s.get("path"))
    if srcs and not svg_ok:
        add("warn", "no valid SVG among editable sources")

    exps = data.get("export_files", [])
    usable = 0
    if not exps:
        add("fail", "no export files recorded")
    for e in exps:
        p = resolve_path(args.out, e.get("path", ""))
        if not e.get("path"):
            add("fail", "export file empty path", str(e.get("format", "")))
            continue
        if not os.path.exists(p):
            add("fail", "export file missing", e.get("path"))
            continue
        magic = file_magic(p, e.get("format"))
        if magic is True:
            usable += 1
            add("pass", "export file openable", e.get("path"))
        else:
            add("fail", "export file corrupt", e.get("path") + " " + str(magic))
        for f in ("format", "width_px", "height_px", "dpi", "color_space", "font_check"):
            if f not in e:
                add("warn", "export field missing", f)
    if exps and usable == 0:
        add("fail", "no usable export file")

    prov = data.get("provenance", [])
    if not prov:
        add("warn", "provenance empty")
    else:
        no_loc = [str(p.get("element")) for p in prov if not str(p.get("locator", "")).strip()]
        no_quote = [str(p.get("element")) for p in prov if not str(p.get("quote", "")).strip()]
        if no_loc:
            add("warn", "provenance missing locator", ", ".join(no_loc))
        if no_quote:
            add("warn", "provenance missing verbatim quote", ", ".join(no_quote))
        add("pass", "provenance entries", str(len(prov)))

    if data.get("change_log"):
        add("pass", "change_log recorded", str(len(data["change_log"])))
    else:
        add("warn", "change_log empty")

    fails = [c for c in checks if c["level"] == "fail"]
    warns = [c for c in checks if c["level"] == "warn"]
    report = {"ok": not fails, "status": data.get("status"), "case_id": data.get("case_id"),
              "fails": len(fails), "warns": len(warns), "checks": checks}
    write_json(os.path.join(args.out, "validation_report.json"), report)
    return report


def cmd_all(args):
    init_out = cmd_init(args)
    render_out = None
    spec_path = os.path.join(args.work, "figure_spec.json")
    if os.path.exists(spec_path):
        render_out = cmd_render(argparse.Namespace(
            spec=spec_path, out=os.path.join(args.out, "figure01.svg"),
            png=os.path.join(args.out, "figure01.png"),
            result=os.path.join(args.out, "result.json")))
    validate_out = cmd_validate(args)
    return {"init": init_out, "render": render_out, "validate": validate_out}


def main(argv=None):
    ap = argparse.ArgumentParser(description="sci-figure-studio: init/render/validate (standard library only)")
    ap.add_argument("command", nargs="?", default="all", choices=["init", "render", "validate", "all", "help"])
    ap.add_argument("--case", default=os.environ.get("CASE_JSON", "/input/case.json"))
    ap.add_argument("--task", default=os.environ.get("TASK_MD", "/input/task.md"))
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--spec", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "help":
        ap.print_help()
        return 0
    if a.result is None:
        a.result = os.path.join(a.out, "result.json")
    if a.spec is None:
        a.spec = os.path.join(a.work, "figure_spec.json")
    try:
        if a.command == "init":
            out = cmd_init(a)
        elif a.command == "render":
            out = cmd_render(a)
        elif a.command == "validate":
            out = cmd_validate(a)
        else:
            out = cmd_all(a)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        out = {"ok": False, "error": "unhandled exception; see traceback"}
    print(json.dumps(out, ensure_ascii=False, default=str))
    if a.command == "validate":
        return 1 if (a.strict and not out.get("ok")) else 0
    if a.command == "all":
        v = out.get("validate") or {}
        return 1 if (a.strict and not v.get("ok")) else 0
    return 0 if out.get("ok") is not False else 1


if __name__ == "__main__":
    sys.exit(main())
