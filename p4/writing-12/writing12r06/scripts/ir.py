#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ir.py — 结构化中间表示（IR）的创建、校验与确定性归约。

三个 IR 文件位于 /app/work：
  plan.json          执行规划：体裁/模板判定、任务分解、决策与理由、时间预算。
  fact_pool.json     事实池：每条事实含 locator + 逐字 quote + 状态 + 使用位置。
  compile_report.json 编译验证：引擎、每轮 exit_code、错误分类与根因、refcheck。

result.json 由 reduce 从三个 IR 确定性归约，不手工另造字段，保证可复核。
仅依赖 Python 标准库。所有子命令对非致命失败返回 0，validate 发现问题时返回 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0.0"
IR_FILES = ("plan.json", "fact_pool.json", "compile_report.json")

FACT_CATEGORIES = {
    "number", "unit", "citation", "figure", "formula", "claim", "term", "other",
}
FACT_STATUS = {"confirmed", "conflict", "pending_human", "not_reported"}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _read_json(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _load(path, default_factory):
    obj = _read_json(path)
    return obj if isinstance(obj, dict) else default_factory()


# ---------------------------------------------------------------------------
# plan.json
# ---------------------------------------------------------------------------
def new_plan(case_id="unknown", contract_keys=None, started_at=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "created_at": started_at or utcnow(),
        "updated_at": started_at or utcnow(),
        "task": {
            "genre": None,          # paper_section|rewrite|proofread|template_adapt|reference_fix|formula_fix|other
            "template": None,
            "audience": None,
            "language": None,
            "contract_keys": list(contract_keys or []),
            "constraints": [],
            "required_fields": [],  # 模板必填项：title/author/abstract/keywords/bibstyle/...
        },
        "materials": [],            # [{"path","role"}]
        "steps": [],                # [{"id","name","input","output","deadline_sec","status","note"}]
        "decisions": [],            # [{"id","choice","reason","alternatives"}]
        "open_questions": [],       # 待人工确认
    }


def _new_step(step_id, name, deadline_sec):
    return {
        "id": step_id, "name": name,
        "input": [], "output": [],
        "deadline_sec": deadline_sec,
        "status": "pending", "note": "",
    }


def cmd_plan_init(args):
    os.makedirs(args.work, exist_ok=True)
    path = os.path.join(args.work, "plan.json")
    existing = _load(path, lambda: None)
    if existing is not None:
        print(json.dumps({"path": path, "action": "exists", "case_id": existing.get("case_id")},
                         ensure_ascii=False))
        return 0
    keys = []
    if args.contract_keys:
        keys = [k for k in args.contract_keys.split(",") if k.strip()]
    plan = new_plan(case_id=args.case_id, contract_keys=keys)
    plan["steps"] = [
        _new_step("P0", "bootstrap", 60),
        _new_step("P1", "extract_facts_and_plan", 120),
        _new_step("P2", "draft_and_compile", 420),
        _new_step("P3", "verify_and_reduce", 270),
        _new_step("P4", "finalize", 30),
    ]
    _write_json(path, plan)
    print(json.dumps({"path": path, "case_id": plan["case_id"], "steps": len(plan["steps"])},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_plan_set(args):
    path = os.path.join(args.work, "plan.json")
    plan = _load(path, lambda: new_plan(args.case_id))
    if args.genre:
        plan.setdefault("task", {})["genre"] = args.genre
    if args.template:
        plan["task"]["template"] = args.template
    if args.audience:
        plan["task"]["audience"] = args.audience
    if args.required_fields:
        plan["task"]["required_fields"] = [f for f in args.required_fields.split(",") if f.strip()]
    if args.material:
        plan.setdefault("materials", []).append({
            "path": args.material, "role": args.material_role or "source",
        })
    plan["updated_at"] = utcnow()
    _write_json(path, plan)
    print(json.dumps({"path": path, "task": plan.get("task")}, ensure_ascii=False, indent=2))
    return 0


def cmd_plan_step(args):
    path = os.path.join(args.work, "plan.json")
    plan = _load(path, lambda: new_plan(args.case_id))
    steps = plan.setdefault("steps", [])
    target = None
    for s in steps:
        if s.get("id") == args.id:
            target = s
            break
    if target is None:
        target = _new_step(args.id, args.name or args.id, args.deadline_sec or 0)
        steps.append(target)
    if args.status:
        target["status"] = args.status
    if args.note:
        target["note"] = args.note
    if args.deadline_sec is not None:
        target["deadline_sec"] = args.deadline_sec
    plan["updated_at"] = utcnow()
    _write_json(path, plan)
    print(json.dumps({"path": path, "step": target}, ensure_ascii=False, indent=2))
    return 0


def cmd_plan_decide(args):
    path = os.path.join(args.work, "plan.json")
    plan = _load(path, lambda: new_plan(args.case_id))
    decisions = plan.setdefault("decisions", [])
    decisions.append({
        "id": args.id,
        "choice": args.choice,
        "reason": args.reason,
        "alternatives": [a for a in (args.alternatives or "").split("|") if a.strip()],
    })
    plan["updated_at"] = utcnow()
    _write_json(path, plan)
    print(json.dumps({"path": path, "decisions": len(decisions)}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# fact_pool.json
# ---------------------------------------------------------------------------
def new_fact_pool(case_id="unknown"):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "facts": [],
        "conflicts": [],
    }


def _fact_id_key(path, fact_id):
    return fact_id


def cmd_fact_add(args):
    path = os.path.join(args.work, "fact_pool.json")
    pool = _load(path, lambda: new_fact_pool(args.case_id))
    if args.category not in FACT_CATEGORIES:
        print(json.dumps({"error": "invalid category %r; allowed=%s" % (
            args.category, sorted(FACT_CATEGORIES))}, ensure_ascii=False))
        return 0
    if args.status not in FACT_STATUS:
        print(json.dumps({"error": "invalid status %r; allowed=%s" % (
            args.status, sorted(FACT_STATUS))}, ensure_ascii=False))
        return 0
    fact = {
        "id": args.id,
        "claim": args.claim,
        "category": args.category,
        "status": args.status,
        "sources": [{"locator": args.locator, "quote": args.quote}],
        "used_in": [],
        "note": args.note or "",
    }
    facts = pool.setdefault("facts", [])
    # Replace by id if present (idempotent re-run).
    facts = [f for f in facts if f.get("id") != args.id]
    facts.append(fact)
    pool["facts"] = facts
    _write_json(path, pool)
    print(json.dumps({"path": path, "fact_count": len(facts), "fact_id": args.id},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_fact_use(args):
    path = os.path.join(args.work, "fact_pool.json")
    pool = _load(path, lambda: new_fact_pool(args.case_id))
    for f in pool.setdefault("facts", []):
        if f.get("id") == args.id:
            used = f.setdefault("used_in", [])
            if args.locator not in used:
                used.append(args.locator)
            break
    _write_json(path, pool)
    print(json.dumps({"path": path, "fact_id": args.id}, ensure_ascii=False))
    return 0


def cmd_fact_conflict(args):
    path = os.path.join(args.work, "fact_pool.json")
    pool = _load(path, lambda: new_plan(args.case_id) and new_fact_pool(args.case_id))
    if "facts" not in pool:  # a plan.json was accidentally loaded
        pool = new_fact_pool(args.case_id)
    pool.setdefault("conflicts", []).append({
        "id": args.id,
        "facts": [x for x in args.facts.split(",") if x.strip()],
        "note": args.note or "",
    })
    _write_json(path, pool)
    print(json.dumps({"path": path, "conflicts": len(pool["conflicts"])}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# compile_report.json
# ---------------------------------------------------------------------------
def new_compile_report(case_id="unknown"):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "engine": {"distribution": None, "version": None, "command": None},
        "passes": [],
        "root_causes": [],
        "refcheck": {"undefined_refs": [], "undefined_cites": [], "unused_labels": []},
        "final_status": "not_compiled",  # compiled | not_compiled | degraded
        "limitations": [],
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _validate_common(obj, name, problems):
    if not isinstance(obj, dict):
        problems.append("%s: not an object" % name)
        return False
    if obj.get("schema_version") != SCHEMA_VERSION:
        problems.append("%s: unexpected schema_version %r" % (name, obj.get("schema_version")))
    return True


def cmd_validate(args):
    problems = []
    plan = _load(os.path.join(args.work, "plan.json"), lambda: None)
    pool = _load(os.path.join(args.work, "fact_pool.json"), lambda: None)
    report = _load(os.path.join(args.work, "compile_report.json"), lambda: None)

    if plan is None:
        problems.append("plan.json missing")
    else:
        _validate_common(plan, "plan.json", problems)

    if pool is None:
        problems.append("fact_pool.json missing")
    else:
        if _validate_common(pool, "fact_pool.json", problems):
            for i, f in enumerate(pool.get("facts", [])):
                if not f.get("id") or not f.get("claim"):
                    problems.append("fact_pool.facts[%d] missing id/claim" % i)
                for j, s in enumerate(f.get("sources", [])):
                    if not s.get("locator") or not s.get("quote"):
                        problems.append("fact_pool.facts[%d].sources[%d] missing locator/quote" % (i, j))

    if report is None:
        problems.append("compile_report.json missing (not required before P2)")
    else:
        _validate_common(report, "compile_report.json", problems)

    print(json.dumps({
        "ok": not problems,
        "problems": problems,
        "present": [n for n in IR_FILES if os.path.isfile(os.path.join(args.work, n))],
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


# ---------------------------------------------------------------------------
# 归约：IR -> result.json
# ---------------------------------------------------------------------------
def _reduce_sources(pool):
    out = []
    for f in pool.get("facts", []):
        for s in f.get("sources", []):
            if s.get("locator") and s.get("quote"):
                out.append({
                    "fact_id": f.get("id"),
                    "category": f.get("category"),
                    "locator": s["locator"],
                    "quote": s["quote"],
                    "status": f.get("status"),
                })
    return out


def _reduce_uncertainty(pool, report, plan):
    out = []
    for f in pool.get("facts", []):
        if f.get("status") == "conflict":
            out.append({"kind": "source_conflict", "fact_id": f.get("id"),
                        "claim": f.get("claim"), "note": f.get("note", "")})
        elif f.get("status") in ("pending_human", "not_reported"):
            out.append({"kind": f.get("status"), "fact_id": f.get("id"),
                        "claim": f.get("claim"), "note": f.get("note", "")})
    for c in pool.get("conflicts", []):
        out.append({"kind": "source_conflict", "id": c.get("id"),
                    "facts": c.get("facts"), "note": c.get("note", "")})
    for q in plan.get("open_questions", []):
        out.append({"kind": "pending_human", "question": q})
    return out


def _reduce_pending_human(uncertainty):
    out = []
    for u in uncertainty:
        if u.get("kind") in ("pending_human", "not_reported"):
            out.append(u.get("claim") or u.get("question") or u.get("fact_id"))
        elif u.get("kind") == "source_conflict":
            out.append("conflict %s: %s" % (u.get("id") or u.get("fact_id"), u.get("note", "")))
    return out


def _reduce_status(result, keys, report):
    if report and report.get("engine", {}).get("distribution") is None:
        # engine missing -> degrade to partial at best; never claim ok.
        pass
    filled = sum(1 for k in keys if result.get(k) not in (None, "", []))
    total = len(keys)
    compile_final = (report or {}).get("final_status")
    if not keys:
        return "input_error"
    if filled == total and compile_final == "compiled":
        return "ok"
    if filled == 0:
        return "insufficient_evidence"
    return "partial"


def cmd_reduce(args):
    os.makedirs(args.output, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    result = _read_json(result_path)
    if not isinstance(result, dict):
        result = {"case_id": "unknown"}

    plan = _load(os.path.join(args.work, "plan.json"), lambda: new_plan())
    pool = _load(os.path.join(args.work, "fact_pool.json"), lambda: new_fact_pool())
    report = _load(os.path.join(args.work, "compile_report.json"), lambda: new_compile_report())

    # Contract keys from result (bootstrap already echoed them); plus plan if present.
    keys = [k for k in result.keys()
            if k not in ("case_id", "status", "artifacts", "sources", "uncertainty",
                         "pending_human", "change_log", "errors", "degradations", "runtime")]
    for k in plan.get("task", {}).get("contract_keys", []):
        if k not in keys:
            keys.append(k)
    for k in keys:
        result.setdefault(k, None)

    uncertainty = _reduce_uncertainty(pool, report, plan)
    # case_id 以 bootstrap 从 case.json 提取的为准（result 骨架），其次 plan，最后 pool；
    # "unknown" 视为未确定，不覆盖已解析出的真实 id。
    cid = result.get("case_id")
    for candidate in (plan.get("case_id"), pool.get("case_id")):
        if not cid or cid == "unknown":
            cid = candidate
    result["case_id"] = cid or "unknown"
    result["sources"] = _reduce_sources(pool)
    result["uncertainty"] = uncertainty
    result["pending_human"] = _reduce_pending_human(uncertainty)
    result["errors"] = [c.get("id") for c in report.get("root_causes", [])]
    result["degradations"] = report.get("limitations", [])
    result.setdefault("artifacts", [])
    result.setdefault("change_log", [])
    result["change_log"] = list(result.get("change_log", [])) + [{
        "time": utcnow(), "step": "ir.reduce",
        "note": "reduced from plan/fact_pool/compile_report; facts=%d sources=%d uncertainty=%d" % (
            len(pool.get("facts", [])), len(result["sources"]), len(uncertainty)),
    }]
    result["runtime"] = result.get("runtime", {}) or {}
    result["runtime"].update({
        "ir_status": "reduced",
        "schema_version": SCHEMA_VERSION,
        "reduced_at": utcnow(),
    })
    result["status"] = _reduce_status(result, keys, report)
    _write_json(result_path, result)
    print(json.dumps({
        "status": result["status"],
        "sources": len(result["sources"]),
        "uncertainty": len(uncertainty),
        "filled_contract_keys": sum(1 for k in keys if result.get(k) not in (None, "", [])),
        "total_contract_keys": len(keys),
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="ir.py",
                                     description="IR 创建/校验/归约（plan/fact_pool/compile_report）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan-init")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--contract-keys", default=None)

    p = sub.add_parser("plan-set")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--genre")
    p.add_argument("--template")
    p.add_argument("--audience")
    p.add_argument("--required-fields", default=None)
    p.add_argument("--material")
    p.add_argument("--material-role", default="source")

    p = sub.add_parser("plan-step")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--id", required=True)
    p.add_argument("--name")
    p.add_argument("--status")
    p.add_argument("--note")
    p.add_argument("--deadline-sec", type=int)

    p = sub.add_parser("plan-decide")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--id", required=True)
    p.add_argument("--choice", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--alternatives", default=None)

    p = sub.add_parser("fact-add")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--id", required=True)
    p.add_argument("--claim", required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--status", default="confirmed")
    p.add_argument("--locator", required=True)
    p.add_argument("--quote", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("fact-use")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--id", required=True)
    p.add_argument("--locator", required=True)

    p = sub.add_parser("fact-conflict")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--id", required=True)
    p.add_argument("--facts", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("validate")
    p.add_argument("--work", default="/app/work")

    p = sub.add_parser("reduce")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return parser


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    handlers = {
        "plan-init": cmd_plan_init,
        "plan-set": cmd_plan_set,
        "plan-step": cmd_plan_step,
        "plan-decide": cmd_plan_decide,
        "fact-add": cmd_fact_add,
        "fact-use": cmd_fact_use,
        "fact-conflict": cmd_fact_conflict,
        "validate": cmd_validate,
        "reduce": cmd_reduce,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
