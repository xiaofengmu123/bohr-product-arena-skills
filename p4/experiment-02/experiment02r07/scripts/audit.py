#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit.py — 确定性算术内核、增量写回与报告渲染。

三个子命令：
    python3 scripts/audit.py --schema                 # 打印结果 schema / 枚举 / 运算清单
    python3 scripts/audit.py --calc ops.json          # 批量执行确定性算术（JSON in, JSON out）
    python3 scripts/audit.py --merge patch.json --into result.json --note "S2 设备"
    python3 scripts/audit.py --render-report result.json -o review_report.md

设计取舍（鲁棒性优先）：
* 时间/成本/参数/样本一律用函数计算并输出算式，不允许"感觉一个数"。
* 缺值/无法解析的量记为 unconfirmed 并从总数中剔除，绝不静默当 0。
* 同输入同输出：无随机数，rounding 用 half-up；报告由 result.json 确定性渲染。
* 仅依赖 Python 标准库，Python >= 3.8。
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys

SKILL_NAME = "experiment-feasibility-audit"
SKILL_VERSION = "2.1.0"

VERDICTS = ["ok", "conditional", "blocked", "unconfirmed", "missing", "conflict"]
RESOURCE_STATES = ["已具备", "可外协", "需采购", "不具备", "未知"]
CONFIDENCE = ["high", "medium", "low"]

DIM_ORDER = [
    ("assumptions_variables", "假设与变量"),
    ("controls", "对照组"),
    ("equipment_materials", "设备与材料"),
    ("time_cost", "时间与成本"),
    ("parameter_ranges", "参数范围"),
    ("risks_exceptions", "风险与异常"),
    ("ethics_safety", "伦理与安全"),
]

UNIT_SYN = {
    "second": "s", "seconds": "s", "sec": "s", "secs": "s", "秒": "s",
    "minute": "min", "minutes": "min", "min": "min", "mins": "min",
    "分": "min", "分钟": "min",
    "hour": "h", "hours": "h", "hr": "h", "hrs": "h", "h": "h",
    "时": "h", "小时": "h",
    "day": "d", "days": "d", "d": "d", "天": "d", "日": "d",
    "week": "wk", "weeks": "wk", "wk": "wk", "wks": "wk", "周": "wk",
    "星期": "wk",
    "month": "mo", "months": "mo", "mo": "mo", "mos": "mo", "月": "mo",
    "year": "y", "years": "y", "yr": "y", "yrs": "y", "年": "y",
    "workday": "wd", "working day": "wd", "workdays": "wd", "工作日": "wd",
}
UNIT_MIN = {"s": 1 / 60, "min": 1, "h": 60, "d": 1440, "wk": 10080,
            "mo": 43200, "y": 525600, "wd": 480}
# 显式声明"计划用近似值"，避免把 30 天/月 当精确值。
APPROX_UNITS = {"mo", "y", "wd"}

NUM_PAT = re.compile(r"[-+]?\d[\d,\s]*(?:\.\d+)?")
RANGE_PAT = re.compile(r"^\s*([\d.,]+)\s*(?:-|–|—|~|to|至)\s*([\d.,]+)")
CUR_MAP = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "CNY", "￥": "CNY"}


def fmt(x):
    if x is None:
        return "?"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    x = float(x)
    if abs(x) < 1e-9:
        return "0"
    return (f"{x:.6f}".rstrip("0").rstrip("."))


def human_minutes(m):
    if m is None:
        return "unconfirmed"
    m = float(m)
    if m >= 1440:
        return f"{fmt(m / 1440)} d（{fmt(m)} min）"
    if m >= 60:
        return f"{fmt(m / 60)} h（{fmt(m)} min）"
    return f"{fmt(m)} min"


def find_unit(s):
    lower = str(s).lower()
    for tok in sorted(UNIT_SYN, key=lambda t: -len(t)):
        if tok in lower:
            return UNIT_SYN[tok]
    return None


