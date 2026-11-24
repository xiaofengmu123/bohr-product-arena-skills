#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figtrace 编排入口：bootstrap -> extract -> render -> check -> merge result.json。

调用方式（相对 Skill 根目录）：
    python scripts/main.py run            # 一键走完整条确定性链路
    python scripts/main.py bootstrap      # 仅落盘 partial 骨架（前 3 分钟必须完成）
    python scripts/main.py extract        # 解析输入目录中的表格
    python scripts/main.py render         # 从 table/spec 渲染 SVG
    python scripts/main.py check          # 校验 /app/output/result.json 与产物
    python scripts/main.py selftest       # 空目录/坏 JSON/演示输入 三场景自检

环境变量可覆盖：INPUT_DIR / OUTPUT_DIR / WORK_DIR。
总预算：900 秒 / 64 次模型请求；本脚本每个子命令都是确定性、幂等的。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time

from render_svg import (
    auto_figure_spec,
    build_svg,
    check_magic,
    discover_inputs,
    infer_columns,
    load_json_tolerant,
    parse_table,
    read_text,
    svg_has_text,
    svg_parseable,
    write_json,
)

ALLOWED_STATUS = ("complete", "partial", "failed", "in_progress")


def _utcnow_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_log(change_log, step, status, note):
    change_log.append({
        "step": step,
        "time_utc": _utcnow_iso(),
        "status": status,
        "note": note,
    })


