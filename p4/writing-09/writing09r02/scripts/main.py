#!/usr/bin/env python3
"""入口编排：read_input → build_result（早落盘骨架）→ check_result；--selftest 端到端自检。

默认路径 /input /app/output /app/work；可显式传参覆盖（开发/自检用）。
编排只做确定性脚手架；写作与证据抽取由 Agent 依据 SKILL.md 完成，
脚本负责可复核的骨架与回检。本文件只做编排，体积保持最小。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import read_input       # noqa: E402
import build_result     # noqa: E402
import check_result     # noqa: E402


def run_read(case, task, input_dir, work):
    return read_input.main(["--case", case, "--task", task, "--input-dir", input_dir, "--work", work])


def run_skeleton(summary, output_dir):
    return build_result.main(["--summary", summary, "--output-dir", output_dir])


def run_check(result, summary):
    return check_result.main(["--result", result, "--summary", summary])


def orchestrate(case, task, input_dir, work, output_dir):
    os.makedirs(work, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    summary = os.path.join(work, "input_summary.json")
    result = os.path.join(output_dir, "result.json")

    rc = run_read(case, task, input_dir, work)
    if rc != 0:
        return rc
    rc = run_skeleton(summary, output_dir)
    if rc != 0:
        return rc
    # 骨架落盘后即“可提交的最小诚实交付”；内容填充与后续 check 由 Agent 执行
    print(json.dumps({"orchestrated": True, "summary": summary, "result": result}, ensure_ascii=False))
    return 0


def selftest():
    """端到端自检：临时目录写 demo case.json/task.md/material，跑 read→build→patch→check。

    演示夹具仅用于验证脚本可运行与接口正确，不是任何正式案例的答案。
    """
    tmp = tempfile.mkdtemp(prefix="main_selftest_")
    input_dir = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    output_dir = os.path.join(tmp, "output")
    os.makedirs(input_dir)
    case = os.path.join(input_dir, "case.json")
    task = os.path.join(input_dir, "task.md")
    material = os.path.join(input_dir, "data.txt")

    case_obj = {
        "case_id": "selftest-demo",
        "output_contract": {"deliverable": None, "facts_sources": None, "version_boundaries": None},
    }
    with open(case, "w", encoding="utf-8") as fh:
        json.dump(case_obj, fh, ensure_ascii=False)
    with open(task, "w", encoding="utf-8") as fh:
        fh.write("# 演示任务（非正式案例）\n依据 data.txt 写出 Results 与 Discussion。\n")
    with open(material, "w", encoding="utf-8") as fh:
        fh.write("Treatment A mean = 3.2 (SD 0.4), n = 12.\n")

    assert orchestrate(case, task, input_dir, work, output_dir) == 0
    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "r", encoding="utf-8") as fh:
        r = json.load(fh)
    assert r["case_id"] == "selftest-demo" and r["status"] == "partial"

    # 模拟 Agent 增量填充（示意，非正式案例答案）
    patch = {
        "deliverable": {"file": "results_discussion.md", "sections": ["Results", "Discussion"]},
        "facts_sources": [{"locator": "data.txt#L1", "quote": "mean = 3.2 (SD 0.4), n = 12", "used_in": "## Results"}],
        "version_boundaries": {"human_confirm": [], "gaps": [], "anomalies": []},
        "status": "complete",
    }
    patch_path = os.path.join(work, "patch.json")
    with open(patch_path, "w", encoding="utf-8") as fh:
        json.dump(patch, fh, ensure_ascii=False)
    assert build_result.main(["--summary", os.path.join(work, "input_summary.json"),
                              "--output-dir", output_dir, "--patch", "--patch-file", patch_path]) == 0

    with open(os.path.join(output_dir, "results_discussion.md"), "w", encoding="utf-8") as fh:
        fh.write("## Results\nMean was 3.2 (SD 0.4), n = 12.\n\n## Discussion\nConsistent with prior work.\n")

    rc = run_check(result_path, os.path.join(work, "input_summary.json"))
    with open(result_path, "r", encoding="utf-8") as fh:
        r2 = json.load(fh)
    assert rc == 0, "check_result returned %d" % rc
    assert r2["status"] == "complete"
    print(json.dumps({
        "selftest": "ok", "case_id": r2["case_id"], "status": r2["status"],
        "contract_keys": sorted(r2.keys()),
    }, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Results & Discussion writing pipeline scaffold")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--selftest", action="store_true", help="run end-to-end self test on a temporary demo fixture")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    return orchestrate(a.case, a.task, a.input_dir, a.work, a.output)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
