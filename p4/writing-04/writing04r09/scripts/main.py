"""Entry point for the reference & citation management skill.

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Contract (important for the 900s / 64-request harness budget):
1. Immediately write a valid result.json skeleton and placeholder artifacts,
   so any later interruption still leaves a parseable, honest partial result.
2. Run the deterministic pipeline (parse, deduplicate, renumber, format,
   consistency check) and write editable artifacts.
3. Always exit 0 with an honest status; never fabricate data or scores.

Claim-level citation placement and source verification are the Agent's job,
following SKILL.md; this script only supplies the deterministic backbone.
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

import core_impl as ce  # noqa: E402

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "artifacts", "runtime",
]
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

MANUSCRIPT_EXT = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}
LIBRARY_EXT = {".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".xlsx"}
SPECIAL_NAMES = {"task.md", "case.json", "challenge.json"}


def discover_files(input_dir: Path) -> dict[str, list[Path]]:
    """Scan recursively; classify without assuming fixed names."""
    manuscript, library, others = [], [], []
    if not input_dir.exists():
        return {"manuscript": [], "library": [], "others": []}
    for p in input_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name.lower() in SPECIAL_NAMES:
            continue
        ext = p.suffix.lower()
        if ext in MANUSCRIPT_EXT:
            manuscript.append(p)
        elif ext in LIBRARY_EXT:
            library.append(p)
            if ext == ".json":
                others.append(p)  # may actually be manuscript-like data
        else:
            others.append(p)
    return {"manuscript": manuscript, "library": library, "others": others}


def load_case(input_dir: Path, ledger: dict) -> dict:
    case_path = input_dir / "case.json"
    case = {}
    if case_path.exists():
        data = ce.load_json_file(case_path)
        if isinstance(data, dict):
            case = data
        else:
            ledger["errors"].append("case.json 不是有效 JSON 对象，按空契约处理。")
    else:
        ledger["degradations"].append("未发现 /input/case.json，case_id 留空。")
    return case


def build_skeleton(case: dict, started_at: str) -> dict:
    """Echo every output_contract key (null) plus standard fields."""
    case_id = ""
    if isinstance(case, dict):
        case_id = str(
            case.get("case_id") or case.get("id") or case.get("challenge_id")
            or case.get("armiId") or ""
        )
    contract = {}
    if isinstance(case, dict):
        raw = case.get("output_contract") or case.get("outputContract") or {}
        if isinstance(raw, dict):
            contract = {str(k): None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {str(k): None for k in raw if isinstance(k, (str, int))}
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
    skeleton["artifacts"] = {}
    skeleton["runtime"] = {"seconds": 0.0, "started_at": started_at}
    return skeleton


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, obj: object) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def parse_library(files: list[Path], ledger: dict) -> list[dict]:
    entries = []
    for p in files:
        try:
            if p.stat().st_size > ce.MAX_FILE_BYTES:
                ledger["degradations"].append(f"{p.name}: 超过 2MB 上限，已跳过。")
                continue
        except OSError:
            continue
        text = ce.read_doc(p)
        if not text:
            if p.suffix.lower() == ".pdf":
                ledger["pending_human"].append(f"{p.name}: PDF 需运行时 pdf-text-tool 提取文本。")
            else:
                ledger["degradations"].append(f"{p.name}: 读取为空或编码异常，已跳过。")
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = ce.parse_bibtex(text, p.name)
            elif ext == ".ris":
                parsed = ce.parse_ris(text, p.name)
            elif ext in {".csv", ".tsv"}:
                parsed = ce.parse_table(text, p.name)
            elif ext in {".json", ".xlsx"}:
                if ext == ".json":
                    data = ce.load_json_text(text, p.name)
                    parsed = ce.parse_reference_json(data, p.name) if data else []
                else:
                    parsed = ce.parse_table(text, p.name)
            elif ext in {".docx", ".enw"}:
                parsed = ce.parse_ris(text, p.name) if ext == ".enw" else []
            else:
                parsed = []
        except Exception as exc:  # noqa: BLE001
            ledger["errors"].append(f"{p.name}: 解析失败（{exc}）。")
            parsed = []
        entries.extend(parsed)
    return entries


def load_citation_map(work_dir: Path, ledger: dict) -> dict[int, str]:
    p = work_dir / "citation_map.json"
    if not p.exists():
        ledger["pending_human"].append(
            "未提供 work/citation_map.json（旧编号 -> 条目 key 的映射），编号重排待语义阶段完成。"
        )
        return {}
    data = ce.load_json_file(p)
    if not isinstance(data, dict):
        return {}
    assignments = data.get("assignments") or data.get("citation_map") or {}
    out: dict[int, str] = {}
    if isinstance(assignments, list):
        for item in assignments:
            if not isinstance(item, dict):
                continue
            old = item.get("old_number") or item.get("old")
            key = item.get("entry_key") or item.get("key")
            if old is not None and key:
                out[int(old)] = str(key)
    elif isinstance(assignments, dict):
        for k, v in assignments.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reference & citation management skill (deterministic backbone).")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--style", default="gbt7714", choices=["gbt7714", "apa"])
    ap.add_argument("--self-test", action="store_true", help="run in-memory self test and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        import selftest
        return 0 if selftest.run() else 1

    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    in_dir, out_dir, work_dir = Path(args.input), Path(args.output), Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    ledger: dict[str, list] = {k: [] for k in
                               ("errors", "degradations", "pending_human", "uncertainty", "change_log")}
    case = load_case(in_dir, ledger)
    state = build_skeleton(case, started_at)
    # --- Stage 0: skeleton first, always. ---
    atomic_write_json(out_dir / "result.json", state)

    files = discover_files(in_dir)
    sources = [str(p) for p in files["manuscript"] + files["library"] + files["others"]]

    # --- Stage 1: deterministic parse + dedup. ---
    library_entries = parse_library(files["library"], ledger)
    if not library_entries and not files["library"]:
        state["status"] = "insufficient_evidence"
        ledger["pending_human"].append("未发现文献库（.bib/.ris/.json/.csv/.xlsx 等），无法完成引用整理。")
    unique, merge_evidence = ce.deduplicate(library_entries)
    entries_by_key = {e["key"]: e for e in unique}
    atomic_write_json(work_dir / "parsed_entries.json", unique)
    atomic_write_json(work_dir / "merge_evidence.json", merge_evidence)

    # --- Stage 2: manuscript + renumber. ---
    manuscript_text = ""
    manuscript_src = files["manuscript"][0] if files["manuscript"] else None
    if manuscript_src:
        manuscript_text = ce.read_doc(manuscript_src)
        if manuscript_src.suffix.lower() == ".pdf" and not manuscript_text:
            ledger["pending_human"].append(
                f"{manuscript_src.name}: PDF 稿件需运行时 pdf-text-tool 提取文本后再处理。"
            )
    if not manuscript_text:
        state["status"] = "insufficient_evidence"
        ledger["pending_human"].append("未发现可读稿件（.md/.txt/.tex/.docx 等）。")

    citation_map = load_citation_map(work_dir, ledger)
    renumbered_text = manuscript_text
    cited_order: list[str] = []
    renumber_unresolved: list[dict] = []
    if manuscript_text and citation_map:
        renumbered_text, cited_order, renumber_unresolved = ce.renumber_numeric(
            manuscript_text, citation_map, entries_by_key
        )
        for u in renumber_unresolved:
            ledger["uncertainty"].append(f"正文旧编号 {u['old_number']} 未在 citation_map 中给出映射。")
    elif manuscript_text:
        markers = ce.detect_citations(manuscript_text)
        if markers:
            ledger["pending_human"].append(
                f"稿件中检测到 {len(markers)} 处数字引用标记，需语义阶段给出 work/citation_map.json 后重跑重排。"
            )

    # --- Stage 3: reference list + consistency. ---
    style = args.style
    if isinstance(case, dict):
        raw = case.get("output_contract") or {}
        if isinstance(raw, dict) and "style" in raw:
            style = str(raw.get("style") or style).lower()
        elif case.get("citation_style"):
            style = str(case["citation_style"]).lower()
    style = style if style in ("gbt7714", "apa") else "gbt7714"

    reference_lines = []
    for key in cited_order:
        e = entries_by_key.get(key)
        if e:
            reference_lines.append(f"{len(reference_lines) + 1}. {ce.format_entry(e, style)}")
    for key in sorted(entries_by_key):
        if key not in cited_order:
            e = entries_by_key[key]
            reference_lines.append(f"[未引用] {ce.format_entry(e, style)}")

    consistency = ce.consistency_check(renumbered_text, cited_order, entries_by_key)
    for k in consistency.get("unresolved_citations", []):
        ledger["uncertainty"].append(f"正文引用了不在文献库中的条目 key：{k}。")
    if consistency.get("numbering_gaps"):
        ledger["uncertainty"].append(f"编号不连续，缺口：{consistency['numbering_gaps']}。")

    # --- Stage 4: write artifacts + finalize result.json. ---
    artifacts = {
        "manuscript": str(out_dir / "manuscript.md"),
        "references": str(out_dir / "references.md"),
        "evidence_map": str(out_dir / "evidence_map.json"),
        "consistency_report": str(out_dir / "consistency_report.json"),
    }
    atomic_write_text(out_dir / "manuscript.md", renumbered_text or _placeholder("稿件"))
    atomic_write_text(out_dir / "references.md", "\n".join(reference_lines) or _placeholder("文献表"))
    evidence = {"entries": unique, "merge_evidence": merge_evidence,
                "citation_map_loaded": bool(citation_map), "renumber_unresolved": renumber_unresolved}
    atomic_write_json(out_dir / "evidence_map.json", evidence)
    atomic_write_json(out_dir / "consistency_report.json", consistency)

    state["status"] = state.get("status") or "partial"
    if state["status"] != "insufficient_evidence" and not ledger["pending_human"] and not renumber_unresolved:
        state["status"] = "partial"  # 'ok' is only set by the Agent after semantic verification.
    state["sources"] = sources
    state["artifacts"] = artifacts
    state["uncertainty"] = ledger["uncertainty"]
    state["change_log"] = ledger["change_log"] + [
        f"deterministic pipeline: parsed {len(library_entries)} records -> {len(unique)} unique entries",
        f"merge evidence: {len(merge_evidence)} clusters",
        f"style: {style}",
    ]
    state["errors"] = ledger["errors"]
    state["degradations"] = ledger["degradations"]
    state["pending_human"] = ledger["pending_human"]
    state["runtime"]["seconds"] = round(time.time() - started, 3)

    # Fill contract fields the script can determine (artifact paths, style).
    _fill_contract(state, style)

    atomic_write_json(out_dir / "result.json", state)
    return 0


ARTIFACT_ALIASES = {
    "manuscript": ("manuscript", "manuscript_path", "manuscript_md", "edited_manuscript", "edited_manuscript_path"),
    "references": ("references", "reference_list", "references_md", "bibliography"),
    "evidence_map": ("evidence_map", "evidence", "source_map"),
    "consistency_report": ("consistency_report", "consistency", "consistency_check"),
}


def _fill_contract(state: dict, style: str) -> None:
    """Map deterministic outputs onto output_contract keys whose names match."""
    artifacts = state.get("artifacts", {})
    for k in list(state.keys()):
        if state[k] is not None:
            continue
        low = str(k).lower().replace("-", "_").replace(" ", "_")
        if low in ("style", "citation_style", "reference_style"):
            state[k] = style
            continue
        for art_key, aliases in ARTIFACT_ALIASES.items():
            if low in aliases or low.endswith(art_key) or art_key in low:
                state[k] = artifacts.get(art_key)
                break


def _placeholder(what: str) -> str:
    return f"[占位] {what}：语义阶段尚未完成，此处为非真实结果占位符，请勿当作正式产物。"


if __name__ == "__main__":
    raise SystemExit(main())
