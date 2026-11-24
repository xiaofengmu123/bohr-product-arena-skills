#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_result.py — 校验 /app/output/result.json 契约与产物文件存在性。

用法:
    python scripts/check_result.py <result.json> [--output-dir <dir>]

退出码: 0=通过, 1=存在阻断错误, 2=仅警告。
产物缺失、status 非法、plan 关键字段缺失为阻断；来源/不确定/变更记录为空为警告。
"""

import argparse
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
REQUIRED_PLAN_KEYS = ("parameters", "response_metrics", "search_strategy",
                      "cost_budget")


def load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:
        return None, str(exc)


def check(path, output_dir):
    errors, warnings = [], []

    def err(m):
        errors.append(m)

    def warn(m):
        warnings.append(m)

    obj, load_err = load(path)
    if obj is None:
        return ["无法读取 result.json: %s" % load_err], warnings
    if not isinstance(obj, dict):
        return ["result.json 不是 JSON 对象"], warnings

    if not (obj.get("case_id") or "").strip():
        warn("case_id 为空")
    status = obj.get("status")
    if status not in STATUS_ENUM:
        err("status=%r 不在允许枚举" % status)

    plan = obj.get("plan")
    if not isinstance(plan, dict):
        err("缺少 plan 对象")
    else:
        for key in REQUIRED_PLAN_KEYS:
            if key not in plan:
                err("plan 缺少关键字段: %s" % key)

    artifacts = obj.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        warn("artifacts 为空")
    else:
        for a in artifacts:
            if not isinstance(a, dict):
                err("artifact 不是对象")
                continue
            rel = a.get("path")
            if not rel:
                err("artifact 缺 path")
                continue
            full = a.get("absolute_path") or (
                os.path.join(output_dir, rel) if output_dir else rel)
            if not os.path.isfile(full):
                err("artifact 文件不存在: %s" % full)

    for meta in ("sources", "uncertainty", "limitations", "pending_human",
                 "change_log", "errors", "degradations", "runtime"):
        if meta not in obj:
            warn("缺少字段: %s" % meta)

    if not obj.get("sources"):
        warn("sources 为空（关键结论应可溯源）")
    if not obj.get("change_log"):
        warn("change_log 为空（应记录骨架落盘等变更）")

    return errors, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", help="result.json 路径")
    parser.add_argument("--output-dir", help="产物目录（默认取 result 所在目录）")
    args = parser.parse_args(argv)
    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.result))
    errors, warnings = check(args.result, output_dir)
    report = {"result": args.result, "errors": errors, "warnings": warnings,
              "exit_code": 1 if errors else (2 if warnings else 0)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
