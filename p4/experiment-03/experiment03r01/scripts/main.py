#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly-skill orchestrator — 实验参数优化建议（仅标准库）。

Subcommands
    init      发现输入文件，写 /app/work/input_digest.json，并落盘 result.json 骨架
              （status=partial；任何中途异常都不会留下不完整产物）。
    finalize  合并 /app/work/plan.json -> /app/output/result.json，并渲染可编辑的
              plan.md 与 plan.json 两个产物。
    doe       由 plan.json 的可行域 + 搜索策略，确定性生成候选点 -> work/candidates.json。
    evidence  校验 evidence_map/claims 的逐字溯源完整性（每条结论 locator + quote）。
    check     校验 /app/output/result.json 契约、evidence_map 结构与产物文件存在性。

默认路径: --input /input --output /app/output --work /app/work
所有路径均可覆盖，便于本地自检。脚本只依赖 Python 标准库，跨平台可运行。
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
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log"}

# 主结果体（plan）内容键 vs 元数据键：用于 finalize 拆分 plan 与 result 顶层。
PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "claims", "uncertainty", "limitations",
                  "pending_human", "change_log"]

CLAIM_TYPES = {
    "parameter_value", "parameter_domain", "constraint", "response_metric",
    "predicted_response", "cost_basis", "risk", "assumption", "control",
    "resource", "procedure", "interpretation",
}
CLAIM_STATUS = {"supported", "inferred", "unverified", "conflicting", "pending_human"}


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_text_safe(path):
    """容错读取文本：utf-8-sig -> utf-8 -> latin-1；失败返回 None。"""
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
        return None, "cannot read file: %s" % path
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
    """扫描输入目录，返回 (relpath, abs_path, size, kind) 列表。"""
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
        "started_at": utcnow(),
        "files": [],
        "case_id": None,
        "case_json_error": None,
        "task_md_preview": None,
    }
    for rel, abs_path, size, kind in files:
        entry = {"path": rel, "size": size, "kind": kind}
        if kind == "text":
            text = read_text_safe(abs_path)
            entry["readable"] = text is not None
            if text is not None:
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
        "claims": [],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumption_risk": []},
        "limitations": [],
        "pending_human": [],
        "change_log": [{"time": utcnow(), "entry": "骨架落盘（init）"}],
    }


def read_case_id_from_digest(work_dir):
    obj, _err = json_load_safe(os.path.join(work_dir, "input_digest.json"))
    if isinstance(obj, dict):
        return obj.get("case_id")
    return None


def build_evidence_map(plan):
    claims = plan.get("claims", []) or []
    conflict_count = sum(1 for c in claims if c.get("conflicts"))
    unverified_count = sum(1 for c in claims if not c.get("verified"))
    return {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflict_count": conflict_count,
        "unverified_count": unverified_count,
    }


def build_result(plan, work_dir, output_dir):
    plan_body = {k: plan.get(k) for k in PLAN_CONTENT_KEYS}
    plan_body["case_id"] = plan.get("case_id") or ""
    plan_body["status"] = plan.get("status", "partial")
    evidence_map = build_evidence_map(plan)
    result = {
        "case_id": plan.get("case_id") or "",
        "status": plan.get("status", "partial"),
        "plan": plan_body,
        "artifacts": {
            "plan.md": os.path.join(output_dir, "plan.md"),
            "plan.json": os.path.join(output_dir, "plan.json"),
        },
        "sources": plan.get("sources", []),
        "evidence_map": evidence_map,
        "uncertainty": plan.get("uncertainty", {}),
        "limitations": plan.get("limitations", []),
        "pending_human": plan.get("pending_human", []),
        "change_log": plan.get("change_log", []),
        "errors": [],
        "degradations": [],
        "runtime": {
            "seconds": None,
            "started_at": None,
            "finished_at": utcnow(),
            "model_requests": None,
        },
    }
    return result


