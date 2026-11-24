#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验解释 Skill 主入口：骨架落盘 / 契约校验 / 定稿。

仅使用 Python 标准库。运行时路径约定：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work

用法：
  python scripts/main.py skeleton --input /input --output /app/output
  python scripts/main.py validate --input /app/output/result.json
  python scripts/main.py finalize --input /app/output/result.json
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

TOP_KEYS = (
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
    "fabrication_gate",
)
INTERPRETATION_KEYS = (
    "trends", "anomalies", "possible_causes", "verification_steps", "followup_experiments",
)
PLAN_KEYS = (
    "hypothesis", "testable_question", "variables", "controls", "steps",
    "parameters", "resources", "interpretation_criteria",
)
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")
EVIDENCE_GRADES = ("G0", "G1", "G2", "G3")
CONFIDENCE_LEVELS = ("高", "中", "低")

MUST_NOT_INVENT = [
    "湿实验测量值（未在材料中出现的读数/浓度/重复数）",
    "异常的原因或机理（无排除过程时）",
    "统计显著性与 p 值（未实际计算时）",
    "文献 DOI、页码、标题（未在材料中逐字出现时）",
    "benchmark 得分与白板对照结果（由平台固定）",
    "设备批次号、校准状态、运行日志（未提供时）",
]

NON_MATERIAL_NAMES = {"case.json", "task.md", "environment.yaml", "resources.yaml", ".ds_store"}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """UTF-8(含 BOM)优先，坏编码降级 latin-1；超限或不可读返回 (None, reason)。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > max_bytes:
        return None, "file exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    return None, "unreadable encoding"


def load_json_safe(path):
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def discover_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append({
                "path": full, "rel": rel, "size_bytes": size,
                "ext": os.path.splitext(fn)[1].lower(),
                "name": fn,
            })
    return out


def resolve_case_id(input_dir):
    obj = load_json_safe(os.path.join(input_dir, "case.json"))
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "case"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        content = obj.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", content, re.I)
            if m:
                return m.group(1)
    text, _ = read_text(os.path.join(input_dir, "task.md"))
    if text:
        m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_ ]?id[:\s`]+([A-Za-z0-9_.-]+)", text, re.I)
        if m:
            return m.group(1)
    base = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown"
    return base


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def extract_contract_keys(contract):
    """从 output_contract（dict/list）中提取需要回填的字段名列表。"""
    if not isinstance(contract, dict):
        return []
    names = []
    for key in ("fields", "required", "schema"):
        v = contract.get(key)
        if isinstance(v, list):
            names.extend(x for x in v if isinstance(x, str))
        elif isinstance(v, dict):
            names.extend(k for k in v if isinstance(k, str))
    if not names:
        names = [k for k in contract if isinstance(k, str)
                 and k not in ("fields", "required", "schema", "description", "title")]
    return names


def merge_output_contract(result, case_obj):
    """若 case.json 含 output_contract，原样回填其全部键（缺值给 null/[]），不改名不删未知键。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    if contract is None:
        return []
    names = []
    if isinstance(contract, dict):
        names = extract_contract_keys(contract)
    elif isinstance(contract, list):
        names = [x for x in contract if isinstance(x, str)]
    merged = []
    for k in names:
        if k not in result:
            default = None
            if isinstance(contract, dict) and k in contract and not isinstance(contract[k], (dict, list)):
                default = contract[k]
            elif isinstance(contract, dict) and k in contract:
                default = _default_for(contract[k])
            result[k] = default
            merged.append(k)
    return merged


def claim():
    """统一结论对象模板。"""
    return {
        "evidence_grade": "G0",
        "confidence": None,
        "confidence_basis": None,
        "evidence": [],
    }


def empty_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "task_type": "experiment_result_interpretation",
        "summary": "",
        "interpretation": {
            "trends": [],
            "anomalies": [],
            "possible_causes": [],
            "verification_steps": [],
            "followup_experiments": [],
        },
        "experiment_plan": {
            "hypothesis": None,
            "testable_question": None,
            "variables": [],
            "controls": [],
            "steps": [],
            "parameters": [],
            "resources": [],
            "interpretation_criteria": [],
        },
        "evidence_and_risk": {
            "evidence_basis": [],
            "risk_and_anomaly_handling": [],
            "assumptions": [],
        },
        "confirmation_items": {
            "safety": [],
            "ethics": [],
            "equipment_availability": [],
            "expert_confirmation": [],
        },
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {},
        "fabrication_gate": {
            "must_not_invent": list(MUST_NOT_INVENT),
            "non_fabrication_checks": [],
        },
    }


