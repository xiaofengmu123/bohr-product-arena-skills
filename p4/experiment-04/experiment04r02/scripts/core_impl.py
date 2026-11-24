"""core_impl.py - deterministic comparison engine for experimental plan selection.

Standard library only. Design goals:
  * inspect() always flushes a valid partial result.json first (early-flush contract),
  * assemble() performs real arithmetic, hard-threshold elimination, weighted scoring,
    weight-sensitivity, and emits CSV / SVG / MD / minimal-ASCII-PDF artifacts,
  * every numeric or consequential claim must carry locator + verbatim quote,
  * deterministic ordering everywhere (no randomness), so repeated runs agree.

Subcommands are dispatched from main.py via runpy; this module can also be run
directly with `python scripts/core_impl.py COMMAND`.
"""
from __future__ import annotations

import csv
import datetime
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
import xml.etree.ElementTree as ET

VERSION = "2.0.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

SEVEN_FIELDS = [
    "assumptions",
    "variables",
    "controls",
    "steps",
    "parameters",
    "resources",
    "interpretation_criteria",
]
FIVE_DIMS = ["goal_fit", "risk", "cost", "feasibility", "evidence"]
DEFAULT_WEIGHTS = {
    "goal_fit": 0.25,
    "feasibility": 0.25,
    "evidence": 0.20,
    "cost": 0.15,
    "risk": 0.15,
}
COST_PARTS = ["machine_time", "consumables", "labor", "sample_prep", "scheduling", "rework"]
TIME_PARTS = ["preparation", "scheduling", "execution", "queueing", "rework"]
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_DEFAULTS = ["experimental_plan", "evidence_and_risks", "confirmation_items"]
MAX_FILE_BYTES = 2 * 1024 * 1024

STAGES = [
    ("s1_inspect", "flush partial skeleton", 180, 3),
    ("s2_read", "read materials and verify constraints", 240, 15),
    ("s3_analyze", "fill seven fields + five-dimension ratings", 300, 30),
    ("s4_assemble", "merge analysis into result + matrix", 120, 5),
    ("s5_validate", "self-check and revise", 60, 8),
]

MATERIAL_EXTS = {".md", ".txt", ".csv", ".pdf", ".json"}
TEXT_EXTS = {".md", ".txt", ".csv", ".json"}


