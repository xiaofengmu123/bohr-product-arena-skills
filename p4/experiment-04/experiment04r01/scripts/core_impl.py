#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation of the experiment-plan comparison Skill.

Design goals (this variant = evidence-traceability hardening):
  * partial-first: `inspect` always writes a valid result.json skeleton before
    any analysis, so a mid-run failure never leaves an empty deliverable;
  * deterministic: no random seeds, no wall-clock in the result, stable key
    ordering, stable candidate ordering -> repeated runs produce byte-identical
    results for identical inputs;
  * evidence ledger: every conclusion / number / parameter window is normalized
    into a global `evidence_map` with claim-level locator + verbatim quote;
  * real arithmetic: six cost categories and five time parts, one-off vs
    per-run split, hard-threshold elimination, weight sensitivity.

Standard library only. Run `python scripts/main.py --help` for usage.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

VERSION = "1.0.0"
SKILL_NAME = "experiment-plan-comparison"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2_000_000

DIMENSIONS = ["goal_fit", "risk", "cost", "feasibility", "evidence"]
DEFAULT_WEIGHTS = {
    "goal_fit": 0.25,
    "feasibility": 0.25,
    "evidence": 0.20,
    "cost": 0.15,
    "risk": 0.15,
}
COST_CATEGORIES = [
    "equipment_time",  # 机时
    "consumables",     # 耗材
    "labor",           # 人力
    "sample_prep",     # 样品准备
    "scheduling",      # 排期
    "rework",          # 返工
]
TIME_PARTS = [
    "sample_prep",  # 样品准备
    "scheduling",   # 设备排期
    "execution",    # 实验执行
    "queue",        # 表征排队
    "rework",       # 返工
]
SEVEN_FIELDS = [
    "assumptions",
    "variables",
    "controls",
    "steps",
    "parameters",
    "resources",
    "interpretation_criteria",
]
DEFAULT_CONTRACT_KEYS = [
    "experimental_plan",
    "evidence_and_risks",
    "confirmation_items",
]

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUS_SET = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

ID_ISH = {"plan_id", "planid", "id", "name", "title", "方案", "方案名", "编号"}
FIELD_TOKENS = {
    "steps": {"steps", "step", "procedure", "步骤", "流程"},
    "parameters": {"parameters", "parameter", "params", "参数", "参数范围", "窗口"},
    "cost": {"cost", "costs", "budget", "成本", "费用", "报价"},
    "time": {"time", "duration", "schedule", "timeline", "时间", "排期", "货期"},
    "risks": {"risks", "risk", "风险"},
    "variables": {"variables", "变量"},
    "controls": {"controls", "control", "对照", "对照组"},
    "assumptions": {"assumptions", "assumption", "假设"},
    "resources": {"resources", "资源"},
    "interpretation_criteria": {"interpretation_criteria", "interpretation", "判读标准", "判读"},
    "seven_fields": {"seven_fields"},
}
CASE_ID_KEYS = ("case_id", "caseId", "challenge_id", "challengeId", "id")
CONSTRAINT_RE = re.compile(
    r"(budget|constraint|limit|deadline|window|max_|min_|cap|threshold|"
    r"预算|约束|上限|下限|时间窗|货期|档期)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# I/O helpers (tolerant, deterministic)
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerating BOM and bad encoding. Returns (text, err)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if size > max_bytes:
        return None, "skipped (larger than %dB): %s" % (max_bytes, path)
    data = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                data = fh.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if data is None:
        try:
            with open(path, "rb") as fh:
                data = fh.read().decode("utf-8", "replace")
        except OSError as exc:
            return None, "unreadable: %s" % exc
    return data, None


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_json(path, obj):
    """Deterministic JSON dump: sorted keys, no ASCII escaping, stable order."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    write_text(path, text + "\n")


def load_json(path):
    """Tolerant JSON load. Returns (obj_or_None, [errors])."""
    txt, err = read_text(path)
    if err:
        return None, [err]
    try:
        return json.loads(txt), []
    except Exception as exc:  # noqa: BLE001 - keep the pipeline alive
        return None, ["bad json in %s: %s" % (path, exc)]


def list_input_files(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        names.sort()
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({
                "path": full,
                "rel": rel,
                "size": size,
                "ext": os.path.splitext(name)[1].lower(),
            })
    return files


# ---------------------------------------------------------------------------
# Input discovery: never assume fixed key names
# ---------------------------------------------------------------------------

def first_value(obj, keys):
    """First scalar value for any of `keys` in a shallow/deep walk."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keys and not isinstance(v, (dict, list)):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found[0] if found else None


def candidate_score(dct):
    if not isinstance(dct, dict):
        return 0
    keys = {str(k).lower() for k in dct}
    score = 0
    if keys & ID_ISH:
        score += 1
    for tokens in FIELD_TOKENS.values():
        if keys & tokens:
            score += 1
    return score


def is_candidate(dct):
    return isinstance(dct, dict) and candidate_score(dct) >= 2


def stable_key(dct, path):
    pid = dct.get("plan_id") or dct.get("planId") or dct.get("id") or dct.get("name")
    if isinstance(pid, dict):
        pid = pid.get("id") or pid.get("name")
    return str(pid) if pid not in (None, "") else "path:" + path


def collect_candidates(obj):
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            if is_candidate(node):
                out.append((path, node))
            for k, v in node.items():
                walk(v, path + "/" + str(k))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, path + "/%d" % i)

    walk(obj, "$")
    seen = set()
    result = []
    for path, dct in out:
        key = stable_key(dct, path)
        if key in seen:
            continue
        seen.add(key)
        result.append({"path": path, "data": dct})
    return result


def normalize_raw_candidates(cands):
    out = []
    for i, item in enumerate(cands):
        dct = item["data"]
        pid = dct.get("plan_id") or dct.get("planId") or dct.get("id") or dct.get("name") or dct.get("title")
        if isinstance(pid, dict):
            pid = pid.get("id") or pid.get("name")
        if pid in (None, ""):
            pid = "P%d" % (i + 1)
        name = dct.get("name") or dct.get("title") or str(pid)
        version = dct.get("version")
        out.append({
            "plan_id": str(pid),
            "name": str(name),
            "version": str(version) if isinstance(version, (str, int)) else "v?",
            "path": item["path"],
            "raw": dct,
        })
    return out


