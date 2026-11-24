#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graphical-abstract 流水线编排：extract / build / validate / full。

职责：归一化输入 → 抽取 FigureIR 中间表示 → 确定性构建分层 SVG → 尝试渲染 PNG/PDF
→ 校验 → 写 /app/output/result.json。默认参数 --input /input --output /app/output
--work /app/work，纯标准库。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_impl import (
    VERSION,
    build_result,
    build_svg,
    detect_renderers,
    extract_figure_ir,
    load_json,
    normalize_case,
    validate_figure,
)


def _ensure(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_svg(svg: Path, fmt: str, out_dir: Path, renderer: Optional[str]) -> Optional[Path]:
    """尝试用已探测的渲染器把 SVG 渲染成 PNG/PDF；失败返回 None。"""
    if renderer is None:
        return None
    dst = out_dir / (svg.stem + "." + fmt)
    exe = Path(renderer).name.lower()
    try:
        if fmt == "png":
            if renderer == "cairosvg:module":
                subprocess.run([sys.executable, "-m", "cairosvg", str(svg), "-o", str(dst)],
                               check=True, timeout=120, capture_output=True)
            elif "rsvg" in exe:
                subprocess.run([renderer, "-o", str(dst), str(svg)],
                               check=True, timeout=120, capture_output=True)
            elif "inkscape" in exe:
                subprocess.run([renderer, "--export-type=png", "--export-filename",
                                str(dst), str(svg)], check=True, timeout=120, capture_output=True)
            elif "magick" in exe or "convert" in exe:
                subprocess.run([renderer, str(svg), str(dst)], check=True,
                               timeout=120, capture_output=True)
            elif "chrom" in exe:
                subprocess.run([renderer, "--headless", "--disable-gpu",
                                "--screenshot=" + str(dst), str(svg)], check=True,
                               timeout=120, capture_output=True)
            else:
                return None
        elif fmt == "pdf":
            if renderer == "cairosvg:module" or "cairosvg" in exe:
                subprocess.run([sys.executable, "-m", "cairosvg", str(svg), "-o", str(dst)],
                               check=True, timeout=120, capture_output=True)
            elif "rsvg" in exe:
                subprocess.run([renderer, "-f", "pdf", "-o", str(dst), str(svg)],
                               check=True, timeout=120, capture_output=True)
            elif "inkscape" in exe:
                subprocess.run([renderer, "--export-type=pdf", "--export-filename",
                                str(dst), str(svg)], check=True, timeout=120, capture_output=True)
            else:
                return None
        else:
            return None
    except (subprocess.SubprocessError, OSError) as exc:
        _log(f"render_failed:{fmt}:{exc}")
        return None
    return dst if dst.exists() else None


def run_extract(input_dir: Path, work_dir: Path) -> Dict[str, Any]:
    _ensure(work_dir)
    case, deg = normalize_case(input_dir)
    _write_json(work_dir / "case_normalized.json", case)
    ir, deg2 = extract_figure_ir(case, work_dir)
    _write_json(work_dir / "fig_ir.json", ir)
    return {"case": case, "ir": ir, "degradations": deg + deg2}


def run_build(ir: Dict[str, Any], output_dir: Path, work_dir: Path,
              case_id: Any) -> Dict[str, Any]:
    _ensure(output_dir)
    cid = str(case_id or ir.get("case_id") or "unknown")
    svg_path = output_dir / f"fig_{cid}.svg"
    build_svg(ir, svg_path)
    renderers = detect_renderers()
    png = _render_svg(svg_path, "png", output_dir, renderers.get("png"))
    pdf = _render_svg(svg_path, "pdf", output_dir, renderers.get("pdf"))
    manifest = {
        "case_id": cid,
        "svg": str(svg_path),
        "schema": "figure-manifest",
        "version": VERSION,
        "layers": ["layer-background", "layer-panels", "layer-labels"],
        "renderers": renderers,
        "exports": {
            "png": str(png) if png else None,
            "pdf": str(pdf) if pdf else None,
        },
    }
    _write_json(work_dir / "fig_manifest.json", manifest)
    return {
        "svg_path": svg_path,
        "png": png,
        "pdf": pdf,
        "renderers": renderers,
        "manifest": manifest,
    }


def _export_entries(png: Optional[Path], pdf: Optional[Path],
                    renderers: Dict[str, Optional[str]]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    entries.append({"format": "png", "path": str(png) if png else None,
                    "renderer": renderers.get("png") or "none",
                    "status": "rendered" if png else "preview_not_rendered"})
    entries.append({"format": "pdf", "path": str(pdf) if pdf else None,
                    "renderer": renderers.get("pdf") or "none",
                    "status": "rendered" if pdf else "preview_not_rendered"})
    return entries


def run(args: argparse.Namespace) -> int:
    t0 = time.time()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    _ensure(output_dir)
    _ensure(work_dir)
    change_log: List[Dict[str, Any]] = [{"version": VERSION, "time": int(t0), "note": "init"}]

    # 0) 输入目录缺 case.json：按降级策略写 failed，不进入构建
    if not (input_dir / "case.json").exists():
        _log("case.json missing -> failed skeleton")
        change_log.append({"version": VERSION, "time": int(t0), "note": "no_case_json"})
        result = build_result(None, "failed", None, change_log,
                              {"errors": ["case_json_missing"],
                               "degradations": [],
                               "limitations": ["no_case_json"],
                               "pending_human": ["provide_input"],
                               "uncertainty": [],
                               "artifacts": [],
                               "runtime": {"seconds": round(time.time() - t0, 2),
                                           "script_version": VERSION}})
        _write_json(output_dir / "result.json", result)
        return 1

    status = "partial"
    uncertainty: List[Dict[str, Any]] = list(uncertainty_override)
    pending_human: List[str] = []
    limitations: List[str] = list(limitations_override)
    errors: List[str] = []
    warnings: List[str] = []
    artifacts: List[Dict[str, str]] = []
    degradations: List[str] = []

    # 1) 尽早落盘合法骨架（前 3 分钟要求）
    skeleton = build_result(None, "partial", None, change_log,
                            {"uncertainty": [], "pending_human": [], "limitations": [],
                             "errors": [], "degradations": [], "artifacts": []})
    _write_json(output_dir / "result.json", skeleton)
    _log("skeleton written")

    case_id = None
    ir: Optional[Dict[str, Any]] = None
    contract: Dict[str, Any] = {}

    # --only validate：只校验既有 SVG + fig_ir.json，不重跑抽取/构建
    if args.only == "validate":
        ir_obj, d = load_json(work_dir / "fig_ir.json")
        degradations.extend(d)
        if not isinstance(ir_obj, dict):
            errors.append("validate_missing_ir")
        else:
            ir = ir_obj
            case_id = ir.get("case_id")
            svg = output_dir / f"fig_{case_id or 'unknown'}.svg"
            verrs, vwarns = validate_figure(svg, ir)
            errors.extend(verrs)
            warnings.extend(vwarns)
            for e in verrs:
                limitations.append(f"validation:{e}")
        result = build_result(case_id, "partial" if errors else "completed", ir, change_log,
                              {"errors": errors, "warnings": warnings,
                               "degradations": degradations, "limitations": limitations,
                               "pending_human": pending_human, "uncertainty": uncertainty,
                               "artifacts": artifacts,
                               "runtime": {"seconds": round(time.time() - t0, 2),
                                           "script_version": VERSION}})
        _write_json(output_dir / "result.json", result)
        return 0 if not errors else 2

    # 2) extract
    try:
        extracted = run_extract(input_dir, work_dir)
        case = extracted["case"]
        ir = extracted["ir"]
        degradations.extend(extracted["degradations"])
        case_id = case.get("case_id")
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            contract = {k: (v if v is not None else None) for k, v in oc.items()}
        if case_id is None:
            limitations.append("case_id_missing")
    except Exception as exc:  # noqa: BLE001 —— 失败也不能留下不完整产物
        errors.append(f"extract_failed:{exc}")
        result = build_result(None, "failed", None, change_log,
                              {"errors": errors, "degradations": degradations,
                               "limitations": ["extract_failed"],
                               "pending_human": ["input_unreadable"],
                               "uncertainty": [], "artifacts": []})
        _write_json(output_dir / "result.json", result)
        return 1

    if args.only == "extract":
        result = build_result(case_id, "partial", ir, change_log,
                              {"errors": errors, "degradations": degradations,
                               "limitations": limitations, "pending_human": pending_human,
                               "uncertainty": uncertainty, "artifacts": artifacts,
                               "runtime": {"seconds": round(time.time() - t0, 2),
                                           "script_version": VERSION}},
                              contract=contract)
        _write_json(output_dir / "result.json", result)
        return 0

    # 3) build
    build_info: Dict[str, Any] = {}
    try:
        build_info = run_build(ir, output_dir, work_dir, case_id)
        artifacts.append({"path": str(build_info["svg_path"]), "kind": "editable_svg",
                          "note": "layered editable source"})
        for fmt, p in (("png", build_info["png"]), ("pdf", build_info["pdf"])):
            if p:
                artifacts.append({"path": str(p), "kind": f"rendered_{fmt}", "note": "preview"})
            else:
                limitations.append(f"no_renderer:{fmt}")
                pending_human.append(f"render_{fmt}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"build_failed:{exc}")
        limitations.append("build_failed")
        result = build_result(case_id, "partial", ir, change_log,
                              {"errors": errors, "degradations": degradations,
                               "limitations": limitations, "pending_human": pending_human,
                               "uncertainty": uncertainty, "artifacts": artifacts,
                               "runtime": {"seconds": round(time.time() - t0, 2),
                                           "script_version": VERSION}},
                              contract=contract)
        _write_json(output_dir / "result.json", result)
        return 1

    # 4) validate
    svg_path = build_info.get("svg_path")
    if svg_path:
        verrs, vwarns = validate_figure(svg_path, ir)
        errors.extend(verrs)
        warnings.extend(vwarns)
        for e in verrs:
            limitations.append(f"validation:{e}")
        if verrs:
            pending_human.append("fix_critical_validation")
    _write_json(work_dir / "validation_report.json",
                {"errors": errors, "warnings": warnings, "degradations": degradations})

    if args.only == "build":
        result = build_result(case_id, "partial", ir, change_log,
                              {"errors": errors, "degradations": degradations,
                               "limitations": limitations, "pending_human": pending_human,
                               "uncertainty": uncertainty, "artifacts": artifacts,
                               "runtime": {"seconds": round(time.time() - t0, 2),
                                           "script_version": VERSION}},
                              contract=contract)
        _write_json(output_dir / "result.json", result)
        return 0

    # 5) 定状态并落盘最终 result.json
    if not errors:
        status = "completed"
    else:
        status = "partial"
    result = build_result(
        case_id, status, ir, change_log,
        {
            "editable_source": {
                "path": str(svg_path) if svg_path else None,
                "layers": ["layer-background", "layer-panels", "layer-labels"],
                "text_editable": True,
                "renderer": "native-svg",
            },
            "exports": _export_entries(build_info.get("png"), build_info.get("pdf"),
                                       build_info.get("renderers", {})),
            "errors": errors,
            "warnings": warnings,
            "degradations": degradations,
            "uncertainty": uncertainty,
            "pending_human": pending_human,
            "limitations": limitations,
            "artifacts": artifacts,
            "runtime": {"seconds": round(time.time() - t0, 2), "script_version": VERSION},
        },
        contract=contract,
    )
    _write_json(output_dir / "result.json", result)
    _log(f"done status={status} errors={len(errors)} warnings={len(warnings)}")
    return 0 if status == "completed" else 2


def main() -> int:
    p = argparse.ArgumentParser(
        description="graphical-abstract pipeline: extract -> build -> validate -> result.json")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--only", choices=["extract", "build", "validate", "full"],
                   default="full")
    p.add_argument("--uncertainty", default=None, help="JSON array of uncertainty entries")
    p.add_argument("--limitations", default=None, help="JSON array of limitation strings")
    args = p.parse_args()
    # 允许从命令行注入不确定性/局限（进阶改进时用）
    global uncertainty_override, limitations_override
    uncertainty_override = []
    limitations_override = []
    if args.uncertainty:
        try:
            uncertainty_override = json.loads(args.uncertainty)
        except json.JSONDecodeError:
            pass
    if args.limitations:
        try:
            limitations_override = json.loads(args.limitations)
        except json.JSONDecodeError:
            pass
    return run(args)


uncertainty_override: List[Any] = []
limitations_override: List[str] = []

if __name__ == "__main__":
    sys.exit(main())
