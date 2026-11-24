#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 审查 schema、确定性算术与方案比较函数库（review.py）

职责：提供七类约束的判定口径、状态枚举、算术校验（时间/参数/样本/成本）、
多版方案排序与证据项结构校验。所有函数为纯函数：相同输入相同输出。
可由 main.py 引用，也可独立运行 `--self-test` / `--print-schema`。
仅依赖 Python 标准库；Python >= 3.8。
"""

from __future__ import annotations

import json
import sys

SCHEMA_VERSION = "2.0.0"

# 七类约束（顺序固定，评分按此口径）
SEVEN_DIMENSIONS = [
    "assumptions_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_exceptions",
    "ethics_safety",
]

# 审查项判定枚举
VERDICTS = ["ok", "conditional", "blocked", "unconfirmed", "missing", "conflict"]

# 资源维度状态枚举（仅 equipment_materials 使用）
RESOURCE_STATES = ["已具备", "可外协", "需采购", "不具备", "未知"]

# 整体状态枚举
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]

# 整体结论枚举
CONCLUSION_VERDICTS = ["可行", "有条件可行", "不可行", "无法判定"]

REVIEW_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "dimensions": SEVEN_DIMENSIONS,
    "verdicts": VERDICTS,
    "resource_states": RESOURCE_STATES,
    "statuses": STATUSES,
    "conclusion_verdicts": CONCLUSION_VERDICTS,
    "review_item": {
        "id": "str，如 eq-1 / time-2 / param-3",
        "dimension": "其一：" + "|".join(SEVEN_DIMENSIONS),
        "claim": "该条审查结论的一句话",
        "verdict": "其一：" + "|".join(VERDICTS),
        "resource_state": "仅设备材料维度：已具备|可外协|需采购|不具备|未知",
        "conditions": "conditional 时必填的逐条可执行条件",
        "evidence": [{"locator": "材料定位", "quote": "逐字原文", "kind": "plan|contract|sop|manual|ethics|other"}],
        "confidence": "high|medium|low",
        "needs_expert": "bool",
        "notes": [],
    },
}


def new_review_item(item_id, dimension, claim, verdict, evidence=None,
                    resource_state=None, conditions=None, confidence="medium",
                    needs_expert=False, notes=None):
    """构造一条结构合法的审查项。evidence 元素须含 locator + quote。"""
    return {
        "id": item_id,
        "dimension": dimension,
        "claim": claim,
        "verdict": verdict,
        "resource_state": resource_state,
        "conditions": conditions or [],
        "evidence": evidence or [],
        "confidence": confidence,
        "needs_expert": bool(needs_expert),
        "notes": notes or [],
    }


def _num(value):
    """宽松转数值；失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def sum_time(steps, hours_key="hours", label_key="label", unit="小时"):
    """确定性时间累加：返回 {total, unit, steps, formula}。steps 每项含 label 与数值时长。"""
    parts = []
    total = 0.0
    formula_parts = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        value = _num(step.get(hours_key))
        if value is None:
            parts.append({"label": step.get(label_key, "?"), "hours": None, "skip_reason": "missing"})
            continue
        total += value
        parts.append({"label": step.get(label_key, ""), "hours": value})
        formula_parts.append(str(value))
    formula = " + ".join(formula_parts) if formula_parts else "0"
    return {
        "total": round(total, 3),
        "unit": unit,
        "steps": parts,
        "formula": "%s = %s" % (formula, round(total, 3)),
    }


def check_range(value, low, high, inclusive_low=True, inclusive_high=True):
    """参数范围校验：返回 {value, low, high, verdict, reason}。"""
    v = _num(value)
    lo = _num(low)
    hi = _num(high)
    if v is None or lo is None or hi is None:
        return {"value": value, "low": low, "high": high,
                "verdict": "missing", "reason": "数值或边界缺失，无法判定"}
    ok_low = v > lo or (inclusive_low and v == lo)
    ok_high = v < hi or (inclusive_high and v == hi)
    verdict = "ok" if (ok_low and ok_high) else "conflict"
    return {"value": v, "low": lo, "high": hi, "verdict": verdict,
            "reason": "在范围内" if verdict == "ok" else "超出边界"}


def check_sample_size(required, available):
    """样本量匹配校验：返回 {required, available, ratio, verdict, reason}。"""
    req = _num(required)
    avail = _num(available)
    if req is None or avail is None:
        return {"required": required, "available": available,
                "verdict": "missing", "reason": "样本量缺失"}
    if req <= 0:
        return {"required": req, "available": avail, "ratio": None,
                "verdict": "conflict", "reason": "需求样本量须为正"}
    ratio = avail / req
    verdict = "ok" if ratio >= 1.0 else "blocked"
    return {"required": req, "available": avail, "ratio": round(ratio, 4),
            "verdict": verdict,
            "reason": "满足需求" if verdict == "ok" else "样本不足"}


def sum_cost(items, cost_key="cost", label_key="label", currency=None):
    """成本累加：返回 {total, currency, items, formula}。"""
    parts = []
    total = 0.0
    formula_parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _num(item.get(cost_key))
        if value is None:
            parts.append({"label": item.get(label_key, "?"), "cost": None, "skip_reason": "missing"})
            continue
        total += value
        parts.append({"label": item.get(label_key, ""), "cost": value})
        formula_parts.append(str(value))
    formula = " + ".join(formula_parts) if formula_parts else "0"
    return {"total": round(total, 2), "currency": currency, "items": parts,
            "formula": "%s = %s" % (formula, round(total, 2))}


