"""compare.py — comparison engine, artifact renderer, validator.

Standard library only. Consumes case_pack.json + evidence_ledger.json +
plan_spec.json (the two intermediate representations) and produces the final
result.json plus comparison_matrix.{svg,csv,pdf} and plan_comparison.md.

Deterministic: no randomness, stable sorted ordering everywhere.
"""

import csv
import json
import os
import xml.etree.ElementTree as ET

import ingest

SEVEN_FIELDS = ingest.SEVEN_FIELDS
FIVE_DIMS = ingest.FIVE_DIMS
DEFAULT_WEIGHTS = ingest.DEFAULT_WEIGHTS
DEFAULT_CONTRACT_FIELDS = ingest.DEFAULT_CONTRACT_FIELDS
ALLOWED_STATUS = ingest.ALLOWED_STATUS

FIELD_LABELS = {
    "assumptions": "Assumptions",
    "variables": "Variables",
    "controls": "Controls",
    "steps": "Steps",
    "parameters": "Parameters",
    "resources": "Resources",
    "interpretation_criteria": "Interpretation",
    "goal_fit": "Goal fit",
    "risk": "Risk",
    "cost": "Cost",
    "feasibility": "Feasibility",
    "evidence": "Evidence",
}

_CONSTRAINT_MAP = [
    (("budget", "cost", "total_budget", "总预算", "预算"), "cost_total", "number"),
    (("time", "duration", "deadline", "time_window", "时间", "排期", "工期"), "time_total", "number"),
    (("equipment_available", "equipment", "设备可用", "设备"), "equipment_available", "text"),
    (("lead_time_ok", "lead_time", "货期"), "lead_time_ok", "text"),
]


def load_work_json(work_dir, name):
    p = os.path.join(work_dir, name)
    if not os.path.isfile(p):
        return None, "missing:%s" % name
    data, err = ingest.read_json(p)
    if data is None:
        return None, "bad_json:%s:%s" % (name, err)
    return data, None


