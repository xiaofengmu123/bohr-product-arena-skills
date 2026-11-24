#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — P3 自检与收尾：校验 result.json 契约、交叉核对、渲染可读版。

职责（Agent 调用）
  - 校验 /app/output/result.json：合法 JSON、case_id、status、契约键、四类要素齐备。
  - 交叉核对：deliverable 中每条论断引用的 source_id 必须存在于 sources；
    每条 source 必须有 source_doc + locator + 非空逐字 quote。
  - 生成 /app/output/SELFTEST.md：逐条可勾选清单 + 计数 + 未通过项。
  - --render：把 result.json 中 sources/uncertainty/pending_human/limitations
    渲染成 /app/output/evidence_writing.md 的“证据附录”可读版（不新增事实）。

退出码：结构致命错误（缺 result.json / 非法 JSON / 缺 case_id / status 非法）
  -> 1；其余问题列为 warning 但退出 0，供 Agent 决定是否返工。
只依赖 Python 标准库。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

FINAL_STATUS = ("complete", "partial", "failed", "in_progress")
ALLOWED_SCOPE = ("full", "partial", "weak", "conflict", "none", None, "")
ALLOWED_STRENGTH = ("S1", "S2", "S3", "S4", None, "")
CONTRACT_CORE = ("deliverable", "sources", "change_log")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(2 * 1024 * 1024)
    except OSError as e:
        return None, "read_error: %s" % e
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return json.loads(raw.decode(enc)), None
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None, "bad_json_or_undecodable"


def md_escape(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_appendix(result, out_dir):
    lines = ["# 证据附录（由 result.json 渲染，不新增事实）", ""]
    lines.append("## 来源与证据映射")
    sources = result.get("sources") or []
    if sources:
        lines.append("| 编号 | 键 | 值 | 来源文档 | 定位 | 原文摘录 | 支撑范围 | 强度 |")
        lines.append("|-|-|-|-|-|-|-|-|")
        for s in sources:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                md_escape(s.get("id", "")), md_escape(s.get("key", "")),
                md_escape(s.get("value", "")), md_escape(s.get("source_doc", "")),
                md_escape(s.get("locator", "")), md_escape(s.get("quote", "")),
                md_escape(s.get("support_scope", "")), md_escape(s.get("strength", "")),
            ))
    else:
        lines.append("（无来源记录）")
    for title, key in (("不确定性", "uncertainty"), ("人工确认项", "pending_human"),
                       ("材料缺口与限制", "limitations"), ("变更记录", "change_log")):
        lines.append("")
        lines.append("## %s" % title)
        items = result.get(key) or []
        if items:
            for it in items:
                if isinstance(it, dict):
                    lines.append("- %s" % md_escape(json.dumps(it, ensure_ascii=False)))
                else:
                    lines.append("- %s" % md_escape(it))
        else:
            lines.append("（无）")
    p = os.path.join(out_dir, "evidence_appendix.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return p


def run_checks(result):
    fatal, warn, ok = [], [], []

    if not isinstance(result, dict):
        return fatal, warn, ok

    if not (result.get("case_id")):
        fatal.append("case_id 缺失或为空")
    else:
        ok.append("case_id 存在")

    status = result.get("status")
    if status not in FINAL_STATUS:
        fatal.append("status 非法: %r（应为 complete|partial|failed）" % status)
    elif status == "in_progress":
        warn.append("status 仍为 in_progress，尚未完成终态，需在收尾前置为 complete/partial/failed")
    else:
        ok.append("status=%s" % status)

    for k in CONTRACT_CORE:
        if k not in result or result.get(k) is None:
            warn.append("契约键 %s 缺失或为 null" % k)
        else:
            ok.append("契约键 %s 已回显" % k)

    sources = result.get("sources")
    if not isinstance(sources, list):
        warn.append("sources 不是列表")
        sources = []
    if sources:
        bad = 0
        for i, s in enumerate(sources):
            for field in ("source_doc", "locator", "quote"):
                if not s.get(field):
                    bad += 1
                    warn.append("sources[%d] 缺 %s（无法独立复核）" % (i, field))
                    break
            if s.get("support_scope") not in ALLOWED_SCOPE:
                warn.append("sources[%d] support_scope 非法: %r" % (i, s.get("support_scope")))
            if s.get("strength") not in ALLOWED_STRENGTH:
                warn.append("sources[%d] strength 非法: %r" % (i, s.get("strength")))
        if bad == 0:
            ok.append("全部 %d 条来源含 source_doc+locator+quote" % len(sources))
    else:
        warn.append("sources 为空（若确无材料则合理，否则为缺口）")

    # 交叉核对：论断引用的 source_id 必须存在。
    source_ids = {s.get("id") for s in sources if isinstance(s, dict)}
    dv = result.get("deliverable")
    claims = []
    if isinstance(dv, dict):
        claims = dv.get("claims") or []
        if isinstance(dv, dict) and isinstance(dv.get("evidence_table"), list):
            claims = claims + dv.get("evidence_table")
    dangling = set()
    claim_count = 0
    for c in claims:
        if not isinstance(c, dict):
            continue
        claim_count += 1
        for sid in (c.get("source_ids") or []):
            if sid and sid not in source_ids:
                dangling.add(sid)
    if dangling:
        warn.append("论断引用了不存在的来源编号: %s" % sorted(dangling))
    elif claim_count:
        ok.append("论断—来源交叉核对通过（%d 条论断）" % claim_count)
    else:
        warn.append("deliverable 中无 claims/evidence_table，无法交叉核对")

    for key in ("uncertainty", "pending_human", "limitations", "change_log"):
        v = result.get(key)
        if not isinstance(v, list):
            warn.append("%s 不是列表（缺省应为 []）" % key)
    if isinstance(result.get("change_log"), list) and result["change_log"]:
        ok.append("change_log 含 %d 条记录" % len(result["change_log"]))
    else:
        warn.append("change_log 为空")

    return fatal, warn, ok


def main():
    ap = argparse.ArgumentParser(description="P3 自检与收尾")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--render", action="store_true",
                    help="渲染证据附录 evidence_appendix.md")
    a = ap.parse_args()

    result_path = os.path.join(a.output, "result.json")
    os.makedirs(a.output, exist_ok=True)
    if not os.path.isfile(result_path):
        print(json.dumps({"ok": False, "fatal": ["result.json 不存在"]}, ensure_ascii=False))
        return 1

    result, err = load_json(result_path)
    if err:
        print(json.dumps({"ok": False, "fatal": [err]}, ensure_ascii=False))
        return 1

    fatal, warn, ok = run_checks(result)

    if a.render:
        render_appendix(result, a.output)

    # 落盘 SELFTEST.md 逐条可勾选清单。
    lines = ["# 自检清单（SELFTEST）", ""]
    lines.append("- 生成时间：%s" % utcnow())
    lines.append("- 结果文件：%s" % result_path)
    lines.append("")
    lines.append("## 通过项")
    for x in ok:
        lines.append("- [x] %s" % x)
    lines.append("")
    lines.append("## 警告项")
    for x in warn:
        lines.append("- [ ] %s" % x)
    lines.append("")
    lines.append("## 致命错误")
    for x in fatal:
        lines.append("- [ ] %s" % x)
    lines.append("")
    lines.append("结论：%s" % ("通过" if not fatal else "未通过"))
    with open(os.path.join(a.output, "SELFTEST.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    summary = {"ok": not fatal, "fatal": fatal, "warnings": warn,
               "passes": ok, "counts": {"ok": len(ok), "warn": len(warn), "fatal": len(fatal)}}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not fatal else 1


if __name__ == "__main__":
    sys.exit(main())
