#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feasibility Gate Review — 确定性算术与门控（S3）。

把"时间/预算/参数域/样本量/设备占用"从口头估计变成可复核算式：
  * 每个派生值都给出 expr（算式）、inputs（输入与定位）、unit、rule_ref；
  * 输入不足时写 not_computable + missing，绝不猜一个数；
  * 同一输入重复运行输出逐字节一致（无随机、无墙钟）。

用法：
    python3 scripts/checks.py --work /app/work --input /input --out /app/work/arithmetic.json
    python3 scripts/checks.py --work /app/work --print

输出 arithmetic.json 的 plans[].gate 给出结构性门控（blocked/conditional/ok/unknown），
plan_comparison 由 compare() 依 (blockers, conditionals, total_h, plan_id) 确定性排序。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"

WINDOW_RE = re.compile(r"(window|deadline|timeline|overall_duration|project_days|total_days)", re.I)
MACHINE_RE = re.compile(r"(instrument|machine|equip|device|占用|机时|equipment)", re.I)
HUMAN_RE = re.compile(r"(hands|operator|human|人工|操作)", re.I)
WAIT_RE = re.compile(r"(wait|queue|周转|等待|incubat|培养)", re.I)

UNITS = (
    (r"(week|weeks|wk|周)", 168.0, "week"),
    (r"(day|days|天)", 24.0, "day"),
    (r"(hour|hours|hrs|hr|_h$|小时|工时)", 1.0, "hour"),
    (r"(min|mins|minute|minutes|分钟)", 1.0 / 60.0, "minute"),
    (r"(sec|secs|second|seconds|秒)", 1.0 / 3600.0, "second"),
)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def load_ledger(work):
    led = load_json(os.path.join(work, "case_ledger.json"))
    if isinstance(led, dict):
        return led
    # 回退：直接复用 main.py 的发现逻辑（同目录 import）。
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import main  # noqa: F401
        files, warnings = main.discover(DEFAULT_INPUT)
        case = None
        for f in files:
            if f["relpath"].rsplit("/", 1)[-1].lower() in main.CASE_NAMES:
                case = f["relpath"]
                break
        data, _ = main.load_json(os.path.join(DEFAULT_INPUT, case)) if case else (None, [])
        data = data if isinstance(data, dict) else {}
        _, led = main.build_skeleton(data, files, warnings, case)
        return led
    except Exception:  # 发现链本身失败时给出空账本，交由 not_computable 说明
        return {"plans": [], "contracts": {"items": []}, "parameters": {"items": []},
                "samples": {"items": []}, "scalars": [], "missing": ["ledger"]}


