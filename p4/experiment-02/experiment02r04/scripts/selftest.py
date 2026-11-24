#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 交付前自检脚本（selftest.py）。

对照评分维度做确定性结构校验：契约回填、证据溯源、条件可执行、限制显式、
数值不编造。退出码：0 = 通过（可能含 WARN），1 = 存在 FAIL 需修正。

仅依赖 Python 标准库；python3 与 python 均可运行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
VERDICT_ENUM = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
RESOURCE_STATE_ENUM = ("已具备", "可外协", "需采购", "不具备", "未知")
REVIEW_DIMS = (
    "assumptions_variables", "controls", "equipment_materials", "time_cost",
    "parameter_ranges", "risks_exceptions", "ethics_safety",
)
LIMITATION_KINDS = (
    "evidence_scope", "arithmetic_uncertainty", "source_quality",
    "missing_contract", "conflicting_sources", "unverifiable_claims",
)
EVIDENCE_REQUIRED_VERDICTS = ("ok", "conditional", "blocked", "conflict")

REQUIRED_KEYS = (
    "case_id", "status", "overall_conclusion", "experiment_plan", "review",
    "plan_comparison", "evidence_and_risks", "confirmation", "limitations",
    "uncertainty", "pending_human", "sources", "errors", "degradations",
    "change_log", "runtime", "artifacts", "contract",
)


def read_json(path):
    """容错读 JSON：BOM/坏编码降级；失败返回 None。"""
    if not os.path.exists(path):
        return None, f"missing: {path}"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"unreadable: {path}: {exc}"
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"bad_json: {path}: {exc}"


class Checker:
    def __init__(self):
        self.fails, self.warns, self.passes = [], [], []

    def fail(self, msg):
        self.fails.append(msg)

    def warn(self, msg):
        self.warns.append(msg)

    def ok(self, msg):
        self.passes.append(msg)


def check(result, checker):
    # 1) 顶层必需字段
    for key in REQUIRED_KEYS:
        if key not in result:
            checker.fail(f"缺少顶层字段: {key}")

    # 2) case_id
    cid = result.get("case_id")
    if not isinstance(cid, str) or not cid.strip():
        checker.fail("case_id 缺失或为空")
    elif cid == "unknown":
        checker.warn("case_id 退化为 unknown，未从输入识别到标识")
    else:
        checker.ok(f"case_id 存在: {cid}")

    # 3) status
    status = result.get("status")
    if status not in STATUS_ENUM:
        checker.fail(f"status 非法: {status!r}")
    else:
        checker.ok(f"status: {status}")

    # 4) 契约回填
    contract = result.get("contract") or {}
    echoed = contract.get("echoed_fields") or []
    for field in echoed:
        name = field.get("name") if isinstance(field, dict) else field
        if name not in result:
            checker.fail(f"output_contract 字段未回填: {name}")
        elif result[name] is None or result[name] == []:
            checker.warn(f"契约字段 {name} 仍为占位值（未填实际结果）")
    if echoed:
        checker.ok(f"契约字段回填 {len(echoed)} 项")

    # 5) review 七类维度
    review = result.get("review") or {}
    for dim in REVIEW_DIMS:
        if dim not in review:
            checker.fail(f"review 缺少维度: {dim}")
        elif not isinstance(review.get(dim), list):
            checker.fail(f"review.{dim} 应为数组")
    present_dims = [d for d in REVIEW_DIMS if d in review and review.get(d)]
    if len(present_dims) < len(REVIEW_DIMS):
        checker.warn(f"review 仅有 {len(present_dims)}/{len(REVIEW_DIMS)} 类有内容")

    # 6) 逐条审查项校验
    total_items = 0
    for dim in REVIEW_DIMS:
        for idx, item in enumerate(review.get(dim) or []):
            total_items += 1
            path = f"review.{dim}[{idx}]"
            if not isinstance(item, dict):
                checker.fail(f"{path} 不是对象")
                continue
            verdict = item.get("verdict")
            if verdict not in VERDICT_ENUM:
                checker.fail(f"{path} verdict 非法: {verdict!r}")
            if not item.get("id") or not item.get("claim"):
                checker.warn(f"{path} 缺少 id 或 claim")
            evidence = item.get("evidence") or []
            if verdict in EVIDENCE_REQUIRED_VERDICTS:
                if not evidence:
                    checker.fail(f"{path} verdict={verdict} 但无 evidence")
                for ei, ev in enumerate(evidence):
                    if not isinstance(ev, dict):
                        checker.fail(f"{path}.evidence[{ei}] 不是对象")
                        continue
                    if not (ev.get("locator") and ev.get("quote")):
                        checker.fail(f"{path}.evidence[{ei}] 缺 locator 或逐字 quote")
                    kind = ev.get("kind")
                    if kind == "derived" and not ev.get("formula"):
                        checker.fail(f"{path}.evidence[{ei}] kind=derived 但无 formula（算式）")
                    if ev.get("numeric") is not None and kind in ("observed", "derived"):
                        if not (ev.get("locator") or ev.get("formula")):
                            checker.fail(f"{path}.evidence[{ei}] 数值无 locator/formula 支撑，疑似编造")
            if verdict == "conditional" and not item.get("conditions"):
                checker.fail(f"{path} verdict=conditional 但 conditions 为空（条件不可执行）")
            if dim == "equipment_materials" and item.get("resource_state") is not None \
                    and item["resource_state"] not in RESOURCE_STATE_ENUM:
                checker.fail(f"{path} resource_state 非法: {item['resource_state']!r}")
    checker.ok(f"审查项总数: {total_items}")

    # 7) limitations 显式化（本版重点）
    limitations = result.get("limitations") or {}
    lim_count = sum(len(limitations.get(k) or []) for k in LIMITATION_KINDS)
    uncertainty = result.get("uncertainty") or []
    pending = result.get("pending_human") or []
    if status == "partial" and lim_count == 0 and not uncertainty and not pending:
        checker.fail("status=partial 但 limitations/uncertainty/pending_human 全为空，未显式标注限制")
    elif lim_count:
        checker.ok(f"limitations 登记 {lim_count} 项")
    if status == "ok" and (lim_count or uncertainty or pending):
        checker.warn("status=ok 但仍留有 limitations/uncertainty/pending_human，请确认是否应收口")

    # 8) change_log
    if not result.get("change_log"):
        checker.fail("change_log 为空（需记录增量改进与版本）")
    else:
        checker.ok(f"change_log {len(result['change_log'])} 条")

    # 9) 可编辑复核报告
    report_path = os.path.join(DEFAULT_OUTPUT, "review_report.md")
    if status == "ok" and not os.path.exists(report_path):
        checker.warn("status=ok 但缺少 /app/output/review_report.md（建议补人工复核报告）")


def main(argv=None):
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="实验方案可行性审查 - 交付前自检")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    result, err = read_json(os.path.join(args.output, "result.json"))
    if err or result is None:
        print(f"FAIL result.json 不可读: {err}")
        return 1
    if not isinstance(result, dict):
        print("FAIL result.json 顶层不是对象")
        return 1

    checker = Checker()
    check(result, checker)

    for m in checker.passes:
        print(f"PASS {m}")
    for m in checker.warns:
        print(f"WARN {m}")
    for m in checker.fails:
        print(f"FAIL {m}")

    print("-" * 40)
    print(f"PASS={len(checker.passes)} WARN={len(checker.warns)} FAIL={len(checker.fails)}")
    return 1 if checker.fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
