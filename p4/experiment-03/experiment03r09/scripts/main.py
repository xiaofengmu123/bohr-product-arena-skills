#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill —— 单一编排器（仅标准库）。

子命令
  init      扫描 /input，写 /app/work/input_digest.json 与 /app/output/result.json 骨架
  finalize  合并 /app/work/plan.json -> /app/output/result.json，并渲染 plan.md / plan.json
  doe       由 plan.json 生成确定性候选点 -> /app/work/candidates.json
  validate  校验 plan.json（schema + 可行域约束），报告 errors/warnings
  check     校验 result.json 契约与产物文件存在性
  selftest  空输入 / 坏 JSON / 正常输入三场景自检

退出码约定：0=通过；1=阻断错误；2=仅警告。
默认路径：--input /input --output /app/output --work /app/work（可覆盖用于本地测试）。
"""

import argparse
import json
import os
import random
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_INPUT_BYTES = 2 * 1024 * 1024
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log"}

# plan.json 主结果体键（进入 result.json 的 plan 字段）
PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables", "parameters",
    "controls", "procedures", "resources", "response_metrics",
    "interpretation_criteria", "search_strategy", "cost_budget", "basis",
    "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "uncertainty", "limitations",
                  "pending_human", "change_log"]

# 校验时必须存在的 plan 顶层键（缺一即判定不完整）
REQUIRED_PLAN_KEYS = [
    "objective", "assumptions", "variables", "parameters", "controls",
    "procedures", "resources", "response_metrics", "interpretation_criteria",
    "search_strategy", "cost_budget", "basis", "risks", "exception_handling",
    "confirmations",
]


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
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
        return None, "cannot read file"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:
        return None, "parse error: %s" % exc


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


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
                found.append({"path": rel, "size": size, "kind": "skipped:too_large"})
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            found.append({"path": rel, "size": size, "kind": kind})
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
    digest = {"input_dir": input_dir, "files": [], "case_id": None,
              "case_json_error": None, "task_md_preview": None}
    for info in discover_inputs(input_dir):
        entry = dict(info)
        if entry["kind"] == "text":
            text = read_text_safe(os.path.join(input_dir, entry["path"]))
            entry["readable"] = text is not None
            if text is not None:
                entry["preview"] = text[:2000]
        else:
            entry["readable"] = False
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


def empty_plan(case_id):
    plan = {k: [] for k in PLAN_CONTENT_KEYS}
    plan.update({
        "research_question": "", "objective": "", "search_strategy": {
            "method": "", "initial_points": [], "per_round": "",
            "stop_criteria": [], "candidate_count": 8, "seed": 0},
        "cost_budget": {"items": [], "total": "", "time_hours": None,
                        "tradeoff_note": ""},
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "conflicts": [], "notes": []},
        "case_id": case_id or "",
    })
    return plan


def _default_plan_value(k):
    """plan.json 各键的缺省值，保证 finalize 输出字段齐全。"""
    if k == "uncertainty":
        return {"unverified_values": [], "missing_data": [],
                "conflicts": [], "notes": []}
    if k == "search_strategy":
        return {"method": "", "initial_points": [], "per_round": "",
                "stop_criteria": [], "candidate_count": 8, "seed": 0}
    if k == "cost_budget":
        return {"items": [], "total": "", "time_hours": None, "tradeoff_note": ""}
    if k in ("research_question", "objective", "title"):
        return ""
    return []


def new_skeleton_result(case_id):
    plan = empty_plan(case_id)
    return {
        "case_id": case_id or "",
        "status": "partial",
        "plan": plan,
        "artifacts": [
            {"type": "markdown", "path": "plan.md",
             "description": "可编辑实验参数方案（人工可读）"},
            {"type": "json", "path": "plan.json",
             "description": "结构化参数方案规格"},
        ],
        "sources": [],
        "uncertainty": plan["uncertainty"],
        "limitations": ["阶段 A 骨架：尚未完成材料读取与方案建模"],
        "pending_human": [],
        "change_log": [{"time": utcnow(), "step": "init",
                        "note": "骨架落盘（status=partial）"}],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": utcnow(), "seconds": None},
    }


# ---------------- 校验 ----------------

def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def check_parameter(p, i):
    errs, warns = [], []
    if not isinstance(p, dict):
        errs.append("parameters[%d] 非对象" % i)
        return errs, warns
    if not isinstance(p.get("name"), str) or not p.get("name").strip():
        errs.append("parameters[%d].name 缺失" % i)
    if "unit" not in p:
        warns.append("parameters[%d].unit 缺失" % i)
    sv = _num(p.get("suggested_value"))
    if "suggested_value" not in p:
        errs.append("parameters[%d].suggested_value 缺失" % i)
    elif sv is None:
        errs.append("parameters[%d].suggested_value 非数值" % i)
    fr = p.get("feasible_range")
    fmin = fmax = None
    if not isinstance(fr, dict):
        errs.append("parameters[%d].feasible_range 缺失" % i)
    else:
        fmin, fmax = _num(fr.get("min")), _num(fr.get("max"))
        if fmin is None or fmax is None:
            errs.append("parameters[%d].feasible_range 缺 min/max" % i)
        elif fmin > fmax:
            errs.append("parameters[%d].feasible_range min>max" % i)
        elif sv is not None and not (fmin <= sv <= fmax):
            errs.append("parameters[%d].suggested_value 不在 feasible_range 内" % i)
    step = _num(p.get("step"))
    if step is not None and step <= 0:
        errs.append("parameters[%d].step 必须 >0" % i)
    hb = p.get("hard_bounds")
    if isinstance(hb, dict):
        hmin, hmax = _num(hb.get("min")), _num(hb.get("max"))
        if hmin is not None and hmax is not None and hmin <= hmax:
            if fmin is not None and fmax is not None and (fmin < hmin or fmax > hmax):
                errs.append("parameters[%d].feasible_range 超出 hard_bounds" % i)
    src = p.get("constraint_source") or p.get("source")
    if not (isinstance(src, dict) and isinstance(src.get("quote"), str)
            and src.get("quote").strip()):
        warns.append("parameters[%d] 缺 constraint_source.quote（逐字溯源）" % i)
    return errs, warns


def check_metric(m, i):
    errs, warns = [], []
    if not isinstance(m, dict):
        errs.append("response_metrics[%d] 非对象" % i)
        return errs, warns
    for k in ("name", "unit", "measurement_method", "direction", "target"):
        if not m.get(k):
            errs.append("response_metrics[%d].%s 缺失" % (i, k))
    if m.get("direction") not in (None, "maximize", "minimize", "target"):
        warns.append("response_metrics[%d].direction 应为 maximize/minimize/target" % i)
    if m.get("acceptable_lower") is None and m.get("acceptable_upper") is None:
        warns.append("response_metrics[%d] 缺 acceptable_lower/acceptable_upper" % i)
    return errs, warns


def check_search_strategy(ss):
    errs, warns = [], []
    if not isinstance(ss, dict):
        errs.append("search_strategy 缺失")
        return errs, warns
    if not isinstance(ss.get("method"), str) or not ss.get("method").strip():
        errs.append("search_strategy.method 缺失")
    if not isinstance(ss.get("initial_points"), list) or not ss.get("initial_points"):
        warns.append("search_strategy.initial_points 为空")
    if not isinstance(ss.get("per_round"), str) or not ss.get("per_round").strip():
        warns.append("search_strategy.per_round 缺失")
    if not ss.get("stop_criteria"):
        warns.append("search_strategy.stop_criteria 为空")
    return errs, warns


def check_cost_budget(cb):
    errs, warns = [], []
    if not isinstance(cb, dict):
        errs.append("cost_budget 缺失")
        return errs, warns
    if not isinstance(cb.get("items"), list) or not cb.get("items"):
        warns.append("cost_budget.items 为空")
    if not isinstance(cb.get("tradeoff_note"), str) or not cb.get("tradeoff_note").strip():
        warns.append("cost_budget.tradeoff_note 缺失")
    return errs, warns


def validate_plan(plan):
    errs, warns = [], []
    if not isinstance(plan, dict):
        return ["plan 非 JSON 对象"], []
    for k in REQUIRED_PLAN_KEYS:
        if k not in plan:
            errs.append("plan.%s 缺失" % k)
    if "parameters" in plan and isinstance(plan["parameters"], list):
        if not plan["parameters"]:
            warns.append("parameters 为空")
        for i, p in enumerate(plan["parameters"]):
            e, w = check_parameter(p, i)
            errs += e
            warns += w
    if "response_metrics" in plan and isinstance(plan["response_metrics"], list):
        if not plan["response_metrics"]:
            warns.append("response_metrics 为空")
        for i, m in enumerate(plan["response_metrics"]):
            e, w = check_metric(m, i)
            errs += e
            warns += w
    if "search_strategy" in plan:
        e, w = check_search_strategy(plan["search_strategy"])
        errs += e
        warns += w
    if "cost_budget" in plan:
        e, w = check_cost_budget(plan["cost_budget"])
        errs += e
        warns += w
    return errs, warns


# ---------------- 候选点（确定性） ----------------

def _grid_values(p):
    fr = p.get("feasible_range", {})
    lo, hi = _num(fr.get("min")), _num(fr.get("max"))
    step = _num(p.get("step"))
    vals = []
    if lo is None or hi is None or step is None or step <= 0:
        return vals
    v = lo
    guard = 0
    while v <= hi + 1e-9 and guard < 100000:
        vals.append(round(v, 9))
        v += step
        guard += 1
    sv = _num(p.get("suggested_value"))
    if sv is not None:
        vals.append(round(sv, 9))
    return sorted(set(vals))


def generate_candidates(plan, seed=0, count=None):
    import itertools
    params = [p for p in plan.get("parameters", []) if isinstance(p, dict)]
    ss = plan.get("search_strategy", {}) if isinstance(plan.get("search_strategy"), dict) else {}
    count = count or int(ss.get("candidate_count") or 8)
    rng = random.Random(seed)
    grids = {i: (_grid_values(p) or [None]) for i, p in enumerate(params)}
    names = [p.get("name") for p in params]
    if not grids:
        return {"seed": seed, "candidate_count": 0,
                "parameter_names": names, "points": []}
    full = 1
    for vals in grids.values():
        full *= len(vals)
    if full <= count:
        combos = list(itertools.product(*[grids[i] for i in range(len(params))]))
        points = [{"parameters": list(c), "source": "grid"} for c in combos]
    else:
        anchor = []
        for i, p in enumerate(params):
            sv = _num(p.get("suggested_value"))
            anchor.append(round(sv, 9) if sv is not None else grids[i][0])
        anchor = tuple(anchor)
        points = [{"parameters": list(anchor), "source": "suggested_value"}]
        if full <= 100000:
            all_combos = list(itertools.product(*[grids[i] for i in range(len(params))]))
            rest = [c for c in all_combos if c != anchor]
            for c in rng.sample(rest, min(count - 1, len(rest))):
                points.append({"parameters": list(c), "source": "seeded_sample"})
        else:
            seen = {anchor}
            while len(points) < count and len(seen) < full:
                combo = tuple(rng.choice(grids[i]) for i in range(len(params)))
                if combo not in seen:
                    seen.add(combo)
                    points.append({"parameters": list(combo), "source": "seeded_sample"})
    return {"seed": seed, "candidate_count": len(points),
            "parameter_names": names, "points": points}


# ---------------- plan.md 渲染 ----------------

def _list_to_md(items):
    if not items:
        return "_（空）_\n"
    lines = []
    for it in items:
        if isinstance(it, str):
            lines.append("- %s" % it)
        elif isinstance(it, dict):
            kv = "; ".join("%s: %s" % (k, v) for k, v in it.items())
            lines.append("- %s" % kv)
        else:
            lines.append("- %s" % it)
    return "\n".join(lines) + "\n"


def _params_table(params):
    if not params:
        return "_（空）_\n"
    head = "| 参数 | 单位 | 建议值 | 可行区间 | 步进 | 硬边界 | 依据 |"
    sep = "|---|---|---|---|---|---|---|"
    rows = [head, sep]
    for p in params:
        if not isinstance(p, dict):
            continue
        fr = p.get("feasible_range", {})
        hb = p.get("hard_bounds", {})
        src = p.get("constraint_source") or p.get("source") or {}
        rows.append("| %s | %s | %s | %s–%s | %s | %s | %s |" % (
            p.get("name", ""), p.get("unit", ""), p.get("suggested_value", ""),
            fr.get("min", ""), fr.get("max", ""), p.get("step", ""),
            ("%s–%s" % (hb.get("min", ""), hb.get("max", "")) if hb else ""),
            src.get("quote", "")[:80]))
    return "\n".join(rows) + "\n"


def render_plan_md(plan, case_id, status):
    title = plan.get("title") or "实验参数优化方案"
    out = ["# %s\n" % title,
           "- case_id: %s\n" % case_id,
           "- status: %s\n" % status]
    out.append("\n## 研究问题\n%s\n" % (plan.get("research_question") or "_（空）_"))
    out.append("\n## 目标\n%s\n" % (plan.get("objective") or "_（空）_"))
    for heading, key in [
        ("假设", "assumptions"), ("变量", "variables"), ("对照", "controls"),
        ("步骤", "procedures"), ("资源", "resources"),
        ("响应指标", "response_metrics"),
        ("判读标准", "interpretation_criteria"), ("依据", "basis"),
        ("风险", "risks"), ("异常处置", "exception_handling"),
        ("确认项", "confirmations"),
    ]:
        out.append("\n## %s\n" % heading)
        out.append(_list_to_md(plan.get(key, [])))
    out.append("\n## 关键参数\n")
    out.append(_params_table(plan.get("parameters", [])))
    ss = plan.get("search_strategy")
    out.append("\n## 搜索策略\n")
    out.append(_list_to_md(["%s: %s" % (k, v) for k, v in ss.items()]
                           if isinstance(ss, dict) else ss))
    cb = plan.get("cost_budget")
    out.append("\n## 成本与时间预算\n")
    out.append(_list_to_md(["%s: %s" % (k, v) for k, v in cb.items()]
                           if isinstance(cb, dict) else cb))
    out.append("\n## 溯源（sources）\n")
    out.append(_list_to_md(plan.get("sources", [])))
    out.append("\n## 不确定性与限制\n")
    out.append("- uncertainty: %s\n" % plan.get("uncertainty", {}))
    out.append(_list_to_md(plan.get("limitations", [])))
    return "".join(out)


# ---------------- 子命令 ----------------

def cmd_init(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    digest = make_input_digest(args.input)
    write_json(os.path.join(args.work, "input_digest.json"), digest)
    case_id = digest.get("case_id")
    result = new_skeleton_result(case_id)
    if digest.get("case_json_error"):
        result["errors"].append({"source": "case.json",
                                 "detail": digest["case_json_error"]})
    write_json(os.path.join(args.output, "result.json"), result)
    print("init ok: case_id=%r files=%d" % (case_id, len(digest["files"])))
    return 0


def _load_plan(work):
    plan, err = json_load_safe(os.path.join(work, "plan.json"))
    if plan is None or not isinstance(plan, dict):
        return None, err or "plan.json 缺失或非对象"
    return plan, None


def _resolve_case_id(plan, work, input_dir):
    cid = plan.get("case_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    digest, _ = json_load_safe(os.path.join(work, "input_digest.json"))
    if digest and digest.get("case_id"):
        return digest["case_id"]
    case_obj, _ = json_load_safe(os.path.join(input_dir, "case.json"))
    cid = extract_case_id(case_obj)
    return cid or ""


def cmd_finalize(args):
    plan, err = _load_plan(args.work)
    if plan is None:
        print("finalize error: %s" % err)
        return 1
    case_id = _resolve_case_id(plan, args.work, args.input)
    status = plan.get("status")
    if status not in STATUS_ENUM:
        status = "partial"
    body = {}
    for k in PLAN_CONTENT_KEYS + PLAN_META_KEYS:
        body[k] = plan.get(k, _default_plan_value(k))
    started = plan.get("runtime_started_at") or utcnow()
    change_log = plan.get("change_log", [])
    if not change_log or change_log[-1].get("step") != "finalize":
        change_log = change_log + [{"time": utcnow(), "step": "finalize",
                                    "note": "合并 plan.json -> result.json"}]
    result = {
        "case_id": case_id,
        "status": status,
        "plan": body,
        "artifacts": [
            {"type": "markdown", "path": "plan.md",
             "description": "可编辑实验参数方案（人工可读）"},
            {"type": "json", "path": "plan.json",
             "description": "结构化参数方案规格"},
        ],
        "sources": plan.get("sources", []),
        "uncertainty": plan.get("uncertainty",
                                {"unverified_values": [], "missing_data": [],
                                 "conflicts": [], "notes": []}),
        "limitations": plan.get("limitations", []),
        "pending_human": plan.get("pending_human", []),
        "change_log": change_log,
        "errors": plan.get("errors", []),
        "degradations": plan.get("degradations", []),
        "runtime": {"started_at": started, "seconds": None,
                    "finished_at": utcnow()},
    }
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.output, "plan.json"), body)
    md = render_plan_md(body, case_id, status)
    with open(os.path.join(args.output, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print("finalize ok: case_id=%r status=%s" % (case_id, status))
    return 0


def cmd_validate(args):
    plan, err = _load_plan(args.work if args.plan_path is None else
                           os.path.dirname(args.plan_path))
    if args.plan_path is not None:
        plan, err = json_load_safe(args.plan_path)
    if plan is None:
        report = {"errors": [err or "plan.json 缺失"], "warnings": []}
    else:
        errs, warns = validate_plan(plan)
        report = {"errors": errs, "warnings": warns}
    if args.report:
        write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["errors"]:
        return 1
    if report["warnings"]:
        return 2
    return 0


def cmd_doe(args):
    plan, err = _load_plan(args.work)
    if plan is None:
        print("doe error: %s" % err)
        return 1
    seed = args.seed if args.seed is not None else 0
    cand = generate_candidates(plan, seed=seed)
    write_json(os.path.join(args.work, "candidates.json"), cand)
    print("doe ok: %d points (seed=%d)" % (cand["candidate_count"], seed))
    return 0


def cmd_check(args):
    result, err = json_load_safe(args.result_path)
    if result is None:
        print("check error: %s" % err)
        return 1
    errs, warns = [], []
    cid = result.get("case_id")
    if not isinstance(cid, str) or not cid.strip():
        errs.append("case_id 缺失或为空")
    if result.get("status") not in STATUS_ENUM:
        errs.append("status 非 %s" % list(STATUS_ENUM))
    plan = result.get("plan")
    if not isinstance(plan, dict):
        errs.append("plan 缺失")
    else:
        for k in REQUIRED_PLAN_KEYS:
            if k not in plan:
                errs.append("plan.%s 缺失" % k)
        if not plan.get("parameters"):
            warns.append("plan.parameters 为空")
        if not plan.get("response_metrics"):
            warns.append("plan.response_metrics 为空")
    sources = result.get("sources")
    if not isinstance(sources, list) or not sources:
        warns.append("sources 为空")
    else:
        for i, s in enumerate(sources):
            if not (isinstance(s, dict) and s.get("locator") and s.get("quote")):
                warns.append("sources[%d] 缺 locator/quote" % i)
    for k in ("uncertainty", "limitations", "pending_human", "change_log"):
        if k not in result:
            warns.append("%s 缺失" % k)
    out_dir = os.path.dirname(args.result_path) or args.output_dir or "."
    for art in ("plan.md", "plan.json"):
        p = os.path.join(out_dir, art)
        if not os.path.isfile(p):
            errs.append("产物缺失: %s" % art)
    print("check: %d errors, %d warnings" % (len(errs), len(warns)))
    for e in errs:
        print("  [E] %s" % e)
    for w in warns:
        print("  [W] %s" % w)
    if errs:
        return 1
    if warns:
        return 2
    return 0


def cmd_selftest(args):
    import selftest
    return selftest.run_selftest()


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    p = argparse.ArgumentParser(parents=[common],
                                description="实验参数优化建议 Skill 编排器")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[common], help="发现输入并写骨架")
    sub.add_parser("finalize", parents=[common],
                   help="合并 plan.json -> result.json")

    v = sub.add_parser("validate", parents=[common], help="校验 plan.json")
    v.add_argument("plan_path", nargs="?", default=None)
    v.add_argument("--report", default=None)

    d = sub.add_parser("doe", parents=[common], help="生成确定性候选点")
    d.add_argument("--seed", type=int, default=None)

    c = sub.add_parser("check", parents=[common],
                       help="校验 result.json 与产物")
    c.add_argument("result_path")
    c.add_argument("--output-dir", default=None)

    sub.add_parser("selftest", parents=[common], help="三场景自检")
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "doe":
        return cmd_doe(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "selftest":
        return cmd_selftest(args)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
