#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl: 开题报告与研究计划书 Skill 的确定性实现（仅标准库）。

子命令语义：
  init   读取 /input/case.json，落盘 status=partial 的诚实骨架（含 case_id 与
         output_contract 全部字段）。
  update 把一个片段 JSON 深合并进 result.json（dict 递归合并、list 整表替换），
         并追加 change_log；用于 Agent 的增量改进，避免最后一次性重写。
  check  校验 result.json：契约字段、七要素、对齐（对得上）、可行性（排得开）、
         证据可回溯、产物真实性。FAIL 退出码 1，WARN 不阻断。
  render 由 result.json 确定性生成 proposal.md / evidence_map.json /
         proposal_preview.html，并回写 artifacts。

设计取舍（本版变体 #降级与不确定性）：
  - 任何一步都先落盘可解析的部分结果，绝不因异常留下空文件。
  - kind 三分类（fact / summary / inference）贯穿节点与证据；inference 必须进入
    uncertainty，事实不足则如实降级，不补造内容。
  - 所有校验只输出结论，不虚构分数或运行环境状态。
"""
import argparse
import json
import os
import re
import sys
import time

# --------------------------------------------------------------------------- #
# 常量与数据模型
# --------------------------------------------------------------------------- #

ELEMENTS = [
    "background_gap",
    "research_questions",
    "objectives",
    "technical_route",
    "phase_plan",
    "risks",
    "expected_outcomes",
]

ENVELOPE = [
    "case_id", "status", "title", "summary", "audience", "genre",
    "format_constraints", "constraints", "proposal", "evidence_map",
    "sources", "uncertainty", "pending_human", "change_log", "errors",
    "degradations", "limitations", "runtime", "artifacts",
]

STATUS_OK = {"complete", "partial", "insufficient_evidence", "input_error", "failed"}
RISK_CATEGORIES = {"data", "method", "schedule", "ethics", "compliance", "other"}
KIND_OK = {"fact", "summary", "inference", "conflict"}

CONTRACT_KEYS = ("output_contract", "output_schema", "required_fields", "contract", "result_schema")
CASE_ID_KEYS = ("case_id", "id", "caseId", "caseID")

DEFAULT_CONTRACT = {
    "title": {"required": True},
    "summary": {"required": True},
    "proposal": {"required": True},
    "evidence_map": {"required": True},
    "sources": {"required": True},
    "artifacts": {"required": True},
}

MAX_JSON_BYTES = 4 * 1024 * 1024  # 4MB 上限，超限跳过


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fix_stdout():
    """强制 stdout 为 UTF-8，避免 Windows 控制台以 GBK 解码导致乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 输入读取（utf-8-sig / 坏编码 / 坏 JSON / 超限 全部容错）
# --------------------------------------------------------------------------- #

def read_json(path):
    """返回 (data, err)；err 为 None 表示成功。绝不抛异常。"""
    if not path or not os.path.exists(path):
        return None, "missing:{}".format(path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "os_error:{}".format(e)
    if len(raw) > MAX_JSON_BYTES:
        return None, "too_large:{}".format(len(raw))
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "bad_json:{}".format(e)


def find_case_id(data):
    if not isinstance(data, dict):
        return None
    for k in CASE_ID_KEYS:
        if k in data and isinstance(data[k], (str, int)):
            return str(data[k])
    return None


def find_output_contract(data):
    if not isinstance(data, dict):
        return {}
    for k in CONTRACT_KEYS:
        if k in data and isinstance(data[k], dict) and data[k]:
            return data[k]
    return {}


def empty_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "title": None,
        "summary": None,
        "audience": None,
        "genre": None,
        "format_constraints": [],
        "constraints": {
            "period": None,
            "budget": None,
            "personnel": None,
            "data": None,
        },
        "proposal": {
            "background_gap": {
                "known": [],
                "unknown": [],
                "to_verify": [],
                "gap_statement": None,
            },
            "research_questions": [],
            "objectives": [],
            "technical_route": [],
            "phase_plan": [],
            "risks": [],
            "expected_outcomes": [],
        },
        "evidence_map": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {},
        "artifacts": [],
    }


def contract_value(spec):
    """根据契约声明给缺省值：array->[], object->{}, 其余 None。"""
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict"):
            return {}
    return None


