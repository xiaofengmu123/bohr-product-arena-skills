#!/usr/bin/env python3
"""main: deterministic read-only scientific DB/CSV query & analysis pipeline.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work \\
      --queries /app/work/queries.json

Stages: 0) discover input and write a legal result.json skeleton immediately;
1) inspect each SQLite/CSV snapshot read-only; 2) profile columns (missing,
duplicates, coded fields, statistics), Pearson correlations and editable SVG
charts with CSV data; 3) execute tagged read-only queries; 4) emit
report/evidence/schema/CSV and finalise an honest status.

Only the Python standard library is used.
"""

import argparse
import csv as _csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_impl as ci

VERSION = "1.1.0"
MAX_QUERY_ROWS = 200

# Envelope keys that belong to the Skill contract rather than the task's
# output_contract. Used to distinguish unfilled contract fields.
ENVELOPE_KEYS = frozenset({
    "case_id", "schema_version", "status", "sources", "uncertainty",
    "pending_human", "change_log", "analysis", "query_log", "answers",
    "artifacts", "errors", "degradations", "limitations", "evidence_map",
})


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---
# discovery & contract
# ---
def discover(input_dir):
    """Scan the input directory; never assume a fixed file name."""
    out = {"case_json": None, "task_md": None, "dbs": [], "csvs": [],
           "text": [], "other": []}
    root = Path(input_dir)
    if not root.exists() or not root.is_dir():
        return out, "input directory does not exist: %s" % input_dir
    try:
        files = sorted(str(p) for p in root.rglob("*") if p.is_file())
    except Exception as exc:  # noqa: BLE001
        return out, "input scan failed: %s" % exc
    for f in files:
        name = os.path.basename(f)
        lower = name.lower()
        if lower == "case.json" and out["case_json"] is None:
            out["case_json"] = f
        elif lower.endswith((".db", ".sqlite", ".sqlite3", ".db3")):
            out["dbs"].append(f)
        elif lower.endswith((".csv", ".tsv")):
            out["csvs"].append(f)
        elif lower == "task.md":
            out["task_md"] = f
        elif lower.endswith((".md", ".json", ".yaml", ".yml")):
            out["text"].append(f)
        else:
            out["other"].append(f)
    if out["case_json"] is None:
        for f in out["text"]:
            if f.lower().endswith(".json"):
                out["case_json"] = f
                break
    return out, None


