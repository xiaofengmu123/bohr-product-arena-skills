#!/usr/bin/env python3
"""slides-01 orchestrator: contract scaffold -> material discovery -> intermediate
representation validation -> deterministic pptx/pdf build -> incremental result.json.

Standard library only. The agent writes plan.json / figure_model.json / draft.json;
this script only scaffolds, validates and materialises them. It never invents paper
content or benchmark numbers.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pptxlib  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

# Envelope fields always present on result.json, on top of the echoed contract.
ENVELOPE = {
    "status": "partial",
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
    "changes": [],
    "runtime": {},
}

LIST_FIELDS = {
    "content_map", "compliance_checks", "exceptions", "sources", "uncertainty",
    "pending_human", "errors", "degradations", "change_log", "changes",
}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}
ROLES = {"title", "background", "method", "result", "discussion", "limitation",
         "conclusion", "other"}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} read failed: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{Path(path).name} is empty"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, f"{Path(path).name} is not UTF-8"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON error: {exc}"


def write_json(path: Path, obj) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(
            (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError as exc:
        print(f"[error] write {path}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return False


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def extract_contract_keys(case: dict) -> list[str]:
    raw = case.get("output_contract")
    keys: list[str] = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    v = item.get(cand)
                    if isinstance(v, str) and v.strip():
                        keys.append(v.strip())
                        break
    ordered, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def scaffold_result(case: dict) -> dict:
    case_id = str(case.get("case_id") or case.get("id") or "unknown-case")
    result = {"case_id": case_id, "status": "partial"}
    for k in extract_contract_keys(case):
        if k not in result:
            result[k] = default_value(k)
    for k, v in ENVELOPE.items():
        result.setdefault(k, v if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
    result.setdefault("change_log", [])
    result.setdefault("changes", [])
    return result


def discover_materials(input_root: Path) -> list[dict]:
    materials = []
    if not input_root.is_dir():
        return materials
    for path in sorted(input_root.rglob("*")):
        if not path.is_file() or path.name == "case.json":
            continue
        rel = path.relative_to(input_root).as_posix()
        suffix = path.suffix.lower()
        kind = "other"
        if suffix == ".pdf":
            kind = "pdf"
        elif suffix in (".pptx", ".ppt"):
            kind = "template" if "template" in path.name.lower() else "slides"
        elif suffix in (".docx", ".doc"):
            kind = "document"
        elif suffix in (".md", ".txt", ".json", ".yaml", ".yml"):
            kind = "text"
        elif suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff"):
            kind = "image"
        materials.append({
            "path": rel, "kind": kind, "size": path.stat().st_size,
            "locator": f"{rel}", "mtime": int(path.stat().st_mtime),
        })
    return materials


def _ok(obj, errs: list, cond: bool, msg: str):
    if not cond:
        errs.append(msg)


def validate_plan(plan: dict) -> list[str]:
    errs = []
    if not isinstance(plan, dict):
        return ["plan.json must be a JSON object"]
    pages = plan.get("pages")
    if not isinstance(pages, list) or not pages:
        errs.append("plan.pages must be a non-empty list")
        return errs
    limit = plan.get("slide_limit")
    if isinstance(limit, int) and len(pages) > limit:
        errs.append(f"page count {len(pages)} exceeds slide_limit {limit}")
    seen = set()
    for i, p in enumerate(pages, start=1):
        if not isinstance(p, dict):
            errs.append(f"pages[{i}] not an object")
            continue
        pid = p.get("id")
        _ok(p, errs, isinstance(pid, str) and pid.strip(), f"pages[{i}].id required")
        if pid in seen:
            errs.append(f"duplicate page id {pid}")
        seen.add(pid)
        _ok(p, errs, p.get("role") in ROLES, f"page {pid}: role must be one of {sorted(ROLES)}")
        _ok(p, errs, isinstance(p.get("claim"), str) and p["claim"].strip(),
            f"page {pid}: claim required (one-sentence assertion)")
        _ok(p, errs, isinstance(p.get("bullet_points"), list) and p["bullet_points"],
            f"page {pid}: bullet_points required (non-empty list)")
        ev = p.get("evidence", [])
        _ok(p, errs, isinstance(ev, list), f"page {pid}: evidence must be a list")
        for j, e in enumerate(ev or []):
            if not isinstance(e, dict):
                errs.append(f"page {pid} evidence[{j}] not an object")
                continue
            _ok(p, errs, isinstance(e.get("locator"), str) and e["locator"].strip(),
                f"page {pid} evidence[{j}]: locator required")
            _ok(p, errs, isinstance(e.get("quote"), str) and e["quote"].strip(),
                f"page {pid} evidence[{j}]: verbatim quote required")
    return errs


def validate_figures(fig: dict) -> list[str]:
    errs = []
    if not isinstance(fig, dict):
        return ["figure_model.json must be a JSON object"]
    labels = set()
    for group in ("figures", "tables", "equations"):
        items = fig.get(group, [])
        if items is None:
            continue
        if not isinstance(items, list):
            errs.append(f"{group} must be a list")
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errs.append(f"{group}[{i}] not an object")
                continue
            label = it.get("label")
            _ok(fig, errs, isinstance(label, str) and label.strip(),
                f"{group}[{i}].label required")
            if label in labels:
                errs.append(f"duplicate label {label}")
            labels.add(label)
            if it.get("kind") not in (None, "data", "schematic", "photo", "equation", "table"):
                errs.append(f"{label}: kind must be data|schematic|photo|equation|table")
    return errs


def cross_check(plan: dict, fig: dict) -> list[str]:
    errs = []
    known = set()
    for group in ("figures", "tables", "equations"):
        for it in fig.get(group, []) or []:
            if isinstance(it, dict) and it.get("label"):
                known.add(it["label"])
    text_types = {"text", "data", "equation", "figure", "table"}
    for p in plan.get("pages", []):
        for e in p.get("evidence", []):
            if not isinstance(e, dict):
                continue
            t = e.get("type", "text")
            label = e.get("label")
            if t in ("figure", "table", "equation") and label:
                if label not in known:
                    errs.append(f"page {p.get('id')} references unknown {t} label '{label}'")
            if t not in text_types and label:
                errs.append(f"page {p.get('id')} evidence type '{t}' unknown")
    # reverse: figure marked used but never referenced
    for group in ("figures", "tables", "equations"):
        for it in fig.get(group, []) or []:
            if not isinstance(it, dict):
                continue
            if it.get("used") is True and it.get("label") and it["label"] not in known:
                errs.append(f"{it['label']} marked used but not in model")
    return errs


def draft_from_plan(plan: dict) -> list[dict]:
    """Materialise slides from plan.json so the agent can build even without a
    separate draft.json. Speaker notes come from plan.notes (not repeated text)."""
    slides = []
    for p in plan.get("pages", []):
        body = []
        claim = p.get("claim", "")
        if claim:
            body.append({"text": claim, "bold": True, "level": 0, "size": 2000})
        for b in p.get("bullet_points", []):
            body.append({"text": str(b), "bold": False, "level": 0, "size": 1800})
        slides.append({
            "title": p.get("title") or p.get("role", "slide"),
            "body": body,
            "notes": p.get("notes", ""),
            "footer": p.get("footer", ""),
        })
    return slides


def load_draft(work: Path):
    draft, err = read_json(work / "draft.json")
    if err is None and isinstance(draft, dict):
        slides = draft.get("slides")
        if isinstance(slides, list):
            return slides, "draft.json"
    plan, err = read_json(work / "plan.json")
    if err is None and isinstance(plan, dict):
        return draft_from_plan(plan), "plan.json (fallback)"
    return None, None


def update_result(result: dict, key: str, value):
    if key in result or key in DICT_FIELDS or key in LIST_FIELDS:
        result[key] = value
    else:
        result[key] = value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="slides-01 skill runner")
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--step", default="all",
                    help="comma-separated steps: scaffold,discover,validate,build,all")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    steps = {s.strip() for s in str(args.step).split(",") if s.strip()}
    valid_steps = {"scaffold", "discover", "validate", "build", "all"}
    unknown = steps - valid_steps
    if unknown:
        ap.error(f"--step unknown: {sorted(unknown)}")

    input_root = Path(args.input).expanduser() if args.input else DEFAULT_INPUT
    output_root = Path(args.output).expanduser() if args.output else DEFAULT_OUTPUT
    work_root = Path(args.work).expanduser() if args.work else DEFAULT_WORK
    for d in (work_root, output_root):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[degrade] cannot create {d}: {exc}", file=sys.stderr)
            work_root = Path.cwd() / "skill_work"
            output_root = Path.cwd() / "skill_output"
            work_root.mkdir(parents=True, exist_ok=True)
            output_root.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    case, case_err = read_json(input_root / "case.json")
    if case_err:
        case = {}
        print(f"[degrade] case.json: {case_err}", file=sys.stderr)
    if not isinstance(case, dict):
        case = {}

    result_path = output_root / "result.json"
    result, _ = read_json(result_path)
    if result is None or not isinstance(result, dict) or args.force:
        result = scaffold_result(case)

    def note_change(action: str, detail: str = ""):
        entry = {"t": now_iso(), "action": action, "detail": detail}
        for key in ("change_log", "changes"):
            lst = result.setdefault(key, [])
            if not isinstance(lst, list):
                lst = []
                result[key] = lst
            lst.append(entry)

    if ("scaffold" in steps or "all" in steps):
        result = scaffold_result(case)
        note_change("scaffold", "result skeleton written")
        write_json(result_path, result)

    materials = discover_materials(input_root)
    write_json(work_root / "materials.json", {"materials": materials})

    if ("discover" in steps or "all" in steps):
        result["sources"] = materials
        note_change("discover", f"{len(materials)} input files")
        write_json(result_path, result)

    if ("validate" in steps or "all" in steps):
        errs = []
        for name, fn in (("plan.json", validate_plan), ("figure_model.json", validate_figures)):
            obj, e = read_json(work_root / name)
            if e:
                errs.append(f"{name}: {e}")
            elif obj is not None:
                errs.extend(fn(obj))
        plan, pe = read_json(work_root / "plan.json")
        fig, fe = read_json(work_root / "figure_model.json")
        if pe is None and fe is None and plan and fig:
            errs.extend(cross_check(plan, fig))
        result["compliance_checks"] = [
            {"check": "intermediate-validation", "status": "pass" if not errs else "fail",
             "errors": errs}]
        for e in errs:
            print(f"[validate] {e}", file=sys.stderr)
        note_change("validate", f"{len(errs)} issues")
        write_json(result_path, result)

    if ("build" in steps or "all" in steps):
        slides, src = load_draft(work_root)
        if slides is None:
            result["errors"] = result.get("errors", []) + [
                "no draft.json or plan.json in work dir; cannot build pptx"]
            result["status"] = "insufficient_evidence"
            note_change("build", "skipped: no plan/draft")
            write_json(result_path, result)
        else:
            pptx_path = output_root / "presentation.pptx"
            pptxlib.build_pptx(slides, pptx_path,
                                title=plan_title(work_root),
                                author="slides-01-skill")
            ok, msg = pptxlib.validate_pptx(pptx_path)
            pdf_path, pdf_err = pptxlib.convert_to_pdf(pptx_path, output_root)
            n = len(slides)
            result["editable_source"] = {
                "path": "presentation.pptx", "format": "pptx", "slides": n,
                "valid": ok, "check": msg, "source_ir": src}
            export = {"pdf": {"path": "presentation.pdf",
                              "pages": pptxlib.pdf_page_count(pdf_path) if pdf_path else None}}
            if pdf_err:
                try:
                    svg_names = pptxlib.render_svg(slides, output_root / "preview")
                    export["svg"] = svg_names
                except OSError as exc:
                    result["errors"] = result.get("errors", []) + [
                        f"svg fallback failed: {exc.__class__.__name__}"]
                result["degradations"] = result.get("degradations", []) + [pdf_err]
                result["exceptions"] = result.get("exceptions", []) + [
                    f"pdf preview not produced: {pdf_err}; svg preview written instead"]
            result["export_artifact"] = export
            if not ok:
                result["errors"] = result.get("errors", []) + [f"pptx invalid: {msg}"]
            result["status"] = "ok" if (ok and not pdf_err and not result.get("errors")) else "partial"
            note_change("build", f"{n} slides -> pptx; valid={ok}; pdf={pdf_path is not None}")
            write_json(result_path, result)

    result.setdefault("runtime", {})
    result["runtime"]["seconds"] = round(time.time() - t0, 2)
    result["runtime"]["finished_at"] = now_iso()
    write_json(result_path, result)
    print(json.dumps({"case_id": result.get("case_id"), "status": result.get("status"),
                      "result": str(result_path), "runtime_s": result["runtime"]["seconds"]},
                     ensure_ascii=False))
    return 0


def plan_title(work: Path) -> str:
    plan, err = read_json(work / "plan.json")
    if err is None and isinstance(plan, dict):
        for k in ("title", "talk_title", "presentation_title"):
            if isinstance(plan.get(k), str) and plan[k].strip():
                return plan[k]
    return "Academic presentation"


if __name__ == "__main__":
    raise SystemExit(main())
