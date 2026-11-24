#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 result.json 与中间模型的结构不变量与回链一致性（仅标准库）。

检查项：
  A. result.json 存在、可解析、case_id 非空、status 合法；
  B. output_contract 的全部字段都在 result.json 中（缺字段报错，不改名不删键）；
  C. 必要元数据键 sources / uncertainty / change_log 存在（pending_human 等缺失给警告）；
  D. 中间产物存在性与 claims/evidence/review 之间的回链不变量；
  E. 筛选标准是否先于决定冻结（criteria 空而 decisions 非空即报错）；
  F. 措辞-强度软校验（established 不应带试探词，suggestive 不应带强断言词）。

退出码：0 = 无 error；1 = 存在 error。warn 不改变退出码。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as scaffold  # 复用 read_json / find_contract / contract_fields

ALLOWED_STATUS = scaffold.ALLOWED_STATUS
ALLOWED_STRENGTH = {"established", "suggestive", "disputed", "not_reported"}
HEDGE_RE = re.compile(r"可能|或许|大概|初步|提示|或可|suggest|may\b|might|could|possibly|perhaps",
                      re.IGNORECASE)
STRONG_RE = re.compile(r"证实|证明|确立|毫无疑问|prove|demonstrate|establish|confirm",
                       re.IGNORECASE)
CLAIM_MARK_RE = re.compile(r"\[(C[A-Za-z0-9_\-]*)\]")


