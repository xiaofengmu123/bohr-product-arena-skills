"""Entry point for the citation-management skill (deterministic backbone).

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Behavior contract (900s / 64-request harness):
1. Immediately create the output directory and write a valid result.json
   skeleton that echoes every output_contract field and all standard fields.
2. Run the deterministic pipeline (parse library, deduplicate, scan
   citations, consistency-check, format) and write editable artifacts.
3. Exit 0 with an honest status; never fabricate data, DOIs, or scores.

Semantic citation placement and source verification are the Agent's job
(SKILL.md / references/citation_rules.md); this script supplies only the
deterministic parts and never invents those judgments.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402
import document_io as dio  # noqa: E402

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime",
    "artifacts", "contract_alignment",
]

LIBRARY_EXTS = {".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".xlsx"}
MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}


def build_skeleton(case: dict[str, Any], ledger: dio.Ledger) -> dict[str, Any]:
    """Echo every output_contract key as its inferred default plus standard fields."""
    case_id = ce.find_case_id(case)
    contract = ce.extract_contract(case)
    skeleton: dict[str, Any] = {"case_id": case_id}
    for key, spec in contract.items():
        skeleton[key] = ce._typed_default(spec)
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
    skeleton["contract_alignment"] = {"fields": {}, "_summary": {"total": 0, "filled": 0, "empty": 0, "empty_fields": []}}
    skeleton["runtime"] = {
        "seconds": 0.0,
        "started_at": dio.utc_now(),
    }
    return skeleton


def find_inputs(input_dir: Path) -> dict[str, list[Path]]:
    """Classify discovered files as manuscript / library / other candidates."""
    manuscript: list[Path] = []
    library: list[Path] = []
    others: list[Path] = []
    for p in dio.list_files_recursive(input_dir):
        ext = p.suffix.lower()
        if ext in LIBRARY_EXTS:
            library.append(p)
        if ext in MANUSCRIPT_EXTS:
            manuscript.append(p)
        elif ext not in LIBRARY_EXTS:
            others.append(p)
    return {"manuscript": manuscript, "library": library, "others": others}


def read_case_and_task(input_dir: Path, ledger: dio.Ledger) -> tuple[dict[str, Any], str]:
    case: dict[str, Any] = {}
    case_path = input_dir / "case.json"
    if case_path.exists():
        loaded = dio.load_json_file(case_path, ledger)
        if isinstance(loaded, dict):
            case = loaded
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text = dio.read_text(task_path, ledger)
    return case, task_text


def parse_library(files: list[Path], ledger: dio.Ledger) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for p in files:
        text = dio.read_text(p, ledger)
        if not text:
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = ce.parse_bibtex(text)
            elif ext == ".ris":
                parsed = ce.parse_ris(text)
            elif ext == ".enw":
                parsed = ce.parse_ris(text)
            elif ext == ".json":
                data = dio.load_json_text(text, p.name, ledger)
                parsed = ce.parse_reference_json(data) if data is not None else []
            elif ext in {".csv", ".tsv"}:
                parsed = _parse_table(text)
            elif ext == ".xlsx":
                ledger.degrade(f"{p.name}: xlsx needs an Office/runtime parser; skipped by deterministic stage")
                parsed = []
            else:
                parsed = []
        except Exception as exc:  # noqa: BLE001
            ledger.error(f"{p.name}: library parse failed ({exc})")
            parsed = []
        entries.extend(parsed)
    return entries


def _parse_table(text: str) -> list[dict[str, str]]:
    import csv
    import io

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:
        return []
    if not reader.fieldnames:
        return []
    out: list[dict[str, str]] = []
    for row in reader:
        out.append(ce._normalize_json_entry({k: v for k, v in row.items() if v is not None}))
    return out


def read_manuscript(files: list[Path], ledger: dio.Ledger) -> tuple[str, str]:
    if not files:
        return "", ""
    p = files[0]
    text = dio.read_text(p, ledger)
    if not text and len(files) > 1:
        for alt in files[1:]:
            text = dio.read_text(alt, ledger)
            if text:
                p = alt
                break
    return text, str(p)


def load_state(work_dir: Path, ledger: dio.Ledger) -> dict[str, Any]:
    state: dict[str, Any] = {"change_log": [], "evidence_map": {"evidence": []}, "status": None}
    p = work_dir / "state.json"
    if p.exists():
        data = dio.load_json_file(p, ledger)
        if isinstance(data, dict):
            for k in state:
                if k in data:
                    state[k] = data[k]
    return state


def save_state(work_dir: Path, state: dict[str, Any]) -> None:
    dio.ensure_dir(work_dir)
    dio.write_json_atomic(work_dir / "state.json", state)


def merge_change_log(log: list[dict[str, Any]], action: str, detail: str) -> list[dict[str, Any]]:
    entry = {
        "step": len(log) + 1,
        "time": dio.utc_now(),
        "action": action,
        "detail": detail,
    }
    out = [x for x in log if not (isinstance(x, dict) and x.get("detail") == detail and x.get("action") == action)]
    out.append(entry)
    return out


def _placeholder(kind: str) -> str:
    return (
        f"<!-- 占位符：材料不足，非真实{kind}。确定性骨架已落盘，语义内容由 Agent 补充。 -->\n"
    )


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, style: str) -> int:
    started = time.time()
    ledger = dio.Ledger()
    dio.ensure_dir(output_dir)
    dio.ensure_dir(work_dir)

    case, task_text = read_case_and_task(input_dir, ledger)
    contract = ce.extract_contract(case)
    case_id = ce.find_case_id(case)

    # ---- Step 1: legal skeleton immediately ----
    skeleton = build_skeleton(case, ledger)
    dio.write_json_atomic(output_dir / "result.json", skeleton)

    state = load_state(work_dir, ledger)

    # ---- Step 2: discover + parse ----
    found = find_inputs(input_dir)
    manuscript_text, manuscript_src = read_manuscript(found["manuscript"], ledger)
    entries = parse_library(found["library"], ledger)
    dedup = ce.deduplicate(entries)

    # ---- Step 3: deterministic transforms ----
    ref_lines = ce.format_references(dedup["entries"], style)
    consistency = ce.consistency_check(manuscript_text, dedup)

    # ---- Step 4: write editable artifacts ----
    dio.ensure_dir(output_dir)
    artifacts: dict[str, str] = {}

    if manuscript_text:
        manuscript_out = manuscript_text
    else:
        manuscript_out = _placeholder("稿件")
        ledger.degrade("manuscript missing; placeholder written")
    dio.write_text_atomic(output_dir / "manuscript.md", manuscript_out)
    artifacts["manuscript"] = str(output_dir / "manuscript.md")

    ref_body = "\n".join(f"[{i + 1}] {line}" for i, line in enumerate(ref_lines))
    ref_header = f"# 参考文献表（{style.upper()}）\n\n> 由确定性脚本生成；语义核验后请复核每条与正文编号对应。\n\n"
    ref_text = ref_header + (ref_body or "_（文献库为空）_") + "\n"
    dio.write_text_atomic(output_dir / "references.md", ref_text)
    artifacts["references"] = str(output_dir / "references.md")

    evidence_map = state.get("evidence_map") or {"evidence": []}
    if not isinstance(evidence_map, dict):
        evidence_map = {"evidence": []}
    if "evidence" not in evidence_map:
        evidence_map["evidence"] = []
    evidence_map["_meta"] = {
        "library_entries": dedup["kept_count"],
        "manuscript_source": manuscript_src,
        "note": "evidence items below are semantic; the Agent fills locator + quote per claim.",
    }
    dio.write_json_atomic(output_dir / "evidence_map.json", evidence_map)
    artifacts["evidence_map"] = str(output_dir / "evidence_map.json")

    consistency["style"] = style
    dio.write_json_atomic(output_dir / "consistency_report.json", consistency)
    artifacts["consistency_report"] = str(output_dir / "consistency_report.json")

    # ---- Step 5: contract alignment ----
    alignment = ce.build_contract_alignment(contract, artifacts, case_id)
    merged = {k: v for k, v in skeleton.items() if k != "contract_alignment"}

    for key in contract:
        if key in alignment["fields"]:
            merged[key] = alignment["fields"][key]["value"]

    merged["case_id"] = case_id
    merged["contract_alignment"] = alignment

    # ---- Step 6: standard fields ----
    change_log = merge_change_log(
        state.get("change_log") or [],
        "deterministic_pipeline",
        f"parsed {len(entries)} library entries -> {dedup['kept_count']} kept after dedup; style={style}",
    )
    if state.get("status") and state["status"] != "partial":
        merged["status"] = state["status"]
    else:
        merged["status"] = "partial" if (entries or manuscript_text) else "insufficient_evidence"

    sources = [
        {"kind": "case", "path": str(input_dir / "case.json")},
        {"kind": "task", "path": str(input_dir / "task.md")},
        {"kind": "manuscript", "path": manuscript_src} if manuscript_src else {"kind": "manuscript", "path": None},
        {"kind": "library_snapshot", "entries": dedup["kept_count"], "merged": dedup["merged_count"]},
    ]
    merged["sources"] = sources
    merged["uncertainty"] = list(state.get("uncertainty") or [])
    merged["change_log"] = change_log
    merged["errors"] = ledger.errors
    merged["degradations"] = ledger.degradations
    merged["pending_human"] = list(state.get("pending_human") or [])
    merged["artifacts"] = artifacts
    merged["runtime"] = {
        "seconds": round(time.time() - started, 3),
        "started_at": skeleton["runtime"]["started_at"],
        "finished_at": dio.utc_now(),
    }
    if not entries and not manuscript_text:
        merged["pending_human"] = sorted(set(merged["pending_human"] + ["无稿件且无文献库，无法完成引用管理"]))

    dio.write_json_atomic(output_dir / "result.json", merged)

    # ---- Step 7: persist state (idempotent accumulation) ----
    state["change_log"] = change_log
    state["evidence_map"] = evidence_map
    state["status"] = merged["status"]
    save_state(work_dir, state)

    return 0


def run_self_test() -> int:
    failures = ce.self_test()
    if failures:
        for f in failures:
            print(f"SELFTEST FAIL: {f}", file=sys.stderr)
        return 1
    print("selftest ok: deterministic engine checks passed")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deterministic citation-management backbone (parse, dedup, renumber, format, align contract)."
    )
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--style", default="gbt7714", choices=["gbt7714", "apa"])
    p.add_argument("--self-test", action="store_true", help="run in-memory engine checks and exit")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_pipeline(
        Path(args.input),
        Path(args.output),
        Path(args.work),
        args.style,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
