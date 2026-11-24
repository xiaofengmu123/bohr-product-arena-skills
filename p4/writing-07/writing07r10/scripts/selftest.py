# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检（仅标准库，不联网、不依赖 /input 或 /app）。

直接 import core_impl 与 main 的 cmd_*，用临时目录跑三组场景：
A) 空输入目录：discover→scaffold→verify 全链路退出码 0，status=insufficient_evidence。
B) 坏 JSON 的 case.json：记录 errors/degradations，仍产出合法 result.json 骨架。
C) 最小真实案例（带 output_contract 与材料）：全链路通过，合同字段全量回填，
   从台账派生 evidence_map/change_log。
D) 确定性：同一输入 scaffold 两次字节一致；main.py < 30KB；文件数与总体积在限内。

用法：python3 -B scripts/selftest.py
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402
import main as M  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print("PASS  %s" % name)
    else:
        FAIL.append(name)
        print("FAIL  %s  %s" % (name, detail))


def run_main(command, input_dir, work_dir, output_dir):
    args = argparse.Namespace(
        command=command, input=input_dir, work=work_dir, output=output_dir
    )
    handler = {"discover": M.cmd_discover, "scaffold": M.cmd_scaffold, "verify": M.cmd_verify}
    return handler[command](args)


def tmpdir():
    return tempfile.mkdtemp(prefix="intro-skill-test-")


def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_pure_functions():
    check("contract_normalize_dict",
          C.normalize_contract({"intro": "text", "refs": []}) ==
          [{"name": "intro", "kind": "str"}, {"name": "refs", "kind": "list"}])
    check("contract_normalize_list",
          [f["name"] for f in C.normalize_contract(["a", "b"])] == ["a", "b"])
    check("placeholder_types",
          C.placeholder("list") == [] and C.placeholder("dict") == {} and C.placeholder("str") is None)
    sk = C.emit_skeleton("case-1", [{"name": "intro", "kind": "str"}])
    check("skeleton_contract_field", "intro" in sk and sk["intro"] is None)
    check("skeleton_status_partial", sk["status"] == "partial" and sk["case_id"] == "case-1")
    rep = C.validate_result({"case_id": "c", "status": "partial"},
                            [{"name": "x", "kind": "str"}], None, None)
    check("validate_missing_field", rep["ok"] is False and "missing_contract_field:x" in rep["errors"])


def test_scenario_empty():
    base = tmpdir()
    inp = os.path.join(base, "empty_input")
    work = os.path.join(base, "work")
    out = os.path.join(base, "output")
    os.makedirs(inp)
    rc1 = run_main("discover", inp, work, out)
    rc2 = run_main("scaffold", inp, work, out)
    result, _ = C.load_json(os.path.join(out, "result.json"), default=None)
    rc3 = run_main("verify", inp, work, out)
    check("empty_discover_exit0", rc1 == 0)
    check("empty_scaffold_exit0", rc2 == 0)
    check("empty_result_exists", isinstance(result, dict))
    check("empty_status_insufficient", result and result.get("status") == "insufficient_evidence",
          "got %r" % (result and result.get("status")))
    check("empty_verify_exit0", rc3 == 0, "rc=%d" % rc3)


