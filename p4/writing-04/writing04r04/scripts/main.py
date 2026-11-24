# -*- coding: utf-8 -*-
"""Entry point for the reference & citation management skill.

Usage
-----
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Behavior contract (important for the 900 s / 64 request harness budget)
----------------------------------------------------------------------
1. Immediately create the output directory and write a *valid* ``result.json``
   skeleton plus placeholder artifacts, so any later interruption never leaves
   an empty or unparseable deliverable.
2. Run the deterministic pipeline (discover files, parse the library,
   deduplicate with an explainable key, renumber citations by first
   appearance, format GB/T 7714--2015 or APA, run the cross-reference
   consistency check) and overwrite the artifacts.
3. Merge any Agent-supplied semantic enrichment from the work directory
   (``agent_evidence_map.json`` / ``agent_change_log.json``) if present, so the
   Agent can call this script repeatedly and improve the deliverable
   incrementally.
4. Always exit 0 with an honest ``status``. It never invents DOIs, metadata,
   scores or scientific data; unresolved items are recorded instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402

RESULT_FIELDS = [
    "status", "sources", "uncertainty", "limitations", "change_log",
    "errors", "degradations", "pending_human", "artifacts", "runtime",
]

MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}
LIBRARY_EXTS = {".bib", ".ris", ".enw", ".csv", ".tsv", ".xlsx", ".json"}
MAX_FILE_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Input discovery / reading
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    """UTF-8-SIG tolerant read; returns '' on any failure and records nothing
    here (callers log)."""
    try:
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            return ""
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def load_json(path: Path) -> Optional[Any]:
    text = read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def discover(input_dir: Path) -> Dict[str, List[Path]]:
    files = [p for p in input_dir.rglob("*") if p.is_file() and p.name not in {".DS_Store"}]
    # The fixed contract files are input, not material: exclude them from the
    # material scan so they do not pollute sources / manuscript / library.
    reserved = {input_dir / "case.json", input_dir / "task.md"}
    files = [p for p in files if p not in reserved]
    out: Dict[str, List[Path]] = {"manuscript": [], "library": [], "other": []}
    for p in files:
        ext = p.suffix.lower()
        if ext in LIBRARY_EXTS:
            out["library"].append(p)
            if ext == ".json":
                out["other"].append(p)
        elif ext in MANUSCRIPT_EXTS:
            out["manuscript"].append(p)
        else:
            out["other"].append(p)
    return out


def find_case_and_task(input_dir: Path) -> tuple:
    case_path = input_dir / "case.json"
    case = load_json(case_path) if case_path.exists() else None
    if not isinstance(case, dict):
        case = {}
    task_path = input_dir / "task.md"
    task_text = read_text(task_path) if task_path.exists() else ""
    return case, task_text


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

def build_skeleton(case: Dict[str, Any]) -> Dict[str, Any]:
    case_id = ""
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "armiId", "caseId"):
            v = case.get(k)
            if v not in (None, ""):
                case_id = str(v)
                break
    contract = {}
    raw = None
    if isinstance(case, dict):
        raw = case.get("output_contract", case.get("outputContract"))
    if isinstance(raw, dict):
        contract = {str(k): None for k in raw.keys()}
    elif isinstance(raw, list):
        contract = {str(k): None for k in raw if isinstance(k, str)}

    skeleton: Dict[str, Any] = {"case_id": case_id, **contract}
    for field in RESULT_FIELDS:
        skeleton.setdefault(field, None)
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["limitations"] = []
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


def placeholder(kind: str) -> str:
    return (
        f"<!-- {kind}: 尚未生成。本文件为部分结果占位；"
        f"完整内容由后续增量运行回填。请勿当作最终成果。 -->\n"
    )


# ---------------------------------------------------------------------------
# Deterministic pipeline
# ---------------------------------------------------------------------------

def parse_library(files: List[Path], errors: List[str], degradations: List[str]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for p in files:
        text = read_text(p)
        if not text:
            if p.stat().st_size > MAX_FILE_BYTES:
                degradations.append(f"{p.name}: 超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限，已跳过")
            else:
                errors.append(f"{p.name}: 无法读取或编码失败")
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = ce.parse_bibtex(text)
            elif ext in (".ris", ".enw"):
                parsed = ce.parse_ris(text)
            elif ext == ".json":
                data = load_json(p)
                parsed = ce.parse_reference_json(data) if data is not None else []
            elif ext in (".csv", ".tsv"):
                parsed = ce.parse_table(text)
            elif ext == ".xlsx":
                degradations.append(f"{p.name}: .xlsx 需运行时转换，本脚本仅记录为待处理")
                parsed = []
            else:
                parsed = []
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{p.name}: 文献库解析失败 ({exc})")
            parsed = []
        entries.extend(parsed)
    return entries


def pick_manuscript(files: List[Path]) -> Optional[Path]:
    # Prefer markdown/text/tex (editable); PDF/docx need runtime tools.
    order = {".md": 0, ".markdown": 0, ".txt": 1, ".tex": 1, ".docx": 2, ".pdf": 3}
    best = None
    for p in files:
        rank = order.get(p.suffix.lower(), 9)
        if best is None or rank < order.get(best.suffix.lower(), 9):
            best = p
    return best


def run_pipeline(input_dir: Path, style: str, errors: List[str],
                 degradations: List[str], work_dir: Path) -> Dict[str, Any]:
    found = discover(input_dir)
    sources = [str(p) for p in found["manuscript"] + found["library"]]
    state: Dict[str, Any] = {"sources": sources}

    ms_path = pick_manuscript(found["manuscript"])
    manuscript_raw = ""
    if ms_path is not None:
        manuscript_raw = read_text(ms_path)
        if not manuscript_raw:
            errors.append(f"{ms_path.name}: 稿件读取失败")
    state["manuscript_original"] = manuscript_raw

    entries = parse_library(found["library"], errors, degradations)
    dedup = ce.deduplicate(entries)
    state["library_entries"] = dedup["entries"]
    state["merge_evidence"] = dedup["merge_evidence"]

    # Renumber citations by first appearance.
    renumbered, mapping, spans = ce.renumber_citations(manuscript_raw)
    state["manuscript_processed"] = renumbered
    state["citation_mapping"] = {str(k): v for k, v in mapping.items()}
    state["citation_spans"] = spans

    ref_count = len(dedup["entries"])
    ref_indices = list(range(1, ref_count + 1))
    consistency = ce.consistency_check(renumbered, ref_indices)
    state["consistency"] = consistency

    if style == "apa":
        ref_lines = [ce.format_apa(e, i + 1) for i, e in enumerate(dedup["entries"])]
    else:
        ref_lines = [ce.format_gbt7714(e, i + 1) for i, e in enumerate(dedup["entries"])]
    state["reference_list"] = "\n\n".join(ref_lines) if ref_lines else placeholder("文献表")

    # Deterministic evidence backbone: citation locators with verbatim quotes.
    locators = ce.citation_locators(renumbered, spans)
    evidence: List[Dict[str, Any]] = []
    for i, loc in enumerate(locators, start=1):
        new_raw = loc.get("new_raw", "")
        cited = ce.expand_marker(new_raw.strip("[]"))
        evidence.append({
            "citation_id": i,
            "marker": new_raw,
            "locator": loc.get("locator"),
            "quote": loc.get("quote"),
            "candidate_entry_ids": cited,
            "claim": None,
            "match_evidence": None,
            "verification": {"status": "pending_agent", "note": "待 Agent 逐句核验论断与文献是否匹配"},
        })
    state["evidence_map"] = {"citations": evidence, "merge_evidence": dedup["merge_evidence"]}

    # Incremental merge of Agent enrichment (idempotent).
    state["agent_evidence_map"] = None
    state["agent_change_log"] = None
    ae_path = work_dir / "agent_evidence_map.json"
    ac_path = work_dir / "agent_change_log.json"
    if ae_path.exists():
        ae = load_json(ae_path)
        if isinstance(ae, dict):
            state["evidence_map"] = ae
            state["agent_evidence_map"] = ae
    if ac_path.exists():
        ac = load_json(ac_path)
        if isinstance(ac, list):
            state["agent_change_log"] = ac
    return state


# ---------------------------------------------------------------------------
# Status / uncertainty derivation (no fabrication)
# ---------------------------------------------------------------------------

def derive_status(state: Dict[str, Any], errors: List[str], degradations: List[str]) -> str:
    manuscript = state.get("manuscript_original", "")
    entries = state.get("library_entries", [])
    if errors:
        # a structural input problem was recorded but pipeline still produced a skeleton
        if not manuscript and not entries:
            return "input_error"
    if not manuscript and not entries:
        return "insufficient_evidence"
    evidence = state.get("evidence_map", {}).get("citations", [])
    all_verified = bool(evidence) and all(
        e.get("verification", {}).get("status") == "verified" for e in evidence
    )
    if all_verified and not errors:
        return "ok"
    return "partial"


def build_uncertainty(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    evidence = state.get("evidence_map", {}).get("citations", [])
    for e in evidence:
        status = e.get("verification", {}).get("status", "pending_agent")
        if status == "pending_agent":
            out.append({
                "id": f"unc-cite-{e.get('citation_id')}",
                "kind": "unverified_claim_match",
                "locator": e.get("locator"),
                "claim": "正文引用与文献条目是否真实匹配尚未核验",
                "detail": e.get("marker", ""),
                "severity": "high",
                "resolution": "逐句比对论断主题/结论与文献摘要，回填 match_evidence",
            })
    for me in state.get("merge_evidence", []):
        if me.get("field_conflicts"):
            out.append({
                "id": f"unc-merge-{me.get('dedup_key', 'x')[:16]}",
                "kind": "merge_field_conflict",
                "locator": "reference library",
                "claim": "去重合并的条目在字段上存在冲突，保留首值但需人工确认",
                "detail": str(me.get("field_conflicts")),
                "severity": "medium",
                "resolution": "人工核对冲突字段（如题名/卷期页）后修正",
            })
    return out


def build_limitations(state: Dict[str, Any], degradations: List[str],
                      style: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ms = state.get("manuscript_original", "")
    if ms.endswith((".docx", ".pdf")) or not ms:
        out.append({
            "scope": "manuscript",
            "limitation": "稿件为 .docx/.pdf 或缺失时，本包交付 Markdown 可编辑版；原格式回写需运行时 office-renderer / pdf-text-tool",
            "status": "pending_runtime_tool",
        })
    if degradations:
        out.append({
            "scope": "input",
            "limitation": "部分输入文件被跳过或降级处理，见 degradations",
            "status": "degraded",
        })
    if style not in ("gbt7714", "apa"):
        out.append({
            "scope": "format",
            "limitation": f"未知样式 {style!r}，已回退到 gbt7714",
            "status": "fallback",
        })
    out.append({
        "scope": "verification",
        "limitation": "DOI 真实性、元数据准确性、论断—文献匹配依赖运行时检索/原文比对；脚本不编造核验结论",
        "status": "pending_agent",
    })
    return out


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_artifacts(out_dir: Path, state: Dict[str, Any]) -> Dict[str, str]:
    artifacts: Dict[str, str] = {}
    for name, text in (
        ("manuscript.md", state.get("manuscript_processed", placeholder("稿件"))),
        ("references.md", state.get("reference_list", placeholder("文献表"))),
    ):
        write_text_atomic(out_dir / name, text)
        artifacts[name.removesuffix(".md")] = str(out_dir / name)
    write_json_atomic(out_dir / "evidence_map.json", state.get("evidence_map", {}))
    artifacts["evidence_map"] = str(out_dir / "evidence_map.json")
    write_json_atomic(out_dir / "consistency_report.json", state.get("consistency", {}))
    artifacts["consistency_report"] = str(out_dir / "consistency_report.json")
    return artifacts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="参考文献与引用管理 Skill 的确定性执行入口",
    )
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--style", choices=["gbt7714", "apa"], default="gbt7714")
    parser.add_argument("--self-test", action="store_true",
                        help="运行内存自检并退出（退出码 0 表示通过）")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    case, task_text = find_case_and_task(input_dir)

    # Phase 0/1: write a valid partial skeleton FIRST.
    skeleton = build_skeleton(case)
    write_json_atomic(output_dir / "result.json", skeleton)
    write_text_atomic(output_dir / "manuscript.md", placeholder("稿件"))
    write_text_atomic(output_dir / "references.md", placeholder("文献表"))
    write_json_atomic(output_dir / "evidence_map.json", {"citations": []})
    write_json_atomic(output_dir / "consistency_report.json", {})

    # Phase 2: deterministic pipeline.
    errors: List[str] = []
    degradations: List[str] = []
    state = run_pipeline(input_dir, args.style, errors, degradations, work_dir)

    artifacts = write_artifacts(output_dir, state)

    status = derive_status(state, errors, degradations)
    uncertainty = build_uncertainty(state)
    limitations = build_limitations(state, degradations, args.style)

    result = build_skeleton(case)
    result["status"] = status
    result["sources"] = state.get("sources", [])
    result["uncertainty"] = uncertainty
    result["limitations"] = limitations
    result["change_log"] = state.get("agent_change_log") or []
    result["errors"] = errors
    result["degradations"] = degradations
    result["pending_human"] = [
        u for u in uncertainty if u.get("severity") == "high" or u.get("kind") == "merge_field_conflict"
    ]
    result["artifacts"] = artifacts
    # Echo output_contract keys with actual artifact paths where they match.
    for key in list(result.keys()):
        if key in RESULT_FIELDS or key == "case_id":
            continue
        if key in artifacts:
            result[key] = artifacts[key]
        elif result[key] is None:
            result[key] = []
    result["runtime"] = {
        "seconds": round(time.time() - t0, 3),
        "started_at": skeleton.get("runtime", {}).get("started_at"),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": status,
    }
    write_json_atomic(output_dir / "result.json", result)
    return 0


def self_test() -> int:
    """In-memory checks; never touches /app/output. Exit 0 == pass."""
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        if not cond:
            ok = False
            print(f"[FAIL] {name}")
        else:
            print(f"[ ok ] {name}")

    bib = """@article{smith2020,
      author = {Smith, John and Li, Wei},
      title = {Deep Learning for Chemistry},
      journal = {Nature},
      year = {2020},
      volume = {10},
      number = {1},
      pages = {1--9},
      doi = {10.1000/abc123}
    }"""
    entries = ce.parse_bibtex(bib)
    check("bibtex parse", len(entries) == 1 and entries[0]["doi"] == "10.1000/abc123")

    dup = ce.deduplicate(entries + [dict(entries[0])])
    check("dedup merges identical", dup["removed"] == 1 and len(dup["entries"]) == 1)

    text = "See [2,3] and [1]. Later [2-3] again."
    new, mapping, spans = ce.renumber_citations(text)
    check("renumber first-appearance", "[1,2]" in new and mapping == {2: 1, 3: 2, 1: 3})
    check("collapse range", "[1-2]" in new or "[1,2]" in new)

    cons = ce.consistency_check(new, [1, 2, 3])
    check("consistency ok", cons["ok"] is True)
    check("consistency detects orphan",
          ce.consistency_check("x [5]", [1, 2])["orphan_citations"] == [5])

    fmt = ce.format_gbt7714(entries[0], 1)
    check("gbt7714 format", "[1]" in fmt and "Nature" in fmt)

    # Skeleton contract: every output_contract key echoed as null/[].
    skel = build_skeleton({"case_id": "C1", "output_contract": {"manuscript": "", "notes": ""}})
    check("skeleton case_id", skel["case_id"] == "C1")
    check("skeleton echoes contract", skel["manuscript"] is None and skel["notes"] is None)
    check("skeleton standard fields", skel["status"] == "partial" and isinstance(skel["uncertainty"], list))

    # Empty input -> insufficient_evidence.
    st = derive_status({"manuscript_original": "", "library_entries": [], "evidence_map": {"citations": []}}, [], [])
    check("empty input status", st == "insufficient_evidence")

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