def make_skeleton(case_data):
    case_id = find_case_id(case_data) or "unknown"
    result = empty_result(case_id)
    contract = find_output_contract(case_data) or dict(DEFAULT_CONTRACT)
    for field, spec in contract.items():
        if field in result and result[field] is not None:
            continue  # 规范字段已有真实缺省结构，不覆盖
        result[field] = contract_value(spec)
    result["status"] = "partial"
    result["runtime"] = {
        "engine": "weekly-skill core_impl",
        "step": "init",
        "contract_fields": sorted(contract.keys()),
    }
    return result


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------- #
# init / update
# --------------------------------------------------------------------------- #

def cmd_init(case_path, out_path):
    data, err = read_json(case_path)
    if data is None:
        result = empty_result("unknown")
        result["status"] = "input_error"
        result["errors"].append({
            "item": "case.json",
            "issue": err,
            "kind": "input_error",
        })
        result["change_log"].append({
            "at": now(), "action": "init", "note": "case.json 不可读，落盘 input_error 骨架",
        })
        write_json(out_path, result)
        print(json.dumps({"ok": False, "status": "input_error", "issue": err}, ensure_ascii=False))
        return 0
    result = make_skeleton(data)
    result["change_log"].append({
        "at": now(), "action": "init",
        "note": "落盘 partial 骨架，含 case_id 与 output_contract 全部字段",
    })
    write_json(out_path, result)
    print(json.dumps({"ok": True, "status": "partial", "case_id": result["case_id"]}, ensure_ascii=False))
    return 0


def deep_merge(dst, src):
    for k, v in src.items():
        if k == "case_id":
            continue  # 保护 case_id，禁止增量覆盖
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


