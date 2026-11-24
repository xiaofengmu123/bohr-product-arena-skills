#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- Skill entry point for the biological-mechanism/pathway figure Skill.

Responsible for input discovery, incremental result maintenance, invoking the
intermediate-representation layer (figure_model.py), writing reviewable
intermediate artifacts and merging the final ``result.json``.

Usage (from the Skill root, e.g. /skills/weekly-skill)::

    python scripts/main.py --input /input --output /app/output --work /app/work
    python scripts/main.py --check --result /app/output/result.json
    python scripts/main.py --register-export /app/output/mechanism.png --format png \\
        --width 1600 --height 1200 --dpi 200 --generated-by browser-automation

Exit codes: 0 = success or warnings only; 1 = blocking validation errors;
2 = usage/IO error (message written to stderr).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import figure_model as fm  # noqa: E402

OUTPUT_CONTRACT_KEYS = [
    "case_id", "status", "task_summary", "editable_source_files",
    "export_artifacts", "entities", "relations", "legend", "data_mappings",
    "figure_model", "intermediate_artifacts", "sources", "uncertainty",
    "pending_human", "change_log", "errors", "degradations", "limitations",
    "runtime",
]

VALID_STATUS = ["success", "partial", "insufficient_evidence", "input_error", "failed"]

TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".tex"}
MATERIAL_DIR_NAMES = {"materials", "material", "sources", "papers", "data"}


