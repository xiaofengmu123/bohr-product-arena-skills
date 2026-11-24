#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the sci-data-cleaning skill (provenance-reinforced variant).

Usage (standard-library only, deterministic, exit code 0 even on bad input):
    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --output /app/output --work /app/work

Guarantees:
  * bootstrap writes a valid result.json skeleton immediately (partial), so any
    later failure leaves a loadable, honest partial deliverable.
  * full discovers materials, profiles/cleans the main tabular source with
    conservative defaults, writes cleaned_data.csv + data_profile.json +
    quality_report.md + evidence_map.json + source_snapshot.json, then finalizes
    result.json. The Agent's semantic decisions (primary keys, QC bounds, unit
    conversions, field semantics) are read from /app/work/agent_rules.json.
  * selfcheck validates result.json and artifacts, re-opens the referenced
    materials to verify every quote verbatim, and recomputes every report number
    from cleaned_data.csv. It reports PASS/FAIL but always exits 0.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C

_start_ts = time.time()

REPORT_FIELDS = ("n_rows", "n_cols", "missing_tallies", "n_duplicate_groups",
                 "n_duplicate_rows", "duplicate_keys", "n_outlier_flags",
                 "n_conversions")

META_PREFIX = "__"


def _log(msg):
    print("[%s] %s" % (C.now_iso(), msg), flush=True)


def load_agent_rules(work_dir):
    path = os.path.join(work_dir, "agent_rules.json")
    if not os.path.isfile(path):
        return {}
    info = C.read_text_robust(path)
    obj, status = C.parse_json_robust(info["text"], {})
    if not isinstance(obj, dict):
        _log("agent_rules.json unreadable; using conservative defaults (status=%s)" % status)
        return {}
    return obj


def _default_bounds_from_rules(agent_rules):
    return agent_rules.get("qc_bounds", {})


def auto_keys(profile):
    keys = []
    for name, col in profile.get("columns", {}).items():
        low = C.normalize_token(name)
        id_hints = ("id", "no", "code", "sample", "subject", "编号", "样本", "受试者", "标识", "时间", "date", "time")
        if any(h in low for h in id_hints) and col.get("unique_ratio", 0) > 0.5:
            keys.append(name)
        if len(keys) >= 3:
            break
    return keys


def _strip_internal(obj):
    """Remove internal keys (leading '_') so artifacts stay readable."""
    if isinstance(obj, dict):
        return {k: _strip_internal(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_internal(v) for v in obj]
    return obj


def _safe_col_suffix(s):
    out = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(s))
    return out or "col"


def _cell(row, idx):
    return row[idx] if 0 <= idx < len(row) else ""


def build_cleaned(header, rows, dups, outl, agent_rules):
    n = len(rows)
    # duplicate group / keep lookup per row_id (1-based)
    dup_group = {}
    dup_keep = {}
    for gi, (key, ids) in enumerate(sorted(dups.items(), key=lambda kv: min(kv[1])), 1):
        keep_first = agent_rules.get("duplicate_keep", {}).get(key, ids[0])
        for rid in ids:
            dup_group[rid] = gi
            dup_keep[rid] = bool(rid == keep_first)
    # outlier flag lookup per field -> row -> (flag, action)
    outl_map = {}
    for field, flags in outl.items():
        outl_map[field] = {f["row_id"]: (f["flag"], f["action"]) for f in flags}
    # conversions
    conversions = agent_rules.get("unit_conversions", []) or []
    conv_by_field = {}
    conv_cols = []
    for conv in conversions:
        field = conv.get("field")
        if not field:
            continue
        to = _safe_col_suffix(conv.get("to_unit", "converted"))
        colname = "%sconv_%s_%s" % (META_PREFIX, _safe_col_suffix(field), to)
        factor = conv.get("factor")
        conv_by_field.setdefault(field, []).append((colname, factor))
        if colname not in conv_cols:
            conv_cols.append(colname)

    meta = ["%srow_id" % META_PREFIX, "%sdup_group" % META_PREFIX, "%sdup_keep" % META_PREFIX,
            "%soutlier_flag" % META_PREFIX, "%soutlier_action" % META_PREFIX]
    new_header = list(header) + meta + conv_cols
    new_rows = []
    for ri, row in enumerate(rows):
        rid = ri + 1
        oflag, oaction = "", ""
        for field, m in outl_map.items():
            ci = C.col_index(header, field)
            if ci is not None and rid in m:
                oflag, oaction = m[rid]
        base = list(row)
        base.append(rid)
        base.append(dup_group.get(rid, ""))
        base.append(dup_keep.get(rid, ""))
        base.append(oflag)
        base.append(oaction)
        for colname in conv_cols:
            base.append("")
        for field, cols in conv_by_field.items():
            ci = C.col_index(header, field)
            if ci is None:
                continue
            val = C.parse_number(_cell(row, ci))
            for colname, factor in cols:
                j = new_header.index(colname)
                if val is None or factor is None:
                    base[j] = ""
                else:
                    base[j] = val * factor
        new_rows.append(base)
    return new_header, new_rows


