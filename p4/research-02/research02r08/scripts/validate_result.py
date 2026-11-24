#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的结果校验器（仅 Python 标准库）。

校验 result.json 是否满足输出契约，并检查各主产物与证据回链：
- result.json 存在且为合法 JSON；
- 含 case_id 与 status（取值合法）；
- 含 output_contract 的全部字段（缺一即为错误）；
- 含 status / sources / uncertainty / pending_human / change_log 诚实字段；
- 主产物文件齐备；
- evidence_table.jsonl 每行字段完整、strength 合法、source_id 可回 inventory；
- quote 逐字回源校验（quote 必须是源文本的子串，防伪造）。

退出码：0 = 通过（可有 warning）；1 = 存在 error。
校验报告写入 <output>/validate_report.json。
"""

import argparse
import json
import os
import re
import sys

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_STRENGTH = {"established", "suggestive", "disputed", "not_reported"}
REQUIRED_HONEST = ["status", "sources", "uncertainty", "pending_human",
                   "change_log"]
REQUIRED_EVIDENCE = ["evidence_id", "source_id", "locator", "quote", "claim",
                     "strength", "field"]
REQUIRED_ARTIFACTS = ["review.md", "screening_log.json", "evidence_table.jsonl",
                      "themes.md", "conflicts_and_gaps.md"]

DEFAULT_FIELDS = ["research_question", "screening_criteria", "screening_log",
                  "evidence_table", "themes", "review_draft",
                  "conflicts_and_gaps", "references"]


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read(), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def norm_text(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def find_output_contract(node):
    if isinstance(node, dict):
        for k, val in node.items():
            if k.lower() in ("output_contract", "outputcontract",
                             "output_schema", "outputschema"):
                return val
        for val in node.values():
            r = find_output_contract(val)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_output_contract(item)
            if r is not None:
                return r
    return None


def fields_from(v):
    if isinstance(v, dict):
        props = v.get("properties")
        if isinstance(props, dict):
            return list(props.keys())
        fl = v.get("fields")
        if isinstance(fl, (dict, list)):
            return fields_from(fl)
        return list(v.keys())
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id") or item.get("id")
                      or item.get("key") or item.get("field"))
                if nm:
                    out.append(nm)
        return out
    if isinstance(v, str) and v.strip():
        return [v]
    return None


def required_fields(args):
    """从 work/contract.json 读取字段；缺失则回退到 case.json；再缺失用默认。"""
    contract_path = os.path.join(args.work, "contract.json")
    if os.path.isfile(contract_path):
        data, err = read_json(contract_path)
        if err is None and isinstance(data, dict):
            f = data.get("fields")
            if isinstance(f, list) and f:
                return [x for x in f if isinstance(x, str)]
    if os.path.isfile(args.case):
        case, err = read_json(args.case)
        if err is None:
            f = fields_from(find_output_contract(case))
            if f:
                return f
    return DEFAULT_FIELDS


def load_inventory(args):
    inv_path = os.path.join(args.work, "inventory.json")
    data, err = read_json(inv_path)
    if err is not None:
        return {}, None
    sources = {}
    for m in data.get("materials", []):
        sources[m.get("source_id")] = m
    return sources, data


def check_evidence(args, sources):
    """返回 (errors, warnings)。"""
    errors, warnings = [], []
    path = os.path.join(args.output, "evidence_table.jsonl")
    if not os.path.isfile(path):
        errors.append("缺少 evidence_table.jsonl")
        return errors, warnings
    text, err = read_text(path)
    if err is not None:
        errors.append("evidence_table.jsonl 读取失败: " + err)
        return errors, warnings
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines, 1):
        try:
            row = json.loads(ln)
        except Exception as e:  # noqa: BLE001
            errors.append("evidence_table.jsonl 第 %d 行非合法 JSON: %s" % (i, e))
            continue
        if not isinstance(row, dict):
            errors.append("evidence_table.jsonl 第 %d 行不是对象" % i)
            continue
        missing = [k for k in REQUIRED_EVIDENCE if k not in row]
        if missing:
            errors.append("evidence_table.jsonl 第 %d 行缺字段: %s" % (
                i, ", ".join(missing)))
            continue
        if row.get("strength") not in ALLOWED_STRENGTH:
            errors.append("第 %d 行 strength 非法: %r" % (i, row.get("strength")))
        sid = row.get("source_id")
        if sid not in sources:
            errors.append("第 %d 行 source_id=%r 不在 inventory 中" % (i, sid))
            continue
        quote = norm_text(row.get("quote"))
        if not quote:
            errors.append("第 %d 行 quote 为空" % i)
            continue
        src = sources[sid]
        ep = src.get("extracted_path")
        if ep and os.path.isfile(ep):
            stext, _ = read_text(ep)
            if stext is None:
                warnings.append("第 %d 行源文本不可读，quote 未校验" % i)
            elif quote not in norm_text(stext):
                errors.append("第 %d 行 quote 未在源文本 %s 中找到（疑似伪造）" % (
                    i, os.path.basename(ep)))
        else:
            warnings.append("第 %d 行源文本缺失/未抽取，quote 无法校验（标 unverified）" % i)
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验综述 Skill 输出")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    errors, warnings = [], []
    result_path = os.path.join(args.output, "result.json")

    if not os.path.isfile(result_path):
        errors.append("缺少 result.json")
    else:
        result, err = read_json(result_path)
        if err is not None:
            errors.append("result.json 解析失败: " + err)
            result = None
        else:
            if not isinstance(result, dict):
                errors.append("result.json 顶层不是对象")
            else:
                if not (result.get("case_id") and str(result["case_id"]).strip()):
                    errors.append("case_id 缺失或为空")
                if result.get("status") not in ALLOWED_STATUS:
                    errors.append("status 非法: %r" % result.get("status"))
                for k in REQUIRED_HONEST:
                    if k not in result:
                        errors.append("缺少诚实字段: " + k)
                for f in required_fields(args):
                    if f not in result:
                        errors.append("缺少 output_contract 字段: " + f)
                if (result.get("status") == "ok"
                        and not isinstance(result.get("sources"), list)):
                    errors.append("status=ok 时 sources 应为列表")
                if not isinstance(result.get("change_log"), list):
                    errors.append("change_log 应为列表")

    # 主产物齐备
    for name in REQUIRED_ARTIFACTS:
        if not os.path.isfile(os.path.join(args.output, name)):
            errors.append("缺少主产物: " + name)

    # screening_log 可解析且含筛选标准
    sl_path = os.path.join(args.output, "screening_log.json")
    if os.path.isfile(sl_path):
        sl, err = read_json(sl_path)
        if err is not None:
            errors.append("screening_log.json 解析失败: " + err)
        elif not isinstance(sl, dict) or "screening_criteria" not in sl:
            errors.append("screening_log.json 缺少 screening_criteria")
    else:
        errors.append("缺少 screening_log.json")

    # 证据表回源校验
    sources, _inv = load_inventory(args)
    ev_errs, ev_warns = check_evidence(args, sources)
    errors.extend(ev_errs)
    warnings.extend(ev_warns)

    report = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "result_path": result_path,
    }
    out_path = os.path.join(args.output, "validate_report.json")
    try:
        os.makedirs(args.output, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:  # noqa: BLE001
        print("无法写 validate_report.json: " + str(e), file=sys.stderr)

    for e in errors:
        print("[ERROR] " + e)
    for w in warnings:
        print("[WARN ] " + w)
    print("validate: %d error(s), %d warning(s) -> %s" % (
        len(errors), len(warnings), "VALID" if not errors else "INVALID"))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