def collect_constraints(obj):
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            # structured constraint-like dict, captured regardless of its key name
            if node.get("value") is not None and (node.get("op") or node.get("name") or node.get("field")):
                out.append({
                    "name": node.get("name") or node.get("field"),
                    "field": node.get("field") or node.get("name"),
                    "value": node.get("value"),
                    "op": node.get("op"),
                    "raw": node,
                    "path": path,
                })
            for k, v in node.items():
                child_path = path + "/" + str(k)
                if isinstance(v, (int, float)) and CONSTRAINT_RE.search(str(k)):
                    out.append({"name": str(k), "field": str(k), "value": v,
                                "op": None, "raw": v, "path": child_path})
                else:
                    walk(v, child_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, path + "/%d" % i)

    walk(obj, "$")
    return out


def find_case_json(files):
    for f in files:
        if f["rel"].lower() == "case.json" or f["rel"].lower().endswith("/case.json"):
            return f["path"]
    for f in files:
        if f["ext"] == ".json":
            return f["path"]
    return None


def build_case_pack(input_dir):
    pack = {
        "input_dir": input_dir,
        "case_id": None,
        "output_contract": None,
        "contract_keys": list(DEFAULT_CONTRACT_KEYS),
        "candidates": [],
        "constraints": [],
        "research_question_raw": None,
        "objectives_raw": [],
        "files": [],
        "raw_json": None,
        "raw_json_path": None,
        "errors": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        pack["errors"].append("input dir not found: %s" % input_dir)
        return pack

    files = list_input_files(input_dir)
    pack["files"] = files

    case_path = find_case_json(files)
    if case_path is None:
        pack["errors"].append("no case.json found under %s" % input_dir)
        return pack

    obj, errs = load_json(case_path)
    pack["errors"].extend(errs)
    if obj is None:
        return pack

    pack["raw_json"] = obj
    pack["raw_json_path"] = os.path.relpath(case_path, input_dir).replace(os.sep, "/")
    pack["case_id"] = (first_value(obj, CASE_ID_KEYS)
                       or os.path.splitext(os.path.basename(case_path))[0])

    oc = obj.get("output_contract")
    if isinstance(oc, dict):
        pack["output_contract"] = oc
        pack["contract_keys"] = list(oc.keys()) or list(DEFAULT_CONTRACT_KEYS)
    else:
        pack["output_contract"] = None
        pack["contract_keys"] = list(DEFAULT_CONTRACT_KEYS)

    rq = obj.get("research_question") or obj.get("question") or obj.get("goal") or obj.get("objective")
    if rq is not None:
        pack["research_question_raw"] = rq

    objectives = obj.get("objectives") or obj.get("goals")
    if isinstance(objectives, list):
        pack["objectives_raw"] = objectives

    pack["candidates"] = normalize_raw_candidates(collect_candidates(obj))
    pack["constraints"] = collect_constraints(obj)
    return pack


# ---------------------------------------------------------------------------
# Arithmetic: cost / time with one-off vs per-run split
# ---------------------------------------------------------------------------

def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_cost(candidate):
    override = candidate.get("cost_override")
    if isinstance(override, dict) and override.get("total") is not None:
        return {
            "method": "override",
            "total": to_float(override.get("total")),
            "currency": override.get("currency", "CNY"),
            "one_off_total": to_float(override.get("one_off_total")),
            "per_run_total": to_float(override.get("per_run_total")),
            "lines": override.get("lines", []),
            "note": override.get("note", ""),
            "evidence": override.get("evidence", ""),
        }

    lines = candidate.get("cost_lines") or []
    if not lines:
        return {"method": "not_computable", "total": None, "currency": "CNY",
                "one_off_total": None, "per_run_total": None, "lines": [], "note": ""}

    items = []
    total = 0.0
    one_off = 0.0
    per_run = 0.0
    for ln in lines:
        qty = to_float(ln.get("quantity"), 0.0) or 0.0
        price = to_float(ln.get("unit_price"), 0.0) or 0.0
        amount = round(qty * price, 4)
        total += amount
        if ln.get("one_off"):
            one_off += amount
        else:
            per_run += amount
        items.append({
            "category": ln.get("category", ""),
            "item": ln.get("item", ""),
            "unit": ln.get("unit", ""),
            "unit_price": price,
            "quantity": qty,
            "amount": amount,
            "one_off": bool(ln.get("one_off")),
            "evidence": ln.get("evidence", {}),
        })
    return {
        "method": "computed",
        "total": round(total, 2),
        "currency": candidate.get("currency", "CNY"),
        "one_off_total": round(one_off, 2),
        "per_run_total": round(per_run, 2),
        "lines": items,
        "note": "",
    }


def compute_time(candidate):
    override = candidate.get("time_override")
    if isinstance(override, dict) and override.get("total") is not None:
        return {
            "method": "override",
            "total": to_float(override.get("total")),
            "unit": override.get("unit", "h"),
            "parts": override.get("parts", {}),
            "computed": bool(override.get("computed", False)),
            "note": override.get("note", ""),
            "evidence": override.get("evidence", ""),
        }

    parts = candidate.get("time_parts") or {}
    if not parts:
        return {"method": "not_computable", "total": None, "unit": "h",
                "parts": {}, "note": ""}

    computed = {}
    total = 0.0
    for part in TIME_PARTS:
        val = to_float(parts.get(part), 0.0) or 0.0
        computed[part] = val
        total += val
    return {
        "method": "computed",
        "total": round(total, 2),
        "unit": parts.get("unit", "h"),
        "parts": computed,
        "computed": True,
        "note": "",
    }


def extract_constraint(pack, names):
    for con in pack.get("constraints", []):
        if con.get("name") in names or con.get("field") in names:
            return con.get("value")
    raw = pack.get("raw_json") or {}
    for name in names:
        val = first_value(raw, (name,))
        if val is not None:
            return val
    return None


def compare(actual, op, value):
    try:
        if op in ("<", "lt"):
            return actual < value
        if op in ("<=", "le", "lte"):
            return actual <= value
        if op in (">", "gt"):
            return actual > value
        if op in (">=", "ge", "gte"):
            return actual >= value
        if op in ("==", "eq"):
            return actual == value
        if op in ("!=", "ne"):
            return actual != value
        return actual > value
    except TypeError:
        return False


def evaluate_hard_constraint(candidate, hc):
    """Return True when the candidate *violates* the constraint."""
    field = str(hc.get("field", ""))
    op = str(hc.get("op", "<="))
    value = hc.get("value")
    if value is None:
        return False
    if field in ("cost", "cost_total", "total_cost", "budget", "budget_cny"):
        actual = candidate["computed_cost"].get("total")
    elif field in ("time", "time_total", "total_time", "duration", "deadline"):
        actual = candidate["computed_time"].get("total")
    else:
        actual = candidate.get(field)
        if isinstance(actual, (dict, list)):
            actual = None
    if actual is None:
        return False
    return not compare(actual, op, value)


# ---------------------------------------------------------------------------
# Scoring, thresholds, sensitivity
# ---------------------------------------------------------------------------

def clamp(value, low, high):
    if value is None:
        return None
    return max(low, min(high, value))


def normalize_weights(weights):
    weights = dict(weights or DEFAULT_WEIGHTS)
    out = {}
    for dim in DIMENSIONS:
        out[dim] = float(weights.get(dim, 0.0))
    total = sum(v for v in out.values() if v > 0)
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: (v / total) for k, v in out.items()}


