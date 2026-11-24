#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 对已交付产物做独立、确定性、逐条可勾选的自检。

用法：
  python scripts/selftest.py --out /app/output --work /app/work
退出码：0 = 全部通过；1 = 有失败项（打印逐条 PASS/FAIL 与统计）。
仅用标准库；可被新评分员或评测方直接运行，不依赖任何中间状态。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性",
]
ALLOWED_STATUS = {"in_progress", "done", "partial", "failed"}
HARD_BAN = ["填补空白", "国际首创", "世界首创", "世界第一", "国内外首创", "全球领先"]


def read_json(path, default=None):
    if not path or not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    out_dir = args.out if args.out.endswith(".json") else os.path.join(args.out, "result.json")
    result = read_json(out_dir, {})
    plan = read_json(os.path.join(args.work, "plan.json"), {})
    manifest = read_json(os.path.join(args.work, "source_manifest.json"), {})
    ledger = read_json(os.path.join(args.work, "evidence_ledger.json"), {})

    checks = []  # (name, ok, detail)

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # 1 结果结构
    add("result.json 可解析", isinstance(result, dict), "type=%s" % type(result).__name__)
    add("case_id 非空", bool(result.get("case_id")), repr(result.get("case_id")))
    add("status 合法", result.get("status") in ALLOWED_STATUS, repr(result.get("status")))
    add("artifact.path 存在", bool((result.get("artifact") or {}).get("path")),
        repr((result.get("artifact") or {}).get("path")))

    art_path = (result.get("artifact") or {}).get("path") or ""
    if art_path and os.path.isfile(art_path):
        add("artifact 文件真实存在", True, art_path)
    elif result.get("status") == "done":
        add("artifact 文件真实存在", False, "status=done 但 %s 不存在" % art_path)
    else:
        add("artifact 文件真实存在", False, "尚未生成 %s" % art_path)

    # 2 事实-来源映射
    fsmap = result.get("fact_source_map") or []
    add("fact_source_map 为列表", isinstance(fsmap, list), "len=%d" % len(fsmap))
    with_quote = sum(1 for e in fsmap if isinstance(e, dict) and (e.get("source_ref") or {}).get("quote"))
    with_loc = sum(1 for e in fsmap if isinstance(e, dict) and (e.get("source_ref") or {}).get("loc"))
    add("每条事实有逐字 quote" if not fsmap else "事实条目有逐字 quote",
        with_quote == len(fsmap), "%d/%d" % (with_quote, len(fsmap)))
    add("事实条目有 locator", with_loc == len(fsmap), "%d/%d" % (with_loc, len(fsmap)))
    add("unmapped/conflict 已说明", all(
        isinstance(e, dict) and (e.get("status") != "unmapped" or e.get("note"))
        for e in fsmap) if fsmap else True,
        "unmapped 需在 note 说明原因")

    # 3 七要素与对齐矩阵（中间表示）
    elems = plan.get("seven_elements") or {}
    add("plan.json 含七要素", all(e in elems for e in SEVEN_ELEMENTS),
        "缺: %s" % ",".join(e for e in SEVEN_ELEMENTS if e not in elems))
    drafted = sum(1 for e in SEVEN_ELEMENTS if (elems.get(e) or {}).get("status") == "draft")
    add("七要素已起草" if not elems else "七要素已起草>0",
        drafted > 0, "%d/%d" % (drafted, len(SEVEN_ELEMENTS)))
    matrix = plan.get("alignment_matrix") or []
    complete = sum(1 for r in matrix if isinstance(r, dict)
                   and r.get("objective") and r.get("content")
                   and r.get("route_steps") and r.get("indicator"))
    add("对齐矩阵行完整", complete == len(matrix), "%d/%d" % (complete, len(matrix)))
    dvp = plan.get("done_vs_planned") or {}
    add("已完成/拟开展已标注", bool(dvp.get("done") or dvp.get("planned")),
        "done=%d planned=%d" % (len(dvp.get("done") or []), len(dvp.get("planned") or [])))

    # 4 来源快照与可核验性
    files = manifest.get("files") or []
    add("来源清单非空", bool(files), "files=%d" % len(files))
    add("来源含 SHA256", all(isinstance(f, dict) and f.get("sha256") for f in files) if files else False,
        "files=%d" % len(files))

    # 5 台账与 change_log
    entries = (ledger or {}).get("entries") or []
    add("事实台账存在", isinstance(entries, list), "entries=%d" % len(entries))
    clog = result.get("change_log") or []
    add("change_log 完整", isinstance(clog, list) and len(clog) > 0, "entries=%d" % len(clog))

    # 6 禁止修饰语（正文）
    art_text = read_text(art_path) if (art_path and os.path.isfile(art_path)) else ""
    hard = [w for w in HARD_BAN if w in art_text]
    add("正文无无法核验修饰语", not hard, ",".join(hard))

    passed = [c for c in checks if c[1]]
    failed = [c for c in checks if not c[1]]
    if not args.quiet:
        for name, ok, detail in checks:
            print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, ("(" + detail + ")") if detail else ""))
        print("----")
        print("selftest: %d/%d passed" % (len(passed), len(checks)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
