#!/usr/bin/env python3
"""开题报告与研究计划书（research proposal）Skill 的核心库。

仅依赖 Python 标准库。所有 I/O 均为防御式：坏编码、坏 JSON、缺失文件、
超大文件、空输入目录都能优雅降级而不崩溃；重复运行结果确定（无随机性）。

职责：材料发现、output_contract 抽取、骨架落盘、七要素校验、可编辑产物渲染。
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件 2MB 上限，超出跳过并记录
MAX_MATERIAL_FILES = 200  # 材料文件数量上限，防止目录扫描爆炸

VALID_STATUS = {
    "complete", "partial", "insufficient_evidence", "input_error", "failed"
}

# 七要素（开题报告不可少的骨架）。缺一即影响“结果完整性”。
SEVEN_ELEMENTS = [
    "research_background",   # 研究背景与缺口（已知—未知—待验证）
    "research_problems",     # 研究问题（具体、可证伪）
    "objectives",            # 目标与考核标准（指标/样本/对照/时间点）
    "technical_route",       # 技术路线（步骤/输入/输出/依赖/可并行）
    "phase_plan",            # 阶段计划（里程碑/关键路径/资源）
    "risks",                 # 风险与备选方案（触发信号/影响/备选/决策点）
    "expected_outcomes",     # 预期成果与考核方式
]

# 风险分类（评分“风险类型齐全”的基准词表）
RISK_CATEGORIES = [
    "data_acquisition",   # 数据获取
    "method_feasibility", # 方法可行性
    "schedule",           # 进度
    "ethics_compliance",  # 伦理与合规
    "other",
]

# 材料发现：按扩展名识别的可读文本/半结构化文件。
MATERIAL_EXT = {
    ".md", ".txt", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".bib", ".tex", ".log",
}
# 需要运行时解析工具的文件（PDF/Office），脚本只登记 locator，不虚构内容。
BINARY_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}


# ---------------------------------------------------------------------------
# 防御式 I/O
# ---------------------------------------------------------------------------
def now_iso():
    """返回 UTC ISO 时间戳，用于 change_log / runtime。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, limit=MAX_FILE_BYTES):
    """安全读取文本。返回 (text_or_None, error_or_None)。

    坏编码用 errors="replace" 容错；超大文件直接跳过并记录原因。
    """
    try:
        if not os.path.exists(path):
            return None, "missing"
        size = os.path.getsize(path)
        if size > limit:
            return None, "oversized:%d>limit:%d" % (size, limit)
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), None
    except Exception as exc:  # noqa: BLE001
        return None, "read_error:%s" % exc


def read_json(path):
    """安全读取 JSON。返回 (obj_or_None, error_or_None)。"""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json:%s" % exc


# 别名：main.py 等调用方使用，语义相同（读取 result.json 并容错）。
load_result_path = read_json


def write_text(path, text):
    """安全写文本。返回 (ok, error_or_None)。写失败不抛异常。"""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(text)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, "write_error:%s" % exc


def write_json(path, obj):
    """安全写 JSON（2 空格缩进、保留中文）。"""
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def normalize_status(status):
    """把任意 status 收敛到合法值；非法值回落到 partial。"""
    if status in VALID_STATUS:
        return status
    return "partial"


# ---------------------------------------------------------------------------
# 输入发现与契约抽取
# ---------------------------------------------------------------------------
def discover_materials(input_dir):
    """扫描输入目录，返回 (materials, degradations)。

    materials: [{path, locator, kind, size, note}]
    按名称排序，保证重复运行结果一致；坏目录/无目录返回空列表而非崩溃。
    """
    materials = []
    degradations = []
    try:
        entries = sorted(os.listdir(input_dir)) if os.path.isdir(input_dir) else []
    except Exception as exc:  # noqa: BLE001
        return [], [{"item": "scan", "issue": "listdir:%s" % exc, "kind": "input_error"}]
    count = 0
    for name in entries:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            continue
        count += 1
        if count > MAX_MATERIAL_FILES:
            degradations.append({
                "item": name, "issue": "material_count>limit:%d" % MAX_MATERIAL_FILES,
                "kind": "oversized",
            })
            continue
        ext = os.path.splitext(name)[1].lower()
        try:
            size = os.path.getsize(full)
        except Exception as exc:  # noqa: BLE001
            degradations.append({"item": name, "issue": "stat:%s" % exc, "kind": "unreadable"})
            continue
        if size > MAX_FILE_BYTES:
            degradations.append({
                "item": name, "issue": "oversized:%d>limit:%d" % (size, MAX_FILE_BYTES),
                "kind": "oversized",
            })
            continue
        if ext in MATERIAL_EXT:
            materials.append({
                "path": full, "locator": name, "kind": "text", "size": size,
                "note": "脚本可直接读取；引用时用 段落/行号 做 locator",
            })
        elif ext in BINARY_EXT:
            materials.append({
                "path": full, "locator": name, "kind": "binary", "size": size,
                "note": "需运行时解析/OCR 工具抽取文本；脚本只登记位置，不假设内容",
            })
        else:
            materials.append({
                "path": full, "locator": name, "kind": "other", "size": size,
                "note": "扩展名未识别，按需人工确认",
            })
    return materials, degradations


