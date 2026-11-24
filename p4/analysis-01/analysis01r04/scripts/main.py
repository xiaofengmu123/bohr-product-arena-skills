#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry point for the sci-data-cleaning skill.

Usage (all standard-library, deterministic, exit code 0 even on bad input):

    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work

Phases:
  * bootstrap  discover materials and write a valid partial result.json
               skeleton within seconds, so any later failure still leaves a
               loadable, honest deliverable.
  * full       parse the main table, build a non-destructive cleaning workbook
               (source snapshot + cleaned data + per-cell change map), a
               field-level profile, a quality report whose numbers are
               recomputed from the cleaned table, and an evidence-map template.
               It merges back into result.json without overwriting any field
               the Agent already filled.
  * selfcheck  validate result.json and artifacts, re-compute key figures from
               the cleaned table and report pass/fail items. Always exit 0; the
               verdict is written inside the report, not in the exit code.
"""

import argparse
import csv
import datetime
import io
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core

# result.json keeps every original output_contract key plus these bookkeeping
# keys. The contract keys themselves are inserted verbatim.
RESULT_BOOKKEEPING = (
    "case_id", "status", "sources", "uncertainty", "limitations",
    "pending_human", "change_log", "errors", "degradations", "runtime",
    "artifacts", "primary_artifact",
)

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# Heuristic key -> artifact mapping, used only when a contract key is empty.
# Order matters: more specific tokens (evidence / snapshot) are matched before
# the broad "map" token so a key like "evidence_map" is not claimed by
# "change_map".
CONTRACT_ARTIFACT_HINTS = (
    (("evidence", "trace", "proof"), "evidence_map"),
    (("snapshot", "raw_source", "source_snapshot"), "source_snapshot"),
    (("change_map", "cell_audit", "audit_map"), "change_map"),
    (("clean", "table", "data"), "cleaned_data"),
    (("report", "quality", "qc"), "quality_report"),
    (("profile", "stat", "summar"), "data_profile"),
    (("rule", "config", "applied"), "rules_applied"),
    (("selfcheck", "check", "verify"), "selfcheck_report"),
)


def parse_args(argv):
    p = argparse.ArgumentParser(description="sci-data-cleaning deterministic CLI")
    p.add_argument("--phase", required=True,
                   choices=("bootstrap", "full", "selfcheck"))
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p.parse_args(argv)


def makedirs(*dirs):
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def ensure_result(path, case, started):
    """Load an existing result.json if present, else build a skeleton."""
    existing, status = core.read_json(path, {})
    if status == "ok" and isinstance(existing, dict):
        result = existing
        result.setdefault("case_id", case.get("case_id"))
    else:
        result = build_skeleton(case, started)
    return result


def build_skeleton(case, started):
    contract = case.get("output_contract") or {}
    result = {
        "case_id": case.get("case_id"),
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "change_log": [
            {"time": started, "step": "bootstrap",
             "action": "write partial result skeleton before any analysis"},
        ],
        "errors": [],
        "degradations": [],
        "runtime": {"started": started, "finished": None, "seconds": None},
        "artifacts": {},
    }
    # Echo every contract key verbatim; unknown keys are kept, empty values are
    # typed null/[]/{} by the value's own shape.
    for key, val in contract.items():
        if key in result:
            continue
        result[key] = core.contract_default(val)
    if case.get("read_error"):
        result["errors"].append({
            "scope": "input",
            "message": case["read_error"],
            "fatal": False,
        })
    return result


def fill_contract_keys(result, contract, artifacts):
    for key, val in contract.items():
        current = result.get(key)
        empty = current is None or current == [] or current == {}
        if not empty:
            continue
        low = str(key).lower()
        for hints, art in CONTRACT_ARTIFACT_HINTS:
            if any(h in low for h in hints):
                if art in artifacts and artifacts[art]:
                    result[key] = artifacts[art]
                break


def load_material_texts(manifest):
    """Return the text of the task, dictionary, rules and workorder files so
    the Agent can read them once and reason with locators; the script itself
    makes no interpretation beyond discovery."""
    out = {}
    for kind in ("task_md", "dictionary", "rules", "workorder"):
        paths = manifest.get(kind) or []
        if isinstance(paths, str):
            paths = [paths]
        items = []
        for p in paths:
            info = core.read_text_robust(p)
            items.append({
                "path": p,
                "encoding": info["encoding"],
                "degraded": info["degraded"],
                "error": info["error"],
                "text": info["text"],
            })
        out[kind] = items
    return out


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------

def phase_bootstrap(args):
    started = core.now_iso()
    makedirs(args.output, args.work)
    case = core.load_case(args.input)
    out_path = os.path.join(args.output, "result.json")
    result = ensure_result(out_path, case, started)
    # Keep the skeleton's change_log even on re-run; append only if new.
    if not any(e.get("step") == "bootstrap" for e in result.get("change_log", [])):
        result.setdefault("change_log", []).append(
            {"time": started, "step": "bootstrap",
             "action": "write partial result skeleton before any analysis"})
    core.write_json(out_path, result)
    core.write_json(os.path.join(args.work, "manifest.json"),
                    {"started": started, "case": {
                        "case_id": case["case_id"],
                        "read_error": case["read_error"],
                        "output_contract_keys": list(case["output_contract"].keys()),
                        "manifest": case["manifest"],
                    }})
    return result


# ---------------------------------------------------------------------------
# full
# ---------------------------------------------------------------------------

def build_cleaning_workbook(case, work):
    manifest = case["manifest"]
    data_paths = manifest["data"]
    tables = []
    errors = []
    for p in data_paths:
        t = core.read_delimited(p)
        t["path"] = p
        if t["error"]:
            errors.append({"scope": "data_file", "path": p, "message": t["error"]})
            continue
        tables.append(t)
    if not tables:
        return None, errors, {}

    # Main table = first successfully parsed data file; extra tables are kept
    # in the manifest so the Agent can align them by primary key.
    main = tables[0]
    headers = main["headers"]
    rows = main["rows"]

    # Non-destructive workbook: verbatim snapshot + row_id, cleaned copy starts
    # identical; the Agent fills the change map with explicit decisions.
    row_ids = ["R%06d" % (i + 1) for i in range(len(rows))]

    snapshot = io.StringIO()
    w = csv.writer(snapshot, lineterminator="\n")
    w.writerow(["__row_id"] + list(headers))
    for rid, row in zip(row_ids, rows):
        w.writerow([rid] + list(row))

    cleaned = io.StringIO()
    w = csv.writer(cleaned, lineterminator="\n")
    w.writerow(["__row_id"] + list(headers))
    for rid, row in zip(row_ids, rows):
        w.writerow([rid] + list(row))

    profile = core.profile_table(headers, rows)

    # Write only the raw material the Agent needs; the Agent then records its
    # decisions in change_map.csv / rules_applied.json and reruns full/selfcheck.
    artifact_paths = {
        "source_snapshot": os.path.join(work, "source_snapshot.csv"),
        "cleaned_data": os.path.join(work, "cleaned_data.csv"),
        "change_map": os.path.join(work, "change_map.csv"),
        "data_profile": os.path.join(work, "data_profile.json"),
        "evidence_map": os.path.join(work, "evidence_map.json"),
        "rules_applied": os.path.join(work, "rules_applied.json"),
        "quality_report": os.path.join(work, "quality_report.md"),
    }
    with open(artifact_paths["source_snapshot"], "w", encoding="utf-8", newline="") as f:
        f.write(snapshot.getvalue())
    with open(artifact_paths["cleaned_data"], "w", encoding="utf-8", newline="") as f:
        f.write(cleaned.getvalue())
    # Empty change map header only; the Agent appends decisions.
    with open(artifact_paths["change_map"], "w", encoding="utf-8", newline="") as f:
        f.write("row_id,column,original_value,cleaned_value,action,reason,rule_locator,quote\n")
    core.write_json(artifact_paths["data_profile"], {
        "source_path": main["path"],
        "encoding": main["encoding"],
        "delimiter": main["delimiter"],
        "degraded": main["degraded"],
        "profile": profile,
    })
    core.write_json(artifact_paths["evidence_map"], {
        "decisions": [],
        "note": "Agent must add one entry per key decision: locator + verbatim quote.",
    })
    core.write_json(artifact_paths["rules_applied"], {
        "missing_sentinels": sorted(core.DEFAULT_MISSING_SENTINELS),
        "not_detected_tokens": sorted(core.DEFAULT_NOT_DETECTED_TOKENS),
        "below_lod_tokens": sorted(core.DEFAULT_BELOW_LOD_TOKENS),
        "qc_bounds_candidates": [],
        "unit_conversions": [],
        "primary_keys": [],
        "note": "Agent confirms bounds/keys/unit factors with locators before use.",
    })
    # Quality report numbers are recomputed from the profile, not hand-written.
    report = render_quality_report(profile, main, artifact_paths["cleaned_data"])
    with open(artifact_paths["quality_report"], "w", encoding="utf-8") as f:
        f.write(report)

    return {"tables": tables, "profile": profile, "row_ids": row_ids,
            "artifacts": artifact_paths}, errors, profile


def render_quality_report(profile, main, cleaned_path):
    lines = []
    lines.append("# Data quality report (auto-recomputed)")
    lines.append("")
    lines.append("- source: `%s`" % main["path"])
    lines.append("- encoding: `%s`, delimiter: `%r`" % (main["encoding"], main["delimiter"]))
    lines.append("- rows: %d, columns: %d" % (profile["row_count"], len(profile["headers"])))
    lines.append("- recomputed from: `%s`" % cleaned_path)
    lines.append("")
    lines.append("| field | present | true_zero | not_detected | below_lod | entry_missing | missing_rate | numeric n / min / max |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for col in profile["columns"]:
        c = col["counts"]
        ns = col.get("numeric_stats")
        num = "-"
        if ns:
            num = "%d / %g / %g" % (ns["n"], ns["min"], ns["max"])
        lines.append("| %s | %d | %d | %d | %d | %d | %.4f | %s |" % (
            col["header"], c["present"], c["true_zero"], c["not_detected"],
            c["below_lod"], c["entry_missing"], col["missing_rate"], num))
    lines.append("")
    lines.append("_All figures above are computed by scripts/core_impl.profile_table; "
                 "do not edit by hand. Any Agent-added conclusion must cite the "
                 "corresponding row/column locator._")
    return "\n".join(lines) + "\n"


def phase_full(args):
    started = core.now_iso()
    makedirs(args.output, args.work)
    case = core.load_case(args.input)
    out_path = os.path.join(args.output, "result.json")
    result = ensure_result(out_path, case, started)

    workbook, errors, profile = build_cleaning_workbook(case, args.work)
    result["errors"] = [e for e in result.get("errors", []) if not isinstance(e, dict)]
    if errors:
        result.setdefault("errors", []).extend(errors)

    artifacts = {}
    if workbook:
        artifacts = workbook["artifacts"]
        # Copy work artifacts to the output directory (final, editable results).
        for name, src in artifacts.items():
            dst = os.path.join(args.output, os.path.basename(src))
            try:
                with open(src, "r", encoding="utf-8") as f_in:
                    with open(dst, "w", encoding="utf-8") as f_out:
                        f_out.write(f_in.read())
                artifacts[name] = dst
            except OSError as exc:
                result.setdefault("errors", []).append(
                    {"scope": "artifact", "name": name, "message": str(exc)})

    result["artifacts"] = {k: v for k, v in artifacts.items() if v}
    result["primary_artifact"] = artifacts.get("cleaned_data") or artifacts.get("source_snapshot")
    fill_contract_keys(result, case.get("output_contract") or {}, artifacts)

    if not workbook:
        result["status"] = "insufficient_evidence" if not result.get("errors") else "input_error"
        result.setdefault("pending_human", []).append(
            {"item": "main tabular data not parsed",
             "reason": "no parseable data file found or empty table"})

    finish_runtime(result, started)
    result.setdefault("change_log", []).append(
        {"time": started, "step": "full",
         "action": "built non-destructive cleaning workbook, profile and auto-recomputed quality report"})
    core.write_json(out_path, result)
    return result


def finish_runtime(result, started):
    finished = core.now_iso()
    rt = result.setdefault("runtime", {})
    rt["started"] = rt.get("started") or started
    rt["finished"] = finished
    rt["seconds"] = None
    try:
        s = datetime.datetime.fromisoformat(rt["started"])
        e = datetime.datetime.fromisoformat(finished)
        rt["seconds"] = round((e - s).total_seconds(), 1)
    except (ValueError, TypeError):
        rt["seconds"] = None


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------

def recompute_from_cleaned(cleaned_path):
    """Recompute row/column counts and per-column numeric stats from the
    cleaned CSV so the selfcheck never trusts the profile."""
    t = core.read_delimited(cleaned_path)
    if t["error"]:
        return {"error": t["error"]}
    headers = t["headers"]
    rows = t["rows"]
    numeric_by_col = {}
    for ci, h in enumerate(headers):
        vals = [r[ci] if ci < len(r) else "" for r in rows]
        nums = []
        for v in vals:
            cls = core.classify_missing(v)
            if cls != "present":
                continue
            val, unit, ok, _ = core.parse_number(v)
            if ok and unit == "":
                nums.append(val)
        if nums:
            numeric_by_col[h] = {
                "n": len(nums), "min": min(nums), "max": max(nums),
                "mean": statistics.fmean(nums), "median": statistics.median(nums),
            }
    return {"headers": headers, "row_count": len(rows), "numeric_by_col": numeric_by_col}


def phase_selfcheck(args):
    started = core.now_iso()
    makedirs(args.output, args.work)
    out_path = os.path.join(args.output, "result.json")
    report = {"time": started, "passed": True, "checks": [], "warnings": []}

    result, status = core.read_json(out_path, {})
    if status != "ok" or not isinstance(result, dict):
        report["passed"] = False
        report["checks"].append({"item": "result.json readable", "ok": False,
                                 "detail": "missing or invalid result.json"})
    else:
        report["checks"].append({"item": "result.json readable", "ok": True})
        report["checks"].append({"item": "case_id present", "ok": bool(result.get("case_id")),
                                 "detail": str(result.get("case_id"))})
        st = result.get("status")
        report["checks"].append({"item": "status valid", "ok": st in STATUS_VALUES,
                                 "detail": str(st)})
        contract = (result.get("_contract_keys") or [])
        if not contract:
            # Re-load contract from case.json when available.
            case = core.load_case(args.input)
            contract = list(case.get("output_contract") or {})
        missing_keys = [k for k in contract if k not in result]
        report["checks"].append({"item": "all output_contract keys present",
                                 "ok": not missing_keys,
                                 "detail": "missing=%s" % (missing_keys or "none")})
        for need in ("sources", "uncertainty", "change_log"):
            ok = isinstance(result.get(need), list)
            report["checks"].append({"item": "%s present (list)" % need, "ok": ok})
        if st != "ok":
            has_lim = isinstance(result.get("limitations"), list) and bool(result.get("limitations"))
            report["checks"].append({"item": "non-ok status carries limitations",
                                     "ok": has_lim,
                                     "detail": "status=%s" % st})
            if not has_lim:
                report["passed"] = False
        # Every source entry must carry a locator and a verbatim quote.
        bad_sources = [s for s in result.get("sources", [])
                       if not isinstance(s, dict) or not s.get("locator") or not s.get("quote")]
        report["checks"].append({"item": "sources have locator+quote",
                                 "ok": not bad_sources,
                                 "detail": "bad=%d" % len(bad_sources)})
        if bad_sources:
            report["passed"] = False
        # Every uncertainty entry needs a claim + basis.
        bad_unc = [u for u in result.get("uncertainty", [])
                   if not isinstance(u, dict) or not u.get("claim") or not u.get("basis")]
        report["checks"].append({"item": "uncertainty entries have claim+basis",
                                 "ok": not bad_unc, "detail": "bad=%d" % len(bad_unc)})
        # change_log entries need time/step/action.
        bad_log = [e for e in result.get("change_log", [])
                   if not isinstance(e, dict) or not all(k in e for k in ("time", "step", "action"))]
        report["checks"].append({"item": "change_log entries complete",
                                 "ok": not bad_log, "detail": "bad=%d" % len(bad_log)})

    # Recompute from cleaned data and compare with the profile/quality report.
    cleaned_path = None
    if isinstance(result, dict):
        cleaned_path = (result.get("artifacts") or {}).get("cleaned_data")
    if cleaned_path and os.path.exists(cleaned_path):
        recomputed = recompute_from_cleaned(cleaned_path)
        report["recomputed"] = recomputed
        profile_path = (result.get("artifacts") or {}).get("data_profile")
        if profile_path and os.path.exists(profile_path):
            prof, pstat = core.read_json(profile_path, {})
            if pstat == "ok" and isinstance(prof, dict):
                pcols = {c["header"]: c for c in prof.get("profile", {}).get("columns", [])}
                mismatches = []
                for h, stats in recomputed.get("numeric_by_col", {}).items():
                    pc = pcols.get(h)
                    if pc and pc.get("numeric_stats"):
                        pn = pc["numeric_stats"]
                        if abs(pn["n"] - stats["n"]) > 0 or abs(pn["mean"] - stats["mean"]) > 1e-9:
                            mismatches.append(h)
                report["checks"].append({
                    "item": "quality-report numbers match cleaned table",
                    "ok": not mismatches,
                    "detail": "mismatch=%s" % (mismatches or "none")})
                if mismatches:
                    report["passed"] = False
            else:
                report["warnings"].append("data_profile.json missing/unreadable; cannot cross-check")
        else:
            report["warnings"].append("data_profile.json missing; cannot cross-check")
    else:
        report["warnings"].append("cleaned_data artifact missing; numeric cross-check skipped")

    core.write_json(os.path.join(args.output, "selfcheck_report.json"), report)
    with open(os.path.join(args.output, "selfcheck_report.md"), "w", encoding="utf-8") as f:
        f.write(render_selfcheck_md(report))

    # Record the verdict inside result.json without erasing other content.
    if isinstance(result, dict):
        result.setdefault("artifacts", {})["selfcheck_report"] = os.path.join(
            args.output, "selfcheck_report.json")
        result.setdefault("errors", [])
        if not report["passed"]:
            result["errors"].append({
                "scope": "selfcheck",
                "message": "selfcheck did not pass; see selfcheck_report.json",
                "fatal": False,
            })
        finish_runtime(result, started)
        core.write_json(out_path, result)
    return report


def render_selfcheck_md(report):
    lines = ["# Selfcheck report", "",
             "- time: `%s`" % report["time"],
             "- passed: **%s**" % ("yes" if report["passed"] else "no"), ""]
    for c in report.get("checks", []):
        mark = "[x]" if c.get("ok") else "[ ]"
        lines.append("- %s %s%s" % (mark, c["item"],
                                    (" — %s" % c["detail"]) if c.get("detail") else ""))
    for w in report.get("warnings", []):
        lines.append("- WARN: %s" % w)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.phase == "bootstrap":
            phase_bootstrap(args)
        elif args.phase == "full":
            phase_full(args)
        elif args.phase == "selfcheck":
            phase_selfcheck(args)
    except Exception as exc:  # never leave a crashed process; degrade honestly
        try:
            makedirs(args.output, args.work)
            started = core.now_iso()
            case = core.load_case(args.input)
            out_path = os.path.join(args.output, "result.json")
            result = ensure_result(out_path, case, started)
            result["status"] = "input_error"
            result.setdefault("errors", []).append(
                {"scope": "pipeline", "message": "%s: %s" % (type(exc).__name__, exc),
                 "fatal": True})
            result.setdefault("degradations", []).append(
                {"step": args.phase, "reason": "unhandled exception",
                 "detail": "%s" % exc})
            core.write_json(out_path, result)
        except Exception:
            pass
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
