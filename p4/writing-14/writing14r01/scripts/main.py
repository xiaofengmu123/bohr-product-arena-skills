#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开题报告与研究计划书 Skill 的确定性引擎。

仅依赖 Python 标准库；幂等、可重复运行。子命令：
  init   读 /input/case.json，写一版 status=partial 的合法 result.json 骨架
  check  校验 result.json（契约字段、七要素、证据溯源、可行性、产物存在性）
  render 由 result.json 生成 proposal.md 与 evidence_map.json 并回填 artifacts

用法（在 SKILL.md 所在目录，即 Skill 根目录执行）：
  python scripts/main.py init   --case /input/case.json --out /app/output/result.json --work /app/work
  python scripts/main.py check  --result /app/output/result.json --case /input/case.json
  python scripts/main.py render --result /app/output/result.json --out /app/output --work /app/work
"""
import argparse
import datetime
import json
import os
import sys

# 契约：题目未在 case.json 声明 output_contract 时使用的默认字段
DEFAULT_CONTRACT_KEYS = [
    "title",
    "summary",
    "deliverable",
    "seven_elements",
    "evidence_map",
    "artifacts",
]

# 开题方案七要素（评分“结果完整性”的核心）
SEVEN_ELEMENTS = [
    "background",
    "problem_definition",
    "objectives",
    "technical_route",
    "stage_plan",
    "risks",
    "expected_outcomes",
]

# 信封字段：保证异常、缺口、待人工项可解释
ENVELOPE_KEYS = [
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "runtime",
    "errors",
    "degradations",
    "limitations",
]

# 风险分类（题目规定：数据获取、方法可行性、进度、伦理与合规）
RISK_CATEGORIES = ["data_access", "method_feasibility", "schedule", "ethics_compliance"]

CONTRACT_SYNONYMS = ("output_contract", "output_schema", "required_fields", "contract")
CASE_ID_KEYS = ("case_id", "id")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _force_utf8_stdout():
    """保证标准输出为 UTF-8，避免 Windows 控制台 mojibake；Linux 下为无操作。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def read_json_tolerant(path):
    """UTF-8(BOM) 容错读取；坏编码/坏 JSON 不抛异常，返回 (obj, error)。"""
    if not path or not os.path.exists(path):
        return None, "文件不存在: %s" % (path,)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8-sig", errors="replace")
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 —— 引擎不崩溃，交由信封字段记录
        return None, "无法解析 %s: %s" % (path, exc)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def extract_case_id(obj):
    for key in CASE_ID_KEYS:
        if isinstance(obj.get(key), str) and obj[key].strip():
            return obj[key].strip()
        if obj.get(key) is not None and not isinstance(obj[key], (dict, list)):
            return str(obj[key])
    return None


def extract_contract_keys(obj):
    """从 case.json 提取 output_contract 字段名；缺失时回落到默认契约。"""
    for key in CONTRACT_SYNONYMS:
        val = obj.get(key)
        if not val:
            continue
        if isinstance(val, dict):
            return [str(k) for k in val.keys()]
        if isinstance(val, list):
            return [str(x) for x in val if isinstance(x, str)]
    return list(DEFAULT_CONTRACT_KEYS)


def default_for_field(key):
    if key == "seven_elements":
        return {e: [] for e in SEVEN_ELEMENTS}
    if key in ("evidence_map",):
        return []
    if key in ("artifacts",):
        return {}
    return None