def extract_case(case_path):
    """读取 case.json。返回 (case_or_None, error_or_None)。"""
    return read_json(case_path)


def case_id_of(case):
    """从 case.json 提取 case_id（兼容 id 键）。"""
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "caseId", "challenge_id"):
        value = case.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def extract_output_contract(case):
    """提取题目 output_contract。兼容 output_contract / contract /
    output_schema / required_fields 等同义键。

    返回 (contract_dict_or_None, error_or_None)。契约必须为 dict；
    缺失时返回 None（Agent 用默认规范字段补齐）。
    """
    if not isinstance(case, dict):
        return None, "case_not_dict"
    for key in ("output_contract", "contract", "output_schema",
                "required_fields", "output_fields"):
        value = case.get(key)
        if isinstance(value, dict):
            return value, None
    return None, "no_output_contract"


def material_paths(case):
    """从 case.json 抽取材料路径（兼容 materials/material/paths/files 等键）。"""
    if not isinstance(case, dict):
        return []
    for key in ("materials", "material_paths", "paths", "files",
                "material_files", "input_files"):
        value = case.get(key)
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


# ---------------------------------------------------------------------------
# 骨架落盘
# ---------------------------------------------------------------------------
def empty_seven():
    """返回七要素的空结构（骨架用；Agent 后续回填）。"""
    return {
        "research_background": {"known": [], "unknown": [], "gap": None,
                                "gap_sources": []},
        "research_problems": [],
        "objectives": [],
        "technical_route": [],
        "phase_plan": {"total_period": None, "budget": None, "personnel": None,
                       "milestones": [], "critical_path": [], "feasibility_note": None},
        "risks": [],
        "expected_outcomes": [],
    }


def build_skeleton(case, case_path):
    """构建诚实骨架。材料不足/输入缺失时如实标注，绝不编造内容。

    返回 result dict。status 依据输入情况：
    - case.json 缺失/坏 JSON → input_error
    - case.json 存在但无材料 → insufficient_evidence
    - 正常 → partial（等 Agent 回填）
    """
    change_log = []
    degradations = []
    uncertainty = []
    status = "partial"

    case_id = case_id_of(case) if isinstance(case, dict) else None
    contract, contract_err = extract_output_contract(case)
    if case is None:
        status = "input_error"
    elif not isinstance(case, dict):
        status = "input_error"
        degradations.append({"item": "case.json", "issue": "case_not_dict", "kind": "input_error"})
    elif contract_err == "no_output_contract":
        degradations.append({"item": "output_contract",
                             "issue": "契约缺失，使用默认规范字段",
                             "kind": "missing"})

    # 初始化契约字段占位：契约声明的键一律原样回填，缺值给 null/[]。
    contract_fields = {}
    if isinstance(contract, dict):
        for key, spec in contract.items():
            default = [] if isinstance(spec, dict) and spec.get("type") == "list" else None
            contract_fields[key] = default

    result = {
        "case_id": case_id or "unknown",
        "status": status,
        "title": None,
        "summary": None,
        "proposal": empty_seven(),
        "artifacts": [],
        "sources": [],
        "uncertainty": uncertainty,
        "pending_human": [],
        "change_log": change_log,
        "errors": [],
        "degradations": degradations,
        "runtime": {"init_at": now_iso(), "budget_seconds": 900,
                    "budget_requests": 64, "input_case": case_path},
    }
    # 契约字段并入顶层（不覆盖信封字段）。
    for key, value in contract_fields.items():
        result.setdefault(key, value)

    if status == "input_error":
        uncertainty.append({
            "item": "case.json", "issue": "无法读取或解析 case.json",
            "kind": "input_error", "action": "确认 /input/case.json 存在且为合法 JSON",
        })
    elif not isinstance(case, dict):
        pass
    else:
        paths = material_paths(case)
        if not paths:
            status = "insufficient_evidence"
            uncertainty.append({
                "item": "materials", "issue": "case.json 未声明材料路径",
                "kind": "missing", "action": "扫描 /input 目录发现材料",
            })
    result["status"] = status
    change_log.append({"at": now_iso(), "action": "init skeleton",
                       "reason": "前 3 分钟内先落盘诚实 partial 骨架，之后只做增量改进"})
    return result


