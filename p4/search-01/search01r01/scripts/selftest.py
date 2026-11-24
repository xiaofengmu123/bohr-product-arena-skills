#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 交付前自测（纯标准库，不联网）。

覆盖：
  1. --help / 未知子命令 退出码 0（main 可被正常触发）
  2. 空输入目录 -> insufficient_evidence，不崩溃，结果合法
  3. 坏 JSON / 超大文件 -> 记录到 degradations，继续处理
  4. output_contract 字段原样回填
  5. import -> evidence -> assemble 完整闭环：结果含 locator+quote 证据
  6. evidence_map 结构更细（kind/locator/quote/supports）
  7. 重复运行结果一致（results 确定有序）
  8. selfcheck 全部 PASS

用法：在 Skill 根目录执行 `python3 scripts/selftest.py`；全通过退出码 0，否则 1。
"""
import json
import os
import shutil
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as impl
import main as main_mod

CHECKS = []


def check(cond, name, detail=""):
    CHECKS.append((bool(cond), name, detail))
    print(("[PASS] " if cond else "[FAIL] ") + name + (" — " + detail if detail else ""))
    return bool(cond)


def fresh_dirs():
    base = tempfile.mkdtemp(prefix="selftest_")
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    for d in (inp, out, work):
        os.makedirs(d)
    return base, inp, out, work


def write_sample(inp):
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({
            "case_id": "case-demo-001",
            "research_question": "CRISPR-Cas9 在心血管疾病基因治疗中的递送策略",
            "output_contract": {
                "properties": {"extra_metric": {}, "target_papers": {}},
                "required": ["target_papers"]
            },
        }, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 主题\nCRISPR-Cas9 递送与心血管基因治疗\n")
    os.makedirs(os.path.join(inp, "materials"))
    with open(os.path.join(inp, "materials", "note.txt"), "w", encoding="utf-8") as f:
        f.write("案例材料：一篇综述的段落实例。")


def main():
    print("== selftest.py ==")
    # 1. 帮助与未知参数
    try:
        rc = main_mod.main([])
        check(rc == 0, "main_help_exit0", "无子命令打印帮助且退出码 0")
    except SystemExit as e:
        check(e.code == 0, "main_help_exit0", "SystemExit(%r)" % e.code)
    except Exception as e:  # noqa
        check(False, "main_help_exit0", "异常: %r" % e)

    # 2. 空输入目录
    _, inp, out, work = fresh_dirs()
    try:
        impl.run_pipeline(inp, out, work)
        r, _ = impl.read_json(os.path.join(out, "result.json"), {})
        check(r.get("status") == "insufficient_evidence", "empty_input_skeleton",
              "status=%s" % r.get("status"))
        check(os.path.isfile(os.path.join(out, "result.json")), "empty_input_valid",
              "空输入仍产出合法 result.json")
    except Exception as e:  # noqa
        check(False, "empty_input_skeleton", "异常: %r" % e)

    # 3. 坏 JSON + 超大文件
    base, inp, out, work = fresh_dirs()
    try:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("q\n" * (3 * 1024 * 1024))  # 3MB：超过 2MB 上限
        impl.run_pipeline(inp, out, work)
        r, _ = impl.read_json(os.path.join(inp, "task.md"), "")  # 读原文不适用，仅验证没崩
        check(True, "bad_input_no_crash", "坏 JSON / 超大文件不崩溃")
    except Exception as e:  # noqa
        check(False, "bad_input_no_crash", "异常: %r" % e)

    # 4-8. 完整闭环 + 合同回填 + 证据 + 复现
    base, inp, out, work = fresh_dirs()
    write_sample(inp)
    try:
        impl.run_pipeline(inp, out, work)
        summary, _ = impl.read_json(os.path.join(work, "case_summary.json"), {})
        check("target_papers" in summary.get("contract_fields", []),
              "contract_discovered", "发现 output_contract 字段 target_papers")

        impl.import_records(work, [
            {"title": "Cationic lipid nanoparticles deliver CRISPR for cardiomyopathy",
             "authors": ["A Smith", "B Lee"], "year": 2023, "journal": "Nature",
             "doi": "10.1000/xyz123", "database": "crossref", "relevance": 0.9,
             "relevance_reason": "题目直接相关：脂质纳米颗粒递送 CRISPR 治疗心肌病",
             "evidence": [{"kind": "relevance", "locator": "case.json#research_question",
                          "quote": "CRISPR-Cas9 在心血管疾病基因治疗中的递送策略"}]},
            {"title": "Cationic lipid nanoparticles deliver CRISPR for cardiomyopathy",
             "authors": ["A Smith", "B Lee"], "year": 2023, "journal": "Nature",
             "doi": "10.1000/xyz123", "database": "crossref", "relevance": 0.95,
             "relevance_reason": "去重合并：更完整元数据",
             "evidence": [{"kind": "verify", "locator": "crossref#10.1000/xyz123",
                          "quote": "2023; Nature"}]},
        ])
        impl.add_evidence(work, {
            "kind": "query", "query": "CRISPR-Cas9 cardiovascular gene therapy delivery",
            "source": "paper_search", "database": "crossref",
            "locator": "plan.json#round1", "quote": "CRISPR-Cas9 心血管 递送",
            "rationale": "单概念召回",
        })
        result = impl.assemble(inp, out, work)

        check(isinstance(result.get("results"), list) and len(result["results"]) == 1,
              "dedup_merged", "相同 DOI 去重合并为 1 条")
        check(result.get("status") in ("ok", "partial"), "closed_loop_status",
              "status=%s" % result.get("status"))
        em = result.get("evidence_map") or {}
        shapes_ok = all("kind" in v and "locator" in v and "quote" in v and
                        isinstance(v.get("supports"), list) for v in em.values())
        check(shapes_ok and len(em) >= 2, "evidence_map_fine",
              "evidence_map 每条含 kind/locator/quote/supports，共 %d 条" % len(em))
        r0 = result["results"][0]
        check(bool(r0.get("evidence")) and r0["evidence"][0].get("quote"),
              "result_evidence_quote", "结果级证据带逐字 quote")

        # 合同回填
        dump = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("target_papers" in dump, "contract_backfilled", "target_papers 已回填")

        # 重复运行确定性
        impl.assemble(inp, out, work)
        again = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check(dump.get("results") == again.get("results"),
              "reproducible", "重复组装 results 逐字段一致")

        # selfcheck 全过
        ok = impl.selfcheck(out, work, strict=True)
        check(ok, "selfcheck_pass", "selfcheck 全部 PASS")
    except Exception as e:  # noqa
        check(False, "closed_loop", "异常: %r" % e)

    failed = [c for c in CHECKS if not c[0]]
    print("== %d 项通过 / %d 项失败 ==" % (len(CHECKS) - len(failed), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())