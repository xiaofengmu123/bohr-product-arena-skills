#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence-index 规则面：量表定义、引文核验、分级聚合、结构校验。

本模块是"判定与复核"的唯一事实来源：判定值受限、阈值明确、无随机数。
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# 判定值词汇表（受限值，保证可比结论）
# ---------------------------------------------------------------------------

# 每条 checkpoints 的"判断"：研究结论是否被原文证据支持
JUDGMENTS = ("支持", "部分支持", "不支持", "证据不足", "无法评价")
# 偏倚领域的四档结论
BIAS_LEVELS = ("明确存在", "可能存在", "未见迹象", "证据不足")
# 检查点关键性
CRITICALITY = ("关键", "重要", "一般")
# 汇总风险等级
GRADES = ("低偏倚风险", "中偏倚风险", "高偏倚风险", "证据不足")

# 关键性权重（分级聚合用）
CRIT_WEIGHT = {"关键": 3, "重要": 2, "一般": 1}
BIAS_WEIGHT = {"明确存在": 1.0, "可能存在": 0.5, "未见迹象": 0.0, "证据不足": 0.0}


# ---------------------------------------------------------------------------
# 量表定义（四类维度 + 文献类型特有检查点）
# ---------------------------------------------------------------------------

DIMENSIONS = [
    {
        "id": "research_design",
        "name": "研究设计",
        "definition": "研究类型是否被正确识别，设计要素（对照、分组、盲法、暴露/结局定义、混杂控制）是否与结论主张相称。",
        "checkpoints": [
            {"id": "design_type", "critical": "关键",
             "question": "研究类型（RCT/队列/病例对照/横断面/定性/系统综述/临床前等）是否在原文明确并可被识别？"},
            {"id": "design_claim_alignment", "critical": "关键",
             "question": "结论主张（因果/关联/描述）是否与研究设计能支撑的推断强度一致？"},
            {"id": "comparator", "critical": "重要",
             "question": "对照组/参照条件是否明确，选择是否合理？"},
            {"id": "allocation_blinding", "critical": "重要",
             "question": "分配/抽样方式是否说明；适用时盲法是否实施并报告？"},
            {"id": "confounding", "critical": "关键",
             "question": "主要混杂因素是否被识别，并在设计或分析中处理？"},
        ],
    },
    {
        "id": "evidence_sufficiency",
        "name": "证据充分性",
        "definition": "样本量、统计功效、效应量及其精度、缺失数据与多重比较是否足以支撑结论。",
        "checkpoints": [
            {"id": "sample_size", "critical": "关键",
             "question": "样本量是否报告，是否有规模/功效依据（功效分析或样本量计算）？"},
            {"id": "effect_estimate", "critical": "关键",
             "question": "主要结果是否给出效应量及精度（置信区间/标准误）？"},
            {"id": "missing_data", "critical": "重要",
             "question": "缺失数据、失访与排除是否报告数量与处理方式？"},
            {"id": "multiplicity", "critical": "一般",
             "question": "多重比较/亚组/敏感性分析是否控制或说明？"},
            {"id": "method_fit", "critical": "重要",
             "question": "统计方法与数据类型/研究设计是否匹配？"},
        ],
    },
    {
        "id": "bias_risk",
        "name": "偏倚风险",
        "definition": "按文献类型选择适用偏倚领域，判定每领域在本文献中明确存在/可能存在/未见迹象/证据不足。",
        "checkpoints": [
            {"id": "selection_bias", "critical": "关键",
             "question": "选择偏倚（纳入/排除、招募、随访）在何处发生，表现如何？"},
            {"id": "performance_bias", "critical": "重要",
             "question": "实施偏倚（干预/暴露执行、盲法、依从）表现如何？"},
            {"id": "detection_bias", "critical": "重要",
             "question": "检测/结局判定偏倚（结局测量、盲评、判定标准）表现如何？"},
            {"id": "attrition_bias", "critical": "重要",
             "question": "失访/退出偏倚表现如何？"},
            {"id": "reporting_bias", "critical": "关键",
             "question": "报告偏倚（选择性报告、结局未预注册）表现如何？"},
        ],
    },
    {
        "id": "reporting_completeness",
        "name": "报告完整性",
        "definition": "方法可复现性、结果完整性、注册/协议、资助与利益冲突、数据/代码与图表可及性。",
        "checkpoints": [
            {"id": "methods_reproducibility", "critical": "关键",
             "question": "方法是否足以被独立研究者复现（对象、步骤、测量、统计）？"},
            {"id": "results_completeness", "critical": "关键",
             "question": "预定义结局是否全部报告（含阴性/不利结果），数字与图表是否一致？"},
            {"id": "registration_protocol", "critical": "重要",
             "question": "是否报告注册号/研究方案，主要结局是否与协议一致？"},
            {"id": "funding_coi", "critical": "一般",
             "question": "资助来源与利益冲突是否披露？"},
            {"id": "data_code_availability", "critical": "一般",
             "question": "数据/代码可用性是否声明；图表/补充材料是否可访问？"},
        ],
    },
]

