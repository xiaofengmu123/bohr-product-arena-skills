#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-schematic-editor 入口：scaffold / build / validate / run。

所有子命令在失败时都尽量返回退出码 0 并输出 JSON 摘要（鲁棒性优先）；
真正的“不可用”会记录在 result.json 的 errors/degradations，而非让进程崩溃。
默认参数：--input /input、--output /app/output、--work /app/work。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import core_impl as core  # noqa: E402


def resolve(p, default):
    if p is None:
        p = default
    return os.path.abspath(os.path.expanduser(p))


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cmd_scaffold(a):
    out = resolve(a.output, "/app/output")
    work = resolve(a.work, "/app/work")
    inp = resolve(a.input, "/input")
    try:
        os.makedirs(out, exist_ok=True)
        os.makedirs(work, exist_ok=True)
    except OSError as e:
        _print({"ok": False, "stage": "scaffold", "error": "mkdir: %s" % e})
        return 0

    files, diserr = core.discover_input(inp)
    case_id = "unknown"
    contract = None
    case_errors = []
    case_meta = {}
    task_meta = {}

    # 优先读 case.json（从发现清单或默认路径）
    case_file = core.find_by_name(files, "case.json")
    if case_file is None and os.path.isfile(os.path.join(inp, "case.json")):
        case_file = {"path": os.path.join(inp, "case.json"), "rel": "case.json",
                     "size_bytes": os.path.getsize(os.path.join(inp, "case.json")),
                     "oversize": False, "likely_binary": False}
    if case_file is not None and not case_file.get("oversize"):
        obj, errs = core.tolerant_json(case_file["path"])
        case_errors.extend(errs)
        if isinstance(obj, dict):
            case_id = obj.get("case_id") or obj.get("id") or "unknown"
            contract = obj.get("output_contract")
            for k in ("task", "work_order", "workorder", "title"):
                if obj.get(k) is not None:
                    case_meta[k] = obj.get(k)
        elif obj is not None:
            case_errors.append("case.json is not a JSON object")
    elif case_file is not None and case_file.get("oversize"):
        case_errors.append("case.json oversize, skipped")

    # 读 task.md（仅做元信息，不决定科学结论）
    task_file = core.find_by_name(files, "task.md")
    if task_file is not None and not task_file.get("oversize"):
        text, meta = core.read_text_robust(task_file["path"])
        task_meta = {"size_bytes": meta.get("size_bytes"), "truncated": meta.get("truncated"),
                     "encoding": meta.get("encoding"), "error": meta.get("error")}
        task_meta["chars"] = len(text)

    note = "initial partial skeleton"
    if case_errors:
        note += "; case errors: " + "; ".join(case_errors[:3])
    result = core.build_skeleton(case_id=case_id, contract=contract, status="partial", note=note)

    # 记录输入发现与限制
    result["sources"] = [
        {"ref": "input discovery", "path": f["path"], "locator": f["rel"],
         "kind": "text" if not f["likely_binary"] else "binary",
         "ocr": False, "used_for": "material inventory",
         "note": "oversize" if f["oversize"] else ""}
        for f in files[:200]
    ]
    for e in diserr:
        result["degradations"].append({"item": "input discovery", "reason": e,
                                       "impact": "some inputs may be unread", "mitigation": "manual locate + record"})
    for e in case_errors:
        result["errors"].append({"item": "case.json", "reason": e, "impact": "case metadata incomplete",
                                 "mitigation": "fallback to /input files"})
    if not files and not os.path.isdir(inp):
        result["errors"].append({"item": "input dir", "reason": "input dir missing: %s" % inp,
                                 "impact": "no materials available", "mitigation": "report insufficient evidence"})
        result["uncertainty"].append({"item": "materials", "reason": "input dir missing",
                                      "impact": "cannot extract entities", "mitigation": "human supplies materials"})
    if not files:
        result["status"] = "partial"
        result["uncertainty"].append({"item": "materials", "reason": "empty input directory",
                                      "impact": "no figure can be drawn from evidence",
                                      "mitigation": "request materials; do not fabricate"})

    try:
        core.write_json(os.path.join(out, "result.json"), result)
        core.write_json(os.path.join(work, "discovery.json"),
                        {"files": files, "case_meta": case_meta, "task_meta": task_meta,
                         "case_errors": case_errors, "discovery_errors": diserr,
                         "scaffold_iso": core.now_iso()})
    except OSError as e:
        _print({"ok": False, "stage": "scaffold", "error": "write: %s" % e,
                "result_path": os.path.join(out, "result.json")})
        return 0
    _print({"ok": True, "stage": "scaffold", "status": result["status"], "case_id": result["case_id"],
            "files": len(files), "result": os.path.join(out, "result.json")})
    return 0


