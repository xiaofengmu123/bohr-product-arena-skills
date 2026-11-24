#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Experiment parameter optimization Skill — orchestrator (standard library only).

Subcommands
    init      discover input files, write /app/work/input_digest.json, five
              intermediate-representation skeletons, plan.json skeleton and an
              /app/output/result.json skeleton (status=partial)
    trace     append a provenance event to /app/work/trace.json
    doe       deterministic candidate-point generation into search_model.json
              and /app/work/candidates.json
    finalize  assemble intermediates into plan.json, render /app/output/plan.md,
              write /app/output/result.json and /app/output/plan.json

Defaults: --input /input --output /app/output --work /app/work
All paths may be overridden for local testing.
"""

import argparse
import json
import os
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
SCHEMA_VERSION = "1.0"
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log"}

PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "uncertainty", "limitations",
                  "pending_human", "change_log"]

INTERMEDIATE_FILES = [
    "input_digest.json", "constraint_model.json", "response_model.json",
    "search_model.json", "evidence_map.json", "tradeoff_model.json",
    "trace.json",
]


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
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
    except Exception as exc:  # noqa: BLE001
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
            rel = os.path.relpath(abs_path, input_dir).replace("\\", "/")
            if size > MAX_INPUT_BYTES:
                found.append((rel, abs_path, size, "skipped:too_large"))
                continue
            ext = os.path.splitext(name)[1].lower()
            found.append((rel, abs_path, size, "text" if ext in TEXT_EXTS else "binary"))
    return found


def extract_case_id(obj):
    if isinstance(obj, dict):
        for key in ("case_id", "caseId", "case", "id", "challenge_id",
                    "challengeId", "armiId"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def make_input_digest(input_dir):
    digest = {"schema_version": SCHEMA_VERSION, "input_dir": input_dir,
              "files": [], "case_id": None, "case_json_error": None,
              "task_md_preview": None}
    for rel, abs_path, size, kind in discover_inputs(input_dir):
        entry = {"path": rel, "size": size, "kind": kind}
        if kind == "text":
            text = read_text_safe(abs_path)
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
        if base in ("task.md", "task.txt", "readme.md") and entry.get("preview"):
            digest["task_md_preview"] = entry["preview"]
    return digest


def _model(case_id, **kw):
    obj = {"schema_version": SCHEMA_VERSION, "case_id": case_id or ""}
    obj.update(kw)
    return obj


def new_constraint_model(case_id):
    return _model(case_id, feasibility={"status": "insufficient_evidence",
                                        "violations": []},
                  parameters=[], constraints=[])


def new_response_model(case_id):
    return _model(case_id, metrics=[], relations=[], model_note="")


def new_search_model(case_id):
    return _model(case_id, method="", initial_points=[], per_round="",
                  stop_criteria=[], candidate_count=8, seed=0,
                  reproducible=True, candidates=[])


def new_evidence_map(case_id):
    return _model(case_id, sources=[])


def new_tradeoff_model(case_id):
    return _model(case_id, budget={"currency": "CNY", "total": None,
                                   "time_hours": None},
                  cost_items=[], total="", time_hours=None,
                  tradeoff_note="", acceptance_criteria="")


def new_trace(case_id):
    return _model(case_id, events=[])


def new_skeleton_plan(case_id):
    return {
        "schema_version": SCHEMA_VERSION,
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
                            "stop_criteria": [], "candidate_count": 8,
                            "seed": 0, "reproducible": True, "candidates": []},
        "cost_budget": {"items": [], "total": "", "time_hours": None,
                        "tradeoff_note": "", "acceptance_criteria": ""},
        "basis": [],
        "risks": [],
        "exception_handling": [],
        "confirmations": [],
        "sources": [],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumptions": [], "prediction_uncertainty": []},
        "limitations": [],
        "pending_human": [],
        "change_log": [],
    }


def cmd_init(args):
    digest = make_input_digest(args.input)
    case_id = digest.get("case_id") or ""
    write_json(os.path.join(args.work, "input_digest.json"), digest)
    write_json(os.path.join(args.work, "constraint_model.json"),
               new_constraint_model(case_id))
    write_json(os.path.join(args.work, "response_model.json"),
               new_response_model(case_id))
    write_json(os.path.join(args.work, "search_model.json"),
               new_search_model(case_id))
    write_json(os.path.join(args.work, "evidence_map.json"),
               new_evidence_map(case_id))
    write_json(os.path.join(args.work, "tradeoff_model.json"),
               new_tradeoff_model(case_id))
    trace = new_trace(case_id)
    trace["events"].append({"at": utcnow(), "stage": "A",
                            "action": "骨架落盘",
                            "files": ["input_digest.json",
                                      "constraint_model.json",
                                      "response_model.json",
                                      "search_model.json",
                                      "evidence_map.json",
                                      "tradeoff_model.json",
                                      "trace.json"]})
    write_json(os.path.join(args.work, "trace.json"), trace)
    plan = new_skeleton_plan(case_id)
    plan["change_log"].append({"at": utcnow(), "stage": "A",
                               "message": "骨架落盘"})
    write_json(os.path.join(args.work, "plan.json"), plan)
    write_json(os.path.join(args.output, "result.json"), skeleton_result(plan))
    print("init ok; case_id=%r; inputs=%d" % (case_id, len(digest["files"])))
    return 0


def skeleton_result(plan):
    return {
        "case_id": plan.get("case_id", ""),
        "status": plan.get("status", "partial"),
        "title": plan.get("title", ""),
        "plan": plan,
        "artifacts": {"plan.md": "", "plan.json": ""},
        "sources": plan.get("sources", []),
        "uncertainty": plan.get("uncertainty", {}),
        "limitations": plan.get("limitations", []),
        "pending_human": plan.get("pending_human", []),
        "change_log": plan.get("change_log", []),
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": utcnow(), "finished_at": None,
                    "requests_used": None, "seconds_used": None},
        "intermediate_representations": {},
    }


def cmd_trace(args):
    path = os.path.join(args.work, "trace.json")
    trace, err = json_load_safe(path)
    if trace is None:
        print("trace: cannot load %s: %s" % (path, err), file=sys.stderr)
        return 1
    if not isinstance(trace.get("events"), list):
        trace["events"] = []
    trace.setdefault("schema_version", SCHEMA_VERSION)
    trace["events"].append({"at": utcnow(), "stage": args.stage,
                            "action": args.message,
                            "files": args.files or []})
    write_json(path, trace)
    print("trace ok: [%s] %s" % (args.stage, args.message))
    return 0


def _load_work(path):
    obj, err = json_load_safe(path)
    return (obj or {}), err


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _param_axis(param):
    """Deterministic candidate values for one parameter: recommended first,
    then min/max and midpoints snapped to step, sorted ascending."""
    vals = []
    rec = _num(param.get("recommended_value"))
    dom = param.get("domain") or {}
    lo, hi, step = _num(dom.get("min")), _num(dom.get("max")), _num(dom.get("step"))
    if rec is not None:
        vals.append(rec)
    if lo is not None and hi is not None:
        vals.extend([lo, hi])
        if step and step > 0 and hi > lo:
            mid = (lo + hi) / 2.0
            vals.append(mid)
            k = int((mid - lo) / step)
            for i in range(1, 4):
                for v in (mid + i * step, mid - i * step):
                    if lo <= v <= hi:
                        vals.append(v)
    out = []
    for v in vals:
        r = round(v, 6)
        if r not in out:
            out.append(r)
    return sorted(set(out)) if out else ([rec] if rec is not None else [])


def _distance(point, centers, spans, keys):
    d = 0.0
    for i, k in enumerate(keys):
        v = point.get(k)
        if v is None or spans[i] == 0:
            continue
        d += abs(v - centers[i]) / spans[i]
    return d


def generate_candidates(search, constraint):
    params = [p for p in constraint.get("parameters", [])
              if isinstance(p, dict) and (p.get("domain") or {}).get("min") is not None]
    keys = [p.get("id") or ("p%d" % i) for i, p in enumerate(params)]
    axes = {k: _param_axis(p) for k, p in zip(keys, params)}
    rec_point = {}
    for p, k in zip(params, keys):
        rec = _num(p.get("recommended_value"))
        if rec is not None:
            rec_point[k] = round(rec, 6)
    points = []
    seen = set()

    def push(point):
        key = tuple(sorted(point.items()))
        if key in seen:
            return
        seen.add(key)
        points.append(point)

    if rec_point:
        push(dict(rec_point))
    # Full factorial capped by candidate_count, ordered deterministically.
    cap = int(search.get("candidate_count") or 8)
    order_keys = sorted(keys)
    spans = []
    centers = []
    for k in keys:
        vals = axes.get(k) or []
        if len(vals) >= 2:
            spans.append(max(vals) - min(vals))
            centers.append((max(vals) + min(vals)) / 2.0)
        else:
            spans.append(0.0)
            centers.append(vals[0] if vals else 0.0)

    def rec(index, point):
        if len(points) >= cap:
            return
        if index == len(order_keys):
            full = dict(point)
            if full:
                push(full)
            return
        k = order_keys[index]
        for v in axes.get(k, []):
            point[k] = v
            rec(index + 1, point)
            if len(points) >= cap:
                return

    rec(0, {})
    ordered = sorted(points, key=lambda p: (_distance(p, centers, spans, order_keys),
                                            [p.get(k, 0) for k in order_keys]))
    candidates = []
    for i, p in enumerate(ordered[:cap], 1):
        d = _distance(p, centers, spans, order_keys)
        rationale = "推荐中心点" if d == 0 else ("边界/中点候选（距离中心 %.3f）" % d)
        candidates.append({"id": "c%d" % i, "round": 1, "point": p,
                           "rationale": rationale})
    return candidates


def cmd_doe(args):
    search_path = os.path.join(args.work, "search_model.json")
    cons_path = os.path.join(args.work, "constraint_model.json")
    search, err1 = _load_work(search_path)
    constraint, err2 = _load_work(cons_path)
    if err1 or err2:
        print("doe: load error search=%s constraint=%s" % (err1, err2),
              file=sys.stderr)
        return 1
    search.setdefault("schema_version", SCHEMA_VERSION)
    search.setdefault("candidate_count", 8)
    if args.seed is not None:
        search["seed"] = int(args.seed)
    search.setdefault("seed", 0)
    search["reproducible"] = True
    candidates = generate_candidates(search, constraint)
    search["candidates"] = candidates
    write_json(search_path, search)
    write_json(os.path.join(args.work, "candidates.json"),
               {"schema_version": SCHEMA_VERSION,
                "case_id": search.get("case_id", ""),
                "seed": search["seed"], "candidates": candidates})
    print("doe ok: %d candidates (seed=%s)" % (len(candidates), search["seed"]))
    return 0


def _nonempty(x):
    if isinstance(x, (list, dict)):
        return len(x) > 0
    return x not in (None, "", [], {})


def _param_to_plan(p):
    out = {"id": p.get("id", ""), "name": p.get("name", ""),
           "unit": p.get("unit", ""),
           "recommended_value": p.get("recommended_value"),
           "domain": p.get("domain", {}),
           "hard_bounds": p.get("hard_bounds", []),
           "constraints": p.get("constraints", []),
           "status": p.get("status", "unverified"),
           "conflict_note": p.get("conflict_note", "")}
    src = None
    for hb in p.get("hard_bounds", []) or []:
        if isinstance(hb, dict) and hb.get("locator"):
            src = {"locator": hb.get("locator"), "quote": hb.get("quote", "")}
            break
    out["source"] = src
    return out


def _search_to_plan(search, fallback):
    fb = fallback or {}
    return {
        "method": search.get("method") or fb.get("method", ""),
        "initial_points": search.get("initial_points") or fb.get("initial_points", []),
        "per_round": search.get("per_round") or fb.get("per_round", ""),
        "stop_criteria": search.get("stop_criteria") or fb.get("stop_criteria", []),
        "candidate_count": search.get("candidate_count", fb.get("candidate_count", 8)),
        "seed": search.get("seed", fb.get("seed", 0)),
        "reproducible": search.get("reproducible", True),
        "candidates": search.get("candidates") or fb.get("candidates", []),
    }


def _tradeoff_to_plan(td, fallback):
    fb = fallback or {}
    return {
        "items": td.get("cost_items") or fb.get("items", []),
        "total": td.get("total") or fb.get("total", ""),
        "time_hours": td.get("time_hours", fb.get("time_hours")),
        "tradeoff_note": td.get("tradeoff_note") or fb.get("tradeoff_note", ""),
        "acceptance_criteria": td.get("acceptance_criteria")
        or fb.get("acceptance_criteria", ""),
        "budget": td.get("budget", fb.get("budget", {})),
    }


def _merge_change_log(plan_log, trace_events):
    merged = []
    seen = set()
    for item in list(plan_log or []) + list(trace_events or []):
        if not isinstance(item, dict):
            continue
        key = (item.get("stage"), item.get("message") or item.get("action"))
        if key in seen:
            continue
        seen.add(key)
        merged.append({"at": item.get("at", utcnow()),
                       "stage": item.get("stage", ""),
                       "message": item.get("message") or item.get("action", "")})
    return merged


def assemble_plan(plan, work):
    constraint, _ = _load_work(os.path.join(work, "constraint_model.json"))
    response, _ = _load_work(os.path.join(work, "response_model.json"))
    search, _ = _load_work(os.path.join(work, "search_model.json"))
    evidence, _ = _load_work(os.path.join(work, "evidence_map.json"))
    tradeoff, _ = _load_work(os.path.join(work, "tradeoff_model.json"))
    trace, _ = _load_work(os.path.join(work, "trace.json"))
    if _nonempty(constraint.get("parameters")):
        plan["parameters"] = [_param_to_plan(p) for p in constraint["parameters"]]
    if _nonempty(response.get("metrics")):
        plan["response_metrics"] = response["metrics"]
    if response.get("relations"):
        plan.setdefault("response_relations", response["relations"])
    if search.get("method") or search.get("candidates"):
        plan["search_strategy"] = _search_to_plan(search, plan.get("search_strategy"))
    if _nonempty(tradeoff.get("cost_items")):
        plan["cost_budget"] = _tradeoff_to_plan(tradeoff, plan.get("cost_budget"))
    if _nonempty(evidence.get("sources")):
        plan["sources"] = evidence["sources"]
    plan["change_log"] = _merge_change_log(plan.get("change_log"),
                                           trace.get("events"))
    return plan


def _fmt_value(v):
    if isinstance(v, float):
        return ("%g" % v)
    return str(v)


def render_plan_md(plan):
    L = []
    a = L.append
    a("# 实验参数优化方案")
    a("")
    a("- case_id: `%s`" % plan.get("case_id", ""))
    a("- status: `%s`" % plan.get("status", "partial"))
    a("- title: %s" % (plan.get("title") or ""))
    a("")

    def section(title):
        a("")
        a("## %s" % title)
        a("")

    def bullets(items):
        if isinstance(items, dict):
            for k, v in items.items():
                a("- %s: %s" % (k, _fmt_value(v)))
        else:
            for it in items or []:
                if isinstance(it, dict):
                    txt = "; ".join("%s=%s" % (k, _fmt_value(v))
                                    for k, v in it.items() if v not in ("", None, []))
                    a("- " + (txt or json.dumps(it, ensure_ascii=False)))
                else:
                    a("- %s" % it)

    if plan.get("research_question"):
        section("研究问题")
        a(plan["research_question"])
    if plan.get("objective"):
        section("目标")
        a(plan["objective"])
    section("假设")
    bullets(plan.get("assumptions") or ["（未填）"])
    section("变量")
    bullets(plan.get("variables"))
    section("关键参数")
    if plan.get("parameters"):
        a("| 参数 | 单位 | 建议值 | 可行区间 | 步进 | 状态 |")
        a("|---|---|---|---|---|---|")
        for p in plan["parameters"]:
            d = p.get("domain") or {}
            a("| %s | %s | %s | [%s, %s] | %s | %s |" % (
                p.get("name") or p.get("id"), p.get("unit") or "",
                _fmt_value(p.get("recommended_value")),
                _fmt_value(d.get("min")), _fmt_value(d.get("max")),
                _fmt_value(d.get("step")), p.get("status") or ""))
    else:
        a("（未填）")
    section("对照")
    bullets(plan.get("controls"))
    section("步骤")
    bullets(plan.get("procedures"))
    section("资源")
    bullets(plan.get("resources"))
    section("响应指标")
    if plan.get("response_metrics"):
        a("| 指标 | 单位 | 方向 | 目标 | 可接受下限 | 测量方法 |")
        a("|---|---|---|---|---|---|")
        for m in plan["response_metrics"]:
            a("| %s | %s | %s | %s | %s | %s |" % (
                m.get("name") or m.get("id"), m.get("unit") or "",
                m.get("direction") or "", _fmt_value(m.get("target")),
                _fmt_value(m.get("acceptable_min")),
                m.get("measurement_method") or ""))
    else:
        a("（未填）")
    section("判读标准")
    bullets(plan.get("interpretation_criteria"))
    section("搜索策略")
    ss = plan.get("search_strategy") or {}
    a("- 方法: %s" % (ss.get("method") or "（未填）"))
    a("- 初始点: %s" % json.dumps(ss.get("initial_points") or [], ensure_ascii=False))
    a("- 每轮补点: %s" % (ss.get("per_round") or "（未填）"))
    a("- 停止判据: %s" % "; ".join(ss.get("stop_criteria") or []) or "（未填）")
    a("- 候选点: %s" % json.dumps(ss.get("candidates") or [], ensure_ascii=False))
    section("成本与时间预算")
    cb = plan.get("cost_budget") or {}
    bullets(cb.get("items") or [])
    a("- 合计: %s；时间: %s h" % (cb.get("total") or "（未填）",
                                  _fmt_value(cb.get("time_hours"))))
    a("- 取舍说明: %s" % (cb.get("tradeoff_note") or "（未填）"))
    a("- 可接受标准: %s" % (cb.get("acceptance_criteria") or "（未填）"))
    section("依据")
    bullets(plan.get("basis"))
    section("风险")
    bullets(plan.get("risks"))
    section("异常处置")
    bullets(plan.get("exception_handling"))
    section("确认项")
    bullets(plan.get("confirmations"))
    section("证据来源")
    for s in plan.get("sources") or []:
        a("- `%s`: %s" % (s.get("locator") or "", s.get("quote") or ""))
    section("不确定性")
    bullets(plan.get("uncertainty"))
    section("限制")
    bullets(plan.get("limitations"))
    section("待人工确认")
    bullets(plan.get("pending_human"))
    section("变更记录")
    for c in plan.get("change_log") or []:
        a("- [%s] %s: %s" % (c.get("stage") or "", c.get("at") or "",
                             c.get("message") or ""))
    return "\n".join(L) + "\n"


def cmd_finalize(args):
    plan_path = os.path.join(args.work, "plan.json")
    plan, err = json_load_safe(plan_path)
    if plan is None:
        print("finalize: cannot load plan.json: %s" % err, file=sys.stderr)
        return 1
    plan = assemble_plan(plan, args.work)
    plan["status"] = plan.get("status", "partial")
    write_json(plan_path, plan)
    md = render_plan_md(plan)
    md_path = os.path.join(args.output, "plan.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    plan_json_path = os.path.join(args.output, "plan.json")
    write_json(plan_json_path, plan)
    result = skeleton_result(plan)
    result["artifacts"] = {"plan.md": md_path, "plan.json": plan_json_path}
    result["sources"] = plan.get("sources", [])
    result["uncertainty"] = plan.get("uncertainty", {})
    result["limitations"] = plan.get("limitations", [])
    result["pending_human"] = plan.get("pending_human", [])
    result["change_log"] = plan.get("change_log", [])
    result["runtime"]["finished_at"] = utcnow()
    ir = {}
    for name in INTERMEDIATE_FILES:
        ir[name.replace(".json", "")] = os.path.join(args.work, name)
    result["intermediate_representations"] = ir
    write_json(os.path.join(args.output, "result.json"), result)
    print("finalize ok: status=%s" % result["status"])
    return 0


def _add_io_args(parser, suppress):
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--input", default=(argparse.SUPPRESS if suppress
                                            else DEFAULT_INPUT))
    parser.add_argument("--output", default=(argparse.SUPPRESS if suppress
                                             else DEFAULT_OUTPUT))
    parser.add_argument("--work", default=(argparse.SUPPRESS if suppress
                                           else DEFAULT_WORK))
    if not suppress:
        parser.set_defaults(input=DEFAULT_INPUT, output=DEFAULT_OUTPUT,
                            work=DEFAULT_WORK)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Experiment parameter optimization orchestrator")
    _add_io_args(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="discover inputs and write skeletons")
    _add_io_args(p_init, suppress=True)
    tr = sub.add_parser("trace", help="append provenance event")
    _add_io_args(tr, suppress=True)
    tr.add_argument("--stage", default="A")
    tr.add_argument("--message", required=True)
    tr.add_argument("--files", nargs="*")
    do = sub.add_parser("doe", help="deterministic candidate generation")
    _add_io_args(do, suppress=True)
    do.add_argument("--seed", default=0)
    p_fin = sub.add_parser("finalize", help="assemble and render final outputs")
    _add_io_args(p_fin, suppress=True)
    args = ap.parse_args(argv)
    ensure_dir(args.work)
    ensure_dir(args.output)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "trace":
        return cmd_trace(args)
    if args.cmd == "doe":
        return cmd_doe(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    ap.error("unknown command: %s" % args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
