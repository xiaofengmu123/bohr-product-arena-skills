#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the sci-data-cleaning skill.

Phases (all standard-library, deterministic, exit code 0 even on bad input):
    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work

Guarantees:
  * bootstrap writes a valid result.json skeleton immediately (status=partial),
    so any later failure still leaves a loadable, honest deliverable.
  * full discovers materials, builds plan.json (structured intermediate
    representation), executes conservative cleaning, writes cleaned_data.csv +
    raw_snapshot.csv + field_model.json + quality_report.md + evidence_map.json,
    then finalizes result.json with all output_contract keys.
  * selfcheck re-derives report numbers from cleaned_data.csv and validates the
    result without inventing scores.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")


def build_skeleton(case, started, phase="bootstrap"):
    result = {
        "case_id": case.get("case_id"),
        "status": "partial",
        "runtime": {"started": started, "finished": core.now_iso(),
                    "phase": phase, "seconds": None},
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"time": core.now_iso(), "step": phase,
             "action": "wrote result.json skeleton (status=partial) before analysis"}
        ],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "artifacts": {},
    }
    result = core.fill_contract_keys(result, case.get("output_contract") or {}, {})
    if case.get("read_error"):
        result["errors"].append({"step": phase, "detail": case["read_error"]})
        result["status"] = "input_error"
    return result


def run_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = core.now_iso()
    case = core.load_case(args.input)
    result = build_skeleton(case, started, "bootstrap")
    # persist discovered manifest as an intermediate input model
    core.write_json(os.path.join(args.work, "manifest.json"),
                    {"case_id": case["case_id"], "manifest": case["manifest"],
                     "output_contract": case["output_contract"]})
    _write_result(args.output, result)
    return result


