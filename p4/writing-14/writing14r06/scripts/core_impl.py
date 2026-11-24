#!/usr/bin/env python3
"""核心实现：开题报告 Skill 的 init / check / render。

设计取舍（本版重点：中间表示与可复核）：
- plan.json      中间表示（IR）：约束 + 七要素 + 要素间对齐，是唯一事实源。
- evidence_map.json  可复核证据映射：claim -> source_id + locator + 逐字 quote。
- result.json    对外契约：case_id + 题目 output_contract 全部字段 + 信封字段。
- render 从 plan.json 编译 proposal.md / route.mmd / schedule.mmd / overview.svg，
  再回填 result.json，保证“中间表示 -> 可编辑产物”无漂移、可复跑。

仅依赖 Python 标准库；所有函数确定性、幂等、可重复运行。
"""
import datetime
import html
import json
import os
import re
import sys

STATUS_VALUES = {"complete", "partial", "insufficient_evidence", "failed"}
SEVEN = [
    "background",          # 研究背景与缺口
    "problem_definition",  # 问题定义
    "objectives",          # 研究目标
    "technical_route",     # 技术路线
    "schedule",            # 阶段计划
    "risks",               # 风险与备选方案
    "expected_outcomes",   # 预期成果
]
RISK_CATEGORIES = ["数据获取", "方法可行性", "进度", "伦理与合规", "其他"]
DEFAULT_CONTRACT_FIELDS = [
    "case_id", "status", "title", "summary", "proposal", "evidence_map",
    "boundary", "artifacts", "sources", "uncertainty", "pending_human", "change_log",
]
DEFAULT_CONTRACT_REQUIRED = DEFAULT_CONTRACT_FIELDS

# ---------------------------------------------------------------------------
# 基础 IO（容错：BOM / 坏编码 / 坏 JSON / 超限文件）
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=4 * 1024 * 1024):
    """读取文本，容错处理 BOM 与多种编码；超限返回 None。"""
    if not path or not os.path.exists(path):
        return None
    try:
        if os.path.getsize(path) > max_bytes:
            return None
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


