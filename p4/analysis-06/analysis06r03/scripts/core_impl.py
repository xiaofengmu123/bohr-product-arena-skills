#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl -- analysis, query execution and report helpers for main.py.

Kept separate from main.py so the entry-point file stays short. All functions
are deterministic and use only the Python standard library plus the sibling
modules dbutil and charts.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbutil
import charts

VERSION = "1.1.0"
MAX_QUERY_ROWS = 200


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_default(o):
    if isinstance(o, float):
        return round(o, 6)
    return str(o)


# ---------------------------------------------------------------------------
# evidence / log builders
# ---------------------------------------------------------------------------
def add_change(result, step, change, status="partial"):
    result["change_log"].append({"ts": _now(), "step": step, "change": change,
                                 "status": status})


def add_error(result, message, where="analysis"):
    result["errors"].append({"where": where, "message": message})


def add_degradation(result, message):
    result["degradations"].append({"message": message})


def add_uncertainty(result, item, level, reason):
    result["uncertainty"].append({"item": item, "level": level,
                                  "reason": reason})


def add_source(result, locator, quote, role):
    result["sources"].append({"locator": locator, "quote": quote, "role": role})


def add_evidence(evidence, claim, locator, quote, role):
    evidence.append({"claim": claim, "locator": locator, "quote": quote,
                     "role": role})


# ---------------------------------------------------------------------------
# per-table analysis
# ---------------------------------------------------------------------------
def analyze_table(conn, source_key, table, schema_meta, output_dir, result,
                  evidence, artifacts, chart_list, stats_rows, dq_rows,
                  corr_rows, value_counts):
    row_count = schema_meta["row_count"]
    columns = schema_meta["columns"]

    evidence.append({
        "claim": "table %s has %d columns and %d rows"
                 % (table, len(columns), row_count),
        "locator": "%s#table=%s" % (source_key, table),
        "quote": "columns=%s; row_count=%d"
                 % (json.dumps([c["name"] for c in columns],
                               ensure_ascii=False), row_count),
        "role": "schema",
    })

    dup = dbutil.detect_duplicates(conn, table)
    evidence.append({
        "claim": "table %s has %d exact duplicate groups" % (table,
                 dup["n_duplicate_groups"]),
        "locator": dup["full_row_dup_sql"] or ("%s#table=%s" % (source_key, table)),
        "quote": "n_duplicate_groups=%d; n_rows_in_groups=%d"
                 % (dup["n_duplicate_groups"], dup["n_rows_in_duplicate_groups"]),
        "role": "data-quality",
    })
    result["analysis"]["data_quality"].append({
        "table": table, "source": source_key,
        "n_duplicate_groups": dup["n_duplicate_groups"],
        "n_rows_in_duplicate_groups": dup["n_rows_in_duplicate_groups"],
        "duplicate_sql": dup["full_row_dup_sql"],
    })

    profiled = []
    numeric = []
    for c in columns:
        prof = dbutil.column_profile(conn, table, c["name"], c.get("type"))
        profiled.append(prof)
        dq_rows.append([
            table, c["name"], prof["kind"],
            "1" if prof["coded_as_text"] else "0",
            prof["n_total"], prof["n_nonnull"], prof["n_missing"],
            prof["n_distinct"], prof["declared_type"] or "",
        ])
        value_counts.setdefault(table, {})[c["name"]] = prof["top_values"]
        if prof.get("numeric"):
            numeric.append({"name": c["name"], "values": prof["numeric"]["values"],
                            "stats": prof["numeric"]["stats"]})
            agg = dbutil.aggregate_numeric(conn, table, c["name"])
            matches = None
            if agg.get("error") is None and agg.get("n") is not None:
                st = prof["numeric"]["stats"]
                matches = (agg["n"] == st["n"]
                           and _close(agg["avg"], st["mean"])
                           and _close(agg["min"], st["min"])
                           and _close(agg["max"], st["max"]))
            stats_rows.append([
                table, c["name"], prof["kind"],
                "1" if prof["coded_as_text"] else "0",
                prof["numeric"]["stats"]["n"], prof["numeric"]["stats"]["mean"],
                prof["numeric"]["stats"]["stdev_sample"],
                prof["numeric"]["stats"]["min"], prof["numeric"]["stats"]["max"],
                prof["numeric"]["stats"]["q1"], prof["numeric"]["stats"]["median"],
                prof["numeric"]["stats"]["q3"],
                agg.get("avg"), "1" if matches else "0",
            ])
            evidence.append({
                "claim": "column %s.%s numeric summary" % (table, c["name"]),
                "locator": agg["sql"],
                "quote": "n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s"
                         % (prof["numeric"]["stats"]["n"],
                            prof["numeric"]["stats"]["mean"],
                            prof["numeric"]["stats"]["stdev_sample"],
                            prof["numeric"]["stats"]["min"],
                            prof["numeric"]["stats"]["max"]),
                "role": "statistic",
            })
            if not matches:
                add_uncertainty(result, "%s.%s" % (table, c["name"]),
                                "sql_python_mismatch",
                                "SQL aggregate and Python recomputation disagree; "
                                "both values are retained in summary_stats.csv")
        else:
            if prof["kind"] == "text":
                evidence.append({
                    "claim": "column %s.%s top values" % (table, c["name"]),
                    "locator": prof["value_counts_sql"],
                    "quote": "n_distinct=%d; top=%s"
                             % (prof["n_distinct"],
                                json.dumps(prof["top_values"][:5],
                                           ensure_ascii=False)),
                    "role": "value-counts",
                })

    result["analysis"]["profiles"][table] = profiled

    if len(numeric) >= 2:
        names = [n["name"] for n in numeric]
        matrix = []
        for i, a in enumerate(numeric):
            row = []
            for j, b in enumerate(numeric):
                r = dbutil.pearson(a["values"], b["values"]) if i <= j else \
                    matrix[j][i]
                row.append(round(r, 6) if r is not None else None)
            matrix.append(row)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                corr_rows.append([table, names[i], names[j],
                                  matrix[i][j],
                                  min(len(numeric[i]["values"]),
                                      len(numeric[j]["values"]))])
        result["analysis"]["correlations"].append({
            "table": table, "source": source_key, "columns": names,
            "matrix": matrix,
        })
        heat = charts.svg_heatmap(names, matrix,
                                  "Pearson correlation: %s" % table)
        if heat:
            fname = "figures/corr_%s.svg" % _slug(table)
            charts.write_chart(output_dir, fname, heat, artifacts,
                               chart_list, "heatmap")
        evidence.append({
            "claim": "table %s Pearson correlation matrix (%d numeric columns)"
                     % (table, len(names)),
            "locator": "correlation_matrix.csv",
            "quote": "columns=%s" % json.dumps(names, ensure_ascii=False),
            "role": "correlation",
        })

    return profiled, numeric


