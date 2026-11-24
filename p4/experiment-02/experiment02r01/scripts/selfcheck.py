#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 /app/output 的产物做独立自检，逐条对应 SKILL.md 第 7 节清单。

用法：
  python3 scripts/selfcheck.py --input /input --output /app/output --work /app/work
  python3 scripts/selfcheck.py --input /input --output /app/output --work /app/work --strict

退出码：默认 0（无论 FAIL 与否）；--strict 且存在 FAIL 时退出码 1。
结果另写 /app/output/selfcheck_report.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402
import assemble as asm  # noqa: E402


def add(rows, code, level, detail):
    rows.append({"check": code, "level": level, "detail": detail})


def main(argv=None):
    ap = argparse.ArgumentParser(description="产物自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    rows = []
    result_path = os.path.join(args.output, "result.json")
    result = None
    if not os.path.isfile(result_path):
        add(rows, "C01", "fail", f"result.json 不存在: {result_path}")
    else:
        try:
            with open(result_path, encoding="utf-8-sig") as fh:
                result = json.load(fh)
            add(rows, "C01", "pass", "result.json 存在且为合法 JSON")
        except (OSError, ValueError) as exc:
            add(rows, "C01", "fail", f"result.json 无法解析: {exc}")

    if result is None:
        result = {}
    case, _, _ = core.load_case(args.input)
    contract, _ = core.contract_fields(case)
    fields = list(contract.keys()) or list(core.DEFAULT_CONTRACT)

    if not result.get("case_id"):
        add(rows, "C02", "fail", "case_id 缺失或为空")
    else:
        add(rows, "C02", "pass", f"case_id={result.get('case_id')}")

    if result.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        add(rows, "C03", "fail", f"status 非法: {result.get('status')!r}")
    else:
        add(rows, "C03", "pass", f"status={result.get('status')}")

    missing = [f for f in fields if f not in result]
    if missing:
        add(rows, "C04", "fail", "输出契约字段缺失: " + ", ".join(missing))
    else:
        add(rows, "C04", "pass", f"输出契约字段 {len(fields)} 个全部回填")

    review = result.get("review") or {}
    dims_ok = True
    for d in core.DIMS:
        items = review.get(d) or []
        if not items:
            add(rows, "C05", "fail", f"维度 {d} 无审查项")
            dims_ok = False
        elif len(items) < 2:
            add(rows, "C05", "warn", f"维度 {d} 仅 {len(items)} 条（建议 >=2）")
    if dims_ok:
        add(rows, "C05", "pass", "七类约束均至少 1 条审查项")

    findings = [f for items in review.values() for f in items]
    ids = [f.get("finding_id") for f in findings]
    if len(ids) != len(set(ids)) or any(not i for i in ids):
        add(rows, "C06", "fail", "finding_id 存在空值或重复")
    else:
        add(rows, "C06", "pass", f"{len(ids)} 个 finding_id 唯一")

    bad_fields = []
    for f in findings:
        if f.get("dimension") not in core.DIMS:
            bad_fields.append(f"{f.get('finding_id')}: dimension")
        if f.get("verdict") not in core.VERDICTS:
            bad_fields.append(f"{f.get('finding_id')}: verdict")
        if not isinstance(f.get("claim"), str) or len(f["claim"]) < 10:
            bad_fields.append(f"{f.get('finding_id')}: claim")
    if bad_fields:
        add(rows, "C07", "fail", "finding 字段非法: " + "; ".join(bad_fields[:8]))
    else:
        add(rows, "C07", "pass", "finding 必填字段与枚举合法")

    unverified, bad_loc = [], []
    ev_count = 0
    for f in findings:
        evs = f.get("evidence") or []
        if not evs:
            add(rows, "C08", "fail", f"{f.get('finding_id')} 无 evidence")
            continue
        for e in evs:
            ev_count += 1
            loc = e.get("locator") or ""
            quote = e.get("quote") or ""
            res = core.resolve_locator(loc, args.input, args.work) if loc else \
                {"ok": False, "kind": "missing_locator", "text": "", "detail": "未提供 locator"}
            hit = bool(res["ok"]) and core.quote_len_ok(quote) and \
                core.norm_ws(quote) in core.norm_ws(res["text"])
            if not res["ok"]:
                bad_loc.append((f.get("finding_id"), loc, res.get("detail")))
            elif not hit:
                unverified.append((f.get("finding_id"), loc))
    if bad_loc:
        add(rows, "C08", "fail", f"{len(bad_loc)} 条 locator 无法解析: "
             + "; ".join(f"{a}@{b} ({c})" for a, b, c in bad_loc[:6]))
    if unverified:
        add(rows, "C08", "fail", f"{len(unverified)} 条 quote 未通过原文回查: "
             + "; ".join(f"{a}@{b}" for a, b in unverified[:6]))
    if not bad_loc and not unverified:
        add(rows, "C08", "pass", f"{ev_count} 条证据全部通过 locator+quote 原文回查")

    rule_bad = []
    for f in findings:
        if f["verdict"] == "conditional" and not f.get("conditions"):
            rule_bad.append(f"{f['finding_id']}: conditional 缺 conditions")
        if f["verdict"] == "blocked" and not (f.get("blockers") or f.get("conditions")):
            rule_bad.append(f"{f['finding_id']}: blocked 未写阻断原因")
        if f["verdict"] == "unconfirmed" and not (f.get("needs_expert")
                                                  or (f.get("action") or {}).get("owner")
                                                  or (f.get("action") or {}).get("ask")):
            rule_bad.append(f"{f['finding_id']}: unconfirmed 缺确认人/事项")
    if rule_bad:
        add(rows, "C09", "fail", "可执行性规则违例: " + "; ".join(rule_bad[:6]))
    else:
        add(rows, "C09", "pass", "conditional/blocked/unconfirmed 均有可执行补充")

    comp_bad = []
    for f in findings:
        comp = f.get("computation")
        if not comp:
            continue
        formula = comp.get("formula")
        operands = comp.get("operands") if isinstance(comp.get("operands"), dict) else {}
        declared = comp.get("result")
        if not formula:
            comp_bad.append(f"{f['finding_id']}: 缺 formula")
            continue
        names = {}
        for k, v in operands.items():
            if isinstance(v, bool):
                names[str(k)] = v
            elif isinstance(v, (int, float)):
                names[str(k)] = v
            else:
                try:
                    names[str(k)] = float(str(v).replace(",", ""))
                except ValueError:
                    names[str(k)] = v
        try:
            got = asm.safe_eval(str(formula), names)
        except Exception as exc:  # noqa: BLE001
            comp_bad.append(f"{f['finding_id']}: 求值失败 {exc}")
            continue
        ok = False
        if isinstance(declared, bool) and isinstance(got, bool):
            ok = declared == got
        elif isinstance(declared, (int, float)) and isinstance(got, (int, float)) \
                and not isinstance(declared, bool):
            ok = abs(float(declared) - float(got)) <= 1e-6 + 1e-9 * abs(float(got))
        if not ok:
            comp_bad.append(f"{f['finding_id']}: 声明 {declared!r} != 复算 {got!r}")
    if comp_bad:
        add(rows, "C10", "fail", "算术复算失败: " + "; ".join(comp_bad[:6]))
    else:
        add(rows, "C10", "pass", "所有 computation 复算一致")

    ledger = result.get("arithmetic_ledger") or []
    if any(not a.get("verified") for a in ledger):
        add(rows, "C11", "fail", "arithmetic_ledger 存在未通过复算的条目")
    else:
        add(rows, "C11", "pass", f"{len(ledger)} 条算术台账全部复算通过")

    plans = [p["version"] for p in
             (core.build_casepack(args.input, args.work).get("plans") or [])]
    under = []
    if plans:
        for v in plans:
            cnt = sum(1 for f in findings if v in (f.get("applies_to") or []))
            if cnt < 3:
                under.append(f"{v}:{cnt}")
    if under:
        add(rows, "C12", "warn", "版本覆盖偏少（建议每版 >=3 条）: " + ", ".join(under))
    else:
        add(rows, "C12", "pass", "各版本均有审查覆盖")

    emap_path = os.path.join(args.output, "evidence_map.json")
    emap = None
    if os.path.isfile(emap_path):
        try:
            with open(emap_path, encoding="utf-8-sig") as fh:
                emap = json.load(fh)
        except (OSError, ValueError):
            emap = None
    if emap is None:
        add(rows, "C13", "fail", "evidence_map.json 缺失或不可解析")
    else:
        items = emap.get("items") or []
        map_ok = emap.get("quote_verification", {}).get("failed", -1) == 0
        locs = {i.get("locator") for i in items}
        res_locs = {e.get("locator") for f in findings for e in (f.get("evidence") or [])}
        if res_locs - locs:
            add(rows, "C13", "fail", "result.json 中部分 locator 未进入 evidence_map")
        elif not map_ok:
            add(rows, "C13", "fail", "evidence_map 存在未通过回查的条目")
        else:
            add(rows, "C13", "pass", f"evidence_map 与 result.json 一致（{len(items)} 条，"
                                     f"{len(res_locs)} 个 locator）")

    report_path = os.path.join(args.output, "review_report.md")
    if not os.path.isfile(report_path):
        add(rows, "C14", "fail", "review_report.md 缺失")
    else:
        text = core.read_text(report_path)[0] or ""
        miss_ids = [f.get("finding_id") for f in findings if f.get("finding_id") not in text]
        miss_loc = [e.get("locator") for f in findings for e in (f.get("evidence") or [])
                    if (e.get("locator") or "") not in text]
        if miss_ids or miss_loc:
            add(rows, "C14", "fail", f"报告缺 {len(miss_ids)} 个 finding_id / "
                                     f"{len(miss_loc)} 个 locator")
        else:
            add(rows, "C14", "pass", "报告包含全部 finding_id 与 locator")

    sources = result.get("sources") or []
    bad_src = [s for s in sources if not core.resolve_locator(s.get("locator", ""),
                                                              args.input, args.work)["ok"]]
    if not sources:
        add(rows, "C15", "fail", "sources 为空")
    elif bad_src:
        add(rows, "C15", "fail", f"{len(bad_src)} 个 sources locator 无法解析")
    else:
        add(rows, "C15", "pass", f"{len(sources)} 个来源全部可解析")

    for key in ("uncertainty", "pending_human", "limitations", "change_log"):
        if key not in result or not isinstance(result[key], list):
            add(rows, "C16", "fail", f"缺 {key}（应为数组）")
    if unverified and not result.get("uncertainty"):
        add(rows, "C16", "fail", "存在未核对 quote 但 uncertainty 为空")
    if not unverified and all(key in result for key in ("uncertainty", "pending_human", "limitations")):
        add(rows, "C16", "pass", "uncertainty/pending_human/limitations/change_log 齐备")

    bad_ph = [f.get("finding_id") for f in findings
              if any(t in (f.get("claim") or "")
                     for t in ("TODO", "TBD", "待填", "占位", "xxx", "XXX", "<placeholder", "<<占位"))]
    if bad_ph:
        add(rows, "C17", "fail", "claim 含占位符: " + ", ".join(bad_ph[:6]))
    else:
        add(rows, "C17", "pass", "无占位符残留")

    fails = [r for r in rows if r["level"] == "fail"]
    if result.get("status") == "ok" and fails:
        add(rows, "C18", "fail", f"status=ok 但仍有 {len(fails)} 个 FAIL，status 不诚实")
    else:
        add(rows, "C18", "pass", "status 与自检结果一致")

    if len(plans) >= 2 and not (result.get("plan_comparison")):
        add(rows, "C19", "fail", "多版本方案但 plan_comparison 为空")
    elif len(plans) >= 2:
        add(rows, "C19", "pass", f"plan_comparison 覆盖 {len(result.get('plan_comparison') or [])} 版")
    else:
        add(rows, "C19", "pass", "单版本/无版本，无需比较")

    artifacts = result.get("artifacts") or []
    miss_art = [a.get("path") for a in artifacts if not os.path.isfile(a.get("path", ""))]
    if miss_art:
        add(rows, "C20", "warn", "部分 artifacts 不在磁盘: " + ", ".join(miss_art[:5]))
    else:
        add(rows, "C20", "pass", f"{len(artifacts)} 个 artifacts 均存在")

    casepack = core.build_casepack(args.input, args.work)
    addressed = {(f.get("dimension"), f.get("verdict")) for f in findings}
    silent_ok = True
    for flag in casepack.get("silence_flags") or []:
        d = flag["dimension"]
        if not any(dd == d and v in ("missing", "unconfirmed", "conditional", "blocked", "conflict")
                   for dd, v in addressed):
            add(rows, "C21", "warn", f"{d} 关键词扫描未见且无 missing/unconfirmed 说明，疑似把未写当满足")
            silent_ok = False
    if silent_ok:
        add(rows, "C21", "pass", "未写=满足的疑似项已处理")

    pass_n = sum(1 for r in rows if r["level"] == "pass")
    warn_n = sum(1 for r in rows if r["level"] == "warn")
    fail_n = len(fails)
    report = {"summary": {"pass": pass_n, "warn": warn_n, "fail": fail_n},
              "checks": rows,
              "advice": "修正全部 FAIL 后重跑 assemble.py 与 selfcheck.py；warn 可接受但需在 uncertainty 说明理由"}
    core.write_json(os.path.join(args.output, "selfcheck_report.json"), report)
    print(f"[selfcheck] PASS {pass_n} / WARN {warn_n} / FAIL {fail_n}")
    for r in rows:
        if r["level"] in ("fail", "warn"):
            print(f"  [{r['level'].upper():4}] {r['check']}: {r['detail']}")
    if args.strict and fail_n:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
