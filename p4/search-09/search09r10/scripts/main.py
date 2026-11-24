#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence-index 运行脚手架（薄 CLI）。

子命令：
  skeleton  立即落盘 partial 结果骨架（含 case_id + 契约字段 + 结构空数组）
  index     对材料建立段落证据索引（中间表示 /app/work/index.json）
  verify    逐条核验 quote 是否逐字存在于所标 locator，报告写入 work/
  grade     按阈值聚合偏倚风险等级，回写 result.json 的 grade 字段
  merge     把 work/assessment_patch.json 增量合并进 result.json（带变更日志）
  validate  结构完整性校验，报告写入 work/，退出码恒为 0（仅报告）
  self-test 三场景自检（空目录/坏 JSON/缺输入 + 合成材料全流程）

用法（在 SKILL.md 所在目录下，以相对路径运行）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py self-test
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_impl as core
import rubric

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------


def cmd_skeleton(args):
    found, disc_errors = core.discover_input(args.input)
    case_path = found["case_json"][0] if found["case_json"] else None
    data, load_errors = (None, [])
    if case_path:
        data, load_errors = core.load_json_lenient(case_path)
    task_text = None
    if found["task_md"]:
        task_text, _ = core.read_text(found["task_md"][0])
    case_id = core.extract_case_id(data, task_text, args.input)
    contract = core.resolve_contract(data)
    notes = disc_errors + load_errors
    if case_path is None:
        notes.append("case_json_not_found")
    if contract is None:
        notes.append("output_contract_not_found: 未在 case.json 中定位契约，仅保留 case_id/status/结构字段")
    result = core.build_skeleton(case_id, contract, notes=notes)
    # 诚实状态：缺输入目录/缺 case.json 分别标记，不伪装为 partial 可继续评价
    if any(str(e).startswith("input_dir_missing") for e in notes):
        result["status"] = "input_error"
    elif case_path is None:
        result["status"] = "insufficient_evidence"
    core.atomic_write_json(Path(args.output) / "result.json", result)
    # 同时落盘一份可读骨架说明
    core.atomic_write_text(
        Path(args.output) / "assessment.md",
        "# 文献研究质量评价（骨架）\n\n"
        "状态：partial 骨架，评价条目尚未填写。\n\n"
        "- case_id: %s\n- 契约字段数: %d\n- 说明: %s\n" % (
            case_id, len(core.contract_defaults(contract)), "; ".join(notes) or "无"
        ),
    )
    print("skeleton written: %s" % (Path(args.output) / "result.json"))
    print("case_id=%s contract_fields=%d notes=%s" % (
        case_id, len(core.contract_defaults(contract)), "; ".join(notes) or "无"))
    return 0


def cmd_index(args):
    found, disc_errors = core.discover_input(args.input)
    materials = found["materials"]
    index, errors, degradations = core.build_index(materials, args.input)
    Path(args.work).mkdir(parents=True, exist_ok=True)
    core.atomic_write_json(Path(args.work) / "index.json", index)
    meta = {
        "material_count": len(materials),
        "indexed_paragraphs": len(index.get("lookup", {})),
        "errors": errors,
        "degradations": degradations,
        "materials": [
            {"rel_path": f["rel_path"], "kind": f["kind"], "paragraphs": len(f["paragraphs"]), "note": f["note"]}
            for f in index.get("files", [])
        ],
    }
    core.atomic_write_json(Path(args.work) / "index_meta.json", meta)
    print("index built: %d materials, %d paragraphs; errors=%d degradations=%d" % (
        len(materials), len(index.get("lookup", {})), len(errors), len(degradations)))
    return 0


def cmd_verify(args):
    result, _ = core.load_result(args.output)
    index, _ = core.load_json_lenient(Path(args.work) / "index.json")
    if result is None:
        print("verify: result.json 缺失或不可解析")
        return 0
    assessments = result.get("assessment", [])
    report = rubric.verify_quotes(index, assessments)
    Path(args.work).mkdir(parents=True, exist_ok=True)
    core.atomic_write_json(Path(args.work) / "verify_report.json", report)
    s = report["summary"]
    print("verify: total=%d ok=%d mismatch=%d missing_locator=%d locator_not_in_index=%d missing_quote=%d" % (
        s["total"], s["ok"], s["mismatch"], s["missing_locator"], s["locator_not_in_index"], s["missing_quote"]))
    # 回写 result.json 的 verification 字段（不删改其他内容）
    result["verification"] = {"status": "done", "summary": s}
    core.atomic_write_json(Path(args.output) / "result.json", result)
    return 0