def _num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("¥", "").replace("元", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _satisfies(actual, op, expected):
    if actual is None or expected is None:
        return None
    try:
        if op in ("<=", "le", "max"):
            return actual <= expected
        if op in ("<", "lt"):
            return actual < expected
        if op in (">=", "ge", "min"):
            return actual >= expected
        if op in (">", "gt"):
            return actual > expected
        if op in ("==", "=", "eq"):
            return abs(actual - expected) < 1e-9
        if op in ("!=", "ne"):
            return abs(actual - expected) >= 1e-9
    except TypeError:
        return None
    return None


def _match_constraint(name):
    low = str(name).lower()
    for keys, metric, kind in _CONSTRAINT_MAP:
        for k in keys:
            if k.lower() in low or low in k.lower():
                return metric, kind
    return None, None


def _constraint_values(cand):
    """Return metrics a constraint can check for a candidate."""
    return {
        "cost_total": _num(cand.get("cost", {}).get("total")),
        "time_total": _num(cand.get("time", {}).get("total")),
        "equipment_available": (cand.get("feasibility", {}) or {}).get("equipment_available"),
        "lead_time_ok": (cand.get("feasibility", {}) or {}).get("lead_time_ok"),
    }


def compute_cost(spec_cost):
    if not isinstance(spec_cost, dict):
        spec_cost = {}
    currency = spec_cost.get("currency", "CNY")
    lines = spec_cost.get("lines", []) or []
    method = spec_cost.get("method")
    total = _num(spec_cost.get("total"))
    one_time = 0.0
    per_run = 0.0
    runs_default = None
    for ln in lines:
        amt = _num(ln.get("amount"))
        if amt is None:
            continue
        per = (ln.get("per") or "once").strip().lower()
        if per == "once":
            one_time += amt
        else:
            per_run += amt
            r = _num(ln.get("runs"))
            if r is not None and runs_default is None:
                runs_default = r
    if runs_default is None:
        runs_default = 1.0
    computed_total = one_time + per_run * runs_default

    if method == "not_computable":
        final_total = None
        final_method = "not_computable"
    elif method == "override" and total is not None:
        final_total = total
        final_method = "override"
    elif total is not None and method == "computed":
        final_total = total
        final_method = "computed"
    elif lines:
        final_total = computed_total
        final_method = "computed"
    else:
        final_total = None
        final_method = "not_computable"

    return {
        "currency": currency,
        "method": final_method,
        "total": final_total,
        "one_time": round(one_time, 4),
        "per_run": round(per_run, 4),
        "lines": lines,
        "note": spec_cost.get("note", ""),
        "runs_default": runs_default,
    }


def compute_time(spec_time):
    if not isinstance(spec_time, dict):
        spec_time = {}
    parts = spec_time.get("parts", {}) or {}
    keys = ("prep", "scheduling", "execution", "characterization", "rework")
    sums = {}
    total_computed = 0.0
    for k in keys:
        v = _num(parts.get(k))
        if v is None:
            v = 0.0
        sums[k] = v
        total_computed += v
    method = spec_time.get("method")
    total = _num(spec_time.get("total"))
    if method == "not_computable":
        final_total, final_method = None, "not_computable"
    elif method == "override" and total is not None:
        final_total, final_method = total, "override"
    elif total is not None:
        final_total, final_method = total, "computed"
    elif any(sums.values()):
        final_total, final_method = total_computed, "computed"
    else:
        final_total, final_method = None, "not_computable"
    return {
        "unit": spec_time.get("unit", "h"),
        "method": final_method,
        "total": final_total,
        "parts": sums,
        "note": spec_time.get("note", ""),
    }


def _short(value, limit=80):
    if value is None:
        return ""
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s[:limit]


def _check_ir(pack, ledger, spec):
    issues, warnings = [], []
    if spec is None:
        return ["plan_spec.json missing"], []
    candidates = spec.get("candidates", []) or []
    if not candidates:
        issues.append("plan_spec.candidates empty")
    entries = (ledger or {}).get("entries", []) or []
    ledger_ids = {e.get("id") for e in entries if isinstance(e, dict) and e.get("id")}

    for cand in candidates:
        pid = cand.get("plan_id", "?")
        seven = cand.get("seven_fields", {}) or {}
        for f in SEVEN_FIELDS:
            if not str(seven.get(f) or "").strip():
                warnings.append("%s.seven_fields.%s empty" % (pid, f))
        ratings = cand.get("dimension_ratings", {}) or {}
        if not ratings:
            warnings.append("%s.dimension_ratings empty" % pid)
        for d in FIVE_DIMS:
            v = ratings.get(d)
            if v is not None and (not isinstance(v, (int, float)) or v < 0 or v > 5):
                warnings.append("%s.dimension_ratings.%s out of 0-5" % (pid, d))
        refs = cand.get("evidence_refs", []) or []
        for ref in refs:
            if ref not in ledger_ids:
                warnings.append("%s.evidence_refs.%s not in ledger" % (pid, ref))
    if entries:
        for e in entries:
            if not e.get("locator") or not e.get("quote"):
                warnings.append("ledger entry %s missing locator/quote" % e.get("id", "?"))
    sel = spec.get("selection", {})
    if sel and sel.get("chosen") and sel["chosen"] not in [c.get("plan_id") for c in candidates]:
        warnings.append("selection.chosen not among candidates")
    return issues, warnings


def _eliminate(cand, constraints):
    reasons = []
    if not isinstance(constraints, list):
        return reasons
    vals = _constraint_values(cand)
    for cst in constraints:
        if not isinstance(cst, dict):
            continue
        metric, kind = _match_constraint(cst.get("name", ""))
        if metric is None:
            continue
        expected = cst.get("value")
        op = cst.get("op", "<=")
        if kind == "number":
            actual = vals.get(metric)
            exp = _num(expected)
            ok = _satisfies(actual, op, exp)
            if ok is False:
                reasons.append("constraint:%s %s %s (actual=%s)" % (cst.get("name"), op, expected, actual))
        else:
            actual = vals.get(metric)
            exp = expected
            if exp is not None and actual not in (None, "", exp):
                reasons.append("constraint:%s requires %s (actual=%s)" % (cst.get("name"), exp, actual))
    return reasons


def _normalize_weights(weights, ratings_keys):
    src = weights or DEFAULT_WEIGHTS
    present = [d for d in FIVE_DIMS if d in ratings_keys]
    if not present:
        present = list(FIVE_DIMS)
    w = {d: max(0.0, float(src.get(d, DEFAULT_WEIGHTS.get(d, 0.0)))) for d in present}
    total = sum(w.values())
    if total <= 0:
        n = len(present)
        w = {d: 1.0 / n for d in present}
    else:
        w = {d: v / total for d, v in w.items()}
    return w


def _score(ratings, weights):
    total = 0.0
    for d, w in weights.items():
        v = ratings.get(d)
        if v is not None:
            total += w * float(v)
    return total


def _sensitivity(plans, weights, feasible_ids, chosen):
    dims = sorted(weights.keys())
    base_scores = {p["plan_id"]: p["score"] for p in plans}
    chosen_ok = 0
    total = 0
    perturbations = []
    for d in dims:
        for delta in (-0.05, 0.05):
            w2 = dict(weights)
            w2[d] = max(0.0, w2[d] + delta)
            s = sum(w2.values())
            if s <= 0:
                continue
            w2 = {k: v / s for k, v in w2.items()}
            scores = {}
            for p in plans:
                if p["plan_id"] not in feasible_ids:
                    continue
                scores[p["plan_id"]] = _score(p["dimension_ratings"], w2)
            ranking = sorted(scores, key=lambda pid: (-scores[pid], pid))
            total += 1
            top = ranking[0] if ranking else None
            if top == chosen:
                chosen_ok += 1
            perturbations.append({"dimension": d, "delta": delta, "ranking": ranking})
    return {
        "perturbations": perturbations,
        "chosen_robust": "%d/%d" % (chosen_ok, total) if total else "n/a",
        "base_scores": {k: round(v, 6) for k, v in base_scores.items()},
    }


def assemble(pack, ledger, spec, output_dir, work_dir, now):
    pack = pack or {}
    result = {
        "case_id": pack.get("case_id", "unknown"),
        "status": ingest.STATUS_PARTIAL,
        "research_question": None,
        "comparison_frame": None,
        "candidates": [],
        "comparison_matrix": None,
        "selection": None,
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"time": pack.get("generated_at") or now, "step": "inspect", "note": "skeleton written by inspect"}
        ],
        "pending_human": [],
        "errors": list(pack.get("errors", [])),
        "degradations": list(pack.get("degradations", [])),
        "runtime": {"started_at": pack.get("generated_at"), "assemble_at": now},
        "artifacts": [],
        "limitations": [],
    }
    contract_fields = (pack.get("output_contract") or {}).get("fields") or list(DEFAULT_CONTRACT_FIELDS)

    ir_issues, ir_warnings = _check_ir(pack, ledger, spec)
    for w in ir_warnings:
        result["degradations"].append("ir_warning:%s" % w)
    for i in ir_issues:
        result["errors"].append("ir_error:%s" % i)

    if spec is None:
        result["status"] = ingest.STATUS_PARTIAL
        result["limitations"].append("plan_spec.json missing; only skeleton available")
        for f in contract_fields:
            result.setdefault(f, ingest._default_contract_value(f))
        return result

    result["research_question"] = spec.get("research_question")
    frame = spec.get("comparison_frame") or {}
    result["comparison_frame"] = frame
    result["uncertainty"] = spec.get("uncertainty", []) or []
    for item in spec.get("change_log", []) or []:
        result["change_log"].append(item)

    entries = (ledger or {}).get("entries", []) or []
    result["sources"] = entries

    constraints = frame.get("hard_constraints", []) or []
    weights = _normalize_weights(spec.get("weights"), {
        d for c in spec.get("candidates", []) for d in (c.get("dimension_ratings") or {})})

    # ---- build enriched candidates ----
    plans = []
    for i, cand in enumerate(spec.get("candidates", []) or []):
        if not isinstance(cand, dict):
            continue
        pid = cand.get("plan_id") or ("P%d" % (i + 1))
        seven = cand.get("seven_fields", {}) or {}
        ratings = cand.get("dimension_ratings", {}) or {}
        cost = compute_cost(cand.get("cost"))
        time = compute_time(cand.get("time"))
        infeasible = _eliminate(
            {"cost": cost, "time": time, "feasibility": cand.get("feasibility") or {}},
            constraints,
        )
        plan = {
            "plan_id": pid,
            "name": cand.get("name", pid),
            "version": cand.get("version", "v1"),
            "seven_fields": {f: str(seven.get(f) or "") for f in SEVEN_FIELDS},
            "dimension_ratings": {d: ratings.get(d) for d in FIVE_DIMS if d in ratings},
            "feasibility": cand.get("feasibility") or {},
            "risks": cand.get("risks", []) or [],
            "evidence_refs": cand.get("evidence_refs", []) or [],
            "cost": cost,
            "time": time,
            "infeasible_reasons": infeasible,
            "raw": cand,
        }
        plan["score"] = round(_score(plan["dimension_ratings"], weights), 6)
        plans.append(plan)

    # attach evidence entries (resolved) to each plan
    ledger_by_id = {e.get("id"): e for e in entries if isinstance(e, dict) and e.get("id")}
    for plan in plans:
        plan["evidence"] = [ledger_by_id[r] for r in plan["evidence_refs"] if r in ledger_by_id]

    feasible = [p for p in plans if not p["infeasible_reasons"]]
    infeasible = [p for p in plans if p["infeasible_reasons"]]
    feasible_sorted = sorted(feasible, key=lambda p: (-p["score"], p["plan_id"]))
    infeasible_sorted = sorted(infeasible, key=lambda p: (-p["score"], p["plan_id"]))

    # ---- selection ----
    sel = spec.get("selection") or {}
    chosen = sel.get("chosen")
    if chosen and chosen in [p["plan_id"] for p in plans]:
        pass
    elif feasible_sorted:
        chosen = feasible_sorted[0]["plan_id"]
    else:
        chosen = None

    ranking = sel.get("ranking") or []
    if not ranking or set(ranking) != {p["plan_id"] for p in feasible_sorted}:
        ranking = [p["plan_id"] for p in feasible_sorted] + [p["plan_id"] for p in infeasible_sorted]

    rejected = []
    for plan in plans:
        if plan["plan_id"] == chosen:
            continue
        reason = "; ".join(plan["infeasible_reasons"]) if plan["infeasible_reasons"] else (
            "lower weighted score (%.3f vs chosen)" % plan["score"])
        rejected.append({"plan_id": plan["plan_id"], "reason": reason})
    if sel.get("rejected"):
        rejected = sel["rejected"]

    sensitivity = _sensitivity(plans, weights, {p["plan_id"] for p in feasible}, chosen)
    selection = {
        "chosen": chosen,
        "ranking": ranking,
        "rationale": sel.get("rationale", ""),
        "rejected": rejected,
        "sensitivity": sensitivity,
        "sensitivity_notes": sel.get("sensitivity_notes", ""),
    }

    # ---- comparison matrix ----
    plans_by_id = {p["plan_id"]: p for p in plans}
    matrix = {"plans": [p["plan_id"] for p in plans], "fields": list(SEVEN_FIELDS),
              "cells": {}, "scores": {}, "normalized": {}, "eliminations": {}}
    for f in SEVEN_FIELDS:
        matrix["cells"][f] = {p["plan_id"]: p["seven_fields"][f] for p in plans}
    for d in FIVE_DIMS:
        matrix["normalized"][d] = {
            p["plan_id"]: (p["dimension_ratings"].get(d) / 5.0 if p["dimension_ratings"].get(d) is not None else None)
            for p in plans}
    matrix["scores"] = {p["plan_id"]: round(p["score"], 4) for p in plans}
    matrix["eliminations"] = {p["plan_id"]: p["infeasible_reasons"] for p in plans}

    # ---- confirmations / pending_human ----
    confirmations = spec.get("confirmations", []) or []
    pending = []
    for c in confirmations:
        if c.get("status") != "confirmed":
            pending.append("%s: %s" % (c.get("type", "confirmation"), c.get("item", "unnamed")))
    for plan in plans:
        feas = plan["feasibility"]
        if feas.get("equipment_available") in ("no", "partial"):
            pending.append("%s: equipment_available=%s" % (plan["plan_id"], feas.get("equipment_available")))
        if plan["cost"]["method"] == "not_computable":
            pending.append("%s: cost not computable" % plan["plan_id"])
        if plan["time"]["method"] == "not_computable":
            pending.append("%s: time not computable" % plan["plan_id"])
        if not plan["evidence"]:
            pending.append("%s: no evidence attached" % plan["plan_id"])
    if not entries:
        pending.append("evidence_ledger empty; every key number needs locator+quote")
    result["confirmations"] = confirmations
    result["pending_human"] = sorted(set(pending))

    # ---- status ----
    all_seven_complete = all(
        all(str(p["seven_fields"][f]).strip() for f in SEVEN_FIELDS) for p in plans)
    if not plans:
        result["status"] = ingest.STATUS_INSUFFICIENT
    elif chosen is None and not feasible:
        result["status"] = ingest.STATUS_PARTIAL
    elif chosen and entries and all_seven_complete and not [c for c in confirmations if c.get("status") != "confirmed"]:
        result["status"] = ingest.STATUS_OK
    else:
        result["status"] = ingest.STATUS_PARTIAL

    if not feasible and plans:
        result["limitations"].append("all candidates infeasible; chosen=null or best-feasible noted")

    result["candidates"] = plans
    result["comparison_matrix"] = matrix
    result["selection"] = selection
    result["change_log"].append({"time": now, "step": "assemble",
                                 "note": "assembled %d candidates, chosen=%s" % (len(plans), chosen)})

    # ---- fill output_contract fields ----
    for f in contract_fields:
        low = f.lower()
        if low == "experimental_plan":
            result[f] = {
                "testable_question": (result["research_question"] or {}).get("testable_question"),
                "variables": (result["research_question"] or {}).get("variables"),
                "candidates": [
                    {"plan_id": p["plan_id"], "name": p["name"], "version": p["version"],
                     "seven_fields": p["seven_fields"],
                     "cost": p["cost"], "time": p["time"]} for p in plans],
            }
        elif low == "evidence_and_risks":
            result[f] = {
                "evidence": entries,
                "risks": [{"plan_id": p["plan_id"], "risks": p["risks"]} for p in plans],
                "exceptions": [{"plan_id": p["plan_id"], "infeasible_reasons": p["infeasible_reasons"]}
                               for p in plans if p["infeasible_reasons"]],
            }
        elif low == "confirmation_items":
            result[f] = confirmations
        else:
            result[f] = spec.get(f)

    # ---- render artifacts ----
    artifacts = render_artifacts(result, plans_by_id, matrix, output_dir, work_dir, now)
    result["artifacts"] = artifacts
    result["runtime"]["artifacts_at"] = now
    return result


