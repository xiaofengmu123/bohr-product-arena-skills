#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize.py — result.json 契约校验、确定性排序与自检报告。

职责（阶段 6）：
  1) 读 result.json / case_normalized.json / scaffold.json
  2) 校验：覆盖完整、字段齐全、证据逐字、status 诚实、output_contract 回填
  3) 按确定性平局键重排 results 并赋 rank（tier 组内 priority_score desc …）
  4) 写 selfcheck.json（errors/warnings），打印汇总；退出码恒为 0（鲁棒）

仅标准库，Python 3.7+。
"""
import argparse
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_RELEVANCE = {"direct", "indirect", "irrelevant", "unknown"}
ALLOWED_TIER = {"retain", "pending", "exclude"}
ALLOWED_CONFIDENCE = {"high", "medium", "low", "unknown"}
TIER_ORDER = {"retain": 0, "pending": 1, "exclude": 2}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        print(f"finalize: cannot load {path}: {e}")
        return None


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def norm_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def evidence_completeness(entry):
    ev = entry.get("evidence")
    if not isinstance(ev, list):
        return 0
    quotes = sum(1 for e in ev if isinstance(e, dict) and isinstance(e.get("quote"), str) and e["quote"].strip())
    reasons = 1 if entry.get("reasons") else 0
    return quotes + reasons


def _year_key(entry):
    y = entry.get("year")
    return y if isinstance(y, (int, float)) else 0


def order_key(entry):
    tier = entry.get("tier") if entry.get("tier") in ALLOWED_TIER else "pending"
    score = entry.get("priority_score")
    if not isinstance(score, (int, float)):
        score = 0.0
    return (
        TIER_ORDER[tier],
        -round(float(score), 6),
        -evidence_completeness(entry),
        -_year_key(entry),
        norm_text(entry.get("title")),
        str(entry.get("record_id") or ""),
    )


def validate(result, norm):
    errors, warnings = [], []
    if not isinstance(result, dict):
        return [f"result.json 顶层不是对象"], []
    cid = result.get("case_id")
    if not cid:
        errors.append("case_id 缺失或为空。")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append(f"status={status!r} 非法，应为 {sorted(ALLOWED_STATUS)}。")
    results = result.get("results")
    if not isinstance(results, list):
        errors.append("results 不是列表。")
        results = []
    candidates = (norm or {}).get("candidates") or []
    cand_ids = [c.get("record_id") for c in candidates if isinstance(c, dict)]
    res_ids = [r.get("record_id") for r in results if isinstance(r, dict)]
    missing = [c for c in cand_ids if c not in res_ids]
    if missing:
        errors.append(f"coverage 不完整：缺 {len(missing)} 条候选：{missing[:5]}{'…' if len(missing) > 5 else ''}。")
    extra = [r for r in res_ids if r not in cand_ids]
    if extra:
        warnings.append(f"results 存在候选集之外的 record_id {extra[:5]}（如为合成夹具可保留）。")
    if not cand_ids and not res_ids:
        warnings.append("候选集与 results 均为空；若确为空输入，status 应为 insufficient_evidence。")

    seen = set()
    for i, r in enumerate(results):
        loc = f"results[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{loc} 不是对象。")
            continue
        rid = r.get("record_id")
        if not rid:
            errors.append(f"{loc} 缺 record_id。")
        elif rid in seen:
            errors.append(f"{loc} record_id={rid} 重复。")
        seen.add(rid)
        rel = r.get("relevance")
        if rel not in ALLOWED_RELEVANCE:
            errors.append(f"{loc}({rid}) relevance={rel!r} 非法。")
        tier = r.get("tier")
        if tier not in ALLOWED_TIER:
            errors.append(f"{loc}({rid}) tier={tier!r} 非法。")
        else:
            if tier == "exclude" and rel not in ("irrelevant", "unknown"):
                warnings.append(f"{loc}({rid}) tier=exclude 但 relevance={rel}，请确认排除仅用于不相关。")
            if tier == "retain" and rel == "unknown":
                warnings.append(f"{loc}({rid}) tier=retain 但 relevance=unknown，语义判断未完成，建议回退 pending。")
        score = r.get("priority_score")
        if score is not None:
            if not isinstance(score, (int, float)) or not (0.0 <= float(score) <= 1.0):
                errors.append(f"{loc}({rid}) priority_score={score!r} 应在 0–1。")
        elif tier in ("retain", "exclude"):
            warnings.append(f"{loc}({rid}) priority_score=null 但 tier={tier}，建议补充分数或降级 pending。")
        conf = r.get("confidence")
        if conf not in ALLOWED_CONFIDENCE:
            warnings.append(f"{loc}({rid}) confidence={conf!r} 非法，建议 high/medium/low。")
        reasons = r.get("reasons")
        if not isinstance(reasons, list) or not any(reasons):
            if tier != "pending" or rel != "unknown":
                warnings.append(f"{loc}({rid}) 无 reasons，无法复核排除/保留理由。")
        ev = r.get("evidence")
        if not isinstance(ev, list):
            errors.append(f"{loc}({rid}) evidence 不是列表。")
        else:
            for j, e in enumerate(ev):
                if not isinstance(e, dict):
                    errors.append(f"{loc}({rid}).evidence[{j}] 不是对象。")
                    continue
                if not e.get("locator"):
                    errors.append(f"{loc}({rid}).evidence[{j}] 缺 locator。")
                if not isinstance(e.get("quote"), str) or not e["quote"].strip():
                    errors.append(f"{loc}({rid}).evidence[{j}] 缺逐字 quote。")
    return errors, warnings


def _check_status_honesty(result, errors, warnings):
    status = result.get("status")
    results = result.get("results") or []
    any_unknown = any(isinstance(r, dict) and r.get("relevance") == "unknown" for r in results)
    if status == "ok" and (any_unknown or not results):
        warnings.append("status=ok 但仍存在 unknown/空结果，建议如实降级为 partial。")
    if status in ("insufficient_evidence", "input_error") and results:
        warnings.append(f"status={status} 却仍有 {len(results)} 条结果，请核对是否矛盾。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="finalize: validate, order, self-check")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)
    output_dir = Path(args.output)
    work_dir = Path(args.work)

    result = _load(output_dir / "result.json")
    norm = _load(work_dir / "case_normalized.json") or {}
    scaffold = _load(work_dir / "scaffold.json") or {}

    if result is None:
        # nothing to validate; write a minimal selfcheck and a minimal legal result
        result = {
            "case_id": norm.get("case_id"),
            "status": "input_error",
            "results": [],
            "evidence": [],
            "status_notes": ["finalize 无法读取 result.json。"],
            "omissions": [],
            "dedupe_groups": [],
            "sources": [],
            "uncertainty": [],
            "pending_human": ["result.json 缺失或损坏，需重跑 discover。"],
            "errors": ["result.json 缺失或损坏。"],
            "degradations": ["finalize 降级路径。"],
            "change_log": [{"ts": now_iso(), "step": "finalize", "action": "missing_result_fallback"}],
            "runtime": {"finalized_at": now_iso()},
        }
        _save(output_dir / "result.json", result)
        selfcheck = {"errors": ["result.json 缺失或损坏。"], "warnings": [], "finalized_at": now_iso()}
        _save(output_dir / "selfcheck.json", selfcheck)
        print("finalize: errors=1 warnings=0 (missing result.json -> input_error)")
        return 0

    errors, warnings = validate(result, norm)
    _check_status_honesty(result, errors, warnings)

    # deterministic ordering + rank assignment
    results = result.get("results")
    if isinstance(results, list) and results:
        results.sort(key=order_key)
        for i, r in enumerate(results):
            r["rank"] = i + 1
    result["results"] = results if isinstance(results, list) else []

    # contract field presence
    contract_fields = (norm or {}).get("contract_fields") or []
    for f in contract_fields:
        if f not in result:
            result[f] = None
            warnings.append(f"output_contract 字段 {f!r} 缺失，已回填 null。")

    result.setdefault("change_log", []).append(
        {"ts": now_iso(), "step": "finalize", "action": f"validated_and_ordered errors={len(errors)} warnings={len(warnings)}"})
    if result.get("runtime") is None:
        result["runtime"] = {}
    result["runtime"]["finalized_at"] = now_iso()
    _save(output_dir / "result.json", result)

    selfcheck = {
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "errors": errors,
        "warnings": warnings,
        "n_results": len(results) if isinstance(results, list) else 0,
        "n_candidates": len((norm or {}).get("candidates") or []),
        "n_dedupe_groups": len(scaffold.get("dedupe_groups") or result.get("dedupe_groups") or []),
        "finalized_at": now_iso(),
    }
    _save(output_dir / "selfcheck.json", selfcheck)
    print(f"finalize: errors={len(errors)} warnings={len(warnings)}")
    for e in errors:
        print(f"  [ERROR] {e}")
    for w in warnings:
        print(f"  [WARN ] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())