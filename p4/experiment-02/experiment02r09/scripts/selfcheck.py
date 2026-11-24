#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 交付自检（selfcheck.py）。

校验 /app/output/result.json 是否符合输出契约与证据溯源硬要求，逐条打印
PASS/FAIL/WARN。存在 FAIL 时退出码 1，否则 0。缺文件/坏 JSON 不崩溃。

可选：--input 用于读取 case.json 的 output_contract，校验其键是否被原样回填。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"

DIMENSIONS = [
    "assumptions_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_exceptions",
    "ethics_safety",
]
VERDICTS = {"ok", "conditional", "blocked", "unconfirmed", "missing", "conflict"}
RESOURCE_STATES = {"已具备", "可外协", "需采购", "不具备", "未知"}
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
# 必须有证据定位的判定
EVIDENCE_REQUIRED = {"ok", "conditional", "blocked", "conflict"}


def load_json(path):
    if not os.path.exists(path):
        return None, f"missing: {path}"
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, f"unreadable_or_bad_json: {path}: {exc}"


def check(result, case_data):
    """返回 (checks, fails, warns)。每条 {"id","level","msg"}。"""
    checks = []
    fails = 0
    warns = 0

    def add(level, cid, msg):
        nonlocal fails, warns
        checks.append({"id": cid, "level": level, "msg": msg})
        if level == "FAIL":
            fails += 1
        elif level == "WARN":
            warns += 1

    if not isinstance(result, dict):
        add("FAIL", "r-1", "result.json 顶层必须是对象")
        return checks, fails, warns

    # 基础字段
    add("PASS" if result.get("case_id") else "FAIL", "r-2",
        f"case_id 存在: {result.get('case_id')!r}")
    add("PASS" if result.get("status") in STATUSES else "FAIL", "r-3",
        f"status ∈ {sorted(STATUSES)}: {result.get('status')!r}")

    # output_contract 键回填
    contract = case_data.get("output_contract") if isinstance(case_data, dict) else None
    if isinstance(contract, dict):
        missing = [k for k in contract if k not in result]
        if missing:
            add("FAIL", "c-1", f"output_contract 字段未回填: {missing}")
        else:
            add("PASS", "c-1", f"output_contract 全部 {len(contract)} 个字段已回填")

    # 主结构
    for field in ("overall_conclusion", "experiment_plan", "evidence_and_risks", "confirmation"):
        add("PASS" if isinstance(result.get(field), dict) else "FAIL", "s-1",
            f"{field} 存在且为对象")
    review = result.get("review")
    if not isinstance(review, dict):
        add("FAIL", "s-2", "review 必须是对象")
        review = {}
    for dim in DIMENSIONS:
        items = review.get(dim)
        if not isinstance(items, list):
            add("FAIL", "s-3", f"review.{dim} 必须是数组")
            continue
        if result.get("status") == "ok" and not items:
            add("FAIL", "s-4", f"status=ok 但 review.{dim} 为空")
        elif not items:
            add("WARN", "s-4", f"review.{dim} 为空（partial/insufficient 阶段可接受）")

    # 证据溯源硬要求
    for dim in DIMENSIONS:
        for i, item in enumerate(review.get(dim) or []):
            if not isinstance(item, dict):
                add("FAIL", "e-1", f"review.{dim}[{i}] 必须是对象")
                continue
            vid = f"{dim}[{i}]"
            verdict = item.get("verdict")
            if verdict not in VERDICTS:
                add("FAIL", "e-2", f"{vid}: verdict 非法: {verdict!r}")
            if verdict in EVIDENCE_REQUIRED:
                ev = item.get("evidence")
                ok_ev = (
                    isinstance(ev, list) and ev and all(
                        isinstance(x, dict) and x.get("locator") and x.get("quote")
                        for x in ev
                    )
                )
                add("PASS" if ok_ev else "FAIL", "e-3",
                    f"{vid}: 判定 {verdict!r} 需带 locator+quote 证据")
            if verdict == "conditional":
                conds = item.get("conditions")
                add("PASS" if isinstance(conds, list) and conds else "FAIL", "e-4",
                    f"{vid}: conditional 需写清可执行 conditions")
            if dim == "equipment_materials" and verdict not in (None, "missing", "unconfirmed", "conflict"):
                rs = item.get("resource_state")
                if rs not in RESOURCE_STATES:
                    add("WARN", "e-5", f"{vid}: 资源维度缺 resource_state")
            if dim in ("time_cost", "parameter_ranges") and verdict in ("ok", "conditional", "blocked"):
                if not (item.get("computation") or item.get("notes")):
                    add("WARN", "e-6", f"{vid}: 建议附 computation/notes 记录算式与依据")

    # change_log 与诚实标注字段
    add("PASS" if isinstance(result.get("change_log"), list) and result.get("change_log")
        else "FAIL", "h-1", "change_log 非空")
    for field in ("sources", "uncertainty", "pending_human", "limitations", "errors", "degradations", "artifacts"):
        add("PASS" if isinstance(result.get(field), list) else "FAIL", "h-2",
            f"{field} 存在且为数组")

    return checks, fails, warns


def main(argv=None):
    ap = argparse.ArgumentParser(description="实验方案可行性审查交付自检")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    result_path = os.path.join(args.output, "result.json")
    result, rerr = load_json(result_path)
    case_data, _ = load_json(os.path.join(args.input, "case.json"))

    if rerr:
        print(f"FAIL: {rerr}")
        return 1

    checks, fails, warns = check(result, case_data)
    for c in checks:
        print(f"[{c['level']}] {c['id']}: {c['msg']}")
    print(f"\nsummary: {len(checks)} checks, {fails} FAIL, {warns} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())