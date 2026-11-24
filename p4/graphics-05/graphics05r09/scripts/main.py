"""Graphical-abstract Skill entry point (stdlib only).

Subcommands:
  init      scan /input for case.json + task.md, persist case_normalized.json,
            a figure_spec.json template and a partial result.json skeleton.
  build     render fig_<case_id>.svg, fig_manifest.json and attempted PNG/PDF.
  validate  check SVG/spec against layout + provenance rules; exit 1 on errors.
  finalize  merge figure_spec + validation + manifest into final result.json.
  run       init -> build -> validate -> finalize in one call (skeleton-safe).

Paths default to the runtime contract (/input, /app/work, /app/output) and can be
overridden with --input/--work/--output for local self-testing.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import svg

MAX_FILE_BYTES = 2 * 1024 * 1024
CANON_KEYS = (
    "graphical_abstract", "editable_source", "exports", "entities", "relations",
    "data_mapping", "legend", "sources", "uncertainty", "limitations", "change_log",
    "pending_human",
)


def _read_text(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(MAX_FILE_BYTES)
    except OSError:
        return None


def _load_json(path):
    text = _read_text(path)
    if text is None:
        return None, "missing_or_unreadable"
    try:
        return json.loads(text), None
    except ValueError as ex:
        return None, f"bad_json: {ex}"


def _dump_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _discover(input_dir):
    files = {"case": None, "task": None, "materials": []}
    for pat in ("**/*", "*"):
        for path in glob.glob(os.path.join(input_dir, pat), recursive=True):
            if not os.path.isfile(path):
                continue
            base = os.path.basename(path).lower()
            if base in ("case.json", "input.json") and files["case"] is None:
                files["case"] = path
            elif base == "task.md" and files["task"] is None:
                files["task"] = path
            elif base.endswith(".json") and files["case"] is None:
                files["case"] = path
            elif base.endswith(".md") and files["task"] is None:
                files["task"] = path
            else:
                files["materials"].append(path)
    return files


def _case_id(case):
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        if isinstance(case, dict) and case.get(key):
            return str(case[key])
    return None


def _contract_keys(output_contract):
    """Return the field names of an output_contract in any common shape."""
    if isinstance(output_contract, dict):
        return list(output_contract.keys())
    if isinstance(output_contract, list):
        keys = []
        for item in output_contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.append(item.get("name") or item.get("key") or item.get("field"))
        return [k for k in keys if isinstance(k, str)]
    return []


def _canon_skeleton(case_id, note, status="partial"):
    return {
        "case_id": case_id,
        "status": status,
        "graphical_abstract": {"question": None, "method": None, "results": None,
                               "conclusion": None},
        "editable_source": None,
        "exports": [],
        "entities": [],
        "relations": [],
        "data_mapping": [],
        "legend": [],
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "change_log": [{"step": "init", "note": note}],
    }


def _empty_figure_spec(case_id, title, output_contract, note):
    return {
        "case_id": case_id,
        "title": title or "Graphical Abstract",
        "output_contract": output_contract,
        "abstract": {"question": None, "method": None, "results": None, "conclusion": None},
        "canvas": {"width": 1400, "height": 900},
        "constraints": {"font_min_px": 8, "grayscale_safe": True, "colorblind_safe": True},
        "elements": [],
        "entities": [],
        "relations": [],
        "data_mapping": [],
        "legend": [],
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "change_log": [{"step": "init", "note": note}],
    }


def cmd_init(args):
    _ensure_dir(args.work)
    _ensure_dir(args.output)
    files = _discover(args.input)

    case, case_err = _load_json(files["case"]) if files["case"] else (None, "missing_case")
    task_text = _read_text(files["task"]) or ""

    if case is None:
        case_id = None
        status = "failed"
        note = f"case.json 缺失或损坏 ({case_err})"
        spec = _empty_figure_spec(None, "Graphical Abstract", {}, note)
        spec["limitations"].append(note)
        result = _canon_skeleton(None, note, status="failed")
        result["limitations"].append(note)
        _dump_json(os.path.join(args.work, "case_normalized.json"),
                   {"case_id": None, "output_contract": {}, "input_files": files})
        _dump_json(os.path.join(args.work, "figure_spec.json"), spec)
        _dump_json(os.path.join(args.output, "result.json"), result)
        print(f"[init] status=failed: {note}")
        return 0

    case_id = _case_id(case) or os.path.splitext(os.path.basename(files["case"]))[0]
    output_contract = case.get("output_contract") if isinstance(case, dict) else None
    output_contract = output_contract if isinstance(output_contract, dict) else {}
    title = (case.get("title") or "").strip() if isinstance(case, dict) else ""
    if not title:
        first = next((l for l in task_text.splitlines() if l.startswith("#")), "")
        title = first.lstrip("#").strip() or "Graphical Abstract"

    note = "skeleton（部分结果骨架，先落盘）"
    spec = _empty_figure_spec(case_id, title, output_contract, note)
    result = _canon_skeleton(case_id, note, status="partial")
    for key in _contract_keys(output_contract):
        result.setdefault(key, None)

    normalized = {
        "case_id": case_id,
        "title": title,
        "output_contract": output_contract,
        "input_files": files,
        "task_has_text": bool(task_text.strip()),
    }
    _dump_json(os.path.join(args.work, "case_normalized.json"), normalized)
    _dump_json(os.path.join(args.work, "figure_spec.json"), spec)
    _dump_json(os.path.join(args.output, "result.json"), result)
    print(f"[init] status=partial case_id={case_id} files={len(files['materials'])}")
    return 0


def _layers_from(spec):
    layers = ["layer-frame"]
    for e in spec.get("elements", []):
        if e.get("type") == "panel":
            layers.append(f"layer-{e.get('id', '')}")
    return layers


def cmd_build(args):
    _ensure_dir(args.work)
    _ensure_dir(args.output)
    spec, err = _load_json(os.path.join(args.work, "figure_spec.json"))
    if spec is None:
        print(f"[build] figure_spec.json 缺失或损坏: {err}")
        return 1

    case_id = spec.get("case_id")
    if not case_id:
        case_id = "case"
    svg_text = svg.build_svg(spec)
    svg_path = os.path.join(args.output, f"fig_{case_id}.svg")
    with open(svg_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg_text)

    exports = svg.render_exports(svg_path, args.output, case_id)
    manifest = {
        "case_id": case_id,
        "svg_path": svg_path,
        "editable_source": {
            "path": svg_path, "format": "svg", "layers": _layers_from(spec),
            "text_editable": True,
        },
        "exports": exports,
    }
    _dump_json(os.path.join(args.work, "fig_manifest.json"), manifest)
    rendered = [e["format"] for e in exports if e["status"] == "rendered"]
    print(f"[build] svg={os.path.basename(svg_path)} previews={rendered or 'none'}")
    return 0


def cmd_validate(args):
    _ensure_dir(args.work)
    spec, err = _load_json(os.path.join(args.work, "figure_spec.json"))
    if spec is None:
        print(f"[validate] figure_spec.json 缺失或损坏: {err}")
        return 1
    case_id = spec.get("case_id") or "case"
    svg_text = _read_text(os.path.join(args.output, f"fig_{case_id}.svg"))
    report = svg.validate(spec, svg_text)
    _dump_json(os.path.join(args.work, "validation.json"), report)
    print(f"[validate] errors={len(report['errors'])} warnings={len(report['warnings'])}")
    for f in report["findings"]:
        print(f"  {f['level']:5} {f['code']}: {f['message']}")
    return 1 if report["errors"] else 0


def _compute_status(spec, report):
    if not spec.get("case_id"):
        return "failed"
    abstract = spec.get("abstract") or {}
    complete = all(abstract.get(k) for k in ("question", "method", "results", "conclusion"))
    if report.get("ok") and complete:
        return "completed"
    return "partial"


def cmd_finalize(args):
    _ensure_dir(args.output)
    spec, err = _load_json(os.path.join(args.work, "figure_spec.json"))
    if spec is None:
        spec = _empty_figure_spec(None, "Graphical Abstract", {}, f"figure_spec 缺失: {err}")
    manifest, _ = _load_json(os.path.join(args.work, "fig_manifest.json"))
    report, _ = _load_json(os.path.join(args.work, "validation.json"))

    if manifest is None:
        manifest = {"case_id": spec.get("case_id"), "editable_source": None, "exports": []}
    if report is None:
        report = {"ok": False, "findings": [], "errors": [], "warnings": []}

    abstract = spec.get("abstract") or {}
    result = {
        "case_id": spec.get("case_id"),
        "status": _compute_status(spec, report),
        "graphical_abstract": {k: abstract.get(k) for k in
                               ("question", "method", "results", "conclusion")},
        "editable_source": manifest.get("editable_source"),
        "exports": manifest.get("exports", []),
        "entities": spec.get("entities", []),
        "relations": spec.get("relations", []),
        "data_mapping": spec.get("data_mapping", []),
        "legend": spec.get("legend", []),
        "sources": spec.get("sources", []),
        "uncertainty": spec.get("uncertainty", []),
        "limitations": spec.get("limitations", []),
        "pending_human": spec.get("pending_human", []),
        "change_log": spec.get("change_log", []),
        "validation": report,
    }
    for key in _contract_keys(spec.get("output_contract") or {}):
        result.setdefault(key, None)

    change_log = result["change_log"]
    if not change_log or change_log[-1].get("step") != "finalize":
        change_log.append({"step": "finalize", "note": f"status={result['status']}"})
    result["change_log"] = change_log

    _dump_json(os.path.join(args.output, "result.json"), result)
    print(f"[finalize] status={result['status']}")
    return 0


def cmd_run(args):
    for fn in (cmd_init, cmd_build, cmd_validate):
        rc = fn(args)
        if rc != 0 and fn is cmd_build:
            print("[run] build 失败，仍尝试收尾")
    return cmd_finalize(args)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Graphical-abstract skill pipeline")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["init", "build", "validate", "finalize", "run"])
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    args = parser.parse_args(argv)
    return {
        "init": cmd_init, "build": cmd_build, "validate": cmd_validate,
        "finalize": cmd_finalize, "run": cmd_run,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
