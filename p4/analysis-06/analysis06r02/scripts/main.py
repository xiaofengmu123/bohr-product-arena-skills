#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main: deterministic read-only scientific DB query & analysis orchestrator.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work

Early-skeleton design (variant focus):
  1. Phase 0 writes a *legal* result.json skeleton (case_id + every
     output_contract key filled with null + status/sources/uncertainty/
     change_log) BEFORE any database is opened. An interruption at any later
     point therefore never leaves an empty or invalid result.json.
  2. Each subsequent phase only enriches the result and appends to change_log;
     the run never deletes already-valid content.
  3. Agent-provided increments under /app/work (queries.json, answers.json,
     annotations.json) are merged idempotently: re-running recomputes the same
     numbers and only adopts the latest agent inputs.

Standard library only. No prepared answers; every number comes from the
authorised snapshot at run time.
"""

import argparse
import csv as _csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as C

VERSION = C.VERSION
STATUS_SET = ("ok", "partial", "insufficient_evidence", "input_error")
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
_agent_sources = []


# --------------------------------------------------------------------------- #
# argument / io helpers
# --------------------------------------------------------------------------- #
def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Read-only scientific DB query & analysis pipeline.")
    p.add_argument("--input", default=DEFAULT_INPUT, help="input directory")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="output directory")
    p.add_argument("--work", default=DEFAULT_WORK, help="work directory")
    p.add_argument("--queries", default=None,
                   help="optional path to tagged queries JSON")
    p.add_argument("--version", action="version", version="main.py " + VERSION)
    return p.parse_args(argv)


def read_json_soft(path):
    if not path or not Path(path).exists():
        return None, "missing file: %s" % path
    txt = C.read_text(path)
    if txt is None:
        return None, "unreadable/oversized file: %s" % path
    try:
        return json.loads(txt), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, "bad JSON in %s: %s" % (path, e)


def ensure_dirs(*dirs):
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def log_step(change_log, step, change, status="partial"):
    change_log.append({"ts": C.utc_now(), "step": step,
                       "change": change, "status": status})


def add_src(sources, claim, locator, quote, role):
    sources.append({"claim": claim, "locator": locator, "quote": quote,
                    "role": role})


# --------------------------------------------------------------------------- #
# input discovery / case reading
# --------------------------------------------------------------------------- #
def read_case(input_dir):
    """Return (case_id, contract_fields, contract_raw, errors, degradations)."""
    errors, degradations = [], []
    inp = C.discover_input(input_dir)
    case_id = None
    contract_fields = []
    contract_raw = None
    case_path = inp.get("case_json") or (inp.get("other_json")[:1] or [None])[0]

    if case_path is None:
        # no case.json at all -> still produce skeleton later
        return None, [], None, errors, degradations, inp

    obj, err = read_json_soft(case_path)
    if err:
        errors.append({"item": "case.json", "detail": err})
        # try to recover id from task.md later; keep contract empty
        return None, [], None, errors, degradations, inp
    if not isinstance(obj, dict):
        errors.append({"item": "case.json", "detail": "not a JSON object"})
        return None, [], None, errors, degradations, inp

    case_id = (obj.get("case_id") or obj.get("caseId") or obj.get("id")
               or (obj.get("case") or {}).get("id")
               or (obj.get("case") or {}).get("case_id"))
    contract = obj.get("output_contract", obj.get("outputContract",
                       obj.get("contract")))
    if contract is None:
        # some tasks nest it under task/inputs
        for key in ("task", "inputs", "spec"):
            if isinstance(obj.get(key), dict) and obj[key].get("output_contract"):
                contract = obj[key]["output_contract"]
                break
    if isinstance(contract, dict):
        contract_fields = sorted(contract.keys())
        contract_raw = contract
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                contract_fields.append(item)
            elif isinstance(item, dict):
                contract_fields.append(item.get("key") or item.get("name")
                                        or item.get("id") or str(item))
        contract_raw = contract
    elif contract is not None:
        contract_fields = [str(contract)]
        contract_raw = contract
    if not contract_fields:
        degradations.append(
            {"item": "output_contract",
             "detail": "no explicit output_contract keys found in case.json; "
                       "result.json still exposes status/sources/uncertainty/"
                       "change_log plus analysis/query_log/answers"})
    return case_id, contract_fields, contract_raw, errors, degradations, inp


def build_skeleton(case_id, contract_fields):
    skel = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "analysis": {},
        "query_log": [],
        "answers": {},
        "artifacts": {},
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {
            "version": VERSION,
            "started": C.utc_now(),
            "finished": None,
            "elapsed_seconds": None,
            "phases": {},
        },
    }
    for f in contract_fields:
        skel[f] = None
    log_step(skel["change_log"], "bootstrap",
             "wrote legal partial skeleton with %d contract key(s)" % len(contract_fields),
             "partial")
    return skel


def write_result(path, result):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# data source handling
# --------------------------------------------------------------------------- #
def open_sources(inp, errors, degradations, change_log):
    sources = []
    for db in inp["db_files"]:
        try:
            conn = C.connect_readonly(db)
            sources.append({"kind": "sqlite", "path": db, "conn": conn,
                            "label": Path(db).name, "read_only": True})
        except Exception as e:
            errors.append({"item": "db:" + db, "detail": "connect failed: %s" % e})
            degradations.append({"item": "db:" + db, "detail": "skipped"})
    if inp["csv_files"]:
        conn = None
        try:
            import sqlite3
            conn = sqlite3.connect(":memory:")
        except Exception as e:
            errors.append({"item": "csv", "detail": "in-memory sqlite failed: %s" % e})
        if conn is not None:
            seen_names = set()
            for cf in inp["csv_files"]:
                delim = "," if cf.lower().endswith(".csv") else "\t"
                try:
                    meta = C.load_csv_table(conn, cf, delim)
                    if meta is None:
                        degradations.append(
                            {"item": "csv:" + cf,
                             "detail": "unreadable or empty CSV, skipped"})
                        continue
                    base = meta["name"]
                    i = 1
                    uname = base
                    while uname in seen_names:
                        uname = "%s_%d" % (base, i)
                        i += 1
                    seen_names.add(uname)
                    if uname != base:
                        conn.execute("ALTER TABLE %s RENAME TO %s"
                                     % (C.qid(base), C.qid(uname)))
                    sources.append({"kind": "csv", "path": cf, "conn": conn,
                                    "label": uname, "read_only": True,
                                    "table_meta": meta})
                except Exception as e:
                    errors.append({"item": "csv:" + cf,
                                   "detail": "load failed: %s" % e})
    if not sources:
        degradations.append(
            {"item": "data",
             "detail": "no SQLite or CSV data snapshot found under input dir"})
    log_step(change_log, "connect",
             "opened %d data source(s) (%d sqlite, %d csv)" %
             (len(sources), len(inp["db_files"]), len(inp["csv_files"])),
             "partial" if sources else "insufficient_evidence")
    return sources


# --------------------------------------------------------------------------- #
# per-table analysis
# --------------------------------------------------------------------------- #
def analyze_sources(sources, result, change_log, figures_dir, artifacts):
    evidence = result["sources"]
    analysis = result["analysis"]
    schema_out = []
    stats_rows = []
    quality_rows = []
    corr_rows = []
    value_counts = {}
    joins_all = []
    all_columns = []

    for src in sources:
        try:
            tables = C.inspect_schema(src["conn"])
        except Exception as e:
            result["errors"].append({"item": src["label"],
                                     "detail": "schema inspect failed: %s" % e})
            continue
        label = src["label"]
        src_schema = []
        for t in tables:
            tname = t["table"]
            cols = t["columns"]
            for c in cols:
                all_columns.append((tname, c["name"]))
            src_schema.append(t)
            evidence.append({
                "claim": "table %s has %d columns and %d rows"
                         % (tname, len(cols), t["row_count"]),
                "locator": "sqlite://%s#table=%s" % (label, tname),
                "quote": "columns=%s; row_count=%d"
                         % ([c["name"] for c in cols], t["row_count"]),
                "role": "schema"})

            # duplicates
            dup = C.detect_duplicates(src["conn"], tname,
                                      [c["name"] for c in cols])
            if dup.get("error"):
                result["degradations"].append(
                    {"item": "%s.%s.duplicates" % (label, tname),
                     "detail": dup["error"]})
            elif dup.get("full_row_dup_sql"):
                evidence.append({
                    "claim": "table %s has %d exact duplicate group(s)"
                             % (tname, dup["n_duplicate_groups"]),
                    "locator": dup["full_row_dup_sql"],
                    "quote": "n_duplicate_groups=%d; n_rows_in_duplicate_groups=%d"
                             % (dup["n_duplicate_groups"],
                                dup["n_rows_in_duplicate_groups"]),
                    "role": "data-quality"})

            # column profiling
            profs = []
            numeric_cols = []  # (name, aligned_values)
            for c in cols:
                prof, aligned = C.profile_column(src["conn"], tname,
                                                 c["name"], c["type"])
                if prof.get("error"):
                    result["degradations"].append(
                        {"item": "%s.%s.%s" % (label, tname, c["name"]),
                         "detail": prof["error"]})
                profs.append(prof)
                result["query_log"].append({
                    "id": "%s:%s:%s:extract" % (label, tname, c["name"]),
                    "sql": prof["sql"]["extraction"],
                    "purpose": "column value extraction",
                    "row_count": prof["n_rows"], "status": "ok"})
                result["query_log"].append({
                    "id": "%s:%s:%s:missing" % (label, tname, c["name"]),
                    "sql": prof["sql"]["missing"],
                    "purpose": "missing/blank value count",
                    "row_count": prof["n_missing"], "status": "ok"})
                if prof["sql"]["value_counts"]:
                    result["query_log"].append({
                        "id": "%s:%s:%s:value_counts" % (label, tname, c["name"]),
                        "sql": prof["sql"]["value_counts"],
                        "purpose": "top value counts",
                        "row_count": len(prof["top_values"]), "status": "ok"})

                quality_rows.append({
                    "source": label, "table": tname, "column": c["name"],
                    "declared_type": c["type"], "kind": prof["kind"],
                    "n_rows": prof["n_rows"], "n_nonnull": prof["n_nonnull"],
                    "n_missing": prof["n_missing"],
                    "n_distinct": prof["n_distinct"],
                    "coded_as_text": 1 if prof["kind"] == "coded_as_text" else 0})
                if prof["numeric"] and prof["numeric"].get("stats"):
                    st = prof["numeric"]["stats"]
                    stats_rows.append({
                        "source": label, "table": tname, "column": c["name"],
                        "kind": prof["kind"], "n": st["n"],
                        "mean": st["mean"], "stdev_sample": st["stdev_sample"],
                        "min": st["min"], "q1": st["q1"],
                        "median": st["median"], "q3": st["q3"],
                        "max": st["max"], "iqr": st["iqr"],
                        "n_outliers": st["n_outliers"]})
                    if aligned is not None and not C.is_id_like(c["name"]):
                        numeric_cols.append((c["name"], aligned))
                    agg = prof["numeric"].get("aggregate")
                    if agg and "error" not in agg:
                        result["query_log"].append({
                            "id": "%s:%s:%s:aggregate" % (label, tname, c["name"]),
                            "sql": agg["sql"],
                            "purpose": "SQL aggregate cross-check vs Python stats",
                            "row_count": agg["count"], "status": "ok",
                            "summary": {"avg": agg["avg"], "min": agg["min"],
                                        "max": agg["max"],
                                        "matches_python": agg["matches_python"]}})
                        if not agg["matches_python"]:
                            result["uncertainty"].append({
                                "item": "%s.%s.%s" % (label, tname, c["name"]),
                                "level": "high",
                                "reason": "SQL aggregate and Python recompute disagree"})
                    evidence.append({
                        "claim": "column %s.%s numeric summary" % (tname, c["name"]),
                        "locator": (agg["sql"] if agg and "error" not in agg
                                    else prof["sql"]["extraction"]),
                        "quote": ("n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s"
                                  % (st["n"], st["mean"], st["stdev_sample"],
                                     st["min"], st["max"])),
                        "role": "statistic"})

            # value counts
            vc = {c["name"]: c["top_values"] for c in profs if c["top_values"]}
            if vc:
                value_counts[tname] = vc

            # correlation
            if len(numeric_cols) >= 2:
                corrs = C.correlate_table(src["conn"], tname, numeric_cols)
                for c_ in corrs:
                    corrs_d = dict(c_)
                    corrs_d["source"] = label
                    corrs_d["table"] = tname
                    corr_rows.append(corrs_d)
                evidence.append({
                    "claim": "table %s has %d pairwise correlations"
                             % (tname, len(corrs)),
                    "locator": "python://core_impl.correlate_table#table=%s" % tname,
                    "quote": "n_correlations=%d" % len(corrs),
                    "role": "statistic"})

        schema_out.extend(src_schema)
        analysis[label] = {"tables": [t["table"] for t in tables],
                           "n_tables": len(tables)}

    # cross-source join inference (requires >= 2 tables across sources)
    flat_tables = []
    for src in sources:
        try:
            for t in C.inspect_schema(src["conn"]):
                flat_tables.append((src["label"], t))
        except Exception:
            continue
    tbl_list = []
    for lbl, t in flat_tables:
        tbl_list.append({"label": lbl, "table": t["table"],
                         "columns": t["columns"]})
    joins_all = C.infer_joins(sources, tbl_list)

    return {"schema": schema_out, "stats_rows": stats_rows,
            "quality_rows": quality_rows, "corr_rows": corr_rows,
            "value_counts": value_counts, "joins": joins_all,
            "all_columns": all_columns, "flat_tables": flat_tables}


# --------------------------------------------------------------------------- #
# artifacts: csv writer (SVG/join logic lives in core_impl.py)
# --------------------------------------------------------------------------- #
def write_csv(path, fieldnames, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


# --------------------------------------------------------------------------- #
# agent increments (queries / answers / annotations)
# --------------------------------------------------------------------------- #
def load_agent_increments(work_dir, explicit_queries, result, change_log):
    qpath = explicit_queries or os.path.join(work_dir, "queries.json")
    queries, qerr = read_json_soft(qpath)
    if qerr and (explicit_queries or Path(qpath).exists()):
        result["degradations"].append({"item": "queries.json", "detail": qerr})
    if isinstance(queries, dict):
        queries = queries.get("queries", [])
    if not isinstance(queries, list):
        queries = []
    executed = []
    for q in queries:
        if not isinstance(q, dict):
            continue
        qid_ = q.get("id") or q.get("qid") or "q%d" % len(executed)
        sql = q.get("sql")
        if not sql:
            continue
        entry = {"id": qid_, "sql": sql,
                 "purpose": q.get("note") or q.get("purpose") or "",
                 "status": "ok", "row_count": None}
        last_err = None
        for s in _agent_sources:
            try:
                res = C.execute_readonly(s["conn"], sql)
                entry["row_count"] = res["row_count"]
                entry["summary"] = {"columns": res["columns"],
                                    "rows_preview": [r for r in res["rows"][:5]],
                                    "source": s["label"]}
                result["answers"][qid_] = {"sql": sql,
                                           "row_count": res["row_count"],
                                           "columns": res["columns"],
                                           "rows": [r for r in res["rows"][:50]],
                                           "source": s["label"]}
                last_err = None
                break
            except Exception as e:
                last_err = e
        if last_err is not None:
            entry["status"] = "error"
            entry["error"] = str(last_err)
            result["errors"].append({"item": "query:" + str(qid_),
                                     "detail": str(last_err)})
        result["query_log"].append(entry)
        executed.append(entry)
    log_step(change_log, "queries",
             "executed %d agent-tagged query/ies" % len(executed), "partial")

    # answers.json: contract values + free-form answers (optional increment)
    apath = os.path.join(work_dir, "answers.json")
    if Path(apath).exists():
        aobj, aerr = read_json_soft(apath)
        if aerr:
            result["degradations"].append({"item": "answers.json", "detail": aerr})
        if isinstance(aobj, dict):
            contract_vals = aobj.get("contract")
            if isinstance(contract_vals, dict):
                for k, v in contract_vals.items():
                    if k in result:
                        result[k] = v
            ans = aobj.get("answers")
            if isinstance(ans, dict):
                for k, v in ans.items():
                    result["answers"][k] = v

    # annotations.json: field interpretations (optional increment)
    anpath = os.path.join(work_dir, "annotations.json")
    if Path(anpath).exists():
        aobj2, aerr2 = read_json_soft(anpath)
        if aerr2:
            result["degradations"].append({"item": "annotations.json",
                                           "detail": aerr2})
        if isinstance(aobj2, dict):
            ann = aobj2.get("annotations", aobj2)
            if isinstance(ann, list):
                result["analysis"]["field_annotations"] = (
                    result["analysis"].get("field_annotations", []) + ann)


# --------------------------------------------------------------------------- #
# status + finalisation
# --------------------------------------------------------------------------- #
def decide_status(result, contract_fields, n_sources):
    if n_sources == 0:
        return "insufficient_evidence"
    if result["errors"]:
        return "partial"
    if contract_fields:
        filled = all(result.get(f) is not None for f in contract_fields)
        if not filled:
            return "partial"
        return "ok"
    return "partial"


def finalize(result, contract_fields, n_sources, start_ts):
    result["status"] = decide_status(result, contract_fields, n_sources)
    if result["status"] == "insufficient_evidence":
        result["pending_human"].append(
            "no SQLite/CSV data snapshot found; provide the authorised snapshot")
    if contract_fields:
        missing = [f for f in contract_fields if result.get(f) is None]
        if missing:
            result["limitations"].append(
                {"item": "output_contract", "detail": "unfilled contract fields: %s"
                 % missing})
    result["runtime"]["finished"] = C.utc_now()
    result["runtime"]["elapsed_seconds"] = round(time.time() - start_ts, 3)
    log_step(result["change_log"], "finalize",
             "status=%s; %d errors, %d degradations"
             % (result["status"], len(result["errors"]),
                len(result["degradations"])), result["status"])


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    start_ts = time.time()
    ensure_dirs(args.output, args.work)
    figures_dir = os.path.join(args.output, "figures")
    ensure_dirs(figures_dir)

    case_id, contract_fields, contract_raw, errors, degradations, inp = \
        read_case(args.input)
    if case_id is None:
        case_id = Path(args.input).name or "unknown_case"
        degradations.append(
            {"item": "case_id",
             "detail": "no case_id in case.json; fell back to input dir name"})
    result = build_skeleton(case_id, contract_fields)
    result["errors"].extend(errors)
    result["degradations"].extend(degradations)
    result["analysis"]["output_contract"] = contract_raw
    result["analysis"]["input_discovery"] = {
        "db_files": [Path(x).name for x in inp["db_files"]],
        "csv_files": [Path(x).name for x in inp["csv_files"]],
        "dict_files": [Path(x).name for x in inp["dict_files"]],
        "case_json": Path(inp["case_json"]).name if inp["case_json"] else None,
        "task_md": Path(inp["task_md"]).name if inp["task_md"] else None,
    }
    write_result(os.path.join(args.output, "result.json"), result)

    # open data sources (read-only)
    t0 = time.time()
    sources = open_sources(inp, result["errors"], result["degradations"],
                           result["change_log"])
    global _agent_sources
    _agent_sources = sources
    result["runtime"]["phases"]["connect"] = round(time.time() - t0, 3)

    artifacts = {}
    chart_list = []
    if sources:
        t0 = time.time()
        ana = analyze_sources(sources, result, result["change_log"],
                              figures_dir, artifacts)
        result["runtime"]["phases"]["analyze"] = round(time.time() - t0, 3)
        result["analysis"]["schema"] = ana["schema"]
        result["analysis"]["join_candidates"] = ana["joins"]
        result["analysis"]["n_tables"] = len(ana["flat_tables"])
        if ana["all_columns"]:
            t0 = time.time()
            ann = C.ground_dictionary(inp["dict_files"], ana["all_columns"])
            if ann:
                result["analysis"]["field_annotations"] = ann
                for a in ann:
                    add_src(result["sources"], a["field"], a["source"],
                            a["description"], "dictionary")
            result["runtime"]["phases"]["dictionary"] = round(time.time() - t0, 3)

        # write tabular artifacts
        write_csv(os.path.join(args.output, "summary_stats.csv"),
                  ["source", "table", "column", "kind", "n", "mean",
                   "stdev_sample", "min", "q1", "median", "q3", "max", "iqr",
                   "n_outliers"], ana["stats_rows"])
        write_csv(os.path.join(args.output, "data_quality.csv"),
                  ["source", "table", "column", "declared_type", "kind",
                   "n_rows", "n_nonnull", "n_missing", "n_distinct",
                   "coded_as_text"], ana["quality_rows"])
        write_csv(os.path.join(args.output, "correlation_matrix.csv"),
                  ["source", "table", "x", "y", "n", "pearson", "spearman"],
                  ana["corr_rows"])

        with open(os.path.join(args.output, "schema.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ana["schema"], f, ensure_ascii=False, indent=2)
        with open(os.path.join(args.output, "value_counts.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ana["value_counts"], f, ensure_ascii=False, indent=2)

        # figures
        t0 = time.time()
        fa, cl = C.make_figures(figures_dir, sources)
        artifacts.update(fa)
        chart_list.extend(cl)
        result["runtime"]["phases"]["charts"] = round(time.time() - t0, 3)

    # agent increments
    load_agent_increments(args.work, args.queries, result, result["change_log"])

    # evidence map + report + query_log.csv
    write_csv(os.path.join(args.output, "query_log.csv"),
              ["id", "sql", "purpose", "row_count", "status"],
              result["query_log"])
    with open(os.path.join(args.output, "evidence_map.json"), "w",
              encoding="utf-8") as f:
        json.dump(result["sources"], f, ensure_ascii=False, indent=2)

    result["artifacts"] = {k: v for k, v in artifacts.items()}
    result["analysis"]["charts"] = chart_list
    result["analysis"]["query_count"] = len(result["query_log"])

    write_report(args.output, result, contract_fields, sources)
    write_result(os.path.join(args.output, "result.json"), result)
    finalize(result, contract_fields, len(sources), start_ts)
    # rewrite once more so final status/change_log are persisted
    write_result(os.path.join(args.output, "result.json"), result)
    return 0


def write_report(output_dir, result, contract_fields, sources):
    lines = ["# 科研数据库查询与分析报告", ""]
    lines.append("- case_id: `%s`" % (result.get("case_id")))
    lines.append("- status: `%s`" % result["status"])
    lines.append("- data sources: %d" % len(sources))
    lines.append("- contract keys: %d" % len(contract_fields))
    lines.append("")
    lines.append("## 数据源")
    for s in sources:
        lines.append("- %s `%s` (read_only=%s)" % (s["kind"], s["label"],
                                                    s.get("read_only")))
    lines.append("")
    lines.append("## 结果文件")
    for name in ["result.json", "schema.json", "summary_stats.csv",
                 "data_quality.csv", "correlation_matrix.csv",
                 "query_log.csv", "value_counts.json", "evidence_map.json"]:
        lines.append("- `%s`" % name)
    if result["analysis"].get("charts"):
        lines.append("")
        lines.append("## 图表")
        for c in result["analysis"]["charts"]:
            lines.append("- `%s` (%s)" % (c["file"], c["kind"]))
    if result["errors"]:
        lines.append("")
        lines.append("## errors")
        for e in result["errors"]:
            lines.append("- `%s` %s" % (e.get("item"), e.get("detail")))
    if result["limitations"]:
        lines.append("")
        lines.append("## limitations")
        for l in result["limitations"]:
            if isinstance(l, dict):
                lines.append("- `%s` %s" % (l.get("item"), l.get("detail")))
            else:
                lines.append("- %s" % l)
    lines.append("")
    try:
        with open(os.path.join(output_dir, "report.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