def as_float(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def duration_hours(key, raw, value):
    """返回 (hours, unit, assumed)。假定单位时必须显式标记 assumed=True。"""
    key_l = str(key).lower()
    text = str(raw).lower()
    for pat, factor, name in UNITS:
        if re.search(pat, key_l) or re.search(pat, text):
            return round(value * factor, 4), name, False
    return round(value, 4), "hour", True


def class_of(key):
    k = str(key)
    if WAIT_RE.search(k):
        return "wait"
    if MACHINE_RE.search(k):
        return "machine"
    if HUMAN_RE.search(k):
        return "human"
    return "unspecified"


# --------------------------------------------------------------------------- #
# 1. 时间
# --------------------------------------------------------------------------- #
def plan_time(plan):
    rows, unknown = [], []
    totals = {"total_h": 0.0, "human_h": 0.0, "machine_h": 0.0, "wait_h": 0.0, "unspecified_h": 0.0}
    for si, step in enumerate(plan.get("steps") or []):
        for d in step.get("durations") or []:
            value = as_float(d.get("value"))
            if value is None:
                continue
            hours, unit, assumed = duration_hours(d.get("key"), d.get("raw"), value)
            cls = class_of(d.get("key"))
            totals["total_h"] += hours
            totals[cls + "_h"] = totals.get(cls + "_h", 0.0) + hours
            rows.append({"step": si, "name": step.get("name", ""), "key": d.get("key"),
                         "raw": d.get("raw"), "hours": hours, "unit": unit,
                         "class": cls, "assumed_unit": assumed,
                         "ptr": (plan.get("ptr", "") + ".steps" + step.get("ptr", ""))})
            if assumed:
                unknown.append("unit_assumed:" + str(d.get("key")))
    expr = " + ".join("%.4g" % r["hours"] for r in rows) if rows else ""
    note = ("求和为单份/单批工时，未按样本量缩放；若方案需按 n 份放大，须在步骤中给出"
            "每份耗时或批处理数，再由审查员在 review.time_cost 中补缩放算式。")
    return {"rows": rows, "totals": {k: round(v, 4) for k, v in totals.items()},
            "expr": expr, "note": note, "unit_notes": sorted(set(unknown))}


# --------------------------------------------------------------------------- #
# 2. 预算
# --------------------------------------------------------------------------- #
def contract_totals(contracts):
    rows, total, n_priced = [], 0.0, 0
    for item in contracts.get("items") or []:
        price = as_float(item.get("price"))
        if price is None:
            continue
        qty = as_float(item.get("qty"))
        qty = 1.0 if qty is None else qty
        amount = price * qty
        total += amount
        n_priced += 1
        rows.append({"name": item.get("name"), "kind": item.get("kind"),
                     "price": price, "qty": qty, "amount": round(amount, 4),
                     "ptr": item.get("ptr")})
    return {"rows": rows, "total": round(total, 4), "n_priced": n_priced,
            "expr": " + ".join("%.4g*%.4g" % (r["price"], r["qty"]) for r in rows)}


# --------------------------------------------------------------------------- #
# 3. 参数域
# --------------------------------------------------------------------------- #
def norm_key(s):
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]", "", str(s).lower())


def find_value(plan, name):
    target = norm_key(name)
    if not target:
        return None, None
    for k, v in (plan.get("params") or {}).items():
        nk = norm_key(k.split(".")[-1])
        if nk == target or nk.endswith(target) or target in nk:
            val = as_float(v)
            if val is not None:
                return val, k
    return None, None


def domain_checks(ledger):
    out = []
    for item in (ledger.get("parameters") or {}).get("items") or []:
        name = item.get("name")
        lo, hi, unit = as_float(item.get("min")), as_float(item.get("max")), item.get("unit") or ""
        allowed = item.get("allowed")
        if lo is None and hi is None and not allowed:
            continue
        for plan in ledger.get("plans") or []:
            val, key = find_value(plan, name)
            row = {"param": name, "plan_id": plan.get("plan_id"), "value": val,
                   "min": lo, "max": hi, "unit": unit, "allowed": allowed, "key": key, "ptr": item.get("ptr")}
            if val is None:
                row["status"] = "unknown"
                row["note"] = "方案未给出该参数值"
            elif allowed:
                row["status"] = "in" if val in [as_float(a) if as_float(a) is not None else a for a in allowed] else "out"
            else:
                ok_lo = lo is None or val >= lo
                ok_hi = hi is None or val <= hi
                row["status"] = "in" if (ok_lo and ok_hi) else "out"
            if lo is not None or hi is not None:
                row["expr"] = "%s in [%s, %s] -> %s" % (
                    ("%g" % val) if val is not None else "?", "%g" % lo if lo is not None else "-inf",
                    "%g" % hi if hi is not None else "+inf", row["status"])
            out.append(row)
    return out


# --------------------------------------------------------------------------- #
# 4. 样本量
# --------------------------------------------------------------------------- #
def sample_checks(ledger):
    rows, req_total, avail_total, unknown = [], 0.0, 0.0, []
    for item in (ledger.get("samples") or {}).get("items") or []:
        req, avail = as_float(item.get("required")), as_float(item.get("available"))
        if req is None:
            unknown.append(item.get("name") or item.get("ptr"))
            continue
        req_total += req
        if avail is not None:
            avail_total += avail
        rows.append({"name": item.get("name"), "required": req, "available": avail,
                     "unit": item.get("unit") or "", "approval": item.get("approval") or "",
                     "ptr": item.get("ptr"),
                     "shortfall": None if avail is None else round(max(0.0, req - avail), 4)})
    shortfall = None
    if rows and all(r["available"] is not None for r in rows):
        shortfall = round(max(0.0, req_total - avail_total), 4)
    return {"rows": rows, "required_total": round(req_total, 4),
            "available_total": round(avail_total, 4), "shortfall": shortfall,
            "expr": "%g - %g = %s" % (req_total, avail_total,
                                      ("%g" % shortfall) if shortfall is not None else "unknown"),
            "unknown": unknown}


