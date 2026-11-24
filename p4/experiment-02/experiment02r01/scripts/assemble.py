#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 findings.jsonl 组装为 result.json / evidence_map.json / review_report.md。

用法：
  python3 scripts/assemble.py --input /input --output /app/output --work /app/work

读取：
  work/casepack.json            main.py init 生成
  work/findings.jsonl           审查项（可多文件 / 目录）
  work/contract_values.json     可选：契约字段的直接取值
  work/conclusion.json          可选：总体结论覆盖
  work/comparison.json          可选：版本比较说明与推荐版本
  work/plan_notes.json          可选：实验方案字段的人工补写
  work/extras.json              可选：uncertainty / pending_human / limitations / errors / confirmation

写出：
  output/result.json            结构化主产物
  output/evidence_map.json      locator ↔ 逐字 quote ↔ 审查项 的双向映射
  output/review_report.md       可编辑、逐条带定位与原文的审查报告

仅依赖标准库；所有引用按空白归一化后回查原文，未通过者标记 verified=false 并计入 errors。
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

CHECK_BASES = ("plan_stated", "plan_silent", "contract", "domain", "computed", "source")
SEVERITY = {"blocked": "blocking", "conflict": "blocking", "unconfirmed": "major",
            "missing": "major", "conditional": "major", "ok": "info"}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
        ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
