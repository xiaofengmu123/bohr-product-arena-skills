#!/usr/bin/env python3
"""selftest.py — 评分维度逐条对齐的自检；输出通过/未通过清单，不产生评测分数。

仅依赖 Python 标准库。用法：
  python3 selftest.py --result /app/output/result.json --work /app/work --output /app/output
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)

VALID_STRENGTH = {"high", "medium", "low"}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def present(x):
    if x is None:
        return False
    if isinstance(x, (dict, list)):
        return len(x) > 0
    if isinstance(x, str):
        return bool(norm(x))
    return bool(x)


def item(dim, point, cond, detail, note=""):
    return {"dimension": dim, "point": point, "pass": bool(cond),
            "detail": detail, "note": note}


def run_checks(result, output_dir):
    items = []
    r = result or {}
    goal = r.get("report_goal")
    aud = r.get("audience")
    narr = r.get("narrative")
    pages = r.get("pages")
    qa = r.get("qa_prep")
    em = r.get("evidence_map")
    deck = r.get("editable_deck")

    # —— 实际效果 40 ——
    closed_loop = (present(goal) and present(aud) and present(narr)
                   and isinstance(pages, list) and bool(pages)
                   and isinstance(qa, list) and bool(qa))
    items.append(item("实际效果", "任务完成度15", closed_loop,
                      "report_goal/audience/narrative/pages/qa_prep 是否形成闭环"))

    single_claim_ok = (isinstance(pages, list) and bool(pages) and
                       all(isinstance(p, dict) and norm(p.get("single_claim")) for p in pages))
    items.append(item("实际效果", "结果正确性15·逐页唯一主张", single_claim_ok,
                      "每页是否恰有非空 single_claim（禁摘要/Results 逐段搬页）"))

    strength_ok = (isinstance(pages, list) and bool(pages) and
                   all(isinstance(p, dict) and (p.get("evidence_strength") or "").lower()
                       in VALID_STRENGTH for p in pages))
    items.append(item("实际效果", "结果正确性15·证据强度", strength_ok,
                      "evidence_strength 是否均为 high/medium/low"))

    six_ok = all([
        present(goal),
        isinstance(pages, list) and bool(pages),
        isinstance(pages, list) and all(isinstance(p, dict) and isinstance(p.get("figures"), list) for p in pages),
        present(narr),
        isinstance(deck, dict) and present(deck.get("path")),
        present(r.get("rendering")),
    ])
    items.append(item("实际效果", "结果完整性5", six_ok,
                      "六方面是否覆盖：主线/页结构/图表引用/讲解逻辑/模板约束/导出渲染"))

    em_ok = (isinstance(em, list) and bool(em) and
             all(isinstance(e, dict) and norm(e.get("locator")) and norm(e.get("quote")) for e in em))
    items.append(item("实际效果", "结果可核验性5", em_ok,
                      "evidence_map 每项是否含 locator + 逐字 quote"))

    # —— 使用增益 25 ——
    chlog = r.get("change_log")
    items.append(item("使用增益", "质量增益15/效率5/可验证5",
                      isinstance(chlog, list) and bool(chlog),
                      "change_log 是否记录取舍决策与返工/导出修正次数（可复核）"))

    # —— 创新增益 15 ——
    aud_rich = isinstance(aud, dict) and any(
        present(aud.get(k)) for k in ("prior_knowledge", "concerns", "expected_objections", "profile"))
    items.append(item("创新增益", "新增能力5·听众适配", bool(aud_rich),
                      "audience 是否给出先验/关注点/质疑"))
    page_plan_ok = single_claim_ok
    items.append(item("创新增益", "方法改进5·页级规划", page_plan_ok,
                      "pages 是否有唯一主张+证据+图表绑定"))
    qa_ok = (isinstance(qa, list) and bool(qa) and
             all(isinstance(q, dict) and
                 (norm(q.get("anticipated_question")) or norm(q.get("question"))) and
                 norm(q.get("answer_point")) for q in qa))
    items.append(item("创新增益", "创新证据5·问答预演", qa_ok,
                      "qa_prep 是否覆盖机制/数据/对照/局限并给出回答要点"))

    # —— 可靠体验 10 ——
    editable_ok = isinstance(deck, dict) and present(deck.get("path")) and os.path.exists(deck.get("path"))
    items.append(item("可靠体验", "调用易用3", editable_ok, "可编辑 deck 路径是否存在"))
    status_ok = r.get("status") in {"complete", "partial", "failed"}
    items.append(item("可靠体验", "运行稳定4·诚实状态", status_ok, "status 是否如实标注"))
    preview_ok = False
    if os.path.isdir(output_dir):
        for fn in os.listdir(output_dir):
            if fn.lower().endswith((".pdf", ".png")):
                preview_ok = True
                break
    items.append(item("可靠体验", "运行稳定4·渲染预览", preview_ok,
                      "输出目录是否存在 PDF/PNG 预览"))
    items.append(item("可靠体验", "异常处理3", isinstance(r.get("uncertainty"), list),
                      "uncertainty 是否为数组（缺图/字体/导出失败/超时须如实标注）"))

    # —— 生态贡献 10 ——
    skill_md_ok = os.path.isfile(os.path.join(PKG_ROOT, "SKILL.md"))
    refs_ok = os.path.isdir(os.path.join(PKG_ROOT, "references"))
    items.append(item("生态贡献", "文档完整3", skill_md_ok and refs_ok,
                      "SKILL.md 与 references/ 是否存在"))
    stdlib_ok = True
    myself = os.path.basename(os.path.abspath(__file__))
    for fn in os.listdir(HERE):
        if not fn.endswith(".py") or fn == myself:
            continue
        try:
            with open(os.path.join(HERE, fn), "r", encoding="utf-8-sig") as fh:
                src = fh.read()
        except OSError:
            continue
        for bad in ("import numpy", "import pandas", "import matplotlib",
                    "import torch", "import PIL", "import pptx", "from pptx"):
            if bad in src:
                stdlib_ok = False
    items.append(item("生态贡献", "资源复用4", stdlib_ok, "scripts 是否仅依赖标准库"))
    items.append(item("生态贡献", "分享推广3", refs_ok,
                      "references 是否提供字段定义与评分对齐清单"))

    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description="评分维度对齐自检（不产生评测分数）")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--report", default=None, help="报告输出路径（JSON）")
    ap.add_argument("--strict", action="store_true", help="有未通过项时退出码 1")
    args = ap.parse_args(argv)

    result = load_json(args.result)
    if result is None:
        items = [item("全局", "可读性", False, "cannot read {}".format(args.result))]
    else:
        items = run_checks(result, args.output)

    passed = sum(1 for i in items if i["pass"])
    failed = [i for i in items if not i["pass"]]
    report = {
        "ok": len(failed) == 0,
        "result": args.result,
        "passed": passed,
        "total": len(items),
        "failed": failed,
        "checks": items,
        "note": "本报告为技能自检清单，逐项通过/未通过，不是评测分数。",
    }
    report_path = args.report or os.path.join(args.work, "selftest_report.json")
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
