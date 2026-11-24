#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检（只用标准库，不产生任何正式案例答案）。

在临时目录中构造**明确标注为合成数据**的微型输入，覆盖四种场景：
  1) 正常（含一个明显离群值的小 CSV + output_contract 回填）
  2) 空目录
  3) 坏 JSON
  4) 坏编码文本
并校验：CLI 退出码、result.json 合法性、facts.json 生成、report/evidence_map 生成、
finalize runtime、以及两次 profile 的确定性（忽略 generated_at 与 runtime）。

全部通过时退出码 0，任何一项失败退出码 1。
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

CASE_JSON = {
    "case_id": "selftest-case-000",
    "output_contract": {
        "required": ["primary_result", "notes"],
        "primary_result": {"type": "object"},
        "notes": {"type": "string"},
    },
}
TASK_MD = "# 合成自检任务\n\n- challenge_id: `selftest-case-000`\n"
CSV = "sample,time_s,response\nA,0,1.0\nA,1,1.1\nA,2,1.05\nB,0,2.0\nB,1,2.2\nB,2,999.0\n"


def run(args, cwd=None):
    return subprocess.run([PY, "-B", MAIN] + args, cwd=cwd,
                          capture_output=True, text=True)


def check(name, cond, detail=""):
    if not cond:
        print("FAIL %s %s" % (name, detail))
        sys.exit(1)
    print("ok   %s" % name)


def build_case(root, case_json=CASE_JSON, task_md=TASK_MD, csv=CSV,
               bad_json=None, bad_enc=None):
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case_json, f, ensure_ascii=False)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
        f.write(task_md)
    if csv is not None:
        with open(os.path.join(root, "data", "measure.csv"), "w", encoding="utf-8") as f:
            f.write(csv)
    if bad_json is not None:
        with open(os.path.join(root, "data", "broken.json"), "w", encoding="utf-8") as f:
            f.write(bad_json)
    if bad_enc is not None:
        with open(os.path.join(root, "data", "legacy.txt"), "wb") as f:
            f.write(bad_enc)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        # ---- 场景 1：正常 + 坏 JSON + 坏编码（latin-1 可降级）----
        # 注意：输出/工作目录与输入目录分离（与运行时 /input 与 /app/output 分离一致）
        case1 = os.path.join(tmp, "case1_input")
        os.makedirs(case1)
        build_case(case1, bad_json="{not valid json",
                   bad_enc="colonne\tr\xe9sultat\n1\t12.5\n".encode("latin-1"))
        out1 = os.path.join(tmp, "case1_output")
        work1 = os.path.join(tmp, "case1_work")
        os.makedirs(out1)

        rc = run(["skeleton", "--input", case1, "--output", out1])
        check("skeleton exit=0", rc.returncode == 0, rc.stderr)
        r1 = load_json(os.path.join(out1, "result.json"))
        check("skeleton case_id", r1["case_id"] == "selftest-case-000")
        check("skeleton status partial", r1["status"] == "partial")
        check("skeleton output_contract 回填",
              "primary_result" in r1 and "notes" in r1)
        for k in ("interpretation", "experiment_plan", "evidence_and_risk",
                  "confirmation_items", "sources", "uncertainty", "change_log"):
            check("skeleton 顶层键 " + k, k in r1)

        rc = run(["profile", "--input", case1, "--work", work1, "--output", out1])
        check("profile exit=0", rc.returncode == 0, rc.stderr)
        facts = load_json(os.path.join(out1, "facts.json"))
        rels = [f["rel"] for f in facts["files"]]
        check("profile 发现 CSV", any("measure.csv" in r for r in rels))
        check("profile 记录坏 JSON",
              any("broken.json" in d.get("file", "") for d in facts["degradations"]))
        check("profile 标记候选异常",
              len(facts.get("candidate_anomalies", [])) >= 1)

        # 用手工填充五段结论（合成，仅供自检走通校验链）
        r1["interpretation"]["trends"] = [{
            "claim": "合成趋势", "direction": "增",
            "evidence": [{"locator": "data/measure.csv#R1.C3",
                          "quote": "1.0"}],
        }]
        r1["interpretation"]["anomalies"] = [{
            "claim": "合成异常", "subject": "B",
            "evidence": [{"locator": "data/measure.csv#R6.C3",
                          "quote": "999.0"}],
        }]
        r1["interpretation"]["possible_causes"] = [{
            "claim": "合成原因", "supporting_evidence": [],
            "excluding_evidence": [],
            "evidence": [{"locator": "data/measure.csv#R6.C3", "quote": "999.0"}],
        }]
        r1["interpretation"]["verification_steps"] = [{
            "claim": "合成复核", "target": "data/measure.csv#R6",
            "expected_if_supported": "x", "decision_rule": "y",
            "evidence": [{"locator": "data/measure.csv#R6.C3", "quote": "999.0"}],
        }]
        r1["interpretation"]["followup_experiments"] = [{
            "claim": "合成补做", "reason": "r", "expected_outcome": "o",
            "evidence": [{"locator": "data/measure.csv#R6.C3", "quote": "999.0"}],
        }]
        with open(os.path.join(out1, "result.json"), "w", encoding="utf-8") as f:
            json.dump(r1, f, ensure_ascii=False)

        rc = run(["validate", "--input", os.path.join(out1, "result.json")])
        check("validate exit=0", rc.returncode == 0, rc.stdout + rc.stderr)

        rc = run(["report", "--input", os.path.join(out1, "result.json"),
                  "--output", out1])
        check("report exit=0", rc.returncode == 0, rc.stderr)
        check("report.md 存在", os.path.exists(os.path.join(out1, "report.md")))
        check("evidence_map.json 存在",
              os.path.exists(os.path.join(out1, "evidence_map.json")))

        rc = run(["finalize", "--input", os.path.join(out1, "result.json")])
        check("finalize exit=0", rc.returncode == 0, rc.stderr)
        r1f = load_json(os.path.join(out1, "result.json"))
        check("finalize runtime.finished_at",
              isinstance(r1f["runtime"].get("finished_at"), str))

        # ---- 场景 2：空目录 ----
        case2 = os.path.join(tmp, "case2")
        os.makedirs(case2)
        out2 = os.path.join(case2, "out")
        rc = run(["skeleton", "--input", case2, "--output", out2])
        check("空目录 skeleton exit=0", rc.returncode == 0, rc.stderr)
        r2 = load_json(os.path.join(out2, "result.json"))
        check("空目录 status=insufficient_evidence",
              r2["status"] == "insufficient_evidence")

        # ---- 场景 3：确定性 ----
        facts2 = None
        rc = run(["profile", "--input", case1, "--work", work1, "--output", out1])
        facts2 = load_json(os.path.join(out1, "facts.json"))
        f1 = {k: v for k, v in facts.items() if k != "generated_at"}
        f2 = {k: v for k, v in facts2.items() if k != "generated_at"}
        check("两次 profile 确定一致", f1 == f2)

        print("SELFTEST ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
