#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graphical-abstract orchestrator.

子命令：
  init      归一化输入，立即落盘 plan.json 骨架 + result.json（status=partial）
  build     读 plan.json → figure_model.json → SVG → PNG/PDF 预览
  validate  校验并更新 result.json（填充 limitations）
  record-exports 把平台工具渲染好的 PNG/PDF 路径记入 result.json.exports
  selftest  运行内置自检（等价于 scripts/selftest.py）
  （缺省）   init + build + validate 一次跑完

路径默认 /input、/app/work、/app/output；用 --input/--work/--output 覆盖。
只依赖 Python 标准库；脚本以 /skills/weekly-skill 为 cwd 时相对路径可解析。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402

CANONICAL_LAYERS = ["layer-background", "layer-panels", "layer-entities",
                    "layer-relations", "layer-data", "layer-legend",
                    "layer-title", "layer-footer"]


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _base_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "graphical_abstract": {"question": None, "method": None,
                               "results": None, "conclusion": None},
        "editable_source": {"path": None, "format": "svg",
                            "layers": CANONICAL_LAYERS, "text_editable": True},
        "exports": {"png": {"path": None, "renderer": None,
                            "status": "preview_not_rendered"},
                    "pdf": {"path": None, "renderer": None,
                            "status": "preview_not_rendered"}},
        "entities": [], "relations": [], "data_mapping": {}, "legend": [],
        "plan_path": None, "figure_model_path": None,
        "sources": [], "uncertainty": [], "limitations": [],
        "pending_human": [], "change_log": [],
        "runtime": {"started_at": C.now_iso(), "finished_at": None,
                    "seconds": None, "renderer": None,
                    "version": C.CANONICAL_VERSION},
    }


def read_case(input_dir):
    case_path, task_path, materials, err = C.discover_inputs(input_dir)
    if err:
        return None, err, materials
    case_obj, case_err = C.load_json(case_path) if case_path else (None, "missing case.json")
    if case_err:
        return None, case_err, materials
    if not isinstance(case_obj, dict):
        return None, "case.json is not an object", materials
    case_id = (case_obj.get("case_id") or case_obj.get("id")
               or case_obj.get("challenge_id") or "unknown")
    task_text, _ = C.read_text(task_path) if task_path else ("", "missing task.md")
    normalized = {
        "case_id": case_id,
        "task_path": task_path,
        "materials": materials,
        "task_text_preview": (task_text or "")[:4000],
        "output_contract_fields": sorted(C.collect_contract_fields(case_obj).keys()),
        "raw_case_keys": sorted(case_obj.keys()),
    }
    return normalized, None, materials


def run_init(input_dir, work_dir, output_dir):
    work = ensure_dir(work_dir)
    out = ensure_dir(output_dir)
    normalized, err, materials = read_case(input_dir)
    if err:
        result = _base_result("unknown")
        result["status"] = "failed"
        result["limitations"].append("input error: %s" % err)
        C.write_json(os.path.join(out, "result.json"), result)
        return result
    case_id = normalized["case_id"]
    plan = C.default_plan(case_id)
    plan["materials"] = materials
    plan["change_log"].append({"at": C.now_iso(), "step": "init",
                               "note": "skeleton plan written"})
    C.write_json(os.path.join(work, "case_normalized.json"), normalized)
    C.write_json(os.path.join(work, "plan.json"), plan)
    result = _base_result(case_id)
    for k in normalized["output_contract_fields"]:
        result[k] = None
    result["plan_path"] = os.path.join(work, "plan.json")
    result["change_log"].append({"at": C.now_iso(), "step": "init",
                                 "note": "partial result skeleton written"})
    C.write_json(os.path.join(out, "result.json"), result)
    return result


def _merge_output_contract_fields(result, prev):
    # 保留上一版 result.json 中出现的 output_contract 自定义键（值回填 null）
    if isinstance(prev, dict):
        for k, v in prev.items():
            if k not in result and k != "case_id":
                result[k] = None if v is None else v
    return result


def collect_result_fields(plan, model, svg_path, exports, issues, work_dir, output_dir):
    ga = plan.get("graphical_abstract") or {}
    renderer = exports["png"]["renderer"] or exports["pdf"]["renderer"]
    result = _base_result(plan.get("case_id", "unknown"))
    result.update({
        "graphical_abstract": {
            "question": ga.get("question"), "method": ga.get("method"),
            "results": ga.get("results"), "conclusion": ga.get("conclusion")},
        "editable_source": {
            "path": svg_path, "format": "svg", "layers": CANONICAL_LAYERS,
            "text_editable": True, "checksum": model.get("checksum")},
        "exports": exports,
        "entities": plan.get("entities") or [],
        "relations": plan.get("relations") or [],
        "data_mapping": model.get("data_mapping") or {},
        "legend": plan.get("legend") or [],
        "plan_path": os.path.join(work_dir, "plan.json"),
        "figure_model_path": os.path.join(work_dir, "figure_model.json"),
        "sources": plan.get("sources") or [],
        "uncertainty": plan.get("uncertainty") or [],
        "limitations": list(plan.get("limitations") or []),
        "pending_human": plan.get("pending_human") or [],
        "change_log": list(plan.get("change_log") or []),
    })
    result["runtime"].update({"renderer": renderer, "finished_at": C.now_iso()})
    for i in issues:
        note = "validate: %s" % i
        if note not in result["limitations"]:
            result["limitations"].append(note)
    if exports["png"]["status"] != "rendered" and exports["pdf"]["status"] != "rendered":
        note = "no renderer available; preview_not_rendered (SVG kept as editable source)"
        if note not in result["limitations"]:
            result["limitations"].append(note)
    return result


