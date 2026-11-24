"""core_impl.py — deterministic experiment-plan comparison engine.

Standard library only. No wall-clock timestamps, no random, sorted keys and
sorted lists everywhere: identical /input plus identical analysis.json produce
byte-identical result.json and artifacts (run twice, compare, they match).
"""

import csv
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

FIVE_DIMS = ["goal_fit", "risk", "cost", "feasibility", "evidence"]
SEVEN_FIELDS = [
    "assumptions", "variables", "controls", "steps",
    "parameters", "resources", "interpretation_criteria",
]
COST_ITEMS = ["machine_time", "consumables", "labor", "sample_prep", "scheduling", "rework"]
TIME_ITEMS = ["preparation", "execution", "scheduling", "rework"]
CANDIDATE_KEYS = (
    "candidates", "plans", "plan_list", "options", "alternatives",
    "schemes", "proposals", "plan_candidates", "candidate_plans",
)
DEFAULT_WEIGHTS = {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20, "cost": 0.15, "risk": 0.15}
MATERIAL_EXTS = (".pdf", ".txt", ".md", ".csv", ".json", ".tsv", ".docx", ".xlsx", ".pptx")


# --------------------------------------------------------------------------- #
# Filesystem / text helpers
# --------------------------------------------------------------------------- #
def _load_text(path):
    """Read UTF-8 text with BOM tolerance; degrade to latin-1 on bad encoding."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, str(exc)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "bad-encoding"


def _read_json(path):
    text, err = _load_text(path)
    if err is not None and text is None:
        return None, err
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "json-decode-error: %s" % exc
    except Exception as exc:  # pragma: no cover - defensive
        return None, str(exc)


def _iter_files(directory):
    out = []
    if not os.path.isdir(directory):
        return out
    for root, _dirs, files in os.walk(directory):
        for name in files:
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, directory).replace("\\", "/")
            out.append({"path": full, "rel": rel, "size": size,
                        "name": name, "ext": os.path.splitext(name)[1].lower()})
    out.sort(key=lambda x: x["rel"])
    return out


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = (path + "." + k) if path else k
            yield k, v, child
            yield from _walk(v, child)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, "%s[%d]" % (path, i))


def _find_contract(obj):
    for k, v, p in _walk(obj):
        if k == "output_contract":
            if isinstance(v, dict):
                return v, p
            if isinstance(v, list):
                return {str(x): None for x in v if isinstance(x, str)}, p
    return None, None


def _find_case_id(obj):
    if isinstance(obj, dict):
        for k in ("case_id", "caseId", "challenge_id", "challengeId"):
            if isinstance(obj.get(k), str) and obj[k]:
                return obj[k]
    for k, v, _p in _walk(obj):
        if k in ("case_id", "caseId") and isinstance(v, str) and v:
            return v
    return None


def _deterministic_time(case_obj, run_id):
    """Timestamp-like value from the input's own declared date, else run id.

    Never the wall clock: identical input files yield the identical value, so
    change_log stays byte-identical across repeated runs.
    """
    if isinstance(case_obj, dict):
        for k, v, _p in _walk(case_obj):
            if k in ("created_at", "generated_at", "timestamp", "submitted_at",
                     "case_date", "date", "updated_at") and isinstance(v, str) and v.strip():
                return v.strip()
    return "run:" + run_id


def _find_candidates(obj):
    found = []
    for k, v, p in _walk(obj):
        if k in CANDIDATE_KEYS and isinstance(v, list):
            items = [x for x in v if isinstance(x, dict)]
            if items:
                found.append((p, items))
    return found


def _plan_id(cand, idx):
    for k in ("plan_id", "planId", "id", "name", "plan", "scheme_id", "option_id"):
        v = cand.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return "P%d" % (idx + 1)


def _content_hash(input_dir):
    h = hashlib.sha256()
    for f in _iter_files(input_dir):
        if f["size"] <= 0 or f["size"] > MAX_FILE_BYTES:
            continue
        h.update(f["rel"].encode("utf-8", "replace"))
        h.update(b"\x00")
        try:
            with open(f["path"], "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"[-+]?\d+(?:\.\d+)?", v.replace(",", ""))
        if m:
            return float(m.group())
    return None


def _sum_numbers(obj):
    total = 0.0
    found = False

    def rec(o):
        nonlocal total, found
        if isinstance(o, dict):
            for v in o.values():
                rec(v)
        elif isinstance(o, list):
            for v in o:
                rec(v)
        elif isinstance(o, bool):
            pass
        elif isinstance(o, (int, float)):
            total += float(o)
            found = True
        elif isinstance(o, str):
            n = _to_number(o)
            if n is not None:
                total += n
                found = True

    rec(obj)
    return total if found else None


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# --------------------------------------------------------------------------- #
# Skeleton (inspect) — always writes a legal result.json
# --------------------------------------------------------------------------- #
def _build_skeleton(case_id, status, contract, errors, degradations, run_id, dtime):
    log = [{"seq": 1, "step": "inspect", "time": dtime, "run": run_id,
            "note": "skeleton written before any analysis; status=%s" % status}]
    return {
        "case_id": case_id,
        "status": status,
        "research_question": None,
        "comparison_frame": None,
        "candidates": [],
        "comparison_matrix": {"fields": FIVE_DIMS + ["weighted_score", "feasible"], "rows": [], "normalized": {}},
        "selection": None,
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "change_log": log,
        "pending_human": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "runtime": {"run_id": run_id, "model_requests_used": None,
                    "seconds_budget": 900, "engine": "deterministic-stdlib"},
        "artifacts": [],
        "experimental_plan": None,
        "evidence_and_risks": None,
        "confirmation_items": None,
    }


def cmd_inspect(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    files = _iter_files(input_dir)
    case_path = None
    task_path = None
    materials = []
    for f in files:
        if f["name"].lower() == "case.json":
            case_path = f
        elif f["name"].lower() == "task.md":
            task_path = f
        elif f["ext"] in MATERIAL_EXTS:
            materials.append(f)

    errors = []
    degradations = []
    case_obj = None
    run_id = _content_hash(input_dir) or "no-input"

    if case_path is None:
        status = "input_error"
        errors.append("case.json not found under %s" % (input_dir or "."))
    else:
        if case_path["size"] <= 0:
            errors.append("case.json is empty")
            status = "input_error"
        elif case_path["size"] > MAX_FILE_BYTES:
            errors.append("case.json exceeds %d bytes; skipped" % MAX_FILE_BYTES)
            status = "input_error"
        else:
            case_obj, err = _read_json(case_path["path"])
            if err is not None:
                errors.append("case.json unreadable: %s" % err)
                status = "input_error"
            else:
                contract, _ = _find_contract(case_obj)
                cands = _find_candidates(case_obj)
                n_cands = sum(len(items) for _p, items in cands)
                if n_cands == 0:
                    status = "insufficient_evidence"
                    errors.append("no candidate plans discovered in case.json")
                elif not materials:
                    status = "insufficient_evidence"
                    errors.append("candidates present but no material files (PDF/txt/csv) found")
                else:
                    status = "partial"

    # Build case_pack (always written; safe even when status is input_error).
    candidates = []
    if case_obj is not None:
        for _p, items in _find_candidates(case_obj):
            for i, c in enumerate(items):
                pid = _plan_id(c, len(candidates))
                name = c.get("name") if isinstance(c.get("name"), str) else pid
                candidates.append({"plan_id": pid, "name": name,
                                   "raw": c, "found_at": _p})

    contract, contract_path = (None, None)
    if case_obj is not None:
        contract, contract_path = _find_contract(case_obj)

    task_text, task_err = ("", None)
    if task_path is not None and task_path["size"] <= MAX_FILE_BYTES:
        task_text, task_err = _load_text(task_path["path"])
        if task_err is not None:
            degradations.append("task.md decoding issue: %s" % task_err)

    case_id = _find_case_id(case_obj) if case_obj is not None else None
    if not case_id:
        case_id = os.path.basename(input_dir.rstrip("/\\")) or "unknown-case"
    dtime = _deterministic_time(case_obj, run_id)

    case_pack = {
        "case_id": case_id,
        "run_id": run_id,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "status_initial": status,
        "case_json": {"path": case_path["path"] if case_path else None,
                      "rel": case_path["rel"] if case_path else None,
                      "parsed": case_obj is not None},
        "task_md": {"path": task_path["path"] if task_path else None,
                    "rel": task_path["rel"] if task_path else None,
                    "size": task_path["size"] if task_path else 0},
        "task_text": task_text,
        "output_contract": contract if isinstance(contract, dict) else None,
        "output_contract_path": contract_path,
        "materials": [{"rel": m["rel"], "size": m["size"], "ext": m["ext"],
                       "needs_extraction": m["ext"] in (".pdf", ".docx", ".xlsx", ".pptx")}
                      for m in materials],
        "candidates": candidates,
        "candidate_count": len(candidates),
        "errors": errors,
        "degradations": degradations,
        "case_time": dtime,
        "change_log": [{"seq": 1, "step": "inspect", "time": dtime, "run": run_id,
                        "note": "case_pack built; %d candidates, %d materials"
                                % (len(candidates), len(materials))}],
    }

    result = _build_skeleton(case_id, status, contract, errors, degradations, run_id, dtime)

    with open(os.path.join(work_dir, "case_pack.json"), "w", encoding="utf-8") as fh:
        json.dump(case_pack, fh, ensure_ascii=False, indent=2, sort_keys=True)
    with open(os.path.join(output_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


# --------------------------------------------------------------------------- #
# Cost / time / thresholds / scoring
# --------------------------------------------------------------------------- #
def _extract_cost(cand):
    override = cand.get("cost_override")
    if isinstance(override, dict) and override.get("total") is not None:
        total = _to_number(override.get("total"))
        return {"total": total, "currency": override.get("currency", "CNY"),
                "lines": override.get("lines", []) or [],
                "method": override.get("method", "override"),
                "note": override.get("note", ""),
                "evidence": override.get("evidence", "")}
    resources = (cand.get("seven_fields") or {}).get("resources")
    if isinstance(resources, dict):
        cost = resources.get("cost")
        if isinstance(cost, dict):
            total = _sum_numbers(cost)
            if total is not None:
                return {"total": total, "currency": cost.get("currency", "CNY"),
                        "lines": sorted(cost.keys()), "method": "computed",
                        "note": "summed numeric leaves under resources.cost",
                        "evidence": ""}
        if cost is not None and _to_number(cost) is not None:
            return {"total": _to_number(cost), "currency": "CNY",
                    "lines": [], "method": "computed", "note": "", "evidence": ""}
    return {"total": None, "currency": None, "lines": [],
            "method": "not_computable",
            "note": "no cost data in candidate or override", "evidence": ""}


def _extract_time(cand):
    override = cand.get("time_override")
    if isinstance(override, dict) and override.get("total") is not None:
        return {"total": _to_number(override.get("total")),
                "unit": override.get("unit", "h"),
                "parts": override.get("parts", {}) or {},
                "computed": bool(override.get("computed", True))}
    resources = (cand.get("seven_fields") or {}).get("resources")
    if isinstance(resources, dict):
        time = resources.get("time")
        if isinstance(time, dict):
            total = _sum_numbers(time)
            if total is not None:
                return {"total": total, "unit": time.get("unit", "h"),
                        "parts": sorted(time.keys()), "computed": True}
        if time is not None and _to_number(time) is not None:
            return {"total": _to_number(time), "unit": "h", "parts": {}, "computed": True}
    return {"total": None, "unit": "h", "parts": {},
            "computed": False, "note": "no time data"}


def _extract_constraints(frame):
    budget = None
    budget_currency = None
    max_time = None
    time_unit = None
    if not isinstance(frame, dict):
        return {"budget": budget, "budget_currency": budget_currency,
                "max_time": max_time, "time_unit": time_unit}

    hc = frame.get("hard_constraints") or frame.get("constraints") or []
    if isinstance(hc, dict):
        hc = [hc]
    if not isinstance(hc, list):
        hc = []
    for item in hc:
        if isinstance(item, dict):
            for bk in ("budget", "max_cost", "total_cost", "budget_limit", "max_budget"):
                if bk in item and _to_number(item[bk]) is not None:
                    budget = _to_number(item[bk])
                    budget_currency = item.get("currency", "CNY")
                    break
            for tk in ("max_time", "deadline", "time_window", "duration", "time_budget"):
                if tk in item and _to_number(item[tk]) is not None:
                    max_time = _to_number(item[tk])
                    time_unit = item.get("unit", item.get("time_unit", "h"))
                    break
        elif isinstance(item, str):
            m = re.search(r"(?:budget|预算|总预算|max_cost)[^\d]*([\d,]+(?:\.\d+)?)\s*([A-Za-z￥¥]*|元|万)", item)
            if m and budget is None:
                budget = _to_number(m.group(1))
                budget_currency = m.group(2) or "CNY"
            m = re.search(r"(?:max_time|deadline|时间窗|工期)[^\d]*([\d,]+(?:\.\d+)?)\s*(h|hr|d|day|天|小时)?", item)
            if m and max_time is None:
                max_time = _to_number(m.group(1))
                time_unit = m.group(2) or "h"
    # flat frame keys fallback
    if budget is None:
        for bk in ("budget", "max_cost", "total_cost"):
            if bk in frame and _to_number(frame[bk]) is not None:
                budget = _to_number(frame[bk])
                budget_currency = frame.get("currency", "CNY")
                break
    if max_time is None:
        for tk in ("max_time", "deadline", "time_window"):
            if tk in frame and _to_number(frame[tk]) is not None:
                max_time = _to_number(frame[tk])
                time_unit = frame.get("time_unit", "h")
                break
    return {"budget": budget, "budget_currency": budget_currency,
            "max_time": max_time, "time_unit": time_unit}


def _norm_weights(weights):
    w = {}
    for d in FIVE_DIMS:
        v = weights.get(d) if isinstance(weights, dict) else None
        if v is None:
            v = DEFAULT_WEIGHTS.get(d, 0.0)
        w[d] = float(v)
    total = sum(w.values()) or 0.0
    if total <= 0:
        w = {d: 1.0 / len(FIVE_DIMS) for d in FIVE_DIMS}
        total = 1.0
    return {d: w[d] / total for d in FIVE_DIMS}


def _weighted_score(ratings, weights):
    present = {d: float(ratings.get(d, 0)) for d in FIVE_DIMS}
    score = sum(weights[d] * present[d] for d in FIVE_DIMS)
    return round(score, 4)


def _apply_sensitivity(cands, weights, chosen):
    """Perturb each dimension weight by +-0.05 (clamped >=0), re-normalize."""
    out = []
    robust = True
    base = _norm_weights(weights)
    eligible = [c for c in cands if c.get("feasible", True)]
    for d in FIVE_DIMS:
        for delta in (-0.05, 0.05):
            w2 = dict(base)
            w2[d] = max(0.0, w2[d] + delta)
            w2 = _norm_weights(w2)
            top = None
            best = -1.0
            for c in eligible:
                s = _weighted_score(c.get("dimension_ratings", {}), w2)
                if s > best or (s == best and (top is None or c["plan_id"] < top)):
                    best = s
                    top = c["plan_id"]
            if top != chosen and chosen is not None:
                robust = False
            out.append({"dimension": d, "delta": delta, "top": top})
    out.sort(key=lambda x: (x["dimension"], x["delta"]))
    return {"perturbations": out, "robust": robust,
            "note": "weights perturbed +-0.05 per dimension and re-normalized"}


# --------------------------------------------------------------------------- #
# Artifacts: SVG / CSV / MD / PDF (all deterministic, ASCII-only PDF)
# --------------------------------------------------------------------------- #
def _render_svg(case_id, cands, path):
    headers = ["plan", "goal_fit", "risk", "cost", "feasibility", "evidence", "score"]
    cols = [30, 190, 300, 380, 460, 540, 620]
    width = 740
    row_h = 24
    height = 56 + row_h * (len(cands) + 2)
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]
    lines.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (width, height))
    lines.append('<text x="%d" y="30" font-family="sans-serif" font-size="15" '
                 'font-weight="bold">%s</text>' % (cols[0], _xml_escape("Plan Comparison — %s" % case_id)))
    y = 56
    for i, h in enumerate(headers):
        lines.append('<text x="%d" y="%d" font-family="sans-serif" font-size="12" '
                     'font-weight="bold" fill="#222222">%s</text>' % (cols[i], y, _xml_escape(h)))
    y += row_h
    for c in cands:
        r = c.get("dimension_ratings", {})
        vals = [c.get("plan_id", "?"),
                r.get("goal_fit", "-"), r.get("risk", "-"), r.get("cost", "-"),
                r.get("feasibility", "-"), r.get("evidence", "-"),
                ("%.3f" % c["weighted_score"]) if isinstance(c.get("weighted_score"), (int, float)) else "-"]
        fill = "#e8f0e8" if c.get("feasible", True) else "#f6e4e4"
        lines.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                     % (cols[0], y - 16, width - cols[0] - 10, row_h - 4, fill))
        for i, v in enumerate(vals):
            lines.append('<text x="%d" y="%d" font-family="sans-serif" font-size="12">%s</text>'
                         % (cols[i], y, _xml_escape(v)))
        y += row_h
    lines.append('</svg>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _render_csv(cands, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["plan_id", "name", "goal_fit", "risk", "cost", "feasibility",
                    "evidence", "weighted_score", "total_cost", "total_time",
                    "feasible", "eliminated_reason"])
        for c in cands:
            r = c.get("dimension_ratings", {})
            w.writerow([c.get("plan_id", ""), c.get("name", ""),
                        r.get("goal_fit", ""), r.get("risk", ""), r.get("cost", ""),
                        r.get("feasibility", ""), r.get("evidence", ""),
                        c.get("weighted_score", ""),
                        c.get("cost", {}).get("total", ""),
                        c.get("time", {}).get("total", ""),
                        bool(c.get("feasible", True)),
                        c.get("eliminated_reason", "")])


def _render_md(case_id, cands, selection, confirmations, pending, path):
    lines = ["# 实验方案比较与选择", "", "- case_id: `%s`" % case_id, ""]
    lines += ["## 可比矩阵", "", "| 方案 | goal_fit | risk | cost | feasibility | evidence | 加权分 | 可行 |", 
              "|---|---|---|---|---|---|---|---|"]
    for c in cands:
        r = c.get("dimension_ratings", {})
        lines.append("| %s | %s | %s | %s | %s | %s | %.3f | %s |" % (
            c.get("plan_id", "?"), r.get("goal_fit", "-"), r.get("risk", "-"),
            r.get("cost", "-"), r.get("feasibility", "-"), r.get("evidence", "-"),
            c.get("weighted_score", 0.0) or 0.0, "是" if c.get("feasible", True) else "否"))
    lines.append("")
    if isinstance(selection, dict):
        lines += ["## 选择结论", "",
                  "- chosen: `%s`" % selection.get("chosen"),
                  "- ranking: %s" % ", ".join(selection.get("ranking") or []),
                  "- rationale: %s" % (selection.get("rationale") or ""),
                  "- sensitivity robust: %s" % bool((selection.get("sensitivity") or {}).get("robust"))]
        for rj in (selection.get("rejected") or []):
            if isinstance(rj, dict):
                lines.append("- rejected %s: %s" % (rj.get("plan_id"), rj.get("reason")))
        lines.append("")
    lines += ["## 确认项与待办", ""]
    for c in (confirmations or []):
        if isinstance(c, dict):
            lines.append("- [%s] %s — %s" % (c.get("status", "pending"), c.get("item"), c.get("note", "")))
    for p in (pending or []):
        lines.append("- pending_human: %s" % p)
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _render_pdf(case_id, cands, selection, path):
    lines = ["Plan Comparison Matrix", "case_id: %s" % case_id,
             "columns: plan goal_fit risk cost feasibility evidence score feasible"]
    for c in cands:
        r = c.get("dimension_ratings", {})
        lines.append("%s %s %s %s %s %s %s %s" % (
            c.get("plan_id", "?"), r.get("goal_fit", "-"), r.get("risk", "-"),
            r.get("cost", "-"), r.get("feasibility", "-"), r.get("evidence", "-"),
            ("%.3f" % c["weighted_score"]) if isinstance(c.get("weighted_score"), (int, float)) else "-",
            "Y" if c.get("feasible", True) else "N"))
    if isinstance(selection, dict):
        lines.append("chosen: %s" % (selection.get("chosen") or "none"))
    ascii_lines = [l.encode("ascii", "replace").decode("ascii") for l in lines]

    content = []
    y = 780
    for line in ascii_lines:
        content.append("BT /F1 10 Tf 50 %d Td (%s) Tj ET" % (y, _pdf_escape(line)))
        y -= 14
    stream = ("\n".join(content)).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += str(i).encode("ascii") + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    n = len(objects) + 1
    out += b"xref\n0 " + str(n).encode("ascii") + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += (b"trailer\n<< /Size " + str(n).encode("ascii") + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode("ascii") + b"\n%%EOF")
    with open(path, "wb") as fh:
        fh.write(out)


# --------------------------------------------------------------------------- #
# Assemble
# --------------------------------------------------------------------------- #
def _dedup_sources(sources):
    seen = set()
    out = []
    for s in (sources or []):
        if not isinstance(s, dict):
            continue
        key = (s.get("locator", ""), s.get("quote", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    out.sort(key=lambda x: (str(x.get("locator", "")), str(x.get("quote", ""))))
    return out


def _fill_contract(result, contract, fills):
    fills = fills if isinstance(fills, dict) else {}
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        keys = [str(x) for x in contract if isinstance(x, str)]
    else:
        return result
    for key in keys:
        if key in fills:
            result[key] = fills[key]
        elif key in ("experimental_plan", "plan", "experiment_plan", "方案", "实验方案"):
            result[key] = result["experimental_plan"]
        elif key in ("evidence_and_risks", "evidence", "risks", "依据与风险"):
            result[key] = result["evidence_and_risks"]
        elif key in ("confirmation_items", "confirmations", "确认项"):
            result[key] = result["confirmation_items"]
        else:
            result[key] = None
    return result


def cmd_assemble(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    cp_path = os.path.join(work_dir, "case_pack.json")
    cp, err = _read_json(cp_path)
    if cp is None:
        cp = {}
    analysis_path = os.path.join(work_dir, "analysis.json")
    analysis, aerr = _read_json(analysis_path)
    if analysis is None:
        analysis = {}

    case_id = analysis.get("case_id") or cp.get("case_id") or "unknown-case"
    run_id = cp.get("run_id") or _content_hash(input_dir) or "no-input"
    dtime = (cp.get("case_time") if isinstance(cp, dict) else None) or ("run:" + run_id)

    # Enrich candidates from analysis (sorted by plan_id for determinism).
    raw_cands = analysis.get("candidates") or []
    if not isinstance(raw_cands, list):
        raw_cands = []
    frame = analysis.get("comparison_frame") or {}
    if not isinstance(frame, dict):
        frame = {}
    weights = analysis.get("weights") or DEFAULT_WEIGHTS
    constraints = _extract_constraints(frame)

    cands = []
    for i, c in enumerate(raw_cands):
        if not isinstance(c, dict):
            continue
        pid = c.get("plan_id") or _plan_id(c, i)
        sf = c.get("seven_fields") or {}
        if not isinstance(sf, dict):
            sf = {}
        cost = _extract_cost(c)
        time = _extract_time(c)
        feasibility = c.get("feasibility") or {}
        if not isinstance(feasibility, dict):
            feasibility = {}
        equipment = str(feasibility.get("equipment_available", "")).lower()
        eliminated = []
        if equipment == "no":
            eliminated.append("equipment_unavailable")
        if cost["total"] is not None and constraints["budget"] is not None \
                and cost["total"] > constraints["budget"]:
            eliminated.append("over_budget")
        if time["total"] is not None and constraints["max_time"] is not None \
                and time["total"] > constraints["max_time"]:
            eliminated.append("over_time_window")
        cands.append({
            "plan_id": pid,
            "name": c.get("name") or pid,
            "version": c.get("version") or "v1",
            "seven_fields": sf,
            "dimension_ratings": {d: int(c.get("dimension_ratings", {}).get(d, 0) or 0) for d in FIVE_DIMS},
            "feasibility": feasibility,
            "risks": c.get("risks") or [],
            "evidence": c.get("evidence") or [],
            "infeasible_reasons": list(c.get("infeasible_reasons") or []),
            "cost": cost,
            "time": time,
            "eliminated_reasons": eliminated,
            "feasible": len(eliminated) == 0 and equipment != "no",
        })
    cands.sort(key=lambda x: x["plan_id"])

    wnorm = _norm_weights(weights)
    for c in cands:
        c["weighted_score"] = _weighted_score(c["dimension_ratings"], wnorm)

    eligible = [c for c in cands if c["feasible"]]
    ranking = sorted(eligible, key=lambda x: (-x["weighted_score"], x["plan_id"]))
    ranking_ids = [c["plan_id"] for c in ranking]
    eliminated_ids = [c["plan_id"] for c in cands if not c["feasible"]]

    sel_raw = analysis.get("selection") or {}
    if not isinstance(sel_raw, dict):
        sel_raw = {}
    chosen = sel_raw.get("chosen") or (ranking_ids[0] if ranking_ids else None)
    sensitivity = _apply_sensitivity(cands, weights, chosen)
    rejected = sel_raw.get("rejected") or []
    for pid in eliminated_ids:
        if not any(isinstance(r, dict) and r.get("plan_id") == pid for r in rejected):
            c = next((x for x in cands if x["plan_id"] == pid), None)
            rejected.append({"plan_id": pid,
                             "reason": "eliminated by hard threshold: %s"
                                       % ", ".join(c["eliminated_reasons"]) if c else "infeasible"})
    selection = {
        "chosen": chosen,
        "ranking": ranking_ids + eliminated_ids,
        "rationale": sel_raw.get("rationale", ""),
        "rejected": rejected,
        "sensitivity": sensitivity,
    }

    # Honest status.
    confirmations = analysis.get("confirmations") or []
    if not isinstance(confirmations, list):
        confirmations = []
    pending = analysis.get("pending_human") or []
    if not isinstance(pending, list):
        pending = []
    errors = list(analysis.get("errors") or [])
    degradations = list(analysis.get("degradations") or [])
    if not cands:
        status = "insufficient_evidence"
    elif chosen is None:
        status = "partial"
        if not any("all candidates infeasible" in str(e) for e in errors):
            errors.append("all candidates infeasible; no defensible choice")
    elif pending or errors or any(isinstance(c, dict) and c.get("status") != "confirmed"
                                  for c in confirmations):
        status = "partial"
    else:
        status = "ok"

    matrix_rows = []
    normalized = {}
    for c in cands:
        r = c["dimension_ratings"]
        row = {"plan_id": c["plan_id"], "feasible": c["feasible"],
               "weighted_score": c["weighted_score"],
               "eliminated_reasons": c["eliminated_reasons"]}
        for d in FIVE_DIMS:
            row[d] = r[d]
        matrix_rows.append(row)
        normalized[c["plan_id"]] = {d: round(r[d] / 5.0, 4) for d in FIVE_DIMS}
    matrix = {"fields": FIVE_DIMS + ["weighted_score", "feasible"],
              "rows": matrix_rows, "normalized": normalized}

    # Artifacts.
    artifacts = []
    for name, fn in (
        ("comparison_matrix.svg", lambda p: _render_svg(case_id, cands, p)),
        ("comparison_matrix.csv", lambda p: _render_csv(cands, p)),
        ("plan_comparison.md", lambda p: _render_md(case_id, cands, selection,
                                                    confirmations, pending, p)),
        ("comparison_matrix.pdf", lambda p: _render_pdf(case_id, cands, selection, p)),
    ):
        apath = os.path.join(output_dir, name)
        try:
            fn(apath)
            # Store only the relative path so result.json is independent of
            # the absolute output directory (byte-identical across runs).
            artifacts.append({"name": name, "path": os.path.relpath(apath, output_dir)})
        except Exception as exc:  # pragma: no cover - defensive
            errors.append("artifact %s failed: %s" % (name, exc))

    sources = _dedup_sources(analysis.get("sources") or [])

    result = {
        "case_id": case_id,
        "status": status,
        "research_question": analysis.get("research_question") or None,
        "comparison_frame": frame,
        "weights_normalized": wnorm,
        "candidates": cands,
        "comparison_matrix": matrix,
        "selection": selection,
        "confirmations": confirmations,
        "sources": sources,
        "uncertainty": analysis.get("uncertainty") or [],
        "change_log": (analysis.get("change_log") or []) + [
            {"seq": 98, "step": "assemble", "time": dtime, "run": run_id,
             "note": "arithmetic, hard thresholds, weighted scoring, artifacts"},
            {"seq": 99, "step": "assemble", "time": dtime, "run": run_id,
             "note": "status=%s chosen=%s" % (status, chosen)},
        ],
        "pending_human": pending,
        "errors": errors,
        "degradations": degradations,
        "runtime": {"run_id": run_id,
                    "model_requests_used": analysis.get("model_requests_used"),
                    "seconds_budget": 900, "engine": "deterministic-stdlib"},
        "artifacts": artifacts,
        "experimental_plan": {
            "research_question": analysis.get("research_question"),
            "comparison_frame": frame,
            "candidates": cands,
            "comparison_matrix": matrix,
            "selection": selection,
        },
        "evidence_and_risks": {
            "sources": sources,
            "risks_by_candidate": {c["plan_id"]: c["risks"] for c in cands},
            "uncertainty": analysis.get("uncertainty") or [],
        },
        "confirmation_items": {"confirmations": confirmations},
    }

    contract = cp.get("output_contract") if isinstance(cp, dict) else None
    result = _fill_contract(result, contract, analysis.get("contract_fills") or {})

    with open(os.path.join(output_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


# --------------------------------------------------------------------------- #
# Validate
# --------------------------------------------------------------------------- #
def _check_pdf(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF"), len(raw)
    except OSError:
        return False, 0


def cmd_validate(target):
    data, err = _read_json(target)
    problems = []
    if data is None:
        problems.append("result.json not readable: %s" % (err or "missing"))
    else:
        if not isinstance(data.get("case_id"), str) or not data["case_id"]:
            problems.append("case_id missing/empty")
        if data.get("status") not in STATUSES:
            problems.append("status invalid: %r" % data.get("status"))
        if not isinstance(data.get("candidates"), list):
            problems.append("candidates not a list")
        else:
            for c in data["candidates"]:
                sf = c.get("seven_fields") if isinstance(c, dict) else None
                if not isinstance(sf, dict):
                    problems.append("%s missing seven_fields" % c.get("plan_id"))
                    continue
                for f in SEVEN_FIELDS:
                    if f not in sf:
                        problems.append("%s missing seven_fields.%s" % (c.get("plan_id"), f))
                for d in FIVE_DIMS:
                    v = c.get("dimension_ratings", {}).get(d) if isinstance(c, dict) else None
                    if not isinstance(v, (int, float)) or isinstance(v, bool):
                        problems.append("%s dimension_ratings.%s not a 0-5 number" % (c.get("plan_id"), d))
        if not isinstance(data.get("sources"), list):
            problems.append("sources missing")
        if not isinstance(data.get("uncertainty"), list):
            problems.append("uncertainty missing")
        if not isinstance(data.get("change_log"), list) or not data["change_log"]:
            problems.append("change_log missing/empty")
        if data.get("selection") is None:
            problems.append("selection missing")
        for a in (data.get("artifacts") or []):
            p = a.get("path")
            if p and not os.path.isabs(p):
                p = os.path.join(os.path.dirname(os.path.abspath(target)), p)
            if p and os.path.exists(p):
                if p.endswith(".svg"):
                    try:
                        ET.parse(p)
                    except Exception as exc:
                        problems.append("SVG invalid: %s" % exc)
                elif p.endswith(".pdf"):
                    ok, _ = _check_pdf(p)
                    if not ok:
                        problems.append("PDF invalid")
                elif p.endswith(".csv"):
                    with open(p, "rb") as fh:
                        head = fh.read(3)
                    if head != b"\xef\xbb\xbf":
                        problems.append("CSV missing UTF-8 BOM")
            else:
                problems.append("artifact missing: %s" % (a.get("path") or a.get("name")))
    summary = {"valid": not problems, "problems": problems, "target": target}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["valid"] else 1


# --------------------------------------------------------------------------- #
# Demo (interface example only — never a real case)
# --------------------------------------------------------------------------- #
DEMO_CASE = {
    "case_id": "demo-case-001",
    "output_contract": {"experimental_plan": None, "evidence_and_risks": None,
                        "confirmation_items": None},
    "candidates": [
        {"plan_id": "P1", "name": "Demo plan A"},
        {"plan_id": "P2", "name": "Demo plan B"},
    ],
}

DEMO_ANALYSIS = {
    "case_id": "demo-case-001",
    "research_question": {"testable_question": "demo question",
                          "variables": {"independent": [], "dependent": [], "controlled": []},
                          "hypothesis": "demo"},
    "comparison_frame": {"objectives": ["demo"], "hard_constraints": ["budget <= 1000 CNY", "max_time <= 40 h"],
                         "criteria": FIVE_DIMS},
    "weights": DEFAULT_WEIGHTS,
    "candidates": [
        {"plan_id": "P1", "name": "Demo plan A", "version": "v1",
         "seven_fields": {f: ("demo %s" % f) for f in SEVEN_FIELDS},
         "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 3, "feasibility": 4, "evidence": 3},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
         "risks": [{"trigger": "demo", "consequence": "demo", "magnitude": "low",
                    "mitigation": "demo", "fallback": "demo"}],
         "evidence": [{"claim": "demo", "locator": "demo/quote.md#p1", "quote": "demo", "kind": "manual"}],
         "cost_override": {"total": 600, "currency": "CNY", "lines": [], "note": "demo"},
         "time_override": {"total": 30, "unit": "h", "parts": {}, "computed": True}},
        {"plan_id": "P2", "name": "Demo plan B", "version": "v1",
         "seven_fields": {f: ("demo %s" % f) for f in SEVEN_FIELDS},
         "dimension_ratings": {"goal_fit": 3, "risk": 4, "cost": 4, "feasibility": 3, "evidence": 2},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
         "risks": [{"trigger": "demo", "consequence": "demo", "magnitude": "low",
                    "mitigation": "demo", "fallback": "demo"}],
         "evidence": [{"claim": "demo", "locator": "demo/quote.md#p1", "quote": "demo", "kind": "manual"}],
         "cost_override": {"total": 800, "currency": "CNY", "lines": [], "note": "demo"},
         "time_override": {"total": 50, "unit": "h", "parts": {}, "computed": True}},
    ],
    "selection": {"chosen": "P1", "ranking": ["P1", "P2"], "rationale": "demo rationale",
                  "rejected": [{"plan_id": "P2", "reason": "demo rejected"}], "sensitivity": {}},
    "confirmations": [{"item": "demo safety check", "type": "safety", "status": "confirmed", "note": "demo"}],
    "uncertainty": [], "sources": [], "contract_fills": {}, "change_log": [], "errors": [],
    "degradations": [], "pending_human": [],
}


def cmd_demo(input_dir, output_dir, work_dir):
    demo_in = os.path.join(work_dir, "demo_input")
    demo_out = os.path.join(output_dir, "demo")
    demo_work = os.path.join(work_dir, "demo_work")
    os.makedirs(demo_in, exist_ok=True)
    os.makedirs(demo_out, exist_ok=True)
    os.makedirs(demo_work, exist_ok=True)
    with open(os.path.join(demo_in, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(DEMO_CASE, fh, ensure_ascii=False, indent=2, sort_keys=True)
    with open(os.path.join(demo_in, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# Demo task (interface example, not a real case)\n")
    with open(os.path.join(demo_in, "quote.md"), "w", encoding="utf-8") as fh:
        fh.write("## p1\n\ndemo evidence line\n")
    rc = cmd_inspect(demo_in, demo_out, demo_work)
    if rc != 0:
        return rc
    with open(os.path.join(demo_work, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(DEMO_ANALYSIS, fh, ensure_ascii=False, indent=2, sort_keys=True)
    rc = cmd_assemble(demo_in, demo_out, demo_work)
    print("demo: wrote example outputs to %s (is_example=true)" % demo_out)
    return rc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_help():
    print("usage: main.py <inspect|assemble|validate|demo> "
          "[--input DIR] [--output DIR] [--work DIR] [--help]")
    print("  inspect   discover /input, write result.json skeleton + case_pack.json")
    print("  assemble  merge analysis.json into final result.json + artifacts")
    print("  validate  check result.json (pass path as positional arg)")
    print("  demo      write a deterministic interface example (is_example=true)")
    print("defaults: --input /input --output /app/output --work /app/work")


def _parse_args(argv):
    opts = {}
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work") and i + 1 < len(argv):
            opts[a[2:] + "_dir"] = argv[i + 1]
            i += 2
        elif a.startswith("--"):
            opts[a[2:]] = True
            i += 1
        else:
            pos.append(a)
            i += 1
    return pos, opts


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("--help", "-h"):
        _print_help()
        return 0
    pos, opts = _parse_args(argv)
    cmd = pos[0] if pos else None
    input_dir = opts.get("input_dir") or os.environ.get("INPUT_DIR") or DEFAULT_INPUT
    output_dir = opts.get("output_dir") or os.environ.get("OUTPUT_DIR") or DEFAULT_OUTPUT
    work_dir = opts.get("work_dir") or os.environ.get("WORK_DIR") or DEFAULT_WORK
    if cmd == "inspect":
        return cmd_inspect(input_dir, output_dir, work_dir)
    if cmd == "assemble":
        return cmd_assemble(input_dir, output_dir, work_dir)
    if cmd == "validate":
        target = pos[1] if len(pos) > 1 else os.path.join(output_dir, "result.json")
        return cmd_validate(target)
    if cmd == "demo":
        return cmd_demo(input_dir, output_dir, work_dir)
    _print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
