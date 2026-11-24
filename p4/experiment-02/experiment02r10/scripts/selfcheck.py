#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selfcheck.py — 分层门校验器 + 确定性自检。

校验顺序（分层门，先验契约再验证据）：
  G1 契约回填：result.json 是否回填 output_contract 的全部字段（原样键名）。
  G2 结构：result.json 是否含必需顶层键与七类 review 维度。
  G3 证据溯源：ok/conditional/blocked/conflict 的审查项是否带 locator+quote，
      且 quote 是否能在 /input 可读文本中找到逐字原文。
  G4 算术：time_cost/parameter_ranges 项的算式字段是否可自圆其说。
  G5 覆盖：七类约束各至少一条判断。
  G6 状态一致：status 与 verdict 之间的一致性（软告警，不判 FAIL）。

退出码恒为 0（自检是报告不是终止），但会打印 PASS/FAIL/WARN 清单。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as engine  # noqa: E402

REQUIRED_TOP = {
    "case_id", "status", "experiment_plan", "review", "plan_comparison",
    "evidence_and_risks", "confirmation", "sources", "uncertainty",
    "pending_human", "errors", "degradations", "change_log", "runtime", "artifacts",
}
REQUIRED_PLAN = {"research_question", "assumptions", "variables", "controls",
                 "steps", "parameters", "resources", "interpretation_criteria"}
VERDICTS_NEED_EVIDENCE = {"ok", "conditional", "blocked", "conflict"}


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": str(exc)}


def _readable_texts(input_dir):
    """收集 /input 下可校验的文本文件内容（跳过二进制 PDF 等）。"""
    texts = {}
    files, _ = engine.discover_inputs(input_dir)
    for item in files:
        if item["status"] != "ok":
            continue
        if item["ext"] not in engine.TEXT_EXTS:
            continue
        text, _ = engine.read_text(item["abspath"])
        if text is not None:
            texts[item["relpath"]] = text
    return texts


def _norm(s):
    return " ".join((s or "").split())


def _check_quote(quote, texts):
    """在可读文本中查找逐字原文。返回 (found_exact, found_norm, locations)。"""
    locations = []
    q = (quote or "").strip()
    if not q:
        return False, False, locations
    for rel, text in texts.items():
        if q in text:
            locations.append(rel)
    exact = bool(locations)
    norm = False
    if not exact:
        nq = _norm(q)
        for rel, text in texts.items():
            if nq and nq in _norm(text):
                locations.append(rel + "#norm")
                norm = True
    return exact, norm, locations


def _walk_review_items(review):
    items = []
    for dim, arr in (review or {}).items():
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict):
                    items.append((dim, it))
    return items


def gate_contract(result, record):
    contract = (record or {}).get("output_contract") or {}
    missing = [k for k in contract if k not in result]
    return {"pass": not missing, "missing": missing, "count": len(contract)}


def gate_schema(result):
    missing = [k for k in REQUIRED_TOP if k not in result]
    plan_missing = []
    plan = result.get("experiment_plan") or {}
    for k in REQUIRED_PLAN:
        if k not in plan:
            plan_missing.append("experiment_plan.%s" % k)
    review = result.get("review") or {}
    dim_missing = [d for d in engine.DIMENSIONS if d not in review]
    return {
        "pass": not missing and not plan_missing and not dim_missing,
        "missing_top": missing, "missing_plan": plan_missing, "missing_dims": dim_missing,
    }


def gate_evidence(result, texts):
    review = result.get("review") or {}
    problems = []
    total_checked = 0
    for dim, it in _walk_review_items(review):
        verdict = it.get("verdict")
        if verdict not in VERDICTS_NEED_EVIDENCE:
            continue
        total_checked += 1
        evidence = it.get("evidence") or []
        if not evidence:
            problems.append("%s(%s): 无 evidence" % (it.get("id"), verdict))
            continue
        for ev in evidence:
            if not ev.get("locator") or not ev.get("quote"):
                problems.append("%s: evidence 缺 locator/quote" % (it.get("id")))
                continue
            exact, norm, locs = _check_quote(ev.get("quote"), texts)
            if not exact and not norm:
                problems.append("%s: quote 未在可读文本中找到（可能来自 PDF，需人工核）" % (it.get("id")))
            elif not exact and norm:
                problems.append("%s: quote 仅经空白归一后匹配（核 locator）" % (it.get("id")))
    return {"pass": not problems, "problems": problems, "checked": total_checked}


def gate_arithmetic(result):
    review = result.get("review") or {}
    problems = []
    for dim, it in _walk_review_items(review):
        if dim in ("time_cost", "parameter_ranges"):
            formula = (it.get("formula") or it.get("notes") or [])
            if not formula and it.get("verdict") in VERDICTS_NEED_EVIDENCE:
                problems.append("%s(%s): 数值结论缺算式" % (it.get("id"), dim))
    return {"pass": not problems, "problems": problems}


def gate_coverage(result):
    review = result.get("review") or {}
    empty = [d for d in engine.DIMENSIONS if not review.get(d)]
    return {"pass": not empty, "empty_dims": empty}


def gate_status(result):
    status = result.get("status")
    warnings = []
    if status == "ok":
        review = result.get("review") or {}
        has_blocked = any(
            it.get("verdict") == "blocked"
            for _, it in _walk_review_items(review))
        if has_blocked:
            warnings.append("status=ok 但存在 blocked 项，请复核整体结论")
    if status not in engine.STATUSES and status is not None:
        warnings.append("status 值非法: %r" % status)
    return {"warnings": warnings}


def run(input_dir, output_dir, work_dir):
    result = _load_json(os.path.join(output_dir, "result.json"))
    if "_error" in result:
        print("FAIL result.json 无法读取: %s" % result["_error"])
        return 1
    record = _load_json(os.path.join(work_dir, "case_record.json"))
    if "_error" in record:
        record = {}

    texts = _readable_texts(input_dir)
    gates = {
        "G1_contract": gate_contract(result, record),
        "G2_schema": gate_schema(result),
        "G3_evidence": gate_evidence(result, texts),
        "G4_arithmetic": gate_arithmetic(result),
        "G5_coverage": gate_coverage(result),
        "G6_status": gate_status(result),
    }

    total_fail = 0
    for name, g in gates.items():
        if isinstance(g, dict) and g.get("pass") is True:
            print("PASS %s" % name)
        elif name == "G6_status":
            if g.get("warnings"):
                print("WARN %s: %s" % (name, "; ".join(g["warnings"])))
            else:
                print("PASS %s" % name)
        else:
            total_fail += 1
            detail = g.get("problems") or g.get("missing") or g.get("missing_top") \
                or g.get("missing_plan") or g.get("missing_dims") or g.get("empty_dims")
            print("FAIL %s: %s" % (name, detail))

    print("SUMMARY: fail=%d readable_text_files=%d" % (total_fail, len(texts)))
    return 0  # 自检是报告，不因 FAIL 终止；修复由 Agent 在 S5 完成


def selftest():
    return engine._selftest()


def main(argv=None):
    parser = argparse.ArgumentParser(description="分层门校验器")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--selftest", action="store_true", help="只跑引擎确定性自检")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    return run(args.input, args.output, args.work)


if __name__ == "__main__":
    raise SystemExit(main())