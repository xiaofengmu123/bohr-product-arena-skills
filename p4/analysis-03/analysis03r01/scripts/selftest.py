#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""包内自检：用一个临时合成案例跑通 init/evidence/update/check 全链路，
并覆盖空输入与坏 JSON 两种降级场景。仅标准库，不依赖 matplotlib。
运行方式（Skill 根目录）：python3 scripts/selftest.py
退出码：全部通过为 0；任何断言失败为 1。合成数据仅用于自检，不是正式答案。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(args, **kw):
    return subprocess.run([PY, MAIN] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def make_input(root, with_case=True, with_contract=True, bad_json=False):
    ind = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(ind, exist_ok=True)
    if with_case:
        contract = {"figure": {"path": None, "format": []}, "summary": None} if with_contract else None
        case = {"case_id": "selftest-001", "id": "selftest-001",
                "output_contract": contract}
        if bad_json:
            open(os.path.join(ind, "case.json"), "w", encoding="utf-8").write("{bad json")
        else:
            json.dump(case, open(os.path.join(ind, "case.json"), "w", encoding="utf-8"),
                      ensure_ascii=False)
    with open(os.path.join(ind, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 自检任务\n\n对 data.csv 做可视化并交付结果。\n")
    with open(os.path.join(ind, "data.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write("group,value\nA,5.1\nB,7.2\n")
    return ind, out, work


def check(cond, msg):
    if not cond:
        print("FAIL: " + msg)
        sys.exit(1)
    print("ok: " + msg)


def main():
    root = tempfile.mkdtemp(prefix="sci-viz-evidence-selftest-")

    # 1) 正常链路
    ind, out, work = make_input(root)
    r = run(["init", "--input", ind, "--output", out, "--work", work])
    check(r.returncode == 0, "init 退出码 0")
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(res.get("status") == "partial", "骨架 status=partial")
    check(res.get("case_id") == "selftest-001", "case_id 正确回填")
    check("figure" in res and "summary" in res, "契约字段已回填")

    r = run(["evidence", "add", "--output", out, "--input", ind,
             "--claim", "组 A 的 value = 5.1", "--locator", "data.csv#r1c2",
             "--quote", "5.1", "--kind", "value", "--derivation", "组 A 行第 2 列"])
    check(r.returncode == 0, "evidence add 退出码 0")
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(len(res["evidence_map"]) == 1 and res["evidence_map"][0]["id"] == "ev-001",
          "evidence_map 生成 ev-001")

    r = run(["evidence", "verify", "--output", out, "--input", ind])
    check(r.returncode == 0, "evidence verify 退出码 0")
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    rec = res["evidence_map"][0]
    check(rec["verified"] is True, "quote 机械复核通过")
    check(rec["observed"] == "5.1", "observed 写回实际文本")

    r = run(["update", "--output", out, "--work", work, "--set", 'status="ok"',
             "--log", "自检完成"])
    check(r.returncode == 0, "update 退出码 0")

    r = run(["check", "--output", out, "--work", work, "--require-contract", "--fail"])
    check(r.returncode == 0, "check --fail 全通过时退出码 0")
    check('"result": "OK"' in r.stdout or '"result": "WARN"' in r.stdout,
          "check 报告 OK/WARN")

    # 2) 空输入目录
    root2 = tempfile.mkdtemp(prefix="sci-viz-evidence-empty-")
    ind2 = os.path.join(root2, "input"); os.makedirs(ind2)
    out2 = os.path.join(root2, "output"); work2 = os.path.join(root2, "work")
    r = run(["init", "--input", ind2, "--output", out2, "--work", work2])
    check(r.returncode == 0, "空输入 init 退出码 0")
    res2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
    check(res2.get("status") == "partial", "空输入仍产出合法骨架")
    check(isinstance(res2.get("degradations"), list), "空输入记录 degradations")

    # 3) 坏 JSON case.json
    root3 = tempfile.mkdtemp(prefix="sci-viz-evidence-badjson-")
    ind3, out3, work3 = make_input(root3, bad_json=True)
    r = run(["init", "--input", ind3, "--output", out3, "--work", work3])
    check(r.returncode == 0, "坏 JSON init 退出码 0")
    res3 = json.load(open(os.path.join(out3, "result.json"), encoding="utf-8"))
    check(res3.get("status") == "partial", "坏 JSON 仍产出合法骨架")

    for rdir in (root, root2, root3):
        shutil.rmtree(rdir, ignore_errors=True)
    print("selftest: ALL PASS")


if __name__ == "__main__":
    main()
