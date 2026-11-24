"""core.py - contract-first experimental plan comparison engine.

Standard library only. The engine treats the organizer's ``output_contract`` as
the single source of truth for the result schema: it recursively flattens the
contract, backfills every field into result.json (null/[] when unknown), and
records a per-field ``contract_alignment`` audit block. All scoring, arithmetic
and artifact generation are deterministic so repeated runs are byte-stable.

Responsibilities split by subcommand:
  inspect   scan inputs, discover contract + candidates + constraints, persist
            a legal result skeleton and a case_pack.json for the agent.
  assemble  read case_pack.json + analysis.json, run arithmetic/thresholds/
            scoring/sensitivity, backfill the contract, write all artifacts.
  validate  load result.json, run the self-check rules, print issues, set the
            process exit code (0 = valid, 1 = issues found).
  demo      run the whole pipeline on an inline sample marked as an example.

The agent remains the authority on scientific content: analysis.json carries
seven-field plans, per-plan evidence with verbatim quotes, manual cost/time
overrides, and contract_fills for any organizer-specific contract field.
"""

import csv
import io
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

DIMENSIONS = ["goal_fit", "risk", "cost", "feasibility", "evidence"]
DEFAULT_WEIGHTS = {
    "goal_fit": 0.25,
    "feasibility": 0.25,
    "evidence": 0.20,
    "cost": 0.15,
    "risk": 0.15,
}

SEVEN_FIELDS = [
    "assumptions",
    "variables",
    "controls",
    "steps",
    "parameters",
    "resources",
    "interpretation_criteria",
]

COST_LINES = [
    "equipment_time",
    "consumables",
    "labor",
    "sample_prep",
    "scheduling",
    "rework",
]

TIME_PARTS = [
    "preparation",
    "scheduling",
    "execution",
    "queue",
    "rework",
]

# Fields backfilled when no explicit output_contract is present.
DEFAULT_CONTRACT_FIELDS = [
    "experimental_plan",
    "evidence_and_risks",
    "confirmation_items",
]

MAX_FILE_BYTES = 2 * 1024 * 1024
SENSITIVITY_DELTA = 0.05

MATERIAL_EXTENSIONS = (
    ".pdf", ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl",
    ".docx", ".xlsx", ".pptx", ".xml", ".yaml", ".yml",
)

# Candidate-discovery keys tried first (case-insensitive substring match).
CANDIDATE_KEYS = (
    "candidates", "candidate", "plans", "plan", "alternatives",
    "options", "schemes", "scheme", "proposals", "proposal",
)

CONSTRAINT_KEYS = (
    "constraint", "constraints", "budget", "budgets", "deadline",
    "time_window", "timeline", "schedule", "resource",
)

OBJECTIVE_KEYS = (
    "objective", "objectives", "goal", "goals", "research_question",
    "research_goal", "target", "aim", "aims",
)


# ----------------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------------

def now_iso():
    """UTC timestamp, ISO-8601 with 'Z' suffix, deterministic per call site."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_text(path):
    """Read a UTF-8 text file tolerating a BOM and minor encoding damage.

    Returns (text, error). Never raises; a bad encoding yields an error string
    so callers can record a degradation instead of crashing.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read(), None
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return fh.read(), "fallback-encoding:latin-1"
        except Exception as exc:  # noqa: BLE001
            return "", "unreadable:{}".format(exc)
    except Exception as exc:  # noqa: BLE001
        return "", "unreadable:{}".format(exc)


def _load_json_text(text, errors, label):
    """Parse JSON text, tolerating a trailing BOM and stray whitespace."""
    if text is None:
        return None, errors
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return None, errors
    try:
        return json.loads(stripped), errors
    except Exception as exc:  # noqa: BLE001
        errors.append({"where": label, "kind": "bad_json", "detail": str(exc)[:300]})
        return None, errors