BIAS_DOMAINS_BY_TYPE = {
    "RCT": ["选择", "实施", "检测", "失访", "报告"],
    "observational": ["选择", "混杂", "测量", "失访", "报告"],
    "preclinical": ["选择/分配", "盲法", "样本量/重复", "结果报告"],
    "systematic_review": ["检索/选择", "数据提取", "纳入研究偏倚", "报告"],
    "qualitative": ["抽样", "可信度/三角验证", "研究者立场", "报告"],
}

# 适用文献类型与检索范围提示
STUDY_TYPE_HINTS = {
    "RCT": "确认随机方法、分配隐藏、盲法、ITT 分析、注册号。",
    "observational": "确认暴露/结局定义、随访、混杂调整、缺失处理。",
    "preclinical": "确认动物/细胞模型、盲法、重复数、统计方法。",
    "systematic_review": "确认检索式、纳入排除标准、质量评价、异质性。",
    "qualitative": "确认抽样策略、数据饱和、编码过程、研究者反思。",
}


# ---------------------------------------------------------------------------
# 引文核验
# ---------------------------------------------------------------------------


def normalize(text):
    """归一化：合并空白、去首尾空白，用于逐字子串匹配。"""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def verify_quotes(index, assessments):
    """逐条核验 quote 是否逐字存在于所标 locator 指向的段落。

    locator 形如 `rel_path#pN`；索引缺失时定位为 locator_not_in_index。
    返回报告（不修改任何产物，只报告）。
    """
    lookup = {}
    if isinstance(index, dict):
        lookup = index.get("lookup", {}) or {}
    summary = {
        "total": 0, "ok": 0, "mismatch": 0, "missing_locator": 0,
        "locator_not_in_index": 0, "missing_quote": 0, "ok_no_quote_required": 0,
    }
    items = []
    for i, a in enumerate(assessments or []):
        summary["total"] += 1
        loc = a.get("locator")
        quote = a.get("quote")
        verdict = a.get("verdict")
        bias = a.get("bias")
        # 仅当存在实质判断（非证据不足/无法评价）时才强制要求引文
        substantive = (verdict not in ("证据不足", "无法评价", None)) or \
                      (bias not in ("证据不足", None))
        if not loc:
            summary["missing_locator"] += 1
            items.append({"index": i, "status": "missing_locator"})
            continue
        if loc not in lookup:
            summary["locator_not_in_index"] += 1
            items.append({"index": i, "locator": loc, "status": "locator_not_in_index"})
            continue
        if not quote:
            if substantive:
                summary["missing_quote"] += 1
                items.append({"index": i, "locator": loc, "status": "missing_quote"})
            else:
                summary["ok_no_quote_required"] += 1
                items.append({"index": i, "locator": loc, "status": "ok_no_quote_required"})
            continue
        para_text = lookup[loc].get("text", "")
        if normalize(quote) in normalize(para_text):
            summary["ok"] += 1
            items.append({"index": i, "locator": loc, "status": "ok"})
        else:
            summary["mismatch"] += 1
            items.append({"index": i, "locator": loc, "status": "mismatch"})
    return {"summary": summary, "items": items}


# ---------------------------------------------------------------------------
# 分级聚合（阈值明确、可解释）
# ---------------------------------------------------------------------------


def _score(item):
    """把单条 assessment 映射为加权偏倚信号与关键缺口。"""
    crit = item.get("critical", "一般")
    if crit not in CRIT_WEIGHT:
        crit = "一般"
    bias = item.get("bias")
    weight = 0.0
    gap = False
    if bias == "明确存在":
        weight = CRIT_WEIGHT[crit] * BIAS_WEIGHT["明确存在"]
    elif bias == "可能存在":
        weight = CRIT_WEIGHT[crit] * BIAS_WEIGHT["可能存在"]
    elif bias == "证据不足":
        gap = (crit == "关键")
    return weight, gap


