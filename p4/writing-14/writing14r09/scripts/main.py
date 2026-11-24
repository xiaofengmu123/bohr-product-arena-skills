#!/usr/bin/env python3
"""weekly-skill 入口：init（落盘 partial 骨架）→ render（生成可编辑产物）→ check（契约自检）。

仅依赖 Python 标准库；可重复运行、幂等、确定性（无随机）。
默认路径指向平台运行时：
  --input /input/case.json  --task /input/task.md  --out /app/output  --work /app/work
本地自检请运行 scripts/selftest.py（合成最小输入，非正式案例结果）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_CONTRACT_FIELDS = [
    "title", "summary", "proposal", "elements", "artifacts",
    "sources", "uncertainty", "pending_human", "change_log",
]
ENVELOPE_FIELDS = ["status", "sources", "uncertainty", "pending_human", "change_log"]
VALID_STATUS = {"complete", "partial", "failed"}
VALID_RISK_CATS = {"data", "method", "schedule", "ethics"}
ELEMENTS_KEYS = ["background", "questions", "objectives", "route", "plan", "risks", "outcomes"]
CONTRACT_ALIASES = ["output_contract", "output_schema", "required_fields", "contract", "schema"]
CASE_ID_KEYS = ["case_id", "id", "caseId"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    """按 utf-8-sig/utf-8/gb18030/latin-1 依次尝试解码；缺失返回 None，坏编码返回可读近似。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json(path):
    txt = read_text(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, ValueError):
        return None


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def find_contract(case):
    """从 case.json 提取 output_contract（或同义键）。返回 (契约对象, 字段名列表)。"""
    if not isinstance(case, dict):
        return None, []
    for key in CONTRACT_ALIASES:
        v = case.get(key)
        if isinstance(v, dict) and v:
            return v, [str(k) for k in v.keys()]
        if isinstance(v, list) and v:
            return {f: None for f in v}, [str(f) for f in v]
    return None, []


def find_case_id(case):
    if isinstance(case, dict):
        for k in CASE_ID_KEYS:
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return "unknown"


def default_elements():
    """七要素的带类型空骨架；与 references/contract.md 的 schema 一致。"""
    return {
        "background": {
            "known": [],        # [{claim, source_ids}]
            "unknown": [],
            "to_verify": [],
            "gap_statement": "",
            "constraints": [],  # 指南/经费/数据/人员约束
        },
        "questions": [],        # [{id, question, falsifiable_test, rationale, source_ids}]
        "objectives": [],       # [{id, question_id, objective, metric, sample, control, timepoint, threshold, threshold_basis, source_ids}]
        "route": [],            # [{id, name, input, output, method, depends_on}]
        "plan": {
            "constraints": {"period": "", "budget": "", "personnel": "", "data": ""},
            "milestones": [],   # [{id, time, tasks, deliverable, resources, depends_on}]
        },
        "risks": [],            # [{id, category, trigger_signal, impact_scope, fallback, decision_point}]
        "outcomes": [],         # [{id, type, description, assessment_method, target}]
    }


def default_typed_value(field):
    if field in ("title", "summary", "proposal"):
        return ""
    if field == "elements":
        return default_elements()
    if field in ("artifacts", "sources", "uncertainty", "pending_human", "change_log"):
        return []
    return None


def cmd_init(args):
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case = read_json(args.input)
    task = read_text(args.task)
    problems = []
    if case is None:
        if os.path.exists(args.input):
            problems.append({"item": "case.json", "issue": "无法解析或读取（坏 JSON/坏编码）", "kind": "input_error", "action": "按 task.md 与材料继续，case_id 记 unknown"})
        else:
            problems.append({"item": "case.json", "issue": "输入不存在", "kind": "missing", "action": "仅据 task.md 处理"})
    if not task:
        problems.append({"item": "task.md", "issue": "缺失或不可读", "kind": "missing", "action": "从 case.json 与材料推断约束，并写入 uncertainty"})

    contract, contract_fields = find_contract(case)
    if not contract_fields:
        contract_fields = list(DEFAULT_CONTRACT_FIELDS)
    case_id = find_case_id(case)

    result = {}
    for f in DEFAULT_CONTRACT_FIELDS:
        result[f] = default_typed_value(f)
    for f in contract_fields:
        if f not in result:
            result[f] = None
    result["case_id"] = case_id
    result["status"] = "partial"
    if "sources" not in result:
        result["sources"] = []
    if "uncertainty" not in result:
        result["uncertainty"] = list(problems)
    else:
        result["uncertainty"] = list(result["uncertainty"]) + problems
    if "pending_human" not in result:
        result["pending_human"] = []
    if "change_log" not in result:
        result["change_log"] = []
    result["change_log"] = list(result["change_log"]) + [
        {"at": now_iso(), "action": "init skeleton", "reason": "前 3 分钟落盘 partial 骨架，后续增量回填"}
    ]
    result["runtime"] = {
        "started_at": now_iso(),
        "budget": {"model_requests": 64, "seconds": 900, "max_tokens_per_response": 16384},
        "contract_fields": contract_fields,
    }
    out_path = os.path.join(args.out, "result.json")
    write_json(out_path, result)
    print(json.dumps({
        "ok": True, "case_id": case_id, "contract_fields": contract_fields,
        "status": "partial", "result": out_path, "problems": len(problems),
    }, ensure_ascii=False))
    return 0


