#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the produced /app/output deliverable (stdlib only).

Validates result.json structure and the artifact files. Prints one [PASS] /
[FAIL] / [WARN] line per check and a final summary. Exit code is 0 whenever the
check itself completes; it is informational, so a failing check does not crash
the harness. The agent should read the FAIL lines and fix them before the
budget expires.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


def _stdout(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate /app/output deliverable.")
    ap.add_argument("--output", default="/app/output", help="output directory")
    args = ap.parse_args(argv)
    out = Path(args.output)
    fails, warns, passes = [], [], []

    def check(cond, name, detail=""):
        if cond:
            passes.append(name)
            _stdout("[PASS] %s" % name)
        else:
            fails.append(name)
            _stdout("[FAIL] %s %s" % (name, detail))

    rp = out / "result.json"
    if not rp.exists():
        _stdout("[FAIL] result.json 不存在")
        _stdout("SUMMARY pass=%d fail=%d warn=%d" % (0, 1, 0))
        return 1

    try:
        result = json.loads(rp.read_text(encoding="utf-8-sig"))
        check(True, "result.json 是合法 JSON")
    except Exception as e:
        _stdout("[FAIL] result.json 不是合法 JSON: %s" % e)
        _stdout("SUMMARY pass=0 fail=1 warn=0")
        return 1

    check(bool((result.get("case_id") or "").strip()), "case_id 存在且非空")
    check(result.get("status") in ALLOWED_STATUS,
          "status 取值合法", "got=%r" % result.get("status"))
    check(isinstance(result.get("sources"), list), "sources 是数组")
    check(isinstance(result.get("uncertainty"), list), "uncertainty 是数组")
    check(isinstance(result.get("change_log"), list) and len(result.get("change_log", [])) > 0,
          "change_log 非空")
    contract = result.get("output_contract")
    if isinstance(contract, dict) and contract:
        missing = [k for k in contract if k not in result]
        check(not missing, "output_contract 字段已全部回填",
              "missing=%s" % ",".join(missing))
    else:
        warns.append("未找到 output_contract 副本")

    artifacts = result.get("artifacts") or {}
    for key in ("mindmap_md", "mindmap_json", "mindmap_mm", "mindmap_svg"):
        rel = artifacts.get(key)
        if rel:
            p = out / rel
            check(p.exists() and p.stat().st_size > 0, "%s 存在且非空" % key)
        else:
            warns.append("%s 未提供" % key)

    mm = out / (artifacts.get("mindmap_mm") or "mindmap.mm")
    if mm.exists():
        try:
            ET.parse(str(mm))
            check(True, "mindmap.mm 是合法 XML（可导入 FreeMind/XMind）")
        except Exception as e:
            check(False, "mindmap.mm XML 解析", str(e))
    svg = out / (artifacts.get("mindmap_svg") or "mindmap.svg")
    if svg.exists():
        head = svg.read_bytes()[:300]
        check(b"<svg" in head, "mindmap.svg 有 <svg> 根元素")

    md = out / (artifacts.get("mindmap_md") or "mindmap.md")
    if md.exists():
        txt = md.read_text(encoding="utf-8", errors="replace")
        check("证据映射" in txt, "mindmap.md 含证据映射小节")
        check("不确定性" in txt or "不确定" in txt, "mindmap.md 含不确定性小节")

    ev = result.get("evidence_map")
    if isinstance(ev, list):
        check(True, "evidence_map 是数组（%d 条）" % len(ev))
    else:
        warns.append("evidence_map 缺失或非数组")

    if result.get("status") in ("insufficient_evidence", "input_error"):
        check(bool(result.get("limitations") or result.get("pending_human")),
              "不足状态已声明限制或待办")

    _stdout("SUMMARY pass=%d fail=%d warn=%d" % (len(passes), len(fails), len(warns)))
    for w in warns:
        _stdout("[WARN] " + w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