def material_files(files):
    return [f for f in files if f["name"].lower() not in NON_MATERIAL_NAMES]


def build_skeleton(input_dir, output_dir):
    started = time.time()
    os.makedirs(output_dir, exist_ok=True)

    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    case_id = resolve_case_id(input_dir)
    result = empty_result(case_id)
    merged = merge_output_contract(result, case_obj)

    files = discover_files(input_dir)
    materials = material_files(files)
    sources = []
    for f in files:
        if f["name"].lower() in NON_MATERIAL_NAMES:
            continue
        sources.append({
            "path": f["path"], "rel": f["rel"],
            "size_bytes": f["size_bytes"], "ext": f["ext"],
            "kind": "material",
        })

    result["sources"] = sources
    result["runtime"] = {
        "started_at": now_ts(),
        "phase": "skeleton",
    }

    # 空输入 / 无材料 → insufficient_evidence
    if not materials:
        result["status"] = "insufficient_evidence"
        result["summary"] = "未发现除 case.json/task.md 之外的材料文件，无法进行结果解释。"
        result["limitations"].append({
            "type": "no_material",
            "locator": None,
            "detail": "输入目录无可读取的材料文件；不填充示例结论。",
        })
        result["pending_human"].append({
            "item": "材料清单",
            "detail": "请确认 /input 下是否已提供数据/曲线/论文材料。",
        })

    result["change_log"].append({
        "ts": now_ts(),
        "phase": "skeleton",
        "action": "写入结果骨架与报告骨架",
        "detail": "status=%s；材料文件 %d 个；output_contract 回填 %d 键"
                 % (result["status"], len(materials), len(merged)),
    })

    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"), report_skeleton(result))

    print(json.dumps({
        "case_id": case_id,
        "status": result["status"],
        "material_count": len(materials),
        "merged_contract_keys": merged,
        "elapsed_seconds": round(time.time() - started, 3),
    }, ensure_ascii=False, indent=2))
    return 0


def report_skeleton(result):
    lines = []
    lines.append("# 结果解释（骨架，partial）")
    lines.append("")
    lines.append("case_id: %s" % result.get("case_id", "unknown"))
    lines.append("status: %s" % result.get("status", "partial"))
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append(result.get("summary") or "（待填充）")
    lines.append("")
    lines.append("## 趋势")
    lines.append("")
    lines.append("（示例占位：未填入真实读数；见 result.json 的 interpretation.trends）")
    lines.append("")
    lines.append("## 异常点")
    lines.append("")
    lines.append("（示例占位；见 result.json 的 interpretation.anomalies）")
    lines.append("")
    lines.append("## 可能原因")
    lines.append("")
    lines.append("（示例占位；见 result.json 的 interpretation.possible_causes）")
    lines.append("")
    lines.append("## 复核步骤")
    lines.append("")
    lines.append("（示例占位；见 result.json 的 interpretation.verification_steps）")
    lines.append("")
    lines.append("## 后续实验建议")
    lines.append("")
    lines.append("（示例占位；见 result.json 的 interpretation.followup_experiments）")
    lines.append("")
    return "\n".join(lines) + "\n"


def _failures(msg):
    print(msg, file=sys.stderr)
    return 1


