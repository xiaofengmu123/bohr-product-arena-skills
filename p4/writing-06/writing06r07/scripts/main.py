#!/usr/bin/env python3
"""Entry point for the manuscript-structure-review Skill.

Subcommands
-----------
skeleton   Write /app/output/result.json partial skeleton (always succeeds,
           even with empty or malformed input). First thing to run.
evidence   Scan /input materials and write /app/work/evidence.json with
           candidate anchors (sections, numbers, claims, citations). Never
           crashes on empty/oversized/undecodable files.
verify     Validate result.json contract and quote-backtracking, and write
           /app/output/verify_report.json.
(default)  Run skeleton -> evidence -> verify as one safe pass.

Robustness contract: any input-related failure is recorded in errors /
degradations and the process still exits 0 with a valid JSON artifact.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402


def env_or(name, default):
    return os.environ.get(name) or default


def resolve_paths(a):
    output_dir = a.output_dir or env_or("OUTPUT_DIR", "/app/output")
    input_dir = a.input_dir or env_or("INPUT_DIR", "/input")
    work_dir = a.work_dir or env_or("WORK_DIR", "/app/work")
    result_json = a.result_json or env_or(
        "RESULT_JSON", os.path.join(output_dir, "result.json"))
    return {
        "case_json": a.case_json or env_or("CASE_JSON",
                                           os.path.join(input_dir, "case.json")),
        "task_md": a.task_md or env_or("TASK_MD",
                                       os.path.join(input_dir, "task.md")),
        "input_dir": input_dir,
        "work_dir": work_dir,
        "output_dir": output_dir,
        "result_json": result_json,
    }


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def cmd_skeleton(p):
    """Write a partial result skeleton; never fails on input problems."""
    case, err = C.read_json_safe(p["case_json"])
    degradations = []
    if err:
        degradations.append({"item": "case.json", "reason": err})
        case = {}
    contract = C.extract_contract(case)
    skel = C.build_skeleton(case, contract, case_path=p["case_json"])
    if degradations:
        skel["degradations"].extend(degradations)
        skel["uncertainty"].append({"item": "case.json", "reason": err})
    os.makedirs(p["output_dir"], exist_ok=True)
    C.write_json_atomic(p["result_json"], skel)
    _emit({"ok": True, "action": "skeleton", "result": p["result_json"],
           "case_id": skel["case_id"], "status": skel["status"],
           "contract_keys": contract})
    return 0


def cmd_evidence(p):
    """Scan materials; write evidence.json; never crashes."""
    os.makedirs(p["work_dir"], exist_ok=True)
    files = C.list_input_files(p["input_dir"])
    ev = {
        "scanned_at": C.now_iso(),
        "input_dir": p["input_dir"],
        "file_count": len(files),
        "files": [],
        "errors": [],
        "degradations": [],
        "candidates": {},
    }
    for f in files:
        path = f["path"]
        kind = C.classify(path)
        rec = {"path": path, "relpath": f["relpath"], "kind": kind,
               "bytes": f["bytes"], "status": "ok", "error": None,
               "skipped": f.get("skipped"), "meta": None, "scan": None}
        if f.get("skipped"):
            rec["status"] = "skipped"
            rec["error"] = "oversized(>%d bytes)" % C.MAX_FILE_BYTES
            ev["degradations"].append({"file": f["relpath"], "reason": rec["error"]})
            ev["files"].append(rec)
            continue
        if kind == "text":
            txt, meta = C.read_text_safe(path)
            rec["meta"] = meta
            if txt is None:
                rec["status"] = "error"
                rec["error"] = meta.get("error")
                ev["errors"].append({"file": f["relpath"], "reason": meta.get("error")})
            else:
                rec["scan"] = C.scan_text(txt)
        elif kind == "pdf":
            txt, err = C.pdf_to_text(path)
            if txt is None:
                rec["status"] = "needs_tool" if err else "error"
                rec["error"] = err
                ev["degradations"].append({"file": f["relpath"], "reason": err})
            else:
                rec["scan"] = C.scan_text(txt)
        elif kind in ("image", "office"):
            rec["status"] = "needs_tool"
            rec["error"] = "requires OCR/office-renderer; not auto-extracted"
            ev["degradations"].append({"file": f["relpath"], "reason": rec["error"]})
        else:
            rec["status"] = "skipped"
            rec["error"] = "unrecognized file type"
            ev["degradations"].append({"file": f["relpath"], "reason": rec["error"]})
        ev["files"].append(rec)

    # Aggregate candidate anchors for the model to verify (first material wins).
    for rec in ev["files"]:
        if rec.get("scan"):
            ev["candidates"] = rec["scan"]
            break
    C.write_json_atomic(os.path.join(p["work_dir"], "evidence.json"), ev)
    _emit({"ok": True, "action": "evidence",
           "out": os.path.join(p["work_dir"], "evidence.json"),
           "files": len(ev["files"]),
           "texts": sum(1 for r in ev["files"] if r.get("scan")),
           "degradations": len(ev["degradations"])})
    return 0


def _collect_source_text(p):
    """Build a searchable text blob from evidence.json's scanned files.

    The blob is built from candidate claims, sections, numbers and citations;
    it is used only to give the model a cheap backtracking hint for quotes.
    """
    parts = []
    ev_path = os.path.join(p["work_dir"], "evidence.json")
    ev, _err = C.read_json_safe(ev_path)
    if ev:
        for rec in ev.get("files", []):
            scan = rec.get("scan")
            if not scan:
                continue
            for key in ("sections", "claims", "numbers", "citations"):
                for item in scan.get(key) or []:
                    if isinstance(item, str) and item.strip():
                        parts.append(item.strip())
    return "\n".join(parts)


def cmd_verify(p):
    """Validate result.json contract; write verify_report.json."""
    os.makedirs(p["output_dir"], exist_ok=True)
    res, err = C.read_json_safe(p["result_json"])
    report = {"verified_at": C.now_iso(), "result": p["result_json"],
              "checks": [], "fail": [], "warn": [], "ok": True}
    if res is None:
        report["fail"].append("result.json unreadable: %s" % err)
        report["ok"] = False
        C.write_json_atomic(os.path.join(p["output_dir"], "verify_report.json"), report)
        _emit({"ok": False, "action": "verify", "fail": len(report["fail"]),
               "warn": len(report["warn"])})
        return 0

    for fld in ("case_id", "status", "sources", "uncertainty", "change_log"):
        ok = fld in res
        report["checks"].append({"check": "field:%s" % fld, "pass": bool(ok)})
        if not ok:
            report["fail"].append("missing field: %s" % fld)
    if res.get("case_id") in (None, ""):
        report["fail"].append("case_id empty")
    if res.get("status") == "in_progress":
        report["warn"].append("status still in_progress (should be final before handoff)")
    if res.get("status") not in ("complete", "partial", "insufficient_evidence",
                                 "input_error", "in_progress"):
        report["warn"].append("unrecognized status: %r" % res.get("status"))

    sources = res.get("sources") or []
    report["checks"].append({"check": "sources_count>0",
                             "pass": bool(len(sources) > 0),
                             "detail": "%d sources" % len(sources)})
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            report["warn"].append("sources[%d] not an object" % i)
            continue
        if not (s.get("locator") and s.get("quote")):
            report["warn"].append("sources[%d] missing locator or quote" % i)

    # Quote-backtracking: warn (not fail) when a quote cannot be located in the
    # scanned text. The model may have normalized typography; a miss is a hint
    # for the human author to re-check, not a hard contract violation.
    blob = _collect_source_text(p)
    norm_blob = C.normalize_ws(blob)
    missed = 0
    for s in sources:
        quote = s.get("quote") if isinstance(s, dict) else None
        if not quote:
            continue
        if C.normalize_ws(quote) and C.normalize_ws(quote) not in norm_blob:
            missed += 1
    report["checks"].append({"check": "quote_backtrack",
                             "pass": missed == 0,
                             "detail": "%d/%d quotes not located in scanned text" % (
                                 missed, len([s for s in sources
                                              if isinstance(s, dict) and s.get("quote")]))})
    if missed:
        report["warn"].append("%d quotes not backtracked to scanned text" % missed)

    report["ok"] = not report["fail"]
    C.write_json_atomic(os.path.join(p["output_dir"], "verify_report.json"), report)
    _emit({"ok": report["ok"], "action": "verify",
           "fail": len(report["fail"]), "warn": len(report["warn"])})
    return 0


def cmd_full(p):
    rc = cmd_skeleton(p)
    rc2 = cmd_evidence(p)
    rc3 = cmd_verify(p)
    return rc or rc2 or rc3


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", dest="case_json", default=None)
    common.add_argument("--task", dest="task_md", default=None)
    common.add_argument("--input", dest="input_dir", default=None)
    common.add_argument("--work", dest="work_dir", default=None)
    common.add_argument("--output", dest="output_dir", default=None)
    common.add_argument("--result", dest="result_json", default=None)

    ap = argparse.ArgumentParser(
        prog="main.py",
        parents=[common],
        description="manuscript-structure-review Skill entry point")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("skeleton", parents=[common],
                   help="write partial result.json skeleton")
    sub.add_parser("evidence", parents=[common],
                   help="scan materials into evidence.json")
    sub.add_parser("verify", parents=[common],
                   help="validate result.json contract")
    sub.add_parser("full", parents=[common],
                   help="skeleton + evidence + verify")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    p = resolve_paths(a)
    command = a.command or "full"
    try:
        if command == "skeleton":
            return cmd_skeleton(p)
        if command == "evidence":
            return cmd_evidence(p)
        if command == "verify":
            return cmd_verify(p)
        return cmd_full(p)
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        _emit({"ok": False, "action": command, "error": "%s" % exc})
        return 0


if __name__ == "__main__":
    sys.exit(main())
