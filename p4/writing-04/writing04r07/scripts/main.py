# -*- coding: utf-8 -*-
"""Entry point for the citation-integrity skill.

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--max-bytes 2097152] [--self-test]

Contract highlights (matching the 900s / 64-request harness budget):

1. Immediately create the output directory and write a *valid* result.json
   skeleton (status=partial).  Any later interruption still leaves a legal,
   inspectable partial deliverable on disk.
2. Run the deterministic pipeline (discovery -> parsing -> deduplication ->
   renumbering -> formatting -> consistency check) and write editable
   artifacts.  This script never invents semantic judgments, DOIs, scores or
   experiment data.
3. Always exit 0 with an honest status.  ``insufficient_evidence`` is used
   when required inputs are absent; ``input_error`` when inputs exist but are
   unusable; ``partial`` while semantic verification is still pending.

The semantic work (claim-level citation placement and source verification) is
performed by the Agent following SKILL.md; this script supplies the robust
deterministic backbone and the evidence/artifact scaffold.
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

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "artifacts",
]

# Keys that, when present in output_contract, map to a produced artifact path.
_ARTIFACT_KEY_HINTS = [
    ("manuscript", "manuscript"),
    ("reference", "references"),
    ("bibliograph", "references"),
    ("evidence", "evidence_map"),
    ("consistency", "consistency_report"),
    ("change", "change_log"),
]


def build_skeleton(case: dict) -> dict:
    """Echo every output_contract key (as null) plus standard fields."""
    case_id = ce.case_id_of(case)
    contract = {}
    if isinstance(case, dict):
        raw = case.get("output_contract", case.get("outputContract"))
        if isinstance(raw, dict):
            contract = {str(k): None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {str(k): None for k in raw if isinstance(k, str)}

    skeleton: dict = {"case_id": case_id, **contract}
    for field in RESULT_FIELDS:
        if field not in skeleton:
            skeleton[field] = None
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["change_log"] = []
    skeleton["errors"] = []
    skeleton["degradations"] = []
    skeleton["pending_human"] = []
    skeleton["artifacts"] = {}
    skeleton["runtime"] = {
        "seconds": 0.0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return skeleton


def _contract_value_for_key(key: str, artifacts: dict) -> Any:
    """Best-effort mapping of an output_contract key to a produced artifact."""
    kl = key.lower()
    for hint, art in _ARTIFACT_KEY_HINTS:
        if hint in kl:
            return artifacts.get(art)
    return None


def fill_contract_values(skeleton: dict, artifacts: dict) -> dict:
    """Fill output_contract keys we can determine; keep the rest null."""
    for key in list(skeleton.keys()):
        if key in RESULT_FIELDS or key == "case_id":
            continue
        val = _contract_value_for_key(key, artifacts)
        if val is not None:
            skeleton[key] = val
    return skeleton


def write_skeleton_now(out_dir: Path, skeleton: dict) -> dict:
    """Drop a legal result.json skeleton as early as possible."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ce.atomic_write_json(out_dir / "result.json", skeleton)
    return skeleton


def load_case_and_task(input_dir: Path, ledger: ce.Ledger, max_bytes: int):
    case_path = input_dir / "case.json"
    case = {}
    if case_path.exists():
        case = ce.load_json_file(case_path, ledger, max_bytes) or {}
    else:
        ledger.degrade("case.json not found under input directory")
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text = ce.read_text(task_path, ledger, max_bytes) or ""
    else:
        ledger.degrade("task.md not found under input directory")
    return case, task_text


