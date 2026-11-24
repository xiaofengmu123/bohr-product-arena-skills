#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize.py - result.json 契约校验 + 机器自检（产出 selfcheck.json）。

只读校验，不自动改写 Agent 的语义判断；errors 必须为 0，warning 逐条修复或如实说明。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

RELEVANCE_VALS = {"direct", "indirect", "irrelevant", "unknown"}
TIER_VALS = {"retain", "pending", "exclude"}
CONFIDENCE_VALS = {"high", "medium", "low"}
COMPLETENESS_VALS = {"full", "partial", "abstract_only", "missing_abstract", "none"}
STATUS_VALS = {"ok", "partial", "insufficient_evidence", "input_error"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_title(text):
    if not text:
        return ""
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text).lower()).strip()


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description="result.json 契约校验与自检")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    rpath = os.path.join(args.output, "result.json")
    npath = os.path.join(args.work, "case_normalized.json")

    checks = []
    errors, warnings = [], []

    def add(cid, label, passed, note="", level="error"):
        checks.append({"id": cid, "label": label, "pass": passed, "note": note,
                       "level": level})
        if not passed:
            (errors if level == "error" else warnings).append({"id": cid, "note": note})

    if not os.path.isfile(rpath):
        add("F0", "result.json 存在", False, "missing result.json", "error")
        sc = {"errors": 1, "warnings": 0, "checks": checks,
              "summary": "FATAL: result.json 缺失（discover.py 未运行或写盘失败）",
              "ts": now_iso()}
        spath = os.path.join(args.output, "selfcheck.json")
        with open(spath, "w", encoding="utf-8") as fh:
            json.dump(sc, fh, ensure_ascii=False, indent=2)
        print("finalize: errors=1 warnings=0 (missing result.json)")
        return 0

    result = load(rpath)

    # F1-F2 基础
    add("F1", "case_id 存在且非空", bool(result.get("case_id")), str(result.get("case_id")))
    add("F2", "status 合法枚举", result.get("status") in STATUS_VALS,
        str(result.get("status")))
    status = result.get("status")

    # 规范化候选覆盖
    expected_ids = []
    if os.path.isfile(npath):
        cn = load(npath)
        expected_ids = [c.get("record_id") for c in cn.get("candidates", [])]
        expected_norm = {c.get("record_id"): c for c in cn.get("candidates", [])}
    else:
        expected_norm = {}

    results = result.get("results") or []
    have_ids = [r.get("record_id") for r in results if isinstance(r, dict)]
    missing_ids = [i for i in expected_ids if i not in have_ids]
    extra_ids = [i for i in have_ids if i not in set(expected_ids)]

    add("F3", "results 非空或已解释为空", bool(results) or bool(result.get("omissions")),
        "results=%d omissions=%d" % (len(results), len(result.get("omissions") or [])))
    if missing_ids:
        add("F4", "覆盖全部候选（无遗漏）", False,
            "missing: %s" % ", ".join(missing_ids), "error")
    else:
        add("F4", "覆盖全部候选（无遗漏）", True, "covered=%d" % len(expected_ids))
    if extra_ids:
        add("F5", "无额外未知记录", False, "extra: %s" % ", ".join(extra_ids), "warning")
    else:
        add("F5", "无额外未知记录", True, "")

    # 逐条字段校验
    rank_ok = True
    order_ok = True
    prev = None
    meta_mismatch = []
    evidence_bad = []
    enum_bad = []
    completeness_bad = []
    uncertainty_bad = []
    for i, r in enumerate(results, 1):
        rid = r.get("record_id")
        if r.get("relevance") not in RELEVANCE_VALS:
            enum_bad.append("%s.relevance=%s" % (rid, r.get("relevance")))
        if r.get("tier") not in TIER_VALS:
            enum_bad.append("%s.tier=%s" % (rid, r.get("tier")))
        if r.get("confidence") not in CONFIDENCE_VALS:
            enum_bad.append("%s.confidence=%s" % (rid, r.get("confidence")))
        if r.get("evidence_completeness") not in COMPLETENESS_VALS:
            enum_bad.append("%s.evidence_completeness=%s" % (rid, r.get("evidence_completeness")))
        try:
            sc = float(r.get("priority_score"))
            if not (0.0 <= sc <= 1.0):
                enum_bad.append("%s.priority_score out of range" % rid)
        except (TypeError, ValueError):
            enum_bad.append("%s.priority_score not numeric" % rid)
        rk = r.get("rank")
        if rk is None:
            rank_ok = False
        elif prev is not None and rk != prev + 1:
            rank_ok = False
        prev = rk if rk is not None else prev

        # 元数据忠实性（与输入归一化对比）
        if rid in expected_norm:
            src = expected_norm[rid]
            for f in ("title", "year", "doi"):
                if f in src and src[f] is not None and f in r and r[f] is not None:
                    a = norm_title(str(src[f]))
                    b = norm_title(str(r[f]))
                    if a and b and a != b and a not in b and b not in a:
                        meta_mismatch.append("%s.%s" % (rid, f))

        # 证据：每条 reason 是否配非空 locator+quote
        ev = r.get("evidence") or []
        reasons = r.get("reasons") or []
        if reasons and not ev:
            evidence_bad.append("%s: reasons but no evidence" % rid)
        else:
            for e in ev:
                if not (e.get("locator") and e.get("quote")):
                    evidence_bad.append("%s: evidence missing locator/quote" % rid)
                    break

        # 证据完整度与缺失字段一致性
        comp = r.get("evidence_completeness")
        missing_fields = [f for f in ("abstract", "year", "doi", "authors")
                          if f not in r or r[f] is None or r[f] == []]
        if missing_fields and comp == "full":
            completeness_bad.append("%s: comp=full but missing %s" % (rid, missing_fields))
        if not missing_fields and comp in ("missing_abstract", "none"):
            completeness_bad.append("%s: comp=%s but fields present" % (rid, comp))

        # 不确定性：低置信或缺失字段应有逐条 uncertainty
        u = r.get("uncertainty") or []
        if (r.get("confidence") == "low" or missing_fields) and not u:
            uncertainty_bad.append("%s: low/missing but no per-record uncertainty" % rid)

    add("F6", "relevance/tier/confidence/completeness 枚举合法",
        not enum_bad, "; ".join(enum_bad[:5]), "error")
    add("F7", "priority_score ∈ [0,1] 且 rank 顺序连续",
        rank_ok and not any("score" in x or "numeric" in x for x in enum_bad),
        "rank_ok=%s" % rank_ok, "error")
    add("F8", "元数据与输入一致（不猜测）", not meta_mismatch,
        "; ".join(meta_mismatch[:5]), "error")
    add("F9", "每条决定有 locator+逐字 quote 证据", not evidence_bad,
        "; ".join(evidence_bad[:5]), "error")
    add("F10", "evidence_completeness 与缺失字段一致", not completeness_bad,
        "; ".join(completeness_bad[:5]), "warning")
    add("F11", "低置信/缺字段有逐条 uncertainty", not uncertainty_bad,
        "; ".join(uncertainty_bad[:5]), "warning")

    # 分层顺序与排除规则
    tiers = [r.get("tier") for r in results]
    torder = {"retain": 0, "pending": 1, "exclude": 2}
    non_monotonic = any(torder.get(tiers[i], 9) > torder.get(tiers[i+1], 9)
                        for i in range(len(tiers)-1))
    add("F12", "retain→pending→exclude 顺序稳定", not non_monotonic,
        "tiers: %s" % (",".join(tiers[:8])), "warning")
    wrongly_excluded = [r.get("record_id") for r in results
                        if r.get("tier") == "exclude"
                        and r.get("relevance") not in ("irrelevant",)]
    add("F13", "exclude 只放 irrelevant", not wrongly_excluded,
        "; ".join(wrongly_excluded[:5]), "error")

    # 降级与不确定性显式化（本版核心）
    limitations = result.get("limitations") or []
    global_unc = result.get("uncertainty") or []
    add("F14", "partial/insufficient_evidence 状态有 limitations",
        not (status in ("partial", "insufficient_evidence") and not limitations),
        "limitations=%d" % len(limitations), "error")
    add("F15", "uncertainty 显式化（全局或逐条至少其一）",
        bool(global_unc or any(r.get("uncertainty") for r in results)),
        "global=%d" % len(global_unc), "warning")
    add("F16", "errors/degradations 数组存在", isinstance(result.get("errors"), list)
        and isinstance(result.get("degradations"), list), "")

    # 记录与可复核性
    cl = result.get("change_log") or []
    add("F17", "change_log 非空且含 discover 骨架步骤", bool(cl)
        and any(c.get("step") == "discover" for c in cl), "entries=%d" % len(cl))
    add("F18", "sources 非空", bool(result.get("sources")), "")
    method = result.get("method") or {}
    add("F19", "method 声明打分口径与平局键", bool(method.get("tie_break")),
        str(method.get("tie_break", "")))
    add("F20", "pending_human 数组存在", isinstance(result.get("pending_human"), list), "")

    # 空候选的 insufficient_evidence 不因空 results/sources/uncertainty 判错
    if status in ("insufficient_evidence",) and not expected_ids:
        waived = {"F3", "F18", "F15"}
        for c in checks:
            if c["id"] in waived and not c["pass"]:
                c["pass"] = True
                c["note"] += " [waived: empty input]"
        errors = [e for e in errors if e["id"] not in waived]
        warnings = [w for w in warnings if w["id"] not in waived]

    summary = "errors=%d warnings=%d" % (len(errors), len(warnings))
    if status == "insufficient_evidence":
        summary += " (insufficient_evidence：诚实降级，F3/F4/F15/F18 允许空)"
    elif status == "input_error":
        summary += " (input_error：输入不可解析)"

    sc = {"errors": len(errors), "warnings": len(warnings),
          "checks": checks, "summary": summary, "ts": now_iso(),
          "result_path": rpath}
    spath = os.path.join(args.output, "selfcheck.json")
    with open(spath, "w", encoding="utf-8") as fh:
        json.dump(sc, fh, ensure_ascii=False, indent=2)

    print("finalize: %s" % summary)
    for e in errors:
        print("  [error] %s %s" % (e["id"], e["note"]))
    for w in warnings:
        print("  [warn]  %s %s" % (w["id"], w["note"]))
    print("  wrote: %s" % spath)
    return 0


if __name__ == "__main__":
    sys.exit(main())