def cmd_grade(args):
    result, load_errors = core.load_result(args.output)
    if result is None:
        print("grade: result.json 缺失或不可解析")
        return 0
    grade = rubric.grade_assessments(result.get("assessment", []))
    result["grade"] = grade
    core.append_change_log(result, "grade", grade["overall"])
    core.atomic_write_json(Path(args.output) / "result.json", result)
    print("grade: %s (%s)" % (grade["overall"], grade["rationale"]))
    return 0


def cmd_merge(args):
    patch_path = Path(args.patch) if getattr(args, "patch", None) else Path(args.work) / "assessment_patch.json"
    result, load_errors = core.load_result(args.output)
    if result is None:
        print("merge: result.json 缺失，先运行 skeleton")
        return 1
    patch, patch_errors = core.load_json_lenient(patch_path)
    if patch is None:
        print("merge: patch 缺失或不可解析: %s" % patch_path)
        return 1
    merged = []
    for key, value in (patch.items() if isinstance(patch, dict) else []):
        if key in core.LIST_FIELDS and isinstance(value, list):
            dest = result.setdefault(key, [])
            dest.extend(value)
            merged.append(key)
        elif key in core.SCALAR_FIELDS:
            result[key] = value
            merged.append(key)
        elif key == "finish_epoch":
            result.setdefault("runtime", {})["finish_epoch"] = value
            merged.append(key)
        else:
            result[key] = value
            merged.append(key)
    core.append_change_log(result, "merge", "merged keys: %s" % ", ".join(sorted(merged)))
    core.atomic_write_json(Path(args.output) / "result.json", result)
    print("merge: %d keys merged from %s" % (len(merged), patch_path))
    return 0


def cmd_validate(args):
    found, _ = core.discover_input(args.input)
    case_path = found["case_json"][0] if found["case_json"] else None
    data, _ = core.load_json_lenient(case_path) if case_path else (None, [])
    contract = core.resolve_contract(data)
    names = rubric_contract_names(contract)
    result, load_errors = core.load_result(args.output)
    index, _ = core.load_json_lenient(Path(args.work) / "index.json")
    if result is None:
        report = {"valid": False, "errors": load_errors, "warnings": []}
    else:
        errors, warnings = rubric.validate_result(result, names, index=index)
        report = {"valid": not errors, "errors": errors, "warnings": warnings}
    Path(args.work).mkdir(parents=True, exist_ok=True)
    core.atomic_write_json(Path(args.work) / "validate_report.json", report)
    print("validate: valid=%s errors=%d warnings=%d" % (
        report["valid"], len(report["errors"]), len(report["warnings"])))
    for e in report["errors"]:
        print("  [error] %s" % e)
    for w in report["warnings"]:
        print("  [warn] %s" % w)
    return 0


def rubric_contract_names(contract):
    return set(core.contract_defaults(contract).keys())


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def _write_case(path, case_json_text, task_text=None, materials=None):
    Path(path).mkdir(parents=True, exist_ok=True)
    if case_json_text is not None:
        with open(Path(path) / "case.json", "w", encoding="utf-8") as fh:
            fh.write(case_json_text)
    if task_text is not None:
        with open(Path(path) / "task.md", "w", encoding="utf-8") as fh:
            fh.write(task_text)
    for rel, content in (materials or {}).items():
        target = Path(path) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)


