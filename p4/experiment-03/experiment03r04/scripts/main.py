#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill —— 编排器（仅标准库）。

子命令
    init      发现输入文件，第一时间落盘一版 partial 结果骨架
    finalize  合并 /app/work/plan.json -> /app/output/result.json，并渲染 plan.md / plan.json
    doe       由 plan.json 确定性生成候选参数点
    check     校验 result.json 契约与产物文件存在性

默认：--input /input --output /app/output --work /app/work；本地自测可覆盖路径。
设计取向（变体“降级与不确定性”）：每个数值都要可溯源或显式标注证据等级；
建议值越界（设备量程/材料限/安全阈值）由 validate_plan 判定为阻断错误，不做静默修正。
"""

import argparse
import json
import os
import random
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_INPUT_BYTES = 2 * 1024 * 1024
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
EVIDENCE_ENUM = ("quoted", "derived", "assumed", "pending")
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log", ".bib"}

# plan.json 中属于“方案内容”的键（进入 result.json["plan"]）
PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
# plan.json 中属于“元数据”的键（result.json 顶层）
PLAN_META_KEYS = ["title", "sources", "uncertainty", "limitations",
                  "pending_human", "change_log"]
REQUIRED_PLAN_KEYS = PLAN_CONTENT_KEYS
REQUIRED_PARAM_KEYS = ("name", "unit", "suggested_value", "min", "max")
REQUIRED_METRIC_KEYS = ("name", "unit", "method", "direction", "target")
HARD_LIMIT_LABELS = (("device_limit", "设备量程"),
                     ("material_limit", "材料限"),
                     ("safety_limit", "安全阈值"))


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_text_safe(path):
    """容错读取文本：utf-8-sig -> utf-8 -> latin-1，失败返回 None。"""
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
        return None, "cannot read file"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:  # noqa: BLE001
        return None, "parse error: %s" % exc


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def _num(value):
    """把 int/float 转 float；bool 与其他不可数值返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# --------------------------------------------------------------------------- #
# 输入发现
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
            rel = os.path.relpath(abs_path, input_dir)
            if size > MAX_INPUT_BYTES:
                found.append((rel, abs_path, size, "skipped:too_large"))
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            found.append((rel, abs_path, size, kind))
    return found


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "case", "id", "challenge_id",
                    "challengeId", "armiId"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def make_input_digest(input_dir):
    files = discover_inputs(input_dir)
    digest = {
        "input_dir": input_dir,
        "files": [],
        "case_id": None,
        "case_json_error": None,
        "task_md_preview": None,
    }
    for rel, abs_path, size, kind in files:
        entry = {"path": rel, "size": size, "kind": kind}
        if kind == "text":
            text = read_text_safe(abs_path)
            if text is None:
                entry["readable"] = False
            else:
                entry["readable"] = True
                entry["preview"] = text[:2000]
        digest["files"].append(entry)

    for entry in digest["files"]:
        base = os.path.basename(entry["path"]).lower()
        if base in ("case.json", "input.json"):
            obj, err = json_load_safe(os.path.join(input_dir, entry["path"]))
            digest["case_json_error"] = err
            if obj is not None:
                digest["case_id"] = extract_case_id(obj)
        if base in ("task.md", "task.txt", "readme.md"):
            text = read_text_safe(os.path.join(input_dir, entry["path"]))
            if text is not None:
                digest["task_md_preview"] = text[:2000]
    return digest


# --------------------------------------------------------------------------- #
# 骨架与契约对象
# --------------------------------------------------------------------------- #
def new_skeleton_plan(case_id):
    return {
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
        "sources": [],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumptions": [], "model_notes": ""},
        "limitations": [],
        "pending_human": [],
        "change_log": [{"at": utcnow(),
                       "entry": "骨架落盘：仅含输入标识与空字段，等待增量填充"}],
    }


