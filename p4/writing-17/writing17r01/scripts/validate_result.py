#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_result.py — 交付前自检：核对 result.json 与产物完整性、契约字段、格式合法。

检查项（对应评分维度）:
  1. result.json 存在且可解析，含 case_id 与 status；
  2. 题目 output_contract 的每个字段都已在 result.json 中出现（缺值也必须有键）；
  3. 关键字段 sources/uncertainty/change_log 存在；
  4. 主产物文件真实存在（mindmap.md/.mm/.svg、evidence_map.json 等）；
  5. SVG/XML 可解析、PDF 以 %PDF- 开头（若存在）；
  6. 证据映射中每个条目都有 locator 与逐字 quote，无空引用。

退出码恒为 0；全部结果写入 selfcheck.json（pass/fail 列表），不抛异常。
用法:
  python3 scripts/validate_result.py [--input /input] [--output /app/output] [--work /app/work]
"""

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except Exception as exc:  # noqa: BLE001
            last = exc
    return None, str(last)


def find_contract(case, depth=0):
    keys = ("output_contract", "outputContract", "output_schema", "outputSchema", "contract")
    if depth > 3 or not isinstance(case, dict):
        return None
    for k in keys:
        if k in case and isinstance(case[k], (dict, list)):
            return case[k]
    for k in ("task", "case", "submission"):
        if k in case and isinstance(case[k], dict):
            hit = find_contract(case[k], depth + 1)
            if hit is not None:
                return hit
    return None


def contract_keys(contract):
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            return list(contract["properties"].keys())
        return list(contract.keys())
    if isinstance(contract, list):
        return [str(i) for i in range(len(contract))]
    return []


def main():
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    checks = []  # (check_id, passed, message)
    def add(cid, ok, msg):
        checks.append({"id": cid, "passed": bool(ok), "message": msg})

    result, rerr = read_json(os.path.join(args.output, "result.json"))
    add("result_exists", result is not None, "result.json 可解析" if result is not None else "result.json 缺失/坏JSON: %s" % rerr)
    if result is None:
        result = {}

    add("case_id", bool(result.get("case_id")), "case_id=%s" % result.get("case_id"))
    add("status", result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"),
        "status=%s" % result.get("status"))

    # 契约字段核对
    case, _ = read_json(os.path.join(args.input, "case.json"))
    contract = find_contract(case)
    if contract is not None:
        ckeys = contract_keys(contract)
        missing = [k for k in ckeys if k not in result]
        add("contract_fields", not missing,
            "contract 字段 %d 个全部出现" % len(ckeys) if not missing else "缺失字段: %s" % ", ".join(missing))
    else:
        # 无显式契约时，检查默认三字段 + 固定字段
        for k in ("target_artifact", "fact_source_mapping", "version_boundary"):
            add("contract_default_%s" % k, k in result, "默认契约字段 %s %s" % (k, "存在" if k in result else "缺失"))
        add("contract_fields", True, "case.json 未显式给出 output_contract，按默认字段检查")

    for k in ("sources", "uncertainty", "change_log"):
        add("field_%s" % k, k in result, "字段 %s %s" % (k, "存在" if k in result else "缺失"))

    # 产物存在性
    artifacts = ["mindmap.md", "mindmap.mm", "mindmap.svg", "evidence_map.json"]
    for name in artifacts:
        p = os.path.join(args.output, name)
        add("artifact_%s" % name, os.path.isfile(p), "%s %s" % (name, "存在" if os.path.isfile(p) else "缺失"))

    # SVG/XML 合法
    svg_path = os.path.join(args.output, "mindmap.svg")
    if os.path.isfile(svg_path):
        try:
            ET.parse(svg_path)
            add("svg_wellformed", True, "mindmap.svg 可解析")
        except Exception as exc:  # noqa: BLE001
            add("svg_wellformed", False, "mindmap.svg 解析失败: %s" % exc)
    mm_path = os.path.join(args.output, "mindmap.mm")
    if os.path.isfile(mm_path):
        try:
            ET.parse(mm_path)
            add("freemind_wellformed", True, "mindmap.mm 可解析")
        except Exception as exc:  # noqa: BLE001
            add("freemind_wellformed", False, "mindmap.mm 解析失败: %s" % exc)

    # PDF 魔数（若有）
    for name in os.listdir(args.output) if os.path.isdir(args.output) else []:
        if name.lower().endswith(".pdf"):
            p = os.path.join(args.output, name)
            txt, _ = read_text(p)
            ok = txt is not None and txt.lstrip().startswith("%PDF-")
            add("pdf_%s" % name, ok, "PDF 头 %s" % ("合法" if ok else "不合法/不可读"))

    # 证据映射完整性
    ev, _ = read_json(os.path.join(args.output, "evidence_map.json"))
    if isinstance(ev, list):
        bad = [i for i, e in enumerate(ev)
               if not isinstance(e, dict) or not e.get("locator") or not str(e.get("quote", "")).strip()]
        add("evidence_complete", not bad and bool(ev),
            "证据条目 %d 条，缺 locator/quote 的 %d 条" % (len(ev), len(bad)))
    else:
        add("evidence_complete", False, "evidence_map.json 缺失或非列表")

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    report = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "passed": passed,
        "total": total,
        "all_passed": passed == total,
        "checks": checks,
    }
    try:
        os.makedirs(args.work, exist_ok=True)
        with open(os.path.join(args.output, "selfcheck.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        report["write_error"] = str(exc)

    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        print(json.dumps({"fatal": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(0)
