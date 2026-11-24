#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验文献综述 Skill 的最终产物是否满足输出契约。

只做结构校验，不做内容质量打分，绝不臆造评测分数：
  - result.json 存在且为合法 JSON；
  - case_id 存在，status 属于枚举且诚实；
  - output_contract 全部字段回填（键名不缺失、不改名）；
  - sources / uncertainty / change_log 存在；
  - status=ok 时四个主产物齐全；
  - evidence_table.jsonl 每行含可回溯字段（locator + quote）。

用法：
  python3 -B scripts/validate_result.py --output /app/output --case /input/case.json
退出码：0=通过；1=存在问题（打印 ISSUE 列表）。
"""

import argparse
import json
import os
import sys

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
MAIN_ARTIFACTS = [
    "review.md",
    "screening_log.json",
    "evidence_table.jsonl",
    "themes.md",
    "conflicts_and_gaps.md",
]
HONEST_FIELDS = ["status", "sources", "uncertainty", "change_log"]
EVIDENCE_REQUIRED_KEYS = {"evidence_id", "source_id", "locator", "quote"}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="校验 result.json 输出契约")
    parser.add_argument("--output", default="/app/output", help="产物目录")
    parser.add_argument("--case", default="/input/case.json", help="case 输入")
    parser.add_argument("--work", default="/app/work", help="中间产物目录")
    args = parser.parse_args(argv)

    issues = []
    result_path = os.path.join(args.output, "result.json")

    result, err = load_json(result_path)
    if result is None:
        print("ISSUE: result.json 缺失或不可解析: " + (err or "不存在"))
        return 1
    if not isinstance(result, dict):
        print("ISSUE: result.json 顶层不是对象")
        return 1

    # case_id
    if not result.get("case_id"):
        issues.append("缺少 case_id")

    # status 枚举
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        issues.append("status 不在枚举内: %r" % status)
    elif status == "ok":
        for name in MAIN_ARTIFACTS:
            if not os.path.isfile(os.path.join(args.output, name)):
                issues.append("status=ok 但缺少主产物: " + name)

    # 诚实字段
    for name in HONEST_FIELDS:
        if name not in result:
            issues.append("缺少字段: " + name)
    if not isinstance(result.get("change_log"), list):
        issues.append("change_log 应为列表")

    # output_contract 字段回填
    contract_path = os.path.join(args.work, "contract.json")
    if os.path.isfile(contract_path):
        contract, _err = load_json(contract_path)
        if contract and isinstance(contract, dict):
            for field in contract.get("fields", []):
                fname = field.get("name")
                if fname is not None and fname not in result:
                    issues.append("契约字段缺失: " + fname)
    else:
        # 兜底：至少检查常见字段存在。
        for fname in ("research_question", "screening_log",
                      "evidence_table", "themes", "references"):
            if fname not in result:
                issues.append("契约字段缺失(兜底检查): " + fname)

    # 证据行可回溯
    et_path = os.path.join(args.output, "evidence_table.jsonl")
    if os.path.isfile(et_path):
        try:
            with open(et_path, "r", encoding="utf-8-sig") as fh:
                for i, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception as exc:  # noqa: BLE001
                        issues.append("evidence_table.jsonl 第 %d 行非 JSON: %s" % (i, exc))
                        continue
                    if not isinstance(row, dict):
                        issues.append("evidence_table.jsonl 第 %d 行不是对象" % i)
                        continue
                    for key in EVIDENCE_REQUIRED_KEYS:
                        if not row.get(key):
                            issues.append(
                                "evidence_table.jsonl 第 %d 行缺少 %s" % (i, key))
        except Exception as exc:  # noqa: BLE001
            issues.append("读取 evidence_table.jsonl 失败: " + str(exc))

    # 防编造：确认 result.json 不含伪造评测分数键。
    suspicious = []
    if isinstance(result, dict):
        for key in ("score", "benchmark_score", "rubric_score", "grade"):
            if key in result:
                suspicious.append(key)
    if suspicious:
        issues.append("发现疑似评测分数键（不允许编造）: " + ",".join(suspicious))

    if issues:
        for issue in issues:
            print("ISSUE: " + issue)
        print("VALIDATE: FAIL (%d 个问题)" % len(issues))
        return 1
    print("VALIDATE: OK status=%s case_id=%s" % (status, result.get("case_id")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