def _set_path(obj, path, value):
    """Set a dotted path inside nested dicts, creating intermediate dicts."""
    parts = path.split(".")
    node = obj
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _get_path(obj, path, default=None):
    """Read a dotted path, returning default when any segment is missing."""
    node = obj
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _sort_key(value):
    """Deterministic sort key for mixed-type values."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


# ----------------------------------------------------------------------------
# Input discovery
# ----------------------------------------------------------------------------

def scan_materials(input_dir):
    """Recursively list material files under input_dir (size-capped).

    Returns (materials, errors, degradations). Files above MAX_FILE_BYTES are
    skipped and recorded as degradations.
    """
    materials = []
    errors = []
    degradations = []
    if not input_dir or not os.path.isdir(input_dir):
        return materials, errors, degradations

    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            lower = name.lower()
            if not lower.endswith(MATERIAL_EXTENSIONS):
                continue
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                errors.append({"where": path, "kind": "stat_failed", "detail": str(exc)})
                continue
            if size > MAX_FILE_BYTES:
                degradations.append({
                    "where": path, "kind": "file_too_large",
                    "detail": "skipped {} bytes > {} limit".format(size, MAX_FILE_BYTES),
                })
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            materials.append({
                "path": path,
                "rel": rel,
                "name": name,
                "size": size,
                "ext": lower.rsplit(".", 1)[-1] if "." in lower else "",
            })
    return materials, errors, degradations


def read_case(input_dir, errors):
    """Read /input/case.json with tolerance for absence and bad JSON."""
    case_path = None
    if input_dir and os.path.isdir(input_dir):
        probe = os.path.join(input_dir, "case.json")
        if os.path.isfile(probe):
            case_path = probe
    if case_path is None:
        errors.append({"where": "case.json", "kind": "missing", "detail": "no case.json in input dir"})
        return None, case_path, errors
    text, err = _read_text(case_path)
    if err:
        errors.append({"where": case_path, "kind": "encoding", "detail": err})
    case, errors = _load_json_text(text, errors, case_path)
    if case is None:
        errors.append({"where": case_path, "kind": "empty_or_bad_json", "detail": "case.json could not be parsed"})
    return case, case_path, errors


def discover_contract(case):
    """Locate the output_contract in a case object, tolerating key variants."""
    if not isinstance(case, dict):
        return None, None
    for key in ("output_contract", "outputContract", "output", "contract",
                "result_schema", "resultSchema", "schema"):
        if key in case and case[key] is not None:
            return case[key], key
    # The contract may also live inside a top-level "runtime" or "spec" object.
    for wrapper in ("runtime", "spec", "harness"):
        if isinstance(case.get(wrapper), dict):
            for key in ("output_contract", "outputContract", "output", "contract"):
                if case[wrapper].get(key) is not None:
                    return case[wrapper][key], "{}.{}".format(wrapper, key)
    return None, None


def flatten_contract(contract, prefix="", out=None):
    """Flatten a contract into dotted field paths.

    Accepted shapes:
      dict              -> each key is a field path (values are descriptions)
      list of str       -> each string is a field path
      list of dict      -> recurse into each dict's keys
      str               -> single field path
    """
    if out is None:
        out = []
    if isinstance(contract, dict):
        for key in sorted(contract.keys()):
            path = "{}.{}".format(prefix, key) if prefix else str(key)
            value = contract[key]
            if isinstance(value, dict) and value:
                flatten_contract(value, path, out)
            else:
                out.append(path)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                flatten_contract(item, prefix, out)
            elif prefix:
                out.append(prefix)
    elif isinstance(contract, str) and contract.strip():
        out.append(contract)
    return out


IDENTITY_KEYS = (
    "plan_id", "planId", "id", "name", "title", "version",
    "方案", "方案名", "计划", "名称", "候选",
)

CONTENT_MARKERS = (
    "variables", "steps", "parameters", "cost", "budget", "risk",
    "assumptions", "controls", "resources", "interpretation",
    "seven_fields", "dimension_ratings", "feasibility",
)


def _has_identity(node):
    """A candidate needs a stable identifier key (plan_id/name/方案/...)."""
    if not isinstance(node, dict):
        return False
    for key in node.keys():
        if str(key).lower() in IDENTITY_KEYS and node[key] not in (None, "", [], {}):
            return True
    return False


def _looks_like_plan(node):
    """A dict is a plan iff it has an identity key and at least one content marker.

    Requiring identity prevents nested seven_fields / dimension_ratings dicts
    (which lack an id/name) from being miscounted as separate candidate plans.
    """
    if not isinstance(node, dict) or not _has_identity(node):
        return False
    hits = sum(1 for k in node.keys() if str(k).lower() in CONTENT_MARKERS)
    return hits >= 1


def _record_candidate(node, out, seen):
    marker = _sort_key(node)
    if marker in seen:
        return
    seen.add(marker)
    out.append(node)


def extract_candidates(case):
    """Find candidate-plan dicts, deterministic and without double counting.

    Matched candidate dicts are recorded but not descended into, so their
    internal seven_fields / dimension_ratings blocks stay out of the list.
    """
    candidates = []
    seen = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if _looks_like_plan(node):
                _record_candidate(node, candidates, seen)
                return
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(case)
    return candidates, []


def _find_by_keys(node, key_names, out):
    """Collect non-empty values at keys whose lowercase form is in key_names."""
    if isinstance(node, list):
        for item in node:
            _find_by_keys(item, key_names, out)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in key_names:
                if isinstance(value, (list, dict)) and value:
                    out.append(value)
            if isinstance(value, (dict, list)):
                _find_by_keys(value, key_names, out)


def extract_constraints(case):
    out = []
    _find_by_keys(case, CONSTRAINT_KEYS, out)
    return out


def extract_objectives(case):
    out = []
    _find_by_keys(case, OBJECTIVE_KEYS, out)
    return out


def _first_str(values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# ----------------------------------------------------------------------------
# Skeleton (inspect)
# ----------------------------------------------------------------------------

def _contract_descriptions(contract):
    """Return a compact {field_path: description-or-null} map."""
    if isinstance(contract, dict):
        out = {}
        for key in sorted(contract.keys()):
            value = contract[key]
            if isinstance(value, dict):
                out.update(_contract_descriptions(value))
            else:
                out[key] = value if isinstance(value, (str, int, float, bool)) else None
        return out
    return {}


def _empty_for_field(path):
    """Default placeholder for a contract field: [] for plural names, else null."""
    last = path.rsplit(".", 1)[-1].lower()
    if last.endswith("s") and not last.endswith("ss"):
        return []
    return None


def build_skeleton(case, contract, contract_key, input_dir, errors, degradations):
    """Build a legal partial result.json before any deep analysis happens."""
    materials, mat_errors, mat_degradations = scan_materials(input_dir)
    errors.extend(mat_errors)
    degradations.extend(mat_degradations)

    contract_paths = flatten_contract(contract)
    if not contract_paths:
        contract_paths = list(DEFAULT_CONTRACT_FIELDS)
        contract = {f: None for f in DEFAULT_CONTRACT_FIELDS}
        contract_key = "default"

    case_id = None
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if case.get(key) not in (None, ""):
                case_id = str(case[key])
                break

    candidates, cand_errors = extract_candidates(case)
    errors.extend(cand_errors)

    status = STATUS_PARTIAL
    if case is None:
        status = STATUS_INPUT_ERROR
    elif not candidates:
        status = STATUS_INSUFFICIENT

    result = {
        "case_id": case_id,
        "status": status,
        "output_contract": contract if contract is not None else {},
        "output_contract_key": contract_key,
        "contract_alignment": {
            path: {
                "path": path,
                "present": False,
                "filled": False,
                "value_type": None,
                "source": "missing",
                "note": "skeleton placeholder",
            }
            for path in contract_paths
        },
        "research_question": {
            "testable_question": _first_str(extract_objectives(case)),
            "variables": {"independent": [], "dependent": [], "controlled": []},
            "hypothesis": None,
        },
        "comparison_frame": {
            "objectives": extract_objectives(case),
            "hard_constraints": extract_constraints(case),
            "criteria": list(DIMENSIONS),
        },
        "candidates": candidates,
        "comparison_matrix": {},
        "selection": {
            "chosen": None,
            "ranking": [],
            "rationale": None,
            "rejected": [],
            "sensitivity": {},
        },
        "experimental_plan": {},
        "evidence_and_risks": {"evidence": [], "risks": []},
        "confirmation_items": [],
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "runtime": {
            "started": now_iso(),
            "finished": None,
            "engine": "sci-plan-comparison-v2",
        },
        "artifacts": [],
        "change_log": [
            {
                "seq": 1,
                "time": now_iso(),
                "step": "inspect",
                "note": "skeleton persisted before analysis; status={}".format(status),
            }
        ],
    }
    return result


def build_case_pack(case, result, contract_paths, input_dir, errors, degradations):
    """Package discovered facts for the agent's analysis step."""
    materials, mat_errors, mat_degradations = scan_materials(input_dir)
    errors.extend(mat_errors)
    degradations.extend(mat_degradations)
    candidates = result.get("candidates", [])
    pack = {
        "case_id": result.get("case_id"),
        "contract_key": result.get("output_contract_key"),
        "contract_paths": contract_paths,
        "output_contract": result.get("output_contract"),
        "research_question": result.get("research_question"),
        "comparison_frame": result.get("comparison_frame"),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "materials": materials,
        "errors": errors,
        "degradations": degradations,
        "pending_human": result.get("pending_human", []),
    }
    return pack


