#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：验证 main.py 脚手架与 result.json 契约。

两种模式：
  --self                        合成场景自检（空目录/坏 JSON/坏编码/最小契约/材料扫描）
  --check --output DIR --case FILE  校验已生成 result.json 与证据表是否满足契约

只使用标准库；重复运行结果一致；任何场景不崩溃，退出码 0 = 全部通过。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import core_impl as ci  # noqa: E402


def run_main(input_dir, output_dir, work_dir):
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-B", MAIN,
         "--input", input_dir, "--output", output_dir, "--work", work_dir],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def write_case(input_dir, case):
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)


def check_result(output_dir, case_path=None):
    """返回 (errors, result)。"""
    errs = []
    rp = os.path.join(output_dir, "result.json")
    if not os.path.isfile(rp):
        return ["result.json 不存在"], None
    try:
        with open(rp, encoding="utf-8") as f:
            result = json.load(f)
    except Exception as e:
        return ["result.json 不是合法 JSON: %s" % e], None
    if not isinstance(result, dict):
        return ["result.json 顶层必须是对象"], result
    if not (result.get("case_id") or "").strip():
        errs.append("case_id 缺失")
    if result.get("status") not in ci.ALLOWED_STATUS:
        errs.append("status 非法: %r" % result.get("status"))
    for k in ("status", "sources", "uncertainty", "change_log"):
        if k not in result:
            errs.append("缺少诚实字段: %s" % k)
    if not isinstance(result.get("uncertainty"), dict):
        errs.append("uncertainty 必须是对象")
    if case_path and os.path.isfile(case_path):
        case, cerrs = ci.read_json(case_path)
        if case is not None:
            fields, _spec = ci.parse_contract(case)
            for name, _default in fields:
                if name not in result:
                    errs.append("缺少契约字段: %s" % name)
    return errs, result


def scenario_empty(tmp):
    input_dir = os.path.join(tmp, "empty_in")
    os.makedirs(input_dir, exist_ok=True)
    out, work = os.path.join(tmp, "empty_out"), os.path.join(tmp, "empty_work")
    proc = run_main(input_dir, out, work)
    errs, result = check_result(out)
    if proc.returncode != 0:
        errs.append("退出码非 0: %d" % proc.returncode)
    if result and result.get("status") != "insufficient_evidence":
        errs.append("空目录应为 insufficient_evidence，实际 %r" % result.get("status"))
    return "空目录", errs


def scenario_bad_json(tmp):
    input_dir = os.path.join(tmp, "bad_in")
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        f.write('{ "case_id": "x", "output_contract": { "a": 1  ')
    out, work = os.path.join(tmp, "bad_out"), os.path.join(tmp, "bad_work")
    proc = run_main(input_dir, out, work)
    errs, result = check_result(out)
    if proc.returncode != 0:
        errs.append("退出码非 0: %d" % proc.returncode)
    if result and result.get("status") != "input_error":
        errs.append("坏 JSON 应为 input_error，实际 %r" % result.get("status"))
    return "坏JSON", errs


def scenario_bad_encoding(tmp):
    input_dir = os.path.join(tmp, "enc_in")
    os.makedirs(input_dir, exist_ok=True)
    raw = ('{"case_id":"caf\\u00e9","research_question":"\\u00e9tudes",'
           '"output_contract":{"properties":{"review_draft":{"type":"string"},'
           '"references":{"type":"array"}}}}').encode("latin-1")
    with open(os.path.join(input_dir, "case.json"), "wb") as f:
        f.write(raw)
    out, work = os.path.join(tmp, "enc_out"), os.path.join(tmp, "enc_work")
    proc = run_main(input_dir, out, work)
    errs, result = check_result(out, os.path.join(input_dir, "case.json"))
    if proc.returncode != 0:
        errs.append("退出码非 0: %d" % proc.returncode)
    if result and result.get("case_id") != "café":
        errs.append("坏编码 case_id 未恢复: %r" % result.get("case_id"))
    return "坏编码", errs