def find_materials(case_obj):
    """从 case.json 提取材料路径候选（不假设固定键名）。"""
    paths = []
    for key in ("materials", "material_paths", "sources", "files", "inputs", "data"):
        val = case_obj.get(key)
        if isinstance(val, str):
            paths.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    paths.append(item)
                elif isinstance(item, dict):
                    for k in ("path", "file", "uri"):
                        if isinstance(item.get(k), str):
                            paths.append(item[k])
    return [p for p in paths if p]


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------
def do_init(case_path, out_path, work_path):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    os.makedirs(work_path, exist_ok=True)

    case_obj, err = read_json_tolerant(case_path)
    case_id = extract_case_id(case_obj) if case_obj else None
    contract_keys = extract_contract_keys(case_obj) if case_obj else list(DEFAULT_CONTRACT_KEYS)

    skeleton = {"case_id": case_id}
    for key in contract_keys:
        skeleton[key] = default_for_field(key)

    # 信封字段（主产物 deliverable 恒存在，无论契约是否声明）
    skeleton.setdefault("deliverable", None)
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["pending_human"] = []
    skeleton["change_log"] = [{"at": now(), "action": "init skeleton", "reason": "前 3 分钟落盘部分结果骨架"}]
    skeleton["runtime"] = {"started": now(), "finished": None, "seconds": None}
    skeleton["errors"] = []
    skeleton["degradations"] = []
    skeleton["limitations"] = []

    if err:
        skeleton["errors"].append({"stage": "init", "error": err})
        skeleton["uncertainty"].append(
            {"item": "case.json", "issue": err, "kind": "input_error", "action": "核对输入文件后重跑"}
        )

    if not case_id:
        skeleton["uncertainty"].append(
            {"item": "case_id", "issue": "case.json 未提供 case_id/id", "kind": "missing", "action": "回填输入标识"}
        )

    materials = find_materials(case_obj) if case_obj else []
    skeleton["runtime"]["materials_discovered"] = len(materials)

    write_json(out_path, skeleton)
    return {
        "ok": True,
        "case_id": case_id,
        "contract_keys": contract_keys,
        "status": skeleton["status"],
        "result": out_path,
    }


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
def collect_claims(seven_elements):
    """把七要素扁平化为带 section 的论断列表，用于证据溯源校验。"""
    claims = []
    if not isinstance(seven_elements, dict):
        return claims
    for section, items in seven_elements.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                c = dict(item)
                c.setdefault("section", section)
                claims.append(c)
            elif isinstance(item, str):
                claims.append({"section": section, "text": item})
    return claims