def _report_summary(profile, dups, outl, conversions, dup_keys):
    tallies = {k: 0 for k in C.MISSING_KINDS}
    for col in profile["columns"].values():
        for k, v in col.get("kind_counts", {}).items():
            if k in tallies:
                tallies[k] = tallies.get(k, 0) + v
    n_dup_rows = sum(len(ids) for ids in dups.values())
    return {
        "n_rows": profile.get("n_rows", 0),
        "n_cols": profile.get("n_cols", 0),
        "missing_tallies": tallies,
        "n_duplicate_groups": len(dups),
        "n_duplicate_rows": n_dup_rows,
        "duplicate_keys": dup_keys,
        "n_outlier_flags": sum(len(f) for f in outl.values()),
        "n_conversions": len(conversions),
    }


def build_assertions(t, profile, dups, outl, agent_rules, artifacts, dup_keys, cap_cells=200):
    """Deterministic assertion scaffolding. The Agent augments this in place."""
    header = t["header"]
    rows = t["rows"]
    aid = 0
    assertions = []

    def nxt():
        nonlocal aid
        aid += 1
        return "A%04d" % aid

    source_path = os.path.basename(t["path"])
    src_sha = t.get("sha256")
    delim = t.get("delimiter") or ","

    def raw_cell(field, row_id):
        ci = C.col_index(header, field)
        if ci is None or not (0 <= row_id - 1 < len(rows)):
            return ""
        return str(rows[row_id - 1][ci])

    # 1) cell-level missing classification (capped; empty cells summarized below)
    field_counts = {}
    for ci, name in enumerate(header):
        field_counts[name] = 0
    for ri, row in enumerate(rows):
        for ci, name in enumerate(header):
            raw = row[ci]
            kind = C.classify_value(raw)
            if kind == C.KIND_VALID or kind == C.KIND_TRUE_ZERO:
                continue
            if str(raw).strip() == "":
                continue  # no verbatim text to quote; covered by field summary
            if field_counts[name] >= cap_cells:
                continue
            field_counts[name] += 1
            loc = "%s::r%d::c[%s]" % (source_path, ri + 1, name)
            assertions.append(C.make_assertion(
                nxt(), "missing_classification",
                "单元格被归为 %s：原文保留、不插补、不与其它类合并。" % kind,
                loc, str(raw), material=source_path, material_sha256=src_sha,
                field=name, record={"row_id": ri + 1}, confidence="high",
                rule_ref="references/cleaning-rules.md#missing-classes"))

    # 1b) field-level summary for empty cells and true zeros (header is verbatim)
    for name, col in profile["columns"].items():
        n_empty = col.get("n_empty", 0)
        n_zero = col.get("kind_counts", {}).get(C.KIND_TRUE_ZERO, 0)
        if n_empty > 0 or n_zero > 0:
            assertions.append(C.make_assertion(
                nxt(), "field_summary",
                "字段 %s：%d 个空单元格（录入缺失）、%d 个真值 0，均保留原文不插补。" % (name, n_empty, n_zero),
                "%s::field::%s" % (source_path, name), name,
                material=source_path, material_sha256=src_sha, field=name,
                confidence="high", rule_ref="references/cleaning-rules.md#missing-classes"))

    # 2) duplicate groups (quote = verbatim first key cell)
    for key, ids in sorted(dups.items(), key=lambda kv: min(kv[1])):
        keep = agent_rules.get("duplicate_keep", {}).get(key, ids[0])
        first = min(ids)
        key_field = dup_keys[0] if dup_keys else header[0] if header else ""
        quote = raw_cell(key_field, first) if key_field else ""
        assertions.append(C.make_assertion(
            nxt(), "duplicate",
            "重复组（近似主键一致）共 %d 条，保留第 %d 条。" % (len(ids), keep),
            "%s::duplicate-group::%s" % (source_path, key[:24]),
            quote, material=source_path, material_sha256=src_sha,
            confidence="medium", rule_ref="references/cleaning-rules.md#duplicates"))

    # 3) outlier flags (quote = verbatim raw cell)
    for field, flags in sorted(outl.items()):
        for f in flags:
            raw = raw_cell(field, f["row_id"])
            assertions.append(C.make_assertion(
                nxt(), "outlier",
                "第 %d 行 %s=%s 命中 %s（处置：%s）。" % (f["row_id"], field, raw, f["flag"], f["action"]),
                "%s::r%d::c[%s]" % (source_path, f["row_id"], field),
                raw, material=source_path, material_sha256=src_sha,
                field=field, record={"row_id": f["row_id"]}, confidence="medium",
                rule_ref="references/cleaning-rules.md#outliers"))

    # 4) conversions (agent-supplied)
    for conv in agent_rules.get("unit_conversions", []) or []:
        field = conv.get("field")
        assertions.append(C.make_assertion(
            nxt(), "conversion",
            "%s 由 %s 换算为 %s（因子 %.10g），原列保留、不覆盖。" % (
                field, conv.get("from_unit", "?"), conv.get("to_unit", "?"), conv.get("factor")),
            conv.get("locator", "%s::conversion" % source_path),
            conv.get("quote", ""), material=conv.get("material", source_path),
            material_sha256=conv.get("material_sha256", src_sha),
            field=field, confidence="high" if conv.get("factor") is not None else "medium",
            rule_ref="references/cleaning-rules.md#units"))

    # 5) field semantics (agent-supplied)
    for field, sem in (agent_rules.get("field_semantics", {}) or {}).items():
        if isinstance(sem, dict):
            assertions.append(C.make_assertion(
                nxt(), "field_semantics",
                "字段 %s 语义：%s。" % (field, sem.get("meaning", "未说明")),
                sem.get("locator", "%s::header" % source_path),
                sem.get("quote", ""), material=sem.get("material", source_path),
                material_sha256=sem.get("material_sha256", src_sha),
                field=field, confidence="medium",
                rule_ref="references/cleaning-rules.md#dictionary"))

    return assertions


