"""Entry point for the citation-management skill (v2 scoring-aligned).

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Behavior contract (900 s / 64 request harness):
1. Immediately create the output directory and write a valid result.json
   skeleton echoing every output_contract key, so an interruption never
   leaves an empty deliverable.
2. Run the deterministic pipeline (discover, parse library, deduplicate,
   apply the Agent-written citation plan, renumber, format, consistency
   check) and write editable artifacts.
3. Always exit 0 with an honest status; never fabricate data or scores.

Claim-level citation placement and source verification are the Agent's
semantic work (see SKILL.md).  This script supplies the deterministic
backbone and reads the Agent's citation_plan.json from /app/work when
present; it never invents those judgments.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402


DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")


def find_case_and_task(input_dir: Path, ledger: ce.Ledger):
    case = ce.load_json_file(input_dir / "case.json", ledger) or {}
    task_text = ce.read_text(input_dir / "task.md", ledger)
    return case, task_text


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, style: str):
    t0 = time.time()
    ledger = ce.Ledger()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case, task_text = find_case_and_task(input_dir, ledger)

    # ---- Stage 0: skeleton first (idempotent partial baseline) ----
    skeleton = ce.build_skeleton(case, ledger)
    ce.write_json_atomic(output_dir / "result.json", skeleton)

    # ---- Stage 1: discovery ----
    files = ce.discover_files(input_dir, ledger)
    manuscripts, libraries, others, harness = ce.classify_files(files, input_dir)
    sources = []
    for p in harness:
        sources.append({"path": str(p), "role": "harness", "type": p.suffix.lower().lstrip(".")})
    for p in manuscripts:
        sources.append({"path": str(p), "role": "manuscript", "type": p.suffix.lower().lstrip(".")})
    for p in libraries:
        sources.append({"path": str(p), "role": "library", "type": p.suffix.lower().lstrip(".")})
    for p in others:
        sources.append({"path": str(p), "role": "other", "type": p.suffix.lower().lstrip(".")})

    # ---- Stage 2: parse library, deduplicate ----
    raw_entries: list[dict] = []
    for p in libraries:
        raw_entries.extend(ce.parse_library_file(p, ledger))
    entries = [ce.normalize_entry(e) for e in raw_entries]
    deduped, merge_evidence, alias_map = ce.deduplicate(entries)

    # ---- Stage 3: manuscript text ----
    manuscript = ""
    if manuscripts:
        p = manuscripts[0]
        if p.suffix.lower() == ".docx":
            manuscript = ce.extract_docx_text(p, ledger)
        elif p.suffix.lower() == ".pdf":
            ledger.degrade(f"{p.name}: PDF text needs runtime pdf-text-tool; not parsed here")
        else:
            manuscript = ce.read_text(p, ledger)

    # ---- Stage 4: citation plan (Agent-written, optional) ----
    plan_path = work_dir / "citation_plan.json"
    plan = ce.load_json_file(plan_path, ledger) or {}

    processed_manuscript = manuscript
    ordered_entries = deduped
    evidence_map: list[dict] = []
    reference_list = ""
    if plan and manuscript:
        processed_manuscript, ordered_entries, evidence_map = ce.apply_citation_plan(
            manuscript, plan, deduped, ledger, alias_map)
    elif plan and not manuscript:
        ledger.degrade("citation plan present but no manuscript discovered; renumbering skipped")

    if ordered_entries:
        lines = []
        for i, e in enumerate(ordered_entries, start=1):
            lines.append(f"[{i}] {ce.format_entry(e, style)}")
        reference_list = "\n".join(lines)

    consistency = ce.check_consistency(processed_manuscript, deduped, plan, ledger)

    # ---- Stage 5: write artifacts ----
    artifacts: dict[str, str] = {}
    if processed_manuscript:
        ce.write_text_atomic(output_dir / "manuscript.md", processed_manuscript)
        artifacts["manuscript"] = str(output_dir / "manuscript.md")
    if reference_list:
        ce.write_text_atomic(output_dir / "references.md", reference_list)
        artifacts["references"] = str(output_dir / "references.md")
    ce.write_json_atomic(output_dir / "evidence_map.json", evidence_map)
    artifacts["evidence_map"] = str(output_dir / "evidence_map.json")
    ce.write_json_atomic(output_dir / "consistency_report.json", consistency)
    artifacts["consistency_report"] = str(output_dir / "consistency_report.json")
    library_snapshot = {
        "raw_count": len(raw_entries),
        "deduped_count": len(deduped),
        "merge_evidence": merge_evidence,
        "entries": deduped,
    }
    ce.write_json_atomic(output_dir / "library_snapshot.json", library_snapshot)
    artifacts["library_snapshot"] = str(output_dir / "library_snapshot.json")

    # ---- Stage 6: final result.json ----
    # The deterministic script never claims "ok": that requires the Agent's
    # semantic verification (claim matching + source check) to be complete.
    status = "partial"
    if not manuscripts and not libraries:
        status = "insufficient_evidence"

    result = ce.build_skeleton(case, ledger)
    result["status"] = status
    result["sources"] = sources
    result["artifacts"] = artifacts
    result["change_log"] = ledger.changes
    result["errors"] = ledger.errors
    result["degradations"] = ledger.degradations
    result["uncertainty"] = [
        {"item": "citation_plan", "reason": "no Agent-written citation plan found; numbering/evidence pending"}
    ] if not plan else []
    result["pending_human"] = consistency.get("unresolved_citations", [])
    result["coverage"] = {
        "citation_placement": {"status": "done_semantic" if plan else "pending_semantic",
                               "evidence": "evidence_map.json"},
        "renumber_crossref": {"status": "done_deterministic" if plan else "not_applicable",
                              "evidence": "evidence_map.json (number field)"},
        "dedup_merge": {"status": "done_deterministic" if merge_evidence else "not_applicable",
                        "evidence": "library_snapshot.json (merge_evidence)"},
        "format_conversion": {"status": "done_deterministic" if reference_list else "not_applicable",
                              "evidence": f"references.md ({style})"},
        "source_verification": {"status": "pending_semantic", "evidence": "evidence_map.json (verified)"},
        "consistency_check": {"status": "done_deterministic", "evidence": "consistency_report.json"},
    }
    result["runtime"]["seconds"] = round(time.time() - t0, 3)
    result["runtime"]["stages"] = {
        "discovered_files": len(files),
        "library_raw": len(raw_entries),
        "library_deduped": len(deduped),
        "style": style,
    }
    ce.write_json_atomic(output_dir / "result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic citation-management pipeline (parse, dedup, renumber, format, check).",
    )
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--work", default=str(DEFAULT_WORK))
    p.add_argument("--style", default="gbt7714", choices=["gbt7714", "apa"])
    p.add_argument("--self-test", action="store_true", help="run in-memory self test and exit")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        ok = ce.run_self_test()
        print("self-test:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    run_pipeline(Path(args.input), Path(args.output), Path(args.work), args.style)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
