#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the sci-data-cleaning-contract skill.

Usage (standard library only, deterministic, exit code 0 even on bad input):

    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work

Guarantees
----------
* bootstrap writes a valid result.json skeleton within seconds (status=partial)
  with every output_contract key backfilled to a type-aware default, so any
  later failure still leaves a loadable, honest partial deliverable.
* full discovers materials, parses the main tabular source, runs a conservative
  deterministic normalization pass, and writes cleaned_data.csv,
  missing_flags.csv, raw_snapshot.*, data_profile.json, quality_report.md,
  evidence_map.json and change_log.md; it then re-aligns result.json to the
  output_contract and refreshes contract_audit.
* selfcheck validates result.json, the output_contract alignment, and artifact
  existence, and always exits 0 with a machine-readable summary line.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core


# --------------------------------------------------------------------------
# Phase implementations
# --------------------------------------------------------------------------
def _ensure_dirs(args):
    for d in (args.output, args.work):
        os.makedirs(d, exist_ok=True)


def phase_bootstrap(args):
    _ensure_dirs(args)
    started = core.now_iso()
    case = core.load_case(args.input)
    result = core.build_skeleton(case, started)
    result["runtime"]["finished"] = core.now_iso()
    core.write_json(os.path.join(args.output, "result.json"), result)
    state = {"case": case, "started": started}
    core.write_json(os.path.join(args.work, "state.json"), state)
    print("bootstrap: result.json skeleton written; status={}".format(result["status"]))
    print("bootstrap: contract keys={}".format(len(case.get("output_contract") or {})))
    return result


def phase_full(args):
    _ensure_dirs(args)
    started = core.now_iso()
    case = core.load_case(args.input)
    contract = case.get("output_contract") or {}
    manifest = case.get("manifest") or core.discover_inputs(args.input)

    result = core.load_existing_result(args.output)
    if not isinstance(result, dict):
        result = core.build_skeleton(case, started)

    errors = list(result.get("errors", []))
    degradations = list(result.get("degradations", []))
    artifacts = {}

    # Choose the main tabular data source.
    data_files = list(manifest.get("data", []))
    if not data_files:
        for f in manifest.get("other", []):
            if f.lower().endswith(core.TABULAR_SUFFIXES):
                data_files.append(f)

    if data_files:
        src = data_files[0]
        tbl = core.parse_table(src)
        if tbl.get("error"):
            errors.append("parse_table failed for {}: {}".format(src, tbl["error"]))
            result["status"] = "partial"
        else:
            if tbl.get("degraded"):
                degradations.append("encoding degraded for {} (read as {})".format(
                    src, tbl.get("encoding")))
            profile = core.profile_table(tbl)
            headers, cleaned_rows, flag_rows = core.build_cleaned_table(tbl)

            cleaned_path = os.path.join(args.output, "cleaned_data.csv")
            flags_path = os.path.join(args.output, "missing_flags.csv")
            profile_path = os.path.join(args.output, "data_profile.json")
            report_path = os.path.join(args.output, "quality_report.md")
            evidence_path = os.path.join(args.output, "evidence_map.json")
            change_path = os.path.join(args.output, "change_log.md")
            snap_ext = os.path.splitext(src)[1] or ".csv"
            snap_path = os.path.join(args.output, "raw_snapshot" + snap_ext)

            core.write_csv(cleaned_path, headers, cleaned_rows)
            core.write_csv(flags_path, headers, flag_rows)
            core.write_json(profile_path, profile)
            core.write_text(report_path, core.render_quality_report(profile, tbl, src))
            snap = core.copy_snapshot(src, snap_path)
            if snap.get("error"):
                degradations.append("raw_snapshot copy failed: {}".format(snap["error"]))
            elif snap.get("truncated"):
                degradations.append("raw_snapshot truncated at {} bytes".format(
                    core.MAX_FILE_BYTES))

            artifacts = {
                "cleaned_data": cleaned_path,
                "missing_flags": flags_path,
                "data_profile": profile_path,
                "quality_report": report_path,
                "evidence_map": evidence_path,
                "change_log_md": change_path,
                "raw_snapshot": snap_path,
            }
            core.write_json(evidence_path, core.build_evidence_map(manifest, src, artifacts))
            core.write_text(change_path, _render_change_log_md(result.get("change_log", [])))
    else:
        errors.append("no tabular data file found under input directory")
        result["status"] = "insufficient_evidence"

    # Align result.json to the output_contract (one-to-one key backfill).
    core.fill_contract_keys(result, contract, artifacts)
    result["artifacts"] = artifacts
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"]["finished"] = core.now_iso()
    result["change_log"].append({
        "time": core.now_iso(), "step": "full",
        "action": "deterministic discovery + conservative normalization pass; "
                  "wrote cleaned_data.csv, missing_flags.csv, data_profile.json, "
                  "quality_report.md, evidence_map.json, change_log.md, raw_snapshot",
        "why": "provide machine-recomputable numbers before Agent-level judgement",
    })
    core.refresh_contract_audit(result, contract)

    # Do not upgrade status to ok automatically: ok requires Agent verification.
    if result.get("status") not in ("ok", "input_error", "insufficient_evidence"):
        result["status"] = "partial"

    core.write_json(os.path.join(args.output, "result.json"), result)
    core.write_json(os.path.join(args.work, "state.json"), {"case": case, "started": started})
    print("full: artifacts written; status={}".format(result["status"]))
    print("full: contract filled={}/{}".format(
        len(result["contract_audit"].get("filled", [])), len(contract)))
    return result


