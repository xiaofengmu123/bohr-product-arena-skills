#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — 交付前校验 result.json / manuscript.md / evidence_map.json 的一致性与完整性。

用法:
    python3 -B scripts/validate.py --work /app/work --output /app/output
缺省: --work /app/work --output /app/output
退出码: 0=通过（含 warning）；1=硬失败。校验报告写 work/validation_report.json。
"""
import argparse
import json
import os
import re
import sys

STANDARD_FIELDS = [
    "status", "sources", "uncertainty", "change_log", "artifacts",
    "pending_human", "errors", "degradations", "runtime",
]
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
SECTION_HEADERS_ZH = ["研究背景", "相关工作", "研究空白", "贡献"]
SECTION_HEADERS_EN = ["background", "related work", "gap", "contribution"]
ANCHOR_RE = re.compile(r"\[S(\d+)\]")


def log(msg):
    sys.stderr.write(msg + "\n")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"__error__": str(e)}


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError as e:
        return None


def check_result(result, normalized, report):
    ok = True
    if "__error__" in result:
        report["errors"].append({"item": "result.json", "reason": result["__error__"]})
        return False
    if "case_id" not in result:
        report["errors"].append({"item": "result.json", "reason": "missing case_id"})
        ok = False
    status = result.get("status")
    if status not in VALID_STATUS:
        report["errors"].append({"item": "result.json", "reason": "invalid status: %r" % status})
        ok = False
    for f in STANDARD_FIELDS:
        if f not in result:
            report["errors"].append({"item": "result.json", "reason": "missing standard field: %s" % f})
            ok = False
    # 合同字段全部出现
    for f in (normalized.get("contract_fields") or []):
        if f not in result:
            report["errors"].append({"item": "result.json", "reason": "missing contract field: %s" % f})
            ok = False
    return ok


def check_manuscript(path, report):
    text = read_text(path)
    if text is None or not text.strip():
        report["errors"].append({"item": "manuscript.md", "reason": "missing or empty"})
        return False, set()
    has_zh = any(h in text for h in SECTION_HEADERS_ZH)
    has_en = all(h.lower() in text.lower() for h in SECTION_HEADERS_EN)
    if not (has_zh or has_en):
        report["warnings"].append({"item": "manuscript.md", "reason": "four-section headers not all found"})
    if "MATERIAL GAP" in text:
        report["warnings"].append({"item": "manuscript.md", "reason": "contains MATERIAL GAP placeholders"})
    anchors = set(int(m) for m in ANCHOR_RE.findall(text))
    return True, anchors


def check_evidence_map(path, anchors, report):
    em = load_json(path)
    if "__error__" in em:
        report["errors"].append({"item": "evidence_map.json", "reason": em["__error__"]})
        return False
    if not isinstance(em, dict):
        report["errors"].append({"item": "evidence_map.json", "reason": "not a dict"})
        return False
    ok = True
    anchors_obj = em.get("anchors", {}) if isinstance(em, dict) else {}
    if not isinstance(anchors_obj, dict):
        report["errors"].append({"item": "evidence_map.json", "reason": "anchors not a dict"})
        return False
    for s in sorted(anchors):
        key = "S%d" % s
        if key not in anchors_obj:
            report["errors"].append({"item": "evidence_map.json",
                                     "reason": "manuscript anchor [%s] missing in evidence_map" % key})
            ok = False
            continue
        a = anchors_obj[key]
        if not isinstance(a, dict):
            report["errors"].append({"item": key, "reason": "anchor not a dict"})
            ok = False
            continue
        if not a.get("locator"):
            report["errors"].append({"item": key, "reason": "anchor missing locator"})
            ok = False
        if not a.get("quote"):
            report["errors"].append({"item": key, "reason": "anchor missing verbatim quote"})
            ok = False
    # 孤儿锚点（evidence_map 有而正文无）仅 warning
    for key in anchors_obj:
        m = re.match(r"S(\d+)$", key)
        if m and int(m.group(1)) not in anchors:
            report["warnings"].append({"item": key, "reason": "orphan anchor not cited in manuscript"})
    return ok


def check_sources(result, report):
    for s in result.get("sources", []) or []:
        if not isinstance(s, dict):
            report["errors"].append({"item": "sources", "reason": "entry not a dict"})
            continue
        if not s.get("locator"):
            report["errors"].append({"item": "sources", "reason": "source missing locator"})
        if not s.get("quote"):
            report["errors"].append({"item": "sources", "reason": "source missing quote"})


def main(argv=None):
    p = argparse.ArgumentParser(description="validate output artifacts")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")
    args = p.parse_args(argv)

    report = {"errors": [], "warnings": []}
    normalized = load_json(os.path.join(args.work, "case_normalized.json")) or {}

    result = load_json(os.path.join(args.output, "result.json"))
    ok = check_result(result, normalized, report)

    man_ok, anchors = check_manuscript(os.path.join(args.output, "manuscript.md"), report)
    em_ok = check_evidence_map(os.path.join(args.output, "evidence_map.json"), anchors, report)

    if "__error__" not in result:
        check_sources(result, report)

    # 汇总
    hard = bool(report["errors"])
    if hard:
        report["verdict"] = "FAIL"
    else:
        report["verdict"] = "PASS" if not report["warnings"] else "PASS_WITH_WARNINGS"

    os.makedirs(args.work, exist_ok=True)
    with open(os.path.join(args.work, "validation_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    log("validate: %s  errors=%d warnings=%d"
        % (report["verdict"], len(report["errors"]), len(report["warnings"])))
    for e in report["errors"]:
        log("  ERROR %s: %s" % (e.get("item"), e.get("reason")))
    for w in report["warnings"]:
        log("  WARN  %s: %s" % (w.get("item"), w.get("reason")))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
