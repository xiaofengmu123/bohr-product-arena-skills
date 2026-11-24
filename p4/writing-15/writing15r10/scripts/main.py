# -*- coding: utf-8 -*-
"""Entry point for the long-document writing skill.

Usage (all paths may be omitted; defaults follow the runtime contract):

    python3 -B scripts/main.py [--input /input] [--output /app/output] \
        [--work /app/work] [--mode run|skeleton|check|finalize]

The default mode ``run`` writes a valid partial result.json *first* (before any
parsing that could fail), then improves it incrementally. Every mode returns
exit code 0 even when inputs are missing: an honest ``insufficient_evidence`` /
``input_error`` result is still a valid deliverable, never a crash.
"""
import argparse
import json
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import checks
import fileio as io_util

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_HEADER_CHARS = 1500


def build_sources(discovered, input_dir):
    """Build the sources list from discovered files. Each entry carries a
    locator, role, and size so downstream evidence can point back to it."""
    sources = []
    def add(path, role):
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        sources.append({"path": path, "locator": path, "role": role,
                        "size_bytes": size})
    add(discovered.get("case_json"), "case_json")
    add(discovered.get("task_md"), "task_md")
    add(discovered.get("workorder"), "workorder")
    for p in discovered.get("materials", []):
        add(p, "material")
    for p in discovered.get("binary", []):
        add(p, "material_binary")
    for p in discovered.get("other", []):
        add(p, "other")
    return sources


def make_skeleton(input_dir, output_dir, work_dir, started):
    """Discover inputs and produce the earliest valid result skeleton."""
    discovered = io_util.discover_inputs(input_dir)
    case, case_err = (None, None)
    if discovered["case_json"]:
        case, case_err = io_util.read_json(discovered["case_json"])
    task_text = None
    if discovered["task_md"]:
        rec = io_util.read_text(discovered["task_md"])
        if rec["ok"]:
            task_text = rec["text"]

    case_id = checks.extract_case_id(case, task_text, input_dir)
    contract_fields, contract_source = checks.find_output_contract(case)

    errors, degradations = [], []
    if case_err:
        degradations.append("case_json_read:%s" % case_err)
    if not discovered["files"]:
        errors.append("no_input_files_found")

    result = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": {
            "fields": contract_fields,
            "source": contract_source,
            "note": "Fields echoed at top level; missing values are null until filled.",
        },
        # Contract fields echoed at top level (placeholder values).
    }
    for f in contract_fields:
        result[f] = None

    # Standard envelope required by the runtime contract.
    result.update({
        "artifacts": [],
        "sources": build_sources(discovered, input_dir),
        "uncertainty": [],
        "change_log": [],
        "limitations": [],
        "errors": errors,
        "degradations": degradations,
        "pending_human": [],
        "runtime": {
            "started": started,
            "input_dir": input_dir,
            "output_dir": output_dir,
            "work_dir": work_dir,
            "discovered_file_count": len(discovered["files"]),
            "contract_fields": contract_fields,
        },
    })
    if task_text:
        result["task_summary"] = task_text[:MAX_HEADER_CHARS]

    if not discovered["files"]:
        result["status"] = "insufficient_evidence"
        result["limitations"].append(
            "input_dir_empty_or_missing:no_case_json_task_md_or_materials")
    elif not discovered["case_json"]:
        result["status"] = "input_error"
        result["errors"].append("case_json_missing")

    result["change_log"].append({
        "time": started, "action": "skeleton_written",
        "detail": "initial partial result created before analysis"})

    ok, err = io_util.write_json(os.path.join(output_dir, "result.json"), result)
    if not ok:
        # Fallback: write to work dir so something valid survives.
        io_util.write_json(os.path.join(work_dir, "result.json.fallback"), result)
    return result, discovered, case, task_text, ok, err


def read_material_texts(discovered, work_dir):
    """Read text materials and best-effort PDFs. Returns dict {locator: text},
    plus a degradations list."""
    texts = {}
    degradations = []
    for p in discovered.get("materials", []):
        rec = io_util.read_text(p)
        if rec["ok"] and rec["text"]:
            texts[p] = rec["text"]
        else:
            degradations.append("material_unreadable:%s:%s" % (p, rec["error"]))
    for p in discovered.get("binary", []):
        if p.lower().endswith(".pdf"):
            txt, err = io_util.extract_pdf_text(p)
            if txt:
                texts[p + "|pdftotext"] = txt
            else:
                degradations.append("pdf_text_unavailable:%s:%s" % (p, err))
        else:
            degradations.append("binary_not_parsed:%s" % p)
    return texts, degradations