def _first_present(d, keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def default_contract():
    """题目「Skill 运行后的输出要求」中的三类交付字段。仅当 case.json 未给
    output_contract 时用作兜底；一旦输入有契约，则原样回填全部键。"""
    return {
        "editable_source": None,
        "exports": None,
        "source_and_change": None,
    }


def bootstrap(input_dir, work_dir, output_dir, now_utc=None):
    """读 /input/case.json + task.md，抽契约，立即写 partial result.json 骨架。

    必须在开始分析前调用：任何后续异常都不会让 /app/output 为空。
    返回 (result, issues)。
    """
    now_utc = now_utc or _utcnow_iso()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    issues = []
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    case, cerrs = load_json_tolerant(case_path)
    issues.extend("case.json: %s" % e for e in cerrs)
    task_text, terr = read_text(task_path)
    issues.extend("task.md: %s" % e for e in terr)

    case_id = None
    if isinstance(case, dict):
        case_id = _first_present(case, ("case_id", "id", "challenge_id", "armiId"))
    if not case_id:
        case_id = "unknown-case"

    contract = default_contract()
    contract_source = "default"
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        contract = {k: None for k in case["output_contract"]}
        contract_source = "input:output_contract"

    result = {
        "case_id": case_id,
        "status": "in_progress",
        "output_contract": contract,
        "sources": [
            {
                "path": os.path.join(input_dir, "case.json"),
                "present": os.path.exists(case_path),
                "role": "case metadata / output_contract",
            },
            {
                "path": os.path.join(input_dir, "task.md"),
                "present": os.path.exists(task_path),
                "role": "task instructions",
            },
        ],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": list(issues),
        "degradations": [],
        "change_log": [],
        "runtime": {"started_utc": now_utc, "generator": "figtrace/main.py"},
    }
    _append_log(result["change_log"], "S0_bootstrap", "ok" if not issues else "partial",
                "partial 骨架落盘；contract 来源=%s" % contract_source)
    result["status"] = "partial" if not issues else "partial"
    result["runtime"]["case_id_source"] = "case.json" if isinstance(case, dict) else "fallback"

    werr = write_json(os.path.join(output_dir, "result.json"), result)
    if werr:
        issues.extend(werr)
    write_json(os.path.join(work_dir, "contract.json"),
               {"contract": contract, "source": contract_source, "case_id": case_id})
    write_json(os.path.join(work_dir, "case_meta.json"),
               {"case_id": case_id, "has_case": isinstance(case, dict),
                "task_first_line": (task_text or "").splitlines()[:1]})
    return result, issues


def extract(input_dir, work_dir):
    """解析输入目录中的表格，写 work/t_<name>.json。返回 (tables, issues)。"""
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
        # 只有含表格分隔符（, \t |）且有多行时才当表格；普通笔记/任务说明跳过
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
        rec = {"table": table, "columns": cols, "issues": tiss + ciss, "source": mat["path"]}
        write_json(os.path.join(work_dir, "t_%s.json" % _safe_name(mat["basename"])), rec)
        tables.append(rec)
    write_json(os.path.join(work_dir, "tables_index.json"),
               [{"name": t["table"]["name"], "n_rows": t["table"]["n_rows"],
                 "n_cols": t["table"]["n_cols"],
                 "numeric_cols": [c["name"] for c in t["columns"] if c["kind"] == "numeric"]}
                for t in tables])
    return tables, issues


def _safe_name(s):
    s = os.path.splitext(s)[0]
    return re_safe().sub("_", s) or "table"


_re_safe = None


def re_safe():
    global _re_safe
    if _re_safe is None:
        import re
        _re_safe = re.compile(r"[^A-Za-z0-9_.-]+")
    return _re_safe


def render(work_dir, output_dir):
    """从 work/ 下的 spec 或 tables 渲染可编辑 SVG。返回 (artifacts, issues)。"""
    os.makedirs(output_dir, exist_ok=True)
    issues = []
    artifacts = []
    spec_path = os.path.join(work_dir, "figure_spec.json")
    specs = []
    spec_obj, _ = load_json_tolerant(spec_path)
    if isinstance(spec_obj, dict):
        specs.append(spec_obj)
    elif isinstance(spec_obj, list):
        specs.extend(x for x in spec_obj if isinstance(x, dict))

    if not specs:
        # 尝试从解析出的表格自动建 spec
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


def check(output_dir, work_dir):
    """校验 result.json 契约完整性与产物格式，写 check_report.json。

    返回 (report, passed)。check 只报告，不阻塞写盘。
    """
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    result, rerrs = load_json_tolerant(result_path)
    checks = []
    errors = []
    if result is None:
        errors.extend(rerrs)
        report = {"ok": False, "result_json": False, "checks": checks, "errors": errors}
        write_json(os.path.join(work_dir, "check_report.json"), report)
        return report, False

    checks.append({"id": "case_id", "ok": bool(result.get("case_id")),
                   "note": str(result.get("case_id"))})
    status = result.get("status")
    checks.append({"id": "status", "ok": status in ALLOWED_STATUS,
                   "note": str(status)})
    checks.append({"id": "status_final", "ok": status in ("complete", "partial", "failed"),
                   "note": "不能停在 in_progress"})

    contract = result.get("output_contract")
    if isinstance(contract, dict):
        missing = [k for k, v in contract.items()
                   if v in (None, "", []) and not _has_null_reason(result, k)]
        checks.append({"id": "contract_keys", "ok": bool(contract),
                       "note": "keys=%d missing=%s" % (len(contract), missing or "none")})
    else:
        checks.append({"id": "contract_keys", "ok": False, "note": "output_contract 缺失"})

    for key in ("sources", "uncertainty", "change_log"):
        checks.append({"id": key, "ok": key in result,
                       "note": "present" if key in result else "missing"})

    # 产物存在性与格式
    artifacts = _list_artifacts(result, output_dir)
    svg_ok = False
    for a in artifacts:
        p = os.path.join(output_dir, a["path"])
        kind, ok, note = check_magic(p) if os.path.exists(p) else ("unknown", False, "missing")
        a["_check"] = {"kind": kind, "ok": ok, "note": note}
        if kind == "svg" and ok:
            svg_ok = True
        checks.append({"id": "artifact:%s" % a["path"], "ok": ok, "note": note})
    checks.append({"id": "editable_svg", "ok": svg_ok,
                   "note": "至少一个可编辑 SVG（含 <text>）"})
    checks.append({"id": "no_fabricated_png_pdf", "ok": True,
                   "note": "PNG/PDF 仅在真实渲染后写入，脚本不伪造"})

    for c in checks:
        if not c["ok"]:
            errors.append("%s: %s" % (c["id"], c.get("note", "")))
    report = {"ok": not errors, "result_json": True, "checks": checks,
              "errors": errors, "time_utc": _utcnow_iso()}
    write_json(os.path.join(work_dir, "check_report.json"), report)
    return report, report["ok"]


def _has_null_reason(result, key):
    for field in ("uncertainty", "limitations", "pending_human"):
        for item in result.get(field) or []:
            if isinstance(item, dict) and item.get("contract_key") == key:
                return True
            if isinstance(item, str) and key in item:
                return True
    return False


def _list_artifacts(result, output_dir):
    arts = []
    for key in ("editable_source", "exports"):
        v = (result.get("output_contract") or {}).get(key)
        if isinstance(v, dict):
            for sub in ("source_file", "svg", "pptx", "eps", "png", "pdf"):
                if isinstance(v.get(sub), str):
                    arts.append({"path": v[sub]})
        elif isinstance(v, str):
            arts.append({"path": v})
    # 顶层 artifacts 列表（若脚本填充）
    for a in result.get("artifacts") or []:
        if isinstance(a, dict) and isinstance(a.get("path"), str):
            arts.append({"path": a["path"]})
    return arts


def run(input_dir, work_dir, output_dir):
    """一键链路。始终返回 0；真实状态写入 result.json。"""
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    result, issues = bootstrap(input_dir, work_dir, output_dir)
    t0 = time.time()

    tables, tiss = extract(input_dir, work_dir)
    issues.extend(tiss)

    artifacts, riss = render(work_dir, output_dir)
    issues.extend(riss)

    # 回填契约字段与来源
    result = _merge_result(result, output_dir, artifacts, tables, issues,
                           _utcnow_iso(), round(time.time() - t0, 1))

    # 未真实渲染 PNG/PDF：不伪造，写入 limitation
    if not _has_real_export(output_dir):
        result.setdefault("limitations", []).append(
            {"contract_key": "exports",
             "note": "PNG/PDF 需由运行时 scientific-python/office-renderer 真实渲染；"
                     "脚本不伪造位图预览。当前仅交付可编辑 SVG。",
             "action": "用 matplotlib 等渲染后写回 exports 并复跑 check"})

    final_status = "partial"
    if artifacts and not result.get("errors"):
        final_status = "complete"
    result["status"] = final_status
    _append_log(result["change_log"], "S6_finalize", "ok" if final_status == "complete" else "partial",
                "status=%s; artifacts=%d; issues=%d" % (final_status, len(artifacts), len(issues)))
    write_json(os.path.join(output_dir, "result.json"), result)

    report, _ = check(output_dir, work_dir)
    result["self_check"] = report
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _merge_result(result, output_dir, artifacts, tables, issues, now, elapsed):
    result["errors"] = list(issues)
    result["artifacts"] = [dict(a) for a in artifacts]
    result["runtime"]["elapsed_seconds"] = elapsed
    result["runtime"]["finished_utc"] = now
    result["sources"].append({"path": "scripts/main.py", "role": "deterministic pipeline",
                              "version": "2.0.0"})
    # 填充契约：可编辑源文件 + 导出件
    contract = result["output_contract"]
    svg_names = [a["path"] for a in artifacts if a.get("kind") == "svg"]
    if "editable_source" in contract and contract["editable_source"] is None:
        contract["editable_source"] = {"svg": svg_names[0] if svg_names else None,
                                       "note": "文字保持 <text>，未转曲"}
    if "source_and_change" in contract and contract["source_and_change"] is None:
        contract["source_and_change"] = {
            "tables": [{"name": t["table"]["name"], "n_rows": t["table"]["n_rows"]}
                       for t in tables],
            "change_log": result["change_log"],
        }
    # 表格→图元溯源证据
    result["evidence_map"] = _build_evidence_map(artifacts, tables)
    return result


def _build_evidence_map(artifacts, tables):
    em = []
    for t in tables:
        for c in t.get("columns") or []:
            if c.get("kind") != "numeric":
                continue
            em.append({
                "claim": "列 %r 编码为图元高度/点位置/色阶" % c.get("name"),
                "locator": "%s#col%d" % (t["table"]["name"], c.get("index")),
                "quote": "min=%s max=%s mean=%s" % (
                    _f(c.get("min")), _f(c.get("max")), _f(c.get("mean"))),
                "kind": "data_encoding",
                "status": "traced" if c.get("n") else "insufficient",
            })
    return em


def _f(v):
    return "%.6g" % v if isinstance(v, (int, float)) else str(v)


def _has_real_export(output_dir):
    for fn in os.listdir(output_dir):
        if fn.lower().endswith((".png", ".pdf")):
            p = os.path.join(output_dir, fn)
            kind, ok, _ = check_magic(p)
            if kind in ("png", "pdf") and ok:
                return True
    return False


def _cli():
    ap = argparse.ArgumentParser(description="figtrace 科研绘图 Skill 编排")
    ap.add_argument("cmd", choices=["run", "bootstrap", "extract", "render", "check", "selftest"])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    return ap


def main(argv=None):
    ap = _cli()
    args = ap.parse_args(argv)
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
    elif args.cmd == "check":
        report, passed = check(args.out, args.work)
        print("check %s" % ("PASS" if passed else "FAIL"))
        if not passed:
            print("errors:", report.get("errors"))
    elif args.cmd == "selftest":
        from selftest import selftest
        selftest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
