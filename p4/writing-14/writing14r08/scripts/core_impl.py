#!/usr/bin/env python3
"""开题报告与研究计划书 Skill —— 核心实现。

仅依赖 Python 标准库。提供四个可重复、幂等的入口：
  init_skeleton(case_path, out_path)  -> 写 /app/output/result.json 的 partial 骨架
  check(case_path, out_path)          -> 校验契约 + 七要素 + 可行性，返回 (fails, warns, result)
  render(out_path, out_dir)           -> 生成 proposal.md / proposal_plan.mmd / proposal_plan.svg
  selftest_demo()                     -> 供 selftest.py 使用的合成示例（标注为示例）

设计原则：脚本只负责"结构、校验、渲染"，事实抽取由调用 Agent 完成；
脚本不生成任何材料外事实，不虚构评分或软件状态。
"""
import html
import json
import os
import re
from datetime import datetime, timezone

# 默认输出契约字段（题目 output_contract 缺失时的兜底规范）
DEFAULT_CONTRACT = ["title", "summary", "proposal", "evidence_map", "artifacts"]
# 信封字段（评分要求：status / sources / uncertainty / pending_human / change_log）
ENVELOPE = ["status", "sources", "uncertainty", "pending_human", "change_log"]
# 开题方案七要素（题目明确：研究背景、问题定义、目标、技术路线、阶段计划、风险、预期成果）
SEVEN_ELEMENTS = [
    "background",
    "research_questions",
    "objectives",
    "technical_route",
    "phase_plan",
    "risks",
    "expected_outcomes",
]
VALID_STATUS = {"complete", "partial", "failed"}
RISK_CATEGORIES = {"数据获取", "方法可行性", "进度", "伦理与合规", "资源与人员", "其他"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    """读 JSON，容忍 BOM / 坏编码 / 坏 JSON，返回 (obj, error)。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, "cannot read %s: %s" % (path, e)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return json.loads(raw.decode(enc)), None
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as e:
            return None, "bad JSON in %s: %s" % (path, e)
    return None, "cannot decode %s" % path


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def find_contract(case):
    """从 case.json 提取 output_contract 字段名；缺省时用 DEFAULT_CONTRACT。"""
    if isinstance(case, dict):
        for key in ("output_contract", "output_schema", "required_fields", "contract"):
            v = case.get(key)
            if isinstance(v, dict) and v:
                return list(v.keys())
            if isinstance(v, list) and v:
                return [x for x in v if isinstance(x, str)]
    return list(DEFAULT_CONTRACT)


def extract_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "id", "caseId"):
            if case.get(k):
                return str(case[k])
    return None


def _empty_element(name):
    if name == "background":
        return {"known": [], "unknown": [], "gap": None}
    return []


def init_skeleton(case_path, out_path):
    """读 case.json，写 status=partial 的诚实骨架。返回 (result, contract, read_err)。"""
    case, err = read_json(case_path)
    contract = find_contract(case) if isinstance(case, dict) else list(DEFAULT_CONTRACT)
    case_id = extract_case_id(case) if isinstance(case, dict) else None
    result = {
        "case_id": case_id,
        "status": "partial",
        "title": None,
        "summary": None,
        "proposal": {k: _empty_element(k) for k in SEVEN_ELEMENTS},
        "evidence_map": [],
        "artifacts": {},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"at": _now(), "action": "init skeleton", "reason": "先落盘 partial 骨架，后续增量改进"}
        ],
        "errors": [],
        "degradations": [],
        "constraints": {},
    }
    for f in contract:
        result.setdefault(f, None)
    if err:
        result["degradations"].append({"at": _now(), "issue": "case.json 读取失败", "detail": err})
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write_json(out_path, result)
    return result, contract, err


# ---------------------------------------------------------------------------
# 周期解析（相对月份，1 起始）。只做启发式解析，解析不了时返回 None，
# 可行性检查会跳过该项而不是误报。
# ---------------------------------------------------------------------------
def parse_period_months(text):
    """把阶段周期字符串解析为 (start_month, end_month)，1 起始；失败返回 None。

    支持 'M1-M3'、'第1-3个月'、'Q1-Q2'、'Q2'、'1-3月'、'12个月'、'2026.01-2026.03'。
    """
    if text is None:
        return None
    t = str(text).strip()
    m = re.fullmatch(r"Q([1-4])", t, re.I)
    if m:
        q = int(m.group(1))
        return ((q - 1) * 3 + 1, q * 3)
    m = re.fullmatch(r"Q([1-4])\s*[-–~到至]\s*Q([1-4])", t, re.I)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return ((a - 1) * 3 + 1, b * 3)
    nums = re.findall(r"\d+", t)
    if not nums:
        return None
    ints = [int(x) for x in nums]
    if len(ints) == 1:
        if ("个" in t or "共" in t or "总" in t) and "月" in t:
            return (1, ints[0])
        return (ints[0], ints[0])
    if len(ints) == 4 and ints[0] >= 2000 and ints[2] >= 2000:
        return (ints[1], ints[3])
    months = [x for x in ints if x < 100]
    if len(months) >= 2:
        return (months[0], months[-1])
    if len(months) == 1:
        return (months[0], months[0])
    return (ints[0], ints[-1])


# ---------------------------------------------------------------------------
# 校验：check
# ---------------------------------------------------------------------------
def _element_empty(x):
    if isinstance(x, dict):
        return not (x.get("known") or x.get("unknown") or x.get("gap"))
    return not x


def _scan_claim_sources(node, ids, warns, path=""):
    """递归校验 claim 级 sources：id 必须存在，且带 locator/quote/id 之一。"""
    if isinstance(node, dict):
        ss = node.get("sources")
        if isinstance(ss, list):
            label = path or "claim"
            for j, s in enumerate(ss, 1):
                if isinstance(s, dict):
                    if s.get("id") and str(s.get("id")) not in ids:
                        warns.append("%s 的 sources[%d] 引用不存在的 id %r" % (label, j, s.get("id")))
                    if not (s.get("locator") or s.get("quote") or s.get("id")):
                        warns.append("%s 的 sources[%d] 缺少 locator/quote/id（可核验）" % (label, j))
        for k, v in node.items():
            _scan_claim_sources(v, ids, warns, ("%s.%s" % (path, k)) if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node, 1):
            _scan_claim_sources(v, ids, warns, "%s[%d]" % (path, i))


def _check_questions(proposal, warns):
    qs = proposal.get("research_questions") or []
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            warns.append("research_questions[%d] 不是对象" % i)
            continue
        if not (q.get("question") or "").strip():
            warns.append("研究问题 Q%d 缺少 question 文本" % i)
        if not (q.get("falsifiable_criterion") or "").strip():
            warns.append("研究问题 Q%d 缺少可证伪判据（立得住）" % i)
    if len(qs) > 3:
        warns.append("研究问题数量 %d 超过 3 个，建议收敛到 1–3 个（立得住）" % len(qs))


def _check_alignment(proposal, warns):
    nq = len(proposal.get("research_questions") or [])
    objs = proposal.get("objectives") or []
    for i, o in enumerate(objs, 1):
        if not isinstance(o, dict):
            warns.append("objectives[%d] 不是对象" % i)
            continue
        ref = o.get("answer_to")
        if ref is None:
            warns.append("目标 %d 缺少 answer_to，无法核对与问题对应（对得上）" % i)
        else:
            m = re.fullmatch(r"Q\s*(\d+)", str(ref).strip(), re.I)
            if not m:
                warns.append("目标 %d 的 answer_to 格式应为 Q<n>，得到 %r（对得上）" % (i, ref))
            elif not (1 <= int(m.group(1)) <= max(nq, 0)):
                warns.append("目标 %d 的 answer_to=%r 无对应研究问题（对得上）" % (i, ref))
        if not o.get("metrics"):
            warns.append("目标 %d 缺少可考核指标 metrics（对得上）" % i)
    if objs and not (proposal.get("expected_outcomes") or []):
        warns.append("有目标但 expected_outcomes 为空（对得上）")


def _check_route(proposal, warns):
    steps = proposal.get("technical_route") or []
    ids = set()
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            warns.append("technical_route[%d] 不是对象" % i)
            continue
        sid = s.get("step_id")
        if not sid:
            warns.append("技术路线第 %d 步缺少 step_id" % i)
        else:
            ids.add(str(sid))
        if not (s.get("step") or "").strip():
            warns.append("技术路线第 %d 步缺少 step 描述" % i)
    for s in steps:
        if not isinstance(s, dict):
            continue
        for dep in (s.get("depends_on") or []):
            if str(dep) not in ids:
                warns.append("技术路线步骤 %r 依赖不存在的步骤 %r（对得上）" % (s.get("step_id"), dep))


def _check_phase(proposal, result, warns):
    phases = proposal.get("phase_plan") or []
    limit = None
    constraints = result.get("constraints") or {}
    pm = constraints.get("period_months")
    if isinstance(pm, (int, float)):
        limit = float(pm)
    elif isinstance(pm, str):
        nums = re.findall(r"\d+", pm)
        if nums:
            limit = float(nums[0])
    for i, ph in enumerate(phases, 1):
        if not isinstance(ph, dict):
            warns.append("phase_plan[%d] 不是对象" % i)
            continue
        if not (ph.get("milestone") or "").strip():
            warns.append("阶段 %d 缺少里程碑 milestone（排得开）" % i)
        period = ph.get("period") or ""
        parsed = parse_period_months(period)
        if parsed:
            if parsed[0] > parsed[1]:
                warns.append("阶段 %d 周期 %r 起止倒置（排得开）" % (i, period))
            if limit is not None and parsed[1] > limit:
                warns.append("阶段 %d 周期 %r 超出总周期 %s 个月（排得开）" % (i, period, limit))
        elif period:
            warns.append("阶段 %d 周期 %r 无法解析，未做可行性校验（排得开）" % (i, period))


def _check_risks(proposal, warns):
    risks = proposal.get("risks") or []
    for i, r in enumerate(risks, 1):
        if not isinstance(r, dict):
            warns.append("risks[%d] 不是对象" % i)
            continue
        if (r.get("category") or "") not in RISK_CATEGORIES:
            warns.append("风险 %d 类别 %r 不在规范词表（说得清）" % (i, r.get("category")))
        for field in ("trigger", "mitigation", "decision_point"):
            if not (r.get(field) or "").strip():
                warns.append("风险 %d 缺少 %s（说得清）" % (i, field))


def _check_sources(result, warns):
    srcs = result.get("sources") or []
    ids = {str(s.get("id")) for s in srcs if isinstance(s, dict) and s.get("id")}
    em = result.get("evidence_map") or []
    for i, e in enumerate(em, 1):
        if not isinstance(e, dict):
            warns.append("evidence_map[%d] 不是对象" % i)
            continue
        if not (e.get("locator") or e.get("quote")):
            warns.append("evidence_map[%d] 缺少 locator 或 quote（可核验）" % i)
        if e.get("id") and str(e.get("id")) not in ids:
            warns.append("evidence_map[%d] 引用不存在的 source id %r" % (i, e.get("id")))
    _scan_claim_sources(result.get("proposal"), ids, warns)


def _check_artifacts(result, out_path, warns):
    arts = result.get("artifacts")
    base = os.path.dirname(os.path.abspath(out_path))
    if isinstance(arts, dict):
        if not arts:
            warns.append("artifacts 为空（尚未 render）")
        for k, v in arts.items():
            if not v:
                warns.append("artifacts.%s 为空" % k)
                continue
            p = v if os.path.isabs(v) else os.path.join(base, v)
            if not os.path.exists(p):
                warns.append("artifacts.%s 指向不存在的文件: %s" % (k, v))
    elif isinstance(arts, list):
        if not arts:
            warns.append("artifacts 为空")
    else:
        warns.append("artifacts 应为对象或非空列表")


def check(case_path, out_path):
    """返回 (fails, warns, result)。FAIL 为硬性契约破坏，WARN 为内容/可行性提示。"""
    fails, warns = [], []
    result, err = read_json(out_path)
    if err:
        return [err], [], None
    if not isinstance(result, dict):
        return ["result.json 不是 JSON 对象"], [], result
    case, _ = read_json(case_path)
    contract = find_contract(case) if isinstance(case, dict) else list(DEFAULT_CONTRACT)
    if not result.get("case_id"):
        fails.append("缺少 case_id")
    if isinstance(case, dict):
        cid = extract_case_id(case)
        if cid and str(result.get("case_id")) != str(cid):
            warns.append("case_id 与 case.json 不一致：%r != %r" % (result.get("case_id"), cid))
    for f in contract:
        if f not in result:
            fails.append("缺少 output_contract 字段: %s" % f)
    for f in ENVELOPE:
        if f not in result:
            fails.append("缺少信封字段: %s" % f)
    if result.get("status") not in VALID_STATUS:
        fails.append("status 非法: %r（应为 complete/partial/failed）" % result.get("status"))
    proposal = result.get("proposal")
    if not isinstance(proposal, dict):
        fails.append("proposal 必须是对象（七要素）")
        return fails, warns, result
    for e in SEVEN_ELEMENTS:
        if e not in proposal:
            fails.append("proposal 缺少要素: %s" % e)
        elif _element_empty(proposal[e]):
            warns.append("要素为空: %s" % e)
    if result.get("status") == "complete" and all(_element_empty(proposal[e]) for e in SEVEN_ELEMENTS):
        warns.append("status=complete 但七要素全空，建议降级为 partial")
    _check_questions(proposal, warns)
    _check_alignment(proposal, warns)
    _check_route(proposal, warns)
    _check_phase(proposal, result, warns)
    _check_risks(proposal, warns)
    _check_sources(result, warns)
    _check_artifacts(result, out_path, warns)
    return fails, warns, result


# ---------------------------------------------------------------------------
# 渲染：render
# ---------------------------------------------------------------------------
def _esc(s):
    return html.escape(str(s), quote=True)


def _mmd_esc(s):
    return str(s).replace('"', "'").replace("\n", " ")[:60]


def _src_lines(lines, item):
    if isinstance(item, dict) and isinstance(item.get("sources"), list):
        for s in item["sources"]:
            if not isinstance(s, dict):
                continue
            loc = s.get("locator") or ""
            quote = s.get("quote") or ""
            sid = ("[" + str(s["id"]) + "] ") if s.get("id") else ""
            lines.append("  - 来源：%s%s「%s」" % (sid, loc, quote))
            if s.get("kind"):
                lines.append("    - 类型：%s" % s["kind"])


def render_markdown(result):
    L = []
    title = result.get("title") or "开题报告与研究计划书"
    L.append("# " + str(title))
    L.append("")
    L.append("> 本文档由结果文件自动生成，可继续编辑；论断与来源映射见文末附录。")
    L.append("")
    if result.get("summary"):
        L.append("## 摘要")
        L.append("")
        L.append(str(result["summary"]))
        L.append("")
    p = result.get("proposal") or {}
    L.append("## 1 研究背景与缺口")
    L.append("")
    bg = p.get("background") or {}
    known = bg.get("known") or []
    if known:
        L.append("### 1.1 已知结论")
        L.append("")
        for k in known:
            txt = k.get("claim") if isinstance(k, dict) else str(k)
            L.append("- " + str(txt))
            _src_lines(L, k)
    unknown = bg.get("unknown") or []
    if unknown:
        L.append("")
        L.append("### 1.2 未知 / 待验证")
        L.append("")
        for u in unknown:
            L.append("- " + str(u))
    gap = bg.get("gap")
    if gap:
        L.append("")
        L.append("### 1.3 缺口陈述")
        L.append("")
        L.append(str(gap.get("statement") if isinstance(gap, dict) else gap))
        _src_lines(L, gap)
    L.append("")
    L.append("## 2 研究问题")
    L.append("")
    qs = p.get("research_questions") or []
    for i, q in enumerate(qs, 1):
        if isinstance(q, dict):
            L.append("### Q%d %s" % (i, q.get("question", "")))
            if q.get("falsifiable_criterion"):
                L.append("")
                L.append("- 可证伪判据：%s" % q["falsifiable_criterion"])
            _src_lines(L, q)
        else:
            L.append("### Q%d %s" % (i, q))
        L.append("")
    L.append("## 3 研究目标与考核标准")
    L.append("")
    objs = p.get("objectives") or []
    for i, o in enumerate(objs, 1):
        if isinstance(o, dict):
            L.append("### 3.%d %s" % (i, o.get("objective", "")))
            L.append("")
            L.append("- 对应问题：%s" % (o.get("answer_to") or "未标注"))
            metrics = o.get("metrics") or []
            if metrics:
                L.append("- 指标：" + "、".join(str(x) for x in metrics))
            if o.get("sample"):
                L.append("- 样本：%s" % o["sample"])
            if o.get("control"):
                L.append("- 对照：%s" % o["control"])
            if o.get("timepoint"):
                L.append("- 时间点：%s" % o["timepoint"])
            _src_lines(L, o)
        else:
            L.append("### 3.%d %s" % (i, o))
        L.append("")
    L.append("## 4 技术路线")
    L.append("")
    steps = p.get("technical_route") or []
    for i, s in enumerate(steps, 1):
        if isinstance(s, dict):
            L.append("### R%d %s" % (i, s.get("step", "")))
            L.append("")
            if s.get("step_id"):
                L.append("- 步骤标识：%s" % s["step_id"])
            if s.get("input"):
                L.append("- 输入：%s" % s["input"])
            if s.get("output"):
                L.append("- 输出：%s" % s["output"])
            if s.get("depends_on"):
                L.append("- 依赖：" + "、".join(str(x) for x in s["depends_on"]))
            _src_lines(L, s)
        else:
            L.append("### R%d %s" % (i, s))
        L.append("")
    L.append("## 5 阶段计划与里程碑")
    L.append("")
    phases = p.get("phase_plan") or []
    for i, ph in enumerate(phases, 1):
        if isinstance(ph, dict):
            L.append("- **%s**（%s）：%s" % (ph.get("phase") or ("阶段 %d" % i), ph.get("period") or "", ph.get("milestone") or ""))
            if ph.get("resources"):
                L.append("  - 资源：%s" % ph["resources"])
        else:
            L.append("- %s" % ph)
    L.append("")
    L.append("## 6 风险与备选方案")
    L.append("")
    risks = p.get("risks") or []
    for i, r in enumerate(risks, 1):
        if isinstance(r, dict):
            L.append("- **[%s] %s**" % (r.get("category") or "未分类", r.get("risk") or ""))
            for k, label in (("trigger", "触发信号"), ("impact", "影响范围"), ("mitigation", "备选方案"), ("decision_point", "决策时点")):
                if r.get(k):
                    L.append("  - %s：%s" % (label, r[k]))
        else:
            L.append("- %s" % r)
    L.append("")
    L.append("## 7 预期成果与考核方式")
    L.append("")
    outs = p.get("expected_outcomes") or []
    for i, o in enumerate(outs, 1):
        if isinstance(o, dict):
            L.append("- 成果：%s" % (o.get("outcome") or ""))
            if o.get("assessment"):
                L.append("  - 考核方式：%s" % o["assessment"])
            _src_lines(L, o)
        else:
            L.append("- %s" % o)
    L.append("")
    L.append("## 8 来源与证据映射")
    L.append("")
    em = result.get("evidence_map") or []
    if em:
        for e in em:
            if isinstance(e, dict):
                L.append("- %s：%s" % (e.get("claim_id") or "c", e.get("claim") or ""))
                if e.get("locator") or e.get("quote"):
                    L.append("  - %s「%s」%s" % (e.get("locator") or "", e.get("quote") or "", ("（%s）" % e["kind"]) if e.get("kind") else ""))
            else:
                L.append("- %s" % e)
    else:
        L.append("（暂无证据映射）")
    L.append("")
    L.append("## 9 边界、限制与待确认项")
    L.append("")
    unc = result.get("uncertainty") or []
    if unc:
        L.append("### 不确定性")
        L.append("")
        for u in unc:
            if isinstance(u, dict):
                L.append("- %s：%s（%s）" % (u.get("item") or "", u.get("issue") or "", u.get("kind") or "unknown"))
                if u.get("action"):
                    L.append("  - 处置：%s" % u["action"])
            else:
                L.append("- %s" % u)
    pending = result.get("pending_human") or []
    if pending:
        L.append("")
        L.append("### 待人工确认")
        L.append("")
        for u in pending:
            if isinstance(u, dict):
                L.append("- %s：%s" % (u.get("item") or "", u.get("reason") or ""))
            else:
                L.append("- %s" % u)
    deg = result.get("degradations") or []
    if deg:
        L.append("")
        L.append("### 降级说明")
        L.append("")
        for d in deg:
            L.append("- %s" % (d.get("detail") if isinstance(d, dict) else d))
    L.append("")
    return "\n".join(L)


def render_mermaid_route(route):
    lines = ["flowchart LR"]
    for i, s in enumerate(route):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("step_id") or ("R%d" % (i + 1)))
        label = _mmd_esc(s.get("step") or sid)
        lines.append('    %s["%s"]' % (sid, label))
        for dep in (s.get("depends_on") or []):
            lines.append("    %s --> %s" % (dep, sid))
    return "\n".join(lines)


def render_svg_plan(phases, title):
    rows = []
    max_end = 0
    for ph in phases:
        if not isinstance(ph, dict):
            continue
        p = parse_period_months(ph.get("period") or "")
        if p:
            max_end = max(max_end, p[1])
            rows.append((ph.get("phase") or ph.get("milestone") or "阶段", p))
    if not rows:
        rows = [("（无阶段计划）", (1, 1))]
        max_end = 1
    total = max(max_end, 1)
    width = 920
    left = 240
    bar_area = width - left - 40
    row_h = 46
    top = 80
    height = top + row_h * len(rows) + 20
    grid_top = 56
    bar_top = 70

    def x(m):
        return left + (m - 1) / total * bar_area

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="sans-serif">'
        % (width, height, width, height)
    ]
    parts.append('<text x="20" y="28" font-size="16" font-weight="bold">%s（阶段计划，M=相对月份）</text>' % _esc(title or "阶段计划"))
    for m in range(1, total + 1):
        xx = x(m)
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e3e3e3"/>' % (xx, grid_top, xx, bar_top + row_h * len(rows)))
        parts.append('<text x="%.1f" y="%d" font-size="10" fill="#888">M%d</text>' % (xx, grid_top - 6, m))
    palette = ["#4c72b0", "#dd8452", "#55a868", "#c44e52", "#8172b2", "#937860", "#ccb974", "#64b5cd"]
    for i, (label, p) in enumerate(rows):
        y = bar_top + i * row_h
        parts.append('<text x="20" y="%d" font-size="12">%s</text>' % (y + 18, _esc(label)))
        x0 = x(p[0])
        x1 = x(p[1] + 1)
        color = palette[i % len(palette)]
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="26" rx="4" fill="%s"/>' % (x0, y, x1 - x0, color))
        parts.append('<text x="%.1f" y="%d" font-size="11" fill="#ffffff">M%d–M%d</text>' % (x0 + 6, y + 18, p[0], p[1]))
    parts.append("</svg>")
    return "\n".join(parts)


def render(out_path, out_dir):
    """从 result.json 生成 proposal.md / proposal_plan.mmd / proposal_plan.svg 并回写 artifacts。"""
    result, err = read_json(out_path)
    if err:
        return False, [err]
    out_dir = out_dir or os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    proposal = result.get("proposal") or {}
    md_path = os.path.join(out_dir, "proposal.md")
    write_text(md_path, render_markdown(result))
    mmd_path = os.path.join(out_dir, "proposal_plan.mmd")
    write_text(mmd_path, render_mermaid_route(proposal.get("technical_route") or []))
    svg_path = os.path.join(out_dir, "proposal_plan.svg")
    write_text(svg_path, render_svg_plan(proposal.get("phase_plan") or [], result.get("title")))
    arts = result.get("artifacts")
    if not isinstance(arts, dict):
        arts = {}
    arts["proposal"] = "proposal.md"
    arts["plan_mmd"] = "proposal_plan.mmd"
    arts["plan_svg"] = "proposal_plan.svg"
    result["artifacts"] = arts
    result.setdefault("change_log", []).append(
        {"at": _now(), "action": "render", "reason": "生成可编辑 Markdown 与路线/阶段预览"}
    )
    write_json(out_path, result)
    return True, [md_path, mmd_path, svg_path]
