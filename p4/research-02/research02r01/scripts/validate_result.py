#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-traced-review 结果校验脚本（仅标准库）。

校验内容：
1) result.json 存在且为合法 JSON，含 case_id 与 output_contract 全部字段；
2) status 合法，sources/uncertainty/change_log/pending_human 字段存在；
3) 证据溯源硬约束：review.md 每个 [S#] 都在 evidence_map 有对应 claim，
   且该 claim 至少一条 quote 的 locator 与 quote 均非空；
4) evidence_table.jsonl 每行必填字段齐全且 quote 非空；
5) artifacts 指向的主产物文件存在。

退出码：0 = 通过（允许存在告警）；1 = 存在硬错误。
"""

import argparse
import json
import os
import re
import sys

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
CLAIM_MARKER = re.compile(r"\[S(\d+)\]")
EVIDENCE_FIELDS = ("evidence_id", "source_id", "locator", "quote",
                   "claim", "strength", "field", "notes")
ARTIFACT_KEYS = ("review", "screening_log", "evidence_table",
                 "evidence_map", "themes", "conflicts_and_gaps")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read(), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def read_jsonl(path):
    rows, errors = [], []
    text, err = read_text(path)
    if text is None:
        return rows, [err]
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:  # noqa: BLE001
            errors.append("evidence_table.jsonl 第 %d 行不是合法 JSON: %s" % (i, exc))
    return rows, errors


def default_for_spec(spec):
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            if t.lower() == "array":
                return []
            if t.lower() == "object":
                return {}
        if isinstance(t, list):
            lowered = [str(x).lower() for x in t]
            if "null" in lowered:
                return None
            if "array" in lowered:
                return []
            if "object" in lowered:
                return {}
        if "items" in spec:
            return []
        if "properties" in spec:
            return {}
    return None


def normalize_fields(contract):
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fields = contract.get("fields")
        if isinstance(fields, (dict, list)):
            return normalize_fields(fields)
        return [(name, default_for_spec(contract[name])) for name in contract]
    if isinstance(contract, list):
        out = []
        for item in contract:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    out.append((nm, default_for_spec(item)))
        return out
    if isinstance(contract, str) and contract.strip():
        return [(contract.strip(), None)]
    return None


def find_contract(node):
    if isinstance(node, dict):
        for key, val in node.items():
            if key.lower() in ("output_contract", "outputcontract",
                               "output_schema", "outputschema"):
                return val
        for val in node.values():
            found = find_contract(val)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_contract(item)
            if found is not None:
                return found
    return None


def check_evidence_map(emap, evidence_ids, errors, warnings):
    if not isinstance(emap, dict) or "claims" not in emap:
        errors.append("evidence_map.json 缺少 claims 数组")
        return {}
    claims = emap.get("claims")
    if not isinstance(claims, list):
        errors.append("evidence_map.json 的 claims 不是数组")
        return {}
    seen = {}
    for claim in claims:
        if not isinstance(claim, dict):
            errors.append("evidence_map 存在非对象 claim")
            continue
        cid = claim.get("claim_id")
        if not cid:
            errors.append("evidence_map 存在缺少 claim_id 的 claim")
            continue
        if cid in seen:
            errors.append("evidence_map claim_id 重复: " + str(cid))
        seen[cid] = claim
        quotes = claim.get("quotes")
        if not isinstance(quotes, list) or not quotes:
            errors.append("claim %s 缺少非空 quotes" % str(cid))
            continue
        valid_quote = False
        for q in quotes:
            if not isinstance(q, dict):
                continue
            loc = q.get("locator")
            quo = q.get("quote")
            if loc and quo:
                valid_quote = True
                break
        if not valid_quote:
            errors.append("claim %s 没有一条 locator 与 quote 均非空的证据" % str(cid))
        for eid in claim.get("evidence_ids") or []:
            if eid not in evidence_ids:
                errors.append("claim %s 引用了不存在的 evidence_id %s" % (cid, eid))
    return seen


def main(argv=None):
    parser = argparse.ArgumentParser(description="evidence-traced-review 校验")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--input", default="/input")
    args = parser.parse_args(argv)

    errors, warnings = [], []

    result_path = os.path.join(args.output, "result.json")
    result, err = read_json(result_path)
    if result is None:
        print("FAIL: result.json 缺失或不可解析: %s" % err)
        return 1

    # 1) case_id / status / 诚实字段
    if not result.get("case_id"):
        errors.append("result.json 缺少 case_id")
    if result.get("status") not in STATUSES:
        errors.append("status 非法: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log", "pending_human"):
        if key not in result:
            errors.append("result.json 缺少字段 %s" % key)

    # 2) output_contract 字段齐全
    case, case_err = read_json(args.case)
    contract = find_contract(case) if isinstance(case, dict) else None
    if contract is not None:
        fields = normalize_fields(contract) or []
        for name, _default in fields:
            if name not in result:
                errors.append("result.json 缺少契约字段 %s" % name)

    # 3) artifacts 指向的文件存在
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        for key in ARTIFACT_KEYS:
            path = artifacts.get(key)
            if path and not os.path.isfile(path):
                warnings.append("主产物尚缺失: %s" % key)
    else:
        warnings.append("result.json 缺少 artifacts 对象")

    # 4) 证据表行校验
    ev_path = os.path.join(args.output, "evidence_table.jsonl")
    evidence_ids = set()
    if os.path.isfile(ev_path):
        rows, row_errs = read_jsonl(ev_path)
        errors.extend(row_errs)
        for row in rows:
            for f in EVIDENCE_FIELDS:
                if f not in row:
                    errors.append("evidence_table 行 %s 缺字段 %s"
                                  % (row.get("evidence_id", "?"), f))
            if not row.get("quote"):
                errors.append("evidence_table 行 %s 的 quote 为空"
                              % row.get("evidence_id", "?"))
            eid = row.get("evidence_id")
            if eid:
                evidence_ids.add(eid)
    else:
        warnings.append("evidence_table.jsonl 尚不存在")

    # 5) evidence_map 校验
    map_path = os.path.join(args.output, "evidence_map.json")
    claims = {}
    if os.path.isfile(map_path):
        emap, merr = read_json(map_path)
        if emap is None:
            errors.append("evidence_map.json 不可解析: %s" % merr)
        else:
            claims = check_evidence_map(emap, evidence_ids, errors, warnings)
    else:
        warnings.append("evidence_map.json 尚不存在")

    # 6) review.md 每个 [S#] 都要能回链
    review_path = os.path.join(args.output, "review.md")
    if os.path.isfile(review_path):
        text, _err = read_text(review_path)
        if text is not None:
            cited = {m.group(1) for m in CLAIM_MARKER.finditer(text)}
            for num in sorted(cited, key=int):
                cid = "S" + num
                if cid not in claims:
                    errors.append("review.md 引用了无对应 claim 的 [%s]" % cid)
            unused = set(claims) - {"S" + n for n in cited}
            if unused:
                warnings.append("evidence_map 有未在 review.md 引用的 claim: %s"
                                % ", ".join(sorted(unused)))
    else:
        warnings.append("review.md 尚不存在")

    print("=== validate_result 报告 ===")
    print("result.json : %s (status=%s)" % (result_path, result.get("status")))
    print("errors   : %d" % len(errors))
    for e in errors:
        print("  [E] " + e)
    print("warnings : %d" % len(warnings))
    for w in warnings:
        print("  [W] " + w)

    if errors:
        print("FAIL")
        return 1
    print("OK（可通过，告警见上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
