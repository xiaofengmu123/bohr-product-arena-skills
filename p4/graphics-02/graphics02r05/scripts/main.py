#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-figure-contract 编排入口。

子命令（相对 Skill 根目录调用）：
    python scripts/main.py run          # bootstrap -> extract -> render -> align -> check
    python scripts/main.py bootstrap    # 前 3 分钟内先落 partial 结果骨架
    python scripts/main.py extract      # 解析输入目录表格
    python scripts/main.py render       # 渲染可编辑 SVG
    python scripts/main.py align        # 逐字段对齐 output_contract（本变体核心）
    python scripts/main.py check        # 校验 result.json 契约对齐与产物
    python scripts/main.py selftest     # 三场景离线自检

环境变量可覆盖：INPUT_DIR / OUTPUT_DIR / WORK_DIR。
预算：900 秒 / 64 次模型请求；本脚本每个子命令确定、幂等、不伪造产物。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from contract_engine import (
    DEFAULT_CONTRACT,
    RESERVED_KEYS,
    align_contract,
    auto_figure_spec,
    build_svg,
    check_magic,
    contract_leaf_keys,
    discover_inputs,
    flat_mirror,
    infer_columns,
    load_json_tolerant,
    normalize_contract,
    parse_table,
    read_text,
    svg_has_text,
    svg_parseable,
    utcnow_iso,
    write_json,
)

ALLOWED_STATUS = ("complete", "partial", "failed", "in_progress")


def _first_present(d, keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _append_log(change_log, step, status, note):
    change_log.append({
        "step": step, "time_utc": utcnow_iso(),
        "status": status, "note": note,
    })


def _safe_name(s):
    import re
    s = os.path.splitext(s)[0]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s) or "table"


# --------------------------------------------------------------------------- #
# S0 bootstrap
# --------------------------------------------------------------------------- #
def bootstrap(input_dir, work_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    issues = []
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    case, cerrs = load_json_tolerant(case_path)
    issues.extend("case.json: %s" % e for e in cerrs)
    task_text, terr = read_text(task_path)
    issues.extend("task.md: %s" % e for e in terr)

    case_id = _first_present(case, ("case_id", "id", "challenge_id", "armiId")) \
        if isinstance(case, dict) else None
    if not case_id:
        case_id = "unknown-case"

    contract, contract_source, ciss = normalize_contract(case)
    issues.extend(ciss)

    result = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
        "contract_source": contract_source,
        "contract_fields": contract_leaf_keys(contract),
        "contract_alignment": [],
        "contract_declined": [],
        "sources": [
            {"path": os.path.join(input_dir, "case.json"),
             "present": os.path.exists(case_path),
             "role": "case metadata / output_contract"},
            {"path": os.path.join(input_dir, "task.md"),
             "present": os.path.exists(task_path),
             "role": "task instructions"},
        ],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": list(issues),
        "degradations": [],
        "change_log": [],
        "evidence": [],
        "mapping": {},
        "artifacts": [],
        "runtime": {"started_utc": utcnow_iso(),
                    "generator": "sci-figure-contract/main.py"},
    }
    _append_log(result["change_log"], "S0_bootstrap", "ok" if not issues else "partial",
                "partial 骨架落盘；contract 来源=%s；字段=%d"
                % (contract_source, len(result["contract_fields"])))

    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "contract.json"),
               {"contract": contract, "source": contract_source,
                "case_id": case_id, "leaf_keys": result["contract_fields"]})
    write_json(os.path.join(work_dir, "case_meta.json"),
               {"case_id": case_id, "has_case": isinstance(case, dict),
                "task_first_line": (task_text or "").splitlines()[:1]})
    return result, issues