def cmd_update(out_path, fragment_path, note):
    result, err = read_json(out_path)
    if result is None:
        result = empty_result("unknown")
        result["status"] = "partial"
        result["errors"].append({"item": "result.json", "issue": err, "kind": "input_error"})
    frag, ferr = read_json(fragment_path)
    if frag is None:
        result["errors"].append({"item": "fragment", "issue": ferr, "kind": "input_error"})
        result["change_log"].append({
            "at": now(), "action": "update",
            "note": "片段不可读，未合并: {}".format(ferr),
        })
        write_json(out_path, result)
        print(json.dumps({"ok": False, "issue": ferr}, ensure_ascii=False))
        return 1
    if not isinstance(frag, dict):
        result["errors"].append({"item": "fragment", "issue": "not_object", "kind": "input_error"})
        write_json(out_path, result)
        return 1
    deep_merge(result, frag)
    result.setdefault("change_log", []).append({
        "at": now(), "action": "update", "note": note or "增量更新",
    })
    write_json(out_path, result)
    print(json.dumps({"ok": True, "status": result.get("status")}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #

def _ref_source_ids(result):
    ids = set()
    p = result.get("proposal") or {}
    bg = p.get("background_gap") or {}
    for block in ("known", "unknown", "to_verify"):
        for it in bg.get(block) or []:
            for s in it.get("sources") or []:
                ids.add(s)
    gs = bg.get("gap_statement")
    if isinstance(gs, dict):
        for s in gs.get("sources") or []:
            ids.add(s)
    for key in ("research_questions", "objectives", "technical_route", "risks"):
        for it in p.get(key) or []:
            for s in it.get("sources") or []:
                ids.add(s)
    return ids


def _collect_items(result):
    """返回 (claim_id, item, kind, sources) 生成器，用于证据校验。"""
    p = result.get("proposal") or {}
    bg = p.get("background_gap") or {}
    for block in ("known", "unknown", "to_verify"):
        for it in bg.get(block) or []:
            yield (it.get("id") or (block + ":" + str(it.get("text", ""))[:20]),
                   it, it.get("kind"), it.get("sources") or [])
    gs = bg.get("gap_statement")
    if isinstance(gs, dict):
        yield ("gap_statement", gs, gs.get("kind"), gs.get("sources") or [])
    for key in ("research_questions", "objectives", "technical_route", "risks"):
        for it in p.get(key) or []:
            yield (it.get("id") or key + str(len(p.get(key) or []))[:8],
                   it, it.get("kind"), it.get("sources") or [])


def cmd_check(case_path, result_path):
    fails, warns = [], []
    result, err = read_json(result_path)
    if result is None:
        fails.append(("F001", "result.json 无法解析: {}".format(err)))
        _print_issues(fails, warns)
        return 1
    if not isinstance(result, dict):
        fails.append(("F002", "result.json 顶层不是对象"))
        _print_issues(fails, warns)
        return 1

    # ---- 契约字段 ----
    contract = None
    if case_path:
        case_data, _ = read_json(case_path)
        contract = find_output_contract(case_data) if case_data else {}
    if contract:
        for field in contract:
            if field not in result:
                fails.append(("F010", "契约字段缺失: {}".format(field)))

    # ---- 信封 ----
    if not result.get("case_id"):
        fails.append(("F011", "case_id 缺失"))
    if result.get("status") not in STATUS_OK:
        fails.append(("F012", "status 非法: {!r}".format(result.get("status"))))
    if not result.get("title"):
        warns.append(("W001", "title 为空（partial 阶段可接受）"))
    if not result.get("summary"):
        warns.append(("W002", "summary 为空（partial 阶段可接受）"))

    # ---- 七要素 ----
    p = result.get("proposal")
    if not isinstance(p, dict):
        fails.append(("F020", "proposal 缺失或非对象"))
        _print_issues(fails, warns)
        return 1
    for el in ELEMENTS:
        if el not in p:
            fails.append(("F021", "七要素缺失: {}".format(el)))

    bg = p.get("background_gap") or {}
    if not isinstance(bg, dict):
        fails.append(("F022", "background_gap 非对象"))
    elif not (bg.get("gap_statement") or (bg.get("known") and bg.get("unknown"))):
        warns.append(("W010", "研究缺口未成形：gap_statement 或 known/unknown 至少一项应有内容"))

    rqs = p.get("research_questions") or []
    if not isinstance(rqs, list) or len(rqs) == 0:
        fails.append(("F030", "research_questions 为空，需 1–3 个可证伪问题"))
    elif len(rqs) > 3:
        warns.append(("W011", "research_questions 超过 3 个，建议收敛"))
    rq_ids = {r.get("id") for r in rqs if isinstance(r, dict) and r.get("id")}

    objs = p.get("objectives") or []
    obj_ids = set()
    for i, o in enumerate(objs if isinstance(objs, list) else []):
        if not isinstance(o, dict):
            fails.append(("F040", "objectives[{}] 非对象".format(i)))
            continue
        obj_ids.add(o.get("id"))
        for f in ("objective", "metric", "threshold"):
            if not o.get(f):
                fails.append(("F041", "objectives[{}] 缺 {}".format(i, f)))
        lq = o.get("linked_question")
        if not lq:
            fails.append(("F042", "objectives[{}] 缺 linked_question（对得上校验）".format(i)))
        elif rq_ids and lq not in rq_ids:
            fails.append(("F043", "objectives[{}].linked_question={!r} 不在研究问题中".format(i, lq)))
        if not o.get("verification") and not o.get("sample"):
            warns.append(("W020", "objectives[{}] 缺 verification/sample（考核口径待补）".format(i)))

    steps = p.get("technical_route") or []
    step_ids = set()
    for i, s in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(s, dict):
            fails.append(("F050", "technical_route[{}] 非对象".format(i)))
            continue
        if s.get("id"):
            step_ids.add(s.get("id"))
        if not s.get("output"):
            fails.append(("F051", "technical_route[{}] 缺 output".format(i)))
    for i, s in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(s, dict):
            continue
        for dep in s.get("depends_on") or []:
            if dep not in step_ids:
                fails.append(("F052", "technical_route[{}] 依赖未知步骤 {!r}".format(i, dep)))

    phases = p.get("phase_plan") or []
    if isinstance(phases, list) and len(phases) == 0:
        warns.append(("W030", "phase_plan 为空（排得开校验无对象）"))
    constraints = result.get("constraints") or {}
    if constraints.get("period") and phases:
        has_time = any((ph.get("duration") or ph.get("phase")) for ph in phases if isinstance(ph, dict))
        if not has_time:
            warns.append(("W031", "已给周期约束但 phase_plan 无 duration/phase 时间映射"))

    risks = p.get("risks") or []
    for i, r in enumerate(risks if isinstance(risks, list) else []):
        if not isinstance(r, dict):
            fails.append(("F060", "risks[{}] 非对象".format(i)))
            continue
        for f in ("category", "risk", "trigger_signal", "fallback"):
            if not r.get(f):
                fails.append(("F061", "risks[{}] 缺 {}".format(i, f)))
        cat = (r.get("category") or "").lower()
        if cat and cat not in RISK_CATEGORIES:
            warns.append(("W040", "risks[{}] 类别 {!r} 不在词表（data/method/schedule/ethics/compliance/other）".format(i, cat)))

    eos = p.get("expected_outcomes") or []
    for i, e in enumerate(eos if isinstance(eos, list) else []):
        if not isinstance(e, dict):
            fails.append(("F070", "expected_outcomes[{}] 非对象".format(i)))
            continue
        for f in ("outcome", "verification_method"):
            if not e.get(f):
                fails.append(("F071", "expected_outcomes[{}] 缺 {}".format(i, f)))
        lo = e.get("linked_objective")
        if lo and obj_ids and lo not in obj_ids:
            fails.append(("F072", "expected_outcomes[{}].linked_objective={!r} 不在目标中".format(i, lo)))
    # 对得上：目标与成果互指
    if obj_ids and eos:
        objs_linked = {e.get("linked_objective") for e in eos if isinstance(e, dict)}
        if not objs_linked.issuperset(obj_ids):
            warns.append(("W041", "部分 objective 无对应 expected_outcome（目标-成果对齐不完整）"))
    if eos and obj_ids:
        for e in eos if isinstance(eos, list) else []:
            if isinstance(e, dict) and e.get("linked_objective") not in obj_ids and not e.get("linked_objective"):
                warns.append(("W042", "存在未关联目标的 expected_outcome"))

    # ---- 证据可回溯 ----
    sources = result.get("sources") or []
    src_ids = {s.get("id") for s in sources if isinstance(s, dict) and s.get("id")}
    if not isinstance(sources, list):
        fails.append(("F080", "sources 非列表"))
    referenced = _ref_source_ids(result)
    for sid in referenced:
        if sid not in src_ids:
            fails.append(("F081", "引用来源 {!r} 不在 sources 清单".format(sid)))
    for i, s in enumerate(sources if isinstance(sources, list) else []):
        if not isinstance(s, dict):
            continue
        if not s.get("locator") and not s.get("quote"):
            warns.append(("W050", "sources[{}] 无 locator 也无 quote".format(i)))
    uncertainty = result.get("uncertainty") or []
    unc_ids = {u.get("item") for u in uncertainty if isinstance(u, dict)}
    for cid, item, kind, srcs in _collect_items(result):
        if kind == "inference" and cid not in unc_ids:
            warns.append(("W051", "inference 项 {!r} 未列入 uncertainty".format(cid)))
        if kind in ("fact", "summary") and not srcs:
            warns.append(("W052", "{!r} 为 {} 但无 sources 溯源".format(cid, kind)))
        for sid in srcs:
            if sid not in src_ids:
                fails.append(("F082", "项 {!r} 引用来源 {!r} 不存在".format(cid, sid)))

    # ---- 产物 ----
    artifacts = result.get("artifacts") or []
    if not artifacts:
        warns.append(("W060", "artifacts 为空（render 后应有产物）"))
    else:
        base = os.path.dirname(os.path.abspath(result_path))
        for a in artifacts:
            ap = a if os.path.isabs(a) else os.path.join(base, a)
            if not os.path.exists(ap):
                fails.append(("F090", "artifacts 指向不存在的文件: {}".format(a)))

    # ---- 降级诚实性 ----
    if result.get("status") == "complete":
        if result.get("pending_human"):
            warns.append(("W061", "存在 pending_human 但 status=complete，建议降级 partial"))
        if fails:
            warns.append(("W062", "status=complete 但仍有 FAIL，矛盾，建议降级"))

    _print_issues(fails, warns)
    print("FAIL={} WARN={}".format(len(fails), len(warns)))
    return 1 if fails else 0


def _print_issues(fails, warns):
    for code, msg in fails:
        print("[FAIL] {} {}".format(code, msg))
    for code, msg in warns:
        print("[WARN] {} {}".format(code, msg))


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #

def _md_escape(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _sources_str(src_ids, sources):
    if not src_ids:
        return ""
    labels = []
    for sid in src_ids:
        for s in sources:
            if s.get("id") == sid:
                loc = s.get("locator") or ((s.get("quote") or "")[:30])
                labels.append("{}".format(sid) + (": {}".format(loc) if loc else ""))
                break
        else:
            labels.append(sid)
    return " `[" + ", ".join(labels) + "]`"


def render_markdown(result):
    p = result.get("proposal") or {}
    bg = p.get("background_gap") or {}
    sources = result.get("sources") or []
    L = []
    L.append("# {}".format(result.get("title") or "开题报告与研究计划书"))
    L.append("")
    if result.get("summary"):
        L.append("> 摘要：{}".format(_md_escape(result.get("summary"))))
        L.append("")
    meta = []
    for k in ("audience", "genre"):
        if result.get(k):
            meta.append("{}: {}".format(k, result.get(k)))
    if result.get("format_constraints"):
        meta.append("格式约束: {}".format("；".join(result.get("format_constraints") or [])))
    if meta:
        L.append("**体裁与受众**：" + "；".join(meta))
        L.append("")

    L.append("## 1. 研究背景与缺口（立得住）")
    gs = bg.get("gap_statement")
    if isinstance(gs, dict):
        L.append("**缺口陈述**：{}{}".format(_md_escape(gs.get("text")), _sources_str(gs.get("sources"), sources)))
        L.append("")
    for title, block in (("已知", "known"), ("未知", "unknown"), ("待验证", "to_verify")):
        items = bg.get(block) or []
        if items:
            L.append("**{}**".format(title))
            for it in items:
                L.append("- {}{}".format(_md_escape(it.get("text")), _sources_str(it.get("sources"), sources)))
            L.append("")

    L.append("## 2. 问题定义")
    for r in p.get("research_questions") or []:
        L.append("- **{}**：{}{}".format(r.get("id"), _md_escape(r.get("question")), _sources_str(r.get("sources"), sources)))
        if r.get("falsifiable_criterion"):
            L.append("  - 可证伪判据：{}".format(_md_escape(r.get("falsifiable_criterion"))))
    L.append("")

    L.append("## 3. 目标（对得上：指标/阈值/样本/对照/时间点）")
    if p.get("objectives"):
        L.append("| ID | 目标 | 指标 | 阈值 | 样本 | 对照 | 时间点 | 对应问题 |")
        L.append("|-|-|-|-|-|-|-|-|")
        for o in p.get("objectives") or []:
            L.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                _md_escape(o.get("id")), _md_escape(o.get("objective")),
                _md_escape(o.get("metric")), _md_escape(o.get("threshold")),
                _md_escape(o.get("sample")), _md_escape(o.get("control")),
                _md_escape(o.get("timepoint")), _md_escape(o.get("linked_question"))))
    L.append("")

    L.append("## 4. 技术路线（有输入输出与依赖）")
    for s in p.get("technical_route") or []:
        L.append("- **{}** {}{}".format(s.get("id"), _md_escape(s.get("name")), _sources_str(s.get("sources"), sources)))
        L.append("  - 输入：{}".format(_md_escape(s.get("input"))))
        L.append("  - 输出：{}".format(_md_escape(s.get("output"))))
        if s.get("depends_on"):
            L.append("  - 依赖：{}".format("、".join(s.get("depends_on"))))
        if s.get("parallel_with"):
            L.append("  - 可并行：{}".format("、".join(s.get("parallel_with"))))
    L.append("")

    L.append("## 5. 阶段计划（排得开）")
    c = result.get("constraints") or {}
    if any(c.values()):
        L.append("**约束**：周期 {}；经费 {}；人员 {}；数据 {}".format(
            c.get("period") or "未给", c.get("budget") or "未给",
            c.get("personnel") or "未给", c.get("data") or "未给"))
        L.append("")
    if p.get("phase_plan"):
        L.append("| 阶段 | 时长 | 里程碑 | 资源 | 关键路径 | 关联步骤 |")
        L.append("|-|-|-|-|-|-|")
        for ph in p.get("phase_plan") or []:
            L.append("| {} | {} | {} | {} | {} | {} |".format(
                _md_escape(ph.get("phase")), _md_escape(ph.get("duration")),
                _md_escape("；".join(ph.get("milestones") or [])),
                _md_escape(ph.get("resources")),
                "是" if ph.get("key_path") else "",
                _md_escape("、".join(ph.get("linked_steps") or []))))
    L.append("")

    L.append("## 6. 风险（说得清：触发信号/影响/备选/决策点）")
    if p.get("risks"):
        L.append("| ID | 类别 | 风险 | 触发信号 | 影响 | 备选方案 | 决策时点 |")
        L.append("|-|-|-|-|-|-|-|")
        for r in p.get("risks") or []:
            L.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                _md_escape(r.get("id")), _md_escape(r.get("category")),
                _md_escape(r.get("risk")), _md_escape(r.get("trigger_signal")),
                _md_escape(r.get("impact")), _md_escape(r.get("fallback")),
                _md_escape(r.get("decision_point"))))
    L.append("")

    L.append("## 7. 预期成果（含考核方式）")
    for e in p.get("expected_outcomes") or []:
        L.append("- **{}**：{}（考核：{}，对应目标：{}）".format(
            _md_escape(e.get("id")), _md_escape(e.get("outcome")),
            _md_escape(e.get("verification_method")), _md_escape(e.get("linked_objective"))))
    L.append("")

    L.append("## 附录：材料不足 / 冲突 / 待人工确认")
    L.append("")
    for u in result.get("uncertainty") or []:
        L.append("- `{}` [{}] {} → {}".format(
            _md_escape(u.get("kind")), _md_escape(u.get("item")),
            _md_escape(u.get("issue")), _md_escape(u.get("action"))))
    for ph in result.get("pending_human") or []:
        L.append("- 待确认：{}".format(_md_escape(ph.get("item") or ph)))
    return "\n".join(L)