def collect_refs(obj, key="source_ids"):
    refs = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, list):
                refs.extend(x for x in v if isinstance(x, str))
            else:
                refs.extend(collect_refs(v, key))
    elif isinstance(obj, list):
        for it in obj:
            refs.extend(collect_refs(it, key))
    return refs


def cmd_check(args):
    result = read_json(args.result)
    issues = []

    def fail(msg):
        issues.append(("FAIL", msg))

    def warn(msg):
        issues.append(("WARN", msg))

    if result is None:
        fail("result.json 无法解析")
        for level, msg in issues:
            print(f"{level} {msg}")
        print("FAIL=1 WARN=0")
        return 0
    if not isinstance(result, dict):
        fail("result.json 顶层不是 JSON 对象")
        for level, msg in issues:
            print(f"{level} {msg}")
        print("FAIL=1 WARN=0")
        return 0

    cid = result.get("case_id")
    if not isinstance(cid, str) or not cid.strip() or cid == "unknown":
        fail(f"case_id 缺失或为占位值：{cid!r}")
    st = result.get("status")
    if st not in VALID_STATUS:
        fail(f"status 非法：{st!r}（应为 complete/partial/failed）")

    case = read_json(args.input)
    _, cf = find_contract(case)
    if not cf:
        cf = (result.get("runtime") or {}).get("contract_fields") or list(DEFAULT_CONTRACT_FIELDS)
    for f in cf:
        if f not in result:
            fail(f"缺少 output_contract 字段：{f}")
    for f in ENVELOPE_FIELDS:
        if f not in result:
            fail(f"缺少信封字段：{f}")

    sources = result.get("sources")
    if not isinstance(sources, list):
        fail("sources 必须是列表")
    else:
        src_ids = set()
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                fail(f"sources[{i}] 不是对象")
                continue
            if s.get("id"):
                src_ids.add(s["id"])
            if not (s.get("locator") or s.get("quote")):
                fail(f"sources[{i}] 缺 locator 与 quote（证据必须可回溯）")

    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        outdir = os.path.dirname(os.path.abspath(args.result))
        for a in artifacts:
            if isinstance(a, str) and a:
                p = a if os.path.isabs(a) else os.path.join(outdir, a)
                if not os.path.exists(p):
                    warn(f"artifact 不存在：{a}")
    elif artifacts is not None:
        fail("artifacts 必须是列表")

    elems = result.get("elements")
    if isinstance(elems, dict):
        missing = [k for k in ELEMENTS_KEYS if k not in elems]
        if missing:
            warn("elements 缺键：" + ", ".join(missing))
        refs = set(collect_refs(elems))
        dangling = refs - src_ids
        if dangling:
            warn("elements 引用了未在 sources 定义的 id：" + ", ".join(sorted(dangling)))

        questions = elems.get("questions") or []
        if not (1 <= len(questions) <= 3):
            warn(f"研究问题数量 {len(questions)} 不在 1–3")
        qids = {q.get("id") for q in questions if isinstance(q, dict)}
        for i, q in enumerate(questions):
            if isinstance(q, dict) and not q.get("falsifiable_test"):
                warn(f"questions[{i}] 缺 falsifiable_test（问题须可证伪）")

        objectives = elems.get("objectives") or []
        for i, o in enumerate(objectives):
            if isinstance(o, dict):
                if o.get("question_id") and o["question_id"] not in qids:
                    warn(f"objectives[{i}] 的 question_id {o['question_id']!r} 不在 questions 中（目标须对得上问题）")
                if not o.get("threshold") and not o.get("threshold_basis"):
                    warn(f"objectives[{i}] 缺 threshold 或 threshold_basis（指标阈值须有依据）")

        risks = elems.get("risks") or []
        for i, r in enumerate(risks):
            if isinstance(r, dict):
                if r.get("category") and r["category"] not in VALID_RISK_CATS:
                    warn(f"risks[{i}] 类别 {r['category']!r} 不在 {sorted(VALID_RISK_CATS)}")
                for field in ("trigger_signal", "impact_scope", "fallback", "decision_point"):
                    if not r.get(field):
                        warn(f"risks[{i}] 缺字段 {field}（风险须有触发信号与备选）")

        plan = elems.get("plan")
        if isinstance(plan, dict):
            cons = plan.get("constraints") or {}
            for field in ("period", "budget", "personnel"):
                if not cons.get(field):
                    warn(f"plan.constraints 缺 {field}（计划须落在给定周期/经费/人员内）")
    elif st == "complete":
        fail("status=complete 但 elements 不是对象")

    if st == "complete":
        if not (result.get("proposal") or "").strip():
            warn("status=complete 但 proposal 为空")

    nfail = sum(1 for lv, _ in issues if lv == "FAIL")
    nwarn = sum(1 for lv, _ in issues if lv == "WARN")
    for lv, msg in issues:
        print(f"{lv} {msg}")
    print(f"FAIL={nfail} WARN={nwarn}")
    return 0


