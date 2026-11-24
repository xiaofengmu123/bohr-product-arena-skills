"""Entry point for the citation-management Skill.

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Behavior contract (important for the 900 s / 64-request harness budget):
1. Immediately create /app/output and write a valid result.json skeleton that
   echoes every output_contract key, so any later interruption still leaves a
   legitimate partial deliverable.
2. Run the deterministic pipeline in core_impl and write editable artifacts.
3. Always exit 0 with an honest status; never fabricate data or scores.

Semantic work (claim-level citation placement and source verification) is done
by the Agent following SKILL.md; this script supplies the deterministic
backbone and never invents those judgments.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "artifacts", "runtime",
]


def _extract_case_id(case: Dict[str, Any]) -> str:
    if not isinstance(case, dict):
        return ""
    for key in ("case_id", "id", "challenge_id", "armiId", "armi_id"):
        v = case.get(key)
        if v:
            return str(v)
    return ""


def build_skeleton(case: Dict[str, Any], ledger: "ce.Ledger") -> Dict[str, Any]:
    """Echo every output_contract key as null/[] plus standard fields."""
    case_id = _extract_case_id(case)
    contract: Dict[str, Any] = {}
    if isinstance(case, dict):
        raw = case.get("output_contract", case.get("outputContract", {}))
        if isinstance(raw, dict):
            contract = {k: None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {k: None for k in raw if isinstance(k, str)}
    skeleton: Dict[str, Any] = {"case_id": case_id, **contract}
    for field in RESULT_FIELDS:
        skeleton.setdefault(field, None)
    skeleton["status"] = "partial"
    skeleton["sources"] = list(ledger.sources)
    skeleton["uncertainty"] = []
    skeleton["change_log"] = []
    skeleton["errors"] = list(ledger.errors)
    skeleton["degradations"] = list(ledger.degradations)
    skeleton["pending_human"] = []
    skeleton["artifacts"] = {}
    skeleton["runtime"] = {
        "seconds": 0.0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return skeleton


def _artifact_paths(out_dir: Path) -> Dict[str, str]:
    return {
        "manuscript": str(out_dir / "manuscript.md"),
        "references": str(out_dir / "references.md"),
        "evidence_map": str(out_dir / "evidence_map.json"),
        "consistency_report": str(out_dir / "consistency_report.json"),
        "ir": str(out_dir / "citation_graph.json"),
    }


def _backfill_contract(result: Dict[str, Any], artifacts: Dict[str, str]) -> None:
    """Fill output_contract keys whose names clearly match a produced artifact.

    Unknown keys stay null — never renamed, never dropped.
    """
    hints = {
        "manuscript": artifacts["manuscript"],
        "reference": artifacts["references"],
        "bibliography": artifacts["references"],
        "evidence": artifacts["evidence_map"],
        "consistency": artifacts["consistency_report"],
        "graph": artifacts["ir"],
        "citation_graph": artifacts["ir"],
    }
    for key in list(result.keys()):
        if key in RESULT_FIELDS or key == "case_id":
            continue
        low = key.lower()
        for token, path in hints.items():
            if token in low:
                result[key] = path
                break


def _status(state: Dict[str, Any]) -> str:
    ledger = state["ledger"]
    if ledger.errors and not state["entries"] and not state["manuscript_text"].strip():
        return "input_error"
    if not state["entries"] and not state["manuscript_text"].strip():
        return "insufficient_evidence"
    # semantic verification always remains for the Agent -> honest partial
    return "partial"


def build_final_result(skeleton: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    ledger: ce.Ledger = state["ledger"]
    out_dir: Path = state["out_dir"]
    artifacts = _artifact_paths(out_dir)
    result = dict(skeleton)
    result["case_id"] = _extract_case_id(state["case"]) or result.get("case_id", "")
    result["status"] = _status(state)
    result["sources"] = list(ledger.sources)
    result["errors"] = list(ledger.errors)
    result["degradations"] = list(ledger.degradations)
    result["uncertainty"] = [
        "Semantic claim-level matching and source verification are pending Agent confirmation.",
        "metadata not confirmed against external databases (no network guarantee).",
    ]
    result["pending_human"] = _pending_human(state)
    result["change_log"] = [
        {
            "step": "skeleton",
            "detail": "result.json skeleton written before pipeline (partial-first).",
        },
        {
            "step": "pipeline",
            "detail": "parsed/normalized/deduped/renumbered/checked deterministically.",
        },
        {
            "step": "artifacts",
            "detail": "manuscript.md, references.md, evidence_map.json, consistency_report.json written.",
        },
    ]
    result["artifacts"] = artifacts
    _backfill_contract(result, artifacts)
    result["runtime"] = {
        "seconds": round(state["runtime"], 3),
        "started_at": skeleton.get("runtime", {}).get("started_at", ""),
    }
    return result


def _pending_human(state: Dict[str, Any]) -> List[str]:
    pending: List[str] = []
    if state["consistency"]["unresolved_citations"]:
        pending.append(
            "unresolved citation numbers beyond library size: "
            + ", ".join(str(n) for n in state["consistency"]["unresolved_citations"])
        )
    if not state["entries"]:
        pending.append("no library entries parsed; provide a .bib/.ris/.enw/.json/.csv library.")
    if not state["manuscript_text"].strip():
        pending.append("no readable manuscript text; provide a .md/.txt/.tex file.")
    pending.append(
        "confirm claim->reference match and fill evidence_map.json match_basis/status fields."
    )
    return pending


def _write_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    ce.write_text_atomic(out_dir / "manuscript.md", state["processed_manuscript"])
    ce.write_text_atomic(out_dir / "references.md", state["reference_list"])
    ce.write_json_atomic(out_dir / "evidence_map.json", state["evidence_map"])
    ce.write_json_atomic(out_dir / "consistency_report.json", state["consistency"])
    ce.write_json_atomic(out_dir / "citation_graph.json", state["ir"])


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # ---- partial-first: write a valid skeleton immediately ----
    probe = ce.Ledger()
    case, _task = ce._find_case_and_task(input_dir, probe)
    skeleton = build_skeleton(case, probe)
    ce.write_json_atomic(out_dir / "result.json", skeleton)

    # ---- deterministic pipeline ----
    state = ce.run_pipeline(input_dir, out_dir, style=args.style)
    state["out_dir"] = out_dir

    _write_artifacts(state, out_dir)

    final = build_final_result(skeleton, state)
    ce.write_json_atomic(out_dir / "result.json", final)
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="Deterministic reference & citation management backbone.",
    )
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--style", default="gbt7714", choices=["gbt7714", "apa"])
    p.add_argument("--self-test", action="store_true", help="run in-memory self-tests")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        import selftest

        return 0 if selftest.run_all() else 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