def _close(a, b):
    if a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) < 1e-6


def _slug(s):
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in str(s))


# ---------------------------------------------------------------------------
# query execution
# ---------------------------------------------------------------------------
def summarize_result(res):
    if res.get("error"):
        return None
    rows = res["rows"]
    if not rows:
        return []
    if len(rows) == 1:
        if len(rows[0]) == 1:
            return next(iter(rows[0].values()))
        return rows[0]
    return rows


def run_queries(queries, connections, result, query_log, qlog_csv):
    if not queries:
        return
    for q in queries:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or "q%d" % (len(query_log) + 1))
        sql = q.get("sql")
        purpose = q.get("purpose") or ""
        src = q.get("source")
        conn, src_key = _pick_connection(connections, src)
        if conn is None:
            entry = {"id": qid, "sql": sql, "purpose": purpose, "source": src,
                     "status": "error", "error": "no data source available"}
            query_log.append(entry)
            qlog_csv.append([qid, src or "", sql or "", purpose, "", "error",
                             "no data source available", ""])
            continue
        if not sql or not isinstance(sql, str):
            entry = {"id": qid, "sql": sql, "purpose": purpose, "source": src_key,
                     "status": "error", "error": "missing sql"}
            query_log.append(entry)
            qlog_csv.append([qid, src_key, sql or "", purpose, "",
                             "error", "missing sql", ""])
            continue
        res = dbutil.execute_query(conn, sql, MAX_QUERY_ROWS)
        summary = summarize_result(res)
        status = "ok" if res.get("error") is None else "error"
        entry = {"id": qid, "sql": sql, "purpose": purpose, "source": src_key,
                 "row_count": res.get("row_count"), "truncated": res.get("truncated"),
                 "status": status}
        if res.get("error"):
            entry["error"] = res["error"]
        else:
            entry["summary"] = summary
        query_log.append(entry)
        qlog_csv.append([qid, src_key, sql, purpose,
                         "" if res.get("row_count") is None else res["row_count"],
                         status, res.get("error") or "",
                         _compact(summary)])
        if status == "ok" and summary is not None:
            result["answers"][qid] = summary
        ck = q.get("contract_key")
        if ck and status == "ok" and summary is not None:
            result["_contract_fills"][str(ck)] = summary
        elif ck and status == "error":
            add_uncertainty(result, str(ck), "query_failed",
                            "query %s failed: %s" % (qid, res.get("error")))