def _run_scenario(name, case_json_text, task_text, materials):
    base = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))
    inp = base / "input"
    out = base / "output"
    work = base / "work"
    _write_case(inp, case_json_text, task_text, materials)
    rc1 = cmd_skeleton(_NS(input=str(inp), output=str(out), work=str(work)))
    rc2 = cmd_index(_NS(input=str(inp), output=str(out), work=str(work)))
    # 写入一条合成评价（含逐字 quote）并走 merge/verify/grade/validate
    if materials:
        patch = {
            "assessment": [{
                "dimension": "research_design", "checkpoint": "design_type",
                "critical": "关键", "verdict": "支持", "judgment": "研究类型可识别为横断面研究。",
                "locator": "paper.md#p0", "quote": "本研究采用横断面调查设计",
                "basis": "paper.md 段落 0",
            }],
            "candidates": [{"id": "paper.md", "included": True, "reason": "题干材料"}],
            "screening": [{"stage": "全文", "kept": 1, "excluded": 0, "basis": "材料齐全"}],
        }
        core.atomic_write_json(work / "assessment_patch.json", patch)
        cmd_merge(_NS(input=str(inp), output=str(out), work=str(work), patch=str(work / "assessment_patch.json")))
        cmd_verify(_NS(input=str(inp), output=str(out), work=str(work)))
        cmd_grade(_NS(input=str(inp), output=str(out), work=str(work)))
    rc4 = cmd_validate(_NS(input=str(inp), output=str(out), work=str(work)))
    result, errs = core.load_result(out)
    ok = result is not None and result.get("case_id") and result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error")
    print("[%s] %s -> %s" % ("PASS" if ok else "FAIL", name, "result=%s" % (result.get("status") if result else None)))
    return 0 if ok else 1


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.patch = kw.get("patch")


def cmd_self_test(args):
    scenarios = []
    # 场景 1：空输入目录
    scenarios.append(("empty_input", None, None, None))
    # 场景 2：坏 JSON + 合法 task.md
    scenarios.append(("bad_json", "{ not valid json", "# 任务\n", None))
    # 场景 3：缺输入目录（不创建 input 目录）
    scenarios.append(("missing_input_dir", "SKIP_MISSING_DIR", None, None))
    # 场景 4：合成材料全流程（含逐字引文核验）
    scenarios.append(("full_pipeline", '{"case_id": "c1", "output_contract": {"properties": {"retrieval_result": {"type": "array"}}}}',
                      "# 任务：评价一篇文献\n", {"paper.md": "本研究采用横断面调查设计。\n\n样本量为 300 人，报告了 95% 置信区间。"}))

    all_ok = True
    for name, case_json_text, task_text, materials in scenarios:
        if name == "missing_input_dir":
            # 缺输入目录：直接用不存在的路径跑 skeleton
            base = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))
            out = base / "output"
            work = base / "work"
            rc = cmd_skeleton(_NS(input=str(base / "no_such_input"), output=str(out), work=str(work)))
            result, _ = core.load_result(out)
            ok = result is not None and result.get("status") in ("insufficient_evidence", "input_error", "partial")
            print("[%s] %s -> status=%s" % ("PASS" if ok else "FAIL", name, result.get("status") if result else None))
            all_ok = all_ok and ok
        else:
            all_ok = _run_scenario(name, case_json_text, task_text, materials) == 0 and all_ok
    print("self-test: %s" % ("ALL PASS" if all_ok else "FAILURES PRESENT"))
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(description="litqa-evidence-index 运行脚手架")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("skeleton", "index", "verify", "grade", "validate", "self-test", "merge"):
        sp = sub.add_parser(name)
        if name in ("skeleton", "index", "verify", "grade", "validate"):
            sp.add_argument("--input", default=DEFAULT_INPUT)
            sp.add_argument("--output", default=DEFAULT_OUTPUT)
            sp.add_argument("--work", default=DEFAULT_WORK)
        if name == "merge":
            sp.add_argument("--input", default=DEFAULT_INPUT)
            sp.add_argument("--output", default=DEFAULT_OUTPUT)
            sp.add_argument("--work", default=DEFAULT_WORK)
            sp.add_argument("--patch", default=None, help="patch JSON 路径；缺省为 <work>/assessment_patch.json")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "skeleton":
        return cmd_skeleton(args)
    if cmd == "index":
        return cmd_index(args)
    if cmd == "verify":
        return cmd_verify(args)
    if cmd == "grade":
        return cmd_grade(args)
    if cmd == "merge":
        return cmd_merge(args)
    if cmd == "validate":
        return cmd_validate(args)
    if cmd == "self-test":
        return cmd_self_test(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
