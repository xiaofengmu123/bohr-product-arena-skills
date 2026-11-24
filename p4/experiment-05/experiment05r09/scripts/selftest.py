#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：空输入 / 坏 JSON / 小 CSV / output_contract 回填 / validate / 确定性。

在 Skill 根目录执行：python scripts/selftest.py
退出码 0 表示全部通过。
"""
import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main


def check(name, fn):
    try:
        fn()
        print("[PASS] " + name)
        return True
    except Exception as exc:
        print("[FAIL] " + name + ": " + repr(exc))
        traceback.print_exc()
        return False


def run():
    results = []
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    work = os.path.join(tmp, "work")

    def t_empty():
        inp = os.path.join(tmp, "empty")
        out = os.path.join(tmp, "out_empty")
        os.makedirs(inp)
        assert main.build_skeleton(inp, out, work) == 0
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        assert r["case_id"]
        assert r["status"] == "insufficient_evidence"
        assert os.path.exists(os.path.join(out, "report.md"))
    results.append(check("空输入落盘 insufficient_evidence", t_empty))

    def t_bad_json():
        inp = os.path.join(tmp, "bad")
        out = os.path.join(tmp, "out_bad")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
            fh.write("t,v\n1,10\n2,12\n3,11\n")
        assert main.build_skeleton(inp, out, work) == 0
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        assert r["status"] == "partial"
        assert any("case.json" in d for d in r.get("degradations", []))
    results.append(check("坏 JSON 降级继续", t_bad_json))

    def t_validate_ok():
        inp = os.path.join(tmp, "ok")
        out = os.path.join(tmp, "out_ok")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-selftest",
                       "output_contract": {"fields": ["conclusion"]}}, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n- challenge_id: case-selftest\n")
        assert main.build_skeleton(inp, out, work) == 0
        path = os.path.join(out, "result.json")
        r = json.load(open(path, encoding="utf-8"))
        assert r["conclusion"] is None
        assert r["case_id"] == "case-selftest"
        main.set_value(path, "summary", "测试摘要", False)
        main.set_value(path, "interpretation.trends",
                       {"trend": "稳定",
                        "evidence": [{"locator": "data.csv#L2", "quote": "v,12"}]}, True)
        ok, msgs = main.validate_result(path)
        assert ok, msgs
    results.append(check("validate 与 output_contract 回填", t_validate_ok))

    def t_deterministic():
        inp = os.path.join(tmp, "det")
        out1 = os.path.join(tmp, "out_det1")
        out2 = os.path.join(tmp, "out_det2")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-det"}, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# t\n- challenge_id: case-det\n")
        main.build_skeleton(inp, out1, work)
        main.build_skeleton(inp, out2, work)
        r1 = json.load(open(os.path.join(out1, "result.json"), encoding="utf-8"))
        r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
        r1["runtime"] = {}
        r2["runtime"] = {}
        r1["change_log"] = []
        r2["change_log"] = []
        assert r1 == r2
    results.append(check("重复运行确定性", t_deterministic))

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(run())
