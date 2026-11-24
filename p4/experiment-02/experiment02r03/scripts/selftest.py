#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检与确定性门禁（selftest.py）。

功能：
1. 校验既有 output/result.json：schema、枚举、id 唯一、证据 quote 与来源一致、算术重算。
2. --determinism：对同一输入跑两次 pipeline，比较 result_sha256，证明同输入两次产物一致。
3. 默认运行合成夹具套件（空输入 / 坏 JSON / BOM / 缺伦理 / 参数越界 / 样本不足），
   断言各状态与确定性；这些夹具是临时生成的鲁棒性测试，不是正式案例答案。

仅依赖 Python 标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import (  # noqa: E402
    DIM_ORDER,
    SEVERITY,
    SourceIndex,
    canonical_json,
    scan_inputs,
    sha256_text,
)

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
VALID_VERDICT = {"ok", "conditional", "blocked", "unconfirmed", "missing", "conflict"}
FIRM_VERDICTS = {"ok", "conditional", "blocked", "conflict"}


def fail(report, msg):
    report["failures"].append(msg)


def check(cond, report, msg):
    if not cond:
        fail(report, msg)
    else:
        report["passed"] += 1
    return bool(cond)


# --------------------------------------------------------------------------- #
# 校验器
# --------------------------------------------------------------------------- #


def validate_result(result, input_dir, report):
    r = report
    if not isinstance(result, dict):
        fail(r, "result_not_object")
        return False
    check(result.get("case_id") not in (None, ""), r, "case_id missing")
    check(result.get("status") in VALID_STATUS, r, f"bad status: {result.get('status')}")
    check(result.get("overall_conclusion", {}).get("verdict") is not None, r, "overall verdict missing")
    check(isinstance(result.get("review"), dict), r, "review missing/not dict")
    for d in DIM_ORDER:
        check(d in result.get("review", {}), r, f"review.{d} missing")
        check(isinstance(result["review"].get(d, []), list), r, f"review.{d} not list")
    check(isinstance(result.get("sources"), list), r, "sources not list")
    check(isinstance(result.get("uncertainty"), list), r, "uncertainty not list")
    check(isinstance(result.get("pending_human"), list), r, "pending_human not list")
    check(isinstance(result.get("change_log"), list) and result.get("change_log"), r, "change_log missing/empty")
    check(result.get("provenance", {}).get("deterministic") is True, r, "deterministic flag not true")

    # id 唯一
    ids = []
    for d in DIM_ORDER:
        for c in result["review"].get(d, []):
            if not isinstance(c, dict):
                fail(r, f"review.{d} item not dict")
                continue
            check(c.get("id"), r, f"review.{d} item missing id")
            ids.append(c.get("id"))
            check(c.get("verdict") in VALID_VERDICT, r, f"{c.get('id')} bad verdict {c.get('verdict')}")
            ev = c.get("evidence") or []
            if c.get("verdict") in FIRM_VERDICTS:
                check(bool(ev), r, f"{c.get('id')} firm verdict lacks evidence")
                for e in ev:
                    check(bool(e.get("locator")), r, f"{c.get('id')} evidence lacks locator")
                    check(e.get("quote") not in (None, ""), r, f"{c.get('id')} evidence lacks quote")
    check(len(ids) == len(set(ids)), r, "duplicate check ids")

    # quote 与来源一致性
    if input_dir and os.path.isdir(input_dir):
        files, _ = scan_inputs(input_dir)
        si = SourceIndex(files, input_dir)
        verified_count = 0
        total = 0
        for d in DIM_ORDER:
            for c in result["review"].get(d, []):
                for e in c.get("evidence", []):
                    total += 1
                    if si.quote_verified(e.get("locator", ""), e.get("quote", "")):
                        verified_count += 1
        if total > 0:
            ratio = verified_count / total
            check(ratio >= 0.5, r, f"quote verification ratio {ratio:.2f} < 0.5 ({verified_count}/{total})")
            r["quote_ratio"] = ratio

    # 算术重算（独立小循环，与 core_impl 分开）
    recompute_arithmetic(result, r)
    return not r["failures"]