def run_full(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = core.now_iso()
    case = core.load_case(args.input)
    # start from skeleton so contract keys are always present
    result = build_skeleton(case, started, "full")
    try:
        if not case["manifest"]["data"]:
            result["status"] = "insufficient_evidence"
            result["pending_human"].append(
                {"topic": "missing-data", "detail": "no tabular data file discovered"})
            result["errors"].append({"step": "full",
                                     "detail": "no tabular data file in /input"})
            _write_result(args.output, result)
            return result

        plan, data_file, data_text, docs, data_info = core.build_plan(case, args.input)
        # snapshot original parsed table before any modification
        parsed_src = core.parse_table(data_text)
        raw_headers = parsed_src["headers"] or [f["name"] for f in plan["fields"]]
        core.write_csv(os.path.join(args.output, "raw_snapshot.csv"),
                       raw_headers, parsed_src["rows"])

        # persist the structured intermediate representation
        core.write_json(os.path.join(args.output, "plan.json"), plan)
        core.write_json(os.path.join(args.work, "plan.json"), plan)

        exec_out = core.execute_plan(plan, data_text)
        core.write_csv(os.path.join(args.output, "cleaned_data.csv"),
                       exec_out["headers"], exec_out["kept_rows"])
        if exec_out["removed_rows"]:
            core.write_csv(os.path.join(args.output, "removed_duplicates.csv"),
                           ["source_row", "key", "reason"],
                           [{"source_row": r["source_row"], "key": json.dumps(r["key"], ensure_ascii=False),
                             "reason": r["reason"]} for r in exec_out["removed_rows"]])
        core.write_json(os.path.join(args.output, "field_model.json"),
                        exec_out["field_model"])

        report_md = core.render_quality_report(exec_out["field_model"], plan, {
            "conversion_log": exec_out["conversion_log"],
            "anomaly_log": exec_out["anomaly_log"],
            "removed_rows": exec_out["removed_rows"]})
        with open(os.path.join(args.output, "quality_report.md"), "w",
                  encoding="utf-8") as f:
            f.write(report_md)

        # evidence map: every decision -> locator + verbatim quote
        evidence = build_evidence(plan, docs, case, args.input)
        core.write_json(os.path.join(args.output, "evidence_map.json"), evidence)

        artifacts = {
            "cleaned_data": "cleaned_data.csv",
            "raw_snapshot": "raw_snapshot.csv",
            "plan": "plan.json",
            "field_model": "field_model.json",
            "quality_report": "quality_report.md",
            "evidence_map": "evidence_map.json",
        }
        result["artifacts"] = artifacts
        result["status"] = "ok" if not case.get("read_error") else "partial"
        result["sources"] = evidence
        result["change_log"].append(
            {"time": core.now_iso(), "step": "full",
             "action": "built plan.json and executed conservative cleaning; wrote 6 artifacts"})
        result = core.fill_contract_keys(result, case["output_contract"], artifacts)

        # honest uncertainty / pending
        for f in plan["fields"]:
            if f.get("conversion_factor") is None and f.get("convert_to"):
                result["pending_human"].append(
                    {"topic": "unit-conversion", "field": f["name"],
                     "detail": "cannot safely convert %s -> %s without molar mass" % (
                         f.get("unit"), f.get("convert_to"))})
                result["uncertainty"].append(
                    {"topic": "unit-conversion", "field": f["name"],
                     "detail": "molar cross-unit conversion left unapplied",
                     "basis": "plan.json conversion_note"})
            if f["role"] == "measure" and f.get("qc_min") is None and f.get("qc_max") is None:
                result["uncertainty"].append(
                    {"topic": "qc-bounds-missing", "field": f["name"],
                     "detail": "no physical/QC bounds found; anomaly flagging is not applied for this field",
                     "basis": "rules evidence absent in plan.json"})
        for doc in docs.get("rules", []) + docs.get("dictionary", []) + docs.get("workorder", []):
            if doc.get("degraded") or doc.get("error"):
                result["degradations"].append(
                    {"step": "full", "detail": "document read degraded: %s" % doc["path"]})
        if data_info and (data_info.get("degraded") or data_info.get("error") or data_info.get("truncated")):
            result["degradations"].append(
                {"step": "full", "detail": "data file read degraded (encoding=%s, size=%s): %s" % (
                    data_info.get("encoding"), data_info.get("size"),
                    data_info.get("error") or "fallback decoding applied")})

        # Honest status: any degradation of the main data file lowers ok -> partial.
        if result.get("degradations"):
            result["status"] = "partial"
        if result.get("errors"):
            result["status"] = "partial"

        result["runtime"]["finished"] = core.now_iso()
        result["runtime"]["phase"] = "full"
        _write_result(args.output, result)
        return result
    except Exception as e:  # never crash the harness
        result["status"] = "partial"
        result["errors"].append({"step": "full", "detail": "%s: %s" % (type(e).__name__, e)})
        result["runtime"]["finished"] = core.now_iso()
        _write_result(args.output, result)
        return result


def build_evidence(plan, docs, case, input_dir):
    evidence = []
    for f in plan["fields"]:
        for kind in ("dictionary", "rules", "workorder"):
            for e in f["evidence"].get(kind, []):
                evidence.append({
                    "field": f["name"], "kind": kind,
                    "locator": e["locator"], "quote": e["quote"],
                    "note": "evidence for per-field plan entry"})
    # document-level evidence
    for kind in ("dictionary", "rules", "workorder"):
        for doc in docs.get(kind, []):
            evidence.append({
                "field": None, "kind": kind,
                "locator": core.rel_locator(doc["path"], input_dir) + "#full",
                "quote": (doc["text"] or "")[:200],
                "note": "document read (encoding=%s)" % doc.get("encoding")})
    evidence.append({
        "field": None, "kind": "global",
        "locator": plan["data_file"] or "unknown",
        "quote": "data_file=%s delimiter=%s header_fields=%d" % (
            plan.get("data_file"), plan.get("delimiter"), plan.get("n_header_fields")),
        "note": "main tabular source identified by discovery"})
    return evidence


def run_selfcheck(args):
    res = core.selfcheck(args.output, args.input)
    out = {"checked_at": core.now_iso(), "ok": res["ok"], "checks": res["checks"]}
    core.write_json(os.path.join(args.output, "selfcheck.json"), out)
    return out


def _write_result(output_dir, result):
    core.write_json(os.path.join(output_dir, "result.json"), result)


def main(argv=None):
    p = argparse.ArgumentParser(description="sci-data-cleaning skill CLI")
    p.add_argument("--phase", required=True,
                   choices=("bootstrap", "full", "selfcheck", "selftest"),
                   help="pipeline phase")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)

    if args.phase == "bootstrap":
        run_bootstrap(args)
    elif args.phase == "full":
        run_full(args)
    elif args.phase == "selfcheck":
        run_selfcheck(args)
    elif args.phase == "selftest":
        import selftest
        sys.exit(0 if selftest.run() else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