def _pick_connection(connections, src):
    if not connections:
        return None, None
    if src:
        for key, conn in connections:
            if key == src or key.endswith(src) or src in key:
                return conn, key
        return None, src
    key, conn = connections[0]
    return conn, key


def _compact(obj):
    s = json.dumps(obj, ensure_ascii=False, default=_json_default)
    return s[:400]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def dedupe_sources(sources):
    seen = set()
    out = []
    for s in sources:
        key = (s.get("locator"), s.get("quote"), s.get("role"))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def build_report(result, table_schema, source_meta, task_text, dict_texts):
    lines = []
    lines.append("# 科研数据库查询与分析报告")
    lines.append("")
    lines.append("- case_id: `%s`" % (result.get("case_id") or "(missing)"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- skill version: `%s`" % VERSION)
    lines.append("")
    lines.append("## 1. 数据源与 schema")
    lines.append("")
    if not source_meta:
        lines.append("未发现可用的 SQLite / CSV 数据快照。")
    else:
        for s in source_meta:
            if s.get("kind") == "sqlite":
                lines.append("- SQLite `%s`：表 %s"
                             % (s["source"], ", ".join(s.get("tables", []))))
            else:
                lines.append("- CSV `%s`（delimiter=%r）→ 表 `%s`，%d 行"
                             % (s["source"], s.get("delimiter"),
                                s.get("table"), s.get("n_rows", 0)))
    lines.append("")
    if table_schema:
        lines.append("| 表 | 行数 | 列 |")
        lines.append("|-|-|-|")
        for t in table_schema:
            lines.append("| `%s` | %d | %s |"
                         % (t["table"], t["row_count"],
                            ", ".join(c["name"] for c in t["columns"])))
    lines.append("")
    lines.append("## 2. 数据质量与统计")
    lines.append("")
    dq = result["analysis"].get("data_quality", [])
    if dq:
        lines.append("| 表 | 重复组 | 涉行 |")
        lines.append("|-|-|-|")
        for d in dq:
            lines.append("| `%s` | %d | %d |"
                         % (d["table"], d["n_duplicate_groups"],
                            d["n_rows_in_duplicate_groups"]))
    lines.append("")
    lines.append("数值列的描述统计见 `summary_stats.csv`；逐列缺失/去重见 "
                 "`data_quality.csv`；相关矩阵见 `correlation_matrix.csv`。")
    lines.append("")
    lines.append("## 3. 任务要求与完成情况")
    lines.append("")
    if task_text:
        lines.append("```text")
        lines.append(task_text[:3000])
        lines.append("```")
    lines.append("")
    lines.append("查询日志见 `query_log.csv` 与 `result.json` 的 `query_log`；"
                 "证据定位见 `evidence_map.json`。")
    lines.append("")
    lines.append("## 4. 异常与待人工确认")
    lines.append("")
    if result["errors"]:
        lines.append("### errors")
        for e in result["errors"]:
            lines.append("- [%s] %s" % (e.get("where"), e.get("message")))
    else:
        lines.append("无错误。")
    if result["uncertainty"]:
        lines.append("### uncertainty")
        for u in result["uncertainty"]:
            lines.append("- %s（%s）：%s"
                         % (u.get("item"), u.get("level"), u.get("reason")))
    if result["pending_human"]:
        lines.append("### pending_human")
        for p in result["pending_human"]:
            lines.append("- %s" % p)
    if result["limitations"]:
        lines.append("### limitations")
        for p in result["limitations"]:
            lines.append("- %s" % p)
    lines.append("")
    lines.append("## 5. 字典材料")
    lines.append("")
    if dict_texts:
        for p, txt in dict_texts.items():
            lines.append("### `%s`" % os.path.basename(p))
            lines.append("```text")
            lines.append(txt[:3000])
            lines.append("```")
            lines.append("")
    else:
        lines.append("未发现独立数据字典文件。")
    return "\n".join(lines) + "\n"
