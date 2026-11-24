#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main -- deterministic read-only scientific database query & analysis.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work

Behaviour
  1. discover input (case.json, task.md, *.db/*.sqlite, *.csv/*.tsv, dict);
  2. write a legal result.json skeleton immediately (case_id + every
     output_contract key + status/sources/uncertainty/change_log);
  3. open each database read-only, inspect schema, profile columns, detect
     missing/duplicates, compute descriptive statistics and Pearson
     correlations, and render editable SVG charts;
  4. execute tagged read-only SQL from <work>/queries.json and map results to
     contract keys when a query declares `contract_key`;
  5. merge <work>/contract_answers.json over the contract fields;
  6. emit report.md, evidence_map.json, schema.json and CSV tables, then
     finalise result.json with an honest status.

Determinism: analysis values depend only on the input files; sorting is fixed,
JSON is serialised with sorted keys, and the only varying fields are the
timestamped entries in `runtime`/`change_log` (documented in SKILL.md).
"""

import argparse
import csv as _csv
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbutil
import charts
from core_impl import (
    VERSION, add_change, add_error, add_degradation, add_uncertainty,
    add_source, analyze_table, run_queries, dedupe_sources, build_report,
)

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_TEXT_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# input / output helpers
# ---------------------------------------------------------------------------
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """Read a text file with encoding fallbacks; never raises."""
    size = os.path.getsize(path)
    if size > MAX_TEXT_BYTES:
        return None, "file larger than 2MB, skipped"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except (UnicodeDecodeError, ValueError):
            continue
        except OSError as e:
            return None, str(e)
    return None, "undecodable text"


def read_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "bad JSON: %s" % e


def dump_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True,
                  default=_json_default)
    os.replace(tmp, path)


def _json_default(o):
    if isinstance(o, float):
        return round(o, 6)
    return str(o)


def write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------
def discover_inputs(input_dir):
    """Scan /input without assuming fixed file names."""
    found = {"case": [], "task": [], "db": [], "csv": [], "other": [], "dict": []}
    if not os.path.isdir(input_dir):
        return found
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        low = name.lower()
        if low == "case.json":
            found["case"].append(path)
        elif low in ("task.md", "readme.md") or low.endswith(".md"):
            if low == "task.md":
                found["task"].append(path)
            else:
                found["dict"].append(path)
        elif low.endswith((".db", ".sqlite", ".sqlite3", ".db3")):
            found["db"].append(path)
        elif low.endswith((".csv", ".tsv")):
            found["csv"].append(path)
        elif low.endswith((".json", ".yaml", ".yml", ".txt")):
            if low.endswith(".json") and low != "case.json":
                found["other"].append(path)
            else:
                found["dict"].append(path)
    return found


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for k in ("case_id", "caseId", "id", "challenge_id"):
            if k in case_obj and case_obj[k] not in (None, ""):
                return str(case_obj[k])
    return None


def extract_contract(case_obj):
    """Return the output_contract object (dict/list) or None."""
    if isinstance(case_obj, dict):
        for k in ("output_contract", "outputContract", "contract"):
            if k in case_obj:
                return case_obj[k]
    return None


def contract_keys(contract):
    """Return an ordered dict of contract key -> default value (None/[])."""
    keys = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            keys[str(k)] = _default_for_contract_value(v)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, dict):
                k = (item.get("key") or item.get("name") or item.get("field")
                     or item.get("id"))
                if k:
                    keys[str(k)] = _default_for_contract_value(item)
            elif isinstance(item, str):
                keys[item] = None
    return keys


def _default_for_contract_value(v):
    if isinstance(v, dict):
        t = str(v.get("type", "")).lower()
        if "array" in t or "list" in t or "items" in v:
            return []
    if isinstance(v, list):
        return []
    return None


# ---------------------------------------------------------------------------
# result scaffold
# ---------------------------------------------------------------------------
def build_result(case_id, keys):
    result = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "analysis": {
            "inputs": [],
            "schema": [],
            "data_quality": [],
            "statistics": [],
            "correlations": [],
            "profiles": {},
            "charts": [],
        },
        "query_log": [],
        "answers": {},
        "artifacts": {},
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {},
        "_contract_fills": {},
    }
    for k, default in keys.items():
        result[k] = default
    return result


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--queries", default=None,
                   help="path to queries.json (default: <work>/queries.json)")
    p.add_argument("--answers", default=None,
                   help="path to contract_answers.json (default: <work>/contract_answers.json)")
    p.add_argument("--version", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.version:
        print(VERSION)
        return 0

    input_dir = args.input
    output_dir = args.output
    work_dir = args.work
    started = time.time()

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "figures"), exist_ok=True)

    found = discover_inputs(input_dir)

    # -- case.json (fall back to any JSON) --------------------------------
    case_obj = None
    case_path = None
    case_err = None
    candidates = found["case"] + found["other"] + found["dict"]
    for p in candidates:
        if not p.lower().endswith(".json"):
            continue
        obj, err = read_json(p)
        if err is None:
            case_obj, case_path = obj, p
            break
        if case_err is None and p in found["case"]:
            case_err = err
    if case_obj is None and found["case"]:
        case_path = found["case"][0]
        case_err = case_err or "case.json not parseable"

    case_id = extract_case_id(case_obj) if case_obj is not None else None
    contract = extract_contract(case_obj) if case_obj is not None else None
    keys = contract_keys(contract)

    # -- previous run (idempotent incremental; read BEFORE overwriting) -----
    prev = None
    prev_path = os.path.join(output_dir, "result.json")
    if os.path.exists(prev_path):
        prev, _ = read_json(prev_path)

    # -- build skeleton and write it immediately --------------------------
    result = build_result(case_id, keys)
    if prev and isinstance(prev, dict):
        for k in keys:
            if prev.get(k) is not None:
                result[k] = prev[k]
    result["runtime"] = {
        "skill_version": VERSION, "started_at": _now(), "input_dir": input_dir,
        "output_dir": output_dir, "work_dir": work_dir,
    }
    add_change(result, "skeleton", "legal partial skeleton written", "partial")
    if case_path is None:
        add_degradation(result, "no case.json found under %s" % input_dir)
    elif case_err:
        add_error(result, "case.json could not be parsed: %s" % case_err, "input")
    if case_id is None:
        add_degradation(result, "case_id missing; status kept at partial")
    dump_json(os.path.join(output_dir, "result.json"), result)

    # -- task.md / dictionary text -----------------------------------------
    task_text = ""
    if found["task"]:
        txt, err = read_text(found["task"][0])
        if err:
            add_degradation(result, "task.md read failed: %s" % err)
        else:
            task_text = txt or ""
    dict_texts = {}
    for p in found["dict"]:
        txt, err = read_text(p)
        if err is None:
            dict_texts[p] = txt or ""

    # record inputs
    for kind in ("case", "task", "db", "csv", "dict"):
        for p in found[kind]:
            try:
                size = os.path.getsize(p)
            except OSError:
                size = None
            result["analysis"]["inputs"].append(
                {"path": p, "kind": kind, "size_bytes": size})

    if case_obj is not None and case_err is None:
        add_source(result,
                   case_path, "case.json parsed; case_id=%s" % case_id, "input")
        if task_text:
            add_source(result, found["task"][0], task_text[:500], "task")

    # -- open data sources ---------------------------------------------------
    connections = []
    source_meta = []
    table_schema = []
    stats_rows = []
    dq_rows = []
    corr_rows = []
    value_counts = {}
    chart_list = []
    artifacts = {}
    evidence = []

    for p in found["db"]:
        try:
            conn = dbutil.connect_db(p)
            key = "sqlite:" + os.path.abspath(p)
            schema = dbutil.inspect_schema(conn)
            connections.append((key, conn))
            source_meta.append({"source": key, "kind": "sqlite",
                                "tables": [t["table"] for t in schema]})
            table_schema.extend([{"source": key, "table": t["table"],
                                  "row_count": t["row_count"],
                                  "columns": t["columns"]} for t in schema])
            add_source(result, key,
                       "connected read-only; tables=%s"
                       % json.dumps([t["table"] for t in schema],
                                    ensure_ascii=False), "data-source")
        except Exception as e:  # noqa: BLE001
            add_error(result, "cannot open %s: %s" % (p, e), "data-source")

    mem_conn = None
    for p in found["csv"]:
        try:
            if mem_conn is None:
                mem_conn = dbutil.connect_memory()
            info = dbutil.load_csv_table(p, mem_conn)
            if info.get("table"):
                key = "csv:" + os.path.abspath(p)
                connections.append((key, mem_conn))
                source_meta.append({"source": key, "kind": "csv",
                                    "delimiter": info["delimiter"],
                                    "table": info["table"],
                                    "n_rows": info["n_rows"]})
                schema = dbutil.inspect_schema(mem_conn)
                table_schema.extend(
                    [{"source": key, "table": t["table"],
                      "row_count": t["row_count"], "columns": t["columns"]}
                     for t in schema if t["table"] == info["table"]])
                add_source(result, key,
                           "CSV loaded; delimiter=%r; table=%s; rows=%d"
                           % (info["delimiter"], info["table"], info["n_rows"]),
                           "data-source")
            else:
                add_error(result, "cannot load %s: %s"
                          % (p, info.get("error")), "data-source")
        except Exception as e:  # noqa: BLE001
            add_error(result, "cannot load %s: %s" % (p, e), "data-source")

    result["analysis"]["schema"] = table_schema
    result["analysis"]["data_sources"] = source_meta

    # -- per-table analysis --------------------------------------------------
    for meta in table_schema:
        src = meta["source"]
        conn = next((c for k, c in connections if k == src), None)
        if conn is None:
            continue
        try:
            analyze_table(conn, src, meta["table"], meta, output_dir, result,
                          evidence, artifacts, chart_list, stats_rows, dq_rows,
                          corr_rows, value_counts)
        except Exception as e:  # noqa: BLE001
            add_error(result, "analysis failed for %s#%s: %s"
                      % (src, meta["table"], e), "table")

    # -- charts: summary statistics & missing --------------------------------
    means = [(("%s.%s" % (r[0], r[1])), r[5]) for r in stats_rows
             if r[5] is not None]
    if means:
        charts.write_chart(output_dir, "figures/summary_means.svg",
                           charts.svg_bar(means, "Mean of numeric columns (summary)"),
                           artifacts, chart_list, "bar")
    missing = [("%s.%s" % (r[0], r[1]), r[6]) for r in dq_rows if r[6]]
    if missing:
        charts.write_chart(output_dir, "figures/missing_values.svg",
                           charts.svg_missing(missing, "Missing values by column"),
                           artifacts, chart_list, "bar")

    result["analysis"]["charts"] = chart_list

    # -- queries ----------------------------------------------------------------
    queries_path = args.queries or os.path.join(work_dir, "queries.json")
    queries = []
    if os.path.exists(queries_path):
        obj, err = read_json(queries_path)
        if err:
            add_error(result, "queries.json: %s" % err, "query")
        elif isinstance(obj, list):
            queries = obj
        else:
            add_error(result, "queries.json must be a JSON array", "query")

    qlog_csv = []
    run_queries(queries, connections, result, result["query_log"], qlog_csv)

    # -- contract_answers merge (explicit, highest priority) -------------------
    answers_path = args.answers or os.path.join(work_dir, "contract_answers.json")
    if os.path.exists(answers_path):
        obj, err = read_json(answers_path)
        if err:
            add_error(result, "contract_answers.json: %s" % err, "answer")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in keys:
                    result[k] = v
                    add_change(result, "contract", "contract field %s set" % k,
                               "partial")
        else:
            add_error(result, "contract_answers.json must be a JSON object",
                      "answer")

    # -- apply contract fills from queries -------------------------------------
    for k, v in result.get("_contract_fills", {}).items():
        if k in keys:
            result[k] = v
            add_change(result, "query", "contract field %s filled from query"
                       % k, "partial")
    result.pop("_contract_fills", None)

    # -- write CSVs -------------------------------------------------------------
    write_csv(os.path.join(output_dir, "summary_stats.csv"),
              ["table", "column", "kind", "coded_as_text", "n", "mean",
               "stdev_sample", "min", "max", "q1", "median", "q3",
               "sql_avg", "sql_python_match"],
              stats_rows)
    write_csv(os.path.join(output_dir, "data_quality.csv"),
              ["table", "column", "kind", "coded_as_text", "n_total",
               "n_nonnull", "n_missing", "n_distinct", "declared_type"],
              dq_rows)
    write_csv(os.path.join(output_dir, "correlation_matrix.csv"),
              ["table", "column_a", "column_b", "pearson_r", "n_pairs"],
              corr_rows)
    write_csv(os.path.join(output_dir, "query_log.csv"),
              ["id", "source", "sql", "purpose", "row_count", "status",
               "error", "summary"],
              qlog_csv)

    dump_json(os.path.join(output_dir, "schema.json"), table_schema)
    dump_json(os.path.join(output_dir, "value_counts.json"), value_counts)
    dump_json(os.path.join(output_dir, "evidence_map.json"), evidence)

    # -- report.md --------------------------------------------------------------
    report = build_report(result, table_schema, source_meta, task_text, dict_texts)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(report)

    # -- status & finish -----------------------------------------------------------
    result["sources"] = dedupe_sources(result["sources"])
    result["analysis"]["evidence_count"] = len(evidence)
    has_data = bool(table_schema) or bool(found["db"]) or bool(found["csv"])
    if case_err:
        result["status"] = "input_error"
        result["pending_human"].append("case.json unparseable: %s" % case_err)
    elif not has_data:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append(
            "no database or CSV snapshot found under /input; cannot compute "
            "statistics or answer the contract")
    else:
        contract_missing = [k for k in keys if result.get(k) is None]
        if contract_missing or result["errors"]:
            result["status"] = "partial"
            if contract_missing:
                result["pending_human"].append(
                    "contract fields not yet filled: %s"
                    % ", ".join(contract_missing))
        else:
            result["status"] = "ok"
    if result["status"] not in STATUSES:
        result["status"] = "partial"

    result["runtime"]["finished_at"] = _now()
    result["runtime"]["elapsed_seconds"] = round(time.time() - started, 3)
    result["runtime"]["version"] = VERSION
    add_change(result, "finalize", "pipeline finalised; status=%s"
               % result["status"], result["status"])
    dump_json(os.path.join(output_dir, "result.json"), result)

    # close connections cleanly
    for _, conn in connections:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if mem_conn is not None and mem_conn not in [c for _, c in connections]:
        try:
            mem_conn.close()
        except Exception:  # noqa: BLE001
            pass

    print("status=%s case_id=%s tables=%d errors=%d"
          % (result["status"], result["case_id"], len(table_schema),
             len(result["errors"])))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        # Last-resort: never crash without a deliverable.
        try:
            out = "/app/output"
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, "result.json")
            if not os.path.exists(path):
                dump_json(path, {
                    "case_id": None, "status": "input_error",
                    "sources": [], "uncertainty": [], "change_log": [
                        {"ts": _now(), "step": "crash", "change": str(exc),
                         "status": "input_error"}],
                    "errors": [{"where": "main", "message": str(exc),
                                "traceback": traceback.format_exc()[:2000]}],
                    "analysis": {}, "query_log": [], "answers": {},
                    "artifacts": {}, "degradations": [], "limitations": [],
                    "pending_human": [], "runtime": {},
                })
        except Exception:  # noqa: BLE001
            pass
        print("fatal: %s" % exc, file=sys.stderr)
        sys.exit(0)