def phase_selfcheck(args):
    _ensure_dirs(args)
    case = core.load_case(args.input)
    contract = case.get("output_contract") or {}
    result = core.load_existing_result(args.output)
    issues = []
    if result is None:
        issues.append("result.json missing or unreadable")
    else:
        issues = core.validate_result(result, contract, args.output)
    summary = {
        "case_id": (result or {}).get("case_id"),
        "status": (result or {}).get("status"),
        "contract_alignment": "ok" if result is not None and
                              all(k in result for k in contract) else "fail",
        "n_contract_keys": len(contract),
        "n_issues": len(issues),
        "issues": issues,
        "exit_code": 0,
    }
    core.write_json(os.path.join(args.work, "selfcheck.json"), summary)
    for i in issues:
        print("selfcheck issue: {}".format(i))
    print("selfcheck summary: contract={} issues={} status={}".format(
        summary["contract_alignment"], len(issues), summary["status"]))
    status_word = "pass" if not issues else "warn"
    print("SELFCHECK_STATUS={} SELFCHECK_ISSUES={}".format(status_word, len(issues)))
    return summary


def _render_change_log_md(changes):
    lines = ["# 变更日志（change_log）", ""]
    if not changes:
        lines.append("（暂无变更记录）")
    for ch in changes:
        lines.append("- {} | step={} | {}".format(
            ch.get("time", "?"), ch.get("step", "?"), ch.get("action", "")))
        if ch.get("why"):
            lines.append("  - 原因：{}".format(ch["why"]))
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="sci-data-cleaning-contract CLI (deterministic helper)")
    parser.add_argument("--phase", required=True,
                        choices=["bootstrap", "full", "selfcheck"])
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    try:
        if args.phase == "bootstrap":
            phase_bootstrap(args)
        elif args.phase == "full":
            phase_full(args)
        elif args.phase == "selfcheck":
            phase_selfcheck(args)
    except Exception as exc:  # never crash: degrade to a skeleton where possible
        try:
            _ensure_dirs(args)
            started = core.now_iso()
            case = core.load_case(args.input)
            result = core.build_skeleton(case, started)
            result["errors"] = list(result.get("errors", [])) + [str(exc)]
            result["runtime"]["finished"] = core.now_iso()
            core.write_json(os.path.join(args.output, "result.json"), result)
        except Exception:
            pass
        print("fatal-but-handled: {}".format(exc))
        print("SELFCHECK_STATUS=warn SELFCHECK_ISSUES=1")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