def grade_assessments(assessments):
    """按文档化阈值把维度判定聚合为整体风险等级。"""
    if not assessments:
        return {
            "overall": "证据不足", "dimensions": {},
            "rationale": "无任何评价条目，材料不足以判定。",
            "thresholds_applied": {},
        }
    total_weight = 0.0
    critical_gaps = 0
    definite_critical = 0
    possible_critical = 0
    possible_any = 0
    dims = {}
    for a in assessments:
        dim = a.get("dimension", "unknown")
        w, gap = _score(a)
        total_weight += w
        if gap:
            critical_gaps += 1
        if a.get("critical") == "关键" and a.get("bias") == "明确存在":
            definite_critical += 1
        if a.get("critical") == "关键" and a.get("bias") == "可能存在":
            possible_critical += 1
        if a.get("bias") in ("明确存在", "可能存在"):
            possible_any += 1
        dims.setdefault(dim, {"items": 0, "weight": 0.0})
        dims[dim]["items"] += 1
        dims[dim]["weight"] += w

    thresholds = {
        "high": "任一关键检查点偏倚明确存在，或≥3个(关键+重要)检查点偏倚明确存在，或≥5个检查点偏倚为明确/可能存在",
        "moderate": "任一重要检查点偏倚明确存在，或≥1个关键检查点偏倚可能存在，或≥2个检查点偏倚可能存在",
        "low": "无明确存在且最多1个一般检查点为可能存在",
        "insufficient": "≥2个关键检查点证据不足，或全文不可得",
    }
    overall = "低偏倚风险"
    rationale = ""
    if definite_critical >= 1 or (definite_critical + possible_any) >= 5:
        overall = "高偏倚风险"
        rationale = "关键检查点明确存在偏倚（%d）或明确/可能存在项≥5（%d）。" % (definite_critical, possible_any)
    elif possible_critical >= 1 or possible_any >= 2:
        overall = "中偏倚风险"
        rationale = "关键检查点可能存在偏倚（%d）或存在偏倚项≥2（%d）。" % (possible_critical, possible_any)
    elif critical_gaps >= 2:
        overall = "证据不足"
        rationale = "≥2 个关键检查点证据不足（%d），不足以分级。" % critical_gaps
    else:
        rationale = "无明确存在偏倚，且至多 1 个一般检查点为可能存在，总体风险低。"

    return {
        "overall": overall,
        "dimensions": {
            k: {"items": v["items"], "weight": round(v["weight"], 3)}
            for k, v in dims.items()
        },
        "rationale": rationale,
        "thresholds_applied": thresholds,
    }


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------


def validate_result(result, contract_names, index=None):
    """校验 result.json；返回 (error_list, warning_list)。"""
    errors = []
    warnings = []
    if not isinstance(result, dict):
        return ["result_not_object"], []
    if not result.get("case_id"):
        errors.append("missing_case_id")
    if result.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        errors.append("invalid_status: %s" % result.get("status"))
    for name in sorted(contract_names or []):
        if name not in result:
            errors.append("missing_contract_field: %s" % name)
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result or not isinstance(result[key], list):
            errors.append("missing_list_field: %s" % key)
    assessments = result.get("assessment")
    if not isinstance(assessments, list):
        errors.append("assessment_not_list")
    else:
        for i, a in enumerate(assessments):
            if not isinstance(a, dict):
                errors.append("assessment_item_not_object: %d" % i)
                continue
            for key in ("dimension", "verdict", "judgment", "locator", "quote", "basis"):
                if key not in a:
                    errors.append("assessment[%d].missing_%s" % (i, key))
            if a.get("verdict") not in ("支持", "部分支持", "不支持", "证据不足", "无法评价"):
                warnings.append("assessment[%d].verdict_outside_vocab: %s" % (i, a.get("verdict")))
            if a.get("critical") not in ("关键", "重要", "一般"):
                warnings.append("assessment[%d].critical_outside_vocab: %s" % (i, a.get("critical")))
    if not assessments:
        warnings.append("assessment_empty")
    if not result.get("candidates"):
        warnings.append("candidates_empty")
    if result.get("status") == "ok" and not assessments:
        warnings.append("status_ok_but_assessment_empty")
    # 交叉校验：索引存在但 result 中有未核验引文时提示
    if index is not None and assessments:
        report = verify_quotes(index, assessments)
        bad = report["summary"]["mismatch"] + report["summary"]["missing_locator"] + \
              report["summary"]["locator_not_in_index"]
        if bad:
            warnings.append("unverified_or_mismatched_quotes: %d" % bad)
    return errors, warnings


if __name__ == "__main__":
    # 冒烟测试：模块可独立加载并验证分级逻辑
    g = grade_assessments([{"dimension": "d", "critical": "关键", "bias": "明确存在"}])
    assert g["overall"] == "高偏倚风险", g
    r = verify_quotes(
        {"lookup": {"f#p0": {"text": "横断面研究"}}},
        [{"locator": "f#p0", "quote": "横断面研究", "verdict": "支持"}],
    )
    assert r["summary"]["mismatch"] == 0 and r["summary"]["ok"] == 1
    print("rubric smoke ok")