def _fmt_list(items, field_names):
    if not items:
        return ""
    lines = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            lines.append(f"- {it}")
            continue
        parts = [str(it.get(f)) for f in field_names if it.get(f)]
        lines.append(f"- {'；'.join(parts)}")
    return "\n".join(lines) + "\n"


def compose_proposal(elems, title="", summary=""):
    if not isinstance(elems, dict):
        elems = {}
    head = f"# {title or '开题报告与研究计划书'}\n"
    if summary:
        head += f"\n**摘要**：{summary}\n"
    bg = elems.get("background") or {}
    lines = [head, "\n## 1 研究背景与缺口", ""]
    if bg.get("gap_statement"):
        lines.append(bg["gap_statement"])
    lines.append("\n### 已知")
    lines.append(_fmt_list(bg.get("known"), ("claim",)))
    lines.append("### 未知")
    lines.append(_fmt_list(bg.get("unknown"), ("claim",)))
    lines.append("### 待验证")
    lines.append(_fmt_list(bg.get("to_verify"), ("claim",)))
    lines.append("### 约束")
    lines.append(_fmt_list(bg.get("constraints"), ("item",)))

    lines += ["", "## 2 研究问题", _fmt_list(elems.get("questions"), ("id", "question", "falsifiable_test"))]
    lines += ["", "## 3 研究目标", _fmt_list(elems.get("objectives"), ("id", "question_id", "objective", "metric", "threshold"))]
    lines += ["", "## 4 技术路线", _fmt_list(elems.get("route"), ("id", "name", "input", "output", "method", "depends_on"))]
    plan = elems.get("plan") or {}
    lines += ["", "## 5 阶段计划"]
    cons = plan.get("constraints") or {}
    if any(cons.get(k) for k in ("period", "budget", "personnel", "data")):
        lines.append("；".join(f"{k}: {cons.get(k)}" for k in ("period", "budget", "personnel", "data") if cons.get(k)))
    lines.append(_fmt_list(plan.get("milestones"), ("id", "time", "tasks", "deliverable", "resources", "depends_on")))
    lines += ["", "## 6 风险与备选方案", _fmt_list(elems.get("risks"), ("id", "category", "trigger_signal", "impact_scope", "fallback", "decision_point"))]
    lines += ["", "## 7 预期成果", _fmt_list(elems.get("outcomes"), ("id", "type", "description", "assessment_method", "target"))]
    return "\n".join(lines).rstrip() + "\n"


def compose_evidence_map(sources):
    if not sources:
        return ""
    lines = ["# 证据映射（论断 → 来源）", ""]
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            continue
        lines.append(f"## {s.get('id') or ('S%d' % (i + 1))}")
        if s.get("locator"):
            lines.append(f"- 定位：`{s['locator']}`")
        if s.get("quote"):
            lines.append(f"- 原文：{s['quote']}")
        if s.get("claims"):
            claims = s["claims"] if isinstance(s["claims"], list) else [s["claims"]]
            lines.append("- 支撑论断：" + "、".join(str(c) for c in claims))
        if s.get("kind"):
            lines.append(f"- 类型：{s['kind']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cmd_render(args):
    result = read_json(args.result)
    if not isinstance(result, dict):
        result = {}
    os.makedirs(args.out, exist_ok=True)
    artifacts = []
    proposal = result.get("proposal") or ""
    if not proposal.strip():
        proposal = compose_proposal(result.get("elements"), result.get("title", ""), result.get("summary", ""))
    if proposal.strip():
        write_text(os.path.join(args.out, "proposal.md"), proposal)
        artifacts.append("proposal.md")
    emap = compose_evidence_map(result.get("sources") or [])
    if emap.strip():
        write_text(os.path.join(args.out, "evidence_map.md"), emap)
        artifacts.append("evidence_map.md")
    cur = result.get("artifacts")
    if not isinstance(cur, list):
        cur = []
    for a in artifacts:
        if a not in cur:
            cur.append(a)
    result["artifacts"] = cur
    write_json(args.result, result)
    print(json.dumps({"ok": True, "artifacts": artifacts, "result": args.result}, ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="开题报告 Skill：init/render/check")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="读入 case.json/task.md，落盘 partial 骨架")
    p.add_argument("--input", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("render", help="生成 proposal.md 与 evidence_map.md 并回填 artifacts")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("check", help="契约与可核验性自检")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--input", default="/input/case.json")
    p.set_defaults(func=cmd_check)
    return ap


def main(argv=None):
    _reconfigure_stdio()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