def score_candidate(candidate, weights):
    ratings = candidate.get("dimension_ratings") or {}
    wsum = 0.0
    ssum = 0.0
    for dim, weight in weights.items():
        value = ratings.get(dim)
        value = clamp(value, 0, 5)
        if value is None:
            continue
        wsum += weight
        ssum += weight * value
    if wsum <= 0:
        return None
    return round(ssum / wsum, 4)


def apply_thresholds(candidates, pack, analysis):
    budget = extract_constraint(pack, ("budget", "budget_cny", "total_budget", "max_cost"))
    deadline = extract_constraint(pack, ("deadline", "deadline_h", "max_time", "time_budget", "time_window"))

    frame = (analysis or {}).get("comparison_frame") or {}
    hard = frame.get("hard_constraints") or []

    for cand in candidates:
        reasons = list(cand.get("infeasible_reasons") or [])
        cost = cand.get("computed_cost") or {}
        tm = cand.get("computed_time") or {}
        if budget is not None and cost.get("total") is not None and cost["total"] > budget:
            reasons.append("over_budget(%.2f>%.2f)" % (cost["total"], budget))
        if deadline is not None and tm.get("total") is not None and tm["total"] > deadline:
            reasons.append("over_time_window(%.2fh>%.2fh)" % (tm["total"], deadline))
        feas = cand.get("feasibility") or {}
        if feas.get("equipment_available") == "no":
            reasons.append("equipment_unavailable")
        for hc in hard:
            if evaluate_hard_constraint(cand, hc):
                reasons.append(str(hc.get("name") or hc.get("field") or "hard_constraint"))
        seen = set()
        dedup = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                dedup.append(r)
        cand["infeasible_reasons"] = dedup
        cand["infeasible"] = bool(dedup)
    return candidates


def run_sensitivity(candidates, weights, base_ranking):
    results = []
    for dim in DIMENSIONS:
        for delta in (+0.05, -0.05):
            w = dict(weights)
            w[dim] = max(0.0, w.get(dim, 0.0) + delta)
            wn = normalize_weights(w)
            feasible = [c for c in candidates if not c.get("infeasible")]
            ranked = sorted(
                feasible,
                key=lambda c: (score_candidate(c, wn) if score_candidate(c, wn) is not None else -1.0),
                reverse=True,
            )
            ids = [c["plan_id"] for c in ranked]
            results.append({
                "dimension": dim,
                "delta": round(delta, 2),
                "ranking": ids,
                "changed": ids != base_ranking,
            })
    robust = all(not r["changed"] for r in results)
    return {
        "robust": robust,
        "note": "per-dimension weight perturbed by +/-0.05, then renormalized",
        "perturbations": results,
    }


# ---------------------------------------------------------------------------
# Evidence map (the hardened, finer ledger)
# ---------------------------------------------------------------------------

def build_evidence_map(analysis, candidates, selection, rejected):
    analysis = analysis or {}
    sources = {}
    for s in analysis.get("sources", []):
        rid = s.get("ref_id") or s.get("id") or ("S%d" % (len(sources) + 1))
        sources[str(rid)] = {
            "ref_id": str(rid),
            "locator": s.get("locator", ""),
            "quote": s.get("quote", ""),
            "kind": s.get("kind", ""),
            "title": s.get("title", ""),
        }

    claims = []
    seen = set()

    def add(subject, field, entry):
        if not isinstance(entry, dict):
            return
        cid = entry.get("claim_id") or ("C%d" % (len(claims) + 1))
        if cid in seen:
            return
        seen.add(cid)
        quote = entry.get("quote") or ""
        locator = entry.get("locator") or ""
        status = "evidenced" if (quote or locator) else "not_reported"
        claims.append({
            "claim_id": str(cid),
            "claim": entry.get("claim", ""),
            "subject": subject,
            "field": field or entry.get("field", ""),
            "assertion_type": entry.get("assertion_type", "fact"),
            "strength": entry.get("strength", "reported" if status == "evidenced" else "not_reported"),
            "locator": locator,
            "quote": quote,
            "kind": entry.get("kind", ""),
            "source_ref": entry.get("source_ref") or entry.get("ref_id"),
            "status": status,
            "conflict": entry.get("conflict"),
        })

    for entry in analysis.get("frame_evidence", []):
        add("frame", entry.get("field", "frame"), entry)

    for cand in candidates:
        for entry in cand.get("evidence", []):
            add(cand["plan_id"], entry.get("field", ""), entry)
        for ln in cand.get("cost_lines", []):
            ev = ln.get("evidence")
            if ev:
                merged = dict(ev)
                merged.setdefault("claim", "%s cost line" % (ln.get("item", "?")))
                add(cand["plan_id"], "cost", merged)
        for entry in cand.get("time_evidence", []):
            add(cand["plan_id"], "time", entry)

    for entry in analysis.get("selection_evidence", []):
        add("selection", entry.get("field", "selection"), entry)

    coverage = {
        "total_claims": len(claims),
        "evidenced": sum(1 for c in claims if c["status"] == "evidenced"),
        "not_reported": sum(1 for c in claims if c["status"] == "not_reported"),
        "conflicts": sum(1 for c in claims if c.get("conflict")),
    }

    conclusion_map = dict(analysis.get("conclusion_links") or {})
    conclusion_map.setdefault("chosen", [c["claim_id"] for c in claims if c["subject"] == "selection"])
    conclusion_map.setdefault(
        "hard_threshold_decisions",
        [c["claim_id"] for c in claims if c["field"] in ("cost", "time")],
    )
    for rej in rejected:
        key = "rejected_" + str(rej.get("plan_id"))
        conclusion_map.setdefault(key, [])

    return {
        "claims": claims,
        "coverage": coverage,
        "conclusion_map": conclusion_map,
        "sources": list(sources.values()),
    }


