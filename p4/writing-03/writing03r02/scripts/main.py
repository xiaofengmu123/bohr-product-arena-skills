#!/usr/bin/env python3
"""main — 入口编排器（仅标准库）。

流程（对应 SKILL.md 阶段预算，900s / 64 请求）：
  P0 盘点(0-60s)：发现输入、读 case.json/task.md、解析 output_contract。
  P1 早落盘(≤180s 前)：立即写 partial result.json 骨架，之后只做增量合并。
  P2 规格反推(60-300s)：模板 docx/tex 反推格式规格 + 原稿内容单元盘点。
  P3 自检(720-900s)：契约核验 + 摘录可定位性 + 内容保真度。

语义工作（正文改写、样式绑定、图表题注、交叉引用、参考文献排版、导出）
由遵循 SKILL.md 的 Agent 完成；本脚本提供确定性的脚手架、证据链与自检，
并把每一步增量写回 result.json（幂等、只增不重建）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import core_impl as ci

RESULT_NAME = "result.json"
EARLY_DEADLINE_S = 180  # 前 3 分钟必须落盘骨架
TOTAL_BUDGET_S = 900
TOTAL_REQUESTS = 64
TOKENS_PER_RESPONSE = 16384


def _report(level: str, msg: str) -> None:
    print("[%s] %s" % (level, msg), file=sys.stderr)


def run_phases(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, RESULT_NAME)

    # ---- P0 盘点 ---------------------------------------------------------
    disc = ci.discover_input(args.input)
    case_obj, case_degs = (None, [])
    if disc.get("case"):
        case_obj, case_degs = ci.load_json_tolerant(disc["case"])
    case_id = None
    output_contract = None
    contract_fields = None
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or case_obj.get("caseId")
        output_contract = case_obj.get("output_contract")
        if isinstance(output_contract, dict):
            contract_fields = list(output_contract.keys())

    # ---- P1 早落盘（幂等；若已有 result.json 则只做增量合并，不重建）----
    if os.path.exists(result_path):
        skeleton = ci.load_json_tolerant(result_path)[0]
        if not isinstance(skeleton, dict) or skeleton.get("case_id") != case_id:
            skeleton = ci.build_skeleton(case_id, output_contract, contract_fields)
            ci.write_json(result_path, skeleton)
    else:
        skeleton = ci.build_skeleton(case_id, output_contract, contract_fields)
        ci.write_json(result_path, skeleton)
    ci.merge_result(result_path, {"discovered": disc.get("files", [])},
                    "early_skeleton", "P1 早落盘（≤%ds）" % EARLY_DEADLINE_S)
    _report("ok", "P1 骨架已落盘：%s" % result_path)

    # ---- P2 规格反推与盘点 ------------------------------------------------
    spec_checklist = {"template_specs": [], "inventories": [], "checklist": []}
    for f in disc.get("files", []):
        kind = f["kind"]
        if kind == "docx":
            spec = ci.extract_docx_spec(f["path"])
            inv = ci.inventory_docx(f["path"])
            spec_checklist["template_specs"].append({"path": f["path"], "name": f["name"], "spec": spec})
            spec_checklist["inventories"].append({"path": f["path"], "name": f["name"], "inventory": inv})
            spec_checklist["checklist"].append(ci.build_checklist(spec, inv))
        elif kind == "tex":
            spec = ci.extract_tex_spec(f["path"])
            inv = ci.inventory_tex(f["path"])
            spec_checklist["template_specs"].append({"path": f["path"], "name": f["name"], "spec": spec})
            spec_checklist["inventories"].append({"path": f["path"], "name": f["name"], "inventory": inv})
            spec_checklist["checklist"].append(ci.build_checklist(spec, inv))
        elif kind in ("text", "office"):
            spec_checklist["inventories"].append(
                {"path": f["path"], "name": f["name"], "inventory": ci.inventory_file(f["path"], kind)})

    work_plan_path = os.path.join(args.work, "work_plan.json")
    ci.write_json(work_plan_path, {
        "case_id": case_id,
        "budget": {"seconds": TOTAL_BUDGET_S, "requests": TOTAL_REQUESTS,
                   "tokens_per_response": TOKENS_PER_RESPONSE,
                   "early_deadline_s": EARLY_DEADLINE_S},
        "input": disc["files"],
        "spec_checklist": spec_checklist,
        "degradations": disc.get("degradations", []) + case_degs,
        "generated": ci.utcnow_iso(),
    })
    ci.merge_result(result_path, {"work_plan_path": work_plan_path,
                                  "degradations": disc.get("degradations", []) + case_degs},
                    "inventory", "P2 规格反推/单元盘点完成")
    _report("ok", "P2 工作清单：%s" % work_plan_path)

    # ---- P3 契约自检 ------------------------------------------------------
    report_path = os.path.join(args.output, "verification_report.json")
    report = verify_contract(result_path, disc.get("files", []))
    ci.write_json(report_path, report)
    elapsed = int(time.monotonic() - t0)
    cur = ci.load_json_tolerant(result_path)[0] or {}
    rt = cur.get("runtime") if isinstance(cur.get("runtime"), dict) else {}
    rt["seconds"] = elapsed
    rt["requests_used"] = rt.get("requests_used", 0)
    ci.merge_result(result_path, {"verification_report_path": report_path,
                                  "verification": report,
                                  "runtime": rt},
                    "verify", "P3 契约自检完成（hard_errors=%d）" % len(report.get("hard_errors", [])))
    _report("ok" if not report.get("hard_errors") else "warn",
            "P3 自检完成：hard_errors=%d" % len(report.get("hard_errors", [])))

    print(json.dumps({"ok": True, "case_id": case_id, "status": skeleton.get("status", "partial"),
                      "result": result_path, "work_plan": work_plan_path,
                      "verification": report_path}, ensure_ascii=False))
    return 2 if report.get("hard_errors") else 0


def verify_contract(result_path: str, files: list[dict]) -> dict:
    """契约 + 摘录可定位性 + 内容保真度自检。返回报告 dict。"""
    result, degs = ci.load_json_tolerant(result_path)
    hard_errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(result, dict):
        return {"ok": False, "hard_errors": ["result_not_json_object"],
                "warnings": [], "degradations": degs}
    if not result.get("case_id"):
        hard_errors.append("missing_case_id")
    for key in ("status", "sources", "uncertainty", "change_log"):
        if key not in result:
            hard_errors.append("missing_" + key)
    if "deliverable" not in result:
        warnings.append("missing_deliverable_field")

    # 摘录可定位性：fact_source_map 每条 quote 必须在某材料文本中出现
    locatable = 0
    unlocatable = 0
    fsm = result.get("fact_source_map")
    if isinstance(fsm, list):
        corpus: list[str] = []
        for f in files:
            kind = f.get("kind")
            if kind in ("text", "tex"):
                txt, _d = ci.read_text_tolerant(f["path"])
                corpus.append(txt)
            elif kind == "docx":
                txt, _d = ci.extract_docx_text(f["path"])
                corpus.append(txt)
        for item in fsm:
            if not isinstance(item, dict):
                continue
            quote = (item.get("quote") or "").strip()
            loc = item.get("locator") or ""
            if not quote:
                warnings.append("claim_missing_quote:%s" % item.get("claim_id", "?"))
                continue
            if not loc:
                warnings.append("claim_missing_locator:%s" % item.get("claim_id", "?"))
            found = any(quote in c for c in corpus if c)
            if found:
                locatable += 1
            else:
                unlocatable += 1
                warnings.append("quote_not_locatable:%s" % item.get("claim_id", "?"))
    report = {"ok": not hard_errors, "hard_errors": hard_errors,
              "warnings": warnings, "degradations": degs,
              "quote_verification": {"locatable": locatable, "unlocatable": unlocatable}}
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="学术格式与模板适配 Skill 入口")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--skeleton-only", action="store_true", help="只落盘骨架（P1）")
    ap.add_argument("--verify-only", action="store_true", help="只做契约自检")
    args = ap.parse_args(argv)

    if args.skeleton_only:
        ci.main(["--input", args.input, "--out", args.output,
                 "--work", args.work, "--skeleton-only"])
        return 0
    if args.verify_only:
        result_path = os.path.join(args.output, RESULT_NAME)
        disc = ci.discover_input(args.input)
        report = verify_contract(result_path, disc.get("files", []))
        ci.write_json(os.path.join(args.output, "verification_report.json"), report)
        print(json.dumps(report, ensure_ascii=False))
        return 2 if report.get("hard_errors") else 0
    return run_phases(args)


if __name__ == "__main__":
    sys.exit(main())