def parse_qty(value, unit=None):
    """把 3 | '3 days' | '~90 min' | '1,200' | '€4.5k' 解析为数量结构。"""
    out = {"raw": value, "value": None, "unit": unit, "unit_canon": None,
           "approx": False, "range": None, "currency": None, "ok": False}
    if value is None or isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.update(value=float(value), unit_canon=unit, ok=True)
        return out
    s = str(value).strip()
    if not s:
        return out
    if any(ch in s for ch in "~≈约大约差不多"):
        out["approx"] = True
    for ch, code in CUR_MAP.items():
        if ch in s:
            out["currency"] = code
            break
    rm = RANGE_PAT.match(s)
    if rm:
        try:
            out["range"] = [float(rm.group(1).replace(",", "")),
                            float(rm.group(2).replace(",", ""))]
        except ValueError:
            pass
    m = NUM_PAT.search(s)
    if not m:
        return out
    num = float(m.group(0).replace(",", "").replace(" ", ""))
    if re.search(r"\d\s*[kK]\b", s):
        num *= 1000
    if "万" in s:
        num *= 10000
    out["value"] = num
    canon = find_unit(s) or unit
    out["unit_canon"] = canon
    out["unit"] = unit or canon
    out["ok"] = True
    return out


def to_minutes(q, default_unit="min"):
    if q is None or not q.get("ok") or q.get("value") is None:
        return None, []
    warns = []
    if q.get("range"):
        v = q["range"][1]
        warns.append("范围取上界作计划值")
    else:
        v = q["value"]
    unit = q.get("unit_canon") or default_unit
    if unit not in UNIT_MIN:
        return None, [f"未知单位 {unit}"]
    if unit in APPROX_UNITS:
        warns.append(f"单位 {unit} 使用近似换算（{UNIT_MIN[unit]} min/单位）")
    return v * UNIT_MIN[unit], warns


