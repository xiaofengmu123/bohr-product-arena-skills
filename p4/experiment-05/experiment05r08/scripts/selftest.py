#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（仅标准库，确定性）。

在系统临时目录生成一小份【合成示例】数据（明确标记，不冒充正式案例），
跑通 skeleton → inspect → build → validate → finalize 全链路，并覆盖：
  1. 正常含数据目录
  2. 空输入目录（应 insufficient_evidence、退出码 0）
  3. 坏 JSON / 坏编码（应记录 errors/degradations 并继续）

不编造评测分数；只断言流程契约与产物合法性。用法：
  python scripts/selftest.py
退出码 0 表示自检通过。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=ROOT):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          cwd=cwd, capture_output=True, text=True)


def make_synthetic_case(d):
    """写入合成示例材料（仅用于自检，不含正式案例数据）。"""
    data_dir = os.path.join(d, "materials")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"challenge_id": "selftest-synthetic", "note": "synthetic self-test input"},
                  fh, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# synthetic self-test\nchallenge_id: selftest-synthetic\n")
    # 一条带离群点的两列曲线
    lines = ["x,force"] + ["%d,%.3f" % (i, 10.0 + i * 0.5 + (50.0 if i == 12 else 0.0))
                           for i in range(1, 21)]
    with open(os.path.join(data_dir, "bend_trace_synthetic.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    # 一个坏 JSON 文件（应被容错）
    with open(os.path.join(data_dir, "broken.json"), "w", encoding="utf-8") as fh:
        fh.write('{"broken": ')
    return d


def check_normal():
    d = tempfile.mkdtemp(prefix="selftest_normal_")
    try:
        make_synthetic_case(d)
        out_dir = os.path.join(d, "out")
        os.makedirs(out_dir)
        r1 = run(["skeleton", "--input", d, "--output", out_dir])
        assert r1.returncode == 0, r1.stderr
        r2 = run(["inspect", "--input", d, "--output", out_dir])
        assert r2.returncode == 0, r2.stderr
        result_path = os.path.join(out_dir, "result.json")
        r3 = run(["build", "--input", result_path, "--output", out_dir])
        assert r3.returncode == 0, r3.stderr
        # 注入少量结论以便校验通过检查项（合成示例）
        obj = json.load(open(result_path, encoding="utf-8"))
        obj["status"] = "partial"
        obj["summary"] = "synthetic self-test partial result"
        obj["interpretation"]["trends"] = [{
            "summary": "合成序列单调上升", "direction": "increasing",
            "evidence": [{"locator": "materials/bend_trace_synthetic.csv#L1-L20",
                          "quote": "x,force"}],
        }]
        obj["interpretation"]["anomalies"] = [{
            "object": "bend_trace_synthetic row 12", "method": "z-score", "threshold": 3.0,
            "evidence": [{"locator": "materials/bend_trace_synthetic.csv#L13",
                          "quote": "12,55.000"}],
        }]
        obj["interpretation"]["verification_steps"] = [{
            "target": "materials/bend_trace_synthetic.csv#L13",
            "expected_if_supported": "该行 force 偏离其余行", "decision_rule": "回查该行原始记录",
        }]
        obj["interpretation"]["possible_causes"] = [{
            "summary": "合成离群注入", "testability": "可回查",
            "evidence": [{"locator": "materials/bend_trace_synthetic.csv#L13",
                          "quote": "12,55.000"}],
        }]
        obj["interpretation"]["followup_experiments"] = [{
            "summary": "补对照", "reason": "合成", "expected_outcome": "验证",
        }]
        obj["sources"] = [{"rel": "materials/bend_trace_synthetic.csv", "role": "material"}]
        obj["change_log"].append({"stage": "selftest", "at": "synthetic", "detail": "self-test"})
        json.dump(obj, open(result_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        r4 = run(["validate", "--input", result_path])
        assert r4.returncode == 0, r4.stdout + r4.stderr
        r5 = run(["finalize", "--input", result_path])
        assert r5.returncode == 0, r5.stderr
        for name in ("result.json", "report.md", "data_profile.json", "evidence_map.json"):
            assert os.path.exists(os.path.join(out_dir, name)), name
        print("PASS: normal pipeline (synthetic input)")
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_empty():
    d = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        out_dir = os.path.join(d, "out")
        os.makedirs(out_dir)
        r = run(["skeleton", "--input", d, "--output", out_dir])
        assert r.returncode == 0, r.stderr
        obj = json.load(open(os.path.join(out_dir, "result.json"), encoding="utf-8"))
        assert obj["status"] == "insufficient_evidence", obj["status"]
        print("PASS: empty input -> insufficient_evidence, exit 0")
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_bad_json():
    d = tempfile.mkdtemp(prefix="selftest_badjson_")
    try:
        data_dir = os.path.join(d, "materials")
        os.makedirs(data_dir)
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"challenge_id": "selftest-bad"}, fh)
        with open(os.path.join(data_dir, "bad.json"), "w", encoding="utf-8") as fh:
            fh.write('{"x": ')
        out_dir = os.path.join(d, "out")
        os.makedirs(out_dir)
        r1 = run(["skeleton", "--input", d, "--output", out_dir])
        assert r1.returncode == 0, r1.stderr
        r2 = run(["inspect", "--input", d, "--output", out_dir])
        assert r2.returncode == 0, r2.stderr
        prof = json.load(open(os.path.join(out_dir, "data_profile.json"), encoding="utf-8"))
        assert any(deg.get("issue") in ("unreadable", "bad_encoding", "bad_json")
                   for deg in prof.get("degradations", [])), prof.get("degradations")
        print("PASS: bad JSON -> degraded, pipeline continues")
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ok = True
    ok = check_normal() and ok
    ok = check_empty() and ok
    ok = check_bad_json() and ok
    print("SELFTEST: %s" % ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
