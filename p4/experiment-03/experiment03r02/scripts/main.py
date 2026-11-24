#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill — 编排器（仅标准库）。

子命令
  init      扫描输入目录，写 /app/work/input_digest.json，并立即写
            /app/output/result.json 的 partial 骨架（阶段 0 早落盘，不得跳过）。
  evaluate  对 plan.json 中的版本化候选方案做可行性判定 + 成本算术 + 预测响应汇总。
  doe       从 plan.json 的有限定义域确定性生成候选点（固定种子，可复现）。
  rank      对候选方案做 Pareto 排序（可行优先，再按成本-响应联合权衡）。
  finalize  把 /app/work/plan.json 合并成 /app/output/result.json，并渲染
            /app/output/plan.md 与 /app/output/plan.json。
  check     校验 result.json 契约与产物文件存在性。

缺省路径：--input /input --output /app/output --work /app/work；本地测试可覆盖。
所有写入容错：坏编码、坏 JSON、缺文件均降级记录，不使进程崩溃。
"""

import argparse
import json
import math
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

CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
META_KEYS = [
    "title", "sources", "uncertainty", "limitations", "pending_human",
    "change_log", "candidates", "candidate_evaluation", "errors", "degradations",
]


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def read_text_safe(path):
    """utf-8-sig -> utf-8 -> latin-1 依次容错读取。"""
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


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def discover_inputs(input_dir):
    """扫描输入目录，返回 (relpath, abs_path, size, kind) 列表，超限标记跳过。"""
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
            if size > MAX_INPUT_BYTES:
                found.append((os.path.relpath(abs_path, input_dir), abs_path, size,
                              "skipped:too_large"))
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            found.append((os.path.relpath(abs_path, input_dir), abs_path, size, kind))
    return found


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "case", "id", "challenge_id", "challengeId"):
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


def new_skeleton_plan(case_id):
    return {
        "case_id": case_id or "",
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
        "search_strategy": {
            "method": "", "initial_points": [], "per_round": "",
            "stop_criteria": [], "candidate_count": 8, "seed": 0,
        },
        "cost_budget": {
            "currency": "", "items": [], "total": 0.0,
            "budget_cap": None, "time_hours": None, "time_budget_hours": None,
            "tradeoff_note": "",
        },
        "basis": [],
        "risks": [],
        "exception_handling": [],
        "confirmations": [],
        "candidates": [],
        "candidate_evaluation": [],
        "sources": [],
        "uncertainty": {
            "unverified_values": [], "missing_data": [], "assumptions": [],
            "prediction_uncertainty": [],
        },
        "limitations": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
    }


def build_result(plan, status, runtime):
    result = {
        "case_id": plan.get("case_id", ""),
        "status": status,
        "title": plan.get("title", ""),
        "plan": {},
        "candidates": plan.get("candidates", []),
        "candidate_evaluation": plan.get("candidate_evaluation", []),
        "artifacts": {},
        "sources": plan.get("sources", []),
        "uncertainty": plan.get("uncertainty", {}),
        "limitations": plan.get("limitations", []),
        "pending_human": plan.get("pending_human", []),
        "change_log": plan.get("change_log", []),
        "errors": plan.get("errors", []),
        "degradations": plan.get("degradations", []),
        "runtime": runtime,
    }
    for k in CONTENT_KEYS:
        result["plan"][k] = plan.get(k)
    return result


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _param_index(parameters):
    idx = {}
    for p in parameters or []:
        if not isinstance(p, dict):
            continue
        for key in ("id", "name"):
            v = p.get(key)
            if isinstance(v, str) and v.strip():
                idx[v.strip()] = p
                idx.setdefault(v.strip().lower(), p)
    return idx


def render_plan_md(plan):
    lines = []
    title = plan.get("title") or "实验参数优化方案"
    lines.append("# %s" % title)
    lines.append("")
    lines.append("- case_id: %s" % (plan.get("case_id", ""),))
    lines.append("")

    def section(head, key):
        val = plan.get(key)
        lines.append("## %s" % head)
        lines.append("")
        if isinstance(val, str):
            lines.append(val if val.strip() else "（未填）")
        elif isinstance(val, list):
            if not val:
                lines.append("（未填）")
            for it in val:
                if isinstance(it, dict):
                    lines.append("- " + json.dumps(it, ensure_ascii=False))
                else:
                    lines.append("- " + str(it))
        elif isinstance(val, dict):
            lines.append("```json")
            lines.append(json.dumps(val, ensure_ascii=False, indent=2))
            lines.append("```")
        else:
            lines.append("（未填）")
        lines.append("")

    for head, key in [
        ("研究问题", "research_question"),
        ("目标", "objective"),
        ("假设", "assumptions"),
        ("变量", "variables"),
        ("关键参数", "parameters"),
        ("对照", "controls"),
        ("步骤", "procedures"),
        ("资源", "resources"),
        ("响应指标", "response_metrics"),
        ("判读标准", "interpretation_criteria"),
        ("搜索策略", "search_strategy"),
        ("成本预算", "cost_budget"),
        ("依据", "basis"),
        ("风险", "risks"),
        ("异常处置", "exception_handling"),
        ("确认项", "confirmations"),
        ("候选方案", "candidates"),
        ("候选方案评估", "candidate_evaluation"),
        ("依据溯源", "sources"),
        ("不确定性", "uncertainty"),
        ("限制", "limitations"),
        ("待人工确认", "pending_human"),
        ("变更记录", "change_log"),
    ]:
        section(head, key)
    return "\n".join(lines)


def _report_check(errors, warnings):
    if errors:
        for e in errors:
            print("ERROR: %s" % e)
    if warnings:
        for w in warnings:
            print("WARN: %s" % w)
    if not errors and not warnings:
        print("OK: result.json 契约通过")


def cmd_init(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    digest = make_input_digest(args.input)
    case_id = digest.get("case_id")
    write_json(os.path.join(args.work, "input_digest.json"), digest)

    plan_path = os.path.join(args.work, "plan.json")
    plan, _ = json_load_safe(plan_path)
    if plan is None:
        plan = new_skeleton_plan(case_id)
        plan["_started_at"] = utcnow()
        write_json(plan_path, plan)

    started_at = plan.get("_started_at") or utcnow()
    result = build_result(plan, "partial", {
        "started_at": started_at, "finished_at": None, "seconds": None,
    })
    if not result["change_log"]:
        result["change_log"] = []
    result["change_log"].append({
        "at": utcnow(), "action": "init", "status": "partial",
        "note": "阶段0骨架落盘：case_id 已填、字段齐全，后续只做增量改进",
    })
    plan["change_log"] = result["change_log"]
    write_json(plan_path, plan)
    write_json(os.path.join(args.output, "result.json"), result)
    print(json.dumps({
        "case_id": case_id, "files": len(digest.get("files", [])),
        "status": "partial",
    }, ensure_ascii=False))
    return 0


def cmd_evaluate(args):
    plan_path = os.path.join(args.work, "plan.json")
    plan, err = json_load_safe(plan_path)
    if plan is None:
        print("cannot load plan.json: %s" % err, file=sys.stderr)
        return 1
    idx = _param_index(plan.get("parameters"))
    budget_cap = _num((plan.get("cost_budget") or {}).get("budget_cap"))
    currency = (plan.get("cost_budget") or {}).get("currency", "")
    evals = []
    for cand in plan.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        version = cand.get("version") or cand.get("name") or "?"
        violations = []
        for pkey, pval in (cand.get("parameters") or {}).items():
            spec = idx.get(pkey) or idx.get(str(pkey).lower())
            val = _num(pval)
            if spec is None:
                violations.append({"parameter": pkey, "kind": "unknown_parameter",
                                   "message": "无对应全局参数定义"})
                continue
            if val is None:
                violations.append({"parameter": pkey, "kind": "non_numeric",
                                   "message": "值非数值"})
                continue
            lo = _num(spec.get("min"))
            hi = _num(spec.get("max"))
            if lo is not None and val < lo:
                violations.append({"parameter": pkey, "kind": "below_min",
                                   "value": val, "min": lo})
            if hi is not None and val > hi:
                violations.append({"parameter": pkey, "kind": "above_max",
                                   "value": val, "max": hi})
            for limkey, label in (
                ("device_limit", "设备量程"),
                ("material_limit", "材料耐温/限制"),
                ("safety_limit", "安全阈值"),
            ):
                lim = spec.get(limkey)
                limv = _num(lim.get("value") if isinstance(lim, dict) else lim)
                if limv is not None and val > limv:
                    violations.append({"parameter": pkey,
                                       "kind": "exceeds_" + limkey,
                                       "value": val, "limit": limv, "label": label})
        total = 0.0
        for item in cand.get("cost_items") or []:
            q = _num(item.get("quantity")) or 0.0
            up = _num(item.get("unit_price")) or 0.0
            total += q * up
        within_budget = True
        if budget_cap is not None and total > budget_cap:
            within_budget = False
            violations.append({"parameter": "*cost", "kind": "over_budget",
                               "total": total, "cap": budget_cap})
        feasible = len(violations) == 0
        evals.append({
            "version": version,
            "feasible": feasible,
            "violations": violations,
            "total_cost": round(total, 4),
            "currency": currency,
            "within_budget": within_budget,
            "predicted_metrics": cand.get("predicted_metrics", {}),
            "notes": cand.get("notes", ""),
        })
    plan["candidate_evaluation"] = evals
    write_json(plan_path, plan)
    write_json(os.path.join(args.work, "candidate_evaluation.json"), evals)
    print(json.dumps({
        "evaluated": len(evals),
        "feasible": sum(1 for e in evals if e["feasible"]),
        "infeasible": sum(1 for e in evals if not e["feasible"]),
    }, ensure_ascii=False))
    return 0


def cmd_doe(args):
    plan_path = os.path.join(args.work, "plan.json")
    plan, err = json_load_safe(plan_path)
    if plan is None:
        print("cannot load plan.json: %s" % err, file=sys.stderr)
        return 1
    ss = plan.get("search_strategy") or {}
    try:
        n = int(ss.get("candidate_count") or 8)
    except (TypeError, ValueError):
        n = 8
    seed = ss.get("seed")
    if seed is None:
        seed = args.seed
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        seed = 0

    grids = {}
    for p in plan.get("parameters") or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or p.get("name")
        if not pid:
            continue
        lo = _num(p.get("min"))
        hi = _num(p.get("max"))
        st = _num(p.get("step"))
        if lo is None or hi is None or st is None or st <= 0:
            continue
        vals = []
        v = lo
        guard = 0
        while v <= hi + 1e-9 and guard < 100000:
            vals.append(round(v, 6))
            v += st
            guard += 1
        if vals:
            grids[pid] = vals

    rng = random.Random(seed)
    candidates = []
    if grids:
        pickers = (("min", lambda g: g[0]),
                   ("max", lambda g: g[-1]),
                   ("mid", lambda g: g[len(g) // 2]))
        for i, (label, picker) in enumerate(pickers):
            candidates.append({"index": i, "origin": "corner_" + label,
                               "parameters": {pid: picker(g) for pid, g in grids.items()}})
    start = len(candidates)
    for i in range(n):
        pt = {}
        for pid, g in grids.items():
            pt[pid] = rng.choice(g)
        candidates.append({"index": start + i, "origin": "seed_%d" % seed,
                           "parameters": pt})
    write_json(os.path.join(args.work, "candidates.json"), candidates)
    print(json.dumps({"candidate_count": len(candidates), "seed": seed},
                     ensure_ascii=False))
    return 0


def cmd_rank(args):
    plan_path = os.path.join(args.work, "plan.json")
    plan, err = json_load_safe(plan_path)
    if plan is None:
        print("cannot load plan.json: %s" % err, file=sys.stderr)
        return 1
    evals = plan.get("candidate_evaluation") or []
    metrics = plan.get("response_metrics") or []
    primary = None
    for m in metrics or []:
        if isinstance(m, dict):
            if m.get("primary") or primary is None:
                primary = m

    def metric_value(e):
        pm = e.get("predicted_metrics") or {}
        if isinstance(pm, dict):
            for _k, v in pm.items():
                if isinstance(v, dict):
                    return _num(v.get("value"))
                return _num(v)
        return None

    feasible = [e for e in evals if e.get("feasible")]
    infeasible = [e for e in evals if not e.get("feasible")]
    direction = (primary or {}).get("direction", "max")

    def sort_key(e):
        mv = metric_value(e)
        cost = e.get("total_cost") or 0.0
        if direction == "min":
            return (mv if mv is not None else float("inf"), cost)
        return (-(mv if mv is not None else float("-inf")), cost)

    feasible_sorted = sorted(feasible, key=sort_key)
    for i, e in enumerate(feasible_sorted):
        e["pareto_rank"] = i + 1
        e["selected"] = (i == 0)
    for e in infeasible:
        e["pareto_rank"] = None
        e["selected"] = False
    plan["candidate_evaluation"] = feasible_sorted + infeasible
    write_json(plan_path, plan)
    write_json(os.path.join(args.work, "candidate_evaluation.json"),
               plan["candidate_evaluation"])
    print(json.dumps({
        "feasible_ranked": len(feasible_sorted),
        "infeasible": len(infeasible),
        "selected": feasible_sorted[0].get("version") if feasible_sorted else None,
    }, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    plan_path = os.path.join(args.work, "plan.json")
    plan, _err = json_load_safe(plan_path)
    if plan is None:
        plan = new_skeleton_plan("")
    status = args.status or plan.get("status") or "partial"
    if status not in STATUS_ENUM:
        status = "partial"
    started = plan.get("_started_at")
    seconds = None
    if isinstance(started, str):
        try:
            t0 = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
            seconds = max(0.0, round(time.time() - t0, 1))
        except Exception:
            seconds = None
    runtime = {"started_at": started, "finished_at": utcnow(), "seconds": seconds}

    out_dir = os.path.abspath(args.output)
    ensure_dir(out_dir)
    result = build_result(plan, status, runtime)
    result["artifacts"] = {
        "plan_md": os.path.join(out_dir, "plan.md"),
        "plan_json": os.path.join(out_dir, "plan.json"),
        "candidates_json": (os.path.join(out_dir, "candidates.json")
                            if os.path.exists(os.path.join(args.work, "candidates.json"))
                            else None),
        "candidate_evaluation_json": (
            os.path.join(out_dir, "candidate_evaluation.json")
            if os.path.exists(os.path.join(args.work, "candidate_evaluation.json"))
            else None),
    }
    write_json(os.path.join(out_dir, "result.json"), result)
    write_json(os.path.join(out_dir, "plan.json"), plan)
    write_text(os.path.join(out_dir, "plan.md"), render_plan_md(plan))
    for src, name in (("candidates.json", "candidates.json"),
                      ("candidate_evaluation.json", "candidate_evaluation.json")):
        sp = os.path.join(args.work, src)
        if os.path.exists(sp):
            try:
                obj, _e = json_load_safe(sp)
                write_json(os.path.join(out_dir, name), obj)
            except Exception:
                pass
    print("finalized -> %s (status=%s)" % (os.path.join(out_dir, "result.json"), status))
    return 0


def cmd_check(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = json_load_safe(result_path)
    errors = []
    warnings = []
    if result is None:
        errors.append("result.json 不可读: %s" % err)
        _report_check(errors, warnings)
        return 1
    if not isinstance(result, dict):
        errors.append("result.json 不是对象")
        _report_check(errors, warnings)
        return 1
    if not (result.get("case_id") and str(result["case_id"]).strip()):
        warnings.append("case_id 为空")
    if result.get("status") not in STATUS_ENUM:
        errors.append("status 非法: %r" % result.get("status"))
    plan = result.get("plan")
    if not isinstance(plan, dict):
        errors.append("plan 缺失或非对象")
    else:
        for k in CONTENT_KEYS:
            if k not in plan:
                warnings.append("plan 缺字段: %s" % k)
    for key in ("sources", "change_log", "limitations", "pending_human"):
        if key not in result or not isinstance(result.get(key), list):
            warnings.append("字段 %s 缺失或非列表" % key)
    if not isinstance(result.get("uncertainty"), dict):
        warnings.append("uncertainty 缺失或非对象")
    arts = result.get("artifacts") or {}
    for name, rel in (("plan_md", "plan.md"), ("plan_json", "plan.json")):
        p = arts.get(name)
        if not p:
            errors.append("artifacts 缺 %s" % name)
            continue
        if not os.path.exists(p):
            if os.path.exists(os.path.join(args.output, rel)):
                warnings.append("%s 指向路径不存在但 %s 存在于输出目录" % (name, rel))
            else:
                errors.append("%s 指向的文件不存在: %s" % (name, p))
    if "runtime" not in result:
        warnings.append("缺 runtime")
    _report_check(errors, warnings)
    if errors:
        return 1
    if warnings:
        return 2
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)
    common.add_argument("--seed", type=int, default=0)
    common.add_argument("--status", default=None,
                        help="finalize 时强制指定 status（默认取 plan.status 或 partial）")
    parser = argparse.ArgumentParser(
        description="实验参数优化建议 Skill 编排器（仅标准库）",
        parents=[common])
    sub = parser.add_subparsers(dest="cmd", required=True)
    # 每个子命令也接受公共参数，使 `main.py init --input ...` 与
    # `main.py --input ... init` 两种写法均可用。
    sub.add_parser("init", parents=[common])
    sub.add_parser("evaluate", parents=[common])
    sub.add_parser("doe", parents=[common])
    sub.add_parser("rank", parents=[common])
    sub.add_parser("finalize", parents=[common])
    sub.add_parser("check", parents=[common])
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "evaluate": cmd_evaluate,
        "doe": cmd_doe,
        "rank": cmd_rank,
        "finalize": cmd_finalize,
        "check": cmd_check,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