def build_evidence_map(result):
    sources = result.get("sources") or []
    src_by_id = {s.get("id"): s for s in sources if isinstance(s, dict) and s.get("id")}
    out = []
    for cid, item, kind, srcs in _collect_items(result):
        for sid in srcs:
            s = src_by_id.get(sid, {})
            out.append({
                "claim_id": cid,
                "kind": kind,
                "source_id": sid,
                "locator": s.get("locator"),
                "quote": s.get("quote"),
                "source_title": s.get("title"),
            })
    return out


def render_html(result, markdown_text):
    body = markdown_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = re.sub(r"^# (.+)$", r"<h1>\1</h1>", body, flags=re.M)
    body = re.sub(r"^## (.+)$", r"<h2>\1</h2>", body, flags=re.M)
    body = re.sub(r"^- (.+)$", r"<li>\1</li>", body, flags=re.M)
    body = re.sub(r"^> (.+)$", r"<blockquote>\1</blockquote>", body, flags=re.M)
    return ("<!DOCTYPE html><html lang='zh'><meta charset='utf-8'>"
            "<style>body{font-family:sans-serif;max-width:900px;margin:2rem auto;"
            "line-height:1.6}h1,h2{color:#1a3a5c}blockquote{color:#555;border-left:3px solid #ccc;"
            "padding-left:1em}li{margin:.2em 0}</style><body>"
            + body + "</body></html>")


