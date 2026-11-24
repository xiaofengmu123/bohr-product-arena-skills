#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \
      --input /input --output /app/output --work /app/work

关键行为：先落盘合法 partial 骨架（甚至早于任何解析），之后只做增量改进；
任何中途异常都不会留下不完整或崩溃的产物。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import core_impl as fs


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="多数据库联合检索：解析 /input，产出可复核 result.json。")
    p.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    p.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    p.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    p.add_argument("--skeleton-only", action="store_true",
                   help="仅落盘骨架，不做解析")
    p.add_argument("--decisions", default=None,
                   help="模型/人工仲裁 decisions.json，用于覆盖排序与状态")
    return p.parse_args(argv)


def _ensure_dir(path: Path) -> List[str]:
    errs: List[str] = []
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errs.append("mkdir_failed:%s:%s" % (path, exc))
    return errs


def _write_json(path: Path, obj: Any) -> List[str]:
    errs: List[str] = []
    try:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        errs.append("write_failed:%s:%s" % (path, exc))
    return errs


def _write_text(path: Path, text: str) -> List[str]:
    errs: List[str] = []
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        errs.append("write_failed:%s:%s" % (path, exc))
    return errs


def _load_decisions(path: Optional[str]) -> Optional[Dict]:
    if not path:
        return None
    obj, _errs = fs.load_json(Path(path))
    return obj if isinstance(obj, dict) else None


def build_auxiliaries(res: Dict, output_dir: Path) -> List[str]:
    """写辅助产物（可打开、可编辑、可复现）。失败只记录，不崩溃。"""
    errors: List[str] = []
    errors += _write_json(output_dir / "merged_records.json",
                          {"case_id": res.get("case_id"),
                           "records": res.get("candidates", [])})
    errors += _write_json(output_dir / "evidence_map.json",
                          {"case_id": res.get("case_id"),
                           "evidence_records": res.get("evidence_records", []),
                           "conflicts": res.get("conflicts", []),
                           "missing_metadata": res.get("missing_metadata", []),
                           "coverage": res.get("coverage")})
    errors += _write_text(output_dir / "coverage_report.md",
                          render_coverage_report(res))
    return errors


def render_coverage_report(res: Dict) -> str:
    cov = res.get("coverage") or {}
    lines = ["# 覆盖报告", "",
             "- case_id: %s" % res.get("case_id"),
             "- status: %s" % res.get("status"),
             "- total_input_records: %s" % cov.get("total_input_records"),
             "- merged_record_count: %s" % cov.get("merged_record_count"),
             "- duplicates_merged: %s" % cov.get("duplicates_merged"),
             ""]
    lines.append("## 单库独有记录（records_in_single_db_only）")
    solo = cov.get("records_in_single_db_only") or []
    if solo:
        for s in solo:
            lines.append("- [%s] %s (count=%s)" % (
                s.get("db"), s.get("title"), s.get("record_count_in_cluster")))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 系统性缺失字段（systematically_missing_fields）")
    smf = cov.get("systematically_missing_fields") or {}
    if smf:
        for db, fields in smf.items():
            lines.append("- %s: %s" % (db, ", ".join(fields) if fields else "无"))
    else:
        lines.append("- 无库数据")
    lines.append("")
    lines.append("## 降级与局限")
    for item in res.get("degradations", []):
        lines.append("- degradation: %s" % item)
    for item in res.get("limitations", []):
        lines.append("- limitation: %s" % item)
    for item in res.get("errors", []):
        lines.append("- error: %s" % item)
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    args = parse_args(argv)
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)

    started_iso = fs.iso_now()
    started_mono = time.monotonic()

    # 1) 读 case.json（容错），确定 case_id 与 output_contract
    case_meta, case_errs = fs.read_case_meta(input_dir / "case.json")
    case_id = "unknown"
    contract: Optional[List[str]] = None
    if isinstance(case_meta, dict):
        case_id = case_meta.get("case_id") or case_meta.get("id") \
            or case_meta.get("challenge_id") or "unknown"
        oc = case_meta.get("output_contract")
        if isinstance(oc, list):
            contract = oc

    # 2) 立即落盘骨架（先于解析），保证中途异常也有合法产物
    skeleton = fs.build_skeleton(case_id, started_iso, contract)
    if case_errs:
        skeleton["errors"].extend(case_errs)
        skeleton["status"] = "input_error"
        skeleton["change_log"].append({
            "step": "case_read", "status": "degraded", "at": started_iso,
            "detail": "; ".join(case_errs),
        })
    dir_errs = _ensure_dir(output_dir) + _ensure_dir(work_dir)
    skeleton["errors"].extend(dir_errs)
    _write_json(output_dir / "result.json", skeleton)

    if args.skeleton_only:
        print(json.dumps({"case_id": case_id, "status": skeleton["status"],
                          "result": str(output_dir / "result.json")},
                         ensure_ascii=False))
        return 0

    # 3) 全量分析（任何异常都不抛出）
    try:
        analysis = fs.analyze_inputs(input_dir)
    except Exception as exc:  # 终极防御
        analysis = {"sources": [], "records": [], "degradations": ["analyze_crashed: %s" % exc],
                    "errors": ["analyze_crashed: %s" % exc], "pdf_texts": []}

    # 4) 组装完整结果
    res = fs.build_result(skeleton, case_meta, analysis)

    # 5) 合并模型/人工仲裁
    decisions = _load_decisions(args.decisions)
    if decisions:
        fs.apply_decisions(res, decisions, fs.iso_now())

    # 6) 收尾并落盘
    fs.finalize(res, started_mono, started_iso)
    _write_json(output_dir / "result.json", res)
    aux_errs = build_auxiliaries(res, output_dir)
    res["errors"].extend(aux_errs)
    if aux_errs:
        res["status"] = "partial"
        _write_json(output_dir / "result.json", res)

    print(json.dumps({"case_id": res.get("case_id"), "status": res.get("status"),
                      "candidates": len(res.get("candidates", [])),
                      "result": str(output_dir / "result.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
