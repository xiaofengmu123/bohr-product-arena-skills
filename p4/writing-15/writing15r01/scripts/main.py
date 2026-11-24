#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学位论文与长文档写作 Skill 的编排入口（仅 Python 标准库）。

用法：
    python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py check    --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py          # 默认依次 scaffold + finalize（尽力而为）

职责：发现输入 → 抽取事实 → 跑一致性检查 → 先落盘 result.json 骨架 → 汇总交付物。
不生成学术正文、不虚构数据；缺失一律 null/[] 并如实标注。
"""

import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import discover
import checks

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 当 case.json 未提供 output_contract 时的兜底字段（键名按题目三类交付物对齐）
DEFAULT_OUTPUT_CONTRACT = {
    "main_artifact": None,
    "evidence_map": None,
    "change_log": None,
    "consistency_report": None,
}


# ---------- 文件工具 ----------

def atomic_write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path):
    if not path or not os.path.exists(path):
        return None
    data, _errs = discover.safe_json_load(path)
    return data


# ---------- 输入解析 ----------

def load_case(case_json_path):
    """读取 case.json，返回 (case_id, output_contract, case_data, errors)。"""
    errors = []
    case_data = None
    if case_json_path:
        case_data, errs = discover.safe_json_load(case_json_path)
        errors.extend(errs)
    else:
        errors.append("case_json_missing")
    if not isinstance(case_data, dict):
        return None, dict(DEFAULT_OUTPUT_CONTRACT), {}, errors

    case_id = (case_data.get("case_id") or case_data.get("id")
               or (case_data.get("case") or {}).get("id")
               or (case_data.get("case") or {}).get("case_id"))
    if not case_id:
        case_id = os.path.splitext(os.path.basename(case_json_path))[0]
        errors.append("case_id_fallback_to_filename")

    contract = case_data.get("output_contract") or case_data.get("outputs")
    if not isinstance(contract, dict):
        contract = dict(DEFAULT_OUTPUT_CONTRACT)
        errors.append("output_contract_missing_used_default")
    return case_id, contract, case_data, errors


# ---------- 事实与检查 ----------

def build_facts(scan):
    mats = [m for m in scan.get("materials", []) if m.get("text")]
    return checks.extract_facts(mats)


def build_report(scan, facts, check_result):
    lines = ["# 一致性检查报告", ""]
    lines.append("- 生成时间：%s" % datetime.datetime.now().isoformat(timespec="seconds"))
    lines.append("- 输入目录：%s" % scan.get("input_dir", ""))
    s = check_result["summary"]
    lines.append("- 抽取：标题 %d、术语 %d、数值 %d、图/表 %d、引用 %d" % (
        s["headings"], s["terms"], s["numbers"], s["fig_tables"], s["citations"]))
    lines.append("- 问题：%d（warn %d，error %d）" % (s["issue_count"], s["warn_count"], s["error_count"]))
    lines.append("")
    if not check_result["issues"]:
        lines.append("未发现需人工复核的一致性问题（启发式检查，不替代逐条核对）。")
    for idx, it in enumerate(check_result["issues"], 1):
        lines.append("")
        lines.append("## %d. [%s] %s" % (idx, it["level"].upper(), it["message"]))
        for loc, quote in zip(it.get("locators", []), it.get("quotes", [])):
            lines.append("- `%s`" % loc)
            if quote:
                lines.append("  - %s" % quote)
    lines.append("")
    return "\n".join(lines)


# ---------- 骨架与汇总 ----------

def make_skeleton(case_id, contract, errors, started_at):
    out = {
        "case_id": case_id,
        "status": "partial",
    }
    for key in contract:
        out[key] = None
    out.update({
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": list(errors),
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": started_at, "seconds": None},
    })
    return out


def _contract_value(key, contract_key, deliverables_map, output_dir, default=None):
    """解析某个 output_contract 键的最终值：deliverables_map 优先，其次按键名启发。"""
    if isinstance(deliverables_map, dict) and contract_key in deliverables_map:
        return deliverables_map[contract_key]
    k = key.lower()
    if any(t in k for t in ("artifact", "manuscript", "draft", "稿", "document")):
        return _first_artifact(output_dir)
    if any(t in k for t in ("evidence", "source", "来源", "溯源")):
        return _rel_path(output_dir, "evidence_map.json")
    if any(t in k for t in ("change", "log", "diff", "改动", "修改记录")):
        return _rel_path(output_dir, "change_log.json")
    if any(t in k for t in ("consistency", "check", "report", "检查")):
        return _rel_path(output_dir, "consistency_report.md")
    return default


def _rel_path(output_dir, name):
    return os.path.join(output_dir, name).replace("\\", "/")


def _first_artifact(output_dir):
    if not os.path.isdir(output_dir):
        return None
    prefer = []
    skip = {"result.json", "evidence_map.json", "change_log.json"}
    for fn in sorted(os.listdir(output_dir)):
        low = fn.lower()
        if low in skip or low.endswith(".tmp"):
            continue
        if low.endswith((".md", ".docx", ".pptx", ".xlsx")):
            prefer.append(fn)
    if prefer:
        return _rel_path(output_dir, prefer[0])
    return None


def compute_status(errors, scan, change_log, output_dir, finalize=False):
    mats = [m for m in scan.get("materials", []) if m.get("text")]
    has_case = bool(scan.get("case_json_path"))
    parse_failed = any("json_parse_failed" in e for e in errors)
    if not mats and not has_case:
        return "insufficient_evidence"
    if parse_failed and not mats:
        return "input_error"
    if finalize:
        if change_log and _first_artifact(output_dir):
            return "ok"
    return "partial"


def write_evidence_map(facts, output_dir):
    trimmed = {}
    for cat, items in facts.items():
        trimmed[cat] = items[:500]
    atomic_write_json(os.path.join(output_dir, "evidence_map.json"), trimmed)
    return _rel_path(output_dir, "evidence_map.json")


# ---------- 子命令 ----------

def cmd_scaffold(args):
    started = datetime.datetime.now().isoformat(timespec="seconds")
    t0 = time.time()
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    scan = discover.scan_inputs(args.input)
    case_id, contract, case_data, case_errors = load_case(scan.get("case_json_path"))
    errors = list(scan.get("errors", [])) + list(case_errors)

    # 1) 立即落盘合法骨架（前 3 分钟纪律）
    skeleton = make_skeleton(case_id, contract, errors, started)
    skeleton["status"] = compute_status(errors, scan, [], args.output, finalize=False)
    skeleton["degradations"] = [i.get("reason", "degraded") for i in scan.get("ignored", [])]
    atomic_write_json(os.path.join(args.output, "result.json"), skeleton)

    # 2) 抽取事实与检查
    facts = build_facts(scan)
    check_result = checks.run_checks(facts)
    report_md = build_report(scan, facts, check_result)

    # 3) 工作目录状态
    state = {
        "case_id": case_id,
        "output_contract_keys": list(contract.keys()),
        "case_json_path": scan.get("case_json_path"),
        "task_md_path": scan.get("task_md_path"),
        "workorder_path": scan.get("workorder_path"),
        "material_count": len([m for m in scan.get("materials", []) if m.get("text")]),
        "material_files": [m["rel"] for m in scan.get("materials", []) if m.get("text")],
        "ignored": scan.get("ignored", []),
        "errors": errors,
    }
    if scan.get("task_md_path"):
        task_text, _enc, _errs = discover.read_text_robust(scan["task_md_path"])
        state["task_md"] = task_text
    if scan.get("workorder_path"):
        state["workorder"] = read_json(scan["workorder_path"])

    atomic_write_json(os.path.join(args.work, "state.json"), state)
    atomic_write_json(os.path.join(args.work, "facts.json"), facts)
    atomic_write_json(os.path.join(args.work, "checks.json"), check_result)
    atomic_write_text(os.path.join(args.output, "consistency_report.md"), report_md)
    atomic_write_json(os.path.join(args.output, "change_log.json"), [])
    write_evidence_map(facts, args.output)

    # 4) 更新骨架：注入证据与检查摘要（不覆盖正文/change_log）
    result = read_json(os.path.join(args.output, "result.json")) or skeleton
    result["sources"] = _sources_from_facts(facts)
    result["runtime"]["seconds"] = round(time.time() - t0, 2)
    atomic_write_json(os.path.join(args.output, "result.json"), result)

    print(json.dumps({
        "case_id": case_id,
        "status": result.get("status"),
        "material_count": state["material_count"],
        "contract_keys": list(contract.keys()),
        "check_summary": check_result["summary"],
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False))
    return 0


def cmd_check(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    scan = discover.scan_inputs(args.input)
    facts = build_facts(scan)
    check_result = checks.run_checks(facts)
    atomic_write_json(os.path.join(args.work, "facts.json"), facts)
    atomic_write_json(os.path.join(args.work, "checks.json"), check_result)
    atomic_write_text(os.path.join(args.output, "consistency_report.md"), build_report(scan, facts, check_result))
    print(json.dumps(check_result["summary"], ensure_ascii=False))
    return 0


def _sources_from_facts(facts):
    sources = []
    for cat in ("terms", "numbers", "fig_tables", "citations"):
        for item in facts.get(cat, [])[:60]:
            sources.append({"locator": item["locator"], "quote": item.get("quote", "")})
    return sources


def cmd_finalize(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    t0 = time.time()

    scan = discover.scan_inputs(args.input)
    case_id, contract, _case_data, case_errors = load_case(scan.get("case_json_path"))
    errors = list(scan.get("errors", [])) + list(case_errors)

    # Agent 可写的工作文件
    change_log = read_json(os.path.join(args.work, "change_log.json")) or []
    deliverables_map = read_json(os.path.join(args.work, "deliverables_map.json")) or {}
    uncertainty = read_json(os.path.join(args.work, "uncertainty.json")) or []
    pending_human = read_json(os.path.join(args.work, "pending_human.json")) or []
    if not isinstance(change_log, list):
        change_log = []
    if not isinstance(deliverables_map, dict):
        deliverables_map = {}
    if not isinstance(uncertainty, list):
        uncertainty = []
    if not isinstance(pending_human, list):
        pending_human = []

    facts = read_json(os.path.join(args.work, "facts.json")) or build_facts(scan)

    result = make_skeleton(case_id, contract, errors, datetime.datetime.now().isoformat(timespec="seconds"))
    for key in contract:
        result[key] = _contract_value(key, key, deliverables_map, args.output, None)
    result["status"] = compute_status(errors, scan, change_log, args.output, finalize=True)
    result["sources"] = _sources_from_facts(facts)
    result["uncertainty"] = uncertainty
    result["change_log"] = change_log
    result["pending_human"] = pending_human
    result["degradations"] = [i.get("reason", "degraded") for i in scan.get("ignored", [])]
    result["runtime"] = {
        "started_at": result["runtime"]["started_at"],
        "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 2),
    }
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    atomic_write_json(os.path.join(args.output, "change_log.json"), change_log)
    print(json.dumps({"case_id": case_id, "status": result["status"], "contract_keys": list(contract.keys())}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="学位论文与长文档写作 Skill 编排入口")
    parser.add_argument("subcommand", nargs="?", default="all",
                        choices=["scaffold", "check", "finalize", "all"])
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    if args.subcommand in ("scaffold", "all"):
        rc = cmd_scaffold(args)
        if args.subcommand == "scaffold":
            return rc
    if args.subcommand == "check":
        return cmd_check(args)
    if args.subcommand in ("finalize", "all"):
        return cmd_finalize(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
