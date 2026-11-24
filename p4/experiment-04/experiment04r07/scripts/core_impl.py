#!/usr/bin/env python3
"""core_impl.py — experiment plan comparison & selection engine (stdlib only).

Backend for the "sci-plan-comparison" skill. It implements a deterministic,
crash-safe pipeline:

  inspect   -> discover inputs, build a case pack, write a valid result skeleton
  assemble  -> merge agent-authored analysis.json into the final result + artifacts
  validate  -> structural self-check of /app/output/result.json
  extract   -> best-effort PDF text extraction via pdftotext (graceful fallback)
  demo      -> self-contained synthetic demo, clearly labelled as an example

Robustness design (the distinguishing emphasis of this variant):

  * Never raise to the caller. Every recoverable failure is captured in
    ``errors`` / ``degradations`` and the process exits 0.
  * A valid result.json skeleton is written *before* any analysis may fail,
    so an interrupted or failing run never leaves an empty deliverable.
  * Deterministic output for identical inputs: sorted JSON keys, stable
    ordering, no wall-clock timestamps in assembled results.
  * Oversized (>2 MB), unreadable, or mis-encoded files are skipped and
    recorded instead of crashing the run.
"""

import argparse
import csv
import io
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

VERSION = "1.1.0"
SKILL_NAME = "sci-plan-comparison"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

SEVEN_FIELDS = ["assumptions", "variables", "controls", "steps",
                "parameters", "resources", "interpretation_criteria"]
DIMENSIONS = ["goal_fit", "risk", "cost", "feasibility", "evidence"]
DEFAULT_WEIGHTS = {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
                   "cost": 0.15, "risk": 0.15}
COST_ITEMS = ["machine_time", "consumables", "labor", "sample_prep",
              "scheduling", "rework"]
TIME_PARTS = ["preparation", "execution", "scheduling", "rework"]
DEFAULT_CONTRACT_KEYS = ["experimental_plan", "evidence_and_risks",
                         "confirmation_items"]

