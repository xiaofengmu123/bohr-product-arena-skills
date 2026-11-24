#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检：校验 /app/output 交付物是否满足本题评分所需的最低完整性与可核验性。

纯标准库。退出码约定：
  0 —— 全部通过（或仅警告，非 --strict）
  1 —— 关键项失败（--strict 时任何 fail 也返回 1）

检查项与评分维度对应：
  任务完成度/完整性 —— result.json、mindmap.* 存在且非空
  结果可核验性       —— 关键节点/关系有证据（locator+quote）
  可靠体验/异常处理  —— result.json 合法、状态字段诚实、errors/degradations 存在
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

REQUIRED_RESULT_FIELDS = {
    "case_id", "status", "sources", "uncertainty", "change_log",
    "nodes", "edges", "evidence_map", "errors", "degradations",
}

EVIDENCE_TYPES = {"evidence", "result", "conclusion"}


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as e:  # noqa: BLE001 - 自检需捕获一切并报告
        return None, str(e)


def check(label, ok, detail=None, warn=False):
    return {"check": label, "ok": bool(ok), "detail": detail, "warn": warn}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    report = {"output": args.output, "checks": [], "warnings": [], "fails": []}
    fails = 0

    result_path = os.path.join(args.output, "result.json")
    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        report["checks"].append(check("result.json exists and parses", False, err))
        report["fails"].append("result.json")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["checks"].append(check("result.json exists and parses", True))

    # 1) 必备字段
    missing = sorted(k for k in REQUIRED_RESULT_FIELDS if k not in result)
    report["checks"].append(check("required top-level fields present", not missing,
                                  ("missing: " + ", ".join(missing)) if missing else None))
    if missing:
        report["fails"].append("missing-fields")
        fails += 1

    # 2) 状态诚实
    status = result.get("status")
    report["checks"].append(check("status in allowed set", status in ALLOWED_STATUS, status))
    if status not in ALLOWED_STATUS:
        report["fails"].append("invalid-status")
        fails += 1

    # 3) 节点/边结构
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    ok_node = all(isinstance(n, dict) and n.get("id") and n.get("label") for n in nodes)
    report["checks"].append(check("nodes have id+label", ok_node, f"{len(nodes)} nodes"))
    if not ok_node:
        report["fails"].append("node-structure")
        fails += 1
    ok_edge = all(isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids for e in edges)
    report["checks"].append(check("edges reference existing nodes", ok_edge, f"{len(edges)} edges"))
    if not ok_edge:
        report["fails"].append("edge-structure")
        fails += 1

    # 4) 状态与内容一致
    if status == "ok" and not nodes:
        report["checks"].append(check("status=ok implies non-empty nodes", False))
        report["fails"].append("status-content-mismatch")
        fails += 1
    else:
        report["checks"].append(check("status=ok implies non-empty nodes", True, status))

    # 5) 关键节点证据可追溯（警告级，提示而非阻断）
    evidence_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") in EVIDENCE_TYPES]
    lacking = [n.get("id") for n in evidence_nodes
               if not (isinstance(n.get("evidence"), list) and n.get("evidence"))]
    if lacking:
        report["warnings"].append({
            "check": "evidence-type nodes carry evidence", "ids": lacking,
            "hint": "关键证据/结论类节点应带 locator+quote 证据",
        })
    report["checks"].append(check("evidence-type nodes carry evidence", not lacking,
                                  (f"{len(lacking)} lacking: {', '.join(map(str, lacking[:8]))}") if lacking else None,
                                  warn=True))

    # 6) 可编辑与可预览产物
    artifacts = result.get("artifacts") or {}
    for name in ("mindmap_json", "mindmap_md", "mindmap_svg"):
        p = artifacts.get(name)
        exists = bool(p and os.path.exists(p))
        report["checks"].append(check(f"artifact {name} exists", exists, p))
        if not exists:
            report["fails"].append(name)
            fails += 1

    # 7) SVG 可解析
    svg_path = artifacts.get("mindmap_svg")
    if svg_path and os.path.exists(svg_path):
        try:
            ET.parse(svg_path)
            report["checks"].append(check("mindmap.svg is well-formed XML", True))
        except ET.ParseError as e:
            report["checks"].append(check("mindmap.svg is well-formed XML", False, str(e)))
            report["fails"].append("svg-parse")
            fails += 1

    report["passed"] = fails == 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if (args.strict and fails) else 0


if __name__ == "__main__":
    sys.exit(main())
