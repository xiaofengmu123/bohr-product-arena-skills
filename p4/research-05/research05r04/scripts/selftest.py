# -*- coding: utf-8 -*-
"""selftest.py —— Skill 包自检（仅 Python 标准库）。

用合成输入（非正式案例、非预制答案）验证三个降级场景与主流程：
  1) 空输入目录   → 退出码 0，status=input_error，result.json 合法；
  2) 坏 JSON      → 退出码 0，status=input_error，错误已登记；
  3) 正常输入     → skeleton→evidence→finalize 全链路，result.json 契约字段齐全，
                     写入 fragment 后重跑 finalize 能深合并。

用法（在 Skill 包根目录）：
  python3 -B scripts/selftest.py
退出码：全部通过为 0；任一断言失败为 1。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(SCRIPTS_DIR, "main.py")


def run(*argv, **kw):
    return subprocess.run([sys.executable, "-B", MAIN_PY] + list(argv),
                          capture_output=True, text=True, **kw)


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_normal_input(root):
    """构造合成输入：case.json + task.md + materials/paper.txt。"""
    os.makedirs(os.path.join(root, "materials"), exist_ok=True)
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "case_id": "selftest-demo-001",
            "output_contract": {
                "research_question": None, "prior_work": None, "novelty": None,
                "feasibility": None, "evidence_matrix": [], "uncertainty": [],
            },
            "research_note": "synthetic self-test input",
        }, fh, ensure_ascii=False)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 自检任务\n\n对研究问题给出可检验表述。\n")
    with open(os.path.join(root, "materials", "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write("Paper A: 提出方法 X，使用数据集 D，报告准确率 0.91。\n"
                 "Paper B: 提出方法 Y，在 D 上报告准确率 0.93。\n")


def check_contract(result, label):
    must = ["case_id", "status", "sources", "uncertainty", "change_log",
            "research_question", "prior_work", "novelty", "feasibility",
            "evidence_matrix", "limitations", "pending_human", "errors", "degradations"]
    for key in must:
        assert key in result, "%s: result.json 缺少字段 %s" % (label, key)
    assert isinstance(result["sources"], list) and isinstance(result["change_log"], list)
    assert result["case_id"], "%s: case_id 为空" % label


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")

    # 0) --help 可运行
    r = run("--help")
    if r.returncode != 0:
        failures.append("--help 退出码 %d" % r.returncode)

    # 1) 空输入目录
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    out1 = os.path.join(tmp, "out1"); work1 = os.path.join(tmp, "work1")
    r = run("skeleton", "--input", empty, "--output", out1, "--work", work1)
    if r.returncode != 0:
        failures.append("空输入 skeleton 退出码 %d" % r.returncode)
    else:
        res = load(os.path.join(out1, "result.json"))
        check_contract(res, "空输入")
        if res.get("status") != "input_error":
            failures.append("空输入 status=%s (期望 input_error)" % res.get("status"))

    # 2) 坏 JSON
    bad = os.path.join(tmp, "bad")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    out2 = os.path.join(tmp, "out2"); work2 = os.path.join(tmp, "work2")
    r = run("skeleton", "--input", bad, "--output", out2, "--work", work2)
    if r.returncode != 0:
        failures.append("坏 JSON skeleton 退出码 %d" % r.returncode)
    else:
        res = load(os.path.join(out2, "result.json"))
        check_contract(res, "坏 JSON")
        if res.get("status") != "input_error":
            failures.append("坏 JSON status=%s (期望 input_error)" % res.get("status"))
        if not res.get("errors"):
            failures.append("坏 JSON 未登记 errors")

    # 3) 正常输入：三阶段全链路 + fragment 合并
    normal = os.path.join(tmp, "normal")
    make_normal_input(normal)
    out3 = os.path.join(tmp, "out3"); work3 = os.path.join(tmp, "work3")
    for ph in ("skeleton", "evidence", "finalize"):
        r = run(ph, "--input", normal, "--output", out3, "--work", work3)
        if r.returncode != 0:
            failures.append("正常输入 %s 退出码 %d" % (ph, r.returncode))
    res = load(os.path.join(out3, "result.json"))
    check_contract(res, "正常输入")
    if res.get("status") not in {"ok", "partial"}:
        failures.append("正常输入 status=%s" % res.get("status"))
    if not res.get("sources"):
        failures.append("正常输入 sources 为空")
    # 写入 fragment 后重跑 finalize，验证深合并
    frag_dir = os.path.join(work3, "fragments")
    os.makedirs(frag_dir, exist_ok=True)
    with open(os.path.join(frag_dir, "01_question.json"), "w", encoding="utf-8") as fh:
        json.dump({"research_question": {"testable_statement": "方法 Y 在 D 上是否优于 X？"}},
                  fh, ensure_ascii=False)
    r = run("finalize", "--input", normal, "--output", out3, "--work", work3)
    if r.returncode != 0:
        failures.append("fragment 重跑 finalize 退出码 %d" % r.returncode)
    res = load(os.path.join(out3, "result.json"))
    if not (res.get("research_question") or {}).get("testable_statement"):
        failures.append("fragment 未合并进 research_question")
    if not os.path.isfile(os.path.join(out3, "report.md")):
        failures.append("report.md 未生成")

    shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED")
        for f in failures:
            print(" -", f)
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
