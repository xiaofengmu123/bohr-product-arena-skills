#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main: 确定性只读科研数据查询与分析流水线（中间表示与可复核版）。

用法:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work [--queries /app/work/queries.json]

行为: 扫描 /input → 立即写合法 result.json 骨架 → 只读连接库/CSV 做 schema 画像、
缺失/重复检测、描述统计、Pearson 相关与可编辑 SVG → 落 plan.json / figure_model.json
两套中间表示 → 执行定向查询 → 产出 report/evidence/CSV，最终诚实标注 status。

仅使用 Python 标准库；不产出任何预写答案。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as ci

VERSION = "1.0.0"
MAX_BAR_CATS_SAFE = ci.MAX_BAR_CATS


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="read-only scientific DB/CSV query & analysis pipeline (IR version)")
    p.add_argument("--input", default="/input", help="input directory (default /input)")
    p.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    p.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    p.add_argument("--queries", default=None,
                   help="optional queries.json path (default <work>/queries.json)")
    return p.parse_args(argv)


def add_change(log, step, change, status):
    log.append({"ts": ci.now_ts(), "step": step, "change": change, "status": status})


def make_skeleton(case_id, contract, status, t0):
    result = {"case_id": case_id}
    for k in contract.keys():
        result[k] = None
    result.update({
        "status": status,
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "analysis": {},
        "query_log": [],
        "answers": {},
        "artifacts": {},
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_at": ci.now_ts(), "elapsed_seconds": None, "version": VERSION},
        "ir": {"plan": None, "figure_model": None, "evidence_map": None},
    })
    return result


def write_result_safe(path, result):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=ci._json_default)
        return True
    except Exception as exc:  # noqa: BLE001
        try:
            minimal = {"case_id": result.get("case_id"),
                       "status": "partial",
                       "errors": ["result_write_failed: %s" % exc],
                       "sources": [], "uncertainty": [], "change_log": []}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(minimal, f, ensure_ascii=False)
            return True
        except Exception:  # noqa: BLE001
            return False


def add_qlog(qlog, qid, sql, purpose, row_count, summary=None, status="ok", error=None):
    e = {"id": qid, "sql": sql, "purpose": purpose, "row_count": row_count, "status": status}
    if summary is not None:
        e["summary"] = summary
    if error is not None:
        e["error"] = error
    qlog.append(e)


def infer_join_candidates(tables_by_name, profiles_by_table, field_sem):
    """FK 优先；否则按同名 + 取值重叠推断候选（不自动 join，只给证据）。"""
    cands = []
    names = sorted(tables_by_name.keys())
    for a in names:
        for b in names:
            if a >= b:
                continue
            fks = tables_by_name[a].get("fks") or []
            fk_matched = False
            for fk in fks:
                if (fk.get("table") or "").lower() == b.lower():
                    cands.append({"left": a, "right": b, "left_key": fk.get("from"),
                                  "right_key": fk.get("to"), "basis": "foreign_key",
                                  "evidence": "PRAGMA foreign_key_list(%s): %s->%s.%s"
                                              % (a, fk.get("from"), fk.get("table"), fk.get("to")),
                                  "confidence": "high"})
                    fk_matched = True
            # 名称匹配推断（补充候选，不覆盖 FK）
            cols_a = {c["name"].lower(): c["name"] for c in tables_by_name[a]["columns"]}
            cols_b = {c["name"].lower(): c["name"] for c in tables_by_name[b]["columns"]}
            for cn in sorted(set(cols_a) & set(cols_b)):
                if fk_matched and any(c["basis"] == "foreign_key"
                                      and c["left"] == a and c["right"] == b
                                      for c in cands):
                    continue
                cands.append({"left": a, "right": b, "left_key": cols_a[cn],
                              "right_key": cols_b[cn], "basis": "name_match",
                              "evidence": "same column name '%s' in %s and %s" % (cols_a[cn], a, b),
                              "confidence": "medium"})
    return cands