# ----------------------------------------------------------------------------
# Arithmetic, thresholds, scoring
# ----------------------------------------------------------------------------

def _num(value):
    """Coerce to float when possible, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("￥", "").replace("¥", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _plan_attr(plan, *names):
    for name in names:
        if name in plan and plan[name] not in (None, "", [], {}):
            return plan[name]
    return None


def compute_cost(plan, default_currency="CNY"):
    """Sum the six cost lines with once-vs-per-run separation.

    Returns (total, currency, lines, method, note). If any required value is
    missing, method becomes 'not_computable' and total is None.
    """
    override = plan.get("cost_override")
    if isinstance(override, dict) and override.get("total") is not None:
        return (override.get("total"), override.get("currency", default_currency),
                override.get("lines", []), override.get("method", "override"),
                override.get("note"), override.get("evidence"))

    lines = []
    currency = default_currency
    runs = _num(plan.get("n_runs")) or 1.0
    once_total = 0.0
    per_run_total = 0.0
    missing = []
    for line_name in COST_LINES:
        line = plan.get(line_name)
        if not isinstance(line, dict):
            missing.append(line_name)
            continue
        amount = _num(line.get("amount", line.get("total", line.get("value"))))
        if amount is None:
            missing.append(line_name)
            continue
        kind = str(line.get("kind", "once")).lower()
        per_run = kind in ("per_run", "per-run", "perrun", "variable", "consumable")
        if per_run:
            per_run_total += amount * runs
        else:
            once_total += amount
        if line.get("currency") and line.get("currency") != currency:
            currency = str(line.get("currency"))
        lines.append({
            "line": line_name,
            "amount": amount,
            "kind": kind,
            "per_run": per_run,
            "currency": line.get("currency", currency),
            "note": line.get("note"),
            "evidence": line.get("evidence"),
        })
    if missing:
        return (None, currency, lines, "not_computable",
                "missing cost lines: {}".format(", ".join(sorted(missing))), None)
    total = once_total + per_run_total
    return (round(total, 4), currency, lines, "computed", None, None)


def compute_time(plan):
    """Sum the five time parts in hours; missing parts are reported."""
    override = plan.get("time_override")
    if isinstance(override, dict) and override.get("total") is not None:
        return (override.get("total"), override.get("unit", "h"),
                override.get("parts", {}), override.get("computed", False), None)

    parts = {}
    missing = []
    for part in TIME_PARTS:
        value = _num(plan.get("time_" + part, plan.get(part)))
        if value is None:
            missing.append(part)
        else:
            parts[part] = value
    if missing:
        return (None, "h", parts, False,
                "missing time parts: {}".format(", ".join(sorted(missing))))
    total = round(sum(parts.values()), 4)
    return (total, "h", parts, True, None)


def _budget_value(constraints):
    """Find a numeric budget among constraint blobs."""
    for blob in constraints:
        if isinstance(blob, dict):
            for key in ("budget", "max_cost", "total_budget", "cost_limit"):
                value = _num(blob.get(key))
                if value is not None:
                    return value, key
        elif isinstance(blob, (int, float)) and not isinstance(blob, bool):
            return float(blob), "scalar"
    return None, None


def _deadline_value(constraints):
    """Find a numeric deadline/duration among constraint blobs."""
    for blob in constraints:
        if isinstance(blob, dict):
            for key in ("deadline_days", "time_window_days", "max_days", "window",
                        "duration_days", "lead_time_days", "max_hours"):
                value = _num(blob.get(key))
                if value is not None:
                    return value, key
    return None, None


def apply_thresholds(plan, constraints):
    """Hard-threshold elimination. Returns (feasible, reasons, notes)."""
    reasons = []
    notes = []
    feas = plan.get("feasibility", {})
    if isinstance(feas, dict):
        if feas.get("equipment_available") in ("no", "false", False, "unavailable"):
            reasons.append("equipment_unavailable")
    budget, budget_key = _budget_value(constraints)
    if budget is not None:
        cost, _cur, _lines, method, _note, _ev = compute_cost(plan)
        if method == "computed" and cost is not None and cost > budget:
            reasons.append("over_budget")
            notes.append("cost {} > budget {} ({})".format(cost, budget, budget_key))
    deadline, deadline_key = _deadline_value(constraints)
    if deadline is not None:
        total_h, unit, _parts, ok, _note = compute_time(plan)
        if ok and total_h is not None:
            days = total_h / 24.0 if unit == "h" else total_h
            if days > deadline:
                reasons.append("over_time_window")
                notes.append("time {}d > window {}d ({})".format(round(days, 2), deadline, deadline_key))
    return (len(reasons) == 0, reasons, notes)


def normalize_weights(weights, dimensions):
    """Return normalized weights over the provided dimensions."""
    available = {}
    for dim in dimensions:
        if dim in weights and _num(weights[dim]) is not None:
            available[dim] = _num(weights[dim])
    if not available:
        # Fall back to defaults for the requested dimensions.
        for dim in dimensions:
            if dim in DEFAULT_WEIGHTS:
                available[dim] = DEFAULT_WEIGHTS[dim]
    total = sum(available.values())
    if total <= 0:
        n = len(available) or 1
        return {dim: 1.0 / n for dim in available}
    return {dim: value / total for dim, value in available.items()}


def compute_score(plan, weights):
    """Weighted score over dimensions where both weight and rating exist."""
    ratings = plan.get("dimension_ratings", {})
    available_dims = [d for d in DIMENSIONS if _num(ratings.get(d)) is not None]
    norm = normalize_weights(weights, available_dims)
    if not norm:
        return None
    total = 0.0
    covered = []
    for dim, weight in norm.items():
        rating = _num(ratings.get(dim))
        if rating is None:
            continue
        total += weight * max(0.0, min(5.0, rating))
        covered.append(dim)
    if not covered:
        return None
    return round(total, 4)


def score_candidates(candidates, weights):
    """Annotate candidates with score, cost, time, threshold results."""
    scored = []
    for cand in candidates:
        entry = dict(cand)
        cost_total, currency, lines, cost_method, cost_note, cost_ev = compute_cost(cand)
        time_total, unit, parts, time_ok, time_note = compute_time(cand)
        feasible, reasons, notes = apply_thresholds(cand, cand.get("__constraints__", []))
        score = compute_score(cand, weights)
        entry["cost"] = {
            "total": cost_total, "currency": currency, "lines": lines,
            "method": cost_method, "note": cost_note, "evidence": cost_ev,
        }
        entry["time"] = {
            "total": time_total, "unit": unit, "parts": parts,
            "computed": time_ok, "note": time_note,
        }
        entry["thresholds"] = {"feasible": feasible, "reasons": reasons, "notes": notes}
        entry["score"] = score
        scored.append(entry)
    return scored


def rank_candidates(scored):
    """Rank by score desc, feasible first, tie-broken by plan_id for determinism."""
    def key(item):
        feas = 1 if item.get("thresholds", {}).get("feasible", True) else 0
        score = item.get("score") if item.get("score") is not None else -1.0
        pid = str(item.get("plan_id", item.get("name", item.get("id", ""))))
        return (feas, score, -ord(pid[0]) if pid else 0, pid)
    return sorted(scored, key=key, reverse=True)


def sensitivity_analysis(scored, weights, delta=SENSITIVITY_DELTA):
    """Perturb each weight by +/-delta and count how often the current top stays.

    Returns {top_plan, stability_fraction, trials, per_plan_wins, note}.
    """
    ranked = rank_candidates(scored)
    if not ranked:
        return {"top_plan": None, "stability_fraction": None, "trials": 0,
                "per_plan_wins": {}, "note": "no scored candidates"}
    top = str(ranked[0].get("plan_id", ranked[0].get("name", "P0")))
    wins = {}
    trials = 0
    perturbations = []
    for dim in DIMENSIONS:
        for sign in (+1, -1):
            perturbed = dict(weights)
            old = _num(perturbed.get(dim))
            if old is None:
                continue
            perturbed[dim] = max(0.0, old + sign * delta)
            norm = normalize_weights(perturbed, DIMENSIONS)
            # Recompute scores under perturbed weights.
            trial_scored = []
            for item in scored:
                entry = dict(item)
                entry["score"] = compute_score(item, norm)
                trial_scored.append(entry)
            trial_ranked = rank_candidates(trial_scored)
            trials += 1
            if trial_ranked:
                winner = str(trial_ranked[0].get("plan_id", trial_ranked[0].get("name", "P0")))
                wins[winner] = wins.get(winner, 0) + 1
                perturbations.append({"dimension": dim, "sign": sign, "winner": winner})
    stable = wins.get(top, 0)
    fraction = round(stable / trials, 4) if trials else None
    return {
        "top_plan": top,
        "stability_fraction": fraction,
        "trials": trials,
        "per_plan_wins": wins,
        "perturbations": perturbations,
        "note": "weight +/-{} perturbations".format(delta),
    }


# ----------------------------------------------------------------------------
# Matrix + artifacts
# ----------------------------------------------------------------------------

def build_matrix(scored):
    """Build a plan x field matrix with identical rows for every candidate."""
    plans = []
    rows = {
        "assumptions": {}, "variables": {}, "controls": {}, "steps": {},
        "parameters": {}, "resources": {}, "interpretation_criteria": {},
        "goal_fit": {}, "risk": {}, "cost": {}, "feasibility": {}, "evidence": {},
        "score": {}, "cost_total": {}, "time_total": {}, "feasible": {},
    }
    for item in scored:
        pid = str(item.get("plan_id", item.get("name", item.get("id", ""))))
        plans.append(pid)
        seven = item.get("seven_fields", {}) or {}
        for field in SEVEN_FIELDS:
            rows[field][pid] = seven.get(field)
        ratings = item.get("dimension_ratings", {}) or {}
        for dim in DIMENSIONS:
            rows[dim][pid] = ratings.get(dim)
        rows["score"][pid] = item.get("score")
        rows["cost_total"][pid] = (item.get("cost") or {}).get("total")
        rows["time_total"][pid] = (item.get("time") or {}).get("total")
        rows["feasible"][pid] = (item.get("thresholds") or {}).get("feasible")
    return {"plans": plans, "rows": rows}


def _esc_xml(text):
    if text is None:
        return ""
    return (str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _cell_text(value):
    if value is None:
        return "-"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("%.3g" % value)
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def render_svg(matrix, meta, out_path):
    """Render an editable, XML-parseable SVG matrix."""
    ET = __import__("xml.etree.ElementTree", fromlist=["ElementTree"])
    plans = matrix.get("plans", [])
    rows = matrix.get("rows", {})
    row_names = list(rows.keys())
    n_cols = len(plans) + 1
    n_rows = len(row_names) + 1
    cell_w, cell_h, head_h = 150, 22, 30
    width = n_cols * cell_w
    height = n_rows * cell_h + head_h

    ns = "http://www.w3.org/2000/svg"
    svg = ET.Element("svg", {
        "xmlns": ns,
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 {} {}".format(width, height),
        "font-family": "sans-serif",
        "font-size": "11",
    })
    title = ET.SubElement(svg, "title")
    title.text = "Experimental plan comparison matrix - {} (sample)" if meta.get("sample") \
        else "Experimental plan comparison matrix - {}".format(meta.get("case_id", ""))

    ET.SubElement(svg, "rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#ffffff",
    })
    # Header row.
    for j, col in enumerate([""] + plans):
        x = j * cell_w
        rect = ET.SubElement(svg, "rect", {
            "x": str(x), "y": "0", "width": str(cell_w), "height": str(head_h),
            "fill": "#eef3f8", "stroke": "#cccccc",
        })
        text = ET.SubElement(svg, "text", {
            "x": str(x + 6), "y": str(head_h / 2 + 4), "fill": "#1a1a1a",
        })
        text.text = _esc_xml(_cell_text(col))
    # Body rows.
    for i, row_name in enumerate(row_names):
        y = head_h + i * cell_h
        ET.SubElement(svg, "rect", {
            "x": "0", "y": str(y), "width": str(cell_w), "height": str(cell_h),
            "fill": "#f7f9fb", "stroke": "#cccccc",
        })
        label = ET.SubElement(svg, "text", {
            "x": "6", "y": str(y + cell_h / 2 + 4), "fill": "#1a1a1a",
        })
        label.text = _esc_xml(_cell_text(row_name))
        for j, col in enumerate(plans):
            x = (j + 1) * cell_w
            ET.SubElement(svg, "rect", {
                "x": str(x), "y": str(y), "width": str(cell_w), "height": str(cell_h),
                "fill": "#ffffff", "stroke": "#cccccc",
            })
            val = ET.SubElement(svg, "text", {
                "x": str(x + 6), "y": str(y + cell_h / 2 + 4), "fill": "#333333",
            })
            val.text = _esc_xml(_cell_text(rows[row_name].get(col)))
    tree = ET.ElementTree(svg)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def render_csv(matrix, out_path):
    """UTF-8 CSV with BOM; identical row schema for every plan."""
    plans = matrix.get("plans", [])
    rows = matrix.get("rows", {})
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\ufeff")
        writer = csv.writer(fh)
        writer.writerow(["field"] + plans)
        for row_name, values in rows.items():
            writer.writerow([row_name] + [_cell_text(values.get(p)) for p in plans])
    return out_path


def render_md(matrix, selection, meta, out_path):
    """Human-editable Markdown report with the matrix and the decision."""
    plans = matrix.get("plans", [])
    rows = matrix.get("rows", {})
    lines = []
    lines.append("# 实验方案比较报告{}".format("（示例）" if meta.get("sample") else ""))
    lines.append("")
    lines.append("- case_id: `{}`".format(meta.get("case_id", "-")))
    lines.append("- status: `{}`".format(meta.get("status", "-")))
    lines.append("")
    if selection.get("chosen"):
        lines.append("## 选择结论")
        lines.append("")
        lines.append("- 选择：`{}`".format(selection.get("chosen")))
        ranking = " > ".join(str(p) for p in selection.get("ranking", []))
        lines.append("- 排序：{}".format(ranking))
        if selection.get("rationale"):
            lines.append("- 理由：{}".format(selection.get("rationale")))
        for rej in selection.get("rejected", []):
            lines.append("- 舍弃 `{}`：{}".format(rej.get("plan_id"), rej.get("reason")))
        lines.append("")
    lines.append("## 比较矩阵")
    lines.append("")
    header = "| 字段 |" + " | ".join(plans) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(plans) + 1))
    for row_name, values in rows.items():
        cells = [_cell_text(values.get(p)) for p in plans]
        lines.append("| {} | {} |".format(row_name, " | ".join(cells)))
    lines.append("")
    if meta.get("note"):
        lines.append("> {}".format(meta["note"]))
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out_path


def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _ascii(text):
    """Coerce a string to ASCII (PDF Helvetica/WinAnsi limitation)."""
    if text is None:
        return "-"
    return str(text).encode("latin-1", "replace").decode("latin-1")


def render_pdf(matrix, selection, meta, out_path):
    """Minimal single-page ASCII PDF. Full Unicode matrix lives in SVG/MD."""
    lines = []
    lines.append("Experimental Plan Comparison Matrix{}".format(" (SAMPLE)" if meta.get("sample") else ""))
    lines.append("case_id: {}".format(meta.get("case_id", "-")))
    lines.append("status: {}".format(meta.get("status", "-")))
    if selection.get("chosen"):
        lines.append("chosen: {}".format(selection.get("chosen")))
        lines.append("ranking: {}".format(" > ".join(str(p) for p in selection.get("ranking", []))))
    lines.append("")
    plans = matrix.get("plans", [])
    rows = matrix.get("rows", {})
    width_plan = 24
    lines.append("field".ljust(28) + "".join(p[:width_plan].ljust(width_plan) for p in plans))
    for row_name, values in rows.items():
        cells = [_cell_text(values.get(p)) for p in plans]
        line = str(row_name).ljust(28)
        for cell in cells:
            line += str(cell)[:width_plan - 1].ljust(width_plan)
        lines.append(line)
    lines.append("")
    lines.append("NOTE: ASCII-only preview; full Unicode matrix is in comparison_matrix.svg / .md")

    page_h = 842.0
    y = page_h - 50.0
    fs = 8.0
    leading = 11.0
    ops = ["BT /F1 {} Tf".format(fs)]
    for ln in lines:
        if y < 50:
            break
        ops.append("1 0 0 1 40 {:.1f} Tm ({}) Tj".format(y, _pdf_escape(_ascii(ln))))
        y -= leading
    ops.append("ET")
    content = "\n".join(ops).encode("latin-1")

    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("{} 0 obj\n".format(i).encode() + obj + b"\nendobj\n")
    xref_pos = len(out)
    out += "xref\n0 {}\n".format(len(objs) + 1).encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += "{:010d} 00000 n \n".format(off).encode()
    out += ("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n"
            .format(len(objs) + 1, xref_pos)).encode()
    with open(out_path, "wb") as fh:
        fh.write(out)
    return out_path


# ----------------------------------------------------------------------------
# Contract backfill
# ----------------------------------------------------------------------------

def backfill_contract(result, analysis, contract_paths):
    """Fill every contract field into result and produce the alignment audit.

    Standard fields map from computed/analysis sections; unknown fields map from
    analysis['contract_fills']; anything left maps to null/[] and stays missing.
    """
    alignment = {}
    fills = (analysis or {}).get("contract_fills", {}) or {}
    for path in contract_paths:
        present, source = False, "missing"
        value = None
        top = path.split(".")[0]
        if top in ("experimental_plan", "evidence_and_risks", "confirmation_items"):
            value = result.get(top)
            if value not in (None, [], {}):
                present, source = True, "standard"
        elif path in fills:
            value = fills[path]
            present, source = True, "agent"
            _set_path(result, path, value)
        elif _get_path(result, path) not in (None, [], {}):
            value = _get_path(result, path)
            present, source = True, "computed"
        if not present:
            value = _empty_for_field(path)
            _set_path(result, path, value)
        alignment[path] = {
            "path": path,
            "present": present,
            "filled": present,
            "value_type": type(value).__name__ if value is not None else None,
            "source": source,
            "note": "" if present else "missing: fill via contract_fills or pending_human",
        }
    result["contract_alignment"] = alignment
    missing = [p for p, a in alignment.items() if not a["filled"]]
    if missing and analysis:
        pend = analysis.get("pending_human", [])
        for path in missing:
            note = "output_contract field '{}' has no grounded value".format(path)
            if note not in [str(p) for p in pend]:
                pend.append(note)
        analysis["pending_human"] = pend
    return result, missing


# ----------------------------------------------------------------------------
# Assemble
# ----------------------------------------------------------------------------

def _read_json_file(path, errors, label):
    if not path or not os.path.isfile(path):
        errors.append({"where": path or label, "kind": "missing", "detail": label + " not found"})
        return None, errors
    text, err = _read_text(path)
    if err:
        errors.append({"where": path, "kind": "encoding", "detail": err})
    obj, errors = _load_json_text(text, errors, path)
    return obj, errors


def assemble(input_dir, work_dir, output_dir):
    """Merge case_pack + analysis and emit the final result + artifacts."""
    started = now_iso()
    errors = []
    degradations = []
    os.makedirs(output_dir, exist_ok=True)

    case, _case_path, errors = read_case(input_dir, errors)
    contract, contract_key = discover_contract(case)
    contract_paths = flatten_contract(contract)
    if not contract_paths:
        contract_paths = list(DEFAULT_CONTRACT_FIELDS)
        contract = {f: None for f in DEFAULT_CONTRACT_FIELDS}
        contract_key = "default"

    # Load the skeleton from inspect (if present) so change_log history is kept.
    skeleton_path = os.path.join(output_dir, "result.json")
    skeleton, skel_errs = _read_json_file(skeleton_path, [], "result.json skeleton")
    errors.extend(skel_errs)

    pack_path = os.path.join(work_dir, "case_pack.json")
    analysis_path = os.path.join(work_dir, "analysis.json")
    pack, pack_errs = _read_json_file(pack_path, [], "case_pack.json")
    analysis, ana_errs = _read_json_file(analysis_path, [], "analysis.json")
    errors.extend(pack_errs)
    errors.extend(ana_errs)

    if analysis is None:
        # No analysis yet: persist an honest partial result and stop.
        result = skeleton if isinstance(skeleton, dict) else build_skeleton(
            case, contract, contract_key, input_dir, errors, degradations)
        result["status"] = result.get("status") or STATUS_PARTIAL
        result["errors"] = errors
        result["degradations"] = degradations
        result["runtime"]["finished"] = now_iso()
        result["change_log"].append({
            "seq": len(result.get("change_log", [])) + 1,
            "time": now_iso(),
            "step": "assemble",
            "note": "analysis.json absent; partial result retained",
        })
        write_result(output_dir, result)
        return result

    weights = analysis.get("weights") or dict(DEFAULT_WEIGHTS)
    constraints = (analysis.get("comparison_frame") or {}).get("hard_constraints", [])
    candidates = analysis.get("candidates") or []
    for cand in candidates:
        cand["__constraints__"] = constraints
    scored = score_candidates(candidates, weights)
    sensitivity = sensitivity_analysis(scored, weights)
    ranked = rank_candidates(scored)

    # Resolve selection: honor the agent's explicit choice if it still exists.
    selection = dict(analysis.get("selection") or {})
    if selection.get("chosen") is None and ranked:
        selection["chosen"] = str(ranked[0].get("plan_id", ranked[0].get("name", "P0")))
    if not selection.get("ranking"):
        selection["ranking"] = [str(c.get("plan_id", c.get("name", "P{}".format(i))))
                                for i, c in enumerate(ranked)]
    selection.setdefault("sensitivity", sensitivity)
    if not selection.get("rationale"):
        selection["rationale"] = None

    matrix = build_matrix(scored)

    result = dict(skeleton) if isinstance(skeleton, dict) else {}
    result.update({
        "case_id": analysis.get("case_id") or result.get("case_id"),
        "status": analysis.get("status", STATUS_PARTIAL),
        "output_contract": contract if contract is not None else result.get("output_contract", {}),
        "output_contract_key": contract_key,
        "research_question": analysis.get("research_question") or result.get("research_question"),
        "comparison_frame": analysis.get("comparison_frame") or result.get("comparison_frame"),
        "candidates": scored,
        "comparison_matrix": matrix,
        "selection": selection,
        "confirmations": analysis.get("confirmations", []),
        "sources": analysis.get("sources", []),
        "uncertainty": analysis.get("uncertainty", []),
        "pending_human": analysis.get("pending_human", []),
        "errors": errors,
        "degradations": degradations,
        "limitations": analysis.get("limitations", []),
    })

    # Standard three-group landing zones (when they are part of the contract).
    result["experimental_plan"] = {
        "candidates": scored,
        "selection": selection,
        "comparison_matrix": matrix,
    }
    result["evidence_and_risks"] = {
        "evidence": [ev for c in scored for ev in (c.get("evidence") or [])],
        "risks": [r for c in scored for r in (c.get("risks") or [])],
    }
    result["confirmation_items"] = analysis.get("confirmations", [])

    result, missing = backfill_contract(result, analysis, contract_paths)
    if missing:
        result.setdefault("limitations", [])
        for path in missing:
            note = "contract field '{}' not grounded".format(path)
            if note not in result["limitations"]:
                result["limitations"].append(note)

    # Honest status reconciliation.
    if result.get("status") not in STATUSES:
        result["status"] = STATUS_PARTIAL
    if analysis.get("status") is None or analysis.get("status") == STATUS_OK:
        feasible_any = any((c.get("thresholds") or {}).get("feasible", True) for c in scored)
        if not feasible_any and scored:
            result["status"] = STATUS_PARTIAL

    # Artifacts.
    meta = {"case_id": result.get("case_id"), "status": result.get("status"),
            "sample": bool(analysis.get("_sample"))}
    svg_path = os.path.join(output_dir, "comparison_matrix.svg")
    csv_path = os.path.join(output_dir, "comparison_matrix.csv")
    md_path = os.path.join(output_dir, "plan_comparison.md")
    pdf_path = os.path.join(output_dir, "comparison_matrix.pdf")
    try:
        render_svg(matrix, meta, svg_path)
        render_csv(matrix, csv_path)
        render_md(matrix, selection, meta, md_path)
        render_pdf(matrix, selection, meta, pdf_path)
        result["artifacts"] = [
            {"path": os.path.relpath(p, output_dir), "kind": "svg"} for p in (svg_path,)
        ] + [
            {"path": os.path.relpath(p, output_dir), "kind": "csv"} for p in (csv_path,)
        ] + [
            {"path": os.path.relpath(p, output_dir), "kind": "md"} for p in (md_path,)
        ] + [
            {"path": os.path.relpath(p, output_dir), "kind": "pdf"} for p in (pdf_path,)
        ]
    except Exception as exc:  # noqa: BLE001
        errors.append({"where": "artifacts", "kind": "render_failed", "detail": str(exc)})
        result["errors"] = errors

    result["runtime"]["finished"] = now_iso()
    result["runtime"]["elapsed_hint"] = "assembled at {}".format(now_iso())
    result["change_log"] = list(result.get("change_log") or [])
    result["change_log"].append({
        "seq": len(result["change_log"]) + 1,
        "time": now_iso(),
        "step": "assemble",
        "note": "final assembly; status={}; {} candidates; {} contract fields missing"
                .format(result["status"], len(scored), len(missing)),
    })

    write_result(output_dir, result)
    return result


def write_result(output_dir, result):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


# ----------------------------------------------------------------------------
# Validate
# ----------------------------------------------------------------------------

def validate_result(path):
    """Audit result.json. Returns (valid, issues, result)."""
    issues = []
    if not path or not os.path.isfile(path):
        return False, ["file not found: {}".format(path)], None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            result = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return False, ["invalid JSON: {}".format(exc)], None

    if not isinstance(result, dict):
        return False, ["result is not an object"], result

    if result.get("case_id") in (None, ""):
        issues.append("missing case_id")
    if result.get("status") not in STATUSES:
        issues.append("status not in {}".format(STATUSES))
    if "change_log" not in result or not isinstance(result["change_log"], list):
        issues.append("missing change_log list")
    if "sources" not in result or not isinstance(result["sources"], list):
        issues.append("missing sources list")
    if "uncertainty" not in result or not isinstance(result["uncertainty"], list):
        issues.append("missing uncertainty list")
    if "contract_alignment" not in result or not isinstance(result["contract_alignment"], dict):
        issues.append("missing contract_alignment audit")

    alignment = result.get("contract_alignment", {})
    for field_path, audit in alignment.items():
        if isinstance(audit, dict) and not audit.get("filled"):
            issues.append("contract field not filled: {}".format(field_path))

    candidates = result.get("candidates", [])
    if candidates:
        for i, cand in enumerate(candidates):
            seven = cand.get("seven_fields") or {}
            missing_seven = [f for f in SEVEN_FIELDS if not seven.get(f)]
            if missing_seven:
                issues.append("candidate[{}] missing seven_fields: {}".format(
                    i, ",".join(missing_seven)))
            ratings = cand.get("dimension_ratings") or {}
            missing_dims = [d for d in DIMENSIONS if ratings.get(d) is None]
            if missing_dims:
                issues.append("candidate[{}] missing dimension_ratings: {}".format(
                    i, ",".join(missing_dims)))
            evidence = cand.get("evidence") or []
            for ev in evidence:
                if not isinstance(ev, dict) or not ev.get("locator") or not ev.get("quote"):
                    issues.append("candidate[{}] has evidence without locator+quote".format(i))

    selection = result.get("selection") or {}
    if candidates and not selection.get("chosen"):
        issues.append("selection.chosen is null although candidates exist")

    valid = len(issues) == 0
    return valid, issues, result


def _report(valid, issues):
    for issue in issues:
        print("  [{}] {}".format("FAIL" if valid is False else "WARN", issue))
    print("validate: valid={} issues={}".format(valid, len(issues)))
    return 0 if valid else 1


# ----------------------------------------------------------------------------
# Demo (marked sample)
# ----------------------------------------------------------------------------

def make_demo_case():
    return {
        "case_id": "demo-case-0001",
        "output_contract": {
            "experimental_plan": {},
            "evidence_and_risks": {},
            "confirmation_items": {},
            "final_recommendation": "string",
        },
        "objective": "在预算 5000 元、4 天内比较三条样品表征路线并选择一条可执行路线。",
        "candidates": [
            {
                "plan_id": "P1", "name": "SEM 直接观察", "version": "v1", "n_runs": 2,
                "seven_fields": {
                    "assumptions": "样品导电、真空兼容", "variables": "加速电压、工作距离",
                    "controls": "同批样品、同仪器", "steps": "制样-镀膜-成像-测量",
                    "parameters": "加速电压 5-15kV", "resources": "SEM、镀膜仪、耗材",
                    "interpretation_criteria": "分辨率与衬度达到目标",
                },
                "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 3, "feasibility": 4, "evidence": 3},
                "equipment_time": {"amount": 800, "currency": "CNY", "kind": "per_run"},
                "consumables": {"amount": 300, "currency": "CNY", "kind": "per_run"},
                "labor": {"amount": 1000, "currency": "CNY", "kind": "once"},
                "sample_prep": {"amount": 200, "currency": "CNY", "kind": "once"},
                "scheduling": {"amount": 0, "currency": "CNY", "kind": "once"},
                "rework": {"amount": 400, "currency": "CNY", "kind": "per_run"},
                "evidence": [{"claim": "示例证据", "locator": "demo/README.md#p1", "quote": "example only", "kind": "pilot"}],
            },
            {
                "plan_id": "P2", "name": "AFM 形貌表征", "version": "v1", "n_runs": 2,
                "seven_fields": {
                    "assumptions": "样品平整", "variables": "扫描范围、速率",
                    "controls": "同批样品", "steps": "制样-定位-扫描-分析",
                    "parameters": "扫描 1-10um", "resources": "AFM、探针",
                    "interpretation_criteria": "表面粗糙度可重复",
                },
                "dimension_ratings": {"goal_fit": 5, "risk": 4, "cost": 4, "feasibility": 5, "evidence": 4},
                "equipment_time": {"amount": 500, "currency": "CNY", "kind": "per_run"},
                "consumables": {"amount": 150, "currency": "CNY", "kind": "per_run"},
                "labor": {"amount": 800, "currency": "CNY", "kind": "once"},
                "sample_prep": {"amount": 150, "currency": "CNY", "kind": "once"},
                "scheduling": {"amount": 0, "currency": "CNY", "kind": "once"},
                "rework": {"amount": 200, "currency": "CNY", "kind": "per_run"},
                "evidence": [{"claim": "示例证据", "locator": "demo/README.md#p2", "quote": "example only", "kind": "pilot"}],
            },
            {
                "plan_id": "P3", "name": "TEM 高分辨", "version": "v1", "n_runs": 2,
                "seven_fields": {
                    "assumptions": "样品可减薄", "variables": "加速电压",
                    "controls": "同批样品", "steps": "减薄-装样-成像",
                    "parameters": "加速电压 80-200kV", "resources": "TEM、减薄设备",
                    "interpretation_criteria": "晶格分辨可解释",
                },
                "dimension_ratings": {"goal_fit": 3, "risk": 2, "cost": 1, "feasibility": 2, "evidence": 3},
                "equipment_time": {"amount": 2000, "currency": "CNY", "kind": "per_run"},
                "consumables": {"amount": 800, "currency": "CNY", "kind": "per_run"},
                "labor": {"amount": 1500, "currency": "CNY", "kind": "once"},
                "sample_prep": {"amount": 600, "currency": "CNY", "kind": "once"},
                "scheduling": {"amount": 500, "currency": "CNY", "kind": "once"},
                "rework": {"amount": 1000, "currency": "CNY", "kind": "per_run"},
                "evidence": [{"claim": "示例证据", "locator": "demo/README.md#p3", "quote": "example only", "kind": "pilot"}],
            },
        ],
    }


def demo(output_dir):
    """Run the full pipeline on an inline sample; marks output as sample."""
    tmp = output_dir or os.path.join(os.getcwd(), "demo_output")
    os.makedirs(tmp, exist_ok=True)
    work = os.path.join(tmp, "_work")
    case_dir = os.path.join(tmp, "_input")
    os.makedirs(work, exist_ok=True)
    os.makedirs(case_dir, exist_ok=True)
    case = make_demo_case()
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(case_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 示例任务（demo，非正式案例）\n")

    # inspect
    result, pack = run_inspect(case_dir, output_dir=tmp, work_dir=work)
    # assemble a synthetic analysis from the skeleton candidates + contract_fills.
    analysis = {
        "case_id": case["case_id"],
        "research_question": result["research_question"],
        "comparison_frame": result["comparison_frame"],
        "weights": dict(DEFAULT_WEIGHTS),
        "candidates": [c for c in result["candidates"]],
        "selection": {},
        "confirmations": [{"item": "demo only", "type": "equipment", "status": "pending", "note": "示例"}],
        "sources": [],
        "uncertainty": [],
        "contract_fills": {"final_recommendation": "示例：选择 P2（示例输出，非正式答案）"},
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "_sample": True,
    }
    with open(os.path.join(work, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, ensure_ascii=False, indent=2)
    final = assemble(case_dir, work, tmp)
    valid, issues, _loaded = validate_result(os.path.join(tmp, "result.json"))
    print("demo: status={} valid={} issues={}".format(final.get("status"), valid, len(issues)))
    print("demo output dir: {}".format(tmp))
    return final


# ----------------------------------------------------------------------------
# Inspect entry
# ----------------------------------------------------------------------------

def run_inspect(input_dir, output_dir, work_dir):
    errors = []
    degradations = []
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case, _case_path, errors = read_case(input_dir, errors)
    contract, contract_key = discover_contract(case)
    contract_paths = flatten_contract(contract)
    if not contract_paths:
        contract_paths = list(DEFAULT_CONTRACT_FIELDS)
        contract = {f: None for f in DEFAULT_CONTRACT_FIELDS}
        contract_key = "default"
    result = build_skeleton(case, contract, contract_key, input_dir, errors, degradations)
    pack = build_case_pack(case, result, contract_paths, input_dir, errors, degradations)
    write_result(output_dir, result)
    with open(os.path.join(work_dir, "case_pack.json"), "w", encoding="utf-8") as fh:
        json.dump(pack, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print("inspect: status={} candidates={} contract_fields={}".format(
        result["status"], len(result["candidates"]), len(contract_paths)))
    return result, pack


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _parse_args(argv):
    args = {"input": os.environ.get("INPUT_DIR", "/input"),
            "output": os.environ.get("OUTPUT_DIR", "/app/output"),
            "work": os.environ.get("WORK_DIR", "/app/work")}
    cmd = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("--input", "-i"):
            args["input"] = argv[i + 1]; i += 2
        elif token in ("--output", "-o"):
            args["output"] = argv[i + 1]; i += 2
        elif token in ("--work", "-w"):
            args["work"] = argv[i + 1]; i += 2
        elif token in ("--help", "-h"):
            args["help"] = True; i += 1
        elif cmd is None and not token.startswith("-"):
            cmd = token; i += 1
        else:
            # Positional trailing path for validate.
            if cmd == "validate" and args.get("validate_path") is None and not token.startswith("-"):
                args["validate_path"] = token
            i += 1
    return cmd, args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, args = _parse_args(argv)
    try:
        if cmd == "inspect":
            run_inspect(args["input"], args["output"], args["work"])
            return 0
        if cmd == "assemble":
            result = assemble(args["input"], args["work"], args["output"])
            return 0 if result.get("status") != STATUS_INPUT_ERROR else 1
        if cmd == "validate":
            path = args.get("validate_path") or os.path.join(args["output"], "result.json")
            valid, issues, _r = validate_result(path)
            return _report(valid, issues)
        if cmd == "demo":
            demo(args["output"])
            return 0
        print("unknown command: {}".format(cmd))
        return 2
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        print("engine failed: {}".format(exc))
        # Last-resort: persist a minimal legal result so no empty output remains.
        try:
            os.makedirs(args["output"], exist_ok=True)
            minimal = {
                "case_id": None, "status": STATUS_INPUT_ERROR,
                "errors": [{"where": "engine", "kind": "crash", "detail": str(exc)}],
                "degradations": [], "sources": [], "uncertainty": [],
                "pending_human": [], "limitations": ["engine crashed; partial result only"],
                "runtime": {"started": now_iso(), "finished": now_iso()},
                "change_log": [{"seq": 1, "time": now_iso(), "step": "crash", "note": str(exc)}],
            }
            write_result(args["output"], minimal)
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
