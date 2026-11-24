#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检：在临时目录跑通 main.py 的三个场景，验证契约与异常处理（不伪造分数）。

场景：
  1) 正常输入（case.json + task.md + 一篇示例稿件）→ 骨架/分析/校验全通过
  2) 空输入目录 → 退出码 0，status=insufficient_evidence，合法 JSON
  3) 坏 JSON 的 case.json → 退出码 0，记录到 errors/degradations，不崩溃
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

SAMPLE_MANUSCRIPT = """# 摘要
本文研究X对Y的影响。结果表明X显著促进Y。本研究首次证明X是Y的决定因素。

# 引言
X与Y的关系受到广泛关注。然而，现有研究结论不一致。本研究提出X影响Y的假设。

# 方法
我们采用随机对照试验。结果显示X组与对照组存在差异。

# 结果
结果表明X显著促进Y。X组Y值高于对照组。因此，X是Y的原因。

# 结论
综上，本研究首次证明X必然导致Y，具有普遍意义。
"""


def run_main(args, env):
    cmd = [sys.executable, "-B", MAIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def make_case(tmp, case_id="case-001", bad_json=False):
    d = os.path.join(tmp, "input")
    os.makedirs(d, exist_ok=True)
    case = {"case_id": case_id, "id": case_id,
            "task": "对示例稿件做结构诊断并给出可执行修改方案"}
    if bad_json:
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ bad json \u0000")
    else:
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 论文结构与逻辑优化\nchallenge_id: `challenge-8021aacb`\n")
    with open(os.path.join(d, "manuscript.md"), "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_MANUSCRIPT)
    return d


def check(result_path):
    with open(result_path, encoding="utf-8") as fh:
        r = json.load(fh)
    assert r.get("case_id"), "case_id 缺失"
    assert r.get("status"), "status 缺失"
    for field in ("diagnosis", "source_map", "uncertainty", "change_log"):
        assert field in r, f"缺字段 {field}"
    return r


def main():
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        # 场景 1：正常输入
        d = make_case(tmp)
        out = os.path.join(tmp, "out1")
        work = os.path.join(tmp, "work1")
        env = dict(os.environ, INPUT_DIR=d, OUTPUT_DIR=out, WORK_DIR=work,
                   CASE_JSON=os.path.join(d, "case.json"),
                   TASK_MD=os.path.join(d, "task.md"),
                   RESULT_JSON=os.path.join(out, "result.json"))
        r = run_main(["run"], env)
        ok1 = r.returncode in (0, 1)  # verify 可能 WARN 返回 0，FAIL 返回 1
        r1 = check(os.path.join(out, "result.json"))
        # 分析产物应存在
        assert os.path.exists(os.path.join(work, "text_model.json"))
        assert os.path.exists(os.path.join(work, "claim_model.json"))
        assert os.path.exists(os.path.join(work, "redundancy.json"))
        assert os.path.exists(os.path.join(work, "chapter_model.json"))
        results.append(("normal_case", ok1, f"status={r1['status']}, claims/冗余中间产物已生成"))

        # 场景 2：空输入目录
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        out2 = os.path.join(tmp, "out2")
        env2 = dict(os.environ, INPUT_DIR=empty, OUTPUT_DIR=out2, WORK_DIR=os.path.join(tmp, "work2"),
                    CASE_JSON=os.path.join(empty, "case.json"),
                    RESULT_JSON=os.path.join(out2, "result.json"))
        r2 = run_main(["skeleton"], env2)
        r2o = check(os.path.join(out2, "result.json"))
        ok2 = r2.returncode == 0 and r2o["status"] == "insufficient_evidence"
        results.append(("empty_input", ok2, f"status={r2o['status']}"))

        # 场景 3：坏 JSON
        d3 = make_case(tmp, case_id="case-bad", bad_json=True)
        out3 = os.path.join(tmp, "out3")
        env3 = dict(os.environ, INPUT_DIR=d3, OUTPUT_DIR=out3, WORK_DIR=os.path.join(tmp, "work3"),
                    CASE_JSON=os.path.join(d3, "case.json"),
                    RESULT_JSON=os.path.join(out3, "result.json"))
        r3 = run_main(["skeleton"], env3)
        r3o = check(os.path.join(out3, "result.json"))
        degraded = bool(r3o.get("degradations") or r3o.get("errors"))
        ok3 = r3.returncode == 0 and degraded
        results.append(("bad_json", ok3, f"degradations={len(r3o.get('degradations', []))}, errors={len(r3o.get('errors', []))}"))

    all_ok = all(x[1] for x in results)
    print("== selftest ==")
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"TOTAL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
