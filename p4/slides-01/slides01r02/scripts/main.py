#!/usr/bin/env python3
"""slides-01 机械脚手架入口（仅标准库）。

用法：
  python3 scripts/main.py [--input DIR] [--output DIR] [--work DIR] [--validate] [--stats] [--strict]

默认模式：发现材料 → 写 result.json 骨架 → 自校验；--validate 校验已有 result.json；
--stats 打印材料清单与 pptx/pdf 页数。内容组织与写作由 Agent 完成，本脚本不做科学推断。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core_impl import (  # noqa: E402
    atomic_write_json, build_skeleton, contract_fields, discover_materials,
    pdf_page_count, pptx_inspect, pptx_slide_count, read_json, summarize_checks,
    validate_result,
)

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


def main() -> int:
    ap = argparse.ArgumentParser(description="slides-01 机械脚手架")
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="输入目录（默认 /input）")
    ap.add_argument("--work", default=str(DEFAULT_WORK), help="工作目录（默认 /app/work）")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出目录（默认 /app/output）")
    ap.add_argument("--validate", action="store_true", help="校验已有 result.json 后退出")
    ap.add_argument("--stats", action="store_true", help="打印材料清单与页数后退出")
    ap.add_argument("--strict", action="store_true", help="产物缺失记为 error（配合 --validate）")
    args = ap.parse_args()

    input_root = Path(args.input).expanduser()
    work_root = Path(args.work).expanduser()
    output_root = Path(args.output).expanduser()
    for d in (work_root, output_root):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[degradation] 无法创建目录 {d}: {exc}", file=sys.stderr)

    if args.stats:
        return run_stats(input_root, output_root)

    case, case_err = read_json(input_root / "case.json")
    if case_err:
        case = {}
    if not isinstance(case, dict):
        case = {}
    fields, degradations = contract_fields(case)

    if args.validate:
        result, err = read_json(output_root / "result.json")
        if err:
            print(f"[error] {err}")
            return 1
        checks = validate_result(result, fields, output_root, strict=args.strict)
        return print_checks(checks)

    # 默认：写骨架
    result = build_skeleton(case, fields)
    materials, mat_degradations = discover_materials(input_root)
    degradations.extend(mat_degradations)
    if case_err:
        result["status"] = "input_error"
        result.setdefault("errors", [])
        if isinstance(result["errors"], list):
            result["errors"].append(f"case.json 不可用: {case_err}")
        result["case_id"] = "unknown-case"
    else:
        result["status"] = "partial"
    if degradations:
        result.setdefault("degradations", [])
        if isinstance(result["degradations"], list):
            result["degradations"].extend(degradations)

    ok = atomic_write_json(output_root / "result.json", result)
    if not ok:
        print("[error] 写 result.json 失败", file=sys.stderr)
        return 1

    summary = {
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "contract_fields": len(fields),
        "materials": len(materials),
        "result_written": ok,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def run_stats(input_root: Path, output_root: Path) -> int:
    materials, degradations = discover_materials(input_root)
    print(f"[materials] {len(materials)} 个文件（跳过 {len(degradations)} 个超限）")
    for m in materials:
        print(f"  {m['kind']:6s} {m['size']:>9d}  {m['rel']}")
    for key, name in (("pptx", "presentation.pptx"), ("pdf", "presentation.pdf")):
        path = output_root / name
        if path.is_file():
            count = pptx_slide_count(path) if key == "pptx" else pdf_page_count(path)
            print(f"[{key}] {name}: pages/slides = {count}")
            if key == "pptx":
                info = pptx_inspect(path)
                if info:
                    print(f"       masters={info['masters']} layouts={info['layouts']} "
                          f"text_previews={len(info['slide_text_preview'])}")
        else:
            print(f"[{key}] {name}: 不存在")
    return 0


def print_checks(checks) -> int:
    errors, warns = summarize_checks(checks)
    for c in checks:
        tag = c["severity"].upper()
        print(f"[{tag}] {c['id']}: {c['message']}")
    if errors:
        print(f"[result] {len(errors)} error(s), {len(warns)} warning(s)")
        return 1
    print(f"[result] PASS（{len(checks)} checks, {len(warns)} warning(s)）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
