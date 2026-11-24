#!/usr/bin/env python3
"""sci-figure-editor entry point.

Deterministic plumbing (standard library only). The scientific extraction —
reading the actual material and writing entities.json / figure_spec.json with
locator + verbatim quote — is done by the agent following SKILL.md. This script
must never invent source content.

Responsibilities:
  1. Phase-0 early skeleton: discover inputs, read case.json tolerantly, and
     write an honest partial result.json FIRST, so any later failure still
     leaves a valid, inspectable artifact (the 0-3 min checkpoint rule).
  2. --run: merge work-dir entities/figure-spec provenance into result.json,
     render the SVG, and run the self-check.

Usage:
  python scripts/main.py --input /input --output /app/output --work /app/work
  python scripts/main.py --input /input --output /app/output --work /app/work --run
"""
import argparse
import datetime
import json
import os
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip oversized inputs, record in degradations


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, limit=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, f"unreadable: {path}"
    if size > limit:
        return None, f"skipped (>{limit} bytes): {path}"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except (UnicodeDecodeError, OSError):
            continue
    return None, f"cannot decode: {path}"


def load_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"bad JSON {path}: {e}"


def discover(input_dir):
    """Find case.json, task.md and candidate material files (no hardcoded keys)."""
    found = {"case": None, "task": None, "materials": []}
    if not os.path.isdir(input_dir):
        return found
    for name in sorted(os.listdir(input_dir)):
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        low = name.lower()
        if low in ("case.json", "case", "input.json"):
            found["case"] = found["case"] or full
        elif low in ("task.md", "task.txt", "task"):
            found["task"] = found["task"] or full
        elif low.endswith((".pdf", ".txt", ".md", ".csv", ".tsv", ".png",
                           ".jpg", ".jpeg", ".tif", ".tiff", ".svg")):
            found["materials"].append(full)
    return found


def extract_case_id(case_data):
    if not isinstance(case_data, dict):
        return "unknown"
    for key in ("case_id", "caseId", "caseID", "id"):
        v = case_data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    # nested contract sometimes holds the id
    for k in ("output_contract", "meta", "metadata"):
        sub = case_data.get(k)
        if isinstance(sub, dict):
            for key in ("case_id", "caseId", "id"):
                v = sub.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return "unknown"


def build_skeleton(case_id, case_path, discovered, errors, started):
    sources = []
    for p in discovered.get("materials", []):
        sources.append({"ref": os.path.basename(p), "path": p, "locator": None,
                        "text_mode": None, "used_for": ""})
    change_log = [{"time": started, "action": "scaffold",
                   "detail": "phase-0 early skeleton written (status=partial)"}]
    if errors:
        change_log.append({"time": started, "action": "degrade",
                           "detail": "; ".join(errors)})
    return {
        "case_id": case_id,
        "schema": "editable_relationship_schematic/v1",
        "status": "partial",
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": list(errors),
        "degradations": [],
        "change_log": change_log,
        "runtime": {"started_at": started, "finished_at": None,
                    "wall_seconds": None, "model_requests_used": None},
    }


def merge_work(result, work_dir, started):
    """Pull entities/figure-spec provenance back into result.json incrementally."""
    changed = False
    for fname, kind in (("entities.json", "entity"), ("figure_spec.json", "figure")):
        data, err = load_json(os.path.join(work_dir, fname))
        if err or not isinstance(data, dict):
            continue
        items = data.get("items") or data.get("provenance") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            entry = {"element": it.get("element") or it.get("id") or "",
                     "source_ref": it.get("source_ref") or it.get("locator") or "",
                     "quote": it.get("quote", ""),
                     "mapping": it.get("mapping", ""),
                     "version": it.get("version", "v1"),
                     "confirmed": bool(it.get("confirmed", False))}
            if entry["element"] and entry["source_ref"]:
                result.setdefault("provenance", []).append(entry)
                changed = True
    # figure spec -> editable source files (path filled after render)
    spec, err = load_json(os.path.join(work_dir, "figure_spec.json"))
    if not err and isinstance(spec, dict) and spec.get("nodes"):
        found = any(sf.get("path", "").endswith(".svg")
                    for sf in result.get("editable_source_files", []))
        if not found:
            result.setdefault("editable_source_files", []).append(
                {"path": "figure01.svg", "kind": "svg", "layers": True,
                 "editable_text": True, "note": "rendered from figure_spec.json"})
            changed = True
    if changed:
        result.setdefault("change_log", []).append(
            {"time": now_iso(), "action": "merge_work",
             "detail": "merged entities/figure-spec provenance from /app/work"})
    return result