def default_contract_value(v):
    """Echo the contract shape with null leaves and empty lists; never copy
    prepared answer content."""
    if isinstance(v, dict):
        return {k: default_contract_value(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return []
    return None


def read_case(case_json_path):
    """Return (raw, meta, error)."""
    data, err = ci.read_json(case_json_path)
    if err is not None:
        return None, None, err
    case_id = None
    for key in ("case_id", "caseId", "id"):
        if key in data and isinstance(data[key], (str, int)):
            case_id = data[key]
            break
    contract = None
    for key in ("output_contract", "outputContract", "contract",
                "expected_output", "result_schema", "answer_contract"):
        if key in data and isinstance(data[key], dict):
            contract = data[key]
            break
    return data, {"case_id": case_id, "contract": contract,
                  "raw_keys": sorted(data.keys())}, None


def build_skeleton(case_id, contract):
    skeleton = {}
    if contract:
        for key, val in contract.items():
            skeleton[key] = default_contract_value(val)
    skeleton["case_id"] = case_id
    skeleton["schema_version"] = VERSION
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["pending_human"] = []
    skeleton["change_log"] = []
    skeleton["analysis"] = {}
    skeleton["query_log"] = []
    skeleton["answers"] = {}
    skeleton["artifacts"] = {}
    skeleton["errors"] = []
    skeleton["degradations"] = []
    skeleton["limitations"] = []
    skeleton["evidence_map"] = []
    return skeleton


# ---
# result mutation helpers
# ---
def add_change(change_log, ts, step, change, status="done"):
    change_log.append({"ts": ts, "step": step, "change": change,
                       "status": status})


def add_error(result, msg, ts, step="pipeline"):
    result["errors"].append({"step": step, "message": msg, "ts": ts})


def add_degradation(result, item, reason, ts, fallback=None):
    entry = {"item": item, "reason": reason, "fallback": fallback or "kept as null/omitted"}
    result["degradations"].append(entry)
    result["limitations"].append(
        "%s: %s (fallback: %s)" % (item, reason, fallback or "kept as null/omitted"))


def add_uncertainty(result, item, level, reason, impact=None):
    result["uncertainty"].append(
        {"item": item, "level": level, "reason": reason,
         "impact": impact or "interpretation weakened"})


def add_source(result, locator, quote, role):
    result["sources"].append({"locator": locator, "quote": str(quote),
                              "role": role})


def add_evidence(result, claim, locator, quote, role, confidence="high"):
    item = {"claim": claim, "locator": locator, "quote": str(quote),
            "role": role, "confidence": confidence}
    result["evidence_map"].append(item)
    result["sources"].append({"locator": locator, "quote": str(quote),
                              "role": role})


# ---
# database / CSV analysis
# ---
def analyze_database(db_path, result, qlog, artifacts, figures_dir,
                     chart_list, ts):
    label = os.path.basename(db_path)
    conn, err = ci.connect_sqlite(db_path)
    if conn is None:
        add_error(result, "%s: %s" % (label, err), ts, "connect")
        add_degradation(result, "database %s" % label, err, ts)
        return

    tables, err = ci.list_tables(conn)
    if err is not None:
        add_error(result, "%s: %s" % (label, err), ts, "list_tables")
        add_degradation(result, "tables of %s" % label, err, ts)
    if not tables:
        add_degradation(result, "database %s" % label,
                        "no user tables/views found", ts, "recorded only")
        conn.close()
        return

    db_record = {"file": db_path, "label": label, "tables": []}
    table_metas = []

    for table in tables:
        tname = table["name"]
        tinfo, terr = ci.table_info(conn, tname)
        if terr is not None:
            add_error(result, "%s.%s: %s" % (label, tname, terr), ts,
                      "table_info")
        row_count, rerr = ci.table_row_count(conn, tname)
        if rerr is not None:
            add_error(result, "%s.%s: %s" % (label, tname, rerr), ts,
                      "row_count")
        columns = [c["name"] for c in tinfo]
        table_metas.append({"name": tname, "columns": columns,
                            "columns_meta": tinfo, "row_count": row_count})

        add_evidence(
            result,
            "table %s has %d columns and %d rows" % (tname, len(tinfo),
                                                     row_count if row_count is not None else 0),
            "sqlite://%s#table=%s" % (label, tname),
            "columns=%s; row_count=%s" % (columns, row_count),
            "schema")
        qlog.append({"id": "%s:%s:schema" % (label, tname),
                     "sql": "PRAGMA table_info(%s)" % ci.quote_ident(tname),
                     "purpose": "schema discovery", "row_count": len(tinfo),
                     "status": "ok" if not terr else "error",
                     "error": terr})

        dup = ci.detect_duplicates(conn, tname)
        if dup.get("error"):
            add_degradation(result, "duplicate detection %s.%s" % (label, tname),
                            dup["error"], ts)
        else:
            add_evidence(
                result,
                "table %s has %d exact full-row duplicate groups" %
                (tname, dup["n_duplicate_groups"]),
                dup["full_row_dup_sql"],
                "n_duplicate_groups=%d; n_rows_in_groups=%d"
                % (dup["n_duplicate_groups"], dup["n_rows_in_duplicate_groups"]),
                "data-quality")
            qlog.append({"id": "%s:%s:duplicates" % (label, tname),
                         "sql": dup["full_row_dup_sql"],
                         "purpose": "exact full-row duplicate detection",
                         "row_count": dup["n_duplicate_groups"], "status": "ok"})

        profiled = []
        numeric_cols = []
        for cmeta in tinfo:
            cname = cmeta["name"]
            vals, extract_sql, verr = ci.extract_column(conn, tname, cname)
            if verr is not None:
                add_error(result, "%s.%s.%s: %s" % (label, tname, cname, verr),
                          ts, "extract")
                continue
            prof = ci.profile_values(vals, cmeta["type"])
            prof["column"] = cname
            profiled.append(prof)
            key = "%s:%s:%s" % (label, tname, cname)
            result["analysis"]["column_profiles"][key] = prof
            qlog.append({"id": "%s:%s:%s:extract" % (label, tname, cname),
                         "sql": extract_sql, "purpose": "column value extraction",
                         "row_count": prof["n_total"], "status": "ok"})

            if prof["coded_as_text"]:
                add_uncertainty(
                    result, "field %s.%s" % (tname, cname), "low",
                    "declared type %s but values are numeric-coded text; "
                    "numeric statistics use an explicit text->float coercion "
                    "rule and non-numeric values are treated as missing"
                    % (cmeta["type"] or "none"),
                    "check the data dictionary before interpreting as a number")
            if prof["conversion_note"] and prof["kind"] == "numeric":
                add_uncertainty(result, "field %s.%s" % (tname, cname),
                                "medium", "numeric coercion: %s"
                                % prof["conversion_note"],
                                "subset used for numeric statistics is smaller")

            if prof["kind"] == "numeric" and prof.get("stats"):
                numeric_cols.append({"name": cname, "values": vals})
                st = prof["stats"]
                add_evidence(
                    result,
                    "numeric summary of %s.%s (n=%d)" % (tname, cname, st["n"]),
                    extract_sql,
                    "n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s"
                    % (st["n"], st["mean"], st["stdev_sample"], st["min"],
                       st["max"]),
                    "statistic")
                if st["ci95_mean"]:
                    add_uncertainty(
                        result, "mean of %s.%s" % (tname, cname), "low",
                        "sampling uncertainty: 95%% CI for the mean "
                        "= [%s, %s] assuming approximate normality and "
                        "independent observations" % (st["ci95_mean"][0],
                                                      st["ci95_mean"][1]),
                        "precision of the reported mean")

            # charts
            if prof["kind"] == "numeric" and prof.get("stats"):
                hist = ci.histogram(prof.get("numeric_values") or
                                    [v for v in vals if v is not None])
                if hist and hist["n"] >= 1:
                    _write_chart(figures_dir, "%s__%s__hist.svg"
                                 % (tname, cname),
                                 ci.render_histogram_svg(hist, "%s.%s distribution"
                                                          % (tname, cname)),
                                 artifacts, chart_list, "histogram")
                    _write_chart_data(figures_dir, "%s__%s__hist.csv"
                                      % (tname, cname),
                                      ["bin_start", "count"],
                                      [[hist["bin_edges"][i], hist["counts"][i]]
                                       for i in range(len(hist["counts"]))],
                                      artifacts)
            elif prof["kind"] in ("text", "numeric") and prof["n_distinct"] and \
                    prof["n_distinct"] <= 20 and prof["top_values"]:
                vc = prof["top_values"]
                _write_chart(figures_dir, "%s__%s__bar.svg" % (tname, cname),
                             ci.render_bar_svg(vc, "%s.%s value counts"
                                                % (tname, cname)),
                             artifacts, chart_list, "bar")
                _write_chart_data(figures_dir, "%s__%s__bar.csv"
                                  % (tname, cname), ["value", "count"],
                                  [[str(i["value"]), i["count"]] for i in vc],
                                  artifacts)

        # correlation matrix (per table, numeric columns only)
        if len(numeric_cols) >= 2:
            labels = [c["name"] for c in numeric_cols]
            n = len(labels)
            matrix = [[None] * n for _ in range(n)]
            long_rows = []
            for i in range(n):
                for j in range(i, n):
                    pair, perr = ci.fetch_pairs(conn, tname, labels[i],
                                                labels[j])
                    if perr is not None:
                        add_degradation(result,
                                        "correlation %s.%s x %s.%s"
                                        % (tname, labels[i], tname, labels[j]),
                                        perr, ts)
                        continue
                    a, b = pair
                    r, npair = ci.pearson(a, b)
                    matrix[i][j] = r
                    matrix[j][i] = r
                    long_rows.append({"column_a": labels[i],
                                      "column_b": labels[j], "n": npair,
                                      "pearson_r": r})
            result["analysis"]["correlations"]["%s:%s" % (label, tname)] = {
                "labels": labels, "matrix": matrix}
            result["analysis"]["correlations_long"]["%s:%s" % (label, tname)] = \
                long_rows
            _write_chart(figures_dir, "%s__corr.svg" % tname,
                         ci.render_heatmap_svg(matrix, labels,
                                                "%s Pearson correlations"
                                                % tname),
                         artifacts, chart_list, "heatmap")
        elif numeric_cols:
            add_degradation(
                result, "correlation matrix %s.%s" % (label, tname),
                "only %d numeric column(s); need >=2" % len(numeric_cols), ts,
                "matrix omitted")

        db_record["tables"].append({"name": tname, "columns": tinfo,
                                    "row_count": row_count, "profiles": profiled})
        result["analysis"]["tables"].append({
            "database": label, "table": tname, "row_count": row_count,
            "n_columns": len(tinfo),
            "n_missing_total": sum(p["n_missing"] for p in profiled)})

    # cross-table candidate join keys (honest: potential, not asserted FK)
    for i in range(len(table_metas)):
        for j in range(i + 1, len(table_metas)):
            la = table_metas[i]["name"]
            lb = table_metas[j]["name"]
            ca = set(table_metas[i]["columns"])
            cb = set(table_metas[j]["columns"])
            for shared in sorted(ca & cb):
                note = ("shared column name found in %s and %s; potential "
                        "join key - verify semantics and one-to-many direction "
                        "against the data dictionary before aggregating"
                        % (la, lb))
                result["analysis"]["cross_table"].append(
                    {"left_table": la, "right_table": lb,
                     "shared_column": shared, "note": note})
                add_uncertainty(result, "join path %s.%s=%s.%s"
                                % (la, shared, lb, shared), "medium", note,
                                "joining on this column may inflate rows")

    result["analysis"]["databases"].append(db_record)
    conn.close()


def analyze_csv(csv_path, result, qlog, artifacts, figures_dir, chart_list, ts):
    label = os.path.basename(csv_path)
    table, err = ci.read_csv_as_table(csv_path)
    if err is not None:
        add_error(result, "%s: %s" % (label, err), ts, "read_csv")
        add_degradation(result, "CSV %s" % label, err, ts)
        return
    n_rows = len(table["rows"])
    qlog.append({"id": "csv:%s:load" % label, "sql": None,
                 "purpose": "CSV load (delimiter=%s, encoding=%s)"
                 % (table["delimiter"], table.get("encoding")),
                 "row_count": n_rows, "status": "ok"})
    add_evidence(result, "CSV %s loaded with %d rows and %d columns"
                 % (label, n_rows, len(table["columns"])),
                 "csv://%s" % label,
                 "rows=%d; columns=%s" % (n_rows,
                                          [c["name"] for c in table["columns"]]),
                 "schema")
    profiled = []
    numeric_cols = []
    for c in table["columns"]:
        prof = ci.profile_values(c["values"], "")
        prof["column"] = c["name"]
        profiled.append(prof)
        key = "csv:%s:%s" % (label, c["name"])
        result["analysis"]["column_profiles"][key] = prof
        if prof["kind"] == "numeric" and prof.get("stats"):
            numeric_cols.append({"name": c["name"], "values": c["values"]})
        if prof["kind"] == "numeric" and prof.get("stats"):
            hist = ci.histogram(prof.get("numeric_values") or
                                [v for v in c["values"] if v is not None])
            if hist:
                _write_chart(figures_dir, "%s__%s__hist.svg"
                             % (label, c["name"]),
                             ci.render_histogram_svg(hist, "%s.%s distribution"
                                                      % (label, c["name"])),
                             artifacts, chart_list, "histogram")
        elif prof["n_distinct"] and prof["n_distinct"] <= 20 and \
                prof["top_values"]:
            _write_chart(figures_dir, "%s__%s__bar.svg" % (label, c["name"]),
                         ci.render_bar_svg(prof["top_values"],
                                            "%s.%s value counts"
                                            % (label, c["name"])),
                         artifacts, chart_list, "bar")
    result["analysis"]["csv_datasets"].append(
        {"file": csv_path, "label": label, "row_count": n_rows,
         "n_columns": len(table["columns"]), "profiles": profiled})


# ---
# chart / artifact writers
# ---
def _register_artifact(artifacts, rel, kind):
    artifacts[rel] = kind


def _write_chart(figures_dir, fname, svg, artifacts, chart_list, kind):
    if svg is None:
        return None
    path = os.path.join(figures_dir, fname)
    err = ci.write_text(path, svg)
    if err is not None:
        return None
    rel = os.path.relpath(path, os.path.dirname(figures_dir))
    chart_list.append({"file": rel, "kind": kind})
    _register_artifact(artifacts, rel, "chart")
    return rel


def _write_chart_data(figures_dir, fname, header, rows, artifacts):
    path = os.path.join(figures_dir, fname)
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(header)
            for r in rows:
                w.writerow(r)
        rel = os.path.relpath(path, os.path.dirname(figures_dir))
        _register_artifact(artifacts, rel, "chart-data")
    except Exception:  # noqa: BLE001 - best effort only
        pass


# ---
# queries
# ---
def run_queries(queries_path, conn, result, qlog, answers):
    data, err = ci.read_json(queries_path)
    if err is not None:
        add_error(result, "queries file: %s" % err, iso_now(), "queries")
        return
    items = data.get("queries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        qid = item.get("id") or item.get("qid") or ("q%d" % (idx + 1))
        sql = item.get("sql") or item.get("query")
        purpose = item.get("note") or item.get("purpose") or ""
        if not sql:
            qlog.append({"id": qid, "sql": None, "purpose": purpose,
                         "row_count": None, "status": "error",
                         "error": "missing sql"})
            continue
        res, qerr = ci.run_select(conn, sql, MAX_QUERY_ROWS)
        if qerr is not None:
            qlog.append({"id": qid, "sql": sql, "purpose": purpose,
                         "row_count": None, "status": "error", "error": qerr})
            result["errors"].append({"step": "query %s" % qid, "sql": sql,
                                     "message": qerr})
            continue
        summary = None
        if res["rows"]:
            summary = res["rows"][:5]
        qlog.append({"id": qid, "sql": sql, "purpose": purpose,
                     "row_count": res["row_count"], "status": "ok",
                     "summary": summary,
                     "truncated": res["truncated"]})
        answers[qid] = {"row_count": res["row_count"],
                        "columns": res["columns"],
                        "rows": res["rows"],
                        "truncated": res["truncated"]}
        if item.get("answer_key") and res["rows"]:
            first = res["rows"][0]
            answers[item["answer_key"]] = first[0] if len(first) == 1 else first


def merge_answers(result):
    """Copy answer entries whose key matches a contract field into the field."""
    if not result.get("answers"):
        return
    contract_keys = [k for k in result.keys() if k not in ENVELOPE_KEYS]
    for key in contract_keys:
        if key in result["answers"]:
            result[key] = result["answers"][key]


# ---
# status decision (honest, rule-based)
# ---
def determine_status(result, has_data):
    if not has_data:
        return "insufficient_evidence"
    if result.get("errors") and not result.get("analysis", {}).get("tables") \
            and not result.get("analysis", {}).get("csv_datasets"):
        return "input_error"
    contract_keys = [k for k in result.keys() if k not in ENVELOPE_KEYS]
    unfilled = [k for k in contract_keys if result.get(k) is None]
    if unfilled:
        result["limitations"].append(
            "unfilled output_contract fields: %s" % ", ".join(unfilled))
        return "partial"
    if result.get("errors") or result.get("degradations"):
        return "partial"
    return "ok"


# ---
# pipeline
# ---
def run_pipeline(input_dir, output_dir, work_dir, queries_path=None,
                 clock=iso_now):
    """Run the full pipeline. Returns the final result dict."""
    ts = clock()
    for d in (output_dir, work_dir):
        ci.ensure_dir(d)
    figures_dir = os.path.join(output_dir, "figures")
    ci.ensure_dir(figures_dir)

    result = None
    qlog = []
    artifacts = {}
    chart_list = []

    # --- stage 0: discover and immediately write a legal skeleton -----------
    found, derr = discover(input_dir)
    if derr is not None:
        result = build_skeleton(None, None)
        add_error(result, derr, ts, "discover")
        result["status"] = "input_error"
    else:
        case_id, contract = None, None
        if found["case_json"] is not None:
            raw, meta, cerr = read_case(found["case_json"])
            if cerr is not None:
                result = build_skeleton(None, None)
                result["analysis"]["inputs"] = found
                result["status"] = "input_error"
                add_error(result, "%s: %s" % (found["case_json"], cerr), ts,
                          "case_json")
            else:
                case_id = meta["case_id"]
                contract = meta["contract"]
                result = build_skeleton(case_id, contract)
                result["analysis"]["inputs"] = found
                add_source(result, "file://%s" % found["case_json"],
                           "case_id=%s; raw_keys=%s"
                           % (case_id, meta["raw_keys"]), "contract")
        else:
            result = build_skeleton(None, None)
            result["analysis"]["inputs"] = found
            add_degradation(result, "case.json", "not found in input", ts,
                            "case_id=null; generic envelope only")
    result["analysis"]["inputs"] = found
    result["analysis"]["tables"] = []
    result["analysis"]["column_profiles"] = {}
    result["analysis"]["correlations"] = {}
    result["analysis"]["correlations_long"] = {}
    result["analysis"]["cross_table"] = []
    result["analysis"]["csv_datasets"] = []
    result["analysis"]["databases"] = []
    add_change(result["change_log"], ts, "stage0-skeleton",
               "legal result skeleton written", "done")
    ci.write_json(os.path.join(output_dir, "result.json"), result)

    # --- stage 1: databases -------------------------------------------------
    for db_path in found["dbs"]:
        analyze_database(db_path, result, qlog, artifacts, figures_dir,
                         chart_list, ts)
    for csv_path in found["csvs"]:
        analyze_csv(csv_path, result, qlog, artifacts, figures_dir,
                    chart_list, ts)

    has_data = bool(found["dbs"] or found["csvs"])
    if not has_data:
        result["pending_human"].append(
            "no SQLite database or CSV snapshot found under %s; supply an "
            "authorized read-only data snapshot" % input_dir)

    # --- stage 2: targeted queries ------------------------------------------
    conn = None
    if found["dbs"]:
        conn, _cerr = ci.connect_sqlite(found["dbs"][0])
    qpath = queries_path or os.path.join(work_dir, "queries.json")
    if os.path.exists(qpath):
        if conn is None and found["dbs"]:
            add_error(result, "cannot open primary DB for queries", ts,
                      "queries")
        else:
            run_queries(qpath, conn, result, qlog, result["answers"])
    if conn is not None:
        conn.close()

    merge_answers(result)

    # --- stage 3: emit tables and report ------------------------------------
    ci.write_summary_csv(result, output_dir, artifacts)
    ci.write_quality_csv(result, output_dir, artifacts)
    ci.write_correlation_csv(result, output_dir, artifacts)
    result["query_log"] = qlog
    ci.write_query_log_csv(qlog, output_dir, artifacts)
    ci.write_json(os.path.join(output_dir, "value_counts.json"),
                  {k: v.get("top_values", [])
                   for k, v in result["analysis"]["column_profiles"].items()})
    _register_artifact(artifacts, "value_counts.json", "table")
    ci.write_json(os.path.join(output_dir, "evidence_map.json"),
                  result["evidence_map"])
    _register_artifact(artifacts, "evidence_map.json", "evidence")
    schema_out = {"databases": result["analysis"].get("databases", []),
                  "tables": result["analysis"].get("tables", [])}
    ci.write_json(os.path.join(output_dir, "schema.json"), schema_out)
    _register_artifact(artifacts, "schema.json", "schema")

    result["artifacts"] = artifacts
    result["analysis"]["charts"] = chart_list
    add_change(result["change_log"], ts, "stage3-tables",
               "CSV tables, evidence map, schema and report written", "done")

    # --- stage 4: honest status and finalise --------------------------------
    result["status"] = determine_status(result, has_data)
    add_change(result["change_log"], ts, "stage4-finalize",
               "status=%s" % result["status"], "done")
    ci.write_report(result, output_dir, artifacts, ts)
    _register_artifact(artifacts, "result.json", "result")
    err = ci.write_json(os.path.join(output_dir, "result.json"), result)
    if err is not None:
        # last-resort minimal legal skeleton; do not crash the process
        minimal = build_skeleton(result.get("case_id"), None)
        minimal["status"] = "input_error"
        minimal["errors"] = [{"step": "finalize", "message": err}]
        ci.write_json(os.path.join(output_dir, "result.json"), minimal)
        return minimal
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="read-only scientific DB/CSV analysis pipeline")
    ap.add_argument("--input", default="/input",
                    help="input directory (default: /input)")
    ap.add_argument("--output", default="/app/output",
                    help="output directory (default: /app/output)")
    ap.add_argument("--work", default="/app/work",
                    help="work directory (default: /app/work)")
    ap.add_argument("--queries", default=None,
                    help="optional queries JSON (default: <work>/queries.json)")
    args = ap.parse_args(argv)
    result = run_pipeline(args.input, args.output, args.work, args.queries)
    return 0 if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
