#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-traced-review 自检脚本（仅标准库）。

覆盖三个确定性场景：
  A. 空输入目录          → main.py 退出码 0，result.status == insufficient_evidence
  B. 坏 JSON case.json   → main.py 退出码 0，result.status == input_error
  C. 最小有效契约        → main.py 退出码 0，status == partial，契约字段齐全；
                           validate_result.py 退出码 0

用法：python3 -B scripts/selftest.py
退出码：0 = 全部通过；1 = 至少一项失败。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(HERE, "main.py")
VALIDATE_PY = os.path.join(HERE, "validate_result.py")

MINIMAL_CASE = {
    "case_id": "demo-01",
    "research_question": "What is the effect of X on Y?",
    "output_contract": {
        "review_draft": {"type": "string"},
        "screening_criteria": {"type": "string"},
        "screening_log": {"type": "array"},
        "evidence_table": {"type": "array"},
        "evidence_map": {"type": "object"},
        "themes": {"type": "array"},
        "conflicts_and_gaps": {"type": "array"},
        "references": {"type": "array"},
    },
}
MINIMAL_CONTRACT_NAMES = sorted(MINIMAL_CASE["output_contract"].keys())


def run_main(input_dir, output_dir, work_dir):
    return subprocess.run(
        [sys.executable, "-B", MAIN_PY,
         "--input", input_dir, "--output", output_dir, "--work", work_dir],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def scenario_empty(tmp):
    inp = os.path.join(tmp, "empty_input")
    out = os.path.join(tmp, "empty_output")
    work = os.path.join(tmp, "empty_work")
    os.makedirs(inp)
    proc = run_main(inp, out, work)
    if proc.returncode != 0:
        return False, "main.py 非零退出 %d: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace"))
    result = load_json(os.path.join(out, "result.json"))
    if result.get("status") != "insufficient_evidence":
        return False, "空输入 status 期望 insufficient_evidence，实得 %r" % result.get("status")
    return True, "status=insufficient_evidence"


def scenario_bad_json(tmp):
    inp = os.path.join(tmp, "bad_input")
    out = os.path.join(tmp, "bad_output")
    work = os.path.join(tmp, "bad_work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")
    proc = run_main(inp, out, work)
    if proc.returncode != 0:
        return False, "main.py 非零退出 %d" % proc.returncode
    result = load_json(os.path.join(out, "result.json"))
    if result.get("status") != "input_error":
        return False, "坏 JSON status 期望 input_error，实得 %r" % result.get("status")
    return True, "status=input_error"


def scenario_minimal(tmp):
    inp = os.path.join(tmp, "min_input")
    out = os.path.join(tmp, "min_output")
    work = os.path.join(tmp, "min_work")
    os.makedirs(os.path.join(inp, "materials"))
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(MINIMAL_CASE, fh, ensure_ascii=False)
    with open(os.path.join(inp, "materials", "paper_a.txt"), "w", encoding="utf-8") as fh:
        fh.write("Sample text with a real sentence about X and Y.\n")
    proc = run_main(inp, out, work)
    if proc.returncode != 0:
        return False, "main.py 非零退出 %d: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace"))
    result = load_json(os.path.join(out, "result.json"))
    if result.get("status") != "partial":
        return False, "最小契约 status 期望 partial，实得 %r" % result.get("status")
    if result.get("case_id") != "demo-01":
        return False, "case_id 期望 demo-01，实得 %r" % result.get("case_id")
    missing = [n for n in MINIMAL_CONTRACT_NAMES if n not in result]
    if missing:
        return False, "契约字段缺失: %s" % ", ".join(missing)
    if not isinstance(result.get("sources"), list) or len(result["sources"]) != 1:
        return False, "sources 期望 1 个条目，实得 %r" % result.get("sources")

    vproc = subprocess.run(
        [sys.executable, "-B", VALIDATE_PY,
         "--output", out, "--case", os.path.join(inp, "case.json")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if vproc.returncode != 0:
        return False, "validate_result.py 非零退出 %d:\n%s" % (
            vproc.returncode, vproc.stdout.decode("utf-8", "replace"))
    return True, "status=partial，契约字段齐全，validate 通过"


def main():
    tmp = tempfile.mkdtemp(prefix="evr_selftest_")
    scenarios = [
        ("A.空输入", scenario_empty),
        ("B.坏JSON", scenario_bad_json),
        ("C.最小契约", scenario_minimal),
    ]
    all_ok = True
    print("=== selftest ===")
    for name, fn in scenarios:
        try:
            ok, msg = fn(tmp)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, "异常: %r" % exc
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, msg))
        all_ok = all_ok and ok
    print("RESULT: %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