def main(argv=None):
    args = parse_args(argv)
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    errors, degradations, sources, uncertainty, pending_human, limitations = [], [], [], [], [], []
    artifacts, query_log, answers = {}, [], {}
    change_log = []
    evidence = []

    # ---- 阶段 0：发现 + 骨架（先落盘再分析） ----
    discovery = ci.discover_inputs(input_dir)
    case, case_file, case_err = ci.load_case(discovery)
    contract = ci.extract_contract(case) if isinstance(case, dict) else {}
    case_id = case.get("case_id") if isinstance(case, dict) else None
    status = "partial"

    result = make_skeleton(case_id, contract, status, t0)
    add_change(change_log, "p0", "skeleton written before analysis", "partial")
    result["change_log"] = list(change_log)
    write_result_safe(output_dir / "result.json", result)

    if case_err:
        errors.append("case_load: %s" % case_err)
        degradations.append("case_load: %s (continuing with null case_id)" % case_err)
    if case_file:
        artifacts["input/case.json"] = "input"
    if discovery.get("task_file"):
        artifacts["input/task.md"] = "input"

    # 任务书摘录作为 sources
    task_text = None
    if discovery.get("task_file"):
        task_text, _enc, _err = ci.read_text(discovery["task_file"])
        if task_text:
            for ln in ci.quote_task_lines(task_text):
                sources.append({"locator": discovery["task_file"], "quote": ln, "role": "task"})
        elif _err:
            degradations.append("task_md_read: %s" % _err)

    # ---- 阶段 1：连接与 schema 画像 ----
    tables_by_name = {}
    profiles_by_table = {}
    data_summary = []
    all_numeric_series = []
    figure_list = []
    value_counts_out = {}
    summary_rows = []
    quality_rows = []

    for db in discovery.get("db_files"):
        conn = None
        try:
            conn = ci.connect_ro(db)
        except Exception as exc:  # noqa: BLE001
            errors.append("connect: %s: %s" % (db, exc))
            degradations.append("connect_failed: %s" % db)
            continue
        try:
            schema = ci.inspect_schema(conn)
            for t in schema:
                tname = t["table"]
                tables_by_name[tname] = t
                data_summary.append({"source": db, "kind": "sqlite", "table": tname,
                                     "row_count": t["row_count"],
                                     "n_columns": len(t["columns"])})
                evidence.append({
                    "claim": "table %s has %d columns and %d rows" % (tname, len(t["columns"]), t["row_count"]),
                    "locator": "sqlite://%s#table=%s" % (db, tname),
                    "quote": "columns=%s; row_count=%d" % ([c["name"] for c in t["columns"]], t["row_count"]),
                    "role": "schema"})
                add_qlog(query_log, "%s:%s:schema" % (Path(db).name, tname),
                         "SELECT name,type FROM sqlite_master; PRAGMA table_info('%s')" % tname.replace("'", "''"),
                         "schema discovery", t["row_count"], None)
                profiles = [ci.column_profile(conn, tname, c["name"], c["type"])
                            for c in t["columns"]]
                profiles_by_table[tname] = profiles
                for prof in profiles:
                    add_qlog(query_log, "%s:%s:%s:extract" % (Path(db).name, tname, prof["column"]),
                             prof["extraction_sql"] or "(csv extract)",
                             "column value extraction", prof["n_nonnull"], None)
                    if prof["kind"] in ("numeric", "coded_as_text"):
                        st = (prof.get("numeric") or {}).get("stats") or {}
                        if st:
                            summary_rows.append([tname, prof["column"], prof["kind"], st.get("n"),
                                                 prof["n_nonnull"], prof["n_missing"],
                                                 prof["n_distinct"], st.get("mean"),
                                                 st.get("stdev_sample"), st.get("min"),
                                                 st.get("max"), st.get("median"),
                                                 st.get("q1"), st.get("q3"), st.get("iqr"),
                                                 st.get("n_outliers_iqr15")])
                            evidence.append({
                                "claim": "column %s.%s numeric summary" % (tname, prof["column"]),
                                "locator": prof["extraction_sql"],
                                "quote": "n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s"
                                         % (st.get("n"), st.get("mean"), st.get("stdev_sample"),
                                            st.get("min"), st.get("max")),
                                "role": "statistic"})
                            agg = prof.get("aggregate")
                            if agg and "error" not in agg:
                                add_qlog(query_log, "%s:%s:%s:aggregate" % (Path(db).name, tname, prof["column"]),
                                         agg["sql"], "SQL aggregate cross-check vs Python",
                                         agg.get("n"),
                                         {"avg": agg.get("avg"), "min": agg.get("min"),
                                          "max": agg.get("max"),
                                          "matches_python": agg.get("matches_python")})
                                if agg.get("matches_python") is False:
                                    uncertainty.append({"item": "%s.%s mean" % (tname, prof["column"]),
                                                        "level": "source_conflict",
                                                        "reason": "SQL AVG differs from Python recomputation"})
                    else:
                        summary_rows.append([tname, prof["column"], prof["kind"], None,
                                             prof["n_nonnull"], prof["n_missing"],
                                             prof["n_distinct"], None, None, None, None,
                                             None, None, None, None, None])
                    quality_rows.append([tname, prof["column"], prof["kind"],
                                         prof["n_total"], prof["n_missing"],
                                         prof["n_distinct"],
                                         prof["top_values"][0]["value"] if prof["top_values"] else "",
                                         prof["top_values"][0]["count"] if prof["top_values"] else ""])
                    # value_counts：仅类别/编码列或低基数数值列
                    if prof["kind"] != "empty":
                        key = "%s.%s" % (tname, prof["column"])
                        value_counts_out[key] = prof["top_values"]
                    if prof["kind"] in ("numeric", "coded_as_text"):
                        nums = (prof.get("numeric") or {}).get("values") or []
                        if nums:
                            all_numeric_series.append(("%s.%s" % (tname, prof["column"]), nums))
                # 重复检测
                dup = ci.detect_duplicates(conn, tname)
                if dup.get("full_row_dup_sql"):
                    add_qlog(query_log, "%s:%s:duplicates" % (Path(db).name, tname),
                             dup["full_row_dup_sql"], "exact full-row duplicate detection",
                             dup["n_duplicate_groups"], dup)
                    evidence.append({
                        "claim": "table %s has %d exact duplicate groups" % (tname, dup["n_duplicate_groups"]),
                        "locator": dup["full_row_dup_sql"],
                        "quote": "n_duplicate_groups=%d; n_rows_in_groups=%d"
                                 % (dup["n_duplicate_groups"], dup["n_rows_in_duplicate_groups"]),
                        "role": "data-quality"})
                    quality_rows.append([tname, "(all columns)", "duplicate_check", None,
                                         dup["n_duplicate_groups"],
                                         dup["n_rows_in_duplicate_groups"], "", ""])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    for csvf in discovery.get("csv_files"):
        header, rows, err = ci.read_csv_as_table(csvf)
        if err:
            errors.append("csv: %s: %s" % (csvf, err))
            degradations.append("csv_failed: %s" % csvf)
            continue
        tname = Path(csvf).stem
        tbl = ci.profile_csv_table(tname, header, rows)
        tables_by_name[tname] = tbl
        profiles_by_table[tname] = tbl["profiles"]
        data_summary.append({"source": csvf, "kind": "csv", "table": tname,
                             "row_count": tbl["row_count"], "n_columns": len(tbl["columns"])})
        evidence.append({"claim": "csv %s has %d columns and %d rows" % (tname, len(tbl["columns"]), tbl["row_count"]),
                         "locator": "csv://%s" % csvf,
                         "quote": "columns=%s; row_count=%d" % ([c["name"] for c in tbl["columns"]], tbl["row_count"]),
                         "role": "schema"})
        for prof in tbl["profiles"]:
            st = (prof.get("numeric") or {}).get("stats") or {}
            summary_rows.append([tname, prof["column"], prof["kind"],
                                 st.get("n"), prof["n_nonnull"], prof["n_missing"],
                                 prof["n_distinct"], st.get("mean"), st.get("stdev_sample"),
                                 st.get("min"), st.get("max"), st.get("median"),
                                 st.get("q1"), st.get("q3"), st.get("iqr"),
                                 st.get("n_outliers_iqr15")])
            quality_rows.append([tname, prof["column"], prof["kind"], prof["n_total"],
                                 prof["n_missing"], prof["n_distinct"],
                                 prof["top_values"][0]["value"] if prof["top_values"] else "",
                                 prof["top_values"][0]["count"] if prof["top_values"] else ""])
            key = "%s.%s" % (tname, prof["column"])
            value_counts_out[key] = prof["top_values"]
            if prof["kind"] in ("numeric", "coded_as_text"):
                nums = (prof.get("numeric") or {}).get("values") or []
                if nums:
                    all_numeric_series.append(("%s.%s" % (tname, prof["column"]), nums))

    # ---- 阶段 2：相关分析 + 图形模型 ----
    corr_rows = []
    if len(all_numeric_series) >= 2:
        matrix, pairs = ci.correlate(all_numeric_series)
        names = [n for n, _ in all_numeric_series]
        for pr in pairs:
            corr_rows.append([pr["var1"], pr["var2"],
                              "" if pr["pearson_r"] is None else "%.6f" % pr["pearson_r"],
                              pr["n_pairs"]])
        heat = ci.corr_heatmap_svg("Pearson correlation", names, matrix)
        if heat:
            (output_dir / "figures").mkdir(parents=True, exist_ok=True)
            with open(output_dir / "figures" / "fig_corr_heatmap.svg", "w", encoding="utf-8") as f:
                f.write(heat)
            artifacts["figures/fig_corr_heatmap.svg"] = "chart"
            figure_list.append({
                "id": "fig_corr_heatmap", "kind": "correlation_heatmap",
                "title": "Pearson correlation matrix",
                "data": {"source": "correlation_matrix.csv",
                         "columns": ["var1", "var2", "pearson_r", "n_pairs"]},
                "encoding": {"x": "var1", "y": "var2", "color": "pearson_r"},
                "caption": "Pairwise Pearson correlation of numeric columns (pairwise complete pairs).",
                "evidence": [{"locator": "correlation_matrix.csv",
                              "quote": "rows=%d" % len(corr_rows)}],
                "svg": "figures/fig_corr_heatmap.svg"})
        # 前两列数值散点
        if len(all_numeric_series) >= 2:
            n1, v1 = all_numeric_series[0]
            n2, v2 = all_numeric_series[1]
            sc = ci.scatter_svg("Scatter: %s vs %s" % (n1, n2), v1, v2, n1, n2)
            if sc:
                with open(output_dir / "figures" / "fig_scatter.svg", "w", encoding="utf-8") as f:
                    f.write(sc)
                artifacts["figures/fig_scatter.svg"] = "chart"
                figure_list.append({
                    "id": "fig_scatter", "kind": "scatter", "title": "%s vs %s" % (n1, n2),
                    "data": {"source": "data snapshot", "columns": [n1, n2]},
                    "encoding": {"x": n1, "y": n2}, "caption": "Scatter of first two numeric columns.",
                    "evidence": [{"locator": "figures/fig_scatter.svg", "quote": "points=%d" % min(len(v1), len(v2))}],
                    "svg": "figures/fig_scatter.svg"})
    # 每表每个低基数列一张条形图（最多 6 张）
    bar_count = 0
    for tname in sorted(profiles_by_table):
        for prof in profiles_by_table[tname]:
            if bar_count >= 6:
                break
            top = prof.get("top_values") or []
            if prof["kind"] in ("categorical", "coded_as_text") and 2 <= len(top) <= MAX_BAR_CATS_SAFE:
                labels = [t["value"] for t in top]
                counts = [t["count"] for t in top]
                svg = ci.bar_chart_svg("%s.%s top values" % (tname, prof["column"]), labels, counts)
                if svg:
                    (output_dir / "figures").mkdir(parents=True, exist_ok=True)
                    fn = "fig_bar_%s.svg" % bar_count
                    with open(output_dir / "figures" / fn, "w", encoding="utf-8") as f:
                        f.write(svg)
                    artifacts["figures/%s" % fn] = "chart"
                    figure_list.append({
                        "id": fn.replace(".svg", ""), "kind": "bar",
                        "title": "%s.%s top values" % (tname, prof["column"]),
                        "data": {"source": "value_counts.json",
                                 "columns": ["%s.%s" % (tname, prof["column"])]},
                        "encoding": {"x": "value", "y": "count"},
                        "caption": "Top value counts for %s.%s" % (tname, prof["column"]),
                        "evidence": [{"locator": "value_counts.json",
                                      "quote": "top_values=%d" % len(top)}],
                        "svg": "figures/%s" % fn})
                    bar_count += 1
        if bar_count >= 6:
            break

    # ---- 阶段 3：字段语义 + 连接候选 + 跨表 ----
    field_sem = []
    for tname in sorted(profiles_by_table):
        field_sem.extend(ci.field_semantics_from_profiles(tname, profiles_by_table[tname]))
    join_cands = infer_join_candidates(tables_by_name, profiles_by_table, field_sem)
    cross_table = {"tables": data_summary, "join_candidates": join_cands,
                   "note": "No automatic join aggregation; candidates recorded for agent review."}
    if len(tables_by_name) <= 1:
        cross_table["note"] = "single table (or single CSV): no cross-table join required"
        cross_table["join_candidates"] = []

    # 数据字典来源登记（供 agent 在阶段 1 补齐字段解释）
    dict_sources = []
    for d in discovery.get("dict_files"):
        if d == discovery.get("case_file"):
            continue
        text, _e, _err = ci.read_text(d)
        if text:
            dict_sources.append({"locator": d, "quote": text[:400], "role": "dictionary"})
        else:
            degradations.append("dict_read: %s: %s" % (d, _err))

    # ---- 阶段 4：定向查询（work/queries.json） ----
    queries_path = Path(args.queries) if args.queries else work_dir / "queries.json"
    tagged = []
    if queries_path.exists():
        obj, qerr = ci.load_json_loose(queries_path)
        if qerr:
            errors.append("queries_json: %s" % qerr)
        elif isinstance(obj, list):
            tagged = [q for q in obj if isinstance(q, dict) and q.get("sql")]
        elif isinstance(obj, dict):
            tagged = [q for q in obj.get("queries", []) if isinstance(q, dict) and q.get("sql")]
    for q in tagged:
        qid = q.get("id") or ("q%d" % len(answers))
        sql = q["sql"]
        note = q.get("note") or q.get("purpose") or "targeted query"
        executed = False
        for db in discovery.get("db_files"):
            conn = None
            try:
                conn = ci.connect_ro(db)
                res, rerr = ci.run_query(conn, sql)
                if rerr is None:
                    add_qlog(query_log, qid, sql, note, res["row_count"],
                             {"columns": res["columns"], "rows": res["rows"],
                              "truncated": res["truncated"]})
                    answers[qid] = {"columns": res["columns"], "rows": res["rows"],
                                    "row_count": res["row_count"],
                                    "truncated": res["truncated"], "source": db}
                    executed = True
                    conn.close()
                    break
                else:
                    add_qlog(query_log, qid, sql, note, None, None, "error", rerr)
            except Exception as exc:  # noqa: BLE001
                add_qlog(query_log, qid, sql, note, None, None, "error", str(exc))
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
        if not executed and not discovery.get("db_files") and discovery.get("csv_files"):
            # 对 CSV 快照执行受限查询：仅支持简单 SELECT * / 计数，失败即记录
            add_qlog(query_log, qid, sql, note, None, None, "error",
                     "no sqlite database available; SQL query not applicable to CSV")
            uncertainty.append({"item": qid, "level": "not_applicable",
                                "reason": "SQL query cannot run against CSV snapshot without DB"})

    # ---- 落盘中间表示与 CSV ----
    (output_dir / "figures").mkdir(parents=True, exist_ok=True)
    plan = ci.build_plan(case_id, discovery, contract, data_summary, field_sem,
                         join_cands, [{"task_file": discovery.get("task_file"),
                                       "quoted_lines": ci.quote_task_lines(task_text or "")}],
                         [], [])
    figure_model = ci.build_figure_model(figure_list)
    ci.write_json(output_dir / "plan.json", plan)
    ci.write_json(output_dir / "figure_model.json", figure_model)
    ci.write_json(output_dir / "schema.json", list(tables_by_name.values()))
    ci.write_json(output_dir / "value_counts.json", value_counts_out)
    ci.write_json(output_dir / "evidence_map.json", evidence)
    ci.write_csv(output_dir / "summary_stats.csv",
              ["table", "column", "kind", "n", "n_nonnull", "n_missing", "n_distinct",
               "mean", "stdev_sample", "min", "max", "median", "q1", "q3", "iqr",
               "n_outliers_iqr15"], summary_rows)
    ci.write_csv(output_dir / "data_quality.csv",
              ["table", "column", "kind", "n_total", "n_missing", "n_distinct",
               "top_value", "top_value_count"], quality_rows)
    ci.write_csv(output_dir / "correlation_matrix.csv",
              ["var1", "var2", "pearson_r", "n_pairs"], corr_rows)
    qlog_rows = [[e.get("id"), e.get("sql"), e.get("purpose"), e.get("row_count"),
                  e.get("status"), e.get("error", "")] for e in query_log]
    ci.write_csv(output_dir / "query_log.csv",
              ["id", "sql", "purpose", "row_count", "status", "error"], qlog_rows)

    artifacts["report.md"] = "report"
    artifacts["evidence_map.json"] = "evidence"
    artifacts["schema.json"] = "schema"
    artifacts["plan.json"] = "ir"
    artifacts["figure_model.json"] = "ir"
    artifacts["summary_stats.csv"] = "table"
    artifacts["data_quality.csv"] = "table"
    artifacts["correlation_matrix.csv"] = "table"
    artifacts["query_log.csv"] = "table"
    artifacts["value_counts.json"] = "table"

    # ---- report.md ----
    report_lines = []
    report_lines.append("# 科研数据库查询与分析报告\n")
    report_lines.append("- case_id: %s" % (case_id if case_id is not None else "(null)"))
    report_lines.append("- status: %s" % status)
    report_lines.append("- data sources: %d (db=%d, csv=%d)\n"
                        % (len(data_summary), len(discovery["db_files"]), len(discovery["csv_files"])))
    report_lines.append("## 数据源\n")
    for ds in data_summary:
        report_lines.append("- %s [%s] table=%s rows=%s cols=%s"
                           % (Path(ds["source"]).name, ds["kind"], ds["table"],
                              ds["row_count"], ds["n_columns"]))
    report_lines.append("\n## 中间表示\n")
    report_lines.append("- 分析计划: `plan.json`")
    report_lines.append("- 图形模型: `figure_model.json`")
    report_lines.append("- 证据映射: `evidence_map.json`")
    report_lines.append("\n## 数据质量\n")
    for q in quality_rows[:40]:
        report_lines.append("- %s.%s: kind=%s, missing=%s, distinct=%s"
                           % (q[0], q[1], q[2], q[4], q[5]))
    report_lines.append("\n## 相关分析\n")
    if corr_rows:
        for r in corr_rows[:20]:
            report_lines.append("- %s ~ %s: r=%s (n=%s)" % (r[0], r[1], r[2], r[3]))
    else:
        report_lines.append("- 数值列不足 2 个，未计算相关。")
    report_lines.append("\n## 图形\n")
    for fig in figure_list:
        report_lines.append("- %s: %s -> %s" % (fig["id"], fig["title"], fig["svg"]))
    report_lines.append("\n## 定向查询\n")
    if tagged:
        for e in query_log:
            if e.get("id") and any(e.get("id") == t.get("id") for t in tagged):
                report_lines.append("- %s: rows=%s status=%s" % (e["id"], e["row_count"], e["status"]))
    else:
        report_lines.append("- 未提供 /app/work/queries.json。")
    report_lines.append("\n## 局限与待人工项\n")
    if limitations:
        report_lines.extend("- %s" % x for x in limitations)
    if pending_human:
        report_lines.extend("- %s" % x for x in pending_human)
    report_lines.append("- 字段含义/单位/口径需在阶段 1 依据数据字典确认后写回 plan.json。")
    with open(output_dir / "report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    # ---- 状态判定 ----
    if not data_summary:
        status = "input_error" if case_err and case_file else "insufficient_evidence"
        pending_human.append("no DB/CSV snapshot found in /input")
        limitations.append("no queryable data source; only skeleton delivered")
    else:
        contract_filled = bool(contract) and all(
            result.get(k) is not None for k in contract.keys())
        status = "ok" if (contract_filled and not errors) else "partial"

    elapsed = time.time() - t0
    result.update({
        "status": status,
        "sources": sources + dict_sources,
        "uncertainty": uncertainty,
        "change_log": change_log,
        "analysis": {
            "data_sources": data_summary,
            "field_semantics": field_sem,
            "cross_table": cross_table,
            "correlation_rows": len(corr_rows),
            "figures": [f["id"] for f in figure_list],
        },
        "query_log": query_log,
        "answers": answers,
        "artifacts": artifacts,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "pending_human": pending_human,
        "runtime": {"started_at": result["runtime"]["started_at"],
                    "elapsed_seconds": round(elapsed, 3), "version": VERSION},
        "ir": {"plan": "plan.json", "figure_model": "figure_model.json",
               "evidence_map": "evidence_map.json"},
    })
    add_change(change_log, "p5", "finalized status=%s" % status, status)
    result["change_log"] = change_log
    write_result_safe(output_dir / "result.json", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