# ---------------------------------------------------------------------------
# 校验（check）
# ---------------------------------------------------------------------------
def validate(result):
    """校验 result.json。返回报告 dict：{fail:[], warn:[], ok:bool}。

    FAIL 级必须修复；WARN 级写入 uncertainty 或正文说明。
    """
    fail, warn = [], []

    if not isinstance(result, dict):
        fail.append("result 不是 JSON 对象")
        return {"fail": fail, "warn": warn, "ok": False}

    if not result.get("case_id") or result.get("case_id") == "unknown":
        warn.append("case_id 为 unknown（case.json 未提供）")
    status = normalize_status(result.get("status"))
    if result.get("status") != status:
        warn.append("status 值非法，已规范化为 %s" % status)

    proposal = result.get("proposal")
    if not isinstance(proposal, dict):
        fail.append("proposal 缺失或不是对象")
    else:
        for element in SEVEN_ELEMENTS:
            if element not in proposal:
                fail.append("缺少七要素：%s" % element)

    # 契约字段：顶层应包含 case.json 声明的契约键（由 init 回填）。
    # 无法在此知道原契约，改为检查“信封四字段 + 主产物字段”齐全。
    for env in ("status", "sources", "uncertainty", "change_log"):
        if env not in result:
            fail.append("缺少信封字段：%s" % env)

    # 证据溯源：每条 sources 记录应有 locator 或 quote 至少其一。
    sources = result.get("sources") or []
    if not isinstance(sources, list):
        fail.append("sources 不是列表")
    else:
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                warn.append("sources[%d] 不是对象" % i)
                continue
            if not (src.get("locator") or src.get("quote")):
                warn.append("sources[%d] 缺 locator 与 quote" % i)

    # artifacts 非空且指向真实文件（若为相对路径则以运行目录解析）。
    artifacts = result.get("artifacts") or []
    if not isinstance(artifacts, list):
        fail.append("artifacts 不是列表")
    elif not artifacts:
        warn.append("artifacts 为空：应至少含可编辑产物与预览")
    else:
        for art in artifacts:
            if isinstance(art, dict):
                path = art.get("path")
            else:
                path = art
            if path and not os.path.exists(path):
                warn.append("artifact 不存在：%s" % path)

    # 目标↔问题↔路线↔成果对齐（对得上）：目标应挂到已存在的问题 id。
    if isinstance(proposal, dict):
        problems = proposal.get("research_problems") or []
        prob_ids = {p.get("id") for p in problems if isinstance(p, dict) and p.get("id")}
        objectives = proposal.get("objectives") or []
        for obj in objectives:
            if isinstance(obj, dict) and obj.get("problem_ref") \
                    and obj["problem_ref"] not in prob_ids:
                warn.append("objective 的 problem_ref 指向不存在的问题：%s"
                            % obj.get("problem_ref"))

    ok = not fail
    return {"fail": fail, "warn": warn, "ok": ok}


