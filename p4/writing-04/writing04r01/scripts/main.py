"""Entry point for the reference & citation management skill.

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Behavior contract (important for the 900 s / 64-request harness budget):
1. Immediately create /app/output and write a valid result.json skeleton with
   every output_contract key echoed (unknown values -> null), before analysis.
2. Run the deterministic pipeline (discover inputs, parse library, deduplicate,
   extract claims, renumber, format, consistency-check) and write editable
   artifacts. These steps are idempotent and never fabricate judgments.
3. Always exit 0 with an honest status.

The semantic work (claim-level match verification, evidence rationale) is done
by the Agent following SKILL.md and recorded into the evidence map; this script
pre-fills every locator and verbatim quote that can be derived mechanically.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import citation_engine as ce  # noqa: E402

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log", "evidence_map_path",
    "errors", "degradations", "pending_human", "runtime",
]


def build_skeleton(case: dict, ledger: dict) -> dict:
    """Echo every output_contract key as null plus standard fields.

    Never renames or drops unknown keys: this is the single authority for the
    output contract the evaluator supplies.
    """
    case_id = ""
    if isinstance(case, dict):
        case_id = str(
            case.get("case_id") or case.get("id") or case.get("challenge_id")
            or case.get("armiId") or case.get("armi_id") or ""
        )
    contract = {}
    if isinstance(case, dict):
        raw = case.get("output_contract", case.get("outputContract", {}))
        if isinstance(raw, dict):
            contract = {k: None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {k: None for k in raw if isinstance(k, str)}
    if not contract:
        contract = {
            "manuscript": None,
            "references": None,
            "evidence_map": None,
            "consistency_report": None,
            "change_log": None,
        }
    skeleton = {"case_id": case_id, **contract}
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
    skeleton["evidence_map_path"] = None
    skeleton["runtime"] = {
        "seconds": 0.0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return skeleton


def find_case_and_task(input_dir: Path, ledger: dict):
    case_path = input_dir / "case.json"
    case = {}
    if case_path.exists():
        case = ce.load_json_file(case_path, ledger) or {}
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text = ce.read_text_bytes(task_path, ledger)
    return case, task_text


MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}
LIBRARY_EXTS = {".bib", ".ris", ".enw", ".csv", ".tsv", ".xlsx"}


def classify_files(paths: list) -> tuple:
    manuscript, library, others = [], [], []
    for p in paths:
        ext = p.suffix.lower()
        if ext in LIBRARY_EXTS:
            library.append(p)
        elif ext == ".json":
            library.append(p)
            others.append(p)
        elif ext in MANUSCRIPT_EXTS:
            manuscript.append(p)
        else:
            others.append(p)
    return manuscript, library, others


def discover_inputs(input_dir: Path, ledger: dict) -> dict:
    """Recursively scan the input tree without assuming fixed file names."""
    paths: list = []
    if input_dir.exists():
        for p in input_dir.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                paths.append(p)
    manuscript, library, others = classify_files(paths)
    return {
        "all": paths,
        "manuscript": manuscript,
        "library": library,
        "others": others,
    }


def parse_library(files: list, ledger: dict) -> list:
    entries = []
    for p in files:
        text = ce.read_text_bytes(p, ledger)
        if not text:
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = ce.parse_bibtex(text)
            elif ext == ".ris":
                parsed = ce.parse_ris(text)
            elif ext == ".enw":
                parsed = ce.parse_bibtex(text)
            elif ext == ".json":
                data = ce.load_json_text(text, p.name, ledger)
                parsed = ce.parse_reference_json(data) if data is not None else []
            elif ext in {".csv", ".tsv", ".xlsx"}:
                if ext == ".xlsx":
                    ce._record(ledger, "degradations",
                               "xlsx not parsed without openpyxl; use CSV export: %s" % p.name)
                    parsed = []
                else:
                    parsed = ce.parse_table(text, ledger)
            else:
                parsed = []
        except Exception as exc:  # noqa: BLE001
            ce._record(ledger, "errors", "%s: library parse failed (%s)" % (p.name, exc))
            parsed = []
        entries.extend(parsed)
    return entries


def read_manuscript(files: list, ledger: dict) -> dict:
    """Read the largest text manuscript; PDFs/DOCX need runtime tools, so they
    are recorded as degradations here rather than guessed."""
    best = {"path": None, "text": ""}
    for p in files:
        ext = p.suffix.lower()
        if ext == ".pdf":
            ce._record(ledger, "degradations",
                       "PDF needs runtime pdf-text-tool; not parsed here: %s" % p.name)
            continue
        if ext == ".docx":
            ce._record(ledger, "degradations",
                       "DOCX needs runtime office-renderer; not parsed here: %s" % p.name)
            continue
        text = ce.read_text_bytes(p, ledger)
        if len(text) > len(best["text"]):
            best = {"path": p, "text": text}
    return best


def write_json(path: Path, obj: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def placeholder(name: str) -> str:
    return "<!-- %s: not produced yet; rerun after semantic pass -->\n" % name


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, style: str) -> dict:
    """Execute the deterministic pipeline and return the result state dict."""
    started = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    ledger: dict = {"errors": [], "degradations": []}

    case, task_text = find_case_and_task(input_dir, ledger)
    skeleton = build_skeleton(case, ledger)
    discovered = discover_inputs(input_dir, ledger)

    library_entries = parse_library(discovered["library"], ledger)
    dedup_result = ce.deduplicate(library_entries)
    entries = dedup_result["entries"]
    merge_evidence = dedup_result["merge_evidence"]

    manuscript = read_manuscript(discovered["manuscript"], ledger)
    manuscript_text = manuscript["text"] or ""
    claims = ce.extract_claims(manuscript_text, manuscript["path"].name if manuscript["path"] else "manuscript")

    renumber_map = ce.build_renumber_map(manuscript_text)
    renumbered = ce.renumber_text(manuscript_text, renumber_map) if manuscript_text else ""
    consistency = ce.consistency_check(manuscript_text, entries, renumber_map)
    evidence_map = ce.build_evidence_map(claims, entries, renumber_map)

    manuscript_out = renumbered if renumbered else placeholder("manuscript")

    ref_lines = []
    for i, e in enumerate(entries, start=1):
        ref_lines.append("[%d] %s" % (i, ce.format_reference(e, style)))
    references_out = "\n".join(ref_lines) if ref_lines else placeholder("reference list")

    # Write artifacts.
    write_text(output_dir / "manuscript.md", manuscript_out)
    write_text(output_dir / "references.md", references_out)
    write_json(output_dir / "evidence_map.json", evidence_map)
    write_json(output_dir / "consistency_report.json", consistency)
    write_json(output_dir / "change_log.json", {
        "schema_version": "1.0",
        "entries": [],
        "note": "Deterministic pass produced this skeleton; semantic changes are appended by the Agent.",
    })
    write_json(output_dir / "merge_evidence.json", {"merge_evidence": merge_evidence})

    # Assemble result.json (echo every contract key with actual paths where known).
    result = dict(skeleton)
    contract_keys = set(skeleton.keys()) - set(RESULT_FIELDS) - {"case_id"}
    for k in contract_keys:
        if k in ("manuscript", "manuscript_path", "edited_manuscript"):
            result[k] = str(output_dir / "manuscript.md")
        elif k in ("references", "reference_list", "bibliography"):
            result[k] = str(output_dir / "references.md")
        elif k in ("evidence_map", "evidence"):
            result[k] = str(output_dir / "evidence_map.json")
        elif k in ("consistency_report", "consistency"):
            result[k] = str(output_dir / "consistency_report.json")
        elif k in ("change_log", "changelog"):
            result[k] = str(output_dir / "change_log.json")
        else:
            result[k] = result.get(k)

    result["sources"] = [
        {"kind": "input", "path": str(p)} for p in discovered["all"]
    ][:200]
    result["errors"] = ledger["errors"]
    result["degradations"] = ledger["degradations"]
    result["evidence_map_path"] = str(output_dir / "evidence_map.json")
    result["pending_human"] = _pending_human(entries, manuscript_text, consistency)
    result["status"] = _initial_status(entries, manuscript_text, ledger)
    result["runtime"]["seconds"] = round(time.time() - started, 3)
    result["runtime"].pop("_start", None)

    write_json(output_dir / "result.json", result)
    return result


def _pending_human(entries: list, manuscript_text: str, consistency: dict) -> list:
    pending = []
    if not entries:
        pending.append("no_library_entries_found")
    if not manuscript_text:
        pending.append("no_manuscript_text_found")
    if consistency.get("unresolved_citations"):
        pending.append("unresolved_citations:%s" % consistency["unresolved_citations"])
    if consistency.get("uncited_entries"):
        pending.append("uncited_entries:%s" % consistency["uncited_entries"])
    return pending


def _initial_status(entries: list, manuscript_text: str, ledger: dict) -> str:
    if ledger["errors"] and not entries and not manuscript_text:
        return "input_error"
    if not entries or not manuscript_text:
        return "insufficient_evidence"
    return "partial"


def run_self_test() -> int:
    import selftest
    return selftest.run_all()


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reference & citation management skill: deterministic backbone.",
    )
    parser.add_argument("--input", default="/input", help="input directory (default /input)")
    parser.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    parser.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    parser.add_argument("--style", choices=["gbt7714", "apa"], default="gbt7714", help="reference style")
    parser.add_argument("--self-test", action="store_true", help="run in-memory self-test and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    start = time.time()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)

    # The 3-minute iron rule: write a skeleton before anything heavy runs, so an
    # interruption never leaves an empty delivery directory.
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    case, _ = find_case_and_task(input_dir, {"errors": [], "degradations": []})
    skeleton = build_skeleton(case, {"errors": [], "degradations": []})
    skeleton["runtime"]["_start"] = start
    write_json(output_dir / "result.json", skeleton)
    write_text(output_dir / "manuscript.md", placeholder("manuscript"))
    write_text(output_dir / "references.md", placeholder("reference list"))
    write_json(output_dir / "evidence_map.json", {"schema_version": "2.0", "claims": []})
    write_json(output_dir / "consistency_report.json", {"note": "skeleton"})

    try:
        run_pipeline(input_dir, output_dir, work_dir, args.style)
    except Exception as exc:  # noqa: BLE001
        result = build_skeleton(case, {"errors": [], "degradations": []})
        result["status"] = "input_error"
        result["errors"] = ["pipeline failed: %s" % exc]
        result["runtime"]["seconds"] = round(time.time() - start, 3)
        write_json(output_dir / "result.json", result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