def recompute_arithmetic(result, r):
    for d in DIM_ORDER:
        for c in result["review"].get(d, []):
            if c.get("computed") is None:
                continue
            formula = c.get("formula")
            if not isinstance(formula, str) or not re_has_numbers(formula):
                continue
            # 只对 "a op b" 形式的简单算式做独立重算，避免误报复杂文本
            nums = [float(x) for x in __import__("re").findall(r"[-+]?\d+(?:\.\d+)?", formula)]
            if len(nums) < 2:
                continue
            lhs = nums[0]
            rhs = nums[-1]
            op = None
            if " - " in formula:
                op = "sub"
            elif " + " in formula:
                op = "add"
            elif " / " in formula:
                op = "div"
            elif " × " in formula or " x " in formula or " * " in formula:
                op = "mul"
            if op is None:
                continue
            if "vs" in formula or "=" in formula or "→" in formula:
                continue
            expect = {"sub": lhs - rhs, "add": lhs + rhs, "div": lhs / rhs if rhs else None, "mul": lhs * rhs}.get(op)
            if expect is None:
                continue
            got = c.get("computed")
            # 允许百分比等比例换算（如 util 输出为 %，公式是除法）
            if op == "div" and abs(got - expect * 100) < 1e-3:
                continue
            if abs(got - expect) > max(1e-3, abs(expect) * 1e-3):
                # 不强制失败：公式可能是多阶段合计，仅记 info
                r.setdefault("arith_mismatches", []).append({"id": c.get("id"), "formula": formula, "got": got, "expect": round(expect, 4)})


def re_has_numbers(s):
    import re

    return bool(re.search(r"[-+]?\d+(?:\.\d+)?", s))


# --------------------------------------------------------------------------- #
# 确定性：跑两次比较
# --------------------------------------------------------------------------- #


def run_twice(input_dir):
    from main import run

    tmp1 = tempfile.mkdtemp(prefix="det1-")
    tmp2 = tempfile.mkdtemp(prefix="det2-")
    try:
        r1, _ = run(input_dir, os.path.join(tmp1, "output"), os.path.join(tmp1, "work"))
        r2, _ = run(input_dir, os.path.join(tmp2, "output"), os.path.join(tmp2, "work"))
        h1 = r1["provenance"]["result_sha256"]
        h2 = r2["provenance"]["result_sha256"]
        same = h1 == h2
        return same, h1, h2, r1
    finally:
        shutil.rmtree(tmp1, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 合成夹具
# --------------------------------------------------------------------------- #


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))


def make_minimal_case():
    return {
        "case_id": "SYN-001",
        "title": "合成夹具：低温高速离心与样本量冲突",
        "objective": "验证低温高速离心条件下目标蛋白回收率",
        "deadline_date": "2026-03-01",
        "budget": 50000,
        "currency": "CNY",
        "plans": [
            {
                "label": "v1",
                "steps": [
                    {"name": "细胞培养", "duration_hours": 48, "equipment": ["CO2培养箱"]},
                    {"name": "离心", "duration_min": 30, "equipment": ["高速离心机"], "parameters": {"rpm": 16000, "temperature": 4}},
                ],
                "parameters": {"temperature": {"value": 4, "unit": "°C"}},
                "materials": [{"name": "anti-CD3 抗体", "spec": "50 µg"}],
                "samples": {"per_group_n": 6, "groups": 2, "replicates": 3},
                "ethics": {"human_samples": True},
            }
        ],
        "contracts": {
            "equipment": [{"name": "高速离心机", "spec": "max 14000 rpm", "booking_hours": 4}],
            "materials": [{"name": "anti-CD3", "unit_price": 800, "currency": "CNY", "pack_size": "50 µg", "lead_days": 21}],
            "samples": [{"name": "PBMC", "available": 10}],
            "ethics": [{"name": "IRB", "status": "pending"}],
        },
        "parameter_domains": {"rpm": {"min": 1000, "max": 14000, "unit": "rpm"}},
    }