def norm_ppf(p):
    """标准正态分位点（Acklam 近似，精度 ~1e-9）。"""
    if not 0 < p < 1:
        raise ValueError("p 必须位于 (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ok(status, **kw):
    d = {"status": status, "ok": status == "computed"}
    d.update(kw)
    return d


def _err(msg):
    return {"status": "error", "ok": False, "error": str(msg)}


# --------------------------------------------------------------------------- #
# 运算处理器
# --------------------------------------------------------------------------- #

def op_sum_time(p):
    items = p.get("items") or []
    if not items:
        return _err("items 为空")
    rows, excluded, serial, parallel = [], [], 0.0, 0.0
    warn_all = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = it.get("label", it.get("name", "?"))
        q = parse_qty(it.get("value", it.get("duration", it.get("time"))),
                      it.get("unit"))
        rep = _fnum(it.get("repeats", 1))
        if rep is None or rep < 0:
            rep = 1.0
            warn_all.append(f"{label}: repeats 非法，按 1 计")
        minutes, warns = to_minutes(q, it.get("unit") or "min")
        if minutes is None:
            excluded.append({"label": label, "raw": q["raw"],
                             "reason": "无法解析时长" + ("；" + ";".join(warns) if warns else "")})
            warn_all.append(f"{label} 被排除（{q['raw']}）")
            continue
        mode = str(it.get("mode", "serial")).lower()
        if mode == "parallel":
            parallel = max(parallel, minutes * rep)
        else:
            serial += minutes * rep
        rows.append({"label": label, "minutes": minutes, "repeats": rep,
                     "mode": mode, "total": minutes * rep, "approx": q["approx"],
                     "warnings": warns})
    total = serial + parallel
    formula = " + ".join(f"{fmt(r['total'])}" for r in rows) or "0"
    formula = f"Σ(时长×重复) + 并行最大 = {formula} = {fmt(total)} min"
    status = "computed" if not excluded else "unconfirmed"
    return _ok(status, value=total, unit="min", human=human_minutes(total),
               serial_min=serial, parallel_min=parallel, rows=rows,
               excluded=excluded, formula=formula, warnings=warn_all)


def op_timeline(p):
    tasks = p.get("tasks") or []
    if not tasks:
        return _err("tasks 为空")
    nodes = {}
    for t in tasks:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        dur, dw = to_minutes(parse_qty(t.get("duration", t.get("value")),
                                       t.get("unit")), t.get("unit") or "min")
        lead, lw = to_minutes(parse_qty(t.get("lead"), t.get("lead_unit")),
                              t.get("lead_unit") or "min")
        wait, ww = to_minutes(parse_qty(t.get("wait"), t.get("wait_unit")),
                              t.get("wait_unit") or "min")
        if dur is None:
            return _err(f"任务 {t['id']} 时长不可解析")
        nodes[t["id"]] = {"id": t["id"], "label": t.get("label", t["id"]),
                          "dur": dur, "lead": lead or 0, "wait": wait or 0,
                          "deps": t.get("deps", t.get("depends_on", [])) or []}
    indeg = {k: 0 for k in nodes}
    for k, v in nodes.items():
        for d in v["deps"]:
            if d in nodes:
                indeg[k] += 1
    start, finish, order = {}, {}, []
    ready = sorted([k for k in nodes if indeg[k] == 0])
    for k in ready:
        nodes[k]["start"] = nodes[k]["lead"]
    while ready:
        k = ready.pop(0)
        order.append(k)
        for d in nodes[k]["deps"]:
            if d in nodes:
                ns = max(nodes[k].get("start", nodes[k]["lead"]),
                         finish.get(d, 0) + nodes[k]["wait"])
                nodes[k]["start"] = ns
        nodes[k]["start"] = max(nodes[k].get("start", nodes[k]["lead"]),
                                nodes[k]["lead"])
        for d in nodes[k]["deps"]:
            if d in nodes:
                nodes[k]["start"] = max(nodes[k]["start"],
                                        finish.get(d, 0) + nodes[k]["wait"])
        finish[k] = nodes[k]["start"] + nodes[k]["dur"]
        for m in nodes:
            if m != k and k in nodes[m]["deps"]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
    if len(order) != len(nodes):
        cyclic = sorted(set(nodes) - set(order))
        return _err(f"依赖存在环：{cyclic}")
    makespan = max(finish.values())
    crit = [k for k in order if finish[k] == makespan]
    slack = None
    deadline = p.get("deadline_min")
    if deadline is not None and _fnum(deadline) is not None:
        slack = float(deadline) - makespan
    rows = [{"id": k, "label": nodes[k]["label"], "start_min": nodes[k]["start"],
             "finish_min": finish[k], "dur_min": nodes[k]["dur"],
             "deps": nodes[k]["deps"]} for k in order]
    formula = f"makespan = max(最早完成) = {fmt(makespan)} min"
    if slack is not None:
        formula += f"；相对截止 {fmt(float(deadline))} min 的松弛 = {fmt(slack)} min"
    status = "computed"
    return _ok(status, value=makespan, unit="min", human=human_minutes(makespan),
               slack_min=slack, critical_path=crit, rows=rows, formula=formula,
               warnings=[])


def op_cost(p):
    items = p.get("items") or []
    fees = p.get("fees") or []
    if not items:
        return _err("items 为空")
    totals, rows, excluded = {}, [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        price = _fnum(it.get("unit_price", it.get("price", it.get("value"))))
        qty = _fnum(it.get("qty", 1))
        if price is None:
            excluded.append({"label": it.get("label", "?"),
                             "reason": "单价缺失，未计入"})
            continue
        if qty is None or qty < 0:
            qty = 1.0
        cur = it.get("currency") or p.get("currency") or "CNY"
        subtotal = price * qty
        totals[cur] = totals.get(cur, 0.0) + subtotal
        rows.append({"label": it.get("label", "?"), "currency": cur,
                     "unit_price": price, "qty": qty, "subtotal": subtotal})
    for f in fees:
        cur = f.get("currency") or p.get("currency") or "CNY"
        kind = str(f.get("kind", "fixed")).lower()
        val = _fnum(f.get("value"))
        if val is None:
            continue
        if kind == "percent":
            fee = totals.get(cur, 0.0) * val / 100.0
        else:
            fee = val
        totals[cur] = totals.get(cur, 0.0) + fee
        rows.append({"label": f.get("label", "fee"), "currency": cur,
                     "kind": kind, "value": val, "subtotal": fee,
                     "is_fee": True})
    budget = _fnum(p.get("budget"))
    over = None
    if budget is not None:
        total_all = sum(totals.values())
        over = total_all - budget
    formula = " + ".join(f"{fmt(r['subtotal'])} {r['currency']}" for r in rows)
    status = "computed" if not excluded else "unconfirmed"
    return _ok(status, totals=totals, budget=budget, over_budget=over,
               rows=rows, excluded=excluded, formula=formula,
               warnings=[] if not excluded else ["部分条目因缺价被排除，总额为部分值"])


def _num_or_none(x):
    return _fnum(x)


def op_range(p):
    name = p.get("name", "value")
    value = _num_or_none(p.get("value"))
    domain = p.get("domain")
    if value is None:
        return _ok("unconfirmed", verdict="unknown", name=name, value=None,
                   formula="无值可判", warnings=["value 缺失"])
    if isinstance(domain, dict) and ("min" in domain or "max" in domain):
        lo, hi = _num_or_none(domain.get("min")), _num_or_none(domain.get("max"))
        step = _num_or_none(domain.get("step"))
        inside = (lo is None or value >= lo) and (hi is None or value <= hi)
        grid_ok = True
        if step and lo is not None:
            k = (value - lo) / step
            grid_ok = abs(k - round(k)) <= 1e-6
        if not inside:
            margin = None
            if hi is not None and value > hi:
                margin = value - hi
            elif lo is not None and value < lo:
                margin = lo - value
            verdict = "out_of_domain"
        elif not grid_ok:
            verdict = "off_grid"
        else:
            verdict = "in_domain"
        formula = f"{fmt(value)} vs [{fmt(lo) if lo is not None else '-∞'}, {fmt(hi) if hi is not None else '+∞'}]"
        if step:
            formula += f" step={fmt(step)}"
        formula += f" → {verdict}"
        return _ok("computed", name=name, value=value, verdict=verdict,
                   margin=margin if not inside else None, formula=formula,
                   warnings=[])
    if isinstance(domain, dict) and isinstance(domain.get("values"), list):
        vals = domain["values"]
        matched = any(abs(value - _fnum(v)) <= 1e-9 * max(1, abs(value))
                      for v in vals if _fnum(v) is not None)
        verdict = "in_domain" if matched else "out_of_domain"
        return _ok("computed", name=name, value=value, verdict=verdict,
                   formula=f"{fmt(value)} ∈ {vals} → {verdict}", warnings=[])
    if isinstance(domain, list) and domain:
        nums = [_fnum(v) for v in domain if _fnum(v) is not None]
        if nums:
            matched = any(abs(value - v) <= 1e-9 * max(1, abs(value)) for v in nums)
            verdict = "in_domain" if matched else "out_of_domain"
            return _ok("computed", name=name, value=value, verdict=verdict,
                       formula=f"{fmt(value)} ∈ {nums} → {verdict}", warnings=[])
    return _ok("unconfirmed", name=name, value=value, verdict="unknown",
               formula="domain 结构未知，无法判定", warnings=["domain 结构无法识别"])


def op_weights(p):
    items = p.get("weights") or []
    vals = []
    for w in items:
        v = _num_or_none(w.get("weight"))
        if v is not None and v >= 0:
            vals.append({"key": w.get("key", "?"), "weight": v})
    tot = sum(v["weight"] for v in vals)
    if tot <= 0:
        return _err("权重之和为 0")
    norm = [{"key": v["key"], "weight": v["weight"], "normalized": v["weight"] / tot}
            for v in vals]
    balanced = abs(tot - 1.0) <= 1e-6
    return _ok("computed", total=round(tot, 9), normalized=norm,
               balanced=balanced, formula="权重归一化：w/Σw",
               warnings=[] if balanced else [f"权重之和 {fmt(tot)} ≠ 1，已归一化"])


def op_score(p):
    plans = p.get("plans") or []
    criteria = p.get("criteria") or []
    if not plans or not criteria:
        return _err("plans/criteria 缺失")
    weights = []
    for c in criteria:
        w = _num_or_none(c.get("weight", 0))
        weights.append(w if w is not None else 0)
    tot = sum(weights)
    if tot <= 0:
        return _err("权重之和为 0")
    wnorm = [w / tot for w in weights]
    norm_table = {}
    for c in criteria:
        key = c["key"]
        vals = {}
        for pl in plans:
            v = _num_or_none((pl.get("scores") or {}).get(key))
            vals[pl.get("id", "?")] = v
        nums = [v for v in vals.values() if v is not None]
        lo, hi = (min(nums), max(nums)) if nums else (0, 0)
        direction = c.get("direction", "max")
        nv = {}
        for pid, v in vals.items():
            if v is None:
                nv[pid] = None
            elif hi - lo < 1e-12:
                nv[pid] = 1.0
            else:
                nv[pid] = (v - lo) / (hi - lo) if direction == "max" else (hi - v) / (hi - lo)
        norm_table[key] = {"lo": lo, "hi": hi, "direction": direction, "norm": nv}
    totals, formulas, excluded = {}, {}, []
    for pl in plans:
        pid = pl.get("id", "?")
        if any(norm_table[c["key"]]["norm"].get(pid) is None for c in criteria):
            excluded.append(pid)
            continue
        total = sum(wnorm[j] * norm_table[c["key"]]["norm"][pid]
                    for j, c in enumerate(criteria))
        totals[pid] = total
        terms = "+".join(f"{fmt(wnorm[j])}×{fmt(norm_table[c['key']]['norm'][pid])}"
                         for j, c in enumerate(criteria))
        formulas[pid] = f"{pid}: {terms} = {fmt(total)}"
    if not totals:
        return _ok("unconfirmed", totals={}, ranking=[], formula="无完整评分",
                   warnings=["所有方案均缺评分"], excluded=excluded, ties=[])
    ranking = sorted(totals, key=lambda i: -totals[i])
    ties = []
    for i in range(len(ranking) - 1):
        if abs(totals[ranking[i]] - totals[ranking[i + 1]]) <= 1e-9:
            ties.append([ranking[i], ranking[i + 1]])
    return _ok("computed", totals=totals, ranking=ranking, formulas=formulas,
               weights_normalized=wnorm, excluded=excluded, ties=ties,
               formula="；".join(formulas.values()))


def op_sample_check(p):
    avail = _num_or_none(p.get("available"))
    req = _num_or_none(p.get("required"))
    loss = _num_or_none(p.get("loss_rate", 0))
    if loss is None:
        loss = 0.0
    if avail is None:
        return _ok("unconfirmed", verdict="unknown", formula="可用样本量缺失",
                   warnings=["available 缺失"])
    usable = math.floor(avail * (1 - loss))
    if req is None:
        return _ok("unconfirmed", available=avail, usable=usable,
                   formula=f"可用×保留率 = {fmt(avail)}×(1-{fmt(loss)}) = {usable}；需求未给",
                   warnings=["required 缺失，无法判够不够"])
    surplus = usable - req
    verdict = "ok" if surplus >= 0 else "short"
    return _ok("computed", available=avail, usable=usable, required=req,
               surplus=surplus, verdict=verdict,
               formula=f"floor({fmt(avail)}×(1-{fmt(loss)})) = {usable}；需 {fmt(req)}；差额 {fmt(surplus)}",
               warnings=[])


def op_sample_size(p):
    design = str(p.get("design", "means")).lower()
    alpha = _num_or_none(p.get("alpha", 0.05))
    power = _num_or_none(p.get("power", 0.8))
    sides = int(p.get("sides", 2))
    try:
        z = norm_ppf(1 - alpha / sides) + norm_ppf(power)
    except ValueError as exc:
        return _err(str(exc))
    if design in ("means", "mean", "连续"):
        delta = _num_or_none(p.get("delta"))
        sd = _num_or_none(p.get("sd"))
        alloc = _num_or_none(p.get("allocation", 1))
        if delta is None or sd is None or alloc is None:
            return _err("均值比较需 delta/sd/allocation")
        n1 = math.ceil(2 * (z ** 2) * (sd ** 2) / (delta ** 2))
        n2 = math.ceil(alloc * n1)
        return _ok("computed", n_per_group=n1, n_group2=n2, n_total=n1 + n2,
                   assumption="正态近似，两组方差相等",
                   formula=f"n1 = ceil(2(zα/2+zβ)²σ²/Δ²) = ceil(2×{fmt(z)}²×{fmt(sd)}²/{fmt(delta)}²) = {n1}；n2={n2}",
                   warnings=[])
    if design in ("proportions", "proportion", "率"):
        p1 = _num_or_none(p.get("p1"))
        p2 = _num_or_none(p.get("p2"))
        if p1 is None or p2 is None:
            return _err("率比较需 p1/p2")
        pbar = (p1 + p2) / 2
        n = math.ceil(2 * pbar * (1 - pbar) * (z ** 2) / ((p1 - p2) ** 2))
        return _ok("computed", n_per_group=n, n_total=2 * n,
                   assumption="正态近似（arcsin 未用）",
                   formula=f"n = ceil(2·p̄(1-p̄)(zα/2+zβ)²/(p1-p2)²) = {n}",
                   warnings=["率比较建议由统计师复核检验选择"])
    return _err(f"未知 design {design}")


def op_tolerance(p):
    nominal = _num_or_none(p.get("nominal"))
    actual = _num_or_none(p.get("actual"))
    tol = _num_or_none(p.get("tol_pct"))
    if nominal is None or actual is None or tol is None:
        return _err("nominal/actual/tol_pct 缺失")
    if nominal == 0:
        return _err("nominal 不能为 0")
    dev = (actual - nominal) / nominal * 100
    within = abs(dev) <= tol
    return _ok("computed", deviation_pct=round(dev, 6), within=within,
               verdict="within" if within else "exceeded",
               formula=f"({fmt(actual)}-{fmt(nominal)})/{fmt(nominal)}×100% = {fmt(dev)}%，容差 ±{fmt(tol)}% → {'满足' if within else '超差'}",
               warnings=[])


def op_gate(p):
    value = _num_or_none(p.get("value"))
    threshold = _num_or_none(p.get("threshold"))
    op = str(p.get("operator", ">="))
    if value is None or threshold is None:
        return _err("value/threshold 缺失")
    ops = {">=": value >= threshold, "<=": value <= threshold, ">": value > threshold,
           "<": value < threshold, "==": value == threshold, "=": value == threshold}
    if op not in ops:
        return _err(f"未知 operator {op}")
    passed = ops[op]
    margin = value - threshold
    return _ok("computed", passed=passed, margin=margin, verdict="pass" if passed else "fail",
               formula=f"{fmt(value)} {op} {fmt(threshold)} → {'通过' if passed else '不通过'}（margin={fmt(margin)}）",
               warnings=[])


def op_lead_gate(p):
    avail = _num_or_none(p.get("available_days"))
    lead = _num_or_none(p.get("lead_days", 0))
    dur = _num_or_none(p.get("duration_days", 0))
    ta = _num_or_none(p.get("turnaround_days", 0))
    if avail is None:
        return _err("available_days 缺失")
    lead, dur, ta = lead or 0, dur or 0, ta or 0
    need = lead + dur + ta
    slack = avail - need
    latest = avail - need
    verdict = "feasible" if slack >= 0 else "infeasible"
    return _ok("computed", needed_days=need, slack_days=slack,
               latest_start_day=latest, verdict=verdict,
               formula=f"所需 {fmt(lead)}+{fmt(dur)}+{fmt(ta)} = {fmt(need)} d；可用 {fmt(avail)} d；松弛 {fmt(slack)} d",
               warnings=[])


def op_convert(p):
    value = _num_or_none(p.get("value"))
    unit = p.get("unit", "min")
    target = p.get("target", "min")
    if value is None:
        return _err("value 缺失")
    if unit not in UNIT_MIN or target not in UNIT_MIN:
        return _err(f"未知单位 {unit} 或 {target}")
    minutes = value * UNIT_MIN[unit]
    out = minutes / UNIT_MIN[target]
    return _ok("computed", value=out, unit=target, minutes=minutes,
               formula=f"{fmt(value)} {unit} = {fmt(out)} {target}",
               warnings=[f"{unit}→{target} 含近似换算"] if (unit in APPROX_UNITS or target in APPROX_UNITS) else [])


OPS = {
    "sum_time": op_sum_time, "timeline": op_timeline, "cost": op_cost,
    "range": op_range, "weights": op_weights, "score": op_score,
    "sample_check": op_sample_check, "sample_size": op_sample_size,
    "tolerance": op_tolerance, "gate": op_gate, "lead_gate": op_lead_gate,
    "convert": op_convert,
}


def run_calc(spec):
    ops = spec.get("ops") if isinstance(spec, dict) else spec
    if not isinstance(ops, list):
        return {"ok": False, "error": "输入必须为 {\"ops\": [...]}", "results": [],
                "summary": {}}
    results, summary = [], {"total": len(ops), "ok": 0, "unconfirmed": 0, "error": 0}
    for op in ops:
        if not isinstance(op, dict):
            summary["error"] += 1
            results.append({"id": "?", "op": "?", "status": "error",
                            "error": "op 不是对象", "ok": False})
            continue
        name = op.get("op")
        oid = op.get("id", name)
        handler = OPS.get(name)
        if handler is None:
            res = {"id": oid, "op": name, "status": "error", "ok": False,
                   "error": "未知 op"}
        else:
            try:
                res = handler(op)
            except Exception as exc:  # noqa: BLE001
                res = {"id": oid, "op": name, "status": "error", "ok": False,
                       "error": type(exc).__name__ + ":" + str(exc)[:200]}
        res.setdefault("id", oid)
        res.setdefault("op", name)
        results.append(res)
        if res.get("status") == "computed":
            summary["ok"] += 1
        elif res.get("status") == "error":
            summary["error"] += 1
        else:
            summary["unconfirmed"] += 1
    return {"ok": summary["error"] == 0, "results": results, "summary": summary}


# --------------------------------------------------------------------------- #
# 增量写回（merge）与报告渲染
# --------------------------------------------------------------------------- #

def deep_merge(base, patch):
    if isinstance(patch, dict) and set(patch.keys()) == {"$append"} and \
            isinstance(base, list):
        base.extend(copy.deepcopy(patch["$append"]))
        return base
    if isinstance(base, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k not in base:
                base[k] = copy.deepcopy(v)
            else:
                base[k] = deep_merge(base[k], v)
        return base
    if isinstance(base, list) and isinstance(patch, list):
        if patch and all(isinstance(x, dict) and "id" in x for x in patch) and \
                all(isinstance(x, dict) and "id" in x for x in base):
            idx = {x["id"]: i for i, x in enumerate(base)}
            for x in patch:
                if x["id"] in idx:
                    base[idx[x["id"]]] = deep_merge(base[idx[x["id"]]], x)
                else:
                    base.append(copy.deepcopy(x))
            return base
        return copy.deepcopy(patch)
    return copy.deepcopy(patch)


def merge_patch(result, patch, note):
    result = deep_merge(result, patch)
    entries = result.setdefault("change_log", [])
    seq = max([e.get("seq", 0) for e in entries if isinstance(e, dict)] or [0]) + 1
    keys = sorted(patch.keys()) if isinstance(patch, dict) else []
    entries.append({"seq": seq, "step": note or "merge", "action": "incremental",
                    "keys": keys, "reason": "增量写回，逐段保存"})
    result["artifacts"] = [a for a in result.get("artifacts", [])
                           if a.get("path") != "review_report.md"]
    result["artifacts"].append({"path": "review_report.md", "kind": "markdown",
                                "status": "pending"})
    return result


def _md(s):
    s = "" if s is None else str(s)
    return s.replace("|", "\\|").replace("\n", "<br>").replace("\r", "")


def render_report(result):
    L = []
    L.append("# 实验方案可开工性审计报告\n")
    meta = result.get("output_contract") or {}
    L.append(f"- case_id: `{result.get('case_id')}`")
    L.append(f"- status: `{result.get('status')}`")
    L.append(f"- run_id: `{result.get('run_id')}`")
    L.append(f"- 生成方式: 由 result.json 确定性渲染（audit.py --render-report）\n")
    oc = result.get("overall_conclusion") or {}
    if oc.get("verdict"):
        L.append("## 总体结论\n")
        L.append(f"- 判定: `{oc.get('verdict')}`")
        L.append(f"- 摘要: {_md(oc.get('summary'))}")
        if oc.get("conditions"):
            L.append("- 条件: " + "；".join(_md(c) for c in oc["conditions"]))
        if oc.get("blockers"):
            L.append("- 阻断: " + "；".join(_md(b) for b in oc["blockers"]))
        L.append("")
    rq = result.get("research_question")
    if rq:
        L.append("## 研究问题\n\n" + _md(rq) + "\n")
    L.append("## 七维审查\n")
    review = result.get("review") or {}
    for key, label in DIM_ORDER:
        items = review.get(key) or []
        L.append(f"### {label}（{len(items)} 条）\n")
        if not items:
            L.append("_（无审查项）_\n")
            continue
        L.append("| id | 检查项 | 判定 | 资源状态 | 证据定位 | 条件/阻断 |")
        L.append("|---|---|---|---|---|---|")
        for it in items:
            if not isinstance(it, dict):
                continue
            locs = "；".join(e.get("locator", "") if isinstance(e, dict) else str(e)
                             for e in (it.get("evidence") or []))
            cond = "；".join(_md(c) for c in (it.get("conditions") or
                                              it.get("blocks") or []))
            L.append(f"| {_md(it.get('id'))} | {_md(it.get('check'))} | "
                     f"`{it.get('verdict')}` | {_md(it.get('resource_state'))} | "
                     f"{_md(locs)} | {cond} |")
        L.append("")
    pc = result.get("plan_comparison") or {}
    if pc.get("ranking"):
        L.append("## 方案比较\n")
        L.append(f"- 排序: {pc.get('ranking')}")
        L.append(f"- 推荐: `{pc.get('recommended')}`")
        if pc.get("tie_break"):
            L.append(f"- 并列裁决: {_md(pc.get('tie_break'))}")
        L.append("")
    ris = result.get("evidence_and_risks") or {}
    if ris.get("risks"):
        L.append("## 风险与异常\n")
        for r in ris["risks"]:
            L.append(f"- {_md(r)}")
        L.append("")
    conf = result.get("confirmation") or {}
    if any(conf.values()):
        L.append("## 确认项\n")
        for k, label in (("safety", "安全"), ("ethics", "伦理"),
                         ("equipment_availability", "设备可用性"),
                         ("expert_confirmation", "专家确认")):
            for it in conf.get(k) or []:
                L.append(f"- [{label}] {_md(it)}")
        L.append("")
    if result.get("uncertainty"):
        L.append("## 不确定性\n")
        for u in result["uncertainty"]:
            L.append(f"- {_md(u)}")
        L.append("")
    if result.get("pending_human"):
        L.append("## 待人工确认\n")
        for u in result["pending_human"]:
            L.append(f"- {_md(u)}")
        L.append("")
    if result.get("degradations"):
        L.append("## 降级与异常\n")
        for u in result["degradations"]:
            L.append(f"- {_md(u)}")
        L.append("")
    if result.get("limitations"):
        L.append("## 限制说明\n")
        for u in result["limitations"]:
            L.append(f"- {_md(u)}")
        L.append("")
    ev = (result.get("evidence_and_risks") or {}).get("evidence") or []
    if ev:
        L.append("## 证据索引\n")
        L.append("| 定位 | 引文 | 类型 |")
        L.append("|---|---|---|")
        for e in ev:
            if isinstance(e, dict):
                q = e.get("quote") or ""
                q = q if len(q) <= 160 else q[:160] + "…"
                L.append(f"| {_md(e.get('locator'))} | {_md(q)} | {_md(e.get('kind'))} |")
        L.append("")
    cl = result.get("change_log") or []
    if cl:
        L.append("## 变更记录\n")
        for e in cl:
            L.append(f"- [{e.get('seq')}] {_md(e.get('step'))}: {_md(e.get('action'))}")
        L.append("")
    return "\n".join(L)


def schema_doc():
    return {
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
        "status_enum": ["ok", "partial", "insufficient_evidence", "input_error"],
        "review_verdicts": VERDICTS,
        "resource_states": RESOURCE_STATES,
        "confidence_enum": CONFIDENCE,
        "review_dimensions": DIM_ORDER,
        "ops": {
            "sum_time": "items:[{label,value,unit,repeats,mode:serial|parallel}] → 分钟/人工可读/算式",
            "timeline": "tasks:[{id,label,duration,unit,deps,lead,lead_unit,wait,wait_unit}] + deadline_min → makespan/关键路径/松弛",
            "cost": "items:[{label,unit_price,qty,currency}] + fees + budget → 分币种总额/超支",
            "range": "{name,value,domain:{min,max,step}|{values:[...]}|[数值]} → in/out_of_domain/off_grid",
            "weights": "weights:[{key,weight}] → 归一化与是否平衡",
            "score": "plans:[{id,scores:{...}}], criteria:[{key,weight,direction}] → 排序/并列",
            "sample_check": "{available,required,loss_rate} → 可用样本/差额",
            "sample_size": "{design:means|proportions,...} → 每组/总样本量（正态近似）",
            "tolerance": "{nominal,actual,tol_pct} → 偏差/是否超差",
            "gate": "{value,operator,threshold} → 通过/不通过",
            "lead_gate": "{available_days,lead_days,duration_days,turnaround_days} → 可行/松弛",
            "convert": "{value,unit,target} → 单位换算（分钟基准）",
        },
    }


def _fix_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv=None):
    _fix_stdio()
    ap = argparse.ArgumentParser(description="可行性审计：算术内核 / 合并 / 报告")
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--calc", metavar="OPS_JSON")
    ap.add_argument("--out", metavar="OUT_JSON")
    ap.add_argument("--merge", metavar="PATCH_JSON")
    ap.add_argument("--into", metavar="RESULT_JSON")
    ap.add_argument("--note", default="merge")
    ap.add_argument("--render-report", metavar="RESULT_JSON")
    ap.add_argument("-o", "--output", metavar="OUT_FILE")
    args = ap.parse_args(argv)

    if args.schema:
        print(json.dumps(schema_doc(), ensure_ascii=False, indent=2))
        return 0

    if args.calc:
        with open(args.calc, "r", encoding="utf-8-sig") as fh:
            spec = json.load(fh)
        out = run_calc(spec)
        text = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")
        else:
            print(text)
        return 0

    if args.merge:
        with open(args.merge, "r", encoding="utf-8-sig") as fh:
            patch_spec = json.load(fh)
        patch = patch_spec.get("patch") if isinstance(patch_spec, dict) else patch_spec
        note = patch_spec.get("note", args.note) if isinstance(patch_spec, dict) else args.note
        into = args.into or "/app/output/result.json"
        with open(into, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        result = merge_patch(result, patch, note)
        with open(into, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(json.dumps({"ok": True, "changed_keys": sorted(patch.keys())
                          if isinstance(patch, dict) else [],
                          "change_log_len": len(result.get("change_log", []))},
                         ensure_ascii=False))
        return 0

    if args.render_report:
        with open(args.render_report, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        md = render_report(result)
        out = args.output or (os.path.splitext(args.render_report)[0] + ".report.md")
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(md)
        print(json.dumps({"ok": True, "report": out, "chars": len(md)},
                         ensure_ascii=False))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