# ---------------------------------------------------------------------------
# 渲染（render）
# ---------------------------------------------------------------------------
def xml_escape(text):
    """XML/SVG 文本转义。"""
    if text is None:
        return ""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def md_escape(text):
    """Markdown 单元格转义（避免竖线截断表格）。"""
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_proposal_md(result):
    """把 proposal 结构渲染为可编辑 Markdown。返回字符串。"""
    p = result.get("proposal") or {}
    lines = []
    title = result.get("title") or "开题报告与研究计划书"
    lines.append("# %s\n" % title)
    if result.get("summary"):
        lines.append("> 摘要：%s\n" % str(result["summary"]).replace("\n", " "))
    lines.append("> case_id: %s · status: %s\n"
                 % (result.get("case_id", ""), result.get("status", "")))

    # 1 研究背景与缺口
    bg = p.get("research_background") or {}
    lines.append("\n## 1. 研究背景与缺口（已知—未知—待验证）\n")
    for label, key in (("已知", "known"), ("未知", "unknown")):
        items = bg.get(key) or []
        lines.append("### 1.%d %s\n" % (1 if key == "known" else 2, label))
        for it in items:
            if isinstance(it, dict):
                lines.append("- %s" % (it.get("statement") or it.get("text") or ""))
                srcs = it.get("sources") or []
                if srcs:
                    lines.append("  - 来源：%s" % ", ".join(str(s) for s in srcs))
            else:
                lines.append("- %s" % it)
    lines.append("### 1.3 研究缺口\n")
    gap = bg.get("gap")
    lines.append("%s\n" % (gap if gap else "（待回填：材料是否真的指向该缺口，而非综述）"))
    for s in bg.get("gap_sources") or []:
        lines.append("- 依据：%s" % s)

    # 2 研究问题
    lines.append("\n## 2. 研究问题（1–3 个，具体、可证伪）\n")
    for prob in p.get("research_problems") or []:
        if isinstance(prob, dict):
            lines.append("### %s %s\n" % (prob.get("id", ""), prob.get("question", "")))
            lines.append("- 可证伪判据：%s" % (prob.get("falsifiable_criterion") or "（待填）"))
            if prob.get("scope"):
                lines.append("- 边界：%s" % prob["scope"])
            for s in prob.get("sources") or []:
                lines.append("- 依据：%s" % s)
        else:
            lines.append("- %s" % prob)

    # 3 目标与考核
    lines.append("\n## 3. 目标与考核标准（指标/样本/对照/时间点）\n")
    for obj in p.get("objectives") or []:
        if isinstance(obj, dict):
            lines.append("### %s %s\n" % (obj.get("id", ""), obj.get("objective", "")))
            lines.append("- 对应问题：%s" % (obj.get("problem_ref") or "（待填）"))
            for m in obj.get("metrics") or []:
                if isinstance(m, dict):
                    lines.append("- 指标 %s：阈值 %s（依据：%s）"
                                 % (m.get("name", ""), m.get("threshold", ""),
                                    m.get("basis") or "（待填）"))
                else:
                    lines.append("- 指标：%s" % m)
            for k, label in (("sample", "样本"), ("control", "对照"), ("timepoint", "时间点")):
                if obj.get(k):
                    lines.append("- %s：%s" % (label, obj[k]))
            for s in obj.get("sources") or []:
                lines.append("- 依据：%s" % s)
        else:
            lines.append("- %s" % obj)

    # 4 技术路线
    lines.append("\n## 4. 技术路线（步骤/输入/输出/依赖/可并行）\n")
    for step in p.get("technical_route") or []:
        if isinstance(step, dict):
            lines.append("### %s %s\n" % (step.get("step_id", ""), step.get("name", "")))
            lines.append("- 输入：%s" % (step.get("input") or "（待填）"))
            lines.append("- 输出：%s" % (step.get("output") or "（待填）"))
            lines.append("- 依赖：%s" % ", ".join(step.get("depends_on") or []) or "无")
            if step.get("parallel_with"):
                lines.append("- 可并行：%s" % ", ".join(step["parallel_with"]))
            for s in step.get("sources") or []:
                lines.append("- 依据：%s" % s)
        else:
            lines.append("- %s" % step)

    # 5 阶段计划
    lines.append("\n## 5. 阶段计划（周期/经费/人员/里程碑/关键路径）\n")
    pp = p.get("phase_plan") or {}
    for k, label in (("total_period", "总周期"), ("budget", "经费"), ("personnel", "人员")):
        if pp.get(k):
            lines.append("- %s：%s" % (label, pp[k]))
    for ms in pp.get("milestones") or []:
        if isinstance(ms, dict):
            lines.append("### %s %s（%s）\n" % (ms.get("id", ""), ms.get("deliverable", ""),
                                               ms.get("quarter", "")))
            lines.append("- 任务：%s" % ", ".join(ms.get("tasks") or []))
            if ms.get("resource"):
                lines.append("- 资源：%s" % ms["resource"])
        else:
            lines.append("- 里程碑：%s" % ms)
    if pp.get("critical_path"):
        lines.append("- 关键路径：%s" % " → ".join(pp["critical_path"]))
    if pp.get("feasibility_note"):
        lines.append("- 可行性说明：%s" % pp["feasibility_note"])

    # 6 风险
    lines.append("\n## 6. 风险与备选方案（触发信号/影响/备选/决策点）\n")
    for risk in p.get("risks") or []:
        if isinstance(risk, dict):
            lines.append("### %s [%s] %s\n" % (risk.get("id", ""), risk.get("category", ""),
                                               risk.get("risk", "")))
            for k, label in (("trigger_signal", "触发信号"), ("impact", "影响范围"),
                             ("alternative", "备选方案"), ("decision_point", "决策时点")):
                if risk.get(k):
                    lines.append("- %s：%s" % (label, risk[k]))
            for s in risk.get("sources") or []:
                lines.append("- 依据：%s" % s)
        else:
            lines.append("- %s" % risk)

    # 7 预期成果
    lines.append("\n## 7. 预期成果与考核方式\n")
    for eo in p.get("expected_outcomes") or []:
        if isinstance(eo, dict):
            lines.append("- %s：%s（考核方式：%s）"
                         % (eo.get("id", ""), eo.get("outcome", ""),
                            eo.get("verification_method") or "（待填）"))
            for s in eo.get("sources") or []:
                lines.append("  - 依据：%s" % s)
        else:
            lines.append("- %s" % eo)

    # 边界
    bounds = p.get("boundaries") or {}
    if bounds.get("in_scope") or bounds.get("out_of_scope"):
        lines.append("\n## 边界\n")
        if bounds.get("in_scope"):
            lines.append("- 范围内：%s" % ", ".join(bounds["in_scope"]))
        if bounds.get("out_of_scope"):
            lines.append("- 范围外：%s" % ", ".join(bounds["out_of_scope"]))

    # 证据清单
    sources = result.get("sources") or []
    if sources:
        lines.append("\n## 附录：证据清单\n")
        for i, s in enumerate(sources, 1):
            if isinstance(s, dict):
                quote = s.get("quote") or ""
                lines.append("%d. `%s` %s" % (i, s.get("locator") or "?",
                                              ("— %s" % quote[:120]) if quote else ""))
    return "\n".join(lines)