def _fmt_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def render_plan_md(plan):
    lines = []
    lines.append("# %s" % (plan.get("title") or "实验参数优化方案"))
    lines.append("")
    lines.append("- case_id: `%s`" % plan.get("case_id", ""))
    lines.append("- status: `%s`" % plan.get("status", "partial"))
    lines.append("")
    if plan.get("research_question"):
        lines.append("## 研究问题")
        lines.append("")
        lines.append(str(plan["research_question"]))
        lines.append("")
    if plan.get("objective"):
        lines.append("## 目标")
        lines.append("")
        lines.append(str(plan["objective"]))
        lines.append("")
    sections = [
        ("assumptions", "假设"),
        ("variables", "变量"),
        ("controls", "对照"),
        ("procedures", "步骤"),
        ("parameters", "关键参数"),
        ("resources", "资源"),
        ("response_metrics", "响应指标"),
        ("interpretation_criteria", "判读标准"),
        ("search_strategy", "搜索策略"),
        ("cost_budget", "成本与时间预算"),
        ("basis", "依据"),
        ("risks", "风险"),
        ("exception_handling", "异常处置"),
        ("confirmations", "确认项"),
        ("sources", "证据来源"),
        ("claims", "证据溯源（evidence_map）"),
    ]
    for key, label in sections:
        val = plan.get(key)
        if not val:
            continue
        lines.append("## %s" % label)
        lines.append("")
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    for k, v in item.items():
                        lines.append("- %s: %s" % (k, _fmt_value(v)))
                    lines.append("")
                else:
                    lines.append("- %s" % _fmt_value(item))
        elif isinstance(val, dict):
            for k, v in val.items():
                lines.append("- %s: %s" % (k, _fmt_value(v)))
        else:
            lines.append(str(val))
        lines.append("")
    if plan.get("uncertainty"):
        lines.append("## 不确定性")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(plan["uncertainty"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def generate_candidates(plan, seed):
    """确定性候选点生成：初值点 + 角点 + 单因子扰动 + 种子随机填充。"""
    params = [p for p in plan.get("parameters", []) if isinstance(p, dict)]
    strategy = plan.get("search_strategy") or {}
    count = int(strategy.get("candidate_count", 8)) if strategy.get("candidate_count") else 8
    if count < 1:
        count = 1
    rng = random.Random(int(seed))
    if not params:
        return []
    suggested = {}
    for p in params:
        name = p.get("name", "param")
        suggested[name] = p.get("suggested_value")
    candidates = []
    seen = []

    def _add(pt):
        key = json.dumps(pt, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.append(key)
            candidates.append(pt)

    _add(dict(suggested))
    _add({p.get("name", "p%d" % i): p.get("feasible_range", {}).get("min")
          for i, p in enumerate(params)})
    _add({p.get("name", "p%d" % i): p.get("feasible_range", {}).get("max")
          for i, p in enumerate(params)})

    for p in params:
        name = p.get("name", "param")
        lo = p.get("feasible_range", {}).get("min")
        hi = p.get("feasible_range", {}).get("max")
        sv = p.get("suggested_value")
        step = p.get("step")
        if lo is None or hi is None or sv is None or step is None:
            continue
        for delta in (step, -step):
            v = sv + delta
            if lo <= v <= hi:
                pt = dict(suggested)
                pt[name] = round(v, 10)
                _add(pt)

    while len(candidates) < count:
        pt = {}
        for p in params:
            name = p.get("name", "param")
            lo = p.get("feasible_range", {}).get("min")
            hi = p.get("feasible_range", {}).get("max")
            step = p.get("step")
            if lo is None or hi is None:
                pt[name] = None
                continue
            if isinstance(step, (int, float)) and step > 0:
                n = int(round((hi - lo) / step)) + 1
                idx = rng.randrange(max(n, 1))
                v = lo + idx * step
            else:
                v = lo + rng.random() * (hi - lo)
            pt[name] = round(max(lo, min(hi, v)), 10)
        _add(pt)

    return candidates[:count]


def cmd_init(args):
    ensure_dir(args.work)
    digest = make_input_digest(args.input)
    write_json(os.path.join(args.work, "input_digest.json"), digest)
    plan = new_skeleton_plan(digest.get("case_id"))
    write_json(os.path.join(args.work, "plan.json"), plan)
    # 立即落盘 result.json 骨架：超时/中断仍可交付部分结果。
    result = build_result(plan, args.work, args.output)
    write_json(os.path.join(args.output, "result.json"), result)
    ensure_dir(args.output)
    with open(os.path.join(args.output, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(render_plan_md(result["plan"]))
    write_json(os.path.join(args.output, "plan.json"), result["plan"])
    print("init ok: case_id=%r files=%d" % (digest.get("case_id"), len(digest["files"])))
    return 0


def cmd_finalize(args):
    plan_path = os.path.join(args.work, "plan.json")
    plan, err = json_load_safe(plan_path)
    if plan is None:
        case_id = read_case_id_from_digest(args.work)
        plan = new_skeleton_plan(case_id)
        plan["degradations"] = plan.get("degradations", []) + [err]
    ensure_dir(args.output)
    result = build_result(plan, args.work, args.output)
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.output, "plan.json"), result["plan"])
    with open(os.path.join(args.output, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(render_plan_md(result["plan"]))
    print("finalize ok: %s" % os.path.join(args.output, "result.json"))
    return 0


def cmd_doe(args):
    plan, err = json_load_safe(os.path.join(args.work, "plan.json"))
    if plan is None:
        print("doe: cannot load plan.json: %s" % err, file=sys.stderr)
        return 1
    seed = args.seed if args.seed is not None \
        else plan.get("search_strategy", {}).get("seed", 0)
    candidates = generate_candidates(plan, seed)
    out = {"seed": seed, "candidate_count": len(candidates), "candidates": candidates}
    write_json(os.path.join(args.work, "candidates.json"), out)
    print("doe ok: %d candidates (seed=%s)" % (len(candidates), seed))
    return 0


def _claim_missing(c):
    if not isinstance(c, dict):
        return "claim is not an object"
    prov = c.get("provenance") or []
    if not c.get("claim_id"):
        return "claim_id missing"
    if not c.get("statement"):
        return "statement missing"
    if not prov:
        return "provenance empty"
    for p in prov:
        if not p.get("locator"):
            return "provenance locator missing"
        if not p.get("quote"):
            return "provenance quote missing (verbatim required)"
    return None


def cmd_evidence(args):
    plan, err = json_load_safe(os.path.join(args.work, "plan.json"))
    if plan is None:
        print("evidence: cannot load plan.json: %s" % err, file=sys.stderr)
        return 1
    problems = []
    for i, p in enumerate(plan.get("parameters", []) or []):
        if not p.get("constraint_source"):
            problems.append("parameter[%d] %s: constraint_source missing"
                            % (i, p.get("name", "?")))
        if not p.get("locator") or not p.get("quote"):
            problems.append("parameter[%d] %s: locator/quote missing (逐字溯源)"
                            % (i, p.get("name", "?")))
    for i, b in enumerate(plan.get("basis", []) or []):
        if not b.get("source_id") or not b.get("locator") or not b.get("quote"):
            problems.append("basis[%d]: source_id/locator/quote missing" % i)
    for i, c in enumerate(plan.get("claims", []) or []):
        miss = _claim_missing(c)
        if miss:
            problems.append("claims[%d]: %s" % (i, miss))
    report = {"problems": problems, "problems_count": len(problems)}
    write_json(os.path.join(args.work, "evidence_report.json"), report)
    if problems:
        print("evidence: %d 问题（见 work/evidence_report.json）" % len(problems))
        return 1
    print("evidence ok: 全部结论逐字溯源完整")
    return 0


def cmd_check(args):
    obj, err = json_load_safe(os.path.join(args.output, "result.json"))
    if obj is None:
        print("check: result.json 无法读取: %s" % err, file=sys.stderr)
        return 1
    errors = []
    if not obj.get("case_id"):
        errors.append("case_id 缺失或为空")
    if obj.get("status") not in STATUS_ENUM:
        errors.append("status 非法: %r" % obj.get("status"))
    for key in ("plan", "artifacts", "sources", "evidence_map", "uncertainty",
                "pending_human", "change_log"):
        if key not in obj:
            errors.append("result.json 缺少字段: %s" % key)
    em = obj.get("evidence_map") or {}
    for i, c in enumerate(em.get("claims", []) or []):
        miss = _claim_missing(c)
        if miss:
            errors.append("evidence_map.claims[%d]: %s" % (i, miss))
    for artifact in ("plan.md", "plan.json"):
        rel = (obj.get("artifacts") or {}).get(artifact)
        if rel and not os.path.exists(rel):
            errors.append("产物文件不存在: %s" % rel)
    if errors:
        for e in errors:
            print("check: %s" % e, file=sys.stderr)
        return 1
    print("check ok: result.json 契约与产物通过")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="weekly-skill orchestrator")
    parser.add_argument("cmd", choices=["init", "finalize", "doe", "evidence", "check"])
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--seed", type=int, default=None,
                        help="doe 随机种子（缺省用 plan.search_strategy.seed）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "init": cmd_init,
        "finalize": cmd_finalize,
        "doe": cmd_doe,
        "evidence": cmd_evidence,
        "check": cmd_check,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