# --------------------------------------------------------------------------- #
# 5. 工期窗口与设备能力
# --------------------------------------------------------------------------- #
def window_hours(ledger):
    for s in ledger.get("scalars") or []:
        key = s.get("key") or ""
        ptr = s.get("ptr") or ""
        if "plans" in ptr:
            continue
        if WINDOW_RE.search(key):
            val = as_float(s.get("value"))
            if val is None:
                continue
            hours, unit, assumed = duration_hours(key, s.get("value"), val)
            return {"value": val, "hours": hours, "unit": unit, "assumed_unit": assumed,
                    "key": key, "ptr": ptr}
    return None


def capacity_check(ledger, machine_h):
    caps = []
    for item in (ledger.get("contracts") or {}).get("items") or []:
        cap = as_float(item.get("capacity_h"))
        if cap is not None:
            caps.append({"name": item.get("name"), "capacity_h": cap, "ptr": item.get("ptr")})
    if not caps:
        return {"status": "unknown", "capacity_h": None, "required_h": machine_h,
                "note": "资源合同未给出可用机时（capacity_h/available_hours/slots）"}
    total_cap = sum(c["capacity_h"] for c in caps)
    return {"status": "ok" if machine_h is None or machine_h <= total_cap else "over",
            "capacity_h": round(total_cap, 4), "required_h": machine_h, "items": caps,
            "expr": "%s vs %g" % (("%.4g" % machine_h) if machine_h is not None else "?", total_cap)}


# --------------------------------------------------------------------------- #
# 6. 门控与比较
# --------------------------------------------------------------------------- #
def gate_plan(plan_arith, domains, budget, window, capacity, samples):
    blockers, conditionals, unknowns = [], [], []
    for d in domains:
        if d.get("plan_id") != plan_arith.get("plan_id"):
            continue
        if d.get("status") == "out":
            blockers.append("参数越界:" + str(d.get("param")))
        elif d.get("status") == "unknown":
            conditionals.append("参数值缺失:" + str(d.get("param")))

    if budget.get("n_priced"):
        if budget.get("plan_budget") is not None and budget["contract_total"] > budget["plan_budget"]:
            blockers.append("采购总额超预算")
    else:
        unknowns.append("无采购/价格合同")

    th = plan_arith.get("time", {}).get("totals", {}).get("total_h")
    if window and th is not None and th > window["hours"]:
        blockers.append("工期超窗:%.4g h > %.4g h" % (th, window["hours"]))

    if capacity.get("status") == "over":
        blockers.append("设备机时不足")
    elif capacity.get("status") == "unknown":
        unknowns.append("设备可用机时未知")

    if samples.get("shortfall") is not None and samples["shortfall"] > 0:
        blockers.append("样本缺口:%g" % samples["shortfall"])

    for note in plan_arith.get("time", {}).get("unit_notes") or []:
        conditionals.append(note)

    if blockers:
        status = "blocked"
    elif conditionals:
        status = "conditional"
    elif unknowns:
        status = "unknown"
    else:
        status = "ok"
    return {"status": status, "blockers": blockers,
            "conditionals": conditionals, "unknowns": unknowns}


