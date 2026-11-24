#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（scripts/selftest.py）。

验证四件事，对应评分维度“可靠体验”与“自检清单”：
  1. main.py --help 退出码 0（调用与易用性）；
  2. 空输入 / 坏 JSON / 缺输入不崩溃，且 status/errors/degradations 诚实（异常处理）；
  3. 同输入两次运行产物逐字节一致（确定性，本变体核心）；
  4. result.json 满足输出契约与 output_contract 回填（selfcheck）。

用法：
  python3 scripts/selftest.py          # 全量自检（临时目录，不污染工作区）
  python3 scripts/selftest.py --quick  # 只做确定性重复运行检查

脚本只依赖标准库；用 subprocess 真实调用 main.py，退出码 0 表示通过。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

PASS = 0
FAIL = 0


def run_main(args):
    """以 -B（不写 .pyc）真实调用 main.py，返回 (returncode, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("PASS | " + name + ((" | " + detail) if detail else ""))
    else:
        FAIL += 1
        print("FAIL | " + name + ((" | " + detail) if detail else ""))


def make_demo_input(root):
    """构造一个含 output_contract 的演示输入（仅自检用，不冒充正式案例）。"""
    input_dir = os.path.join(root, "input")
    os.makedirs(input_dir, exist_ok=True)
    case = {
        "case_id": "case-demo-001",
        "research_question": "用于锂离子电池固态电解质的高通量计算方法",
        "output_contract": {
            "results": [],
            "must_have_sources": True,
            "required_fields": ["title", "doi"],
        },
    }
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False, indent=2)
    task = (
        "# 演示任务（示例，非正式案例）\n\n"
        "- challenge_id: `case-demo-001`\n"
        "- 研究问题：用于锂离子电池固态电解质的高通量计算方法\n"
    )
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as f:
        f.write(task)
    return input_dir


def demo_candidates():
    """两条同 DOI（大小写不同）记录，用于验证去重与排序确定性。"""
    return [
        {
            "title": "High-throughput screening of solid-state electrolytes for lithium batteries",
            "authors": ["Alice Example", "Bob Sample"],
            "year": 2021,
            "journal": "Nature Energy",
            "doi": "10.1000/example.2021",
            "url": "https://example.org/10.1000/example.2021",
            "database": "crossref",
            "relevance": 0.9,
            "relevance_reason": "题目核心概念：高通量 + 固态电解质 + 锂电池",
            "evidence": {
                "locator": "https://example.org/10.1000/example.2021#abstract",
                "quote": "We screen solid-state electrolytes for lithium-ion batteries.",
            },
        },
        {
            "title": "High-throughput screening of solid-state electrolytes",
            "authors": ["Alice Example", "Bob Sample"],
            "year": 2021,
            "journal": "Nature Energy",
            "doi": "10.1000/EXAMPLE.2021",
            "url": "https://example.org/10.1000/example.2021",
            "database": "crossref",
            "relevance": 0.9,
            "relevance_reason": "重复记录，用于去重验证",
            "evidence": {"locator": "dup", "quote": "dup"},
        },
    ]


def full_pipeline(input_dir, work_dir, output_dir, with_candidates=True):
    """在独立 work/output 目录跑完整骨架流水线并返回 result.json。"""
    rc, _, err = run_main(["run", "--input", input_dir,
                           "--output", output_dir, "--work", work_dir])
    if rc != 0:
        return None, "run rc=%d: %s" % (rc, err)
    if with_candidates:
        cand_file = os.path.join(work_dir, "candidates_in.json")
        with open(cand_file, "w", encoding="utf-8") as f:
            json.dump(demo_candidates(), f, ensure_ascii=False)
        run_main(["import", "--file", cand_file, "--work", work_dir])
        run_main(["evidence", "--inline",
                  json.dumps({"query": "high-throughput solid-state electrolyte lithium",
                              "source": "paper_search", "database": "crossref",
                              "rationale": "单概念到 AND 组合的多轮召回", "kind": "query"}),
                  "--work", work_dir])
        run_main(["assemble", "--input", input_dir,
                  "--output", output_dir, "--work", work_dir])
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None, "result.json 未生成"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), ""


def test_help():
    rc, out, _ = run_main(["--help"])
    record("main.py --help 退出码 0", rc == 0, "rc=%d" % rc)
    record("--help 输出含子命令", "run" in out and "selfcheck" in out)


def test_empty_input():
    root = tempfile.mkdtemp(prefix="skill_empty_")
    empty = os.path.join(root, "in"); os.makedirs(empty, exist_ok=True)
    out = os.path.join(root, "out"); work = os.path.join(root, "work")
    rc, _, err = run_main(["run", "--input", empty, "--output", out, "--work", work])
    record("空输入 run 退出码 0", rc == 0, "rc=%d %s" % (rc, err[:80]))
    res = read_result(out)
    record("空输入 status=insufficient_evidence",
           bool(res) and res.get("status") == "insufficient_evidence")


def test_bad_json():
    root = tempfile.mkdtemp(prefix="skill_bad_")
    ind = os.path.join(root, "in"); os.makedirs(ind, exist_ok=True)
    with open(os.path.join(ind, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json")
    out = os.path.join(root, "out"); work = os.path.join(root, "work")
    rc, _, _ = run_main(["run", "--input", ind, "--output", out, "--work", work])
    record("坏 JSON run 退出码 0", rc == 0, "rc=%d" % rc)
    res = read_result(out)
    deg = res.get("degradations") if res else None
    record("坏 JSON 记录到 degradations",
           bool(deg), str(deg)[:60])


def test_determinism():
    root = tempfile.mkdtemp(prefix="skill_det_")
    ind = make_demo_input(root)
    a_out = os.path.join(root, "outA"); a_work = os.path.join(root, "workA")
    b_out = os.path.join(root, "outB"); b_work = os.path.join(root, "workB")
    res_a, err_a = full_pipeline(ind, a_work, a_out)
    res_b, err_b = full_pipeline(ind, b_work, b_out)
    record("两次运行均成功", res_a is not None and res_b is not None,
           "%s %s" % (err_a, err_b))
    if res_a is None or res_b is None:
        return
    fa = res_a.get("determinism", {}).get("fingerprint")
    fb = res_b.get("determinism", {}).get("fingerprint")
    record("两次运行指纹一致", bool(fa) and fa == fb)
    ta = read_text(os.path.join(a_out, "result.json"))
    tb = read_text(os.path.join(b_out, "result.json"))
    record("两次运行 result.json 逐字节一致", ta == tb)
    record("去重后仅 1 条结果", len(res_a.get("results") or []) == 1,
           "len=%d" % len(res_a.get("results") or []))
    record("output_contract 回填 must_have_sources",
           res_a.get("must_have_sources") is True)
    record("output_contract 保留在 contract.results",
           isinstance((res_a.get("contract") or {}).get("results"), list))


def test_idempotent_same_dir():
    """同一 work/output 目录连续两次 run，产物也应逐字节一致（幂等）。"""
    root = tempfile.mkdtemp(prefix="skill_idem_")
    ind = make_demo_input(root)
    out = os.path.join(root, "out"); work = os.path.join(root, "work")
    full_pipeline(ind, work, out)
    h1 = file_hash(os.path.join(out, "result.json"))
    full_pipeline(ind, work, out)
    h2 = file_hash(os.path.join(out, "result.json"))
    record("同一目录两次 run 逐字节一致（幂等）", h1 == h2)


def file_hash(p):
    import hashlib
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_selfcheck():
    root = tempfile.mkdtemp(prefix="skill_sc_")
    ind = make_demo_input(root)
    out = os.path.join(root, "out"); work = os.path.join(root, "work")
    full_pipeline(ind, work, out)
    rc, stdout, _ = run_main(["selfcheck", "--output", out, "--work", work])
    record("selfcheck 退出码 0", rc == 0, "rc=%d" % rc)
    record("selfcheck 输出 PASS", "selfcheck: PASS" in stdout)


def read_result(out):
    p = os.path.join(out, "result.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_text(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(description="Skill 自检")
    ap.add_argument("--quick", action="store_true", help="只做确定性检查")
    args = ap.parse_args()

    if args.quick:
        test_determinism()
    else:
        test_help()
        test_empty_input()
        test_bad_json()
        test_determinism()
        test_idempotent_same_dir()
        test_selfcheck()

    print("\nselftest: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