def new_skeleton_result(case_id, status="partial"):
    plan = new_skeleton_plan(case_id)
    plan["status"] = status
    started = utcnow()
    return {
        "case_id": case_id or "",
        "status": status,
        "title": "",
        "plan": {k: plan[k] for k in PLAN_CONTENT_KEYS},
        "artifacts": [],
        "sources": [],
        "uncertainty": plan["uncertainty"],
        "limitations": [],
        "pending_human": [],
        "change_log": plan["change_log"],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": started, "finished_at": None,
                    "seconds": None, "model_requests": None},
    }


# --------------------------------------------------------------------------- #
# 校验：plan.json
# --------------------------------------------------------------------------- #
def _evidence_ok(item):
    """有逐字来源，或显式标注 assumed/pending 时视为可解释。"""
    src = (item.get("constraint_source") or item.get("locator") or "").strip()
    quote = (item.get("quote") or "").strip()
    level = (item.get("evidence_level") or "").strip().lower()
    return (src and quote) or level in ("assumed", "pending", "derived")


def validate_parameter(p, idx, errors, warnings):
    name = p.get("name") or ("参数#%d" % (idx + 1))
    for key in REQUIRED_PARAM_KEYS:
        if p.get(key) in (None, ""):
            errors.append("[参数:%s] 缺少字段 %s" % (name, key))
    sv, lo, hi = _num(p.get("suggested_value")), _num(p.get("min")), _num(p.get("max"))
    if None not in (lo, hi) and lo > hi:
        errors.append("[参数:%s] 可行区间无效：min=%s > max=%s" % (name, lo, hi))
    if None not in (sv, lo, hi) and not (lo - 1e-9 <= sv <= hi + 1e-9):
        errors.append("[参数:%s] 建议值 %s 越出可行区间 [%s, %s]" % (name, sv, lo, hi))
    for lim_key, lim_label in HARD_LIMIT_LABELS:
        lim = p.get(lim_key)
        if not isinstance(lim, dict):
            continue
        llo, lhi = _num(lim.get("min")), _num(lim.get("max"))
        if sv is not None and lhi is not None and sv > lhi + 1e-9:
            errors.append("[参数:%s] 建议值 %s 超出%s上限 %s" % (name, sv, lim_label, lhi))
        if sv is not None and llo is not None and sv < llo - 1e-9:
            errors.append("[参数:%s] 建议值 %s 低于%s下限 %s" % (name, sv, lim_label, llo))
        if lo is not None and lhi is not None and lo > lhi + 1e-9:
            errors.append("[参数:%s] 可行区间下限 %s 超出%s上限 %s" % (name, lo, lim_label, lhi))
        if hi is not None and llo is not None and hi < llo - 1e-9:
            errors.append("[参数:%s] 可行区间上限 %s 低于%s下限 %s" % (name, hi, lim_label, llo))
    if not _evidence_ok(p):
        warnings.append("[参数:%s] 建议值缺少逐字来源且未标注 evidence_level=assumed/pending" % name)


def validate_metric(m, idx, errors, warnings):
    name = m.get("name") or ("响应指标#%d" % (idx + 1))
    for key in REQUIRED_METRIC_KEYS:
        if m.get(key) in (None, ""):
            errors.append("[指标:%s] 缺少字段 %s" % (name, key))
    if not _evidence_ok(m):
        warnings.append("[指标:%s] 口径缺少逐字来源且未标注 evidence_level" % name)


def validate_plan_obj(plan):
    errors, warnings = [], []
    for key in REQUIRED_PLAN_KEYS:
        if key not in plan:
            errors.append("缺少方案字段 %s" % key)
    for idx, p in enumerate(plan.get("parameters") or []):
        validate_parameter(p, idx, errors, warnings)
    for idx, m in enumerate(plan.get("response_metrics") or []):
        validate_metric(m, idx, errors, warnings)
    ss = plan.get("search_strategy")
    if not isinstance(ss, dict):
        errors.append("search_strategy 必须是对象")
    else:
        if not (ss.get("method") or "").strip():
            warnings.append("search_strategy.method 为空")
        if not isinstance(ss.get("stop_criteria"), list) or not ss["stop_criteria"]:
            warnings.append("search_strategy.stop_criteria 为空，收敛/停止判据缺失")
    cb = plan.get("cost_budget")
    if not isinstance(cb, dict) or not isinstance(cb.get("items"), list):
        errors.append("cost_budget.items 必须是列表")
    return errors, warnings