def test_scenario_bad_json():
    base = tmpdir()
    inp = os.path.join(base, "input")
    work = os.path.join(base, "work")
    out = os.path.join(base, "output")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{ not valid json ")
    write(os.path.join(inp, "task.md"), "# task\n材料缺失场景")
    run_main("discover", inp, work, out)
    run_main("scaffold", inp, work, out)
    result, _ = C.load_json(os.path.join(out, "result.json"), default=None)
    spec, _ = C.load_json(os.path.join(work, "contract_spec.json"), default={})
    check("badjson_result_valid", isinstance(result, dict) and result.get("status") in C.STATUSES)
    check("badjson_recorded",
          bool((spec or {}).get("case_error")),
          "case_error=%r" % ((spec or {}).get("case_error")))


def test_scenario_full():
    base = tmpdir()
    inp = os.path.join(base, "input")
    work = os.path.join(base, "work")
    out = os.path.join(base, "output")
    os.makedirs(os.path.join(inp, "materials"))
    case = {
        "case_id": "case-demo-1",
        "task": "为一份神经科学论文撰写引言与相关工作",
        "audience": "期刊审稿人",
        "language": "zh",
        "output_contract": {"introduction": "string", "related_work": "string",
                            "research_gap": "string", "contributions": "list"},
    }
    write(os.path.join(inp, "case.json"), C.dumps(case))
    write(os.path.join(inp, "task.md"), "# 任务\n四段齐全，逐条证据")
    write(os.path.join(inp, "materials", "paper.md"),
          "## 背景\n本文提出方法 X，精度 92.3%。\n## 相关工作\n已有方法 Y 精度 88.1%。\n")
    run_main("discover", inp, work, out)
    run_main("scaffold", inp, work, out)
    # Agent 增量：写台账 + 稿件 + 回填合同字段。
    ledger = {
        "schema_version": 1,
        "case_id": "case-demo-1",
        "claims": [
            {"id": "C1", "section": "background", "claim": "方法 X 精度 92.3%",
             "quote": "本文提出方法 X，精度 92.3%。", "locator": "materials/paper.md#p1",
             "kind": "fact", "source_ref": "S1", "status": "supported", "used_in": ["manuscript.md"]},
            {"id": "C2", "section": "related_work", "claim": "方法 Y 精度 88.1%",
             "quote": "已有方法 Y 精度 88.1%。", "locator": "materials/paper.md#p1",
             "kind": "fact", "source_ref": "S2", "status": "supported", "used_in": ["manuscript.md"]},
            {"id": "C3", "section": "gap", "claim": "Y 精度低于 X，存在缺口",
             "quote": "已有方法 Y 精度 88.1%。", "locator": "materials/paper.md#p1",
             "kind": "gap", "source_ref": "S2", "status": "author_judgment", "used_in": ["manuscript.md"]},
            {"id": "C4", "section": "contribution", "claim": "提出方法 X",
             "quote": "本文提出方法 X", "locator": "materials/paper.md#p1",
             "kind": "contribution", "source_ref": "S1", "status": "supported", "used_in": ["manuscript.md"]},
        ],
        "sources": [
            {"key": "S1", "locator": "materials/paper.md#p1", "quote": "本文提出方法 X，精度 92.3%。", "kind": "fact"},
            {"key": "S2", "locator": "materials/paper.md#p1", "quote": "已有方法 Y 精度 88.1%。", "kind": "fact"},
        ],
        "changes": [
            {"at": "draft", "target": "manuscript.md", "before": None,
             "after": "背景段改写", "reason": "按四段结构重排"},
        ],
        "conflicts": [],
    }
    write(os.path.join(work, "ledger.json"), C.dumps(ledger))
    manuscript = (
        "# 研究背景\n本文提出方法 X [S1]。\n\n"
        "# 相关工作\n已有方法 Y 精度 88.1% [S2]。\n\n"
        "# 研究空白\nY 精度低于 X，存在缺口 [S2]。\n\n"
        "# 贡献\n提出方法 X [S1]。\n"
    )
    write(os.path.join(out, "manuscript.md"), manuscript)
    result, _ = C.load_json(os.path.join(out, "result.json"), default=None)
    result["introduction"] = "研究背景：本文提出方法 X。"
    result["related_work"] = "相关工作：方法 Y 精度 88.1%。"
    result["research_gap"] = "研究空白：Y 与 X 的精度差构成缺口。"
    result["contributions"] = ["提出方法 X"]
    result["status"] = "ok"
    result["sources"] = ledger["sources"]
    result["uncertainty"] = [{"item": "gap", "reason": "缺口由单一对比推出，属作者判断"}]
    result["change_log"] = ledger["changes"]
    write(os.path.join(out, "result.json"), C.dumps(result))

    rc = run_main("verify", inp, work, out)
    report, _ = C.load_json(os.path.join(work, "verify_report.json"), default={})
    ev, _ = C.load_json(os.path.join(out, "evidence_map.json"), default={})
    cl, _ = C.load_json(os.path.join(out, "change_log.json"), default=[])
    check("full_verify_ok", rc == 0 and (report or {}).get("ok") is True, "rc=%d report=%r" % (rc, report))
    check("full_contract_backfilled",
          all(k in result for k in ("introduction", "related_work", "research_gap", "contributions")))
    check("full_evidence_derived", set(ev.keys()) == {"S1", "S2"} and ev["S1"]["quote"].startswith("本文提出"))
    check("full_changelog_derived", isinstance(cl, list) and len(cl) == 1)


def test_determinism():
    base = tmpdir()
    inp = os.path.join(base, "input")
    work1 = os.path.join(base, "work1")
    work2 = os.path.join(base, "work2")
    out1 = os.path.join(base, "out1")
    out2 = os.path.join(base, "out2")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"),
          C.dumps({"case_id": "d-1", "output_contract": {"intro": "string"}}))
    run_main("discover", inp, work1, out1)
    run_main("discover", inp, work2, out2)
    run_main("scaffold", inp, work1, out1)
    run_main("scaffold", inp, work2, out2)
    r1, _, _ = C.read_text(os.path.join(out1, "result.json"))
    r2, _, _ = C.read_text(os.path.join(out2, "result.json"))
    check("determinism_scaffold", r1 == r2)


def test_budget_limits():
    root = os.path.dirname(os.path.abspath(__file__))
    main_path = os.path.join(root, "main.py")
    size = os.path.getsize(main_path)
    check("main_under_30kb", size < 30 * 1024, "size=%dB" % size)
    pkg = os.path.dirname(root)
    files = []
    total = 0
    for dirpath, _dirs, fnames in os.walk(pkg):
        for fn in fnames:
            fp = os.path.join(dirpath, fn)
            files.append(fp)
            total += os.path.getsize(fp)
    check("file_count_le_14", len(files) <= 14, "count=%d" % len(files))
    check("total_le_1_2mb", total <= int(1.2 * 1024 * 1024), "total=%dB" % total)


def main():
    try:
        test_pure_functions()
        test_scenario_empty()
        test_scenario_bad_json()
        test_scenario_full()
        test_determinism()
        test_budget_limits()
    except Exception:
        traceback.print_exc()
        FAIL.append("uncaught_exception")
    print("\n== %d passed, %d failed ==" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