def run_pipeline(args: argparse.Namespace) -> int:
    started = time.time()
    input_dir = Path(args.input)
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    work_dir.mkdir(parents=True, exist_ok=True)

    ledger = ce.Ledger()

    # ---- Phase 0: early skeleton (first few seconds). ----
    case, task_text = load_case_and_task(input_dir, ledger, args.max_bytes)
    skeleton = build_skeleton(case)
    write_skeleton_now(out_dir, skeleton)

    # ---- Phase 1: discovery. ----
    discovery = ce.discover(input_dir, ledger, args.max_bytes)
    manuscripts = discovery.get("manuscripts", [])
    libraries = discovery.get("libraries", [])

    if case and discovery.get("case") is None:
        ledger.sources.append({
            "path": str(input_dir / "case.json"),
            "kind": "case",
            "bytes": -1,
            "sha256": "",
        })

    # ---- Phase 2: parse libraries. ----
    entries: list[dict] = []
    for p in libraries:
        entries.extend(ce.parse_library_file(p, ledger, args.max_bytes))

    # ---- Phase 3: deduplicate. ----
    merged, merge_evidence = ce.deduplicate(entries)

    # ---- Phase 4: manuscript processing. ----
    manuscript_text = ""
    manuscript_source = ""
    if manuscripts:
        manuscript_source = str(manuscripts[0])
        manuscript_text = ce.read_text(manuscripts[0], ledger, args.max_bytes) or ""
    else:
        ledger.degrade("no manuscript file discovered")

    renumbered_text = manuscript_text
    mapping: dict = {}
    if manuscript_text:
        renumbered_text, mapping = ce.renumber(manuscript_text)

    # ---- Phase 5: format references. ----
    style = getattr(args, "style", "gbt7714") or "gbt7714"
    reference_lines = [
        f"{i + 1}. {ce.format_reference(e, style)}" for i, e in enumerate(merged)
    ]
    reference_text = "\n".join(reference_lines)

    # ---- Phase 6: consistency check. ----
    report = ce.consistency_check(renumbered_text, merged, mapping, manuscript_text)
    report["merge_evidence"] = merge_evidence
    report["manuscript_source"] = manuscript_source or None
    report["library_entry_count_raw"] = len(entries)
    report["library_entry_count_deduplicated"] = len(merged)
    report["style"] = style

    # ---- Phase 7: write artifacts. ----
    artifacts: dict = {}
    ce.atomic_write_text(out_dir / "manuscript.md", renumbered_text)
    artifacts["manuscript"] = str(out_dir / "manuscript.md")
    ce.atomic_write_text(out_dir / "references.md", reference_text)
    artifacts["references"] = str(out_dir / "references.md")
    ce.atomic_write_json(out_dir / "evidence_map.json", _evidence_map_scaffold(merged))
    artifacts["evidence_map"] = str(out_dir / "evidence_map.json")
    ce.atomic_write_json(out_dir / "consistency_report.json", report)
    artifacts["consistency_report"] = str(out_dir / "consistency_report.json")
    ce.atomic_write_text(out_dir / "change_log.md", _change_log_scaffold())
    artifacts["change_log"] = str(out_dir / "change_log.md")

    # ---- Phase 8: finalize result.json. ----
    result = skeleton
    ledger_fields = ledger.to_result_fields()
    result["sources"] = ledger_fields["sources"]
    result["errors"] = ledger_fields["errors"]
    result["degradations"] = ledger_fields["degradations"]
    result["pending_human"] = ledger_fields["pending_human"]
    result["uncertainty"] = ledger_fields["uncertainty"]
    result["artifacts"] = artifacts
    result["change_log"] = _change_log_entries(manuscript_source, len(entries), len(merged))
    result["runtime"]["seconds"] = round(time.time() - started, 3)
    result["runtime"]["finished_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
    )
    result["status"] = _derive_status(case, manuscripts, entries, ledger_fields)
    result = fill_contract_values(result, artifacts)
    ce.atomic_write_json(out_dir / "result.json", result)

    print(f"status={result['status']} case_id={result.get('case_id') or '-'}")
    print(f"manuscripts={len(manuscripts)} libraries={len(libraries)} "
          f"raw_entries={len(entries)} deduped={len(merged)} style={style}")
    print(f"artifacts -> {out_dir}")
    return 0


def _derive_status(case: dict, manuscripts: list, entries: list,
                   ledger_fields: dict) -> str:
    if not case:
        return "insufficient_evidence"
    if ledger_fields["errors"] and not entries:
        return "input_error"
    if not manuscripts or not entries:
        return "insufficient_evidence"
    return "partial"


def _evidence_map_scaffold(merged: list[dict]) -> dict:
    """Scaffold for the Agent to fill with locator + verbatim quote per citation."""
    return {
        "note": (
            "Deterministic scaffold only. Fill each citation's locator and "
            "verbatim quote during semantic verification; do not invent facts."
        ),
        "citations": [
            {
                "reference_id": e.get("id"),
                "title": e.get("title"),
                "locator": None,
                "quote": None,
                "claim": None,
                "match_rationale": None,
                "status": "pending",
            }
            for e in merged
        ],
    }