def compare(gates):
    def key(g):
        rank = {"ok": 0, "conditional": 1, "unknown": 2, "blocked": 3}.get(g["gate"]["status"], 4)
        th = g.get("time", {}).get("totals", {}).get("total_h")
        return (rank, len(g["gate"]["blockers"]), len(g["gate"]["conditionals"]),
                th if th is not None else float("inf"), str(g.get("plan_id")))
    ordered = sorted(gates, key=key)
    return [{"rank": i + 1, "plan_id": g["plan_id"], "gate": g["gate"]["status"],
             "blockers": g["gate"]["blockers"], "total_h": g.get("time", {}).get("totals", {}).get("total_h"),
             "reason": "按 (门控状态, 阻断数, 条件数, 总工时) 排序"} for i, g in enumerate(ordered)]


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def compute(ledger):
    plans = ledger.get("plans") or []
    contracts = ledger.get("contracts") or {}
    ctot = contract_totals(contracts)
    domains = domain_checks(ledger)
    samples = sample_checks(ledger)
    window = window_hours(ledger)

    plan_arith, derivations, not_computable = [], [], []
    for plan in plans:
        t = plan_time(plan)
        plan_budget = as_float(plan.get("budget"))
        budget = {"plan_budget": plan_budget, "contract_total": ctot["total"],
                  "n_priced": ctot["n_priced"], "expr": ctot["expr"], "over": None}
        if plan_budget is not None and ctot["n_priced"]:
            budget["over"] = round(ctot["total"] - plan_budget, 4)
        cap = capacity_check(ledger, t["totals"].get("machine_h"))
        g = gate_plan({"plan_id": plan.get("plan_id"), "time": t}, domains, budget, window, cap, samples)
        plan_arith.append({"plan_id": plan.get("plan_id"), "ptr": plan.get("ptr"),
                           "time": t, "budget": budget, "capacity": cap, "gate": g})
        derivations.append({"id": "D-time-%s" % plan.get("plan_id"), "kind": "time",
                            "plan_id": plan.get("plan_id"), "expr": t["expr"] or "no duration fields",
                            "result": t["totals"].get("total_h"), "unit": "h",
                            "rule_ref": "rubric.md#T1"})
        if plan_budget is not None and ctot["n_priced"]:
            derivations.append({"id": "D-budget-%s" % plan.get("plan_id"), "kind": "budget",
                                "plan_id": plan.get("plan_id"), "expr": ctot["expr"] or "no priced items",
                                "result": budget["over"], "unit": "currency",
                                "rule_ref": "rubric.md#B1"})
        if not t["rows"]:
            not_computable.append({"kind": "time", "plan_id": plan.get("plan_id"),
                                   "missing": ["steps[].duration/time/hour/day"], "note": "未给出可解析的时长字段"})

    if not ctot["n_priced"]:
        not_computable.append({"kind": "budget", "missing": ["contracts/price/qty"],
                               "note": "无带价格/数量的合同项，无法核对采购成本"})
    if not (ledger.get("parameters") or {}).get("items"):
        not_computable.append({"kind": "parameter_domain", "missing": ["parameters[min,max,allowed]"],
                               "note": "无有限参数域，无法判定参数越界"})
    if not (ledger.get("samples") or {}).get("items"):
        not_computable.append({"kind": "sample", "missing": ["samples[required,available]"],
                               "note": "无样本信息，无法核对样本量"})
    if window is None:
        not_computable.append({"kind": "schedule", "missing": ["window/deadline/timeline"],
                               "note": "无工期窗口，无法判定排期可行性"})

    return {
        "generated_by": "scripts/checks.py",
        "ruleset": "seven-gate-v1",
        "contracts": ctot,
        "domain_checks": domains,
        "samples": samples,
        "window": window,
        "plans": plan_arith,
        "derivations": derivations,
        "not_computable": not_computable,
        "plan_comparison": compare(plan_arith),
    }


def print_table(res):
    print("plan                 gate        total_h  blockers  conditionals")
    for p in res["plans"]:
        print("%-20s %-11s %-8s %-9d %d" % (
            str(p["plan_id"])[:20], p["gate"]["status"],
            "%.4g" % p["time"]["totals"]["total_h"] if p["time"]["rows"] else "-",
            len(p["gate"]["blockers"]), len(p["gate"]["conditionals"])))
    print("derivations=%d not_computable=%d" % (len(res["derivations"]), len(res["not_computable"])))


def main():
    ap = argparse.ArgumentParser(description="确定性算术与门控")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--out", default=None, help="默认 <work>/arithmetic.json")
    ap.add_argument("--print", action="store_true", help="打印简明表格")
    args = ap.parse_args()

    ledger = load_ledger(args.work)
    res = compute(ledger)
    out = args.out or os.path.join(args.work, "arithmetic.json")
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("write failed: %s\n" % exc)
    if args.print:
        print_table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
