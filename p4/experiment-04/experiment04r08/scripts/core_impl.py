#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py - implementation of the experiment-plan-comparison skill.

Standard library only. Deterministic: all output uses sort_keys=True and stable
ordering; no randomness. The pipeline is:

    inspect  -> /app/output/result.json (legal skeleton, written first)
             -> /app/work/case_pack.json + /app/work/analysis.json (template)
    assemble -> merges /app/work/analysis.json + case_pack.json, does real
                arithmetic, hard-threshold elimination, weighted scoring,
                sensitivity, and writes result.json + comparison artifacts.
    validate -> contract / rubric / artifact self-check on a result.json.
    demo     -> runs inspect+assemble+validate on a synthetic, clearly-marked
                example in a temporary directory.

Run with:
    python scripts/main.py inspect|assemble|validate|demo [--input DIR ...]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

SKILL_NAME = "experiment-plan-comparison"
VERSION = "1.0.0"
RUBRIC_VERSION = "1.0.0"

DEFAULT_INPUT = os.environ.get("SKILL_INPUT", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK", "/app/work")

MAX_SINGLE_FILE = 2 * 1024 * 1024  # 2 MB
VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

SEVEN_FIELDS = [
    "assumptions", "variables", "controls", "steps",
    "parameters", "resources", "interpretation_criteria",
]
FIVE_DIMS = ["goal_fit", "risk", "cost", "feasibility", "evidence"]
COST_COMPONENTS = [
    "machine_hours", "consumables", "labor", "sample_prep", "scheduling", "rework",
]
TIME_COMPONENTS = ["preparation", "scheduling", "execution", "rework"]
DEFAULT_WEIGHTS = {
    "goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
    "cost": 0.15, "risk": 0.15,
}
DEFAULT_CONTRACT_KEYS = [
    "experimental_plan", "evidence_and_risks", "confirmation_items",
]

RUBRIC = {
    "actual_effect": {
        "points": 40,
        "subitems": ["task_completion", "correctness", "completeness", "verifiability"],
        "served_by": ["comparison_matrix", "selection", "candidates", "sources"],
        "selfcheck": [
            "all candidates fill the same seven-field matrix",
            "every number/parameter/conclusion points to locator+quote",
            "selection has chosen+ranking+rationale+rejected",
        ],
    },
    "usage_gain": {
        "points": 25,
        "subitems": ["quality_gain", "efficiency_gain", "gain_verifiability"],
        "served_by": ["seven_field_template", "cost_time_arithmetic", "evidence_template"],
        "selfcheck": [
            "one shared template avoids per-plan re-alignment",
            "cost/time components are summed by script, not by hand",
            "efficiency gain is reproducible via assemble re-run",
        ],
    },
    "innovation": {
        "points": 15,
        "subitems": ["new_capability", "method_improvement", "innovation_evidence"],
        "served_by": ["hard_thresholds", "scoring_matrix", "sensitivity", "evidence_ledger"],
        "selfcheck": [
            "hard-threshold rules are executed and recorded",
            "weight +/-0.05 sensitivity is computed",
            "evidence ledger deduplicates locator+quote pairs",
        ],
    },
    "reliability": {
        "points": 10,
        "subitems": ["usability", "stability", "exception_handling"],
        "served_by": ["inspect", "assemble", "validate", "selftest", "degradation"],
        "selfcheck": [
            "empty input / bad JSON / missing args exit 0 without crash",
            "repeated runs with same input give identical results",
            "infeasible or unresolved cases are marked partial + pending_human",
        ],
    },
    "ecosystem": {
        "points": 10,
        "subitems": ["doc_completeness", "reusability", "sharing_value"],
        "served_by": ["SKILL.md", "rubric-mapping.md", "checklist.md", "templates"],
        "selfcheck": [
            "SKILL.md and scripts versions match",
            "artifacts (json/csv/svg/md/pdf) open and parse",
            "another agent can reproduce from SKILL.md alone",
        ],
    },
}


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def write_json(path, obj):
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    text = jdump(obj)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def read_text(path, max_bytes=MAX_SINGLE_FILE):
    """Read a UTF-8 text file with BOM/encoding tolerance. Returns (text, error)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_failed: %s" % exc
    if size > max_bytes:
        return None, "skipped: size %d > %d bytes" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_failed: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "decode_fallback"


def read_json(path):
    text, err = read_text(path)
    if err is not None and "skipped" in err:
        return None, err
    if text is None:
        return None, err or "empty"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        return None, "bad_json: %s" % exc


def sanitize_xml_text(text):
    if not isinstance(text, str):
        text = str(text)
    return "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 0x20)


def ascii_fold(text):
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", "replace").decode("ascii")


def clip(text, limit=80):
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Input discovery (recursive: do not assume fixed key names)
# --------------------------------------------------------------------------- #
def walk_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.startswith(".") or fn.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, fn)
            out.append(full)
    return out


def find_case_file(input_dir):
    files = walk_files(input_dir)
    for full in files:
        base = os.path.basename(full).lower()
        if base == "case.json":
            return full
    for full in files:
        if full.lower().endswith(".json"):
            return full
    return None


def _is_candidate_dict(obj):
    if not isinstance(obj, dict):
        return False
    hits = 0
    for key in ("plan_id", "name", "title", "steps", "步骤", "parameters", "参数",
                "resources", "资源", "cost", "成本", "variables", "变量", "方案"):
        if key in obj:
            hits += 1
    return hits >= 2


def _walk(obj, path=""):
    """Yield (key_path, value) for every nested container/leaf."""
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = "%s.%s" % (path, k) if path else str(k)
            yield from _walk(v, kp)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, "%s[%d]" % (path, i))


def discover_candidates(doc):
    """Recursively find list items that look like candidate plans."""
    found = []
    seen = set()

    def visit(node):
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and _is_candidate_dict(item):
                    marker = json.dumps(item, sort_keys=True, default=str)
                    if marker not in seen:
                        seen.add(marker)
                        found.append(item)
                else:
                    visit(item)
        elif isinstance(node, dict):
            for v in node.values():
                visit(v)

    visit(doc)
    return found


def discover_constraints(doc):
    """Best-effort discovery of budget and time window. No fabrication: null when absent."""
    budget_value = None
    time_value = None
    budget_source = None
    time_source = None
    for path, val in _walk(doc):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        key = path.split(".")[-1].lower()
        if budget_value is None and key in (
            "budget", "total_budget", "max_cost", "total_cost_limit", "预算", "总预算",
        ):
            budget_value = float(val)
            budget_source = path
        if time_value is None and key in (
            "time_window", "deadline_hours", "max_time", "window_hours", "时间窗", "期限",
        ):
            time_value = float(val)
            time_source = path
    return {
        "budget": {"value": budget_value, "currency": "CNY", "source": budget_source},
        "time_window": {"value": time_value, "unit": "h", "source": time_source},
    }


def derive_case_id(doc, case_path):
    for key in ("case_id", "id", "challenge_id", "challengeId", "caseId"):
        v = doc.get(key) if isinstance(doc, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    digest = hashlib.sha256(os.path.basename(case_path).encode("utf-8")).hexdigest()[:12]
    return "case-" + digest


# --------------------------------------------------------------------------- #
# Scoring / arithmetic (deterministic)
# --------------------------------------------------------------------------- #
def normalize_weights(weights):
    w = {}
    for k in FIVE_DIMS:
        v = (weights or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            w[k] = float(v)
    if not w:
        w = dict(DEFAULT_WEIGHTS)
    total = sum(w.values())
    if total <= 0:
        w = dict(DEFAULT_WEIGHTS)
        total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def resolve_cost(cand):
    c = cand.get("cost") or {}
    ov = cand.get("cost_override")
    method = c.get("method", "computed")
    components = c.get("components") or {}
    currency = c.get("currency", "CNY")
    if isinstance(ov, dict) and _num(ov.get("total")) is not None:
        return {
            "total": float(ov["total"]),
            "currency": ov.get("currency", currency),
            "components": components,
            "one_time": c.get("one_time") or ov.get("one_time") or [],
            "method": "override",
            "note": ov.get("note", c.get("note", "")),
            "evidence": ov.get("evidence", c.get("evidence", "")),
            "lines": ov.get("lines") or [],
        }
    if method == "not_computable":
        return {
            "total": None, "currency": currency, "components": components,
            "one_time": c.get("one_time") or [], "method": "not_computable",
            "note": c.get("note", ""), "evidence": c.get("evidence", ""), "lines": [],
        }
    total = 0.0
    for k in COST_COMPONENTS:
        v = _num(components.get(k))
        if v is not None:
            total += v
    return {
        "total": total, "currency": currency, "components": components,
        "one_time": c.get("one_time") or [], "method": "computed",
        "note": c.get("note", ""), "evidence": c.get("evidence", ""), "lines": [],
    }


def resolve_time(cand):
    t = cand.get("time") or {}
    ov = cand.get("time_override")
    method = t.get("method", "computed")
    components = t.get("components") or {}
    unit = t.get("unit", "h")
    if isinstance(ov, dict) and _num(ov.get("total")) is not None:
        return {
            "total": float(ov["total"]), "unit": ov.get("unit", unit),
            "components": components, "method": "override",
            "note": ov.get("note", t.get("note", "")),
            "evidence": ov.get("evidence", t.get("evidence", "")),
        }
    if method == "not_computable":
        return {
            "total": None, "unit": unit, "components": components,
            "method": "not_computable", "note": t.get("note", ""),
            "evidence": t.get("evidence", ""),
        }
    total = 0.0
    for k in TIME_COMPONENTS:
        v = _num(components.get(k))
        if v is not None:
            total += v
    return {
        "total": total, "unit": unit, "components": components,
        "method": "computed", "note": t.get("note", ""), "evidence": t.get("evidence", ""),
    }


def weighted_score(ratings, weights):
    score = 0.0
    for k, w in weights.items():
        r = ratings.get(k)
        if not isinstance(r, (int, float)) or isinstance(r, bool):
            r = 0.0
        score += w * max(0.0, min(5.0, float(r)))
    return round(score, 4)


def evaluate_constraints(cand, cost, time, constraints):
    reasons = []
    unresolved = []
    fe = cand.get("feasibility") or {}
    if fe.get("equipment_available") == "no":
        reasons.append("equipment_unavailable")
    if fe.get("lead_time_ok") == "no":
        reasons.append("lead_time_not_ok")
    budget = (constraints or {}).get("budget") or {}
    if _num(budget.get("value")) is not None:
        if _num(cost.get("total")) is None and cost.get("method") == "not_computable":
            unresolved.append("cost_not_computable")
        elif _num(cost.get("total")) is not None and cost["total"] > budget["value"]:
            reasons.append("over_budget")
    tw = (constraints or {}).get("time_window") or {}
    if _num(tw.get("value")) is not None:
        if _num(time.get("total")) is None and time.get("method") == "not_computable":
            unresolved.append("time_not_computable")
        elif _num(time.get("total")) is not None and time["total"] > tw["value"]:
            reasons.append("over_time_window")
    return reasons, unresolved


def rank_candidates(scored, feasible_ids, weights):
    rows = []
    for r in scored:
        if r["plan_id"] not in feasible_ids:
            continue
        s = weighted_score(r["dimension_ratings"], weights)
        rows.append((r["plan_id"], s))
    rows.sort(key=lambda x: (-x[1], x[0]))
    return [p for p, _ in rows]


def sensitivity(scored, feasible_ids, weights, base_rank):
    base_top = base_rank[0] if base_rank else None
    perturbations = []
    flips = []
    for dim in FIVE_DIMS:
        for delta in (-0.05, 0.05):
            w2 = dict(weights)
            w2[dim] = max(0.01, min(0.99, weights.get(dim, 0.0) + delta))
            w2 = normalize_weights(w2)
            ranked = rank_candidates(scored, feasible_ids, w2)
            top = ranked[0] if ranked else None
            perturbations.append({"dim": dim, "delta": delta, "top": top})
            if top != base_top:
                flips.append({"dim": dim, "delta": delta, "top": top})
    return {"stable": len(flips) == 0, "perturbations": perturbations, "top_flips": flips}


# --------------------------------------------------------------------------- #
# Matrix + artifacts
# --------------------------------------------------------------------------- #
def build_matrix(candidates):
    cols = [c["plan_id"] for c in candidates]
    rows = list(SEVEN_FIELDS) + ["total_cost", "total_time", "feasibility", "weighted_score"] + list(FIVE_DIMS)
    cells = {}
    dim_scores = {}
    weighted = {}
    for c in candidates:
        pid = c["plan_id"]
        seven = c.get("seven_fields") or {}
        dim_scores[pid] = dict(c.get("dimension_ratings") or {})
        weighted[pid] = c.get("weighted_score")
        cells[pid] = {}
        for f in SEVEN_FIELDS:
            cells[pid][f] = seven.get(f, "")
        cells[pid]["total_cost"] = (c.get("cost_resolved") or {}).get("total")
        cells[pid]["total_time"] = (c.get("time_resolved") or {}).get("total")
        cells[pid]["feasibility"] = (c.get("feasibility") or {}).get("equipment_available", "")
        cells[pid]["weighted_score"] = c.get("weighted_score")
        for d in FIVE_DIMS:
            cells[pid][d] = dim_scores[pid].get(d, 0)
    return {
        "row_order": rows, "column_order": cols, "cells": cells,
        "dimension_scores": dim_scores, "weighted_scores": weighted,
    }


def write_csv(matrix, path):
    cols = matrix["column_order"]
    header = ["field"] + cols
    rows = []
    for field in matrix["row_order"]:
        row = [field]
        for pid in cols:
            val = matrix["cells"].get(pid, {}).get(field, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            row.append("" if val is None else str(val))
        rows.append(row)
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\ufeff")  # UTF-8 BOM
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def build_svg(matrix, case_id, status, chosen):
    cols = matrix["column_order"]
    n = max(1, len(cols))
    label_w = 160
    col_w = 210
    row_h = 30
    head_h = 52
    width = label_w + n * col_w + 20
    height = head_h + len(matrix["row_order"]) * row_h + 30
    ns = "http://www.w3.org/2000/svg"
    svg = ET.Element("svg", {
        "xmlns": ns, "width": str(width), "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    })
    ET.SubElement(svg, "rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#ffffff",
    })
    title = "experiment-plan-comparison matrix | case %s | status %s" % (case_id, status)
    t = ET.SubElement(svg, "text", {"x": "10", "y": "24", "font-size": "14", "font-family": "sans-serif"})
    t.text = sanitize_xml_text(title)

    def cell(x, y, w, h, text, fill, bold=False, color="#111111"):
        ET.SubElement(svg, "rect", {
            "x": str(x), "y": str(y), "width": str(w), "height": str(h),
            "fill": fill, "stroke": "#cccccc",
        })
        tx = ET.SubElement(svg, "text", {
            "x": str(x + 6), "y": str(y + int(h * 0.62)), "font-size": "12",
            "font-family": "sans-serif", "fill": color,
            "font-weight": "bold" if bold else "normal",
        })
        tx.text = sanitize_xml_text(clip(text, 34))

    # header
    cell(10, 40, label_w, head_h - 8, "field", "#e8eef5", bold=True)
    for i, pid in enumerate(cols):
        cell(10 + label_w + i * col_w, 40, col_w, head_h - 8, pid, "#e8eef5", bold=True)
    # rows
    y = head_h
    for field in matrix["row_order"]:
        cell(10, y, label_w, row_h, field, "#f5f5f5", bold=True)
        for i, pid in enumerate(cols):
            val = matrix["cells"].get(pid, {}).get(field, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            cell(10 + label_w + i * col_w, y, col_w, row_h, "" if val is None else str(val), "#ffffff")
        y += row_h
    return svg


def write_svg(matrix, case_id, status, chosen, path):
    tree = build_svg(matrix, case_id, status, chosen)
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    ET.ElementTree(tree).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _pdf_escape(text):
    return (text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))


def build_text_pdf(title, lines):
    """Minimal valid PDF (ASCII preview). lines: list of (font_size, text)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = []
    y = 800.0
    for size, text in lines:
        safe = _pdf_escape(ascii_fold(text))
        stream.append("BT /F1 %d Tf 50 %.1f Td (%s) Tj ET" % (size, y, safe))
        y -= size * 1.35
    content = ("\n".join(stream)).encode("ascii", "replace")
    objs.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")

    out = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n")
    return out


def write_pdf(matrix, case_id, status, chosen, path):
    lines = [
        (12, "experiment-plan-comparison matrix (ASCII preview)"),
        (9, "case_id: %s   status: %s   chosen: %s" % (case_id, status, chosen or "none")),
        (9, ""),
        (8, "field | " + " | ".join(matrix["column_order"])),
    ]
    for field in matrix["row_order"]:
        vals = []
        for pid in matrix["column_order"]:
            val = matrix["cells"].get(pid, {}).get(field, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            vals.append(clip("" if val is None else str(val), 26))
        lines.append((8, "%s | %s" % (field, " | ".join(vals))))
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    with open(path, "wb") as fh:
        fh.write(build_text_pdf("experiment-plan-comparison", lines))
    return path


def write_md(case_id, status, selection, matrix, confirmations, sources, path):
    cols = matrix["column_order"]
    lines = []
    lines.append("# 实验方案比较报告")
    lines.append("")
    lines.append("- case_id: `%s`" % case_id)
    lines.append("- status: `%s`" % status)
    lines.append("- chosen: `%s`" % (selection.get("chosen") or "none"))
    lines.append("")
    lines.append("## 选择结论")
    lines.append("")
    lines.append((selection.get("rationale") or "（未提供）"))
    lines.append("")
    lines.append("### ranking")
    lines.append("")
    lines.append(", ".join(selection.get("ranking") or []) or "（无）")
    lines.append("")
    lines.append("### rejected")
    lines.append("")
    for r in selection.get("rejected") or []:
        pid = r.get("plan_id", "?")
        reason = r.get("reason", "")
        lines.append("- `%s`: %s" % (pid, reason))
    lines.append("")
    lines.append("## 比较矩阵")
    lines.append("")
    header = ["field"] + cols
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for field in matrix["row_order"]:
        vals = []
        for pid in cols:
            val = matrix["cells"].get(pid, {}).get(field, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            s = "" if val is None else str(val)
            s = s.replace("|", "\\|").replace("\n", " ")
            vals.append(clip(s, 60))
        lines.append("| " + " | ".join([field] + vals) + " |")
    lines.append("")
    lines.append("## 证据来源（sources）")
    lines.append("")
    if sources:
        for s in sources:
            lines.append("- `%s` (%s): %s" % (s.get("locator", "?"), s.get("kind", "?"), clip(s.get("quote", ""), 100)))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 确认项")
    lines.append("")
    if confirmations:
        for c in confirmations:
            lines.append("- [%s] %s — %s" % (c.get("status", "pending"), c.get("item", "?"), clip(c.get("note", ""), 60)))
    else:
        lines.append("（无）")
    lines.append("")
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


# --------------------------------------------------------------------------- #
# inspect command
# --------------------------------------------------------------------------- #
def build_skeleton(case_id, status, contract_keys, note=""):
    result = {
        "case_id": case_id,
        "status": status,
        "method": {"skill": SKILL_NAME, "version": VERSION, "rubric_version": RUBRIC_VERSION},
        "output_contract_keys": contract_keys,
        "sources": [], "uncertainty": [], "change_log": [],
        "pending_human": [], "errors": [], "degradations": [], "limitations": [],
        "runtime": {}, "artifacts": [],
        "rubric_map": RUBRIC,
    }
    for k in contract_keys:
        result[k] = None
    result["change_log"] = [{
        "time": now_iso(), "step": "inspect", "note": "skeleton written first" + (": " + note if note else ""),
    }]
    return result


def cmd_inspect(args):
    input_dir = args.input or DEFAULT_INPUT
    work_dir = args.work or DEFAULT_WORK
    output_dir = args.output or DEFAULT_OUTPUT
    ensure_dir(work_dir)
    ensure_dir(output_dir)

    case_path = find_case_file(input_dir)
    errors = []
    degradations = []

    if case_path is None:
        result = build_skeleton("unknown", "input_error", list(DEFAULT_CONTRACT_KEYS),
                                "no case.json found in %s" % input_dir)
        result["errors"].append({"kind": "missing_input", "detail": "no case.json in %s" % input_dir})
        write_json(os.path.join(output_dir, "result.json"), result)
        return 0

    doc, err = read_json(case_path)
    if err is not None:
        result = build_skeleton(derive_case_id({}, case_path), "input_error",
                                list(DEFAULT_CONTRACT_KEYS), err)
        result["errors"].append({"kind": "bad_case_json", "detail": err, "file": case_path})
        write_json(os.path.join(output_dir, "result.json"), result)
        return 0

    case_id = derive_case_id(doc, case_path)
    contract = doc.get("output_contract")
    contract_keys = list(contract.keys()) if isinstance(contract, dict) and contract else list(DEFAULT_CONTRACT_KEYS)
    raw_candidates = discover_candidates(doc)
    constraints = discover_constraints(doc)

    # material files (excluding the case file itself)
    material_files = [f for f in walk_files(input_dir) if os.path.abspath(f) != os.path.abspath(case_path)]

    # candidate skeletons (raw preserved so nothing is lost)
    candidate_skeletons = []
    for i, raw in enumerate(raw_candidates):
        pid = raw.get("plan_id") or raw.get("id") or ("P%d" % (i + 1))
        candidate_skeletons.append({
            "plan_id": str(pid),
            "name": raw.get("name") or raw.get("title") or raw.get("方案") or "",
            "version": raw.get("version", "v1"),
            "raw": raw,
            "seven_fields": {f: "" for f in SEVEN_FIELDS},
            "dimension_ratings": {d: 0 for d in FIVE_DIMS},
            "feasibility": {"equipment_available": "unknown", "lead_time_ok": "unknown", "notes": ""},
            "risks": [], "evidence": [], "infeasible_reasons": [],
            "cost": {"components": {k: 0 for k in COST_COMPONENTS}, "one_time": [], "currency": "CNY",
                     "note": "", "evidence": "", "method": "computed"},
            "time": {"components": {k: 0 for k in TIME_COMPONENTS}, "unit": "h",
                     "note": "", "evidence": "", "method": "computed"},
            "cost_override": None, "time_override": None,
        })

    case_pack = {
        "case_id": case_id,
        "case_file": os.path.relpath(case_path, input_dir) if case_path.startswith(input_dir) else case_path,
        "task_file": "task.md",
        "output_contract": contract,
        "output_contract_keys": contract_keys,
        "constraints": constraints,
        "candidate_count": len(candidate_skeletons),
        "material_files": [os.path.relpath(f, input_dir) if f.startswith(input_dir) else f for f in material_files],
        "errors": errors,
    }

    analysis_template = {
        "case_id": case_id,
        "research_question": {
            "testable_question": "", "variables": {"independent": [], "dependent": [], "controlled": []},
            "hypothesis": "",
        },
        "comparison_frame": {
            "objectives": [], "hard_constraints": [], "criteria": list(FIVE_DIMS),
            "weights": dict(DEFAULT_WEIGHTS),
        },
        "candidates": candidate_skeletons,
        "selection": {"chosen": "", "ranking": [], "rationale": "", "rejected": [], "sensitivity": {}},
        "confirmations": [], "uncertainty": [], "sources": [], "contract_fills": {},
        "change_log": [], "errors": errors, "degradations": degradations,
        "pending_human": [], "limitations": [],
    }

    status = "insufficient_evidence" if not candidate_skeletons else "partial"
    note = "%d candidate(s) discovered" % len(candidate_skeletons)
    if not candidate_skeletons:
        note += "; no candidates found (recursive discovery)"
    result = build_skeleton(case_id, status, contract_keys, note)
    result["constraints"] = constraints
    result["material_files"] = case_pack["material_files"]
    result["candidate_count"] = len(candidate_skeletons)
    result["errors"].extend(errors)

    write_json(os.path.join(work_dir, "case_pack.json"), case_pack)
    write_json(os.path.join(work_dir, "analysis.json"), analysis_template)
    write_json(os.path.join(output_dir, "result.json"), result)
    return 0


# --------------------------------------------------------------------------- #
# assemble command
# --------------------------------------------------------------------------- #
def build_evidence_ledger(candidates):
    ledger = []
    seen = set()
    for c in candidates:
        pid = c.get("plan_id")
        for ev in c.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            key = (ev.get("locator", ""), ev.get("quote", ""))
            if not key[0] and not key[1]:
                continue
            if key in seen:
                continue
            seen.add(key)
            entry = dict(ev)
            entry["plan_id"] = pid
            ledger.append(entry)
    ledger.sort(key=lambda e: (e.get("locator", ""), e.get("quote", "")))
    return ledger


def cmd_assemble(args):
    input_dir = args.input or DEFAULT_INPUT
    work_dir = args.work or DEFAULT_WORK
    output_dir = args.output or DEFAULT_OUTPUT
    ensure_dir(work_dir)
    ensure_dir(output_dir)

    case_pack, err = read_json(os.path.join(work_dir, "case_pack.json"))
    if case_pack is None:
        case_pack = {"case_id": "unknown", "output_contract": None,
                     "output_contract_keys": list(DEFAULT_CONTRACT_KEYS),
                     "constraints": {}, "material_files": []}
        if err:
            case_pack["errors"] = [{"kind": "missing_case_pack", "detail": err}]
    analysis, aerr = read_json(os.path.join(work_dir, "analysis.json"))
    if analysis is None:
        # fall back to skeleton-level partial; never leave empty artifacts
        analysis = {"case_id": case_pack.get("case_id", "unknown"), "candidates": [],
                    "selection": {}, "confirmations": [], "sources": [], "uncertainty": [],
                    "change_log": [], "errors": [], "degradations": [], "pending_human": [],
                    "limitations": []}
        if aerr:
            analysis.setdefault("errors", []).append({"kind": "missing_analysis", "detail": aerr})

    # carry over the inspect skeleton's change_log so the final record is incremental;
    # keep only the inspect entry to avoid unbounded growth on repeated assemble runs
    skeleton, _ = read_json(os.path.join(output_dir, "result.json"))
    prior_log = []
    if isinstance(skeleton, dict) and isinstance(skeleton.get("change_log"), list):
        prior_log = [e for e in skeleton["change_log"]
                     if isinstance(e, dict) and e.get("step") == "inspect"]
    analysis["change_log"] = prior_log + list(analysis.get("change_log") or [])

    case_id = analysis.get("case_id") or case_pack.get("case_id") or "unknown"
    contract_keys = case_pack.get("output_contract_keys") or list(DEFAULT_CONTRACT_KEYS)
    constraints = case_pack.get("constraints") or discover_constraints(analysis)

    weights = normalize_weights((analysis.get("comparison_frame") or {}).get("weights") or DEFAULT_WEIGHTS)

    processed = []
    for cand in (analysis.get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        pid = str(cand.get("plan_id") or ("P%d" % (len(processed) + 1)))
        cost = resolve_cost(cand)
        time = resolve_time(cand)
        ratings = {d: _num((cand.get("dimension_ratings") or {}).get(d)) or 0 for d in FIVE_DIMS}
        score = weighted_score(ratings, weights)
        reasons, unresolved = evaluate_constraints(cand, cost, time, constraints)
        feasible = len(reasons) == 0
        row = {
            "plan_id": pid,
            "name": cand.get("name", ""),
            "version": cand.get("version", "v1"),
            "seven_fields": cand.get("seven_fields") or {},
            "dimension_ratings": {d: int(ratings[d]) for d in FIVE_DIMS},
            "feasibility": cand.get("feasibility") or {},
            "risks": cand.get("risks") or [],
            "evidence": cand.get("evidence") or [],
            "cost_resolved": cost,
            "time_resolved": time,
            "infeasible_reasons": reasons,
            "unresolved": unresolved,
            "feasible": feasible,
            "weighted_score": score,
        }
        processed.append(row)

    feasible_ids = [r["plan_id"] for r in processed if r["feasible"]]
    all_ids = [r["plan_id"] for r in processed]

    if feasible_ids:
        base_rank = rank_candidates(processed, feasible_ids, weights)
    else:
        base_rank = rank_candidates(processed, all_ids, weights) if processed else []

    chosen = (analysis.get("selection") or {}).get("chosen")
    if chosen not in feasible_ids and feasible_ids:
        chosen = feasible_ids[0]
    elif not feasible_ids:
        chosen = None

    sens = sensitivity(processed, feasible_ids, weights, base_rank)
    chosen_is_top = bool(chosen) and bool(base_rank) and chosen == base_rank[0]

    rejected = []
    for r in processed:
        if r["plan_id"] not in feasible_ids:
            rejected.append({"plan_id": r["plan_id"], "reason": "; ".join(r["infeasible_reasons"]) or "infeasible"})
    rejected.sort(key=lambda x: x["plan_id"])

    unresolved_any = any(r["unresolved"] for r in processed)
    pending_human = list(analysis.get("pending_human") or [])
    if unresolved_any:
        for r in processed:
            for u in r["unresolved"]:
                pending_human.append({"plan_id": r["plan_id"], "item": u,
                                      "note": "cost/time not computable from provided materials"})
    # dedupe pending_human
    deduped = []
    seen_p = set()
    for p in pending_human:
        if not isinstance(p, dict):
            continue
        key = json.dumps(p, sort_keys=True, default=str)
        if key not in seen_p:
            seen_p.add(key)
            deduped.append(p)
    pending_human = deduped

    errors = list(analysis.get("errors") or [])
    degradations = list(analysis.get("degradations") or [])
    limitations = list(analysis.get("limitations") or [])

    status = "ok"
    if case_pack.get("errors"):
        status = "partial"
    if not processed:
        status = "insufficient_evidence"
    elif chosen is None:
        status = "partial"
    elif unresolved_any:
        status = "partial"
    elif any(e.get("severity") == "fatal" for e in errors):
        status = "partial"

    matrix = build_matrix(processed)
    ledger = build_evidence_ledger(processed)

    sources = list(analysis.get("sources") or [])
    if ledger and not sources:
        # promote evidence ledger into sources so sources is never empty when evidence exists
        for ev in ledger:
            sources.append({"locator": ev.get("locator", ""), "quote": ev.get("quote", ""),
                            "kind": ev.get("kind", "evidence")})

    confirmations = list(analysis.get("confirmations") or [])

    selection = {
        "chosen": chosen,
        "chosen_is_top": chosen_is_top,
        "ranking": base_rank,
        "rationale": (analysis.get("selection") or {}).get("rationale", ""),
        "rejected": rejected,
        "sensitivity": sens,
        "decision_rules": [
            "equipment_available == 'no' -> infeasible",
            "lead_time_ok == 'no' -> infeasible",
            "cost.total > budget -> infeasible",
            "time.total > time_window -> infeasible",
            "cost/time method == 'not_computable' -> unresolved + pending_human",
        ],
        "weights": weights,
    }

    rq = analysis.get("research_question") or {}
    cf = analysis.get("comparison_frame") or {}

    result = {
        "case_id": case_id,
        "status": status,
        "method": {"skill": SKILL_NAME, "version": VERSION, "rubric_version": RUBRIC_VERSION},
        "output_contract_keys": contract_keys,
        "research_question": rq,
        "comparison_frame": {
            "objectives": cf.get("objectives", []),
            "hard_constraints": cf.get("hard_constraints", []),
            "criteria": cf.get("criteria", list(FIVE_DIMS)),
            "weights": weights,
        },
        "constraints": constraints,
        "candidates": processed,
        "comparison_matrix": matrix,
        "selection": selection,
        "sources": sources,
        "uncertainty": analysis.get("uncertainty") or [],
        "confirmations": confirmations,
        "rubric_map": RUBRIC,
        "errors": errors,
        "degradations": degradations,
        "pending_human": pending_human,
        "limitations": limitations,
        "change_log": list(analysis.get("change_log") or []),
        "runtime": {"started_at": now_iso()},
        "artifacts": [],
    }

    # fill output_contract keys (原样回填全部键)
    computed = {
        "experimental_plan": {
            "research_question": rq,
            "comparison_frame": result["comparison_frame"],
            "candidates": processed,
            "comparison_matrix": matrix,
        },
        "evidence_and_risks": {
            "sources": sources,
            "risks": [r["risks"] for r in processed],
            "uncertainty": result["uncertainty"],
            "evidence_ledger": ledger,
        },
        "confirmation_items": confirmations,
    }
    contract_fills = analysis.get("contract_fills") or {}
    for k in contract_keys:
        if k in computed:
            result[k] = computed[k]
        else:
            result[k] = contract_fills.get(k, None)

    result["change_log"].append({"time": now_iso(), "step": "assemble", "note": "assembled final result"})

    # artifacts
    base = os.path.join(output_dir, "comparison_matrix")
    write_csv(matrix, base + ".csv")
    write_svg(matrix, case_id, status, chosen, base + ".svg")
    write_pdf(matrix, case_id, status, chosen, base + ".pdf")
    write_md(case_id, status, selection, matrix, confirmations, sources, os.path.join(output_dir, "plan_comparison.md"))
    result["artifacts"] = [
        os.path.join(output_dir, "result.json"),
        base + ".csv", base + ".svg", base + ".pdf",
        os.path.join(output_dir, "plan_comparison.md"),
    ]

    write_json(os.path.join(output_dir, "result.json"), result)
    return 0


# --------------------------------------------------------------------------- #
# validate command
# --------------------------------------------------------------------------- #
def _file_ok(path):
    if not os.path.isfile(path):
        return False, "missing"
    return True, None


def cmd_validate(args):
    target = getattr(args, "result_json", None)
    if not target and len(getattr(args, "rest", []) or []) > 0:
        target = args.rest[0]
    if not target:
        target = os.path.join(args.output or DEFAULT_OUTPUT, "result.json")
    if not os.path.isfile(target):
        print("valid=false")
        print("missing result.json: %s" % target)
        return 1

    doc, err = read_json(target)
    if doc is None:
        print("valid=false")
        print("unreadable/bad json: %s" % (err or target))
        return 1

    issues = []
    if not doc.get("case_id"):
        issues.append("missing case_id")
    if doc.get("status") not in VALID_STATUS:
        issues.append("invalid status: %r" % doc.get("status"))
    for key in ("sources", "uncertainty", "change_log", "pending_human", "errors", "degradations"):
        if key not in doc:
            issues.append("missing key: %s" % key)
    keys = doc.get("output_contract_keys") or list(DEFAULT_CONTRACT_KEYS)
    for k in keys:
        if k not in doc:
            issues.append("missing output_contract field: %s" % k)
    cands = doc.get("candidates") or []
    for c in cands:
        for f in SEVEN_FIELDS:
            if f not in (c.get("seven_fields") or {}):
                issues.append("candidate %s missing seven_field %s" % (c.get("plan_id"), f))
        for d in FIVE_DIMS:
            if d not in (c.get("dimension_ratings") or {}):
                issues.append("candidate %s missing dimension %s" % (c.get("plan_id"), d))
    sel = doc.get("selection") or {}
    for key in ("chosen", "ranking", "rationale", "rejected", "sensitivity"):
        if key not in sel:
            issues.append("selection missing key: %s" % key)
    matrix = doc.get("comparison_matrix") or {}
    if not matrix.get("row_order") or not matrix.get("column_order"):
        issues.append("comparison_matrix empty")
    rubric = doc.get("rubric_map") or {}
    for dim in RUBRIC:
        if dim not in rubric:
            issues.append("rubric_map missing dimension: %s" % dim)

    # artifacts
    outdir = args.output or DEFAULT_OUTPUT
    for name, checks in (
        ("comparison_matrix.csv", ("csv",)),
        ("comparison_matrix.svg", ("svg",)),
        ("comparison_matrix.pdf", ("pdf",)),
        ("plan_comparison.md", ("md",)),
    ):
        path = os.path.join(outdir, name)
        ok, why = _file_ok(path)
        if not ok:
            issues.append("artifact %s: %s" % (name, why))
            continue
        if "svg" in checks:
            try:
                ET.parse(path)
            except Exception as exc:  # noqa: BLE001
                issues.append("svg not parseable: %s" % exc)
        if "pdf" in checks:
            with open(path, "rb") as fh:
                head = fh.read(5)
                fh.seek(-6, os.SEEK_END)
                tail = fh.read(6)
            if not head.startswith(b"%PDF-"):
                issues.append("pdf missing %PDF- header")
            if b"%%EOF" not in tail:
                issues.append("pdf missing %%EOF tail")

    valid = len(issues) == 0
    print("valid=%s" % ("true" if valid else "false"))
    print("status=%s case_id=%s candidates=%d issues=%d" % (
        doc.get("status"), doc.get("case_id"), len(cands), len(issues)))
    for i in issues:
        print("  - %s" % i)
    return 0 if valid else 1


# --------------------------------------------------------------------------- #
# demo command (synthetic example, clearly marked)
# --------------------------------------------------------------------------- #
def _demo_case():
    return {
        "case_id": "demo-example-001",
        "title": "示例：三种纯化路线的比较（非正式案例）",
        "note": "示例输入，仅用于验证脚本可运行；不含真实数据。",
        "budget": 5000,
        "time_window": 120,
        "candidates": [
            {"plan_id": "P1", "name": "示例：柱层析路线", "version": "v1"},
            {"plan_id": "P2", "name": "示例：重结晶路线", "version": "v1"},
            {"plan_id": "P3", "name": "示例：膜过滤路线", "version": "v1"},
        ],
    }


def cmd_demo(args):
    tmp = None
    if args.input and args.output and args.work:
        dinput, dwork, doutput = args.input, args.work, args.output
        for d in (dinput, dwork, doutput):
            os.makedirs(d, exist_ok=True)
    else:
        tmp = tempfile.mkdtemp(prefix="epc-demo-")
        dinput = os.path.join(tmp, "input")
        dwork = os.path.join(tmp, "work")
        doutput = os.path.join(tmp, "output")
        for d in (dinput, dwork, doutput):
            os.makedirs(d, exist_ok=True)
    write_json(os.path.join(dinput, "case.json"), _demo_case())
    with open(os.path.join(dinput, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo task (示例，非正式案例)\n")

    ns_inspect = argparse.Namespace(input=dinput, output=doutput, work=dwork,
                                    result_json=None, rest=[])
    ns_assemble = argparse.Namespace(input=dinput, output=doutput, work=dwork,
                                     result_json=None, rest=[])
    ns_validate = argparse.Namespace(input=dinput, output=doutput, work=dwork,
                                     result_json=os.path.join(doutput, "result.json"), rest=[])
    cmd_inspect(ns_inspect)

    # simulate the agent filling analysis.json (example values, marked 示例)
    analysis, _ = read_json(os.path.join(dwork, "analysis.json"))
    cands = analysis["candidates"]
    demo_vals = [
        ("P1", 4, 2, 3, 4, 4, 800, 16),
        ("P2", 4, 3, 4, 4, 3, 1200, 20),
        ("P3", 3, 4, 4, 3, 3, 1500, 24),
    ]
    for c, (pid, g, r, co, f, e, cost, hours) in zip(cands, demo_vals):
        c["plan_id"] = pid
        c["seven_fields"] = {
            "assumptions": "示例假设：样品纯度目标为 99%。",
            "variables": "示例：自变量=纯化方法；因变量=纯度/收率。",
            "controls": "示例：同批原料、同目标纯度。",
            "steps": "示例：步骤 1 预处理；步骤 2 纯化；步骤 3 检测。",
            "parameters": "示例：温度 25±2℃；流速 1.0 mL/min。",
            "resources": "示例：设备 A 一台；耗材 B。",
            "interpretation_criteria": "示例：纯度≥99% 且收率≥80% 为合格。",
        }
        c["dimension_ratings"] = {"goal_fit": g, "risk": r, "cost": co, "feasibility": f, "evidence": e}
        c["feasibility"] = {"equipment_available": "yes", "lead_time_ok": "yes", "notes": "示例"}
        c["cost"] = {"components": {"machine_hours": cost * 0.5, "consumables": cost * 0.3,
                                    "labor": cost * 0.1, "sample_prep": 50, "scheduling": 30, "rework": 20},
                     "one_time": [], "currency": "CNY", "note": "示例数字", "evidence": "demo", "method": "computed"}
        c["time"] = {"components": {"preparation": 4, "scheduling": 2, "execution": hours, "rework": 2},
                     "unit": "h", "note": "示例数字", "evidence": "demo", "method": "computed"}
        c["evidence"] = [{"claim": "示例：纯化方法影响收率。", "locator": "demo/README.md#L1",
                          "quote": "示例引用，非真实材料。", "kind": "literature"}]
    analysis["research_question"] = {
        "testable_question": "示例：哪种纯化路线在成本与纯度约束下最优？",
        "variables": {"independent": ["纯化方法"], "dependent": ["纯度", "收率"], "controlled": ["原料批次"]},
        "hypothesis": "示例：P2 在成本与可行性上占优。",
    }
    analysis["selection"] = {"chosen": "P2", "ranking": [], "rationale": "示例选择理由。", "rejected": [], "sensitivity": {}}
    analysis["change_log"] = [{"time": now_iso(), "step": "agent", "note": "示例：填入候选方案"}]
    write_json(os.path.join(dwork, "analysis.json"), analysis)

    cmd_assemble(ns_assemble)
    code = cmd_validate(ns_validate)
    if tmp:
        print("demo tmp dir: %s" % tmp)
    else:
        print("demo wrote to input=%s output=%s work=%s" % (dinput, doutput, dwork))
    return code


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="main.py", add_help=False)
    p.add_argument("command", nargs="?", default=None)
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)
    p.add_argument("--help", "-h", action="store_true")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args, rest = parser.parse_known_args(argv)
    args.rest = rest
    if args.help or not args.command:
        print("experiment-plan-comparison v%s" % VERSION)
        print("usage: main.py [--input DIR] [--output DIR] [--work DIR] "
              "{inspect|assemble|validate|demo} [result.json]")
        return 0
    cmd = args.command.lower()
    if cmd == "inspect":
        return cmd_inspect(args)
    if cmd == "assemble":
        return cmd_assemble(args)
    if cmd == "validate":
        return cmd_validate(args)
    if cmd == "demo":
        return cmd_demo(args)
    print("unknown command: %s" % args.command)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        sys.stderr.write("fatal: %s\n" % exc)
        # write a minimal legal skeleton so a failure never leaves empty output
        try:
            out = os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT)
            ensure_dir(out)
            result = build_skeleton("unknown", "input_error", list(DEFAULT_CONTRACT_KEYS),
                                    "fatal exception: %s" % exc)
            result["errors"].append({"kind": "fatal", "detail": str(exc)})
            write_json(os.path.join(out, "result.json"), result)
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)