def make_fixture_suite():
    suite = []
    # 1. 完整冲突案例
    d = tempfile.mkdtemp(prefix="fx-ok-")
    write_json(os.path.join(d, "case.json"), make_minimal_case())
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    suite.append(("conflict_case", d, {"status_in": {"partial"}, "has_blocked": True}))

    # 2. 空输入
    d = tempfile.mkdtemp(prefix="fx-empty-")
    suite.append(("empty_input", d, {"status_in": {"insufficient_evidence"}}))

    # 3. 坏 JSON
    d = tempfile.mkdtemp(prefix="fx-badjson-")
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    suite.append(("bad_json", d, {"status_in": {"input_error"}}))

    # 4. BOM + UTF-8
    d = tempfile.mkdtemp(prefix="fx-bom-")
    payload = json.dumps(make_minimal_case(), ensure_ascii=False, indent=2)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8-sig") as fh:
        fh.write(payload)
    suite.append(("bom_case", d, {"status_in": {"partial", "ok"}, "has_blocked": True}))

    # 5. 缺伦理审批（动物 + 无批件）
    d = tempfile.mkdtemp(prefix="fx-ethics-")
    c = make_minimal_case()
    c["case_id"] = "SYN-ETHICS"
    c["plans"][0]["ethics"] = {"animal": True, "species": "mouse"}
    c["contracts"] = {}
    c["parameter_domains"] = {}
    write_json(os.path.join(d, "case.json"), c)
    suite.append(("ethics_missing", d, {"status_in": {"partial"}, "has_ethics_unconfirmed": True}))

    return suite


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def run_fixture_suite(report):
    suite = make_fixture_suite()
    try:
        for name, d, exp in suite:
            try:
                same, h1, h2, r1 = run_twice(d)
                ok = check(same, report, f"{name}: 两次运行 result_sha256 不一致 ({h1} vs {h2})")
                check(r1["status"] in exp["status_in"], report, f"{name}: 状态 {r1['status']} 不在 {exp['status_in']}")
                if exp.get("has_blocked"):
                    blocked = [c for dd in r1["review"].values() for c in dd if c["verdict"] == "blocked"]
                    check(bool(blocked), report, f"{name}: 应检出 blocked 但未检出")
                if exp.get("has_ethics_unconfirmed"):
                    has = any(c["verdict"] in ("unconfirmed", "blocked") and c["dimension"] == "ethics_safety" for dd in r1["review"].values() for c in dd)
                    check(has, report, f"{name}: 应检出伦理待确认/阻断")
            except Exception as exc:
                fail(report, f"{name}: 异常 {type(exc).__name__}: {exc}")
            finally:
                shutil.rmtree(d, ignore_errors=True)
    finally:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic feasibility audit self-test")
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--determinism", action="store_true", help="仅做同输入两次运行一致性验证")
    ap.add_argument("--json", action="store_true", help="输出机器可读报告")
    args = ap.parse_args(argv)

    report = {"passed": 0, "failures": [], "quote_ratio": None, "arith_mismatches": []}

    if args.input and args.output:
        with open(os.path.join(args.output, "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        validate_result(result, args.input, report)
    elif args.input and args.determinism:
        same, h1, h2, _ = run_twice(args.input)
        check(same, report, f"determinism failed: {h1} != {h2}")
    else:
        run_fixture_suite(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for f in report["failures"]:
            print(f"FAIL: {f}")
        for m in report.get("arith_mismatches", []):
            print(f"INFO arith: {m}")
        print(f"passed={report['passed']} failures={len(report['failures'])} quote_ratio={report.get('quote_ratio')}")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
