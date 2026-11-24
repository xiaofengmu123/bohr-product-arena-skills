#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开题报告与研究计划书 Skill 的核心实现（纯标准库）。

设计取向（与参考包「思维导图」明显不同）：
1. 中间表示 spec.json：先落约束、七要素、对齐矩阵与证据映射，再生成正文。
   正文（proposal.md）是 spec 的渲染结果，二者同源，改 spec 即可重渲染。
2. 模块拆分：main.py 只做命令行分发；核心逻辑集中在 core_impl.py；
   selftest.py 独立做空输入 / 坏 JSON / 骨架 / 校验 / 渲染的自检。
3. 校验顺序固定：契约字段 -> 七要素 -> 对齐矩阵 -> 计划可行性 -> 证据可回溯
   -> 产物存在性。前一级 FAIL 会阻止后一级判定，避免"边写边漏"。

所有函数纯计算、确定性、可重复运行，不依赖网络或第三方库。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 常量与默认契约
# ---------------------------------------------------------------------------

# 题目 output_contract 未显式声明时的默认契约字段（按任务七要素设计）。
DEFAULT_CONTRACT_FIELDS = [
    "title",              # 项目名称
    "background",         # 研究背景与缺口（known/unknown/to_verify）
    "research_questions", # 一到三个可证伪的研究问题
    "objectives",         # 目标与可考核判断标准
    "technical_route",    # 技术路线（有输入/输出/依赖）
    "schedule",           # 阶段计划（里程碑 -> 周期/资源）
    "risks",              # 风险与备选方案（触发信号/影响/决策时点）
    "expected_outcomes",  # 预期成果与考核方式
    "citations",          # 引用与出处（论断 <-> 引用一一对应）
    "artifacts",          # 可编辑产物与预览路径
]

# 七要素（评分维度"实际效果"要求的硬要素）。
SEVEN_ELEMENTS = [
    "background", "research_questions", "objectives",
    "technical_route", "schedule", "risks", "expected_outcomes",
]

# 风险分类（题目要求按数据获取/方法可行性/进度/伦理与合规分类）。
RISK_CATEGORIES = [
    "data_acquisition", "method_feasibility", "progress", "ethics_compliance",
]

# 信封字段（与题目 output_contract 并列，必须始终存在）。
ENVELOPE_FIELDS = [
    "case_id", "status", "sources", "uncertainty", "pending_human", "change_log",
]

# 单材料文件大小上限（字节），超限跳过并记录降级。
MAX_MATERIAL_BYTES = 2 * 1024 * 1024

VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
SOURCE_KINDS = {"fact", "summary", "inference", "conflict", "missing"}


# ---------------------------------------------------------------------------
# 基础 I/O（容错编码 / BOM / 坏 JSON）
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC ISO-8601 时间戳，用于 change_log / runtime。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_text(path: str, max_bytes: int = MAX_MATERIAL_BYTES) -> tuple[str | None, str | None]:
    """按 utf-8-sig 读取文本；坏编码降级为 replace；超限返回 None + 原因。"""
    if not path or not os.path.isfile(path):
        return None, "not_found"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, f"stat_error:{exc}"
    if size > max_bytes:
        return None, f"too_large:{size}>{max_bytes}"
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=encoding, errors="replace") as fh:
                return fh.read(), None
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "undecodable"


def load_json_loose(path: str) -> tuple[dict | list | None, str | None]:
    """容错读取 JSON；BOM 剥离；返回 (obj, error)。"""
    text, err = load_text(path)
    if err:
        return None, err
    try:
        return json.loads(text.lstrip("\ufeff")), None
    except json.JSONDecodeError as exc:
        return None, f"bad_json:{exc.msg}@line{exc.lineno}"


