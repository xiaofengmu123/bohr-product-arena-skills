#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本（仅标准库）。构造最小演示输入，跑 init/profile/check 验证健壮性。

覆盖：正常路径、空目录、坏 JSON、缺 output_contract。
退出码 0 = 通过；非 0 = 存在失败断言。
"""
import json
import os
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir  # noqa: E402
import main  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("PASS  %s" % msg)
    else:
        print("FAIL  %s" % msg)
        FAILS.append(msg)


def make_input(root):
    case = {"case_id": "demo-001", "output_contract": {
        "figures": None, "statistics": None, "caption": None, "notes": None}}
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    csv = ("sepal_length,sepal_width,petal_length,petal_width,species\n"
           "5.1,3.5,1.4,0.2,setosa\n4.9,3.0,1.4,0.2,setosa\n"
           "7.0,3.2,4.7,1.4,versicolor\n6.4,3.2,4.5,1.5,versicolor\n"
           "6.3,3.3,6.0,2.5,virginica\n5.8,2.7,5.1,1.9,virginica\n")
    with open(os.path.join(root, "data.csv"), "w", encoding="utf-8") as f:
        f.write(csv)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\n对 species 分组比较 sepal_length，输出箱线图。\n")


def run_init(work, out, inp):
    return main.main(["init", "--input", inp, "--output", out, "--work", work])


def run_profile(work, out, inp):
    return main.main(["profile", "--input", inp, "--output", out, "--work", work])


def run_check(work, out, require=False, fail=False):
    argv = ["check", "--output", out, "--work", work]
    if require:
        argv.append("--require-contract")
    if fail:
        argv.append("--fail")
    return main.main(argv)


def test_happy():
    base = tempfile.mkdtemp(prefix="viz_selftest_")
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    make_input(inp)
    check(run_init(work, out, inp) == 0, "init 退出码 0")
    res = ir.load_json_robust(os.path.join(out, "result.json"))[0]
    check(res.get("case_id") == "demo-001", "case_id 回填")
    check(res.get("status") == "partial", "骨架 status=partial")
    check(res.get("figures") is None and res.get("statistics") is None, "契约字段回填 null")
    check(res.get("change_log") and res["change_log"][0]["step"] == "init", "change_log 有 init 记录")
    check(run_profile(work, out, inp) == 0, "profile 退出码 0")
    prof = ir.load_json_robust(os.path.join(work, "profile.json"))[0]
    check(len(prof.get("tables", [])) == 1, "profile 识别 1 张表")
    t = prof["tables"][0]
    num = [c["name"] for c in t["columns"] if c["dtype"] == "numeric"]
    cat = [c["name"] for c in t["columns"] if c["dtype"] == "categorical"]
    check(len(num) == 4 and "species" in cat, "列类型推断 numeric/categorical")
    ir_obj = ir.load_json_robust(os.path.join(work, "ir.json"))[0]
    check(len(ir_obj.get("recommendations", [])) >= 2, "生成选图推荐（含不推荐原因）")
    check(run_check(work, out) == 0, "check（无 --fail）退出码 0")
    check(run_check(work, out, require=True, fail=True) == 1,
          "check --require-contract --fail 在缺证据/产物时退出 1")
    return base


def test_empty():
    base = tempfile.mkdtemp(prefix="viz_selftest_empty_")
    inp = os.path.join(base, "input")
    os.makedirs(inp)
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    check(run_init(work, out, inp) == 0, "空目录 init 退出码 0")
    res = ir.load_json_robust(os.path.join(out, "result.json"))[0]
    check(res.get("status") == "insufficient_evidence", "空目录 status=insufficient_evidence")


def test_bad_json():
    base = tempfile.mkdtemp(prefix="viz_selftest_bad_")
    inp = os.path.join(base, "input")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as f:
        f.write("a,b\n1,2\n")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    check(run_init(work, out, inp) == 0, "坏 JSON init 退出码 0")
    res = ir.load_json_robust(os.path.join(out, "result.json"))[0]
    check(bool(res.get("case_id")), "坏 JSON 仍有 case_id")
    check(any("case.json" in d for d in res.get("degradations", [])), "坏 JSON 记入 degradations")


def test_no_contract():
    base = tempfile.mkdtemp(prefix="viz_selftest_noc_")
    inp = os.path.join(base, "input")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "demo-002"}, f)
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    check(run_init(work, out, inp) == 0, "缺契约 init 退出码 0")
    res = ir.load_json_robust(os.path.join(out, "result.json"))[0]
    check(res.get("analysis") is None, "缺契约回填兜底字段 analysis=null")


if __name__ == "__main__":
    test_happy()
    test_empty()
    test_bad_json()
    test_no_contract()
    if FAILS:
        print("\n%d 项失败" % len(FAILS))
        sys.exit(1)
    print("\n全部自检通过")
    sys.exit(0)
