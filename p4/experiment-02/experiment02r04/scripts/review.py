#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 确定性校验与证据/限制登记助手。

纯函数模块：所有函数确定性、无副作用，供审查阶段做真实算术与证据分类，
避免“感觉一个数”。可被 import，也可作为 CLI 打印 schema 与状态枚举。

本模块是“降级与不确定性”变体的核心：每一个关键数值结论必须显式标注其证据
类型（observed / derived / assumed / unreported）；无法标注的进入 unverifiable_claims
限制登记，绝不写编造数值。
"""

from __future__ import annotations

import argparse
import json
import sys


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

VERDICT_ENUM = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
RESOURCE_STATE_ENUM = ("已具备", "可外协", "需采购", "不具备", "未知")
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
EVIDENCE_KIND_ENUM = ("observed", "derived", "assumed", "unreported")
LIMITATION_KIND_ENUM = (
    "evidence_scope", "arithmetic_uncertainty", "source_quality",
    "missing_contract", "conflicting_sources", "unverifiable_claims",
)
SEVERITY_ENUM = ("blocker", "major", "minor")

_REVIEW_DIMS = (
    "assumptions_variables", "controls", "equipment_materials", "time_cost",
    "parameter_ranges", "risks_exceptions", "ethics_safety",
)


def _to_float(value):
    """把数值或可解析数值字符串转 float；不可解析返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def round2(x):
    return round(x + 0.0, 2)


def sum_time(items, key="hours", label_key="step"):
    """确定性时长求和。

    items: 步骤字典列表，每项在 key 处给时长（数值或可解析字符串，缺省 None）。
    返回 {total, stated, missing, detail}；缺失项计入 missing 并逐条列出，
    绝不把缺失默认为 0。
    """
    total, stated, missing, detail = 0.0, 0, 0, []
    for i, item in enumerate(items or []):
        label = item.get(label_key, f"#{i}") if isinstance(item, dict) else f"#{i}"
        value = _to_float(item.get(key)) if isinstance(item, dict) else _to_float(item)
        if value is None:
            missing += 1
            detail.append({"step": label, "value": None, "status": "missing"})
        else:
            total += value
            stated += 1
            detail.append({"step": label, "value": value, "status": "stated"})
    return {"total": round2(total), "stated": stated, "missing": missing, "detail": detail}


def check_range(value, lo, hi, inclusive=True):
    """参数范围校验：返回 in_range / out_of_range / missing，不给口头估计。"""
    v, l, h = _to_float(value), _to_float(lo), _to_float(hi)
    if v is None or l is None or h is None:
        return {"status": "missing", "value": value, "lo": lo, "hi": hi}
    if l > h:
        l, h = h, l
    if inclusive:
        in_range = l <= v <= h
    else:
        in_range = l < v < h
    return {"status": "in_range" if in_range else "out_of_range",
            "value": v, "lo": l, "hi": h, "inclusive": inclusive}


def check_sample_size(required, available, loss_rate=0.0, buffer=0.0):
    """样本量校验：可用量 = available*(1-loss_rate)，需求加 buffer 后比较。

    返回 {status: sufficient|insufficient|missing, required, available,
          usable, needed, shortfall, loss_rate, buffer}。
    """
    req, avail = _to_float(required), _to_float(available)
    loss, buf = _to_float(loss_rate) or 0.0, _to_float(buffer) or 0.0
    if req is None or avail is None:
        return {"status": "missing", "required": required, "available": available,
                "loss_rate": loss_rate, "buffer": buffer}
    usable = avail * (1.0 - loss)
    needed = req * (1.0 + buf)
    shortfall = needed - usable
    status = "sufficient" if shortfall <= 1e-9 else "insufficient"
    return {"status": status, "required": req, "available": avail,
            "usable": round2(usable), "needed": round2(needed),
            "shortfall": round2(max(0.0, shortfall)),
            "loss_rate": loss, "buffer": buf}


def classify_evidence(kind, has_locator=False, has_quote=False, has_formula=False):
    """证据置信度分级：direct/derived/assumed/unreported -> high/medium/low/insufficient。

    - observed + locator + quote = high；observed 仅 locator = medium。
    - derived + formula = medium；derived 无公式 = low。
    - assumed = low；unreported = insufficient。
    """
    if kind not in EVIDENCE_KIND_ENUM:
        raise ValueError(f"unknown evidence kind: {kind}")
    if kind == "unreported":
        return "insufficient"
    if kind == "observed":
        return "high" if (has_locator and has_quote) else ("medium" if has_locator else "low")
    if kind == "derived":
        return "medium" if has_formula else "low"
    return "low"  # assumed


def make_evidence(locator, quote, kind="observed", numeric=None, formula=None):
    """构造 evidence 字典（逐字 quote 必填，否则视为 unreported）。"""
    has_quote = bool(quote and quote.strip())
    effective_kind = kind if has_quote or kind in ("derived", "unreported", "assumed") else "unreported"
    conf = classify_evidence(
        effective_kind, has_locator=bool(locator), has_quote=has_quote,
        has_formula=bool(formula),
    )
    return {
        "locator": locator or None,
        "quote": quote if has_quote else None,
        "kind": effective_kind,
        "numeric": numeric,
        "formula": formula,
        "confidence": conf,
    }


def make_limitation(kind, claim, severity, basis, resolution,
                    affects=None, needs_expert=False, seq=0):
    """构造 limitation 记录（登记到 result.limitations.<kind>）。"""
    if kind not in LIMITATION_KIND_ENUM:
        raise ValueError(f"unknown limitation kind: {kind}")
    if severity not in SEVERITY_ENUM:
        raise ValueError(f"unknown severity: {severity}")
    return {
        "id": f"lim-{kind}-{seq}",
        "kind": kind,
        "claim": claim,
        "severity": severity,
        "affects": affects or [],
        "basis": basis,
        "resolution": resolution,
        "needs_expert": bool(needs_expert),
    }


def schema():
    return {
        "verdict_enum": list(VERDICT_ENUM),
        "resource_state_enum": list(RESOURCE_STATE_ENUM),
        "status_enum": list(STATUS_ENUM),
        "evidence_kind_enum": list(EVIDENCE_KIND_ENUM),
        "limitation_kind_enum": list(LIMITATION_KIND_ENUM),
        "severity_enum": list(SEVERITY_ENUM),
        "review_dimensions": list(_REVIEW_DIMS),
        "arithmetic_functions": {
            "sum_time": "sum_time(items, key='hours', label_key='step')",
            "check_range": "check_range(value, lo, hi, inclusive=True)",
            "check_sample_size": "check_sample_size(required, available, loss_rate=0.0, buffer=0.0)",
            "classify_evidence": "classify_evidence(kind, has_locator, has_quote, has_formula)",
        },
    }


def main(argv=None):
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="实验方案可行性审查 - 确定性校验助手")
    ap.add_argument("--print-schema", action="store_true", help="打印 schema 与状态枚举")
    args = ap.parse_args(argv)
    if args.print_schema:
        print(json.dumps(schema(), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