def validate_plan_file(path):
    obj, err = json_load_safe(path)
    if obj is None:
        return {"ok": False, "errors": ["无法读取 plan.json: %s" % err],
                "warnings": [], "exit_code": 1}
    if not isinstance(obj, dict):
        return {"ok": False, "errors": ["plan.json 顶层必须是对象"],
                "warnings": [], "exit_code": 1}
    errors, warnings = validate_plan_obj(obj)
    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "exit_code": 1 if errors else (2 if warnings else 0),
        "checked": {
            "parameters": len(obj.get("parameters") or []),
            "response_metrics": len(obj.get("response_metrics") or []),
            "sources": len(obj.get("sources") or []),
            "limitations": len(obj.get("limitations") or []),
            "pending_human": len(obj.get("pending_human") or []),
        },
    }
    return report


# --------------------------------------------------------------------------- #
# 确定性候选点生成
# --------------------------------------------------------------------------- #
def _candidate_values(p):
    lo, hi = _num(p.get("min")), _num(p.get("max"))
    sv = _num(p.get("suggested_value"))
    hard_lo = hard_hi = None
    for lim_key, _label in HARD_LIMIT_LABELS:
        lim = p.get(lim_key)
        if isinstance(lim, dict):
            v = _num(lim.get("min"))
            if v is not None and (hard_lo is None or v > hard_lo):
                hard_lo = v
            v = _num(lim.get("max"))
            if v is not None and (hard_hi is None or v < hard_hi):
                hard_hi = v
    if hard_lo is not None:
        lo = max(lo, hard_lo) if lo is not None else hard_lo
    if hard_hi is not None:
        hi = min(hi, hard_hi) if hi is not None else hard_hi
    if lo is None or hi is None:
        return [sv] if sv is not None else []
    if lo > hi:
        return []
    step = _num(p.get("step"))
    if step is None or step <= 0:
        vals = [lo, hi]
        if sv is not None and lo <= sv <= hi:
            vals.append(sv)
    else:
        vals, v = [], lo
        while v <= hi + 1e-9:
            vals.append(v)
            v += step
        if vals and abs(vals[-1] - hi) > 1e-9:
            vals.append(hi)
    return sorted({round(v, 8) for v in vals})


def generate_candidates(plan, seed=0, max_points=None):
    params = plan.get("parameters") or []
    ss = plan.get("search_strategy") or {}
    max_points = max_points or ss.get("candidate_count", 8)
    if not isinstance(max_points, int) or max_points < 1:
        max_points = 8
    if not params:
        return {"seed": seed, "method": "none", "points": [],
                "notes": ["plan.parameters 为空，无法生成候选点"]}
    grids = [_candidate_values(p) for p in params]
    for i, g in enumerate(grids):
        if not g:
            sv = _num(params[i].get("suggested_value"))
            grids[i] = [sv] if sv is not None else [None]
    total = 1
    for g in grids:
        total *= len(g)
    rng = random.Random(seed)
    indices = list(range(total))
    method = "full_factorial"
    if total > max_points:
        indices = rng.sample(indices, max_points)
        method = "deterministic_stratified_sample"
    points = []
    for k, idx in enumerate(indices):
        combo, rem = [], idx
        for g in reversed(grids):
            n = len(g)
            combo.append(g[rem % n])
            rem //= n
        combo = list(reversed(combo))
        point = {"id": "cand-%04d" % (k + 1), "parameters": {},
                 "feasible": True, "blocked_by": []}
        for p, v in zip(params, combo):
            pname = p.get("name") or ("param%d" % (k + 1))
            point["parameters"][pname] = v
            if v is None:
                point["feasible"] = False
                point["blocked_by"].append(pname + ":无可用值")
                continue
            for lim_key, lim_label in HARD_LIMIT_LABELS:
                lim = p.get(lim_key)
                if not isinstance(lim, dict):
                    continue
                llo, lhi = _num(lim.get("min")), _num(lim.get("max"))
                if lhi is not None and v > lhi + 1e-9:
                    point["feasible"] = False
                    point["blocked_by"].append(pname + ":超" + lim_label)
                if llo is not None and v < llo - 1e-9:
                    point["feasible"] = False
                    point["blocked_by"].append(pname + ":低于" + lim_label)
        points.append(point)
    feasible_n = sum(1 for pt in points if pt["feasible"])
    return {"seed": seed, "method": method, "total_grid": total,
            "points": points,
            "feasible_count": feasible_n,
            "notes": [] if feasible_n == len(points)
            else ["存在被硬约束拦截的候选点，见 blocked_by"]}


