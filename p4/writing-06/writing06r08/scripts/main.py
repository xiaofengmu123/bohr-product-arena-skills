#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文结构与逻辑优化 Skill 入口。

用法（相对路径，工作目录为 Skill 根目录）：
  python scripts/main.py --init          # 落盘 result.json 骨架
  python scripts/main.py --scan          # 盘点材料 → /app/work/evidence.json
  python scripts/main.py --full          # init + scan + 诊断候选，落盘 result.json
  python scripts/main.py --validate      # 校验已有 result.json 契约完整性
  python scripts/main.py --help

可覆盖参数：--case / --input / --work / --out（对应环境变量同上）。

仅依赖 Python 标准库。环境变量可覆盖默认路径：
  CASE_JSON / TASK_MD / INPUT_DIR / WORK_DIR / OUTPUT_DIR / RESULT_JSON
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C

DEFAULTS = {
    "case_json": "/input/case.json",
    "task_md": "/input/task.md",
    "input_dir": "/input",
    "work_dir": "/app/work",
    "output_dir": "/app/output",
    "result_json": "/app/output/result.json",
}
_ENV_MAP = [("case_json", "CASE_JSON"), ("task_md", "TASK_MD"),
            ("input_dir", "INPUT_DIR"), ("work_dir", "WORK_DIR"),
            ("output_dir", "OUTPUT_DIR"), ("result_json", "RESULT_JSON")]


def paths():
    d = dict(DEFAULTS)
    for key, env in _ENV_MAP:
        val = os.environ.get(env)
        if val:
            d[key] = val
    return d


def load_case(p):
    return C.load_case(p)


def resolve_contract_fields(case):
    """从 case.json 读取 output_contract 字段名；无则用默认契约。"""
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            keys = [k for k in oc.keys()]
            if keys:
                return keys, oc
        if isinstance(oc, list) and oc:
            return [str(k) for k in oc], {}
    return C.DEFAULT_CONTRACT_FIELDS, {}


def build_scan_payload(case, p, materials, degradations):
    """从盘点材料生成候选锚点 payload（确定性）。"""
    payload = {"case_id": case.get("case_id") or case.get("id") or "unknown",
               "materials": [], "degradations": list(degradations),
               "anchors": {"sections": [], "sentences": [], "claims": [],
                           "evidence": [], "redundancy_pairs": [],
                           "conclusion_boundary": {},
                           "questions": []}}
    text_sources = []
    for m in materials:
        payload["materials"].append({"name": m["name"], "kind": m["kind"],
                                     "ext": m["ext"], "size": m["size"],
                                     "path": m["path"]})
        if m["kind"] == "text" and m.get("text"):
            text_sources.append(m["text"])
    if text_sources:
        # 只对最长文本（最可能是稿件正文）做完整结构扫描，其余文本仅计数
        main = max(text_sources, key=len)
        anchors = C.extract_anchors(main)
        payload["anchors"]["sections"] = anchors["sections"]
        payload["anchors"]["sentences"] = anchors["sentences"]
        payload["anchors"]["claims"] = anchors["claims"]
        payload["anchors"]["evidence"] = anchors["evidence"]
        payload["anchors"]["redundancy_pairs"] = C.detect_redundancy(
            anchors["sentences"])
        # 结论边界：聚合 conclusion/discussion 章节正文
        concl = C.join_sections_bodies(anchors["sections"], main,
                                       ("conclusion", "discussion", "结论", "讨论"))
        if concl.strip():
            payload["anchors"]["conclusion_boundary"] = C.check_conclusion_boundary(concl)
        # 研究问题句：聚合 abstract/intro/background 章节正文
        intro = C.join_sections_bodies(anchors["sections"], main,
                                       ("abstract", "intro", "background",
                                        "摘要", "引言", "背景"))
        if intro.strip():
            payload["anchors"]["questions"] = C.extract_questions(intro)
    return payload


def cmd_init(p):
    case, err = load_case(p["case_json"])
    fields, _ = resolve_contract_fields(case if isinstance(case, dict) else {})
    if not isinstance(case, dict):
        case = {}
    result = C.build_skeleton(case, contract_fields=fields)
    result["status"] = "partial" if err and err not in ("empty",) else "in_progress"
    if err and err not in ("empty",):
        result["errors"].append("case_json_load: %s" % err)
    C.write_json(p["result_json"], result)
    return result


def cmd_scan(p):
    os.makedirs(p["work_dir"], exist_ok=True)
    files, degradations = C.discover_materials(p["input_dir"])
    case, err = load_case(p["case_json"])
    if not isinstance(case, dict):
        case = {}
    payload = build_scan_payload(case, p, files, degradations)
    payload["case_json_err"] = err if err not in ("ok", "empty") else None
    out = os.path.join(p["work_dir"], "evidence.json")
    C.write_json(out, payload)
    return payload


