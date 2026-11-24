#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lit-review-synth 自检脚本（仅 Python 标准库）。

校验：
1) result.json 存在、为合法 JSON；
2) case_id 非空、status 取值合法、审计字段齐备；
3) output_contract 的全部字段已在 result.json 中回填；
4) 五个主产物文件存在；
5) screening_log.json 结构合法；evidence_table.jsonl 每行可解析且含必需字段。

退出码 0 表示结构自检通过；1 表示存在失败项。
"""

import argparse
import json
import os
import sys

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
REQUIRED_HONEST = ["status", "sources", "uncertainty", "change_log"]
EVIDENCE_KEYS = ["evidence_id", "source_id", "locator", "quote", "claim", "strength", "field"]

DEFAULT_CONTRACT = {
    "research_question": None,
    "screening_criteria": None,
    "screening_log": [],
    "evidence_table": [],
    "themes": [],
    "review_draft": None,
    "conflicts_and_gaps": [],
    "references": [],
}

ARTIFACT_FILES = {
    "review": "review.md",
    "screening_log": "screening_log.json",
    "evidence_table": "evidence_table.jsonl",
    "themes": "themes.md",
    "conflicts_and_gaps": "conflicts_and_gaps.md",
}


def default_for_spec(spec):
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            if t.lower() == "array":
                return []
            if t.lower() == "object":
                return {}
        elif isinstance(t, list):
            ts = [str(x).lower() for x in t]
            if "array" in ts and "null" in ts:
                return None
            if "array" in ts:
                return []
            if "object" in ts and "null" in ts:
                return None
            if "object" in ts:
                return {}
        if "properties" in spec:
            return {}
        if "items" in spec:
            return []
        if "default" in spec:
            return spec["default"]
    return None


def fields_from(v):
    if isinstance(v, dict):
        props = v.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fl = v.get("fields")
        if isinstance(fl, (dict, list)):
            return fields_from(fl)
        return [(name, default_for_spec(v[name])) for name in v]
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    out.append((nm, default_for_spec(item)))
        return out
    if isinstance(v, str) and v.strip():
        return [(v, None)]
    return None


def find_output_contract(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("output_contract", "outputcontract", "output_schema", "outputschema"):
                return v
        for v in node.values():
            r = find_output_contract(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_output_contract(item)
            if r is not None:
                return r
    return None


def load_json(path):
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f), None
        except UnicodeDecodeError:
            continue
        except Exception as e:  # noqa: BLE001
            return None, str(e)
    return None, "无法以 UTF-8 读取"


def main(argv=None):
    ap = argparse.ArgumentParser(description="lit-review-synth 结果自检")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    checks = []
    failures = []

    def check(cond, label, detail=""):
        ok = bool(cond)
        checks.append((ok, label, detail))
        if not ok:
            failures.append(label)

    result_path = os.path.join(args.output, "result.json")
    if not os.path.isfile(result_path):
        print("FAIL result.json 不存在: " + result_path)
        return 1
    result, err = load_json(result_path)
    check(err is None, "result.json 为合法 JSON", err or "")

    if err is None:
        check(isinstance(result, dict), "result.json 为对象")
        check(bool(result.get("case_id")), "case_id 非空")
        check(result.get("status") in ALLOWED_STATUS, "status 取值合法", str(result.get("status")))
        for k in REQUIRED_HONEST:
            check(k in result, "审计字段 %s 存在" % k)

    # 契约字段回填
    case = None
    if os.path.isfile(args.case):
        case, _cerr = load_json(args.case)
    spec = find_output_contract(case) if isinstance(case, (dict, list)) else None
    fields = fields_from(spec) if spec is not None else None
    if not fields:
        fields = [(k, None) for k in DEFAULT_CONTRACT]
    if err is None and isinstance(result, dict):
        for name, _d in fields:
            check(name in result, "契约字段 %s 已回填" % name)

    # 主产物存在
    for label, fname in ARTIFACT_FILES.items():
        p = os.path.join(args.output, fname)
        check(os.path.isfile(p), "产物存在 %s" % fname)

    # screening_log 结构
    scr_path = os.path.join(args.output, "screening_log.json")
    if os.path.isfile(scr_path):
        scr, serr = load_json(scr_path)
        check(serr is None and isinstance(scr, dict), "screening_log.json 为合法 JSON")
        if serr is None and isinstance(scr, dict):
            check("criteria" in scr, "screening_log 含 criteria")
            check("decisions" in scr and isinstance(scr["decisions"], list),
                  "screening_log 含 decisions 列表")

    # evidence_table 每行可解析且字段齐备
    ev_path = os.path.join(args.output, "evidence_table.jsonl")
    if os.path.isfile(ev_path) and os.path.getsize(ev_path) > 0:
        with open(ev_path, "r", encoding="utf-8-sig") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception as e:  # noqa: BLE001
                    check(False, "evidence_table 第 %d 行为合法 JSON" % i, str(e))
                    continue
                for k in EVIDENCE_KEYS:
                    check(k in row, "evidence_table 第 %d 行含 %s" % (i, k))

    for ok, label, detail in checks:
        print(("PASS " if ok else "FAIL ") + label + (("  |  " + detail) if detail else ""))
    print("=" * 44)
    print("自检项 %d，失败 %d" % (len(checks), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
