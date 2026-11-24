#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill —— 确定性编排器（仅标准库）。

子命令
    init       发现输入、写 /app/work/input_digest.json，并落盘一版可交付骨架
    finalize   合并 /app/work/plan.json -> /app/output/result.json，渲染可编辑
               /app/output/plan.md，同时写 /app/output/plan.json
    doe        从 plan.json 生成确定性候选点 -> /app/work/candidates.json
    fingerprint 打印输入的确定性指纹（sha256）

默认路径：--input /input --output /app/output --work /app/work
所有路径可被覆盖，便于本地自检。

确定性约定（本包与参考实现的关键差异）
    - 所有 JSON 以 sort_keys=True + LF 换行写出；不含任何墙钟时间。
    - 候选点用固定随机种子，参数按名称排序后求笛卡尔积，可逐位复现。
    - 同一 plan.json 输入，finalize / doe 两次运行产物逐字节一致。
    - result.json 记录 input_fingerprint 与 revision，便于核对输入是否变化。
"""

import argparse
import hashlib
import itertools
import json
import os
import random
import sys
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

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

# 主结果正文键（进入 result.json 的 plan），语义顺序用于渲染 plan.md。
PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "uncertainty", "limitations",
                  "pending_human", "change_log"]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_bytes_safe(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def read_text_safe(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
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
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
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
            rel = os.path.relpath(abs_path, input_dir).replace("\\", "/")
            if size > MAX_INPUT_BYTES:
                found.append((rel, abs_path, size, "skipped:too_large"))
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            found.append((rel, abs_path, size, kind))
    found.sort(key=lambda t: t[0])
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
        "input_fingerprint": None,
    }
    rows = []
    for rel, abs_path, size, kind in files:
        entry = {"path": rel, "size": size, "kind": kind}
        raw = read_bytes_safe(abs_path)
        if raw is not None:
            entry["sha256"] = sha256_bytes(raw)
        if kind == "text":
            text = read_text_safe(abs_path)
            if text is None:
                entry["readable"] = False
            else:
                entry["readable"] = True
                entry["preview"] = text[:2000]
        digest["files"].append(entry)
        if entry.get("sha256"):
            rows.append((rel, size, entry["sha256"]))
    rows.sort()
    digest["input_fingerprint"] = sha256_bytes(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8"))

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
            "method": "",
            "initial_points": [],
            "per_round": "",
            "stop_criteria": [],
            "candidate_count": 8,
            "seed": 0,
        },
        "cost_budget": {
            "items": [],
            "total": "",
            "time_hours": None,
            "tradeoff_note": "",
        },
        "basis": [],
        "risks": [],
        "exception_handling": [],
        "confirmations": [],
        "sources": [],
        "uncertainty": {
            "unverified_values": [],
            "missing_data": [],
            "assumptions": [],
        },
        "limitations": [],
        "pending_human": [],
        "change_log": [
            {"step": "init", "action": "write partial skeleton",
             "reason": "deliver early recoverable result before analysis"}
        ],
    }


def derive_status(plan):
    params = [p for p in (plan.get("parameters") or [])
              if isinstance(p, dict) and p.get("name")]
    metrics = [m for m in (plan.get("response_metrics") or [])
               if isinstance(m, dict) and m.get("name")]
    has_content = bool(plan.get("research_question")
                       or plan.get("objective") or params or metrics)
    if not has_content:
        return "insufficient_evidence"
    hint = plan.get("status_hint")
    if hint in STATUS_ENUM:
        return hint
    if (plan.get("pending_human") or plan.get("limitations")
            or (plan.get("uncertainty") or {}).get("unverified_values")
            or (plan.get("uncertainty") or {}).get("missing_data")):
        return "partial"
    if params and metrics:
        return "ok"
    return "partial"


def canonicalize_plan(plan):
    """把语义等价的 plan.json 归一化：去重、排序无顺序语义的集合字段。"""
    if not isinstance(plan, dict):
        return plan
    for key in ("parameters", "response_metrics"):
        items = plan.get(key) or []
        seen, out = set(), []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(it)
        out.sort(key=lambda d: str(d.get("name", "")).lower())
        plan[key] = out
    sources = plan.get("sources") or []
    sources = [s for s in sources if isinstance(s, dict)]
    sources.sort(key=lambda d: str(d.get("locator", "")))
    plan["sources"] = sources
    return plan


def render_item(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return "; ".join("%s=%s" % (k, v) for k, v in item.items())
    return str(item)


def render_list(lines):
    if not lines:
        return "_（未填写）_\n"
    return "\n".join("- %s" % render_item(x) for x in lines) + "\n"


def render_md(plan, status, meta):
    ss = (plan.get("search_strategy") or {})
    cb = (plan.get("cost_budget") or {})
    out = []
    out.append("# %s" % (plan.get("title") or "实验参数优化方案"))
    out.append("")
    out.append("- case_id: `%s`" % plan.get("case_id", ""))
    out.append("- status: `%s`" % status)
    out.append("- input_fingerprint: `%s`" % meta.get("input_fingerprint", ""))
    out.append("- revision: %d" % meta.get("revision", 0))
    out.append("")

    out.append("## 研究问题与目标")
    out.append("")
    out.append(render_item(plan.get("research_question") or "_（未填写）_"))
    out.append("")
    out.append(render_item(plan.get("objective") or "_（未填写）_"))
    out.append("")

    out.append("## 假设")
    out.append("")
    out.append(render_list(plan.get("assumptions")))
    out.append("## 变量")
    out.append("")
    out.append(render_list(plan.get("variables")))

    out.append("## 关键参数")
    out.append("")
    params = plan.get("parameters") or []
    if params:
        out.append("| 参数 | 单位 | 建议值 | 可行区间 | 步进 | 约束来源 | 依据 quote |")
        out.append("|---|---|---|---|---|---|---|")
        for p in params:
            lo, hi = p.get("min"), p.get("max")
            rng = "-" if lo is None or hi is None else "%s–%s" % (lo, hi)
            out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                p.get("name", ""), p.get("unit", ""), p.get("suggested", "-"),
                rng, p.get("step", "-"), p.get("constraint_source", "-"),
                (p.get("basis_quote") or "-").replace("|", "\\|")))
        out.append("")
    else:
        out.append("_（未填写）_")
        out.append("")

    out.append("## 对照")
    out.append("")
    out.append(render_list(plan.get("controls")))

    out.append("## 步骤")
    out.append("")
    procs = plan.get("procedures") or []
    if procs:
        out.append("\n".join("%d. %s" % (i + 1, render_item(x))
                             for i, x in enumerate(procs)))
        out.append("")
    else:
        out.append("_（未填写）_")
        out.append("")

    out.append("## 资源")
    out.append("")
    out.append(render_list(plan.get("resources")))

    out.append("## 响应指标")
    out.append("")
    metrics = plan.get("response_metrics") or []
    if metrics:
        out.append("| 指标 | 口径 | 单位 | 测量方法 | 方向 | 目标 | 可接受下限 | 预测方式 | 不确定度 |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for m in metrics:
            out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                m.get("name", ""), m.get("definition", "-"), m.get("unit", "-"),
                m.get("method", "-"), m.get("direction", "-"), m.get("target", "-"),
                m.get("accept_lower", "-"), m.get("prediction_method", "-"),
                m.get("uncertainty", "-")))
        out.append("")
    else:
        out.append("_（未填写）_")
        out.append("")

    out.append("## 判读标准")
    out.append("")
    out.append(render_list(plan.get("interpretation_criteria")))

    out.append("## 搜索策略")
    out.append("")
    out.append("- 方法: %s" % ss.get("method", "_（未填写）_"))
    out.append("- 初始点: %s" % "; ".join(map(str, ss.get("initial_points") or [])))
    out.append("- 每轮补点: %s" % ss.get("per_round", "_（未填写）_"))
    out.append("- 候选点数: %s（seed=%s）" % (ss.get("candidate_count", 8),
                                              ss.get("seed", 0)))
    out.append("- 停止判据: %s" % render_list(ss.get("stop_criteria")).strip() or "_（未填写）_")
    out.append("")

    out.append("## 候选点（确定性，可由 `python scripts/main.py doe` 复现）")
    out.append("")
    cands = meta.get("candidates") or []
    if cands:
        out.append("| # | %s |" % " | ".join(cands["param_order"]))
        out.append("|%s|" % "|".join("---" for _ in range(len(cands["param_order"]) + 1)))
        for c in cands["points"]:
            vals = [str(c["point"].get(n, "-")) for n in cands["param_order"]]
            out.append("| %d | %s |" % (c["index"], " | ".join(vals)))
        out.append("")
    else:
        out.append("_（运行 doe 后生成）_")
        out.append("")

    out.append("## 成本与时间预算")
    out.append("")
    items = cb.get("items") or []
    if items:
        out.append("| 项目 | 单价 | 数量 | 小计 | 依据 |")
        out.append("|---|---|---|---|---|")
        for it in items:
            out.append("| %s | %s | %s | %s | %s |" % (
                it.get("name", ""), it.get("unit_cost", "-"),
                it.get("quantity", "-"), it.get("total", "-"),
                it.get("basis", "-")))
        out.append("")
    out.append("- 合计: %s" % cb.get("total", "_（未填写）_"))
    out.append("- 预计时长(h): %s" % cb.get("time_hours", "_（未填写）_"))
    out.append("- 取舍说明: %s" % cb.get("tradeoff_note", "_（未填写）_"))
    out.append("")

    out.append("## 依据")
    out.append("")
    out.append(render_list(plan.get("basis")))
    out.append("## 风险")
    out.append("")
    out.append(render_list(plan.get("risks")))
    out.append("## 异常处置")
    out.append("")
    out.append(render_list(plan.get("exception_handling")))
    out.append("## 确认项")
    out.append("")
    out.append(render_list(plan.get("confirmations")))

    out.append("## 局限与不确定性")
    out.append("")
    out.append(render_list(plan.get("limitations")))
    out.append("## 待人工确认")
    out.append("")
    out.append(render_list(plan.get("pending_human")))

    out.append("## 证据溯源")
    out.append("")
    sources = plan.get("sources") or []
    if sources:
        out.append("| locator | quote | claim |")
        out.append("|---|---|---|")
        for s in sources:
            out.append("| %s | %s | %s |" % (
                s.get("locator", ""),
                (s.get("quote", "") or "").replace("|", "\\|"),
                s.get("claim", "")))
        out.append("")
    else:
        out.append("_（未填写）_")
        out.append("")

    out.append("## 变更日志")
    out.append("")
    for entry in plan.get("change_log") or []:
        out.append("- [%s] %s —— %s" % (entry.get("step", ""),
                                       entry.get("action", ""),
                                       entry.get("reason", "")))
    out.append("")
    return "\n".join(out)


def _build_result(plan, status, digest, errors, degradations, out_dir, work_dir):
    meta = {
        "case_id": plan.get("case_id", ""),
        "status": status,
        "input_fingerprint": digest.get("input_fingerprint") if digest else None,
    }
    candidates = None
    cand_path = os.path.join(work_dir, "candidates.json")
    if os.path.isfile(cand_path):
        obj, err = json_load_safe(cand_path)
        if err is None and isinstance(obj, dict):
            candidates = obj
    meta["candidates"] = candidates
    meta["revision"] = len(plan.get("change_log") or [])

    plan_doc = dict(plan)
    plan_doc.pop("status_hint", None)
    plan_doc.pop("case_id", None)
    for key in PLAN_CONTENT_KEYS + PLAN_META_KEYS:
        plan_doc.setdefault(key, [] if key not in ("title", "research_question",
                                                  "objective") else "")

    result = {
        "case_id": plan.get("case_id", ""),
        "status": status,
        "plan": {k: plan_doc.get(k) for k in PLAN_CONTENT_KEYS},
        "title": plan_doc.get("title", ""),
        "sources": plan_doc.get("sources", []),
        "uncertainty": plan_doc.get("uncertainty", {}),
        "limitations": plan_doc.get("limitations", []),
        "pending_human": plan_doc.get("pending_human", []),
        "change_log": plan_doc.get("change_log", []),
        "errors": errors,
        "degradations": degradations,
        "artifacts": [],
        "input_fingerprint": meta["input_fingerprint"],
        "revision": meta["revision"],
    }
    return result, meta, plan_doc


def cmd_init(argv):
    ap = argparse.ArgumentParser(prog="main.py init")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    a = ap.parse_args(argv)

    ensure_dir(a.output)
    ensure_dir(a.work)
    digest = make_input_digest(a.input)
    write_json(os.path.join(a.work, "input_digest.json"), digest)

    case_id = digest.get("case_id") or ""
    plan_path = os.path.join(a.work, "plan.json")
    if os.path.isfile(plan_path):
        existing, err = json_load_safe(plan_path)
        if err is None and isinstance(existing, dict) and (
                existing.get("research_question")
                or existing.get("parameters") or existing.get("objective")):
            # 保留已有进展，不覆盖 Agent 的工作。
            print("init: existing non-empty plan preserved at %s" % plan_path)
        else:
            write_json(plan_path, new_skeleton_plan(case_id))
    else:
        write_json(plan_path, new_skeleton_plan(case_id))

    result_path = os.path.join(a.output, "result.json")
    if not os.path.isfile(result_path):
        skeleton = new_skeleton_plan(case_id)
        result, meta, plan_doc = _build_result(
            skeleton, "partial", digest, [], [], a.output, a.work)
        result["artifacts"] = _artifact_list(a.output)
        write_json(result_path, result)
        write_json(os.path.join(a.output, "plan.json"),
                   {k: skeleton.get(k) for k in PLAN_CONTENT_KEYS + PLAN_META_KEYS})
        with open(os.path.join(a.output, "plan.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(render_md(skeleton, "partial", meta))
    else:
        print("init: existing result.json preserved")
    print("init: case_id=%r fingerprint=%s files=%d"
          % (case_id, digest.get("input_fingerprint"),
             len(digest.get("files", []))))
    return 0


def _artifact_list(output_dir):
    arts = []
    for name in ("plan.md", "plan.json", "candidates.json"):
        p = os.path.join(output_dir, name)
        if os.path.isfile(p):
            arts.append({"path": name, "type": name.rsplit(".", 1)[-1],
                         "description": "editable artifact"})
    return arts


def cmd_finalize(argv):
    ap = argparse.ArgumentParser(prog="main.py finalize")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    a = ap.parse_args(argv)

    ensure_dir(a.output)
    errors, degradations = [], []

    plan, err = json_load_safe(os.path.join(a.work, "plan.json"))
    if plan is None or not isinstance(plan, dict):
        plan = new_skeleton_plan("")
        errors.append("plan.json unreadable: %s" % err)

    plan = canonicalize_plan(plan)
    status = derive_status(plan)

    digest = None
    d, derr = json_load_safe(os.path.join(a.work, "input_digest.json"))
    if derr is None and isinstance(d, dict):
        digest = d
    else:
        digest = make_input_digest(a.input)
        degradations.append("input_digest.json missing; recomputed from input")

    # 把输入层的可恢复问题如实带入 result.errors/degradations。
    if isinstance(digest, dict):
        if digest.get("case_json_error"):
            errors.append("case.json parse issue: %s" % digest["case_json_error"])
        for entry in digest.get("files") or []:
            if entry.get("kind") == "text" and entry.get("readable") is False:
                degradations.append("unreadable text file: %s" % entry.get("path"))
            if str(entry.get("kind", "")).startswith("skipped"):
                degradations.append("skipped oversized input: %s" % entry.get("path"))

    # 追加（去重的）finalize 变更日志条目。
    log = plan.get("change_log") or []
    entry = {"step": "finalize", "action": "render result.json/plan.md/plan.json",
             "reason": "merge canonical plan into deliverable artifacts"}
    if entry not in log:
        log = list(log) + [entry]
    plan["change_log"] = log

    # 候选点若已生成，复制到输出目录供复核。
    cand_src = os.path.join(a.work, "candidates.json")
    if os.path.isfile(cand_src):
        cand, cerr = json_load_safe(cand_src)
        if cerr is None and isinstance(cand, dict):
            write_json(os.path.join(a.output, "candidates.json"), cand)
        else:
            degradations.append("candidates.json unreadable: %s" % cerr)

    result, meta, plan_doc = _build_result(
        plan, status, digest, errors, degradations, a.output, a.work)
    write_json(os.path.join(a.output, "result.json"), result)
    write_json(os.path.join(a.output, "plan.json"),
               {k: plan_doc.get(k) for k in PLAN_CONTENT_KEYS + PLAN_META_KEYS})
    with open(os.path.join(a.output, "plan.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(render_md(plan, status, meta))
    result["artifacts"] = _artifact_list(a.output)
    write_json(os.path.join(a.output, "result.json"), result)

    print("finalize: status=%s revision=%d fingerprint=%s"
          % (status, meta["revision"], meta["input_fingerprint"]))
    return 0


def to_dec(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _quantize6(d):
    q = d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return q.normalize()


def _fmt_num(d):
    q = _quantize6(d)
    if q == q.to_integral_value():
        return int(q)
    return float(q)


def gen_levels(p):
    lo, hi = to_dec(p.get("min")), to_dec(p.get("max"))
    if lo is None or hi is None:
        return None
    if lo > hi:
        lo, hi = hi, lo
    step = to_dec(p.get("step"))
    n_levels = int(p.get("n_levels") or 5)
    if step and step > 0:
        vals, v = [], lo
        while v <= hi + step / 2:
            vals.append(min(v, hi))
            v += step
    else:
        n_levels = max(2, n_levels)
        vals = [lo + (hi - lo) * Decimal(i) / Decimal(n_levels - 1)
                for i in range(n_levels)]
    seen, out = set(), []
    for v in vals:
        if v < lo:
            continue
        if v > hi:
            v = hi
        key = _quantize6(v)
        if key not in seen:
            seen.add(key)
            out.append(_fmt_num(key))
    return out


def cmd_doe(argv):
    ap = argparse.ArgumentParser(prog="main.py doe")
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)

    plan, err = json_load_safe(os.path.join(a.work, "plan.json"))
    if plan is None or not isinstance(plan, dict):
        print("doe: cannot read plan.json: %s" % err)
        return 1
    ss = plan.get("search_strategy") or {}
    seed = a.seed if a.seed is not None else int(ss.get("seed", 0) or 0)
    candidate_count = int(ss.get("candidate_count", 8) or 8)

    params = [p for p in (plan.get("parameters") or [])
              if isinstance(p, dict) and p.get("name") and p.get("min") is not None
              and p.get("max") is not None]
    names = sorted(p["name"] for p in params)
    levels = {p["name"]: gen_levels(p) for p in params
              if p.get("name") in names}
    names = [n for n in names if levels.get(n)]
    if not names:
        write_json(os.path.join(a.work, "candidates.json"),
                   {"points": [], "param_order": [], "seed": seed,
                    "candidate_count": candidate_count, "total": 0,
                    "note": "no feasible numeric parameters with min/max"})
        print("doe: no feasible parameters; wrote empty candidates")
        return 0

    combos = list(itertools.product(*(levels[n] for n in names)))
    total = len(combos)
    rng = random.Random(seed)
    if total > candidate_count:
        combos = rng.sample(combos, candidate_count)
    points = [dict(zip(names, combo)) for combo in combos]
    points.sort(key=lambda d: tuple((n, d[n]) for n in names))
    payload = {
        "seed": seed,
        "candidate_count": candidate_count,
        "total": total,
        "param_order": names,
        "points": [{"index": i, "point": p} for i, p in enumerate(points)],
    }
    write_json(os.path.join(a.work, "candidates.json"), payload)
    print("doe: seed=%d params=%d grid_total=%d selected=%d"
          % (seed, len(names), total, len(points)))
    return 0


def cmd_fingerprint(argv):
    ap = argparse.ArgumentParser(prog="main.py fingerprint")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    a = ap.parse_args(argv)
    digest = make_input_digest(a.input)
    print(digest.get("input_fingerprint") or "")
    return 0


def run(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] in ("init", "finalize", "doe", "fingerprint"):
        fn = {"init": cmd_init, "finalize": cmd_finalize,
              "doe": cmd_doe, "fingerprint": cmd_fingerprint}[argv[0]]
        return fn(argv[1:])
    print("unknown subcommand: %s" % argv[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