def _strip_json_noise(txt):
    """移除行注释、块注释与尾部逗号（仅处理字符串外部分）。"""
    out = []
    i, n = 0, len(txt)
    in_str = False
    while i < n:
        c = txt[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(txt[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if txt.startswith("//", i):
            while i < n and txt[i] != "\n":
                i += 1
            continue
        if txt.startswith("/*", i):
            i += 2
            while i < n and not txt.startswith("*/", i):
                i += 1
            if i < n:
                i += 2
            continue
        if c == ",":
            j = i + 1
            while j < n and txt[j] in " \t\r\n":
                j += 1
            if j < n and txt[j] in "]}":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def read_json(path):
    """读取 JSON；坏 JSON 做一次宽容降级（去注释/尾逗号），仍失败返回 None。"""
    txt = read_text(path)
    if txt is None:
        return None
    txt = txt.lstrip("\ufeff \t\r\n")
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, ValueError):
        try:
            return json.loads(_strip_json_noise(txt))
        except (json.JSONDecodeError, ValueError):
            return None


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    return False


# ---------------------------------------------------------------------------
# 输入发现：case_id 与 output_contract（不假设固定键名）
# ---------------------------------------------------------------------------

def discover_case(case_path):
    """扫描 case.json，返回 case_id / output_contract / raw / error。"""
    out = {"case_id": None, "output_contract": None, "raw": None, "error": None}
    if not os.path.exists(case_path):
        out["error"] = "case.json 不存在：%s" % case_path
        return out
    data = read_json(case_path)
    if not isinstance(data, dict):
        out["error"] = "case.json 无法解析或不是对象：%s" % case_path
        out["raw"] = {}
        return out
    out["raw"] = data
    cid = data.get("case_id") or data.get("id") or data.get("caseId") or ""
    out["case_id"] = str(cid).strip() if cid is not None else ""
    for k in ("output_contract", "output_schema", "required_fields", "contract",
              "result_schema", "output_fields"):
        v = data.get(k)
        if isinstance(v, (dict, list)):
            out["output_contract"] = v
            break
    return out


def contract_required_map(oc):
    """把 output_contract（dict 或 list）规范化为 {字段: 是否必填}。"""
    if isinstance(oc, list):
        return {str(x): True for x in oc}
    if isinstance(oc, dict):
        m = {}
        for k, spec in oc.items():
            if isinstance(spec, dict):
                m[str(k)] = bool(spec.get("required", False))
            else:
                m[str(k)] = True
        return m
    return {}


# ---------------------------------------------------------------------------
# 骨架与空结构
# ---------------------------------------------------------------------------

def empty_proposal():
    return {
        "background": {"known": [], "unknown": [], "to_verify": [], "gap_statement": ""},
        "problem_definition": {"questions": []},
        "objectives": [],
        "technical_route": {"steps": [], "critical_path": [], "resources": {}},
        "schedule": {"cycle": "", "milestones": []},
        "risks": [],
        "expected_outcomes": [],
    }


def _has_content(result):
    prop = result.get("proposal")
    if isinstance(prop, dict) and any(not is_empty(prop.get(k)) for k in SEVEN):
        return True
    if result.get("summary") and str(result["summary"]).strip():
        return True
    if result.get("evidence_map"):
        return True
    return False


def init(case_path, out_dir, work_dir, case=None):
    """前 3 分钟落盘合法 partial 骨架：result.json + plan.json + evidence_map.json。"""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    info = case or discover_case(case_path)
    result_path = os.path.join(out_dir, "result.json")
    existing = read_json(result_path)
    if isinstance(existing, dict) and _has_content(existing):
        return {"ok": True, "case_id": existing.get("case_id"),
                "result": result_path, "note": "已存在非空结果，保留增量内容，不重写骨架"}
    cid = info.get("case_id") or os.path.basename(os.path.dirname(case_path)) or "unknown"
    raw = info.get("raw") or {}
    title = str(raw.get("title") or raw.get("topic") or raw.get("case_title") or "").strip()
    oc = info.get("output_contract")
    echo = list(contract_required_map(oc).keys()) if oc else DEFAULT_CONTRACT_FIELDS
    now = utcnow()
    result = {
        "case_id": cid,
        "status": "partial",
        "title": title or "未命名开题方案",
        "summary": "",
        "proposal": empty_proposal(),
        "evidence_map": [],
        "boundary": {"changes": [], "material_gaps": [], "exceptions": []},
        "artifacts": [],
        "sources": [],
        "uncertainty": [{"item": "result", "issue": "骨架已落盘，材料与分析尚未回填",
                         "kind": "missing", "action": "按 SKILL.md 执行流程增量回填"}],
        "pending_human": [],
        "change_log": [{"at": now, "action": "init skeleton",
                        "reason": "前3分钟先落盘合法 partial 结果骨架，后续仅增量改进"}],
        "output_contract_echo": echo,
    }
    write_json(result_path, result)
    write_json(os.path.join(out_dir, "plan.json"),
               {"case_id": cid, "generated_at": now, "constraints": {}, "proposal": empty_proposal()})
    write_json(os.path.join(out_dir, "evidence_map.json"),
               {"case_id": cid, "generated_at": now, "entries": []})
    return {"ok": True, "case_id": cid, "result": result_path,
            "plan": os.path.join(out_dir, "plan.json"),
            "evidence": os.path.join(out_dir, "evidence_map.json")}


# ---------------------------------------------------------------------------
# check：契约 / 七要素 / 证据可回溯 / 产物存在
# ---------------------------------------------------------------------------

def check(case_path, out_dir, case=None):
    fails, warns = [], []
    info = case or discover_case(case_path)
    result = read_json(os.path.join(out_dir, "result.json"))
    if not isinstance(result, dict):
        return [("result.json 缺失或不可解析", "result.json")], []

    cid = info.get("case_id")
    if cid and result.get("case_id") != cid:
        fails.append((f"case_id 不一致: result={result.get('case_id')!r} case={cid!r}", "case_id"))

    oc = info.get("output_contract")
    req_map = contract_required_map(oc) if oc else {k: True for k in DEFAULT_CONTRACT_REQUIRED}
    for k, req in req_map.items():
        if req and k not in result:
            fails.append((f"契约必填字段缺失: {k}", k))
        elif k in result and is_empty(result[k]):
            warns.append((f"契约字段为空: {k}", k))

    prop = result.get("proposal")
    if not isinstance(prop, dict):
        fails.append(("proposal 非对象或缺失", "proposal"))
    else:
        for k in SEVEN:
            if k not in prop:
                fails.append((f"七要素缺失: {k}", f"proposal.{k}"))
            elif is_empty(prop[k]):
                warns.append((f"七要素为空: {k}", f"proposal.{k}"))

    em = result.get("evidence_map") or []
    src_ids = {s.get("id") for s in (result.get("sources") or []) if isinstance(s, dict)}
    for e in em:
        if not isinstance(e, dict):
            fails.append(("evidence_map 存在非对象条目", "evidence_map"))
            continue
        sid = e.get("source_id") or e.get("source")
        if not sid:
            fails.append((f"evidence_map 缺 source_id: {e.get('claim_id', '?')}", "evidence_map"))
        elif sid not in src_ids:
            fails.append((f"evidence_map 引用的 source 不存在: {sid}", "evidence_map"))
        if not (e.get("locator") or e.get("quote")):
            fails.append((f"evidence_map 缺 locator/quote: {e.get('claim_id', '?')}", "evidence_map"))

    arts = result.get("artifacts") or []
    if not arts:
        warns.append(("artifacts 为空，应含 proposal.md 与中间产物", "artifacts"))
    else:
        for a in arts:
            if not isinstance(a, str):
                fails.append((f"artifacts 存在非字符串条目: {a!r}", "artifacts"))
                continue
            p = a if os.path.isabs(a) else os.path.join(out_dir, a)
            if not os.path.exists(p):
                fails.append((f"artifacts 指向不存在文件: {a}", "artifacts"))

    if result.get("status") not in STATUS_VALUES:
        fails.append((f"status 非法: {result.get('status')!r}", "status"))
    for s in (result.get("sources") or []):
        if isinstance(s, dict) and not (s.get("locator") or s.get("quote")):
            warns.append((f"source 缺 locator/quote: {s.get('id', '?')}", "sources"))
    return fails, warns


# ---------------------------------------------------------------------------
# render：从 plan.json（中间表示）编译可编辑产物，并回填 result.json
# ---------------------------------------------------------------------------

def _append_log_once(log, action, reason):
    if log and log[-1].get("action") == action:
        log[-1]["reason"] = reason
        return log
    log.append({"at": utcnow(), "action": action, "reason": reason})
    return log


def render(case_path, out_dir, case=None):
    info = case or discover_case(case_path)
    plan = read_json(os.path.join(out_dir, "plan.json"))
    em = read_json(os.path.join(out_dir, "evidence_map.json"))
    result = read_json(os.path.join(out_dir, "result.json"))
    if not isinstance(result, dict):
        result = {"case_id": (info.get("case_id") or "unknown"), "status": "partial"}

    if isinstance(plan, dict) and isinstance(plan.get("proposal"), dict):
        prop = plan["proposal"]
    elif isinstance(result.get("proposal"), dict):
        prop = result["proposal"]
    else:
        prop = empty_proposal()
    entries = em.get("entries", []) if isinstance(em, dict) else (result.get("evidence_map") or [])
    if not isinstance(entries, list):
        entries = []

    write_text(os.path.join(out_dir, "proposal.md"), render_markdown(result, prop, entries))
    write_text(os.path.join(out_dir, "route.mmd"), render_route_mmd(prop))
    write_text(os.path.join(out_dir, "schedule.mmd"), render_schedule_mmd(prop))
    write_text(os.path.join(out_dir, "overview.svg"), render_svg(result, prop))
    write_text(os.path.join(out_dir, "evidence_map.md"), render_evidence_md(result, entries))

    result["proposal"] = prop
    result["evidence_map"] = entries
    result["artifacts"] = ["proposal.md", "plan.json", "evidence_map.json",
                           "evidence_map.md", "route.mmd", "schedule.mmd", "overview.svg"]
    for k in ("summary", "sources", "uncertainty", "pending_human"):
        result.setdefault(k, [] if k != "summary" else "")
    result.setdefault("boundary", {"changes": [], "material_gaps": [], "exceptions": []})
    _append_log_once(result.setdefault("change_log", []), "render",
                     "由中间表示 plan.json 编译生成可编辑产物并回填 result.json")
    write_json(os.path.join(out_dir, "result.json"), result)
    return {"ok": True, "case_id": result.get("case_id"), "artifacts": result["artifacts"]}


# ---------------------------------------------------------------------------
# Markdown 稿件（七要素，从 IR 编译）
# ---------------------------------------------------------------------------

def render_markdown(result, prop, entries):
    L = []
    title = (result.get("title") or "开题报告与研究计划书").strip()
    L.append("# " + title)
    L.append("")
    summary = (result.get("summary") or "").strip()
    if summary:
        L.append(summary)
        L.append("")
    bg = prop.get("background") or {}
    L.append("## 一、研究背景与缺口")
    gap = (bg.get("gap_statement") or "").strip()
    if gap:
        L.append(gap)
        L.append("")
    for label, key in (("已知", "known"), ("未知", "unknown"), ("待验证", "to_verify")):
        items = [str(x) for x in (bg.get(key) or []) if str(x).strip()]
        if items:
            L.append("**%s**" % label)
            for it in items:
                L.append("- " + it)
            L.append("")
    pd = prop.get("problem_definition") or {}
    L.append("## 二、问题定义")
    for i, q in enumerate(pd.get("questions") or [], 1):
        L.append("### 问题 %d：%s" % (i, q.get("question", "")))
        if q.get("falsifiable_test"):
            L.append("- 可证伪检验：%s" % q["falsifiable_test"])
        if q.get("gap_evidence"):
            L.append("- 缺口证据：%s" % "；".join(str(x) for x in q["gap_evidence"]))
        if q.get("assumptions"):
            L.append("- 前提假设：%s" % "；".join(str(x) for x in q["assumptions"]))
        L.append("")
    L.append("## 三、研究目标")
    for o in prop.get("objectives") or []:
        crit = o.get("criteria") or {}
        L.append("### %s %s" % (o.get("id", ""), o.get("objective", "")))
        L.append("- 对应问题：%s" % o.get("question_ref", ""))
        L.append("- 成功判据：%s" % o.get("success_judgement", ""))
        if crit:
            L.append("- 考核标准：指标=%s；样本=%s；对照=%s；时间点=%s" % (
                crit.get("metrics", ""), crit.get("sample", ""),
                crit.get("control", ""), crit.get("timepoint", "")))
        L.append("")
    tr = prop.get("technical_route") or {}
    L.append("## 四、技术路线")
    for s in tr.get("steps") or []:
        L.append("### 步骤 %s：%s" % (s.get("id", ""), s.get("name", "")))
        L.append("- 输入：%s" % s.get("input", ""))
        L.append("- 输出：%s" % s.get("output", ""))
        L.append("- 方法：%s" % s.get("method", ""))
        L.append("- 依赖：%s" % ("、".join(str(x) for x in (s.get("depends_on") or [])) or "无"))
        L.append("- 可并行：%s" % ("是" if s.get("parallelizable") else "否"))
        L.append("")
    if tr.get("critical_path"):
        L.append("**关键路径**：%s" % " → ".join(str(x) for x in tr["critical_path"]))
        L.append("")
    L.append("## 五、阶段计划")
    sch = prop.get("schedule") or {}
    if sch.get("cycle"):
        L.append("- 研究周期：%s" % sch["cycle"])
    for m in sch.get("milestones") or []:
        L.append("- **%s**（%s）：%s；工作量=%s；依赖=%s" % (
            m.get("id", ""), m.get("month", ""), m.get("deliverable", ""),
            m.get("workload", ""), "、".join(str(x) for x in (m.get("depends_on") or [])) or "无"))
    L.append("")
    L.append("## 六、风险与备选方案")
    for r in prop.get("risks") or []:
        L.append("### %s（%s）%s" % (r.get("id", ""), r.get("category", ""), r.get("description", "")))
        L.append("- 触发信号：%s" % r.get("trigger_signal", ""))
        L.append("- 影响范围：%s" % r.get("impact", ""))
        L.append("- 备选方案：%s" % r.get("mitigation", ""))
        L.append("- 决策时点：%s" % r.get("decision_point", ""))
        L.append("")
    L.append("## 七、预期成果")
    for e in prop.get("expected_outcomes") or []:
        L.append("- **%s** %s（对应问题 %s）：考核方式=%s；目标=%s" % (
            e.get("id", ""), e.get("outcome", ""), e.get("question_ref", ""),
            e.get("assessment_method", ""), e.get("target", "")))
    L.append("")
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Mermaid / SVG 预览
# ---------------------------------------------------------------------------

def _safe_id(s):
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", str(s).strip())
    if not s:
        s = "n"
    if s[0].isdigit():
        s = "n" + s
    return s


def _esc_mmd(s):
    return str(s).replace('"', "'").replace("\n", " ").replace("[", "(").replace("]", ")")


def render_route_mmd(prop):
    tr = prop.get("technical_route") or {}
    steps = tr.get("steps") or []
    lines = ["flowchart LR"]
    if not steps:
        lines.append('    n0["（技术路线待填写）"]')
        return "\n".join(lines) + "\n"
    for s in steps:
        sid = _safe_id(s.get("id") or s.get("name"))
        label = _esc_mmd(f"{s.get('id', '')} {s.get('name', '')}")
        lines.append('    %s["%s"]' % (sid, label))
    for s in steps:
        sid = _safe_id(s.get("id") or s.get("name"))
        for dep in s.get("depends_on") or []:
            lines.append("    %s --> %s" % (_safe_id(dep), sid))
    if tr.get("critical_path"):
        cp = tr["critical_path"]
        for a, b in zip(cp, cp[1:]):
            lines.append("    %s -.->|关键路径| %s" % (_safe_id(a), _safe_id(b)))
    return "\n".join(lines) + "\n"


def _parse_start_month(sch):
    cycle = str(sch.get("cycle") or "")
    m = re.search(r"(20\d{2})", cycle)
    year = int(m.group(1)) if m else datetime.date.today().year
    return year, 1


def _month_offset(value, idx):
    s = str(value)
    m = re.search(r"(20\d{2})\s*[-/年]?\s*0?(\d{1,2})?", s)
    if m and m.group(2):
        return max(0, int(m.group(2)) - 1)
    digits = re.findall(r"\d+", s)
    if digits:
        return max(0, int(digits[0]) - 1)
    return idx


def _add_months(year, month, offset):
    total = (year - 2000) * 12 + (month - 1) + offset
    y = 2000 + total // 12
    m = total % 12 + 1
    return "%04d-%02d" % (y, m)


def render_schedule_mmd(prop):
    sch = prop.get("schedule") or {}
    milestones = sch.get("milestones") or []
    lines = ["gantt", "    title 阶段计划", "    dateFormat YYYY-MM"]
    year, month = _parse_start_month(sch)
    if not milestones:
        lines.append("    section 待填写")
        lines.append("    里程碑待填写 :%s, %s" % (
            "%04d-%02d" % (year, month), _add_months(year, month, 1)))
        return "\n".join(lines) + "\n"
    lines.append("    section 里程碑")
    for i, m in enumerate(milestones):
        off = _month_offset(m.get("month", ""), i)
        name = _esc_mmd(f"{m.get('id', '')} {m.get('deliverable', '')}")
        d0 = _add_months(year, month, off)
        d1 = _add_months(year, month, off + 1)
        lines.append("    %s :%s, %s" % (name, d0, d1))
    return "\n".join(lines) + "\n"


def _esc_xml(s):
    return html.escape(str(s), quote=True)


def _count(prop, key, sub=None):
    v = prop.get(key)
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict) and sub:
        return len(v.get(sub) or [])
    return 0


def render_svg(result, prop):
    title = _esc_xml((result.get("title") or "开题报告与研究计划书").strip())
    n_q = _count(prop, "problem_definition", "questions")
    n_o = _count(prop, "objectives")
    tr = prop.get("technical_route") or {}
    n_s = _count(prop, "technical_route", "steps")
    cp = tr.get("critical_path") or []
    n_m = _count(prop, "schedule", "milestones")
    n_r = _count(prop, "risks")
    n_e = _count(prop, "expected_outcomes")
    gap = (prop.get("background") or {}).get("gap_statement", "") or "（缺口陈述待填写）"
    rows = [
        ("① 研究背景与缺口", gap),
        ("② 问题定义", "%d 个可回答问题" % n_q),
        ("③ 研究目标", "%d 项可考核目标" % n_o),
        ("④ 技术路线", "%d 个步骤，关键路径 %s" % (n_s, " → ".join(map(str, cp)) if cp else "待填")),
        ("⑤ 阶段计划", "%d 个里程碑" % n_m),
        ("⑥ 风险与备选方案", "%d 条风险（数据/方法/进度/伦理）" % n_r),
        ("⑦ 预期成果", "%d 项成果与考核方式" % n_e),
    ]
    W, margin, top = 860, 40, 40
    box_h, gap_h, head = 58, 12, 40
    H = margin + head + len(rows) * (box_h + gap_h) + margin
    parts = []
    parts.append(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="sans-serif">' % (W, H, W, H))
    parts.append('<rect width="%d" height="%d" fill="#f7f8fa"/>' % (W, H))
    parts.append('<text x="%d" y="%d" font-size="22" font-weight="bold" fill="#1a1a1a">%s</text>'
                 % (margin, margin + 24, title))
    parts.append('<text x="%d" y="%d" font-size="12" fill="#666">七要素结构预览（由 plan.json 中间表示编译）</text>'
                 % (margin, margin + 44))
    y = margin + head + 16
    for label, note in rows:
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="#ffffff" stroke="#2f6fed" stroke-width="1.5"/>'
                     % (margin, y, W - 2 * margin, box_h))
        parts.append('<text x="%d" y="%d" font-size="15" font-weight="bold" fill="#14367a">%s</text>'
                     % (margin + 14, y + 24, _esc_xml(label)))
        parts.append('<text x="%d" y="%d" font-size="12" fill="#444">%s</text>'
                     % (margin + 14, y + 44, _esc_xml(note[:90])))
        if y > margin + head + 16:
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#b9c6e8" stroke-width="1.5" '
                         'marker-end="url(#arrow)"/>' % (W // 2, y - gap_h, W // 2, y))
        y += box_h + gap_h
    parts.append(
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="4" '
        'orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#b9c6e8"/></marker></defs>')
    parts.append("</svg>\n")
    return "".join(parts)


def render_evidence_md(result, entries):
    L = ["# 证据溯源（evidence_map）", ""]
    if not entries:
        L.append("（证据映射待填写）")
        return "\n".join(L) + "\n"
    src_by_id = {s.get("id"): s for s in (result.get("sources") or []) if isinstance(s, dict)}
    for e in entries:
        if not isinstance(e, dict):
            continue
        L.append("## %s" % e.get("claim_id", "?"))
        L.append("- 论断：%s" % e.get("claim", ""))
        L.append("- 来源：%s" % (e.get("source_id") or e.get("source") or "?"))
        src = src_by_id.get(e.get("source_id") or e.get("source"))
        if isinstance(src, dict) and src.get("path"):
            L.append("- 路径：%s" % src.get("path", ""))
        L.append("- 定位：%s" % e.get("locator", ""))
        q = e.get("quote", "")
        if q:
            L.append("- 原文：“%s”" % q)
        L.append("")
    return "\n".join(L).rstrip() + "\n"