# --------------------------------------------------------------------------
# Artifact rendering
# --------------------------------------------------------------------------

def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _ascii(s, limit=100):
    s = _short(s, limit)
    return s.encode("ascii", "replace").decode("ascii")


def render_csv(matrix, plans_by_id, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field"] + matrix["plans"])
        rows = []
        for f in SEVEN_FIELDS:
            rows.append([FIELD_LABELS.get(f, f)] + [matrix["cells"][f][pid] for pid in matrix["plans"]])
        for d in FIVE_DIMS:
            rows.append(["rating_%s" % d] +
                        [str(plans_by_id[pid]["dimension_ratings"].get(d)) for pid in matrix["plans"]])
        rows.append(["weighted_score"] + [str(matrix["scores"][pid]) for pid in matrix["plans"]])
        rows.append(["cost_total(%s)" % plans_by_id[matrix["plans"][0]]["cost"]["currency"] if matrix["plans"] else "cost"] +
                    [str(plans_by_id[pid]["cost"]["total"]) for pid in matrix["plans"]])
        rows.append(["time_total(h)"] + [str(plans_by_id[pid]["time"]["total"]) for pid in matrix["plans"]])
        rows.append(["feasibility_equipment"] +
                    [str(plans_by_id[pid]["feasibility"].get("equipment_available")) for pid in matrix["plans"]])
        rows.append(["infeasible_reasons"] +
                    ["; ".join(matrix["eliminations"][pid]) for pid in matrix["plans"]])
        w.writerows(rows)


def render_svg(matrix, plans_by_id, path, title):
    plans = matrix["plans"]
    ncols = len(plans) + 1
    col_w = 170
    first_w = 170
    row_h = 26
    header_h = 34
    rows = []
    for f in SEVEN_FIELDS:
        rows.append([FIELD_LABELS.get(f, f)] + [_ascii(matrix["cells"][f].get(pid, ""), 30) for pid in plans])
    for d in FIVE_DIMS:
        rows.append(["rating_%s" % d] + [str(plans_by_id[pid]["dimension_ratings"].get(d)) for pid in plans])
    rows.append(["weighted_score"] + [str(matrix["scores"][pid]) for pid in plans])
    rows.append(["infeasible"] + ["; ".join(matrix["eliminations"][pid])[:30] for pid in plans])

    width = first_w + ncols * col_w
    height = header_h + len(rows) * row_h + 10
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (width, height))
    parts.append('<text x="12" y="22" font-family="Arial" font-size="16" fill="#111">%s</text>'
                 % _xml_escape(_ascii(title, 60)))
    y0 = header_h
    # header
    parts.append('<text x="12" y="%d" font-family="Arial" font-size="12" fill="#333">field</text>' % (y0 - 8))
    for j, pid in enumerate(plans):
        x = first_w + j * col_w + 6
        parts.append('<text x="%d" y="%d" font-family="Arial" font-size="12" fill="#333">%s</text>'
                     % (x, y0 - 8, _xml_escape(_ascii(pid, 20))))
    for i, row in enumerate(rows):
        y = y0 + i * row_h
        if i % 2 == 0:
            parts.append('<rect x="0" y="%d" width="%d" height="%d" fill="#f4f6f8"/>'
                         % (y - row_h + 4, width, row_h))
        parts.append('<text x="12" y="%d" font-family="Arial" font-size="11" fill="#111">%s</text>'
                     % (y, _xml_escape(_ascii(row[0], 22))))
        for j, val in enumerate(row[1:]):
            x = first_w + j * col_w + 6
            parts.append('<text x="%d" y="%d" font-family="Arial" font-size="11" fill="#111">%s</text>'
                         % (x, y, _xml_escape(_ascii(val, 26))))
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")