def cmd_full(p):
    os.makedirs(p["output_dir"], exist_ok=True)
    os.makedirs(p["work_dir"], exist_ok=True)
    files, degradations = C.discover_materials(p["input_dir"])
    case, err = load_case(p["case_json"])
    if not isinstance(case, dict):
        case = {}
    fields, oc = resolve_contract_fields(case)
    result = C.build_skeleton(case, contract_fields=fields)
    payload = build_scan_payload(case, p, files, degradations)
    C.write_json(os.path.join(p["work_dir"], "evidence.json"), payload)
    # 诊断字段先落六类默认状态（不生成结论，仅状态标记）
    result["diagnosis"] = C.six_dimension_status()
    result["edit_plan"] = {"actions": []}
    result["claim_evidence_map"] = {"claims": []}
    result["source_map"] = {"entries": []}
    result["version_boundary"] = {"diff": [], "pending_human": [],
                                  "material_gaps": [], "anomalies": []}
    # sources：把候选论断写入，逐条带 locator + 逐字 quote + 低置信度标记
    for i, cl in enumerate(payload["anchors"]["claims"]):
        result["sources"].append({
            "claim_id": "CLM-%04d" % (i + 1),
            "claim": cl["quote"],
            "locator": cl["locator"],
            "quote": cl["quote"],
            "source_ref": cl["locator"],
            "confidence": "candidate_unverified",
        })
    result["degradations"] = list(degradations)
    if err and err not in ("ok", "empty"):
        result["errors"].append("case_json_load: %s" % err)
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({"id": "U-INPUT-EMPTY",
                                      "item": "输入目录无可用材料",
                                      "reason": "无法做结构诊断",
                                      "fallback": "交付骨架并说明材料缺口"})
    elif not payload["anchors"]["sections"] and not payload["anchors"]["sentences"]:
        result["status"] = "insufficient_evidence"
    else:
        result["status"] = "partial"
    result["change_log"].append({"time": C._now_iso(),
                                 "action": "scan_and_anchor_candidates",
                                 "status": result["status"]})
    result["runtime"]["finished_at"] = C._now_iso()
    C.write_json(p["result_json"], result)
    return result


def cmd_validate(p):
    text = C.safe_read(p["result_json"])
    data, err = C.parse_json_tolerant(text)
    report = {"result_json": p["result_json"], "valid_json": err in ("ok", "truncated_fixed"),
              "parse_err": err, "checks": []}
    if not isinstance(data, dict):
        report["checks"].append({"item": "is_object", "ok": False})
        return report
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        ok = key in data
        report["checks"].append({"item": "has_%s" % key, "ok": ok})
    for key in ("sources", "uncertainty", "change_log"):
        if key in data and not isinstance(data[key], list):
            report["checks"].append({"item": "%s_is_list" % key, "ok": False})
    report["all_ok"] = all(c["ok"] for c in report["checks"])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="论文结构与逻辑优化 Skill 入口")
    ap.add_argument("--init", action="store_true", help="落盘 result.json 骨架")
    ap.add_argument("--scan", action="store_true", help="盘点材料 → evidence.json")
    ap.add_argument("--full", action="store_true", help="init+scan+诊断候选+落盘")
    ap.add_argument("--validate", action="store_true", help="校验 result.json")
    ap.add_argument("--case", default=None, help="case.json 路径（默认 /input/case.json）")
    ap.add_argument("--input", default=None, help="材料输入目录（默认 /input）")
    ap.add_argument("--work", default=None, help="中间产物目录（默认 /app/work）")
    ap.add_argument("--out", default=None, help="result.json 路径（默认 /app/output/result.json）")
    args = ap.parse_args(argv)
    p = paths()
    if args.case:
        p["case_json"] = args.case
    if args.input:
        p["input_dir"] = args.input
    if args.work:
        p["work_dir"] = args.work
    if args.out:
        p["result_json"] = args.out
        p["output_dir"] = os.path.dirname(args.out) or "."
    if args.init and not args.full:
        result = cmd_init(p)
        print(json.dumps({"ok": True, "mode": "init",
                          "result": p["result_json"],
                          "case_id": result.get("case_id")}, ensure_ascii=False))
        return 0
    if args.scan and not args.full:
        payload = cmd_scan(p)
        print(json.dumps({"ok": True, "mode": "scan",
                          "evidence": os.path.join(p["work_dir"], "evidence.json"),
                          "materials": len(payload["materials"]),
                          "claims": len(payload["anchors"]["claims"]),
                          "redundancy_pairs": len(payload["anchors"]["redundancy_pairs"]),
                          "degradations": len(payload["degradations"])},
                         ensure_ascii=False))
        return 0
    if args.validate:
        report = cmd_validate(p)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("all_ok") else 1
    # 默认（含 --full）
    result = cmd_full(p)
    print(json.dumps({"ok": True, "mode": "full",
                      "result": p["result_json"],
                      "case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "sources": len(result.get("sources", [])),
                      "degradations": len(result.get("degradations", []))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