def dump_json(obj, path: str) -> None:
    """写 JSON（ensure_ascii=False、缩进 2），父目录自动创建。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------------------
# 输入发现与契约抽取（先发现，再假设）
# ---------------------------------------------------------------------------

def discover_inputs(input_dir: str) -> dict:
    """扫描输入目录，识别 case.json / task.md / 材料文件，不假设固定键名。"""
    result = {
        "input_dir": os.path.abspath(input_dir),
        "case": None,
        "case_path": None,
        "task_path": None,
        "materials": [],
        "errors": [],
    }
    if not os.path.isdir(input_dir):
        result["errors"].append(f"input_dir_not_found:{input_dir}")
        return result
    names = sorted(os.listdir(input_dir))
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.isfile(full):
            low = name.lower()
            if low in ("case.json", "case.jsonl", "input.json"):
                result["case_path"] = full
            elif low in ("task.md", "task.txt"):
                result["task_path"] = full
            elif low.endswith((".pdf", ".md", ".txt", ".json", ".csv", ".docx", ".tex")):
                result["materials"].append(full)
    if result["case_path"]:
        obj, err = load_json_loose(result["case_path"])
        if err:
            result["errors"].append(f"case_unreadable:{err}")
        else:
            result["case"] = obj
    return result


def _first_mapping(obj, keys: list[str]):
    """从对象里取第一个存在的 dict 键（用于识别 output_contract 的同义键）。"""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
    return None


def extract_contract(case) -> dict:
    """从 case.json 抽取 output_contract；缺失时用默认契约。"""
    if not isinstance(case, dict):
        return {"fields": list(DEFAULT_CONTRACT_FIELDS), "declared": False, "source": "default"}
    for path in (
        ["output_contract"],
        ["output_schema"],
        ["contract"],
        ["required_fields"],
        ["result_contract"],
    ):
        node = case
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok:
            fields = _fields_from(node)
            if fields:
                return {"fields": fields, "declared": True, "source": ".".join(path)}
    # 也可能直接在顶层声明 required_fields
    alt = _first_mapping(case, ["output_contract", "output_schema", "contract", "required_fields"])
    fields = _fields_from(alt)
    if fields:
        return {"fields": fields, "declared": True, "source": "top-level"}
    return {"fields": list(DEFAULT_CONTRACT_FIELDS), "declared": False, "source": "default"}


def _fields_from(node) -> list[str]:
    """从契约对象提取字段名列表（dict 键或 required 列表）。"""
    if isinstance(node, dict):
        if "fields" in node and isinstance(node["fields"], list):
            names = [str(f) for f in node["fields"]]
        else:
            names = [str(k) for k, v in node.items() if v is not None]
        # 保留原始顺序，去重
        out, seen = [], set()
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out
    if isinstance(node, list):
        return [str(x) for x in node if isinstance(x, str)]
    return []


# ---------------------------------------------------------------------------
# 中间表示：骨架与增量更新
# ---------------------------------------------------------------------------

def make_skeleton(case_id: str, contract_fields: list[str], note: str = "") -> dict:
    """生成诚实 partial 骨架：契约字段 + 信封字段 + 空七要素占位。"""
    skeleton: dict = {"case_id": case_id, "status": "partial"}
    for field in contract_fields:
        if field not in skeleton:
            if field in ("title",):
                skeleton[field] = ""
            elif field in ("research_questions", "objectives", "technical_route",
                           "schedule", "risks", "expected_outcomes", "citations",
                           "artifacts", "sources"):
                skeleton[field] = []
            elif field in ("background",):
                skeleton[field] = {"known": [], "unknown": [], "to_verify": [], "sources": []}
            elif field == "alignment":
                skeleton[field] = {"matrix": []}
            else:
                skeleton[field] = None
    for field in ENVELOPE_FIELDS:
        if field not in skeleton:
            skeleton[field] = [] if field in ("sources", "uncertainty", "pending_human", "change_log") else None
        elif field == "status":
            skeleton["status"] = "partial"
    skeleton["errors"] = skeleton.get("errors", [])
    skeleton["degradations"] = skeleton.get("degradations", [])
    # 记录契约字段，便于 validate 复跑时不必重新发现 case.json。
    skeleton["contract_fields"] = list(contract_fields)
    skeleton["runtime"] = {
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "contract_declared": bool(contract_fields),
        "note": note or "skeleton written before analysis",
    }
    if skeleton["change_log"] == []:
        skeleton["change_log"].append({
            "at": _now_iso(), "action": "init_skeleton",
            "reason": note or "先落盘 partial 骨架，之后增量改进",
        })
    return skeleton


def upsert(result: dict, path: list, value) -> None:
    """按路径写入 result，缺失层级自动建 dict。"""
    node = result
    for key in path[:-1]:
        if not isinstance(node, dict):
            return
        if key not in node or not isinstance(node[key], dict):
            node[key] = {}
        node = node[key]
    if isinstance(node, dict):
        node[path[-1]] = value


def log_change(result: dict, action: str, reason: str) -> None:
    result.setdefault("change_log", [])
    result["change_log"].append({"at": _now_iso(), "action": action, "reason": reason})
    if isinstance(result.get("runtime"), dict):
        result["runtime"]["updated_at"] = _now_iso()


# ---------------------------------------------------------------------------
# 校验（顺序固定，FAIL 优先于 WARN）
# ---------------------------------------------------------------------------

def _is_nonempty(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) > 0
    return True


def validate(result: dict, contract_fields: list[str], out_dir: str | None = None) -> list[dict]:
    """返回问题清单；顺序：契约 -> 七要素 -> 对齐 -> 计划 -> 证据 -> 产物。

    out_dir：产物目录（用于 artifacts 存在性检查）；None 时按相对路径相对 CWD 判断。
    """
    issues: list[dict] = []

    def add(level, code, msg):
        issues.append({"level": level, "code": code, "msg": msg})

    # 1. 基础 + 契约字段
    if not isinstance(result, dict):
        add("FAIL", "result_not_object", "result.json 顶层必须是对象")
        return issues
    if not result.get("case_id"):
        add("FAIL", "missing_case_id", "缺少 case_id")
    if "status" not in result or result.get("status") not in VALID_STATUS:
        add("FAIL", "invalid_status", f"status 缺失或非法（允许 {sorted(VALID_STATUS)}）")
    for field in contract_fields:
        if field not in result:
            add("FAIL", "missing_contract_field", f"缺少契约字段 {field}")
        elif not _is_nonempty(result[field]):
            add("WARN", "empty_contract_field", f"契约字段 {field} 为空")

    # 2. 七要素结构
    for elem in SEVEN_ELEMENTS:
        if elem not in result:
            add("FAIL", "missing_element", f"缺少七要素之一：{elem}")
            continue
        val = result[elem]
        if elem in ("research_questions", "objectives", "technical_route",
                    "schedule", "risks", "expected_outcomes", "citations"):
            if not isinstance(val, list):
                add("FAIL", "element_not_list", f"{elem} 应为列表")
            elif not val:
                add("WARN", "element_empty", f"{elem} 为空列表")
        elif elem == "background":
            if not isinstance(val, dict):
                add("FAIL", "background_not_dict", "background 应为对象（known/unknown/to_verify/sources）")
            else:
                for sub in ("known", "unknown", "to_verify"):
                    if sub not in val:
                        add("WARN", "background_missing_slot", f"background 缺少 {sub} 槽位")
        else:
            if not _is_nonempty(val):
                add("WARN", "element_empty", f"{elem} 为空")

    # 3. 对齐矩阵：问题 <-> 目标 <-> 路线 <-> 成果
    q_ids = {q.get("id") for q in result.get("research_questions", []) if isinstance(q, dict)}
    o_ids = {o.get("id") for o in result.get("objectives", []) if isinstance(o, dict)}
    s_ids = {s.get("id") for s in result.get("technical_route", []) if isinstance(s, dict)}
    e_ids = {e.get("id") for e in result.get("expected_outcomes", []) if isinstance(e, dict)}
    matrix = None
    if isinstance(result.get("alignment"), dict):
        matrix = result["alignment"].get("matrix")
    if isinstance(matrix, list) and matrix:
        for i, row in enumerate(matrix):
            if not isinstance(row, dict):
                add("WARN", "alignment_row_bad", f"alignment.matrix[{i}] 非对象")
                continue
            for key, pool in (("question", q_ids), ("objective", o_ids),
                              ("route_step", s_ids), ("outcome", e_ids)):
                ref = row.get(key)
                if ref and ref not in pool:
                    add("WARN", "alignment_dangling_ref",
                        f"alignment.matrix[{i}].{key}={ref} 未指向已存在的 {key}")
    else:
        # 没有显式矩阵时，用引用字段做软校验
        for o in result.get("objectives", []):
            if isinstance(o, dict) and o.get("question_ref") and o["question_ref"] not in q_ids:
                add("WARN", "objective_dangling_ref", f"objective {o.get('id')} 引用不存在的问题 {o['question_ref']}")
        for e in result.get("expected_outcomes", []):
            if isinstance(e, dict) and e.get("objective_ref") and e["objective_ref"] not in o_ids:
                add("WARN", "outcome_dangling_ref", f"expected_outcome {e.get('id')} 引用不存在的目标 {e['objective_ref']}")
        for s in result.get("technical_route", []):
            if isinstance(s, dict):
                for dep in (s.get("depends_on") or []):
                    if dep not in s_ids:
                        add("WARN", "step_dangling_dep", f"technical_route {s.get('id')} 依赖不存在的步骤 {dep}")

    # 4. 计划可行性：周期 / 里程碑月序
    period = None
    if isinstance(result.get("period"), dict):
        period = result["period"]
    elif isinstance(result.get("constraints"), dict):
        period = result["constraints"].get("period")
    if isinstance(period, dict):
        start = period.get("start") or period.get("start_month")
        end = period.get("end") or period.get("end_month")
        for m in result.get("schedule", []):
            if not isinstance(m, dict):
                continue
            ms = m.get("month") or m.get("month_start")
            me = m.get("month_end") or m.get("month")
            if isinstance(start, (int, float)) and isinstance(ms, (int, float)) and ms < start:
                add("WARN", "milestone_before_start", f"里程碑 {m.get('id')} 早于周期起点 {start}")
            if isinstance(end, (int, float)) and isinstance(me, (int, float)) and me > end:
                add("WARN", "milestone_after_end", f"里程碑 {m.get('id')} 晚于周期终点 {end}")

    # 5. 证据可回溯
    sources = result.get("sources") or []
    source_ids = set()
    for src in sources:
        if isinstance(src, dict) and src.get("id"):
            source_ids.add(src["id"])
            if not (src.get("locator") or src.get("quote")):
                add("WARN", "source_no_locator", f"source {src['id']} 缺 locator 与 quote")
            kind = src.get("kind")
            if kind and kind not in SOURCE_KINDS:
                add("WARN", "source_bad_kind", f"source {src['id']} kind={kind} 非法")
    def _check_refs(container, label):
        for item in container:
            if not isinstance(item, dict):
                continue
            refs = item.get("sources") or []
            if not isinstance(refs, list):
                add("WARN", "ref_not_list", f"{label} {item.get('id')} 的 sources 非列表")
                continue
            for ref in refs:
                rid = ref.get("id") if isinstance(ref, dict) else ref
                if isinstance(ref, dict) and ref.get("locator") and ref.get("quote"):
                    continue
                if rid not in source_ids:
                    add("WARN", "dangling_source_ref", f"{label} {item.get('id')} 引用不存在的 source {rid}")
    for key, label in (
        ("research_questions", "research_question"),
        ("objectives", "objective"),
        ("technical_route", "technical_route"),
        ("risks", "risk"),
        ("expected_outcomes", "expected_outcome"),
        ("citations", "citation"),
    ):
        if isinstance(result.get(key), list):
            _check_refs(result[key], label)
    if isinstance(result.get("background"), dict):
        _check_refs([result["background"]], "background")

    # 6. 产物存在性
    artifacts = result.get("artifacts") or []
    if not isinstance(artifacts, list):
        add("FAIL", "artifacts_not_list", "artifacts 应为列表")
    elif not artifacts:
        add("WARN", "artifacts_empty", "artifacts 为空")
    else:
        for art in artifacts:
            path = art.get("path") if isinstance(art, dict) else art
            if not path:
                add("WARN", "artifact_no_path", "artifacts 存在无 path 条目")
                continue
            full = path if os.path.isabs(path) else os.path.join(out_dir or "", path)
            if not os.path.isfile(full):
                add("WARN", "artifact_missing", f"产物不存在：{path}")
    return issues


def fail_count(issues: list[dict]) -> int:
    return sum(1 for i in issues if i["level"] == "FAIL")


# ---------------------------------------------------------------------------
# 渲染：从 result.json 生成可编辑产物
# ---------------------------------------------------------------------------

def _md_quote(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_proposal_md(result: dict) -> str:
    """渲染七要素正文（可编辑 Markdown，保留章节编号）。"""
    title = result.get("title") or "未命名研究计划"
    lines = [f"# {title}", ""]

    bg = result.get("background") or {}
    lines.append("## 1 研究背景与缺口")
    for sub, label in (("known", "已知"), ("unknown", "未知"), ("to_verify", "待验证")):
        items = bg.get(sub) or []
        lines.append(f"### 1.{['1', '2', '3'][['known', 'unknown', 'to_verify'].index(sub)]} {label}")
        for it in items:
            lines.append(f"- {it}")
        lines.append("")

    lines.append("## 2 问题定义")
    for q in result.get("research_questions") or []:
        qid, text = q.get("id", "?"), q.get("question", "")
        fals = q.get("falsifiable", "")
        lines.append(f"- **{qid}** {text}" + (f"（可证伪判据：{fals}）" if fals else ""))
    lines.append("")

    lines.append("## 3 研究目标与判断标准")
    for o in result.get("objectives") or []:
        oid, goal = o.get("id", "?"), o.get("goal", "")
        metric, target = o.get("metric", ""), o.get("target", "")
        judge = o.get("judgment", "")
        lines.append(f"- **{oid}** {goal}")
        if metric or target:
            lines.append(f"  - 指标：{metric}" + (f"，目标值：{target}" if target else ""))
        if judge:
            lines.append(f"  - 判断标准：{judge}")
    lines.append("")

    lines.append("## 4 技术路线")
    for s in result.get("technical_route") or []:
        sid, step = s.get("id", "?"), s.get("step", "")
        dep = f"（依赖：{', '.join(s.get('depends_on') or [])}）" if s.get("depends_on") else ""
        lines.append(f"- **{sid}** {step}{dep}")
    lines.append("")

    lines.append("## 5 阶段计划")
    for m in result.get("schedule") or []:
        mid, label = m.get("id", "?"), m.get("milestone", m.get("name", ""))
        month = m.get("month", m.get("month_start", ""))
        lines.append(f"- **{mid}**（{month}）{label}")
    lines.append("")

    lines.append("## 6 风险与备选方案")
    for r in result.get("risks") or []:
        rid, cat = r.get("id", "?"), r.get("category", "")
        desc = r.get("risk", r.get("description", ""))
        trig = r.get("trigger", "")
        miti = r.get("mitigation", "")
        lines.append(f"- **{rid}**（{cat}）{desc}")
        if trig:
            lines.append(f"  - 触发信号：{trig}")
        if miti:
            lines.append(f"  - 备选方案：{miti}")
    lines.append("")

    lines.append("## 7 预期成果与考核方式")
    for e in result.get("expected_outcomes") or []:
        eid, out = e.get("id", "?"), e.get("outcome", "")
        assess = e.get("assessment", "")
        lines.append(f"- **{eid}** {out}" + (f"（考核：{assess}）" if assess else ""))
    lines.append("")

    cites = result.get("citations") or []
    if cites:
        lines.append("## 8 引用")
        for c in cites:
            if isinstance(c, dict):
                lines.append(f"- {c.get('claim', '')} → {c.get('citation', '')}")
    return "\n".join(lines) + "\n"


def render_evidence_md(result: dict) -> str:
    """渲染证据映射：论断/要素 <-> source（locator + quote）。"""
    lines = ["# 证据映射（可逐条回溯）", ""]
    for src in result.get("sources") or []:
        if not isinstance(src, dict):
            continue
        lines.append(f"- **{src.get('id')}** [{src.get('kind', '')}] {src.get('locator', '')}")
        if src.get("quote"):
            lines.append(f"  > {_md_quote(src['quote'])}")
    if not (result.get("sources") or []):
        lines.append("（暂无 source）")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_alignment_md(result: dict) -> str:
    """渲染对齐矩阵（问题 <-> 目标 <-> 路线 <-> 成果）。"""
    lines = ["# 要素对齐矩阵", ""]
    matrix = None
    if isinstance(result.get("alignment"), dict):
        matrix = result["alignment"].get("matrix")
    if not isinstance(matrix, list) or not matrix:
        # 从引用字段自动重建
        q_by_id = {q.get("id"): q.get("question", "") for q in result.get("research_questions", []) if isinstance(q, dict)}
        o_by_id = {o.get("id"): o.get("goal", "") for o in result.get("objectives", []) if isinstance(o, dict)}
        matrix = []
        for o in result.get("objectives", []):
            if not isinstance(o, dict):
                continue
            matrix.append({
                "question": o.get("question_ref", ""),
                "objective": o.get("id", ""),
                "route_step": "",
                "outcome": "",
            })
        for e in result.get("expected_outcomes", []):
            if isinstance(e, dict) and e.get("objective_ref"):
                matrix.append({"question": "", "objective": e["objective_ref"], "route_step": "", "outcome": e.get("id", "")})
    lines.append("| 研究问题 | 目标 | 路线步骤 | 预期成果 |")
    lines.append("|---|---|---|---|")
    for row in matrix:
        if isinstance(row, dict):
            lines.append(f"| {row.get('question', '')} | {row.get('objective', '')} | {row.get('route_step', '')} | {row.get('outcome', '')} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def render(result: dict, out_dir: str) -> list[str]:
    """生成可编辑产物并返回相对路径列表。"""
    os.makedirs(out_dir, exist_ok=True)
    files = []
    proposal = render_proposal_md(result)
    evidence = render_evidence_md(result)
    alignment = render_alignment_md(result)
    mapping = {
        "proposal.md": proposal,
        "evidence_map.md": evidence,
        "alignment.md": alignment,
    }
    for name, text in mapping.items():
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        files.append(name)
    return files


# ---------------------------------------------------------------------------
# result.json 装配
# ---------------------------------------------------------------------------

def assemble_final(case_id: str, contract_fields: list[str], result: dict, artifacts: list[str]) -> dict:
    """把信封字段补齐，artifacts 写入，确定 status。"""
    out = dict(result)
    out["case_id"] = case_id
    for field in ENVELOPE_FIELDS:
        if field not in out:
            out[field] = [] if field in ("sources", "uncertainty", "pending_human", "change_log") else None
    out.setdefault("errors", [])
    out.setdefault("degradations", [])
    runtime = out.setdefault("runtime", {})
    runtime["updated_at"] = _now_iso()
    if out.get("status") not in VALID_STATUS:
        out["status"] = "partial"
    # artifacts：优先保留已有条目，追加渲染产物
    existing = out.get("artifacts") or []
    if isinstance(existing, list):
        out["artifacts"] = [a for a in existing if a]
    else:
        out["artifacts"] = []
    for name in artifacts:
        if name not in [a.get("path") if isinstance(a, dict) else a for a in out["artifacts"]]:
            out["artifacts"].append({"path": name, "format": name.rsplit(".", 1)[-1] if "." in name else "text"})
    # 契约字段兜底
    for field in contract_fields:
        if field not in out:
            out[field] = None
    return out


if __name__ == "__main__":
    sys.stderr.write("core_impl 是库模块，请通过 scripts/main.py 调用。\n")
    sys.exit(2)