def render_md(result, plans_by_id, matrix, path):
    lines = ["# 实验方案比较与选择", "",
             "- case_id: `%s`" % result["case_id"],
             "- status: `%s`" % result["status"],
             "- chosen: `%s`" % result["selection"]["chosen"], ""]
    lines.append("## 选择结论")
    lines.append(result["selection"]["rationale"] or "(未填写 rationale)")
    lines.append("")
    lines.append("## 候选方案七类字段对照")
    header = ["字段"] + matrix["plans"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for f in SEVEN_FIELDS:
        cells = [FIELD_LABELS.get(f, f)] + [
            matrix["cells"][f].get(pid, "").replace("\n", " ") for pid in matrix["plans"]]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## 五维评分与加权分")
    lines.append("| 方案 | " + " | ".join(FIVE_DIMS) + " | 加权分 |")
    lines.append("|---|" + "---|" * (len(FIVE_DIMS) + 1))
    for pid in matrix["plans"]:
        p = plans_by_id[pid]
        cells = [str(p["dimension_ratings"].get(d)) for d in FIVE_DIMS]
        lines.append("| %s | %s | %s |" % (pid, " | ".join(cells), matrix["scores"][pid]))
    lines.append("")
    lines.append("## 舍弃理由")
    for r in result["selection"]["rejected"]:
        lines.append("- `%s`: %s" % (r["plan_id"], r["reason"]))
    lines.append("")
    lines.append("## 证据台账（locator + quote）")
    for e in result["sources"]:
        lines.append("- `%s` [%s] %s → `%s`" % (
            e.get("id"), e.get("kind"), _short(e.get("claim"), 60), e.get("locator")))
        lines.append("  > %s" % _short(e.get("quote"), 160))
    lines.append("")
    lines.append("## 待确认项")
    for item in result["pending_human"]:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 已知限制")
    for item in result["limitations"]:
        lines.append("- %s" % item)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_pdf(path, lines, title):
    esc = lambda s: (s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))  # noqa: E731
    content = []
    y = 780
    content.append("BT /F1 14 Tf 40 %d Td (%s) Tj ET" % (y, esc(_ascii(title, 60))))
    y -= 26
    for ln in lines:
        content.append("BT /F1 9 Tf 40 %d Td (%s) Tj ET" % (y, esc(_ascii(ln, 100))))
        y -= 14
        if y < 50:
            break
    stream = "\n".join(content)
    stream_b = stream.encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream_b) + stream_b + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(len(buf))
        buf += b"%d 0 obj\n" % i
        buf += body
        buf += b"\nendobj\n"
    xref_pos = len(buf)
    n = len(objs) + 1
    buf += b"xref\n0 %d\n" % n
    buf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += b"%010d 00000 n \n" % off
    buf += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % n
    buf += b"startxref\n%d\n%%%%EOF\n" % xref_pos
    with open(path, "wb") as fh:
        fh.write(bytes(buf))


