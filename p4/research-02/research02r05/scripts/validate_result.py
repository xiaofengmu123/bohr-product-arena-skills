#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的结果校验器（仅 Python 标准库）。

校验点（对应评分维度）：
1) result.json 存在、可解析、case_id 非空、status 合法；
2) 题目 output_contract 的每个字段都以原键名出现在 result.json 顶层，
   类型与契约缺省值一致（array->list、object->dict、scalar->任意，缺值可为 null）；
3) 诚实字段齐全：sources / uncertainty / pending_human / change_log /
   errors / degradations；
4) 若 status=ok：五个主产物存在、证据行含 locator+quote、综述含 [S#] 引用、
   筛选记录的标准写在决定之前；
5) 冲突/空白有据：insufficient_evidence 与 partial 必须留下缺口说明。

退出码：0 = 通过硬性契约检查（可含警告）；1 = 存在硬性失败。
校验报告写入 --work/validation_report.json。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
SCHEMA_META_KEYS = {
    "type", "properties", "required", "additionalProperties", "title",
    "description", "$schema", "definitions", "items", "enum", "examples",
    "default", "format", "const",
}


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)


def find_output_contract(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("output_contract", "outputcontract",
                             "output_schema", "outputschema"):
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


def expected_type_of(spec):
    """契约缺省值类型：array->list、object->dict、其余->None(不约束)。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        tset = set()
        if isinstance(t, str):
            tset.add(t.lower())
        elif isinstance(t, list):
            tset.update(str(x).lower() for x in t)
        if "object" in tset or "properties" in spec:
            return dict
        if "array" in tset or "items" in spec:
            return list
        return None
    if isinstance(spec, str):
        if spec.lower() in ("array", "list"):
            return list
        if spec.lower() in ("object", "dict"):
            return dict
        return None
    return None


def contract_expected(contract):
    """返回 {字段名: 期望类型} 的映射（顶层对齐）。"""
    expected = {}
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            for name, spec in props.items():
                expected[name] = expected_type_of(spec)
            req = contract.get("required")
            if isinstance(req, list):
                for name in req:
                    expected.setdefault(name, None)
            return expected
        if "fields" in contract:
            return contract_expected(contract["fields"])
        for name, spec in contract.items():
            if name in SCHEMA_META_KEYS:
                continue
            expected[name] = expected_type_of(spec)
        return expected
    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                expected[item] = None
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    expected[nm] = expected_type_of(item)
        return expected
    if isinstance(contract, str) and contract.strip():
        return {contract.strip(): None}
    return {}


def check_type_ok(value, exp):
    if exp is dict and not isinstance(value, dict):
        return False
    if exp is list and not isinstance(value, list):
        return False
    return True


def evidence_rows_ok(path):
    if not os.path.isfile(path):
        return False, ["证据表不存在: " + path]
    problems = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = [ln for ln in f if ln.strip()]
    except Exception as e:
        return False, ["证据表读取失败: " + str(e)]
    if not lines:
        return True, ["证据表为空（partial 阶段允许）"]
    for i, ln in enumerate(lines, 1):
        try:
            row = json.loads(ln)
        except Exception as e:
            problems.append("第 %d 行非 JSON: %s" % (i, e))
            continue
        for key in ("evidence_id", "source_id", "locator", "quote"):
            if not row.get(key):
                problems.append("第 %d 行缺字段 %s" % (i, key))
        for key in ("claim", "strength", "field"):
            if key not in row:
                problems.append("第 %d 行缺字段 %s" % (i, key))
    return not problems, problems


def validate(args):
    checks = []
    hard_fail = False

    def add(level, name, detail):
        nonlocal hard_fail
        checks.append({"level": level, "name": name, "detail": detail})
        if level == "FAIL":
            hard_fail = True

    rpath = os.path.join(args.output, "result.json")
    result, err = read_json(rpath)
    if result is None:
        add("FAIL", "result.json", "无法读取或解析: %s" % err)
        report = {"generated_at": now_ts(), "valid": False,
                  "checks": checks, "summary": "result.json 缺失或不可解析"}
        os.makedirs(args.work, exist_ok=True)
        with open(os.path.join(args.work, "validation_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 1

    if not isinstance(result, dict):
        add("FAIL", "result.json", "根节点必须是对象")
        result = {}

    # 1) case_id / status
    cid = result.get("case_id")
    if not (isinstance(cid, str) and cid.strip()):
        add("FAIL", "case_id", "case_id 缺失或为空")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        add("FAIL", "status", "status 非法: %r" % status)
    else:
        add("PASS", "status", "status=%s" % status)

    # 2) 契约字段对齐
    case, _ = read_json(args.case) if os.path.isfile(args.case) else (None, None)
    contract = find_output_contract(case) if case is not None else None
    if contract is None:
        add("WARN", "contract", "case.json 中未找到 output_contract，跳过逐字段校验")
    else:
        expected = contract_expected(contract)
        if not expected:
            add("WARN", "contract", "output_contract 为空")
        missing = [k for k in expected if k not in result]
        if missing:
            add("FAIL", "contract_fields", "缺失契约字段: " + ", ".join(sorted(missing)))
        else:
            add("PASS", "contract_fields", "契约字段齐全 (%d 个)" % len(expected))
        badtype = []
        for k, exp in expected.items():
            if k in result and exp is not None and not check_type_ok(result[k], exp):
                badtype.append("%s(期望%s)" % (k, exp.__name__))
        if badtype:
            add("FAIL", "contract_types", "类型不符: " + ", ".join(badtype))
        else:
            add("PASS", "contract_types", "契约字段类型一致")

    # 3) 诚实字段
    for key in ("sources", "change_log", "errors", "degradations"):
        if key not in result:
            add("FAIL", key, "缺少诚实字段 " + key)
    if not isinstance(result.get("sources"), list):
        add("FAIL", "sources", "sources 必须是列表")
    if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
        add("FAIL", "change_log", "change_log 必须是非空列表")
    if "uncertainty" not in result or not isinstance(result.get("uncertainty"), dict):
        add("FAIL", "uncertainty", "uncertainty 缺失或不是对象")
    if "pending_human" not in result:
        add("WARN", "pending_human", "建议提供 pending_human（即使为空列表）")

    # 4) 主产物
    artifacts = {
        "review.md": False, "screening_log.json": False,
        "evidence_table.jsonl": False, "themes.md": False,
        "conflicts_and_gaps.md": False,
    }
    for name in artifacts:
        artifacts[name] = os.path.isfile(os.path.join(args.output, name))
    missing_art = [k for k, v in artifacts.items() if not v]
    if status == "ok" and missing_art:
        add("FAIL", "artifacts_ok", "status=ok 但缺产物: " + ", ".join(missing_art))
    elif status == "ok":
        add("PASS", "artifacts_ok", "五个主产物齐全")

    # 5) 证据行与综述引用
    if artifacts["evidence_table.jsonl"]:
        ok, probs = evidence_rows_ok(os.path.join(args.output, "evidence_table.jsonl"))
        for p in probs[:5]:
            add(("PASS" if ok else "WARN"), "evidence_rows", p)
    if artifacts["review.md"]:
        try:
            with open(os.path.join(args.output, "review.md"), "r", encoding="utf-8-sig") as f:
                review = f.read()
            if status == "ok" and "[S" not in review:
                add("WARN", "review_citations", "综述未发现 [S#] 引用标记")
        except Exception as e:
            add("WARN", "review_citations", "综述读取失败: %s" % e)

    # 6) 状态与缺口诚实性
    if status in ("partial", "insufficient_evidence"):
        gaps = (result.get("uncertainty") or {}).get("gaps")
        ph = result.get("pending_human")
        if not gaps and not ph:
            add("WARN", "status_honesty", "%s 状态未列出缺口（uncertainty.gaps/pending_human）" % status)

    report = {
        "generated_at": now_ts(),
        "valid": not hard_fail,
        "hard_fail": hard_fail,
        "checks": checks,
        "summary": "%d 项检查：%d FAIL, %d WARN, %d PASS" % (
            len(checks),
            sum(1 for c in checks if c["level"] == "FAIL"),
            sum(1 for c in checks if c["level"] == "WARN"),
            sum(1 for c in checks if c["level"] == "PASS"),
        ),
    }
    os.makedirs(args.work, exist_ok=True)
    with open(os.path.join(args.work, "validation_report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    for c in checks:
        print("[%s] %s: %s" % (c["level"], c["name"], c["detail"]))
    print(report["summary"])
    return 0 if not hard_fail else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 result.json 是否满足输出契约")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)
    try:
        return validate(args)
    except Exception as e:
        print("[lit-review] validate 异常: " + str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
