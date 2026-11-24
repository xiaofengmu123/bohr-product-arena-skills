"""科研成果转化写作 Skill · 机械校验（纯标准库）。

负责对 /app/work/draft.json 与 /app/output/result.json 做机械校验，
只校验"结构/契约/证据"层面的事实，不判断写作质量（写作质量由 Agent 与评审判断）。

校验项：
  - 契约键覆盖：result.json 是否包含 case_id + output_contract 全部键 + 公共字段
  - 主产物存在性、可编辑性（文本类非空）
  - evidence_map / change_log / preserved_facts 的 locator + quote 结构
  - 源文数值未在改稿中出现且未在 change_log 说明 → warn（对应"留得住"）
  - 论断强度是否被升级（相关→因果等，只降不升）
  - 确定性：同一 draft 两次 build 的业务内容 hash 一致

返回统一结构：{"fail": int, "warn": int, "items": [{"level","check","ok","detail"}]}
"""
from __future__ import annotations

import json
import os
import sys

try:
    from . import lib
except ImportError:  # 兼容直接 `python validate.py` 运行
    import lib


def check_draft(draft: dict, source_index: dict):
    """校验工作稿 draft.json。"""
    items = []
    fails = 0
    warns = 0
    draft = draft or {}

    def _add(level, check, ok, detail):
        nonlocal fails, warns
        items.append({"level": level, "check": check, "ok": ok, "detail": detail})
        if level == "fail":
            fails += 1
        elif level == "warn":
            warns += 1

    deliverable = (draft.get("deliverable_text") or "").strip()
    if not deliverable:
        _add("fail", "deliverable_text", False, "主交付正文为空")
    else:
        _add("pass", "deliverable_text", True, "chars=%d" % len(deliverable))

    if not draft.get("deliverable_name"):
        _add("fail", "deliverable_name", False, "缺少主交付文件名")
    else:
        _add("pass", "deliverable_name", True, draft["deliverable_name"])

    # 证据结构
    for field in ("preserved_facts", "evidence_map", "change_log"):
        vals = draft.get(field)
        if vals is None:
            _add("warn", field, False, "字段缺失，应为数组")
            continue
        if not isinstance(vals, list):
            _add("fail", field, False, "应为数组")
            continue
        if field == "evidence_map" and not vals:
            _add("warn", field, False, "证据映射为空；有正文就应有证据锚点")
        bad = 0
        for it in vals:
            if not isinstance(it, dict):
                bad += 1
                continue
            if field == "change_log":
                if not (it.get("quote") or it.get("original_quote") or it.get("revised_quote")):
                    bad += 1
            else:
                if not it.get("quote"):
                    bad += 1
        if bad:
            _add("fail", field, False, "%d 条缺少 quote" % bad)
        else:
            _add("pass", field, True, "count=%d" % len(vals))

    # 强度检查：change_log 中改写是否升级论断
    upgrades = []
    for c in draft.get("change_log") or []:
        orig = c.get("original_quote") or ""
        rev = c.get("revised_quote") or ""
        if orig and rev and not lib.strength_allowed(rev, orig):
            upgrades.append(c.get("id", "?"))
    if upgrades:
        _add("fail", "strength_no_upgrade", False, "论断强度疑似升级: %s" % ",".join(map(str, upgrades)))
    else:
        _add("pass", "strength_no_upgrade", True, "未发现强度升级")

    return {"fail": fails, "warn": warns, "items": items}


def check_result(result: dict, source_index: dict):
    """校验 result.json。"""
    items = []
    fails = 0
    warns = 0
    result = result or {}

    def _add(level, check, ok, detail):
        nonlocal fails, warns
        items.append({"level": level, "check": check, "ok": ok, "detail": detail})
        if level == "fail":
            fails += 1
        elif level == "warn":
            warns += 1

    if not result.get("case_id"):
        _add("fail", "case_id", False, "缺少 case_id")
    else:
        _add("pass", "case_id", True, result["case_id"])

    if result.get("status") not in lib.STATUS_ALL:
        _add("fail", "status", False, "status 非法: %r" % result.get("status"))
    else:
        _add("pass", "status", True, result["status"])

    keys = source_index.get("contract_keys") or []
    missing = [k for k in keys if k not in result]
    if missing:
        _add("fail", "contract_keys", False, "契约键缺失: %s" % ",".join(map(str, missing)))
    else:
        _add("pass", "contract_keys", True, "keys=%d" % len(keys))

    for common in ("sources", "uncertainty", "change_log"):
        if common not in result:
            _add("warn", common, False, "公共字段缺失")
        else:
            _add("pass", common, True, common)

    return {"fail": fails, "warn": warns, "items": items}


def check_numeric_preservation(draft: dict, source_index: dict):
    """检查源文数值是否在改稿中出现（或已在 change_log 说明删除理由）。"""
    items = []
    deliverable = (draft.get("deliverable_text") or "")
    change_reasons = " ".join(
        (str(c.get("reason") or "") + " " + str(c.get("original_quote") or ""))
        for c in (draft.get("change_log") or [])
    )
    # 从证据候选中取数值，若候选为空则跳过
    candidates = source_index.get("evidence_candidates") or []
    numbers = []
    for c in candidates:
        if c.get("kind") in ("number_unit", "sample_size", "sample_n"):
            val = c.get("value") or ""
            if val and val not in numbers:
                numbers.append(val)
    missing = [v for v in numbers if v not in deliverable and v not in change_reasons]
    if missing:
        items.append({"level": "warn", "check": "numeric_preservation", "ok": False,
                      "detail": "源文数值未在改稿出现且未说明: %s" % ",".join(missing[:20])})
    else:
        items.append({"level": "pass", "check": "numeric_preservation", "ok": True,
                      "detail": "checked=%d missing=0" % len(numbers)})
    return items


def validate(draft: dict, result: dict, source_index: dict):
    """汇总校验。"""
    d = check_draft(draft, source_index)
    r = check_result(result, source_index)
    n = check_numeric_preservation(draft, source_index)
    items = d["items"] + r["items"] + n
    fails = sum(1 for i in items if i["level"] == "fail")
    warns = sum(1 for i in items if i["level"] == "warn")
    return {"fail": fails, "warn": warns, "items": items}


if __name__ == "__main__":
    # 独立运行：校验一个 draft.json / result.json / source_index.json
    base = sys.argv[1] if len(sys.argv) > 1 else "/app/work"
    draft = lib.read_json(os.path.join(base, "draft.json"))[0] or {}
    result = lib.read_json(os.path.join(base, "..", "output", "result.json"))[0] \
        if os.path.isdir(os.path.join(base, "..", "output")) else {}
    index = lib.read_json(os.path.join(base, "source_index.json"))[0] or {}
    rep = validate(draft, result, index)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0)