def cmd_build(a):
    out = resolve(a.output, "/app/output")
    spec_path = a.spec
    if not spec_path:
        _print({"ok": False, "stage": "build", "error": "missing --spec"})
        return 0
    spec_path = os.path.abspath(os.path.expanduser(spec_path))
    spec, errs = core.tolerant_json(spec_path)
    if not isinstance(spec, dict):
        summary = {"ok": False, "stage": "build", "error": "spec missing/invalid",
                   "detail": "; ".join(errs)}
        try:
            os.makedirs(resolve(a.work, "/app/work"), exist_ok=True)
            core.write_json(os.path.join(resolve(a.work, "/app/work"), "build_error.json"), summary)
        except OSError:
            pass
        _print(summary)
        return 0
    svg = core.render_spec(spec)
    out_name = spec.get("output_file") or "figure.svg"
    if "/" in out_name or "\\" in out_name:
        out_name = os.path.basename(out_name)
    out_path = os.path.join(out, out_name)
    try:
        os.makedirs(out, exist_ok=True)
        core.write_text(out_path, svg)
    except OSError as e:
        _print({"ok": False, "stage": "build", "error": "write: %s" % e})
        return 0
    canvas = spec.get("canvas") or {}
    w_mm = canvas.get("width_mm") or 180
    h_mm = canvas.get("height_mm") or 120
    dpi = canvas.get("dpi") or 300
    built = {
        "path": out_path, "svg_bytes": len(svg),
        "width_mm": w_mm, "height_mm": h_mm, "dpi": dpi,
        "width_px": core.mm_to_px(w_mm, dpi), "height_px": core.mm_to_px(h_mm, dpi),
        "layers": ["background", "groups", "relations", "entities", "legend", "annotations"],
        "warnings": errs,
    }
    try:
        core.write_json(os.path.join(resolve(a.work, "/app/work"), "svg_built.json"), built)
    except OSError:
        pass
    _print({"ok": True, "stage": "build", "path": out_path, "svg_bytes": len(svg),
            "width_px": built["width_px"], "height_px": built["height_px"], "warnings": errs})
    return 0


def cmd_validate(a):
    out = resolve(a.output_dir, "/app/output")
    work = resolve(a.work, "/app/work")
    result_path = a.result or os.path.join(out, "result.json")
    result_path = os.path.abspath(os.path.expanduser(result_path))
    obj, errs = core.tolerant_json(result_path)
    report_path = os.path.join(work, "validation_report.json")
    if not isinstance(obj, dict):
        report = {"ok": False, "errors": ["result.json missing/invalid: %s" % "; ".join(errs)],
                  "warnings": [], "checks": 1, "summary": "cannot validate"}
    else:
        report = core.validate_result(obj, out)
    try:
        core.write_json(report_path, report)
    except OSError as e:
        _print({"ok": False, "stage": "validate", "error": "write report: %s" % e})
        return 0
    _print({"ok": report["ok"], "stage": "validate", "errors": report["errors"],
            "warnings": report["warnings"], "report": report_path})
    return 0


def cmd_run(a):
    out = resolve(a.output, "/app/output")
    work = resolve(a.work, "/app/work")
    cmd_scaffold(a)
    spec = os.path.join(work, "figure_spec.json")
    if os.path.isfile(spec):
        cmd_build(argparse.Namespace(spec=spec, output=a.output, work=a.work))
    else:
        # 无 spec：诚实标注 LLM 抽取阶段尚未产出，不让 run 空转假装成功
        try:
            core.write_json(os.path.join(work, "pipeline_note.json"),
                            {"note": "no figure_spec.json yet; LLM extraction stage pending"})
        except OSError:
            pass
    cmd_validate(argparse.Namespace(result=os.path.join(out, "result.json"),
                                    output_dir=a.output, work=a.work))
    # 更新 runtime 收尾信息（仅元信息，不改科学结论）
    result_path = os.path.join(out, "result.json")
    obj, _ = core.tolerant_json(result_path)
    if isinstance(obj, dict):
        started = (obj.get("runtime") or {}).get("started_iso")
        obj.setdefault("runtime", {})
        obj["runtime"]["ended_iso"] = core.now_iso()
        obj["change_log"] = list(obj.get("change_log") or [])
        obj["change_log"].append({"time": core.now_iso(), "action": "run-pipeline",
                                  "detail": "scaffold+build+validate finished"})
        try:
            core.write_json(result_path, obj)
        except OSError:
            pass
    _print({"ok": True, "stage": "run", "result": result_path})
    return 0


def main():
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="sci-schematic-editor 管线（scaffold/build/validate/run）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="发现输入并落盘 partial 结果骨架")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    p = sub.add_parser("build", help="figure_spec -> 分层 SVG")
    p.add_argument("--spec", required=True)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    p = sub.add_parser("validate", help="校验 result.json 契约与产物")
    p.add_argument("--result", default=None)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--work", default=None)

    p = sub.add_parser("run", help="一键 scaffold -> build(若有spec) -> validate")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)

    a = ap.parse_args()
    fn = {"scaffold": cmd_scaffold, "build": cmd_build, "validate": cmd_validate, "run": cmd_run}[a.cmd]
    try:
        return fn(a)
    except Exception as e:  # 兜底：任何内部错误都不崩溃，输出可解释摘要
        _print({"ok": False, "stage": a.cmd, "fatal": "%s: %s" % (type(e).__name__, e)})
        return 0


if __name__ == "__main__":
    sys.exit(main())
