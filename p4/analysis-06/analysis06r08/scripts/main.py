#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main: deterministic read-only scientific database query & analysis pipeline.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --selftest

Behaviour (idempotent, re-runnable):
  1. discover input (case.json, task.md, SQLite, CSV/TSV, dictionaries);
  2. IMMEDIATELY write a legal result.json skeleton (case_id + every
     output_contract key + status/sources/uncertainty/change_log);
  3. open each data source read-only, introspect schema, profile every column,
     detect missing / duplicates, compute descriptive statistics, Pearson
     correlations, infer join paths, render editable SVG charts;
  4. merge <work>/contract_answers.json and execute tagged SQL from
     <work>/queries.json (read-only, row-counted, summarised);
  5. emit report.md, evidence_map.json, schema.json, CSV tables and query_log,
     then finalise result.json with an honest status.

Only the Python standard library is used. No prepared answers are shipped.
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbkit
import charts

VERSION = "1.1.0"
MAX_QUERY_ROWS = 200
MAX_HISTOGRAMS = 6
MAX_BAR_CHARTS = 3

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log", "analysis",
    "query_log", "answers", "artifacts", "errors", "degradations",
    "limitations", "pending_human", "runtime", "contract_schema",
}

now_ts = dbkit.now_ts


def add_change(result, step, change, status="done"):
    result["change_log"].append({
        "ts": now_ts(), "step": step, "change": change, "status": status,
    })


def add_uncertainty(result, item, level, reason):
    for u in result["uncertainty"]:
        if u.get("item") == item and u.get("reason") == reason:
            return
    result["uncertainty"].append({"item": item, "level": level, "reason": reason})


def add_source(result, locator, quote, role):
    for s in result["sources"]:
        if s.get("locator") == locator:
            return
    result["sources"].append({"locator": locator, "quote": quote, "role": role})


def add_qlog(result, qid, sql, purpose, row_count, summary=None, status="ok",
             error=None):
    entry = {"id": qid, "sql": sql, "purpose": purpose, "row_count": row_count,
             "status": status}
    if summary is not None:
        entry["summary"] = summary
    if error is not None:
        entry["error"] = error
    result["query_log"].append(entry)


def write_result(result, output_dir):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    dbkit.write_json(os.path.join(output_dir, "result.json"), result)


def make_skeleton(case_id, contract):
    """Build a legal result skeleton mirroring every contract key."""
    skeleton = dbkit.nullify(contract) if isinstance(contract, dict) else {}
    result = dict(skeleton)
    result["case_id"] = case_id
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["analysis"] = {}
    result["query_log"] = []
    result["answers"] = {}
    result["artifacts"] = {}
    result["errors"] = []
    result["degradations"] = []
    result["limitations"] = []
    result["pending_human"] = []
    result["runtime"] = {"version": VERSION, "started_at": now_ts(),
                         "elapsed_s": None}
    result["contract_schema"] = {
        "keys": sorted(skeleton.keys()) if isinstance(contract, dict) else [],
    }
    return result


def apply_contract_answers(result, answers):
    """Merge agent-written contract values idempotently (no key rename/delete)."""
    if not isinstance(answers, dict):
        return
    n = 0
    for k, v in answers.items():
        if k in RESERVED_KEYS:
            continue
        result[k] = v
        n += 1
    add_change(result, "merge", "applied %d contract answer(s)" % n)


def classify_status(result, has_data, contract_keys, case_err):
    if not has_data:
        return "insufficient_evidence"
    if case_err and case_err not in ("missing",):
        return "partial"
    if not contract_keys:
        return "partial"
    filled = True
    for k in contract_keys:
        v = result.get(k)
        if v is None or v == [] or v == {}:
            filled = False
            break
    if filled and not result["errors"]:
        return "ok"
    return "partial"


# --------------------------------------------------------------------------- #
# data-source analysis
# --------------------------------------------------------------------------- #
class Source:
    def __init__(self, kind, path, label, conn=None, note=None):
        self.kind = kind          # 'sqlite' or 'tabular'
        self.path = path
        self.label = label
        self.conn = conn
        self.note = note or ""
        self.tables = []