def render_artifacts(result, plans_by_id, matrix, output_dir, work_dir, now):
    os.makedirs(output_dir, exist_ok=True)
    artifacts = []
    title = "comparison matrix (%s)" % result["case_id"]

    csv_path = os.path.join(output_dir, "comparison_matrix.csv")
    render_csv(matrix, plans_by_id, csv_path)
    artifacts.append({"path": csv_path, "kind": "csv", "note": "UTF-8 with BOM"})

    svg_path = os.path.join(output_dir, "comparison_matrix.svg")
    render_svg(matrix, plans_by_id, svg_path, title)
    artifacts.append({"path": svg_path, "kind": "svg", "note": "editable vector"})

    md_path = os.path.join(output_dir, "plan_comparison.md")
    render_md(result, plans_by_id, matrix, md_path)
    artifacts.append({"path": md_path, "kind": "md", "note": "editable report"})

    pdf_lines = ["field | " + " | ".join(matrix["plans"])]
    for f in SEVEN_FIELDS:
        pdf_lines.append("%s | %s" % (FIELD_LABELS[f],
                                      " | ".join(_ascii(matrix["cells"][f].get(pid, ""), 20) for pid in matrix["plans"])))
    pdf_path = os.path.join(output_dir, "comparison_matrix.pdf")
    write_pdf(pdf_path, pdf_lines, title)
    artifacts.append({"path": pdf_path, "kind": "pdf", "note": "ASCII numeric summary"})

    return artifacts


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_result(result_path, work_dir, input_root=None):
    issues, warnings = [], []
    data = None
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "issues": ["result.json not parseable: %s" % exc], "warnings": []}

    if not data.get("case_id"):
        issues.append("missing case_id")
    if data.get("status") not in ALLOWED_STATUS:
        issues.append("status not in allowed set: %r" % data.get("status"))

    pack = None
    if work_dir and os.path.isfile(os.path.join(work_dir, "case_pack.json")):
        pack, _ = load_work_json(work_dir, "case_pack.json")
    fields = ((pack or {}).get("output_contract") or {}).get("fields") or list(DEFAULT_CONTRACT_FIELDS)
    for f in fields:
        if f not in data:
            issues.append("missing output_contract field: %s" % f)
    if not data.get("sources"):
        warnings.append("sources empty (every key number needs locator+quote)")
    else:
        for s in data["sources"]:
            if not s.get("locator") or not s.get("quote"):
                issues.append("source entry missing locator/quote: %s" % s.get("id", "?"))

    for c in data.get("candidates", []):
        pid = c.get("plan_id", "?")
        for f in SEVEN_FIELDS:
            if not str(c.get("seven_fields", {}).get(f) or "").strip():
                issues.append("%s.seven_fields.%s empty" % (pid, f))
        for d in FIVE_DIMS:
            if d not in c.get("dimension_ratings", {}):
                warnings.append("%s missing dimension_ratings.%s" % (pid, d))
    if data.get("selection") is None:
        issues.append("selection missing")

    for a in data.get("artifacts", []):
        p = a.get("path")
        if not p or not os.path.isfile(p):
            warnings.append("artifact missing on disk: %s" % p)
            continue
        kind = a.get("kind")
        try:
            if kind == "svg":
                ET.parse(p)
            elif kind == "csv":
                with open(p, "rb") as fh:
                    head = fh.read(3)
                if head != b"\xef\xbb\xbf":
                    warnings.append("csv without BOM: %s" % p)
            elif kind == "pdf":
                with open(p, "rb") as fh:
                    head = fh.read(1024)
                if not head.startswith(b"%PDF-"):
                    issues.append("pdf not starting %PDF-: %s" % p)
                else:
                    with open(p, "rb") as fh:
                        tail = fh.read()[-64:]
                    if b"%%EOF" not in tail:
                        issues.append("pdf missing %%EOF: %s" % p)
        except Exception as exc:  # noqa: BLE001
            issues.append("artifact %s invalid: %s" % (p, exc))

    return {"valid": len(issues) == 0, "issues": issues, "warnings": warnings}
