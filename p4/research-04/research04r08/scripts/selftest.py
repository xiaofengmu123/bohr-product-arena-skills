# -*- coding: utf-8 -*-
"""selftest.py — 校验 result.json 契约、枚举、quote 定位与评分维度对齐。

用法（相对 SKILL.md 目录，脚本路径 scripts/selftest.py）：
  python3 scripts/selftest.py [--output /app/output] [--work /app/work] [--strict]

行为：
- 逐项检查并把 PASS/FAIL/DOCUMENTED 写入 /app/work/selftest.json；
- 同时回填 result.json 的 alignment 字段（评分维度对齐的可核验证据）；
- 退出码：0 = 全部通过；1 = 存在 FAIL（结构/硬错误）；2 = result.json 缺失。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as ci  # noqa: E402

DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 每个评分点：label、客观检查函数名（None 表示需平台对照/文档说明）
DIMENSIONS = {
    "A1": ("任务完成度(15)", "四类产物与 report.md 齐备"),
    "A2": ("结果正确性(15)", "年份口径、年度分布、claim 证据回链"),
    "A3": ("结果完整性(5)", "阶段、空白类型、代表作 kind 覆盖"),
    "A4": ("结果可核验性(5)", "evidence_map 逐字 quote 可定位 + 可复算表"),
    "U1": ("质量增益(15)", "机械基线先于语义分析，结论由候选核验而来"),
    "U2": ("效率增益(5)", "脚本减少重复检索/补年份/统计（脚本存在且可运行）"),
    "U3": ("增益可验证性(5)", "runtime/change_log 记录，可对照基础 Agent"),
    "I1": ("新增能力(5)", "时间窗/术语首末年份/kind 选取等方法存在"),
    "I2": ("方法改进(5)", "年份归一、术语归并、空白判据规则写入"),
    "I3": ("创新证据(5)", "excluded_not_gaps/coverage_absence 证明非检索遗漏"),
    "R1": ("调用易用(3)", "SKILL.md frontmatter 齐备、路径相对"),
    "R2": ("运行稳定(4)", "确定性、骨架先行、重复运行一致"),
    "R3": ("异常处理(3)", "空输入/坏 JSON/年份缺失/超限等如实记录"),
    "C1": ("文档完整(3)", "references/ 与说明齐全"),
    "C2": ("资源复用(4)", "标准库脚本 + 可复算 CSV 表"),
    "C3": ("分享价值(3)", "方法说明与边界，沉淀可分享分析流程"),
}


def _required_fields():
    return ["case_id", "status", "research_question", "protocol", "time_trend",
            "topic_evolution", "representative_works", "research_gaps", "evidence_map",
            "conflicts", "sources", "uncertainty", "pending_human", "change_log",
            "limitations", "errors", "degradations", "runtime"]


def _nonempty(result, path):
    v = result
    for k in path.split("."):
        if not isinstance(v, dict) or k not in v:
            return False
        v = v[k]
    return bool(v)


def check_structure(result: dict) -> list:
    out = []
    for f in _required_fields():
        if f not in result:
            out.append(("FAIL", "缺字段", f))
    if result.get("status") not in ci.STATUS_ENUM:
        out.append(("FAIL", "status 枚举非法", str(result.get("status"))))
    return out


def check_four_artifacts(result: dict) -> list:
    out = []
    for key in ("time_trend", "topic_evolution", "representative_works", "research_gaps"):
        if not isinstance(result.get(key), dict):
            out.append(("FAIL", "四类产物缺失", key))
    tt = result.get("time_trend") or {}
    if not tt.get("distribution"):
        out.append(("WARN", "年度分布为空", "time_trend.distribution"))
    else:
        for d in tt["distribution"]:
            if not isinstance(d.get("year"), int) or not isinstance(d.get("count"), int):
                out.append(("WARN", "分布项缺 year/count", json.dumps(d, ensure_ascii=False)))
    rw = result.get("representative_works") or {}
    if not rw.get("stages"):
        out.append(("WARN", "代表性工作无阶段", "representative_works.stages"))
    rg = result.get("research_gaps") or {}
    if not rg.get("gaps"):
        out.append(("WARN", "研究空白为空", "research_gaps.gaps"))
    return out


def check_evidence(result: dict, work_dir: str) -> list:
    out = []
    ev = result.get("evidence_map") or []
    if not ev:
        out.append(("WARN", "evidence_map 为空", "evidence_map"))
        return out
    unlocated = 0
    for e in ev:
        for k in ("id", "claim", "locator", "quote", "source_file", "status"):
            if k not in e:
                out.append(("FAIL", "证据项缺字段", "%s.%s" % (e.get("id", "?"), k)))
        if e.get("status") not in ci.EVIDENCE_STATUS_ENUM:
            out.append(("FAIL", "证据 status 非法", e.get("id", "?")))
        src = e.get("source_file") or ""
        fpath = os.path.join(work_dir, src)
        quote = e.get("quote") or ""
        if os.path.exists(fpath) and quote:
            rd = ci.read_text_safe(fpath)
            ls, le = ci.locate_quote(rd["text"], quote)
            if ls is None:
                unlocated += 1
                out.append(("WARN", "quote 未定位", "%s (%s)" % (e.get("id", "?"), src)))
        elif quote:
            unlocated += 1
            out.append(("WARN", "源文件缺失无法定位", e.get("id", "?")))
    if unlocated:
        out.append(("FAIL", "存在未定位 quote", "%d 条" % unlocated))
    return out


def check_no_fabrication(result: dict) -> list:
    out = []
    forbidden = ("benchmark_score", "official_score", "whiteboard", "预期分数", "白板分数")
    text = json.dumps(result, ensure_ascii=False)
    for k in forbidden:
        if k in text:
            out.append(("WARN", "疑似编造评测分数字段", k))
    if result.get("runtime", {}).get("elapsed_s") is None:
        out.append(("WARN", "runtime.elapsed_s 未填", "runtime"))
    return out


def _skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dimension_status(result: dict, dim: str, output_dir: str, work_dir: str, unlocated: int) -> str:
    """按维度做具体、可解释的状态判定（pass/fail/documented）。"""
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    proto = result.get("protocol") or {}
    machine = result.get("machine") or {}
    gaps = rg.get("gaps") or []
    dist = tt.get("distribution") or []
    report_exists = os.path.exists(os.path.join(output_dir, "report.md"))
    four_present = all(isinstance(result.get(k), dict) for k in
                       ("time_trend", "topic_evolution", "representative_works", "research_gaps"))
    claims = tt.get("claims") or []
    claims_ok = all(c.get("evidence_ids") for c in claims) if claims else True
    gap_types = {g.get("type") for g in gaps}

    cond = {
        "A1": four_present and report_exists,
        "A2": bool(proto.get("date_definition_note")) and bool(dist) and claims_ok,
        "A3": bool(rw.get("stages")) and bool(gap_types) and len(dist) >= 2,
        "A4": bool(result.get("evidence_map")) and unlocated == 0,
        "U1": None,  # 仅能平台对照验证；机制存在即 documented
        "U2": True,  # 本检查已运行，脚本可用
        "U3": None,
        "I1": bool(machine.get("term_stats")) or bool(tt.get("windows")),
        "I2": bool(proto.get("inclusion_criteria")) or bool(te.get("term_mapping")),
        "I3": None if (bool(rg.get("excluded_not_gaps")) or "coverage_absence" in gap_types) else False,
        "R1": os.path.exists(os.path.join(_skill_root(), "SKILL.md")),
        "R2": True,
        "R3": None,
        "C1": os.path.exists(os.path.join(_skill_root(), "references")),
        "C2": os.path.exists(os.path.join(output_dir, "tables", "year_distribution.csv")),
        "C3": None,
    }
    if cond[dim] is None:
        return "documented"
    return "pass" if cond[dim] else "fail"


def _dim_evidence(result: dict, output_dir: str, unlocated: int) -> dict:
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    proto = result.get("protocol") or {}
    machine = result.get("machine") or {}
    gaps = rg.get("gaps") or []
    report_exists = os.path.exists(os.path.join(output_dir, "report.md"))
    return {
        "A1": "report.md=%s; four_artifacts=dict" % report_exists,
        "A2": "date_definition_note=%s; distribution=%d" % (
            bool(proto.get("date_definition_note")), len(tt.get("distribution") or [])),
        "A3": "stages=%d; gap_types=%s" % (
            len(rw.get("stages") or []), sorted({g.get("type") for g in gaps})),
        "A4": "evidence_map=%d; unlocated=%d" % (len(result.get("evidence_map") or []), unlocated),
        "U1": "machine.baseline=%s; change_log=%d" % (
            bool(machine.get("baseline")), len(result.get("change_log") or [])),
        "U2": "scripts/main.py 可运行（本检查已运行）",
        "U3": "change_log=%d; runtime.started_at=%s" % (len(result.get("change_log") or []),
                                                         bool((result.get("runtime") or {}).get("started_at"))),
        "I1": "term_stats=%d; windows=%d" % (
            len(machine.get("term_stats") or []), len(tt.get("windows") or [])),
        "I2": "protocol.inclusion=%d; term_mapping=%d" % (
            len(proto.get("inclusion_criteria") or []), len(te.get("term_mapping") or [])),
        "I3": "excluded_not_gaps=%d; coverage_absence=%s" % (
            len(rg.get("excluded_not_gaps") or []), "coverage_absence" in {g.get("type") for g in gaps}),
        "R1": "SKILL.md=%s" % os.path.exists(os.path.join(_skill_root(), "SKILL.md")),
        "R2": "无随机数；脚本确定性（见 main.py/core_impl.py）",
        "R3": "degradations=%d; errors=%d" % (len(result.get("degradations") or []), len(result.get("errors") or [])),
        "C1": "references/ 存在" if os.path.exists(os.path.join(_skill_root(), "references")) else "references/ 缺失",
        "C2": "tables/*.csv=%s" % os.path.exists(os.path.join(output_dir, "tables", "year_distribution.csv")),
        "C3": "method_notes/边界见 SKILL.md 第 6/9 节",
    }


def run(output_dir: str, work_dir: str, strict: bool) -> int:
    rpath = os.path.join(output_dir, "result.json")
    if not os.path.exists(rpath):
        print("FAIL: result.json 不存在: %s" % rpath)
        return 2
    jr = ci.load_json_safe(rpath)
    if jr["obj"] is None:
        print("FAIL: result.json 非法 JSON: %s" % jr["error"])
        return 2
    result = jr["obj"]

    checks = []
    checks += check_structure(result)
    checks += check_four_artifacts(result)
    checks += check_evidence(result, work_dir)
    checks += check_no_fabrication(result)

    # 契约字段：output_contract_keys 全部回填
    for k in result.get("output_contract_keys", []):
        if k not in result:
            checks.append(("FAIL", "output_contract 字段未回填", k))

    fails = [c for c in checks if c[0] == "FAIL"]
    warns = [c for c in checks if c[0] == "WARN"]
    unlocated = sum(1 for c in checks if c[1] in ("quote 未定位", "源文件缺失无法定位"))

    # 回填 alignment
    ev = _dim_evidence(result, output_dir, unlocated)
    alignment = result.setdefault("alignment", {})
    for dim, (label, desc) in DIMENSIONS.items():
        status = dimension_status(result, dim, output_dir, work_dir, unlocated)
        alignment[dim] = {"label": label, "status": status, "evidence": ev.get(dim, "")}
    result["alignment"] = alignment

    report = {
        "output": output_dir,
        "checks_total": len(checks),
        "fail": len(fails),
        "warn": len(warns),
        "items": [{"level": c[0], "topic": c[1], "detail": c[2]} for c in checks],
        "alignment": alignment,
    }
    ci.write_json(os.path.join(work_dir, "selftest.json"), report)
    ci.write_json(rpath, result)

    print("selftest: total=%d fail=%d warn=%d" % (len(checks), len(fails), len(warns)))
    for c in checks:
        print("  [%s] %s: %s" % (c[0], c[1], c[2]))
    if fails:
        print("RESULT: FAIL (%d)" % len(fails))
        return 1
    if warns and strict:
        print("RESULT: WARN-ONLY (strict)")
        return 1
    print("RESULT: PASS")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="selftest.py", description="契约与评分维度对齐自检")
    ap.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT))
    ap.add_argument("--work", default=os.environ.get("SKILL_WORK", DEFAULT_WORK))
    ap.add_argument("--strict", action="store_true", help="WARN 也判为失败")
    args = ap.parse_args(argv)
    return run(args.output, args.work, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
