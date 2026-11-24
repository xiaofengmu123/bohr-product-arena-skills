"""Entry point for the citation-management skill (deterministic backbone).

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--phase skeleton|pipeline|finalize]
                               [--budget] [--self-test]

Contract highlights (see SKILL.md for the full 900s / 64-request plan):
1. Immediately create /app/output and /app/work and write a valid
   result.json skeleton + placeholder artifacts (the "early partial"
   guarantee, required inside the first 3 minutes).
2. Run the deterministic pipeline (parse library, deduplicate, renumber,
   format, consistency check) and write editable artifacts.
3. Append a checkpoint line to /app/work/checkpoint.jsonl after each phase
   so the time-budget discipline is auditable.
4. Always exit 0 with an honest status; never fabricate metadata or scores.

Semantic work (claim-level citation placement, claim<->reference matching,
external verification) is done by the Agent following SKILL.md and written
back into /app/output/evidence_map.json and result.json; --finalize
re-syncs result.json with the current artifacts.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ci  # noqa: E402

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "artifacts", "runtime",
]

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")


def _case_id(case: dict) -> str:
    if not isinstance(case, dict):
        return ""
    for key in ("case_id", "id", "challenge_id", "armiId", "caseId"):
        v = case.get(key)
        if v:
            return str(v)
    return ""


def build_skeleton(case: dict, ledger: ci.Ledger) -> tuple:
    """Echo every output_contract key as null/[] plus standard fields.

    Returns (skeleton, contract_keys) so the pipeline can later backfill the
    contract keys with actual artifact paths where they match."""
    contract = {}
    if isinstance(case, dict):
        raw = case.get("output_contract", case.get("outputContract", {}))
        if isinstance(raw, dict):
            contract = {k: None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {k: None for k in raw if isinstance(k, str)}
    contract_keys = set(contract.keys())
    skeleton = {"case_id": _case_id(case), **contract}
    for field in RESULT_FIELDS:
        skeleton.setdefault(field, None)
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["change_log"] = []
    skeleton["errors"] = list(ledger.errors)
    skeleton["degradations"] = list(ledger.degradations)
    skeleton["pending_human"] = []
    skeleton["artifacts"] = {}
    skeleton["runtime"] = {
        "seconds": 0.0,
        "started_at": ci.timestamp(),
    }
    return skeleton, contract_keys


def _placeholder(kind: str) -> str:
    return (
        f"<!-- {kind} placeholder: semantic work pending. "
        "This is a deterministic skeleton, not a fabricated result. -->\n"
    )


def write_placeholders(out: Path, work: Path) -> dict:
    artifacts = {}
    for name, content in (
        ("manuscript.md", _placeholder("manuscript")),
        ("references.md", _placeholder("reference list")),
        ("evidence_map.json", "{}"),
        ("consistency_report.json", "{}"),
    ):
        ci.write_text_atomic(out / name, content)
        artifacts[name.split(".")[0]] = str(out / name)
    return artifacts


def append_checkpoint(work: Path, phase: str, status: str, seconds: float,
                      extra: dict | None = None) -> None:
    record = {
        "phase": phase,
        "status": status,
        "at": ci.timestamp(),
        "elapsed_seconds": round(seconds, 3),
    }
    if extra:
        record.update(extra)
    try:
        with open(work / "checkpoint.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_case_and_task(input_dir: Path, ledger: ci.Ledger):
    case_path = input_dir / "case.json"
    case = {}
    if case_path.exists():
        case = ci.load_json_file(case_path, ledger) or {}
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text = ci.read_text(task_path, ledger)
    return case, task_text


def build_input_manifest(discovered: dict, work: Path) -> list:
    manifest = []
    for kind, paths in discovered.items():
        for p in paths:
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            manifest.append({
                "kind": kind,
                "path": str(p),
                "name": p.name,
                "bytes": size,
            })
    return manifest


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, style: str) -> int:
    started = time.monotonic()
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    ledger = ci.Ledger()
    case, task_text = read_case_and_task(input_dir, ledger)

    # --- Phase A: early skeleton (first seconds; required in first 3 min) ---
    skeleton, contract_keys = build_skeleton(case, ledger)
    ci.write_json_atomic(output_dir / "result.json", skeleton)
    placeholder_artifacts = write_placeholders(output_dir, work_dir)
    skeleton["artifacts"] = placeholder_artifacts
    skeleton["runtime"]["seconds"] = round(time.monotonic() - started, 3)
    ci.write_json_atomic(output_dir / "result.json", skeleton)
    append_checkpoint(work_dir, "skeleton", skeleton["status"],
                      time.monotonic() - started)

    # --- Phase B: deterministic pipeline ---
    discovered = ci.discover_files(input_dir, ledger)
    manifest = build_input_manifest(discovered, work_dir)
    ci.write_json_atomic(work_dir / "input_manifest.json", manifest)

    entries = ci.parse_library(discovered["library"], ledger)
    canonical, merge_map = ci.deduplicate(entries)

    manuscript_text = ""
    manuscript_sources = []
    for p in discovered["manuscript"]:
        txt = ci.read_text(p, ledger)
        if txt:
            if manuscript_text:
                manuscript_text += f"\n\n<!-- source: {p.name} -->\n\n"
            manuscript_text += txt
            manuscript_sources.append(p.name)

    citations = ci.detect_citations(manuscript_text)
    mapping = ci.build_renumber_plan(citations)
    renumbered = ci.apply_renumber(manuscript_text, citations, mapping) \
        if mapping else manuscript_text

    # Reference order: numeric style uses first-appearance order; APA keeps
    # canonical (library) order until the Agent finalizes semantic ordering.
    if style == "apa":
        order = list(range(len(canonical)))
    else:
        order = [canonical_index_of(canonical, old) for old in _first_appearance(mapping)]
        order = [i for i in order if i is not None]
        order += [i for i in range(len(canonical)) if i not in order]

    reference_text = ci.format_reference_list(canonical, style, order) \
        if canonical else _placeholder("reference list (no library entries found)")

    report = ci.consistency_check(canonical, citations, mapping)

    evidence_map = {
        "version": "skeleton",
        "claims": [],
        "note": ("Semantic claim<->reference matching is pending; the Agent "
                 "fills locator + verbatim quote per SKILL.md section 5."),
    }

    # Normalized snapshot for reproducibility and later semantic work.
    ci.write_json_atomic(work_dir / "normalized_entries.json", {
        "entries": canonical,
        "merge_map": merge_map,
    })
    ci.write_json_atomic(work_dir / "renumber_plan.json", {
        "mapping": mapping,
        "citations": citations,
    })
    ci.write_json_atomic(work_dir / "consistency_report.json", report)

    # --- Phase C: write artifacts ---
    artifacts = {
        "manuscript": str(output_dir / "manuscript.md"),
        "references": str(output_dir / "references.md"),
        "evidence_map": str(output_dir / "evidence_map.json"),
        "consistency_report": str(output_dir / "consistency_report.json"),
    }
    ci.write_text_atomic(output_dir / "manuscript.md",
                         renumbered if renumbered.strip() else _placeholder("manuscript"))
    ci.write_text_atomic(output_dir / "references.md", reference_text)
    ci.write_json_atomic(output_dir / "evidence_map.json", evidence_map)
    ci.write_json_atomic(output_dir / "consistency_report.json", report)

    # --- Phase D: status & result.json ---
    if not manuscript_sources and not canonical:
        status = "insufficient_evidence"
    elif not manuscript_sources or not canonical:
        status = "partial"
    else:
        status = "partial"  # semantic verification still pending

    result = dict(skeleton)
    # Backfill output_contract keys that match a produced artifact.
    for key in contract_keys:
        if key in artifacts:
            result[key] = artifacts[key]
    result["status"] = status
    result["sources"] = [
        {"kind": "case", "path": str(input_dir / "case.json")},
        {"kind": "task", "path": str(input_dir / "task.md")},
    ] + [{"kind": m["kind"], "path": m["path"], "bytes": m["bytes"]}
         for m in manifest]
    result["artifacts"] = artifacts
    result["errors"] = list(ledger.errors)
    result["degradations"] = list(ledger.degradations)
    result["change_log"] = [
        {"op": "skeleton", "at": ci.timestamp(),
         "detail": "valid partial result.json + placeholder artifacts written first"},
        {"op": "parse", "at": ci.timestamp(),
         "detail": f"parsed {len(entries)} library entries; "
                   f"deduplicated to {len(canonical)} canonical entries"},
        {"op": "renumber", "at": ci.timestamp(),
         "detail": f"renumber plan for {len(mapping)} cited ids; "
                   f"{len(citations)} citation markers detected"},
        {"op": "consistency", "at": ci.timestamp(),
         "detail": f"{len(report['unresolved_citations'])} unresolved citations; "
                   f"{len(report['uncited_entries'])} uncited entries"},
    ]
    result["uncertainty"] = [
        "semantic claim<->reference matching not yet performed (Agent step)",
        "fuzzy/near-duplicate entries are NOT auto-merged; left for Agent confirmation",
    ] if canonical else []
    result["pending_human"] = []
    if not canonical:
        result["pending_human"].append("no reference library entries found or parsed")
    if not manuscript_sources:
        result["pending_human"].append("no manuscript text found")
    result["runtime"]["seconds"] = round(time.monotonic() - started, 3)

    ci.write_json_atomic(output_dir / "result.json", result)
    append_checkpoint(work_dir, "pipeline", status,
                      time.monotonic() - started,
                      {"entries": len(entries), "canonical": len(canonical),
                       "citations": len(citations)})
    return 0


def _first_appearance(mapping: dict) -> list:
    # mapping keys are old ids; sort by new number and return old ids.
    return [old for old, _ in sorted(mapping.items(), key=lambda kv: kv[1])]


def canonical_index_of(canonical: list, old_number: int):
    """Map an old citation number (which equals the raw entry 'index') to
    the canonical list position."""
    for i, e in enumerate(canonical):
        if e["index"] == old_number:
            return i
        if e.get("merged_from") and old_number in e["merged_from"]:
            return i
    return None


def run_finalize(output_dir: Path, work_dir: Path) -> int:
    """Re-sync result.json with the artifacts the Agent has edited, without
    recomputing the deterministic pipeline.  Idempotent and cheap."""
    result_path = output_dir / "result.json"
    if not result_path.exists():
        print("finalize: result.json missing; run pipeline first", file=sys.stderr)
        return 1
    result = ci.load_json_file(result_path, ci.Ledger()) or {}
    artifacts = {}
    for name in ("manuscript.md", "references.md", "evidence_map.json",
                 "consistency_report.json"):
        p = output_dir / name
        if p.exists():
            artifacts[name.split(".")[0]] = str(p)
    result["artifacts"] = artifacts
    runtime = result.get("runtime") or {}
    try:
        started = time.mktime(time.strptime(
            runtime.get("started_at", ci.timestamp()),
            "%Y-%m-%dT%H:%M:%SZ"))
        runtime["seconds"] = round(max(0.0, time.time() - started), 3)
    except (ValueError, OverflowError):
        runtime["seconds"] = 0.0
    result["runtime"] = runtime
    change_log = result.get("change_log") or []
    if not isinstance(change_log, list):
        change_log = []
    change_log.append({"op": "finalize", "at": ci.timestamp(),
                       "detail": "result.json re-synced with current artifacts"})
    result["change_log"] = change_log
    if result.get("status") not in STATUSES:
        result["status"] = "partial"
    ci.write_json_atomic(result_path, result)
    print("finalized:", result.get("case_id"), result.get("status"))
    return 0


def run_budget(work_dir: Path) -> int:
    total = 900.0
    journal = work_dir / "checkpoint.jsonl"
    elapsed = 0.0
    phases = []
    if journal.exists():
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                phases.append(rec)
                elapsed = max(elapsed, float(rec.get("elapsed_seconds", 0.0)))
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    print(f"budget: total=900s; elapsed~{elapsed:.1f}s; "
          f"remaining~{max(0.0, total - elapsed):.1f}s; "
          f"checkpoints={len(phases)}")
    for rec in phases:
        print(f"  - {rec.get('phase')}: {rec.get('status')} "
              f"@ {rec.get('elapsed_seconds', 0):.1f}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Citation-management deterministic backbone.")
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--work", default=str(DEFAULT_WORK))
    p.add_argument("--style", choices=["gbt7714", "apa"], default="gbt7714")
    p.add_argument("--phase", choices=["pipeline", "finalize"],
                   default="pipeline")
    p.add_argument("--budget", action="store_true",
                   help="print checkpoint journal and remaining time budget")
    p.add_argument("--self-test", action="store_true",
                   help="run in-memory self-tests (exit 0 on pass)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        from selftest import run
        return run()
    if args.budget:
        return run_budget(Path(args.work))
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    if args.phase == "finalize":
        return run_finalize(output_dir, work_dir)
    return run_pipeline(input_dir, output_dir, work_dir, args.style)


if __name__ == "__main__":
    raise SystemExit(main())
