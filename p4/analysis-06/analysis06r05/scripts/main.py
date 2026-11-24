#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract-faithful read-only scientific DB analysis pipeline.

Writes a legal result.json skeleton (case_id + every output_contract leaf)
first, then profiles each SQLite/CSV snapshot read-only, runs tagged queries
from <work>/queries.json, fills matched contract fields, records change_log
and finalises an honest status. Standard library only; no prepared answers.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbscan  # noqa: E402

VERSION = dbscan.VERSION
MAX_QUERY_ROWS = 200

write_csv = dbscan.write_csv
write_json = dbscan.write_json
build_report = dbscan.build_report


# small helpers
def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def change(change_log, ts, step, what, status="ok"):
    change_log.append({"ts": ts, "step": step, "change": what,
                       "status": status})


def add_qlog(qlog, qid, sql, purpose, row_count, summary=None, status="ok",
             error=None):
    entry = {"id": qid, "sql": sql, "purpose": purpose, "row_count": row_count,
             "status": status}
    if summary is not None:
        entry["summary"] = summary
    if error is not None:
        entry["error"] = error
    qlog.append(entry)


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# output-contract handling (the focus of this variant)
def extract_case_id(case):
    for key in ("case_id", "caseId", "id"):
        if isinstance(case, dict) and case.get(key) is not None:
            return case[key]
    return None


def extract_contract(case):
    if not isinstance(case, dict):
        return None
    for key in ("output_contract", "outputContract", "contract"):
        if key in case and case[key] is not None:
            return case[key]
    return None


def _leaf_fields(contract, prefix, out):
    """Flatten a contract into ordered (path, kind) leaves."""
    if isinstance(contract, dict):
        for key in sorted(contract.keys()):
            val = contract[key]
            path = key if not prefix else "%s.%s" % (prefix, key)
            if isinstance(val, dict) and "type" not in val and val:
                # nested object -> recurse; a dict with only 'type' is a spec
                _leaf_fields(val, path, out)
            elif isinstance(val, dict):
                kind = _kind_from_spec(val)
                out.append({"path": path, "kind": kind})
            elif isinstance(val, (list, tuple)):
                out.append({"path": path, "kind": "list"})
            else:
                out.append({"path": path, "kind": "scalar"})
    elif isinstance(contract, (list, tuple)):
        if not contract:
            return
        if all(isinstance(x, str) for x in contract):
            for name in contract:
                out.append({"path": name, "kind": "scalar"})
        else:
            for item in contract:
                if isinstance(item, dict):
                    name = (item.get("name") or item.get("field")
                            or item.get("key") or item.get("path"))
                    if name:
                        out.append({"path": name,
                                    "kind": _kind_from_spec(item)})
                    else:
                        _leaf_fields(item, prefix, out)
                else:
                    out.append({"path": str(item), "kind": "scalar"})
    elif isinstance(contract, str) and contract.strip():
        out.append({"path": contract, "kind": "scalar"})


def _kind_from_spec(spec):
    if not isinstance(spec, dict):
        return "scalar"
    typ = str(spec.get("type", "")).lower()
    if "array" in typ or "list" in typ:
        return "list"
    if "object" in typ or "dict" in typ or "map" in typ:
        return "object"
    return "scalar"


def typed_default(kind):
    if kind == "list":
        return []
    if kind == "object":
        return {}
    return None


def set_path(obj, path, value):
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def build_skeleton(case_id, fields):
    sk = {"case_id": case_id}
    for f in fields:
        set_path(sk, f["path"], typed_default(f["kind"]))
    return sk


# input discovery / parsing
def read_json(path, errors, degradations):
    text = dbscan.read_text_file(path, errors)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append("%s: invalid JSON (%s)" % (path, exc))
        return None


def discover_inputs(input_dir):
    files = dbscan.discover_files(input_dir, dbscan.DB_EXTENSIONS)
    csvs = dbscan.discover_files(input_dir, dbscan.CSV_EXTENSIONS)
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    return {"dbs": files, "csvs": csvs,
            "case": case_path if os.path.isfile(case_path) else None,
            "task": task_path if os.path.isfile(task_path) else None}


