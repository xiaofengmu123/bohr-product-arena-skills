#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build (or merge into) the result.json skeleton for /app/output.

The skeleton always contains every field the output contract requires, so an
early crash never leaves a structurally incomplete product. When a previous
result.json exists, its content is preserved and only missing keys are filled.

Exit codes:
  0  skeleton written/merged
  2  bad arguments
  3  /input manifest unavailable and cannot be built
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# The fixed output-contract keys. "facts_sources" covers 事实与来源映射,
# "version_boundary" covers 版本与边界, "deliverable" covers 目标产物.
REQUIRED_TOP_KEYS = [
    "case_id", "status", "task", "deliverable", "facts_sources",
    "change_log", "version_boundary", "sources", "uncertainty",
    "errors", "degradations", "runtime",
]
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def read_json(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def empty_skeleton(case_id, status):
    return {
        "case_id": case_id,
        "status": status,
        "task": {
            "audience": None,
            "genre": None,
            "format": None,
            "word_limit": None,
            "brief": "",
        },
        "deliverable": {
            "paths": [],
            "format": "markdown",
            "title": None,
            "note": "目标产物：可编辑稿件/改稿/结构化写作产物，满足题目体裁、受众与格式。",
        },
        "facts_sources": [],
        "change_log": [],
        "version_boundary": {
            "pending_human": [],
            "gaps": [],
            "conflicts": [],
            "note": "版本与边界：修改差异、人工确认项、材料缺口与异常；结果可继续编辑。",
        },
        "sources": [],
        "uncertainty": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "start": datetime.now(timezone.utc).isoformat(),
            "end": None,
            "seconds": None,
            "model_requests_used": None,
            "model_requests_budget": 64,
            "time_budget_seconds": 900,
        },
    }


def deep_merge(base, update):
    """Merge update into base, filling missing keys but never overwriting values."""
    if not isinstance(base, dict) or not isinstance(update, dict):
        return base
    for k, v in update.items():
        if k not in base or base[k] is None:
            base[k] = v
        elif isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
    return base


def main():
    ap = argparse.ArgumentParser(description="Build result.json skeleton")
    ap.add_argument("--input", default="/input", help="input dir (default /input)")
    ap.add_argument("--work", default="/app/work", help="work dir (default /app/work)")
    ap.add_argument("--output", default="/app/output", help="output dir (default /app/output)")
    ap.add_argument("--case-id", default=None, help="override case_id")
    args = ap.parse_args()

    manifest_path = os.path.join(args.work, "input_manifest.json")
    manifest = read_json(manifest_path) if os.path.exists(manifest_path) else None

    case_id = args.case_id
    status = "partial"
    task_brief = ""

    if manifest is None:
        # Try reading /input/case.json directly as fallback.
        for fn in ("case.json", "task.md"):
            p = os.path.join(args.input, fn)
            if os.path.exists(p):
                data = read_json(p)
                if isinstance(data, dict) and data:
                    case_id = case_id or data.get("case_id") or data.get("id")
        if case_id is None:
            case_id = os.path.basename(os.path.normpath(args.input)) or "unknown"
    else:
        case_id = case_id or manifest.get("case_id")
        if not manifest.get("materials") and not manifest.get("facts_inventory"):
            status = "insufficient_evidence"
            task_brief = "未发现科研材料，无法进行有证据的改写。"
        else:
            cons = manifest.get("constraints") or {}
            task_brief = (manifest.get("task_text") or "")[:400]
            _ = cons  # constraints may be re-read by the agent from the manifest

    if not isinstance(case_id, str) or not case_id:
        case_id = "unknown"

    skeleton = empty_skeleton(case_id, status)
    if task_brief:
        skeleton["task"]["brief"] = task_brief
    if manifest and isinstance(manifest.get("constraints"), dict):
        cons = manifest["constraints"]
        mapping = {
            "audience_hint": "audience",
            "genre_hint": "genre",
            "format_hint": "format",
            "word_limit": "word_limit",
        }
        for src_key, dst_key in mapping.items():
            if src_key in cons and cons[src_key] is not None:
                skeleton["task"][dst_key] = cons[src_key]

    out_dir = args.output
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "result.json")

    if os.path.exists(out_path):
        existing = read_json(out_path)
        if isinstance(existing, dict):
            merged = deep_merge(existing, skeleton)
            # keep status honest: never downgrade ok/partial to skeleton default
            if existing.get("status") in ALLOWED_STATUS and (
                    existing["status"] == "ok" or existing["status"] == "insufficient_evidence"):
                merged["status"] = existing["status"]
            if not isinstance(merged.get("case_id"), str) or not merged["case_id"]:
                merged["case_id"] = case_id
            merged["case_id"] = merged["case_id"] or case_id
            skeleton = merged

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "result": out_path,
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "skeleton": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