# --------------------------------------------------------------------------- #
# 渲染 plan.md
# --------------------------------------------------------------------------- #
def _md_lines(plan):
    L = []
    add = L.append
    add("# %s" % (plan.get("title") or "实验参数优化方案"))
    add("")
    add("- case_id: `%s`" % plan.get("case_id", ""))
    add("- status: `%s`" % plan.get("status", "partial"))
    add("")
    if plan.get("research_question"):
        add("## 研究问题"); add(""); add(str(plan["research_question"])); add("")
    if plan.get("objective"):
        add("## 目标"); add(""); add(str(plan["objective"])); add("")
    for sec, key in (("假设", "assumptions"), ("变量", "variables"),
                     ("对照", "controls"), ("步骤", "procedures"),
                     ("资源", "resources"), ("判读标准", "interpretation_criteria"),
                     ("依据", "basis"), ("风险", "risks"),
                     ("异常处置", "exception_handling"), ("确认项", "confirmations")):
        items = plan.get(key) or []
        add("## %s" % sec); add("")
        if not items:
            add("（待补充）"); add("")
        else:
            for it in items:
                add("- %s" % _stringify(it)); add("")
    params = plan.get("parameters") or []
    add("## 关键参数"); add("")
    if not params:
        add("（待补充）"); add("")
    else:
        add("| 参数 | 单位 | 建议值 | 可行区间 | 步进 | 证据等级 | 约束来源 |")
        add("|---|---|---|---|---|---|---|")
        for p in params:
            lo, hi = p.get("min"), p.get("max")
            rng = "[%s, %s]" % (lo, hi) if lo is not None and hi is not None else "-"
            add("| %s | %s | %s | %s | %s | %s | %s |" % (
                p.get("name", ""), p.get("unit", ""), p.get("suggested_value", ""),
                rng, p.get("step", ""), p.get("evidence_level", ""),
                (p.get("constraint_source") or "")))
        add("")
    metrics = plan.get("response_metrics") or []
    add("## 响应指标"); add("")
    if not metrics:
        add("（待补充）"); add("")
    else:
        add("| 指标 | 单位 | 方法 | 方向 | 目标 | 可接受下限 |")
        add("|---|---|---|---|---|---|")
        for m in metrics:
            add("| %s | %s | %s | %s | %s | %s |" % (
                m.get("name", ""), m.get("unit", ""), m.get("method", ""),
                m.get("direction", ""), m.get("target", ""),
                m.get("acceptable_lower", "")))
        add("")
    ss = plan.get("search_strategy") or {}
    add("## 搜索策略"); add("")
    add("- 方法：%s" % (ss.get("method") or "（待补充）"))
    add("- 初始点：%s" % (_stringify(ss.get("initial_points")) or "（待补充）"))
    add("- 每轮补点：%s" % (ss.get("per_round") or "（待补充）"))
    add("- 停止判据：%s" % (_stringify(ss.get("stop_criteria")) or "（待补充）"))
    add("")
    cb = plan.get("cost_budget") or {}
    add("## 成本与时间预算"); add("")
    if cb.get("items"):
        for it in cb["items"]:
            add("- %s" % _stringify(it))
    add("- 总成本口径：%s" % (cb.get("total") or "（待补充）"))
    th = cb.get("time_hours")
    add("- 预计时间（小时）：%s" % (th if th is not None else "（待补充）"))
    add("- 取舍说明：%s" % (cb.get("tradeoff_note") or "（待补充）"))
    add("")
    add("## 不确定性与限制"); add("")
    for it in plan.get("limitations") or []:
        add("- %s" % _stringify(it)); add("")
    un = plan.get("uncertainty") or {}
    add("- 未验证值：%s" % _stringify(un.get("unverified_values")))
    add("- 缺失数据：%s" % _stringify(un.get("missing_data")))
    add("- 预测方式说明：%s" % (un.get("model_notes") or "（待补充）"))
    add("")
    ph = plan.get("pending_human") or []
    add("## 待人工确认"); add("")
    for it in ph:
        add("- %s" % _stringify(it)); add("")
    if not ph:
        add("（待补充）"); add("")
    add("## 变更记录"); add("")
    for it in plan.get("change_log") or []:
        add("- %s" % _stringify(it)); add("")
    return "\n".join(L).rstrip() + "\n"


