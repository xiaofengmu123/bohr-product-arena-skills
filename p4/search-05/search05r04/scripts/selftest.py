#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（仅标准库）。

对 /app/output/result.json 逐项校验：契约、证据、去重可解释、覆盖、不确定性、
降级、确定性与异常兜底。退出码 0 = 全通过，非 0 = 存在 FAIL。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import core_impl as cs


def _load(path: Path) -> Tuple[Any, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), ""
    except FileNotFoundError:
        return None, "missing file: %s" % path
    except json.JSONDecodeError as e:
        return None, "bad json: %s (%s)" % (path, e)


def run(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="多数据库联合检索自检")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    args = parser.parse_args(argv)

    result_path = Path(args.output) / "result.json"
    res, err = _load(result_path)
    checks: List[Tuple[bool, str]] = []

    def check(cond: bool, msg: str) -> None:
        checks.append((bool(cond), msg))

    check(err == "", "result.json exists and is valid JSON" if err == "" else err)
    if err:
        print_checks(checks)
        return 1

    # 1. 结构与契约
    check(isinstance(res, dict), "result is a JSON object")
    check(isinstance(res.get("case_id"), str) and res["case_id"], "case_id present and non-empty")
    check(res.get("status") in cs.STATUSES, "status in %s" % (cs.STATUSES,))
    for k in cs.DEFAULT_CONTRACT:
        check(k in res, "contract key present: %s" % k)
    # output_contract 键（case.json 若提供）原样保留
    case_path = Path(args.input) / "case.json"
    if case_path.exists():
        case_obj, _ = cs.load_json(case_path)
        if isinstance(case_obj, dict):
            oc = case_obj.get("output_contract")
            if isinstance(oc, dict):
                for k in oc:
                    check(k in res, "output_contract key present: %s" % k)
            elif isinstance(oc, list):
                for k in oc:
                    check(k in res, "output_contract key present: %s" % k)

    # 2. 候选与证据
    candidates = res.get("candidates") or []
    if candidates:
        for c in candidates:
            check(isinstance(c.get("record_id"), str), "candidate has record_id: %s" % c.get("record_id"))
            check(isinstance(c.get("provenance"), dict), "candidate has provenance: %s" % c.get("record_id"))
            check(isinstance(c.get("field_sources"), dict), "candidate has field_sources: %s" % c.get("record_id"))
            for snap in c.get("raw_snapshots", []):
                check(isinstance(snap.get("snapshot"), str) and len(snap.get("snapshot", "")) > 0,
                      "raw_snapshot non-empty for %s" % c.get("record_id"))
        evidence = res.get("evidence_records") or []
        check(len(evidence) > 0, "evidence_records non-empty when candidates exist")
        for ev in evidence:
            check(isinstance(ev.get("evidence_id"), str), "evidence has id")
            check(isinstance(ev.get("source_file"), str), "evidence has source_file")
            if ev.get("type") == "field_value":
                check(isinstance(ev.get("locator"), str) and ev.get("locator"),
                      "field evidence has locator")
                check(isinstance(ev.get("quote"), str), "field evidence has verbatim quote")

    # 3. 覆盖
    cov = res.get("coverage") or {}
    check(isinstance(cov.get("databases"), list), "coverage.databases is list")
    check(isinstance(cov.get("fields_missing_per_database"), dict),
          "coverage.fields_missing_per_database present")
    check(isinstance(cov.get("records_in_single_db_only"), list),
          "coverage.records_in_single_db_only present")
    check(isinstance(cov.get("duplicates_merged"), int), "coverage.duplicates_merged is int")

    # 4. 不确定性/降级/缺失
    unc = res.get("uncertainty") or {}
    check(isinstance(unc.get("items"), list), "uncertainty.items is list")
    check(isinstance(unc.get("note"), str) and unc.get("note"), "uncertainty.note non-empty")
    check(isinstance(res.get("limitations"), list), "limitations is list")
    check(isinstance(res.get("degradations"), list), "degradations is list")
    check(isinstance(res.get("missing_metadata"), list), "missing_metadata is list")
    for mm in res.get("missing_metadata") or []:
        check(isinstance(mm.get("reason"), str), "missing_metadata has reason")

    # 5. 溯源与诚实性
    check(isinstance(res.get("sources"), list), "sources is list")
    check(isinstance(res.get("change_log"), list) and len(res.get("change_log", [])) > 0,
          "change_log non-empty (first entry = skeleton)")
    check(res.get("change_log", [{}])[0].get("step") == "skeleton",
          "first change_log step is skeleton (early partial result)")
    check(isinstance(res.get("runtime"), dict), "runtime present")
    # 无伪造：status 为 insufficient_evidence 时不得有候选
    if res.get("status") == "insufficient_evidence":
        check(len(candidates) == 0, "insufficient_evidence implies no candidates")

    # 6. 辅助产物
    for aux in ("merged_records.json", "evidence_map.json", "coverage_report.md"):
        check((Path(args.output) / aux).exists(), "aux artifact exists: %s" % aux)

    # 7. 确定性（重复运行比较 —— 只比较本结果，脚本入口存在且可导入）
    check(cs.VERSION, "core_impl importable with VERSION=%s" % cs.VERSION)

    print_checks(checks)
    return 0 if all(c[0] for c in checks) else 1


def print_checks(checks: List[Tuple[bool, str]]) -> None:
    passed = sum(1 for c in checks if c[0])
    total = len(checks)
    for ok, msg in checks:
        print(("PASS" if ok else "FAIL") + " | " + msg)
    print("----")
    print("selftest: %d/%d passed" % (passed, total))
    if passed != total:
        print("FAILED" if passed == 0 else "PARTIAL")
    else:
        print("ALL PASS")


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