def validate(result_path):
    obj = load_json_safe(result_path)
    if obj is None:
        return _failures("无法解析 JSON：%s" % result_path)
    if not isinstance(obj, dict):
        return _failures("result.json 顶层必须是对象")

    problems = []
    warnings = []

    # 必填顶层键
    for k in TOP_KEYS:
        if k not in obj:
            problems.append("缺少顶层键 %s" % k)

    if not obj.get("case_id"):
        problems.append("case_id 为空")

    if obj.get("status") not in STATUSES:
        problems.append("status 非法：%r" % obj.get("status"))

    interp = obj.get("interpretation")
    if not isinstance(interp, dict):
        problems.append("interpretation 必须是对象")
    else:
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                problems.append("interpretation 缺少 %s" % k)

    plan = obj.get("experiment_plan")
    if not isinstance(plan, dict):
        problems.append("experiment_plan 必须是对象")
    else:
        for k in PLAN_KEYS:
            if k not in plan:
                problems.append("experiment_plan 缺少 %s" % k)

    risk = obj.get("evidence_and_risk")
    if not isinstance(risk, dict):
        problems.append("evidence_and_risk 必须是对象")
    else:
        for k in RISK_KEYS:
            if k not in risk:
                problems.append("evidence_and_risk 缺少 %s" % k)

    confirm = obj.get("confirmation_items")
    if not isinstance(confirm, dict):
        problems.append("confirmation_items 必须是对象")
    else:
        for k in CONFIRM_KEYS:
            if k not in confirm:
                problems.append("confirmation_items 缺少 %s" % k)

    # 五段结论完整性
    def check_claims(section):
        claims = interp.get(section) if isinstance(interp, dict) else []
        if not isinstance(claims, list):
            problems.append("interpretation.%s 必须是数组" % section)
            return
        for i, c in enumerate(claims):
            tag = "interpretation.%s[%d]" % (section, i)
            if not isinstance(c, dict):
                problems.append("%s 必须是对象" % tag)
                continue
            if c.get("evidence_grade") not in EVIDENCE_GRADES:
                problems.append("%s evidence_grade 非法：%r" % (tag, c.get("evidence_grade")))
            if c.get("confidence") not in CONFIDENCE_LEVELS and c.get("confidence") is not None:
                warnings.append("%s confidence 建议为 高/中/低" % tag)
            ev = c.get("evidence")
            if c.get("evidence_grade") in ("G1", "G2", "G3"):
                if not isinstance(ev, list) or not ev:
                    problems.append("%s 缺少 evidence 数组" % tag)
                else:
                    for j, e in enumerate(ev):
                        if not isinstance(e, dict) or not e.get("locator") or not e.get("quote"):
                            problems.append("%s evidence[%d] 缺 locator/quote" % (tag, j))
            # 异常点必须有阈值
            if section == "anomalies":
                if not c.get("threshold") and c.get("evidence_grade") in ("G1", "G2"):
                    problems.append("%s 未写明判定阈值" % tag)

    if isinstance(interp, dict):
        for s in INTERPRETATION_KEYS:
            check_claims(s)

    # 证据溯源抽查：evidence 必须带 quote
    gate = obj.get("fabrication_gate")
    if not isinstance(gate, dict):
        problems.append("缺少 fabrication_gate 对象")
    else:
        if not isinstance(gate.get("must_not_invent"), list) or not gate["must_not_invent"]:
            warnings.append("fabrication_gate.must_not_invent 为空")

    if obj.get("status") == "ok":
        for s in INTERPRETATION_KEYS:
            claims = interp.get(s, []) if isinstance(interp, dict) else []
            if not claims:
                warnings.append("status=ok 但 interpretation.%s 为空" % s)

    print(json.dumps({
        "valid": not problems,
        "problems": problems,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    if problems:
        return _failures("validate 失败：%d 个硬性问题" % len(problems))
    print("validate 通过（warnings=%d）" % len(warnings))
    return 0


def _fmt_claim(section, idx, c):
    out = []
    grade = c.get("evidence_grade", "G0")
    conf = c.get("confidence")
    basis = c.get("confidence_basis")
    out.append("### %s[%d]  %s / 置信:%s" % (section, idx, grade, conf or "未标"))
    text = c.get("text") or c.get("statement") or c.get("summary") or ""
    if text:
        out.append("")
        out.append(str(text))
    if basis:
        out.append("")
        out.append("置信依据: %s" % basis)
    ev = c.get("evidence")
    if isinstance(ev, list) and ev:
        out.append("")
        out.append("证据:")
        for e in ev:
            loc = e.get("locator") or "?"
            quote = e.get("quote") or ""
            out.append("- `%s` → %s" % (loc, quote))
    return "\n".join(out)


def build_report(result_path, output_dir):
    obj = load_json_safe(result_path)
    if obj is None:
        return _failures("无法解析 JSON：%s" % result_path)
    os.makedirs(output_dir, exist_ok=True)

    lines = ["# 实验结果解释报告", ""]
    lines.append("case_id: %s" % obj.get("case_id", "unknown"))
    lines.append("status: %s" % obj.get("status", "partial"))
    lines.append("task_type: %s" % obj.get("task_type", ""))
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append(str(obj.get("summary") or "（无摘要）"))
    lines.append("")

    interp = obj.get("interpretation") or {}
    for section in INTERPRETATION_KEYS:
        lines.append("## %s" % section)
        lines.append("")
        claims = interp.get(section) or []
        if not claims:
            lines.append("（空）")
        for i, c in enumerate(claims):
            lines.append(_fmt_claim(section, i, c))
            lines.append("")
        lines.append("")

    plan = obj.get("experiment_plan") or {}
    lines.append("## 实验方案")
    lines.append("")
    for k in PLAN_KEYS:
        v = plan.get(k)
        lines.append("- %s: %s" % (k, json.dumps(v, ensure_ascii=False) if v not in (None, "", []) else "（未填）"))
    lines.append("")

    lines.append("## 依据与风险")
    lines.append("")
    risk = obj.get("evidence_and_risk") or {}
    for k in RISK_KEYS:
        lines.append("- %s: %s" % (k, json.dumps(risk.get(k), ensure_ascii=False) if risk.get(k) not in (None, "", []) else "（未填）"))
    lines.append("")

    lines.append("## 确认项")
    lines.append("")
    confirm = obj.get("confirmation_items") or {}
    for k in CONFIRM_KEYS:
        lines.append("- %s: %s" % (k, json.dumps(confirm.get(k), ensure_ascii=False) if confirm.get(k) not in (None, "", []) else "（未填）"))
    lines.append("")

    lines.append("## 不确定性与限制")
    lines.append("")
    lines.append("uncertainty: %s" % json.dumps(obj.get("uncertainty", []), ensure_ascii=False))
    lines.append("limitations: %s" % json.dumps(obj.get("limitations", []), ensure_ascii=False))
    lines.append("pending_human: %s" % json.dumps(obj.get("pending_human", []), ensure_ascii=False))
    lines.append("")

    write_text_atomic(os.path.join(output_dir, "report.md"), "\n".join(lines) + "\n")

    # evidence_map.json：所有 locator→quote 去重
    emap = {}
    for section in INTERPRETATION_KEYS:
        for c in (interp.get(section) or []):
            for e in (c.get("evidence") or []):
                loc = e.get("locator")
                if loc:
                    emap.setdefault(loc, []).append(e.get("quote", ""))
    write_json_atomic(os.path.join(output_dir, "evidence_map.json"), {
        "case_id": obj.get("case_id"),
        "evidence_count": sum(len(v) for v in emap.values()),
        "locators": {k: v for k, v in sorted(emap.items())},
    })
    print(json.dumps({"report": "report.md", "evidence_map": "evidence_map.json",
                      "evidence_count": sum(len(v) for v in emap.values())}, ensure_ascii=False))
    return 0


def finalize(result_path):
    obj = load_json_safe(result_path)
    if obj is None:
        return _failures("无法解析 JSON：%s" % result_path)
    if not isinstance(obj, dict):
        return _failures("result.json 顶层必须是对象")

    rt = obj.get("runtime")
    if not isinstance(rt, dict):
        rt = {}
    started = rt.get("started_at")
    finished = now_ts()
    elapsed = None
    if isinstance(started, str):
        try:
            t0 = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
            t1 = time.mktime(time.strptime(finished, "%Y-%m-%dT%H:%M:%SZ"))
            elapsed = max(0.0, round(t1 - t0, 3))
        except ValueError:
            elapsed = None
    rt["finished_at"] = finished
    rt["elapsed_seconds"] = elapsed
    rt["phase"] = "finalized"
    obj["runtime"] = rt

    obj.setdefault("change_log", []).append({
        "ts": finished,
        "phase": "finalize",
        "action": "写 runtime 与耗时",
        "detail": "elapsed_seconds=%s" % elapsed,
    })
    write_json_atomic(result_path, obj)
    print(json.dumps({"finished_at": finished, "elapsed_seconds": elapsed}, ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="实验解释 Skill 主入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("skeleton", help="写 result.json 骨架与 report.md")
    ps.add_argument("--input", default=DEFAULT_INPUT)
    ps.add_argument("--output", default=DEFAULT_OUTPUT)
    ps.add_argument("--work", default=DEFAULT_WORK)

    pv = sub.add_parser("validate", help="校验 result.json")
    pv.add_argument("--input", required=True)

    pr = sub.add_parser("report", help="由 result.json 生成 report.md 与 evidence_map.json")
    pr.add_argument("--input", required=True)
    pr.add_argument("--output", default=DEFAULT_OUTPUT)

    pf = sub.add_parser("finalize", help="写 runtime")
    pf.add_argument("--input", required=True)

    args = p.parse_args(argv)
    if args.cmd == "skeleton":
        return build_skeleton(args.input, args.output)
    if args.cmd == "validate":
        return validate(args.input)
    if args.cmd == "report":
        return build_report(args.input, args.output)
    if args.cmd == "finalize":
        return finalize(args.input)
    return _failures("未知子命令")


if __name__ == "__main__":
    sys.exit(main())
