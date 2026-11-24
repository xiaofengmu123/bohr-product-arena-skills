#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S6 自检：校验 result.json 契约、字段完整性与主产物存在性。

用法：
  python3 -B scripts/validate.py --input /input --output /app/output

退出码：0 通过；1 有 FAIL（需修复后再提交）；2 无法读取 result.json。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_lib as ev

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
REQUIRED_EXTRA = ["case_id", "status", "sources", "uncertainty", "change_log"]


def load_json(path):
    p = Path(path)
    if not p.exists():
        return None, "missing"
    text, deg = ev.read_text(p)
    if text is None:
        return None, deg or "unreadable"
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"bad_json:{type(e).__name__}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 result.json 契约")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)

    fails, warns = [], []
    outdir = Path(args.output)

    result, rdeg = load_json(outdir / "result.json")
    if result is None:
        print(f"FAIL result.json: {rdeg}")
        return 2
    if not isinstance(result, dict):
        print("FAIL result.json: not a JSON object")
        return 1

    case, cdeg = load_json(Path(args.input) / "case.json")
    contract = case.get("output_contract") if isinstance(case, dict) else None
    case_id = case.get("case_id") if isinstance(case, dict) else None

    for k in REQUIRED_EXTRA:
        if k not in result:
            fails.append(f"missing required key: {k}")

    if case_id and result.get("case_id") != case_id:
        warns.append(f"case_id mismatch: result={result.get('case_id')!r} input={case_id!r}")

    if isinstance(contract, dict):
        for k in contract:
            if k not in result:
                fails.append(f"missing output_contract field: {k}")
            elif result[k] is None and result.get("status") not in ("insufficient_evidence", "input_error"):
                warns.append(f"output_contract field is null: {k}")

    if result.get("status") not in ALLOWED_STATUS:
        fails.append(f"invalid status: {result.get('status')!r}")

    for key in ("sources", "uncertainty", "change_log"):
        v = result.get(key)
        if v is not None and not isinstance(v, list):
            fails.append(f"{key} must be an array")

    arts = result.get("artifacts") or {}
    missing_artifacts = []
    if isinstance(arts, dict):
        for name, rel in arts.items():
            if isinstance(rel, str):
                p = (outdir / rel) if not os.path.isabs(rel) else Path(rel)
                if not p.exists():
                    missing_artifacts.append(rel)
    if missing_artifacts:
        warns.append(f"artifact files missing: {missing_artifacts}")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        for w in warns:
            print(" WARN:", w)
        return 1
    if warns:
        print("PASS with warnings")
        for w in warns:
            print(" -", w)
        return 0
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