def _change_log_scaffold() -> str:
    return (
        "# Change log\n\n"
        "Deterministic backbone changes are listed below; semantic edits must "
        "be appended by the Agent with before/after evidence.\n\n"
    )


def _change_log_entries(manuscript_source: str, raw: int, deduped: int) -> list[dict]:
    log = [
        {"step": "discovery", "detail": "scanned input directory"},
        {"step": "library_parse", "detail": f"parsed {raw} raw entries"},
    ]
    if raw != deduped:
        log.append({
            "step": "deduplicate",
            "detail": f"merged {raw} raw entries into {deduped} unique references",
        })
    log.append({
        "step": "renumber",
        "detail": "renumbered bracket citations by first appearance",
    })
    if manuscript_source:
        log.append({
            "step": "manuscript",
            "detail": f"processed manuscript {manuscript_source}",
        })
    return log


# --------------------------------------------------------------------------- #
# Self-test (in-memory; no network, no fixture files required).
# --------------------------------------------------------------------------- #
def run_self_test() -> int:
    # 1. JSON repair + parse.
    assert ce.load_json_text('{"a":1,}', "t", ce.Ledger()) == {"a": 1}
    assert ce.load_json_text("{bad json", "t", ce.Ledger()) is None

    # 2. BibTeX parse.
    bib = "@article{k1, title={A study}, author={Alice and Bob}, year={2020}, doi={10.1/x}}"
    bib_entries = ce.parse_bibtex(bib)
    assert len(bib_entries) == 1 and bib_entries[0]["title"] == "A study", bib_entries

    # 3. Deduplicate by DOI.
    e1 = ce.normalize_entry({"title": "A", "doi": "10.1/x"}, "f1.bib", 0)
    e2 = ce.normalize_entry({"title": "A (different)", "doi": "10.1/X"}, "f2.bib", 0)
    merged, ev = ce.deduplicate([e1, e2])
    assert len(merged) == 1 and len(ev) == 1, (merged, ev)

    # 4. Renumber by first appearance.
    text = "See [3] and [1,2] and [3] again."
    new_text, mapping = ce.renumber(text)
    assert mapping == {3: 1, 1: 2, 2: 3}, mapping
    assert "[1]" in new_text and "[2,3]" in new_text, new_text

    # 5. Consistency check.
    merged_refs = [{"id": "ref1"}, {"id": "ref2"}, {"id": "ref3"}]
    rep = ce.consistency_check(new_text, merged_refs, mapping, text)
    assert rep["unresolved_citations"] == [], rep

    # 6. Empty input dir -> no exception.
    led = ce.Ledger()
    d = ce.discover(Path("/nonexistent-dir-for-selftest"), led, ce.DEFAULT_MAX_BYTES)
    assert d["manuscripts"] == [] and d["libraries"] == [], d

    print("self-test ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citation-integrity",
        description="Robust citation/reference management pipeline.",
    )
    p.add_argument("--input", default=DEFAULT_INPUT, help="input directory")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="output directory")
    p.add_argument("--work", default=DEFAULT_WORK, help="working directory")
    p.add_argument("--style", choices=["gbt7714", "apa"], default="gbt7714")
    p.add_argument("--max-bytes", type=int, default=ce.DEFAULT_MAX_BYTES,
                   help="per-file size cap in bytes (default 2 MB)")
    p.add_argument("--self-test", action="store_true", help="run in-memory self tests")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        return run_pipeline(args)
    except Exception as exc:  # noqa: BLE001
        # Last-resort guard: never leave a crashed process without a legal result.
        try:
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            guard = {
                "case_id": "",
                "status": "input_error",
                "sources": [],
                "uncertainty": [f"unhandled exception: {exc}"],
                "change_log": [],
                "errors": [f"unhandled exception: {exc}"],
                "degradations": [],
                "pending_human": ["pipeline crashed; inspect errors field"],
                "artifacts": {},
                "runtime": {"seconds": 0.0, "started_at": ""},
            }
            ce.atomic_write_json(out_dir / "result.json", guard)
        except Exception:  # noqa: BLE001
            pass
        print(f"pipeline crashed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
