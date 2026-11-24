#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-plan-comparison core implementation (standard library only).

Subcommands:
  inspect    scan input dir, write result.json skeleton + case_pack.json
  assemble   merge case_pack.json + analysis.json -> final result.json + artifacts
  validate   check a result.json against the output contract
  demo       run an end-to-end example (explicitly labelled 示例)
"""
import argparse
import csv
import html
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
ENV_INPUT = "SKILL_INPUT"
ENV_OUTPUT = "SKILL_OUTPUT"
ENV_WORK = "SKILL_WORK"

MAX_FILE_BYTES = 2 * 1024 * 1024
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
SEVEN_FIELDS = ("assumptions", "variables", "controls", "steps",
                "parameters", "resources", "interpretation_criteria")
FIVE_DIMS = ("goal_fit", "risk", "cost", "feasibility", "evidence")
DEFAULT_WEIGHTS = {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
                   "cost": 0.15, "risk": 0.15}
COST_PARTS = ("equipment_time", "consumables", "labor", "sample_prep",
              "scheduling", "rework")
TIME_PARTS = ("prep", "scheduling", "execution", "rework")
DEFAULT_CONTRACT_KEYS = ("experimental_plan", "evidence_and_risks",
                         "confirmation_items")
MATERIAL_EXTS = (".pdf", ".txt", ".md", ".csv", ".json", ".tsv", ".tex", ".rtf")

FIELD_KEYS = {
    "assumptions": ("assumptions", "assumption"),
    "variables": ("variables", "variable"),
    "controls": ("controls", "control", "control_group"),
    "steps": ("steps", "procedure", "protocol", "methods"),
    "parameters": ("parameters", "parameter", "params"),
    "resources": ("resources", "equipment", "materials"),
    "interpretation_criteria": ("interpretation_criteria", "interpretation",
                                "readout", "success_criteria"),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path, limit=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "unreadable"
    if size > limit:
        return None, "skipped_over_2MB"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except (UnicodeDecodeError, ValueError):
            continue
    return None, "bad_encoding"


def load_json(path):
    text, err = read_text(path)
    if text is None:
        return None, "unreadable:" + str(err)
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as e:
        return None, "bad_json:" + str(e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
    return path


def write_json_safe(path, obj, log):
    try:
        write_json(path, obj)
    except Exception as e:  # pragma: no cover - defensive
        minimal = {"case_id": (obj or {}).get("case_id") if isinstance(obj, dict) else "",
                   "status": "input_error", "errors": [str(e)]}
        try:
            write_json(path, minimal)
        except Exception:
            pass
        log.append("write_failed:" + str(e))


# ---------------------------------------------------------------- discovery

def discover_input(input_dir):
    info = {"exists": False, "case": None, "case_error": None, "task_md": None,
            "materials": [], "contract_keys": None, "case_id": None}
    if not os.path.isdir(input_dir):
        return info
    info["exists"] = True
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        data, err = load_json(case_path)
        if data is None:
            info["case_error"] = err
        else:
            info["case"] = data
            info["case_id"] = str(data.get("case_id") or data.get("id")
                                  or data.get("challenge_id") or "")
            if isinstance(data.get("output_contract"), dict):
                info["contract_keys"] = sorted(data["output_contract"].keys())
    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        info["task_md"], _ = read_text(task_path)
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            if fn in ("case.json", "task.md", "result.json"):
                continue
            p = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext in MATERIAL_EXTS:
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = -1
                rel = os.path.relpath(p, input_dir).replace("\\", "/")
                info["materials"].append({"path": rel, "size": size,
                                          "kind": ext.lstrip(".") or "file"})
    return info


def _looks_like_plan(d):
    if not isinstance(d, dict):
        return False
    if any(k in d for k in ("plan_id", "candidate_id", "scheme_id")):
        return True
    if "id" in d and any(k in d for k in ("steps", "description", "procedure",
                                          "protocol", "methods")):
        return True
    return False


def discover_candidates(obj, acc=None, path="$"):
    if acc is None:
        acc = []
    plan_keys = ("candidates", "plans", "schemes", "候选方案", "方案",
                 "experimental_plans")
    if isinstance(obj, dict):
        for k in plan_keys:
            v = obj.get(k)
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if _looks_like_plan(item):
                        acc.append({"found_at": "%s.%s[%d]" % (path, k, i),
                                    "data": item})
        if _looks_like_plan(obj):
            acc.append({"found_at": path, "data": obj})
        for k, v in obj.items():
            if k in plan_keys:
                continue
            discover_candidates(v, acc, path + "." + str(k))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            discover_candidates(item, acc, path + "[%d]" % i)
    return acc


def _extract_budget(obj):
    hints = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kl = k.lower()
                if ("budget" in kl or "预算" in k or "总成本" in k) \
                        and isinstance(v, (int, float)):
                    hints.setdefault(k, v)
                if ("time_window" in kl or "deadline" in kl or "工期" in k
                        or "时间窗口" in k):
                    hints.setdefault(k, v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(obj)
    return hints


# ---------------------------------------------------------------- skeleton

def make_skeleton(case_id, contract_keys, status, note):
    keys = list(contract_keys) if contract_keys else list(DEFAULT_CONTRACT_KEYS)
    result = {
        "case_id": case_id or "",
        "status": status,
        "task": "experimental_plan_comparison",
        "research_question": {"testable_question": None,
                              "variables": {"independent": [], "dependent": [],
                                            "controlled": []},
                              "hypothesis": None},
        "comparison_frame": {"objectives": [], "hard_constraints": [],
                             "criteria": list(FIVE_DIMS)},
        "candidates": [],
        "comparison_matrix": {"schemes": [], "rows": []},
        "selection": {"chosen": None, "ranking": [], "rationale": None,
                      "rejected": [], "sensitivity": {}},
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "confirmations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"created_at": now_iso(), "budget_seconds": 900,
                    "budget_requests": 64, "note": note},
        "artifacts": [],
    }
    for k in keys:
        if k not in result:
            result[k] = None
    result["experimental_plan"] = {f: None for f in SEVEN_FIELDS}
    result["evidence_and_risks"] = {"evidence": [], "risks": []}
    result["confirmation_items"] = []
    result["change_log"].append({"time": now_iso(), "step": "inspect",
                                 "note": note})
    return result


def _plan_id(d, i):
    pid = d.get("plan_id") or d.get("candidate_id") or d.get("scheme_id") \
        or d.get("id")
    if pid is None or pid == "":
        return "P%d" % (i + 1)
    return str(pid)


def _clamp_rating(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if 0.0 <= f <= 5.0 else None
    return None


def _field_from_plan(d, field):
    for k in FIELD_KEYS.get(field, ()):
        if d.get(k) not in (None, "", []):
            return d[k]
    return None


# ---------------------------------------------------------------- arithmetic

def weighted_score(ratings, weights):
    ratings = ratings or {}
    score = 0.0
    total_w = 0.0
    for dim, w in weights.items():
        r = ratings.get(dim)
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            score += float(r) * float(w)
            total_w += float(w)
    if total_w <= 0:
        return None
    return round(score / total_w, 3)


def _rank(plans, weights):
    scored = []
    for p in plans:
        s = weighted_score(p.get("dimension_ratings") or {}, weights)
        scored.append((p.get("plan_id"), s))
    scored.sort(key=lambda t: (t[1] is None, -(t[1] if t[1] is not None else 0.0),
                               str(t[0])))
    return [t[0] for t in scored]


def _compute_cost(d):
    result = {"total": None, "currency": "CNY", "method": "not_computable",
              "lines": [], "note": ""}
    override = d.get("cost_override")
    if isinstance(override, dict) and isinstance(override.get("total"), (int, float)):
        result["total"] = float(override["total"])
        result["currency"] = override.get("currency") or "CNY"
        result["method"] = "override"
        result["lines"] = override.get("lines") or []
        result["note"] = override.get("note") or ""
        return result
    c = d.get("cost")
    if isinstance(c, dict):
        result["currency"] = c.get("currency") or "CNY"
        result["lines"] = c.get("lines") or []
        result["note"] = c.get("note") or ""
        if isinstance(c.get("total"), (int, float)):
            result["total"] = float(c["total"])
            result["method"] = "given_total"
            return result
        total = 0.0
        any_amt = False
        for ln in result["lines"]:
            if isinstance(ln, dict) and isinstance(ln.get("amount"), (int, float)) \
                    and not isinstance(ln.get("amount"), bool):
                total += float(ln["amount"])
                any_amt = True
        if any_amt:
            result["total"] = round(total, 2)
            result["method"] = "line_sum"
    return result


def _compute_time(d):
    result = {"total": None, "unit": "h", "method": "not_computable",
              "parts": {}, "note": ""}
    override = d.get("time_override")
    if isinstance(override, dict) and isinstance(override.get("total"), (int, float)):
        result["total"] = float(override["total"])
        result["unit"] = override.get("unit") or "h"
        result["method"] = "override"
        result["parts"] = override.get("parts") or {}
        result["note"] = override.get("note") or ""
        return result
    t = d.get("time")
    if isinstance(t, dict):
        result["unit"] = t.get("unit") or "h"
        result["parts"] = t.get("parts") or {}
        result["note"] = t.get("note") or ""
        if isinstance(t.get("total"), (int, float)):
            result["total"] = float(t["total"])
            result["method"] = "given_total"
            return result
        parts = result["parts"]
        total = 0.0
        any_num = False
        for k in TIME_PARTS:
            v = parts.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += float(v)
                any_num = True
        if any_num:
            result["total"] = round(total, 2)
            result["method"] = "parts_sum"
    return result


def _evaluate_thresholds(feasibility, cost, time, analysis):
    reasons = []
    hits = []
    hc = (analysis.get("comparison_frame") or {}).get("hard_constraints") or []
    for c in hc:
        if not isinstance(c, dict):
            continue
        field = str(c.get("field") or c.get("constraint") or "").lower()
        op = str(c.get("op") or "max").lower()
        val = c.get("value")
        cost_total = cost.get("total")
        time_total = time.get("total")
        if field in ("cost", "total_cost", "budget", "预算", "总成本") \
                and isinstance(val, (int, float)) and cost_total is not None:
            if op in ("max", "<=", "le") and cost_total > float(val):
                reasons.append("cost %.2f exceeds %.2f" % (cost_total, float(val)))
                hits.append({"constraint": c, "actual": cost_total})
        if field in ("time", "total_time", "duration", "window", "工期", "时间窗口") \
                and isinstance(val, (int, float)) and time_total is not None:
            if op in ("max", "<=", "le") and time_total > float(val):
                reasons.append("time %.1f exceeds %.1f" % (time_total, float(val)))
                hits.append({"constraint": c, "actual": time_total})
        if field in ("equipment_available", "equipment") and c.get("required") \
                and feasibility.get("equipment_available") == "no":
            reasons.append("required equipment unavailable")
            hits.append({"constraint": c, "actual": "no"})
    return reasons, hits


# ---------------------------------------------------------------- evidence

def verify_quote(locator, quote, input_dir, work_dir):
    if not quote or not locator:
        return False, "empty_quote_or_locator"
    norm = lambda s: re.sub(r"\s+", "", s or "")
    q = norm(quote)
    if not q:
        return False, "empty_quote"
    path_part = (locator or "").split("#")[0].strip()
    candidates = []
    if path_part:
        stripped = re.sub(r"^(materials/|input/|/input/|/app/input/)", "",
                          path_part)
        candidates = [os.path.join(input_dir, path_part),
                      os.path.join(work_dir, path_part),
                      os.path.join(input_dir, stripped),
                      os.path.join(work_dir, stripped)]
    for p in candidates:
        if not os.path.isfile(p):
            continue
        text, _ = read_text(p)
        if text is None:
            continue
        if q in norm(text):
            return True, "verbatim"
    return False, "quote_not_found_in_located_file"


def _build_evidence(ev, input_dir, work_dir):
    out = []
    if not isinstance(ev, list):
        return out
    for e in ev:
        if not isinstance(e, dict):
            continue
        quote = e.get("quote") or ""
        locator = e.get("locator") or ""
        verified, why = verify_quote(locator, quote, input_dir, work_dir)
        item = dict(e)
        item["quote_verified"] = verified
        item["quote_status"] = why
        item.setdefault("kind", "literature")
        item.setdefault("value_kind", "not_reported")
        item.setdefault("strength",
                        "confirmed" if verified else "not_reported")
        out.append(item)
    return out


# ---------------------------------------------------------------- plan build

def build_plan(d, i, input_dir, work_dir, analysis):
    pid = _plan_id(d, i)
    name = d.get("name") or d.get("title") or pid
    version = str(d.get("version") or d.get("plan_version") or "v?")
    sf = d.get("seven_fields") or d.get("experimental_plan") or {}
    if not isinstance(sf, dict):
        sf = {}
    seven = {}
    for f in SEVEN_FIELDS:
        seven[f] = sf.get(f) if sf.get(f) not in (None, "", []) \
            else _field_from_plan(d, f)
    ratings = {}
    dr = d.get("dimension_ratings") or d.get("ratings") or {}
    for dim in FIVE_DIMS:
        ratings[dim] = _clamp_rating(dr.get(dim)) if isinstance(dr, dict) else None
    feasibility = d.get("feasibility") if isinstance(d.get("feasibility"), dict) else {}
    risks = d.get("risks") if isinstance(d.get("risks"), list) else []
    evidence = _build_evidence(d.get("evidence"), input_dir, work_dir)
    cost = _compute_cost(d)
    time = _compute_time(d)
    reasons, hits = _evaluate_thresholds(feasibility, cost, time, analysis)
    reasons = d.get("infeasible_reasons") if isinstance(d.get("infeasible_reasons"), list) \
        else reasons
    uncertainty = d.get("uncertainty") if isinstance(d.get("uncertainty"), list) else []
    for e in evidence:
        if not e.get("quote_verified"):
            uncertainty.append({"item": "unverified evidence",
                                "locator": e.get("locator"),
                                "reason": e.get("quote_status")})
    for dim in FIVE_DIMS:
        if ratings[dim] is None:
            uncertainty.append({"item": "missing rating", "dimension": dim,
                                "reason": "not_assessed"})
    return {
        "plan_id": pid, "name": name, "version": version,
        "seven_fields": seven, "dimension_ratings": ratings,
        "feasibility": feasibility, "risks": risks, "evidence": evidence,
        "uncertainty": uncertainty,
        "cost": cost, "time": time,
        "infeasible_reasons": reasons,
        "threshold_hits": hits,
    }


def _reject_reason(p):
    if p.get("infeasible_reasons"):
        return "; ".join(p["infeasible_reasons"])
    return "ranked below chosen plan on weighted score"


def _sensitivity(plans, weights, chosen):
    base = _rank(plans, weights)
    out = []
    for dim in FIVE_DIMS:
        for delta in (0.05, -0.05):
            w2 = dict(weights)
            w2[dim] = max(0.0, round(w2[dim] + delta, 4))
            s = sum(w2.values()) or 1.0
            w2 = {k: round(v / s, 4) for k, v in w2.items()}
            r2 = _rank(plans, w2)
            out.append({"dimension": dim, "delta": delta, "ranking": r2,
                        "chosen_unchanged": bool(r2 and r2[0] == chosen)})
    return {"base_ranking": base, "perturbations": out,
            "stable": bool(out) and all(x["chosen_unchanged"] for x in out)}


# ---------------------------------------------------------------- artifacts

def _score_color(s):
    if s is None:
        return "#cccccc"
    t = max(0.0, min(5.0, float(s))) / 5.0
    r = int(220 - 150 * t)
    g = int(60 + 130 * t)
    b = 60
    return "#%02x%02x%02x" % (r, g, b)


def write_csv(path, plans, weights):
    header = ["plan_id", "name", "version", "infeasible"] + list(FIVE_DIMS) \
        + ["weighted_score", "cost_total", "cost_currency",
           "time_total", "time_unit"]
    rows = []
    for p in plans:
        ws = weighted_score(p.get("dimension_ratings") or {}, weights)
        c = p.get("cost") or {}
        t = p.get("time") or {}
        row = [p.get("plan_id"), p.get("name"), p.get("version"),
               "yes" if p.get("infeasible_reasons") else "no"]
        row += ["" if p.get("dimension_ratings", {}).get(dim) is None
                else p["dimension_ratings"][dim] for dim in FIVE_DIMS]
        row += ["" if ws is None else ws,
                "" if c.get("total") is None else c["total"],
                c.get("currency", ""),
                "" if t.get("total") is None else t["total"],
                t.get("unit", "")]
        rows.append(row)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_svg(path, plans, weights):
    label_w = 150
    col_w = 82
    row_h = 40
    header_h = 40
    ncols = len(FIVE_DIMS)
    nrows = len(plans)
    width = label_w + ncols * col_w + 20
    height = header_h + nrows * row_h + 30
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    parts.append('<text x="10" y="24" font-family="sans-serif" font-size="14" '
                 'fill="#111">comparison matrix (5-dim, 0-5)</text>')
    x = label_w
    for j, dim in enumerate(FIVE_DIMS):
        parts.append('<text x="%d" y="%d" font-family="sans-serif" font-size="12" '
                     'fill="#111" text-anchor="middle">%s</text>'
                     % (x + col_w / 2, header_h - 10, html.escape(dim)))
        x += col_w
    y = header_h
    for p in plans:
        ws = weighted_score(p.get("dimension_ratings") or {}, weights)
        parts.append('<text x="10" y="%d" font-family="sans-serif" font-size="11" '
                     'fill="#111">%s</text>'
                     % (y + 22, html.escape("%s %s" % (p.get("plan_id"), p.get("name") or ""))))
        x = label_w
        for dim in FIVE_DIMS:
            v = p.get("dimension_ratings", {}).get(dim)
            col = _score_color(v)
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                         'stroke="#ccc"/>' % (x, y, col_w, row_h - 6, col))
            txt = "" if v is None else str(v)
            parts.append('<text x="%d" y="%d" font-family="sans-serif" '
                         'font-size="12" fill="#111" text-anchor="middle">%s</text>'
                         % (x + col_w / 2, y + 20, html.escape(txt)))
            x += col_w
        flag = "INFEASIBLE" if p.get("infeasible_reasons") else (
            "score=%s" % ("" if ws is None else ws))
        parts.append('<text x="%d" y="%d" font-family="sans-serif" font-size="10" '
                     'fill="#800">%s</text>'
                     % (label_w, y + 32, html.escape(flag)))
        y += row_h
    parts.append('</svg>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def _pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_minimal_pdf(path, text):
    text = text.encode("ascii", "replace").decode("ascii")
    lines = text.split("\n") or [""]
    content_lines = []
    for i, ln in enumerate(lines[:60]):
        y = 750 - i * 14
        content_lines.append("BT /F1 9 Tf 50 %d Td (%s) Tj ET"
                             % (y, _pdf_escape(ln)))
    stream = "\n".join(content_lines).encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray()
    out += b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += (str(i) + " 0 obj\n").encode("ascii")
        out += body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += ("xref\n0 %d\n" % n).encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (n, xref_pos)).encode("ascii")
    with open(path, "wb") as f:
        f.write(out)


def write_pdf(path, plans, chosen):
    lines = ["sci-plan-comparison matrix (ASCII rendering; see SVG/MD for full text)"]
    for p in plans:
        r = p.get("dimension_ratings") or {}
        score = " ".join("%s=%s" % (d, ("" if r.get(d) is None else r[d]))
                         for d in FIVE_DIMS)
        flag = "INFEASIBLE" if p.get("infeasible_reasons") else "OK"
        lines.append("%s [%s] %s" % (p.get("plan_id"), flag, score))
    lines.append("chosen: %s" % (chosen or "(none)"))
    _write_minimal_pdf(path, "\n".join(lines))


def write_md(path, plans, weights, chosen, status, limitations, uncertainty):
    L = []
    L.append("# 实验方案比较与选择（运行产物，可编辑摘要）")
    L.append("")
    L.append("- status: `%s`；完整字段见 `result.json`。" % status)
    L.append("")
    L.append("## 五维评分矩阵（0–5，5 最优）")
    L.append("")
    header = "| plan_id | " + " | ".join(FIVE_DIMS) + " | weighted | cost_total | time_total | infeasible |"
    L.append(header)
    L.append("|" + "---|" * (len(FIVE_DIMS) + 6))
    for p in plans:
        r = p.get("dimension_ratings") or {}
        ws = weighted_score(r, weights)
        c = p.get("cost") or {}
        t = p.get("time") or {}
        cells = [p.get("plan_id") or ""]
        cells += ["" if r.get(d) is None else str(r[d]) for d in FIVE_DIMS]
        cells += ["" if ws is None else str(ws)]
        cells += ["" if c.get("total") is None else "%s %s" % (c["total"], c.get("currency", ""))]
        cells += ["" if t.get("total") is None else "%s %s" % (t["total"], t.get("unit", ""))]
        cells += ["yes" if p.get("infeasible_reasons") else "no"]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append("## 选择结论")
    L.append("")
    L.append("- chosen: `%s`" % (chosen or "(none)"))
    L.append("")
    if limitations:
        L.append("## 局限（limitations）")
        L.append("")
        for li in limitations[:20]:
            if isinstance(li, dict):
                L.append("- %s：%s（影响：%s；缓解：%s）"
                         % (li.get("scope", ""), li.get("cause", ""),
                            li.get("impact", ""), li.get("mitigation", "")))
        L.append("")
    if uncertainty:
        L.append("## 不确定性（uncertainty）")
        L.append("")
        for u in uncertainty[:20]:
            if isinstance(u, dict):
                L.append("- %s" % (u.get("item") or u.get("reason") or str(u)))
        L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------- commands

def cmd_inspect(args):
    input_dir = args.input or os.environ.get(ENV_INPUT) or DEFAULT_INPUT
    output_dir = args.output or os.environ.get(ENV_OUTPUT) or DEFAULT_OUTPUT
    work_dir = args.work or os.environ.get(ENV_WORK) or DEFAULT_WORK
    for d in (output_dir, work_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    info = discover_input(input_dir)
    errors, degradations = [], []
    case = info.get("case")
    candidates = discover_candidates(case) if isinstance(case, dict) else []
    if not info.get("exists"):
        status = "input_error"
        note = "input directory does not exist"
        errors.append(note)
    elif case is None:
        status = "input_error"
        note = "case.json missing or unreadable"
        errors.append("case.json: " + str(info.get("case_error")))
    elif not candidates:
        status = "insufficient_evidence"
        note = "no candidate plans discovered in case.json; cannot compare"
        degradations.append(note)
    else:
        status = "partial"
        note = "skeleton written; run assemble after authoring analysis.json"
    case_id = info.get("case_id") or ""
    skeleton = make_skeleton(case_id, info.get("contract_keys"), status, note)
    skeleton["errors"].extend(errors)
    skeleton["degradations"].extend(degradations)
    for i, c in enumerate(candidates):
        d = c.get("data") or {}
        pid = _plan_id(d, i)
        skeleton["candidates"].append({
            "plan_id": pid,
            "name": d.get("name") or d.get("title") or pid,
            "version": str(d.get("version") or "v?"),
            "found_at": c.get("found_at"),
            "seven_fields": {f: None for f in SEVEN_FIELDS},
            "dimension_ratings": {d_: None for d_ in FIVE_DIMS},
            "feasibility": {},
            "risks": [], "evidence": [], "uncertainty": [],
            "infeasible_reasons": [],
        })
    result_path = os.path.join(output_dir, "result.json")
    write_json_safe(result_path, skeleton, degradations)
    pack = {
        "case_id": case_id,
        "input_dir": input_dir,
        "contract_keys": info.get("contract_keys"),
        "materials": info.get("materials"),
        "candidate_plan_ids": [c["plan_id"] for c in skeleton["candidates"]],
        "candidate_count": len(candidates),
        "candidate_dump": candidates,
        "budget_hint": _extract_budget(case) if isinstance(case, dict) else {},
        "created_at": now_iso(),
        "errors": errors,
    }
    write_json_safe(os.path.join(work_dir, "case_pack.json"), pack, degradations)
    print(json.dumps({"status": status, "case_id": case_id,
                      "materials": len(info.get("materials")),
                      "candidates": len(candidates),
                      "result": result_path}, ensure_ascii=False))


def cmd_assemble(args):
    input_dir = args.input or os.environ.get(ENV_INPUT) or DEFAULT_INPUT
    output_dir = args.output or os.environ.get(ENV_OUTPUT) or DEFAULT_OUTPUT
    work_dir = args.work or os.environ.get(ENV_WORK) or DEFAULT_WORK
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    pack, _ = load_json(os.path.join(work_dir, "case_pack.json"))
    if not isinstance(pack, dict):
        pack = {}
    analysis, aerr = load_json(os.path.join(work_dir, "analysis.json"))
    if not isinstance(analysis, dict):
        analysis = {}
    degradations = []
    case_id = str(analysis.get("case_id") or pack.get("case_id") or "")
    contract_keys = pack.get("contract_keys")
    if not contract_keys and isinstance(analysis.get("output_contract"), dict):
        contract_keys = sorted(analysis["output_contract"].keys())
    result = make_skeleton(case_id, contract_keys, "partial", "assembled")

    rq = analysis.get("research_question") or {}
    result["research_question"] = {
        "testable_question": rq.get("testable_question"),
        "variables": rq.get("variables") or {"independent": [], "dependent": [],
                                             "controlled": []},
        "hypothesis": rq.get("hypothesis"),
    }
    cf = analysis.get("comparison_frame") or {}
    result["comparison_frame"] = {
        "objectives": cf.get("objectives") or [],
        "hard_constraints": cf.get("hard_constraints") or [],
        "criteria": cf.get("criteria") or list(FIVE_DIMS),
    }
    weights = dict(DEFAULT_WEIGHTS)
    aw = analysis.get("weights") or {}
    for k in FIVE_DIMS:
        if isinstance(aw.get(k), (int, float)) and not isinstance(aw.get(k), bool):
            weights[k] = float(aw[k])
    sw = sum(weights.values()) or 1.0
    weights = {k: round(v / sw, 4) for k, v in weights.items()}
    result["weights"] = weights

    src = analysis.get("candidates")
    if not isinstance(src, list) or not src:
        src = pack.get("candidate_dump") or []
    plans = []
    for i, c in enumerate(src):
        if isinstance(c, dict) and isinstance(c.get("data"), dict) \
                and _looks_like_plan(c["data"]):
            d = c["data"]
        else:
            d = c if isinstance(c, dict) else {}
        plans.append(build_plan(d, i, input_dir, work_dir, analysis))
    plans.sort(key=lambda p: str(p.get("plan_id") or ""))

    feasible_ids = [p["plan_id"] for p in plans if not p.get("infeasible_reasons")]
    computed_top = _rank(plans, weights)
    sel = analysis.get("selection") if isinstance(analysis.get("selection"), dict) else {}
    ranking = sel.get("ranking") if isinstance(sel.get("ranking"), list) else computed_top
    chosen = sel.get("chosen")
    if chosen not in [p["plan_id"] for p in plans]:
        chosen = None
        for pid in computed_top:
            if pid in feasible_ids:
                chosen = pid
                break
    rejected = sel.get("rejected") if isinstance(sel.get("rejected"), list) else []
    if not rejected:
        for p in plans:
            if p["plan_id"] != chosen:
                rejected.append({"plan_id": p["plan_id"], "reason": _reject_reason(p)})
    rationale = sel.get("rationale")
    if not rationale and chosen:
        rationale = ("chosen plan %s is the top-ranked feasible plan "
                     "(weighted score, hard thresholds, and +/-0.05 weight "
                     "sensitivity applied)" % chosen)
    sensitivity = _sensitivity(plans, weights, chosen)
    result["selection"] = {
        "chosen": chosen, "ranking": ranking, "rationale": rationale,
        "rejected": rejected, "sensitivity": sensitivity, "weights": weights,
        "method": "weighted_score_with_hard_thresholds_and_sensitivity",
    }

    # contract fields
    chosen_plan = next((p for p in plans if p["plan_id"] == chosen), None)
    ep = dict((chosen_plan or {}).get("seven_fields") or {})
    for f in SEVEN_FIELDS:
        ep.setdefault(f, None)
    ep["plan_id"] = chosen
    ep["plan_name"] = (chosen_plan or {}).get("name")
    result["experimental_plan"] = ep
    result["evidence_and_risks"] = {
        "evidence": (chosen_plan or {}).get("evidence", []),
        "risks": (chosen_plan or {}).get("risks", []),
    }
    result["confirmation_items"] = analysis.get("confirmations") \
        if isinstance(analysis.get("confirmations"), list) else []

    # comparison matrix
    schemes = [p["plan_id"] for p in plans]
    rows = []
    for dim in FIVE_DIMS:
        row = {"dimension": dim}
        for p in plans:
            row[p["plan_id"]] = p.get("dimension_ratings", {}).get(dim)
        rows.append(row)
    result["comparison_matrix"] = {
        "schemes": schemes, "rows": rows,
        "cost_row": {p["plan_id"]: (p.get("cost") or {}).get("total") for p in plans},
        "time_row": {p["plan_id"]: (p.get("time") or {}).get("total") for p in plans},
        "weighted_score_row": {p["plan_id"]: weighted_score(
            p.get("dimension_ratings") or {}, weights) for p in plans},
    }

    # sources from evidence
    result["sources"] = analysis.get("sources") if isinstance(analysis.get("sources"), list) else []
    seen = set()
    for p in plans:
        for e in p.get("evidence", []):
            loc = e.get("locator")
            if loc and loc not in seen:
                seen.add(loc)
                result["sources"].append({"locator": loc, "quote": e.get("quote"),
                                          "kind": e.get("kind"),
                                          "quote_verified": e.get("quote_verified")})

    # uncertainty / limitations / pending_human
    result["uncertainty"] = analysis.get("uncertainty") if isinstance(analysis.get("uncertainty"), list) else []
    for p in plans:
        for u in p.get("uncertainty", []):
            if isinstance(u, dict):
                result["uncertainty"].append(dict(u, plan_id=p["plan_id"]))
    result["limitations"] = analysis.get("limitations") if isinstance(analysis.get("limitations"), list) else []
    if result["status"] != "ok":
        result["limitations"].append({"scope": "result", "cause": "status=%s" % result["status"],
                                      "impact": "conclusions are provisional",
                                      "mitigation": "resolve pending_human items before execution"})
    for p in plans:
        if (p.get("cost") or {}).get("method") == "not_computable":
            result["limitations"].append({"plan_id": p["plan_id"], "scope": "cost",
                                          "cause": "no numeric cost evidence",
                                          "impact": "budget feasibility not verifiable",
                                          "mitigation": "obtain quote/manual and set cost_override"})
    result["pending_human"] = analysis.get("pending_human") if isinstance(analysis.get("pending_human"), list) else []
    for p in plans:
        if not p.get("seven_fields") or any(p["seven_fields"].get(f) in (None, "") for f in SEVEN_FIELDS):
            result["pending_human"].append({"item": "seven_fields incomplete",
                                            "plan_id": p["plan_id"]})
    result["confirmations"] = result["confirmation_items"]

    # status decision
    n = len(plans)
    if n == 0:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append({"item": "no candidate plans available",
                                        "reason": "case_pack/analysis contain no candidates"})
    elif all(p.get("infeasible_reasons") for p in plans):
        result["status"] = "partial"
        result["selection"]["chosen"] = None
        result["pending_human"].append({"item": "all candidates infeasible",
                                        "reason": "every plan violates a hard threshold"})
    elif not analysis:
        result["status"] = "partial"
        result["pending_human"].append({"item": "analysis.json missing/empty",
                                        "reason": "seven fields and ratings not authored"})
    else:
        unverified = [e for p in plans for e in p.get("evidence", [])
                      if not e.get("quote_verified")]
        missing_ratings = [p["plan_id"] for p in plans
                           if any(p["dimension_ratings"].get(dim) is None for dim in FIVE_DIMS)]
        missing_fields = [p["plan_id"] for p in plans
                          if any(p["seven_fields"].get(f) in (None, "") for f in SEVEN_FIELDS)]
        if unverified or missing_ratings or missing_fields:
            result["status"] = "partial"
        else:
            result["status"] = "ok"

    result["candidates"] = plans
    result["errors"] = analysis.get("errors") if isinstance(analysis.get("errors"), list) else []
    result["degradations"] = degradations + (analysis.get("degradations") if isinstance(analysis.get("degradations"), list) else [])
    result["runtime"].update({"assembled_at": now_iso(),
                              "analysis_error": aerr if analysis is None or aerr else None,
                              "candidate_count": n})
    result["change_log"].append({"time": now_iso(), "step": "assemble",
                                 "note": "merged case_pack + analysis; status=%s" % result["status"]})

    # artifacts
    paths = {
        "csv": os.path.join(output_dir, "comparison_matrix.csv"),
        "svg": os.path.join(output_dir, "comparison_matrix.svg"),
        "md": os.path.join(output_dir, "plan_comparison.md"),
        "pdf": os.path.join(output_dir, "comparison_matrix.pdf"),
    }
    write_csv(paths["csv"], plans, weights)
    write_svg(paths["svg"], plans, weights)
    write_md(paths["md"], plans, weights, chosen, result["status"],
             result["limitations"], result["uncertainty"])
    write_pdf(paths["pdf"], plans, chosen)
    result["artifacts"] = [{"name": os.path.basename(v), "path": os.path.basename(v)}
                           for v in paths.values()]
    write_json_safe(os.path.join(output_dir, "result.json"), result, degradations)
    print(json.dumps({"status": result["status"], "case_id": case_id,
                      "candidates": n, "chosen": chosen,
                      "result": os.path.join(output_dir, "result.json")},
                     ensure_ascii=False))


def cmd_validate(args):
    result_path = args.result
    if not result_path:
        output_dir = args.output or os.environ.get(ENV_OUTPUT) or DEFAULT_OUTPUT
        result_path = os.path.join(output_dir, "result.json")
    issues, warnings = [], []
    result, err = load_json(result_path)
    if result is None:
        print(json.dumps({"valid": False, "path": result_path,
                          "errors": ["cannot_load_json: " + str(err)],
                          "warnings": []}, ensure_ascii=False))
        return
    _validate_result(result, result_path, issues, warnings)
    print(json.dumps({"valid": len(issues) == 0, "path": result_path,
                      "errors": issues, "warnings": warnings}, ensure_ascii=False))


def _validate_result(result, result_path, issues, warnings):
    if not isinstance(result, dict):
        issues.append("result_not_object")
        return
    if not result.get("case_id"):
        issues.append("missing_case_id")
    if result.get("status") not in STATUSES:
        issues.append("invalid_status")
    for k in ("sources", "uncertainty", "change_log", "pending_human", "limitations"):
        if not isinstance(result.get(k), list):
            issues.append("missing_or_invalid_" + k)
    if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
        issues.append("change_log_empty")
    for k in DEFAULT_CONTRACT_KEYS:
        if k not in result:
            issues.append("missing_contract_field_" + k)
    cands = result.get("candidates")
    if not isinstance(cands, list) or not cands:
        issues.append("no_candidates")
    else:
        for p in cands:
            if not isinstance(p, dict):
                issues.append("candidate_not_object")
                continue
            if not p.get("plan_id"):
                issues.append("candidate_missing_plan_id")
            sf = p.get("seven_fields")
            if not isinstance(sf, dict) or any(sf.get(f) is None for f in SEVEN_FIELDS):
                issues.append("candidate_%s_seven_fields_incomplete" % (p.get("plan_id", "?")))
            dr = p.get("dimension_ratings")
            if not isinstance(dr, dict) or any(dr.get(d) is None for d in FIVE_DIMS):
                warnings.append("candidate_%s_ratings_incomplete" % (p.get("plan_id", "?")))
    sel = result.get("selection")
    if not isinstance(sel, dict):
        issues.append("missing_selection")
    else:
        for k in ("chosen", "ranking", "rationale", "rejected", "sensitivity"):
            if k not in sel:
                issues.append("selection_missing_" + k)
    cm = result.get("comparison_matrix")
    if not isinstance(cm, dict) or not isinstance(cm.get("schemes"), list) \
            or not isinstance(cm.get("rows"), list):
        issues.append("comparison_matrix_invalid")
    out_dir = os.path.dirname(os.path.abspath(result_path))
    for fn in ("comparison_matrix.csv", "comparison_matrix.svg",
               "comparison_matrix.pdf", "plan_comparison.md"):
        p = os.path.join(out_dir, fn)
        if not os.path.isfile(p):
            warnings.append("artifact_missing_" + fn)
            continue
        if fn.endswith(".svg"):
            import xml.etree.ElementTree as ET
            try:
                ET.parse(p)
            except Exception as e:
                issues.append("svg_unparseable: " + str(e))
        elif fn.endswith(".pdf"):
            with open(p, "rb") as f:
                head = f.read(8)
                f.seek(-6, 2)
                tail = f.read(6)
            if not head.startswith(b"%PDF-"):
                issues.append("pdf_bad_header")
            if b"%%EOF" not in tail:
                issues.append("pdf_bad_trailer")
        elif fn.endswith(".csv"):
            with open(p, "rb") as f:
                bom = f.read(3)
            if bom != b"\xef\xbb\xbf":
                warnings.append("csv_missing_bom")


def _write_demo_input(inp, wrk):
    case = {
        "case_id": "demo-0001",
        "title": "示例：候选合成路线比较（非正式案例）",
        "output_contract": {"experimental_plan": {}, "evidence_and_risks": {},
                            "confirmation_items": []},
        "candidates": [
            {"plan_id": "P1", "name": "路线A", "version": "v1",
             "steps": "示例步骤", "description": "示例候选方案"},
            {"plan_id": "P2", "name": "路线B", "version": "v1",
             "steps": "示例步骤", "description": "示例候选方案"},
            {"plan_id": "P3", "name": "路线C", "version": "v1",
             "steps": "示例步骤", "description": "示例候选方案"},
        ],
        "note": "示例，非正式案例",
    }
    write_json(os.path.join(inp, "case.json"), case)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 示例任务（非正式案例）\n\n比较三个示例候选方案。\n")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    with open(os.path.join(inp, "materials", "paper1.md"), "w", encoding="utf-8") as f:
        f.write("# 示例材料\n\n示例报价：机时 200 元/小时；耗材 500 元/批。\n")
    analysis = {
        "case_id": "demo-0001",
        "research_question": {
            "testable_question": "示例：哪个方案在预算与时间窗内达成目标",
            "variables": {"independent": ["route"], "dependent": ["yield"],
                          "controlled": ["temperature"]},
            "hypothesis": "路线B综合最优"},
        "comparison_frame": {
            "objectives": ["示例目标"],
            "hard_constraints": [{"field": "cost", "op": "max", "value": 3000},
                                 {"field": "time", "op": "max", "value": 48}],
            "criteria": list(FIVE_DIMS)},
        "weights": dict(DEFAULT_WEIGHTS),
        "candidates": [
            {"plan_id": "P1", "name": "路线A", "version": "v1",
             "seven_fields": {f: "示例：" + f for f in SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 3, "risk": 3, "cost": 4,
                                   "feasibility": 4, "evidence": 3},
             "feasibility": {"equipment_available": "yes"},
             "risks": [{"trigger": "示例", "consequence": "示例", "magnitude": "low",
                        "mitigation": "示例", "fallback": "示例"}],
             "evidence": [{"claim": "示例材料中的报价", "locator": "materials/paper1.md",
                           "quote": "机时 200 元/小时", "kind": "quote",
                           "value_kind": "contract"}],
             "cost": {"currency": "CNY",
                      "lines": [{"part": "equipment_time", "amount": 1000},
                                {"part": "consumables", "amount": 500}]},
             "time": {"unit": "h", "parts": {"prep": 2, "execution": 20, "rework": 4}}},
            {"plan_id": "P2", "name": "路线B", "version": "v1",
             "seven_fields": {f: "示例：" + f for f in SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 5, "risk": 4, "cost": 3,
                                   "feasibility": 4, "evidence": 4},
             "feasibility": {"equipment_available": "yes"},
             "risks": [{"trigger": "示例", "consequence": "示例", "magnitude": "low",
                        "mitigation": "示例", "fallback": "示例"}],
             "evidence": [{"claim": "示例材料中的报价", "locator": "materials/paper1.md",
                           "quote": "机时 200 元/小时", "kind": "quote",
                           "value_kind": "contract"}],
             "cost": {"currency": "CNY",
                      "lines": [{"part": "equipment_time", "amount": 800},
                                {"part": "consumables", "amount": 600}]},
             "time": {"unit": "h", "parts": {"prep": 1, "execution": 16, "rework": 2}}},
            {"plan_id": "P3", "name": "路线C", "version": "v1",
             "seven_fields": {f: "示例：" + f for f in SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 2, "risk": 2, "cost": 2,
                                   "feasibility": 3, "evidence": 2},
             "feasibility": {"equipment_available": "yes"},
             "risks": [{"trigger": "示例", "consequence": "示例", "magnitude": "high",
                        "mitigation": "示例", "fallback": "示例"}],
             "evidence": [{"claim": "示例材料中的报价", "locator": "materials/paper1.md",
                           "quote": "机时 200 元/小时", "kind": "quote",
                           "value_kind": "contract"}],
             "cost": {"currency": "CNY",
                      "lines": [{"part": "equipment_time", "amount": 1500},
                                {"part": "consumables", "amount": 1800}]},
             "time": {"unit": "h", "parts": {"prep": 3, "execution": 30, "rework": 8}}},
        ],
        "selection": {"chosen": "P2", "ranking": ["P2", "P1", "P3"],
                      "rationale": "示例：综合评分与硬阈值约束下选 P2",
                      "rejected": [{"plan_id": "P3", "reason": "示例：超预算"}],
                      "sensitivity": {}},
        "confirmations": [{"item": "示例：设备确认", "type": "equipment",
                           "status": "pending", "note": "示例"}],
        "uncertainty": [{"item": "示例：报价为示意", "basis": "示例材料",
                         "impact": "成本估算为示意", "confidence": "low"}],
        "limitations": [{"scope": "示例", "cause": "演示数据",
                         "impact": "结论仅用于接口演示",
                         "mitigation": "正式运行使用 /input 事实材料"}],
        "sources": [],
        "change_log": [{"time": now_iso(), "step": "demo",
                        "note": "示例分析（非正式）"}],
        "errors": [], "degradations": [], "pending_human": [],
    }
    write_json(os.path.join(wrk, "analysis.json"), analysis)


def cmd_demo(args):
    tmp = tempfile.mkdtemp(prefix="sci-plan-demo-")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    wrk = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(wrk, exist_ok=True)
    _write_demo_input(inp, wrk)
    cmd_inspect(argparse.Namespace(input=inp, output=out, work=wrk))
    cmd_assemble(argparse.Namespace(input=inp, output=out, work=wrk))
    cmd_validate(argparse.Namespace(input=inp, output=out, work=wrk,
                                    result=os.path.join(out, "result.json")))
    print("demo done (示例，非正式案例): %s" % tmp)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="sci-plan-comparison skill runner")
    parser.add_argument("--input", help="input directory")
    parser.add_argument("--output", help="output directory")
    parser.add_argument("--work", help="work directory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect", help="write result.json skeleton + case_pack.json")
    sub.add_parser("assemble", help="build final result.json + artifacts")
    vp = sub.add_parser("validate", help="validate a result.json")
    vp.add_argument("result", nargs="?", help="path to result.json")
    sub.add_parser("demo", help="run end-to-end example (示例)")
    args = parser.parse_args(argv)
    if args.cmd == "inspect":
        cmd_inspect(args)
    elif args.cmd == "assemble":
        cmd_assemble(args)
    elif args.cmd == "validate":
        cmd_validate(args)
    elif args.cmd == "demo":
        cmd_demo(args)


if __name__ == "__main__":
    main(sys.argv[1:])
