#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 判定口径与确定性算术校验（review.py）。

提供：七类维度、verdict/resource_state/confidence 枚举、时间归一化与累加、
参数范围判定、样本量/成本求和。纯标准库、纯函数、确定性；供 Agent 审查引用，
也供 selfcheck.py 复用。单独运行时打印 schema 与一个算术校验示例。
"""

from __future__ import annotations

import argparse
import json
import re

# 七类约束（顺序即评分要求顺序）
DIMENSIONS = [
    "assumptions_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_exceptions",
    "ethics_safety",
]

VERDICTS = ["ok", "conditional", "blocked", "unconfirmed", "missing", "conflict"]
RESOURCE_STATES = ["已具备", "可外协", "需采购", "不具备", "未知"]
CONFIDENCES = ["high", "medium", "low"]

# 时间单位 → 小时（工作日按 8h、周按 40h、月按 160h 折算）
_UNIT_HOURS = {
    "min": 1 / 60.0, "minute": 1 / 60.0, "m": 1 / 60.0,
    "分钟": 1 / 60.0, "分": 1 / 60.0,
    "h": 1.0, "hr": 1.0, "hour": 1.0, "hours": 1.0,
    "小时": 1.0, "时": 1.0,
    "d": 8.0, "day": 8.0, "days": 8.0, "天": 8.0, "工作日": 8.0,
    "w": 40.0, "week": 40.0, "weeks": 40.0, "周": 40.0, "星期": 40.0,
    "month": 160.0, "months": 160.0, "月": 160.0,
}
_NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z\u4e00-\u9fff]+)")


def parse_duration(text):
    """把 '30分钟'/'2小时'/'3天'/'2周'/'1.5月' 等解析为小时数(float)；无法解析返回 None。"""
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().lower()
    total = 0.0
    matched = False
    for num, unit in _NUM_RE.findall(s):
        factor = _UNIT_HOURS.get(unit)
        if factor is None:
            continue
        total += float(num) * factor
        matched = True
    if matched and total >= 0.0:
        return total
    # 单个纯数字视为已以小时为单位
    try:
        return float(s)
    except ValueError:
        return None


def sum_time(items, duration_key="duration"):
    """items: [{label, duration, ...}] → 累加结果。仅累加可解析项，无法解析的写入 notes。"""
    breakdown = []
    total = 0.0
    unparsed = []
    for it in items:
        label = it.get("label", "") if isinstance(it, dict) else str(it)
        raw = it.get(duration_key) if isinstance(it, dict) else it
        hours = parse_duration(raw)
        breakdown.append({"label": label, "duration": raw, "hours": hours})
        if hours is None:
            unparsed.append(label or str(it))
        else:
            total += hours
    return {
        "total_hours": round(total, 3),
        "breakdown": breakdown,
        "unparsed": unparsed,
        "formula": " + ".join(
            f"{b['hours']}h" for b in breakdown if b["hours"] is not None
        ) + (f" = {round(total, 3)}h" if breakdown else "0h"),
    }


def check_range(value, low=None, high=None, tol=0.0):
    """数值对 [low, high]（可给容差 tol）的范围判定。verdict ∈ in/below/above/border/out/unknown。"""
    if value is None:
        return {"verdict": "unknown", "value": value, "low": low, "high": high, "tol": tol}
    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"verdict": "unknown", "value": value, "low": low, "high": high, "tol": tol}
    lo = None if low is None else float(low)
    hi = None if high is None else float(high)
    if lo is not None and v < lo - tol:
        verdict = "below"
    elif hi is not None and v > hi + tol:
        verdict = "above"
    elif (lo is not None and abs(v - lo) <= tol) or (hi is not None and abs(v - hi) <= tol):
        verdict = "border"
    elif (lo is None or v >= lo) and (hi is None or v <= hi):
        verdict = "in"
    else:
        verdict = "out"
    return {"verdict": verdict, "value": v, "low": lo, "high": hi, "tol": tol}


def sum_numeric(rows, key="count"):
    """对 rows 的指定数值键求和（如样本量、成本）。非数值按 0 处理并记入 skipped。"""
    total = 0.0
    skipped = []
    out = []
    for r in rows:
        label = r.get("label", "") if isinstance(r, dict) else str(r)
        v = r.get(key) if isinstance(r, dict) else None
        try:
            f = float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            f = None
        out.append({"label": label, key: v})
        if f is None:
            skipped.append(label or str(r))
        else:
            total += f
    return {"total": round(total, 3), "rows": out, "skipped": skipped}


def schema():
    return {
        "dimensions": DIMENSIONS,
        "verdicts": VERDICTS,
        "resource_states": RESOURCE_STATES,
        "confidences": CONFIDENCES,
        "units": sorted(_UNIT_HOURS.keys()),
    }


def demo():
    return {
        "sum_time_example": sum_time([
            {"label": "SOP 温控", "duration": "30分钟"},
            {"label": "孵育", "duration": "2小时"},
            {"label": "采购货期", "duration": "3周"},
        ]),
        "check_range_in": check_range(37.5, low=36.5, high=37.8, tol=0.0),
        "check_range_out": check_range(40.1, low=36.5, high=37.8, tol=0.0),
        "sum_numeric_example": sum_numeric(
            [{"label": "组A", "count": 12}, {"label": "组B", "count": 12}, {"label": "对照", "count": 6}],
            "count",
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="打印七类审查 schema/枚举与算术校验示例")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--demo", action="store_true", help="附带打印算术校验示例")
    args = ap.parse_args(argv)
    print(json.dumps(schema(), ensure_ascii=False, indent=2))
    if args.demo:
        print(json.dumps(demo(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())