def _render_quality_report(profile, dups, outl, conversions, report, t):
    lines = []
    lines.append("# 数据质量报告（由 data_profile.json 实时生成，数字不可手写）")
    lines.append("")
    lines.append("- 数据源：`%s`（编码 %s，分隔符 `%s`，含表头：%s）" % (
        os.path.basename(t["path"]), t["encoding"], t["delimiter"], t["has_header"]))
    lines.append("- 行数：%d；原始列数：%d" % (report["n_rows"], report["n_cols"]))
    lines.append("")
    lines.append("## 1. 缺失/未检测/低于检出限/真值0 分类统计")
    lines.append("")
    lines.append("| 类别 | 单元格数 |")
    lines.append("|---|---|")
    label = {
        C.KIND_ENTERED_MISSING: "录入缺失（空/哨兵）",
        C.KIND_NOT_DETECTED: "未检测",
        C.KIND_BELOW_LOD: "低于检出限（左删失）",
        C.KIND_TRUE_ZERO: "真值 0",
    }
    for k in C.MISSING_KINDS:
        lines.append("| %s | %d |" % (label[k], report["missing_tallies"].get(k, 0)))
    lines.append("")
    lines.append("## 2. 字段级清单")
    lines.append("")
    lines.append("| 字段 | 行数 | 缺失率 | 数值数 | 唯一值 | 判定类型 | 检出哨兵 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, col in profile["columns"].items():
        sent = ", ".join(str(s) for s in col.get("sentinel_tokens_found", [])[:5])
        lines.append("| %s | %d | %.4f | %d | %d | %s | %s |" % (
            name, col["n"], col["missing_rate"], col["n_numeric"], col["n_unique"],
            col["dtype_guess"], sent or "—"))
    lines.append("")
    lines.append("## 3. 重复与异常")
    lines.append("")
    lines.append("- 重复组：%d 组、%d 行；主键：%s" % (
        report["n_duplicate_groups"], report["n_duplicate_rows"], ", ".join(report["duplicate_keys"]) or "（未定，待 Agent 确认）"))
    lines.append("- 异常标记：%d 条（诊断性，默认保留；物理上下界与处置由 QC 规则/Agent 判定）" % report["n_outlier_flags"])
    lines.append("- 单位换算：%d 项（原列保留，新增换算列，因子入 evidence_map）" % report["n_conversions"])
    lines.append("")
    lines.append("> 本报告所有数字来自 data_profile.json，并由 `--phase selfcheck` 从 cleaned_data.csv 实时重算核对。")
    return "\n".join(lines) + "\n"


def run_bootstrap(input_dir, output_dir, started):
    case = C.load_case(input_dir)
    result = {}
    for k, v in case["output_contract"].items():
        result[k] = C.contract_default(v)
    has_signal = bool(case["case_id"] or case["manifest"].get("data"))
    result.update({
        "case_id": case["case_id"],
        "status": C.STATUS_PARTIAL if has_signal else C.STATUS_INSUFFICIENT,
        "sources": [],
        "uncertainty": [],
        "change_log": [{"time": C.now_iso(), "step": "bootstrap", "action": "写入 partial 结果骨架；随后只做增量改进"}],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": started, "finished_at": C.now_iso(), "seconds": round(time.time() - _start_ts, 2)},
        "artifacts": {},
        "_meta": {
            "schema_version": "2.0",
            "contract_keys": list(case["output_contract"].keys()),
            "generated_by": "sci-data-cleaning/main.py",
            "bootstrap": True,
            "input_dir": os.path.abspath(input_dir),
        },
    })
    if case["read_error"]:
        result["errors"].append({"step": "bootstrap", "detail": case["read_error"]})
    if not has_signal:
        result["pending_human"].append("输入目录无可解析的 case.json 或数据表；请核对 /input")
    os.makedirs(output_dir, exist_ok=True)
    C.atomic_write_json(os.path.join(output_dir, "result.json"), result)
    return result


def run_full(input_dir, output_dir, work_dir, started):
    case = C.load_case(input_dir)
    result = run_bootstrap(input_dir, output_dir, started)
    agent_rules = load_agent_rules(work_dir)
    artifacts = {
        "cleaned_data": "cleaned_data.csv",
        "data_profile": "data_profile.json",
        "quality_report": "quality_report.md",
        "evidence_map": "evidence_map.json",
        "source_snapshot": "source_snapshot.json",
    }
    result["artifacts"] = artifacts

    data_files = case["manifest"].get("data", []) or []
    # Prefer the largest tabular file as the main aggregate table.
    main_path = None
    for f in data_files:
        if main_path is None or (os.path.isfile(f) and os.path.getsize(f) > os.path.getsize(main_path)):
            main_path = f

    if main_path is None:
        result["status"] = C.STATUS_INSUFFICIENT
        result["pending_human"].append("未发现主数据表（CSV/TSV/TXT）；材料可能缺失或命名未被识别")
        C.atomic_write_json(os.path.join(output_dir, "result.json"), result)
        _log("no main table found -> insufficient_evidence")
        return result

    t = C.read_table_robust(main_path)
    if t["read_error"]:
        result["status"] = C.STATUS_PARTIAL
        result["errors"].append({"step": "full", "detail": "主表 %s 读取失败：%s" % (os.path.basename(main_path), t["read_error"])})
        C.atomic_write_json(os.path.join(output_dir, "result.json"), result)
        _log("main table unreadable -> partial")
        return result

    header, rows = t["header"], t["rows"]
    profile = C.profile_table(header, rows)
    profile_clean = _strip_internal(profile)

    dup_keys = agent_rules.get("primary_keys") or auto_keys(profile)
    dups = C.detect_duplicates(header, rows, dup_keys) if dup_keys else {}
    bounds = _default_bounds_from_rules(agent_rules)
    outl = C.outlier_flags(header, rows, bounds)
    conversions = agent_rules.get("unit_conversions", []) or []

    report = _report_summary(profile, dups, outl, conversions, dup_keys)

    cleaned_header, cleaned_rows = build_cleaned(header, rows, dups, outl, agent_rules)

    data_profile = {
        "case_id": case["case_id"],
        "source": {
            "path": os.path.basename(main_path), "sha256": t.get("sha256"),
            "encoding": t.get("encoding"), "delimiter": t.get("delimiter"),
            "has_header": t.get("has_header"), "n_rows": t.get("n_rows"),
            "n_cols": t.get("n_cols"),
        },
        "columns": profile_clean["columns"],
        "report": report,
    }

    snapshot = {
        "case_id": case["case_id"],
        "files": [{"path": os.path.relpath(f, input_dir) if os.path.isabs(f) else f,
                   "sha256": C.sha256_file(f) if os.path.isfile(f) else None,
                   "size": os.path.getsize(f) if os.path.isfile(f) else None,
                   "role": ("case" if f == case["manifest"].get("case_json")
                            else "task" if f == case["manifest"].get("task_md")
                            else "dictionary" if f in case["manifest"].get("dictionary", [])
                            else "rules" if f in case["manifest"].get("rules", [])
                            else "workorder" if f in case["manifest"].get("workorder", [])
                            else "data")}
                  for f in case["manifest"]["all"]],
    }

    assertions = build_assertions(t, profile, dups, outl, agent_rules, artifacts, dup_keys)
    evidence_map = C.build_evidence_map(case["case_id"], assertions)
    quality_report = _render_quality_report(profile, dups, outl, conversions, report, t)

    C.atomic_write_csv(os.path.join(output_dir, artifacts["cleaned_data"]), cleaned_header, cleaned_rows)
    C.atomic_write_json(os.path.join(output_dir, artifacts["data_profile"]), data_profile)
    C.atomic_write_text(os.path.join(output_dir, artifacts["quality_report"]), quality_report)
    C.atomic_write_json(os.path.join(output_dir, artifacts["evidence_map"]), evidence_map)
    C.atomic_write_json(os.path.join(output_dir, artifacts["source_snapshot"]), snapshot)

    # sources: one verbatim quote per material head (first non-empty line).
    sources = []
    for f in [case["manifest"].get("case_json"), case["manifest"].get("task_md"),
              main_path] + case["manifest"].get("dictionary", []) + case["manifest"].get("rules", []) + case["manifest"].get("workorder", []):
        if not f:
            continue
        info = C.read_text_robust(f)
        first = next((ln.strip() for ln in info["text"].splitlines() if ln.strip()), "")
        sources.append({
            "locator": "%s::line1" % os.path.basename(f),
            "quote": first[:200],
            "note": "材料首行逐字摘录（用于来源定位）",
        })

    result["status"] = C.STATUS_OK
    result["sources"] = sources
    result["change_log"].append({"time": C.now_iso(), "step": "full",
                                 "action": "生成清洗表、数据画像、质量报告、证据映射与原始快照"})
    result["pending_human"] = list(agent_rules.get("pending_human", []) or [])
    if not dup_keys:
        result["pending_human"].append("未确定真实主键；近似去重仅按 Agent 提供的 primary_keys 执行，否则留空待确认")
    result["runtime"]["finished_at"] = C.now_iso()
    result["runtime"]["seconds"] = round(time.time() - _start_ts, 2)
    result["_meta"]["bootstrap"] = False
    C.fill_contract_keys(result, case["output_contract"], artifacts)
    C.atomic_write_json(os.path.join(output_dir, "result.json"), result)
    _log("full done: %d rows x %d cols; %d assertions" % (len(rows), len(header), len(assertions)))
    return result


def _resolve_material(output_dir, input_dir, material):
    if not material:
        return None
    candidates = [os.path.join(output_dir, material)]
    if input_dir:
        candidates.append(os.path.join(input_dir, material))
    for c in candidates:
        if os.path.isfile(c):
            return c
    # recursive basename search under input_dir
    if input_dir and os.path.isdir(input_dir):
        base = os.path.basename(material)
        for full in C.list_files(input_dir):
            if os.path.basename(full) == base:
                return full
    return None


def run_selfcheck(output_dir, work_dir, input_dir="/input"):
    result_path = os.path.join(output_dir, "result.json")
    report = {"phase": "selfcheck", "ok": False, "issues": [], "checks": {}}
    if not os.path.isfile(result_path):
        report["issues"].append("result.json not found")
        C.atomic_write_json(os.path.join(output_dir, "selfcheck.json"), report)
        print("SELFCHECK FAIL: result.json not found")
        return report

    info = C.read_text_robust(result_path)
    result, status = C.parse_json_robust(info["text"], {})
    ok, issues = C.validate_result(result)
    report["issues"].extend(issues)
    input_dir = (result.get("_meta", {}) or {}).get("input_dir", input_dir)

    # 1) artifacts exist
    artifacts = result.get("artifacts", {}) or {}
    missing_arts = [v for v in artifacts.values() if v and not os.path.isfile(os.path.join(output_dir, v))]
    report["checks"]["artifacts"] = {"expected": list(artifacts.values()), "missing": missing_arts}
    if missing_arts:
        report["issues"].append("missing artifacts: %s" % ", ".join(missing_arts))

    # 2) quote verification against referenced materials
    evidence_map_path = os.path.join(output_dir, artifacts.get("evidence_map", "evidence_map.json"))
    quote_fail = []
    if os.path.isfile(evidence_map_path):
        em_info = C.read_text_robust(evidence_map_path)
        em, _ = C.parse_json_robust(em_info["text"], {})
        for a in (em.get("assertions", []) if isinstance(em, dict) else []):
            ev = a.get("evidence", {})
            quote = ev.get("quote")
            material = ev.get("material")
            text = ""
            mat_path = _resolve_material(output_dir, input_dir, material)
            if mat_path:
                text = C.read_text_robust(mat_path)["text"]
            if not C.verify_quote(text, quote):
                quote_fail.append(a.get("id"))
    report["checks"]["quote_verification"] = {"failing_assertions": quote_fail}
    if quote_fail:
        report["issues"].append("unverified quotes in evidence_map: %s" % ", ".join(quote_fail[:10]))

    # 3) recompute report numbers from cleaned_data.csv
    cleaned_path = os.path.join(output_dir, artifacts.get("cleaned_data", "cleaned_data.csv"))
    profile_path = os.path.join(output_dir, artifacts.get("data_profile", "data_profile.json"))
    recompute = {}
    if os.path.isfile(cleaned_path) and os.path.isfile(profile_path):
        ct = C.read_table_robust(cleaned_path)
        pinfo = C.read_text_robust(profile_path)
        prof, _ = C.parse_json_robust(pinfo["text"], {})
        stored_report = (prof or {}).get("report", {}) if isinstance(prof, dict) else {}
        if ct["header"]:
            orig_cols = [h for h in ct["header"] if not str(h).startswith(META_PREFIX)]
            idx_outlier = ct["header"].index("%soutlier_flag" % META_PREFIX) if "%soutlier_flag" % META_PREFIX in ct["header"] else None
            idx_dup = ct["header"].index("%sdup_group" % META_PREFIX) if "%sdup_group" % META_PREFIX in ct["header"] else None
            tallies = {k: 0 for k in C.MISSING_KINDS}
            for row in ct["rows"]:
                for ci, h in enumerate(ct["header"]):
                    if h in orig_cols:
                        k = C.classify_value(_cell(row, ci))
                        if k in tallies:
                            tallies[k] += 1
            dup_groups = set()
            n_dup_rows = 0
            for row in ct["rows"]:
                g = str(_cell(row, idx_dup)).strip() if idx_dup is not None else ""
                if g:
                    dup_groups.add(g)
                    n_dup_rows += 1
            n_outlier_flags = sum(
                1 for r in ct["rows"]
                if idx_outlier is not None and str(_cell(r, idx_outlier)).strip() != "")
            recompute = {
                "n_rows": ct["n_rows"],
                "n_cols": len(orig_cols),
                "missing_tallies": tallies,
                "n_outlier_flags": n_outlier_flags,
                "n_duplicate_rows": n_dup_rows,
                "n_duplicate_groups": len(dup_groups),
            }
            for key in ("n_rows", "n_cols", "missing_tallies", "n_outlier_flags",
                        "n_duplicate_rows", "n_duplicate_groups"):
                if key in stored_report and stored_report[key] != recompute.get(key):
                    report["issues"].append("report mismatch on %s: stored=%r recomputed=%r" % (
                        key, stored_report[key], recompute.get(key)))
    report["checks"]["recompute"] = recompute

    report["ok"] = len(report["issues"]) == 0
    C.atomic_write_json(os.path.join(output_dir, "selfcheck.json"), report)
    print("SELFCHECK %s (%d issue(s))" % ("PASS" if report["ok"] else "FAIL", len(report["issues"])))
    for issue in report["issues"]:
        print("  - %s" % issue)
    return report


def main(argv=None):
    global _start_ts
    _start_ts = time.time()
    parser = argparse.ArgumentParser(description="sci-data-cleaning CLI")
    parser.add_argument("--phase", choices=("bootstrap", "full", "selfcheck"), default="full")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = C.now_iso()
    if args.phase == "bootstrap":
        run_bootstrap(args.input, args.output, started)
    elif args.phase == "full":
        run_full(args.input, args.output, args.work, started)
    elif args.phase == "selfcheck":
        run_selfcheck(args.output, args.work, args.input)
    return 0


if __name__ == "__main__":
    sys.exit(main())