def render_evidence_map(result):
    """渲染独立证据映射（claim → locator + quote）。"""
    evidence = []
    for s in result.get("sources") or []:
        if isinstance(s, dict):
            evidence.append({
                "locator": s.get("locator"),
                "quote": s.get("quote"),
                "kind": s.get("kind"),
            })
    return {
        "case_id": result.get("case_id"),
        "claim_count": len(result.get("sources") or []),
        "evidence": evidence,
    }


def render_route_mmd(result):
    """把技术路线渲染为 Mermaid 流程图。返回 (mmd_str, ok)。"""
    p = result.get("proposal") or {}
    steps = p.get("technical_route") or []
    if not steps:
        return "flowchart TD\n    A[（技术路线待回填）]\n", False
    lines = ["flowchart TD"]
    ids = []
    for step in steps:
        if isinstance(step, dict):
            sid = step.get("step_id") or "S%d" % (len(ids) + 1)
            ids.append(sid)
            label = step.get("name") or sid
            lines.append('    %s["%s"]' % (sid, label.replace('"', "'")))
    # 顺序连接（可并行的步骤用虚线标注）。
    for i, step in enumerate(steps[:-1]):
        if not isinstance(steps[i], dict) or not isinstance(steps[i + 1], dict):
            continue
        cur = steps[i].get("step_id") or "S%d" % (i + 1)
        nxt = steps[i + 1].get("step_id") or "S%d" % (i + 2)
        if steps[i + 1].get("parallel_with"):
            lines.append("    %s -.-> %s" % (cur, nxt))
        else:
            lines.append("    %s --> %s" % (cur, nxt))
    return "\n".join(lines), True


