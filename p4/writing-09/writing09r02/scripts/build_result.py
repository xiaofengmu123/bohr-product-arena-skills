#!/usr/bin/env python3
"""生成 / 合并 / 增量更新 result.json。

默认模式：按 input_summary.json 的 output_contract 生成合法骨架——
case_id + 全部契约字段（null 占位）+ status=partial + sources/uncertainty/
pending_human/change_log/limitations——保证“可提交的最小诚实交付”。
--patch：用补丁 JSON 增量合并并追加 change_log。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

VALID_STATUS = {"in_progress", "partial", "complete", "failed", "insufficient_evidence"}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: str):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def deep_merge(base, patch):
    """递归合并：dict 递归；change_log 追加；其余类型被 patch 覆盖。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k == "change_log" and isinstance(v, list) and isinstance(out.get(k), list):
                out[k] = out[k] + v
            elif k in out:
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch


def build_skeleton(case_id, contract):
    body = {"case_id": case_id}
    for k in contract:
        body[k] = None
    body.update({
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": now_utc(),
            "action": "skeleton",
            "detail": "早落盘：生成可提交的最小诚实骨架，status=partial，契约字段占位为 null。",
        }],
        "limitations": [],
    })
    return body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build/merge result.json (early skeleton + incremental patch)")
    ap.add_argument("--summary", default="/app/work/input_summary.json")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--patch", action="store_true", help="merge a JSON patch into existing result.json")
    ap.add_argument("--patch-file", default=None, help="path to patch JSON; if omitted, read from stdin")
    a = ap.parse_args(argv)

    os.makedirs(a.output_dir, exist_ok=True)
    result_path = os.path.join(a.output_dir, "result.json")

    summary = load_json(a.summary)
    case_id = summary.get("case_id") or "unknown"
    contract = summary.get("output_contract") or {}

    if not a.patch:
        body = build_skeleton(case_id, contract)
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
        print(json.dumps({
            "ok": True, "result": result_path, "mode": "skeleton",
            "case_id": case_id, "status": body["status"],
            "contract_keys": sorted(contract.keys()),
        }, ensure_ascii=False))
        return 0

    # patch mode
    if not os.path.exists(result_path):
        body = build_skeleton(case_id, contract)
    else:
        body = load_json(result_path)

    if a.patch_file:
        with open(a.patch_file, "r", encoding="utf-8-sig") as fh:
            patch = json.load(fh)
    else:
        patch = json.load(sys.stdin)

    if not isinstance(patch, dict):
        print(json.dumps({"ok": False, "error": "patch must be a JSON object"}, ensure_ascii=False))
        return 2

    patch.setdefault("change_log", [])
    if not any(isinstance(e, dict) and e.get("action") == "patch" for e in patch.get("change_log", [])):
        changed = ", ".join(sorted(k for k in patch.keys() if k != "change_log")) or "无字段"
        patch["change_log"] = list(patch["change_log"]) + [{
            "ts": now_utc(), "action": "patch", "detail": "增量合并补丁：%s" % changed,
        }]

    merged = deep_merge(body, patch)
    merged["case_id"] = body.get("case_id") or summary.get("case_id") or "unknown"
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    print(json.dumps({
        "ok": True, "result": result_path, "mode": "patch",
        "case_id": merged["case_id"], "status": merged.get("status"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
