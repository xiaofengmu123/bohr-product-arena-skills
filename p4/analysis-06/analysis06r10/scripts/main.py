#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main: verification-first read-only scientific DB analysis pipeline (CLI).

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work

Contract:
  1. discover input and immediately persist a legal result.json skeleton
     (case_id + every output_contract key + status=partial) BEFORE any heavy
     analysis, so an interrupted run still leaves a parseable deliverable;
  2. build a read-only catalog (SQLite + CSV-as-in-memory-SQLite), profile
     schema/columns, detect missing/duplicates, compute stats and Pearson
     correlations, ground fields against the data dictionary, render editable
     SVG charts;
  3. execute tagged read-only queries from <work>/queries.json and log each;
  4. emit report/evidence/schema/CSV artifacts and finalize result.json with an
     honest status. Re-runs are incremental: existing result.json contract
     values are preserved, only missing keys are filled, change_log appended.

Only the Python standard library is used.
"""

import argparse
import csv as _csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402


def _rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


def _safe_name(s):
    return "".join(ch if ch.isalnum() or ch in "-_." else "_"
                   for ch in str(s))[:60]


def add_qlog(qlog, qid, sql, purpose, row_count, status="ok", summary=None,
             error=None, source=None):
    e = {"id": qid, "sql": sql, "purpose": purpose, "row_count": row_count,
         "status": status}
    if summary is not None:
        e["summary"] = summary
    if error is not None:
        e["error"] = error
    if source is not None:
        e["source"] = source
    qlog.append(e)


def render_charts(chart_data, figures_dir, artifacts):
    chart_list = []
    os.makedirs(figures_dir, exist_ok=True)
    for item in chart_data:
        table = item["table"]
        if item["kind"] == "histogram":
            col = item["column"]
            svg = core.render_histogram_svg(col, item["profile"], table)
            fname = "hist_%s_%s.svg" % (_safe_name(table), _safe_name(col))
        elif item["kind"] == "bar":
            col = item["column"]
            svg = core.render_bar_svg(col, item["profile"].get("top_values", []),
                                      table)
            fname = "bar_%s_%s.svg" % (_safe_name(table), _safe_name(col))
        elif item["kind"] == "heatmap":
            svg = core.render_heatmap_svg(item.get("pairs", []),
                                          item.get("names", []))
            fname = "heatmap_%s.svg" % _safe_name(table)
        else:
            continue
        if not svg:
            continue
        path = os.path.join(figures_dir, fname)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        chart_list.append({"file": os.path.join("figures", fname),
                           "kind": item["kind"], "table": table,
                           "column": item.get("column")})
        artifacts[os.path.join("figures", fname)] = "chart"
    return chart_list


def emit_artifacts(output_dir, analysis, semantics, dictionaries,
                   dict_notes, dict_errors, query_log):
    artifacts = {}
    # schema.json
    schema_path = os.path.join(output_dir, "schema.json")
    core.write_json(schema_path, analysis["schema"])
    artifacts["schema.json"] = "schema"
    # summary_stats.csv
    ss = os.path.join(output_dir, "summary_stats.csv")
    ss_rows = []
    for t in analysis["profiles"]:
        for p in t["columns"]:
            if p.get("kind") == "numeric":
                st = p.get("numeric", {})
                ss_rows.append([t["table"], p["name"], st.get("n"),
                                st.get("mean"), st.get("stdev_sample"),
                                st.get("min"), st.get("q1"), st.get("median"),
                                st.get("q3"), st.get("max"), st.get("sum")])
    _write_csv(ss, ["table", "column", "n", "mean", "stdev_sample", "min",
                    "q1", "median", "q3", "max", "sum"], ss_rows)
    artifacts["summary_stats.csv"] = "table"
    # data_quality.csv
    dq = os.path.join(output_dir, "data_quality.csv")
    dq_rows = []
    for t in analysis["profiles"]:
        for p in t["columns"]:
            dq_rows.append([t["table"], p["name"], p.get("type"),
                            p.get("kind"), p.get("coded_as_text"),
                            p.get("low_cardinality"),
                            p.get("n_total"), p.get("n_nonnull"),
                            p.get("n_missing"), p.get("n_distinct")])
    _write_csv(dq, ["table", "column", "declared_type", "kind",
                    "coded_as_text", "low_cardinality", "n_total",
                    "n_nonnull", "n_missing", "n_distinct"], dq_rows)
    artifacts["data_quality.csv"] = "table"
    # correlation_matrix.csv
    cm = os.path.join(output_dir, "correlation_matrix.csv")
    cm_rows = [[c.get("table"), c.get("x"), c.get("y"), c.get("pearson"),
                c.get("n"), c.get("note")] for c in analysis["correlation"]]
    _write_csv(cm, ["table", "x", "y", "pearson", "n", "note"], cm_rows)
    artifacts["correlation_matrix.csv"] = "table"
    # query_log.csv
    ql = os.path.join(output_dir, "query_log.csv")
    _write_csv(ql, ["id", "sql", "purpose", "row_count", "status", "summary",
                    "error", "source"],
               [[q.get("id"), q.get("sql"), q.get("purpose"),
                 q.get("row_count"), q.get("status"), q.get("summary"),
                 q.get("error"), q.get("source")] for q in query_log])
    artifacts["query_log.csv"] = "table"
    # value_counts.json
    vc = {}
    for t in analysis["profiles"]:
        for p in t["columns"]:
            if p.get("top_values"):
                vc["%s.%s" % (t["table"], p["name"])] = p["top_values"]
    vc_path = os.path.join(output_dir, "value_counts.json")
    core.write_json(vc_path, vc)
    artifacts["value_counts.json"] = "table"
    # evidence_map.json
    ev_path = os.path.join(output_dir, "evidence_map.json")
    core.write_json(ev_path, analysis["evidence"])
    artifacts["evidence_map.json"] = "evidence"
    # report.md
    report_path = os.path.join(output_dir, "report.md")
    _write_report(report_path, analysis, semantics, dictionaries,
                  dict_notes, dict_errors, query_log)
    artifacts["report.md"] = "report"
    # charts
    figures_dir = os.path.join(output_dir, "figures")
    chart_list = render_charts(analysis["chart_data"], figures_dir, artifacts)
    return artifacts, chart_list


def _write_report(path, analysis, semantics, dictionaries, dict_notes,
                  dict_errors, query_log):
    lines = ["# 科研数据库查询与分析报告", ""]
    lines.append("## 数据概览")
    for t in analysis["schema"]:
        lines.append("- 表 `%s`（来源 `%s`，kind=%s）：%d 列，%s 行"
                    % (t["table"], t["source"], t["kind"], len(t["columns"]),
                       t["row_count"]))
        dup = t.get("duplicates", {})
        if dup.get("n_duplicate_groups"):
            lines.append("  - 全行重复组 %d 组，涉及 %d 行"
                         % (dup["n_duplicate_groups"],
                            dup["n_rows_in_duplicate_groups"]))
    lines.append("")
    lines.append("## 字段语义（依据数据字典）")
    grounded = [s for s in semantics if s.get("grounded")]
    if grounded:
        for s in grounded:
            d = s.get("dictionary", {})
            desc = d.get("description") or d.get("说明") or d.get("含义") \
                or d.get("definition") or "、".join(
                    "%s=%s" % (k, v) for k, v in d.items())
            lines.append("- `%s.%s`：%s"
                         % (s["table"], s["column"], desc))
    else:
        lines.append("- 未发现可对接的数据字典条目。")
    if dict_errors:
        lines.append("- 数据字典解析问题：%s" % "；".join(dict_errors))
    if dict_notes:
        lines.append("- 数据字典备注：%s" % "；".join(dict_notes))
    lines.append("")
    lines.append("## 描述统计（数值列）")
    found = False
    for t in analysis["profiles"]:
        for p in t["columns"]:
            if p.get("kind") == "numeric":
                found = True
                st = p.get("numeric", {})
                coded = "（coded_as_text，CAST 转换）" if p.get(
                    "coded_as_text") else ""
                lines.append("- `%s.%s`%s：n=%s mean=%s sd=%s min=%s "
                             "median=%s max=%s"
                             % (t["table"], p["name"], coded, st.get("n"),
                                st.get("mean"), st.get("stdev_sample"),
                                st.get("min"), st.get("median"),
                                st.get("max")))
    if not found:
        lines.append("- 无数值列。")
    lines.append("")
    lines.append("## 相关矩阵（Pearson，仅数值列）")
    for c in analysis["correlation"]:
        lines.append("- `%s.%s` × `%s.%s`：r=%s（n=%s）%s"
                     % (c.get("table"), c.get("x"), c.get("table"),
                        c.get("y"), c.get("pearson"), c.get("n"),
                        c.get("note") or ""))
    if not analysis["correlation"]:
        lines.append("- 无可计算的相关对。")
    lines.append("")
    lines.append("## 定向查询日志（%d 条）" % len(query_log))
    for q in query_log:
        lines.append("- `%s`：%s 行，status=%s%s"
                     % (q.get("id"), q.get("row_count"), q.get("status"),
                        ("，error=%s" % q["error"]) if q.get("error") else ""))
    lines.append("")
    lines.append("## 证据溯源")
    lines.append("完整逐字证据见 `evidence_map.json`；本报告中的数字均由 "
                 "`schema.json` / `summary_stats.csv` / `correlation_matrix.csv` "
                 "独立复算，SQL 聚合与 Python 重算的对照见各列的 "
                 "`matches_python` 字段。")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def decide_status(catalog, contract, result, errors, degradations):
    if not catalog:
        return "insufficient_evidence"
    if errors:
        return "partial"
    filled = [result.get(k) is not None for k in contract]
    if contract and all(filled):
        return "ok"
    return "partial"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="verification-first read-only scientific DB analysis")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--queries", default=None)
    args = ap.parse_args(argv)

    input_dir, output_dir, work_dir = args.input, args.output, args.work
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    queries_path = args.queries or os.path.join(work_dir, "queries.json")
    result_path = os.path.join(output_dir, "result.json")

    # --- stage 0: discover + skeleton ------------------------------------ #
    discovery = core.discover_input(input_dir)
    case_id, contract, raw_case, case_errors = core.load_case(discovery)

    # Preserve an existing valid result on re-run (incremental updates only).
    existing = None
    if os.path.exists(result_path):
        obj, err = core.read_json(result_path)
        if err is None and isinstance(obj, dict):
            existing = obj
    if existing is not None:
        result = existing
        for k in contract:
            result.setdefault(k, None)
        result.setdefault("change_log", []).append({
            "ts": core.now_iso(), "step": "rerun",
            "change": "incremental re-run; preserved existing contract values",
            "status": result.get("status", "partial")})
    else:
        result = core.build_skeleton(case_id, contract, discovery)
        core.write_json(result_path, result)

    errors = list(case_errors)
    degradations = list(discovery.get("degradations", []))
    limitations = []
    pending_human = []
    uncertainty = []

    # --- catalog ---------------------------------------------------------- #
    catalog, cat_errors, cat_deg = core.build_catalog(discovery, work_dir)
    errors.extend(cat_errors)
    degradations.extend(cat_deg)

    # --- semantics -------------------------------------------------------- #
    dictionaries = discovery.get("dicts", [])
    semantics = []
    dict_notes, dict_errors = [], []
    if catalog:
        try:
            semantics, _entries, dict_notes, dict_errors = \
                core.apply_semantics(analysis_schema(catalog), dictionaries)
        except Exception as e:  # noqa: BLE001
            dict_errors.append("semantics failed: %s" % e)

    # --- analysis (IR) ---------------------------------------------------- #
    analysis = {"schema": [], "profiles": [], "correlation": [],
                "chart_data": [], "evidence": [], "tables": []}
    if catalog:
        try:
            analysis = core.compute_analysis(catalog, discovery, dictionaries)
        except Exception as e:  # noqa: BLE001
            errors.append("analysis failed: %s" % e)

    # --- queries ---------------------------------------------------------- #
    query_log = []
    answers = {}
    queries, qerr = core.load_queries(queries_path)
    if qerr:
        degradations.append("queries.json: %s" % qerr)
    for q in queries or []:
        qid = q.get("id") or "q%d" % (len(query_log) + 1)
        sql = q.get("sql", "")
        note = q.get("note", "")
        res, source, route_err = core.route_and_execute(catalog, sql)
        if route_err:
            add_qlog(query_log, qid, sql, note, None, "error", None,
                     route_err, None)
            answers[qid] = {"rows": [], "columns": [],
                            "row_count": None, "error": route_err}
            continue
        summary = None
        if res.get("error") is None and res.get("columns"):
            summary = "columns=%s; first_row=%s" % (
                res["columns"], res["rows"][0] if res["rows"] else [])
        add_qlog(query_log, qid, sql, note, res.get("row_count"),
                 "ok" if res.get("error") is None else "error", summary,
                 res.get("error"), source)
        answers[qid] = {"rows": res.get("rows", []),
                        "columns": res.get("columns", []),
                        "row_count": res.get("row_count"),
                        "error": res.get("error"), "source": source}

    # --- contract resolution --------------------------------------------- #
    contract_notes = {}
    if contract:
        resolved = core.resolve_contract(contract, analysis)
        for key, info in resolved.items():
            contract_notes[key] = info
            if info["confidence"] == "high" and result.get(key) is None:
                result[key] = info["value"]
                result.setdefault("change_log", []).append({
                    "ts": core.now_iso(), "step": "contract",
                    "change": "auto-filled %s from computed analysis" % key,
                    "status": "partial"})
            elif result.get(key) is None:
                pending_human.append(
                    "contract key %r unresolved (%s)" % (key,
                                                         info["reason"]))
    for k in contract:
        if result.get(k) is None:
            pending_human.append("contract key %r not resolved" % k)

    # --- artifacts -------------------------------------------------------- #
    artifacts = {}
    chart_list = []
    if catalog:
        try:
            artifacts, chart_list = emit_artifacts(
                output_dir, analysis, semantics, dictionaries,
                dict_notes, dict_errors, query_log)
        except Exception as e:  # noqa: BLE001
            errors.append("artifact emission failed: %s" % e)

    # --- uncertainty / limitations --------------------------------------- #
    for t in analysis["profiles"]:
        for p in t["columns"]:
            if p.get("coded_as_text"):
                uncertainty.append({
                    "item": "%s.%s" % (t["table"], p["name"]),
                    "level": "low",
                    "reason": "coded_as_text: values are text but numeric-like;"
                              " converted via CAST(col AS REAL) for aggregates"})
            if p.get("low_cardinality"):
                uncertainty.append({
                    "item": "%s.%s" % (t["table"], p["name"]),
                    "level": "low",
                    "reason": "numeric column with <=10 distinct values; "
                              "treated as categorical-like (excluded from "
                              "correlation, rendered as bar chart)"})
            agg = p.get("aggregate") or {}
            if agg.get("error") is None and \
                    agg.get("matches_python") is False:
                uncertainty.append({
                    "item": "%s.%s aggregate cross-check" % (t["table"],
                                                             p["name"]),
                    "level": "medium",
                    "reason": "SQL AVG differs from Python recompute"})
    if not catalog:
        limitations.append("no DB/CSV data snapshot found in /input")
        pending_human.append("provide authorized data snapshot or data "
                             "dictionary to complete analysis")
    for d in dict_errors:
        degradations.append("dictionary: %s" % d)

    # --- finalize --------------------------------------------------------- #
    status = decide_status(catalog, contract, result, errors, degradations)
    result["status"] = status
    result["analysis"] = {
        "input": {"input_dir": input_dir,
                  "case_json": discovery.get("case_json"),
                  "task_md": discovery.get("task_md"),
                  "dbs": discovery.get("dbs"),
                  "csvs": discovery.get("csvs"),
                  "dicts": discovery.get("dicts")},
        "schema": analysis["schema"],
        "field_semantics": semantics,
        "profiles": analysis["profiles"],
        "correlation": analysis["correlation"],
        "charts": chart_list,
        "contract_notes": contract_notes,
        "numeric_column_count": analysis.get("numeric_column_count", 0),
        "table_row_counts": analysis.get("table_row_counts", []),
    }
    result["sources"] = analysis.get("evidence", [])
    result["uncertainty"] = (result.get("uncertainty") or []) + uncertainty
    result["query_log"] = query_log
    result["answers"] = answers
    result["artifacts"] = artifacts
    result["errors"] = (result.get("errors") or []) + errors
    result["degradations"] = (result.get("degradations") or []) + degradations
    result["limitations"] = limitations
    result["pending_human"] = pending_human
    result["runtime"] = {
        "started_at": result.get("runtime", {}).get("started_at"),
        "finished_at": core.now_iso(),
        "version": core.VERSION,
    }
    result.setdefault("change_log", []).append({
        "ts": core.now_iso(), "step": "finalize",
        "change": "finalized result with status=%s" % status,
        "status": status})

    # --- analysis_ir (replayable intermediate) --------------------------- #
    try:
        core.write_json(os.path.join(work_dir, "analysis_ir.json"), {
            "case_id": case_id,
            "contract": contract,
            "catalog_labels": [c["label"] for c in catalog],
            "analysis": analysis,
            "query_log": query_log,
            "contract_notes": contract_notes,
        })
    except Exception as e:  # noqa: BLE001
        degradations.append("analysis_ir write failed: %s" % e)

    try:
        core.write_json(result_path, result)
    except Exception as e:  # noqa: BLE001
        # last-resort minimal legal skeleton must not crash the process
        minimal = core.build_skeleton(case_id, contract, discovery)
        minimal["errors"] = ["result write failed: %s" % e]
        try:
            core.write_json(result_path, minimal)
        except Exception:
            pass
    return 0


def analysis_schema(catalog):
    return [t for entry in catalog for t in entry["schema"]]


if __name__ == "__main__":
    sys.exit(main())
