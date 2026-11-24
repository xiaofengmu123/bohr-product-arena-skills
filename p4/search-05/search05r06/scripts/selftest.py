#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（对应评分维度，退出码 0 = 全部通过）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output

检查项：
  1. result.json 合法、含 case_id、status 合法
  2. 题目 output_contract 全部键回填（不改名、不删键）
  3. 每条候选有 record_id + provenance + raw_snapshot（逐字片段）
  4. coverage 列明每库 fields_missing 与单库记录数
  5. 有候选时 evidence_records 非空且含 locator + quote
  6. 中间表示 plan.json / record_model.json 存在且可解析
  7. 空输入 / 坏 JSON / 缺 case.json 不崩溃（只做结构性校验，不重跑主流程）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import core_impl as fs


def _load(path: Path) -> Tuple[Optional[Any], str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return json.loads(text), ""
    except FileNotFoundError:
        return None, "missing: %s" % path
    except json.JSONDecodeError as e:
        return None, "bad_json: %s: %s" % (path, e)


def _read_case_contract(input_dir: Path) -> Tuple[Optional[List[str]], Dict[str, Any]]:
    case_path = input_dir / "case.json"
    if not case_path.is_file():
        return None, {}
    obj, _ = _load(case_path)
    if not isinstance(obj, dict):
        return None, {}
    oc = obj.get("output_contract")
    if isinstance(oc, list):
        return [str(k) for k in oc], {}
    if isinstance(oc, dict):
        defaults = {}
        for k, spec in oc.items():
            if isinstance(spec, dict) and "default" in spec:
                defaults[str(k)] = spec["default"]
            elif isinstance(spec, str):
                defaults[str(k)] = {"string": "", "list": [], "dict": {}, "null": None}.get(spec.lower(), None)
        return [str(k) for k in oc.keys()], defaults
    return None, {}


def check(input_dir: Path, output_dir: Path) -> Tuple[List[str], List[str]]:
    passes: List[str] = []
    fails: List[str] = []

    contract_keys, defaults = _read_case_contract(input_dir)
    result_path = output_dir / "result.json"
    result, err = _load(result_path)
    if err:
        fails.append(err)
        return passes, fails

    if not isinstance(result, dict):
        fails.append("result.json 顶层不是对象")
        return passes, fails

    # 1. 基本字段
    if result.get("case_id"):
        passes.append("case_id present")
    else:
        fails.append("case_id missing or empty")
    if result.get("status") in fs.STATUSES:
        passes.append("status valid: %s" % result["status"])
    else:
        fails.append("status invalid: %r" % result.get("status"))

    # 2. 契约键回填
    keys = contract_keys if contract_keys is not None else fs.DEFAULT_CONTRACT
    missing_keys = [k for k in keys if k not in result]
    if missing_keys:
        fails.append("contract keys missing: %s" % ", ".join(missing_keys))
    else:
        passes.append("contract keys complete (%d)" % len(keys))

    # 3. 固定字段
    for k in ("sources", "uncertainty", "change_log", "errors", "degradations",
              "pending_human", "runtime"):
        if k in result:
            passes.append("%s present" % k)
        else:
            fails.append("%s missing" % k)

    # 4. 候选字段与溯源
    candidates = result.get("candidates") or []
    if not isinstance(candidates, list):
        fails.append("candidates not a list")
        candidates = []
    for i, c in enumerate(candidates):
        if not isinstance(c, dict):
            fails.append("candidate[%d] not object" % i)
            continue
        rid = c.get("record_id")
        if not rid:
            fails.append("candidate[%d] missing record_id" % i)
        prov = c.get("provenance")
        if not isinstance(prov, list) or not prov:
            fails.append("candidate[%s] missing provenance" % (rid or i))
        else:
            has_snapshot = any(isinstance(p, dict) and p.get("raw_snapshot") for p in prov)
            if not has_snapshot:
                fails.append("candidate[%s] provenance missing raw_snapshot" % (rid or i))

    # 5. 覆盖
    cov = result.get("coverage") or {}
    if isinstance(cov, dict) and cov.get("merged_record_count") is not None:
        passes.append("coverage present")
        per_db = cov.get("systematically_missing_fields") or {}
        if isinstance(per_db, dict):
            passes.append("per-db fields_missing present (%d db)" % len(per_db))
        else:
            fails.append("systematically_missing_fields not a dict")
    else:
        fails.append("coverage missing merged_record_count")

    # 6. 证据
    evidence = result.get("evidence_records") or []
    if candidates and not evidence:
        fails.append("evidence_records empty while candidates exist")
    elif evidence:
        ok_ev = all(isinstance(e, dict) and e.get("quote") for e in evidence)
        # record_origin 证据必须带 locator；query/source_conflict 用 quote/detail 承载定位
        bad_loc = [e for e in evidence if isinstance(e, dict)
                   and not e.get("locator")
                   and e.get("type") not in ("query", "source_conflict")]
        if ok_ev:
            passes.append("evidence_records have quote (%d)" % len(evidence))
        else:
            fails.append("some evidence_records missing quote")
        if not bad_loc:
            passes.append("evidence_records have locator/type")
        else:
            fails.append("%d evidence_records missing locator" % len(bad_loc))

    # 7. 中间表示
    for name in ("plan.json", "record_model.json"):
        obj, err = _load(output_dir / name)
        if err:
            fails.append(err)
        elif isinstance(obj, dict) and obj.get("schema_version"):
            passes.append("%s parseable" % name)
        else:
            fails.append("%s missing schema_version" % name)

    # 8. 空结果诚实性
    if not candidates and result.get("status") not in ("insufficient_evidence", "input_error"):
        fails.append("no candidates but status=%s (expected insufficient_evidence/input_error)" % result.get("status"))
    elif not candidates:
        passes.append("empty result honest status: %s" % result.get("status"))

    return passes, fails


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="交付前自检（退出码 0 = 全通过）")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)
    passes, fails = check(Path(args.input), Path(args.output))
    for p in passes:
        print("[PASS] %s" % p)
    for f in fails:
        print("[FAIL] %s" % f)
    print("selfcheck: %d pass, %d fail" % (len(passes), len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
