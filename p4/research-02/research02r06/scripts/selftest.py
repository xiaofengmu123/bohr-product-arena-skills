#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本（仅标准库）：在临时目录构造四种场景，验证 main.py 与 validate.py 的契约。

场景：
  A 空输入目录  -> main.py 退出 0，写出合法 result.json，status=insufficient_evidence；
  B 坏 JSON     -> main.py 退出 0，写出合法 result.json，status=input_error，errors 非空；
  C 演示输入    -> main.py 退出 0，status=partial，contract 字段全部回填；重复运行字节一致；
  D 合成完整产物 -> validate.py 先通过，再破坏一条回链后应报 error。

selftest.py 不向仓库写任何正式测试产物，全部落在 tempfile.mkdtemp()。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VALIDATE = os.path.join(HERE, "validate.py")
PY = sys.executable


def run(py_file, *args):
    return subprocess.run([PY, "-B", py_file] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_empty(root):
    os.makedirs(root, exist_ok=True)


def make_bad(root):
    write(os.path.join(root, "case.json"), "{ this is not json")
    write(os.path.join(root, "task.md"), "# 任务\n")


DEMO_CASE = {
    "case_id": "demo-case-001",
    "research_question": "某种干预对某结局的影响是什么？",
    "output_contract": {
        "type": "object",
        "properties": {
            "research_question": {"type": "string"},
            "screening_criteria": {"type": "object"},
            "screening_log": {"type": "array"},
            "evidence_table": {"type": "array"},
            "themes": {"type": "array"},
            "review_draft": {"type": "string"},
            "conflicts_and_gaps": {"type": "array"},
            "references": {"type": "array"},
        },
    },
}

DEMO_MATERIAL = ("# 样例文献\n"
                 "方法：随机对照试验，样本量 120。\n"
                 "结论：干预显著改善结局（p<0.01）。\n")


def make_demo(root):
    write(os.path.join(root, "case.json"), json.dumps(DEMO_CASE, ensure_ascii=False))
    write(os.path.join(root, "task.md"), "# 文献综述生成（演示）\n")
    write(os.path.join(root, "materials", "paper1.txt"), DEMO_MATERIAL)


def complete(output_dir):
    """把演示输入补成一份可被 validate.py 判为 ok 的合成产物（仅供自检）。"""
    evidence = [
        {"evidence_id": "E01", "source_id": "S1",
         "locator": "materials/paper1.txt#L2",
         "quote": "方法：随机对照试验，样本量 120。",
         "claim_ids": ["C1"], "field": "method", "strength": "established", "notes": ""},
        {"evidence_id": "E02", "source_id": "S1",
         "locator": "materials/paper1.txt#L3",
         "quote": "结论：干预显著改善结局（p<0.01）。",
         "claim_ids": ["C1"], "field": "result", "strength": "established", "notes": ""},
    ]
    claims = [
        {"claim_id": "C1",
         "text": "该干预显著改善结局（p<0.01）。",
         "strength": "established",
         "evidence_refs": ["E02"],
         "relation": "support",
         "theme_id": "T1",
         "boundary": "仅限该样本与结局定义"},
        {"claim_id": "C2",
         "text": "该研究为随机对照试验，样本量 120。",
         "strength": "established",
         "evidence_refs": ["E01"],
         "relation": "support",
         "theme_id": "T1",
         "boundary": "仅限该试验设计"},
    ]
    review = ("# 综述草稿（自检合成示例，非正式结果）\n\n"
              "在本批文献中，主要研究采用随机对照试验设计，样本量为 120 例，"
              "并以主要结局作为效应指标 [C2]。"
              "其结果显示，该干预在主要结局上存在显著改善，效应达到统计学显著水平（p<0.01），"
              "方向与事先设定一致 [C1]。"
              "据此，该干预在该人群与结局定义下具有明确的短期获益证据。"
              "上述结论适用于该试验的人群范围、随访时点与结局定义，"
              "外推到其他人群、更长时间窗或不同结局时需谨慎。\n")
    screening = {
        "research_question": DEMO_CASE["research_question"],
        "criteria": {
            "inclusion": ["研究对象为临床人群"],
            "exclusion": ["非原始研究"],
            "dedup_rules": ["同 SHA-256 去重"],
            "frozen_at": "phase1",
        },
        "decisions": [{"source_id": "S1", "decision": "include", "reason": "符合纳入条件"}],
        "dedup": [],
    }
    write(os.path.join(output_dir, "claims.json"), json.dumps({"claims": claims}, ensure_ascii=False))
    with open(os.path.join(output_dir, "evidence_table.jsonl"), "w", encoding="utf-8") as f:
        for e in evidence:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    write(os.path.join(output_dir, "review.md"), review)
    write(os.path.join(output_dir, "screening_log.json"),
          json.dumps(screening, ensure_ascii=False))
    result = read_json(os.path.join(output_dir, "result.json"))
    result["status"] = "ok"
    result["sources"] = [{"source_id": "S1", "type": "text",
                          "path": "materials/paper1.txt", "fulltext_available": True}]
    result["artifacts"] = ["claims.json", "evidence_table.jsonl", "review.md",
                           "screening_log.json", "plan.json"]
    write(os.path.join(output_dir, "result.json"), json.dumps(result, ensure_ascii=False, indent=2))


def fail(msg):
    print("FAIL: " + msg)
    return False


def scenario_a():
    root = tempfile.mkdtemp(prefix="litreview-selftest-a-")
    inp = os.path.join(root, "input")
    make_empty(inp)
    r = run(MAIN, "--input", inp, "--output", os.path.join(root, "output"),
            "--work", os.path.join(root, "work"))
    if r.returncode != 0:
        return fail("A: main.py 退出码非 0: %d\n%s" % (r.returncode, r.stderr))
    result = read_json(os.path.join(root, "output", "result.json"))
    if result.get("status") != "insufficient_evidence":
        return fail("A: status=%r，期望 insufficient_evidence" % result.get("status"))
    print("PASS A: 空输入 -> %s" % result.get("status"))
    return True


def scenario_b():
    root = tempfile.mkdtemp(prefix="litreview-selftest-b-")
    inp = os.path.join(root, "input")
    make_bad(inp)
    r = run(MAIN, "--input", inp, "--output", os.path.join(root, "output"),
            "--work", os.path.join(root, "work"))
    if r.returncode != 0:
        return fail("B: main.py 退出码非 0: %d\n%s" % (r.returncode, r.stderr))
    result = read_json(os.path.join(root, "output", "result.json"))
    if result.get("status") != "input_error":
        return fail("B: status=%r，期望 input_error" % result.get("status"))
    if not result.get("errors"):
        return fail("B: errors 应为空时非空（坏 JSON 未记录）")
    print("PASS B: 坏 JSON -> %s" % result.get("status"))
    return True


def scenario_c():
    root = tempfile.mkdtemp(prefix="litreview-selftest-c-")
    inp = os.path.join(root, "input")
    make_demo(inp)
    out1 = os.path.join(root, "out1")
    out2 = os.path.join(root, "out2")
    r1 = run(MAIN, "--input", inp, "--output", out1, "--work", os.path.join(root, "w1"))
    r2 = run(MAIN, "--input", inp, "--output", out2, "--work", os.path.join(root, "w2"))
    if r1.returncode != 0 or r2.returncode != 0:
        return fail("C: main.py 退出码非 0")
    res1 = read_json(os.path.join(out1, "result.json"))
    res2 = read_json(os.path.join(out2, "result.json"))
    if res1.get("status") != "partial":
        return fail("C: status=%r，期望 partial" % res1.get("status"))
    for key in DEMO_CASE["output_contract"]["properties"]:
        if key not in res1:
            return fail("C: contract 字段缺失: " + key)
    if res1.get("case_id") != "demo-case-001":
        return fail("C: case_id=%r" % res1.get("case_id"))
    b1 = open(os.path.join(out1, "result.json"), "rb").read()
    b2 = open(os.path.join(out2, "result.json"), "rb").read()
    if b1 != b2:
        return fail("C: 重复运行 result.json 字节不一致")
    print("PASS C: 演示输入 -> partial，contract 回填齐全，重复运行字节一致")
    # 继续做 D 场景的输入准备（--case 指向 case.json 文件）
    return out1, os.path.join(inp, "case.json")


def scenario_d(out1, case_path):
    complete(out1)
    r = run(VALIDATE, "--output", out1, "--case", case_path)
    if r.returncode != 0:
        return fail("D: validate.py 对完整产物应退出 0\nstdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr))
    # 破坏一条回链：claim 引用不存在的证据
    claims = read_json(os.path.join(out1, "claims.json"))
    claims["claims"][0]["evidence_refs"] = ["E99"]
    write(os.path.join(out1, "claims.json"), json.dumps(claims, ensure_ascii=False))
    r2 = run(VALIDATE, "--output", out1, "--case", case_path)
    if r2.returncode == 0:
        return fail("D: 破坏回链后 validate.py 应退出非 0")
    print("PASS D: validate.py 先通过，破坏回链后报 error")
    return True


def main():
    print("== lit-review selftest ==")
    ok = True
    ok &= scenario_a()
    ok &= scenario_b()
    demo = scenario_c()
    if demo:
        ok &= scenario_d(demo[0], demo[1])
    # --help 契约
    h = run(MAIN, "--help")
    if h.returncode != 0:
        ok &= fail("--help 退出码非 0")
    else:
        print("PASS: main.py --help 退出 0")
    print("SELFTEST " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