# ---------------------------------------------------------------------------
# Matrix and artifacts
# ---------------------------------------------------------------------------

def fmt(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def build_matrix(candidates, weights):
    plans = [c["plan_id"] for c in candidates]
    dim_scores = {}
    for dim in DIMENSIONS:
        dim_scores[dim] = {c["plan_id"]: c.get("dimension_ratings", {}).get(dim) for c in candidates}
    dim_norm = {}
    for dim, row in dim_scores.items():
        dim_norm[dim] = {}
        for plan, val in row.items():
            dim_norm[dim][plan] = round(val / 5.0, 4) if isinstance(val, (int, float)) else None
    field_cov = {}
    for field in SEVEN_FIELDS:
        field_cov[field] = {}
        for c in candidates:
            sf = c.get("seven_fields") or {}
            field_cov[field][c["plan_id"]] = bool(str(sf.get(field, "")).strip())
    return {
        "dimensions": DIMENSIONS,
        "seven_fields": SEVEN_FIELDS,
        "plans": plans,
        "weights": normalize_weights(weights),
        "dimension_scores": dim_scores,
        "dimension_normalized": dim_norm,
        "weighted_score": {c["plan_id"]: c.get("weighted_score") for c in candidates},
        "feasibility": {c["plan_id"]: ("infeasible" if c.get("infeasible") else "feasible") for c in candidates},
        "infeasible_reasons": {c["plan_id"]: c.get("infeasible_reasons", []) for c in candidates},
        "cost": {c["plan_id"]: c.get("computed_cost") for c in candidates},
        "time": {c["plan_id"]: c.get("computed_time") for c in candidates},
        "seven_field_coverage": field_cov,
    }


def matrix_rows(matrix, candidates):
    plans = [c["plan_id"] for c in candidates]
    rows = []
    for dim in DIMENSIONS:
        rows.append(["dimension_score_%s_0_5" % dim] + [fmt(matrix["dimension_scores"][dim].get(p)) for p in plans])
    rows.append(["weighted_score"] + [fmt(matrix["weighted_score"].get(p)) for p in plans])
    rows.append(["feasibility"] + [matrix["feasibility"].get(p) for p in plans])
    rows.append(["cost_total_CNY"] + [fmt((matrix["cost"].get(p) or {}).get("total")) for p in plans])
    rows.append(["time_total_h"] + [fmt((matrix["time"].get(p) or {}).get("total")) for p in plans])
    for field in SEVEN_FIELDS:
        rows.append(["seven_field_%s" % field] + [matrix["seven_field_coverage"][field].get(p) for p in plans])
    return rows


def render_csv(matrix, candidates, selection, path):
    plans = [c["plan_id"] for c in candidates]
    rows = [["field"] + plans]
    rows.extend(matrix_rows(matrix, candidates))
    rows.append(["infeasible_reasons"])
    for c in candidates:
        rows.append([c["plan_id"] + "_reasons"] + ["; ".join(c.get("infeasible_reasons", [])) or ""])
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


def esc_xml(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_svg(matrix, candidates, selection, title, path):
    plans = [c["plan_id"] for c in candidates]
    header = ["field"] + plans
    rows = matrix_rows(matrix, candidates)
    ncols = 1 + len(plans)
    nrows = 1 + len(rows)
    colw = 120
    rowh = 22
    x0, y0 = 16, 30
    width = x0 * 2 + ncols * colw
    height = y0 + nrows * rowh + 10
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="monospace">' % (width, height, width, height),
        '<text x="%d" y="20" font-size="14" font-weight="bold">%s</text>' % (x0, esc_xml(title)),
    ]
    for j, head in enumerate(header):
        x = x0 + j * colw
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#e8eef7" stroke="#888"/>'
                     % (x, y0, colw, rowh))
        parts.append('<text x="%d" y="%d" font-size="12">%s</text>'
                     % (x + 4, y0 + 15, esc_xml(head)))
    for i, row in enumerate(rows):
        y = y0 + (i + 1) * rowh
        for j, val in enumerate(row):
            x = x0 + j * colw
            fill = "#f5f5f5" if j == 0 else "#ffffff"
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#888"/>'
                         % (x, y, colw, rowh, fill))
            parts.append('<text x="%d" y="%d" font-size="11">%s</text>'
                         % (x + 4, y + 15, esc_xml(val)))
    parts.append("</svg>")
    write_text(path, "\n".join(parts))


def render_markdown(matrix, candidates, selection, evidence_map, path):
    plans = [c["plan_id"] for c in candidates]
    lines = [
        "# 实验方案比较（实验方案比较与选择 Skill 产物）",
        "",
        "> 本文件与 result.json、comparison_matrix.csv 由脚本确定性生成；",
        "> 数字以 result.json 为准，本表供人工复核与编辑。",
        "",
        "## 1. 可比矩阵",
        "",
        "| field | " + " | ".join(plans) + " |",
        "|-" * (1 + len(plans)) + "|",
    ]
    for row in matrix_rows(matrix, candidates):
        lines.append("| " + " | ".join(fmt(v) for v in row) + " |")
    lines.append("")
    lines.append("## 2. 选择结论")
    sel = selection or {}
    lines.append("- chosen: `%s`" % (sel.get("chosen") or "null"))
    lines.append("- ranking: `%s`" % ", ".join(sel.get("ranking") or []))
    if sel.get("rationale"):
        lines.append("- rationale: %s" % sel["rationale"])
    for rej in sel.get("rejected") or []:
        lines.append("- rejected `%s`: %s" % (rej.get("plan_id"), rej.get("reason", "")))
    lines.append("- sensitivity.robust: `%s`" % (sel.get("sensitivity") or {}).get("robust"))
    lines.append("")
    lines.append("## 3. 证据溯源（evidence_map）")
    cov = (evidence_map or {}).get("coverage") or {}
    lines.append("- 总结论 %d 条；已挂逐字证据 %d 条；未报告 %d 条；冲突 %d 条"
                 % (cov.get("total_claims", 0), cov.get("evidenced", 0),
                    cov.get("not_reported", 0), cov.get("conflicts", 0)))
    for claim in (evidence_map or {}).get("claims", [])[:40]:
        lines.append("- `%s` [%s/%s] %s → %s"
                     % (claim.get("claim_id"), claim.get("subject"), claim.get("field"),
                        claim.get("claim", ""), claim.get("locator", "")))
    lines.append("")
    write_text(path, "\n".join(lines) + "\n")


