"""Entry point for the citation/reference management skill.

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--init] [--self-test]

Behavior contract (designed for the 900 s / 64-request harness budget):
1. Immediately create the output directory and write a *valid* result.json
   skeleton plus placeholder artifacts, so any interruption still leaves a
   complete, honest partial deliverable.
2. Run the deterministic pipeline: discover materials, parse the library,
   extract claims from the manuscript, propose deduplication, build the
   intermediate representation (citation_model.json), merge the agent's
   semantic decisions (agent_edits.json), renumber, render editable artifacts,
   and run a consistency check.
3. Always exit 0 with an honest status; never fabricate data, DOIs, or scores.

The semantic work (claim->reference matching, source verification, merge
confirmation) is performed by the Agent following SKILL.md; this script
supplies the deterministic backbone and never invents those judgments.
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

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime",
]

MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex"}
LIBRARY_EXTS = {".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".xlsx"}


def _case_id_of(case):
    for key in ("case_id", "id", "challenge_id", "armiId", "benchmarkId"):
        v = case.get(key)
        if v:
            return str(v)
    return ""


def build_skeleton(case, ledger):
    """Echo every output_contract key (null/[]) plus the standard fields."""
    case_id = _case_id_of(case) if isinstance(case, dict) else ""
    contract = {}
    if isinstance(case, dict):
        raw = case.get("output_contract") or case.get("outputContract") or {}
        if isinstance(raw, dict):
            contract = {k: None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {k: None for k in raw if isinstance(k, str)}
    skeleton = {**contract, "case_id": case_id}
    for field in RESULT_FIELDS:
        skeleton.setdefault(field, None)
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["change_log"] = []
    skeleton["errors"] = []
    skeleton["degradations"] = []
    skeleton["pending_human"] = []
    skeleton["artifacts"] = {}
    skeleton["runtime"] = {"seconds": 0.0}
    return skeleton


def discover(input_dir, ledger):
    input_dir = Path(input_dir)
    materials = []
    if not input_dir.exists():
        ledger.error(f"input dir not found: {input_dir}")
        return materials
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        rel = str(p.relative_to(input_dir))
        if p.name in ("case.json", "task.md"):
            role = p.name
        elif ext in LIBRARY_EXTS:
            role = "library"
        elif ext in MANUSCRIPT_EXTS:
            role = "manuscript"
        else:
            role = "other"
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        materials.append({"path": str(p), "rel": rel, "name": p.name,
                          "ext": ext, "role": role, "size": size})
    return materials


def _placeholder(kind):
    return f"<!-- placeholder: {kind} not yet produced; rerun after semantic phase -->\n"


def _pick_manuscript(materials):
    for m in materials:
        if m["role"] == "manuscript":
            return Path(m["path"])
    return None


def _pick_library_files(materials):
    return [Path(m["path"]) for m in materials if m["role"] == "library"]


def run_pipeline(args, ledger, init_only=False):
    t0 = time.time()
    case = {}
    task_text = ""
    case_path = Path(args.input) / "case.json"
    if case_path.exists():
        case = ce.load_json_safe(case_path, ledger) or {}
    task_path = Path(args.input) / "task.md"
    if task_path.exists():
        task_text = ce.read_text_safe(task_path, ledger)

    materials = discover(args.input, ledger)

    style = args.style if args.style in ce.STYLES else ce.DEFAULT_STYLE

    # ---- build skeleton immediately (first-3-minutes guarantee) ----
    result = build_skeleton(case, ledger)
    work_dir = Path(args.work)
    out_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = {
        "schema_version": ce.IR_SCHEMA_VERSION,
        "case_id": result["case_id"],
        "target_style": style,
        "materials": materials,
        "budget": {"seconds_total": 900, "requests_total": 64,
                   "seconds_elapsed": 0.0, "requests_used": 0},
        "phase": "bootstrap",
        "checkpoint": "skeleton written at start; incremental improvement only",
    }
    ce.write_json_atomic(work_dir / "plan.json", plan)

    # Land a *valid* partial result.json right now, before any heavy parsing,
    # so an interruption can never leave an empty output directory.
    result["sources"] = [m["rel"] for m in materials
                         if m["role"] in ("manuscript", "library", "case.json", "task.md")]
    result["errors"] = list(ledger.errors)
    result["degradations"] = list(ledger.degradations)
    result["runtime"] = {"seconds": round(time.time() - t0, 3), "phase": "bootstrap"}
    ce.write_json_atomic(out_dir / "result.json", result)

    if init_only:
        # Fast first checkpoint (the documented --init command).
        for name, content in (("manuscript.md", _placeholder("manuscript")),
                              ("references.md", _placeholder("references"))):
            ce.write_text_atomic(out_dir / name, content)
        ce.write_json_atomic(out_dir / "evidence_map.json", [])
        ce.write_json_atomic(out_dir / "consistency_report.json",
                             {"issue_count": -1, "clean": False,
                              "note": "bootstrap placeholder; rerun full pipeline"})
        ce.write_json_atomic(out_dir / "citation_model.json",
                             {"schema_version": ce.IR_SCHEMA_VERSION,
                              "case_id": result["case_id"], "bootstrap": True})
        result["artifacts"] = {
            "manuscript": str(out_dir / "manuscript.md"),
            "references": str(out_dir / "references.md"),
            "evidence_map": str(out_dir / "evidence_map.json"),
            "consistency_report": str(out_dir / "consistency_report.json"),
            "citation_model": str(out_dir / "citation_model.json"),
        }
        ce.write_json_atomic(out_dir / "result.json", result)
        return result

    manuscript_path = _pick_manuscript(materials)
    library_paths = _pick_library_files(materials)

    manuscript_text = ce.read_text_safe(manuscript_path, ledger) if manuscript_path else ""
    entries = ce.parse_library_files(library_paths, ledger) if library_paths else []

    # ---- intermediate representation: mechanical extraction ----
    claims = ce.extract_claims(manuscript_text) if manuscript_text else []
    refs = ce.assign_ref_ids(entries)
    model = {
        "schema_version": ce.IR_SCHEMA_VERSION,
        "case_id": result["case_id"],
        "target_style": style,
        "materials": materials,
        "manuscript_path": str(manuscript_path) if manuscript_path else "",
        "claims": claims,
        "references": refs,
        "citations": [],
        "evidence": [],
        "merge_proposals": ce.deduplicate(entries)[1] if entries else [],
        "numbering": {},
        "conflicts": [],
        "pending": [],
        "semantic_verified": False,
        "agent_notes": "",
    }
    ce.apply_merge_proposals(model)

    # ---- merge agent decisions if present ----
    agent_edits_path = work_dir / "agent_edits.json"
    agent_edits = ce.load_json_safe(agent_edits_path, ledger) if agent_edits_path.exists() else None
    if agent_edits:
        model = ce.merge_agent_edits(model, agent_edits)

    # ---- resolve any citation still pointing at a merged (duplicate) entry ----
    model = ce.resolve_duplicate_citations(model)

    # ---- numbering: order of first citation ----
    claim_order = [c["id"] for c in claims]
    model["numbering"] = ce.compute_numbering(model["citations"], claim_order)

    # ---- citations-by-claim map for rendering ----
    citations_by_claim = {}
    for cit in model["citations"]:
        citations_by_claim.setdefault(cit["claim_id"], []).extend(cit.get("ref_ids", []))

    # ---- render artifacts ----
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    manuscript_out = ce.render_manuscript(manuscript_text, claims, model["numbering"],
                                          citations_by_claim, style) if manuscript_text else _placeholder("manuscript")
    ce.write_text_atomic(out_dir / "manuscript.md", manuscript_out)
    artifacts["manuscript"] = str(out_dir / "manuscript.md")

    refs_out = ce.render_reference_list(model["references"], model["numbering"], style) if refs else _placeholder("references")
    ce.write_text_atomic(out_dir / "references.md", refs_out)
    artifacts["references"] = str(out_dir / "references.md")

    ce.write_json_atomic(out_dir / "evidence_map.json", model.get("evidence", []))
    artifacts["evidence_map"] = str(out_dir / "evidence_map.json")

    consistency = ce.run_consistency(model)
    model["consistency"] = consistency
    ce.write_json_atomic(out_dir / "consistency_report.json", consistency)
    artifacts["consistency_report"] = str(out_dir / "consistency_report.json")

    # ---- intermediate representation snapshots (reviewability) ----
    ce.write_json_atomic(work_dir / "citation_model.json", model)
    ce.write_json_atomic(out_dir / "citation_model.json", model)
    artifacts["citation_model"] = str(out_dir / "citation_model.json")

    # ---- honest status ----
    status = _derive_status(model, manuscript_text, entries, consistency)
    result["status"] = status
    result["artifacts"] = artifacts
    result["sources"] = [m["rel"] for m in materials if m["role"] in ("manuscript", "library", "case.json", "task.md")]
    result["errors"] = list(ledger.errors)
    result["degradations"] = list(ledger.degradations)
    result["uncertainty"] = _uncertainty_list(model, consistency)
    result["pending_human"] = _pending_list(model, consistency, manuscript_text, entries)
    result["change_log"] = _change_log(model, entries)
    result["runtime"] = {"seconds": round(time.time() - t0, 3),
                         "style": style,
                         "materials": len(materials),
                         "references": len(refs),
                         "claims": len(claims)}

    # backfill output_contract keys with real artifact paths where sensible
    _backfill_contract(result, artifacts, model)

    ce.write_json_atomic(out_dir / "result.json", result)
    return result


def _derive_status(model, manuscript_text, entries, consistency):
    if not manuscript_text and not entries:
        return "insufficient_evidence"
    if not manuscript_text or not entries:
        return "partial"
    if model.get("semantic_verified") and consistency.get("clean"):
        return "ok"
    return "partial"


def _uncertainty_list(model, consistency):
    u = []
    if consistency.get("unresolved_citations"):
        u.append(f"unresolved_citations={len(consistency['unresolved_citations'])}")
    if consistency.get("pending_claims"):
        u.append(f"pending_claims={len(consistency['pending_claims'])}")
    if consistency.get("uncited_references"):
        u.append(f"uncited_references={len(consistency['uncited_references'])}")
    if consistency.get("unconfirmed_merges"):
        u.append(f"unconfirmed_merges={len(consistency['unconfirmed_merges'])}")
    if not model.get("semantic_verified"):
        u.append("semantic_verification_not_declared")
    return u


def _pending_list(model, consistency, manuscript_text, entries):
    p = []
    if not manuscript_text:
        p.append("manuscript_missing")
    if not entries:
        p.append("library_missing")
    for cid in consistency.get("pending_claims", []):
        p.append(f"claim_{cid}_needs_semantic_decision")
    for item in consistency.get("unresolved_citations", []):
        p.append(f"citation_ref_unknown_{item.get('ref_id')}")
    for item in consistency.get("unconfirmed_merges", []):
        p.append(f"merge_candidate_{item.get('id')}_needs_confirmation")
    return p


def _change_log(model, entries):
    log = []
    log.append("bootstrap: result skeleton + IR written by scripts/main.py")
    if entries:
        log.append(f"library_parsed: {len(entries)} raw entries")
    if model.get("merge_proposals"):
        log.append(f"dedup_proposed: {len(model['merge_proposals'])} groups")
    if model.get("citations"):
        log.append(f"citations_recorded: {len(model['citations'])}")
    if model.get("semantic_verified"):
        log.append("semantic_verified: true")
    return log


def _backfill_contract(result, artifacts, model):
    """Fill known contract keys with real artifact paths; keep unknown keys null."""
    mapping = {
        "manuscript": artifacts.get("manuscript"),
        "manuscript_path": artifacts.get("manuscript"),
        "references": artifacts.get("references"),
        "reference_list": artifacts.get("references"),
        "references_path": artifacts.get("references"),
        "evidence_map": artifacts.get("evidence_map"),
        "evidence": artifacts.get("evidence_map"),
        "consistency_report": artifacts.get("consistency_report"),
        "citation_model": artifacts.get("citation_model"),
        "case_id": result.get("case_id"),
        "status": result.get("status"),
    }
    for k in list(result.keys()):
        if k in mapping and result.get(k) is None:
            result[k] = mapping[k]


def run_selftest(ledger):
    """In-memory self test; returns True on success."""
    bib = """@article{smith2020,
  author = {Smith, John and Doe, Jane},
  title = {A Test of Citation Management},
  journal = {J. Test. Res.},
  year = {2020},
  volume = {12},
  number = {3},
  pages = {100-110},
  doi = {10.1000/xyz123}
}"""
    entries = ce.parse_bibtex(bib)
    assert len(entries) == 1, "bibtex parse"
    norm = ce.normalize_entry(entries[0])
    assert norm["fields"]["doi"] == "10.1000/xyz123", "doi normalize"
    assert norm["fields"]["_entry_type"] == "article", "entry type"

    claims = ce.extract_claims("First result. Second result [1, 2]. Third result.")
    assert len(claims) >= 3, "claim extraction"
    assert claims[1]["markers"], "marker extraction"

    refs = ce.assign_ref_ids([norm])
    assert refs[0]["id"] == "r001", "ref id"
    num = ce.compute_numbering([{"claim_id": "c002", "ref_ids": ["r001"]}], ["c001", "c002"])
    assert num["id_to_number"]["r001"] == 1, "numbering"
    assert ce.compress_numbers([1, 2, 3]) == "1-3", "compress range"
    assert ce.compress_numbers([1, 3]) == "1,3", "compress non-contiguous"

    model = {"claims": [], "references": refs, "citations": [], "numbering": num,
             "evidence": []}
    cons = ce.run_consistency(model)
    assert "unresolved_citations" in cons, "consistency keys"
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Citation & reference management skill (deterministic backbone).")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--style", default=ce.DEFAULT_STYLE, choices=sorted(ce.STYLES))
    ap.add_argument("--init", action="store_true",
                    help="Bootstrap skeleton only (fast first checkpoint).")
    ap.add_argument("--self-test", action="store_true",
                    help="Run in-memory self tests and exit non-zero on failure.")
    args = ap.parse_args(argv)

    ledger = ce.Ledger()
    if args.self_test:
        try:
            ok = run_selftest(ledger)
        except Exception as exc:  # noqa: BLE001
            print(f"self-test FAILED: {exc}")
            return 1
        print("self-test PASSED")
        return 0 if ok else 1

    try:
        run_pipeline(args, ledger, init_only=args.init)
    except Exception as exc:  # noqa: BLE001
        # Never leave an empty output dir: write a minimal honest result.
        ledger.error(f"pipeline failed: {exc}")
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = build_skeleton({}, ledger)
        result["status"] = "input_error"
        result["errors"] = list(ledger.errors)
        result["degradations"] = list(ledger.degradations)
        result["runtime"] = {"seconds": 0.0}
        ce.write_json_atomic(out_dir / "result.json", result)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
