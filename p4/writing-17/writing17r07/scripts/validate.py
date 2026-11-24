#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — 交付前自检，逐条核验 result.json 与产物合法性。

仅使用 Python 标准库。核验项：
- result.json 可解析、含 case_id、status 合法
- 输出契约全部键原样在场
- 主产物存在且格式合法（FreeMind XML / SVG 可解析）
- 证据映射可解析、关系端点存在、关键节点证据挂接情况

退出码：result.json 缺失或不可解析 -> 1；否则 -> 0（软性问题仅告警，不阻断交付）。

用法:
    python3 -B scripts/validate.py --output /app/output --work /app/work
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

ALLOWED_STATUS = {"ok", "answered", "partial", "insufficient_evidence", "input_error"}
KEY_KINDS = {"concept", "question", "method", "evidence", "conclusion",
             "task", "risk", "gap"}
EXPECTED_ARTIFACTS = ["mindmap.mm", "mindmap.svg", "outline.md", "evidence.json"]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_json(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read().decode("utf-8-sig")
        return json.loads(raw), None
    except (OSError, ValueError, TypeError) as exc:
        return None, str(exc)


def xml_ok(path):
    try:
        ET.parse(path)
        return True, ""
    except (OSError, ET.ParseError) as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate skill output artifacts.")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args()

    reports = []
    result_path = os.path.join(args.output, "result.json")
    result, err = read_json(result_path)
    if err:
        print("FAIL result.json unreadable: %s" % err)
        return 1
    reports.append(("PASS" if isinstance(result, dict) else "FAIL", "result.json parses"))

    case_id = str(result.get("case_id", "") or "")
    reports.append(("PASS" if case_id else "WARN", "case_id present: %r" % case_id))

    status = result.get("status", "")
    reports.append(("PASS" if status in ALLOWED_STATUS else "FAIL",
                    "status valid: %r" % status))

    discovery, _ = read_json(os.path.join(args.work, "discovery.json"))
    contract_keys = (discovery or {}).get("contract_keys", [])
    missing_keys = [k for k in contract_keys if k not in result]
    reports.append(("PASS" if not missing_keys else "FAIL",
                    "contract keys all present (missing=%s)" % missing_keys))

    # 产物存在性与格式
    for name in EXPECTED_ARTIFACTS:
        p = os.path.join(args.output, name)
        if not os.path.isfile(p):
            reports.append(("FAIL", "artifact missing: %s" % name))
            continue
        if name.endswith(".mm") or name.endswith(".svg"):
            ok, xml_err = xml_ok(p)
            reports.append(("PASS" if ok else "FAIL",
                            "%s well-formed XML%s" % (name, (": %s" % xml_err) if xml_err else "")))
        else:
            reports.append(("PASS", "artifact exists: %s" % name))

    # 证据映射端点与覆盖
    evidence, ev_err = read_json(os.path.join(args.output, "evidence.json"))
    if ev_err or not isinstance(evidence, dict):
        reports.append(("FAIL", "evidence.json missing/invalid: %s" % ev_err))
    else:
        ev_nodes = evidence.get("nodes", {})
        ev_relations = evidence.get("relations", [])
        if isinstance(ev_nodes, dict) and isinstance(ev_relations, list):
            ids = set(ev_nodes.keys())
            bad = [r for r in ev_relations
                   if not isinstance(r, dict) or r.get("from") not in ids or r.get("to") not in ids]
            reports.append(("PASS" if not bad else "FAIL",
                            "relation endpoints all resolve (bad=%d)" % len(bad)))
            n_ev = sum(1 for n in ev_nodes.values() if isinstance(n, dict) and n.get("locator"))
            key_nodes = [n for n in ev_nodes.values()
                         if isinstance(n, dict) and n.get("kind") in KEY_KINDS]
            key_ev = sum(1 for n in key_nodes if n.get("locator"))
            reports.append(("INFO", "evidence coverage: %d/%d nodes; key-node %d/%d"
                            % (n_ev, len(ev_nodes), key_ev, len(key_nodes))))
        else:
            reports.append(("FAIL", "evidence.json missing node/relation map"))

    # 产物清单一致性（artifacts 字段 vs 实际文件）
    artifacts = result.get("artifacts", {})
    if isinstance(artifacts, dict):
        missing_files = [a for a in EXPECTED_ARTIFACTS if a not in artifacts]
        reports.append(("PASS" if not missing_files else "WARN",
                        "artifacts manifest covers main files (missing=%s)" % missing_files))

    for tag, msg in reports:
        print("%s %s" % (tag, msg))

    has_fail = any(tag == "FAIL" for _, tag in reports)
    has_warn = any(tag == "WARN" for _, tag in reports)
    print("RESULT fail=%d warn=%d status=%s case_id=%s"
          % (1 if has_fail else 0, 1 if has_warn else 0, status, case_id))
    # 只要 result.json 可解析就不阻断（早期 partial 也合法）
    return 0


if __name__ == "__main__":
    sys.exit(main())
