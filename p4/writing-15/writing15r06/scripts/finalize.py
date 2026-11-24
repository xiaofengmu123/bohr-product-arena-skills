#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize.py -- 结果合并、校验与终稿落盘。

职责：
1. 读取 result.json 与 inventory.json，补齐 case_id 与 output_contract 全部字段。
2. 校验元数据字段（status/sources/uncertainty/change_log/errors/degradations/runtime）。
3. 合并可选的一致性报告与证据文件，写回一份 JSON 合法的最终 result.json。
4. 输出缺失/待办摘要，便于 Agent 决定是否还有必须补齐项。

只做合并与校验，不编造事实；缺值字段保留 null/[] 并列入 pending_human。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

REQUIRED_META = [
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "artifacts", "pending_human", "runtime",
]

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def load_json(path, errors):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as fh:
        try:
            return json.load(fh)
        except ValueError as exc:
            errors.append({"file": path, "error": "bad_json", "detail": str(exc)[:200]})
            return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="合并校验 result.json 并落盘终稿")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--status", default=None, help="可选状态覆盖：ok|partial|insufficient_evidence|input_error")
    ap.add_argument("--case-id", default=None, help="可选 case_id 覆盖")
    ap.add_argument("--report", default=None, help="可选一致性报告 JSON，合并进 result")
    ap.add_argument("--evidence", default=None, help="可选证据映射 JSON，合并进 result")
    args = ap.parse_args(argv)

    errors = []
    os.makedirs(args.output, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    result = load_json(result_path, errors) or {}

    inv_path = os.path.join(args.work, "inventory.json")
    inventory = load_json(inv_path, errors) or {}

    case_id = args.case_id or result.get("case_id") or inventory.get("case_id")
    contract = inventory.get("output_contract") or result.get("output_contract") or {}

    # 1) case_id 与状态
    result["case_id"] = case_id
    if args.status and args.status in VALID_STATUS:
        result["status"] = args.status
    if result.get("status") not in VALID_STATUS:
        result["status"] = "partial"

    # 2) echo output_contract 字段（保持既有值，缺省 null/[]）
    for field in contract:
        if field in ("output_contract", "output_contract_spec"):
            continue
        if field not in result:
            spec = contract[field]
            if isinstance(spec, dict) and str(spec.get("type", "")).lower() in ("list", "array"):
                result[field] = []
            else:
                result[field] = None
    result.setdefault("output_contract", contract)

    # 3) 元数据字段兜底
    for key in REQUIRED_META:
        if key not in result:
            if key in ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human"):
                result[key] = []
            elif key == "artifacts":
                result[key] = {}
            elif key == "runtime":
                result[key] = {}
            else:
                result[key] = None

    # 4) 合并可选报告/证据
    if args.report:
        rep = load_json(args.report, errors)
        if isinstance(rep, dict):
            result.setdefault("consistency_report", rep)
    if args.evidence:
        ev = load_json(args.evidence, errors)
        if isinstance(ev, dict):
            result.setdefault("evidence_map", ev)

    # 5) runtime 收尾
    rt = result.get("runtime")
    if not isinstance(rt, dict):
        rt = {}
        result["runtime"] = rt
    rt["finished_at"] = datetime.now(timezone.utc).isoformat()
    rt["stage"] = "finalized"

    # 6) 若骨架阶段的错误未并入 errors，则追加
    for err in errors:
        if err not in result.get("errors", []):
            result.setdefault("errors", []).append(err)

    # 7) 校验 JSON 可序列化，写回
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    empty_meta = [k for k in ("sources", "uncertainty", "change_log", "pending_human")
                  if isinstance(result.get(k), list) and len(result[k]) == 0]
    print(json.dumps({
        "case_id": case_id,
        "status": result.get("status"),
        "result": result_path,
        "contract_fields": sorted(contract.keys()),
        "empty_meta": empty_meta,
        "errors": len(result.get("errors", [])),
        "note": "空数组的元数据字段若与任务相关，应在收尾前补齐，否则保留为空并如实标注",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
