#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-first experiment-parameter optimization core (standard library only).

Architecture (differentiated from a monolithic orchestrator):
  * one versioned intermediate representation (IR):
      PlanModel        -> the editable structured plan
      FeasibleDomain   -> compiled intersection of device/material/safety limits
      SourceRegistry   -> deduplicated locator+quote evidence with conflict flags
  * validation order is evidence-first:
      1. source grounding (every numeric claim must carry locator + verbatim quote)
      2. schema conformance
      3. compiled-domain feasibility (suggested value, range and step are checked
         against the intersection of limits, not against ad-hoc keys)
  * the candidate generator and the validator share the same IR, so the points the
    generator emits are guaranteed to be the same points the validator accepts.

No third-party packages are imported.
"""

import json
import os
import random
import re
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log"}

# Keys that belong to the plan body (content) vs. metadata carried in result.json.
PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "uncertainty", "limitations",
                  "pending_human", "change_log"]
PARAMETER_KEYS = {"name", "unit", "role", "suggested_value", "feasible_range",
                  "step", "limits", "exclusions", "basis", "note"}
RESPONSE_KEYS = {"name", "definition", "unit", "measurement_method", "direction",
                 "target", "acceptable_lower", "prediction_method", "uncertainty"}


# --------------------------------------------------------------------------- #
# time / filesystem / text primitives
# --------------------------------------------------------------------------- #
def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def read_text_safe(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def json_load_safe(path):
    raw = read_text_safe(path)
    if raw is None:
        return None, "cannot read file: %s" % path
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:  # pragma: no cover - defensive
        return None, "parse error: %s" % exc


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def to_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_key(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


# --------------------------------------------------------------------------- #
# input discovery
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    found = []
    if not os.path.isdir(input_dir):
        return found
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            abs_path = os.path.join(root, name)
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                continue
            rel = os.path.relpath(abs_path, input_dir).replace(os.sep, "/")
            if size > MAX_INPUT_BYTES:
                found.append({"path": rel, "size": size, "kind": "skipped:too_large",
                              "readable": False})
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            entry = {"path": rel, "size": size, "kind": kind, "readable": False}
            if kind == "text":
                text = read_text_safe(abs_path)
                if text is not None:
                    entry["readable"] = True
                    entry["preview"] = text[:2000]
            found.append(entry)
    return found


def make_input_digest(input_dir):
    files = discover_inputs(input_dir)
    digest = {"input_dir": input_dir, "files": files, "case_id": None,
              "case_json_error": None, "task_md_preview": None}
    for entry in files:
        base = os.path.basename(entry["path"]).lower()
        abs_path = os.path.join(input_dir, entry["path"].replace("/", os.sep))
        if base in ("case.json", "input.json") and entry.get("readable"):
            obj, err = json_load_safe(abs_path)
            digest["case_json_error"] = err
            if obj is not None:
                digest["case_id"] = first_key(obj, "case_id", "caseId", "case",
                                              "id", "challenge_id", "challengeId",
                                              "armiId")
        if base in ("task.md", "task.txt", "readme.md") and entry.get("readable"):
            text = read_text_safe(abs_path)
            if text is not None:
                digest["task_md_preview"] = text[:2000]
    return digest


# --------------------------------------------------------------------------- #
# SourceRegistry: evidence-first bookkeeping
# --------------------------------------------------------------------------- #
class SourceRegistry(object):
    """Collect locator+quote records and flag conflicts.

    A record is accepted only when it carries both a non-empty locator and a
    non-empty verbatim quote; otherwise it is recorded as an error so numeric
    claims cannot be accepted without grounding.
    """

    def __init__(self):
        self.records = []
        self.errors = []

    def add(self, locator, quote, claim=None, kind=None):
        locator = (locator or "").strip()
        quote = (quote or "").strip()
        if not locator or not quote:
            self.errors.append(
                "evidence record missing locator or verbatim quote: "
                "locator=%r quote=%r" % (locator[:80], quote[:80]))
            return None
        rec = {"locator": locator, "quote": quote, "kind": kind or "source"}
        if claim:
            rec["claims"] = [claim] if isinstance(claim, str) else list(claim)
        self.records.append(rec)
        return rec

    def add_conflicts(self, conflicts):
        for item in conflicts or []:
            self.add(item.get("locator"), item.get("quote"),
                     claim=item.get("claim"), kind="source_conflict")


def collect_sources(plan, sources=None):
    """Merge top-level sources with per-parameter/per-metric basis records."""
    reg = SourceRegistry()
    for rec in sources or []:
        if isinstance(rec, dict):
            reg.add(rec.get("locator"), rec.get("quote"),
                    claim=rec.get("claim") or rec.get("claims"), kind=rec.get("kind"))
    for param in plan.get("parameters", []):
        basis = param.get("basis")
        if isinstance(basis, dict):
            reg.add(basis.get("locator"), basis.get("quote"),
                    claim=basis.get("claim") or ("parameter %s" % param.get("name")),
                    kind=basis.get("kind"))
        for limit in param.get("limits") or []:
            if isinstance(limit, dict):
                reg.add(limit.get("source"), limit.get("quote"),
                        claim="limit %s of %s" % (limit.get("kind"),
                                                  param.get("name")),
                        kind=limit.get("kind"))
    for metric in plan.get("response_metrics", []):
        basis = metric.get("basis")
        if isinstance(basis, dict):
            reg.add(basis.get("locator"), basis.get("quote"),
                    claim=basis.get("claim") or ("metric %s" % metric.get("name")),
                    kind=basis.get("kind"))
    return reg


# --------------------------------------------------------------------------- #
# skeleton / normalization
# --------------------------------------------------------------------------- #
def new_skeleton_plan(case_id=""):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id or "",
        "status": "partial",
        "title": "",
        "research_question": "",
        "objective": "",
        "assumptions": [],
        "variables": [],
        "parameters": [],
        "controls": [],
        "procedures": [],
        "resources": [],
        "response_metrics": [],
        "interpretation_criteria": [],
        "search_strategy": {"method": "", "initial_points": [], "per_round": "",
                            "stop_criteria": [], "candidate_count": 8, "seed": 0},
        "cost_budget": {"items": [], "total": "", "time_hours": None,
                        "tradeoff_note": ""},
        "basis": [],
        "risks": [],
        "exception_handling": [],
        "confirmations": [],
    }


def normalize_plan(plan):
    plan = plan if isinstance(plan, dict) else {}
    plan["schema_version"] = plan.get("schema_version", SCHEMA_VERSION)
    for key in PLAN_CONTENT_KEYS:
        if key not in plan:
            if key in ("search_strategy", "cost_budget"):
                plan[key] = new_skeleton_plan().get(key)
            elif key in ("research_question", "objective"):
                plan[key] = ""
            else:
                plan[key] = []
    plan["search_strategy"] = plan.get("search_strategy") or {}
    plan["cost_budget"] = plan.get("cost_budget") or {}
    for param in plan.get("parameters", []):
        for key in ("feasible_range", "basis"):
            if key not in param:
                param[key] = {} if key == "feasible_range" else None
        if "limits" not in param:
            param["limits"] = []
    return plan


# --------------------------------------------------------------------------- #
# FeasibleDomain: compile limits then check against them
# --------------------------------------------------------------------------- #
def _bounds_from(limit):
    lo = to_float(limit.get("min")) if isinstance(limit, dict) else None
    hi = to_float(limit.get("max")) if isinstance(limit, dict) else None
    return lo, hi


def compile_feasible_domain(param):
    """Intersect all device/material/safety limits; return (lo, hi, errors)."""
    errors = []
    lo, hi = None, None
    limits = param.get("limits") or []
    for limit in limits:
        if not isinstance(limit, dict):
            errors.append("parameter %r: limit entry is not an object" % param.get("name"))
            continue
        kind = limit.get("kind") or "unspecified"
        l, h = _bounds_from(limit)
        if l is None and h is None:
            continue
        if l is not None and h is not None and l > h:
            errors.append("parameter %r: %s limit inverted (%s > %s)"
                          % (param.get("name"), kind, l, h))
            continue
        lo = l if lo is None else (max(lo, l) if l is not None else lo)
        hi = h if hi is None else (min(hi, h) if h is not None else hi)
    if lo is not None and hi is not None and lo > hi:
        errors.append("parameter %r: feasible domain is empty after intersecting "
                      "limits (%s .. %s)" % (param.get("name"), lo, hi))
    return lo, hi, errors


def check_parameter(param, strict_sources=False):
    errors, warnings = [], []
    name = param.get("name") or "(unnamed)"
    unit = param.get("unit")
    if not isinstance(name, str) or not name.strip():
        errors.append("parameter missing name")
    if not isinstance(unit, str) or not unit.strip():
        warnings.append("parameter %r missing unit" % name)

    lo, hi, derr = compile_feasible_domain(param)
    errors.extend(derr)

    rng = param.get("feasible_range") or {}
    rng_lo, rng_hi = _bounds_from(rng)
    suggested = to_float(param.get("suggested_value"))
    step = to_float(param.get("step"))

    # evidence-first: a numeric claim must carry locator + verbatim quote.
    numeric_claims = any(v is not None for v in (rng_lo, rng_hi, suggested))
    if numeric_claims:
        basis = param.get("basis")
        has_quote = (isinstance(basis, dict)
                     and (basis.get("locator") or "").strip()
                     and (basis.get("quote") or "").strip())
        has_limit_source = any(isinstance(x, dict) and (x.get("source") or "").strip()
                               and (x.get("quote") or "").strip()
                               for x in (param.get("limits") or []))
        if not has_quote and not has_limit_source:
            msg = ("parameter %r carries a numeric value but no locator+verbatim "
                   "quote; values must be grounded in the source material" % name)
            (errors if strict_sources else warnings).append(msg)

    # feasibility checks against the compiled domain (not raw keys).
    if lo is not None and rng_lo is not None and rng_lo < lo - 1e-9:
        errors.append("parameter %r feasible_range.min %s < domain min %s"
                      % (name, rng_lo, lo))
    if hi is not None and rng_hi is not None and rng_hi > hi + 1e-9:
        errors.append("parameter %r feasible_range.max %s > domain max %s"
                      % (name, rng_hi, hi))
    if suggested is not None:
        if lo is not None and suggested < lo - 1e-9:
            errors.append("parameter %r suggested_value %s below domain min %s"
                          % (name, suggested, lo))
        if hi is not None and suggested > hi + 1e-9:
            errors.append("parameter %r suggested_value %s above domain max %s"
                          % (name, suggested, hi))
    if step is not None and step <= 0:
        errors.append("parameter %r step must be > 0, got %s" % (name, step))
    if step is not None and rng_lo is not None and rng_hi is not None and step > 0:
        span = rng_hi - rng_lo
        if span < step - 1e-9:
            warnings.append("parameter %r step %s larger than range span %s"
                            % (name, step, span))
    return errors, warnings


def check_response_metric(metric, strict_sources=False):
    errors, warnings = [], []
    name = metric.get("name") or "(unnamed)"
    if not isinstance(name, str) or not name.strip():
        errors.append("response metric missing name")
    direction = metric.get("direction")
    if direction not in ("maximize", "minimize", "target", None):
        errors.append("metric %r has invalid direction %r" % (name, direction))
    for req in ("unit", "measurement_method"):
        if not (metric.get(req) or "").strip():
            warnings.append("metric %r missing %s" % (name, req))
    if metric.get("target") is not None and to_float(metric.get("target")) is None:
        warnings.append("metric %r target is not numeric" % name)
    return errors, warnings


# --------------------------------------------------------------------------- #
# validation pipelines
# --------------------------------------------------------------------------- #
def validate_evidence(plan, sources=None, strict=True):
    """Step 1 of the pipeline: source grounding."""
    reg = collect_sources(plan, sources)
    errors = list(reg.errors)
    # any parameter numeric claim without grounding is caught inside
    # check_parameter, but also require at least one record overall when the plan
    # makes claims.
    numeric_params = 0
    for param in plan.get("parameters", []):
        vals = [to_float(param.get("suggested_value"))]
        rng = param.get("feasible_range") or {}
        vals += [to_float(rng.get("min")), to_float(rng.get("max"))]
        if any(v is not None for v in vals):
            numeric_params += 1
    if numeric_params and not reg.records and strict:
        errors.append("plan makes numeric parameter claims but has no source records")
    return reg, errors, []


def validate_schema(plan):
    """Step 2: presence of required sections and basic types."""
    errors, warnings = [], []
    if plan.get("research_question", "").strip() == "":
        warnings.append("research_question empty")
    if plan.get("objective", "").strip() == "":
        warnings.append("objective empty")
    for key in ("parameters", "response_metrics", "procedures", "risks",
                "exception_handling", "confirmations", "controls", "resources"):
        if not isinstance(plan.get(key), list):
            errors.append("plan.%s must be a list" % key)
    strat = plan.get("search_strategy") or {}
    if not (strat.get("method") or "").strip():
        warnings.append("search_strategy.method empty")
    if not isinstance(strat.get("initial_points"), list):
        errors.append("search_strategy.initial_points must be a list")
    cost = plan.get("cost_budget") or {}
    if not isinstance(cost.get("items"), list):
        errors.append("cost_budget.items must be a list")
    if cost.get("tradeoff_note") in (None, ""):
        warnings.append("cost_budget.tradeoff_note empty (multi-objective tradeoff "
                        "must be stated)")
    return errors, warnings


def validate_feasibility(plan, strict_sources=False):
    """Step 3: compiled-domain feasibility for every parameter/metric."""
    errors, warnings = [], []
    for param in plan.get("parameters", []):
        e, w = check_parameter(param, strict_sources=strict_sources)
        errors.extend(e)
        warnings.extend(w)
    for metric in plan.get("response_metrics", []):
        e, w = check_response_metric(metric, strict_sources=strict_sources)
        errors.extend(e)
        warnings.extend(w)
    return errors, warnings


def validate_plan(plan, sources=None, strict_sources=True):
    """Full evidence-first pipeline: evidence -> schema -> feasibility."""
    plan = normalize_plan(plan)
    reg, e1, w1 = validate_evidence(plan, sources, strict=strict_sources)
    e2, w2 = validate_schema(plan)
    e3, w3 = validate_feasibility(plan, strict_sources=strict_sources)
    report = {
        "schema_version": plan.get("schema_version", SCHEMA_VERSION),
        "errors": e1 + e2 + e3,
        "warnings": w1 + w2 + w3,
        "source_count": len(reg.records),
        "source_errors": list(reg.errors),
        "sources": reg.records,
    }
    return report, plan


# --------------------------------------------------------------------------- #
# deterministic candidate generation (shares IR with the validator)
# --------------------------------------------------------------------------- #
def grid_values(lo, hi, step, limit=20):
    if lo is None or hi is None:
        return []
    if step is None or step <= 0:
        return [round((lo + hi) / 2.0, 6)] if hi >= lo else []
    out, cur = [], lo
    while cur <= hi + 1e-9 and len(out) < limit:
        out.append(round(cur, 6))
        cur += step
    if hi not in out and len(out) < limit:
        out.append(round(hi, 6))
    return out


def generate_candidates(plan, seed=None):
    plan = normalize_plan(plan)
    strat = plan.get("search_strategy") or {}
    seed = strat.get("seed", 0) if seed is None else seed
    count = int(strat.get("candidate_count") or 8)
    count = max(1, min(count, 64))
    rng = random.Random(seed)
    numeric = []
    for p in plan.get("parameters", []):
        lo, hi, _ = compile_feasible_domain(p)
        if lo is None and hi is None:
            rng_lo, rng_hi = _bounds_from(p.get("feasible_range") or {})
            lo, hi = rng_lo, rng_hi
        step = to_float(p.get("step"))
        grid = grid_values(lo, hi, step)
        if grid:
            numeric.append((p.get("name"), lo, hi, grid, p.get("suggested_value")))

    suggested = {"round": 0, "parameters": {}, "note": "suggested baseline (from plan)"}
    for name, _lo, _hi, _grid, sugg in numeric:
        if to_float(sugg) is not None:
            suggested["parameters"][name] = to_float(sugg)

    candidates = [suggested]
    for i in range(count):
        cand = {"round": i + 1, "parameters": {}, "note": "deterministic candidate"}
        for name, lo, hi, grid, _sugg in numeric:
            if i < len(grid):
                cand["parameters"][name] = grid[i]
            else:
                cand["parameters"][name] = round(rng.uniform(lo, hi), 6)
        candidates.append(cand)
    return {"seed": seed, "count": count, "candidates": candidates}


# --------------------------------------------------------------------------- #
# plan.md rendering (editable artifact)
# --------------------------------------------------------------------------- #
def _bullet(title, items):
    lines = ["## %s" % title, ""]
    if not items:
        lines.append("_(待补：无内容)_")
    for it in items:
        if isinstance(it, dict):
            if it.get("name"):
                lines.append("- **%s**" % it["name"])
                for k in ("unit", "suggested_value", "feasible_range", "step",
                          "basis", "limits", "direction", "target",
                          "measurement_method", "uncertainty", "note"):
                    if it.get(k) not in (None, "", [], {}):
                        lines.append("  - %s: %s" % (k, json.dumps(
                            it.get(k), ensure_ascii=False)))
            else:
                lines.append("- " + json.dumps(it, ensure_ascii=False))
        elif isinstance(it, str):
            lines.append("- " + it)
        else:
            lines.append("- " + json.dumps(it, ensure_ascii=False))
    lines.append("")
    return lines


def render_plan_md(plan):
    plan = normalize_plan(plan)
    out = []
    out.append("# %s" % (plan.get("title") or "实验参数优化方案"))
    out.append("")
    out.append("- case_id: %s" % (plan.get("case_id") or ""))
    out.append("- schema_version: %s" % plan.get("schema_version", SCHEMA_VERSION))
    out.append("")
    for key in ("research_question", "objective"):
        val = plan.get(key, "")
        if val:
            out.append("## %s" % key)
            out.append("")
            out.append(str(val))
            out.append("")
    out += _bullet("假设 assumptions", plan.get("assumptions"))
    out += _bullet("变量 variables", plan.get("variables"))
    out += _bullet("关键参数 parameters", plan.get("parameters"))
    out += _bullet("对照 controls", plan.get("controls"))
    out += _bullet("步骤 procedures", plan.get("procedures"))
    out += _bullet("资源 resources", plan.get("resources"))
    out += _bullet("响应指标 response_metrics", plan.get("response_metrics"))
    out += _bullet("判读标准 interpretation_criteria", plan.get("interpretation_criteria"))
    strat = plan.get("search_strategy") or {}
    out.append("## 搜索策略 search_strategy")
    out.append("")
    out.append("- method: %s" % strat.get("method", ""))
    out.append("- initial_points: %s" % json.dumps(strat.get("initial_points", []),
                                                   ensure_ascii=False))
    out.append("- per_round: %s" % strat.get("per_round", ""))
    out.append("- stop_criteria: %s" % json.dumps(strat.get("stop_criteria", []),
                                                  ensure_ascii=False))
    out.append("- candidate_count: %s  seed: %s" % (strat.get("candidate_count"),
                                                    strat.get("seed")))
    out.append("")
    cost = plan.get("cost_budget") or {}
    out.append("## 成本时间预算 cost_budget")
    out.append("")
    out.append("- total: %s  time_hours: %s" % (cost.get("total", ""),
                                                cost.get("time_hours")))
    out.append("- tradeoff_note: %s" % cost.get("tradeoff_note", ""))
    out.append("- items:")
    for it in cost.get("items", []):
        out.append("  - " + (json.dumps(it, ensure_ascii=False)
                             if not isinstance(it, str) else it))
    out.append("")
    out += _bullet("依据 basis", plan.get("basis"))
    out += _bullet("风险 risks", plan.get("risks"))
    out += _bullet("异常处置 exception_handling", plan.get("exception_handling"))
    out += _bullet("确认项 confirmations", plan.get("confirmations"))
    return "\n".join(out)
