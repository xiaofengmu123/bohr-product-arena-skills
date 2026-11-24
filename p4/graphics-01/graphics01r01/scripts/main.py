#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研示意图 Skill 入口（薄调度层，实现见 core_impl.py）。

子命令：
  scaffold  先落盘合法 partial 结果骨架（前 3 分钟内必须调用一次）
  extract   发现材料并抽取 PDF 文本，写 /app/work 下的 locator 索引与摘要
  render    把 figure_spec.json 渲染为分层可编辑 SVG
  export    把 SVG 导出为 PNG/PDF 并测量参数
  validate  校验 result.json 契约与产物可打开性
  merge     把 evidence_map / provenance 合并进 result.json

默认（无子命令）：打印帮助并退出 0；不静默覆盖产物。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def cmd_scaffold(a):
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    case_path, task_path, _materials, _warnings = C.discover_inputs(a.input)
    summary = C.scan_cases_and_tasks(case_path, task_path)
    res = C.make_skeleton(summary, C.now_iso())
    # 空输入目录也要留下合法结果（insufficient 语义并入 uncertainty）
    if not summary.get("case_keys") and summary["case_id"] == "unknown":
        res["uncertainty"].append({
            "item": "case.json", "reason": "未找到 /input/case.json 或为空",
            "impact": "无案例标识，无法继续抽取", "mitigation": "补足输入后重跑 scaffold",
        })
    out_path = os.path.join(a.out, "result.json")
    C.save_json(out_path, res)
    print(json.dumps({"ok": True, "case_id": res["case_id"], "out": out_path},
                     ensure_ascii=False))
    return 0


def cmd_extract(a):
    os.makedirs(a.work, exist_ok=True)
    case_path, task_path, materials, warnings = C.discover_inputs(a.input)
    summary = C.scan_cases_and_tasks(case_path, task_path)
    report = {"case": summary, "materials": [], "warnings": warnings,
              "pdf_index": {}}
    for m in materials:
        low = m.lower()
        item = {"path": m, "rel": os.path.relpath(m, a.input),
                "bytes": os.path.getsize(m)}
        if low.endswith(".pdf"):
            index, note = C.extract_pdf_text(m, a.work)
            item["pdf"] = {"pages": list(index.keys()), "note": note}
            if not index:
                item["pdf"]["no_text_layer"] = True
                report.setdefault("ocr_needed", []).append(m)
        report["materials"].append(item)
    # 摘要写 work，供后续阶段读取
    C.save_json(os.path.join(a.work, "extraction_report.json"), report)
    # 骨架仍以 case_id 为准，不覆盖已存在的正式产物
    result_path = os.path.join(a.out, "result.json")
    if not os.path.exists(result_path):
        res = C.make_skeleton(summary, C.now_iso())
        C.save_json(result_path, res)
    print(json.dumps({"ok": True, "materials": len(materials),
                      "ocr_needed": report.get("ocr_needed", []),
                      "report": os.path.join(a.work, "extraction_report.json")},
                     ensure_ascii=False))
    return 0


def cmd_render(a):
    spec = C.load_json(a.spec)
    if not spec:
        print(json.dumps({"ok": False, "error": "spec 缺失或不可解析: %s" % a.spec},
                         ensure_ascii=False))
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    C.render_svg(spec, a.out)
    inv = C.svg_inventory(a.out)
    print(json.dumps({"ok": True, "out": a.out, **inv}, ensure_ascii=False))
    return 0


def cmd_export(a):
    os.makedirs(a.out, exist_ok=True)
    if not os.path.exists(a.svg):
        print(json.dumps({"ok": False, "error": "SVG 不存在: %s" % a.svg},
                         ensure_ascii=False))
        return 2
    records = C.export_svg(a.svg, a.out, a.work)
    C.save_json(os.path.join(a.work, "export_records.json"),
                {"svg": a.svg, "records": records, "time": C.now_iso()})
    print(json.dumps({"ok": True, "records": records}, ensure_ascii=False))
    return 0


def cmd_validate(a):
    issues, warnings = C.validate_result(a.result, a.output_dir)
    print(json.dumps({"ok": not issues, "errors": issues,
                      "warnings": warnings}, ensure_ascii=False))
    return 0 if not issues else 1


def cmd_merge(a):
    if not os.path.exists(a.result):
        print(json.dumps({"ok": False, "error": "result.json 不存在"}, ensure_ascii=False))
        return 2
    res = C.merge_result(a.result, a.evidence, a.spec, a.work)
    print(json.dumps({"ok": True, "status": res.get("status"),
                      "evidence": len(res.get("evidence_map", [])),
                      "provenance": len(res.get("provenance", []))}, ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="科研示意图 Skill 管线")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--case", default="/input/case.json")
    common.add_argument("--out", default="/app/output")
    common.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("scaffold", parents=[common], help="写 partial 结果骨架")
    s.set_defaults(func=cmd_scaffold)

    e = sub.add_parser("extract", parents=[common], help="发现材料并抽取 PDF 文本")
    e.set_defaults(func=cmd_extract)

    r = sub.add_parser("render", parents=[common], help="figure_spec.json -> SVG")
    r.add_argument("--spec", required=True)
    r.add_argument("--svg", dest="out", required=True)
    r.set_defaults(func=cmd_render)

    x = sub.add_parser("export", parents=[common], help="SVG -> PNG/PDF")
    x.add_argument("--svg", required=True)
    x.set_defaults(func=cmd_export)

    v = sub.add_parser("validate", parents=[common], help="校验 result.json 与产物")
    v.add_argument("--result", required=True)
    v.add_argument("--output-dir", default="/app/output")
    v.set_defaults(func=cmd_validate)

    m = sub.add_parser("merge", parents=[common], help="合并 evidence_map/provenance")
    m.add_argument("--result", required=True)
    m.add_argument("--evidence", default="")
    m.add_argument("--spec", default="")
    m.set_defaults(func=cmd_merge)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 0
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