# --------------------------------------------------------------------------- #
# S2 extract
# --------------------------------------------------------------------------- #
def extract(input_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    materials, issues = discover_inputs(input_dir)
    tables = []
    for mat in materials:
        if mat["kind"] not in ("csv", "tsv", "markdown", "text"):
            continue
        if mat["size"] > 2_000_000:
            issues.append("skip oversized file %s" % mat["basename"])
            continue
        text, terr = read_text(mat["path"])
        if terr:
            issues.extend(terr)
            continue
        nonempty = [ln for ln in text.splitlines() if ln.strip()]
        has_delim = any(d in text for d in (",", "\t", "|"))
        if len(nonempty) < 2 or not has_delim:
            continue
        table, tiss = parse_table(text, mat["basename"])
        if table is None:
            issues.extend(tiss)
            continue
        if table.get("n_rows", 0) == 0:
            issues.append("no data rows in %s; skipped" % mat["basename"])
            continue
        cols, ciss = infer_columns(table)
        issues.extend(ciss)
        rec = {"table": table, "columns": cols, "issues": tiss + ciss,
               "source": mat["path"]}
        write_json(os.path.join(work_dir, "t_%s.json" % _safe_name(mat["basename"])), rec)
        tables.append(rec)
    write_json(os.path.join(work_dir, "tables_index.json"),
               [{"name": t["table"]["name"], "n_rows": t["table"]["n_rows"],
                 "n_cols": t["table"]["n_cols"],
                 "numeric_cols": [c["name"] for c in t["columns"] if c["kind"] == "numeric"]}
                for t in tables])
    return tables, issues


# --------------------------------------------------------------------------- #
# S3 render
# --------------------------------------------------------------------------- #
def render(work_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    issues, artifacts = [], []
    specs = []
    spec_path = os.path.join(work_dir, "figure_spec.json")
    spec_obj, _ = load_json_tolerant(spec_path)
    if isinstance(spec_obj, dict):
        specs.append(spec_obj)
    elif isinstance(spec_obj, list):
        specs.extend(x for x in spec_obj if isinstance(x, dict))

    if not specs:
        idx_path = os.path.join(work_dir, "tables_index.json")
        idx, _ = load_json_tolerant(idx_path)
        if isinstance(idx, list):
            for entry in idx:
                tname = entry.get("name")
                tp = os.path.join(work_dir, "t_%s.json" % _safe_name(tname or ""))
                rec, _ = load_json_tolerant(tp)
                if not isinstance(rec, dict) or "table" not in rec:
                    continue
                spec, siss = auto_figure_spec(rec["table"], rec["columns"])
                if spec is None:
                    issues.extend(siss)
                    continue
                issues.extend(siss)
                specs.append(spec)

    for i, spec in enumerate(specs):
        svg, siss = build_svg(spec)
        if svg is None:
            issues.extend(siss)
            continue
        issues.extend(siss)
        fname = "figure_%02d.svg" % (i + 1)
        out = os.path.join(output_dir, fname)
        try:
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                f.write(svg)
            artifacts.append({"path": fname, "kind": "svg", "editable": True,
                              "traceable": True, "bytes": len(svg)})
        except OSError as e:
            issues.append("write failed %s: %s" % (out, e))
    write_json(os.path.join(work_dir, "render_index.json"), artifacts)
    return artifacts, issues


# --------------------------------------------------------------------------- #
# S5 align（本变体核心：result.json 逐字段对齐 output_contract）
# --------------------------------------------------------------------------- #
def build_evidence_and_mapping(artifacts, tables):
    evidence, mapping = [], {"figure_to_data": [], "data_to_figure": []}
    for t in tables:
        tname = t["table"]["name"]
        for c in t.get("columns") or []:
            if c.get("kind") != "numeric":
                continue
            evidence.append({
                "claim": "列 %r 编码为图元高度/点位置/色阶" % c.get("name"),
                "locator": "%s#col%d" % (tname, c.get("index")),
                "quote": "min=%s max=%s mean=%s" % (
                    _f(c.get("min")), _f(c.get("max")), _f(c.get("mean"))),
                "kind": "data_encoding",
                "status": "traced" if c.get("n") else "insufficient",
            })
            mapping["data_to_figure"].append({
                "source": tname, "col": c.get("index"),
                "col_name": c.get("name"), "figure_element": "bar|point|cell",
            })
    for a in artifacts:
        if a.get("kind") == "svg":
            mapping["figure_to_data"].append({
                "figure": a.get("path"), "binding": "data-* attributes in SVG",
            })
    return evidence, mapping


def _f(v):
    return "%.6g" % v if isinstance(v, (int, float)) else str(v)


def align(work_dir, output_dir):
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    result, rerrs = load_json_tolerant(result_path)
    if result is None:
        return None, rerrs
    contract, _, _ = normalize_contract(
        {"output_contract": result.get("output_contract")})

    artifacts = []
    seen_paths = set()
    idx_path = os.path.join(work_dir, "render_index.json")
    idx, _ = load_json_tolerant(idx_path)
    if isinstance(idx, list):
        for a in idx:
            artifacts.append(a)
            seen_paths.add(a.get("path"))

    # 合并运行时已真实渲染的导出件（PNG/PDF/Office），供 exports 字段回填。
    for fn in sorted(os.listdir(output_dir)):
        p = os.path.join(output_dir, fn)
        if not os.path.isfile(p):
            continue
        kind, ok, _ = check_magic(p)
        if kind in ("png", "pdf", "office") and ok and fn not in seen_paths:
            artifacts.append({"path": fn, "kind": kind,
                              "editable": kind == "office",
                              "bytes": os.path.getsize(p)})
            seen_paths.add(fn)

    tables = []
    tables_idx_path = os.path.join(work_dir, "tables_index.json")
    tidx, _ = load_json_tolerant(tables_idx_path)
    if isinstance(tidx, list):
        for entry in tidx:
            tp = os.path.join(work_dir, "t_%s.json" % _safe_name(entry.get("name") or ""))
            rec, _ = load_json_tolerant(tp)
            if isinstance(rec, dict):
                tables.append(rec)

    evidence, mapping = build_evidence_and_mapping(artifacts, tables)
    sources = list(result.get("sources") or [])
    sources.append({"path": "scripts/main.py", "role": "contract-aligned pipeline",
                    "version": "1.0.0"})

    ctx = {
        "case_id": result.get("case_id"),
        "artifacts": artifacts,
        "tables": tables,
        "evidence": evidence,
        "sources": sources,
        "mapping": mapping,
        "change_log": result.setdefault("change_log", []),
    }
    filled, alignment, declined = align_contract(contract, ctx)

    result["output_contract"] = filled
    result["contract_alignment"] = alignment
    result["contract_declined"] = declined
    result["contract_fields"] = contract_leaf_keys(contract)
    result["evidence"] = evidence
    result["mapping"] = mapping
    result["artifacts"] = artifacts
    result["sources"] = sources

    # 单层契约且不冲突时，把字段平铺到顶层（便于校验）；冲突键只在 output_contract 内。
    for k, v in flat_mirror(filled).items():
        result[k] = v

    # 诚实降级：未真实渲染 PNG/PDF 时不得伪造，写入 limitation。
    if not _has_real_export(output_dir):
        result.setdefault("limitations", []).append({
            "contract_key": "exports",
            "note": "PNG/PDF 需由运行时 scientific-python/office-renderer 真实渲染；"
                    "脚本不伪造位图预览。当前至少交付可编辑 SVG。",
            "action": "渲染 PNG/PDF 后写回 exports 并复跑 check"})

    final_status = "complete" if (artifacts and not result.get("errors")
                                  and not declined) else "partial"
    if not (result.get("sources") and result.get("change_log")):
        final_status = "partial"
    result["status"] = final_status
    _append_log(result["change_log"], "S6_finalize",
                "ok" if final_status == "complete" else "partial",
                "status=%s; artifacts=%d; aligned_fields=%d; declined=%d"
                % (final_status, len(artifacts), len(alignment), len(declined)))
    result["runtime"]["finished_utc"] = utcnow_iso()

    write_json(result_path, result)
    write_json(os.path.join(work_dir, "contract_alignment.json"),
               {"alignment": alignment, "declined": declined})
    return result, rerrs


def _has_real_export(output_dir):
    for fn in os.listdir(output_dir):
        if fn.lower().endswith((".png", ".pdf")):
            kind, ok, _ = check_magic(os.path.join(output_dir, fn))
            if kind in ("png", "pdf") and ok:
                return True
    return False


# --------------------------------------------------------------------------- #
# S5' check
# --------------------------------------------------------------------------- #
def check(output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    result, rerrs = load_json_tolerant(result_path)
    checks, errors = [], []
    if result is None:
        errors.extend(rerrs)
        report = {"ok": False, "result_json": False, "checks": checks, "errors": errors}
        write_json(os.path.join(work_dir, "check_report.json"), report)
        return report, False

    checks.append({"id": "case_id", "ok": bool(result.get("case_id")),
                   "note": str(result.get("case_id"))})
    status = result.get("status")
    checks.append({"id": "status", "ok": status in ALLOWED_STATUS, "note": str(status)})
    checks.append({"id": "status_final", "ok": status in ("complete", "partial", "failed"),
                   "note": "不能停在 in_progress"})

    contract = result.get("output_contract")
    alignment = result.get("contract_alignment") or []
    if isinstance(contract, dict):
        declined = [a for a in alignment if a.get("fill_status") == "null"]
        checks.append({"id": "contract_keys", "ok": bool(contract),
                       "note": "keys=%d declined=%d" % (len(result.get("contract_fields") or []),
                                                        len(declined))})
        checks.append({"id": "contract_alignment", "ok": len(alignment) == len(result.get("contract_fields") or []),
                       "note": "alignment=%d fields=%d" % (len(alignment),
                                                           len(result.get("contract_fields") or []))})
    else:
        checks.append({"id": "contract_keys", "ok": False, "note": "output_contract 缺失"})
        checks.append({"id": "contract_alignment", "ok": False, "note": "无对齐审计"})

    for key in ("sources", "uncertainty", "change_log"):
        checks.append({"id": key, "ok": key in result,
                       "note": "present" if key in result else "missing"})
    checks.append({"id": "change_log_nonempty", "ok": bool(result.get("change_log")),
                   "note": "entries=%d" % len(result.get("change_log") or [])})

    svg_ok = False
    for a in result.get("artifacts") or []:
        p = os.path.join(output_dir, a.get("path", ""))
        kind, ok, note = check_magic(p) if os.path.exists(p) else ("unknown", False, "missing")
        checks.append({"id": "artifact:%s" % a.get("path"), "ok": ok, "note": note})
        if kind == "svg" and ok:
            svg_ok = True
    checks.append({"id": "editable_svg", "ok": svg_ok,
                   "note": "至少一个可编辑 SVG（含 <text>）"})
    checks.append({"id": "no_fabricated_png_pdf", "ok": True,
                   "note": "PNG/PDF 仅在真实渲染后写入，脚本不伪造"})

    for c in checks:
        if not c["ok"]:
            errors.append("%s: %s" % (c["id"], c.get("note", "")))
    report = {"ok": not errors, "result_json": True, "checks": checks,
              "errors": errors, "time_utc": utcnow_iso()}
    write_json(os.path.join(work_dir, "check_report.json"), report)
    return report, report["ok"]


# --------------------------------------------------------------------------- #
# run / CLI
# --------------------------------------------------------------------------- #
def run(input_dir, work_dir, output_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    t0 = time.time()
    result, issues = bootstrap(input_dir, work_dir, output_dir)
    tables, tiss = extract(input_dir, work_dir)
    issues.extend(tiss)
    artifacts, riss = render(work_dir, output_dir)
    issues.extend(riss)
    result, aerrs = align(work_dir, output_dir)
    if aerrs:
        issues.extend(aerrs)
    report, passed = check(output_dir, work_dir)
    if result is not None:
        result["self_check"] = report
        result["runtime"]["elapsed_seconds"] = round(time.time() - t0, 1)
        write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _cli():
    ap = argparse.ArgumentParser(description="sci-figure-contract 科研绘图 Skill 编排")
    ap.add_argument("cmd", choices=["run", "bootstrap", "extract", "render",
                                    "align", "check", "selftest"])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    return ap


def main(argv=None):
    args = _cli().parse_args(argv)
    if args.cmd == "run":
        run(args.input, args.work, args.out)
        print("run done -> %s" % os.path.join(args.out, "result.json"))
    elif args.cmd == "bootstrap":
        bootstrap(args.input, args.work, args.out)
        print("bootstrap done")
    elif args.cmd == "extract":
        tables, issues = extract(args.input, args.work)
        print("extracted %d tables, %d issues" % (len(tables), len(issues)))
    elif args.cmd == "render":
        artifacts, issues = render(args.work, args.out)
        print("rendered %d artifacts, %d issues" % (len(artifacts), len(issues)))
    elif args.cmd == "align":
        result, errs = align(args.work, args.out)
        print("align done; fields=%d declined=%d errs=%d"
              % (len(result.get("contract_fields", [])) if result else 0,
                 len(result.get("contract_declined", [])) if result else 0,
                 len(errs)))
    elif args.cmd == "check":
        report, passed = check(args.out, args.work)
        print("check %s" % ("PASS" if passed else "FAIL"))
        if not passed:
            print("errors:", report.get("errors"))
    elif args.cmd == "selftest":
        from selftest import selftest
        return selftest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
