#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — 交付前自检，并把产物清单合并进 result.json。

仅使用 Python 标准库。逐条核验：
- result.json 可解析、含 case_id、status 合法
- 输出契约全部键原样在场
- 主产物存在且格式合法（FreeMind XML / SVG 可解析）
- 证据映射可解析、关系端点存在、关键节点证据挂接情况

退出码：result.json 缺失或不可解析 -> 1；否则 -> 0（软性问题仅告警）。

用法:
    python3 -B scripts/validate.py --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
KEY_KINDS = {"concept", "question", "method", "evidence", "conclusion",
             "task", "risk", "gap", "decision", "goal"}


def read_json(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read().decode("utf-8-sig")
        return json.loads(raw), None
    except (OSError, ValueError, TypeError) as e:
        return None, str(e)


def xml_ok(path):
    try:
        ET.parse(path)
        return True, None
    except (OSError, ET.ParseError) as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser(description="Validate skill output artifacts.")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    reports = []
    result_path = os.path.join(args.output, "result.json")
    result, err = read_json(result_path)
    if err:
        print("FAIL result.json unreadable: %s" % err)
        return 1
    reports.append(("PASS" if result else "FAIL", "result.json parses"))

    case_id = str(result.get("case_id", "") or "")
    reports.append(("PASS" if case_id else "FAIL",
                    "case_id present: %r" % case_id))

    status = result.get("status", "")
    reports.append(("PASS" if status in ALLOWED_STATUS else "FAIL",
                    "status valid: %r" % status))

    discovery, _ = read_json(os.path.join(args.work, "discovery.json"))
    contract_keys = (discovery or {}).get("contract_keys", [])
    missing_keys = [k for k in contract_keys if k not in result]
    reports.append(("PASS" if not missing_keys else "FAIL",
                    "contract keys all present (missing=%s)" % missing_keys))

    # 合并产物清单
    artifacts_manifest, _ = read_json(os.path.join(args.work, "artifacts.json"))
    if artifacts_manifest and isinstance(artifacts_manifest, dict):
        arts = artifacts_manifest.get("artifacts", [])
        result.setdefault("artifacts", {})
        if isinstance(result["artifacts"], dict):
            for a in arts:
                result["artifacts"][a["path"]] = {
                    "bytes": a.get("bytes"),
                    "sha256": a.get("sha256"),
                    "type": a.get("type"),
                }
        # 合并限制与待确认
        for k in ("limitations", "pending_human", "errors", "degradations"):
            vals = artifacts_manifest.get(k)
            if isinstance(vals, list) and vals:
                existing = result.setdefault(k, [])
                if isinstance(existing, list):
                    for v in vals:
                        if v not in existing:
                            existing.append(v)

    rt = result.setdefault("runtime", {})
    if isinstance(rt, dict):
        rt["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                       time.localtime())
    clog = result.setdefault("change_log", [])
    if isinstance(clog, list):
        clog.append({"t": rt.get("finished", ""), "action": "validate",
                     "detail": "self-check completed"})

    # 保守自动回填：仍为 null 的契约字段按字段名匹配产物路径
    autofill = []
    for k in contract_keys:
        if result.get(k) is not None:
            continue
        lk = str(k).lower()
        if "svg" in lk or "preview" in lk or "png" in lk:
            result[k] = "mindmap.svg"
        elif "evidence" in lk:
            result[k] = "evidence.json"
        elif "outline" in lk or lk.endswith("_md"):
            result[k] = "outline.md"
        elif "mindmap" in lk or "map" in lk or "mm" in lk:
            result[k] = "mindmap.mm"
        else:
            continue
        autofill.append(k)
    if autofill:
        clog.append({"t": rt.get("finished", ""), "action": "autofill_contract",
                     "detail": "filled null contract fields by name: %s"
                               % ", ".join(autofill)})

    # 产物存在性与格式
    expected = ["mindmap.mm", "mindmap.svg", "outline.md", "evidence.json"]
    for name in expected:
        p = os.path.join(args.output, name)
        if not os.path.isfile(p):
            reports.append(("FAIL", "artifact missing: %s" % name))
            continue
        if name.endswith(".mm") or name.endswith(".svg"):
            ok, e = xml_ok(p)
            reports.append(("PASS" if ok else "FAIL",
                            "%s well-formed XML%s" % (name,
                                                      (": %s" % e) if e else "")))
        else:
            reports.append(("PASS", "artifact exists: %s" % name))

    # 证据映射端点校验
    ev, _ = read_json(os.path.join(args.output, "evidence.json"))
    if ev and isinstance(ev.get("nodes"), dict) and isinstance(ev.get("relations"), list):
        ids = set(ev["nodes"].keys())
        bad = [r for r in ev["relations"]
               if r.get("from") not in ids or r.get("to") not in ids]
        reports.append(("PASS" if not bad else "FAIL",
                        "relation endpoints all resolve (bad=%d)" % len(bad)))
        n_ev = sum(1 for n in ev["nodes"].values()
                   if n.get("locator"))
        n_key = sum(1 for n in ev["nodes"].values()
                    if n.get("kind") in KEY_KINDS and n.get("locator"))
        n_key_total = sum(1 for n in ev["nodes"].values()
                          if n.get("kind") in KEY_KINDS)
        reports.append(("INFO", "evidence coverage: %d/%d nodes, key-node "
                       "%d/%d" % (n_ev, len(ev["nodes"]), n_key, n_key_total)))
    else:
        reports.append(("FAIL", "evidence.json missing/invalid node map"))

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    for tag, msg in reports:
        print("%s %s" % (tag, msg))

    has_fail = any(tag == "FAIL" for tag, _ in reports)
    if has_fail:
        print("RESULT has_fail=1")
        return 0 if case_id and status in ALLOWED_STATUS else 1
    print("RESULT ok status=%s case_id=%s" % (status, case_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