def render_route_svg(result):
    """把技术路线渲染为自包含纯 SVG 预览（标准库产出，无需外部软件）。

    采用简单垂直布局：每步一个圆角矩形，顺序箭头向下，可并行步用虚线。
    返回 (svg_str, ok)。
    """
    p = result.get("proposal") or {}
    steps = p.get("technical_route") or []
    if not steps:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="520" height="120" '
               'viewBox="0 0 520 120"><rect width="520" height="120" fill="white"/>'
               '<text x="20" y="40" font-size="16" fill="#555">'
               '技术路线待回填（no technical_route steps）</text></svg>')
        return svg, False

    box_w, box_h, gap = 480, 64, 36
    x = 40
    width = box_w + 2 * x
    height = len(steps) * (box_h + gap) + gap
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]
    parts.append('<rect width="%d" height="%d" fill="white"/>' % (width, height))
    positions = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        y = gap + i * (box_h + gap)
        positions.append((i, y))
        label = step.get("name") or step.get("step_id") or "S%d" % (i + 1)
        detail = step.get("output") or ""
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" rx="8" '
                     'fill="#eef4fb" stroke="#2b6cb0" stroke-width="1.5"/>'
                     % (x, y, box_w, box_h))
        parts.append('<text x="%d" y="%d" font-size="15" font-weight="bold" '
                     'fill="#1a365d">%s</text>'
                     % (x + 14, y + 26, xml_escape(label[:40])))
        if detail:
            parts.append('<text x="%d" y="%d" font-size="12" fill="#4a5568">'
                         '输出：%s</text>'
                         % (x + 14, y + 46, xml_escape(detail[:40])))
    for a in range(1, len(positions)):
        _, prev_y = positions[a - 1]
        i, cur_y = positions[a]
        if isinstance(steps[i], dict) and steps[i].get("parallel_with"):
            dash = ' stroke-dasharray="4,3"'
        else:
            dash = ""
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#718096" '
                     'stroke-width="1.5"%s/>'
                     % (x + box_w / 2, prev_y + box_h, x + box_w / 2, cur_y, dash))
        parts.append('<polygon points="%d,%d %d,%d %d,%d" fill="#718096"/>'
                     % (x + box_w / 2 - 5, cur_y - 1,
                        x + box_w / 2 + 5, cur_y - 1,
                        x + box_w / 2, cur_y + 8))
    parts.append('</svg>')
    return "".join(parts), True


def render(result, out_dir):
    """渲染全部可编辑产物，返回 (artifacts, degradations)。

    artifacts: [{path, format, role, generated_at}]
    """
    os.makedirs(out_dir, exist_ok=True)
    artifacts, degradations = [], []

    md_text = render_proposal_md(result)
    md_path = os.path.join(out_dir, "proposal.md")
    ok, err = write_text(md_path, md_text)
    if ok:
        artifacts.append({"path": md_path, "format": "markdown",
                          "role": "editable_main", "generated_at": now_iso()})
    else:
        degradations.append({"item": "proposal.md", "issue": err, "kind": "render_failed"})

    ev = render_evidence_map(result)
    ev_path = os.path.join(out_dir, "evidence_map.json")
    ok, err = write_json(ev_path, ev)
    if ok:
        artifacts.append({"path": ev_path, "format": "json",
                          "role": "evidence_map", "generated_at": now_iso()})
    else:
        degradations.append({"item": "evidence_map.json", "issue": err, "kind": "render_failed"})

    mmd, _ = render_route_mmd(result)
    mmd_path = os.path.join(out_dir, "route.mmd")
    ok, err = write_text(mmd_path, mmd)
    if ok:
        artifacts.append({"path": mmd_path, "format": "mermaid",
                          "role": "route_editable", "generated_at": now_iso()})
    else:
        degradations.append({"item": "route.mmd", "issue": err, "kind": "render_failed"})

    svg, _ = render_route_svg(result)
    svg_path = os.path.join(out_dir, "route.svg")
    ok, err = write_text(svg_path, svg)
    if ok:
        artifacts.append({"path": svg_path, "format": "svg",
                          "role": "route_preview", "generated_at": now_iso()})
    else:
        degradations.append({"item": "route.svg", "issue": err, "kind": "render_failed"})

    return artifacts, degradations
