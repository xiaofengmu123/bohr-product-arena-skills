#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litsearch-audit 独立自检脚本。

用法：
  python3 -B scripts/selftest.py

覆盖两件事：
  1. 鲁棒性场景（空输入 / 坏 JSON / 缺输入目录 / 超大文件 / 坏编码 /
     缺参数 env 默认 / locator 与逐字核验 / 分级聚合 / 结果校验）；
  2. 一个端到端最小调用示例（skeleton -> locators -> verify -> grade -> validate），
     全部使用临时目录与标注为「示例」的合成内容，不冒充正式案例。

退出码：0 表示全部通过；非 0 表示存在失败项。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as core  # noqa: E402


def demo_end_to_end():
    """端到端最小调用示例（合成内容，仅演示接口）。返回失败数。"""
    failed = 0
    base = Path(tempfile.mkdtemp(prefix="litsk_demo_"))
    inp = base / "input"
    out = base / "output"
    work = base / "work"
    inp.mkdir(parents=True)

    # 示例 case.json：含 output_contract（properties 形式）
    contract = {"properties": {"candidates": {"type": "array"},
                               "assessment": {"type": "array"},
                               "summary": {"type": "string"}}}
    (inp / "case.json").write_text(
        json.dumps({"case_id": "demo-case-001", "output_contract": contract},
                   ensure_ascii=False), encoding="utf-8")
    # 示例材料
    (inp / "materials").mkdir()
    (inp / "materials" / "paper.md").write_text(
        "# Demo study\n\nWe randomized 120 patients into two arms. "
        "Blinding was not reported.\n\nThe trial was registered before enrollment.",
        encoding="utf-8")

    def step(name, ok, msg):
        nonlocal failed
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else " " + msg))
        if not ok:
            failed += 1

    path, result, errs = core.run_skeleton(str(inp), str(out), str(work))
    step("demo_skeleton", Path(path).exists() and result.get("case_id") == "demo-case-001",
         "path=%s errs=%s" % (path, errs))
    # 契约字段回填验证
    loaded, _ = core.load_json_lenient(path)
    step("demo_contract_echo", loaded and "summary" in loaded and "candidates" in loaded,
         "contract echo missing")

    # locators
    text, errs = core.read_text(str(inp / "materials" / "paper.md"))
    records, lmap = core.make_locators(text or "", "paper")
    step("demo_locators", bool(records) and any(r["locator"] == "paper#p1" for r in records),
         "records=%d" % len(records))

    # verify（内容位于第 2 段）
    okq, _ = core.verify_quote("randomized 120 patients", "paper#p2", lmap)
    step("demo_verify", okq, "quote not found")

    # grade（示例判定）
    assm = [{"dimension": "study_design", "checkpoint": "blinding",
             "critical": True, "verdict": "not_satisfied",
             "judgment": "未报告盲法", "locator": "paper#p2",
             "quote": "Blinding was not reported."}]
    g = core.grade(assm)
    step("demo_grade", g["grade"] == "high", "grade=%s" % g)

    # 组装并校验示例结果
    demo = build_demo_result(result, assm, records)
    core.atomic_write_json(str(out / "result.json"), demo)
    errs_v, warns_v = core.validate_result(demo)
    step("demo_validate", not errs_v, "errs=%s" % errs_v)
    print("demo artifacts in:", out)
    return failed


def build_demo_result(skeleton, assm, records):
    """示例结果组装（仅演示，标注 example）。"""
    sk = dict(skeleton)
    sk["status"] = "ok"
    sk["assessment"] = assm
    sk["sources"] = [{"id": "paper", "type": "md", "path_or_uri": "materials/paper.md",
                      "locator": "paper#p2",
                      "quote": "We randomized 120 patients into two arms.",
                      "note": "示例材料"}]
    sk["uncertainty"] = [{"item": "blinding", "basis": "quote: Blinding was not reported."}]
    sk["limitations"] = ["仅示例：非正式案例"]
    sk["runtime"]["finish_epoch"] = core.time.time()
    return sk


def main():
    print("=== 鲁棒性自检 ===")
    passed, failed, details = core.run_self_test()
    for line in details:
        print(line)
    print("robustness: %d passed, %d failed" % (passed, failed))

    print("\n=== 端到端最小调用示例（合成内容，仅演示） ===")
    demo_failed = demo_end_to_end()

    total = failed + demo_failed
    print("\nselftest total: %d failed" % total)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
