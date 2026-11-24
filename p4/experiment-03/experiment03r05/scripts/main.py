#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""有界实验参数优化 Skill —— 编排器（仅 Python 标准库）。

子命令
    init      发现输入文件，写 /app/work/input_digest.json 与
              /app/output/result.json 骨架（status=partial）
    finalize  合并 /app/work/plan.json 到 /app/output/result.json，
              并渲染可编辑 /app/output/plan.md 与 /app/output/plan.json
    doe       从 plan.json 确定性生成候选点，写 /app/work/candidates.json

默认路径：--input /input --output /app/output --work /app/work。
所有路径可覆盖，便于本地测试。同一输入 + 同一 seed 结果完全一致。
"""

import argparse
import hashlib
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
DIRECTION_ENUM = ("maximize", "minimize", "target")
SEARCH_METHOD_ENUM = ("grid", "sequential", "bayesian", "staged_screen", "custom")
BOUNDS_ORIGIN_ENUM = ("device_range", "material_limit", "safety_threshold",
                      "historical", "expert", "unspecified")
CONFIRMATION_KIND_ENUM = ("safety", "ethics", "equipment", "expert", "other")
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log"}

PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]

RESULT_META_KEYS = [
    "case_id", "status", "plan", "artifacts", "sources", "uncertainty",
    "limitations", "pending_human", "change_log", "errors",
    "degradations", "runtime",
]


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_text_safe(path):
    """按 utf-8-sig -> utf-8 -> latin-1 顺序容错读取文本。"""
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


def canonical_dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def hash_json(obj):
    return hashlib.sha256(canonical_dump(obj).encode("utf-8")).hexdigest()[:16]


def _is_number(val):
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def discover_inputs(input_dir):
    """返回输入目录下候选文件列表（相对路径、大小、类别）。"""
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
            rel = os.path.relpath(abs_path, input_dir).replace("\\", "/")
            if size > MAX_INPUT_BYTES:
                found.append({"path": rel, "size": size,
                              "kind": "skipped:too_large"})
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            entry = {"path": rel, "size": size, "kind": kind}
            if kind == "text":
                text = read_text_safe(abs_path)
                entry["readable"] = text is not None
                if text is not None:
                    entry["preview"] = text[:1200]
            found.append(entry)
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
        "files": files,
        "case_id": None,
        "case_json_error": None,
        "case_obj": None,
        "task_md_preview": None,
    }
    for entry in files:
        base = os.path.basename(entry["path"]).lower()
        abs_path = os.path.join(input_dir, entry["path"].replace("/", os.sep))
        if base in ("case.json", "input.json"):
            obj, err = json_load_safe(abs_path)
            digest["case_json_error"] = err
            if obj is not None:
                digest["case_id"] = extract_case_id(obj)
                digest["case_obj"] = obj
        if base in ("task.md", "task.txt", "readme.md"):
            text = read_text_safe(abs_path)
            if text is not None:
                digest["task_md_preview"] = text[:1200]
    return digest


def empty_plan(case_id):
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
            "method": "",
            "initial_points": [],
            "per_round": "",
            "convergence_criteria": [],
            "stop_criteria": [],
            "candidate_count": 8,
            "seed": 0,
            "rounds_planned": 1,
        },
        "cost_budget": {
            "items": [],
            "total": "",
            "time_hours": None,
            "sample_count": None,
            "throughput_note": "",
            "tradeoff_note": "",
        },
        "basis": [],
        "risks": [],
        "exception_handling": [],
        "confirmations": [],
    }


def new_skeleton_result(case_id, plan):
    return {
        "case_id": case_id,
        "status": "partial",
        "plan": plan,
        "artifacts": [],
        "sources": [],
        "uncertainty": {
            "unverified_values": [],
            "missing_data": [],
            "assumptions": [],
            "prediction_notes": "",
        },
        "limitations": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "skeleton",
            "action": "committed_partial_skeleton",
            "detail": "骨架落盘：status=partial，字段齐全，待增量补全。",
            "content_hash": hash_json(plan),
        }],
        "errors": [],
        "degradations": [],
        "runtime": {"seconds": 0, "model_requests": None, "notes": ""},
    }


def _read_input_contract(digest):
    obj = digest.get("case_obj")
    if isinstance(obj, dict) and isinstance(obj.get("output_contract"), dict):
        return obj["output_contract"]
    return None


def _merge_contract_placeholders(result, contract):
    """输入自带 output_contract 时：原样回填未知键，缺值给 null，不删键。"""
    if isinstance(contract, dict):
        result["output_contract"] = contract
        for key in contract:
            if key not in result:
                result[key] = None
    return result


def cmd_init(input_dir, output_dir, work_dir):
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    digest = make_input_digest(input_dir)
    write_json(os.path.join(work_dir, "input_digest.json"), digest)
    case_id = digest.get("case_id") or ""
    plan = empty_plan(case_id)
    result = new_skeleton_result(case_id, plan)
    result = _merge_contract_placeholders(result, _read_input_contract(digest))
    write_json(os.path.join(work_dir, "plan.json"), plan)
    write_json(os.path.join(output_dir, "result.json"), result)
    return {
        "input_digest": os.path.join(work_dir, "input_digest.json"),
        "plan": os.path.join(work_dir, "plan.json"),
        "result": os.path.join(output_dir, "result.json"),
    }


def render_plan_md(plan, case_id):
    lines = ["# %s" % (plan.get("title") or "实验参数方案"), ""]
    lines.append("- case_id: `%s`" % (case_id or ""))
    lines.append("- status: `%s`" % (plan.get("status") or "partial"))
    lines.append("")
    lines.append("## 研究问题")
    lines.append("")
    lines.append(str(plan.get("research_question") or "（未填）"))
    lines.append("")
    lines.append("## 目标")
    lines.append("")
    lines.append(str(plan.get("objective") or "（未填）"))
    lines.append("")
    lines.append("## 假设")
    lines.append("")
    for a in plan.get("assumptions") or []:
        if isinstance(a, dict):
            lines.append("- %s（证据：%s）" % (a.get("text", "?"), a.get("evidence", "")))
        else:
            lines.append("- %s" % a)
    lines.append("")
    lines.append("## 变量")
    lines.append("")
    for v in plan.get("variables") or []:
        if isinstance(v, dict):
            lines.append("- %s（%s，%s，角色=%s）" % (
                v.get("name", "?"), v.get("type", "?"),
                v.get("unit", ""), v.get("role", "")))
        else:
            lines.append("- %s" % v)
    lines.append("")
    lines.append("## 关键参数")
    lines.append("")
    lines.append("| 参数 | 单位 | 建议值 | 可行区间 | 步进 | 约束来源 |")
    lines.append("|-|-|-|-|-|-|")
    for p in plan.get("parameters") or []:
        if not isinstance(p, dict):
            continue
        rng = p.get("feasible_range") or {}
        lines.append("| %s | %s | %s | [%s, %s] | %s | %s |" % (
            p.get("name", "?"), p.get("unit", ""), p.get("suggested_value", ""),
            rng.get("min", ""), rng.get("max", ""), p.get("step", ""),
            p.get("constraint_source", "")))
    lines.append("")
    lines.append("## 响应指标")
    lines.append("")
    for m in plan.get("response_metrics") or []:
        if isinstance(m, dict):
            lines.append("- %s（%s）：%s，单位 %s，方向 %s，目标 %s，下限 %s" % (
                m.get("name", "?"), m.get("definition", ""),
                m.get("measurement_method", ""), m.get("unit", ""),
                m.get("direction", ""), m.get("target", ""),
                m.get("acceptable_lower_bound", "")))
    lines.append("")
    lines.append("## 对照与步骤")
    lines.append("")
    for c in plan.get("controls") or []:
        if isinstance(c, dict):
            lines.append("- 对照：%s = %s" % (c.get("name", "?"), c.get("value", "")))
    for pr in plan.get("procedures") or []:
        if isinstance(pr, dict):
            lines.append("- 步骤：%s（%s）" % (pr.get("step", "?"), pr.get("note", "")))
        else:
            lines.append("- 步骤：%s" % pr)
    lines.append("")
    lines.append("## 搜索策略")
    ss = plan.get("search_strategy") or {}
    lines.append("")
    lines.append("- 方法：%s；候选点 %s；seed %s" % (
        ss.get("method", ""), ss.get("candidate_count", ""), ss.get("seed", "")))
    lines.append("- 停止判据：%s" % "；".join(ss.get("stop_criteria") or []))
    lines.append("")
    lines.append("## 成本与预算")
    lines.append("")
    cb = plan.get("cost_budget") or {}
    lines.append("- 合计：%s" % cb.get("total", ""))
    lines.append("- 取舍：%s" % cb.get("tradeoff_note", ""))
    lines.append("")
    lines.append("## 风险与异常处置")
    lines.append("")
    for r in plan.get("risks") or []:
        if isinstance(r, dict):
            lines.append("- 风险：%s（缓解：%s）" % (r.get("risk", "?"), r.get("mitigation", "")))
    for e in plan.get("exception_handling") or []:
        if isinstance(e, dict):
            lines.append("- 异常：%s → %s" % (e.get("condition", "?"), e.get("action", "")))
    lines.append("")
    lines.append("## 确认项")
    lines.append("")
    for c in plan.get("confirmations") or []:
        if isinstance(c, dict):
            lines.append("- %s（%s，%s）" % (c.get("item", "?"), c.get("kind", ""), c.get("status", "")))
    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_finalize(input_dir, output_dir, work_dir):
    ensure_dir(output_dir)
    plan_path = os.path.join(work_dir, "plan.json")
    plan_obj, _err = json_load_safe(plan_path)
    if not isinstance(plan_obj, dict):
        plan_obj = empty_plan("")
    case_id = plan_obj.get("case_id") or ""
    result_path = os.path.join(output_dir, "result.json")
    existing, _e = json_load_safe(result_path)
    prev_log = []
    prev_contract = None
    if isinstance(existing, dict):
        prev_log = existing.get("change_log")
        prev_contract = existing.get("output_contract")
    if not isinstance(prev_log, list):
        prev_log = []
    result = new_skeleton_result(case_id, plan_obj)
    result["change_log"] = list(prev_log) + [{
        "time": utcnow(),
        "step": "finalize",
        "action": "merged_plan_into_result",
        "detail": "将 plan.json 合并进 result.json 并渲染 plan.md。",
        "content_hash": hash_json(plan_obj),
    }]
    if isinstance(plan_obj.get("status"), str) and plan_obj["status"] in STATUS_ENUM:
        result["status"] = plan_obj["status"]
    if prev_contract is not None:
        result = _merge_contract_placeholders(result, prev_contract)
    result["artifacts"] = [
        {"path": os.path.join(output_dir, "plan.md"), "kind": "markdown",
         "role": "editable_plan"},
        {"path": os.path.join(output_dir, "plan.json"), "kind": "json",
         "role": "structured_plan"},
    ]
    write_json(os.path.join(output_dir, "plan.json"), plan_obj)
    with open(os.path.join(output_dir, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(render_plan_md(plan_obj, case_id))
    write_json(result_path, result)
    return result_path


def _param_levels(p):
    lo = float(p["feasible_range"]["min"])
    hi = float(p["feasible_range"]["max"])
    step = float(p.get("step") or 1.0)
    if step <= 0:
        step = 1.0
    vals = []
    v = lo
    guard = 0
    while v <= hi + 1e-9 and guard < 60:
        vals.append(v)
        v = round(v + step, 9)
        guard += 1
    if not vals:
        vals = [lo]
    return vals


def _grid_points(params):
    levels = [_param_levels(p) for p in params]
    total = 1
    for lv in levels:
        total *= len(lv)
    if total > 512:
        trimmed = []
        for lv in levels:
            if len(lv) > 4:
                idx = sorted(set([0, len(lv) // 3, 2 * len(lv) // 3, len(lv) - 1]))
                lv = [lv[i] for i in idx]
            trimmed.append(lv)
        levels = trimmed
    points = [{}]
    for p, lv in zip(params, levels):
        new_points = []
        for pt in points:
            for val in lv:
                q = dict(pt)
                q[p["name"]] = val
                new_points.append(q)
        points = new_points
    return points


def generate_candidates(plan, seed=None):
    ss = plan.get("search_strategy") or {}
    seed = ss.get("seed", 0) if seed is None else seed
    count = int(ss.get("candidate_count") or 8)
    if count < 1:
        count = 1
    candidates = []
    n = 1
    for ip in ss.get("initial_points") or []:
        if not isinstance(ip, dict):
            continue
        candidates.append({
            "id": "c%d" % n,
            "params": ip.get("params") or {},
            "origin": "initial",
            "round": ip.get("round", 0),
            "reason": ip.get("reason", ""),
        })
        n += 1
    remaining = count - len(candidates)
    gridable = [p for p in (plan.get("parameters") or [])
                if isinstance(p, dict)
                and isinstance(p.get("feasible_range"), dict)
                and _is_number(p["feasible_range"].get("min"))
                and _is_number(p["feasible_range"].get("max"))]
    if remaining > 0 and gridable:
        grid = _grid_points(gridable)
        rng = random.Random(seed)
        order = list(range(len(grid)))
        rng.shuffle(order)
        chosen = sorted(order[:remaining])
        for idx in chosen:
            candidates.append({"id": "c%d" % n, "params": grid[idx],
                               "origin": "grid", "round": 1})
            n += 1
    return candidates


def cmd_doe(work_dir, seed):
    ensure_dir(work_dir)
    plan_path = os.path.join(work_dir, "plan.json")
    plan_obj, err = json_load_safe(plan_path)
    if not isinstance(plan_obj, dict):
        plan_obj = empty_plan("")
        plan_obj["_read_error"] = err
    candidates = generate_candidates(plan_obj, seed)
    out = {"case_id": plan_obj.get("case_id") or "", "seed": seed,
           "candidate_count": len(candidates), "candidates": candidates}
    write_json(os.path.join(work_dir, "candidates.json"), out)
    return out


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="有界实验参数优化 Skill 编排器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="发现输入并落盘 result.json 骨架")
    p_init.add_argument("--input", default=DEFAULT_INPUT)
    p_init.add_argument("--output", default=DEFAULT_OUTPUT)
    p_init.add_argument("--work", default=DEFAULT_WORK)
    p_fin = sub.add_parser("finalize", help="合并 plan.json 并渲染 plan.md")
    p_fin.add_argument("--input", default=DEFAULT_INPUT)
    p_fin.add_argument("--output", default=DEFAULT_OUTPUT)
    p_fin.add_argument("--work", default=DEFAULT_WORK)
    p_doe = sub.add_parser("doe", help="确定性生成候选点")
    p_doe.add_argument("--work", default=DEFAULT_WORK)
    p_doe.add_argument("--seed", type=int, default=0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        paths = cmd_init(args.input, args.output, args.work)
        print(json.dumps(paths, ensure_ascii=False))
    elif args.cmd == "finalize":
        print(cmd_finalize(args.input, args.output, args.work))
    elif args.cmd == "doe":
        out = cmd_doe(args.work, args.seed)
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