_FUNCS = {"sum": sum, "min": min, "max": max, "abs": abs, "round": round,
          "ceil": math.ceil, "floor": math.floor, "sqrt": math.sqrt}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def safe_eval(expr, names):
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"公式语法错误: {exc}") from exc

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool):
                return n.value
            if isinstance(n.value, (int, float)):
                return n.value
            raise ValueError("只允许数值/布尔常量")
        if isinstance(n, ast.Name):
            if n.id not in names:
                raise ValueError(f"未知变量 {n.id}")
            return names[n.id]
        if isinstance(n, ast.List):
            return [ev(x) for x in n.elts]
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.UAdd):
                return v
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.Not):
                return not v
            raise ValueError("一元运算不支持")
        if isinstance(n, ast.BinOp):
            op = _BIN.get(type(n.op))
            if op is None:
                raise ValueError("二元运算不支持")
            return op(ev(n.left), ev(n.right))
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                fn = _CMP.get(type(op))
                if fn is None:
                    raise ValueError("比较运算不支持")
                right = ev(comp)
                if not fn(left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in _FUNCS:
                raise ValueError("函数不允许")
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(f"不支持的表达式节点 {type(n).__name__}")

    return ev(tree)


def load_json_opt(path, default=None):
    if not path or not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def load_findings(paths):
    raw, issues = [], []
    for path in paths:
        if os.path.isdir(path):
            names = sorted(n for n in os.listdir(path) if n.endswith((".jsonl", ".json")))
            files = [os.path.join(path, n) for n in names]
        else:
            files = [path]
        for f in files:
            if not os.path.isfile(f):
                issues.append({"level": "warn", "detail": f"findings 文件不存在: {f}"})
                continue
            text, _ = core.read_text(f)
            if not text:
                issues.append({"level": "warn", "detail": f"findings 文件不可读: {f}"})
                continue
            if f.endswith(".jsonl"):
                for i, line in enumerate(text.splitlines(), 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        issues.append({"level": "fail", "detail": f"{f}:{i} 坏 JSON: {exc}"})
            else:
                data, err = core.load_json(f)
                if isinstance(data, list):
                    raw.extend(data)
                elif isinstance(data, dict):
                    raw.extend(data.get("findings") or [])
                else:
                    issues.append({"level": "fail", "detail": f"{f}: 非数组 JSON ({';'.join(err)})"})
    return raw, issues


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return value
    return [value]


def norm_finding(raw, idx):
    issues = []
    if not isinstance(raw, dict):
        return None, [{"level": "fail", "detail": f"findings[{idx}] 不是对象"}]
    fid = str(raw.get("finding_id") or raw.get("id") or "").strip()
    if not fid:
        fid = f"F-{idx + 1:03d}"
        issues.append({"level": "warn", "detail": f"缺 finding_id，自动命名 {fid}"})
    dim = str(raw.get("dimension") or raw.get("dim") or "").strip()
    if dim not in core.DIMS:
        issues.append({"level": "fail", "detail": f"{fid}: dimension 非法或缺失: {dim!r}"})
    verdict = str(raw.get("verdict") or "").strip()
    if verdict not in core.VERDICTS:
        issues.append({"level": "fail", "detail": f"{fid}: verdict 非法或缺失: {verdict!r}"})
    claim = str(raw.get("claim") or "").strip()
    if len(claim) < 10:
        issues.append({"level": "fail", "detail": f"{fid}: claim 过短或缺失"})
    ev = []
    for j, item in enumerate(_as_list(raw.get("evidence"))):
        if not isinstance(item, dict):
            issues.append({"level": "fail", "detail": f"{fid}: evidence[{j}] 不是对象"})
            continue
        role = str(item.get("role") or "supports")
        if role not in core.EVIDENCE_ROLES:
            issues.append({"level": "warn", "detail": f"{fid}: evidence[{j}] role 非法({role})，按 supports 处理"})
            role = "supports"
        ev.append({"locator": str(item.get("locator") or "").strip(),
                   "quote": str(item.get("quote") or ""),
                   "role": role})
    if not ev:
        issues.append({"level": "fail", "detail": f"{fid}: 缺 evidence（至少 1 条 locator+quote）"})
    applies = raw.get("applies_to")
    if isinstance(applies, str):
        applies = [applies]
    applies = [str(x) for x in (applies or []) if str(x).strip()]
    if not applies:
        pv = raw.get("plan_version") or raw.get("plan")
        applies = [str(pv)] if pv else []
    basis = str(raw.get("check_basis") or "plan_stated")
    if basis not in CHECK_BASES:
        issues.append({"level": "warn", "detail": f"{fid}: check_basis 非法({basis})，按 plan_stated 处理"})
        basis = "plan_stated"
    action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
    return {
        "finding_id": fid,
        "dimension": dim,
        "claim": claim,
        "verdict": verdict,
        "severity": SEVERITY.get(verdict, "major"),
        "check_basis": basis,
        "applies_to": applies,
        "evidence": ev,
        "computation": raw.get("computation") if isinstance(raw.get("computation"), dict) else None,
        "conditions": [str(x) for x in _as_list(raw.get("conditions"))],
        "blockers": [str(x) for x in _as_list(raw.get("blockers"))],
        "resource_state": raw.get("resource_state") if raw.get("resource_state") in core.RESOURCE_STATES else None,
        "action": {"owner": str(action.get("owner") or ""), "by": str(action.get("by") or ""),
                   "acceptance": str(action.get("acceptance") or ""),
                   "ask": str(action.get("ask") or "")},
        "confidence": str(raw.get("confidence") or "medium"),
        "needs_expert": bool(raw.get("needs_expert")),
        "notes": [str(x) for x in _as_list(raw.get("notes"))],
    }, issues


def verify_evidence(ev, input_dir, work):
    out = dict(ev)
    res = (core.resolve_locator(ev["locator"], input_dir, work) if ev["locator"]
           else {"ok": False, "kind": "missing_locator", "text": "", "detail": "未提供 locator"})
    quote = core.norm_ws(ev["quote"])
    hit = bool(res["ok"]) and core.quote_len_ok(quote) and quote in core.norm_ws(res["text"])
    out.update({"locator_ok": bool(res["ok"]), "quote_verified": hit,
                "quote_chars": len(quote), "source_kind": res.get("kind")})
    if not hit:
        out["suggest"] = core.norm_ws(res.get("text", ""))[:220]
        out["resolve_detail"] = res.get("detail")
    return out


def check_computation(comp, cands, fid):
    issues = []
    if not isinstance(comp, dict):
        return None, [{"level": "fail", "detail": f"{fid}: computation 不是对象"}]
    entry = dict(comp)
    ref = str(comp.get("ref") or "").strip()
    if ref:
        cand = cands.get(ref)
        if cand is None:
            issues.append({"level": "warn", "detail": f"{fid}: computation.ref={ref} 不在 casepack 候选中"})
            entry["ref_resolved"] = False
        else:
            entry["ref_resolved"] = True
            for k in ("kind", "formula", "operands", "result", "unit", "refs"):
                if entry.get(k) in (None, "", {}, []):
                    entry[k] = cand.get(k)
    formula = entry.get("formula")
    operands = entry.get("operands") if isinstance(entry.get("operands"), dict) else {}
    declared = entry.get("result")
    if not formula:
        return entry, issues + [{"level": "fail", "detail": f"{fid}: computation 缺 formula"}]
    names = {}
    for k, v in operands.items():
        if isinstance(v, bool):
            names[str(k)] = v
        elif isinstance(v, (int, float)):
            names[str(k)] = v
        else:
            try:
                names[str(k)] = float(str(v).replace(",", ""))
            except ValueError:
                names[str(k)] = v
    try:
        computed = safe_eval(str(formula), names)
    except Exception as exc:  # noqa: BLE001
        return entry, issues + [{"level": "fail", "detail": f"{fid}: computation 求值失败: {exc}"}]
    ok = False
    if isinstance(declared, bool) and isinstance(computed, bool):
        ok = declared == computed
    elif isinstance(declared, (int, float)) and isinstance(computed, (int, float)) \
            and not isinstance(declared, bool):
        ok = abs(float(declared) - float(computed)) <= 1e-6 + 1e-9 * abs(float(computed))
    entry["computed"] = computed
    entry["verified"] = ok
    if not ok:
        issues.append({"level": "fail",
                       "detail": f"{fid}: computation 结果不符，声明 {declared!r}，复算 {computed!r}"})
    return entry, issues


def _unit_ok(entry):
    if entry.get("unit") in (None, ""):
        return isinstance(entry.get("computed"), (bool, str))
    return True


def build_experiment_plan(casepack, notes, conclusion, comparison):
    plans = casepack.get("plans") or []
    by_ver = {p["version"]: p for p in plans}
    pick = None
    for cand in (comparison.get("recommended"), conclusion.get("recommended_plan")):
        if cand and str(cand) in by_ver:
            pick = by_ver[str(cand)]
            break
    pick = pick or (plans[0] if plans else {})
    rq = (notes.get("research_question") or casepack.get("research_question")
          or casepack.get("title"))
    params = []
    for name, rec in (pick.get("parameters") or {}).items():
        dom = (casepack.get("domains") or {}).get(name) or {}
        params.append({"name": name, "value": rec.get("value"), "unit": dom.get("unit", ""),
                       "domain": {"min": dom.get("min"), "max": dom.get("max")} if dom else None,
                       "locator": rec.get("locator")})
    resources = []
    for item in pick.get("resources") or []:
        resources.append({"item": item["text"], "locator": item["locator"], "status": None})
    for ent in ((casepack.get("contracts") or {}).get("equipment") or {}).get("entries", []):
        resources.append({"item": ent["preview"], "locator": ent["locator"], "status": None})
    return {
        "research_question": rq,
        "assumptions": notes.get("assumptions") or pick.get("assumptions") or [],
        "variables": notes.get("variables") or pick.get("variables") or [],
        "controls": notes.get("controls") or pick.get("controls") or [],
        "steps": notes.get("steps") or [{"order": s["index"], "step": s["text"], "locator": s["locator"]}
                                        for s in (pick.get("steps") or [])],
        "parameters": notes.get("parameters") or params,
        "resources": notes.get("resources") or resources,
        "interpretation_criteria": notes.get("interpretation_criteria") or pick.get("readouts") or [],
        "plan_version": pick.get("version"),
        "auto_derived": not bool(notes),
    }


def build_conclusion(findings, conclusion, notes):
    counts = {v: sum(1 for f in findings if f["verdict"] == v) for v in core.VERDICTS}
    dims = sum(1 for d in core.DIMS if any(f["dimension"] == d for f in findings))
    auto_verdict = "ok"
    if counts["conflict"]:
        auto_verdict = "conflict"
    elif counts["blocked"]:
        auto_verdict = "blocked"
    elif counts["conditional"] + counts["unconfirmed"] + counts["missing"]:
        auto_verdict = "conditional"
    summary = (f"共 {len(findings)} 条审查项，覆盖 {dims}/7 类约束；blocked {counts['blocked']}、"
               f"conditional {counts['conditional']}、unconfirmed {counts['unconfirmed']}、"
               f"missing {counts['missing']}、conflict {counts['conflict']}、ok {counts['ok']}。")
    conditions = []
    for f in findings:
        for c in f["conditions"]:
            conditions.append(f"[{f['finding_id']}] {c}")
    blocks = [{"finding_id": f["finding_id"], "claim": f["claim"]}
              for f in findings if f["verdict"] in ("blocked", "conflict")]
    out = {"verdict": conclusion.get("verdict") or auto_verdict,
           "summary": conclusion.get("summary") or summary,
           "conditions": conclusion.get("conditions") or conditions,
           "blocks": conclusion.get("blocks") or blocks,
           "recommended_plan": conclusion.get("recommended_plan"),
           "notes": conclusion.get("notes") or []}
    if notes.get("overall_note"):
        out["notes"].append(notes["overall_note"])
    return out


def build_comparison(casepack, findings, cfg):
    plans = casepack.get("plans") or []
    if len(plans) < 2:
        return [], {}
    diffs = {d["curr"]: d for d in (casepack.get("plan_diff") or [])}
    per = cfg.get("per_plan") if isinstance(cfg.get("per_plan"), dict) else {}
    rows = []
    for p in plans:
        ver = p["version"]
        own = [f for f in findings if ver in f["applies_to"]]
        counts = {v: sum(1 for f in own if f["verdict"] == v) for v in core.VERDICTS}
        viol = sum(1 for c in (p.get("parameter_checks") or {}).values() if c.get("result") is False)
        row = {
            "version": ver,
            "locator": p["locator"],
            "findings": len(own),
            "verdict_counts": counts,
            "blocking": counts["blocked"] + counts["conflict"],
            "unresolved": counts["conditional"] + counts["unconfirmed"] + counts["missing"],
            "time_total_minutes": p.get("duration_total_minutes"),
            "time_total_hours": (round(p["duration_total_minutes"] / 60.0, 2)
                                 if p.get("duration_total_minutes") else None),
            "costs_by_currency": p.get("costs_by_currency") or {},
            "parameter_violations": viol,
            "steps": len(p.get("steps") or []),
            "changed_fields_vs_previous": (diffs.get(ver) or {}).get("changed"),
            "note": (per.get(ver) or {}).get("note", ""),
        }
        rows.append(row)
    basis = ("按 blocking、unresolved、参数越界数、总时长升序排列；仅为算术参考，"
             "不替代专家判断，也未计入未量化的可行性因素")
    ranked = sorted(rows, key=lambda r: (r["blocking"], r["unresolved"], r["parameter_violations"],
                                         r["time_total_minutes"] if r["time_total_minutes"] is not None else 1e18,
                                         str(r["version"])))
    meta = {"heuristic_ranking": [r["version"] for r in ranked],
            "ranking_basis": basis,
            "recommended": cfg.get("recommended"),
            "criteria": cfg.get("criteria") or [],
            "rationale": cfg.get("rationale") or ""}
    return rows, meta


def build_sources(casepack, findings):
    used = {}
    for f in findings:
        for e in f["evidence"]:
            rel = e["locator"].split("#", 1)[0]
            used.setdefault(rel, set()).add(f["finding_id"])
    out = []
    for s in casepack.get("files") or []:
        ids = sorted(used.get(s["rel"], []))
        out.append({"locator": s["rel"], "kind": s["kind"], "bytes": s["bytes"],
                    "sha1_8": s["sha1_8"], "used_by": ids, "used": bool(ids)})
    return out


def build_confirmation(findings):
    out = {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []}
    for f in findings:
        item = {"finding_id": f["finding_id"], "claim": f["claim"][:160], "verdict": f["verdict"],
                "action": f["action"], "conditions": f["conditions"]}
        if f["dimension"] == "ethics_safety":
            claim = f["claim"].lower()
            key = "ethics" if ("伦理" in f["claim"] or "ethic" in claim or "irb" in claim) else "safety"
            out[key].append(item)
        if f["dimension"] == "equipment_materials":
            out["equipment_availability"].append(item)
        if f["needs_expert"]:
            out["expert_confirmation"].append(item)
    return out


def build_uncertainty(casepack, findings, contract_gaps, issues, extras):
    out = []
    unverified = [f["finding_id"] for f in findings
                  if any(not e.get("quote_verified") for e in f["evidence"])]
    if unverified:
        out.append({"kind": "evidence_quote_unverified", "count": len(unverified),
                    "detail": "以下审查项存在未通过原文回查的 quote：" + ", ".join(unverified[:12])})
    if contract_gaps:
        out.append({"kind": "contract_gap", "count": len(contract_gaps),
                    "detail": "未填写的契约字段：" + ", ".join(contract_gaps)})
    addressed = {(f["dimension"], f["verdict"]) for f in findings}
    for flag in casepack.get("silence_flags") or []:
        dim = flag["dimension"]
        if not any(d == dim and v in ("missing", "unconfirmed", "conditional", "blocked", "conflict")
                   for d, v in addressed):
            out.append({"kind": "silence_unaddressed", "dimension": dim,
                        "detail": f"{dim} 在候选方案文本中未检出相关关键词，但没有任何 missing/unconfirmed 判断说明'方案未写'；请确认是否把未写当成满足"})
    for d in core.DIMS:
        if not any(f["dimension"] == d for f in findings):
            out.append({"kind": "coverage_gap", "dimension": d, "detail": f"{d} 无任何审查项"})
    low = [f["finding_id"] for f in findings if f["confidence"] == "low"]
    if low:
        out.append({"kind": "low_confidence", "count": len(low), "detail": ", ".join(low[:12])})
    fails = [i for i in issues if i.get("level") == "fail"]
    if fails:
        out.append({"kind": "assembly_issues", "count": len(fails),
                    "detail": "; ".join(i["detail"] for i in fails[:6])})
    out.extend(_as_list(extras.get("uncertainty")))
    return out


def build_pending(findings, extras):
    out = []
    for f in findings:
        if f["verdict"] in ("unconfirmed", "missing", "blocked", "conditional") and \
                (f["action"].get("ask") or f["action"].get("owner") or f["needs_expert"]):
            out.append({"finding_id": f["finding_id"], "verdict": f["verdict"],
                        "ask": f["action"].get("ask") or f["claim"][:120],
                        "owner": f["action"].get("owner") or "",
                        "by": f["action"].get("by") or "",
                        "acceptance": f["action"].get("acceptance") or ""})
    out.extend(_as_list(extras.get("pending_human")))
    return out


def assign_contract(result, contract, contract_values, base):
    binding, gaps, values = {}, [], {}
    for f in contract or {}:
        if f in base and base[f] not in (None, [], {}):
            values[f] = base[f]
            binding[f] = "auto"
        elif f in contract_values:
            values[f] = contract_values[f]
            binding[f] = "contract_values"
        elif f in base:
            values[f] = base[f]
            binding[f] = "auto_empty"
        else:
            values[f] = None
            binding[f] = "unfilled"
            gaps.append(f)
    out = {}
    for f in contract or {}:
        out[f] = values.get(f)
    for k, v in base.items():
        out.setdefault(k, v)
    return out, {"source": "case.json#output_contract", "fields": list((contract or {}).keys()),
                 "binding": binding, "unfilled": gaps}


def render_report(result, evidence_map):
    lines = []
    lines.append("# 实验方案可行性审查报告")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| case_id | `{result.get('case_id')}` |")
    lines.append(f"| status | `{result.get('status')}` |")
    plan_versions = ", ".join(p["version"] for p in (result.get("plan_comparison") or []))
    lines.append(f"| 方案版本 | {plan_versions or '未识别'} |")
    es = result.get("evidence_summary") or {}
    lines.append(f"| 审查项 / 证据 | {len(result.get('findings_index') or [])} / {es.get('items', 0)}"
                 f"（原文核对通过 {es.get('verified', 0)}） |")
    lines.append(f"| 生成 | `assemble.py`（输入 `/input`，产物 `/app/output`） |")
    lines.append("")
    lines.append("> 本报告由 `result.json` 与 `evidence_map.json` 机械生成：每条判断给出 locator 与逐字 quote，"
                 "quote 按空白归一化后回查原文；未通过的条目在证据行标 ✗ 并计入 `errors`。")
    lines.append("")
    conc = result.get("overall_conclusion") or {}
    lines.append("## 1 结论摘要")
    lines.append("")
    lines.append(f"- verdict: `{conc.get('verdict')}`")
    lines.append(f"- {conc.get('summary')}")
    for b in conc.get("blocks") or []:
        lines.append(f"- 阻断项 `{b.get('finding_id')}`: {b.get('claim')}")
    if conc.get("recommended_plan"):
        lines.append(f"- 推荐版本：`{conc.get('recommended_plan')}`")
    lines.append("")
    cmp_ = result.get("plan_comparison_meta") or {}
    rows_ = result.get("plan_comparison") or []
    if rows_:
        lines.append("## 2 版本对照")
        lines.append("")
        lines.append("| 版本 | 审查项 | blocking | unresolved | 总时长(h) | 参数越界 | 相对上一版变更字段 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows_:
            lines.append(f"| {r['version']} | {r['findings']} | {r['blocking']} | {r['unresolved']} | "
                         f"{r['time_total_hours']} | {r['parameter_violations']} | "
                         f"{r['changed_fields_vs_previous']} |")
        lines.append("")
        if cmp_.get("heuristic_ranking"):
            lines.append(f"- 算术排序（非最终裁定）：{' > '.join(cmp_['heuristic_ranking'])}")
            lines.append(f"- 排序依据：{cmp_.get('ranking_basis')}")
        if cmp_.get("rationale"):
            lines.append(f"- 人工说明：{cmp_['rationale']}")
        lines.append("")
    lines.append("## 3 逐条审查")
    lines.append("")
    dim_titles = {"assumptions_variables": "假设与变量", "controls": "对照组",
                  "equipment_materials": "设备与材料", "time_cost": "时间成本",
                  "parameter_ranges": "参数范围", "risks_exceptions": "风险与异常",
                  "ethics_safety": "伦理与安全"}
    review = result.get("review") or {}
    for i, d in enumerate(core.DIMS, 1):
        items = review.get(d) or []
        lines.append(f"### 3.{i} {d}（{dim_titles[d]}）")
        lines.append("")
        if not items:
            lines.append("- （无审查项：覆盖缺口，见第 6 节）")
            lines.append("")
            continue
        for f in items:
            lines.append(f"#### {f['finding_id']} — `{f['verdict']}` / {f['severity']}")
            lines.append("")
            lines.append(f"- 主张：{f['claim']}")
            if f.get("applies_to"):
                lines.append(f"- 适用版本：{', '.join(f['applies_to'])}")
            if f.get("resource_state"):
                lines.append(f"- 资源状态：{f['resource_state']}")
            lines.append(f"- 判定依据类型：`{f.get('check_basis')}`；置信度：{f.get('confidence')}；"
                         f"需专家确认：{'是' if f.get('needs_expert') else '否'}")
            lines.append("- 证据：")
            for e in f.get("evidence") or []:
                mark = "✓" if e.get("quote_verified") else "✗"
                quote = (e.get("quote") or "").replace("\n", " ")
                lines.append(f"  - {mark} `{e.get('locator')}`（{e.get('role')}）「{quote}」")
            comp = f.get("computation")
            if comp:
                ops = ", ".join(f"{k}={v}" for k, v in (comp.get("operands") or {}).items())
                lines.append(f"- 计算：`{comp.get('formula')}`；代入 {ops}；结果 {comp.get('result')} "
                             f"{comp.get('unit') or ''}（复算 {comp.get('computed')}，"
                             f"{'通过' if comp.get('verified') else '不符'}）")
            if f.get("conditions"):
                lines.append("- 条件：")
                for c in f["conditions"]:
                    lines.append(f"  - {c}")
            if f.get("blockers"):
                lines.append("- 阻断原因：")
                for b in f["blockers"]:
                    lines.append(f"  - {b}")
            act = f.get("action") or {}
            if any(act.values()):
                lines.append(f"- 责任/时限/验收：{act.get('owner') or '未指定'} / "
                             f"{act.get('by') or '未指定'} / {act.get('acceptance') or '未指定'}")
            if f.get("notes"):
                lines.append(f"- 备注：{'；'.join(f['notes'])}")
            lines.append("")
    lines.append("## 4 算术台账")
    lines.append("")
    ledger = result.get("arithmetic_ledger") or []
    if not ledger:
        lines.append("- （无计算条目）")
    else:
        lines.append("| ID | 公式 | 结果 | 复算 | 单位 | 引用 |")
        lines.append("|---|---|---|---|---|---|")
        for a in ledger:
            refs = "<br>".join(f"`{r}`" for r in (a.get("refs") or [])[:4])
            lines.append(f"| {a.get('id')} | `{a.get('formula')}` | {a.get('result')} | "
                         f"{a.get('computed')} | {a.get('unit') or ''} | {refs} |")
    lines.append("")
    diff = result.get("plan_diff") or []
    if diff:
        lines.append("## 5 版本差异（脚本对齐）")
        lines.append("")
        for pair in diff:
            lines.append(f"### {pair['prev']} → {pair['curr']}（{pair['changed']} 处字段差异）")
            lines.append("")
            for c in (pair.get("changes") or [])[:12]:
                lines.append(f"- `{c.get('a_locator')}` → `{c.get('b_locator')}`："
                             f"{c.get('a')} ⇒ {c.get('b')}")
            lines.append("")
    lines.append("## 6 缺口、不确定性与限制")
    lines.append("")
    for u in result.get("uncertainty") or []:
        if isinstance(u, dict):
            lines.append(f"- `{u.get('kind')}`: {u.get('detail')}")
        else:
            lines.append(f"- {u}")
    for lim in result.get("limitations") or []:
        lines.append(f"- 限制：{lim.get('detail') if isinstance(lim, dict) else lim}")
    lines.append("")
    lines.append("## 7 待人工确认")
    lines.append("")
    pend = result.get("pending_human") or []
    if not pend:
        lines.append("- （无）")
    for p in pend:
        if isinstance(p, dict):
            lines.append(f"- `{p.get('finding_id') or '-'}` [{p.get('verdict') or '-'}] "
                         f"{p.get('ask')}（责任：{p.get('owner') or '未指定'}；"
                         f"时限：{p.get('by') or '未指定'}；验收：{p.get('acceptance') or '未指定'}）")
        else:
            lines.append(f"- {p}")
    lines.append("")
    lines.append("## 8 变更记录")
    lines.append("")
    for c in result.get("change_log") or []:
        lines.append(f"- {c.get('step')}: {c.get('action')} — {c.get('detail')}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="组装 result.json / evidence_map.json / review_report.md")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--findings", nargs="*", default=[])
    ap.add_argument("--strict-quotes", action="store_true", help="存在未回查通过的 quote 时返回码 2")
    ap.add_argument("--requests", type=int, default=None)
    args = ap.parse_args(argv)

    casepack = load_json_opt(os.path.join(args.work, "casepack.json"))
    if not isinstance(casepack, dict):
        casepack = core.build_casepack(args.input, args.work)
    contract = casepack.get("contract_fields") or {}
    contract_values = load_json_opt(os.path.join(args.work, "contract_values.json"), {}) or {}
    conclusion = load_json_opt(os.path.join(args.work, "conclusion.json"), {}) or {}
    comparison_cfg = load_json_opt(os.path.join(args.work, "comparison.json"), {}) or {}
    notes = load_json_opt(os.path.join(args.work, "plan_notes.json"), {}) or {}
    extras = load_json_opt(os.path.join(args.work, "extras.json"), {}) or {}

    paths = args.findings or [os.path.join(args.work, "findings.jsonl")]
    raw, issues = load_findings(paths)
    cands = {c["id"]: c for c in (casepack.get("candidates") or [])}
    findings, seen = [], {}
    for i, item in enumerate(raw):
        f, probs = norm_finding(item, i)
        issues.extend(probs)
        if not f:
            continue
        if f["finding_id"] in seen:
            issues.append({"level": "fail", "detail": f"{f['finding_id']}: finding_id 重复"})
            continue
        seen[f["finding_id"]] = True
        evs = []
        for e in f["evidence"]:
            ev = verify_evidence(e, args.input, args.work)
            evs.append(ev)
            if not ev["quote_verified"]:
                issues.append({"level": "fail",
                               "detail": f"{f['finding_id']}: quote 未通过原文回查 @ {e['locator']}"
                                         f"（{len(core.norm_ws(e['quote']))} 字）"})
        f["evidence"] = evs
        if f["computation"]:
            comp, probs = check_computation(f["computation"], cands, f["finding_id"])
            issues.extend(probs)
            f["computation"] = comp
        findings.append(f)

    for f in findings:
        if f["verdict"] == "conditional" and not f["conditions"]:
            issues.append({"level": "fail", "detail": f"{f['finding_id']}: conditional 缺 conditions"})
        if f["verdict"] == "blocked" and not (f["blockers"] or f["conditions"]):
            issues.append({"level": "fail", "detail": f"{f['finding_id']}: blocked 未写阻断原因"})
        if f["verdict"] == "unconfirmed" and not (f["needs_expert"] or f["action"].get("owner")
                                                  or f["action"].get("ask")):
            issues.append({"level": "fail", "detail": f"{f['finding_id']}: unconfirmed 未写确认人或确认事项"})

    review = {d: [f for f in findings if f["dimension"] == d] for d in core.DIMS}
    findings_index = [{"finding_id": f["finding_id"], "dimension": f["dimension"],
                       "verdict": f["verdict"], "severity": f["severity"],
                       "applies_to": f["applies_to"], "claim": f["claim"][:160]} for f in findings]

    ledger, ledger_keys = [], {}
    for f in findings:
        comp = f.get("computation")
        if not comp:
            continue
        key = (comp.get("formula"), json.dumps(comp.get("operands"), sort_keys=True, ensure_ascii=False))
        if key in ledger_keys:
            entry = ledger_keys[key]
            if f["finding_id"] not in entry["used_by"]:
                entry["used_by"].append(f["finding_id"])
            continue
        entry = {"id": comp.get("id") or comp.get("ref") or f"A-{len(ledger) + 1:02d}",
                 "kind": comp.get("kind") or "computed",
                 "formula": comp.get("formula"), "operands": comp.get("operands") or {},
                 "result": comp.get("result"), "computed": comp.get("computed"),
                 "verified": bool(comp.get("verified")), "unit": comp.get("unit") or "",
                 "refs": comp.get("refs") or [],
                 "used_by": [f["finding_id"]]}
        if comp.get("ref"):
            entry["ref"] = comp["ref"]
            entry["ref_resolved"] = comp.get("ref_resolved")
        ledger.append(entry)
        ledger_keys[key] = entry
    if not all(a["verified"] for a in ledger):
        issues.append({"level": "fail", "detail": "算术台账中存在未通过复算的条目"})

    evidence_items, ev_index = [], {}
    for f in findings:
        for e in f["evidence"]:
            key = (e["locator"], e["quote"])
            if key not in ev_index:
                ev_index[key] = {"locator": e["locator"], "quote": e["quote"],
                                 "quote_chars": e["quote_chars"], "quote_sha1_8": core.sha1_8(e["quote"]),
                                 "source_kind": e.get("source_kind"),
                                 "locator_ok": e.get("locator_ok"),
                                 "verified": e.get("quote_verified", False),
                                 "findings": [], "roles": []}
                evidence_items.append(ev_index[key])
            item = ev_index[key]
            if f["finding_id"] not in item["findings"]:
                item["findings"].append(f["finding_id"])
            if e["role"] not in item["roles"]:
                item["roles"].append(e["role"])
            if not e.get("quote_verified"):
                item["verified"] = False
                item.setdefault("hint", e.get("suggest"))
            if e.get("locator_ok") is False:
                item["locator_ok"] = False
    checked = len(evidence_items)
    passed = sum(1 for i in evidence_items if i["verified"])
    unique_locators = len({i["locator"] for i in evidence_items})

    experiment_plan = build_experiment_plan(casepack, notes, conclusion, comparison_cfg)
    overall = build_conclusion(findings, conclusion, notes)
    comparison_rows, comparison_meta = build_comparison(casepack, findings, comparison_cfg)
    sources = build_sources(casepack, findings)
    confirmation = build_confirmation(findings)
    for key, val in (extras.get("confirmation") or {}).items():
        if key in confirmation and isinstance(val, list):
            confirmation[key].extend(val)

    base = core.empty_base()
    base.update({
        "case_id": casepack.get("case_id"),
        "challenge_id": casepack.get("case_id"),
        "task_title": casepack.get("title"),
        "review_scope": {"plans": [p["version"] for p in casepack.get("plans") or []],
                         "dimensions": list(core.DIMS),
                         "materials": [s["rel"] for s in casepack.get("files") or []],
                         "silence_flags": [f["dimension"] for f in casepack.get("silence_flags") or []],
                         "not_covered": [d for d in core.DIMS if not review.get(d)]},
        "experiment_plan": experiment_plan,
        "review": review,
        "findings_index": findings_index,
        "plan_comparison": comparison_rows,
        "plan_comparison_meta": comparison_meta,
        "overall_conclusion": overall,
        "evidence_and_risks": {
            "evidence": [{"finding_id": i["findings"][0], "locator": i["locator"], "quote": i["quote"],
                          "role": i["roles"][0]} for i in evidence_items[:15]],
            "risks": [{"finding_id": f["finding_id"], "claim": f["claim"], "verdict": f["verdict"]}
                      for f in review["risks_exceptions"]],
            "exception_handling": [{"finding_id": f["finding_id"], "conditions": f["conditions"],
                                    "action": f["action"]}
                                   for f in review["risks_exceptions"] if f["conditions"] or any(f["action"].values())],
        },
        "confirmation": confirmation,
        "arithmetic_ledger": ledger,
        "evidence_summary": {"items": checked, "verified": passed, "unverified": checked - passed,
                             "unique_locators": unique_locators, "map": "evidence_map.json"},
        "sources": sources,
        "plan_diff": casepack.get("plan_diff") or [],
        "degradations": [w for w in (casepack.get("warnings") or []) if w] + [str(x) for x in _as_list(extras.get("degradations"))],
        "limitations": [g.get("detail") if isinstance(g, dict) else str(g)
                        for g in (casepack.get("gaps") or [])] + [str(x) for x in _as_list(extras.get("limitations"))],
        "errors": [i for i in issues if i.get("level") == "fail"] + [str(x) for x in _as_list(extras.get("errors"))],
        "change_log": [
            {"step": "S0", "action": "写 partial 骨架 + casepack",
             "detail": f"发现 {len(casepack.get('files') or [])} 个输入文件、"
                       f"{len(casepack.get('plans') or [])} 个方案版本"},
            {"step": "S2-S4", "action": "写入审查项",
             "detail": f"接收 {len(findings)} 条审查项，覆盖 {sum(1 for d in core.DIMS if review.get(d))}/7 类"},
            {"step": "S4", "action": "证据回查与算术复算",
             "detail": f"证据 {checked} 条（通过 {passed}），算术 {len(ledger)} 条"},
            {"step": "S5", "action": "生成 result.json / evidence_map.json / review_report.md",
             "detail": "见 artifacts"},
        ],
        "runtime": {"started_at": None, "seconds": 0.0, "requests": args.requests, "model": None},
        "artifacts": [],
    })

    if not findings:
        base["status"] = "insufficient_evidence"
        base["uncertainty"].append({"kind": "no_findings", "detail": "未收到任何审查项，仅保留骨架与缺口"})
    else:
        incomplete = (checked - passed) > 0 or any(not review.get(d) for d in core.DIMS)
        base["status"] = "partial" if incomplete else "ok"
    if conclusion.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"):
        base["status"] = conclusion["status"]
    if casepack.get("case_file") in (None, "") and not findings:
        base["status"] = "insufficient_evidence"

    state = load_json_opt(os.path.join(args.work, "run_state.json"), {}) or {}
    started = state.get("started_at")
    base["runtime"]["started_at"] = started
    if isinstance(state.get("epoch"), (int, float)):
        base["runtime"]["seconds"] = round(max(0.0, __import__("time").time() - state["epoch"]), 2)

    result, binding = assign_contract(None, contract, contract_values, base)
    result["contract_binding"] = binding

    uncertainty = build_uncertainty(casepack, findings, binding["unfilled"], issues, extras)
    if base["uncertainty"]:
        uncertainty.extend(base["uncertainty"])
    result["uncertainty"] = uncertainty
    result["pending_human"] = build_pending(findings, extras)
    if base["status"] == "ok" and uncertainty and any(
            u.get("kind") in ("silence_unaddressed", "coverage_gap", "evidence_quote_unverified")
            for u in uncertainty if isinstance(u, dict)):
        result["status"] = "partial"
        base["status"] = "partial"

    ev_map = {"generated_by": "assemble.py",
              "quote_verification": {"method": "whitespace-normalized substring, quote>=8 chars",
                                     "checked": checked, "passed": passed, "failed": checked - passed},
              "coverage": {"findings": len(findings),
                           "findings_with_evidence": sum(1 for f in findings if f["evidence"]),
                           "unique_locators": unique_locators,
                           "by_dimension": {d: len(review.get(d) or []) for d in core.DIMS}},
              "items": evidence_items}
    ev_map["unverified"] = [i for i in evidence_items if not i["verified"]]

    os.makedirs(args.output, exist_ok=True)
    paths_out = {}
    for name, obj in (("result.json", result), ("evidence_map.json", ev_map)):
        path = os.path.join(args.output, name)
        ok, msg = core.write_json(path, obj)
        if not ok:
            issues.append({"level": "fail", "detail": f"写盘失败 {path}: {msg}"})
            print(f"[assemble] 写盘失败 {path}: {msg}")
        paths_out[name] = path
    report = render_report(result, ev_map)
    report_path = os.path.join(args.output, "review_report.md")
    try:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(report)
    except OSError as exc:
        print(f"[assemble] 报告写盘失败 {report_path}: {exc}")

    for name in ("result.json", "evidence_map.json", "review_report.md"):
        p = os.path.join(args.output, name)
        if os.path.isfile(p):
            result["artifacts"].append({"path": p, "kind": "json" if name.endswith(".json") else "markdown",
                                        "bytes": os.path.getsize(p)})
    core.write_json(os.path.join(args.output, "result.json"), result)

    print(f"[assemble] findings={len(findings)} evidence={checked} verified={passed} "
          f"ledger={len(ledger)} status={result['status']}")
    print(f"[assemble] result.json / evidence_map.json / review_report.md -> {args.output}")
    fails = [i for i in issues if i.get("level") == "fail"]
    if fails:
        print(f"[assemble] {len(fails)} 个 fail 级问题（详见 result.json errors）：")
        for i in fails[:10]:
            print(f"  - {i['detail']}")
    if args.strict_quotes and passed < checked:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
