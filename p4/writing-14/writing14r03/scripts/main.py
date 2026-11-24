#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly-skill 运行助手：开题报告与研究计划书 Skill 的 init/check/render。

仅依赖 Python 标准库；可重复运行；确定性渲染（相同 result.json -> 相同产物）。
在 Skill 根目录执行：python scripts/main.py <subcommand> ...
"""
import argparse
import json
import os
import sys

ALLOWED_STATUS = ("complete", "partial", "failed", "insufficient_evidence", "input_error")

SEVEN_ELEMENTS = ("background", "problems", "objectives", "methods", "plan",
                  "risks", "expected_outcomes")
DICT_ELEMENTS = ("background", "plan", "boundaries")
LIST_ELEMENTS = ("problems", "objectives", "methods", "risks",
                 "expected_outcomes", "references")
ENVELOPE = ("status", "sources", "uncertainty", "pending_human", "change_log",
            "artifacts", "errors", "degradations", "limitations")
CONTRACT_KEYS = ("output_contract", "output_schema", "required_fields", "contract", "output")
STRINGISH = ("title", "summary", "abstract", "name", "label")

RISK_CATEGORIES = ("数据获取", "方法可行性", "进度", "伦理与合规", "资源")


def read_json(path, max_bytes=2 * 1024 * 1024):
    """读取 JSON，容忍 BOM 与 utf-8-sig；超限或坏文件抛 ValueError。"""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) > max_bytes:
        raise ValueError("文件超过大小上限(2MB): %s" % path)
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    text = raw.decode("utf-8-sig", errors="replace")
    return json.loads(text)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def extract_contract(case):
    """从 case.json 抽取输出契约字段；返回 [(字段名, 是否必填), ...]。"""
    if not isinstance(case, dict):
        case = {}
    for key in CONTRACT_KEYS:
        val = case.get(key)
        if isinstance(val, dict) and val:
            fields = []
            for k, spec in val.items():
                req = True
                if isinstance(spec, dict):
                    req = bool(spec.get("required", True))
                fields.append((str(k), req))
            return fields
        if isinstance(val, list) and val:
            return [(str(k), True) for k in val]
        if isinstance(val, str) and val.strip():
            return [(val.strip(), True)]
    return [("title", True), ("summary", True), ("proposal", True), ("artifacts", True)]


def default_value(field):
    if field in STRINGISH or field in ("case_id", "id"):
        return ""
    if field in LIST_ELEMENTS or field in ("artifacts", "sources", "uncertainty",
                                            "pending_human", "change_log",
                                            "errors", "degradations"):
        return []
    if field in DICT_ELEMENTS:
        return {}
    return None


def init_skeleton(case):
    case_id = ""
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or ""
    result = {
        "case_id": str(case_id),
        "status": "partial",
        "title": "",
        "summary": "",
        "limitations": "",
        "background": {},
        "plan": {},
        "boundaries": {},
        "problems": [],
        "objectives": [],
        "methods": [],
        "risks": [],
        "expected_outcomes": [],
        "references": [],
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
    }
    for field, _req in extract_contract(case):
        if field not in result:
            result[field] = default_value(field)
    return result


def _empty(v):
    return v is None or (isinstance(v, (list, dict, str)) and len(v) == 0)


def check_element_quality(result, warns):
    bg = result.get("background") or {}
    if not bg.get("gap"):
        warns.append("background.gap 为空：未写清'已知→未知→待验证'缺口")
    if not bg.get("known") and not bg.get("to_verify"):
        warns.append("background 缺 known/to_verify：背景未指向具体缺口")

    probs = result.get("problems") or []
    if not (1 <= len(probs) <= 3):
        warns.append("problems 应为 1–3 个可证伪问题，当前 %d 个" % len(probs))
    for i, p in enumerate(probs):
        if isinstance(p, dict):
            if not p.get("question"):
                warns.append("problems[%d].question 缺失" % i)
            if not p.get("falsifiable") and not p.get("gap_link"):
                warns.append("problems[%d] 缺 falsifiable/gap_link" % i)

    objs = result.get("objectives") or []
    for i, o in enumerate(objs):
        if isinstance(o, dict):
            if not o.get("problem_ref"):
                warns.append("objectives[%d] 缺 problem_ref（与问题一一对应）" % i)
            if not o.get("criteria"):
                warns.append("objectives[%d] 缺 criteria（指标/样本/对照/时间点）" % i)

    methods = result.get("methods") or []
    for i, m in enumerate(methods):
        if isinstance(m, dict):
            for k in ("step", "input", "output"):
                if not m.get(k):
                    warns.append("methods[%d].%s 缺失" % (i, k))

    plan = result.get("plan") or {}
    if not plan.get("milestones"):
        warns.append("plan.milestones 为空：缺阶段计划/里程碑")
    if not plan.get("period") and not plan.get("budget"):
        warns.append("plan 缺 period/budget：未落在给定周期与经费内")

    risks = result.get("risks") or []
    for i, r in enumerate(risks):
        if isinstance(r, dict):
            for k in ("category", "trigger", "impact", "alternative", "decision_point"):
                if not r.get(k):
                    warns.append("risks[%d].%s 缺失" % (i, k))

    outcomes = result.get("expected_outcomes") or []
    for i, e in enumerate(outcomes):
        if isinstance(e, dict):
            if not e.get("outcome"):
                warns.append("expected_outcomes[%d].outcome 缺失" % i)
            if not e.get("verification"):
                warns.append("expected_outcomes[%d].verification 缺失" % i)

    if not (result.get("references") or []):
        warns.append("references 为空：关键论断与引用应一一对应")


def check_result(case, result):
    fails, warns = [], []
    if not isinstance(result, dict):
        return ["result 不是 JSON 对象"], []
    case_id = str(result.get("case_id") or result.get("id") or "").strip()
    if not case_id:
        fails.append("case_id 缺失或为空")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        fails.append("status 非法: %r（允许 %s）" % (status, "/".join(ALLOWED_STATUS)))
    for k in SEVEN_ELEMENTS:
        if k not in result:
            if status == "complete":
                fails.append("七要素缺字段: %s" % k)
            else:
                warns.append("七要素缺字段: %s" % k)
        elif status == "complete" and _empty(result[k]):
            fails.append("status=complete 但七要素为空: %s" % k)
    for field, req in extract_contract(case):
        if field not in result:
            (fails if req else warns).append(
                "契约字段缺失%s: %s" % ("" if req else "(非必填)", field))
    for k in ENVELOPE:
        if k not in result:
            fails.append("信封字段缺失: %s" % k)
    for i, s in enumerate(result.get("sources", []) or []):
        if not isinstance(s, dict):
            fails.append("sources[%d] 不是对象" % i)
        elif not (s.get("locator") or s.get("quote")):
            warns.append("sources[%d] 缺 locator 或 quote（证据不可回溯）" % i)
    for a in result.get("artifacts", []) or []:
        if isinstance(a, str) and not os.path.exists(a):
            warns.append("artifact 不存在: %s" % a)
    if status in ("partial", "insufficient_evidence", "failed"):
        if not result.get("uncertainty") and not str(result.get("limitations") or "").strip():
            warns.append("%s 状态未写 uncertainty/limitations" % status)
    check_element_quality(result, warns)
    return fails, warns


def _fmt_value(v):
    if isinstance(v, list):
        return "；".join(_fmt_value(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return ""
    return str(v)


def _render_dict_section(lines, data, order, heading):
    if not isinstance(data, dict) or not data:
        return
    lines.append("## " + heading)
    lines.append("")
    keys = [k for k in order if k in data]
    rest = sorted(k for k in data if k not in order)
    for k in keys + rest:
        lines.append("- **%s**: %s" % (k, _fmt_value(data[k])))
    lines.append("")


def _render_list_section(lines, items, order, heading):
    if not isinstance(items, list) or not items:
        return
    lines.append("## " + heading)
    lines.append("")
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            lines.append("### %s %d" % (heading, idx))
            lines.append("")
            keys = [k for k in order if k in item]
            rest = sorted(k for k in item if k not in order)
            for k in keys + rest:
                lines.append("- **%s**: %s" % (k, _fmt_value(item[k])))
            lines.append("")
        else:
            lines.append("- " + _fmt_value(item))
            lines.append("")


def render(result, out_dir):
    """确定性渲染 proposal.md 与 evidence.md；返回绝对路径。"""
    os.makedirs(out_dir, exist_ok=True)
    title = result.get("title") or result.get("case_id") or "开题报告与研究计划书"
    lines = ["# " + title, ""]
    if result.get("summary"):
        lines += [str(result.get("summary")), ""]
    if result.get("limitations"):
        lines += ["**边界与限制**：" + str(result.get("limitations")), ""]

    _render_dict_section(lines, result.get("background"),
                         ["known", "gap", "to_verify"], "一、研究背景与缺口")
    _render_list_section(lines, result.get("problems"),
                         ["question", "falsifiable", "gap_link"], "二、研究问题")
    _render_list_section(lines, result.get("objectives"),
                         ["problem_ref", "goal", "criteria"], "三、研究目标与考核标准")
    _render_list_section(lines, result.get("methods"),
                         ["step", "input", "output", "depends_on"], "四、技术路线")
    _render_dict_section(lines, result.get("plan"),
                         ["period", "budget", "milestones", "critical_path",
                          "parallelizable"], "五、阶段计划")
    _render_list_section(lines, result.get("risks"),
                         ["category", "trigger", "impact", "alternative",
                          "decision_point"], "六、风险与备选方案")
    _render_list_section(lines, result.get("expected_outcomes"),
                         ["outcome", "verification"], "七、预期成果")
    _render_list_section(lines, result.get("references"),
                         ["claim", "source_id", "locator"], "引用与证据映射")
    _render_dict_section(lines, result.get("boundaries"),
                         ["in_scope", "out_of_scope"], "边界")

    proposal_path = os.path.join(out_dir, "proposal.md")
    with open(proposal_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    ev = ["# 证据溯源表", "", "| id | locator | quote | 引用 |", "|---|---|---|---|"]
    for s in result.get("sources", []) or []:
        if isinstance(s, dict):
            ev.append("| %s | %s | %s | %s |" % (
                str(s.get("id", "")).replace("|", "\\|"),
                str(s.get("locator", "")).replace("|", "\\|"),
                str(s.get("quote", "")).replace("\n", " ").replace("|", "\\|"),
                str(s.get("claim", "")).replace("\n", " ").replace("|", "\\|")))
    evidence_path = os.path.join(out_dir, "evidence.md")
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ev).rstrip() + "\n")

    return proposal_path, evidence_path


def cmd_init(args):
    case = {}
    if os.path.exists(args.case):
        case = read_json(args.case)
    result = init_skeleton(case)
    out_dir = os.path.dirname(os.path.abspath(args.result))
    os.makedirs(out_dir, exist_ok=True)
    write_json(args.result, result)
    print(json.dumps({"ok": True, "action": "init",
                      "case_id": result["case_id"], "status": result["status"]},
                     ensure_ascii=False))


def cmd_check(args):
    case = {}
    if os.path.exists(args.case):
        try:
            case = read_json(args.case)
        except ValueError:
            case = {}
    try:
        result = read_json(args.result)
    except (ValueError, OSError) as e:
        print("FAIL result.json 不可读: %s" % e)
        return 2
    fails, warns = check_result(case, result)
    for m in fails:
        print("FAIL  " + m)
    for m in warns:
        print("WARN  " + m)
    print("SUMMARY fail=%d warn=%d" % (len(fails), len(warns)))
    return 0 if not fails else 1


def cmd_render(args):
    result = read_json(args.result)
    out_dir = args.out or os.path.dirname(os.path.abspath(args.result))
    proposal, evidence = render(result, out_dir)
    arts = list(result.get("artifacts") or [])
    for p in (proposal, evidence):
        if p not in arts:
            arts.append(p)
    result["artifacts"] = arts
    write_json(args.result, result)
    print(json.dumps({"ok": True, "artifacts": arts}, ensure_ascii=False))


def build_parser():
    ap = argparse.ArgumentParser(description="开题报告与研究计划书 Skill 助手")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="落盘 status=partial 骨架")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--result", default="/app/output/result.json")
    p = sub.add_parser("check", help="校验 result.json 契约/七要素/证据")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--result", default="/app/output/result.json")
    p = sub.add_parser("render", help="生成 proposal.md + evidence.md")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        cmd_init(args)
        return 0
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "render":
        cmd_render(args)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