def finalize(result):
    result["runtime"]["finished_at"] = now_iso()
    try:
        start = datetime.datetime.fromisoformat(
            result["runtime"]["started_at"].replace("Z", "+00:00"))
        result["runtime"]["wall_seconds"] = round(
            (datetime.datetime.now(datetime.timezone.utc) - start).total_seconds(), 1)
    except (ValueError, KeyError, TypeError):
        result["runtime"]["wall_seconds"] = None
    return result


def _load_script(here, name):
    sys.path.insert(0, here)
    import importlib
    return importlib.import_module(name)


def main(argv=None):
    ap = argparse.ArgumentParser(description="sci-figure-editor entry point")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--force", action="store_true", help="rewrite skeleton even if present")
    ap.add_argument("--run", action="store_true", help="merge work, render SVG, self-check")
    a = ap.parse_args(argv)

    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    started = now_iso()
    here = os.path.dirname(os.path.abspath(__file__))
    result_path = os.path.join(a.output, "result.json")

    discovered = discover(a.input)
    errors = []

    case_data, case_err = load_json(discovered["case"]) if discovered["case"] else (None, None)
    if discovered["case"] is None:
        errors.append(f"no case.json found under {a.input}")
    if case_err:
        errors.append(case_err)

    case_id = extract_case_id(case_data)

    if a.force or not os.path.exists(result_path):
        result = build_skeleton(case_id, discovered.get("case"), discovered, errors, started)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        action = "skeleton_written"
    else:
        result, err = load_json(result_path)
        if err or not isinstance(result, dict):
            result = build_skeleton(case_id, discovered.get("case"), discovered,
                                    [err] if err else [], started)
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            action = "skeleton_recovered"
        else:
            action = "skeleton_present"

    rendered = None
    if a.run:
        result = merge_work(result, a.work, started)
        spec, spec_err = load_json(os.path.join(a.work, "figure_spec.json"))
        if not spec_err and isinstance(spec, dict):
            try:
                spec_to_svg = _load_script(here, "spec_to_svg")
                svg = spec_to_svg.render(spec)
                svg_path = os.path.join(a.output, "figure01.svg")
                with open(svg_path, "w", encoding="utf-8") as f:
                    f.write(svg)
                rendered = {"path": "figure01.svg", "bytes": len(svg.encode("utf-8"))}
                result["change_log"].append(
                    {"time": now_iso(), "action": "render",
                     "detail": f"rendered {svg_path} ({rendered['bytes']} bytes)"})
            except Exception as e:  # render failure is recorded, not raised
                result["errors"].append(f"render failed: {e}")
                result["degradations"].append({"item": "render", "reason": str(e)})
                result["change_log"].append(
                    {"time": now_iso(), "action": "render_failed", "detail": str(e)})
        # self-check must see the merged in-memory state, so persist first
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        try:
            selftest = _load_script(here, "selftest")
            ok, report = selftest.check(result_path, a.output)
            result["selfcheck"] = {"ok": ok, "errors": report["errors"],
                                   "warnings": report["warnings"]}
            result["change_log"].append(
                {"time": now_iso(), "action": "selfcheck",
                 "detail": f"ok={ok}, errors={len(report['errors'])}, "
                           f"warnings={len(report['warnings'])}"})
        except Exception as e:
            result["selfcheck"] = {"ok": False, "errors": [str(e)], "warnings": []}

    result = finalize(result)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True, "action": action, "case_id": case_id,
        "result": result_path, "rendered": rendered,
        "inputs": discovered, "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