def _stringify(item):
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, (list, tuple)):
        if not item:
            return ""
        return "; ".join(_stringify(x) for x in item)
    if isinstance(item, dict):
        if "name" in item and "value" in item:
            return "%s：%s" % (item["name"], item["value"])
        return ", ".join("%s=%s" % (k, v) for k, v in item.items())
    return str(item)


# --------------------------------------------------------------------------- #
# init / finalize / doe / check
# --------------------------------------------------------------------------- #
def cmd_init(input_dir, output_dir, work_dir):
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    digest = make_input_digest(input_dir)
    write_json(os.path.join(work_dir, "input_digest.json"), digest)
    case_id = digest.get("case_id") or ""
    if not digest["files"]:
        status = "insufficient_evidence"
    elif digest.get("case_json_error"):
        status = "input_error"
    else:
        status = "partial"
    plan = new_skeleton_plan(case_id)
    plan["status"] = status
    write_json(os.path.join(work_dir, "plan.json"), plan)
    result = new_skeleton_result(case_id, status)
    result["errors"] = ([digest["case_json_error"]]
                        if digest.get("case_json_error") else [])
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def cmd_finalize(input_dir, output_dir, work_dir):
    ensure_dir(output_dir)
    plan_obj, err = json_load_safe(os.path.join(work_dir, "plan.json"))
    if plan_obj is None:
        return {"ok": False, "error": "plan.json 不可读: %s" % err}
    if not isinstance(plan_obj, dict):
        return {"ok": False, "error": "plan.json 顶层必须是对象"}
    case_id = plan_obj.get("case_id") or extract_case_id_from_input(input_dir) or ""
    plan_obj["case_id"] = case_id
    status = plan_obj.get("status")
    if status not in STATUS_ENUM:
        status = "partial"
    # 先校验，阻断错误不静默吞掉
    errors, warnings = validate_plan_obj(plan_obj)
    if errors and status == "ok":
        status = "partial"
    plan_obj["status"] = status
    started = plan_obj.get("change_log") and \
        plan_obj["change_log"][0].get("at") or utcnow()
    finished = utcnow()
    result = {
        "case_id": case_id,
        "status": status,
        "title": plan_obj.get("title", ""),
        "plan": {k: plan_obj.get(k, "" if k in ("research_question", "objective") else [])
                 for k in PLAN_CONTENT_KEYS},
        "artifacts": [
            {"path": "plan.md", "kind": "markdown", "exists": True},
            {"path": "plan.json", "kind": "json", "exists": True},
        ],
        "sources": plan_obj.get("sources", []),
        "uncertainty": plan_obj.get("uncertainty", {}),
        "limitations": plan_obj.get("limitations", []),
        "pending_human": plan_obj.get("pending_human", []),
        "change_log": plan_obj.get("change_log", []),
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": started, "finished_at": finished,
                    "seconds": None, "model_requests": None},
    }
    if errors:
        result["errors"] = errors
    if warnings:
        result["degradations"] = ["校验警告（未阻断）: %s" % w for w in warnings]
    # 写产物
    write_json(os.path.join(output_dir, "plan.json"), plan_obj)
    md = _md_lines(plan_obj)
    with open(os.path.join(output_dir, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    write_json(os.path.join(output_dir, "result.json"), result)
    return {"ok": True, "result": result, "errors": errors, "warnings": warnings}


def extract_case_id_from_input(input_dir):
    digest = make_input_digest(input_dir)
    return digest.get("case_id") or ""


def cmd_doe(work_dir, seed):
    plan_obj, err = json_load_safe(os.path.join(work_dir, "plan.json"))
    if plan_obj is None:
        return {"ok": False, "error": "plan.json 不可读: %s" % err}
    candidates = generate_candidates(plan_obj, seed=seed)
    write_json(os.path.join(work_dir, "candidates.json"), candidates)
    return {"ok": True, "candidates": candidates}


def cmd_check(output_dir):
    result_obj, err = json_load_safe(os.path.join(output_dir, "result.json"))
    errors, warnings = [], []
    if result_obj is None:
        return {"ok": False, "errors": ["result.json 不可读: %s" % err],
                "warnings": [], "exit_code": 1}
    if not (result_obj.get("case_id") or "").strip():
        warnings.append("case_id 为空（无 case.json 输入时允许，需人工确认）")
    if result_obj.get("status") not in STATUS_ENUM:
        errors.append("status 非法: %s" % result_obj.get("status"))
    plan = result_obj.get("plan")
    if not isinstance(plan, dict):
        errors.append("plan 必须是对象")
    else:
        for key in REQUIRED_PLAN_KEYS:
            if key not in plan:
                errors.append("plan 缺少字段 %s" % key)
    sources = result_obj.get("sources") or []
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            errors.append("sources[%d] 必须是对象" % i); continue
        if not (s.get("locator") or "").strip() and not (s.get("quote") or "").strip():
            warnings.append("sources[%d] locator 与 quote 均空" % i)
    for field in ("uncertainty", "limitations", "pending_human", "change_log"):
        if field not in result_obj:
            errors.append("缺少字段 %s" % field)
    if not isinstance(result_obj.get("uncertainty"), dict):
        errors.append("uncertainty 必须是对象")
    artifacts = result_obj.get("artifacts") or []
    for a in artifacts:
        p = os.path.join(output_dir, a.get("path", ""))
        if not os.path.isfile(p):
            errors.append("产物缺失: %s" % a.get("path"))
    for name in ("plan.md", "plan.json"):
        if not os.path.isfile(os.path.join(output_dir, name)):
            errors.append("产物缺失: %s" % name)
    exit_code = 1 if errors else (2 if warnings else 0)
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "exit_code": exit_code}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    parser = argparse.ArgumentParser(description="实验参数优化建议 Skill 编排器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("init", "finalize", "doe", "check"):
        p = sub.add_parser(name)
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)
        if name == "doe":
            p.add_argument("--seed", type=int, default=0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        result = cmd_init(args.input, args.output, args.work)
        print(json.dumps({"cmd": "init", "case_id": result.get("case_id"),
                          "status": result.get("status")},
                         ensure_ascii=False))
        return 0
    if args.cmd == "finalize":
        out = cmd_finalize(args.input, args.output, args.work)
        print(json.dumps({"cmd": "finalize", "ok": out.get("ok"),
                          "errors": out.get("errors"),
                          "warnings": out.get("warnings")},
                         ensure_ascii=False))
        return 0 if out.get("ok") else 1
    if args.cmd == "doe":
        out = cmd_doe(args.work, args.seed)
        print(json.dumps({"cmd": "doe", "ok": out.get("ok"),
                          "method": (out.get("candidates") or {}).get("method")},
                         ensure_ascii=False))
        return 0 if out.get("ok") else 1
    if args.cmd == "check":
        report = cmd_check(args.output)
        print(json.dumps(report, ensure_ascii=False))
        return report["exit_code"]
    return 1


if __name__ == "__main__":
    sys.exit(main())