def cmd_render(result_path, out_dir):
    result, err = read_json(result_path)
    if result is None:
        print("FAIL 无法解析 result.json: {}".format(err))
        return 1
    os.makedirs(out_dir, exist_ok=True)
    md = render_markdown(result)
    em = build_evidence_map(result)

    proposal_path = os.path.join(out_dir, "proposal.md")
    evidence_path = os.path.join(out_dir, "evidence_map.json")
    preview_path = os.path.join(out_dir, "proposal_preview.html")

    with open(proposal_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(evidence_path, "w", encoding="utf-8") as f:
        json.dump(em, f, ensure_ascii=False, indent=2)
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(render_html(result, md))

    artifacts = ["proposal.md", "evidence_map.json", "proposal_preview.html"]
    result["artifacts"] = artifacts
    result["runtime"] = dict(result.get("runtime") or {})
    result["runtime"].update({
        "step": "render",
        "rendered_at": now(),
        "engine": "weekly-skill core_impl",
    })
    result["change_log"] = result.get("change_log") or []
    result["change_log"].append({"at": now(), "action": "render", "note": "生成 proposal.md / evidence_map.json / preview"})
    write_json(result_path, result)
    print(json.dumps({"ok": True, "artifacts": artifacts}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    _fix_stdout()
    ap = argparse.ArgumentParser(prog="main.py", description="开题报告与研究计划书 Skill 实现")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--out", default="/app/output/result.json")

    p = sub.add_parser("update")
    p.add_argument("--out", default="/app/output/result.json")
    p.add_argument("--fragment", required=True)
    p.add_argument("--note", default=None)

    p = sub.add_parser("check")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--result", default="/app/output/result.json")

    p = sub.add_parser("render")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")

    a = ap.parse_args(argv)
    if a.cmd == "init":
        return cmd_init(a.case, a.out)
    if a.cmd == "update":
        return cmd_update(a.out, a.fragment, a.note)
    if a.cmd == "check":
        return cmd_check(a.case, a.result)
    if a.cmd == "render":
        return cmd_render(a.result, a.out)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