# --------------------------------------------------------------------------- #
# small deterministic helpers
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v):
    """Return a float when v is a plain number or numeric string, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _clamp_rating(v):
    n = _num(v)
    if n is None:
        return None
    n = int(round(n))
    if n < 0:
        return 0
    if n > 5:
        return 5
    return n


def read_text(path) -> str:
    b = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", errors="replace")


def write_json(path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path, errors):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(read_text(str(p)))
    except Exception as exc:  # noqa: BLE001 - robustness boundary
        errors.append({"path": str(p), "error": "JSON parse failed: %s" % exc})
        return None


def load_json_robust(path, errors):
    p = Path(path)
    try:
        b = p.read_bytes()
    except OSError as exc:
        errors.append({"path": str(p), "error": "read failed: %s" % exc})
        return None
    variants = []
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            variants.append(b.decode(enc))
        except (UnicodeDecodeError, LookupError):
            pass
    if not variants:
        variants = [b.decode("latin-1", errors="replace")]
    last = None
    for txt in variants:
        try:
            return json.loads(txt)
        except Exception as exc:  # noqa: BLE001
            last = exc
    errors.append({"path": str(p), "error": "JSON parse failed: %s" % last})
    return None


def _stable(items):
    return sorted(items, key=lambda x: (str(x.get("plan_id") or ""), str(x.get("name") or "")))


# --------------------------------------------------------------------------- #
# discovery (never assumes fixed key names)
# --------------------------------------------------------------------------- #

def find_all(obj, key, path="$", out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append((path + "/" + str(k), v))
            find_all(v, key, path + "/" + str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_all(v, key, path + "/" + str(i), out)
    return out


_PLAN_INDICATORS = (
    "plan_id", "name", "title", "version", "seven_fields", "steps",
    "parameters", "resources", "dimension_ratings", "cost", "feasibility",
)
_CAND_KEYS = ("candidates", "plans", "alternatives", "options", "候选方案", "方案", "候选")


def looks_like_plan(d) -> bool:
    if not isinstance(d, dict):
        return False
    keys = {str(k).lower() for k in d.keys()}
    hits = 0
    for ind in _PLAN_INDICATORS:
        if any(ind in k for k in keys):
            hits += 1
    return hits >= 2


def _normalize_candidate(item, locator):
    pid = item.get("plan_id") or item.get("id") or item.get("name") or item.get("title")
    name = item.get("name") or item.get("title") or str(pid)
    version = item.get("version") or item.get("ver") or "v?"
    return {
        "plan_id": str(pid),
        "name": str(name),
        "version": str(version),
        "locator": locator,
        "source_keys": sorted(str(k) for k in item.keys()),
    }


def _collect_plans(found, seen, val, locator):
    if not isinstance(val, list):
        return
    for i, item in enumerate(val):
        if looks_like_plan(item):
            pid = str(item.get("plan_id") or item.get("id") or item.get("name"))
            if pid not in seen:
                seen.add(pid)
                found.append(_normalize_candidate(item, "%s/%d" % (locator, i)))


def find_lists_of_plans(obj, path="$", out=None):
    if out is None:
        out = []
    if isinstance(obj, list):
        plans = [it for it in obj if looks_like_plan(it)]
        if len(plans) >= 2 and len(plans) >= 0.5 * len(obj):
            out.append((path, obj))
        for i, v in enumerate(obj):
            find_lists_of_plans(v, path + "/" + str(i), out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            find_lists_of_plans(v, path + "/" + str(k), out)
    return out


def discover_candidates(obj):
    found, seen = [], set()
    for key in _CAND_KEYS:
        for path, val in find_all(obj, key):
            _collect_plans(found, seen, val, path)
    if not found:
        for path, val in find_lists_of_plans(obj):
            _collect_plans(found, seen, val, path)
    return found


_BUDGET_RE = re.compile(r"budget|cost_limit|max_cost|预算", re.I)
_TIME_RE = re.compile(r"time_window|time_limit|max_time|duration|工期|排期|窗口", re.I)


def discover_constraints(obj, path="$", out=None, seen=None):
    if out is None:
        out = []
    if seen is None:
        seen = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            val = _num(v) if not isinstance(v, (dict, list)) else None
            if val is not None:
                if _BUDGET_RE.search(ks):
                    cid = "budget_" + ks.lower()
                    if cid not in seen:
                        seen.add(cid)
                        out.append({
                            "id": cid, "key": ks, "value": val,
                            "metric": "total_cost", "op": "<=",
                            "locator": path + "/" + ks,
                        })
                elif _TIME_RE.search(ks):
                    cid = "time_" + ks.lower()
                    if cid not in seen:
                        seen.add(cid)
                        out.append({
                            "id": cid, "key": ks, "value": val,
                            "metric": "total_time", "op": "<=",
                            "locator": path + "/" + ks,
                        })
            discover_constraints(v, path + "/" + ks, out, seen)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            discover_constraints(v, path + "/" + str(i), out, seen)
    return out


def discover_contract_keys(obj):
    for _, val in find_all(obj, "output_contract"):
        if isinstance(val, dict) and val:
            keys = [k for k in val.keys()]
            if keys:
                return keys
    return list(CONTRACT_DEFAULTS)


def extract_case_id(case_obj, case_path):
    if isinstance(case_obj, dict):
        for k in ("case_id", "challenge_id", "id"):
            v = case_obj.get(k)
            if isinstance(v, str) and v:
                return v
    if case_path:
        return Path(case_path).stem
    return "unknown_case"


def material_kind(path):
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".md", ".txt"}:
        return "text"
    if ext == ".csv":
        return "csv"
    if ext == ".json":
        return "json"
    return "other"


def scan_input(input_dir):
    base = Path(input_dir)
    result = {"case_path": None, "task_path": None, "materials": [], "skipped": []}
    if not base.exists():
        return result
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        name = p.name
        if name.startswith("."):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            result["skipped"].append({"path": str(p), "reason": "size_limit"})
            continue
        low = name.lower()
        ext = p.suffix.lower()
        if result["case_path"] is None and (low == "case.json" or (ext == ".json" and "case" in low)):
            result["case_path"] = str(p)
        elif result["task_path"] is None and low == "task.md":
            result["task_path"] = str(p)
        elif ext in MATERIAL_EXTS:
            result["materials"].append(str(p))
    if result["case_path"] is None:
        for p in sorted(base.glob("*.json")):
            result["case_path"] = str(p)
            break
    return result


def build_case_pack(input_dir, errors, degradations):
    scan = scan_input(input_dir)
    case_obj = load_json_robust(scan["case_path"], errors) if scan["case_path"] else None
    task_text = read_text(scan["task_path"]) if scan["task_path"] else None
    contract_keys = discover_contract_keys(case_obj) if case_obj else list(CONTRACT_DEFAULTS)
    candidates = discover_candidates(case_obj) if case_obj else []
    constraints = discover_constraints(case_obj) if case_obj else []
    case_id = extract_case_id(case_obj, scan.get("case_path"))
    materials = [
        {"path": p, "basename": Path(p).name, "kind": material_kind(p)}
        for p in scan["materials"]
    ]
    return {
        "case_id": case_id,
        "case_path": scan["case_path"],
        "task_path": scan["task_path"],
        "materials": materials,
        "skipped": scan["skipped"],
        "contract_keys": contract_keys,
        "candidates": candidates,
        "constraints": constraints,
        "task_text": task_text,
        "errors": errors,
        "degradations": degradations,
        "generated_at": now_iso(),
    }


# --------------------------------------------------------------------------- #
# skeleton + ledger (early-flush contract)
# --------------------------------------------------------------------------- #

def _contract_default(key):
    if key == "experimental_plan":
        return {f: None for f in SEVEN_FIELDS}
    if key == "evidence_and_risks":
        return {"evidence": [], "risks": [], "exceptions": [], "assumptions": None}
    if key == "confirmation_items":
        return []
    return None


def build_skeleton(case_id, contract_keys, status, errors, degradations, started):
    result = {
        "case_id": case_id,
        "status": status,
    }
    for k in contract_keys:
        result[k] = _contract_default(k)
    result.update({
        "research_question": None,
        "comparison_frame": None,
        "weights": dict(DEFAULT_WEIGHTS),
        "candidates": [],
        "comparison_matrix": None,
        "selection": None,
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"time": started, "step": "inspect", "note": "partial skeleton flushed (early-flush contract)"}
        ],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "runtime": {"seconds": None, "stages": {}},
        "artifacts": [],
    })
    return result


def build_ledger(started, t0, done_stage):
    stages = {}
    for sid, label, secs, reqs in STAGES:
        stages[sid] = {
            "label": label,
            "seconds_budget": secs,
            "requests_budget": reqs,
            "done": (sid == done_stage),
            "finished_at": (now_iso() if sid == done_stage else None),
        }
    return {
        "version": VERSION,
        "started_at": started,
        "elapsed_seconds": round(time.time() - t0, 1),
        "total_seconds_budget": 900,
        "total_requests_budget": 64,
        "early_flush_deadline_seconds": 180,
        "stages": stages,
    }


# --------------------------------------------------------------------------- #
# inspect command (step 1)
# --------------------------------------------------------------------------- #

def cmd_inspect(args):
    input_dir = args.get("--input") or DEFAULT_INPUT
    output_dir = args.get("--output") or DEFAULT_OUTPUT
    work_dir = args.get("--work") or DEFAULT_WORK
    started = now_iso()
    t0 = time.time()
    errors, degradations = [], []

    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(work_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print("inspect: cannot create dirs: %s" % exc)
        return

    cp = build_case_pack(input_dir, errors, degradations)

    if cp["case_path"] is None:
        errors.append({"path": input_dir, "error": "no case.json found"})
    if not cp["candidates"] and cp["case_path"]:
        degradations.append({"issue": "no candidate plans discovered in case.json"})

    status = "input_error"
    if cp["case_path"] is not None:
        status = "insufficient_evidence" if not cp["candidates"] else "partial"

    write_json(os.path.join(work_dir, "case_pack.json"), cp)
    skeleton = build_skeleton(cp["case_id"], cp["contract_keys"], status, errors, degradations, started)
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    write_json(os.path.join(work_dir, "budget_ledger.json"), build_ledger(started, t0, "s1_inspect"))

    print("inspect: status=%s case_id=%s materials=%d candidates=%d contract_keys=%d"
          % (status, cp["case_id"], len(cp["materials"]), len(cp["candidates"]), len(cp["contract_keys"])))
    print("inspect: wrote %s and %s" % (
        os.path.join(output_dir, "result.json"),
        os.path.join(work_dir, "case_pack.json"),
    ))


# --------------------------------------------------------------------------- #
# cost / time normalization
# --------------------------------------------------------------------------- #

def _parts_to_lines(parts, part_names):
    lines = []
    for k in part_names:
        v = parts.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lines.append({"item": k, "amount": float(v), "kind": "unspecified"})
        elif isinstance(v, dict):
            amt = _num(v.get("amount") if v.get("amount") is not None else v.get("total"))
            one = _num(v.get("one_time"))
            per = _num(v.get("per_run"))
            qty = _num(v.get("quantity") if v.get("quantity") is not None else v.get("runs"))
            uc = _num(v.get("unit_cost"))
            if amt is None:
                if one is not None and per is not None and qty is not None:
                    amt = one + per * qty
                elif uc is not None and qty is not None:
                    amt = uc * qty
                elif one is not None:
                    amt = one
            if amt is not None:
                lines.append({
                    "item": k,
                    "amount": float(amt),
                    "kind": str(v.get("kind") or "unspecified"),
                    "one_time": one,
                    "per_run": per,
                    "quantity": qty,
                    "unit_cost": uc,
                })
    return lines


def _summarize_lines(lines):
    total = None
    one_time_total = None
    per_run_total = None
    if lines:
        total = round(sum(float(l["amount"]) for l in lines), 2)
        one = sum(float(l["amount"]) for l in lines if l.get("kind") == "one_time")
        per = sum(float(l["amount"]) for l in lines if l.get("kind") == "per_run")
        if any(l.get("kind") == "one_time" for l in lines):
            one_time_total = round(one, 2)
        if any(l.get("kind") == "per_run" for l in lines):
            per_run_total = round(per, 2)
    return total, one_time_total, per_run_total


def normalize_cost(c):
    ov = c.get("cost_override")
    if isinstance(ov, dict) and _num(ov.get("total")) is not None:
        return {
            "method": "manual",
            "total": float(_num(ov["total"])),
            "currency": str(ov.get("currency") or "CNY"),
            "lines": ov.get("lines") or [],
            "note": ov.get("note"),
            "evidence": ov.get("evidence"),
        }
    parts = c.get("cost") if isinstance(c.get("cost"), dict) else None
    if parts:
        lines = _parts_to_lines(parts, COST_PARTS)
        total, one_time, per_run = _summarize_lines(lines)
        if total is not None:
            return {
                "method": "computed",
                "total": total,
                "currency": str(parts.get("currency") or "CNY"),
                "lines": lines,
                "one_time_total": one_time,
                "per_run_total": per_run,
                "note": None,
                "evidence": None,
            }
    return {
        "method": "not_computable", "total": None, "currency": "CNY",
        "lines": [], "note": "no cost data supplied", "evidence": None,
    }


def normalize_time(c):
    ov = c.get("time_override")
    if isinstance(ov, dict) and _num(ov.get("total")) is not None:
        return {
            "method": "manual",
            "total": float(_num(ov["total"])),
            "unit": str(ov.get("unit") or "h"),
            "parts": ov.get("parts") or {},
            "computed": bool(ov.get("computed", False)),
            "note": ov.get("note"),
            "evidence": ov.get("evidence"),
        }
    parts = c.get("time") if isinstance(c.get("time"), dict) else None
    if parts:
        lines = _parts_to_lines(parts, TIME_PARTS)
        total, _, _ = _summarize_lines(lines)
        if total is not None:
            return {
                "method": "computed", "total": total,
                "unit": str(parts.get("unit") or "h"),
                "parts": {l["item"]: l for l in lines},
                "note": None, "evidence": None,
            }
    return {
        "method": "not_computable", "total": None, "unit": "h",
        "parts": {}, "note": "no time data supplied", "evidence": None,
    }


# --------------------------------------------------------------------------- #
# candidate record construction
# --------------------------------------------------------------------------- #

def build_candidate(c, i):
    pid = c.get("plan_id") or ("P%d" % (i + 1))
    sf = c.get("seven_fields") if isinstance(c.get("seven_fields"), dict) else {}
    filled = {}
    for f in SEVEN_FIELDS:
        filled[f] = sf.get(f)
    dims = c.get("dimension_ratings") if isinstance(c.get("dimension_ratings"), dict) else {}
    ratings = {}
    for d in FIVE_DIMS:
        ratings[d] = _clamp_rating(dims.get(d))
    return {
        "plan_id": str(pid),
        "name": str(c.get("name") or pid),
        "version": str(c.get("version") or "v1"),
        "seven_fields": filled,
        "dimension_ratings": ratings,
        "cost": normalize_cost(c),
        "time": normalize_time(c),
        "feasibility": c.get("feasibility") if isinstance(c.get("feasibility"), dict) else {},
        "risks": c.get("risks") if isinstance(c.get("risks"), list) else [],
        "evidence": c.get("evidence") if isinstance(c.get("evidence"), list) else [],
        "infeasible_reasons": c.get("infeasible_reasons") if isinstance(c.get("infeasible_reasons"), list) else [],
        "eliminated": False,
        "eliminated_by": None,
        "reasons": [],
        "score": None,
    }


# --------------------------------------------------------------------------- #
# hard thresholds, scoring, sensitivity
# --------------------------------------------------------------------------- #

def _compare(actual, op, limit):
    if actual is None or limit is None:
        return True
    try:
        a = float(actual)
        b = float(limit)
    except (TypeError, ValueError):
        a = str(actual)
        b = str(limit)
    if op in ("<=", "le"):
        return a <= b
    if op in ("<", "lt"):
        return a < b
    if op in (">=", "ge"):
        return a >= b
    if op in (">", "gt"):
        return a > b
    if op in ("==", "eq"):
        return a == b
    if op in ("!=", "ne"):
        return a != b
    return True


def _eliminate(rec, constraint, reason):
    rec["eliminated"] = True
    rec["eliminated_by"] = constraint.get("id") or constraint.get("metric")
    rec["reasons"].append(reason)


def apply_thresholds(rec, constraints):
    for c in constraints:
        metric = c.get("metric")
        op = c.get("op", "<=")
        limit = c.get("value")
        if limit is None:
            continue
        if metric == "total_cost" and rec["cost"]["total"] is not None:
            if not _compare(rec["cost"]["total"], op, limit):
                _eliminate(rec, c, "cost %.2f violates %s %.4g" % (rec["cost"]["total"], op, limit))
        elif metric == "total_time" and rec["time"]["total"] is not None:
            if not _compare(rec["time"]["total"], op, limit):
                _eliminate(rec, c, "time %.2f %s violates %s %.4g" % (rec["time"]["total"], rec["time"]["unit"], op, limit))
        elif metric in ("equipment_available", "equipment"):
            av = str(rec["feasibility"].get("equipment_available") or "").lower()
            want = str(limit).lower()
            if av == "no" and want in ("yes", "required", "true"):
                _eliminate(rec, c, "required equipment unavailable (equipment_available=%s)" % av)
            elif av == "" and want in ("yes", "required", "true"):
                rec["reasons"].append("equipment availability not stated; constraint '%s' cannot be verified" % c.get("id"))
        elif metric in ("safety_approved", "safety"):
            st = str(rec["feasibility"].get("safety_approved") or "").lower()
            if st in ("no", "false") and str(limit).lower() in ("yes", "true", "required"):
                _eliminate(rec, c, "safety approval not obtained")
        elif metric in ("ethics_approved", "ethics"):
            st = str(rec["feasibility"].get("ethics_approved") or "").lower()
            if st in ("no", "false") and str(limit).lower() in ("yes", "true", "required"):
                _eliminate(rec, c, "ethics approval not obtained")


def normalize_weights(weights_in, cands):
    present = []
    for d in FIVE_DIMS:
        if any(c["dimension_ratings"].get(d) is not None for c in cands):
            present.append(d)
    if not present:
        present = list(FIVE_DIMS)
    raw = {}
    for d in present:
        v = _num(weights_in.get(d) if isinstance(weights_in, dict) else None)
        raw[d] = v if v is not None else float(DEFAULT_WEIGHTS.get(d, 0.2))
    tot = sum(raw.values())
    if tot <= 0:
        raw = {d: 1.0 / len(present) for d in present}
    else:
        raw = {d: round(v / tot, 6) for d, v in raw.items()}
    return raw


def compute_score(rec, weights):
    s = 0.0
    for d, w in weights.items():
        r = rec["dimension_ratings"].get(d)
        if r is not None:
            s += w * r
    return round(s, 3)


def _rank_top(cands, weights):
    feasible = [c for c in cands if not c["eliminated"]]
    if not feasible:
        return None
    best = max(feasible, key=lambda c: (compute_score(c, weights), _neg_name(c)))
    return best["plan_id"]


def _neg_name(c):
    # stable deterministic tie-break: lexicographically smallest name wins
    return tuple(-ord(ch) for ch in str(c["name"])) if str(c["name"]) else (0,)


def sensitivity_analysis(cands, weights_in, chosen):
    base = normalize_weights(weights_in, cands)
    flips, perturbations = [], []
    for d in base:
        for delta in (-0.05, 0.05):
            w2 = dict(base)
            w2[d] = max(0.0, w2[d] + delta)
            tot = sum(w2.values())
            w2 = {k: v / tot for k, v in w2.items()} if tot > 0 else w2
            top = _rank_top(cands, w2)
            perturbations.append({"dimension": d, "delta": delta, "top": top})
            if top != chosen:
                flips.append({"dimension": d, "delta": delta, "top": top})
    return {
        "base_weights": base,
        "perturbations": perturbations,
        "rank_flips": flips,
        "robust": bool(chosen is None or len(flips) == 0),
    }


# --------------------------------------------------------------------------- #
# matrix + serialization
# --------------------------------------------------------------------------- #

def build_matrix(cands):
    row_labels = SEVEN_FIELDS + FIVE_DIMS + ["cost_total", "time_total", "feasible"]
    col_labels = [c["plan_id"] for c in cands]
    data = []
    for f in SEVEN_FIELDS:
        data.append({"row": f, "cells": [c["seven_fields"].get(f) for c in cands]})
    for d in FIVE_DIMS:
        data.append({"row": d, "cells": [c["dimension_ratings"].get(d) for c in cands]})
    data.append({"row": "cost_total", "cells": [c["cost"]["total"] for c in cands]})
    data.append({"row": "time_total", "cells": [c["time"]["total"] for c in cands]})
    data.append({
        "row": "feasible",
        "cells": ["no" if c["eliminated"] else _feasible_label(c) for c in cands],
    })
    return {"row_labels": row_labels, "column_labels": col_labels, "data": data}


def _feasible_label(c):
    av = str(c["feasibility"].get("equipment_available") or "").lower()
    if av == "no":
        return "no"
    if av == "partial":
        return "partial"
    return "yes"


def serialize_candidate(c):
    return {
        "plan_id": c["plan_id"],
        "name": c["name"],
        "version": c["version"],
        "seven_fields": c["seven_fields"],
        "dimension_ratings": c["dimension_ratings"],
        "cost": c["cost"],
        "time": c["time"],
        "feasibility": c["feasibility"],
        "risks": c["risks"],
        "evidence": c["evidence"],
        "infeasible_reasons": c["infeasible_reasons"],
        "eliminated": c["eliminated"],
        "eliminated_by": c["eliminated_by"],
        "reasons": c["reasons"],
        "score": c["score"],
    }


# --------------------------------------------------------------------------- #
# contract synthesis
# --------------------------------------------------------------------------- #

def _find(cands, pid):
    for c in cands:
        if c["plan_id"] == pid:
            return c
    return None


def synthesize_contract(key, cands, chosen, an):
    chosen_c = _find(cands, chosen)
    if key == "experimental_plan":
        return dict(chosen_c["seven_fields"]) if chosen_c else {f: None for f in SEVEN_FIELDS}
    if key == "evidence_and_risks":
        return {
            "assumptions": chosen_c["seven_fields"]["assumptions"] if chosen_c else None,
            "evidence": (chosen_c["evidence"] if chosen_c else []) + (an.get("sources") or []),
            "risks": chosen_c["risks"] if chosen_c else [],
            "exceptions": an.get("degradations") or [],
        }
    if key == "confirmation_items":
        return an.get("confirmations") or []
    return None


def fill_contract(result, contract_keys, fills, cands, chosen, an):
    for k in contract_keys:
        if isinstance(fills, dict) and k in fills and fills[k] is not None:
            result[k] = fills[k]
        else:
            result[k] = synthesize_contract(k, cands, chosen, an)


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #

def _short(v, n=60):
    s = "" if v is None else str(v)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def emit_artifacts(output_dir, case_id, matrix, selection, cands, weights, example=False):
    artifacts = []
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. CSV (UTF-8 with BOM)
    csv_path = out / "comparison_matrix.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["field"] + matrix["column_labels"])
        for row in matrix["data"]:
            w.writerow([row["row"]] + [_short(v, 200) for v in row["cells"]])
    artifacts.append({"path": str(csv_path), "name": csv_path.name, "kind": "csv",
                      "format": "utf-8-sig", "example": example})

    # 2. SVG (XML parseable)
    svg_path = out / "comparison_matrix.svg"
    write_svg(svg_path, case_id, matrix, selection, example)
    artifacts.append({"path": str(svg_path), "name": svg_path.name, "kind": "svg",
                      "format": "xml", "example": example})

    # 3. Markdown
    md_path = out / "plan_comparison.md"
    write_markdown(md_path, case_id, matrix, selection, cands, weights, example)
    artifacts.append({"path": str(md_path), "name": md_path.name, "kind": "md",
                      "format": "utf-8", "example": example})

    # 4. minimal ASCII PDF (numeric summary only; full CJK matrix stays in SVG/MD/CSV)
    pdf_path = out / "comparison_matrix.pdf"
    write_minimal_pdf(pdf_path, case_id, matrix, selection, example)
    artifacts.append({"path": str(pdf_path), "name": pdf_path.name, "kind": "pdf",
                      "format": "%PDF", "example": example})

    return artifacts


def write_svg(path, case_id, matrix, selection, example):
    ns = "http://www.w3.org/2000/svg"
    root = ET.Element("svg", {
        "xmlns": ns, "width": "960", "height": str(120 + 26 * len(matrix["data"])),
        "viewBox": "0 0 960 %d" % (120 + 26 * len(matrix["data"])),
    })
    title = ET.SubElement(root, "title")
    title.text = "plan comparison matrix — %s%s" % (case_id, " (EXAMPLE)" if example else "")
    style = ET.SubElement(root, "style")
    style.text = (
        "text{font-family:sans-serif;font-size:12px} "
        "rect{fill:#f4f4f4;stroke:#999;stroke-width:0.5} "
        "text.cell{font-family:sans-serif;font-size:11px}"
    )
    cols = len(matrix["column_labels"]) + 1
    col_w = [180] + [max(90, int(700 / max(1, len(matrix["column_labels"]))))] * (cols - 1)
    x0 = 20
    y = 40
    ET.SubElement(root, "rect", {"x": str(x0), "y": str(y), "width": str(sum(col_w)), "height": "24"})
    x = x0
    ET.SubElement(root, "text", {"x": str(x + 4), "y": str(y + 16)}).text = "field"
    x += col_w[0]
    for j, lab in enumerate(matrix["column_labels"]):
        ET.SubElement(root, "text", {"x": str(x + 4), "y": str(y + 16)}).text = str(lab)
        x += col_w[j + 1]
    y += 26
    for row in matrix["data"]:
        ET.SubElement(root, "rect", {"x": str(x0), "y": str(y), "width": str(sum(col_w)), "height": "24"})
        x = x0
        ET.SubElement(root, "text", {"x": str(x + 4), "y": str(y + 16)}).text = str(row["row"])
        x += col_w[0]
        for j, v in enumerate(row["cells"]):
            ET.SubElement(root, "text", {"class": "cell", "x": str(x + 4), "y": str(y + 16)}).text = _short(v, 26)
            x += col_w[j + 1]
        y += 26
    y += 20
    ET.SubElement(root, "text", {"x": str(x0), "y": str(y)}).text = (
        "chosen: %s | %s" % (selection.get("chosen"), "EXAMPLE" if example else "generated")
    )
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_markdown(path, case_id, matrix, selection, cands, weights, example):
    lines = []
    lines.append("# 实验方案比较 — %s" % case_id)
    if example:
        lines.append("\n> **本文件为自检示例（EXAMPLE），不是正式案例结果。**")
    lines.append("\n## 选择结论\n")
    lines.append("- chosen: `%s`" % (selection.get("chosen")))
    lines.append("- ranking: %s" % ", ".join(selection.get("ranking") or []))
    lines.append("- robust under ±0.05 weight perturbation: `%s`" % bool(selection.get("sensitivity", {}).get("robust")))
    lines.append("\n## 可比矩阵\n")
    header = "| field | " + " | ".join(matrix["column_labels"]) + " |"
    sep = "|---|" + "---|" * len(matrix["column_labels"])
    lines.append(header)
    lines.append(sep)
    for row in matrix["data"]:
        lines.append("| %s | %s |" % (row["row"], " | ".join(_short(v, 40) for v in row["cells"])))
    lines.append("\n## 成本 / 时间 / 评分\n")
    lines.append("| plan | cost (CNY) | time (h) | score | eliminated |")
    lines.append("|---|---|---|---|---|")
    for c in cands:
        lines.append("| %s | %s | %s | %s | %s |" % (
            c["plan_id"],
            "" if c["cost"]["total"] is None else "%.2f" % c["cost"]["total"],
            "" if c["time"]["total"] is None else "%.2f" % c["time"]["total"],
            "" if c["score"] is None else "%.3f" % c["score"],
            "yes" if c["eliminated"] else "no",
        ))
    lines.append("\n## 权重\n")
    lines.append("```json\n%s\n```" % json.dumps(weights, ensure_ascii=False, indent=2))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _ascii(s):
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(s))


def _pdf_stream(lines):
    parts = ["BT", "/F1 9 Tf", "50 750 Td", "13 TL"]
    for ln in lines:
        parts.append("(%s) Tj T*" % _pdf_escape(_ascii(ln)[:110]))
    parts.append("ET")
    return "\n".join(parts)


def write_minimal_pdf(path, case_id, matrix, selection, example):
    lines = []
    lines.append("Plan comparison matrix - %s%s" % (case_id, " (EXAMPLE)" if example else ""))
    lines.append("")
    lines.append("chosen: %s" % (selection.get("chosen") or "none"))
    lines.append("ranking: %s" % (", ".join(selection.get("ranking") or [])))
    lines.append("")
    lines.append("field | " + " | ".join(matrix["column_labels"]))
    for row in matrix["data"]:
        cells = [_short(_ascii(v), 22) for v in row["cells"]]
        lines.append("%s | %s" % (row["row"], " | ".join(cells)))
    stream = _pdf_stream(lines).encode("ascii", "replace")
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        4: b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for n in range(1, 6):
        offsets[n] = len(out)
        out += ("%d 0 obj\n" % n).encode("ascii")
        out += objs[n] + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 6\n"
    out += b"0000000000 65535 f \n"
    for n in range(1, 6):
        out += ("%010d 00000 n \n" % offsets[n]).encode("ascii")
    out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
    out += str(xref_pos).encode("ascii") + b"\n%%EOF"
    Path(path).write_bytes(bytes(out))


# --------------------------------------------------------------------------- #
# assemble command (step 4)
# --------------------------------------------------------------------------- #

def collect_constraints(cp, an):
    constraints = []
    frame = an.get("comparison_frame") if isinstance(an.get("comparison_frame"), dict) else {}
    for c in frame.get("hard_constraints") or []:
        if isinstance(c, dict) and c.get("value") is not None:
            constraints.append(dict(c))
    seen = {c.get("id") for c in constraints}
    for c in cp.get("constraints") or []:
        if c.get("id") not in seen:
            constraints.append(dict(c))
            seen.add(c.get("id"))
    return constraints


def _is_complete(c):
    if not all(c["seven_fields"].get(f) for f in SEVEN_FIELDS):
        return False
    if any(c["dimension_ratings"].get(d) is None for d in FIVE_DIMS):
        return False
    if c["cost"]["method"] == "not_computable" or c["time"]["method"] == "not_computable":
        return False
    return True


def derive_status(cands, chosen):
    if not cands:
        return "insufficient_evidence"
    if chosen is None:
        return "partial"
    if all(_is_complete(c) for c in cands):
        return "ok"
    return "partial"


def append_change(result, started, step, note):
    result.setdefault("change_log", []).append({"time": started, "step": step, "note": note})


def cmd_assemble(args):
    input_dir = args.get("--input") or DEFAULT_INPUT
    output_dir = args.get("--output") or DEFAULT_OUTPUT
    work_dir = args.get("--work") or DEFAULT_WORK
    started = now_iso()
    t0 = time.time()
    errors, degradations = [], []

    result_path = os.path.join(output_dir, "result.json")

    cp_path = os.path.join(work_dir, "case_pack.json")
    cp = read_json(cp_path, errors)
    if cp is None:
        cp = build_case_pack(input_dir, errors, degradations)

    an = read_json(os.path.join(work_dir, "analysis.json"), errors)

    if an is None:
        result = build_skeleton(cp.get("case_id"), cp.get("contract_keys") or list(CONTRACT_DEFAULTS),
                                "partial", cp.get("errors") or [], cp.get("degradations") or [], started)
        result["limitations"] = ["analysis.json missing; only inspect skeleton is available"]
        result["pending_human"] = [{
            "item": "write work/analysis.json then re-run assemble",
            "reason": "analysis not yet produced at assemble time",
        }]
        append_change(result, started, "assemble", "analysis.json missing; kept partial skeleton")
        result["runtime"] = {"seconds": round(time.time() - t0, 1), "stages": {"s4_assemble": {"elapsed": round(time.time() - t0, 1)}}}
        write_json(result_path, result)
        print("assemble: analysis.json missing; kept partial skeleton (status=%s)" % result["status"])
        return

    case_id = an.get("case_id") or cp.get("case_id") or "unknown_case"
    contract_keys = cp.get("contract_keys") or list(CONTRACT_DEFAULTS)
    weights_in = an.get("weights") or dict(DEFAULT_WEIGHTS)

    cands = [build_candidate(c, i) for i, c in enumerate(an.get("candidates") or [])]
    cands = _stable(cands)

    constraints = collect_constraints(cp, an)
    for rec in cands:
        apply_thresholds(rec, constraints)

    weights = normalize_weights(weights_in, cands)
    for rec in cands:
        rec["score"] = compute_score(rec, weights)

    feasible = [c for c in cands if not c["eliminated"]]
    feasible = sorted(feasible, key=lambda c: (-c["score"], c["name"], c["plan_id"]))
    ranking = [c["plan_id"] for c in feasible]
    chosen = feasible[0]["plan_id"] if feasible else None

    sensitivity = sensitivity_analysis(cands, weights_in, chosen)

    rejected = []
    for c in cands:
        if c["eliminated"]:
            rejected.append({"plan_id": c["plan_id"], "reason": "; ".join(c["reasons"]) or "hard threshold"})
        elif c["plan_id"] != chosen:
            rejected.append({
                "plan_id": c["plan_id"],
                "reason": "lower weighted score (%.3f vs %.3f)" % (
                    c["score"], (feasible[0]["score"] if feasible else 0.0)),
            })

    sel_in = an.get("selection") if isinstance(an.get("selection"), dict) else {}
    rationale = sel_in.get("rationale") or (
        "Chosen by deterministic weighted scoring over the five calibrated dimensions; "
        "hard-threshold feasibility was applied first."
    )
    selection = {
        "chosen": chosen,
        "ranking": ranking,
        "rationale": rationale,
        "rejected": rejected,
        "sensitivity": sensitivity,
    }

    matrix = build_matrix(cands)
    example = bool(an.get("_example"))
    artifacts = emit_artifacts(output_dir, case_id, matrix, selection, cands, weights, example=example)

    result = build_skeleton(case_id, contract_keys, "partial",
                            cp.get("errors") or [], cp.get("degradations") or [], started)
    fill_contract(result, contract_keys, an.get("contract_fills"), cands, chosen, an)

    result.update({
        "case_id": case_id,
        "research_question": an.get("research_question"),
        "comparison_frame": an.get("comparison_frame"),
        "weights": weights,
        "candidates": [serialize_candidate(c) for c in cands],
        "comparison_matrix": matrix,
        "selection": selection,
        "confirmations": an.get("confirmations") or [],
        "sources": an.get("sources") or [],
        "uncertainty": an.get("uncertainty") or [],
        "pending_human": an.get("pending_human") or [],
        "errors": (cp.get("errors") or []) + (an.get("errors") or []),
        "degradations": (cp.get("degradations") or []) + (an.get("degradations") or []),
        "limitations": an.get("limitations") or [],
        "artifacts": artifacts,
    })
    result["status"] = derive_status(cands, chosen)
    append_change(result, started, "assemble", "assembled %d candidates; chosen=%s" % (len(cands), chosen))
    result["runtime"] = {
        "seconds": round(time.time() - t0, 1),
        "stages": {"s4_assemble": {"elapsed": round(time.time() - t0, 1)}},
    }
    write_json(result_path, result)

    ledger_path = os.path.join(work_dir, "budget_ledger.json")
    ledger = read_json(ledger_path, errors) or {}
    if isinstance(ledger.get("stages"), dict) and "s4_assemble" in ledger["stages"]:
        ledger["stages"]["s4_assemble"]["done"] = True
        ledger["stages"]["s4_assemble"]["finished_at"] = started
    ledger["elapsed_seconds"] = round(ledger.get("elapsed_seconds", 0) + (time.time() - t0), 1)
    write_json(ledger_path, ledger)

    print("assemble: status=%s chosen=%s ranking=%s feasible=%d/%d robust=%s"
          % (result["status"], chosen, ranking, len(feasible), len(cands), sensitivity["robust"]))
    print("assemble: wrote %s and %d artifacts" % (result_path, len(artifacts)))


# --------------------------------------------------------------------------- #
# validate command (step 5)
# --------------------------------------------------------------------------- #

def cmd_validate(args):
    path = args.get("RESULT") or os.path.join(DEFAULT_OUTPUT, "result.json")
    if not os.path.exists(path):
        print("valid: false")
        print("missing result file: %s" % path)
        return 1
    issues = []
    r = read_json(path, issues)
    if r is None:
        print("valid: false")
        print("JSON parse error: %s" % (issues[0]["error"] if issues else "unknown"))
        return 1
    checks = [
        ("case_id present", bool(r.get("case_id"))),
        ("status in allowed set", r.get("status") in STATUSES),
        ("sources is list", isinstance(r.get("sources"), list)),
        ("uncertainty is list", isinstance(r.get("uncertainty"), list)),
        ("change_log is list", isinstance(r.get("change_log"), list)),
        ("candidates is list", isinstance(r.get("candidates"), list)),
        ("comparison_matrix present", isinstance(r.get("comparison_matrix"), dict)),
        ("selection present", isinstance(r.get("selection"), dict)),
    ]
    valid = True
    for label, ok in checks:
        valid = valid and ok
        print("[%s] %s" % ("PASS" if ok else "FAIL", label))

    for art in r.get("artifacts") or []:
        p = art.get("path")
        if not p or not os.path.exists(p):
            valid = False
            print("[FAIL] artifact missing: %s" % p)
            continue
        kind = art.get("kind")
        try:
            if kind == "svg":
                import xml.etree.ElementTree as ET
                ET.parse(p)
                print("[PASS] svg parses: %s" % art.get("name"))
            elif kind == "pdf":
                head = Path(p).read_bytes()[:5]
                tail = Path(p).read_bytes()[-6:]
                okp = head == b"%PDF-" and b"%%EOF" in tail
                valid = valid and okp
                print("[%s] pdf marker: %s" % ("PASS" if okp else "FAIL", art.get("name")))
            elif kind == "csv":
                b = Path(p).read_bytes()
                okc = b.startswith(b"\xef\xbb\xbf")
                valid = valid and okc
                print("[%s] csv BOM: %s" % ("PASS" if okc else "FAIL", art.get("name")))
            elif kind == "md":
                okm = Path(p).exists() and Path(p).stat().st_size > 0
                valid = valid and okm
                print("[%s] md non-empty: %s" % ("PASS" if okm else "FAIL", art.get("name")))
        except Exception as exc:  # noqa: BLE001
            valid = False
            print("[FAIL] %s: %s" % (art.get("name"), exc))

    print("valid: %s" % ("true" if valid else "false"))
    return 0 if valid else 1


# --------------------------------------------------------------------------- #
# progress command (stage budget ledger)
# --------------------------------------------------------------------------- #

def cmd_progress(args):
    work_dir = args.get("--work") or DEFAULT_WORK
    errors = []
    ledger = read_json(os.path.join(work_dir, "budget_ledger.json"), errors)
    if ledger is None:
        print("no budget_ledger.json yet; run `inspect` first")
        return
    print("elapsed %.1fs / 900s budget | requests 0 / 64 (agent-side count)" % ledger.get("elapsed_seconds", 0))
    print("%-14s %-40s %6s %6s %s" % ("stage", "action", "secs", "reqs", "done"))
    for sid, label, secs, reqs in STAGES:
        st = ledger.get("stages", {}).get(sid, {})
        print("%-14s %-40s %6d %6d %s" % (sid, label, secs, reqs, "yes" if st.get("done") else "no"))


# --------------------------------------------------------------------------- #
# demo (clearly marked EXAMPLE)
# --------------------------------------------------------------------------- #

def write_demo_input(inp):
    base = Path(inp)
    (base / "materials").mkdir(parents=True, exist_ok=True)
    (base / "case.json").write_text(json.dumps({
        "case_id": "EXAMPLE-001",
        "title": "示例：候选实验方案比较（非正式案例，仅用于自检）",
        "output_contract": {
            "experimental_plan": {},
            "evidence_and_risks": {},
            "confirmation_items": [],
        },
        "constraints": {"budget_CNY": 50000, "time_window_h": 120},
        "candidates": [
            {"plan_id": "P1", "name": "Plan A (EXAMPLE)", "version": "v1", "feasibility": {"equipment_available": "yes"}},
            {"plan_id": "P2", "name": "Plan B (EXAMPLE)", "version": "v1", "feasibility": {"equipment_available": "yes"}},
            {"plan_id": "P3", "name": "Plan C (EXAMPLE)", "version": "v1", "feasibility": {"equipment_available": "no"}},
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (base / "task.md").write_text(
        "# 示例任务（EXAMPLE）\n本文件仅用于 Skill 自检，不是组织方正式案例。\n", encoding="utf-8")
    (base / "materials" / "quote_sheet.md").write_text(
        "# 示例报价单（EXAMPLE）\n机时 1000 CNY/h；耗材 A 单价 50 CNY/次。均为演示数值，不构成真实报价。\n",
        encoding="utf-8")


def write_demo_analysis(work):
    cand = []
    for pid, name, eq, cost, timeh in [
        ("P1", "Plan A (EXAMPLE)", "yes", 32000, 70),
        ("P2", "Plan B (EXAMPLE)", "yes", 41000, 95),
        ("P3", "Plan C (EXAMPLE)", "no", 28000, 60),
    ]:
        cand.append({
            "plan_id": pid,
            "name": name,
            "version": "v1",
            "seven_fields": {
                "assumptions": "EXAMPLE: assumptions for %s" % pid,
                "variables": "EXAMPLE: independent/dependent variables for %s" % pid,
                "controls": "EXAMPLE: controls for %s" % pid,
                "steps": "EXAMPLE: steps for %s" % pid,
                "parameters": "EXAMPLE: parameter ranges for %s" % pid,
                "resources": "EXAMPLE: equipment and consumables for %s" % pid,
                "interpretation_criteria": "EXAMPLE: readout and pass/fail criteria for %s" % pid,
            },
            "dimension_ratings": {
                "goal_fit": {"P1": 5, "P2": 4, "P3": 5}[pid],
                "risk": {"P1": 3, "P2": 4, "P3": 2}[pid],
                "cost": {"P1": 4, "P2": 3, "P3": 5}[pid],
                "feasibility": {"P1": 4, "P2": 4, "P3": 1}[pid],
                "evidence": {"P1": 4, "P2": 5, "P3": 3}[pid],
            },
            "feasibility": {"equipment_available": eq},
            "risks": [{"trigger": "EXAMPLE trigger", "consequence": "EXAMPLE consequence", "magnitude": "medium", "mitigation": "EXAMPLE mitigation", "fallback": "EXAMPLE fallback"}],
            "evidence": [{"claim": "EXAMPLE numeric claim", "locator": "materials/quote_sheet.md#L1", "quote": "机时 1000 CNY/h", "kind": "quote"}],
            "cost_override": {"total": cost, "currency": "CNY", "lines": [], "note": "EXAMPLE only", "evidence": "materials/quote_sheet.md"},
            "time_override": {"total": timeh, "unit": "h", "parts": {}, "computed": False, "note": "EXAMPLE only"},
        })
    analysis = {
        "case_id": "EXAMPLE-001",
        "research_question": {
            "testable_question": "EXAMPLE testable question",
            "variables": {"independent": [], "dependent": [], "controlled": []},
            "hypothesis": "EXAMPLE hypothesis",
        },
        "comparison_frame": {
            "objectives": ["EXAMPLE objective"],
            "hard_constraints": [
                {"id": "budget", "metric": "total_cost", "op": "<=", "value": 50000, "unit": "CNY"},
                {"id": "time_window", "metric": "total_time", "op": "<=", "value": 120, "unit": "h"},
                {"id": "equipment", "metric": "equipment_available", "op": "==", "value": "yes"},
            ],
            "criteria": list(FIVE_DIMS),
        },
        "weights": dict(DEFAULT_WEIGHTS),
        "candidates": cand,
        "selection": {"chosen": None, "ranking": [], "rationale": "EXAMPLE rationale", "rejected": [], "sensitivity": {}},
        "confirmations": [{"item": "EXAMPLE equipment confirmation", "type": "equipment", "status": "pending", "note": "EXAMPLE"}],
        "uncertainty": ["EXAMPLE uncertainty note"],
        "sources": [{"locator": "materials/quote_sheet.md", "quote": "机时 1000 CNY/h", "kind": "quote"}],
        "contract_fills": {},
        "change_log": [{"time": now_iso(), "step": "demo", "note": "EXAMPLE analysis"}],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "limitations": ["EXAMPLE only; not a real case"],
        "_example": True,
    }
    write_json(os.path.join(work, "analysis.json"), analysis)


def cmd_demo(args):
    base = Path(tempfile.mkdtemp(prefix="plan_demo_"))
    inp = base / "input"
    out = base / "output"
    work = base / "work"
    write_demo_input(str(inp))
    a = {"--input": str(inp), "--output": str(out), "--work": str(work)}
    cmd_inspect(a)
    write_demo_analysis(str(work))
    cmd_assemble(a)
    code = cmd_validate({"RESULT": str(out / "result.json"), "--output": str(out), "--work": str(work)})
    print("demo output directory: %s" % out)
    print("demo result: %s" % ("ok" if code == 0 else "invalid"))
    return 0 if code == 0 else 1


# --------------------------------------------------------------------------- #
# CLI dispatch
# --------------------------------------------------------------------------- #

def _parse_args(argv):
    args = {}
    i = 0
    positional = []
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work"):
            if i + 1 < len(argv):
                args[a] = argv[i + 1]
                i += 2
            else:
                i += 1
        elif a.startswith("--input="):
            args["--input"] = a.split("=", 1)[1]
            i += 1
        elif a.startswith("--output="):
            args["--output"] = a.split("=", 1)[1]
            i += 1
        elif a.startswith("--work="):
            args["--work"] = a.split("=", 1)[1]
            i += 1
        elif a in ("--help", "-h"):
            args["_cmd"] = "help"
            i += 1
        elif a.startswith("-"):
            i += 1
        else:
            positional.append(a)
            i += 1
    if positional:
        args["_cmd"] = positional[0]
        if len(positional) > 1:
            args["RESULT"] = positional[1]
    return args


def _print_help():
    print(
        "usage: python scripts/core_impl.py COMMAND [--input DIR] [--output DIR] [--work DIR]\n"
        "\ncommands: inspect | assemble | validate RESULT | demo | progress\n"
    )


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(argv)
    cmd = args.get("_cmd")
    if cmd in (None, "help"):
        _print_help()
        return 0
    if cmd == "inspect":
        cmd_inspect(args)
    elif cmd == "assemble":
        cmd_assemble(args)
    elif cmd == "validate":
        return cmd_validate(args)
    elif cmd == "demo":
        return cmd_demo(args)
    elif cmd == "progress":
        cmd_progress(args)
    else:
        print("unknown command: %s" % cmd)
        _print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
