#!/usr/bin/env python3
"""Academic paper writing skill entry point.

Pipeline steps (each idempotent, deterministic, stdlib-only):
  init      discover input, write partial result.json skeleton + material index
            + evidence map + contract meta (the "first-3-minutes" safe landing).
  record    append a change_log entry (--note ...).
  check     delegate to consistency_check.py against draft/evidence/index.
  finalize  merge evidence/sources/uncertainty/change_log into result.json.

Degradation contract: every step tolerates missing/bad files, records
errors/degradations, and still exits 0 with a legal partial result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_FILE_BYTES = 2 * 1024 * 1024
SKIP_NAMES = {"case.json", "task.md", "result.json"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".tex",
             ".rst", ".xml", ".yaml", ".yml"}
BINARY_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg",
               ".gif", ".svg", ".tif", ".tiff", ".webp", ".odt", ".ods"}
DEFAULT_CONTRACT = {"deliverable": "manuscript.md", "fact_source_map": {},
                    "version_limits": {}}
STANDARD_KEYS = ["sources", "uncertainty", "pending_human", "change_log",
                 "errors", "degradations", "outputs", "runtime"]


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default=None):
    """Read JSON with BOM/bad-encoding tolerance; never raises."""
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        return json.loads(text)
    except Exception:
        return default


def save_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                                   sort_keys=True), encoding="utf-8")
        return True
    except Exception:
        return False


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        data = path.read_bytes()
        if limit is not None and len(data) > limit:
            data = data[:limit]
        return data.decode("utf-8-sig", errors="replace")
    except Exception:
        return ""


def nullify(value):
    """Deep-copy a contract value, mapping None -> null; containers kept."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: nullify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [nullify(v) for v in value]
    return value


def discover_case(input_dir: Path):
    """Return (case_dict, task_text). Never raises."""
    case = {}
    task = ""
    if not input_dir.is_dir():
        return case, task
    for name in ("case.json", "Case.json", "CASE.json"):
        p = input_dir / name
        if p.is_file():
            case = load_json(p, default={}) or {}
            break
    t = input_dir / "task.md"
    if t.is_file():
        task = read_text(t)
    return case, task


def material_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in BINARY_EXTS:
        return "binary"
    return "other"


def discover_materials(input_dir: Path):
    """Return sorted list of material file dicts, skipping inputs/results."""
    items = []
    if not input_dir.is_dir():
        return items
    for p in sorted(input_dir.rglob("*"), key=lambda x: str(x).lower()):
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES or p.name.startswith("."):
            continue
        if p.name == "task.md":
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        rel = str(p.relative_to(input_dir))
        items.append({
            "ref_id": "",
            "path": rel,
            "type": material_type(p),
            "size": size,
            "status": "unindexed" if size <= MAX_FILE_BYTES else "too_long",
            "key_claims": [],
            "numbers": [],
            "citations": [],
            "flags": [],
        })
    for i, it in enumerate(items, 1):
        it["ref_id"] = f"S{i}"
    return items


def build_index(case, materials, generated_at: str):
    return {
        "case_id": case.get("case_id") or case.get("id") or "unknown",
        "generated_at": generated_at,
        "materials": materials,
        "literature": [],
    }


def skeleton_result(case, contract, start_ts: str):
    result = {
        "case_id": case.get("case_id") or case.get("id") or "unknown",
        "status": "in_progress",
    }
    merged = dict(DEFAULT_CONTRACT)
    merged.update(contract if isinstance(contract, dict) else {})
    for key, value in merged.items():
        result[key] = nullify(value)
    for key in STANDARD_KEYS:
        if key in result:
            continue
        result[key] = [] if key in ("sources", "uncertainty", "pending_human",
                                    "change_log", "errors", "degradations",
                                    "outputs") else {}
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{"ts": start_ts, "step": "init",
                             "note": "partial skeleton written"}]
    result["errors"] = []
    result["degradations"] = []
    result["outputs"] = []
    result["runtime"] = {"started_at": start_ts, "finished_at": None,
                         "steps": ["init"]}
    return result


def record_step(work_dir: Path, note: str, step: str = "record"):
    result_path = work_dir.parent / "output" / "result.json"
    # tolerate work_dir passed as /app/output vs /app/work; read/write via paths
    log = {"ts": now_ts(), "step": step, "note": note}
    # append to result.json if present
    candidates = [Path("/app/output/result.json"),
                  Path("output/result.json"),
                  work_dir.parent / "output" / "result.json"]
    for c in candidates:
        if c.is_file():
            data = load_json(c, default={})
            if isinstance(data, dict):
                data.setdefault("change_log", []).append(log)
                save_json(c, data)
                return True
    # fallback: append to a work-side log file
    log_path = work_dir / "change_log.jsonl"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_evidence(work_dir: Path):
    p = work_dir / "evidence_map.json"
    if p.is_file():
        data = load_json(p, default={}) or {}
        return data.get("entries", [])
    return []


def read_uncertainty(work_dir: Path):
    p = work_dir / "uncertainty.json"
    if p.is_file():
        data = load_json(p, default={}) or {}
        return data.get("items", [])
    return []


def infer_status(materials, evidence, deliverable_exists: bool,
                 errors, explicit: str | None):
    if explicit:
        return explicit
    if not materials:
        return "insufficient_evidence"
    if errors:
        return "partial"
    if deliverable_exists and evidence:
        return "complete"
    return "partial"


