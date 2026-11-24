#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proposal Skill 核心实现：契约发现、早落盘骨架、结果校验、可编辑产物渲染。

仅依赖 Python 标准库；确定性（无随机数、固定排序），重复运行结果一致。
对应评分维度：
- 早落盘（可靠体验）：init 立即写出合法 partial 骨架。
- 可核验（实际效果）：check 校验契约字段齐全、证据可回溯、要素对齐与计划可行性。
- 可编辑交付（实际效果/创新增益）：render 产出 markdown + mermaid gantt + 自包含 SVG。
"""
import datetime
import json
import os

SIZE_CAP = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过
CONTRACT_KEYS = ("output_contract", "output_schema", "required_fields", "contract", "output_fields")
STATUS_VALUES = ("complete", "partial", "insufficient_evidence", "input_error", "failed")
SEVEN_ELEMENTS = ("background_gap", "research_questions", "objectives",
                  "technical_route", "schedule", "risks", "expected_outcomes")
RISK_CATEGORIES = ("数据获取", "方法可行性", "进度", "伦理与合规")
CONSTRAINT_KEYS = ("cycle", "funding", "data", "personnel", "metrics")

DEFAULT_CONTRACT = {
    "title": {"required": True},
    "summary": {"required": True},
    "genre": {"required": False},
    "audience": {"required": False},
    "format": {"required": False},
    "constraints": {"required": False},
    "seven_elements": {"required": True},
    "evidence_map": {"required": True},
    "artifacts": {"required": True},
}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """utf-8-sig 优先，坏编码逐级降级，不崩溃。"""
    with open(path, "rb") as f:
        raw = f.read(SIZE_CAP + 1)
    if len(raw) > SIZE_CAP:
        raise ValueError("file too large: %s" % path)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def load_json(path, errs):
    """容错读 JSON：缺失/BOM/坏编码/坏 JSON 均不崩溃，写入 errs 并返回 None。"""
    if not os.path.exists(path):
        errs.append("missing file: %s" % path)
        return None
    try:
        text = read_text(path).lstrip("\ufeff")
    except (OSError, ValueError) as e:
        errs.append("read error: %s (%s)" % (path, e))
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        errs.append("bad json: %s (%s)" % (path, e))
        return None


def find_contract(data):
    """优先从 case.json 提取 output_contract 等同义键；否则用默认规范字段。"""
    if isinstance(data, dict):
        for key in CONTRACT_KEYS:
            val = data.get(key)
            if isinstance(val, dict) and val:
                return dict(val)
        for key in ("required_fields", "fields"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return {str(k): {"required": True} for k in val}
    return dict(DEFAULT_CONTRACT)


def contract_fields(data):
    return sorted(find_contract(data).keys())


def extract_case_id(data):
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "id", "caseId", "caseID"):
        val = data.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def build_skeleton(case_path, errs):
    """早落盘：写入一份可解析的 partial 结果，之后只做增量回填。"""
    data = load_json(case_path, errs)
    result = {
        "case_id": extract_case_id(data),
        "status": "partial",
        # 默认规范字段（题目未给契约时使用）
        "title": None,
        "summary": None,
        "genre": None,
        "audience": None,
        "format": None,
        "constraints": {k: ([] if k == "metrics" else None) for k in CONSTRAINT_KEYS},
        "seven_elements": {
            "background_gap": {"text": None, "known": [], "unknown": [], "to_verify": [], "evidence": []},
            "research_questions": [],
            "objectives": [],
            "technical_route": [],
            "schedule": [],
            "risks": [],
            "expected_outcomes": [],
        },
        "evidence_map": [],
        "artifacts": [],
        # 信封字段
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"at": utcnow(), "action": "init skeleton", "reason": "前3分钟早落盘 partial 骨架，供后续增量回填"}],
    }
    # 原样回填契约全部键：不改名、不删未知键，缺值置空。
    for fname in contract_fields(data):
        if fname not in result:
            result[fname] = None
    return result


def _ids(items):
    out = set()
    for it in items or []:
        if isinstance(it, dict) and it.get("id"):
            out.add(str(it["id"]))
    return out


def _check_ev(ev_refs, ev_ids, where, findings):
    for ref in ev_refs or []:
        if isinstance(ref, str) and ref and ref not in ev_ids:
            findings.append(("FAIL", "evidence", "%s 引用的证据 %s 不在 evidence_map" % (where, ref)))


def validate(case_path, result_path):
    """校验 result.json，返回 [(level, code, msg)]，level ∈ {FAIL, WARN}。"""
    findings = []
    errs = []
    data = load_json(result_path, errs)
    for e in errs:
        findings.append(("FAIL", "json", e))
    if not isinstance(data, dict):
        findings.append(("FAIL", "json", "result.json 顶层必须是对象"))
        return findings

    case_data = load_json(case_path, [])
    contract = find_contract(case_data)
    # 契约字段齐全
    for fname in sorted(contract.keys()):
        if fname not in data:
            findings.append(("FAIL", "contract", "缺少契约字段: %s" % fname))
    # 信封
    if not data.get("case_id"):
        findings.append(("FAIL", "case_id", "缺少 case_id"))
    status = data.get("status")
    if status not in STATUS_VALUES:
        findings.append(("FAIL", "status", "status 必须属于 %s" % (STATUS_VALUES,)))
    elif status == "failed":
        findings.append(("WARN", "status", "status=failed，需在 change_log 说明原因"))
    if not isinstance(data.get("change_log"), list) or not data["change_log"]:
        findings.append(("WARN", "change_log", "change_log 为空，无法还原增量过程"))
    for key in ("sources", "uncertainty", "pending_human"):
        if not isinstance(data.get(key), list):
            findings.append(("FAIL", key, "%s 必须是数组" % key))

    # 七要素
    se = data.get("seven_elements")
    if isinstance(se, dict):
        for sub in SEVEN_ELEMENTS:
            if sub not in se:
                findings.append(("FAIL", "seven_elements", "缺少要素: %s" % sub))
        if not se.get("research_questions"):
            findings.append(("WARN", "seven_elements", "research_questions 为空"))
        if not se.get("risks"):
            findings.append(("WARN", "seven_elements", "risks 为空，未覆盖风险类型"))
        if not se.get("expected_outcomes"):
            findings.append(("WARN", "seven_elements", "expected_outcomes 为空，未给考核方式"))

        ev_ids = _ids(data.get("evidence_map"))
        rq_ids = _ids(se.get("research_questions"))
        obj_ids = _ids(se.get("objectives"))
        # 要素对齐（对得上）：目标↔问题、成果↔目标、路线↔目标/问题
        for obj in se.get("objectives") or []:
            ref = obj.get("question_ref")
            if ref and str(ref) not in rq_ids:
                findings.append(("FAIL", "align", "目标 %s 的 question_ref 未匹配研究问题" % obj.get("id", "?")))
        for eo in se.get("expected_outcomes") or []:
            ref = eo.get("objective_ref")
            if ref and str(ref) not in obj_ids:
                findings.append(("FAIL", "align", "预期成果 %s 的 objective_ref 未匹配目标" % eo.get("id", "?")))
        for tr in se.get("technical_route") or []:
            ref = tr.get("objective_ref") or tr.get("question_ref")
            if ref and str(ref) not in (obj_ids | rq_ids):
                findings.append(("FAIL", "align", "技术路线 %s 的 objective_ref/question_ref 未匹配" % tr.get("id", "?")))
        # 证据回查
        bg = se.get("background_gap")
        if isinstance(bg, dict):
            _check_ev(bg.get("evidence"), ev_ids, "background_gap", findings)
        for sub in SEVEN_ELEMENTS[1:]:
            node = se.get(sub)
            if isinstance(node, list):
                for it in node:
                    if isinstance(it, dict) and isinstance(it.get("evidence"), list):
                        _check_ev(it["evidence"], ev_ids, sub + "." + str(it.get("id", "?")), findings)
        # 风险字段完整 + 类别合法
        for r in se.get("risks") or []:
            for f in ("category", "description", "trigger", "impact", "mitigation", "decision_point"):
                if not r.get(f):
                    findings.append(("WARN", "risk", "风险 %s 缺字段 %s" % (r.get("id", "?"), f)))
            if r.get("category") and r["category"] not in RISK_CATEGORIES:
                findings.append(("WARN", "risk", "风险类别 %r 不在四类中" % r["category"]))
        # 计划可行性（排得开）：周期
        cycle = (data.get("constraints") or {}).get("cycle")
        if isinstance(cycle, (int, float)):
            for m in se.get("schedule") or []:
                end = m.get("month_end")
                if isinstance(end, (int, float)) and end > cycle:
                    findings.append(("FAIL", "feasibility", "里程碑 %s 结束于第%s月，超出周期 %s" % (m.get("id", "?"), end, cycle)))

    # evidence_map 结构（说得清）
    for ev in data.get("evidence_map") or []:
        if not ev.get("locator"):
            findings.append(("FAIL", "evidence", "证据 %s 缺 locator" % ev.get("id", "?")))
        if not ev.get("quote"):
            findings.append(("FAIL", "evidence", "证据 %s 缺逐字 quote" % ev.get("id", "?")))

    # artifacts
    arts = data.get("artifacts")
    if not arts:
        findings.append(("FAIL", "artifacts", "artifacts 为空，需运行 render 生成可编辑产物"))
    else:
        for a in arts:
            p = a.get("path") if isinstance(a, dict) else None
            if p and not os.path.exists(p):
                findings.append(("WARN", "artifacts", "产物未找到（可能未 render）: %s" % p))
    return findings


def _fmt(v):
    if v is None or v == "":
        return "未给出"
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return str(v)


def _render_proposal_md(data, se):
    L = ["# %s" % (data.get("title") or "开题报告与研究计划书"), ""]
    if data.get("summary"):
        L.append("> %s" % data["summary"])
        L.append("")
    meta = " | ".join(filter(None, [data.get("genre"), data.get("audience"), data.get("format")]))
    if meta:
        L.append("**体裁 / 受众 / 格式**：%s" % meta)
        L.append("")
    c = data.get("constraints") or {}
    if any(c.get(k) not in (None, "", []) for k in CONSTRAINT_KEYS):
        L.append("## 0. 约束与边界")
        L.append("")
        for k, label in (("cycle", "研究周期"), ("funding", "经费"), ("data", "数据条件"), ("personnel", "人员条件")):
            if c.get(k) not in (None, "", []):
                L.append("- **%s**：%s" % (label, _fmt(c[k])))
        if c.get("metrics"):
            L.append("- **考核指标**：%s" % _fmt(c["metrics"]))
        L.append("")
    bg = se.get("background_gap") or {}
    L.append("## 1. 研究背景与缺口")
    L.append("")
    if bg.get("text"):
        L.append(str(bg["text"]))
        L.append("")
    for k, label in (("known", "已知"), ("unknown", "未知"), ("to_verify", "待验证")):
        if bg.get(k):
            L.append("### %s" % label)
            for x in bg[k]:
                L.append("- %s" % x)
            L.append("")
    L.append("## 2. 研究问题")
    L.append("")
    for rq in se.get("research_questions") or []:
        L.append("### %s %s" % (rq.get("id", "RQ"), rq.get("text") or ""))
        if rq.get("falsifiable"):
            L.append("- 可证伪表述：%s" % rq["falsifiable"])
        for k, label in (("metrics", "指标"), ("sample", "样本"), ("control", "对照"), ("timepoint", "时间点")):
            if rq.get(k):
                L.append("- %s：%s" % (label, _fmt(rq[k])))
        L.append("")
    L.append("## 3. 研究目标")
    L.append("")
    for o in se.get("objectives") or []:
        L.append("### %s %s" % (o.get("id", "O"), o.get("text") or ""))
        if o.get("question_ref"):
            L.append("- 对应问题：%s" % o["question_ref"])
        if o.get("criteria"):
            L.append("- 考核标准：%s" % _fmt(o["criteria"]))
        L.append("")
    L.append("## 4. 技术路线")
    L.append("")
    for tr in se.get("technical_route") or []:
        L.append("### %s %s" % (tr.get("id", "TR"), tr.get("step") or ""))
        for k, label in (("input", "输入"), ("output", "输出"), ("depends_on", "依赖"), ("objective_ref", "服务目标"), ("question_ref", "服务问题")):
            if tr.get(k):
                L.append("- %s：%s" % (label, _fmt(tr[k])))
        L.append("")
    L.append("## 5. 阶段计划")
    L.append("")
    for m in se.get("schedule") or []:
        L.append("- **%s**（%s）第%s–%s月：%s" % (
            m.get("id", "M"), m.get("phase") or "", m.get("month_start", "?"), m.get("month_end", "?"), m.get("milestone") or ""))
    L.append("")
    L.append("## 6. 风险与备选方案")
    L.append("")
    for r in se.get("risks") or []:
        L.append("### %s（%s）" % (r.get("id", "R"), r.get("category") or "未分类"))
        for k, label in (("description", "描述"), ("trigger", "触发信号"), ("impact", "影响范围"), ("mitigation", "备选方案"), ("decision_point", "决策时点")):
            L.append("- %s：%s" % (label, _fmt(r.get(k))))
        L.append("")
    L.append("## 7. 预期成果")
    L.append("")
    for eo in se.get("expected_outcomes") or []:
        L.append("### %s %s" % (eo.get("id", "E"), eo.get("outcome") or ""))
        if eo.get("objective_ref"):
            L.append("- 对应目标：%s" % eo["objective_ref"])
        if eo.get("assessment"):
            L.append("- 考核方式：%s" % eo["assessment"])
        L.append("")
    if data.get("uncertainty"):
        L.append("## 8. 不确定与待确认")
        L.append("")
        for u in data["uncertainty"]:
            L.append("- [%s] %s：%s（处置：%s）" % (u.get("kind", "?"), u.get("item", ""), u.get("issue", ""), u.get("action", "")))
        L.append("")
    if data.get("pending_human"):
        L.append("## 9. 待人工确认")
        L.append("")
        for ph in data["pending_human"]:
            L.append("- %s：%s（原因：%s）" % (ph.get("item", ""), ph.get("question", ""), ph.get("why", "")))
        L.append("")
    return "\n".join(L) + "\n"


def _render_evidence_md(data):
    L = ["# 证据映射（逐条可回溯）", ""]
    L.append("| id | locator | 逐字 quote | 支撑项 |")
    L.append("|---|---|---|---|")
    for ev in data.get("evidence_map") or []:
        quote = str(ev.get("quote") or "").replace("|", "\\|").replace("\n", " ")
        refs = ", ".join(str(x) for x in (ev.get("claim_refs") or []))
        L.append("| %s | %s | %s | %s |" % (ev.get("id", ""), ev.get("locator", ""), quote, refs))
    L.append("")
    L.append("## 材料清单")
    L.append("")
    for s in data.get("sources") or []:
        L.append("- %s：%s（%s）" % (s.get("id", ""), s.get("path", ""), s.get("kind", "")))
    return "\n".join(L) + "\n"


def _render_schedule_mmd(schedule):
    L = ["gantt", "    title 阶段计划", "    dateFormat  YYYY-MM-DD", "    axisFormat %m月"]

    def d(month):
        m = int(month) if isinstance(month, (int, float)) else 1
        y = 2026 + (m - 1) // 12
        mm = (m - 1) % 12 + 1
        return datetime.date(y, mm, 1).strftime("%Y-%m-%d")

    for m in schedule or []:
        sid = str(m.get("id", "")).replace(",", " ")
        label = ("%s %s" % (m.get("id", ""), m.get("phase") or "")).replace(",", " ")
        L.append("    %s :%s, %s, %s" % (label, sid, d(m.get("month_start", 1)), d(m.get("month_end", 1))))
    return "\n".join(L) + "\n"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _render_preview_svg(data, se):
    rows = [("开题方案：" + (data.get("title") or "未命名"), 1)]
    labels = (("background_gap", "1 背景与缺口"), ("research_questions", "2 研究问题"),
              ("objectives", "3 目标"), ("technical_route", "4 技术路线"),
              ("schedule", "5 阶段计划"), ("risks", "6 风险"), ("expected_outcomes", "7 预期成果"))
    for sub, label in labels:
        node = se.get(sub)
        n = 0
        if isinstance(node, list):
            n = len(node)
        elif isinstance(node, dict) and node.get("text"):
            n = 1
        rows.append((label, n))
    w, top = 640, 24
    step = 28
    h = top + step * len(rows) + 10
    lines = []
    y = top
    for text, n in rows:
        lines.append('<text x="20" y="%d" font-family="sans-serif" font-size="15">%s</text>' % (y, _esc(text)))
        lines.append('<text x="600" y="%d" text-anchor="end" font-family="sans-serif" font-size="15" fill="#666666">%d</text>' % (y, n))
        y += step
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">\n'
           '<rect width="100%%" height="100%%" fill="#ffffff"/>\n'
           '<text x="20" y="18" font-family="sans-serif" font-size="17" font-weight="bold">开题方案七要素预览</text>\n'
           '%s\n</svg>\n') % (w, h, w, h, "\n".join(lines))
    return svg


def render(result_path, out_dir):
    """由 result.json 生成可编辑产物，并把产物清单写回 result.json 的 artifacts。
    返回 [(name, path, kind)]。幂等：重复运行产物内容一致。"""
    errs = []
    data = load_json(result_path, errs) or {}
    os.makedirs(out_dir, exist_ok=True)
    se = data.get("seven_elements") or {}
    arts = []
    for name, text, kind in (
        ("proposal.md", _render_proposal_md(data, se), "editable"),
        ("evidence_map.md", _render_evidence_md(data), "editable"),
        ("schedule.mmd", _render_schedule_mmd(se.get("schedule")), "preview"),
        ("preview.svg", _render_preview_svg(data, se), "preview"),
    ):
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        arts.append((name, p, kind))
    data["artifacts"] = [{"name": name, "path": path, "kind": kind} for name, path, kind in arts]
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return arts
