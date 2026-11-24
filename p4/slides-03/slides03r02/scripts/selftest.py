#!/usr/bin/env python3
"""selftest.py — 对 result.json 执行契约与产物自检（仅标准库）。

检查项分两类：
- 机器可判（pass/fail）：结构完整、字段合法、产物文件存在、PDF/PNG 魔数正确；
- 需人工判（needs_human）：语义正确性（数字与源一致、单页单一主张等），只列出不替人下结论。

默认退出码恒为 0；加 --strict 时，任一机器 fail 项才返回非零。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WRAPPER_KEYS = ["case_id", "status", "sources", "uncertainty", "pending_human", "change_log"]
CONTENT_KEYS = ["report_goal", "audience", "narrative", "pages",
                "editable_deck", "evidence_map", "qa_prep", "rendering"]
VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
EVIDENCE_LEVELS = {"high", "medium", "low"}
PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def load_json(path):
    if not os.path.exists(path):
        return None, "missing: {0}".format(path)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except (ValueError, OSError) as e:
        return None, "bad json: {0}".format(e)


def _resolve(p, base):
    if not p:
        return None
    if os.path.isabs(p):
        return p
    return os.path.join(base, p)


def _magic_ok(path, kind):
    if not os.path.exists(path):
        return False, "missing"
    if kind == "pdf":
        with open(path, "rb") as f:
            return f.read(5).startswith(PDF_MAGIC), "magic mismatch"
    if kind == "png":
        with open(path, "rb") as f:
            return f.read(8) == PNG_MAGIC, "magic mismatch"
    return os.path.getsize(path) > 0, "empty"


def check(result, output_dir, work_dir):
    checks = []

    def add(cid, name, dim, status, detail):
        checks.append({"id": cid, "name": name, "dimension": dim,
                       "status": status, "detail": detail})

    if not isinstance(result, dict):
        add("C00", "结果可解析", "可靠体验", "fail", "result.json 不是 JSON 对象")
        return checks

    # —— 结构完整（可靠体验 / 实际效果） ——
    add("C01", "case_id 非空", "可靠体验",
        "pass" if isinstance(result.get("case_id"), str) and result["case_id"] else "fail",
        "case_id={0}".format(result.get("case_id")))
    add("C02", "status 合法", "可靠体验",
        "pass" if result.get("status") in VALID_STATUS else "fail",
        "status={0}".format(result.get("status")))
    missing_wrap = [k for k in WRAPPER_KEYS if k not in result]
    add("C03", "包裹字段齐全", "可靠体验",
        "pass" if not missing_wrap else "fail",
        "缺失: {0}".format(missing_wrap) if missing_wrap else "齐全")
    missing_content = [k for k in CONTENT_KEYS if k not in result]
    add("C04", "内容契约字段齐全", "实际效果",
        "pass" if not missing_content else "fail",
        "缺失: {0}".format(missing_content) if missing_content else "齐全")

    # —— 证据溯源（实际效果 40 分） ——
    pages = result.get("pages")
    add("C05", "pages 为列表", "实际效果",
        "pass" if isinstance(pages, list) else "fail",
        "pages 类型: {0}".format(type(pages).__name__))
    page_issues = []
    if isinstance(pages, list):
        for i, pg in enumerate(pages):
            if not isinstance(pg, dict):
                page_issues.append("page[{0}] 非对象".format(i))
                continue
            if not isinstance(pg.get("single_claim"), str) or not pg["single_claim"].strip():
                page_issues.append("page[{0}] 缺 single_claim".format(i))
            if pg.get("evidence_strength") not in EVIDENCE_LEVELS:
                page_issues.append("page[{0}] evidence_strength 非法".format(i))
            refs = pg.get("source_refs")
            if pg.get("evidence_strength") in ("high", "medium") and not refs:
                page_issues.append("page[{0}] 高强度但无 source_refs".format(i))
    add("C06", "每页单一主张+强度合法", "实际效果",
        "pass" if isinstance(pages, list) and not page_issues else ("fail" if page_issues else "warn"),
        "; ".join(page_issues) if page_issues else "ok")

    em = result.get("evidence_map")
    em_issues = []
    entries = []
    if isinstance(em, list):
        entries = em
    elif isinstance(em, dict):
        entries = list(em.values())
    for j, e in enumerate(entries):
        if not isinstance(e, dict):
            em_issues.append("evidence[{0}] 非对象".format(j))
            continue
        if not e.get("locator"):
            em_issues.append("evidence[{0}] 缺 locator".format(j))
        if not e.get("quote"):
            em_issues.append("evidence[{0}] 缺 quote".format(j))
    add("C07", "evidence_map 含 locator+quote", "实际效果",
        "pass" if not em_issues else ("fail" if em_issues else "warn"),
        "; ".join(em_issues) if em_issues else "ok（或未提供）")

    # —— 产物存在（可靠体验 10 分） ——
    ed = result.get("editable_deck")
    deck_path = ed.get("path") if isinstance(ed, dict) else None
    deck_abs = _resolve(deck_path, output_dir)
    add("C08", "可编辑演示文件存在", "可靠体验",
        "pass" if deck_abs and os.path.isfile(deck_abs) else
        ("warn" if deck_path is None else "fail"),
        deck_abs or "未声明 editable_deck.path")

    preview_issues = []
    preview_count = 0
    render = result.get("rendering")
    for key, kind in (("pdf", "pdf"), ("png", "png")):
        if isinstance(render, dict):
            p = render.get(key)
            if p:
                preview_count += 1
                ok, why = _magic_ok(_resolve(p, output_dir), kind)
                if not ok:
                    preview_issues.append("{0}: {1}".format(key, why))
    add("C09", "渲染预览 PDF/PNG 存在", "可靠体验",
        "pass" if preview_count and not preview_issues else
        ("warn" if preview_count == 0 else "fail"),
        "检查 {0} 个预览".format(preview_count) + ("; " + "; ".join(preview_issues) if preview_issues else ""))

    # —— 诚实标注（异常处理 3 分） ——
    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        add("C10_{0}".format(key), "{0} 为列表".format(key), "可靠体验",
            "pass" if isinstance(result.get(key), list) else "fail",
            "类型: {0}".format(type(result.get(key)).__name__))
    cl = result.get("change_log")
    add("C11", "change_log 含时间戳", "使用增益",
        "pass" if isinstance(cl, list) and cl and all(isinstance(x, dict) and x.get("ts") for x in cl) else
        ("warn" if not cl else "fail"),
        "change_log 条数: {0}".format(len(cl) if isinstance(cl, list) else 0))

    # —— 需人工判定的语义项 ——
    add("H01", "数字/图表引用与源一致", "实际效果", "needs_human",
        "机器只能检查结构，语义一致性需人工对照材料")
    add("H02", "取舍决策可复核", "使用增益", "needs_human",
        "narrative/change_log 是否记录上页与顺序决策")
    add("H03", "问答预演覆盖机制/数据/对照/局限", "创新增益", "needs_human",
        "qa_prep 是否四类追问并给可引用页面")

    return checks


def summarize(checks):
    dims = {}
    for c in checks:
        d = dims.setdefault(c["dimension"], {"pass": 0, "fail": 0, "warn": 0, "needs_human": 0})
        d[c["status"]] = d.get(c["status"], 0) + 1
    return dims


def render_report(checks):
    lines = ["# 自检报告", ""]
    by_dim = {}
    for c in checks:
        by_dim.setdefault(c["dimension"], []).append(c)
    mark = {"pass": "[x]", "fail": "[ ]", "warn": "[?]", "needs_human": "[人]"}
    for dim in ("实际效果", "使用增益", "创新增益", "可靠体验", "生态贡献"):
        if dim not in by_dim:
            continue
        lines.append("## {0}".format(dim))
        for c in by_dim[dim]:
            lines.append("- {0} {1} — {2}".format(mark.get(c["status"], "[ ]"), c["name"], c["detail"]))
        lines.append("")
    lines.append("## 汇总")
    for d, counts in summarize(checks).items():
        lines.append("- {0}: pass={1} fail={2} warn={3} needs_human={4}".format(
            d, counts["pass"], counts["fail"], counts["warn"], counts["needs_human"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="result.json 契约与产物自检（仅标准库）")
    ap.add_argument("--result", default="/app/output/result.json", help="result.json 路径")
    ap.add_argument("--work", default="/app/work", help="工作目录")
    ap.add_argument("--output", default="/app/output", help="输出目录（解析相对产物路径用）")
    ap.add_argument("--strict", action="store_true", help="任一机器 fail 项返回非零")
    a = ap.parse_args(argv)

    result, err = load_json(a.result)
    if result is None:
        result = {}
    checks = check(result, a.output, a.work)
    report = render_report(checks)
    report_obj = {"ok": True, "checks": checks, "summary": summarize(checks)}
    os.makedirs(a.work, exist_ok=True)
    with open(os.path.join(a.work, "selftest_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_obj, f, ensure_ascii=False, indent=2)
    print(report)
    hard_fail = any(c["status"] == "fail" for c in checks)
    if a.strict and hard_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