def open_sources(disc, result):
    sources = []
    for db_path in disc["databases"]:
        label = "sqlite:" + Path(db_path).name
        try:
            conn = dbkit.open_db_readonly(db_path)
            sources.append(Source("sqlite", db_path, label, conn))
        except sqlite3.Error as e:
            result["errors"].append(
                {"source": db_path, "error": "connect_failed:%s" % e})
            add_qlog(result, dbkit.slug(label) + ":connect", None,
                     "read-only connection attempt", 0, None,
                     status="error", error=str(e))
    for tb in disc["tabular"]:
        label = "tabular:" + Path(tb).name
        try:
            conn, table, note = dbkit.load_tabular(tb)
            src = Source("tabular", tb, label, conn, note)
            src.tables.append(table)
            sources.append(src)
        except Exception as e:  # noqa: BLE001 - degradation, not crash
            result["degradations"].append(
                {"source": tb, "note": "tabular_load_failed:%s" % e})
    return sources


def analyze_source(src, result, out_dir):
    """Introspect one source and emit schema/statistics/quality evidence."""
    figures_dir = os.path.join(out_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    schema = dbkit.inspect_schema(src.conn)
    if src.kind == "tabular" and src.tables:
        schema = [t for t in schema if t["table"] in src.tables]
    result["analysis"].setdefault("schema", [])
    result["analysis"].setdefault("summary_stats", [])
    result["analysis"].setdefault("data_quality", [])
    result["analysis"].setdefault("correlations", [])
    result["analysis"].setdefault("suggested_joins", [])
    result["analysis"].setdefault("value_counts", {})

    all_numeric = []
    cat_cols = []
    for t in schema:
        table = t["table"]
        card = {"source": src.label, "table": table,
                "row_count": t["row_count"],
                "columns": t["columns"], "foreign_keys": t["foreign_keys"]}
        result["analysis"]["schema"].append(card)
        add_qlog(result, "%s:%s:schema" % (src.label, table),
                 "SELECT name, type, notnull, pk FROM pragma_table_info('%s')"
                 % table.replace("'", "''"),
                 "schema discovery", t["row_count"],
                 {"columns": [c["name"] for c in t["columns"]]})

        dup = dbkit.detect_duplicates(src.conn, table)
        if dup.get("full_row_dup_sql"):
            add_qlog(result, "%s:%s:duplicates" % (src.label, table),
                     dup["full_row_dup_sql"], "exact full-row duplicate detection",
                     dup.get("n_duplicate_groups"),
                     {"n_duplicate_groups": dup.get("n_duplicate_groups"),
                      "n_rows_in_groups": dup.get("n_rows_in_duplicate_groups")},
                     status="error" if dup.get("error") else "ok")
        if dup.get("n_duplicate_groups"):
            result["analysis"].setdefault("duplicates", []).append({
                "source": src.label, "table": table,
                "n_duplicate_groups": dup["n_duplicate_groups"],
                "n_rows_in_groups": dup["n_rows_in_duplicate_groups"],
            })
            add_uncertainty(result, "%s.%s duplicates" % (src.label, table),
                            "medium", "exact duplicate groups present; dedup "
                            "rule must be stated before aggregation")

        numeric_cols = []
        for c in t["columns"]:
            prof = dbkit.column_profile(src.conn, table, c["name"], c["type"])
            if prof.get("kind") == "error":
                result["degradations"].append(
                    {"source": src.label, "table": table, "column": c["name"],
                     "note": "profile_failed:%s" % prof.get("error")})
                continue
            result["analysis"]["data_quality"].append({
                "source": src.label, "table": table, "column": c["name"],
                "kind": prof["kind"], "declared_type": prof["declared_type"],
                "n_total": prof["n_total"], "n_nonnull": prof["n_nonnull"],
                "n_missing": prof["n_missing"],
                "missing_ratio": prof["missing_ratio"],
                "n_distinct": prof["n_distinct"],
                "coded_as_text": prof["coded_as_text"],
                "id_like": prof["id_like"],
            })
            result["analysis"]["value_counts"][
                "%s.%s.%s" % (src.label, table, c["name"])] = prof["top_values"]
            add_qlog(result, "%s:%s:%s:extract" % (src.label, table, c["name"]),
                     "SELECT %s FROM %s" % (dbkit.quote_ident(c["name"]),
                                            dbkit.quote_ident(table)),
                     "column value extraction", prof["n_nonnull"])
            add_qlog(result, "%s:%s:%s:missing" % (src.label, table, c["name"]),
                     "NULL / blank counted from extracted values",
                     "missing/blank value count", prof["n_missing"])
            add_qlog(result, "%s:%s:%s:value_counts" % (src.label, table,
                                                        c["name"]),
                     "top value counts (python)", "top value counts",
                     len(prof["top_values"]),
                     {"top": prof["top_values"][:5]})
            if prof["coded_as_text"]:
                add_uncertainty(
                    result, "%s.%s.%s" % (src.label, table, c["name"]),
                    "medium", "column stores numeric-looking values as text "
                    "(coded_as_text); convert explicitly before aggregation")
            if prof["kind"] in ("integer", "numeric") and "numeric" in prof:
                st = prof["numeric"]
                numeric_cols.append({
                    "name": c["name"],
                    "values": [dbkit._to_number(v) for v in
                               dbkit.extract_values(src.conn, table,
                                                    c["name"])]})
                result["analysis"]["summary_stats"].append({
                    "source": src.label, "table": table, "column": c["name"],
                    "n": st["n"], "mean": st["mean"],
                    "stdev_sample": st["stdev_sample"], "min": st["min"],
                    "q1": st["q1"], "median": st["median"], "q3": st["q3"],
                    "max": st["max"], "n_missing": prof["n_missing"],
                    "n_outliers_iqr": st["n_outliers_iqr"],
                })
                agg = dbkit.sql_aggregate(src.conn, table, c["name"])
                if agg.get("sql") and agg.get("avg") is not None:
                    ok = (dbkit.matches(agg["avg"], st["mean"])
                          and dbkit.matches(agg["min"], st["min"])
                          and dbkit.matches(agg["max"], st["max"]))
                    add_qlog(result, "%s:%s:%s:aggregate"
                             % (src.label, table, c["name"]), agg["sql"],
                             "SQL aggregate cross-check vs python stats",
                             agg["count"],
                             {"avg": agg["avg"], "min": agg["min"],
                              "max": agg["max"], "matches_python": ok})
                    if not ok:
                        add_uncertainty(result, "%s.%s.%s cross-check"
                                        % (src.label, table, c["name"]),
                                        "high", "SQL and python aggregates "
                                        "disagree; inspect type/encoding")
                elif agg.get("error"):
                    add_uncertainty(result, "%s.%s.%s aggregate"
                                    % (src.label, table, c["name"]), "low",
                                    "SQL aggregate unavailable: %s"
                                    % agg["error"])
            else:
                if (prof["kind"] == "categorical"
                        and prof["n_distinct"] <= 20):
                    cat_cols.append({
                        "source": src.label, "table": table, "column": c["name"],
                        "counts": prof["top_values"],
                    })

        if numeric_cols:
            all_numeric.append((src.label, table, numeric_cols))
            for e in dbkit.correlate_table(src.conn, table, numeric_cols):
                e.update({"source": src.label, "table": table})
                result["analysis"]["correlations"].append(e)

    add_source(result, "sqlite://%s" % src.label,
               "kind=%s; tables=%s" % (src.kind,
                                       [t["table"] for t in schema]),
               "data_snapshot")

    # charts: histograms (capped), boxplots, correlation heatmaps, bar charts.
    hist_done = 0
    for label, table, numcols in all_numeric:
        for nc in numcols:
            if hist_done >= MAX_HISTOGRAMS:
                break
            fname = "hist_%s.svg" % dbkit.slug("%s_%s_%s" % (label, table,
                                                              nc["name"]))
            svg = charts.histogram_svg(nc["values"], "%s.%s distribution"
                                       % (table, nc["name"]), nc["name"])
            if svg:
                dbkit.write_chart(figures_dir, fname, svg, result, "histogram")
                hist_done += 1
        if len(numcols) >= 2:
            svg = charts.boxplot_svg([c["values"] for c in numcols],
                                     [c["name"] for c in numcols],
                                     "%s numeric columns" % table)
            if svg:
                dbkit.write_chart(figures_dir, "box_%s.svg" %
                                  dbkit.slug(label + "_" + table), svg,
                                  result, "boxplot")
        if len(numcols) >= 3:
            svg = charts.heatmap_svg(dbkit.corr_matrix(numcols),
                                     [c["name"] for c in numcols],
                                     [c["name"] for c in numcols],
                                     "%s correlation" % table)
            if svg:
                dbkit.write_chart(figures_dir, "corr_%s.svg" %
                                  dbkit.slug(label + "_" + table), svg,
                                  result, "heatmap")

    bar_done = 0
    for cc in sorted(cat_cols, key=lambda x: (-len(x["counts"]),
                                              x["column"])):
        if bar_done >= MAX_BAR_CHARTS:
            break
        svg = charts.bar_svg([c["value"] for c in cc["counts"]],
                             [c["count"] for c in cc["counts"]],
                             "%s.%s value counts" % (cc["table"],
                                                     cc["column"]), "count")
        if svg:
            dbkit.write_chart(figures_dir, "bar_%s.svg" % dbkit.slug(
                "%s_%s_%s" % (cc["source"], cc["table"], cc["column"])),
                svg, result, "bar")
            bar_done += 1


def infer_all_joins(sources, result):
    for src in sources:
        schema = [t for t in dbkit.inspect_schema(src.conn)
                  if src.kind != "tabular" or t["table"] in src.tables]
        for j in dbkit.infer_joins(schema):
            j["left_distinct"] = dbkit.distinct_count(
                src.conn, j["left_table"], j["left_col"])
            j["right_distinct"] = dbkit.distinct_count(
                src.conn, j["right_table"], j["right_col"])
            j["source"] = src.label
            result["analysis"]["suggested_joins"].append(j)
            add_uncertainty(result, "%s join %s.%s->%s.%s"
                            % (src.label, j["left_table"], j["left_col"],
                               j["right_table"], j["right_col"]), "low",
                            "suggested join key (%s); verify direction and "
                            "cardinality before aggregating" % j["join_kind"])


def execute_queries(queries, sources, result):
    if not isinstance(queries, list):
        return
    conn_by_label = {s.label: s.conn for s in sources}
    default_conn = sources[0].conn if sources else None
    for q in queries:
        if not isinstance(q, dict) or "sql" not in q:
            continue
        qid = q.get("id") or "q%d" % (len(result["answers"]) + 1)
        sql = str(q["sql"])
        purpose = q.get("purpose") or q.get("note") or ""
        target = q.get("db") or q.get("source")
        conn = conn_by_label.get(target, default_conn)
        if conn is None:
            add_qlog(result, qid, sql, purpose, 0, status="error",
                     error="no data source available")
            result["answers"][qid] = {"status": "error",
                                      "error": "no data source"}
            continue
        try:
            cur = conn.execute(sql)
            rows = cur.fetchall()[:MAX_QUERY_ROWS]
            cols = [d[0] for d in (cur.description or [])]
            row_count = len(rows)
            summary = {"columns": cols, "preview_rows": rows[:10],
                       "truncated": False}
            status = "ok"
        except sqlite3.Error as e:
            result["errors"].append({"query": qid, "error": str(e), "sql": sql})
            add_qlog(result, qid, sql, purpose, 0, status="error", error=str(e))
            result["answers"][qid] = {"status": "error", "error": str(e)}
            continue
        add_qlog(result, qid, sql, purpose, row_count, summary, status=status)
        result["answers"][qid] = {"status": status, "row_count": row_count,
                                  "columns": cols, "preview_rows": rows[:10]}


def emit_artifacts(result, out_dir, case_id):
    a = result["analysis"]
    summary_header = ["source", "table", "column", "n", "mean", "stdev_sample",
                      "min", "q1", "median", "q3", "max", "n_missing",
                      "n_outliers_iqr"]
    dbkit.write_csv(os.path.join(out_dir, "summary_stats.csv"), summary_header,
                    [[r.get(h) for h in summary_header]
                     for r in a.get("summary_stats", [])])
    result["artifacts"]["summary_stats.csv"] = "csv"

    dq_header = ["source", "table", "column", "kind", "declared_type",
                 "n_total", "n_nonnull", "n_missing", "missing_ratio",
                 "n_distinct", "coded_as_text", "id_like"]
    dbkit.write_csv(os.path.join(out_dir, "data_quality.csv"), dq_header,
                    [[r.get(h) for h in dq_header]
                     for r in a.get("data_quality", [])])
    result["artifacts"]["data_quality.csv"] = "csv"

    corr_header = ["source", "table", "col_a", "col_b", "pearson_r",
                   "n_paired"]
    dbkit.write_csv(os.path.join(out_dir, "correlation_matrix.csv"),
                    corr_header, [[r.get(h) for h in corr_header]
                                  for r in a.get("correlations", [])])
    result["artifacts"]["correlation_matrix.csv"] = "csv"

    qlog_header = ["id", "status", "row_count", "purpose", "sql"]
    dbkit.write_csv(os.path.join(out_dir, "query_log.csv"), qlog_header,
                    [[q.get("id"), q.get("status"), q.get("row_count"),
                      q.get("purpose"), q.get("sql")]
                     for q in result["query_log"]])
    result["artifacts"]["query_log.csv"] = "csv"

    dbkit.write_json(os.path.join(out_dir, "schema.json"),
                     a.get("schema", []))
    result["artifacts"]["schema.json"] = "json"
    dbkit.write_json(os.path.join(out_dir, "value_counts.json"),
                     a.get("value_counts", {}))
    result["artifacts"]["value_counts.json"] = "json"

    dbkit.write_json(os.path.join(out_dir, "evidence_map.json"),
                     dbkit.build_evidence(result))
    result["artifacts"]["evidence_map.json"] = "json"

    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(dbkit.build_report(result, case_id, VERSION))
    result["artifacts"]["report.md"] = "report"


# --------------------------------------------------------------------------- #
# pipeline entry
# --------------------------------------------------------------------------- #
def run_pipeline(input_dir, output_dir, work_dir, queries_path=None):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    t0 = time.time()

    disc = dbkit.discover_inputs(input_dir)
    case_path = disc["case_json"] or dbkit.find_fallback_json(input_dir)
    case_id, contract, _raw_case, case_err = dbkit.load_case_json(case_path)

    result = make_skeleton(case_id, contract)
    contract_keys = sorted(result["contract_schema"].get("keys", []))
    if case_err and case_err != "missing":
        result["errors"].append({"step": "case_json", "error": case_err})
        add_change(result, "input", "case.json parse issue: %s" % case_err,
                   status="error")
    add_change(result, "skeleton", "wrote legal partial skeleton")
    write_result(result, output_dir)          # fail-fast partial deliverable

    if disc["task_md"]:
        text, _enc = dbkit.read_text(disc["task_md"])
        if text:
            add_source(result, "input:task.md", text[:240], "task_statement")
    for d in disc["dictionaries"]:
        text, _enc = dbkit.read_text(d)
        if text:
            add_source(result, "input:" + Path(d).name, text[:240],
                       "dictionary")

    has_data = bool(disc["databases"] or disc["tabular"])
    sources = open_sources(disc, result)

    try:
        for src in sources:
            analyze_source(src, result, output_dir)
        infer_all_joins(sources, result)
    except Exception as e:  # noqa: BLE001 - never leave an invalid result
        result["errors"].append({"step": "auto_analysis", "error": str(e),
                                 "trace": traceback.format_exc()[-2000:]})
        add_change(result, "auto_analysis", "auto analysis failed: %s" % e,
                   status="error")

    answers = dbkit.load_json_optional(os.path.join(work_dir,
                                                    "contract_answers.json"))
    apply_contract_answers(result, answers)

    q_path = queries_path or os.path.join(work_dir, "queries.json")
    execute_queries(dbkit.load_json_optional(q_path), sources, result)

    for s in sources:
        try:
            if s.conn is not None:
                s.conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not has_data:
        result["pending_human"].append(
            "no SQLite/CSV snapshot found under /input; provide data or "
            "connection parameters")
        result["limitations"].append("no readable data source")
    for e in result["errors"]:
        result["limitations"].append("error recorded: %s" % e)

    result["status"] = classify_status(result, has_data, contract_keys,
                                       case_err)
    result["runtime"]["elapsed_s"] = round(time.time() - t0, 3)
    add_change(result, "finalize", "status=%s" % result["status"])
    emit_artifacts(result, output_dir, case_id)
    write_result(result, output_dir)
    return result


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
def selftest():
    pkg_root = Path(__file__).resolve().parent.parent
    checks = []

    def check(name, fn):
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, "exception:%s" % e
        checks.append((name, ok, msg))
        return ok

    check("frontmatter", lambda: dbkit.frontmatter_ok(pkg_root / "SKILL.md"))

    def _compile():
        for p in sorted((pkg_root / "scripts").glob("*.py")):
            with open(p, "r", encoding="utf-8") as f:
                compile(f.read(), str(p), "exec")
        return True, "ok"
    check("scripts_compile", _compile)

    def _import():
        import dbkit, charts  # noqa: F401
        return True, "ok"
    check("modules_import", _import)

    with tempfile.TemporaryDirectory(prefix="sci-db-selftest-",
                                     ignore_cleanup_errors=True) as td:
        td_path = Path(td)

        def _empty():
            out = td_path / "empty_out"
            r = run_pipeline(str(td_path / "empty_in"), str(out),
                             str(td_path / "empty_work"))
            ok = (r["status"] == "insufficient_evidence"
                  and r.get("case_id") is None)
            return ok, "status=%s" % r["status"]
        check("empty_input", _empty)

        (td_path / "demo_in").mkdir()
        db_path = td_path / "demo_in" / "demo.db"
        dbkit.make_synthetic_db(str(db_path))
        case = {"case_id": "demo-case",
                "output_contract": {"answer_value": None,
                                    "answer_rows": []}}
        (td_path / "demo_in" / "case.json").write_text(
            json.dumps(case), encoding="utf-8")
        (td_path / "demo_in" / "task.md").write_text(
            "# demo task\ncompute mean value by group.\n", encoding="utf-8")

        def _synthetic():
            out = td_path / "demo_out"
            r = run_pipeline(str(td_path / "demo_in"), str(out),
                             str(td_path / "demo_work"))
            ok = (r["status"] in ("partial", "ok")
                  and r["case_id"] == "demo-case"
                  and "answer_value" in r and "answer_rows" in r
                  and "sources" in r and "uncertainty" in r
                  and "change_log" in r)
            return ok, "status=%s keys=%s" % (
                r["status"], sorted(k for k in r
                                    if k in ("answer_value", "answer_rows")))
        check("synthetic_contract", _synthetic)

        def _determinism():
            out1 = td_path / "det_out1"
            out2 = td_path / "det_out2"
            run_pipeline(str(td_path / "demo_in"), str(out1),
                         str(td_path / "det_work1"))
            run_pipeline(str(td_path / "demo_in"), str(out2),
                         str(td_path / "det_work2"))
            f1 = (out1 / "summary_stats.csv").read_text(encoding="utf-8")
            f2 = (out2 / "summary_stats.csv").read_text(encoding="utf-8")
            return f1 == f2, "identical" if f1 == f2 else "DIFFER"
        check("determinism", _determinism)

        def _svg():
            figs = td_path / "demo_out" / "figures"
            svgs = list(figs.glob("*.svg"))
            if not svgs:
                return False, "no svg produced"
            for s in svgs:
                ET.parse(str(s))
            return True, "%d svg parsed" % len(svgs)
        check("svg_parse", _svg)

        bad_dir = td_path / "bad_in"
        bad_dir.mkdir()
        (bad_dir / "case.json").write_text("{not valid json", encoding="utf-8")
        (bad_dir / "demo.db").write_bytes(db_path.read_bytes())

        def _badjson():
            out = td_path / "bad_out"
            r = run_pipeline(str(bad_dir), str(out), str(td_path / "bad_work"))
            ok = r["status"] in ("partial", "insufficient_evidence")
            return ok, "status=%s errors=%d" % (r["status"], len(r["errors"]))
        check("bad_json_continues", _badjson)

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, msg in checks:
        print("[%s] %s : %s" % ("PASS" if ok else "FAIL", name, msg))
    print("selftest: %d/%d passed" % (n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Read-only scientific DB query & analysis pipeline.")
    p.add_argument("--input", default="/input",
                   help="input directory (default /input)")
    p.add_argument("--output", default="/app/output",
                   help="output directory (default /app/output)")
    p.add_argument("--work", default="/app/work",
                   help="work directory (default /app/work)")
    p.add_argument("--queries", default=None,
                   help="path to tagged queries JSON (default <work>/queries.json)")
    p.add_argument("--selftest", action="store_true",
                   help="run the built-in self-test and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.selftest:
        sys.exit(selftest())
    try:
        run_pipeline(args.input, args.output, args.work, args.queries)
    except Exception as e:  # noqa: BLE001 - fail-safe legal result
        os.makedirs(args.output, exist_ok=True)
        minimal = {
            "case_id": None, "status": "input_error",
            "sources": [], "uncertainty": [], "change_log": [{
                "ts": now_ts(), "step": "fatal", "change": str(e),
                "status": "error"}],
            "errors": [str(e)], "limitations": ["pipeline crashed"],
            "pending_human": [],
        }
        dbkit.write_json(os.path.join(args.output, "result.json"), minimal)
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
