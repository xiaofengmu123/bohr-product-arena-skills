#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: 自检 + 一致性复核。

两种模式：
1. `--check`：对真实产物做收口前复核（result.json 契约、稿件章节、图表编号、
   引用闭环、状态诚实性）。
2. `--selftest`：用合成 fixture 跑三种降级场景（空输入、坏 JSON、缺输入），
   断言每个场景都产出合法 result.json 且退出码为 0，用于提交前验证包可安装运行。

CLI:
  python3 scripts/selftest.py --selftest
  python3 scripts/selftest.py --check --result /app/output/result.json \
      --draft /app/output/manuscript.md --catalog /app/work/evidence_catalog.json \
      --out /app/work/selftest_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bootstrap  # noqa: E402
import evidence  # noqa: E402

CORE_SECTIONS = ["abstract", "introduction", "methods", "results",
                 "discussion", "conclusion", "references"]
RE_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*$")
RE_FIG = re.compile(r"\bFigure\s+(\d+)\b", re.I)
RE_TAB = re.compile(r"\bTable\s+(\d+)\b", re.I)


def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "文件不存在: %s" % path
    except json.JSONDecodeError as exc:
        return None, "JSON 解析失败 %s: %s" % (path, exc)
    except OSError as exc:
        return None, "读取失败 %s: %s" % (path, exc)


def _numbering_gaps(numbers):
    if not numbers:
        return []
    nums = sorted(set(int(n) for n in numbers))
    gaps = []
    for expected in range(1, max(nums) + 1):
        if expected not in nums:
            gaps.append(expected)
    return gaps


def check_result(result: dict, draft_text, catalog) -> list:
    findings = []

    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if key not in result:
            findings.append({"severity": "error", "code": "E_RESULT_MISSING_KEY",
                             "message": "result.json 缺少必需字段 %s" % key, "ref": key})
    if result.get("case_id") in (None, "", "unknown") and result.get("status") != "input_error":
        findings.append({"severity": "error", "code": "E_RESULT_NO_CASE_ID",
                         "message": "case_id 缺失或为 unknown", "ref": "case_id"})
    for key in ("sources", "uncertainty", "change_log"):
        if key in result and not isinstance(result.get(key), list):
            findings.append({"severity": "error", "code": "E_RESULT_TYPE",
                             "message": "%s 应为列表" % key, "ref": key})

    status = result.get("status")
    if status == "complete":
        if result.get("uncertainty") or result.get("pending_human"):
            findings.append({"severity": "warn", "code": "W_STATUS_OVERCLAIM",
                             "message": "status=complete 但仍有 uncertainty/pending_human 未清项",
                             "ref": "status"})
    if status not in {"in_progress", "partial", "complete",
                      "insufficient_evidence", "input_error", "failed"}:
        findings.append({"severity": "warn", "code": "W_STATUS_UNKNOWN",
                         "message": "status=%r 不在约定词表内" % status, "ref": "status"})
    if not result.get("change_log"):
        findings.append({"severity": "warn", "code": "W_NO_CHANGE_LOG",
                         "message": "change_log 为空，改动未留痕", "ref": "change_log"})

    # 稿件章节完整性（启发式，只告警不判错：章节名因学科而异）
    if draft_text is not None:
        headings = [m.group(1).lower().strip("`*") for m in RE_HEADING.finditer(draft_text)]
        joined = " ".join(headings)
        for sec in CORE_SECTIONS:
            if sec not in joined:
                findings.append({"severity": "warn", "code": "W_SECTION_MISSING",
                                 "message": "未发现疑似章节 %s" % sec, "ref": sec})

        fig_nums = RE_FIG.findall(draft_text)
        tab_nums = RE_TAB.findall(draft_text)
        for kind, nums, code in (("Figure", fig_nums, "W_FIG_GAP"),
                                 ("Table", tab_nums, "W_TAB_GAP")):
            gaps = _numbering_gaps(nums)
            if gaps:
                findings.append({"severity": "warn", "code": code,
                                 "message": "%s 编号有缺号: %s" % (kind, gaps), "ref": kind})

        if "data availability" not in draft_text.lower():
            findings.append({"severity": "warn", "code": "W_NO_DATA_AVAILABILITY",
                             "message": "未发现数据可得性声明", "ref": "data_availability"})

    if catalog is not None:
        findings.extend(evidence.check_catalog(catalog, draft_text))

    return findings