OPS = {
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


# --------------------------------------------------------------------------
# Small deterministic helpers
# --------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def env_or(name, default):
    v = os.environ.get(name)
    return v if v else default


def _as_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except (ValueError, TypeError):
            return None
    return None


def _sorted_keys(obj):
    """Return a JSON string with stable key ordering for deterministic output."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def safe_write_text(path, text):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def safe_write_json(path, obj):
    return safe_write_text(path, _sorted_keys(obj) + "\n")


def safe_write_bytes(path, data):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file with encoding fallbacks. Never raises."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "unreadable", None
    if size > max_bytes:
        return None, "oversized", size
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), "ok", size
        except UnicodeDecodeError:
            continue
        except Exception:  # noqa: BLE001
            break
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8", errors="replace"), "decoded_with_replacement", size
    except Exception as e:  # noqa: BLE001
        return None, "unreadable", str(e)


def safe_read_json(path):
    text, status, size = read_text(path)
    if text is None:
        return None, status, None
    try:
        return json.loads(text), "ok", size
    except json.JSONDecodeError as e:
        return None, "bad_json", str(e)
    except Exception as e:  # noqa: BLE001
        return None, "bad_json", str(e)


def load_json_file(path):
    obj, status, _ = safe_read_json(path)
    if status != "ok" or not isinstance(obj, dict):
        return None
    return obj


# --------------------------------------------------------------------------
# Input discovery (never assumes fixed names, never crashes)
# --------------------------------------------------------------------------

def _file_kind(name):
    low = name.lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".json"):
        return "json"
    if low.endswith(".csv"):
        return "csv"
    if low.endswith((".md", ".txt", ".text")):
        return "text"
    if low.endswith((".svg", ".xml")):
        return "xml"
    if low.endswith((".xlsx", ".xls")):
        return "excel"
    if low.endswith((".docx", ".doc")):
        return "word"
    if low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        return "image"
    return "other"


def discover_input(input_dir):
    files = []
    errors = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        names.sort()
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            entry = {"rel": rel, "path": path, "kind": _file_kind(name)}
            try:
                size = os.path.getsize(path)
            except OSError as e:
                entry.update({"size": None, "status": "unreadable", "error": str(e)})
                errors.append("unreadable_file: %s (%s)" % (rel, e))
                files.append(entry)
                continue
            entry["size"] = size
            entry["oversized"] = size > MAX_FILE_BYTES
            entry["status"] = "oversized" if entry["oversized"] else "present"
            if entry["oversized"]:
                errors.append("oversized_file_skipped: %s (%d bytes)" % (rel, size))
            files.append(entry)
    return files, errors


# --------------------------------------------------------------------------
# Case object helpers
# --------------------------------------------------------------------------

def _looks_like_plan(d):
    if not isinstance(d, dict):
        return False
    has_name = any(k in d for k in ("name", "plan_id", "id", "title", "方案名"))
    signal = [k for k in ("steps", "parameters", "cost", "resources",
                          "variables", "risks", "feasibility", "assumptions",
                          "cost_override", "time_override", "seven_fields")
              if k in d]
    return has_name and len(signal) >= 2


def _collect_plan_dicts(node, out, seen, is_root=False):
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        if not is_root and _looks_like_plan(node):
            out.append(node)
        for v in node.values():
            _collect_plan_dicts(v, out, seen, is_root=False)
    elif isinstance(node, list):
        if id(node) in seen:
            return
        seen.add(id(node))
        for v in node:
            _collect_plan_dicts(v, out, seen, is_root=False)


def extract_candidates(case_obj):
    if not isinstance(case_obj, dict):
        return []
    found = []
    seen = set()
    # 1) honour explicit container keys first (deterministic precedence)
    for key in ("candidates", "plans", "options", "候选方案", "plans_list"):
        val = case_obj.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    found.append(item)
        elif isinstance(val, dict):
            found.append(val)
    # 2) fallback: recursive heuristic discovery (root excluded)
    if not found:
        _collect_plan_dicts(case_obj, found, seen, is_root=True)
    # de-duplicate by identity while preserving order
    uniq, seen_ids = [], set()
    for d in found:
        if id(d) not in seen_ids:
            seen_ids.add(id(d))
            uniq.append(d)
    return uniq


def normalize_candidates(dicts):
    out = []
    used_ids = set()
    for i, d in enumerate(dicts, 1):
        pid = d.get("plan_id") or d.get("id") or d.get("planId") or ("P%d" % i)
        pid = str(pid)
        if pid in used_ids:
            pid = "P%d" % i
            while pid in used_ids:
                pid = "P%d" % i
                break
        used_ids.add(pid)
        name = d.get("name") or d.get("title") or d.get("方案名") or pid
        out.append({"plan_id": pid, "name": str(name),
                    "version": str(d.get("version") or "v1"),
                    "source": d})
    return out


def extract_contract_keys(case_obj):
    if not isinstance(case_obj, dict):
        return DEFAULT_CONTRACT_KEYS, "default"
    oc = case_obj.get("output_contract")
    if isinstance(oc, dict):
        keys = [str(k) for k in oc.keys()]
        if keys:
            return keys, "case.json#output_contract"
    if isinstance(oc, list):
        keys = [str(k) for k in oc if isinstance(k, (str, int, float))]
        if keys:
            return keys, "case.json#output_contract"
    return DEFAULT_CONTRACT_KEYS, "default"


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return None
    for k in ("case_id", "caseId", "id", "challenge_id"):
        v = case_obj.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return None


def find_constraint_hits(case_obj):
    """Best-effort discovery of numeric budget / time / currency mentions.

    The agent should verify and finalise constraints in analysis.json; these
    raw hits are only a starting point and are labelled ``auto_extracted``.
    """
    budget_hits, time_hits, currency_hits = [], [], []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).lower().replace(" ", "_").replace("-", "_")
                if any(t in kl for t in ("budget", "max_cost", "cost_cap", "总预算")):
                    budget_hits.append({"key": k, "value": v})
                if any(t in kl for t in ("deadline", "time_window", "time_limit",
                                         "max_time", "lead_time", "周期")):
                    time_hits.append({"key": k, "value": v})
                if "currency" in kl or kl in ("币种",):
                    currency_hits.append({"key": k, "value": v})
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(case_obj)
    constraints = {}
    budget_nums = [_as_number(h["value"]) for h in budget_hits]
    budget_nums = [x for x in budget_nums if x is not None]
    if budget_nums:
        constraints["budget"] = min(budget_nums)
    time_nums = [_as_number(h["value"]) for h in time_hits]
    time_nums = [x for x in time_nums if x is not None]
    if time_nums:
        constraints["time_limit"] = min(time_nums)
    if currency_hits:
        constraints["currency"] = str(currency_hits[0]["value"])
    constraints["raw_hits"] = {"budget": budget_hits, "time": time_hits,
                               "currency": currency_hits}
    constraints["auto_extracted"] = True
    return constraints


# --------------------------------------------------------------------------
# PDF extraction (best effort)
# --------------------------------------------------------------------------

def try_pdftotext(path, timeout=60):
    for exe in ("pdftotext", "pdf-text-tool"):
        try:
            proc = subprocess.run([exe, path, "-"], capture_output=True,
                                  timeout=timeout, text=True,
                                  encoding="utf-8", errors="replace")
            if proc.returncode == 0 and (proc.stdout or "").strip():
                return proc.stdout, "ok"
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None, "pdftotext_unavailable"


# --------------------------------------------------------------------------
# Case pack
# --------------------------------------------------------------------------

def build_case_pack(input_dir):
    errors, degradations = [], []
    files, dir_errors = discover_input(input_dir)
    errors.extend(dir_errors)

    case_json = None
    case_obj = None
    case_present = False
    parse_status = None
    parse_error = None
    case_json_rel = None

    for f in files:
        if f["rel"].split("/")[-1] == "case.json" and case_json is None:
            case_json = f
    if case_json is None:
        for f in files:
            if f["kind"] == "json" and f["rel"].split("/")[-1].startswith("case"):
                case_json = f
                break
    if case_json is not None:
        case_json_rel = case_json["rel"]
        case_present = True
        if case_json["oversized"]:
            parse_status = "oversized"
            parse_error = "case.json exceeds %d bytes" % MAX_FILE_BYTES
            errors.append(parse_error)
        else:
            obj, status, err = safe_read_json(case_json["path"])
            parse_status = status
            parse_error = err
            if status == "ok" and isinstance(obj, dict):
                case_obj = obj
            else:
                errors.append("case.json %s: %s" % (status, err))

    contract_keys, contract_source = extract_contract_keys(case_obj)
    candidates_raw = extract_candidates(case_obj)
    candidates = normalize_candidates(candidates_raw)
    constraints = find_constraint_hits(case_obj)

    materials, pdfs = [], []
    for f in files:
        if f is case_json:
            continue
        if f["kind"] == "pdf":
            pdfs.append({"rel": f["rel"], "path": f["path"],
                         "size": f["size"], "status": f["status"]})
        elif f["kind"] in ("text", "csv", "json", "xml", "md"):
            preview = None
            if not f["oversized"]:
                text, st, _ = read_text(f["path"])
                if st == "ok" and text is not None:
                    preview = text[:500]
            materials.append({"rel": f["rel"], "path": f["path"], "kind": f["kind"],
                              "size": f["size"], "status": f["status"],
                              "preview": preview})
        else:
            materials.append({"rel": f["rel"], "path": f["path"], "kind": f["kind"],
                              "size": f["size"], "status": f["status"]})

    pack = {
        "schema": "case_pack/1.1",
        "input_dir": input_dir,
        "input_dir_exists": os.path.isdir(input_dir),
        "files": [{"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                   "status": f["status"]} for f in files],
        "case_json": {"rel": case_json_rel, "present": case_present,
                      "parse_status": parse_status, "parse_error": parse_error},
        "case_obj": case_obj,
        "case_id": extract_case_id(case_obj),
        "contract_keys": contract_keys,
        "contract_source": contract_source,
        "constraints": constraints,
        "candidates": [{"plan_id": c["plan_id"], "name": c["name"],
                        "version": c["version"], "source": c["source"]}
                       for c in candidates],
        "materials": materials,
        "pdfs": pdfs,
        "errors": errors,
        "degradations": degradations,
    }
    return pack, errors, degradations


def classify_status(pack):
    if not pack.get("input_dir_exists"):
        return STATUS_INPUT_ERROR
    if not pack["case_json"].get("present"):
        return STATUS_INPUT_ERROR
    if pack["case_json"].get("parse_status") != "ok":
        return STATUS_INPUT_ERROR
    if not pack.get("candidates"):
        return STATUS_INSUFFICIENT
    return STATUS_PARTIAL


# --------------------------------------------------------------------------
# Result skeleton / contract defaults
# --------------------------------------------------------------------------

def default_contract_value(key):
    if key in ("experimental_plan", "evidence_and_risks"):
        return {}
    if key == "confirmation_items":
        return []
    return None


def empty_research_question():
    return {"testable_question": "", "variables": {"independent": [],
                                                   "dependent": [],
                                                   "controlled": []},
            "hypothesis": ""}


def build_skeleton(pack, status, errors, degradations):
    contract_keys = pack.get("contract_keys", DEFAULT_CONTRACT_KEYS)
    result = {
        "case_id": pack.get("case_id"),
        "status": status,
        "schema_version": "1.1",
        "skill": {"name": SKILL_NAME, "version": VERSION},
        "output_contract": {"keys": contract_keys,
                            "source": pack.get("contract_source", "default")},
    }
    for k in contract_keys:
        result[k] = default_contract_value(k)
    result.update({
        "research_question": empty_research_question(),
        "comparison_frame": {"objectives": [], "hard_constraints": [],
                             "criteria": list(DIMENSIONS),
                             "weights": dict(DEFAULT_WEIGHTS),
                             "auto_extracted_constraints":
                                 pack.get("constraints", {})},
        "candidates": [],
        "comparison_matrix": {"plans": [], "row_order": [],
                              "rows": {}, "notes": ""},
        "selection": {"chosen": None, "ranking": [], "rationale": "",
                      "rejected": [], "sensitivity": {}},
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "change_log": [{"time": now_iso(), "step": "inspect",
                        "note": "result skeleton written; status=%s" % status}],
        "runtime": {"engine": SKILL_NAME, "version": VERSION,
                    "contract": "deterministic"},
        "artifacts": [{"path": "result.json", "kind": "json",
                       "status": "written"}],
        "input_summary": {
            "files": pack.get("files", []),
            "candidate_count": len(pack.get("candidates", [])),
            "material_count": len(pack.get("materials", [])),
            "pdf_count": len(pack.get("pdfs", [])),
        },
    })
    return result


# --------------------------------------------------------------------------
# Analysis normalization / arithmetic
# --------------------------------------------------------------------------

def normalize_candidate(c, idx):
    plan_id = str(c.get("plan_id") or c.get("id") or ("P%d" % (idx + 1)))
    seven = {}
    sf = c.get("seven_fields") or c.get("fields") or {}
    for f in SEVEN_FIELDS:
        v = sf.get(f, "")
        seven[f] = v if isinstance(v, (str, list, dict)) else (json.dumps(v, ensure_ascii=False) if v is not None else "")
    ratings = {}
    for d in DIMENSIONS:
        v = (c.get("dimension_ratings") or {}).get(d)
        if isinstance(v, bool):
            ratings[d] = None
        elif isinstance(v, (int, float)):
            ratings[d] = int(round(max(0.0, min(5.0, float(v)))))
        else:
            ratings[d] = None
    return {
        "plan_id": plan_id,
        "name": str(c.get("name") or plan_id),
        "version": str(c.get("version") or "v1"),
        "seven_fields": seven,
        "dimension_ratings": ratings,
        "feasibility": c.get("feasibility") or {},
        "risks": c.get("risks") or [],
        "evidence": c.get("evidence") or [],
        "infeasible_reasons": list(c.get("infeasible_reasons") or []),
        "cost_override": c.get("cost_override"),
        "time_override": c.get("time_override"),
        "cost_input": c.get("cost"),
        "time_input": c.get("time"),
    }


def stub_candidate(pc, idx):
    """Build a minimal candidate when analysis.json is missing but case pack has plans."""
    return {
        "plan_id": pc.get("plan_id", "P%d" % (idx + 1)),
        "name": pc.get("name", "P%d" % (idx + 1)),
        "version": pc.get("version", "v1"),
        "seven_fields": {f: "" for f in SEVEN_FIELDS},
        "dimension_ratings": {d: None for d in DIMENSIONS},
        "feasibility": {},
        "risks": [],
        "evidence": [],
        "infeasible_reasons": [],
        "cost_override": None,
        "time_override": None,
        "cost_input": None,
        "time_input": None,
    }


def normalized_weights(weights):
    w = {}
    total = 0.0
    for d in DIMENSIONS:
        v = _as_number(weights.get(d)) if isinstance(weights, dict) else None
        if v is not None and v >= 0:
            w[d] = v
            total += v
    if total <= 0:
        w = dict(DEFAULT_WEIGHTS)
        total = sum(w.values())
    return {d: w[d] / total for d in w}


def resolve_cost(cand):
    co = cand.get("cost_override") or {}
    total = _as_number(co.get("total"))
    if total is not None:
        return {"total": round(total, 2), "currency": co.get("currency", "CNY"),
                "lines": co.get("lines", []), "method": "agent_override",
                "note": co.get("note", ""), "evidence": co.get("evidence", "")}
    c = cand.get("cost_input") or {}
    lines, total, any_num = [], 0.0, False
    for item in COST_ITEMS:
        v = _as_number(c.get(item))
        if v is not None:
            lines.append({"item": item, "amount": round(v, 2)})
            total += v
            any_num = True
    if any_num:
        return {"total": round(total, 2), "currency": c.get("currency", "CNY"),
                "lines": lines, "method": "parts_sum",
                "note": "computed from cost parts", "evidence": ""}
    return {"total": None, "currency": c.get("currency", "CNY"),
            "lines": lines, "method": "not_computable",
            "note": "no cost numbers supplied; see pending_human", "evidence": ""}


def resolve_time(cand):
    to = cand.get("time_override") or {}
    total = _as_number(to.get("total"))
    if total is not None:
        return {"total": round(total, 2), "unit": to.get("unit", "h"),
                "parts": to.get("parts", {}), "method": "agent_override",
                "note": to.get("note", ""), "evidence": to.get("evidence", "")}
    t = cand.get("time_input") or {}
    parts, total, any_num = {}, 0.0, False
    for p in TIME_PARTS:
        v = _as_number(t.get(p))
        if v is not None:
            parts[p] = round(v, 2)
            total += v
            any_num = True
    if any_num:
        return {"total": round(total, 2), "unit": t.get("unit", "h"),
                "parts": parts, "method": "parts_sum",
                "note": "computed from time parts", "evidence": ""}
    return {"total": None, "unit": t.get("unit", "h"),
            "parts": {}, "method": "not_computable",
            "note": "no time numbers supplied; see pending_human", "evidence": ""}


def score_candidate(cand, weights):
    w = normalized_weights(weights)
    ratings = cand.get("dimension_ratings") or {}
    num, den = 0.0, 0.0
    for d in DIMENSIONS:
        r = ratings.get(d)
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            r = max(0.0, min(5.0, float(r)))
            num += w.get(d, 0.0) * r
            den += w.get(d, 0.0)
    if den <= 0:
        return None, None
    s05 = round(num / den, 4)
    return s05, round(s05 * 20, 2)


def evaluate_feasibility(cand, hard_constraints):
    reasons = list(cand.get("infeasible_reasons") or [])
    feas = cand.get("feasibility") or {}
    if feas.get("equipment_available") == "no":
        reasons.append({"reason": "equipment_available=no"})
    if feas.get("lead_time_ok") == "no":
        reasons.append({"reason": "lead_time_ok=no"})
    cost = resolve_cost(cand)["total"]
    time = resolve_time(cand)["total"]
    for c in hard_constraints:
        if not isinstance(c, dict):
            continue
        dim = str(c.get("dimension", "")).lower()
        op = str(c.get("op", "le")).lower()
        val = _as_number(c.get("value"))
        if val is None or op not in OPS:
            continue
        if dim in ("cost", "budget", "max_cost") and cost is not None:
            if not OPS[op](cost, val):
                reasons.append({"constraint": c, "actual": cost,
                                "reason": "cost %.2f violates %s %.2f" % (cost, op, val)})
        elif dim in ("time", "duration", "lead_time", "deadline") and time is not None:
            if not OPS[op](time, val):
                reasons.append({"constraint": c, "actual": time,
                                "reason": "time %.2f violates %s %.2f" % (time, op, val)})
    # de-dup reasons (stable, by json repr)
    uniq, seen = [], set()
    for r in reasons:
        key = json.dumps(r, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def top_by_score(candidates, weights):
    order = sorted(candidates,
                   key=lambda c: (not c.get("feasible", True),
                                  -(c.get("_score05") if c.get("_score05") is not None else -1.0),
                                  c.get("plan_id", "")))
    return order[0]["plan_id"] if order else None


def compute_sensitivity(candidates, weights):
    base = top_by_score(candidates, weights)
    runs, flips, results = 0, 0, {}
    for d in DIMENSIONS:
        for delta in (-0.05, 0.05):
            w = dict(weights)
            w[d] = max(0.0, float(w.get(d, DEFAULT_WEIGHTS.get(d, 0.0))) + delta)
            top = top_by_score(candidates, w)
            label = "%s%s%.2f" % (d, "+" if delta > 0 else "-", abs(delta))
            results[label] = top
            runs += 1
            if top != base:
                flips += 1
    return {"base_top": base, "perturbations": results, "top_flips": flips,
            "total_runs": runs, "stable": flips == 0}


# --------------------------------------------------------------------------
# Assemble (merge analysis.json + case pack -> final result)
# --------------------------------------------------------------------------

def build_matrix(candidates):
    plans = [c["plan_id"] for c in candidates]
    rows = {}
    for d in DIMENSIONS:
        rows[d] = {c["plan_id"]: c["dimension_ratings"].get(d) for c in candidates}
    rows["score_0_5"] = {c["plan_id"]: c["_score05"] for c in candidates}
    rows["score_100"] = {c["plan_id"]: c["_score100"] for c in candidates}
    rows["cost_total"] = {c["plan_id"]: c["cost_summary"]["total"] for c in candidates}
    rows["time_total_h"] = {c["plan_id"]: c["time_summary"]["total"] for c in candidates}
    rows["feasible"] = {c["plan_id"]: c["feasible"] for c in candidates}
    for f in SEVEN_FIELDS:
        rows[f] = {c["plan_id"]: c["seven_fields"].get(f, "") for c in candidates}
    row_order = (DIMENSIONS + ["score_0_5", "score_100", "cost_total",
                               "time_total_h", "feasible"] + SEVEN_FIELDS)
    return {"plans": plans, "row_order": row_order, "rows": rows,
            "notes": "rows are the shared comparison frame; every plan uses the "
                     "same rows so the matrix is directly comparable"}


def auto_experimental_plan(candidates, selection):
    chosen = selection.get("chosen")
    c = next((x for x in candidates if x["plan_id"] == chosen), None)
    if c is None:
        c = candidates[0] if candidates else None
    plan_list = [{"plan_id": x["plan_id"], "name": x["name"],
                  "feasible": x["feasible"]} for x in candidates]
    if c is None:
        return {"selected_plan": None, "candidate_plans": plan_list}
    return {
        "selected_plan": c["plan_id"],
        "plan_name": c["name"],
        "plan_version": c["version"],
        "seven_fields": c["seven_fields"],
        "candidate_plans": plan_list,
    }


def auto_evidence_risks(candidates, analysis):
    evidence, risks = [], []
    for c in candidates:
        for e in c.get("evidence") or []:
            if isinstance(e, dict):
                e2 = dict(e)
                e2.setdefault("plan_id", c["plan_id"])
                evidence.append(e2)
        for r in c.get("risks") or []:
            if isinstance(r, dict):
                r2 = dict(r)
                r2.setdefault("plan_id", c["plan_id"])
                risks.append(r2)
    return {"evidence": evidence, "risks": risks,
            "limitations": analysis.get("limitations") or []}


def build_final_result(pack, analysis, errors, degradations):
    contract_keys = pack.get("contract_keys", DEFAULT_CONTRACT_KEYS)
    cands_in = analysis.get("candidates") or []
    candidates = [normalize_candidate(c, i) for i, c in enumerate(cands_in)]
    if not candidates and pack.get("candidates"):
        candidates = [stub_candidate(pc, i)
                      for i, pc in enumerate(pack["candidates"])]
        degradations.append("analysis.json has no candidates; stubs from case pack "
                            "carry no ratings/evidence")

    weights = analysis.get("weights") or DEFAULT_WEIGHTS
    frame = analysis.get("comparison_frame") or {}
    hard_constraints = frame.get("hard_constraints") or []

    for c in candidates:
        c["feasibility_reasons"] = evaluate_feasibility(c, hard_constraints)
        c["feasible"] = len(c["feasibility_reasons"]) == 0
        c["cost_summary"] = resolve_cost(c)
        c["time_summary"] = resolve_time(c)
        c["_score05"], c["_score100"] = score_candidate(c, weights)

    # public score fields (drop private underscore keys before serialisation)
    for c in candidates:
        c["score"] = {"value_0_5": c["_score05"], "value_100": c["_score100"]}

    matrix = build_matrix(candidates)

    sel_in = analysis.get("selection") or {}
    ranked = sorted(candidates,
                    key=lambda c: (not c["feasible"],
                                   -(c["_score05"] if c["_score05"] is not None else -1.0),
                                   c["plan_id"]))
    ranking = sel_in.get("ranking") or [c["plan_id"] for c in ranked]
    chosen = sel_in.get("chosen")
    if not chosen:
        feas = [c for c in ranked if c["feasible"]]
        chosen = (feas[0]["plan_id"] if feas
                  else (ranked[0]["plan_id"] if ranked else None))
    sensitivity = sel_in.get("sensitivity") or compute_sensitivity(candidates, weights)
    rejected = list(sel_in.get("rejected") or [])
    existing = {str(r.get("plan_id")) for r in rejected if isinstance(r, dict)}
    for c in ranked:
        if c["plan_id"] != chosen and c["feasibility_reasons"] \
                and c["plan_id"] not in existing:
            rejected.append({"plan_id": c["plan_id"],
                             "reason": "; ".join(str(x.get("reason", ""))
                                                 for x in c["feasibility_reasons"])})
    selection = {"chosen": chosen, "ranking": ranking,
                 "rationale": sel_in.get("rationale") or "",
                 "rejected": rejected, "sensitivity": sensitivity}

    result = {
        "case_id": pack.get("case_id"),
        "status": STATUS_PARTIAL,
        "schema_version": "1.1",
        "skill": {"name": SKILL_NAME, "version": VERSION},
        "output_contract": {"keys": contract_keys,
                            "source": pack.get("contract_source", "default")},
    }
    fills = analysis.get("contract_fills") or {}
    for k in contract_keys:
        val = fills.get(k)
        if val is None:
            if k == "experimental_plan":
                val = auto_experimental_plan(candidates, selection)
            elif k == "evidence_and_risks":
                val = auto_evidence_risks(candidates, analysis)
            elif k == "confirmation_items":
                val = analysis.get("confirmations") or []
            else:
                val = default_contract_value(k)
        result[k] = val

    result.update({
        "research_question": analysis.get("research_question")
                             or empty_research_question(),
        "comparison_frame": {
            "objectives": frame.get("objectives") or [],
            "hard_constraints": hard_constraints,
            "criteria": frame.get("criteria") or list(DIMENSIONS),
            "weights": normalized_weights(weights),
            "raw_weights": dict(weights),
            "auto_extracted_constraints": pack.get("constraints", {}),
        },
        "candidates": candidates,
        "comparison_matrix": matrix,
        "selection": selection,
        "confirmations": analysis.get("confirmations") or [],
        "sources": analysis.get("sources") or [],
        "uncertainty": analysis.get("uncertainty") or [],
        "pending_human": analysis.get("pending_human") or [],
        "limitations": analysis.get("limitations") or [],
        "errors": list(errors) + list(analysis.get("errors") or []),
        "degradations": list(degradations) + list(analysis.get("degradations") or []),
        "change_log": list(analysis.get("change_log") or []),
        "runtime": {"engine": SKILL_NAME, "version": VERSION,
                    "contract": "deterministic"},
        "artifacts": [],
        "input_summary": {
            "files": pack.get("files", []),
            "candidate_count": len(pack.get("candidates", [])),
            "material_count": len(pack.get("materials", [])),
            "pdf_count": len(pack.get("pdfs", [])),
        },
    })

    # scrub private fields
    for c in candidates:
        c.pop("_score05", None)
        c.pop("_score100", None)

    # status upgrade decision
    result["status"] = decide_status(candidates, analysis, pack)
    return result


def decide_status(candidates, analysis, pack):
    if not pack.get("input_dir_exists") or not pack["case_json"].get("present"):
        return STATUS_INPUT_ERROR
    if pack["case_json"].get("parse_status") != "ok":
        return STATUS_INPUT_ERROR
    if not pack.get("candidates"):
        return STATUS_INSUFFICIENT
    if not candidates:
        return STATUS_INSUFFICIENT
    rated = [c for c in candidates
             if any(isinstance(c["dimension_ratings"].get(d), (int, float))
                    for d in DIMENSIONS)]
    evidenced = [c for c in candidates if c.get("evidence")]
    has_selection = bool(analysis.get("selection", {}).get("rationale"))
    missing_confirm = any((c.get("status") != "confirmed")
                          for c in (analysis.get("confirmations") or []))
    if rated and evidenced and has_selection and not missing_confirm:
        return STATUS_OK
    return STATUS_PARTIAL


# --------------------------------------------------------------------------
# Artifact renderers (editable + real PDF preview)
# --------------------------------------------------------------------------

def _csv_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def write_csv(matrix, path):
    plans = matrix["plans"]
    rows = matrix["row_order"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["field"] + plans)
    for r in rows:
        cells = matrix["rows"].get(r, {})
        w.writerow([r] + [_csv_value(cells.get(p)) for p in plans])
    data = "\ufeff" + buf.getvalue()  # UTF-8 BOM for Excel
    return safe_write_text(path, data)


def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


def write_svg(matrix, title, path):
    plans = matrix["plans"]
    rows = matrix["row_order"]
    ncols = len(plans) + 1
    col_w = 150
    row_h = 26
    label_w = 130
    width = label_w + col_w * len(plans)
    height = row_h * (len(rows) + 1) + 40
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'font-family="sans-serif">' % (width, height),
             '<rect width="100%%" height="100%%" fill="#ffffff"/>',
             '<text x="%d" y="22" font-size="14" font-weight="bold">%s</text>'
             % (10, xml_escape(title))]
    y = 40
    parts.append('<text x="%d" y="%d" font-size="11" font-weight="bold">field</text>'
                 % (10, y + 16))
    for j, p in enumerate(plans):
        x = label_w + j * col_w
        parts.append('<text x="%d" y="%d" font-size="11" font-weight="bold">%s</text>'
                     % (x + 4, y + 16, xml_escape(p)))
    y += row_h
    for r in rows:
        parts.append('<rect x="0" y="%d" width="%d" height="%d" fill="#f5f7fa"/>'
                     % (y - 14, width, row_h))
        parts.append('<text x="%d" y="%d" font-size="10">%s</text>'
                     % (10, y + 4, xml_escape(r)))
        cells = matrix["rows"].get(r, {})
        for j, p in enumerate(plans):
            x = label_w + j * col_w
            val = cells.get(p)
            val_s = "" if val is None else xml_escape(_csv_value(val))
            if len(val_s) > 42:
                val_s = val_s[:42] + "…"
            parts.append('<text x="%d" y="%d" font-size="10">%s</text>'
                         % (x + 4, y + 4, val_s))
        y += row_h
    parts.append('</svg>')
    return safe_write_text(path, "\n".join(parts) + "\n")


def _pdf_esc(s):
    return (str(s).replace("\\", "\\\\").replace("(", "\\(")
            .replace(")", "\\)"))


def _ascii(s):
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(s))


def _pdf_content(title, lines):
    out = ["BT", "/F1 10 Tf", "50 770 Td", "(%s) Tj" % _pdf_esc(_ascii(title)), "ET"]
    y = 748
    for ln in lines:
        if y < 42:
            break
        out.append("BT")
        out.append("/F1 9 Tf")
        out.append("50 %d Td" % y)
        out.append("(%s) Tj" % _pdf_esc(_ascii(ln)))
        out.append("ET")
        y -= 15
    return ("\n".join(out)).encode("latin-1", errors="replace")


def write_pdf(matrix, title, path):
    plans = matrix["plans"]
    rows = matrix["row_order"]
    lines = ["status: partial (numeric preview; editable full matrix in CSV/SVG/MD)",
             "plans: %s" % ", ".join(plans),
             "rows: %s" % ", ".join(rows)]
    lines.append("field | " + " | ".join(plans))
    for r in rows:
        cells = matrix["rows"].get(r, {})
        vals = []
        for p in plans:
            v = cells.get(p)
            vals.append("" if v is None else str(v)[:20])
        lines.append("%s | %s" % (r[:24], " | ".join(vals)))
    content = _pdf_content(title, lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode("latin-1") +
        b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode("latin-1") + obj + b"\nendobj\n"
    xref = len(out)
    out += ("xref\n0 %d\n" % (len(objects) + 1)).encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += ("%010d 00000 n \n" % off).encode("latin-1")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n"
            % (len(objects) + 1, xref)).encode("latin-1")
    return safe_write_bytes(path, bytes(out))


def write_markdown(result, path):
    lines = ["# 实验方案比较与选择（结果）", "",
             "- case_id: %s" % (result.get("case_id") or "-"),
             "- status: %s" % result.get("status"),
             "- chosen: %s" % (result.get("selection", {}).get("chosen") or "-"),
             ""]
    sel = result.get("selection") or {}
    ranking = sel.get("ranking") or []
    if ranking:
        lines.append("## 排名")
        lines.append("")
        for i, p in enumerate(ranking, 1):
            mark = " **（选中）**" if p == sel.get("chosen") else ""
            lines.append("%d. %s%s" % (i, p, mark))
        lines.append("")
    if sel.get("rationale"):
        lines += ["## 选择理由", "", sel["rationale"], ""]
    rejected = sel.get("rejected") or []
    if rejected:
        lines += ["## 舍弃理由", ""]
        for r in rejected:
            lines.append("- %s: %s" % (r.get("plan_id"), r.get("reason", "")))
        lines.append("")
    matrix = result.get("comparison_matrix") or {}
    plans = matrix.get("plans") or []
    rows = matrix.get("row_order") or []
    if plans and rows:
        lines += ["## 比较矩阵", "", "| field | " + " | ".join(plans) + " |",
                  "|---|---" + "|---" * len(plans) + "|"]
        for r in rows:
            cells = matrix.get("rows", {}).get(r, {})
            vals = [_csv_value(cells.get(p)) for p in plans]
            vals = [v.replace("|", "\\|").replace("\n", " ") for v in vals]
            lines.append("| %s | %s |" % (r, " | ".join(vals)))
        lines.append("")
    lines += ["## 确认项（未确认 = pending）", ""]
    for c in result.get("confirmations") or []:
        lines.append("- [%s] %s" % (c.get("status", "pending"), c.get("item", "")))
    lines.append("")
    lines += ["## 待人工处理", ""]
    ph = result.get("pending_human") or []
    if ph:
        for x in ph:
            lines.append("- %s" % x)
    else:
        lines.append("- （无）")
    lines.append("")
    return safe_write_text(path, "\n".join(lines) + "\n")


def write_artifacts(result, output_dir):
    artifacts = []
    matrix = result.get("comparison_matrix") or {}
    title = "experiment-plan-comparison case=%s" % (result.get("case_id") or "unknown")

    for name, writer in (
            ("comparison_matrix.csv", lambda p: write_csv(matrix, p)),
            ("comparison_matrix.svg", lambda p: write_svg(matrix, title, p)),
            ("comparison_matrix.pdf", lambda p: write_pdf(matrix, title, p)),
            ("plan_comparison.md", lambda p: write_markdown(result, p))):
        path = os.path.join(output_dir, name)
        ok, err = writer(path)
        artifacts.append({"path": name, "kind": name.split(".")[-1],
                          "status": "written" if ok else "failed",
                          "error": err if not ok else None})
    result["artifacts"] = [{"path": "result.json", "kind": "json",
                            "status": "written"}] + artifacts
    return result


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def _paths(args):
    return args.input, args.output, args.work


def cmd_inspect(args):
    pack, errors, degradations = build_case_pack(args.input)
    status = classify_status(pack)
    skeleton = build_skeleton(pack, status, errors, degradations)
    ok1, e1 = safe_write_json(os.path.join(args.work, "case_pack.json"), pack)
    if not ok1:
        degradations.append("cannot write case_pack.json: %s" % e1)
    result_path = os.path.join(args.output, "result.json")
    ok2, e2 = safe_write_json(result_path, skeleton)
    if not ok2:
        # last-resort fallback so an unwritable output dir still leaves a product
        result_path = os.path.join(args.work, "result.skeleton.json")
        safe_write_json(result_path, skeleton)
        degradations.append("cannot write %s: %s; skeleton at %s"
                            % (os.path.join(args.output, "result.json"), e2, result_path))
    summary = {"case_id": pack.get("case_id"), "status": status,
               "candidate_count": len(pack.get("candidates", [])),
               "material_count": len(pack.get("materials", [])),
               "pdf_count": len(pack.get("pdfs", [])),
               "contract_keys": pack.get("contract_keys"),
               "errors": errors, "degradations": degradations,
               "result_path": result_path}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_assemble(args):
    errors, degradations = [], []
    for d in (args.work, args.output):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            degradations.append("cannot mkdir %s: %s" % (d, e))
    pack, errs, degs = build_case_pack(args.input)
    errors.extend(errs)
    degradations.extend(degs)
    analysis = load_json_file(os.path.join(args.work, "analysis.json"))
    if analysis is None:
        degradations.append("analysis.json missing or invalid; assembling from case pack only")
        analysis = {}
    result = build_final_result(pack, analysis, errors, degradations)
    result = write_artifacts(result, args.output)
    result_path = os.path.join(args.output, "result.json")
    ok, e = safe_write_json(result_path, result)
    if not ok:
        result_path = os.path.join(args.work, "result.json")
        safe_write_json(result_path, result)
        degradations.append("cannot write %s: %s; wrote %s"
                            % (os.path.join(args.output, "result.json"), e, result_path))
    print(json.dumps({"case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "chosen": result.get("selection", {}).get("chosen"),
                      "candidate_count": len(result.get("candidates", [])),
                      "artifacts": result.get("artifacts"),
                      "errors": result.get("errors"),
                      "degradations": result.get("degradations"),
                      "result_path": result_path},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_extract(args):
    pack, errors, degradations = build_case_pack(args.input)
    out_dir = os.path.join(args.work, "extracted")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        degradations.append("cannot mkdir extracted: %s" % e)
    results = []
    for pdf in pack.get("pdfs", []):
        if pdf["status"] == "oversized":
            results.append({"rel": pdf["rel"], "status": "oversized_skipped"})
            continue
        text, status = try_pdftotext(pdf["path"])
        if status == "ok":
            out_name = pdf["rel"].replace("/", "__").replace("\\", "__") + ".txt"
            safe_write_text(os.path.join(out_dir, out_name), text)
            results.append({"rel": pdf["rel"], "out": out_name, "status": "ok",
                            "chars": len(text)})
        else:
            results.append({"rel": pdf["rel"], "status": status})
    report = {"pdfs": results, "errors": errors, "degradations": degradations,
              "output_dir": out_dir}
    safe_write_json(os.path.join(args.work, "extract_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def validate_result(path):
    checks, errors = [], []
    out_dir = os.path.dirname(os.path.abspath(path))
    if not os.path.isfile(path):
        return {"valid": False, "path": path, "errors": ["result.json missing"],
                "checks": []}
    obj, status, err = safe_read_json(path)
    if status != "ok" or not isinstance(obj, dict):
        return {"valid": False, "path": path,
                "errors": ["bad JSON: %s" % err], "checks": []}
    if "case_id" in obj:
        checks.append("case_id present")
    else:
        errors.append("case_id key missing")
    if obj.get("status") in STATUSES:
        checks.append("status valid")
    else:
        errors.append("status invalid: %r" % obj.get("status"))
    for k in ("sources", "uncertainty", "change_log", "pending_human",
              "errors", "degradations", "runtime", "artifacts", "limitations"):
        if k in obj:
            checks.append("%s present" % k)
        else:
            errors.append("%s key missing" % k)
    contract = obj.get("output_contract") or {}
    keys = contract.get("keys") or []
    for k in keys:
        if k in obj:
            checks.append("contract field %s present" % k)
        else:
            errors.append("contract field %s missing" % k)
    for c in obj.get("candidates") or []:
        pid = c.get("plan_id", "?")
        sf = c.get("seven_fields") or {}
        missing = [f for f in SEVEN_FIELDS if f not in sf]
        if missing:
            errors.append("%s: seven_fields missing %s" % (pid, missing))
        else:
            checks.append("%s: seven_fields complete" % pid)
        dr = c.get("dimension_ratings") or {}
        rated = [d for d in DIMENSIONS if isinstance(dr.get(d), (int, float))]
        if not rated:
            errors.append("%s: no dimension ratings" % pid)
        else:
            checks.append("%s: %d dimensions rated" % (pid, len(rated)))
    for a in obj.get("artifacts") or []:
        rel = a.get("path", "")
        if rel and rel != "result.json" and not os.path.isfile(os.path.join(out_dir, rel)):
            errors.append("artifact missing: %s" % rel)
        elif rel:
            checks.append("artifact %s present" % rel)
    valid = len(errors) == 0
    return {"valid": valid, "path": path, "errors": errors, "checks": checks}


def cmd_validate(args):
    path = args.path or os.path.join(args.output, "result.json")
    report = validate_result(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if (report["valid"] or not args.strict) else 1


# --------------------------------------------------------------------------
# Demo (self-contained synthetic example, clearly labelled)
# --------------------------------------------------------------------------

DEMO_CASE = {
    "case_id": "demo-synthetic-001",
    "title": "示例：三方案比较（合成数据，非正式案例）",
    "output_contract": {
        "experimental_plan": {},
        "evidence_and_risks": {},
        "confirmation_items": [],
    },
    "budget": 500000,
    "currency": "CNY",
    "time_window_hours": 300,
    "candidates": [
        {"plan_id": "P1", "name": "路线A-标准流程",
         "cost": {"machine_time": 120000, "consumables": 80000, "labor": 40000,
                  "sample_prep": 20000, "scheduling": 10000, "rework": 20000},
         "time": {"preparation": 24, "execution": 96, "scheduling": 48, "rework": 24}},
        {"plan_id": "P2", "name": "路线B-高证据",
         "cost": {"machine_time": 200000, "consumables": 120000, "labor": 60000,
                  "sample_prep": 30000, "scheduling": 15000, "rework": 30000},
         "time": {"preparation": 36, "execution": 144, "scheduling": 72, "rework": 36}},
        {"plan_id": "P3", "name": "路线C-低成本",
         "cost": {"machine_time": 60000, "consumables": 50000, "labor": 30000,
                  "sample_prep": 15000, "scheduling": 8000, "rework": 15000},
         "time": {"preparation": 20, "execution": 72, "scheduling": 40, "rework": 20}},
    ],
}

DEMO_ANALYSIS = {
    "case_id": "demo-synthetic-001",
    "research_question": {
        "testable_question": "示例：三种路线在同一目标下如何权衡（合成示例）",
        "variables": {"independent": ["route"], "dependent": ["cost", "time"],
                      "controlled": ["budget", "time window"]},
        "hypothesis": "示例假设",
    },
    "comparison_frame": {
        "objectives": ["示例目标"],
        "hard_constraints": [{"dimension": "cost", "op": "le", "value": 500000},
                             {"dimension": "time", "op": "le", "value": 300}],
        "criteria": DIMENSIONS,
    },
    "weights": dict(DEFAULT_WEIGHTS),
    "candidates": [
        {"plan_id": "P1", "name": "路线A-标准流程",
         "seven_fields": {f: ("示例%s" % f) for f in SEVEN_FIELDS},
         "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 4,
                               "feasibility": 4, "evidence": 3},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
         "risks": [{"trigger": "示例触发", "consequence": "示例后果",
                    "magnitude": "中", "mitigation": "示例缓解",
                    "fallback": "示例回退"}],
         "evidence": [{"claim": "示例依据", "locator": "demo/synthetic",
                       "quote": "示例引用", "kind": "literature"}],
         "cost_override": None, "time_override": None,
         "cost": {"machine_time": 120000, "consumables": 80000, "labor": 40000,
                  "sample_prep": 20000, "scheduling": 10000, "rework": 20000},
         "time": {"preparation": 24, "execution": 96, "scheduling": 48,
                  "rework": 24}},
        {"plan_id": "P2", "name": "路线B-高证据",
         "seven_fields": {f: ("示例%s" % f) for f in SEVEN_FIELDS},
         "dimension_ratings": {"goal_fit": 5, "risk": 3, "cost": 2,
                               "feasibility": 3, "evidence": 5},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
         "risks": [], "evidence": [],
         "cost_override": None, "time_override": None,
         "cost": {"machine_time": 200000, "consumables": 120000, "labor": 60000,
                  "sample_prep": 30000, "scheduling": 15000, "rework": 30000},
         "time": {"preparation": 36, "execution": 144, "scheduling": 72,
                  "rework": 36}},
        {"plan_id": "P3", "name": "路线C-低成本",
         "seven_fields": {f: ("示例%s" % f) for f in SEVEN_FIELDS},
         "dimension_ratings": {"goal_fit": 3, "risk": 4, "cost": 5,
                               "feasibility": 4, "evidence": 2},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
         "risks": [], "evidence": [],
         "cost_override": None, "time_override": None,
         "cost": {"machine_time": 60000, "consumables": 50000, "labor": 30000,
                  "sample_prep": 15000, "scheduling": 8000, "rework": 15000},
         "time": {"preparation": 20, "execution": 72, "scheduling": 40,
                  "rework": 20}},
    ],
    "selection": {"chosen": "P2", "ranking": ["P2", "P1", "P3"],
                  "rationale": "示例理由：证据强度优先（合成示例，非正式结论）",
                  "rejected": [{"plan_id": "P3", "reason": "示例：证据不足"}]},
    "confirmations": [{"item": "示例确认项", "type": "expert",
                       "status": "confirmed", "note": "示例"}],
    "uncertainty": [], "sources": [], "contract_fills": {},
    "change_log": [{"time": "2026-01-01T00:00:00+00:00", "step": "demo",
                    "note": "synthetic demo, not a real case"}],
    "errors": [], "degradations": [], "pending_human": [],
    "limitations": ["全部为合成示例数据"],
}


def cmd_demo(args):
    base = args.work if args.work else os.path.join(os.getcwd(), ".demo_work")
    demo_in = os.path.join(base, "demo_input")
    demo_out = os.path.join(base, "demo_output")
    demo_work = os.path.join(base, "demo_work")
    for d in (demo_in, demo_out, demo_work):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": "mkdir %s: %s" % (d, e)},
                             ensure_ascii=False))
            return 1
    safe_write_json(os.path.join(demo_in, "case.json"), DEMO_CASE)
    safe_write_text(os.path.join(demo_in, "task.md"),
                    "# 示例任务说明（合成示例，非正式案例）\n")
    safe_write_text(os.path.join(demo_in, "materials", "sample.txt"),
                    "synthetic demo material\n")
    safe_write_json(os.path.join(demo_work, "analysis.json"), DEMO_ANALYSIS)

    class _A:
        input = demo_in
        output = demo_out
        work = demo_work
        path = None
        strict = False

    a = _A()
    cmd_inspect(a)
    cmd_assemble(a)
    report = validate_result(os.path.join(demo_out, "result.json"))
    report["demo"] = True
    report["note"] = "synthetic demo only; not a formal case result"
    report["output_dir"] = demo_out
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="Experiment plan comparison & selection engine (stdlib only).")
    ap.add_argument("cmd", nargs="?", default="inspect",
                    choices=["inspect", "assemble", "validate", "extract", "demo"])
    ap.add_argument("path", nargs="?", default=None,
                    help="result.json path for validate")
    ap.add_argument("--input", default=env_or("SKILL_INPUT", DEFAULT_INPUT))
    ap.add_argument("--output", default=env_or("SKILL_OUTPUT", DEFAULT_OUTPUT))
    ap.add_argument("--work", default=env_or("SKILL_WORK", DEFAULT_WORK))
    ap.add_argument("--strict", action="store_true",
                    help="validate: exit non-zero when invalid")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "inspect":
            return cmd_inspect(args)
        if args.cmd == "assemble":
            return cmd_assemble(args)
        if args.cmd == "validate":
            return cmd_validate(args)
        if args.cmd == "extract":
            return cmd_extract(args)
        if args.cmd == "demo":
            return cmd_demo(args)
    except Exception as e:  # noqa: BLE001 — never crash the harness
        print(json.dumps({"ok": False, "fatal_error": str(e),
                          "cmd": args.cmd}, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