def score_plan(plan_review_items):
    """确定性方案评分：按阻断/条件/缺失项计数，返回 {score, blocked, conditional, missing, total}。
    评分口径：blocked 每项 -3，conditional -1，missing -1，ok 0，conflict -2。分数越高越可行。"""
    weights = {"ok": 0, "conditional": -1, "missing": -1,
               "blocked": -3, "conflict": -2, "unconfirmed": -1}
    counts = {v: 0 for v in VERDICTS}
    score = 0
    for item in plan_review_items:
        if not isinstance(item, dict):
            continue
        v = item.get("verdict", "missing")
        if v not in weights:
            v = "missing"
        counts[v] += 1
        score += weights[v]
    return {"score": score, "counts": counts,
            "blocked": counts["blocked"], "conditional": counts["conditional"],
            "missing": counts["missing"], "total": sum(counts.values())}


def compare_plans(plans):
    """多版方案排序：输入 [{plan_id, review_items:[...]}]，输出按 score 降序。
    同分时按 plan_id 字典序，保证确定性。"""
    scored = []
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        result = score_plan(plan.get("review_items", []))
        entry = {"plan_id": plan.get("plan_id"), "score": result["score"],
                 "counts": result["counts"], "blocked": result["blocked"],
                 "conditional": result["conditional"], "missing": result["missing"]}
        scored.append(entry)
    scored.sort(key=lambda x: (-x["score"], str(x["plan_id"])))
    for i, entry in enumerate(scored):
        entry["rank"] = i + 1
    return scored


def validate_review_item(item):
    """校验单条审查项，返回问题列表。"""
    problems = []
    if not isinstance(item, dict):
        return ["review_item_not_object"]
    if item.get("dimension") not in SEVEN_DIMENSIONS:
        problems.append("bad_dimension: %s" % item.get("dimension"))
    verdict = item.get("verdict")
    if verdict not in VERDICTS:
        problems.append("bad_verdict: %s" % verdict)
    if verdict in ("ok", "conditional", "blocked", "conflict"):
        ev = item.get("evidence")
        if not isinstance(ev, list) or not ev:
            problems.append("missing_evidence_for_%s" % verdict)
        else:
            for e in ev:
                if not isinstance(e, dict) or not e.get("locator") or not e.get("quote"):
                    problems.append("evidence_missing_locator_or_quote")
    if verdict == "conditional":
        cond = item.get("conditions")
        if not isinstance(cond, list) or not cond:
            problems.append("conditional_without_conditions")
    if item.get("dimension") == "equipment_materials" and verdict not in ("missing",):
        rs = item.get("resource_state")
        if rs not in RESOURCE_STATES:
            problems.append("equipment_item_without_resource_state: %s" % rs)
    return problems


def resolve_status(review):
    """由七类审查结果解析整体 status。
    ok：全部维度 ok/conditional 且无 missing；partial：有缺失但已有证据化判断；
    insufficient_evidence：任何维度为空或全 missing。"""
    if not isinstance(review, dict):
        return "insufficient_evidence"
    verdicts = []
    for dim in SEVEN_DIMENSIONS:
        items = review.get(dim)
        if not isinstance(items, list) or not items:
            return "insufficient_evidence"
        verdicts.extend(i.get("verdict") for i in items if isinstance(i, dict))
    if not verdicts:
        return "insufficient_evidence"
    if "blocked" in verdicts or "conflict" in verdicts:
        return "partial"
    if "missing" in verdicts:
        return "partial"
    return "ok"


def _self_test():
    """内置确定性自测，全部断言通过返回 0。"""
    checks = []
    t = sum_time([{"label": "A", "hours": 1.5}, {"label": "B", "hours": 2.25}])
    checks.append(("sum_time_total", t["total"] == 3.75))
    checks.append(("sum_time_formula", t["formula"].startswith("1.5 + 2.25")))
    r = check_range(37, 36, 38)
    checks.append(("range_ok", r["verdict"] == "ok"))
    r2 = check_range(39, 36, 38)
    checks.append(("range_conflict", r2["verdict"] == "conflict"))
    s = check_sample_size(10, 8)
    checks.append(("sample_blocked", s["verdict"] == "blocked"))
    s2 = check_sample_size(10, 10)
    checks.append(("sample_ok", s2["verdict"] == "ok"))
    c = sum_cost([{"label": "x", "cost": 100}, {"label": "y", "cost": 25.5}])
    checks.append(("cost_total", c["total"] == 125.5))
    plans = [
        {"plan_id": "p1", "review_items": [new_review_item("a", "controls", "c", "blocked")]},
        {"plan_id": "p2", "review_items": [new_review_item("b", "controls", "c", "ok")]},
    ]
    ranks = compare_plans(plans)
    checks.append(("compare_rank", ranks[0]["plan_id"] == "p2"))
    bad = validate_review_item(new_review_item("x", "controls", "c", "conditional"))
    checks.append(("conditional_needs_conditions", "conditional_without_conditions" in bad))
    status = resolve_status({"assumptions_variables": [{"verdict": "ok"}],
                             "controls": [{"verdict": "ok"}],
                             "equipment_materials": [{"verdict": "ok"}],
                             "time_cost": [{"verdict": "ok"}],
                             "parameter_ranges": [{"verdict": "ok"}],
                             "risks_exceptions": [{"verdict": "ok"}],
                             "ethics_safety": [{"verdict": "ok"}]})
    checks.append(("resolve_ok", status == "ok"))
    failed = [name for name, ok in checks if not ok]
    print(json.dumps({"self_test": "pass" if not failed else "fail",
                      "checks": len(checks), "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--print-schema" in argv:
        print(json.dumps(REVIEW_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    if "--self-test" in argv:
        return _self_test()
    print(json.dumps({"usage": "review.py --print-schema | --self-test"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
