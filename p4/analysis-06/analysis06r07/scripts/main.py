#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main: deterministic, strictly read-only scientific DB/CSV analysis pipeline.

Robustness-first: a legal result.json skeleton is written before any analysis,
every input file is isolated so one bad file cannot crash the run, and the
process always exits 0 with an honest status. No prepared answers are produced.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
      --queries /app/work/queries.json
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as ci

VERSION = "1.0.0"

# Top-level keys owned by this skill; output_contract keys that collide are
# preserved verbatim under result["output"] so nothing is lost.
RESERVED = {
    "case_id", "status", "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "limitations", "runtime", "analysis", "query_log",
    "answers", "artifacts", "output",
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def add_change(log, step, change, status):
    log.append({"ts": now_iso(), "step": step, "change": change, "status": status})


class Result:
    """Owns result.json state and guarantees a valid document at all times."""

    def __init__(self, output_dir, work_dir):
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.work_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.output_dir = Path(".")
            self.work_dir = Path(".")
        self.case_id = None
        self.contract = {}
        self.answers = {}
        self.status = "partial"
        self.sources = []
        self.uncertainty = []
        self.pending_human = []
        self.change_log = []
        self.query_log = []
        self.analysis = {"databases": [], "tabs": [], "tables": [], "datasets": [],
                         "data_quality": [], "correlation": [], "figures": [],
                         "dictionaries": []}
        self.artifacts = {}
        self.errors = []
        self.degradations = []
        self.limitations = []
        self.runtime = {"started_at": now_iso(), "script": "main.py",
                        "version": VERSION, "python": sys.version.split()[0]}
        self._conns = {}
        self._t0 = time.time()

    def build(self):
        doc = {}
        doc["case_id"] = self.case_id
        doc["status"] = self.status
        for k in self.contract:
            val = self.answers.get(k, None)
            if k in RESERVED:
                doc.setdefault("output", {})[k] = val
            else:
                doc[k] = val
        doc["sources"] = self.sources
        doc["uncertainty"] = self.uncertainty
        doc["pending_human"] = self.pending_human
        doc["change_log"] = self.change_log
        doc["analysis"] = self.analysis
        doc["query_log"] = self.query_log
        doc["answers"] = self.answers
        doc["artifacts"] = self.artifacts
        doc["errors"] = self.errors
        doc["degradations"] = self.degradations
        doc["limitations"] = self.limitations
        doc["runtime"] = self.runtime
        return doc

    def write(self):
        self.runtime["elapsed_seconds"] = round(time.time() - self._t0, 3)
        try:
            ci.atomic_write_json(self.output_dir / "result.json", self.build())
            return True
        except Exception as e:
            try:
                ci.atomic_write_json(self.output_dir / "result.json", {
                    "case_id": self.case_id, "status": "input_error",
                    "sources": [], "uncertainty": [],
                    "change_log": [{"ts": now_iso(), "step": "fallback",
                                    "change": "minimal result after write "
                                              "failure: %s" % e,
                                    "status": "input_error"}],
                    "errors": [{"item": "result_write", "detail": str(e)}],
                    "degradations": [], "limitations": [], "pending_human": [],
                    "analysis": {}, "query_log": [], "answers": {},
                    "artifacts": {}, "runtime": self.runtime})
                return True
            except Exception:
                print("FATAL: could not write result.json", file=sys.stderr)
                return False

    def add_conn(self, db_path, conn):
        self._conns[db_path] = conn

    def close_conns(self):
        for c in self._conns.values():
            try:
                c.close()
            except Exception:
                pass
        self._conns = {}


# --------------------------------------------------------------------------- #
# analysis steps
# --------------------------------------------------------------------------- #
def analyze_db(res, db_path):
    label = Path(db_path).name
    conn, err = ci.safe_open_sqlite(db_path)
    if err:
        res.errors.append({"item": "db:%s" % db_path, "detail": err})
        res.degradations.append({"item": "db:%s" % db_path,
                                 "detail": "skipped: connection failed"})
        return
    res.analysis["databases"].append(db_path)
    res.add_conn(db_path, conn)
    res.sources.append({"locator": "sqlite://%s" % db_path,
                        "quote": "read-only connection opened; PRAGMA query_only=ON",
                        "role": "data-source"})

    tables, terr = ci.inspect_schema(conn)
    if terr:
        res.errors.append({"item": "db:%s" % db_path, "detail": terr})
    schema_entry = {"database": db_path, "tables": []}
    for t in tables:
        tname = t["table"]
        if not t.get("columns") or (len(t["columns"]) == 1
                                    and t["columns"][0].get("error")):
            res.degradations.append({"item": "table:%s" % tname,
                                     "detail": "unreadable schema"})
            continue
        cols = t["columns"]
        res.query_log.append({
            "id": "%s:%s:schema" % (label, tname),
            "sql": "PRAGMA table_info('%s'); SELECT COUNT(*) FROM \"%s\""
                   % (tname.replace("'", "''"), tname.replace('"', '""')),
            "purpose": "schema discovery", "row_count": t["row_count"],
            "status": "ok",
            "summary": {"columns": len(cols), "rows": t["row_count"]}})
        res.sources.append({
            "claim": "table %s has %d columns and %s rows"
                     % (tname, len(cols), t["row_count"]),
            "locator": "sqlite://%s#table=%s" % (db_path, tname),
            "quote": "columns=%s; row_count=%s"
                     % ([c["name"] for c in cols], t["row_count"]),
            "role": "schema"})

        dup = ci.detect_duplicates(conn, tname, cols)
        if dup.get("error"):
            res.degradations.append({"item": "dup:%s" % tname,
                                     "detail": dup["error"]})
        else:
            res.query_log.append({
                "id": "%s:%s:duplicates" % (label, tname),
                "sql": dup.get("full_row_dup_sql", ""),
                "purpose": "exact full-row duplicate detection",
                "row_count": dup["n_duplicate_groups"], "status": "ok",
                "summary": dup})
            res.analysis["data_quality"].append({
                "database": db_path, "table": tname,
                "duplicate_groups": dup["n_duplicate_groups"],
                "duplicate_rows": dup["n_rows_in_duplicate_groups"]})
            if dup["n_duplicate_groups"]:
                res.uncertainty.append({
                    "item": "duplicates:%s" % tname,
                    "level": "medium",
                    "reason": "%d exact duplicate groups affect %d rows"
                              % (dup["n_duplicate_groups"],
                                 dup["n_rows_in_duplicate_groups"])})

        numeric_cols = []
        table_entry = {"database": db_path, "table": tname,
                       "row_count": t["row_count"], "columns": []}
        profiles = []
        for c in cols:
            prof = ci.column_profile(conn, tname, c["name"], c["type"])
            if prof.get("error"):
                res.degradations.append({
                    "item": "column:%s.%s" % (tname, c["name"]),
                    "detail": prof["error"]})
            profiles.append(prof)
            entry = {"name": c["name"], "declared_type": c["type"],
                     "kind": prof.get("kind"),
                     "n_nonnull": prof.get("n_nonnull"),
                     "n_missing": prof.get("n_missing"),
                     "distinct_count": prof.get("distinct_count"),
                     "coded_as_text": bool(prof.get("coded_as_text")),
                     "top_values": prof.get("top_values", [])}
            if prof.get("kind") == "numeric":
                num = dict(prof.get("numeric") or {})
                num.pop("values", None)  # keep result.json lean
                entry["numeric"] = num
                numeric_cols.append({"name": c["name"],
                                     "values": prof["numeric"].get("values", [])})
                st = prof["numeric"]
                res.sources.append({
                    "claim": "column %s.%s numeric summary" % (tname, c["name"]),
                    "locator": prof.get("extraction_sql", ""),
                    "quote": "n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s; "
                             "median=%s" % (st["n"], st["mean"], st["stdev_sample"],
                                            st["min"], st["max"], st["median"]),
                    "role": "statistic"})
                agg = prof.get("aggregate")
                if agg and "error" in agg:
                    res.degradations.append({
                        "item": "agg:%s.%s" % (tname, c["name"]),
                        "detail": agg["error"]})
                elif agg:
                    res.query_log.append({
                        "id": "%s:%s:%s:aggregate" % (label, tname, c["name"]),
                        "sql": agg["sql"],
                        "purpose": "SQL aggregate cross-check against Python stats",
                        "row_count": agg.get("n"), "status": "ok",
                        "summary": {"avg": agg.get("avg"), "min": agg.get("min"),
                                    "max": agg.get("max"),
                                    "matches_python": agg.get("matches_python")}})
                    if not agg.get("matches_python"):
                        res.uncertainty.append({
                            "item": "crosscheck:%s.%s" % (tname, c["name"]),
                            "level": "high",
                            "reason": "SQL AVG differs from Python recomputation"})
            else:
                res.query_log.append({
                    "id": "%s:%s:%s:extract" % (label, tname, c["name"]),
                    "sql": prof.get("extraction_sql", ""),
                    "purpose": "column value extraction",
                    "row_count": prof.get("n_nonnull"),
                    "status": "ok" if not prof.get("error") else "error"})
                if prof.get("coded_as_text"):
                    res.degradations.append({
                        "item": "coded:%s.%s" % (tname, c["name"]),
                        "detail": "numeric-looking text column; converted to "
                                  "float in Python, never aggregated as text"})
            res.analysis["data_quality"].append({
                "database": db_path, "table": tname, "column": c["name"],
                "missing": prof.get("n_missing"), "nonnull": prof.get("n_nonnull"),
                "distinct": prof.get("distinct_count")})
            table_entry["columns"].append(entry)

        schema_entry["tables"].append(table_entry)
        res.analysis["datasets"].append(table_entry)

        if len(numeric_cols) >= 2:
            corr = ci.correlate_table(numeric_cols)
            res.analysis["correlation"].append(
                {"database": db_path, "table": tname, **corr})

        ci.make_charts(res, tname, numeric_cols, profiles)

    res.analysis["tables"].append(schema_entry)


def analyze_tabular_file(res, path):
    label = Path(path).name
    prof = ci.analyze_tabular(path)
    res.analysis["tabs"].append(path)
    if prof.get("error"):
        res.errors.append({"item": "csv:%s" % path, "detail": prof["error"]})
        res.degradations.append({"item": "csv:%s" % path,
                                 "detail": "skipped: parse failed"})
        return
    res.sources.append({
        "locator": "file://%s" % path,
        "quote": "delimiter=%s; header=%s; row_count=%s"
                 % (prof.get("delimiter"), prof.get("header"), prof.get("row_count")),
        "role": "data-source"})
    entry = {"file": path, "kind": "csv", "row_count": prof.get("row_count"),
             "columns": []}
    numeric_cols = []
    for c in prof.get("columns", []):
        e = {"name": c["name"], "kind": c["kind"],
             "n_nonnull": c.get("n_nonnull"), "n_missing": c.get("n_missing"),
             "distinct_count": c.get("distinct_count"),
             "coded_as_text": bool(c.get("coded_as_text")),
             "top_values": c.get("top_values", [])}
        if c.get("kind") == "numeric":
            num = dict(c.get("numeric") or {})
            num.pop("values", None)
            e["numeric"] = num
            numeric_cols.append({"name": c["name"],
                                 "values": c["numeric"].get("values", [])})
        entry["columns"].append(e)
        res.analysis["data_quality"].append({
            "database": None, "table": label, "column": c["name"],
            "missing": c.get("n_missing"), "nonnull": c.get("n_nonnull"),
            "distinct": c.get("distinct_count")})
        if c.get("coded_as_text"):
            res.degradations.append({
                "item": "coded:%s.%s" % (label, c["name"]),
                "detail": "numeric-looking text column converted to float"})
    res.analysis["datasets"].append(entry)
    if prof.get("truncated_rows"):
        res.degradations.append({"item": "csv:%s" % path,
                                 "detail": "row cap reached (%d)" % ci.MAX_CSV_ROWS})
    if len(numeric_cols) >= 2:
        corr = ci.correlate_table(numeric_cols)
        res.analysis["correlation"].append({"database": None, "table": label,
                                            **corr})


def run_queries(res, args):
    candidates = []
    if getattr(args, "queries", None):
        candidates.append(args.queries)
    candidates.append(os.environ.get("QUERIES_JSON"))
    candidates.append(str(res.work_dir / "queries.json"))
    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if not p.is_file():
            continue
        obj, err, degraded = ci.load_json_robust(str(p))
        if err:
            res.errors.append({"item": "queries:%s" % cand, "detail": err})
            continue
        if degraded:
            res.degradations.append({"item": "queries:%s" % cand,
                                     "detail": "salvaged/truncated read"})
        queries = obj if isinstance(obj, list) else obj.get("queries", [])
        if not isinstance(queries, list):
            res.degradations.append({"item": "queries:%s" % cand,
                                     "detail": "expected a JSON list of "
                                               "{id, sql, note}"})
            queries = []
        for q in queries:
            if not isinstance(q, dict):
                continue
            qid = q.get("id") or "q%d" % (len(res.query_log) + 1)
            run_one_query(res, qid, q.get("sql"), q.get("note", ""))
        break


def run_one_query(res, qid, sql, note):
    if not res._conns:
        res.query_log.append({"id": qid, "sql": sql, "purpose": note,
                              "row_count": 0, "status": "error",
                              "error": "no open data connection"})
        res.degradations.append({"item": "query:%s" % qid,
                                 "detail": "no open data connection"})
        return
    ran = False
    multi = len(res._conns) > 1
    for db_path in sorted(res._conns):
        conn = res._conns[db_path]
        cols, rows, n, err, st = ci.run_safe_query(conn, sql)
        entry_id = "%s@%s" % (qid, Path(db_path).name) if multi else qid
        entry = {"id": entry_id, "sql": sql, "purpose": note,
                 "row_count": n, "status": st}
        if err:
            entry["error"] = err
        else:
            ran = True
            entry["columns"] = cols
            entry["summary"] = {"rows_returned": n, "first_rows": rows[:10]}
            if qid in res.contract:
                res.answers[qid] = (rows[:20] if n > 1 else
                                    (rows[0] if rows else []))
        res.query_log.append(entry)
    if not ran:
        res.degradations.append({"item": "query:%s" % qid,
                                 "detail": "query failed or was blocked on all "
                                           "open data sources"})


def compute_status(res):
    if res.status == "input_error":
        return res.status
    has_data = bool(res.analysis.get("databases") or res.analysis.get("tabs"))
    unfilled = [k for k in res.contract if res.answers.get(k) is None]
    if not has_data:
        res.status = "insufficient_evidence"
        if not res.pending_human:
            res.pending_human.append("no analysable data source (DB/CSV) found "
                                     "in /input")
        return res.status
    if unfilled:
        res.status = "partial"
        res.limitations.append("unfilled output_contract fields: %s"
                               % ", ".join(unfilled))
    elif res.errors or res.pending_human or res.degradations:
        res.status = "partial"
    else:
        res.status = "ok"
    return res.status


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def run(args):
    res = Result(args.output, args.work)
    add_change(res.change_log, "phase0", "start", "partial")

    inputs, disc_err = ci.discover_inputs(args.input)
    if disc_err:
        res.degradations.append({"item": "input_scan", "detail": disc_err})

    if inputs["case_json"]:
        obj, err, degraded = ci.load_json_robust(inputs["case_json"])
        if err:
            res.errors.append({"item": "case.json", "detail": err})
            if obj is None:
                res.status = "input_error"
                res.pending_human.append("case.json unparseable; cannot recover "
                                         "case_id or output_contract")
        else:
            if not isinstance(obj, dict):
                res.errors.append({"item": "case.json",
                                   "detail": "not a JSON object"})
                obj = {}
            res.case_id = obj.get("case_id") or obj.get("id")
            contract = obj.get("output_contract") or obj.get("output") or {}
            if isinstance(contract, dict):
                res.contract = contract
            else:
                res.contract = {}
                res.degradations.append({"item": "output_contract",
                                         "detail": "output_contract is not an "
                                                   "object; treated as empty"})
        if degraded:
            res.degradations.append({"item": "case.json",
                                     "detail": "salvaged/truncated read"})
    else:
        res.pending_human.append("case.json missing; case_id and output_contract "
                                 "cannot be determined")
        res.status = "insufficient_evidence"

    for k in res.contract:
        res.answers.setdefault(k, None)
    res.analysis["output_contract"] = res.contract

    res.write()
    add_change(res.change_log, "phase0", "skeleton persisted", res.status)

    if inputs["task_md"]:
        text, enc, truncated, err, degraded = ci.read_text_robust(inputs["task_md"])
        if err or text is None:
            res.errors.append({"item": "task.md", "detail": err or "unreadable"})
        else:
            res.sources.append({"locator": inputs["task_md"],
                                "quote": text[:1000], "role": "task"})
            if truncated:
                res.degradations.append({"item": "task.md",
                                         "detail": "truncated at %d bytes"
                                         % ci.MAX_TEXT_BYTES})
            if degraded:
                res.degradations.append({"item": "task.md",
                                         "detail": "decoded with fallback encoding "
                                                   "(%s)" % enc})

    for d in inputs["dictionaries"]:
        text, enc, truncated, err, degraded = ci.read_text_robust(d)
        if err or text is None:
            res.errors.append({"item": "dictionary:%s" % d,
                               "detail": err or "unreadable"})
            continue
        res.analysis["dictionaries"].append(d)
        res.sources.append({"locator": d, "quote": text[:500],
                            "role": "data-dictionary"})
        if truncated:
            res.degradations.append({"item": "dictionary:%s" % d,
                                     "detail": "truncated at %d bytes"
                                     % ci.MAX_TEXT_BYTES})

    for db in inputs["dbs"]:
        try:
            analyze_db(res, db)
        except Exception as e:  # per-source isolation
            res.errors.append({"item": "db:%s" % db,
                               "detail": "unhandled: %s" % e})
            res.degradations.append({"item": "db:%s" % db,
                                     "detail": "skipped after unexpected error"})

    for tab in inputs["tabs"]:
        try:
            analyze_tabular_file(res, tab)
        except Exception as e:
            res.errors.append({"item": "csv:%s" % tab,
                               "detail": "unhandled: %s" % e})
            res.degradations.append({"item": "csv:%s" % tab,
                                     "detail": "skipped after unexpected error"})

    try:
        run_queries(res, args)
    except Exception as e:
        res.errors.append({"item": "queries", "detail": "unhandled: %s" % e})

    ci.write_artifact_csvs(res)
    ci.write_report(res)
    res.close_conns()
    res.status = compute_status(res)
    res.write()
    add_change(res.change_log, "finalize", "finished with status=%s" % res.status,
               res.status)
    res.write()
    print("main.py done: status=%s case_id=%s" % (res.status, res.case_id))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Deterministic read-only DB/CSV analysis pipeline.")
    parser.add_argument("--input", default="/input",
                        help="input directory (default: /input)")
    parser.add_argument("--output", default="/app/output",
                        help="output directory (default: /app/output)")
    parser.add_argument("--work", default="/app/work",
                        help="work directory (default: /app/work)")
    parser.add_argument("--queries", default=None,
                        help="path to queries.json (default: <work>/queries.json)")
    parser.add_argument("--max-text-bytes", type=int, default=ci.MAX_TEXT_BYTES,
                        help="per-text-file byte cap (default: 2 MiB)")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SystemExit:
        raise
    except Exception as e:
        print("uncaught: %s" % e, file=sys.stderr)
        try:
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            ci.atomic_write_json(out / "result.json", {
                "case_id": None, "status": "input_error",
                "sources": [], "uncertainty": [],
                "change_log": [{"ts": now_iso(), "step": "fatal",
                                "change": "uncaught: %s" % e,
                                "status": "input_error"}],
                "pending_human": ["uncaught error; see errors"],
                "analysis": {}, "query_log": [], "answers": {},
                "artifacts": {}, "errors": [{"item": "fatal", "detail": str(e)}],
                "degradations": [], "limitations": [],
                "runtime": {"script": "main.py", "version": VERSION}})
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