def load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def read_text(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def report(level, msg):
    print("[%s] %s" % (level, msg))
    return 1 if level == "error" else 0


def main(argv):
    ap = argparse.ArgumentParser(description="校验综述产物与回链")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    args = ap.parse_args(argv)

    errors = 0
    warnings = 0

    result_path = os.path.join(args.output, "result.json")
    result = load_json(result_path)
    if result is None:
        errors += report("error", "result.json 不存在或无法解析: " + result_path)
        print("summary: %d errors, %d warnings" % (errors, warnings))
        return 1

    # A. 基本字段
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        errors += report("error", "case_id 缺失或为空")
    if result.get("status") not in ALLOWED_STATUS:
        errors += report("error", "status 非法: %r" % result.get("status"))

    # B. contract 字段回填
    case_data, case_errs = scaffold.read_json(args.case)
    if case_data is not None:
        contract = scaffold.find_contract(case_data)
        fields = scaffold.contract_fields(contract) or []
        for name, _default in fields:
            if name not in result:
                errors += report("error", "output_contract 字段缺失于 result.json: " + name)
        if case_errs:
            warnings += report("warn", "case.json 读取异常但继续校验: " + "; ".join(case_errs))
    else:
        warnings += report("warn", "未找到或无法解析 case.json，跳过 contract 字段校验")

    # C. 元数据键
    for key in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(key), list):
            errors += report("error", "必要元数据键 %s 缺失或非数组" % key)
    for key in ("pending_human", "errors", "degradations"):
        if key not in result:
            warnings += report("warn", "建议元数据键 %s 缺失" % key)

    status = result.get("status")

    # D. 中间产物与回链
    claims_path = os.path.join(args.output, "claims.json")
    evidence_path = os.path.join(args.output, "evidence_table.jsonl")
    review_path = os.path.join(args.output, "review.md")
    screening_path = os.path.join(args.output, "screening_log.json")
    plan_path = os.path.join(args.output, "plan.json")

    claims_doc = load_json(claims_path) or {}
    claims = claims_doc.get("claims", []) if isinstance(claims_doc, dict) else []
    evidence_rows = load_jsonl(evidence_path)
    review_text = read_text(review_path) or ""
    screening = load_json(screening_path) or {}

    if status == "ok":
        for p in (claims_path, evidence_path, review_path, screening_path, plan_path):
            if not os.path.isfile(p):
                errors += report("error", "status=ok 但缺失主产物: " + p)
        if not claims:
            errors += report("error", "status=ok 但 claims.json 为空")
        if not evidence_rows:
            errors += report("error", "status=ok 但 evidence_table.jsonl 为空")
        if len(review_text.strip()) < 200:
            errors += report("error", "status=ok 但 review.md 过短（<200 字符）")

    # claims 结构校验
    claim_ids = set()
    for i, c in enumerate(claims):
        cid = c.get("claim_id") if isinstance(c, dict) else None
        if not cid:
            errors += report("error", "claims[%d] 缺 claim_id" % i)
            continue
        if cid in claim_ids:
            errors += report("error", "claim_id 重复: " + str(cid))
        claim_ids.add(str(cid))
        if not isinstance(c.get("text"), str) or not c["text"].strip():
            errors += report("error", "claim %s 缺 text" % cid)
        if c.get("strength") not in ALLOWED_STRENGTH:
            errors += report("error", "claim %s strength 非法: %r" % (cid, c.get("strength")))
        refs = c.get("evidence_refs") or c.get("evidence_ids") or []
        if not isinstance(refs, list) or len(refs) == 0:
            errors += report("error", "claim %s 没有证据引用（evidence_refs 空）" % cid)

    # 证据行结构校验
    evidence_ids = set()
    for i, e in enumerate(evidence_rows):
        if not isinstance(e, dict):
            errors += report("error", "evidence_table 第 %d 行非对象" % (i + 1))
            continue
        eid = e.get("evidence_id")
        if not eid:
            errors += report("error", "evidence 第 %d 行缺 evidence_id" % (i + 1))
            continue
        evidence_ids.add(str(eid))
        for key in ("source_id", "locator", "quote"):
            v = e.get(key)
            if not isinstance(v, str) or not v.strip():
                errors += report("error", "evidence %s 缺非空 %s" % (eid, key))
        if e.get("strength") not in ALLOWED_STRENGTH:
            warnings += report("warn", "evidence %s strength 非法: %r" % (eid, e.get("strength")))

    # 回链：claim.evidence_refs -> evidence_ids；review [C#] -> claim_ids
    for c in claims:
        cid = c.get("claim_id")
        refs = c.get("evidence_refs") or c.get("evidence_ids") or []
        for ref in refs:
            if str(ref) not in evidence_ids:
                errors += report("error", "claim %s 引用不存在的证据 %s" % (cid, ref))
    referenced_claims = set(CLAIM_MARK_RE.findall(review_text)) if review_text else set()
    for rc in sorted(referenced_claims):
        if rc not in claim_ids:
            errors += report("error", "review.md 引用不存在的 claim [C%s]" % rc)
    orphan = sorted(claim_ids - referenced_claims)
    if orphan and status == "ok":
        warnings += report("warn", "claim 未被 review.md 引用（孤儿）: " + ", ".join(orphan))

    # E. 筛选标准先于决定
    criteria = screening.get("criteria", {}) if isinstance(screening, dict) else {}
    decisions = screening.get("decisions", []) if isinstance(screening, dict) else []
    has_criteria = bool((criteria.get("inclusion") or criteria.get("exclusion")
                         or criteria.get("dedup_rules")) if isinstance(criteria, dict) else criteria)
    if decisions and not has_criteria:
        errors += report("error", "screening_log 有决定但未先写纳入/排除/去重标准")

    # F. 措辞-强度软校验
    for c in claims:
        txt = c.get("text") or ""
        strength = c.get("strength")
        if strength == "established" and HEDGE_RE.search(txt):
            warnings += report("warn", "claim %s 标 established 但含试探词: %s" % (c.get("claim_id"), txt[:60]))
        if strength == "suggestive" and STRONG_RE.search(txt):
            warnings += report("warn", "claim %s 标 suggestive 但含强断言词: %s" % (c.get("claim_id"), txt[:60]))

    # 诚实状态
    if status == "insufficient_evidence" and not result.get("uncertainty"):
        warnings += report("warn", "status=insufficient_evidence 但 uncertainty 为空")

    print("summary: %d errors, %d warnings" % (errors, warnings))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
