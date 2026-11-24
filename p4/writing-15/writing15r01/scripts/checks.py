#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长文档一致性检查与事实抽取（仅 Python 标准库，全部为可复核启发式）。

提供两类能力：
- extract_facts(texts): 抽取标题、术语/缩写、数字带单位、图/表/公式编号、引用标记；
- run_checks(facts): 检查编号连续、交叉引用目标、引用缺漏、同指标数值冲突。

所有启发式结果都带 locator（文件相对路径:行号）与逐字 quote；不声称精确，
只用于提示 Agent 复核，不自动改写原文。
"""

import re

# ---------- 通用工具 ----------

def _line_locator(rel, line_no):
    return "%s#L%d" % (rel, line_no)


def _trunc(s, n=240):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- 事实抽取 ----------

HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+)$")
CN_SECTION_RE = re.compile(
    r"^\s*(第\s*[0-9一二三四五六七八九十百]+\s*[章节部分篇]|[0-9]+(?:\.[0-9]+)+\s+)\s*(.*)$"
)
TERM_CN_EN_RE = re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\- ]{1,30}?)\s*[（(]\s*([A-Za-z][A-Za-z0-9\-\s]{1,20})\s*[)）]")
ABBR_RE = re.compile(r"\b([A-Z]{2,10})\s*\(\s*([^()]{2,80})\s*\)")
NUM_UNIT_RE = re.compile(
    r"[-+]?\d+(?:\.\d+)?\s*(?:%|‰|nm|µm|μm|mm|cm|m|km|mg|g|kg|μg|ug|s|ms|h|min|Hz|kHz|MHz|GHz|V|mV|A|mA|K|℃|°C|Pa|kPa|MPa|GPa|mol|mmol|M|mM|μM|uM|L|mL|μL|uL|个|篇|人|组|例|万元|亿元|年|月|天)"
)
# 无单位的科学数值（≥2 位小数），用于捕获“率为 0.001”这类表述
NUM_BARE_RE = re.compile(r"[-+]?\d+\.\d{2,}")
FIG_TABLE_RE = re.compile(r"(?:图|表)\s*[0-9]+[a-zA-Z]?|(?:Figure|Table|Fig\.)\s*[0-9]+[a-zA-Z]?")
CITE_NUM_RE = re.compile(r"\[(\d+(?:[-,]\s*\d+)*)\]")
CITE_AUTHOR_RE = re.compile(r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z\-]+)?\s*,?\s*\d{4}[a-z]?)\)")


def extract_facts(materials):
    """materials: discover.scan_inputs 返回的 materials 列表。返回 dict。"""
    facts = {
        "headings": [],
        "terms": [],
        "abbreviations": [],
        "numbers": [],
        "fig_tables": [],
        "citations": [],
    }
    for mat in materials:
        text = mat.get("text")
        rel = mat.get("rel", mat.get("path", "?"))
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            loc = _line_locator(rel, i)
            m = HEADING_RE.match(line)
            if m:
                facts["headings"].append({"locator": loc, "level": len(m.group(1)), "title": m.group(2).strip(), "quote": _trunc(line)})
                continue
            m2 = CN_SECTION_RE.match(line)
            if m2 and len(line.strip()) <= 80:
                facts["headings"].append({"locator": loc, "level": 0, "title": line.strip(), "quote": _trunc(line)})
            for t in TERM_CN_EN_RE.finditer(line):
                facts["terms"].append({"locator": loc, "term": t.group(1).strip(), "symbol": t.group(2).strip(), "quote": _trunc(t.group(0), 120)})
            for a in ABBR_RE.finditer(line):
                facts["abbreviations"].append({"locator": loc, "abbr": a.group(1), "definition": a.group(2).strip(), "quote": _trunc(a.group(0), 120)})
            seen_num_spans = set()
            for n in NUM_UNIT_RE.finditer(line):
                facts["numbers"].append({"locator": loc, "value": n.group(0), "quote": _trunc(line, 160)})
                seen_num_spans.add((n.start(), n.end()))
            for n in NUM_BARE_RE.finditer(line):
                if any(s <= n.start() < e for s, e in seen_num_spans):
                    continue  # 已被单位模式覆盖
                facts["numbers"].append({"locator": loc, "value": n.group(0), "quote": _trunc(line, 160)})
            for f in FIG_TABLE_RE.finditer(line):
                facts["fig_tables"].append({"locator": loc, "label": f.group(0), "quote": _trunc(line, 160)})
            for c in CITE_NUM_RE.finditer(line):
                facts["citations"].append({"locator": loc, "kind": "numeric", "label": c.group(1), "quote": _trunc(line, 160)})
            for c in CITE_AUTHOR_RE.finditer(line):
                facts["citations"].append({"locator": loc, "kind": "author-year", "label": c.group(1), "quote": _trunc(line, 160)})
    return facts


# ---------- 一致性检查 ----------

def _label_num(label):
    m = re.search(r"(\d+)", label)
    return int(m.group(1)) if m else None


def check_fig_table_numbering(facts):
    """图/表编号：重复与跳跃提示。返回 issues 列表。"""
    issues = []
    seen = {}
    for ft in facts["fig_tables"]:
        label = ft["label"]
        n = _label_num(label)
        if n is None:
            continue
        base = re.sub(r"[0-9]+", "N", label)
        key = (base, n)
        seen.setdefault(base, []).append((n, ft))
    for base, items in seen.items():
        nums = sorted(set(n for n, _ in items))
        dupes = {}
        for n, ft in items:
            dupes.setdefault(n, []).append(ft)
        for n, lst in dupes.items():
            if len(lst) > 1:
                issues.append({
                    "category": "fig_table_numbering",
                    "level": "warn",
                    "message": "编号 %s 重复出现 %d 次" % (base.replace("N", str(n)), len(lst)),
                    "locators": [ft["locator"] for ft in lst],
                    "quotes": [ft["quote"] for ft in lst],
                })
        full = set(range(min(nums), max(nums) + 1))
        missing = sorted(full - set(nums))
        if missing and len(nums) >= 2:
            issues.append({
                "category": "fig_table_numbering",
                "level": "info",
                "message": "%s 编号可能存在缺口：缺 %s" % (base, ",".join(map(str, missing))),
                "locators": [items[0][1]["locator"]],
                "quotes": [items[0][1]["quote"]],
            })
    return issues


# 交叉引用触发词：句中出现这些词后跟编号，属于引用而非定义
CROSSREF_HINTS = ("见", "如", "参照", "参见", "如图", "见表", "see", "shown in", "refer to")


def check_crossrefs(facts):
    """交叉引用：句中引用的图/表编号是否在材料中有标题式定义。"""
    issues = []
    defined = set()
    for ft in facts["fig_tables"]:
        quote = ft["quote"]
        if re.match(r"^\s*(图|表|Figure|Table|Fig\.)\s*[0-9]+", quote):
            n = _label_num(ft["label"])
            if n is not None:
                defined.add(n)
    referenced = {}
    for ft in facts["fig_tables"]:
        quote = ft["quote"]
        if re.match(r"^\s*(图|表|Figure|Table|Fig\.)\s*[0-9]+", quote):
            continue  # 已是标题式定义
        low = quote.lower()
        if any(h in low for h in CROSSREF_HINTS):
            n = _label_num(ft["label"])
            if n is not None:
                referenced.setdefault(n, []).append(ft)
    for n in sorted(referenced):
        if n not in defined:
            evs = referenced[n]
            issues.append({
                "category": "crossref",
                "level": "warn",
                "message": "正文引用了编号 %d，但材料中未发现其标题式定义，请核对目标是否存在" % n,
                "locators": [e["locator"] for e in evs],
                "quotes": [e["quote"] for e in evs],
            })
    return issues


def check_citations(facts):
    """引用缺漏：数字引用编号是否有明显越界/缺失的参考文献条目。启发式。"""
    issues = []
    numeric = [c for c in facts["citations"] if c["kind"] == "numeric"]
    nums = []
    for c in numeric:
        for part in re.split(r",", c["label"]):
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    nums.extend(range(int(a.strip()), int(b.strip()) + 1))
                except ValueError:
                    continue
            else:
                try:
                    nums.append(int(part.strip()))
                except ValueError:
                    continue
    if numeric and not facts["citations"]:
        pass
    # 只提示最大编号，供 Agent 判断参考文献列表是否覆盖
    if nums:
        issues.append({
            "category": "citations",
            "level": "info",
            "message": "检测到数字引用，最大编号 %d；请核对参考文献列表是否包含全部编号" % max(nums),
            "locators": [numeric[0]["locator"]],
            "quotes": [numeric[0]["quote"]],
        })
    return issues


def check_value_conflicts(facts):
    """同类型数值冲突：同一单位标签出现多个不同数值时提示，供人工确认。"""
    issues = []
    by_unit = {}
    for n in facts["numbers"]:
        value = n["value"]
        unit_match = re.search(r"([%‰a-zA-Zμµ℃°]+|[个篇人组例万元亿元年月天]+)$", value)
        unit = unit_match.group(1) if unit_match else "?"
        by_unit.setdefault(unit, set()).add(value)
    for unit, vals in by_unit.items():
        if unit in ("%", "?"):
            continue  # % 常见且非冲突；? 表示无法归类，不据以告警
        if len(vals) >= 2:
            issues.append({
                "category": "value_conflict",
                "level": "warn",
                "message": "单位 %s 出现多个不同数值，需人工核对是否为同一指标冲突" % unit,
                "locators": [],
                "quotes": sorted(vals)[:12],
            })
    return issues


def run_checks(facts):
    """汇总所有检查，返回 {"issues": [...], "summary": {...}}。"""
    issues = []
    issues += check_fig_table_numbering(facts)
    issues += check_crossrefs(facts)
    issues += check_citations(facts)
    issues += check_value_conflicts(facts)
    summary = {
        "issue_count": len(issues),
        "warn_count": sum(1 for i in issues if i["level"] == "warn"),
        "error_count": sum(1 for i in issues if i["level"] == "error"),
        "headings": len(facts["headings"]),
        "terms": len(facts["terms"]),
        "numbers": len(facts["numbers"]),
        "fig_tables": len(facts["fig_tables"]),
        "citations": len(facts["citations"]),
    }
    return {"issues": issues, "summary": summary}