def read_draft_texts(work_dir):
    """Read any .md/.txt drafts the Agent has already written into work_dir."""
    texts = {}
    if not os.path.isdir(work_dir):
        return texts
    for name in sorted(os.listdir(work_dir)):
        if name.startswith("."):
            continue
        full = os.path.join(work_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in (".md", ".txt"):
            rec = io_util.read_text(full)
            if rec["ok"] and rec["text"]:
                texts[full] = rec["text"]
    return texts


def run_checks_mode(input_dir, output_dir, work_dir):
    """Run deterministic checks over materials + drafts, write check_report.json."""
    discovered = io_util.discover_inputs(input_dir)
    material_texts, degradations = read_material_texts(discovered, work_dir)
    draft_texts = read_draft_texts(work_dir)
    claims = []
    # Extract claims from any claim list the Agent wrote.
    claims_path = os.path.join(work_dir, "claims.json")
    if os.path.isfile(claims_path):
        data, err = io_util.read_json(claims_path)
        if not err and isinstance(data, list):
            claims = [str(c) for c in data]
    report = checks.run_checks(material_texts, draft_texts, claims)
    report["degradations"] = degradations
    report["generated_at"] = io_util.now_iso()
    ok, err = io_util.write_json(os.path.join(output_dir, "check_report.json"), report)
    return report, ok, err


def finalize(input_dir, output_dir, work_dir):
    """Read an existing result.json (possibly edited by the Agent), validate it,
    ensure contract fields exist, and write it back."""
    result_path = os.path.join(output_dir, "result.json")
    result, err = io_util.read_json(result_path)
    if err or not isinstance(result, dict):
        result, discovered, case, task_text, _ok, _e = make_skeleton(
            input_dir, output_dir, work_dir, io_util.now_iso())
        return result, ["finalize_fell_back_to_skeleton:%s" % err]

    case, case_err = (None, None)
    discovered = io_util.discover_inputs(input_dir)
    if discovered["case_json"]:
        case, case_err = io_util.read_json(discovered["case_json"])
    contract_fields, _src = checks.find_output_contract(case)
    result = checks.merge_contract_fields(result, contract_fields)
    if "case_id" not in result or not result["case_id"]:
        result["case_id"] = checks.extract_case_id(
            case, result.get("task_summary"), input_dir)
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("change_log", [])
    result.setdefault("limitations", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("pending_human", [])
    result.setdefault("artifacts", [])

    problems = checks.validate_result(result)
    result["_validation"] = {"problems": problems, "ok": not problems}
    result["runtime"] = dict(result.get("runtime", {}))
    result["runtime"]["finalized"] = io_util.now_iso()
    result["runtime"]["validation_problems"] = problems

    if result.get("status") == "partial" and not result.get("limitations"):
        result["limitations"].append("partial_result_not_yet_completed")

    ok, err = io_util.write_json(result_path, result)
    return result, ([] if ok else ["finalize_write:%s" % err])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Long-document writing skill helper (stdlib only).")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--mode", default="run",
                        choices=["run", "skeleton", "check", "finalize"])
    args = parser.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = io_util.now_iso()

    if args.mode == "skeleton":
        _r, _d, _c, _t, ok, err = make_skeleton(
            args.input, args.output, args.work, started)
        print("skeleton %s" % ("ok" if ok else ("FAILED:" + str(err))))
        return 0

    if args.mode == "check":
        report, ok, err = run_checks_mode(args.input, args.output, args.work)
        print("check %s (%d term_variants, %d numbering_issues)" % (
            "ok" if ok else ("FAILED:" + str(err)),
            len(report.get("term_variants", [])),
            len(report.get("numbering", {}).get("issues", []))))
        return 0

    if args.mode == "finalize":
        _result, notes = finalize(args.input, args.output, args.work)
        print("finalize done; notes=%s" % (notes or "none"))
        return 0

    # Default: run = skeleton first, then checks, then finalize.
    result, discovered, case, task_text, ok, err = make_skeleton(
        args.input, args.output, args.work, started)
    if not ok:
        print("skeleton_write_failed:%s" % err, file=sys.stderr)

    material_texts, degradations = read_material_texts(discovered, args.work)
    draft_texts = read_draft_texts(args.work)
    report = checks.run_checks(material_texts, draft_texts, [])
    report["degradations"] = degradations
    report["generated_at"] = io_util.now_iso()
    io_util.write_json(os.path.join(args.output, "check_report.json"), report)

    # Merge check results into the result.json for traceability.
    result_path = os.path.join(args.output, "result.json")
    current, _e = io_util.read_json(result_path)
    if isinstance(current, dict):
        current["_check_summary"] = {
            "term_variant_count": len(report.get("term_variants", [])),
            "numbering_issues": report.get("numbering", {}).get("issues", []),
            "citation_issues": report.get("citations", {}).get("issues", []),
            "material_chars": report.get("stats", {}).get("material_chars", 0),
        }
        current["degradations"] = list(
            set(current.get("degradations", []) + degradations))
        current["change_log"].append({
            "time": io_util.now_iso(), "action": "checks_run",
            "detail": "deterministic consistency checks executed"})
        io_util.write_json(result_path, current)

    finalize(args.input, args.output, args.work)
    print("run done: status=%s case_id=%s" % (
        (result or {}).get("status"), (result or {}).get("case_id")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
