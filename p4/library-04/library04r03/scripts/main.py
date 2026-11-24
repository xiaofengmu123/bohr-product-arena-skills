"""main.py — deterministic pipeline entry point for the research-KB QA skill.

Usage
-----
python3 scripts/main.py --input /input --output /app/output --work /app/work \
    --phase scaffold|inventory|qa|finalize|all [--answers /app/work/answers.json]

Phases can be re-run independently; each phase only writes or overwrites its
own artifacts, so an agent can iterate without losing earlier work.  The first
phase (scaffold) writes a valid partial result.json before any analysis, so a
crash or timeout never leaves the output directory without a legal product.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import core_impl as c


def resolve_dir(value: str, fallback: str) -> str:
    return os.path.abspath(value) if value else os.path.abspath(fallback)


def load_state(work: str) -> dict:
    path = os.path.join(work, "state.json")
    data, err = c.safe_json(path)
    if isinstance(data, dict) and not err:
        return data
    return {"case_summary": {}, "errors": [], "degradations": [],
            "change_log": []}


def save_state(work: str, state: dict) -> None:
    os.makedirs(work, exist_ok=True)
    c.write_json(os.path.join(work, "state.json"), state)


def append_log(state: dict, step: str, action: str, status: str) -> None:
    state.setdefault("change_log", []).append(
        c.log_entry(step, action, status))


def phase_scaffold(inp: str, out: str, work: str, state: dict) -> dict:
    errors = state.setdefault("errors", [])
    degradations = state.setdefault("degradations", [])
    case_path = os.path.join(inp, "case.json")
    task_path = os.path.join(inp, "task.md")

    case = None
    err = None
    if os.path.exists(case_path):
        case, err = c.safe_json(case_path)
        if err:
            errors.append(f"case.json: {err}")
    else:
        errors.append("case.json_missing")
    task_text = ""
    if os.path.exists(task_path):
        task_text, _e, terr = c.read_text(task_path)
        if terr:
            degradations.append({"file": "task.md", "kind": "text_degraded",
                                 "detail": terr})

    case_id = None
    if isinstance(case, dict):
        case_id = (c.normalize_id(case.get("case_id")) or
                   c.normalize_id(case.get("challenge_id")) or
                   c.normalize_id(case.get("id")) or
                   c.normalize_id(case.get("armiId")))
    if not case_id:
        case_id = case.get("id") if isinstance(case, dict) else None
    case_id = case_id or "unknown"

    contract = case.get("output_contract") if isinstance(case, dict) else None
    if not isinstance(contract, (dict, list)):
        contract = None

    questions = c.extract_questions(case, task_text)

    state["case_summary"] = {
        "case_id": case_id,
        "output_contract": contract,
        "questions": questions,
        "task_md_exists": os.path.exists(task_path),
        "case_json_exists": os.path.exists(case_path),
        "input_dir": os.path.abspath(inp),
    }

    # immediate partial skeleton — honest, empty, but valid
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
        "answers": [{"id": q["id"], "question": q["text"], "answer": None,
                     "evidence": [], "uncertainty": "insufficient_evidence",
                     "missing": "尚未检索与判定"} for q in questions],
        "sources": [],
        "uncertainty": [],
        "pending_human": [{"item": q["text"], "why": "骨架阶段尚未检索"}
                          for q in questions],
        "change_log": [c.log_entry("scaffold", "write partial skeleton",
                                   "partial")],
        "errors": list(errors),
        "degradations": list(degradations),
        "runtime": {"started": c.now_iso(), "finished": None,
                    "phase": "scaffold"},
        "limitations": ["scaffold 阶段仅落盘骨架，尚未盘点/检索/问答"],
    }
    for k, v in (contract.items() if isinstance(contract, dict) else []):
        if k not in skeleton:
            skeleton[k] = v
    os.makedirs(out, exist_ok=True)
    c.write_json(os.path.join(out, "result.json"), skeleton)
    append_log(state, "scaffold", "write /app/output/result.json skeleton",
               "ok" if not errors else "partial")
    save_state(work, state)
    return state


def phase_inventory(inp: str, out: str, work: str, state: dict) -> dict:
    errors = state.setdefault("errors", [])
    degradations = state.setdefault("degradations", [])
    inventory = c.build_inventory(inp, errors, degradations)
    c.write_json(os.path.join(work, "inventory.json"), inventory)

    # evidence index: per-text-file line pointer list for deterministic recall
    index = []
    for f in inventory.get("files", []):
        if f["type"] != "text" or not f.get("text_encoding"):
            continue
        text, _enc, _err = c.read_text(f["abspath"])
        if text is None:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped:
                index.append({"file": f["relpath"], "line": idx,
                              "tokens": sorted(
                                  c.tokenize(stripped))[:80]})
    c.write_json(os.path.join(work, "evidence_index.json"),
                 {"lines": index})
    append_log(state, "inventory",
               f"inventory {inventory['summary']['file_count']} files",
               "ok")
    save_state(work, state)
    return state


def phase_qa(inp: str, out: str, work: str, state: dict) -> dict:
    """Write honest retrieval-candidate answers only if the agent has not
    already produced /app/work/answers.json (so model edits are never clobbered)."""
    target = os.path.join(work, "answers.json")
    if os.path.exists(target):
        append_log(state, "qa", "answers.json already present; skip retrieval",
                   "ok")
        save_state(work, state)
        return state

    summary = state.get("case_summary", {})
    questions = summary.get("questions", [])
    inv_path = os.path.join(work, "inventory.json")
    inventory, _ = c.safe_json(inv_path) or ({}, None)
    files = inventory.get("files", []) if isinstance(inventory, dict) else []

    answers = []
    for q in questions:
        cands = c.retrieve_candidates(files, q.get("text", ""))
        answers.append({
            "id": q.get("id"), "question": q.get("text"),
            "answer": None,
            "evidence": [],
            "candidates": cands,
            "uncertainty": "insufficient_evidence",
            "missing": "脚本仅提供检索候选，待模型逐条核对原文后作答",
        })
    qa = {"answers": answers, "uncertainty": []}
    c.write_json(target, qa)
    append_log(state, "qa", "write retrieval candidate answers.json",
               "ok")
    save_state(work, state)
    return state


def phase_finalize(inp: str, out: str, work: str, state: dict,
                   answers_path: str | None) -> dict:
    errors = state.setdefault("errors", [])
    degradations = state.setdefault("degradations", [])
    summary = state.get("case_summary", {})
    inv_path = os.path.join(work, "inventory.json")
    inventory, _ = c.safe_json(inv_path) or ({}, None)
    if not isinstance(inventory, dict) or not inventory.get("files"):
        inventory = {"files": [], "summary": {}, "directory_tree": {},
                     "duplicate_groups": [], "version_families": [],
                     "empty_files": []}

    ans_path = answers_path or os.path.join(work, "answers.json")
    answers, aerr = c.safe_json(ans_path) if os.path.exists(ans_path) else (
        {"answers": [], "uncertainty": []}, "answers.json_not_found")
    if aerr:
        degradations.append({"file": "answers.json", "kind": "json_error",
                             "detail": aerr})
    if not isinstance(answers, dict):
        answers = {"answers": [], "uncertainty": []}

    started = state.get("_started") or c.now_iso()
    result = c.finalize(summary, inventory, answers, errors, degradations)
    result["change_log"] = state.get("change_log", []) + [
        c.log_entry("finalize", "write final result.json", "ok")]
    result["runtime"] = {"started": started, "finished": c.now_iso(),
                         "phase": "finalize",
                         "python": sys.version.split()[0],
                         "pid": os.getpid()}
    result["artifact"] = {
        "result_json": "result.json",
        "inventory_md": "inventory.md",
        "evidence_map_json": "evidence_map.json",
    }
    result["limitations"] = state.get("limitations", [])

    os.makedirs(out, exist_ok=True)
    c.write_json(os.path.join(out, "result.json"), result)

    # editable artifacts
    md = c.render_inventory_md(inventory)
    with open(os.path.join(out, "inventory.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(md)

    evidence_map = {}
    for a in result.get("answers", []):
        evidence_map[str(a.get("id", a.get("question", "?")))] = {
            "question": a.get("question"),
            "answer": a.get("answer"),
            "uncertainty": a.get("uncertainty"),
            "evidence": a.get("evidence", []),
        }
    c.write_json(os.path.join(out, "evidence_map.json"), evidence_map)

    append_log(state, "finalize", "write final result + editable artifacts",
               result["status"])
    save_state(work, state)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="research-KB QA deterministic pipeline")
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--phase", default="all",
                    choices=["scaffold", "inventory", "qa", "finalize", "all"])
    ap.add_argument("--answers", default=None)
    args = ap.parse_args(argv)

    inp = resolve_dir(args.input, c.DEFAULT_ROOT["input"])
    out = resolve_dir(args.output, c.DEFAULT_ROOT["output"])
    work = resolve_dir(args.work, c.DEFAULT_ROOT["work"])
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    state = load_state(work)
    state.setdefault("errors", [])
    state.setdefault("degradations", [])
    state.setdefault("change_log", [])
    state.setdefault("limitations", [])
    if "_started" not in state:
        state["_started"] = c.now_iso()

    order = (["scaffold", "inventory", "qa", "finalize"] if args.phase == "all"
             else [args.phase])
    try:
        for phase in order:
            t0 = time.time()
            if phase == "scaffold":
                phase_scaffold(inp, out, work, state)
            elif phase == "inventory":
                phase_inventory(inp, out, work, state)
            elif phase == "qa":
                phase_qa(inp, out, work, state)
            elif phase == "finalize":
                phase_finalize(inp, out, work, state, args.answers)
            state.setdefault("phase_seconds", {})[phase] = round(
                time.time() - t0, 3)
        save_state(work, state)
        return 0
    except Exception as exc:  # noqa: BLE001 - never leave an invalid product
        minimal = {
            "case_id": state.get("case_summary", {}).get("case_id",
                                                        "unknown"),
            "status": "partial",
            "errors": state.get("errors", []) +
                      [f"{type(exc).__name__}: {exc}"],
            "change_log": state.get("change_log", []) + [
                c.log_entry("crash", "pipeline exception -> minimal product",
                            "partial")],
            "sources": [],
            "uncertainty": [],
            "runtime": {"finished": c.now_iso()},
        }
        try:
            c.write_json(os.path.join(out, "result.json"), minimal)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())