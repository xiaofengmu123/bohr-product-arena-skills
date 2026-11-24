#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — 校验并自愈 result.json。

纯标准库。检查 result.json 是否合法、合同字段与标准字段是否齐全、
佐证文件是否可用；缺字段只补齐（null/[]/{}），不覆盖已有值，并写
`validation` 报告。result.json 缺失或坏 JSON 时降级写最小合法结果。

用法：
    python3 -B scripts/validate.py \
        [--work /app/work] [--output /app/output]
"""

import argparse
import json
import os
import sys

DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

STANDARD_FIELDS = {
    "status": "partial",
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "artifacts": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "runtime": {},
}

SOURCE_REQUIRED = {"key", "locator", "quote", "kind"}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, "invalid_json: %s" % exc


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def expand_contract_value(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: expand_contract_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return value  # 标量默认值/约束，原样保留


def contract_top_keys(contract):
    """与 bootstrap 一致的合同字段顶层键。"""
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(item.keys())
    return keys


def minimal_result(reason):
    result = {"case_id": None, "status": "input_error"}
    for key, value in STANDARD_FIELDS.items():
        if key not in result:
            result[key] = value
    result["errors"] = ["validate_fallback: %s" % reason]
    result["validation"] = {"checked": False, "fallback": True,
                            "reason": reason}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="校验并自愈 result.json")
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")

    result, load_err = load_json(result_path)
    warnings = []
    fixed = []

    if result is None or not isinstance(result, dict):
        result = minimal_result(load_err or "unknown")
        write_json(result_path, result)
        print(json.dumps({"ok": False, "fallback": True,
                          "reason": load_err}, ensure_ascii=False))
        return 0

    # 读取合同字段（尽力；缺失不影响校验）
    contract = None
    normalized_path = os.path.join(args.work, "case_normalized.json")
    normalized, _ = load_json(normalized_path)
    if isinstance(normalized, dict):
        contract = normalized.get("contract")

    # 1) 补齐合同字段
    for key in contract_top_keys(contract):
        if key not in result:
            if isinstance(contract, dict):
                result[key] = expand_contract_value(contract[key])
            else:
                result[key] = None
            fixed.append("contract:%s" % key)

    # 2) 补齐标准字段；合同字段同名时合同优先（不覆盖已有键）
    for key, default in STANDARD_FIELDS.items():
        if key not in result:
            result[key] = default
            fixed.append("standard:%s" % key)

    # 3) status 合法性
    if result.get("status") not in ALLOWED_STATUS:
        warnings.append("invalid_status:%s -> partial"
                        % result.get("status"))
        result["status"] = "partial"

    # 4) 佐证文件与结构检查（只报告，不修改正文）
    manuscript_path = os.path.join(args.output, "manuscript.md")
    manuscript_exists = os.path.isfile(manuscript_path)
    manuscript_nonempty = False
    if manuscript_exists:
        with open(manuscript_path, "r", encoding="utf-8-sig",
                  errors="replace") as fh:
            manuscript_nonempty = bool(fh.read().strip())
    if not manuscript_nonempty:
        warnings.append("manuscript.md 缺失或为空")

    evidence_path = os.path.join(args.output, "evidence_map.json")
    evidence_entries = None
    evidence_ok = True
    if os.path.isfile(evidence_path):
        evidence, ev_err = load_json(evidence_path)
        if ev_err:
            evidence_ok = False
            warnings.append("evidence_map.json 解析失败: %s" % ev_err)
        elif isinstance(evidence, dict):
            evidence_entries = len(evidence)
            for sid, item in evidence.items():
                if not isinstance(item, dict) or \
                   not {"locator", "quote"} <= set(item):
                    evidence_ok = False
                    warnings.append("evidence_map 条目缺 locator/quote: %s"
                                    % sid)
        else:
            evidence_ok = False
            warnings.append("evidence_map.json 应为对象")
    else:
        warnings.append("evidence_map.json 缺失")

    # 5) sources 形状检查
    bad_sources = []
    for src in result.get("sources", []):
        if not isinstance(src, dict) or not SOURCE_REQUIRED <= set(src):
            bad_sources.append(src)
    if bad_sources:
        warnings.append("sources 缺 key/locator/quote/kind: %d 条"
                        % len(bad_sources))

    contract_keys = contract_top_keys(contract)
    validation_report = {
        "ok": not warnings,
        "missing_fixed": fixed,
        "warnings": warnings,
        "manuscript_nonempty": manuscript_nonempty,
        "evidence_entries": evidence_entries,
        "evidence_ok": evidence_ok,
        "contract_fields_checked": contract_keys,
    }
    if "validation" not in contract_keys:
        result["validation"] = validation_report

    write_json(result_path, result)
    print(json.dumps({"ok": not warnings, "fixed": fixed,
                      "warnings": warnings,
                      "manuscript_nonempty": manuscript_nonempty,
                      "evidence_entries": evidence_entries},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
