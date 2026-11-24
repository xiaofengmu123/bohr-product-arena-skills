#!/usr/bin/env python3
"""统一入口：scaffold（落骨架）/ extract（解析表格）/ build（出图+证据链+渲染）。

只依赖 Python 标准库。调用示例（相对 Skill 根目录）：

    python scripts/main.py scaffold
    python scripts/main.py extract --input /input/data.csv --name fig2a --out /app/work/t_fig2a.json
    python scripts/main.py build --spec /app/work/figure_spec.json --out /app/output/figure.svg --name figure

环境变量可覆盖 INPUT_DIR / OUTPUT_DIR / WORK_DIR（默认 /input、/app/output、/app/work）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import (  # noqa: E402
    now_utc, load_json_safe, read_text_safe, scan_inputs, parse_table_file,
    render_spec, write_svg, render_svg, evidence_record,
)

VERSION = "2.0.0"


def _defaults():
    return {
        "input": os.environ.get("INPUT_DIR", "/input"),
        "output": os.environ.get("OUTPUT_DIR", "/app/output"),
        "work": os.environ.get("WORK_DIR", "/app/work"),
    }


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _load_result(path):
    obj, err = load_json_safe(path)
    return obj if isinstance(obj, dict) else {}, err


def _append_change(result, step, status, note):
    log = result.setdefault("change_log", [])
    log.append({"step": step, "time_utc": now_utc(), "status": status, "note": note})
    return result


def cmd_scaffold(args):
    d = _defaults()
    os.makedirs(d["output"], exist_ok=True)
    os.makedirs(d["work"], exist_ok=True)
    case_path = os.path.join(d["input"], "case.json")
    task_path = os.path.join(d["input"], "task.md")
    case, err = load_json_safe(case_path)
    task_text, task_err = read_text_safe(task_path)

    case_id = None
    contract = {}
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id")
        if isinstance(case.get("output_contract"), dict):
            contract = {k: (None if v is None else v)
                        for k, v in case["output_contract"].items()}
    # 无 case.json 时 case_id 为 None，仍产出合法骨架，不崩溃
    # 扫描材料
    scanned = scan_inputs(d["input"])
    materials = [s for s in scanned if not s.get("skipped")]
    skipped = [s["path"] for s in scanned if s.get("skipped")]

    # 契约字段：原样回填，缺值填 null，保留未知键
    contract_filled = {}
    for k, v in contract.items():
        contract_filled[k] = v

    result = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract_filled,
        "artifacts": [],
        "evidence_map": [],
        "sources": {
            "case_json": case_path if os.path.exists(case_path) else None,
            "task_md": task_path if os.path.exists(task_path) else None,
            "input_files": materials,
            "skipped_large_files": skipped,
            "parse": {
                "case_json_error": err,
                "task_md_error": task_err,
                "pdf_tool": "pdftotext (via core_impl.extract_pdf_text)",
            },
            "skill_version": VERSION,
        },
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
    }
    if err:
        result["uncertainty"].append("case.json 读取失败：%s" % err)
    if task_err:
        result["uncertainty"].append("task.md 读取失败：%s" % task_err)
    if not case_id:
        result["uncertainty"].append("case_id 缺失，待人工确认或后续补写")
    if contract_filled:
        nulls = [k for k, v in contract_filled.items() if v is None]
        if nulls:
            result["uncertainty"].append(
                "output_contract 有 %d/%d 个字段暂为 null（%s），随进度回填" %
                (len(nulls), len(contract_filled), ",".join(nulls)))
    else:
        result["uncertainty"].append("output_contract 未提供或为空，按实际情况回填")
    if task_text:
        # 仅存一份任务文本供后续引用，不当作结论来源
        with open(os.path.join(d["work"], "task_copy.md"), "w", encoding="utf-8") as f:
            f.write(task_text)

    _append_change(result, "S0 scaffold", "ok", "结果骨架落盘，后续只做增量改进")
    _write_json(os.path.join(d["output"], "result.json"), result)
    _write_json(os.path.join(d["work"], "contract.json"),
                {"case_id": case_id, "output_contract": contract_filled,
                 "input_scan": scanned, "written_at": now_utc()})
    print(json.dumps({"ok": True, "case_id": case_id,
                      "contract_fields": len(contract_filled),
                      "input_files": len(materials)}, ensure_ascii=False))
    return 0


def cmd_extract(args):
    d = _defaults()
    os.makedirs(args.out and os.path.dirname(args.out) or d["work"], exist_ok=True)
    out_path = args.out or os.path.join(d["work"], "t_%s.json" % args.name)
    if args.text:
        from core_impl import parse_table_text
        obj = parse_table_text(args.text, args.name)
    elif args.input:
        obj = parse_table_file(args.input, args.name)
    else:
        obj = {"name": args.name, "error": "需要 --input 或 --text", "rows": [],
               "columns": [], "issues": [], "mapping_hints": []}
    _write_json(out_path, obj)
    print(json.dumps({"ok": True, "out": out_path, "n_data_rows": obj.get("n_data_rows", 0),
                      "issues": obj.get("issues", [])}, ensure_ascii=False))
    return 0


def _derive_evidence(spec):
    """从 spec 的 series.evidence 生成与 SVG element_id 一致的证据链。"""
    records = []
    panels = spec.get("panels", [])
    for panel in panels:
        pid = panel.get("id", "")
        kind = panel.get("kind", "bar")
        for si, s in enumerate(panel.get("series", [])):
            evs = s.get("evidence") or []
            vals = s.get("values", []) or s.get("points", []) or []
            for i, v in enumerate(vals):
                ev = evs[i] if i < len(evs) and isinstance(evs[i], dict) else \
                    (s.get("evidence_default") or {})
                if not ev:
                    continue
                if kind == "bar":
                    etype, eid = "bar", "%s.bar.%d.%d" % (pid, si, i)
                elif kind == "line":
                    etype, eid = "point", "%s.point.%d.%d" % (pid, si, i)
                elif kind == "scatter":
                    etype, eid = "point", "%s.point.%d.%d" % (pid, si, i)
                elif kind == "box":
                    etype, eid = "box", "%s.box.%d.%d" % (pid, si, i)
                else:
                    continue
                value = ev.get("value")
                if value is None and isinstance(v, (int, float)):
                    value = v
                if value is None and isinstance(v, (list, tuple)) and v and \
                        all(isinstance(x, (int, float)) for x in v):
                    value = list(v)
                records.append(evidence_record(
                    element_id=eid, element_type=etype,
                    source_file=ev.get("source_file", s.get("source_file", "task.md")),
                    locator=ev.get("locator", ""),
                    quote=ev.get("quote", ""),
                    value=value,
                    unit=ev.get("unit", s.get("unit")),
                    mapping=ev.get("mapping", "%s = value" % kind),
                    confidence=ev.get("confidence", "exact"),
                    conflicts=ev.get("conflicts"),
                    note=ev.get("note")))
    # 额外记录（示意/机制图的 node/edge/annotation）
    for rec in spec.get("evidence_map_extra", []) or []:
        if isinstance(rec, dict) and rec.get("element_id") and rec.get("quote"):
            records.append(rec)
    return records


def cmd_build(args):
    d = _defaults()
    spec, err = load_json_safe(args.spec)
    if not isinstance(spec, dict):
        print(json.dumps({"ok": False, "error": err or "spec 不是对象"}, ensure_ascii=False))
        return 1
    os.makedirs(os.path.dirname(args.out) or d["output"], exist_ok=True)
    svg = render_spec(spec)
    write_svg(args.out, svg)

    evidence = _derive_evidence(spec)
    name = args.name or os.path.splitext(os.path.basename(args.out))[0]
    render = render_svg(args.out, os.path.dirname(args.out) or d["output"], name,
                        dpi=int(args.dpi or 300),
                        width=spec.get("width"), height=spec.get("height"))

    # 证据链落盘到 work
    ev_path = os.path.join(d["work"], "evidence_%s.json" % name)
    _write_json(ev_path, {"evidence_map": evidence})

    # 更新 result.json
    result_path = args.result or os.path.join(d["output"], "result.json")
    result, _ = _load_result(result_path)
    artifacts = result.setdefault("artifacts", [])
    rel_svg = os.path.relpath(args.out, d["output"]).replace(os.sep, "/")
    if rel_svg not in artifacts:
        artifacts.append(rel_svg)
    rel_png = rel_pdf = None
    if render.get("png"):
        rel_png = os.path.relpath(os.path.join(os.path.dirname(args.out), render["png"]), d["output"]).replace(os.sep, "/")
        if rel_png not in artifacts:
            artifacts.append(rel_png)
    if render.get("pdf"):
        rel_pdf = os.path.relpath(os.path.join(os.path.dirname(args.out), render["pdf"]), d["output"]).replace(os.sep, "/")
        if rel_pdf not in artifacts:
            artifacts.append(rel_pdf)
    # 自动回填 output_contract 中仍为 null 的字段（按关键字匹配产物，Agent 可在 S6 覆盖）
    contract = result.setdefault("output_contract", {})
    if isinstance(contract, dict):
        for k, v in list(contract.items()):
            if v is not None:
                continue
            kl = k.lower()
            if ("svg" in kl or "editable" in kl or "source" in kl or "vector" in kl) and rel_svg:
                contract[k] = rel_svg
            elif "pdf" in kl and rel_pdf:
                contract[k] = rel_pdf
            elif ("png" in kl or "raster" in kl or "export" in kl) and rel_png:
                contract[k] = rel_png
        # 刷新“暂为 null”的 uncertainty 摘要，避免陈旧
        nulls = [k for k, v in contract.items() if v is None]
        un = result.setdefault("uncertainty", [])
        un[:] = [u for u in un if not (isinstance(u, str) and u.startswith("output_contract 有"))]
        if nulls:
            un.append("output_contract 有 %d/%d 个字段暂为 null（%s），随进度回填" %
                      (len(nulls), len(contract), ",".join(nulls)))
    # 证据链并入 result
    existing = {r.get("element_id") for r in result.setdefault("evidence_map", [])}
    for r in evidence:
        if r.get("element_id") and r["element_id"] not in existing:
            result["evidence_map"].append(r)
    result.setdefault("sources", {})["figure_spec"] = args.spec
    result.setdefault("sources", {})["renderer"] = render.get("renderer")
    if render.get("error"):
        result.setdefault("uncertainty", []).append(
            "导出失败/部分失败：%s" % render["error"])
        result.setdefault("pending_human", []).append(
            "请人工在可用环境中渲染 PNG/PDF 并复核字体嵌入")
    _append_change(result, "S3 build", "ok" if (render.get("png") or render.get("pdf"))
                   else "partial", "出图 %s，证据 %d 条，渲染器 %s"
                   % (rel_svg, len(evidence), render.get("renderer") or "无"))
    _write_json(result_path, result)
    print(json.dumps({"ok": True, "svg": rel_svg, "png": render.get("png"),
                      "pdf": render.get("pdf"), "renderer": render.get("renderer"),
                      "evidence": len(evidence), "render_error": render.get("error")},
                     ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="main.py", description="科研图表 Skill 统一入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scaffold", help="读取输入并落盘结果骨架（前 3 分钟强制）")
    p = sub.add_parser("extract", help="解析 CSV/TSV/Markdown 表格")
    p.add_argument("--input")
    p.add_argument("--text")
    p.add_argument("--name", default="table")
    p.add_argument("--out")
    p = sub.add_parser("build", help="按 spec 渲染 SVG + 证据链 + PNG/PDF")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--name")
    p.add_argument("--dpi", default=300)
    p.add_argument("--result")

    a = ap.parse_args()
    return {"scaffold": cmd_scaffold, "extract": cmd_extract, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
