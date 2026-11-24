#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill — 编排器（仅标准库）。

子命令
    init      发现输入文件，写 /app/work/input_digest.json 与
              /app/output/result.json 骨架（status=partial/insufficient_evidence）
    finalize  合并 /app/work/plan.json -> /app/output/result.json，
              渲染可编辑的 /app/output/plan.md 与 /app/output/plan.json
    doe       由 /app/work/plan.json 确定性生成候选点 -> /app/work/candidates.json

缺省：--input /input --output /app/output --work /app/work，路径均可覆盖以便本地测试。
设计取舍：本模块优先保证“任何输入下都能留下合法、可诊断的产物”，因此所有文件读写
均容错（空输入、坏 JSON、坏编码、缺参数、超大文件、写盘失败都不崩溃、不编造数据）。
"""

import argparse
import itertools
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
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log"}

PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "uncertainty", "limitations",
                  "pending_human", "change_log", "errors", "degradations"]

_CONTENT_DEFAULTS = {
    "research_question": "", "objective": "", "assumptions": [],
    "variables": [], "parameters": [], "controls": [], "procedures": [],
    "resources": [], "response_metrics": [], "interpretation_criteria": [],
    "search_strategy": {"method": "", "initial_points": [], "per_round": "",
                        "stop_criteria": [], "candidate_count": 8, "seed": 0},
    "cost_budget": {"items": [], "total": "", "time_hours": None,
                    "tradeoff_note": ""},
    "basis": [], "risks": [], "exception_handling": [], "confirmations": [],
}


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if not path:
        return
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass


def read_text_safe(path):
    """容错读取文本：utf-8-sig -> utf-8 -> latin-1；不可读返回 None。"""
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


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    except OSError:
        return None


def write_json(path, obj):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        return path
    except (OSError, TypeError):
        return None


def discover_inputs(input_dir):
    """返回候选输入文件列表；超大文件跳过并标注，不崩溃。"""
    found = []
    if not input_dir or not os.path.isdir(input_dir):
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
                found.append({"path": rel, "size": size,
                              "kind": "skipped:too_large"})
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
    files = discover_inputs(input_dir)
    digest = {"input_dir": input_dir, "files": [], "case_id": None,
              "case_json_error": None, "task_md_preview": None}
    for entry in files:
        out = {"path": entry["path"], "size": entry["size"],
               "kind": entry["kind"]}
        if entry["kind"] == "text":
            text = read_text_safe(os.path.join(input_dir, entry["path"]))
            if text is None:
                out["readable"] = False
            else:
                out["readable"] = True
                out["preview"] = text[:2000]
        digest["files"].append(out)

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


def new_skeleton_plan(case_id, status):
    plan = {"case_id": case_id or "", "status": status, "title": ""}
    for key, default in _CONTENT_DEFAULTS.items():
        import copy
        plan[key] = copy.deepcopy(default)
    plan.update({
        "sources": [],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumptions_flagged": [],
                        "prediction_uncertainty": ""},
        "limitations": [], "pending_human": [], "change_log": [],
        "errors": [], "degradations": [],
    })
    return plan


def load_plan_safe(path):
    obj, err = json_load_safe(path)
    if obj is None:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def build_result(plan, fallback_case_id, started_at, output_dir):
    case_id = plan.get("case_id") or fallback_case_id or ""
    status = plan.get("status")
    if status not in STATUS_ENUM:
        status = "partial"
    content = {}
    for key in PLAN_CONTENT_KEYS:
        content[key] = plan.get(key, _CONTENT_DEFAULTS.get(key))
    artifacts = [
        {"path": "plan.md", "type": "markdown",
         "description": "可编辑的实验参数方案（Markdown）",
         "absolute_path": os.path.join(output_dir, "plan.md")},
        {"path": "plan.json", "type": "json",
         "description": "结构化实验参数方案",
         "absolute_path": os.path.join(output_dir, "plan.json")},
    ]
    return {
        "case_id": case_id,
        "status": status,
        "plan": content,
        "artifacts": artifacts,
        "sources": plan.get("sources", []),
        "uncertainty": plan.get("uncertainty", {}),
        "limitations": plan.get("limitations", []),
        "pending_human": plan.get("pending_human", []),
        "change_log": plan.get("change_log", []),
        "errors": plan.get("errors", []),
        "degradations": plan.get("degradations", []),
        "runtime": {"started_at": started_at, "finished_at": utcnow(),
                    "skill": "weekly-skill"},
    }


# ---------- plan.md 渲染 ----------

def _list(items):
    if not isinstance(items, list) or not items:
        return "（空）"
    lines = []
    for it in items:
        if isinstance(it, dict):
            parts = []
            for k, v in it.items():
                if v not in (None, "", [], {}):
                    parts.append("%s=%s" % (k, v))
            lines.append("- " + "；".join(parts) if parts else "- " + json.dumps(it, ensure_ascii=False))
        else:
            lines.append("- " + str(it))
    return "\n".join(lines)


def _params_table(params):
    if not isinstance(params, list) or not params:
        return "（无）"
    headers = ["名称", "单位", "建议值", "可行区间", "步进", "约束来源", "逐字依据"]
    rows = ["| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |"]
    for p in params:
        if not isinstance(p, dict):
            continue
        rng = p.get("feasible_range") or {}
        rng_s = "[%s, %s]" % (rng.get("min", ""), rng.get("max", ""))
        row = [p.get("name", ""), p.get("unit", ""), p.get("recommended_value", ""),
               rng_s, p.get("step", ""), p.get("constraint_source", ""),
               p.get("quote", "")]
        rows.append("| " + " | ".join(str(x).replace("|", "\\|") for x in row) + " |")
    return "\n".join(rows)


def _metrics_table(metrics):
    if not isinstance(metrics, list) or not metrics:
        return "（无）"
    headers = ["指标", "单位", "测量方法", "方向", "目标", "可接受下限", "预测方式", "不确定度"]
    rows = ["| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |"]
    for m in metrics:
        if not isinstance(m, dict):
            continue
        row = [m.get("name", ""), m.get("unit", ""), m.get("measurement_method", ""),
               m.get("direction", ""), m.get("target", ""),
               m.get("acceptable_lower_bound", ""), m.get("prediction_method", ""),
               m.get("uncertainty", "")]
        rows.append("| " + " | ".join(str(x).replace("|", "\\|") for x in row) + " |")
    return "\n".join(rows)


def _sources_block(sources):
    if not isinstance(sources, list) or not sources:
        return "（无）"
    lines = []
    for s in sources:
        if isinstance(s, dict):
            lines.append("- **%s** `%s`\n  - 引用：%s\n  - 支撑：%s%s" % (
                s.get("id", ""), s.get("locator", ""), s.get("quote", ""),
                s.get("claim", ""),
                ("\n  - 冲突：source_conflict" if s.get("conflict") else "")))
    return "\n".join(lines)


def render_plan_md(plan):
    strat = plan.get("search_strategy") or {}
    cb = plan.get("cost_budget") or {}
    lines = [
        "# 实验参数优化方案",
        "",
        "- case_id：%s" % (plan.get("case_id") or "（未提供）"),
        "- status：%s" % (plan.get("status") or "partial"),
        "- 标题：%s" % (plan.get("title") or "（未命名）"),
        "",
        "## 研究问题与目标",
        "",
        "- 研究问题：%s" % (plan.get("research_question") or "（待补充）"),
        "- 目标：%s" % (plan.get("objective") or "（待补充）"),
        "",
        "## 假设",
        "",
        _list(plan.get("assumptions")),
        "",
        "## 变量",
        "",
        _list(plan.get("variables")),
        "",
        "## 关键参数与可行区间",
        "",
        _params_table(plan.get("parameters")),
        "",
        "## 对照",
        "",
        _list(plan.get("controls")),
        "",
        "## 步骤",
        "",
        _list(plan.get("procedures")),
        "",
        "## 资源",
        "",
        _list(plan.get("resources")),
        "",
        "## 响应指标口径",
        "",
        _metrics_table(plan.get("response_metrics")),
        "",
        "## 判读标准",
        "",
        _list(plan.get("interpretation_criteria")),
        "",
        "## 搜索策略",
        "",
        "- 方法：%s" % (strat.get("method") or "（待定）"),
        "- 初始点：%s" % (strat.get("initial_points") or "（待定）"),
        "- 每轮补点：%s" % (strat.get("per_round") or "（待定）"),
        "- 停止判据：%s" % (strat.get("stop_criteria") or "（待定）"),
        "- 候选点数：%s；种子：%s" % (strat.get("candidate_count", 8), strat.get("seed", 0)),
        "",
        "## 成本与时间预算",
        "",
        "- 明细：",
        _list(cb.get("items")),
        "- 总成本：%s" % (cb.get("total") or "（待补充）"),
        "- 时间（小时）：%s" % (cb.get("time_hours") if cb.get("time_hours") is not None else "（待补充）"),
        "- 多目标取舍：%s" % (cb.get("tradeoff_note") or "（待补充）"),
        "",
        "## 依据",
        "",
        _list(plan.get("basis")),
        "",
        "## 风险",
        "",
        _list(plan.get("risks")),
        "",
        "## 异常处置",
        "",
        _list(plan.get("exception_handling")),
        "",
        "## 确认项",
        "",
        _list(plan.get("confirmations")),
        "",
        "## 证据溯源",
        "",
        _sources_block(plan.get("sources")),
        "",
        "## 不确定性",
        "",
        _list([plan.get("uncertainty")]) if isinstance(plan.get("uncertainty"), dict) else "（无）",
        "",
        "## 局限",
        "",
        _list(plan.get("limitations")),
        "",
        "## 待人工确认",
        "",
        _list(plan.get("pending_human")),
        "",
        "## 变更记录",
        "",
        _list(plan.get("change_log")),
        "",
        "## 运行错误 / 降级",
        "",
        "- errors：",
        _list(plan.get("errors")),
        "- degradations：",
        _list(plan.get("degradations")),
        "",
    ]
    return "\n".join(lines)


# ---------- 候选点生成 ----------

def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _levels(param, max_levels=8):
    rng = param.get("feasible_range") or param.get("range") or {}
    lo = _num(rng.get("min"))
    hi = _num(rng.get("max"))
    if lo is None or hi is None or lo > hi:
        return None
    step = _num(param.get("step"))
    if step is None or step <= 0:
        if max_levels <= 1:
            return [lo]
        return [lo + (hi - lo) * i / (max_levels - 1) for i in range(max_levels)]
    vals = []
    v = lo
    eps = (hi - lo) * 1e-9 + 1e-12
    guard = 0
    while v <= hi + eps and guard < 100000:
        vals.append(round(v, 9))
        v += step
        guard += 1
    if not vals:
        return None
    if len(vals) > max_levels:
        idx = sorted({int(round(i * (len(vals) - 1) / (max_levels - 1)))
                      for i in range(max_levels)})
        vals = [vals[i] for i in idx]
    return vals


def generate_points(params, method, count, seed):
    rng = random.Random(seed)
    numeric = []
    for p in (params or []):
        if not isinstance(p, dict):
            continue
        name = p.get("name") or p.get("parameter") or ""
        if not name:
            continue
        lev = _levels(p)
        if lev:
            numeric.append((name, lev))
    if not numeric:
        return []
    if method in ("grid", "factorial", ""):
        total = 1
        for _, lev in numeric:
            total *= len(lev)
        if total <= 4096:
            combos = list(itertools.product(*[lev for _, lev in numeric]))
            if count > 1 and len(combos) > count:
                combos = [combos[int(round(i * (len(combos) - 1) / (count - 1)))]
                          for i in range(count)]
            points = [{name: combo[j] for j, (name, _) in enumerate(numeric)}
                      for combo in combos]
        else:
            points = []
            for i in range(count):
                pt = {}
                for name, lev in numeric:
                    pt[name] = lev[(i * 2654435761) % len(lev)]
                points.append(pt)
    else:
        points = []
        for _ in range(count):
            pt = {}
            for name, lev in numeric:
                pt[name] = rng.uniform(lev[0], lev[-1])
            points.append(pt)
    for pt in points:
        for k in list(pt):
            try:
                pt[k] = round(float(pt[k]), 6)
            except (TypeError, ValueError):
                pass
    return points


# ---------- 子命令 ----------

def cmd_init(args):
    errors, degradations = [], []
    started_at = utcnow()
    digest = make_input_digest(args.input)
    if digest.get("case_json_error"):
        degradations.append("case.json 解析失败：%s" % digest["case_json_error"])
    case_id = digest.get("case_id")
    files = digest.get("files", [])
    if not case_id:
        degradations.append("未能从 case.json 提取 case_id")
    if not files:
        degradations.append("空输入目录：无候选输入文件")

    status = "partial" if case_id else "insufficient_evidence"
    if write_json(os.path.join(args.work, "input_digest.json"), digest) is None:
        errors.append("写 input_digest.json 失败")

    plan_path = os.path.join(args.work, "plan.json")
    plan = load_plan_safe(plan_path)
    if plan is None or not (plan.get("case_id") or "").strip():
        plan = new_skeleton_plan(case_id, status)
        if write_json(plan_path, plan) is None:
            errors.append("写 work/plan.json 失败")
    else:
        if not plan.get("case_id"):
            plan["case_id"] = case_id or ""
        if plan.get("status") not in STATUS_ENUM:
            plan["status"] = status

    result = build_result(plan, case_id, started_at, args.output)
    result["errors"] = (result.get("errors") or []) + errors
    result["degradations"] = (result.get("degradations") or []) + degradations
    if write_json(os.path.join(args.output, "result.json"), result) is None:
        errors.append("写 result.json 骨架失败")

    return {"cmd": "init", "case_id": case_id, "status": result["status"],
            "input_files": len(files), "errors": errors,
            "degradations": degradations}


def cmd_finalize(args):
    errors, degradations = [], []
    started_at = utcnow()
    plan_path = os.path.join(args.work, "plan.json")
    plan = load_plan_safe(plan_path)
    if plan is None:
        plan = new_skeleton_plan(None, "insufficient_evidence")
        degradations.append("work/plan.json 缺失或不可读，使用骨架")

    digest = make_input_digest(args.input)
    case_id = plan.get("case_id") or digest.get("case_id") or ""
    plan["case_id"] = plan.get("case_id") or case_id
    if plan.get("status") not in STATUS_ENUM:
        plan["status"] = "partial"

    result = build_result(plan, case_id, started_at, args.output)
    if write_text(os.path.join(args.output, "plan.md"), render_plan_md(plan)) is None:
        errors.append("写 plan.md 失败")
    if write_json(os.path.join(args.output, "plan.json"), plan) is None:
        errors.append("写 plan.json 失败")
    if write_json(os.path.join(args.output, "result.json"), result) is None:
        errors.append("写 result.json 失败")

    result["errors"] = (result.get("errors") or []) + errors
    result["degradations"] = (result.get("degradations") or []) + degradations
    write_json(os.path.join(args.output, "result.json"), result)

    return {"cmd": "finalize", "case_id": result["case_id"],
            "status": result["status"], "errors": errors,
            "degradations": degradations,
            "artifacts": [a["path"] for a in result["artifacts"]]}


def cmd_doe(args):
    plan = load_plan_safe(os.path.join(args.work, "plan.json"))
    if plan is None:
        plan = new_skeleton_plan(None, "insufficient_evidence")
    strat = plan.get("search_strategy") or {}
    seed = args.seed if args.seed is not None else strat.get("seed", 0)
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        seed = 0
    method = strat.get("method") or "grid"
    count = strat.get("candidate_count", 8)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 8
    count = max(1, min(count, 128))

    points = generate_points(plan.get("parameters") or [], method, count, seed)
    out = {"case_id": plan.get("case_id") or "", "seed": seed,
           "method": method, "candidate_count": count,
           "generated_at": utcnow(), "points": points,
           "note": "确定性候选点；仅含可数值化参数，非数值参数见 plan.parameters"}
    path = write_json(os.path.join(args.work, "candidates.json"), out)
    return {"cmd": "doe", "count": len(points), "seed": seed,
            "method": method, "path": path,
            "error": None if path else "写 candidates.json 失败"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="发现输入并落盘骨架")
    p_init.add_argument("--input", default=DEFAULT_INPUT)
    p_init.add_argument("--output", default=DEFAULT_OUTPUT)
    p_init.add_argument("--work", default=DEFAULT_WORK)

    p_fin = sub.add_parser("finalize", help="合并 plan.json 并渲染产物")
    p_fin.add_argument("--input", default=DEFAULT_INPUT)
    p_fin.add_argument("--output", default=DEFAULT_OUTPUT)
    p_fin.add_argument("--work", default=DEFAULT_WORK)

    p_doe = sub.add_parser("doe", help="确定性生成候选点")
    p_doe.add_argument("--work", default=DEFAULT_WORK)
    p_doe.add_argument("--seed", type=int, default=None)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            summary = cmd_init(args)
        elif args.cmd == "finalize":
            summary = cmd_finalize(args)
        elif args.cmd == "doe":
            summary = cmd_doe(args)
        else:
            summary = {"error": "unknown command"}
    except Exception as exc:  # 顶层兜底：任何异常都不崩溃
        summary = {"error": "%s: %s" % (type(exc).__name__, exc),
                   "degraded": True}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
