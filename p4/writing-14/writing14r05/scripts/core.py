#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core library for the research-proposal (开题报告与研究计划书) skill.

Only the Python standard library is used. All functions are deterministic and
idempotent: the same input produces the same output (fixed ordering, no RNG).

Responsibilities
----------------
- input discovery: load case.json / task.md tolerantly (BOM, bad encoding)
- contract extraction: read output_contract / output_schema / required_fields
- skeleton: build a valid partial result.json early (envelope + contract keys)
- validation: check the result against the contract and the seven-element
  proposal schema, returning FAIL / WARN items
- rendering: produce editable markdown (proposal / evidence map / checks) and a
  minimal but real .docx (OOXML built with zipfile) from the markdown
"""

import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

# ---------------------------------------------------------------------------
# Constants: the domain vocabulary for this task
# ---------------------------------------------------------------------------

# 七要素 (seven elements required by the scoring rubric)
SEVEN_ELEMENTS = [
    "background_gap",       # 研究背景与缺口 (known / unknown / to_verify)
    "problems",             # 问题定义 (1-3 falsifiable research questions)
    "objectives",           # 目标 (goal + metric + sample + control + timepoint)
    "technical_route",      # 技术路线 (steps with input/output/dependency)
    "stage_plan",           # 阶段计划 (milestones / critical path / resources)
    "risks",                # 风险与备选方案 (trigger / impact / fallback / decision)
    "expected_outcomes",    # 预期成果 (outcome + criterion + linked objective)
]

# 风险类别 (risk categories used by the rubric)
RISK_CATEGORIES = [
    "data_acquisition",     # 数据获取
    "method_feasibility",   # 方法可行性
    "schedule",             # 进度
    "ethics_compliance",    # 伦理与合规
]

# 四问 (four focus questions: 立得住 / 对得上 / 排得开 / 说得清)
FOUR_CHECKS = [
    "stand_up",             # 立得住：问题具体可证伪，背景真正指向缺口
    "align",                # 对得上：目标/路线/验证/成果与问题一一对应
    "schedule_feasible",    # 排得开：阶段计划落在周期/经费/数据/人员内
    "traceable",            # 说得清：风险有触发信号与备选，论断可追溯
]

ALLOWED_STATUS = [
    "complete",
    "partial",
    "failed",
    "insufficient_evidence",
    "input_error",
]

# Keys that may name the output contract inside a case.json
CONTRACT_KEYS = (
    "output_contract",
    "output_schema",
    "required_fields",
    "contract",
    "schema",
    "fields",
)

# Keys that may name the case identifier
CASE_ID_KEYS = ("case_id", "id", "caseId", "case")


def now_iso():
    """UTC timestamp in ISO-8601, deterministic enough for a change log."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path, default=None):
    """Load JSON with UTF-8 BOM / bad-encoding tolerance. Never raises."""
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        # Fallback: strip a UTF-8 BOM manually and retry.
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            if raw[:3] == b"\xef\xbb\xbf":
                raw = raw[3:]
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return default