def esc_pdf(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def ascii_only(text):
    return text.encode("ascii", "replace").decode("ascii")


def build_pdf(stream_lines):
    content = b"\n".join(line.encode("ascii", "replace") for line in stream_lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for i, body in enumerate(objects, 1):
        offsets[i] = len(out)
        out += b"%d 0 obj\n" % i
        out += body
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for i in range(1, len(objects) + 1):
        out += b"%010d 00000 n \n" % offsets[i]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_pos)
    return bytes(out)


def render_pdf(matrix, candidates, selection, path):
    lines = []
    y = 750
    lines.append("BT /F1 11 Tf 40 %d Td (%s) Tj ET" % (y, esc_pdf(ascii_only("Experiment Plan Comparison"))))
    y -= 16
    chosen = (selection or {}).get("chosen") or "none"
    lines.append("BT /F1 9 Tf 40 %d Td (%s) Tj ET" % (y, esc_pdf(ascii_only("chosen: " + str(chosen)))))
    y -= 14
    for cand in candidates:
        cost = (cand.get("computed_cost") or {}).get("total")
        tm = (cand.get("computed_time") or {}).get("total")
        feas = "infeasible" if cand.get("infeasible") else "feasible"
        text = "%s  score=%s  cost=%s  time=%s  %s" % (
            cand.get("plan_id"), fmt(cand.get("weighted_score")),
            fmt(cost), fmt(tm), feas)
        lines.append("BT /F1 9 Tf 40 %d Td (%s) Tj ET" % (y, esc_pdf(ascii_only(text))))
        y -= 13
    lines.append("BT /F1 8 Tf 40 %d Td (%s) Tj ET" % (y, esc_pdf(ascii_only("ASCII summary; full matrix in CSV/MD/SVG"))))
    write_bytes(path, build_pdf(lines))


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def make_skeleton(pack, status):
    change_log = [
        {"ts": "T+00s", "seq": 1, "step": "inspect",
         "note": "partial skeleton written before analysis (partial-first rule)"},
    ]
    result = {
        "case_id": pack.get("case_id"),
        "status": status,
        "research_question": {},
        "comparison_frame": {},
        "candidates": [],
        "comparison_matrix": {},
        "selection": {"chosen": None, "ranking": [], "rationale": "", "rejected": [], "sensitivity": {}},
        "evidence_map": {"claims": [], "coverage": {
            "total_claims": 0, "evidenced": 0, "not_reported": 0, "conflicts": 0},
            "conclusion_map": {}, "sources": []},
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": change_log,
        "errors": list(pack.get("errors", [])),
        "degradations": list(pack.get("degradations", [])),
        "limitations": [],
        "runtime": {
            "skill": SKILL_NAME,
            "skill_version": VERSION,
            "python": sys.version.split()[0],
            "deterministic": True,
        },
        "artifacts": {},
    }
    for key in pack.get("contract_keys") or DEFAULT_CONTRACT_KEYS:
        result[key] = None
    return result


def build_default_contract_field(key, candidates, chosen, evidence_map, confirmations):
    chosen_c = next((c for c in candidates if c["plan_id"] == chosen), None) if chosen else None
    if key == "experimental_plan":
        return {
            "chosen_plan_id": chosen,
            "name": (chosen_c or {}).get("name"),
            "seven_fields": (chosen_c or {}).get("seven_fields", {}),
            "steps": (chosen_c or {}).get("seven_fields", {}).get("steps", ""),
            "parameters": (chosen_c or {}).get("seven_fields", {}).get("parameters", ""),
            "resources": (chosen_c or {}).get("seven_fields", {}).get("resources", ""),
            "interpretation_criteria": (chosen_c or {}).get("seven_fields", {}).get("interpretation_criteria", ""),
            "alternatives": [c["plan_id"] for c in candidates if c["plan_id"] != chosen],
        }
    if key == "evidence_and_risks":
        return {
            "assumptions": (chosen_c or {}).get("seven_fields", {}).get("assumptions", ""),
            "risks": (chosen_c or {}).get("risks", []),
            "evidence_claims": [c["claim_id"] for c in evidence_map.get("claims", []) if c["subject"] == chosen],
            "safety_notes": "",
        }
    if key == "confirmation_items":
        return confirmations
    return None


def merge_candidates(analysis, pack):
    raw = list((analysis or {}).get("candidates") or [])
    pack_cands = pack.get("candidates", [])
    by_id = {c["plan_id"]: c for c in pack_cands}

    if raw:
        merged = []
        for c in raw:
            pid = c.get("plan_id") or ("P%d" % (len(merged) + 1))
            c = dict(c)
            c["plan_id"] = str(pid)
            c.setdefault("name", str(pid))
            c.setdefault("version", "v?")
            if pid in by_id and not c.get("raw"):
                c["raw"] = by_id[pid]["raw"]
            merged.append(c)
        return merged

    # No analysis yet: minimal candidates from the pack so the matrix still renders.
    merged = []
    for item in pack_cands:
        merged.append({
            "plan_id": item["plan_id"],
            "name": item.get("name", item["plan_id"]),
            "version": item.get("version", "v?"),
            "seven_fields": {f: "" for f in SEVEN_FIELDS},
            "dimension_ratings": {},
            "feasibility": {},
            "risks": [],
            "evidence": [],
            "cost_lines": [],
            "time_parts": {},
            "infeasible_reasons": [],
            "raw": item.get("raw"),
        })
    return merged


def build_result(pack, analysis, output_dir, work_dir):
    candidates = merge_candidates(analysis, pack)
    for cand in candidates:
        cand["computed_cost"] = compute_cost(cand)
        cand["computed_time"] = compute_time(cand)

    candidates = apply_thresholds(candidates, pack, analysis)

    weights = normalize_weights((analysis or {}).get("weights") or DEFAULT_WEIGHTS)
    for cand in candidates:
        cand["weighted_score"] = score_candidate(cand, weights)

    feasible = [c for c in candidates if not c.get("infeasible")]
    base_ranking = sorted(
        feasible,
        key=lambda c: (c["weighted_score"] if c["weighted_score"] is not None else -1.0),
        reverse=True,
    )
    base_ranking_ids = [c["plan_id"] for c in base_ranking]

    sel = (analysis or {}).get("selection") or {}
    chosen = sel.get("chosen")
    if chosen not in base_ranking_ids:
        chosen = base_ranking_ids[0] if base_ranking_ids else None
    ranking = base_ranking_ids

    rejected = []
    for rej in sel.get("rejected") or []:
        rejected.append(dict(rej))
    rejected_ids = {r.get("plan_id") for r in rejected}
    for cand in candidates:
        if cand.get("infeasible") and cand["plan_id"] not in rejected_ids:
            rejected.append({
                "plan_id": cand["plan_id"],
                "reason": "; ".join(cand.get("infeasible_reasons", [])) or "infeasible",
            })
            rejected_ids.add(cand["plan_id"])

    sensitivity = run_sensitivity(candidates, weights, base_ranking_ids)

    confirmations = list((analysis or {}).get("confirmations") or [])
    for conf in confirmations:
        conf.setdefault("status", "pending")

    selection = {
        "chosen": chosen,
        "ranking": ranking,
        "rationale": sel.get("rationale", ""),
        "rejected": rejected,
        "sensitivity": sensitivity,
    }

    evidence_map = build_evidence_map(analysis, candidates, selection, rejected)

    pending = list((analysis or {}).get("pending_human") or [])
    for claim in evidence_map["claims"]:
        if claim["status"] == "not_reported":
            pending.append({
                "item": "evidence missing: %s" % claim["claim"],
                "claim_id": claim["claim_id"],
                "locator": claim["locator"] or None,
                "reason": "no verbatim quote or locator provided",
            })
    for conf in confirmations:
        if conf.get("status") != "confirmed":
            pending.append({
                "item": conf.get("item"),
                "type": conf.get("type"),
                "reason": "unconfirmed (kept pending)",
            })

    critical_pending = [p for p in pending if p.get("type") in ("safety", "ethics", "equipment", "expert")]

    matrix = build_matrix(candidates, weights)

    title = "Plan comparison (chosen: %s)" % (chosen or "none")
    artifact_names = {
        "result.json": "result.json",
        "comparison_matrix.csv": "comparison_matrix.csv",
        "comparison_matrix.svg": "comparison_matrix.svg",
        "plan_comparison.md": "plan_comparison.md",
        "comparison_matrix.pdf": "comparison_matrix.pdf",
    }
    render_csv(matrix, candidates, selection, os.path.join(output_dir, "comparison_matrix.csv"))
    render_svg(matrix, candidates, selection, title, os.path.join(output_dir, "comparison_matrix.svg"))
    render_markdown(matrix, candidates, selection, evidence_map, os.path.join(output_dir, "plan_comparison.md"))
    render_pdf(matrix, candidates, selection, os.path.join(output_dir, "comparison_matrix.pdf"))

    status = STATUS_PARTIAL
    if not candidates:
        status = STATUS_INSUFFICIENT
    elif chosen and not critical_pending and evidence_map["coverage"].get("evidenced", 0) > 0:
        status = STATUS_OK
    if pack.get("errors") and not pack.get("raw_json"):
        status = STATUS_INPUT_ERROR

    fills = (analysis or {}).get("contract_fills") or {}
    result = {
        "case_id": pack.get("case_id"),
        "status": status,
        "research_question": (analysis or {}).get("research_question") or {},
        "comparison_frame": (analysis or {}).get("comparison_frame") or {},
        "candidates": candidates,
        "comparison_matrix": matrix,
        "selection": selection,
        "evidence_map": evidence_map,
        "confirmations": confirmations,
        "sources": (analysis or {}).get("sources") or [],
        "uncertainty": (analysis or {}).get("uncertainty") or [],
        "pending_human": pending,
        "change_log": build_change_log(analysis),
        "errors": list(pack.get("errors", [])),
        "degradations": list(pack.get("degradations", [])),
        "limitations": list((analysis or {}).get("limitations") or []),
        "runtime": {
            "skill": SKILL_NAME,
            "skill_version": VERSION,
            "python": sys.version.split()[0],
            "deterministic": True,
        },
        "artifacts": artifact_names,
    }
    for key in pack.get("contract_keys") or DEFAULT_CONTRACT_KEYS:
        if key in fills:
            result[key] = fills[key]
        else:
            result[key] = build_default_contract_field(key, candidates, chosen, evidence_map, confirmations)
    return result


def build_change_log(analysis):
    entries = list((analysis or {}).get("change_log") or [])
    last = entries[-1] if entries else None
    if not last or last.get("step") != "assemble":
        entries.append({
            "ts": "T+90s",
            "seq": len(entries) + 1,
            "step": "assemble",
            "note": "assembled final result + matrix artifacts",
        })
    return entries


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_inspect(args):
    os.makedirs(args["output"], exist_ok=True)
    os.makedirs(args["work"], exist_ok=True)
    pack = build_case_pack(args["input"])
    if pack.get("errors") or not pack.get("raw_json"):
        status = STATUS_INPUT_ERROR
    elif not pack.get("candidates"):
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_PARTIAL
    result = make_skeleton(pack, status)
    write_json(os.path.join(args["output"], "result.json"), result)
    write_json(os.path.join(args["work"], "case_pack.json"), pack)
    return 0


def cmd_assemble(args):
    os.makedirs(args["output"], exist_ok=True)
    os.makedirs(args["work"], exist_ok=True)
    pack_path = os.path.join(args["work"], "case_pack.json")
    if os.path.exists(pack_path):
        pack, errs = load_json(pack_path)
        if pack is None:
            pack = build_case_pack(args["input"])
            write_json(pack_path, pack)
    else:
        pack = build_case_pack(args["input"])
        write_json(pack_path, pack)

    analysis_path = os.path.join(args["work"], "analysis.json")
    analysis = None
    if os.path.exists(analysis_path):
        analysis, errs = load_json(analysis_path)
    result = build_result(pack, analysis, args["output"], args["work"])
    write_json(os.path.join(args["output"], "result.json"), result)
    return 0


def cmd_validate(args):
    path = args.get("validate_path") or os.path.join(args["output"], "result.json")
    issues = []
    result, errs = load_json(path)
    if result is None:
        issues.extend(errs or ["result.json not parseable"])
    else:
        if not result.get("case_id"):
            issues.append("missing case_id")
        if result.get("status") not in STATUS_SET:
            issues.append("invalid status")
        for key in ("sources", "uncertainty", "change_log"):
            if key not in result:
                issues.append("missing key: %s" % key)
        if "evidence_map" not in result:
            issues.append("missing evidence_map")
        if "comparison_matrix" not in result:
            issues.append("missing comparison_matrix")

    outdir = os.path.dirname(os.path.abspath(path))
    for name in ("comparison_matrix.svg", "comparison_matrix.csv",
                 "plan_comparison.md", "comparison_matrix.pdf"):
        fp = os.path.join(outdir, name)
        if not os.path.exists(fp):
            issues.append("missing artifact: %s" % name)
            continue
        if name.endswith(".svg"):
            try:
                ET.parse(fp)
            except Exception as exc:  # noqa: BLE001
                issues.append("svg invalid: %s" % exc)
        elif name.endswith(".pdf"):
            with open(fp, "rb") as fh:
                head = fh.read(8)
            if not head.startswith(b"%PDF-"):
                issues.append("pdf header missing")
        elif name.endswith(".csv"):
            with open(fp, "rb") as fh:
                head = fh.read(3)
            if head != b"\xef\xbb\xbf":
                issues.append("csv missing UTF-8 BOM")

    valid = not issues
    print(json.dumps({"valid": valid, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if valid else 1


# ---------------------------------------------------------------------------
# Demo (synthetic, clearly marked as an example)
# ---------------------------------------------------------------------------

def demo_case():
    return {
        "case_id": "demo-synthetic-EXAMPLE",
        "output_contract": {
            "experimental_plan": None,
            "evidence_and_risks": None,
            "confirmation_items": None,
        },
        "research_question": {
            "testable_question": "示例：在预算与时间窗内比较三条合成路线（非正式案例，仅演示流程）",
        },
        "objectives": ["示例目标：选择成本与时间窗内证据最强的路线"],
        "hard_constraints": [
            {"name": "budget_cny", "field": "cost_total", "op": "<=", "value": 50000},
            {"name": "deadline_h", "field": "time_total", "op": "<=", "value": 120},
        ],
        "candidates": [
            {"plan_id": "P1", "name": "路线A-化学合成（示例）", "version": "v1",
             "steps": "示例：三步合成", "parameters": "示例：60 C, 12 h",
             "resources": "示例：反应釜", "risks": [{"trigger": "示例：产率低", "consequence": "示例：返工"}]},
            {"plan_id": "P2", "name": "路线B-酶催化（示例）", "version": "v1",
             "steps": "示例：一锅酶催化", "parameters": "示例：37 C, pH 7",
             "resources": "示例：摇床", "risks": [{"trigger": "示例：酶失活", "consequence": "示例：转化率低"}]},
            {"plan_id": "P3", "name": "路线C-光催化（示例）", "version": "v1",
             "steps": "示例：可见光催化", "parameters": "示例：450 nm, 24 h",
             "resources": "示例：光反应器", "risks": [{"trigger": "示例：光源不稳", "consequence": "示例：重复性差"}]},
        ],
    }


def demo_material():
    return "\n".join([
        "# 示例材料（非正式案例，仅用于演示 Skill 流程）",
        "",
        "- 预算上限：50,000 CNY。",
        "- 时间窗口：120 小时。",
        "- 路线A机时费：300 CNY/小时。",
        "- 路线B机时费：200 CNY/小时。",
        "- 路线C机时费：250 CNY/小时。",
        "- 本文件所有数值均为演示用合成数值，不构成任何正式科研结论。",
        "",
    ])


def demo_analysis():
    return {
        "case_id": "demo-synthetic-EXAMPLE",
        "research_question": {
            "testable_question": "示例：在 50,000 CNY 与 120 h 内比较三条合成路线",
            "variables": {"independent": ["route"], "dependent": ["cost", "time", "evidence"], "controlled": ["goal"]},
            "hypothesis": "示例：成本最低且时间窗内可行者为候选（非正式结论）",
        },
        "comparison_frame": {
            "objectives": ["示例目标"],
            "hard_constraints": [
                {"name": "budget_cny", "field": "cost_total", "op": "<=", "value": 50000},
                {"name": "deadline_h", "field": "time_total", "op": "<=", "value": 120},
            ],
            "criteria": DIMENSIONS,
        },
        "weights": dict(DEFAULT_WEIGHTS),
        "candidates": [
            {
                "plan_id": "P1", "name": "路线A-化学合成（示例）", "version": "v1",
                "seven_fields": {
                    "assumptions": "示例假设：原料可得",
                    "variables": "示例变量：温度、时间",
                    "controls": "示例对照：无催化剂对照",
                    "steps": "示例：三步合成",
                    "parameters": "示例：60 C, 12 h",
                    "resources": "示例：反应釜",
                    "interpretation_criteria": "示例：以产率≥70%为通过",
                },
                "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 3, "feasibility": 4, "evidence": 3},
                "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
                "risks": [{"trigger": "示例：产率低", "consequence": "示例：返工", "magnitude": "中",
                          "mitigation": "示例：优化温度", "fallback": "示例：延长反应时间"}],
                "cost_lines": [
                    {"category": "equipment_time", "item": "机时费", "unit": "h", "unit_price": 300,
                     "quantity": 40, "one_off": False,
                     "evidence": {"claim_id": "demo-A1", "claim": "路线A机时费为 300 CNY/小时",
                                  "locator": "materials/example_note.md#L3", "quote": "- 路线A机时费：300 CNY/小时。",
                                  "kind": "quote", "assertion_type": "fact"}},
                ],
                "time_parts": {"sample_prep": 6, "scheduling": 8, "execution": 40, "queue": 12, "rework": 10, "unit": "h"},
                "evidence": [],
            },
            {
                "plan_id": "P2", "name": "路线B-酶催化（示例）", "version": "v1",
                "seven_fields": {
                    "assumptions": "示例假设：酶活性稳定",
                    "variables": "示例变量：pH、温度",
                    "controls": "示例对照：热灭活酶对照",
                    "steps": "示例：一锅酶催化",
                    "parameters": "示例：37 C, pH 7",
                    "resources": "示例：摇床",
                    "interpretation_criteria": "示例：转化率≥80%为通过",
                },
                "dimension_ratings": {"goal_fit": 5, "risk": 3, "cost": 4, "feasibility": 5, "evidence": 4},
                "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
                "risks": [{"trigger": "示例：酶失活", "consequence": "示例：转化率低", "magnitude": "低",
                          "mitigation": "示例：补加酶", "fallback": "示例：换批次酶"}],
                "cost_lines": [
                    {"category": "equipment_time", "item": "机时费", "unit": "h", "unit_price": 200,
                     "quantity": 30, "one_off": False,
                     "evidence": {"claim_id": "demo-B1", "claim": "路线B机时费为 200 CNY/小时",
                                  "locator": "materials/example_note.md#L4", "quote": "- 路线B机时费：200 CNY/小时。",
                                  "kind": "quote", "assertion_type": "fact"}},
                ],
                "time_parts": {"sample_prep": 4, "scheduling": 5, "execution": 30, "queue": 8, "rework": 4, "unit": "h"},
                "evidence": [],
            },
            {
                "plan_id": "P3", "name": "路线C-光催化（示例）", "version": "v1",
                "seven_fields": {
                    "assumptions": "示例假设：光源稳定",
                    "variables": "示例变量：波长、光照时间",
                    "controls": "示例对照：避光对照",
                    "steps": "示例：可见光催化",
                    "parameters": "示例：450 nm, 24 h",
                    "resources": "示例：光反应器",
                    "interpretation_criteria": "示例：TON≥50 为通过",
                },
                "dimension_ratings": {"goal_fit": 3, "risk": 4, "cost": 3, "feasibility": 3, "evidence": 3},
                "feasibility": {"equipment_available": "yes", "lead_time_ok": "no"},
                "risks": [{"trigger": "示例：光源不稳", "consequence": "示例：重复性差", "magnitude": "高",
                          "mitigation": "示例：校准光源", "fallback": "示例：换光源"}],
                "cost_lines": [
                    {"category": "equipment_time", "item": "机时费", "unit": "h", "unit_price": 250,
                     "quantity": 45, "one_off": False,
                     "evidence": {"claim_id": "demo-C1", "claim": "路线C机时费为 250 CNY/小时",
                                  "locator": "materials/example_note.md#L5", "quote": "- 路线C机时费：250 CNY/小时。",
                                  "kind": "quote", "assertion_type": "fact"}},
                ],
                "time_parts": {"sample_prep": 8, "scheduling": 10, "execution": 45, "queue": 15, "rework": 20, "unit": "h"},
                "evidence": [],
            },
        ],
        "selection": {"chosen": "P2", "rationale": "示例：成本与时间窗内综合评分最高（非正式结论）"},
        "confirmations": [{"item": "示例：设备档期确认", "type": "equipment", "status": "pending"}],
        "sources": [{"ref_id": "S1", "title": "示例材料", "locator": "materials/example_note.md",
                     "quote": "本文件所有数值均为演示用合成数值", "kind": "quote"}],
        "change_log": [{"ts": "T+00s", "seq": 1, "step": "agent_analysis", "note": "示例分析写入"}],
    }


def cmd_demo(args):
    os.makedirs(args["work"], exist_ok=True)
    demo_input = os.path.join(args["work"], "demo_input")
    demo_output = os.path.join(args["work"], "demo_output")
    demo_work = os.path.join(args["work"], "demo_work")
    for d in (demo_input, demo_output, demo_work):
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(demo_input, "materials"), exist_ok=True)
    write_json(os.path.join(demo_input, "case.json"), demo_case())
    write_text(os.path.join(demo_input, "materials", "example_note.md"), demo_material())
    write_json(os.path.join(demo_work, "analysis.json"), demo_analysis())

    sub = {"input": demo_input, "output": demo_output, "work": demo_work}
    cmd_inspect(sub)
    cmd_assemble(sub)

    result_path = os.path.join(demo_output, "result.json")
    result, errs = load_json(result_path)
    if result is not None:
        result["runtime"]["demo"] = True
        result["limitations"].append("demo output is a synthetic EXAMPLE, not a formal case result")
        write_json(result_path, result)

    issues = cmd_validate({"output": demo_output, "validate_path": result_path})
    print("demo artifacts written under: %s" % demo_output)
    print("demo result status: %s" % (result or {}).get("status"))
    print("demo artifacts marked as synthetic EXAMPLE (not a formal answer)")
    return 0 if issues == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv):
    args = {
        "command": None,
        "input": os.environ.get("SKILL_INPUT", DEFAULT_INPUT),
        "output": os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT),
        "work": os.environ.get("SKILL_WORK", DEFAULT_WORK),
        "validate_path": None,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--help", "-h"):
            args["command"] = "help"
            i += 1
        elif a == "--input":
            args["input"] = argv[i + 1]
            i += 2
        elif a.startswith("--input="):
            args["input"] = a.split("=", 1)[1]
            i += 1
        elif a == "--output":
            args["output"] = argv[i + 1]
            i += 2
        elif a.startswith("--output="):
            args["output"] = a.split("=", 1)[1]
            i += 1
        elif a == "--work":
            args["work"] = argv[i + 1]
            i += 2
        elif a.startswith("--work="):
            args["work"] = a.split("=", 1)[1]
            i += 1
        elif a in ("inspect", "assemble", "validate", "demo", "selftest", "help"):
            args["command"] = a
            if a == "validate" and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                args["validate_path"] = argv[i + 1]
                i += 1
            i += 1
        else:
            i += 1
    return args


HELP_TEXT = """usage: main.py <command> [options]

commands:
  inspect    scan input dir, write partial result.json skeleton + case_pack.json
  assemble   merge case_pack + analysis.json -> final result.json + matrix artifacts
  validate   [RESULT.json] check contract and artifact validity
  demo       run a bundled synthetic example (marked EXAMPLE)
  selftest   run built-in robustness checks

options:
  --input DIR   default $SKILL_INPUT or /input
  --output DIR  default $SKILL_OUTPUT or /app/output
  --work DIR    default $SKILL_WORK or /app/work
"""


def run(argv):
    args = parse_args(argv)
    command = args["command"]
    if command in (None, "help"):
        print(HELP_TEXT)
        return 0
    if command == "inspect":
        return cmd_inspect(args)
    if command == "assemble":
        return cmd_assemble(args)
    if command == "validate":
        return cmd_validate(args)
    if command == "demo":
        return cmd_demo(args)
    if command == "selftest":
        import selftest
        return selftest.main([])
    print(HELP_TEXT)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
