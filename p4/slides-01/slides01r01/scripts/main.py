#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 命令行入口。子命令为「可选单次工具」，不要求串成流水线：

  scaffold   立即落盘一版合法 partial result 骨架（前 3 分钟必须执行）
  inspect    扫描 /input，打印材料清单与 output_contract 键名
  validate   校验 draft.json 的证据溯源 / 最终 result.json 的契约完整
  build-pptx 由 draft.json 生成可编辑 pptx（提供模板则继承其版式/主题）
  pdf-pages  统计 PDF 页数（验证「源文件页数 == 导出 PDF 页数」）
  finalize   合并骨架 + draft + 产物信息，写回最终 result.json

退出码约定：0=成功；1=业务性失败（输入缺失/校验不过，已写 errors）；2=意外异常。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import core_impl as core

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


def _load_case(input_root: Path):
    case, err = core.read_json(input_root / "case.json")
    if err:
        return {}, [f"case.json unavailable: {err}"]
    return (case if isinstance(case, dict) else {}), []


def _path_of(dest: str):
    p = Path(dest)
    return p if p.is_absolute() else (Path.cwd() / p)


def cmd_scaffold(args) -> int:
    input_root = _path_of(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    output = _path_of(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    case, errs = _load_case(input_root)
    result = core.scaffold_result(case, status=getattr(args, "status", "partial") or "partial")
    for e in errs:
        result.setdefault("errors", []).append(e)
    core.ensure_dir(output)
    ok = core.write_json(output / "result.json", result)
    print(json.dumps({"ok": ok, "case_id": result.get("case_id"),
                      "status": result.get("status"), "output": str(output / "result.json")},
                     ensure_ascii=False))
    return 0 if ok else 1


def cmd_inspect(args) -> int:
    input_root = _path_of(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    case, errs = _load_case(input_root)
    case_id, keys, degraded = core.discover_contract(case)
    items = core.inventory_input(input_root)
    report = {
        "case_id": case_id,
        "output_contract_keys": keys,
        "contract_degraded": degraded,
        "materials": items,
        "errors": errs,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args) -> int:
    draft_path = _path_of(args.draft)
    result_path = _path_of(args.result) if getattr(args, "result", None) else None
    draft, err = core.read_json(draft_path)
    if err:
        print(json.dumps({"ok": False, "errors": [err]}, ensure_ascii=False))
        return 1
    findings = core.validate_draft(draft)
    if result_path and result_path.is_file():
        res, rerr = core.read_json(result_path)
        if rerr is None and isinstance(res, dict):
            keys, _, _ = core.discover_contract(
                {"output_contract": res.get("output_contract")} if res.get("output_contract") else {}
            )
            findings += core.validate_result(res, keys)
    errors = [f for f in findings if f["level"] == "error"]
    print(json.dumps({"ok": not errors, "findings": findings}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def cmd_build(args) -> int:
    draft_path = _path_of(args.draft)
    out_path = _path_of(args.out)
    template = _path_of(args.template) if getattr(args, "template", None) else None
    draft, err = core.read_json(draft_path)
    if err:
        print(json.dumps({"ok": False, "errors": [err]}, ensure_ascii=False))
        return 1
    findings = core.validate_draft(draft)
    if any(f["level"] == "error" for f in findings):
        print(json.dumps({"ok": False, "errors": findings}, ensure_ascii=False, indent=2))
        return 1
    ok, err2, info = core.build_pptx(draft, out_path, template_path=template)
    if not ok:
        print(json.dumps({"ok": False, "errors": [err2]}, ensure_ascii=False))
        return 1
    ok_v, verr = core.validate_pptx(out_path)
    print(json.dumps({"ok": ok_v, "slides": info.get("slides"),
                      "inherited_template": info.get("inherited_template"),
                      "pptx_validate": verr, "output": str(out_path)}, ensure_ascii=False))
    return 0 if ok_v else 1


def cmd_pdfpages(args) -> int:
    n = core.pdf_page_count(_path_of(args.pdf))
    print(json.dumps({"pages": n, "valid": n >= 0}, ensure_ascii=False))
    return 0


def cmd_finalize(args) -> int:
    output = _path_of(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    input_root = _path_of(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    draft_path = _path_of(args.draft) if getattr(args, "draft", None) else None
    pptx_path = _path_of(args.pptx) if getattr(args, "pptx", None) else None
    pdf_path = _path_of(args.pdf) if getattr(args, "pdf", None) else None

    result_path = output / "result.json"
    if result_path.is_file():
        result, err = core.read_json(result_path)
        result = result if isinstance(result, dict) else core.scaffold_result({})
    else:
        case, _ = _load_case(input_root)
        result = core.scaffold_result(case)

    if draft_path and draft_path.is_file():
        draft, derr = core.read_json(draft_path)
        if derr is None and isinstance(draft, dict):
            if isinstance(result.get("content_map"), list):
                result["content_map"] = draft.get("slides") or result["content_map"]
            # 细粒度证据映射（本版强化点）：每条结论带 locator + 逐字 quote
            emap = []
            for s in (draft.get("slides") or []):
                emap.append({
                    "page_id": s.get("index"),
                    "role": s.get("role"),
                    "claim": s.get("claim"),
                    "evidence": s.get("evidence") or [],
                    "numeric_inference": bool(s.get("numeric_inference")),
                })
            result["evidence_map"] = emap
            result.setdefault("compliance_checks", [])
            if isinstance(result["compliance_checks"], list):
                result["compliance_checks"] = core.validate_draft(draft) + result["compliance_checks"]

    if pptx_path and pptx_path.is_file():
        ok_v, verr = core.validate_pptx(pptx_path)
        if isinstance(result.get("editable_source"), dict):
            result["editable_source"] = {
                "path": pptx_path.name, "format": "pptx",
                "slides": 0, "pptx_validate_ok": ok_v, "pptx_validate_err": verr,
            }
    if pdf_path and pdf_path.is_file():
        pages = core.pdf_page_count(pdf_path)
        if isinstance(result.get("export_artifact"), dict):
            result["export_artifact"] = {"pdf": {"path": pdf_path.name, "pages": pages}, "png": []}

    result.setdefault("change_log", []).append({
        "t": core.now_iso(), "phase": "finalize",
        "note": "合并 draft/evidence_map/pptx/pdf 信息，写入最终 result.json",
    })
    rt = result.get("runtime") or {}
    rt["phase"] = "finalize"
    result["runtime"] = rt
    ok = core.write_json(result_path, result)
    print(json.dumps({"ok": ok, "output": str(result_path)}, ensure_ascii=False))
    return 0 if ok else 1


def _add_common(sp):
    """向子命令添加 --input/--output/--work；default=SUPPRESS 以保留顶层值。"""
    sp.add_argument("--input", default=argparse.SUPPRESS, help="输入目录（默认 /input）")
    sp.add_argument("--output", default=argparse.SUPPRESS, help="输出目录（默认 /app/output）")
    sp.add_argument("--work", default=argparse.SUPPRESS, help="工作目录（默认 /app/work）")


def run(argv=None) -> int:
    p = argparse.ArgumentParser(prog="scripts/main.py",
                                description="slides-01 学术 PPT 生成与证据溯源工具（纯标准库）")
    p.add_argument("--input", help="输入目录（默认 /input）")
    p.add_argument("--output", help="输出目录（默认 /app/output）")
    p.add_argument("--work", help="工作目录（默认 /app/work）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold", help="立即落盘 partial result 骨架")
    _add_common(sp)
    sp.add_argument("--status", default="partial")

    sp = sub.add_parser("inspect", help="扫描输入目录并列出契约键名与材料")
    _add_common(sp)

    vp = sub.add_parser("validate", help="校验 draft.json / result.json")
    _add_common(vp)
    vp.add_argument("--draft", required=True)
    vp.add_argument("--result")

    bp = sub.add_parser("build-pptx", help="由 draft.json 生成可编辑 pptx")
    _add_common(bp)
    bp.add_argument("--draft", required=True)
    bp.add_argument("--out", required=True)
    bp.add_argument("--template", help="模板 pptx（提供则继承其 master/layout/theme）")

    pp = sub.add_parser("pdf-pages", help="统计 PDF 页数")
    _add_common(pp)
    pp.add_argument("--pdf", required=True)

    fp = sub.add_parser("finalize", help="合并骨架与产物信息，写回最终 result.json")
    _add_common(fp)
    fp.add_argument("--draft")
    fp.add_argument("--pptx")
    fp.add_argument("--pdf")

    args = p.parse_args(argv)
    try:
        start = time.time()
        handlers = {
            "scaffold": cmd_scaffold, "inspect": cmd_inspect, "validate": cmd_validate,
            "build-pptx": cmd_build, "pdf-pages": cmd_pdfpages, "finalize": cmd_finalize,
        }
        code = handlers[args.cmd](args)
        if getattr(args, "verbose", False):
            print(f"[main] {args.cmd} took {time.time() - start:.2f}s", file=sys.stderr)
        return code
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "fatal": f"{exc.__class__.__name__}: {exc}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run())