def parse_duration(value):
    """把 '24'、'36个月'、'3年'、'12 months' 之类解析为月数；失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re_match_duration(value)
        if m is not None:
            return m
    return None


_DURATION_RE = None


def re_match_duration(text):
    global _DURATION_RE
    import re
    if _DURATION_RE is None:
        _DURATION_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(年|个月|月|month|months|m|周|周|week|weeks|天|day|days)")
    m = _DURATION_RE.search(text.lower())
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit in ("年",):
        return num * 12
    if unit in ("个月", "月", "month", "months", "m"):
        return num
    if unit in ("周", "week", "weeks"):
        return num / 4.345
    if unit in ("天", "day", "days"):
        return num / 30.44
    return None


def extract_period_months(case_obj):
    """从 case.json 提取给定周期（月数）。"""
    for key in ("period_months", "duration_months", "period", "duration", "months", "总周期"):
        val = case_obj.get(key)
        months = parse_duration(val)
        if months is not None:
            return months
    return None


def check_feasibility(obj, case_obj):
    """排得开：阶段计划总时长与给定周期/经费/人员相容性校验。

    返回 (warns, notes)：超出周期入 warns，落在周期内入 notes（不计 FAIL/WARN 数）。
    """
    warns, notes = [], []
    stage_plan = (obj.get("seven_elements") or {}).get("stage_plan") or []
    total = 0.0
    have_duration = False
    for item in stage_plan:
        if not isinstance(item, dict):
            continue
        months = parse_duration(item.get("months") or item.get("duration") or item.get("duration_months"))
        if months is not None:
            total += months
            have_duration = True
    period = extract_period_months(case_obj) if case_obj else None
    if have_duration and period is not None and total > period + 1e-6:
        warns.append("阶段计划总时长 %.1f 个月超出给定周期 %.1f 个月（排得开 FAIL）" % (total, period))
    elif have_duration and period is not None:
        notes.append("阶段计划总时长 %.1f 个月在给定周期 %.1f 个月内（排得开 OK）" % (total, period))
    return warns, notes


def check_contract(obj, contract_keys):
    fails = []
    for key in contract_keys:
        if key not in obj:
            fails.append("缺少契约字段 %s" % key)
    for key in ("case_id", "deliverable", "status", "sources", "uncertainty", "pending_human", "change_log"):
        if key not in obj:
            fails.append("缺少信封字段 %s" % key)
    return fails


def do_check(result_path, case_path):
    obj, err = read_json_tolerant(result_path)
    if err:
        return {"ok": False, "fail": 1, "warn": 0,
                "fails": ["result.json 不可解析: %s" % err], "warns": []}

    case_obj, case_err = read_json_tolerant(case_path) if case_path else (None, None)
    contract_keys = extract_contract_keys(case_obj) if case_obj else list(DEFAULT_CONTRACT_KEYS)

    fails = check_contract(obj, contract_keys)
    warns = []

    if not obj.get("case_id"):
        fails.append("case_id 为空")

    # 七要素完整性
    se = obj.get("seven_elements")
    if isinstance(se, dict):
        for elem in SEVEN_ELEMENTS:
            if elem not in se:
                fails.append("seven_elements 缺少要素 %s" % elem)
            elif not isinstance(se[elem], list) or len(se[elem]) == 0:
                warns.append("seven_elements.%s 为空" % elem)
    else:
        fails.append("seven_elements 应为对象且含七个要素")

    # 证据溯源：每条 fact/summary 论断必须有 locator + 逐字 quote
    for claim in collect_claims(se):
        cid = claim.get("claim_id") or claim.get("text") or "(未命名)"
        kind = claim.get("kind")
        if kind in ("fact", "summary"):
            if not claim.get("locator"):
                warns.append("论断 %s 缺 locator" % cid)
            if not claim.get("quote") or not str(claim.get("quote")).strip():
                warns.append("论断 %s 缺逐字 quote" % cid)

    # evidence_map 细粒度字段
    em = obj.get("evidence_map")
    if isinstance(em, list):
        for item in em:
            if isinstance(item, dict):
                cid = item.get("claim_id") or "(未命名)"
                if not item.get("claim_id"):
                    warns.append("evidence_map 项缺 claim_id")
                if not item.get("locator"):
                    warns.append("evidence_map[%s] 缺 locator" % cid)
                if not item.get("quote"):
                    warns.append("evidence_map[%s] 缺 quote" % cid)
    elif em not in (None, []):
        fails.append("evidence_map 应为数组")

    # 可行性（排得开）
    notes = []
    if case_obj:
        feas_warns, notes = check_feasibility(obj, case_obj)
        warns.extend(feas_warns)

    # 产物存在性
    artifacts = obj.get("artifacts")
    if isinstance(artifacts, dict):
        for key, path in artifacts.items():
            if isinstance(path, str) and path and not os.path.exists(path):
                warns.append("artifact %s 文件不存在: %s" % (key, path))
    elif artifacts:
        fails.append("artifacts 应为对象")

    ok = len(fails) == 0
    return {"ok": ok, "fail": len(fails), "warn": len(warns), "fails": fails, "warns": warns,
            "notes": notes}


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------
SECTION_HEADINGS = {
    "background": ("一、研究背景与缺口", "已知—未知—待验证"),
    "problem_definition": ("二、研究问题定义", "具体、可证伪"),
    "objectives": ("三、研究目标与判断标准", "指标、样本、对照、时间点"),
    "technical_route": ("四、技术路线与验证方式", "输入、输出、前后依赖"),
    "stage_plan": ("五、阶段计划与里程碑", "周期、经费、数据、人员相容"),
    "risks": ("六、风险与备选方案", "触发信号、影响、备选、决策时点"),
    "expected_outcomes": ("七、预期成果与考核方式", "可考核、边界写明"),
}


def claim_text(item):
    if isinstance(item, dict):
        return item.get("text") or item.get("claim") or item.get("summary") or ""
    return str(item)


def render_proposal_md(obj):
    lines = ["# " + (obj.get("title") or "开题报告与研究计划书"), ""]
    if obj.get("summary"):
        lines += ["## 摘要", "", str(obj["summary"]), ""]
    se = obj.get("seven_elements") or {}
    for key, (heading, hint) in SECTION_HEADINGS.items():
        items = se.get(key) or []
        lines += ["## " + heading, ""]
        lines.append("<!-- %s -->" % hint)
        lines.append("")
        if not items:
            lines.append("（待补充：材料不足或尚未分析）")
        for item in items:
            if not isinstance(item, dict):
                lines.append("- " + str(item))
                continue
            cid = item.get("claim_id") or ""
            text = claim_text(item)
            lines.append("- [%s] %s" % (cid, text) if cid else "- " + text)
            meta = []
            if item.get("kind"):
                meta.append("类型：" + str(item["kind"]))
            if item.get("locator"):
                meta.append("定位：" + str(item["locator"]))
            if item.get("verdict"):
                meta.append("判定：" + str(item["verdict"]))
            if meta:
                lines.append("  " + "；".join(meta))
            if item.get("quote") and str(item["quote"]).strip():
                lines.append("  > 原文：" + str(item["quote"]))
        lines.append("")
    # 附录：证据源清单
    sources = obj.get("sources") or []
    if sources:
        lines += ["## 附录 A：证据源清单", ""]
        for s in sources:
            if isinstance(s, dict):
                sid = s.get("source_id") or "—"
                loc = s.get("locator") or s.get("path") or "—"
                q = s.get("quote") or ""
                line = "- [%s] %s" % (sid, loc)
                if q:
                    line += " — \"" + str(q) + "\""
                lines.append(line)
        lines.append("")
    # 附录：不确定性与待人工确认
    un = obj.get("uncertainty") or []
    ph = obj.get("pending_human") or []
    if un or ph:
        lines += ["## 附录 B：不确定性与待人工确认", ""]
        for u in un:
            if isinstance(u, dict):
                lines.append("- [不确定] %s：%s（%s）" % (u.get("item") or "—", u.get("issue") or "", u.get("kind") or ""))
        for p in ph:
            if isinstance(p, dict):
                lines.append("- [待确认] %s：%s → %s" % (p.get("item") or "—", p.get("issue") or "", p.get("action") or ""))
        lines.append("")
    return "\n".join(lines)


def do_render(result_path, out_dir, work_dir):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    obj, err = read_json_tolerant(result_path)
    if err or obj is None:
        return {"ok": False, "error": err or "result.json 不可读取"}

    # 若 evidence_map 为空，从七要素内联证据回填（保持细粒度字段）
    em = obj.get("evidence_map")
    if not isinstance(em, list) or len(em) == 0:
        em = []
        for claim in collect_claims(obj.get("seven_elements")):
            if isinstance(claim, dict) and claim.get("claim_id"):
                em.append({
                    "claim_id": claim.get("claim_id"),
                    "section": claim.get("section"),
                    "text": claim_text(claim),
                    "locator": claim.get("locator"),
                    "quote": claim.get("quote"),
                    "source_id": claim.get("source_id"),
                    "kind": claim.get("kind"),
                    "verdict": claim.get("verdict"),
                })
        obj["evidence_map"] = em

    proposal_path = os.path.join(out_dir, "proposal.md")
    write_text(proposal_path, render_proposal_md(obj))
    evidence_path = os.path.join(out_dir, "evidence_map.json")
    write_json(evidence_path, em)

    obj.setdefault("artifacts", {})
    obj["artifacts"]["proposal_md"] = proposal_path
    obj["artifacts"]["evidence_map_json"] = evidence_path
    obj["deliverable"] = proposal_path
    runtime = obj.setdefault("runtime", {})
    runtime["finished"] = now()
    write_json(result_path, obj)
    return {"ok": True, "proposal_md": proposal_path, "evidence_map_json": evidence_path,
            "evidence_count": len(em) if isinstance(em, list) else 0}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(description="开题方案写作 Skill 引擎")
    sub = ap.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="写 status=partial 的结果骨架")
    p_init.add_argument("--case", default="/input/case.json")
    p_init.add_argument("--out", default="/app/output/result.json")
    p_init.add_argument("--work", default="/app/work")

    p_check = sub.add_parser("check", help="校验 result.json")
    p_check.add_argument("--result", default="/app/output/result.json")
    p_check.add_argument("--case", default="/input/case.json")

    p_render = sub.add_parser("render", help="生成可编辑产物")
    p_render.add_argument("--result", default="/app/output/result.json")
    p_render.add_argument("--out", default="/app/output")
    p_render.add_argument("--work", default="/app/work")
    return ap


def main(argv=None):
    _force_utf8_stdout()
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command == "init":
        print(json.dumps(do_init(args.case, args.out, args.work), ensure_ascii=False))
        return 0
    if args.command == "check":
        result = do_check(args.result, args.case)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    if args.command == "render":
        result = do_render(args.result, args.out, args.work)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