# ---------------------------------------------------------------------------
# Input discovery / decoding
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    """Read a UTF-8 text file, tolerating BOM and common encoding damage."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_json_tolerant(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    """Parse JSON; return (value, error). Never raises."""
    try:
        text = read_text(path)
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{path.name}: {exc}"


def discover_inputs(input_dir: Path) -> Dict[str, Any]:
    """Scan /input for case.json, task.md and material files."""
    found: Dict[str, Any] = {
        "case_path": None, "task_path": None, "material_files": [],
        "case_json": None, "case_error": None, "task_text": None,
        "input_files": [],
    }
    if not input_dir.exists():
        found["case_error"] = f"input directory not found: {input_dir}"
        return found

    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        found["input_files"].append(str(p.relative_to(input_dir)))
        name = p.name.lower()
        if name == "case.json":
            found["case_path"] = p
            found["case_json"], found["case_error"] = parse_json_tolerant(p)
        elif name in ("task.md", "task.txt"):
            found["task_path"] = p
            found["task_text"] = read_text(p)
        elif name == "task.json":
            found["task_path"] = p
            found["task_text"] = read_text(p)

    # Material files: text files and PDFs outside the case/task files.
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p == found["case_path"] or p == found["task_path"]:
            continue
        ext = p.suffix.lower()
        rel = str(p.relative_to(input_dir))
        parent = p.parent.name.lower()
        in_material_dir = parent in MATERIAL_DIR_NAMES or any(
            part.lower() in MATERIAL_DIR_NAMES for part in p.parts)
        if ext == ".pdf":
            found["material_files"].append({"path": rel, "kind": "pdf",
                                            "needs_pdf_tool": True})
        elif ext in TEXT_EXTS or in_material_dir:
            try:
                text = read_text(p)
                found["material_files"].append({
                    "path": rel, "kind": "text", "text": text,
                    "needs_pdf_tool": False,
                })
            except Exception as exc:  # noqa: BLE001
                found["material_files"].append({
                    "path": rel, "kind": "text", "read_error": str(exc),
                    "needs_pdf_tool": False,
                })
        elif ext in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            found["material_files"].append({"path": rel, "kind": "image",
                                            "needs_ocr": True})
    return found


def extract_case_id(case_json: Any, input_dir: Path) -> str:
    if isinstance(case_json, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "caseId", "task_id"):
            val = case_json.get(key)
            if val not in (None, ""):
                return str(val)
        # nested "input" or "meta" objects occasionally hold the id
        for sub in ("input", "meta", "metadata"):
            if isinstance(case_json.get(sub), dict):
                for key in ("case_id", "id", "challenge_id"):
                    val = case_json[sub].get(key)
                    if val not in (None, ""):
                        return str(val)
    if case_json is not None and not isinstance(case_json, dict):
        return "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# Plan discovery (the agent writes plan.json into /app/work)
# ---------------------------------------------------------------------------

def load_plan(work_dir: Path, plan_arg: Optional[str]) -> Tuple[Dict[str, Any], str]:
    candidates = []
    if plan_arg:
        candidates.append(Path(plan_arg))
    candidates.append(work_dir / "plan.json")
    candidates.append(work_dir / "figure_plan.json")
    for p in candidates:
        if p.exists():
            try:
                return json.loads(read_text(p)), str(p)
            except Exception as exc:  # noqa: BLE001
                return {}, f"failed to parse plan {p}: {exc}"
    return {}, ""


# ---------------------------------------------------------------------------
# Result maintenance
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def new_skeleton(case_id: str) -> Dict[str, Any]:
    return {
        "schema_version": fm.SCHEMA_VERSION,
        "case_id": case_id,
        "status": "partial",
        "task_summary": "",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "figure_model": {},
        "intermediate_artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "at": now_iso(),
            "action": "skeleton_written",
            "detail": "partial result skeleton persisted before any analysis",
        }],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {"started_at": now_iso(), "finished_at": None},
    }


def load_existing_result(out_dir: Path) -> Optional[Dict[str, Any]]:
    p = out_dir / "result.json"
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:  # noqa: BLE001
        return None


def append_change(result: Dict[str, Any], action: str, detail: str) -> None:
    result.setdefault("change_log", []).append({
        "at": now_iso(), "action": action, "detail": detail,
    })


def aggregate_sources(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(srcs, refs):
        for s in srcs or []:
            key = (s.get("locator", ""), s.get("quote", ""))
            if key in seen or not (s.get("locator") or s.get("quote")):
                continue
            seen.add(key)
            out.append({"locator": s.get("locator", ""),
                        "quote": s.get("quote", ""),
                        "element_refs": refs})

    for ent in model.get("entities", []):
        add(ent.get("sources"), [ent.get("id")])
    for rel in model.get("relations", []):
        add(rel.get("sources"), [rel.get("id")])
    for dm in model.get("data_mappings", []):
        add(dm.get("sources"), [dm.get("entity") or dm.get("subject_raw")])
    return out


def derive_status(errors: List[str], model: Dict[str, Any], case_error: Optional[str],
                  has_material: bool, warnings: List[str]) -> str:
    if errors:
        return "input_error"
    if case_error and not model.get("entities") and not model.get("relations"):
        return "insufficient_evidence"
    if not model.get("entities") and not model.get("relations"):
        return "insufficient_evidence" if not has_material else "partial"
    if model.get("uncertainty") or model.get("pending_human"):
        return "partial"
    if warnings:
        return "partial"
    return "success"


def summarize_task(found: Dict[str, Any]) -> str:
    if found.get("task_text"):
        text = str(found["task_text"]).strip()
        return text[:400]
    if isinstance(found.get("case_json"), dict):
        for key in ("title", "task", "description", "summary"):
            if found["case_json"].get(key):
                return str(found["case_json"][key])[:400]
    return ""


# ---------------------------------------------------------------------------
# Build / check / register-export
# ---------------------------------------------------------------------------

def build(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    found = discover_inputs(input_dir)
    case_id = extract_case_id(found.get("case_json"), input_dir)

    result = load_existing_result(out_dir)
    if result is None or result.get("case_id") != case_id:
        result = new_skeleton(case_id)
    result.setdefault("runtime", {})["started_at"] = result["runtime"].get("started_at") or now_iso()

    errors: List[str] = []
    degradations: List[str] = []

    # Record PDF/image material that the script cannot read itself.
    for mf in found["material_files"]:
        if mf.get("needs_pdf_tool") or mf.get("needs_ocr"):
            degradations.append(
                f"material {mf['path']} needs platform tool "
                f"({'pdf-text-tool' if mf.get('needs_pdf_tool') else 'ocr-tool'}); "
                "agent should extract text and re-run")
    if found.get("case_error"):
        degradations.append(str(found["case_error"]))

    # Persist the agent-authored extraction plan (if any) into output for audit.
    plan, plan_path = load_plan(work_dir, args.plan)
    if plan_path:
        plan_dst = out_dir / "plan.json"
        try:
            shutil.copyfile(plan_path, plan_dst)
        except Exception as exc:  # noqa: BLE001
            degradations.append(f"could not copy plan.json: {exc}")

    model: Dict[str, Any]
    if plan:
        model = fm.build_model(plan)
    else:
        model = {"schema_version": fm.SCHEMA_VERSION, "case_id": case_id,
                 "entities": [], "relations": [], "layout": {},
                 "legend": [], "data_mappings": [], "uncertainty": [],
                 "pending_human": [], "_internal": {}}
    if not model.get("layout"):
        model["layout"] = {
            "compartments": [], "bands": [], "positions": {},
            "canvas": {"width": 680, "height": 200},
            "feedback_loops": [], "orientation": "vertical",
            "order": "extracellular -> membrane -> cytosol -> nucleus",
        }

    model_errors, model_warnings = fm.validate_model(model)
    errors.extend(model_errors)

    # Render SVG + PDF regardless of validation errors (best-effort partial).
    svg_text = fm.render_svg(model)
    pdf_bytes = fm.render_pdf(model)
    svg_path = out_dir / "mechanism.svg"
    pdf_path = out_dir / "mechanism.pdf"
    model_path = out_dir / "figure_model.json"
    svg_path.write_text(svg_text, encoding="utf-8")
    pdf_path.write_bytes(pdf_bytes)
    model_path.write_text(json.dumps(fm.publicize(model), ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # Build the merged result.
    public = fm.publicize(model)
    result.update({
        "case_id": case_id,
        "task_summary": summarize_task(found),
        "editable_source_files": [{
            "path": "mechanism.svg",
            "format": "svg",
            "editable": True,
            "layers": ["compartments", "relations", "entities", "data", "legend"],
            "description": "layered editable SVG with data-* traceability attributes",
        }],
        "entities": public.get("entities", []),
        "relations": public.get("relations", []),
        "legend": public.get("legend", []),
        "data_mappings": public.get("data_mappings", []),
        "figure_model": public,
        "intermediate_artifacts": [
            {"path": "plan.json", "role": "extraction_plan",
             "description": "agent-authored extraction plan with verbatim quotes"},
            {"path": "figure_model.json", "role": "canonical_figure_model",
             "description": "normalized, validated, reviewable figure model"},
        ],
        "sources": aggregate_sources(model),
        "uncertainty": public.get("uncertainty", []),
        "pending_human": public.get("pending_human", []),
        "errors": errors,
        "degradations": list(dict.fromkeys(degradations)),
        "limitations": _build_limitations(model_warnings, degradations),
        "runtime": result.get("runtime", {}),
    })
    result["runtime"]["finished_at"] = now_iso()
    result["runtime"]["wall_seconds_estimate"] = _elapsed(result["runtime"])

    # Export artifacts: SVG + PDF are script-generated; PNG is registered later
    # by the agent (browser-automation). Keep prior registrations.
    result["export_artifacts"] = _merge_export_artifacts(
        result.get("export_artifacts", []),
        [
            {"path": "mechanism.svg", "format": "svg", "generated_by": "scripts/figure_model.py",
             "width_px": public["layout"]["canvas"]["width"],
             "height_px": public["layout"]["canvas"]["height"],
             "font_check": "font-family stack replaceable (SVG authoritative for CJK)",
             "color_check": "palette self-consistent with legend"},
            {"path": "mechanism.pdf", "format": "pdf", "generated_by": "scripts/figure_model.py",
             "width_pt": public["layout"]["canvas"]["width"],
             "height_pt": public["layout"]["canvas"]["height"],
             "font_check": "built-in Helvetica/Helvetica-Oblique; CJK labels degraded to '?' in PDF only",
             "color_check": "palette matches SVG"},
        ])

    has_material = bool(found["material_files"])
    result["status"] = derive_status(errors, model, found.get("case_error"), has_material,
                                     model_warnings)
    append_change(result, "build",
                  f"entities={len(public.get('entities', []))} "
                  f"relations={len(public.get('relations', []))} "
                  f"errors={len(errors)} warnings={len(model_warnings)}")

    out_path = out_dir / "result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if model_warnings:
        print("[warnings]")
        for w in model_warnings:
            print("  -", w)
    if errors:
        print("[errors]")
        for e in errors:
            print("  -", e)
        return 1
    return 0


def _build_limitations(warnings: List[str], degradations: List[str]) -> List[str]:
    limits = list(dict.fromkeys(warnings + degradations))
    return limits


def _elapsed(runtime: Dict[str, Any]) -> Optional[float]:
    try:
        a = _dt.datetime.fromisoformat(runtime.get("started_at", ""))
        b = _dt.datetime.fromisoformat(runtime.get("finished_at", ""))
        return max(0.0, (b - a).total_seconds())
    except Exception:  # noqa: BLE001
        return None


def _merge_export_artifacts(existing: List[Dict[str, Any]],
                            generated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in existing:
        merged[item.get("path", "")] = dict(item)
    for item in generated:
        merged[item.get("path", "")] = dict(item)
    return [merged[k] for k in sorted(merged)]


def check(args: argparse.Namespace) -> int:
    out_dir = Path(args.output) if args.output else None
    result_path = Path(args.result) if args.result else (out_dir / "result.json" if out_dir else None)
    if result_path is None or not result_path.exists():
        print(f"result file not found: {result_path}", file=sys.stderr)
        return 2
    try:
        result = json.loads(read_text(result_path))
    except Exception as exc:  # noqa: BLE001
        print(f"invalid result.json: {exc}", file=sys.stderr)
        return 1

    errors: List[str] = []
    warnings: List[str] = []

    for key in OUTPUT_CONTRACT_KEYS:
        if key not in result:
            errors.append(f"missing contract key: {key}")
    if result.get("case_id") in (None, ""):
        errors.append("empty case_id")
    if result.get("status") not in VALID_STATUS:
        errors.append(f"invalid status: {result.get('status')}")

    base = result_path.parent
    for src in result.get("editable_source_files", []) or []:
        p = base / src.get("path", "")
        if not p.exists():
            errors.append(f"editable source file missing: {src.get('path')}")
    for art in result.get("export_artifacts", []) or []:
        p = base / art.get("path", "")
        if not p.exists():
            errors.append(f"export artifact missing: {art.get('path')}")
    for ia in result.get("intermediate_artifacts", []) or []:
        p = base / ia.get("path", "")
        if not p.exists():
            warnings.append(f"intermediate artifact missing: {ia.get('path')}")

    if result.get("status") in ("partial", "insufficient_evidence", "input_error", "failed"):
        if not result.get("limitations"):
            warnings.append(f"status={result.get('status')} but limitations empty")
    if not result.get("change_log"):
        errors.append("change_log empty (skeleton must be persisted first)")

    for e in errors:
        print("ERROR:", e)
    for w in warnings:
        print("WARN:", w)
    print(f"check: errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


def register_export(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    result_path = out_dir / "result.json"
    result = load_existing_result(out_dir)
    if result is None:
        print("no result.json to register into; run build first", file=sys.stderr)
        return 2
    target = Path(args.register_export[0])
    fmt = (args.format or target.suffix.lstrip(".")).lower()
    if not target.exists():
        print(f"artifact file not found: {target}", file=sys.stderr)
        return 2
    if fmt == "png" and not target.read_bytes().startswith(b"\x89PNG"):
        print("file is not a valid PNG", file=sys.stderr)
        return 2
    if fmt == "pdf" and not target.read_bytes().startswith(b"%PDF"):
        print("file is not a valid PDF", file=sys.stderr)
        return 2
    item = {
        "path": str(target.relative_to(out_dir) if _is_relative_to(target, out_dir) else target),
        "format": fmt,
        "generated_by": args.generated_by or "external-tool",
        "width_px": args.width,
        "height_px": args.height,
        "dpi": args.dpi,
        "font_check": args.font_check or "not assessed",
        "color_check": args.color_check or "not assessed",
    }
    item = {k: v for k, v in item.items() if v is not None}
    result["export_artifacts"] = _merge_export_artifacts(
        result.get("export_artifacts", []), [item])
    append_change(result, "register_export", f"registered {fmt} artifact {item['path']}")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"registered {fmt}: {item['path']}")
    return 0


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="biological mechanism/pathway figure skill")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--plan", default=None, help="explicit plan.json path")
    ap.add_argument("--check", action="store_true", help="validate an existing result.json")
    ap.add_argument("--result", default=None, help="result.json path for --check")
    ap.add_argument("--register-export", nargs="+", metavar="FILE",
                    help="register an externally rendered artifact (PNG/PDF)")
    ap.add_argument("--format", default=None)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--dpi", type=int, default=None)
    ap.add_argument("--generated-by", default=None)
    ap.add_argument("--font-check", default=None)
    ap.add_argument("--color-check", default=None)
    args = ap.parse_args(argv)

    try:
        if args.check:
            return check(args)
        if args.register_export:
            return register_export(args)
        return build(args)
    except Exception as exc:  # noqa: BLE001
        # Never leave a crash without a best-effort legal result.
        try:
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            result = load_existing_result(out_dir) or new_skeleton(
                extract_case_id(None, Path(args.input)))
            result["status"] = "failed"
            result.setdefault("errors", []).append(f"unhandled exception: {exc}")
            result.setdefault("limitations", []).append(traceback.format_exc(limit=5))
            result.setdefault("runtime", {})["finished_at"] = now_iso()
            (out_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
