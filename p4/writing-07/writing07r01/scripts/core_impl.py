#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emit_skeleton.py — 落盘 partial 结果骨架（result.json + 四段 manuscript.md + 空佐证文件）。

用法:
    python3 -B scripts/emit_skeleton.py --work /app/work --output /app/output
缺省: --work /app/work --output /app/output
幂等性: 若目标文件已存在且已非骨架（result.json 的 status 不是 partial 且文件非空），
        默认跳过，避免覆盖 Agent 的增量成果；--force 强制重写。
"""
import argparse
import json
import os
import sys

MANUSCRIPT_SKELETON = """# 引言与相关工作（草稿骨架）

> 状态：partial 骨架。以下为占位，待依据材料填充；未填充处标 MATERIAL GAP，不编造。

## 研究背景

MATERIAL GAP：待从材料中提取领域脉络、具体问题与技术约束，背景须指向真实未解决问题。

## 相关工作

MATERIAL GAP：待按问题设定/方法路线/数据与评价方式分组比较，不做逐篇摘要拼接。

## 研究空白

MATERIAL GAP：待由已引文献的共同限制、未覆盖场景或冲突结论推出，并写明本文如何填补。

## 贡献

MATERIAL GAP：待与方法、图表、实验结果一一对应，不夸大适用范围。
"""

EVIDENCE_MAP_SKELETON = {
    "schema": "evidence_map_v2",
    "anchors": {},
    "citation_map": {},
    "group_rationale": {},
    "gap_derivation": {},
    "contribution_map": {},
}


def log(msg):
    sys.stderr.write(msg + "\n")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def dump_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def build_result(manifest, normalized):
    r = {}
    # 1) 合同字段优先（原样回填字段名，缺省值）
    fields = normalized.get("contract_fields", []) or []
    defaults = normalized.get("contract_defaults", {}) or {}
    for f in fields:
        r[f] = defaults.get(f)
    # 2) 标准字段补位（不与合同字段重名时）
    std = {
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "artifacts": {
            "manuscript": "manuscript.md",
            "evidence_map": "evidence_map.json",
            "change_log": "change_log.json",
        },
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"seconds_elapsed": None, "model_requests_used": None},
    }
    for k, v in std.items():
        if k not in r:
            r[k] = v
    # 3) 强制关键字段
    r["case_id"] = normalized.get("case_id")
    r["status"] = determine_skeleton_status(manifest)
    # 4) 继承扫描期发现的降级/错误
    if manifest:
        for item in manifest.get("degradations", []) or []:
            r["degradations"].append(item)
        for item in manifest.get("errors", []) or []:
            r["errors"].append(item)
    return r


def determine_skeleton_status(manifest):
    """空输入/无材料 -> insufficient_evidence；否则 partial 骨架。"""
    has_case = bool(manifest.get("case_json_found"))
    material_count = sum(
        1 for f in (manifest.get("files") or [])
        if os.path.basename(f.get("relpath", "")) not in ("case.json", "task.md")
    )
    if not has_case and material_count == 0:
        return "insufficient_evidence"
    return "partial"


def main(argv=None):
    p = argparse.ArgumentParser(description="emit partial skeleton artifacts")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing artifacts even if substantive")
    args = p.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)

    manifest = load_json(os.path.join(args.work, "input_manifest.json")) or {}
    normalized = load_json(os.path.join(args.work, "case_normalized.json")) or {}

    result_path = os.path.join(args.output, "result.json")
    manuscript_path = os.path.join(args.output, "manuscript.md")
    evidence_path = os.path.join(args.output, "evidence_map.json")
    changelog_path = os.path.join(args.output, "change_log.json")

    # 幂等保护：已有 result.json 时不覆盖（Agent 只应运行一次骨架，之后用编辑工具增量改进）
    if not args.force and os.path.exists(result_path):
        log("result.json 已存在，跳过骨架落盘（如需重写加 --force）")
        return 0

    result = build_result(manifest, normalized)
    dump_json(result_path, result)

    if not os.path.exists(manuscript_path) or args.force:
        with open(manuscript_path, "w", encoding="utf-8") as f:
            f.write(MANUSCRIPT_SKELETON)
    if not os.path.exists(evidence_path) or args.force:
        dump_json(evidence_path, EVIDENCE_MAP_SKELETON)
    if not os.path.exists(changelog_path) or args.force:
        dump_json(changelog_path, [])

    log("skeleton written: %s (status=%s)" % (result_path, result["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