def load_text(path, default=""):
    """Load plain text with encoding tolerance."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="replace") as fh:
                return fh.read()
        except (OSError, ValueError):
            continue
    return default


def write_json(path, obj):
    """Write JSON atomically-ish: to a temp name then rename, ensuring valid JSON."""
    path = os.fspath(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Contract discovery
# ---------------------------------------------------------------------------

def extract_case_id(case):
    """Return the case identifier, or '' when absent."""
    if isinstance(case, dict):
        for key in CASE_ID_KEYS:
            val = case.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                return str(val).strip()
    return ""


def find_contract(case):
    """Locate the output contract inside a case object.

    Returns a dict mapping each required field name to its spec (a dict, which
    may be empty). Supports these shapes:

      {"output_contract": {"title": {"required": true}, "proposal": {...}}}
      {"output_contract": {"fields": ["title", "proposal"]}}
      {"output_schema": ["title", "proposal"]}
      {"required_fields": "proposal"}
    """
    if not isinstance(case, dict):
        return {}
    for key in CONTRACT_KEYS:
        if key not in case:
            continue
        spec = case[key]
        if isinstance(spec, dict):
            # {"fields": [...]} wrapper
            fields = spec.get("fields")
            if isinstance(fields, list):
                return {f: {"required": True} for f in fields if isinstance(f, str) and f}
            # {"field": {...}} map
            return {f: (v if isinstance(v, dict) else {}) for f, v in spec.items()}
        if isinstance(spec, list):
            return {f: {"required": True} for f in spec if isinstance(f, str) and f}
        if isinstance(spec, str) and spec.strip():
            return {spec.strip(): {"required": True}}
    return {}


def _default_for_field(name):
    """Guess a neutral empty value for a contract field name."""
    name_l = (name or "").lower()
    plural = any(t in name_l for t in (
        "list", "items", "nodes", "edges", "risks", "milestones",
        "objectives", "problems", "sources", "evidence", "artifacts",
        "tasks", "steps", "stages", "refs", "references", "records",
    ))
    if plural:
        return []
    return None


# ---------------------------------------------------------------------------
# Skeleton (the early partial deliverable)
# ---------------------------------------------------------------------------

def empty_seven_elements():
    return {
        "background_gap": {"known": [], "unknown": [], "to_verify": []},
        "problems": [],
        "objectives": [],
        "technical_route": [],
        "stage_plan": {
            "milestones": [],
            "critical_path": [],
            "resources": [],
            "parallel": [],
        },
        "risks": [],
        "expected_outcomes": [],
    }


def empty_four_checks():
    return {k: {"status": "pending", "note": "", "evidence": []} for k in FOUR_CHECKS}


def empty_constraints():
    return {
        "cycle": {"start": None, "end": None, "duration_months": None},
        "budget": None,
        "data": None,
        "personnel": None,
        "audience": None,
        "genre": None,
        "format": None,
        "discipline_norm": None,
        "guideline_refs": [],
    }


def init_result(case, source_path=None):
    """Build a valid partial result skeleton from a case object.

    The skeleton always carries: case_id, status, every contract field (neutral
    default), the seven-element proposal schema, the four-check schema, the
    constraints object, and the envelope fields (sources / uncertainty /
    pending_human / change_log / artifacts). This lets the agent commit a legal
    JSON immediately and then improve it incrementally.
    """
    result = {"case_id": extract_case_id(case), "status": "partial"}

    # 1) contract fields first, so the harness can find them at the top level
    for fname in find_contract(case):
        result.setdefault(fname, _default_for_field(fname))

    # 2) standard proposal content fields (namespaced, unlikely to collide)
    result.setdefault("title", None)
    result.setdefault("summary", None)
    result.setdefault("constraints", empty_constraints())
    result.setdefault("seven_elements", empty_seven_elements())
    result.setdefault("four_checks", empty_four_checks())
    result.setdefault("evidence_map", [])

    # 3) envelope fields
    result.setdefault("artifacts", [])
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [{
        "at": now_iso(),
        "action": "init",
        "reason": "skeleton partial result written early",
        "source": source_path or "case.json",
    }])
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def check_result(result, output_dir=None, case=None):
    """Validate a result dict. Returns (fails, warns), each a list of dicts
    with keys: level, code, message. FAIL items must be fixed before delivery.
    """
    fails, warns = [], []

    def add(level, code, message):
        item = {"level": level, "code": code, "message": message}
        (fails if level == "FAIL" else warns).append(item)

    if not isinstance(result, dict):
        add("FAIL", "result_not_dict", "result.json 顶层必须是 JSON 对象")
        return fails, warns

    # case_id
    if not extract_case_id(result):
        add("FAIL", "missing_case_id", "缺少非空 case_id")

    # status
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        add("FAIL", "bad_status", "status 必须是 %s 之一" % ", ".join(ALLOWED_STATUS))

    # contract fields (when the case is available)
    if case is not None:
        for fname in find_contract(case):
            if fname not in result:
                add("FAIL", "missing_contract_field", "缺少 output_contract 字段: %s" % fname)
            elif result.get(fname) is None:
                add("WARN", "empty_contract_field", "output_contract 字段为 null: %s" % fname)

    # seven elements
    seven = result.get("seven_elements")
    if not isinstance(seven, dict):
        add("FAIL", "missing_seven_elements", "缺少 seven_elements 对象")
    else:
        for elem in SEVEN_ELEMENTS:
            if elem not in seven:
                add("FAIL", "missing_element", "缺少七要素之一: %s" % elem)
        problems = seven.get("problems") or []
        if isinstance(problems, list) and len(problems) > 3:
            add("WARN", "too_many_problems", "研究问题建议 1-3 个，当前 %d 个" % len(problems))
        if isinstance(problems, list) and len(problems) == 0:
            add("WARN", "no_problems", "problems 为空，研究问题尚未定义")

    # evidence map
    evidence = result.get("evidence_map")
    if not isinstance(evidence, list):
        add("FAIL", "bad_evidence_map", "evidence_map 必须是数组")
    else:
        for idx, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                add("FAIL", "bad_evidence_item", "evidence_map[%d] 必须是对象" % idx)
                continue
            if not (ev.get("claim") or ev.get("quote")):
                add("FAIL", "evidence_no_claim", "evidence_map[%d] 缺少 claim 或 quote" % idx)
            if not (ev.get("locator") or ev.get("quote")):
                add("FAIL", "evidence_no_locator", "evidence_map[%d] 缺少 locator 或逐字 quote" % idx)

    # four checks
    checks = result.get("four_checks")
    if not isinstance(checks, dict):
        add("FAIL", "missing_four_checks", "缺少 four_checks 对象")
    else:
        for ck in FOUR_CHECKS:
            if ck not in checks:
                add("FAIL", "missing_four_check", "缺少四问自检之一: %s" % ck)

    # envelope lists
    for key in ("sources", "uncertainty", "pending_human", "change_log", "artifacts"):
        if not isinstance(result.get(key), list):
            add("FAIL", "bad_envelope", "%s 必须是数组" % key)

    if not isinstance(result.get("change_log"), list) or len(result.get("change_log") or []) == 0:
        add("WARN", "empty_change_log", "change_log 为空，无法还原增量过程")

    # artifacts must point to real files when present
    if isinstance(result.get("artifacts"), list) and result.get("artifacts"):
        if not output_dir:
            add("WARN", "artifacts_unverifiable", "artifacts 未经验证（未提供输出目录）")
        else:
            for art in result.get("artifacts"):
                if not isinstance(art, (dict, str)):
                    continue
                path = art if isinstance(art, str) else (art.get("path") or art.get("file"))
                if not path:
                    add("WARN", "artifact_no_path", "artifacts 中一项缺少 path")
                    continue
                full = path if os.path.isabs(path) else os.path.join(output_dir, path)
                if not os.path.exists(full):
                    add("WARN", "artifact_missing", "产物文件不存在: %s" % path)

    # status honesty: complete requires the seven elements to be populated
    if status == "complete":
        seven = result.get("seven_elements") or {}
        problems = seven.get("problems") or []
        objectives = seven.get("objectives") or []
        if not problems or not objectives:
            add("WARN", "complete_without_content",
                "status=complete 但 problems/objectives 为空，建议降级为 partial")

    return fails, warns


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _table(rows):
    """Render a list of rows (each a list of cell strings) as a markdown table."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    header = norm[0]
    lines = ["| " + " | ".join(str(c) for c in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for r in norm[1:]:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def _kv_table(pairs):
    return _table([["项目", "内容"]] + [[k, v] for k, v in pairs])


def proposal_to_markdown(result):
    """Render the full editable proposal document (proposal.md) with numbered
    sections matching the seven elements. Missing content renders as a marked
    placeholder rather than fabricated text."""
    title = result.get("title") or "开题报告与研究计划书（未命名）"
    summary = result.get("summary") or ""
    constraints = result.get("constraints") or {}
    seven = result.get("seven_elements") or empty_seven_elements()
    status = result.get("status", "partial")

    cycle = constraints.get("cycle") or {}
    lines = []
    lines.append("# " + str(title))
    lines.append("")
    lines.append("> 运行状态：%s；生成时间：%s" % (status, now_iso()))
    audience = constraints.get("audience")
    genre = constraints.get("genre")
    fmt = constraints.get("format")
    if any((audience, genre, fmt)):
        meta = "；".join(x for x in (
            ("受众: %s" % audience) if audience else None,
            ("体裁: %s" % genre) if genre else None,
            ("格式: %s" % fmt) if fmt else None,
        ) if x)
        lines.append("> " + meta)
    lines.append("")

    lines.append("## 摘要")
    lines.append("")
    lines.append(summary or "_（待填写：本课题做什么、为什么现在做、如何验证）_")
    lines.append("")

    lines.append("## 0. 输入约束（周期 / 经费 / 数据 / 人员 / 格式）")
    lines.append("")
    lines.append(_kv_table([
        ("周期", (str(cycle.get("start") or "?") + " ~ " + str(cycle.get("end") or "?")
                  + ("（%s个月）" % cycle.get("duration_months") if cycle.get("duration_months") else ""))),
        ("经费", constraints.get("budget") or "未提供"),
        ("数据", constraints.get("data") or "未提供"),
        ("人员", constraints.get("personnel") or "未提供"),
        ("学科规范", constraints.get("discipline_norm") or "未提供"),
        ("课题指南依据", "; ".join(constraints.get("guideline_refs") or []) or "未提供"),
    ]))
    lines.append("")

    # 1. background & gap
    bg = seven.get("background_gap") or {}
    lines.append("## 1. 研究背景与缺口（已知 — 未知 — 待验证）")
    lines.append("")
    for key, label in (("known", "已知（Known）"), ("unknown", "未知（Unknown）"),
                       ("to_verify", "待验证（To verify）")):
        lines.append("### " + label)
        lines.append("")
        items = bg.get(key) or []
        if items:
            for it in items:
                lines.append("- " + (str(it) if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
        else:
            lines.append("- _（待填写）_")
        lines.append("")

    # 2. problems
    lines.append("## 2. 问题定义（1–3 个可证伪研究问题）")
    lines.append("")
    problems = seven.get("problems") or []
    if problems:
        for i, p in enumerate(problems, 1):
            if isinstance(p, dict):
                q = p.get("question") or p.get("text") or json.dumps(p, ensure_ascii=False)
                fals = p.get("falsifiable") or p.get("criterion") or ""
                gap = p.get("gap_ref") or p.get("background_ref") or ""
                lines.append("### Q%d. %s" % (i, q))
                lines.append("")
                if fals:
                    lines.append("- 可证伪判据：%s" % fals)
                if gap:
                    lines.append("- 对应缺口：%s" % gap)
            else:
                lines.append("- Q%d. %s" % (i, p))
    else:
        lines.append("- _（待填写：把宽泛方向收敛为 1–3 个可回答、可证伪的问题）_")
    lines.append("")

    # 3. objectives
    lines.append("## 3. 目标（每个问题的可考核目标与判断标准）")
    lines.append("")
    objectives = seven.get("objectives") or []
    if objectives:
        rows = [["问题", "目标", "指标", "样本", "对照", "时间点"]]
        for o in objectives:
            if isinstance(o, dict):
                rows.append([
                    o.get("problem_id") or o.get("problem") or "",
                    o.get("goal") or "",
                    o.get("metric") or "",
                    o.get("sample") or "",
                    o.get("control") or "",
                    o.get("timepoint") or "",
                ])
        lines.append(_table(rows))
    else:
        lines.append("- _（待填写：指标、样本、对照、时间点）_")
    lines.append("")

    # 4. technical route
    lines.append("## 4. 技术路线（步骤、输入、输出、依赖与可并行）")
    lines.append("")
    route = seven.get("technical_route") or []
    if route:
        rows = [["步骤", "输入", "输出", "依赖", "可并行"]]
        for s in route:
            if isinstance(s, dict):
                rows.append([
                    s.get("step") or s.get("name") or "",
                    s.get("input") or "",
                    s.get("output") or "",
                    s.get("depends_on") if isinstance(s.get("depends_on"), str)
                    else "; ".join(s.get("depends_on") or []),
                    s.get("parallel_with") if isinstance(s.get("parallel_with"), str)
                    else "; ".join(s.get("parallel_with") or []),
                ])
        lines.append(_table(rows))
    else:
        lines.append("- _（待填写：拆成有输入输出和前后依赖的步骤）_")
    lines.append("")

    # 5. stage plan
    lines.append("## 5. 阶段计划（里程碑 / 关键路径 / 资源 / 可并行）")
    lines.append("")
    plan = seven.get("stage_plan") or {}
    for key, label in (("milestones", "里程碑"), ("critical_path", "关键路径"),
                       ("resources", "资源需求"), ("parallel", "可并行部分")):
        lines.append("### " + label)
        lines.append("")
        items = plan.get(key) or []
        if items:
            for it in items:
                lines.append("- " + (str(it) if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
        else:
            lines.append("- _（待填写）_")
        lines.append("")

    # 6. risks
    lines.append("## 6. 风险与备选方案")
    lines.append("")
    risks = seven.get("risks") or []
    if risks:
        rows = [["类别", "风险", "触发信号", "影响", "备选方案", "决策时点"]]
        for r in risks:
            if isinstance(r, dict):
                rows.append([
                    r.get("category") or r.get("type") or "",
                    r.get("risk") or "",
                    r.get("trigger") or "",
                    r.get("impact") or "",
                    r.get("fallback") or "",
                    r.get("decision_point") or "",
                ])
        lines.append(_table(rows))
    else:
        lines.append("- _（待填写：数据获取/方法可行性/进度/伦理与合规 四类）_")
    lines.append("")

    # 7. expected outcomes
    lines.append("## 7. 预期成果")
    lines.append("")
    outcomes = seven.get("expected_outcomes") or []
    if outcomes:
        rows = [["成果", "考核方式", "关联目标"]]
        for o in outcomes:
            if isinstance(o, dict):
                rows.append([
                    o.get("outcome") or "",
                    o.get("criterion") or "",
                    o.get("linked_objective") or "",
                ])
        lines.append(_table(rows))
    else:
        lines.append("- _（待填写：成果 + 考核方式 + 关联目标）_")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_本文件由 Skill 生成，可继续编辑。未确认项见 result.json 的 uncertainty / pending_human。_")
    return "\n".join(lines)


def evidence_to_markdown(result):
    evidence = result.get("evidence_map") or []
    lines = ["# 证据映射（逐条可回溯）", ""]
    if evidence:
        rows = [["ID", "论断", "定位 locator", "逐字引用 quote", "类型", "支撑对象"]]
        for i, ev in enumerate(evidence, 1):
            if isinstance(ev, dict):
                rows.append([
                    ev.get("id") or ("ev-%02d" % i),
                    ev.get("claim") or "",
                    ev.get("locator") or "",
                    ev.get("quote") or "",
                    ev.get("kind") or "",
                    ev.get("target") or "",
                ])
        lines.append(_table(rows))
    else:
        lines.append("_（暂无证据映射条目）_")
    lines.append("")

    uncertainty = result.get("uncertainty") or []
    pending = result.get("pending_human") or []
    if uncertainty or pending:
        lines.append("## 未确认 / 冲突 / 缺口")
        lines.append("")
        for u in uncertainty:
            if isinstance(u, dict):
                lines.append("- [%s] %s：%s" % (u.get("kind", "?"), u.get("item", "?"), u.get("issue", "")))
            else:
                lines.append("- " + str(u))
        for p in pending:
            if isinstance(p, dict):
                lines.append("- [待人工] %s：%s" % (p.get("item", "?"), p.get("question", "")))
            else:
                lines.append("- [待人工] " + str(p))
    return "\n".join(lines)


def checks_to_markdown(result):
    checks = result.get("four_checks") or {}
    labels = {
        "stand_up": "立得住（问题具体可证伪，背景指向缺口）",
        "align": "对得上（目标/路线/验证/成果与问题一一对应）",
        "schedule_feasible": "排得开（计划落在周期/经费/数据/人员内）",
        "traceable": "说得清（风险有触发信号与备选，论断可追溯）",
    }
    lines = ["# 四问自检", ""]
    for ck in FOUR_CHECKS:
        c = checks.get(ck) or {}
        if not isinstance(c, dict):
            c = {}
        status = c.get("status", "pending")
        lines.append("## %s" % labels.get(ck, ck))
        lines.append("")
        lines.append("- 判定：`%s`" % status)
        if c.get("note"):
            lines.append("- 说明：%s" % c["note"])
        ev = c.get("evidence") or []
        if ev:
            lines.append("- 依据：%s" % "; ".join(str(x) for x in ev))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Minimal .docx writer (standard library only)
# ---------------------------------------------------------------------------

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '</Relationships>'
)

_DOC_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:body>'
)
_DOC_TAIL = '<w:sectPr/></w:body></w:document>'

_INLINE_RE = re.compile(r'(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)')


def _inline_runs(text):
    runs = []
    for tok in _INLINE_RE.split(text or ""):
        if tok == "":
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) >= 4:
            runs.append((tok[2:-2], True, False, False))
        elif tok.startswith("*") and tok.endswith("*") and len(tok) >= 2:
            runs.append((tok[1:-1], False, True, False))
        elif tok.startswith("`") and tok.endswith("`") and len(tok) >= 2:
            runs.append((tok[1:-1], False, False, True))
        else:
            runs.append((tok, False, False, False))
    return runs


def _run_xml(text, bold=False, italic=False, code=False):
    esc = escape(text)
    rpr = ""
    if bold or italic or code:
        rpr = "<w:rPr>"
        if bold:
            rpr += "<w:b/>"
        if italic:
            rpr += "<w:i/>"
        if code:
            rpr += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        rpr += "</w:rPr>"
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, esc)


def _para_xml(runs, style=None, indent=None, num_prefix=None):
    ppr = "<w:pPr>"
    if style:
        ppr += '<w:pStyle w:val="%s"/>' % style
    if indent:
        ppr += '<w:ind w:left="%d"/>' % indent
    ppr += "</w:pPr>"
    body = "".join(_run_xml(t, b, i, c) for (t, b, i, c) in runs)
    if num_prefix:
        body = _run_xml(num_prefix + " ") + body
    return "<w:p>%s%s</w:p>" % (ppr, body)


def _split_table_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _table_xml(rows):
    ncols = max(len(r) for r in rows)
    grid = "".join('<w:gridCol w:w="%d"/>' % (9000 // max(ncols, 1)) for _ in range(ncols))
    out = ["<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>",
           "<w:tblGrid>%s</w:tblGrid>" % grid]
    for r in rows:
        cells = r + [""] * (ncols - len(r))
        out.append("<w:tr>")
        for cell in cells:
            p = _para_xml(_inline_runs(cell))
            out.append("<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>%s</w:tc>" % p)
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def md_to_docx(md_text, out_path):
    """Build a minimal, valid .docx from markdown (stdlib zipfile + OOXML).

    Supported: ATX headings, paragraphs, `-`/`*` bullet lists, `1.` numbered
    lists, pipe tables, and **bold** / *italic* / `code` inline spans.
    """
    lines = md_text.splitlines()
    body = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # table block
        if stripped.startswith("|") and "|" in stripped[1:]:
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_table_row(lines[i]))
                i += 1
            # drop the separator row (|---|)
            if len(rows) > 1 and all(re.fullmatch(r":?-{2,}:?", c) for c in rows[1]):
                rows = [rows[0]] + rows[2:]
            if rows:
                body.append(_table_xml(rows))
            continue
        # headings
        m = re.match(r'^(#{1,6})\s+(.*)$', line)
        if m:
            level = len(m.group(1))
            body.append(_para_xml(_inline_runs(m.group(2)), style="Heading%d" % level))
            i += 1
            continue
        # horizontal rule / thematic break
        if re.match(r'^\s*[-*_]{3,}\s*$', line):
            i += 1
            continue
        # blockquote
        if stripped.startswith(">"):
            body.append(_para_xml(_inline_runs(stripped[1:].strip()), indent=360))
            i += 1
            continue
        # bullet list
        m = re.match(r'^\s*[-*+]\s+(.*)$', line)
        if m:
            body.append(_para_xml(_inline_runs(m.group(1)), indent=360, num_prefix="\u2022"))
            i += 1
            continue
        # numbered list
        m = re.match(r'^\s*\d+[.)]\s+(.*)$', line)
        if m:
            body.append(_para_xml(_inline_runs(m.group(1)), indent=360, num_prefix=m.group(0).split()[0]))
            i += 1
            continue
        # plain paragraph
        body.append(_para_xml(_inline_runs(line)))
        i += 1

    xml_doc = _DOC_HEAD + "".join(body) + _DOC_TAIL
    out_path = os.fspath(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", xml_doc)
    return out_path


# ---------------------------------------------------------------------------
# Artifact assembly
# ---------------------------------------------------------------------------

def build_artifacts(result, output_dir, work_dir=None):
    """Render proposal.md / evidence_map.md / checks.md / proposal.docx and
    record their paths in result['artifacts']. Returns the artifact list."""
    os.makedirs(output_dir, exist_ok=True)
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)

    proposal_md = proposal_to_markdown(result)
    evidence_md = evidence_to_markdown(result)
    checks_md = checks_to_markdown(result)

    paths = {
        "proposal.md": proposal_md,
        "evidence_map.md": evidence_md,
        "checks.md": checks_md,
    }
    for name, text in paths.items():
        with open(os.path.join(output_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")

    try:
        md_to_docx(proposal_md, os.path.join(output_dir, "proposal.docx"))
    except Exception:
        # degrade gracefully: markdown remains the editable artifact
        pass

    artifacts = []
    for name in ("proposal.md", "evidence_map.md", "checks.md"):
        if os.path.exists(os.path.join(output_dir, name)):
            artifacts.append({"path": name, "kind": "editable"})
    if os.path.exists(os.path.join(output_dir, "proposal.docx")):
        artifacts.append({"path": "proposal.docx", "kind": "editable_office"})
    result["artifacts"] = artifacts
    return artifacts