def scenario_minimal_contract(tmp):
    input_dir = os.path.join(tmp, "min_in")
    os.makedirs(input_dir, exist_ok=True)
    case = {
        "case_id": "case-42",
        "research_question": "在真实文献上如何综合证据并说明边界？",
        "output_contract": {
            "properties": {
                "research_question": {"type": ["string", "null"]},
                "screening_log": {"type": "array", "items": {"type": "object"}},
                "review_draft": {"type": "string"},
                "references": {"type": "array"},
            }
        },
    }
    write_case(input_dir, case)
    out, work = os.path.join(tmp, "min_out"), os.path.join(tmp, "min_work")
    proc = run_main(input_dir, out, work)
    case_path = os.path.join(input_dir, "case.json")
    errs, result = check_result(out, case_path)
    if proc.returncode != 0:
        errs.append("退出码非 0: %d" % proc.returncode)
    if result and result.get("case_id") != "case-42":
        errs.append("case_id 错误: %r" % result.get("case_id"))
    if result and result.get("status") != "partial":
        errs.append("最小契约应为 partial，实际 %r" % result.get("status"))
    if result and result.get("research_question") != case["research_question"]:
        errs.append("research_question 未回填: %r" % result.get("research_question"))
    if result and result.get("references") != []:
        errs.append("数组字段 references 应为 []: %r" % result.get("references"))
    if result and result.get("review_draft") is not None:
        errs.append("字符串字段 review_draft 应为 null: %r" % result.get("review_draft"))
    return "最小契约", errs


def scenario_materials(tmp):
    input_dir = os.path.join(tmp, "mat_in")
    os.makedirs(input_dir, exist_ok=True)
    case = {
        "case_id": "case-mat",
        "output_contract": {"properties": {"review_draft": {"type": "string"}}},
    }
    write_case(input_dir, case)
    with open(os.path.join(input_dir, "fake.pdf"), "wb") as f:
        f.write(b"%PDF-1.4\nnot a real pdf body")
    with open(os.path.join(input_dir, "note.txt"), "w", encoding="utf-8") as f:
        f.write("示例材料（仅自检用）")
    out, work = os.path.join(tmp, "mat_out"), os.path.join(tmp, "mat_work")
    proc = run_main(input_dir, out, work)
    errs, result = check_result(out, os.path.join(input_dir, "case.json"))
    if proc.returncode != 0:
        errs.append("退出码非 0: %d" % proc.returncode)
    inv_path = os.path.join(work, "inventory.json")
    if not os.path.isfile(inv_path):
        errs.append("inventory.json 未生成")
    else:
        inv = json.load(open(inv_path, encoding="utf-8"))
        if len(inv.get("materials", [])) != 2:
            errs.append("材料数应为 2，实际 %d" % len(inv.get("materials", [])))
    if result and not result.get("degradations"):
        errs.append("假 PDF 抽取失败或缺 pdftotext 应记入 degradations")
    return "材料扫描", errs


def scenario_evidence_validation(_tmp=None):
    good = {
        "evidence_id": "E1", "source_id": "S1",
        "locator": "materials/paper.md#p3", "quote": "原文逐字摘录",
        "claim": "该证据支撑的论断", "strength": "suggestive", "field": "结果",
    }
    bad = {"evidence_id": "E1"}
    errs = []
    if ci.validate_evidence_row(good):
        errs.append("合法证据行被误判: %r" % ci.validate_evidence_row(good))
    if not ci.validate_evidence_row(bad):
        errs.append("缺字段证据行未被识别")
    return "证据行校验", errs


def run_self():
    scenarios = [
        scenario_empty,
        scenario_bad_json,
        scenario_bad_encoding,
        scenario_minimal_contract,
        scenario_materials,
        scenario_evidence_validation,
    ]
    failures = 0
    for fn in scenarios:
        tmp = tempfile.mkdtemp(prefix="selftest_")
        try:
            name, errs = fn(tmp)
        except Exception as e:  # 任何异常都算该场景失败
            name, errs = fn.__name__, ["场景抛出异常: %r" % e]
        if errs:
            failures += 1
            print("FAIL  %s" % name)
            for e in errs:
                print("      - " + e)
        else:
            print("PASS  %s" % name)
    print("自检完成: %d/%d 通过" % (len(scenarios) - failures, len(scenarios)))
    return 0 if failures == 0 else 1


def run_check(output_dir, case_path):
    errs, result = check_result(output_dir, case_path)
    # 若存在证据表，逐行校验。
    et_path = os.path.join(output_dir, "evidence_table.jsonl")
    if os.path.isfile(et_path):
        with open(et_path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception as e:
                    errs.append("evidence_table.jsonl 第 %d 行非法 JSON: %s" % (i, e))
                    continue
                errs.extend("evidence_table 第 %d 行: %s" % (i, m)
                            for m in ci.validate_evidence_row(row))
    if errs:
        print("CHECK FAIL")
        for e in errs:
            print("  - " + e)
        return 1
    print("CHECK PASS  status=%s" % (result or {}).get("status"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="文献综述 Skill 自检")
    ap.add_argument("--self", action="store_true", help="合成场景自检")
    ap.add_argument("--check", action="store_true", help="校验已生成 result.json")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    args = ap.parse_args(argv)
    if args.check:
        return run_check(args.output, args.case)
    return run_self()


if __name__ == "__main__":
    sys.exit(main())