def decide_status(plan, model, exports, issues):
    fs = plan.get("final_status")
    if fs in ("completed", "partial", "failed"):
        return fs
    critical = [i for i in issues if i.startswith("missing source") or "svg" in i
                or i.startswith("overflow")]
    if not (plan.get("entities") or []):
        critical.append("no entities extracted")
    if critical:
        return "partial"
    if exports["png"]["status"] == "rendered" or exports["pdf"]["status"] == "rendered":
        return "completed"
    return "partial"


def run_build(work_dir, output_dir, plan_override=None):
    work = ensure_dir(work_dir)
    out = ensure_dir(output_dir)
    plan, err = C.load_json(os.path.join(work, "plan.json"))
    if err or not isinstance(plan, dict):
        plan = C.default_plan("unknown")
        plan["limitations"].append("plan.json missing or invalid: %s" % err)
    if isinstance(plan_override, dict):
        plan = plan_override
    case_id = plan.get("case_id", "unknown")
    model = C.build_figure_model(plan)
    C.write_json(os.path.join(work, "figure_model.json"), model)
    svg_path = os.path.join(out, "fig_%s.svg" % case_id)
    C.render_svg(model, svg_path)
    exports = C.try_render(svg_path, os.path.join(out, "fig_%s" % case_id))
    issues = C.validate_model(model, svg_path)
    result = collect_result_fields(plan, model, svg_path, exports, issues,
                                   work_dir, output_dir)
    prev, _ = C.load_json(os.path.join(out, "result.json"), {})
    _merge_output_contract_fields(result, prev)
    result["status"] = decide_status(plan, model, exports, issues)
    C.write_json(os.path.join(out, "result.json"), result)
    return result


def run_validate(work_dir, output_dir):
    work = ensure_dir(work_dir)
    out = ensure_dir(output_dir)
    model, merr = C.load_json(os.path.join(work, "figure_model.json"), {})
    result, rerr = C.load_json(os.path.join(out, "result.json"), {})
    issues = []
    if merr or not isinstance(model, dict):
        issues.append("figure_model missing or invalid: %s" % merr)
        model = {}
    else:
        svg_path = (result or {}).get("editable_source", {}).get("path")
        issues = C.validate_model(model, svg_path)
    if isinstance(result, dict):
        result["limitations"] = list(result.get("limitations") or [])
        for i in issues:
            note = "validate: %s" % i
            if note not in result["limitations"]:
                result["limitations"].append(note)
        result["runtime"]["finished_at"] = C.now_iso()
        C.write_json(os.path.join(out, "result.json"), result)
    return issues


def run_flow(input_dir, work_dir, output_dir):
    t0 = time.time()
    run_init(input_dir, work_dir, output_dir)
    result = run_build(work_dir, output_dir)
    run_validate(work_dir, output_dir)
    final, _ = C.load_json(os.path.join(output_dir, "result.json"), {})
    if isinstance(final, dict):
        final["runtime"]["finished_at"] = C.now_iso()
        final["runtime"]["seconds"] = round(time.time() - t0, 2)
        C.write_json(os.path.join(output_dir, "result.json"), final)
        return final
    return result


def run_record_exports(output_dir, png_path=None, pdf_path=None, renderer="platform-tool"):
    out = ensure_dir(output_dir)
    result, err = C.load_json(os.path.join(out, "result.json"), {})
    if not isinstance(result, dict):
        result = _base_result("unknown")
    exports = result.get("exports") or {}
    if png_path and os.path.isfile(png_path) and C._is_png(png_path):
        exports["png"] = {"path": png_path, "renderer": renderer, "status": "rendered"}
    if pdf_path and os.path.isfile(pdf_path) and C._is_pdf(pdf_path):
        exports["pdf"] = {"path": pdf_path, "renderer": renderer, "status": "rendered"}
    result["exports"] = exports
    cur = (result.get("runtime") or {}).get("renderer")
    result.setdefault("runtime", {})["renderer"] = (
        exports["png"].get("renderer") or exports["pdf"].get("renderer") or cur)
    C.write_json(os.path.join(out, "result.json"), result)
    return result


def run_selftest():
    try:
        import selftest
    except ImportError:
        print("selftest module not found; run `python scripts/selftest.py`")
        return 1
    return selftest.main([])


def main(argv):
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["init", "build", "validate", "record-exports", "run", "selftest"])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--plan", default=None, help="optional plan.json override for build")
    ap.add_argument("--png", default=None, help="rendered PNG path for record-exports")
    ap.add_argument("--pdf", default=None, help="rendered PDF path for record-exports")
    ap.add_argument("--renderer", default="platform-tool",
                    help="renderer label for record-exports")
    args = ap.parse_args(argv)
    if args.command == "selftest":
        return run_selftest()
    if args.command == "init":
        run_init(args.input, args.work, args.output)
    elif args.command == "build":
        override = None
        if args.plan:
            override, _ = C.load_json(args.plan)
        run_build(args.work, args.output, override)
    elif args.command == "validate":
        run_validate(args.work, args.output)
    elif args.command == "record-exports":
        run_record_exports(args.output, args.png, args.pdf, args.renderer)
    else:
        run_flow(args.input, args.work, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