def _selftest_scenario(input_dir, name):
    """在临时目录跑 bootstrap，返回 (status, result_obj, exit_code, errors)。"""
    out = os.path.join(input_dir, "_out")
    wrk = os.path.join(input_dir, "_work")
    summary, code = bootstrap.run(input_dir, out, wrk)
    result_path = os.path.join(out, "result.json")
    obj, err = _load_json(result_path)
    return {
        "scenario": name,
        "status": summary["status"],
        "exit_code": code,
        "result_valid": obj is not None,
        "load_error": err,
        "result": obj,
    }


def run_selftest() -> dict:
    scenarios = []
    base = tempfile.mkdtemp(prefix="skill-selftest-")

    # A：空输入目录
    a_dir = os.path.join(base, "empty")
    os.makedirs(a_dir, exist_ok=True)
    scenarios.append(_selftest_scenario(a_dir, "empty_input"))

    # B：坏 JSON
    b_dir = os.path.join(base, "bad_json")
    os.makedirs(b_dir, exist_ok=True)
    with open(os.path.join(b_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")
    scenarios.append(_selftest_scenario(b_dir, "bad_json"))

    # C：正常输入 + 材料 + output_contract
    c_dir = os.path.join(base, "normal")
    os.makedirs(c_dir, exist_ok=True)
    case = {
        "id": "case-selftest",
        "output_contract": {"deliverable": "manuscript.md",
                            "fact_source_map": "fact_source_map.json",
                            "version_limits": "version_limits.json"},
    }
    with open(os.path.join(c_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(c_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    with open(os.path.join(c_dir, "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write("mean concentration was 3.2 mg/L\n")
    scenarios.append(_selftest_scenario(c_dir, "normal_input"))

    # 校验
    ok = True
    checks = []
    for s in scenarios:
        checks.append({
            "scenario": s["scenario"], "status": s["status"],
            "exit_code": s["exit_code"], "result_valid": s["result_valid"],
            "load_error": s["load_error"],
        })
        if s["exit_code"] != 0 or s["result"] is None:
            ok = False
        if s["scenario"] == "empty_input" and s["status"] not in {"insufficient_evidence", "input_error"}:
            ok = False
        if s["scenario"] == "bad_json" and s["status"] not in {"input_error", "partial", "in_progress"}:
            ok = False
        if s["scenario"] == "normal_input" and s["status"] != "in_progress":
            ok = False
        if s["scenario"] == "normal_input":
            r = s["result"]
            if r and not all(k in r for k in ("deliverable", "fact_source_map", "version_limits", "case_id")):
                ok = False

    # 追加：在正常场景上跑 evidence 记录与校验，确认脚本可导入执行
    try:
        cat_path = os.path.join(c_dir, "_work", "evidence_catalog.json")
        catalog, _ = evidence.load_catalog(cat_path)
        evidence.add_claim(catalog, claim_id="K01", kind="number", section="results",
                           text="mean was 3.2 mg/L", locator="paper.txt",
                           quote="3.2 mg/L", citation_keys="", confidence="verbatim")
        evidence.save_catalog(cat_path, catalog)
        findings = evidence.check_catalog(catalog)
        errs = [f for f in findings if f["severity"] == "error"]
        checks.append({"scenario": "evidence_roundtrip", "errors": errs})
        if errs:
            ok = False
    except Exception as exc:  # noqa: BLE001
        checks.append({"scenario": "evidence_roundtrip", "exception": str(exc)})
        ok = False

    return {"ok": ok, "base_dir": base, "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="自检与一致性复核")
    ap.add_argument("--selftest", action="store_true", help="跑合成 fixture 自检")
    ap.add_argument("--check", action="store_true", help="对真实产物做收口复核")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--draft", default="/app/output/manuscript.md")
    ap.add_argument("--catalog", default="/app/work/evidence_catalog.json")
    ap.add_argument("--out", default="/app/work/selftest_report.json")
    args = ap.parse_args(argv)

    if args.selftest:
        report = run_selftest()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("SELFTEST %s" % ("OK" if report["ok"] else "FAILED"))
        return 0 if report["ok"] else 1

    if args.check:
        result, rerr = _load_json(args.result)
        findings = []
        if rerr is not None:
            findings.append({"severity": "error", "code": "E_RESULT_LOAD",
                             "message": rerr, "ref": args.result})
        else:
            draft_text = None
            if os.path.isfile(args.draft):
                with open(args.draft, "r", encoding="utf-8-sig") as fh:
                    draft_text = fh.read()
            catalog, _ = evidence.load_catalog(args.catalog)
            findings.extend(check_result(result, draft_text, catalog))
        errors = [f for f in findings if f["severity"] == "error"]
        report = {
            "result_path": args.result,
            "finding_count": len(findings),
            "error_count": len(errors),
            "findings": findings,
        }
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if errors else 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