def list_outputs(output_dir: Path):
    outs = []
    if output_dir.is_dir():
        for p in sorted(output_dir.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.name not in {"result.json"}:
                outs.append(p.name)
    return outs


def run_check(draft: Path, index: Path, evidence: Path, out: Path):
    """Delegate to consistency_check.py; returns report dict or None."""
    mod = HERE / "consistency_check.py"
    if not mod.exists():
        return None
    sys.path.insert(0, str(HERE))
    try:
        import consistency_check as cc
        report = cc.run_all(draft, index, evidence, None)
        save_json(out, report)
        return report
    except Exception:
        return None


def finalize(args):
    start_ts = now_ts()
    case, task = discover_case(args.input)
    contract = case.get("output_contract", {}) if isinstance(case, dict) else {}
    materials = discover_materials(args.input)
    work_dir = args.work
    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    result = skeleton_result(case, contract, start_ts)
    evidence = read_evidence(work_dir)
    uncertainty = read_uncertainty(work_dir)
    errors = []
    degradations = []

    # preserve change_log already accumulated since init (incl. --step record)
    prior_result = load_json(output_dir / "result.json", default={}) or {}
    prior_log = prior_result.get("change_log") if isinstance(prior_result, dict) else None
    if isinstance(prior_log, list) and prior_log:
        result["change_log"] = list(prior_log)
        if result["change_log"] and result["change_log"][0].get("step") != "init":
            result["change_log"].insert(0, {"ts": start_ts, "step": "init",
                                            "note": "partial skeleton written"})
    # merge any change_log.jsonl fallback entries
    logj = work_dir / "change_log.jsonl"
    if logj.is_file():
        merged_notes = {e.get("note") for e in result["change_log"]}
        for line in read_text(logj).splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if isinstance(entry, dict) and entry.get("note") not in merged_notes:
                result["change_log"].append(entry)
                merged_notes.add(entry.get("note"))

    index_path = work_dir / "material_index.json"
    if index_path.is_file():
        idx = load_json(index_path, default={}) or {}
        # keep the recorded materials instead of re-scanning, if present
        if isinstance(idx.get("materials"), list) and idx["materials"]:
            materials = idx["materials"]
    else:
        degradations.append("material_index.json missing; using fresh scan")

    for m in materials:
        if m.get("status") == "unparseable":
            degradations.append(f"{m.get('ref_id')} unparseable")
        if m.get("status") == "too_long":
            degradations.append(f"{m.get('ref_id')} exceeds size cap")

    deliverable = result.get("deliverable")
    deliverable_name = deliverable if isinstance(deliverable, str) else "manuscript.md"
    deliverable_exists = (output_dir / deliverable_name).is_file()
    if not deliverable_exists:
        # also check manuscript.md default
        deliverable_exists = (output_dir / "manuscript.md").is_file()
        if deliverable_exists and not isinstance(deliverable, str):
            result["deliverable"] = "manuscript.md"

    status = infer_status(materials, evidence, deliverable_exists, errors,
                          args.status)

    result["status"] = status
    result["sources"] = evidence
    result["uncertainty"] = uncertainty
    if not uncertainty:
        result["uncertainty"] = []
    result["errors"] = errors
    result["degradations"] = list(dict.fromkeys(degradations))
    result["outputs"] = list_outputs(output_dir)
    result["change_log"].append({"ts": now_ts(), "step": "finalize",
                                 "note": f"status={status}; sources={len(evidence)}"})
    result["runtime"]["finished_at"] = now_ts()
    result["runtime"]["steps"] = ["init", "finalize"]

    # ensure output_contract fields remain intact (they were echoed in skeleton)
    save_json(output_dir / "result.json", result)

    # also persist the material index + evidence map we used
    save_json(work_dir / "material_index.json",
              build_index(case, materials, start_ts))
    save_json(work_dir / "evidence_map.json",
              {"case_id": result["case_id"], "entries": evidence})
    return result


def init_step(args):
    start_ts = now_ts()
    case, task = discover_case(args.input)
    contract = case.get("output_contract", {}) if isinstance(case, dict) else {}
    materials = discover_materials(args.input)

    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)

    result = skeleton_result(case, contract, start_ts)
    result["status"] = "partial" if materials else "insufficient_evidence"
    result["degradations"] = []
    for m in materials:
        if m.get("status") == "too_long":
            result["degradations"].append(f"{m['ref_id']} too long (>2MB)")

    index = build_index(case, materials, start_ts)
    evidence = {"case_id": result["case_id"], "entries": []}
    contract_meta = {"case_id": result["case_id"],
                     "output_contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else [],
                     "task_bytes": len(task)}

    save_json(args.output / "result.json", result)
    save_json(args.work / "material_index.json", index)
    save_json(args.work / "evidence_map.json", evidence)
    save_json(args.work / "contract_meta.json", contract_meta)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Academic paper writing skill")
    ap.add_argument("--step", choices=["init", "record", "check", "finalize"],
                    default="init")
    ap.add_argument("--input", type=Path, default=Path("/input"))
    ap.add_argument("--output", type=Path, default=Path("/app/output"))
    ap.add_argument("--work", type=Path, default=Path("/app/work"))
    ap.add_argument("--draft", type=Path, default=None)
    ap.add_argument("--status", default=None,
                    choices=["in_progress", "partial", "complete",
                             "insufficient_evidence", "input_error", "failed"])
    ap.add_argument("--note", default=None)
    args = ap.parse_args(argv)

    if args.step == "init":
        init_step(args)
    elif args.step == "record":
        ok = record_step(args.work, args.note or "manual change")
        if not ok:
            print(json.dumps({"ok": False, "step": "record"}))
            return 0
    elif args.step == "check":
        draft = args.draft or (args.output / "manuscript.md")
        report = run_check(draft, args.work / "material_index.json",
                           args.work / "evidence_map.json",
                           args.work / "check_report.json")
        print(json.dumps({"ok": True, "step": "check",
                          "report": report or {}}, ensure_ascii=False))
    elif args.step == "finalize":
        finalize(args)

    print(json.dumps({"ok": True, "step": args.step}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
