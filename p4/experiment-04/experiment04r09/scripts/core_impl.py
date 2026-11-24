#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案横向比较与选择 — 确定性辅助脚本（仅 Python 标准库）。

职责边界：本脚本只做「可机械复核」的部分——解析输入、立即落盘合法结果骨架、
成本/时间算术、硬阈值淘汰、加权评分与排名、权重敏感性、矩阵与预览产物渲染、
结果结构校验。方案内容、证据逐字引用、维度打分与最终取舍理由由 Agent 读实际
材料后写入 work/analysis.json，本脚本不臆造任何科学数据或报价。

子命令:
  inspect   解析 /input，第一时间落盘合法 result.json 骨架 + work/case_pack.json
  assemble  读取 work/analysis.json 与 work/case_pack.json，计算并渲染全部产物
  validate  校验 result.json 结构与输出契约（目标文件作为最后一个位置参数）
  selftest  运行自检：仅标准库、demo、空输入、坏 JSON、确定性、产物合法性
  demo      在临时目录生成并跑通一个明确标注为「示例」的最小案例

路径优先级: --input/--output/--work 参数 > SKILL_INPUT/SKILL_OUTPUT/SKILL_WORK
            环境变量 > 运行时默认 /input、/app/output、/app/work > 本地 ./demo/*
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

VERSION = "1.0.0"
DIMENSIONS = ("goal_fit", "risk", "cost", "feasibility", "evidence")
DEFAULT_WEIGHTS = {"goal_fit": 0.25, "risk": 0.15, "cost": 0.15, "feasibility": 0.25, "evidence": 0.20}
SEVEN_FIELDS = ("assumptions", "variables", "controls", "steps", "parameters", "resources", "interpretation_criteria")
COST_ITEMS = ("machine_time", "consumables", "labor", "sample_prep", "scheduling", "rework")
TIME_PARTS = ("preparation", "scheduling", "execution", "rework", "other")
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_FILE_BYTES = 2 * 1024 * 1024

FIELD_CN = {
    "assumptions": "假设", "variables": "变量", "controls": "对照", "steps": "步骤",
    "parameters": "参数", "resources": "资源", "interpretation_criteria": "判读标准",
}
DIM_CN = {"goal_fit": "目标契合", "risk": "风险", "cost": "成本", "feasibility": "可行性", "evidence": "证据"}


# --------------------------------------------------------------------------- 基础工具

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _float_or(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int_or(v, default=1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _is_one_time(cat):
    return str(cat).strip().lower() in ("one_time", "onetime", "一次性", "固定", "fixed")


def _is_per_run(cat):
    return str(cat).strip().lower() in ("per_run", "perrun", "按次", "每次", "variable", "run")


def _clamp_rating(v):
    if v is None:
        return 0.0
    return max(0.0, min(5.0, _float_or(v, 0.0)))


def _xml(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _ascii(s):
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(s))


def _pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _md_cell(s, maxlen=200):
    t = "" if s is None else str(s)
    t = t.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return (t[:maxlen] + "…") if len(t) > maxlen else t


def _fmt_cell(v):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".")
    return str(v)


def resolve_dirs(args):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def pick(flag, env, default, demo_rel):
        if flag:
            return os.path.abspath(flag)
        if env and os.getenv(env):
            return os.path.abspath(os.getenv(env))
        if default and os.path.isdir(default):
            return default
        return os.path.join(root, demo_rel)

    return (
        pick(args.input, "SKILL_INPUT", "/input", "demo/input"),
        pick(args.output, "SKILL_OUTPUT", "/app/output", "demo/output"),
        pick(args.work, "SKILL_WORK", "/app/work", "demo/work"),
    )


def read_text(path, max_chars=None):
    try:
        data = open(path, "rb").read()
    except OSError as e:
        return None, str(e)
    text = None
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = data.decode("latin-1", "replace")
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text, None


def parse_json(text):
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001 - 必须吞掉所有坏 JSON
        return None, str(e)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json_file(path):
    if not os.path.isfile(path):
        return None
    raw, _ = read_text(path)
    if raw is None:
        return None
    parsed, _ = parse_json(raw)
    return parsed


def scan_files(directory):
    found = []
    if not os.path.isdir(directory):
        return found
    for base, dirs, files in os.walk(directory):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(base, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            found.append({"path": p, "rel": os.path.relpath(p, directory).replace(os.sep, "/"), "size": size})
    return found


# --------------------------------------------------------------------------- 输入发现

def discover_case(inp):
    files = scan_files(inp)
    case_json = task_md = None
    for f in files:
        rel = f["rel"].lower()
        if case_json is None and rel.split("/")[-1] == "case.json":
            case_json = f
        if task_md is None and rel.split("/")[-1] == "task.md":
            task_md = f
    if case_json is None:
        case_json = next((f for f in files if f["rel"].lower().endswith(".json")), None)
    if task_md is None:
        task_md = next((f for f in files if f["rel"].lower().endswith((".md", ".txt"))), None)
    return case_json, task_md, files


def find_by_key(obj, key, path="", out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k == key:
                out.append((p, v))
            find_by_key(v, key, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_by_key(v, key, f"{path}[{i}]", out)
    return out


def count_candidates(case):
    for key in ("candidates", "plans", "options", "alternatives", "候选方案", "方案"):
        for _p, v in find_by_key(case, key):
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return len(v)
    ids = set()
    for key in ("plan_id", "planId", "id"):
        for _p, v in find_by_key(case, key):
            ids.add(str(v))
    return len(ids)


CONSTRAINT_PATTERNS = (
    "budget", "cost_limit", "total_budget", "max_cost", "deadline",
    "time_limit", "duration", "lead_time", "window", "max_time",
)


def detect_constraints(case):
    out = []

    def walk(obj, path):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                kl = k.lower()
                if any(pat in kl for pat in CONSTRAINT_PATTERNS) and isinstance(v, (int, float, str, dict)):
                    out.append({"path": p, "value": v})
                walk(v, p)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(case, "")
    return out[:40]


def extract_contract(case):
    hits = find_by_key(case, "output_contract") or find_by_key(case, "outputContract")
    if hits:
        v = hits[0][1]
        if isinstance(v, dict):
            return {k: None for k in v.keys()}
        if isinstance(v, list):
            return {str(k): None for k in v}
    return {}


# --------------------------------------------------------------------------- inspect

def make_skeleton(pack, status):
    result = {
        "case_id": pack.get("case_id"),
        "status": status,
        "research_question": {"testable_question": None, "variables": {"independent": [], "dependent": [], "controlled": []}, "hypothesis": None},
        "comparison_frame": {"objectives": [], "hard_constraints": [], "criteria": list(DIMENSIONS)},
        "candidates": [],
        "comparison_matrix": {"row_labels": [], "columns": [], "rows": []},
        "selection": {"chosen": None, "ranking": [], "rationale": None, "rejected": [], "sensitivity": {}},
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "change_log": [{"time": now_iso(), "step": "inspect", "note": "skeleton written before analysis"}],
        "errors": list(pack.get("errors") or []),
        "degradations": list(pack.get("degradations") or []),
        "runtime": {"started": now_iso(), "seconds": 0, "script_version": VERSION},
        "artifacts": [],
    }
    for k, v in (pack.get("output_contract") or {}).items():
        if k not in result:
            result[k] = v
    return result


def _do_inspect(inp, pack):
    if not os.path.isdir(inp):
        pack.setdefault("errors", []).append("input directory missing: %s" % inp)
        return pack
    case_json, task_md, files = discover_case(inp)
    pack["input_files"] = [f["rel"] for f in files]
    pack["case_json_path"] = case_json["path"] if case_json else None
    pack["task_md_path"] = task_md["path"] if task_md else None
    if case_json is None:
        pack.setdefault("errors", []).append("case.json not found under input")
        return pack
    raw, err = read_text(case_json["path"])
    if raw is None:
        pack.setdefault("errors", []).append("cannot read case.json: %s" % err)
        return pack
    parsed, jerr = parse_json(raw)
    if parsed is None:
        pack.setdefault("errors", []).append("case.json is not valid JSON: %s" % jerr)
        return pack
    pack["case_preview"] = json.dumps(parsed, ensure_ascii=False)[:3000]
    pack["case_id"] = str(parsed.get("case_id") or parsed.get("caseId") or parsed.get("id") or "case_unknown")
    pack["output_contract"] = extract_contract(parsed)
    pack["candidate_count"] = count_candidates(parsed)
    pack["detected_constraints"] = detect_constraints(parsed)
    if task_md:
        t, _ = read_text(task_md["path"], max_chars=3000)
        pack["task_preview"] = t or ""
    return pack


def _inspect_status(pack):
    if pack.get("errors"):
        return "input_error"
    if not pack.get("candidate_count"):
        return "insufficient_evidence"
    return "partial"


def cmd_inspect(args):
    inp, out, work = resolve_dirs(args)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    started = time.time()
    pack = {
        "case_id": None, "output_contract": {}, "candidate_count": 0,
        "input_files": [], "errors": [], "degradations": [], "notes": [],
        "runtime": {"started": now_iso(), "script_version": VERSION},
    }
    try:
        pack = _do_inspect(inp, pack)
        status = _inspect_status(pack)
    except Exception as e:  # noqa: BLE001 - 兜底，绝不因异常留空产物
        pack.setdefault("errors", []).append("inspect exception: %s" % e)
        status = "input_error"
    result = make_skeleton(pack, status)
    result["runtime"]["seconds"] = round(time.time() - started, 2)
    result["runtime"]["ended"] = now_iso()
    write_json(os.path.join(out, "result.json"), result)
    write_json(os.path.join(work, "case_pack.json"), pack)
    print("inspect: status=%s case_id=%s candidates=%d contract_keys=%d" %
          (status, pack.get("case_id"), pack.get("candidate_count"), len(pack.get("output_contract") or {})))
    return 0


# --------------------------------------------------------------------------- 算术与评分

def compute_cost(c):
    c = c or {}
    lines = c.get("lines") or []
    runs = max(1, _int_or(c.get("runs"), 1))
    base = {"currency": c.get("currency", "CNY"), "runs": runs, "lines": lines}
    if c.get("method") == "not_computable":
        base.update(total=None, method="not_computable", note=c.get("note", ""))
        return base
    if lines:
        one = sum(_float_or(l.get("amount"), 0.0) for l in lines if _is_one_time(l.get("category")))
        per = sum(_float_or(l.get("amount"), 0.0) for l in lines if _is_per_run(l.get("category")))
        base.update(one_time=round(one, 2), per_run=round(per, 2), total=round(one + per * runs, 2), method="sum")
        return base
    if isinstance(c.get("total"), (int, float)):
        base.update(total=c.get("total"), method="manual_override", note=c.get("note", ""))
        return base
    base.update(total=None, method="not_computable", note=c.get("note") or "无成本明细且无总额")
    return base


def compute_time(t):
    t = t or {}
    parts = t.get("parts") or {}
    base = {"unit": t.get("unit", "h"), "parts": parts}
    if t.get("method") == "not_computable":
        base.update(total=None, method="not_computable", note=t.get("note", ""))
        return base
    if parts:
        base.update(total=round(sum(_float_or(parts.get(k), 0.0) for k in TIME_PARTS), 2), method="sum")
        return base
    if isinstance(t.get("total"), (int, float)):
        base.update(total=t.get("total"), method="manual_override", note=t.get("note", ""))
        return base
    base.update(total=None, method="not_computable", note=t.get("note") or "无时间明细且无总额")
    return base


def _op_ok(actual, op, target):
    op = str(op).strip()
    if op in ("<=", "≤", "le"):
        return actual <= target
    if op in (">=", "≥", "ge"):
        return actual >= target
    if op in ("<", "lt"):
        return actual < target
    if op in (">", "gt"):
        return actual > target
    if op in ("==", "=", "eq"):
        return abs(actual - target) < 1e-9
    return True


def check_constraints(candidate, constraints, cost, time):
    violations = []
    for con in constraints or []:
        value = con.get("value")
        if value is None:
            continue
        field = con.get("field") or ""
        actual = None
        if field == "cost.total":
            actual = cost.get("total")
        elif field == "time.total":
            actual = time.get("total")
        elif field == "cost.one_time":
            actual = cost.get("one_time")
        elif field == "cost.per_run":
            actual = cost.get("per_run")
        elif field == "time.execution":
            actual = time.get("parts", {}).get("execution")
        if actual is None:
            continue
        if not _op_ok(actual, con.get("op", "<="), _float_or(value)):
            violations.append({
                "id": con.get("id"),
                "field": field,
                "actual": actual,
                "op": con.get("op", "<="),
                "limit": value,
                "source": con.get("source"),
            })
    return violations


def compute_scores(candidates, weights):
    w = {d: _float_or((weights or {}).get(d), DEFAULT_WEIGHTS.get(d, 0.0)) for d in DIMENSIONS}
    dims = [d for d in DIMENSIONS if d in (weights or {}) and all(d in (c.get("dimension_ratings") or {}) for c in candidates)]
    if not dims:
        dims = [d for d in DIMENSIONS if d in (weights or {})] or list(DIMENSIONS)
    wsum = sum(max(0.0, w.get(d, 0.0)) for d in dims) or 1.0
    w_eff = {d: max(0.0, w.get(d, 0.0)) / wsum for d in dims}
    scores = {}
    for c in candidates:
        r = c.get("dimension_ratings") or {}
        scores[c["plan_id"]] = round(sum(w_eff[d] * _clamp_rating(r.get(d)) for d in dims), 3)
    return scores, w_eff, dims


def rank_candidates(candidates, scores):
    items = sorted((scores.get(c["plan_id"], 0.0), c["plan_id"]) for c in candidates)
    items.sort(key=lambda t: (-t[0], t[1]))
    return [pid for _s, pid in items]


def compute_sensitivity(candidates, weights, dims, chosen):
    scores = {c["plan_id"]: c["weighted_score"] for c in candidates}
    base = rank_candidates(candidates, scores)
    pert = []
    stable = True
    for d in dims:
        for delta in (0.05, -0.05):
            w2 = {k: _float_or(v) for k, v in (weights or {}).items()}
            w2[d] = max(0.0, w2.get(d, 0.0) + delta)
            wsum = sum(max(0.0, w2.get(x, 0.0)) for x in dims) or 1.0
            wn = {x: max(0.0, w2.get(x, 0.0)) / wsum for x in dims}
            scores2 = {}
            for c in candidates:
                r = c.get("dimension_ratings") or {}
                scores2[c["plan_id"]] = round(sum(wn[x] * _clamp_rating(r.get(x)) for x in dims), 3)
            rk2 = rank_candidates(candidates, scores2)
            top = rk2[0] if rk2 else None
            pert.append({"dimension": d, "delta": delta, "top": top, "ranking": rk2})
            if chosen and top != chosen:
                stable = False
    return {"stable": bool(stable), "base_ranking": base, "perturbations": pert,
            "note": "每个维度权重单独 ±0.05 后重新归一化，观察首选是否变化"}


def build_matrix(candidates, scores, dims):
    columns = [c["plan_id"] for c in candidates]
    rows = []
    for d in dims:
        rows.append({"label": DIM_CN.get(d, d), "key": d, "values": [c.get("dimension_ratings", {}).get(d) for c in candidates]})
    rows.append({"label": "加权得分", "key": "weighted_score", "values": [c.get("weighted_score") for c in candidates]})
    rows.append({"label": "成本(CNY)", "key": "cost_total", "values": [c.get("cost", {}).get("total") for c in candidates]})
    rows.append({"label": "时间(h)", "key": "time_total", "values": [c.get("time", {}).get("total") for c in candidates]})
    rows.append({"label": "可行", "key": "feasible", "values": [c.get("feasible") for c in candidates]})
    rows.append({"label": "排名", "key": "rank", "values": [c.get("rank") for c in candidates]})
    return {"row_labels": [r["label"] for r in rows], "columns": columns, "rows": rows, "criteria": list(dims)}


def final_status(analysis, candidates, chosen, infeasible_all, errors):
    base = analysis.get("status")
    if base in ("input_error", "insufficient_evidence"):
        return base
    if not candidates:
        return "insufficient_evidence"
    if infeasible_all or not chosen:
        return "partial"
    confirmations = analysis.get("confirmations") or []
    pending_conf = any(str(c.get("status")) == "pending" for c in confirmations)
    chosen_c = next((c for c in candidates if c["plan_id"] == chosen), None)
    missing_ev = bool(chosen_c) and not (chosen_c.get("evidence") or [])
    if pending_conf or missing_ev or errors:
        return "partial"
    return "ok"


def merge_selection(sel, sensitivity):
    out = dict(sel or {})
    out.setdefault("chosen", None)
    out.setdefault("ranking", [])
    out.setdefault("rationale", None)
    out.setdefault("rejected", [])
    out["sensitivity"] = sensitivity
    out["script_ranking"] = sensitivity.get("base_ranking", [])
    chosen = out.get("chosen")
    base = sensitivity.get("base_ranking") or []
    out["rank_agreement"] = (base[0] == chosen) if (chosen and base) else None
    return out


def build_result(pack, analysis, candidates, status, matrix, sensitivity, artifacts, errors, auto_limits):
    result = {
        "case_id": analysis.get("case_id") or pack.get("case_id"),
        "status": status,
        "research_question": analysis.get("research_question") or {},
        "comparison_frame": analysis.get("comparison_frame") or {},
        "candidates": candidates,
        "comparison_matrix": matrix,
        "selection": merge_selection(analysis.get("selection"), sensitivity),
        "confirmations": analysis.get("confirmations") or [],
        "sources": analysis.get("sources") or [],
        "uncertainty": analysis.get("uncertainty") or [],
        "pending_human": analysis.get("pending_human") or [],
        "limitations": list(analysis.get("limitations") or []) + auto_limits,
        "change_log": list(analysis.get("change_log") or []) + [{"time": now_iso(), "step": "assemble", "note": "final result assembled"}],
        "errors": list(errors) + list(analysis.get("errors") or []),
        "degradations": analysis.get("degradations") or [],
        "runtime": {"started": now_iso(), "seconds": 0, "script_version": VERSION},
        "artifacts": artifacts,
    }
    fills = analysis.get("contract_fills") or {}
    for k in (pack.get("output_contract") or {}):
        if k not in result:
            result[k] = fills.get(k)
    return result


# --------------------------------------------------------------------------- 产物渲染

def _score_color(v):
    x = _clamp_rating(v) if isinstance(v, (int, float)) else _clamp_rating(None)
    if x >= 4:
        return "#1e7d32"
    if x >= 3:
        return "#7a6a00"
    if x >= 2:
        return "#b26a00"
    return "#b3261e"


def _svg_text(x, y, text, fill, size, anchor="start"):
    return '<text x="%d" y="%d" fill="%s" font-size="%d" text-anchor="%s">%s</text>' % (x, y, fill, size, anchor, _xml(text))


def write_csv(path, matrix):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["field"] + matrix["columns"])
        for r in matrix["rows"]:
            w.writerow([r["label"]] + [_fmt_cell(v) for v in r["values"]])


def write_svg(path, title, matrix):
    cols, rows = matrix["columns"], matrix["rows"]
    label_w, col_w, row_h, title_h, head_h = 170, 110, 30, 34, 30
    W = label_w + col_w * len(cols)
    H = title_h + head_h + row_h * len(rows)
    p = []
    p.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="Arial, sans-serif">' % (W, H, W, H))
    p.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
    p.append('<rect width="%d" height="%d" fill="#1f4e78"/>' % (W, title_h))
    p.append(_svg_text(12, int(title_h / 2) + 5, title, "#ffffff", 15))
    y = title_h
    p.append('<rect y="%d" width="%d" height="%d" fill="#d9e2f3"/>' % (y, W, head_h))
    p.append(_svg_text(12, y + int(head_h / 2) + 5, "维度 / 指标", "#1f4e78", 11))
    for j, cname in enumerate(cols):
        p.append(_svg_text(label_w + col_w * j + 8, y + int(head_h / 2) + 5, cname, "#1f4e78", 12))
    y += head_h
    for i, r in enumerate(rows):
        fill = "#f4f6fa" if i % 2 == 0 else "#ffffff"
        p.append('<rect y="%d" width="%d" height="%d" fill="%s"/>' % (y, W, row_h, fill))
        p.append(_svg_text(12, y + int(row_h / 2) + 5, r["label"], "#222222", 11))
        for j, v in enumerate(r["values"]):
            color = "#222222"
            if r.get("key") in DIMENSIONS and isinstance(v, (int, float)):
                color = _score_color(v)
            p.append(_svg_text(label_w + col_w * j + 8, y + int(row_h / 2) + 5, _fmt_cell(v), color, 12))
        y += row_h
    p.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c9d3e0"/>' % (label_w, title_h + head_h, label_w, H))
    for j in range(1, len(cols)):
        x = label_w + col_w * j
        p.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c9d3e0"/>' % (x, title_h + head_h, x, H))
    for i in range(1, len(rows) + 1):
        yy = title_h + head_h + row_h * i
        p.append('<line x1="0" y1="%d" x2="%d" y2="%d" stroke="#c9d3e0"/>' % (yy, W, yy))
    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p))


def write_md(path, pack, analysis, candidates, matrix, selection, status):
    L = []
    L.append("# 实验方案比较与选择")
    L.append("")
    L.append("- case_id: `%s`" % (pack.get("case_id")))
    L.append("- 运行状态: `%s`" % status)
    L.append("")
    rq = analysis.get("research_question") or {}
    L.append("## 1. 研究问题与变量")
    L.append("")
    L.append("- 可检验问题: %s" % _md_cell(rq.get("testable_question") or "未填写"))
    vars_ = rq.get("variables") or {}
    L.append("- 自变量: %s" % _md_cell(", ".join(vars_.get("independent") or []) or "未填写"))
    L.append("- 因变量: %s" % _md_cell(", ".join(vars_.get("dependent") or []) or "未填写"))
    L.append("- 控制变量: %s" % _md_cell(", ".join(vars_.get("controlled") or []) or "未填写"))
    L.append("- 假设: %s" % _md_cell(rq.get("hypothesis") or "未填写"))
    L.append("")
    frame = analysis.get("comparison_frame") or {}
    L.append("## 2. 比较框架与硬约束")
    L.append("")
    for obj in (frame.get("objectives") or []):
        L.append("- 目标: %s" % _md_cell(obj))
    for con in (frame.get("hard_constraints") or []):
        L.append("- 硬约束 `%s`: `%s` %s %s%s" % (con.get("id"), con.get("field"), con.get("op"), con.get("value"), (" " + con.get("unit")) if con.get("unit") else ""))
    L.append("")
    L.append("## 3. 可比矩阵（五维评分 + 成本 + 时间 + 可行性）")
    L.append("")
    L.append("| 字段 | " + " | ".join(_md_cell(c) for c in matrix["columns"]) + " |")
    L.append("|" + "---|" * (len(matrix["columns"]) + 1))
    for r in matrix["rows"]:
        L.append("| %s | %s |" % (r["label"], " | ".join(_fmt_cell(v) for v in r["values"])))
    L.append("")
    L.append("## 4. 七类字段（逐方案）")
    L.append("")
    L.append("| 字段 | " + " | ".join(_md_cell(c) for c in matrix["columns"]) + " |")
    L.append("|" + "---|" * (len(matrix["columns"]) + 1))
    for f in SEVEN_FIELDS:
        cells = []
        for c in candidates:
            cells.append(_md_cell((c.get("seven_fields") or {}).get(f) or "未填写"))
        L.append("| %s | %s |" % (FIELD_CN[f], " | ".join(cells)))
    L.append("")
    L.append("## 5. 成本与时间")
    L.append("")
    L.append("| 方案 | 成本 | 成本口径 | 时间 | 时间口径 |")
    L.append("|---|---|---|---|---|")
    for c in candidates:
        cost = c.get("cost") or {}
        tm = c.get("time") or {}
        L.append("| %s | %s %s | %s | %s %s | %s |" % (
            c.get("plan_id"), _fmt_cell(cost.get("total")), cost.get("currency") or "",
            cost.get("method") or "", _fmt_cell(tm.get("total")), tm.get("unit") or "h", tm.get("method") or ""))
    L.append("")
    L.append("## 6. 风险与异常")
    L.append("")
    for c in candidates:
        L.append("### %s" % c.get("plan_id"))
        for rk in (c.get("risks") or []):
            L.append("- 触发: %s → 后果: %s（量级 %s）；缓解: %s；回退: %s" % (
                _md_cell(rk.get("trigger")), _md_cell(rk.get("consequence")), _md_cell(rk.get("magnitude")),
                _md_cell(rk.get("mitigation")), _md_cell(rk.get("fallback"))))
        if not (c.get("risks") or []):
            L.append("- 未填写风险项")
        L.append("")
    L.append("## 7. 证据溯源")
    L.append("")
    for c in candidates:
        for ev in (c.get("evidence") or []):
            L.append("- `%s` · %s · `%s` · 引用: %s" % (c.get("plan_id"), ev.get("claim") or "", ev.get("kind") or "", _md_cell(ev.get("quote") or "")))
    L.append("")
    L.append("## 8. 选择结论")
    L.append("")
    L.append("- 选择: `%s`" % (selection.get("chosen")))
    L.append("- 排序: %s" % (", ".join(selection.get("ranking") or [])))
    L.append("- 理由: %s" % _md_cell(selection.get("rationale") or "未填写"))
    for rj in (selection.get("rejected") or []):
        L.append("- 舍弃 `%s`: %s" % (rj.get("plan_id"), _md_cell(rj.get("reason"))))
    L.append("- 敏感性: %s（首选稳定: %s）" % (selection.get("sensitivity", {}).get("note", ""), selection.get("sensitivity", {}).get("stable")))
    L.append("")
    L.append("## 9. 确认项与待办")
    L.append("")
    for cf in (analysis.get("confirmations") or []):
        L.append("- [%s] %s（%s）: %s" % (cf.get("status"), cf.get("item"), cf.get("type"), _md_cell(cf.get("note"))))
    for ph in (analysis.get("pending_human") or []):
        L.append("- 待人工: %s" % _md_cell(ph))
    L.append("")
    L.append("## 10. 不确定性与限制")
    L.append("")
    for u in (analysis.get("uncertainty") or []):
        L.append("- %s" % _md_cell(u))
    for lim in (analysis.get("limitations") or []):
        L.append("- %s" % _md_cell(lim))
    L.append("")
    L.append("---")
    L.append("由 `scripts/main.py assemble` 确定性生成（无随机、排序稳定）。数值须以 result.json 为准。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def make_pdf(path, title, headers, rows):
    content_lines = ["BT /F1 12 Tf 50 770 Td (%s) Tj ET" % _pdf_escape(_ascii(title))]
    content_lines.append("BT /F1 9 Tf 50 750 Td (%s) Tj ET" % _pdf_escape(_ascii(" | ".join(headers))))
    y = 736
    for row in rows:
        content_lines.append("BT /F1 8 Tf 50 %d Td (%s) Tj ET" % (y, _pdf_escape(_ascii(" | ".join(row)))))
        y -= 13
        if y < 50:
            break
    stream = "\n".join(content_lines).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i
        out += obj
        out += b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n" % n
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos)
    with open(path, "wb") as f:
        f.write(bytes(out))


def write_pdf(path, title, matrix):
    headers = ["field"] + matrix["columns"]
    rows = [[r.get("key") or r["label"]] + [_fmt_cell(v) for v in r["values"]] for r in matrix["rows"]]
    make_pdf(path, title, headers, rows)


# --------------------------------------------------------------------------- assemble

def empty_matrix():
    return {"row_labels": [], "columns": [], "rows": [], "criteria": []}


def cmd_assemble(args):
    inp, out, work = resolve_dirs(args)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    started = time.time()
    pack = read_json_file(os.path.join(work, "case_pack.json")) or {}
    analysis = read_json_file(os.path.join(work, "analysis.json"))
    base = read_json_file(os.path.join(out, "result.json")) or {}
    errors = list(base.get("errors") or [])

    if analysis is None:
        result = base or make_skeleton(pack, "partial")
        result["status"] = result.get("status") or "partial"
        result.setdefault("errors", []).append("analysis.json missing under work dir; run inspect then fill analysis.json")
        result.setdefault("pending_human", [])
        if "fill /app/work/analysis.json" not in result["pending_human"]:
            result["pending_human"].append("fill /app/work/analysis.json")
        result["change_log"] = (result.get("change_log") or []) + [{"time": now_iso(), "step": "assemble", "note": "analysis.json missing; kept skeleton"}]
        write_json(os.path.join(out, "result.json"), result)
        print("assemble: analysis.json missing; kept skeleton (partial)")
        return 0

    candidates_in = analysis.get("candidates") or []
    if not candidates_in:
        result = build_result(pack, analysis, [], "insufficient_evidence", empty_matrix(), {"base_ranking": []}, [], errors, ["无候选方案可比较"])
        result["runtime"]["seconds"] = round(time.time() - started, 2)
        result["runtime"]["ended"] = now_iso()
        write_json(os.path.join(out, "result.json"), result)
        print("assemble: no candidates; status=insufficient_evidence")
        return 0

    weights = analysis.get("weights") or dict(DEFAULT_WEIGHTS)
    constraints = (analysis.get("comparison_frame") or {}).get("hard_constraints") or []
    enriched, used = [], set()
    auto_limits = []

    for c in candidates_in:
        pid = str(c.get("plan_id") or "P%d" % (len(enriched) + 1))
        if pid in used:
            pid = "%s_%d" % (pid, len(enriched) + 1)
        used.add(pid)
        cc = dict(c)
        cc["plan_id"] = pid
        cost = compute_cost(c.get("cost"))
        tm = compute_time(c.get("time"))
        cc["cost"], cc["time"] = cost, tm
        if cost.get("method") == "not_computable":
            auto_limits.append("%s: 成本无法核算（%s）" % (pid, cost.get("note", "")))
        if tm.get("method") == "not_computable":
            auto_limits.append("%s: 时间无法核算（%s）" % (pid, tm.get("note", "")))
        violations = check_constraints(cc, constraints, cost, tm)
        explicit = list(c.get("infeasible_reasons") or [])
        equip = (c.get("feasibility") or {}).get("equipment_available", "unknown")
        feasible = (not explicit) and (not violations) and equip != "no"
        cc["feasible"] = bool(feasible)
        cc["constraint_violations"] = violations
        enriched.append(cc)

    scores, w_eff, dims = compute_scores(enriched, weights)
    for cc in enriched:
        cc["weighted_score"] = scores.get(cc["plan_id"])
        cc["effective_dimensions"] = dims
        cc["effective_weights"] = w_eff
    ranking = rank_candidates(enriched, scores)
    for rank, pid in enumerate(ranking, 1):
        for cc in enriched:
            if cc["plan_id"] == pid:
                cc["rank"] = rank
    infeasible_all = not any(cc["feasible"] for cc in enriched)
    chosen = (analysis.get("selection") or {}).get("chosen")
    sensitivity = compute_sensitivity(enriched, weights, dims, chosen)
    matrix = build_matrix(enriched, scores, dims)

    artifacts = ["comparison_matrix.csv", "comparison_matrix.svg", "plan_comparison.md", "comparison_matrix.pdf"]
    write_csv(os.path.join(out, "comparison_matrix.csv"), matrix)
    write_svg(os.path.join(out, "comparison_matrix.svg"), "case %s" % (pack.get("case_id") or "unknown"), matrix)
    write_md(os.path.join(out, "plan_comparison.md"), pack, analysis, enriched, matrix,
             merge_selection(analysis.get("selection"), sensitivity), final_status(analysis, enriched, chosen, infeasible_all, errors))
    write_pdf(os.path.join(out, "comparison_matrix.pdf"), "case %s" % (pack.get("case_id") or "unknown"), matrix)

    status = final_status(analysis, enriched, chosen, infeasible_all, errors)
    result = build_result(pack, analysis, enriched, status, matrix, sensitivity, artifacts, errors, auto_limits)
    result["runtime"]["seconds"] = round(time.time() - started, 2)
    result["runtime"]["ended"] = now_iso()
    write_json(os.path.join(out, "result.json"), result)
    print("assemble: status=%s chosen=%s ranking=%s feasible=%d/%d" %
          (status, chosen, ranking, sum(1 for c in enriched if c["feasible"]), len(enriched)))
    return 0


# --------------------------------------------------------------------------- validate

def validate_result(path):
    issues = []
    if not os.path.isfile(path):
        return ["file missing: %s" % path]
    raw, err = read_text(path)
    if raw is None:
        return ["cannot read: %s" % err]
    r, jerr = parse_json(raw)
    if r is None:
        return ["invalid JSON: %s" % jerr]
    if not r.get("case_id"):
        issues.append("case_id missing")
    if r.get("status") not in STATUSES:
        issues.append("status not in %s" % str(STATUSES))
    for k in ("sources", "uncertainty", "pending_human", "change_log", "limitations"):
        if k not in r:
            issues.append("missing key: %s" % k)
    for c in (r.get("candidates") or []):
        sf = c.get("seven_fields") or {}
        for f in SEVEN_FIELDS:
            if f not in sf:
                issues.append("%s: seven_fields missing %s" % (c.get("plan_id"), f))
        dr = c.get("dimension_ratings") or {}
        for d in DIMENSIONS:
            if d not in dr:
                issues.append("%s: dimension_ratings missing %s" % (c.get("plan_id"), d))
            elif not (0 <= _clamp_rating(dr[d]) <= 5) or dr[d] is None:
                issues.append("%s: rating %s out of 0-5 or None" % (c.get("plan_id"), d))
    sel = r.get("selection") or {}
    for k in ("chosen", "ranking", "rationale", "rejected"):
        if k not in sel:
            issues.append("selection missing %s" % k)
    if "comparison_matrix" not in r:
        issues.append("comparison_matrix missing")
    return issues


def cmd_validate(args):
    path = args.path or os.path.join(resolve_dirs(args)[1], "result.json")
    issues = validate_result(path)
    if issues:
        print("valid=false")
        for i in issues:
            print(" - " + i)
        return 1
    print("valid=true")
    return 0


# --------------------------------------------------------------------------- demo

def demo_case():
    case = {
        "case_id": "demo-plan-001",
        "title": "三种聚合物合成路线比较（示例）",
        "objective": "在 30 天、总预算 8 万元内选择一条证据充分且可执行的合成路线。",
        "output_contract": {"experimental_plan": None, "evidence_and_risks": None, "confirmation_items": None},
        "hard_constraints": [
            {"id": "budget", "field": "cost.total", "op": "<=", "value": 80000, "unit": "CNY", "source": "case.json#hard_constraints"},
            {"id": "deadline", "field": "time.total", "op": "<=", "value": 720, "unit": "h", "source": "case.json#hard_constraints"},
        ],
        "candidates": [{"plan_id": "P1"}, {"plan_id": "P2"}, {"plan_id": "P3"}],
    }
    task = "# 示例任务（demo，非正式案例）\n比较三条路线，给出选择与舍弃理由。本文件为最小调用示例，仅演示接口。\n"
    sf = {f: "示例内容（demo，非真实科研数据）" for f in SEVEN_FIELDS}
    analysis = {
        "case_id": "demo-plan-001",
        "research_question": {
            "testable_question": "在给定预算与工期下，哪条合成路线的综合评分最高且可行（示例）",
            "variables": {"independent": ["路线"], "dependent": ["成本", "时间", "风险"], "controlled": ["预算", "工期"]},
            "hypothesis": "示例假设，不作为真实结论",
        },
        "comparison_frame": {
            "objectives": ["示例：选出可行且证据充分的路线"],
            "hard_constraints": case["hard_constraints"],
            "criteria": list(DIMENSIONS),
        },
        "weights": dict(DEFAULT_WEIGHTS),
        "candidates": [
            {
                "plan_id": "P1", "name": "路线 A（示例）", "version": "v1",
                "seven_fields": dict(sf),
                "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 1, "feasibility": 4, "evidence": 3},
                "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes", "notes": "示例：可行但超预算"},
                "risks": [{"trigger": "示例触发", "consequence": "示例后果", "magnitude": "中", "mitigation": "示例缓解", "fallback": "示例回退"}],
                "evidence": [{"claim": "示例证据", "locator": "demo/materials#example", "quote": "本条目为 demo 示例引用", "kind": "demo"}],
                "infeasible_reasons": [],
                "cost": {"currency": "CNY", "runs": 5, "lines": [
                    {"item": "machine_time", "amount": 30000, "category": "one_time"},
                    {"item": "labor", "amount": 20000, "category": "one_time"},
                    {"item": "sample_prep", "amount": 5000, "category": "one_time"},
                    {"item": "scheduling", "amount": 3000, "category": "one_time"},
                    {"item": "rework", "amount": 7000, "category": "one_time"},
                    {"item": "consumables", "amount": 5000, "category": "per_run"},
                ]},
                "time": {"unit": "h", "parts": {"preparation": 60, "scheduling": 48, "execution": 200, "rework": 60, "other": 20}},
            },
            {
                "plan_id": "P2", "name": "路线 B（示例）", "version": "v1",
                "seven_fields": dict(sf),
                "dimension_ratings": {"goal_fit": 5, "risk": 3, "cost": 4, "feasibility": 4, "evidence": 4},
                "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes", "notes": "示例：首选"},
                "risks": [{"trigger": "示例触发", "consequence": "示例后果", "magnitude": "低", "mitigation": "示例缓解", "fallback": "示例回退"}],
                "evidence": [{"claim": "示例证据", "locator": "demo/materials#example", "quote": "本条目为 demo 示例引用", "kind": "demo"}],
                "infeasible_reasons": [],
                "cost": {"currency": "CNY", "runs": 3, "lines": [
                    {"item": "machine_time", "amount": 25000, "category": "one_time"},
                    {"item": "labor", "amount": 15000, "category": "one_time"},
                    {"item": "sample_prep", "amount": 4000, "category": "one_time"},
                    {"item": "scheduling", "amount": 2000, "category": "one_time"},
                    {"item": "rework", "amount": 3000, "category": "one_time"},
                    {"item": "consumables", "amount": 2000, "category": "per_run"},
                ]},
                "time": {"unit": "h", "parts": {"preparation": 40, "scheduling": 30, "execution": 150, "rework": 30, "other": 10}},
            },
            {
                "plan_id": "P3", "name": "路线 C（示例）", "version": "v1",
                "seven_fields": dict(sf),
                "dimension_ratings": {"goal_fit": 3, "risk": 2, "cost": 4, "feasibility": 1, "evidence": 2},
                "feasibility": {"equipment_available": "no", "lead_time_ok": "no", "notes": "示例：设备不可得，不可行"},
                "risks": [{"trigger": "示例触发", "consequence": "示例后果", "magnitude": "高", "mitigation": "示例缓解", "fallback": "示例回退"}],
                "evidence": [{"claim": "示例证据", "locator": "demo/materials#example", "quote": "本条目为 demo 示例引用", "kind": "demo"}],
                "infeasible_reasons": [{"reason": "设备不可得（demo）", "source": "demo feasibility"}],
                "cost": {"currency": "CNY", "runs": 4, "total": 60000},
                "time": {"unit": "h", "parts": {"preparation": 50, "scheduling": 40, "execution": 180, "rework": 40, "other": 20}},
            },
        ],
        "selection": {
            "chosen": "P2", "ranking": ["P2", "P1", "P3"],
            "rationale": "示例：P2 可行、成本与工期均在约束内、证据较充分（demo）。",
            "rejected": [{"plan_id": "P1", "reason": "示例：超预算（demo）"}, {"plan_id": "P3", "reason": "示例：设备不可得（demo）"}],
        },
        "confirmations": [{"item": "示例确认项", "type": "expert", "status": "confirmed", "note": "demo 已确认"}],
        "sources": [{"locator": "demo/README#note", "quote": "demo 示例", "kind": "demo"}],
        "uncertainty": ["demo：所有内容均为示例，不代表真实科研结论"],
        "limitations": ["demo 示例，无真实材料"],
        "contract_fills": {
            "experimental_plan": {"assumptions": "demo", "variables": "demo", "controls": "demo", "steps": "demo", "parameters": "demo", "resources": "demo", "interpretation_criteria": "demo"},
            "evidence_and_risks": {"evidence": "demo", "risks": "demo"},
            "confirmation_items": ["demo"],
        },
        "change_log": [{"time": now_iso(), "step": "demo", "note": "示例分析写入"}],
        "errors": [], "degradations": [], "pending_human": [],
    }
    return {"case": case, "task": task, "analysis": analysis}


def _write_demo_files(inp, work):
    demo = demo_case()
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(demo["case"], f, ensure_ascii=False, indent=2)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write(demo["task"])
    with open(os.path.join(work, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(demo["analysis"], f, ensure_ascii=False, indent=2)


def cmd_demo(args):
    tmp = tempfile.mkdtemp(prefix="skill_demo_")
    inp, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(inp), os.makedirs(out), os.makedirs(work)
    _write_demo_files(inp, work)
    a = argparse.Namespace(input=inp, output=out, work=work, path=None)
    cmd_inspect(a)
    cmd_assemble(a)
    rc = cmd_validate(argparse.Namespace(input=inp, output=out, work=work, path=os.path.join(out, "result.json")))
    print("demo output dir: " + out)
    print("NOTE: 以上为示例输出（demo），非正式案例结果。")
    return rc


# --------------------------------------------------------------------------- selftest

STDLIB_FALLBACK = {
    "argparse", "csv", "json", "os", "re", "sys", "tempfile", "time", "xml", "io", "math", "string",
}


def _stdlib_only(path, allowed_local=()):
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return False
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else STDLIB_FALLBACK
    for m in re.findall(r"^\s*(?:from\s+([a-zA-Z_][\w]*)\s+import|import\s+([a-zA-Z_][\w]*))", src, re.M):
        name = m[0] or m[1]
        if name == "__future__" or name in allowed_local:
            continue
        if name in STDLIB_FALLBACK or name in stdlib:
            continue
        return False
    return True


def _strip_runtime(obj):
    if isinstance(obj, dict):
        return {k: _strip_runtime(v) for k, v in obj.items() if k not in ("runtime", "change_log")}
    if isinstance(obj, list):
        return [_strip_runtime(v) for v in obj]
    return obj


def cmd_selftest(args=None):
    ok = True

    def report(name, cond, detail=""):
        nonlocal ok
        print("[PASS] " + name if cond else "[FAIL] " + name, (" — " + detail) if detail else "")
        if not cond:
            ok = False
        return cond

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(root, "scripts", "main.py")
    core_py = os.path.join(root, "scripts", "core_impl.py")
    report("脚本仅标准库", _stdlib_only(core_py, {"core_impl"}) and _stdlib_only(main_py, {"core_impl"}))

    tmp = tempfile.mkdtemp(prefix="skill_selftest_demo_")
    inp, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(inp), os.makedirs(out), os.makedirs(work)
    _write_demo_files(inp, work)
    a = argparse.Namespace(input=inp, output=out, work=work, path=None)
    rc1 = cmd_inspect(a)
    rc2 = cmd_assemble(a)
    res_path = os.path.join(out, "result.json")
    issues = validate_result(res_path)
    report("demo inspect+assemble 跑通", rc1 == 0 and rc2 == 0)
    report("demo result.json 结构合法", not issues, "; ".join(issues[:4]))

    for fn in ("comparison_matrix.csv", "comparison_matrix.svg", "plan_comparison.md", "comparison_matrix.pdf"):
        report("产物 %s 存在" % fn, os.path.isfile(os.path.join(out, fn)))
    try:
        ET.parse(os.path.join(out, "comparison_matrix.svg"))
        report("SVG 可解析", True)
    except Exception as e:  # noqa: BLE001
        report("SVG 可解析", False, str(e))
    with open(os.path.join(out, "comparison_matrix.pdf"), "rb") as f:
        head = f.read(5)
    report("PDF 以 %PDF- 开头", head == b"%PDF-")

    tmp2 = tempfile.mkdtemp(prefix="skill_selftest_empty_")
    a2 = argparse.Namespace(input=os.path.join(tmp2, "missing"), output=os.path.join(tmp2, "out"), work=os.path.join(tmp2, "work"), path=None)
    rc_empty = cmd_inspect(a2)
    r2 = read_json_file(os.path.join(a2.output, "result.json"))
    report("空输入退出0且 status=input_error", rc_empty == 0 and bool(r2) and r2.get("status") == "input_error")

    tmp3 = tempfile.mkdtemp(prefix="skill_selftest_bad_")
    os.makedirs(os.path.join(tmp3, "input"))
    with open(os.path.join(tmp3, "input", "case.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json ")
    a3 = argparse.Namespace(input=os.path.join(tmp3, "input"), output=os.path.join(tmp3, "out"), work=os.path.join(tmp3, "work"), path=None)
    rc_bad = cmd_inspect(a3)
    r3 = read_json_file(os.path.join(a3.output, "result.json"))
    report("坏 JSON 不崩溃且如实标注", rc_bad == 0 and bool(r3) and r3.get("status") == "input_error")

    d1 = read_json_file(res_path)
    cmd_assemble(a)
    d2 = read_json_file(res_path)
    report("重复 assemble 结果一致（去时间戳）", _strip_runtime(d1) == _strip_runtime(d2))

    print("selftest: " + ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


# --------------------------------------------------------------------------- 入口

def print_help():
    print("""usage: python scripts/main.py <command> [--input DIR] [--output DIR] [--work DIR] [result.json]

commands:
  inspect   解析 /input，先落盘合法 result.json 骨架 + work/case_pack.json
  assemble  读取 work/analysis.json，做算术/淘汰/评分/敏感性，渲染全部产物
  validate  校验 result.json 结构与输出契约（目标文件作为最后一个位置参数）
  selftest  自检：仅标准库、demo、空输入、坏 JSON、确定性、产物合法性
  demo      在临时目录生成并跑通一个标注为「示例」的最小案例

默认路径: /input、/app/output、/app/work；本地无这些目录时回退到 ./demo/*
仅标准库；确定性（无随机、排序稳定）。""")


def main(argv):
    cmd = argv[0] if argv else "--help"
    opts = {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work") and i + 1 < len(argv):
            opts[a[2:]] = argv[i + 1]
            i += 2
        elif a in ("--help", "-h"):
            cmd = "--help"
            i += 1
        else:
            opts["path"] = a
            i += 1
    args = argparse.Namespace(
        input=opts.get("input"), output=opts.get("output"), work=opts.get("work"), path=opts.get("path"))
    if cmd in ("--help", "-h") or cmd not in ("inspect", "assemble", "validate", "selftest", "demo"):
        print_help()
        return 0
    return {
        "inspect": cmd_inspect, "assemble": cmd_assemble, "validate": cmd_validate,
        "selftest": cmd_selftest, "demo": cmd_demo,
    }[cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