# per-table analysis
def analyze_table(conn, table, label, figures_dir, qlog, evidence, artifacts,
                  chart_list, errors, degradations):
    schema = dbscan.inspect_schema(conn)
    meta = next((t for t in schema if t["table"] == table), None)
    row_count = meta["row_count"] if meta else None
    columns = meta["columns"] if meta else []

    add_qlog(qlog, "%s:%s:schema" % (label, table),
             "SELECT name, type, \"notnull\", pk FROM pragma_table_info('%s')"
             % table.replace("'", "''"),
             "schema discovery", row_count if row_count is not None else 0)
    evidence.append({
        "claim": "table %s has %d columns and %s rows"
                 % (table, len(columns),
                    str(row_count) if row_count is not None else "unknown"),
        "locator": "sqlite://%s#table=%s" % (label, table),
        "quote": "columns=%s; row_count=%s"
                 % ([c["name"] for c in columns], str(row_count)),
        "role": "schema"})

    dup = dbscan.detect_duplicates(conn, table)
    if dup.get("error"):
        degradations.append("%s:%s duplicate detection: %s"
                            % (label, table, dup["error"]))
    elif dup.get("full_row_dup_sql"):
        add_qlog(qlog, "%s:%s:duplicates" % (label, table),
                 dup["full_row_dup_sql"], "exact full-row duplicate detection",
                 dup["n_duplicate_groups"] or 0, dup)
        evidence.append({
            "claim": "table %s has %s exact duplicate groups"
                     % (table, str(dup["n_duplicate_groups"])),
            "locator": dup["full_row_dup_sql"],
            "quote": "n_duplicate_groups=%s; n_rows_in_groups=%s"
                     % (str(dup["n_duplicate_groups"]),
                        str(dup["n_rows_in_duplicate_groups"])),
            "role": "data-quality"})

    profiled = []
    numeric_cols = []
    for c in columns:
        prof = dbscan.column_profile(conn, table, c["name"], c["type"])
        if prof.get("error"):
            errors.append("%s:%s:%s profile: %s"
                          % (label, table, c["name"], prof["error"]))
            continue
        profiled.append(prof)
        add_qlog(qlog, "%s:%s:%s:extract" % (label, table, c["name"]),
                 prof["extraction_sql"], "column value extraction",
                 prof["n_nonnull"])
        add_qlog(qlog, "%s:%s:%s:missing" % (label, table, c["name"]),
                 prof["missing_sql"], "missing/blank value count",
                 prof["n_missing"])
        add_qlog(qlog, "%s:%s:%s:value_counts" % (label, table, c["name"]),
                 prof["value_counts_sql"], "top value counts",
                 len(prof["top_values"]))
        if prof.get("inferred_kind") == "numeric":
            numeric_cols.append({"name": c["name"],
                                 "values": prof["numeric"].get("raw_values",
                                                               [])})
            st = prof["numeric"]["stats"]
            agg = prof.get("aggregate")
            sql_for_evidence = (agg["sql"] if agg and "error" not in agg
                                else prof["extraction_sql"])
            quote = ("n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s"
                     % (st["n"], st["mean"], st["stdev_sample"],
                        st["min"], st["max"]))
            if agg and "error" not in agg:
                add_qlog(qlog, "%s:%s:%s:aggregate" % (label, table, c["name"]),
                         agg["sql"], "SQL aggregate cross-check", agg["n"],
                         {"avg": agg["avg"], "min": agg["min"],
                          "max": agg["max"],
                          "matches_python": agg.get("matches_python")})
            evidence.append({
                "claim": "column %s.%s numeric summary" % (table, c["name"]),
                "locator": sql_for_evidence,
                "quote": quote,
                "role": "statistic"})
        elif prof.get("coded_as_text"):
            degradations.append(
                "%s:%s:%s is declared TEXT but contains numeric values; "
                "converted in Python only, not aggregated in SQL"
                % (label, table, c["name"]))

    corr = dbscan.correlate_table(conn, table, numeric_cols) \
        if len(numeric_cols) >= 2 else []
    for p in corr:
        add_qlog(qlog, "%s:%s:corr:%s-%s" % (label, table, p["a"], p["b"]),
                 "(Python Pearson, pairs of extracted numeric columns)",
                 "Pearson correlation", p["n"], p)

    # charts
    hist_data = []
    for c in columns[:12]:
        prof = next((p for p in profiled if p["column"] == c["name"]), None)
        if prof and prof.get("inferred_kind") == "numeric":
            vals = prof["numeric"].get("raw_values", [])
            if vals:
                hist_data.append((c["name"], vals))
    if hist_data:
        for name, vals in hist_data:
            svg = dbscan.svg_histogram(vals, "%s.%s histogram" % (table, name),
                                       name)
            if svg:
                safe = re.sub(r"[^A-Za-z0-9_]", "_", name)[:24] or "col"
                write_chart(figures_dir,
                            "%s_%s_hist.svg" % (label.replace("/", "_"), safe),
                            svg, artifacts, chart_list, "histogram")
    heat = dbscan.svg_corr_heatmap(corr)
    if heat:
        write_chart(figures_dir, "%s_corr_heatmap.svg"
                    % label.replace("/", "_"), heat, artifacts, chart_list,
                    "correlation-heatmap")
    miss = dbscan.svg_missing_bar(profiled)
    if miss:
        write_chart(figures_dir, "%s_missing_bar.svg" % label.replace("/", "_"),
                    miss, artifacts, chart_list, "missing-bar")

    return {"table": table, "label": label, "row_count": row_count,
            "columns": columns, "profiled": profiled,
            "numeric_cols": numeric_cols, "correlations": corr,
            "duplicates": dup}


