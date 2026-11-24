"""scaffold.py -- write the initial legal partial result.json skeleton + plan.json + state.json.

Call early (first 3 minutes). Reads /input/case.json + task.md and /app/work/manifest.json.
Backfills case.json.output_contract keys into result.json top level (missing -> null/[]/"").
Creates plan.json: five facets x five questions with status="unchecked".

Stdlib only. Exit 0 unless a write fails fatally.
"""
import argparse
import copy
import json
import os
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

FACETS = [
    ("research_design", "研究设计", [
        ("rd_01", "设计类型（RCT/队列/病例对照/横断面/配对/实验）是否陈述，并与所答研究问题匹配？"),
        ("rd_02", "对照/分组设置是否成立；随机化、分配隐藏、盲法（如适用）是否说明？"),
        ("rd_03", "主要终点与研究假设是否事先明确；有无事后更换终点迹象？"),
        ("rd_04", "选择/混杂/失访/测量偏倚是否被识别并给出控制措施？"),
        ("rd_05", "研究单位与统计单位是否一致（个体/眼/牙/巢/重复测量）？"),
    ]),
    ("variable_definition", "变量定义", [
        ("vd_01", "暴露、结局、协变量、分组变量的定义/测量方式/单位是否清晰？"),
        ("vd_02", "分类变量的切点/编码是否说明并有依据？"),
        ("vd_03", "连续变量的单位换算、正态化或变换是否前后一致？"),
        ("vd_04", "缺失值数量与处理方式是否报告？"),
        ("vd_05", "变量类型（连续/二分类/计数/生存/有序）与后续模型是否对得上？"),
    ]),
    ("statistical_methods", "统计方法", [
        ("sm_01", "检验方法与数据结构（独立/配对/多组/重复测量）是否匹配？"),
        ("sm_02", "分布假设（正态、方差齐）是否检验；不满足时是否改用非参数/稳健方法？"),
        ("sm_03", "多重比较是否校正（Bonferroni/Holm/FDR）并说明作用于哪些终点？"),
        ("sm_04", "交互项、混杂调整、敏感性分析是否设置并报告？"),
        ("sm_05", "生存/计数/重复测量等设计是否用相应模型（Cox/Poisson/混合效应/GEE）？"),
    ]),
    ("sample_size_power", "样本量与检验效能", [
        ("sp_01", "样本量估算是否报告：显著性水平、把握度、效应量及其来源？"),
        ("sp_02", "实际入组/分析样本量与估算量是否一致；差异是否解释？"),
        ("sp_03", "失访、剔除、缺失是否报告并处理（ITT/PP/敏感性）？"),
        ("sp_04", "是否存在以 post-hoc 功效分析冒充先验功效？"),
        ("sp_05", "亚组/交互检验的把握度限制是否说明？"),
    ]),
    ("results_reporting", "结果报告", [
        ("rr_01", "效应量、置信区间、精确 p 值、统计量与自由度是否报告？"),
        ("rr_02", "摘要/正文/表格/图注的数值与显著性表述是否一致？"),
        ("rr_03", "显著性结论与 p/CI 是否一致（比值指标 null=1、差值指标 null=0）？"),
        ("rr_04", "是否符合相应报告规范（CONSORT/STROBE/PRISMA 等）？"),
        ("rr_05", "图表单位/图例/误差条含义是否说明？"),
    ]),
]

CHECK_CLASSES = ["fact_check", "citation_support", "method_design",
                 "statistical_reporting", "argumentation_chain", "academic_norms"]


def log(msg):
    print("[scaffold] " + msg, flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Write the initial partial result.json skeleton and plan.json.")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p.parse_args(argv)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_plan(case_id):
    facets = []
    for fid, title, qs in FACETS:
        facets.append({
            "id": fid, "title": title,
            "questions": [{"qid": qid, "question": q, "status": "unchecked",
                           "finding_ids": [], "note": ""} for qid, q in qs],
        })
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "facets": facets,
        "check_classes": CHECK_CLASSES,
        "checked_facets": [],
        "unchecked": [f[0] for f in FACETS],
    }


def empty_value(kind):
    return {"string": "", "list": [], "object": None}.get(kind, None)


def backfill_contract(result, contract):
    """Copy every output_contract key to result top level, preserving unknown keys.

    Missing keys within a known contract are filled with null/[]/"" according to a
    conservative type hint map when available, else null.
    """
    if not isinstance(contract, dict):
        return
    result["output_contract"] = copy.deepcopy(contract)
    for k, v in contract.items():
        if isinstance(v, str) and v == "":
            result[k] = ""
        elif isinstance(v, list) and not v:
            result[k] = []
        elif v is None:
            result[k] = None
        else:
            result[k] = copy.deepcopy(v)


def empty_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "summary": {
            "verdict": "unchecked",
            "counts": {"total": 0},
            "by_facet": {f: 0 for f, _, _ in FACETS},
            "by_type": {},
            "by_severity": {},
        },
        "review_report": {"findings": []},
        "result_status": {
            "overall": "未检查",
            "pass": [],
            "issue": [],
            "needs_review": [],
            "unchecked": [f for f, _, _ in FACETS],
        },
        "evidence": {"evidence_map": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started": time.time(), "seconds": None},
        "numeric_checks": [],
    }


def main(argv=None):
    args = parse_args(argv)
    ensure_dir(args.work)
    ensure_dir(args.output)
    started = time.time()

    manifest = {}
    mpath = os.path.join(args.work, "manifest.json")
    if os.path.isfile(mpath):
        try:
            manifest = load_json(mpath)
        except Exception as e:  # noqa: BLE001
            manifest = {"errors": [{"kind": "manifest_bad_json", "detail": str(e)}]}

    case = {}
    case_path = os.path.join(args.input, "case.json")
    if os.path.isfile(case_path):
        try:
            case = load_json(case_path)
        except Exception as e:  # noqa: BLE001
            case = {"case_id": None, "_read_error": str(e)}

    case_id = case.get("case_id") or manifest.get("case_id")
    result = empty_result(case_id)
    result["change_log"].append({
        "time": time.time(), "action": "scaffold",
        "reason": "initial partial skeleton"})

    # collect errors/degradations from manifest and case read problems
    for e in manifest.get("errors", []):
        result["errors"].append(copy.deepcopy(e))
    for d in manifest.get("degradations", []):
        result["degradations"].append(copy.deepcopy(d))
    if case.get("_read_error"):
        result["errors"].append({"file": "case.json", "kind": "bad_json",
                                 "detail": case["_read_error"]})

    # status decision
    has_case = os.path.isfile(case_path)
    materials = manifest.get("materials", []) or []
    seg_count = manifest.get("segment_count", 0)
    if not has_case and not materials:
        result["status"] = "input_error"
        result["summary"]["verdict"] = "unchecked"
    elif not materials or seg_count == 0:
        result["status"] = "insufficient_evidence"
        result["summary"]["verdict"] = "unchecked"
        result["limitations"].append(
            {"item": "no_extractable_material",
             "detail": "no readable material or text segments available"})

    if isinstance(case.get("output_contract"), dict):
        backfill_contract(result, case["output_contract"])

    save_json(os.path.join(args.output, "result.json"), result)
    save_json(os.path.join(args.work, "plan.json"), build_plan(case_id))
    save_json(os.path.join(args.work, "state.json"), {
        "phase": "skeleton", "case_id": case_id, "started": started,
        "findings_written": 0, "checked_facets": [], "next": "phase1"})

    log("case_id=%s status=%s materials=%d segments=%d contract=%s"
        % (case_id, result["status"], len(materials), seg_count,
           isinstance(case.get("output_contract"), dict)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
