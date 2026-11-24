#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic read-only scientific DB query & analysis pipeline.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work

Behaviour (see SKILL.md for the operating contract):
  1. discover /input without assuming fixed names;
  2. immediately write a legal result.json skeleton (case_id + every
     output_contract key + status/sources/uncertainty/change_log);
  3. open each data source read-only, profile every column, detect missing
     values / duplicates / coded-as-text columns, compute descriptive stats,
     Pearson correlations and editable SVG charts;
  4. execute tagged, read-only queries found in <work>/queries.json and merge
     <work>/contract_answers.json so contract fields are filled incrementally;
  5. emit report.md, evidence_map.json, schema.json, CSV tables and a
     query_log, then finalise an honest status.

Standard library only. No prepared answers are produced.
"""

import argparse
import csv as _csv
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbutil
import charts

VERSION = "1.1.0"
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_QUERY_ROWS = 200
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
QUERIES_FILE = "queries.json"
CONTRACT_ANSWERS_FILE = "contract_answers.json"


def _iso_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fallback(obj):
    try:
        return dbutil.jsonable(obj)
    except Exception:
        return str(obj)


def _log(change_log, step, change, status="ok"):
    change_log.append({"ts": _iso_ts(), "step": step, "change": change,
                       "status": status})


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_fallback)


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([dbutil.jsonable(x) for x in r])


def _pick_conn(tables, sql):
    tokens = [t for t in re.split(r"[^0-9A-Za-z_]", sql) if t]
    refs = set()
    for i, tok in enumerate(tokens):
        if tok.lower() in ("from", "join", "into", "update", "table"):
            if i + 1 < len(tokens):
                refs.add(tokens[i + 1].strip('"'))
    known = {t["table"]: t["conn"] for t in tables}
    candidates = [known[r] for r in refs if r in known]
    if candidates:
        return candidates[0]
    if tables:
        return tables[0]["conn"]
    return None


def _execute_query(conn, sql):
    if not sql or not sql.strip():
        return None, None, "empty_sql"
    first = sql.strip().split(None, 1)[0].lower().rstrip(";")
    if first not in dbutil.ALLOWED_FIRST:
        return None, None, "disallowed_statement:%s" % first
    cur = conn.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in (cur.description or [])]
    return cols, rows, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    args = ap.parse_args(argv)

    out_dir = Path(args.output)
    work_dir = Path(args.work)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    figures_dir = out_dir / "figures"
    os.makedirs(figures_dir, exist_ok=True)

    t0 = time.time()
    result = {
        "case_id": None, "status": "partial", "sources": [],
        "uncertainty": [], "change_log": [], "analysis": {},
        "query_log": [], "answers": {}, "artifacts": {},
        "errors": [], "degradations": [], "limitations": [],
        "pending_human": [], "runtime": {},
    }
    change_log = result["change_log"]

    # ---- discover + read case identity ----------------------------------- #
    found = dbutil.discover(args.input)
    case_data, case_err = (None, "no_case_json")
    if found["case_json"] is not None:
        case_data, case_err = dbutil.load_json(found["case_json"])
    case_id = None
    contract = {}
    if isinstance(case_data, dict):
        case_id = case_data.get("case_id")
        raw = case_data.get("output_contract", {})
        if isinstance(raw, dict):
            contract = raw
        elif isinstance(raw, list):
            contract = {str(k): None for k in raw}
    if case_err and found["case_json"] is None:
        result["errors"].append({"item": "case.json", "detail": case_err})

    result["case_id"] = case_id
    contract_keys = []
    for k in contract:
        if k not in result:
            result[k] = None
            contract_keys.append(k)
    result["analysis"]["contract"] = contract

    # ---- write the early legal skeleton (before any analysis) ------------- #
    result["runtime"] = {
        "start_epoch": t0, "version": VERSION,
        "python": sys.version.split()[0],
        "status": "skeleton",
    }
    _log(change_log, "skeleton", "legal partial skeleton written", "partial")
    _write_json(out_dir / "result.json", result)

    # ---- open data sources -------------------------------------------------- #
    tables = dbutil.open_tables(found, result["errors"], result["degradations"])
    has_data = len(tables) > 0
    _log(change_log, "sources", "%d data tables opened" % len(tables),
         "ok" if has_data else "degraded")

    if not has_data:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append(
            {"item": "data_sources",
             "detail": "no readable SQLite/CSV snapshot found in /input"})
        result["runtime"]["status"] = "done"
        result["runtime"]["duration_sec"] = round(time.time() - t0, 2)
        _finalize_missing(out_dir, result)
        _log(change_log, "finalize", "no data: insufficient_evidence", "degraded")
        _write_json(out_dir / "result.json", result)
        return 0

    schema = dbutil.inspect_schema(tables)
    schema = dbutil.annotate_dictionary(found["dicts"], schema, result["errors"])

    # ---- task.md / dictionary evidence -------------------------------------- #
    _add_text_source(result, found["task_md"], "task")
    for f in found["dicts"]:
        _add_text_source(result, f, "dictionary")

    # ---- per-table profiling ------------------------------------------------ #
    summary_rows = []
    dq_rows = []
    vc = {}
    corr_pairs_all = []
    numeric_meta = []
    evidence = []
    chart_files = []

    for t in schema:
        conn = next(x["conn"] for x in tables if x["table"] == t["table"]
                    and x["label"] == t["label"])
        columns = t["columns"]
        dup = dbutil.detect_duplicates(conn, t["table"], columns)
        if dup.get("full_row_dup_sql"):
            result["query_log"].append({
                "id": "%s:duplicates" % t["label"],
                "sql": dup["full_row_dup_sql"],
                "purpose": "exact full-row duplicate detection",
                "row_count": dup.get("n_duplicate_groups", 0),
                "status": "ok", "summary": {
                    "n_duplicate_groups": dup.get("n_duplicate_groups"),
                    "n_rows_in_duplicate_groups": dup.get("n_rows_in_duplicate_groups"),
                },
            })
        evidence.append({
            "claim": "table %s has %d columns and %d rows" % (t["table"], len(columns), t["row_count"]),
            "locator": "sqlite://%s#table=%s" % (t["label"], t["table"]),
            "quote": "row_count=%d; columns=%s" % (t["row_count"], [c["name"] for c in columns]),
            "role": "schema",
        })
        result["sources"].append({
            "locator": "sqlite://%s#table=%s" % (t["label"], t["table"]),
            "quote": "row_count=%d" % t["row_count"],
            "role": "schema",
        })

        table_numeric = []
        miss_items = []
        for c in columns:
            prof = dbutil.column_profile(conn, t["table"], c["name"], c["type"])
            vals = None
            if prof.get("numeric") and "values" in prof["numeric"]:
                vals = prof["numeric"].pop("values")
            prof["table"] = t["table"]
            prof["label"] = t["label"]
            if vals is not None:
                table_numeric.append({"name": c["name"], "values": vals})
                numeric_meta.append({"table": t["table"], "column": c["name"]})
            if prof.get("numeric") and prof["numeric"].get("aggregate"):
                agg = prof["numeric"]["aggregate"]
                if "error" not in agg:
                    mean_py = prof["numeric"].get("mean")
                    matches = (mean_py is not None and agg["avg"] is not None
                               and abs(float(agg["avg"]) - float(mean_py))
                               <= 1e-9 * max(1.0, abs(float(mean_py))))
                    agg["matches_python"] = bool(matches)
                    result["query_log"].append({
                        "id": "%s:%s:aggregate" % (t["label"], c["name"]),
                        "sql": agg["sql"], "purpose": "SQL aggregate cross-check",
                        "row_count": agg.get("n", 0), "status": "ok",
                        "summary": {"avg": agg["avg"], "min": agg["min"],
                                    "max": agg["max"],
                                    "matches_python": bool(matches)},
                    })
                    if not matches:
                        result["uncertainty"].append({
                            "item": "%s.%s" % (t["table"], c["name"]),
                            "level": "source_conflict",
                            "reason": "SQL aggregate differs from Python re-check",
                        })
            result["query_log"].append({
                "id": "%s:%s:missing" % (t["label"], c["name"]),
                "sql": prof["missing_sql"], "purpose": "missing/blank value count",
                "row_count": prof.get("n_missing", 0), "status": "ok",
            })
            evidence.append({
                "claim": "column %s.%s profile" % (t["table"], c["name"]),
                "locator": prof["extraction_sql"],
                "quote": "n=%d; n_nonnull=%d; n_missing=%d; n_distinct=%d" % (prof.get("n_total", 0), prof.get("n_nonnull", 0), prof.get("n_missing", 0), prof.get("n_distinct", 0)),
                "role": "profile",
            })
            summary_rows.append([
                t["table"], c["name"], prof.get("kind"),
                prof.get("n_total"), prof.get("n_nonnull"),
                prof.get("n_missing"),
                prof.get("numeric", {}).get("mean"),
                prof.get("numeric", {}).get("stdev_sample"),
                prof.get("numeric", {}).get("min"),
                prof.get("numeric", {}).get("max"),
                prof.get("numeric", {}).get("median"),
                prof.get("numeric", {}).get("q1"),
                prof.get("numeric", {}).get("q3"),
                prof.get("numeric", {}).get("iqr"),
                prof.get("numeric", {}).get("n_outliers_iqr"),
                bool(prof.get("coded_as_text")),
            ])
            top = prof.get("top_values") or []
            dq_rows.append([
                t["table"], c["name"], prof.get("n_total"),
                prof.get("n_missing"), prof.get("missing_rate"),
                prof.get("n_distinct"),
                top[0]["value"] if top else None,
                top[0]["count"] if top else None,
                1 if prof.get("kind") == "numeric" else 0,
                bool(prof.get("coded_as_text")),
            ])
            vc.setdefault(t["table"], {})[c["name"]] = top
            miss_items.append({
                "label": "%s.%s" % (t["table"], c["name"]),
                "missing_rate": prof.get("missing_rate") or 0.0,
                "n_total": prof.get("n_total"), "n_missing": prof.get("n_missing"),
            })

        dq_rows.append([t["table"], "*ALL*", t["row_count"], None, None, None,
                        None, None, None,
                        "%d dup groups" % dup.get("n_duplicate_groups", 0)])

        pairs = dbutil.correlate(table_numeric)
        corr_pairs_all.extend([
            {"table": t["table"], "a": p["a"], "b": p["b"], "r": p["r"], "n": p["n"]}
            for p in pairs
        ])

        # charts for this table
        for mc in table_numeric[:8]:
            fname = "hist_%s_%s.svg" % (t["table"], mc["name"])
            svg = charts.histogram("%s.%s distribution" % (t["table"], mc["name"]), mc["values"])
            _write_artifact(figures_dir, fname, svg, result, chart_files, "histogram")
        if len(miss_items) > 1:
            svg = charts.missingness(miss_items)
            _write_artifact(figures_dir, "missing_%s.svg" % t["table"], svg,
                            result, chart_files, "missingness")
        labels = [m["name"] for m in table_numeric]
        if len(labels) >= 2:
            mat = charts.build_corr_matrix(pairs, labels)
            svg = charts.corr_heatmap(mat, labels)
            _write_artifact(figures_dir, "corr_%s.svg" % t["table"], svg,
                            result, chart_files, "correlation")

    # ---- directed queries (incremental, read-only) --------------------------- #
    qpath = work_dir / QUERIES_FILE
    if qpath.is_file():
        qdata, qerr = dbutil.load_json(qpath)
        if qerr:
            result["errors"].append({"item": QUERIES_FILE, "detail": qerr})
        else:
            qlist = qdata if isinstance(qdata, list) else []
            for q in qlist:
                if not isinstance(q, dict):
                    continue
                qid = str(q.get("id", "q%d" % len(result["query_log"])))
                sql = str(q.get("sql", "")).strip()
                note = str(q.get("note", ""))
                conn = _pick_conn(tables, sql)
                if conn is None:
                    result["errors"].append({"item": qid,
                                             "detail": "no data connection"})
                    continue
                cols, rows, err = _execute_query(conn, sql)
                if err:
                    result["errors"].append({"item": qid, "detail": err})
                    result["query_log"].append({
                        "id": qid, "sql": sql, "purpose": note,
                        "row_count": 0, "status": "error", "error": err})
                    continue
                truncated = len(rows) > MAX_QUERY_ROWS
                keep = rows[:MAX_QUERY_ROWS]
                rows_list = [
                    {cols[i]: r[i] for i in range(len(cols))} for r in keep
                ]
                summary = rows_list[:5]
                result["query_log"].append({
                    "id": qid, "sql": sql, "purpose": note,
                    "row_count": len(rows), "status": "ok",
                    "summary": summary, "truncated": truncated,
                })
                result["answers"][qid] = {
                    "sql": sql, "columns": cols, "rows": rows_list,
                    "row_count": len(rows), "truncated": truncated,
                }
                ck = q.get("contract_key")
                if ck:
                    _apply_contract(result, ck, q.get("as", "rows"), rows_list,
                                    cols)
                evidence.append({
                    "claim": "query %s returned %d rows" % (qid, len(rows)),
                    "locator": sql, "quote": "row_count=%d" % len(rows),
                    "role": "query",
                })
        _log(change_log, "queries", "%d tagged queries executed" % len(result["answers"]))
    else:
        _log(change_log, "queries", "no %s; auto-analysis only" % QUERIES_FILE)

    # ---- explicit contract answers (agent-authored) --------------------------- #
    ca_path = work_dir / CONTRACT_ANSWERS_FILE
    if ca_path.is_file():
        ca, ca_err = dbutil.load_json(ca_path)
        if ca_err:
            result["errors"].append({"item": CONTRACT_ANSWERS_FILE, "detail": ca_err})
        elif isinstance(ca, dict):
            n = 0
            for k, v in ca.items():
                if k in result and k not in ("case_id", "status"):
                    result[k] = v
                    n += 1
            _log(change_log, "contract", "%d contract fields merged" % n)

    # ---- write artifacts ------------------------------------------------------ #
    _write_csv(out_dir / "summary_stats.csv",
               ["table", "column", "kind", "n_total", "n_nonnull", "n_missing",
                "mean", "stdev_sample", "min", "max", "median", "q1", "q3",
                "iqr", "n_outliers_iqr", "coded_as_text"], summary_rows)
    _write_csv(out_dir / "data_quality.csv",
               ["table", "column", "n_total", "n_missing", "missing_rate",
                "n_distinct", "top_value", "top_count", "is_numeric",
                "coded_or_dup_note"], dq_rows)
    _write_corr_csv(out_dir / "correlation_matrix.csv", corr_pairs_all)
    _write_csv(out_dir / "query_log.csv",
               ["id", "sql", "purpose", "row_count", "status", "error"],
               [[q.get("id"), q.get("sql"), q.get("purpose"),
                 q.get("row_count"), q.get("status"), q.get("error")]
                for q in result["query_log"]])
    _write_json(out_dir / "schema.json", schema)
    _write_json(out_dir / "value_counts.json", vc)

    evidence_map = evidence
    result["analysis"].update({
        "contract": contract,
        "n_tables": len(tables),
        "n_columns": sum(len(t["columns"]) for t in schema),
        "n_numeric_columns": len(numeric_meta),
        "n_coded_as_text": sum(1 for r in summary_rows if r[15]),
        "n_duplicate_groups_total": sum(
            (q["summary"].get("n_duplicate_groups") or 0)
            for q in result["query_log"] if q["id"].endswith(":duplicates")),
        "charts": chart_files,
        "correlation": corr_pairs_all,
    })
    _write_json(out_dir / "evidence_map.json", evidence_map)
    _write_report(out_dir / "report.md", result, schema, corr_pairs_all)

    # ---- finalise status ------------------------------------------------------- #
    unfilled = [k for k in contract_keys if result.get(k) is None]
    if result["errors"]:
        result["status"] = "partial"
    elif unfilled:
        result["status"] = "partial"
        result["pending_human"].append({
            "item": "contract_fields",
            "detail": "unfilled output_contract keys: %s" % ", ".join(unfilled),
        })
    else:
        result["status"] = "ok"
    for k in contract_keys:
        if result.get(k) is None:
            result[k] = None
    if unfilled:
        result["limitations"].append({
            "item": "contract_coverage",
            "detail": "not all output_contract fields were resolved",
        })
    result["runtime"]["status"] = "done"
    result["runtime"]["duration_sec"] = round(time.time() - t0, 2)
    result["runtime"]["version"] = VERSION
    _log(change_log, "finalize", "status=%s" % result["status"], result["status"])
    _write_json(out_dir / "result.json", result)
    return 0


def _add_text_source(result, path, role):
    if path is None:
        return
    text = dbutil.read_text(path) or ""
    snippet = text[:200].replace("\n", " ").strip()
    result["sources"].append({
        "locator": "file://%s" % Path(path).name,
        "quote": snippet, "role": role,
    })
    result["analysis"].setdefault("text_sources", []).append({
        "file": Path(path).name, "role": role, "chars": len(text)})


def _write_artifact(figures_dir, fname, content, result, chart_files, kind):
    if content is None:
        return
    path = figures_dir / fname
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    rel = "figures/%s" % fname
    chart_files.append({"file": rel, "kind": kind})
    result["artifacts"][rel] = "chart"


def _write_corr_csv(path, pairs):
    rows = [[p["table"], p["a"], p["b"],
             (round(p["r"], 6) if p["r"] is not None else None), p["n"]
             ] for p in pairs]
    _write_csv(path, ["table", "a", "b", "pearson_r", "n_used"], rows)


def _apply_contract(result, key, as_type, rows_list, cols):
    if as_type == "scalar":
        if rows_list and cols:
            result[key] = rows_list[0].get(cols[0])
        else:
            result[key] = None
    elif as_type == "value":
        result[key] = rows_list[0].get(cols[0]) if rows_list and cols else None
    else:
        result[key] = rows_list


def _finalize_missing(out_dir, result):
    _write_json(out_dir / "schema.json", [])
    _write_json(out_dir / "evidence_map.json", [])
    _write_json(out_dir / "value_counts.json", {})
    _write_csv(out_dir / "summary_stats.csv",
               ["table", "column", "kind", "n_total", "n_nonnull", "n_missing",
                "mean", "stdev_sample", "min", "max", "median", "q1", "q3",
                "iqr", "n_outliers_iqr", "coded_as_text"], [])
    _write_csv(out_dir / "data_quality.csv",
               ["table", "column", "n_total", "n_missing", "missing_rate",
                "n_distinct", "top_value", "top_count", "is_numeric",
                "coded_or_dup_note"], [])
    _write_corr_csv(out_dir / "correlation_matrix.csv", [])
    _write_csv(out_dir / "query_log.csv",
               ["id", "sql", "purpose", "row_count", "status", "error"], [])
    _write_report(out_dir / "report.md", result, [], [])
    result["analysis"] = {"contract": {}, "n_tables": 0, "n_columns": 0,
                          "charts": []}


def _write_report(path, result, schema, pairs):
    lines = ["# 科研数据快照查询与分析报告", "",
             "- case_id: %s" % (result.get("case_id") or "unknown"),
             "- status: %s" % result.get("status"),
             "- 数据表数: %d" % len(schema),
             "- 数值列数: %d" % result["analysis"].get("n_numeric_columns", 0),
             "- 编码为文本但可作数值的列数: %d" % result["analysis"].get("n_coded_as_text", 0),
             ""]
    if schema:
        lines.append("## 表概览")
        lines.append("")
        for t in schema:
            lines.append("- %s（%s，%d 行，%d 列）" % (t["table"], t["label"], t["row_count"], len(t["columns"])))
        lines.append("")
    if pairs:
        lines.append("## 相关分析（Pearson，按成对完整观测）")
        lines.append("")
        for p in pairs[:60]:
            r = round(p["r"], 4) if p["r"] is not None else None
            lines.append("- %s.%s × %s.%s：r=%s（n=%d）" % (p["table"], p["a"], p["table"], p["b"], r, p["n"]))
        lines.append("")
    if result.get("errors"):
        lines.append("## 错误")
        lines.append("")
        for e in result["errors"]:
            lines.append("- %s: %s" % (e.get("item"), e.get("detail")))
        lines.append("")
    if result.get("limitations"):
        lines.append("## 限制")
        lines.append("")
        for l in result["limitations"]:
            lines.append("- %s: %s" % (l.get("item"), l.get("detail")))
        lines.append("")
    lines.append("## 证据")
    lines.append("")
    lines.append("逐条可复核证据见 `evidence_map.json`（locator + 逐字 quote）；"
                 "可复跑 SQL 见 `query_log.csv`。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())