def write_chart(figures_dir, fname, content, artifacts, chart_list, kind):
    if content is None:
        return None
    path = os.path.join(figures_dir, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        return None
    rel = os.path.relpath(path, os.path.dirname(figures_dir))
    chart_list.append({"file": rel, "kind": kind})
    artifacts[rel] = "chart"
    return rel


# queries.json handling
def load_queries(work_dir, queries_path, errors):
    path = queries_path or os.path.join(work_dir, "queries.json")
    if not os.path.isfile(path):
        return []
    text = dbscan.read_text_file(path, errors)
    if text is None:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append("%s: invalid JSON (%s)" % (path, exc))
        return []
    if isinstance(data, dict):
        data = data.get("queries", data.get("queries_json", []))
    if not isinstance(data, list):
        return []
    out = []
    for i, item in enumerate(data):
        if isinstance(item, str):
            out.append({"id": "q%d" % (i + 1), "sql": item, "note": ""})
        elif isinstance(item, dict):
            sql = item.get("sql") or item.get("query")
            if sql:
                out.append({"id": item.get("id") or "q%d" % (i + 1),
                            "sql": sql,
                            "note": item.get("note") or item.get("purpose") or ""})
    return out


# contract filling (fuzzy match of computed answers onto contract leaves)
def build_computed(tables, query_results, figures):
    computed = {
        "tables": [t["table"] for t in tables],
        "table_list": [t["table"] for t in tables],
        "row_counts": {t["table"]: t["row_count"] for t in tables},
        "schema": [{"table": t["table"], "columns": t["columns"],
                    "row_count": t["row_count"]} for t in tables],
        "columns": {t["table"]: [c["name"] for c in t["columns"]]
                    for t in tables},
        "duplicate_groups": {t["table"]: t["duplicates"].get("n_duplicate_groups")
                             for t in tables},
        "statistics": [],
        "correlations": [],
        "missing_values": [],
        "value_counts": {},
        "figures": figures,
        "query_log": [],
        "query_results": query_results,
    }
    for t in tables:
        for p in t["profiled"]:
            row = {"table": t["table"], "column": p["column"],
                   "declared_type": p["declared_type"],
                   "inferred_kind": p["inferred_kind"],
                   "n_total": p["n_total"], "n_nonnull": p["n_nonnull"],
                   "n_missing": p["n_missing"], "n_distinct": p["n_distinct"]}
            if p.get("inferred_kind") == "numeric":
                row.update(p["numeric"]["stats"])
            computed["statistics"].append(row)
            computed["missing_values"].append(
                {"table": t["table"], "column": p["column"],
                 "n_missing": p["n_missing"], "n_total": p["n_total"],
                 "missing_pct": (round(100.0 * p["n_missing"] / p["n_total"], 4)
                                 if p["n_total"] else None)})
            computed["value_counts"].setdefault(
                t["table"], {})[p["column"]] = {
                    str(v["value"]): v["count"] for v in p["top_values"]}
        for c in t["correlations"]:
            c2 = dict(c)
            c2["table"] = t["table"]
            computed["correlations"].append(c2)
    return computed


def fill_contract(result, fields, computed, change_log, ts):
    """Fill contract leaves whose path matches a computed answer key."""
    keys = {norm_key(k): k for k in computed}
    aliases = {"schema": "schema", "schemas": "schema", "tables": "tables",
               "tablenames": "tables", "rowcount": "row_counts",
               "rowcounts": "row_counts", "statistics": "statistics",
               "stats": "statistics", "summary": "statistics",
               "correlation": "correlations", "correlations": "correlations",
               "missing": "missing_values", "missingvalues": "missing_values",
               "duplicates": "duplicate_groups", "valuecounts": "value_counts",
               "figures": "figures", "charts": "figures",
               "querylog": "query_log", "answers": "query_results"}
    filled = 0
    for f in fields:
        nk = norm_key(f["path"].split(".")[-1])
        target = None
        if nk in keys:
            target = computed[keys[nk]]
        elif nk in aliases and aliases[nk] in computed:
            target = computed[aliases[nk]]
        elif nk in aliases:
            target = computed.get(aliases[nk])
        # secondary: any computed key contains the leaf name
        if target is None:
            for k, v in computed.items():
                if nk and len(nk) >= 3 and (nk in k or k in nk):
                    target = v
                    break
        if target is not None:
            set_path(result, f["path"], target)
            filled += 1
            change(change_log, ts, "contract-fill",
                   "filled contract field %s from computed answer" % f["path"],
                   "ok")
    return filled, len(fields)


def write_chart(figures_dir, fname, content, artifacts, chart_list, kind):
    if content is None:
        return None
    path = os.path.join(figures_dir, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        return None
    rel = os.path.relpath(path, os.path.dirname(figures_dir))
    chart_list.append({"file": rel, "kind": kind})
    artifacts[rel] = "chart"
    return rel


# main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--queries", default=None)
    args = ap.parse_args(argv)

    input_dir = args.input
    output_dir = args.output
    work_dir = args.work
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    figures_dir = os.path.join(output_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)

    errors, degradations, sources, uncertainty = [], [], [], []
    change_log, pending_human = [], []
    qlog = []
    artifacts = {}
    chart_list = []
    ts = now_ts()
    start = time.time()

    # ---- stage 0: discover + skeleton (always written first) ----
    found = discover_inputs(input_dir)
    case = None
    if found["case"]:
        case = read_json(found["case"], errors, degradations)
    elif found["dbs"] or found["csvs"]:
        # no case.json: fall back to any .json in /input
        jsons = dbscan.discover_files(input_dir, (".json",))
        for jp in jsons:
            case = read_json(jp, errors, degradations)
            if case is not None:
                break

    case_id = extract_case_id(case) if case else None
    contract = extract_contract(case) if case else None
    fields = []
    _leaf_fields(contract, "", fields)
    skeleton = build_skeleton(case_id, fields)

    skeleton.update({
        "status": "partial",
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "analysis": {},
        "query_log": qlog,
        "answers": {},
        "artifacts": artifacts,
        "contract_fidelity": {"total": len(fields), "filled": 0,
                              "unfilled": len(fields), "per_field": []},
        "limitations": [],
        "runtime": {"started_at": ts, "version": VERSION,
                    "seconds": 0.0},
    })
    change(change_log, ts, "skeleton",
           "wrote legal partial skeleton with %d contract fields"
           % len(fields), "ok")
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    if not case:
        if found["case"] is not None:
            skeleton["status"] = "input_error"
            pending_human.append("case.json exists but could not be parsed; "
                                 "see errors")
        else:
            skeleton["status"] = "insufficient_evidence"
            pending_human.append("no case.json / JSON input found; cannot "
                                 "extract case_id or output_contract")
        change(change_log, now_ts(), "degrade",
               "status=%s" % skeleton["status"], "ok")
        _finalize_common(skeleton, output_dir, errors, degradations,
                         pending_human, change_log, qlog, artifacts,
                         chart_list, start)
        return 0

    # ---- stage 1: connect data sources ----
    tables = []
    conns = []
    if found["dbs"]:
        sources.append({"locator": "filesystem", "role": "database",
                        "quote": "SQLite snapshots: %s"
                                 % ", ".join(os.path.basename(p)
                                             for p in found["dbs"])})
    for dbpath in found["dbs"]:
        try:
            conn = dbscan.open_sqlite_ro(dbpath)
        except Exception as exc:
            errors.append("%s: connect failed (%s)" % (dbpath, exc))
            continue
        conns.append(conn)
        label = os.path.splitext(os.path.basename(dbpath))[0]
        for table in dbscan.list_tables(conn):
            if table.startswith("__sqlite_error__"):
                errors.append("%s: list tables failed (%s)" % (dbpath, table))
                continue
            res = analyze_table(conn, table, label, figures_dir, qlog, sources,
                                artifacts, chart_list, errors, degradations)
            tables.append(res)

    for csvpath in found["csvs"]:
        got = dbscan.ingest_csv(csvpath, errors, degradations)
        if got is None:
            continue
        table, conn, src = got
        conns.append(conn)
        label = os.path.splitext(os.path.basename(csvpath))[0]
        res = analyze_table(conn, table, label, figures_dir, qlog, sources,
                            artifacts, chart_list, errors, degradations)
        tables.append(res)
        sources.append({"locator": src, "role": "csv-snapshot",
                        "quote": "table=%s; columns=%s"
                                 % (table, [c["name"] for c in res["columns"]])})

    if not tables and not found["dbs"] and not found["csvs"]:
        pending_human.append("no SQLite or CSV snapshot found under %s"
                             % input_dir)

    # ---- stage 2: execute tagged queries from work/queries.json ----
    query_results = {}
    queries_path = args.queries or os.path.join(work_dir, "queries.json")
    for q in load_queries(work_dir, queries_path, errors):
        # try each query against the first connection (or all)
        ran = False
        for conn in conns:
            res = dbscan.run_query(conn, q["sql"], MAX_QUERY_ROWS)
            if res.get("error"):
                continue
            qid = q["id"]
            add_qlog(qlog, qid, q["sql"], q["note"] or "tagged query",
                     res["row_count"],
                     {"rows": res["rows"][:5], "truncated": res["truncated"]})
            query_results[qid] = {"id": qid, "sql": q["sql"],
                                  "note": q["note"],
                                  "row_count": res["row_count"],
                                  "sample_rows": res["rows"][:5]}
            ran = True
            break
        if not ran:
            errors.append("%s: query failed on all connections" % q["id"])
            add_qlog(qlog, q["id"], q["sql"], q["note"] or "tagged query",
                     None, status="error", error="no connection executed it")

    # ---- stage 3: assemble computed answers + fill contract ----
    computed = build_computed(tables, query_results, chart_list)
    computed["query_log"] = qlog
    filled, total = fill_contract(skeleton, fields, computed, change_log,
                                  now_ts())

    skeleton["analysis"] = {
        "tables": computed["tables"],
        "schema": computed["schema"],
        "row_counts": computed["row_counts"],
        "statistics": computed["statistics"],
        "correlations": computed["correlations"],
        "missing_values": computed["missing_values"],
        "duplicate_groups": computed["duplicate_groups"],
        "value_counts": computed["value_counts"],
    }
    skeleton["answers"] = query_results
    skeleton["query_log"] = qlog
    skeleton["artifacts"] = artifacts
    skeleton["contract_fidelity"] = {
        "total": total, "filled": filled, "unfilled": total - filled,
        "per_field": [
            {"path": f["path"],
             "filled": get_path(skeleton, f["path"]) is not None}
            for f in fields]}

    # ---- stage 4: write auxiliary artifacts ----
    write_json(os.path.join(output_dir, "schema.json"),
               {"tables": computed["schema"]})
    write_json(os.path.join(output_dir, "evidence_map.json"), sources)
    write_json(os.path.join(output_dir, "value_counts.json"),
               computed["value_counts"])

    summary_rows = []
    for t in tables:
        for p in t["profiled"]:
            row = [t["table"], p["column"], p["inferred_kind"],
                   p["n_total"], p["n_nonnull"], p["n_missing"],
                   p["n_distinct"]]
            if p.get("inferred_kind") == "numeric":
                s = p["numeric"]["stats"]
                row += [s["mean"], s["stdev_sample"], s["min"], s["q1"],
                        s["median"], s["q3"], s["max"], s["iqr"],
                        s["n_outliers"]]
            else:
                row += [None] * 9
            summary_rows.append(row)
    write_csv(os.path.join(output_dir, "summary_stats.csv"),
              ["table", "column", "kind", "n_total", "n_nonnull", "n_missing",
               "n_distinct", "mean", "stdev_sample", "min", "q1", "median",
               "q3", "max", "iqr", "n_outliers"], summary_rows)

    dq_rows = []
    for t in tables:
        for p in t["profiled"]:
            dq_rows.append([t["table"], p["column"], p["declared_type"],
                            p["inferred_kind"], p["coded_as_text"],
                            p["n_total"], p["n_missing"],
                            (round(100.0 * p["n_missing"] / p["n_total"], 4)
                             if p["n_total"] else None),
                            p["n_distinct"],
                            p["top_values"][0]["value"] if p["top_values"]
                            else None,
                            p["top_values"][0]["count"] if p["top_values"]
                            else None])
        dup = t["duplicates"]
        dq_rows.append([t["table"], "(full-row)", "", "duplicate-group", "",
                        dup.get("n_duplicate_groups"),
                        dup.get("n_rows_in_duplicate_groups"), None,
                        dup.get("n_duplicate_groups"), None, None])
    write_csv(os.path.join(output_dir, "data_quality.csv"),
              ["table", "column", "declared_type", "inferred_kind",
               "coded_as_text", "n_total", "n_missing", "missing_pct",
               "n_distinct", "top_value", "top_count"], dq_rows)

    corr_rows = []
    for t in tables:
        for c in t["correlations"]:
            corr_rows.append([t["table"], c["a"], c["b"], c["pearson_r"],
                              c["n"]])
    write_csv(os.path.join(output_dir, "correlation_matrix.csv"),
              ["table", "col_a", "col_b", "pearson_r", "n"], corr_rows)

    qlog_rows = [[q.get("id"), q.get("sql"), q.get("purpose"),
                  q.get("row_count"), q.get("status"),
                  json.dumps(q.get("summary") or {}, ensure_ascii=False,
                             default=str)]
                 for q in qlog]
    write_csv(os.path.join(output_dir, "query_log.csv"),
              ["id", "sql", "purpose", "row_count", "status", "summary"],
              qlog_rows)

    # ---- stage 5: honest status + report ----
    has_data = bool(tables)
    unfilled = total - filled
    status = finalize_status(has_data, bool(errors), unfilled)
    if not tables and (found["dbs"] or found["csvs"]):
        pending_human.append("data snapshots present but no table was profiled")
    skeleton["status"] = status
    skeleton["sources"] = sources
    skeleton["uncertainty"] = uncertainty
    skeleton["pending_human"] = pending_human
    skeleton["change_log"] = change_log
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations
    skeleton["limitations"] = list(dict.fromkeys(
        ["%d of %d contract fields remain unfilled" % (unfilled, total)]
        if unfilled else []))

    report = build_report(skeleton, tables, degradations, errors,
                          pending_human, skeleton["contract_fidelity"])
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    artifacts["report.md"] = "report"
    for key, val in [("schema.json", "schema"),
                     ("evidence_map.json", "evidence"),
                     ("summary_stats.csv", "statistics"),
                     ("data_quality.csv", "data-quality"),
                     ("correlation_matrix.csv", "correlation"),
                     ("query_log.csv", "query-log"),
                     ("value_counts.json", "value-counts")]:
        artifacts[key] = val
    skeleton["artifacts"] = artifacts

    _finalize_common(skeleton, output_dir, errors, degradations, pending_human,
                     change_log, qlog, artifacts, chart_list, start)
    return 0


def finalize_status(has_data, has_errors, unfilled):
    if not has_data:
        return "insufficient_evidence"
    if has_errors:
        return "partial"
    if unfilled == 0:
        return "ok"
    return "partial"


def _finalize_common(result, output_dir, errors, degradations, pending_human,
                     change_log, qlog, artifacts, chart_list, start):
    result["runtime"]["seconds"] = round(time.time() - start, 3)
    change(change_log, now_ts(), "finalize",
           "final status=%s" % result["status"], "ok")
    write_json(os.path.join(output_dir, "result.json"), result)


if __name__ == "__main__":
    sys.exit(main())

