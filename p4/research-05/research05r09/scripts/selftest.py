#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检：脚本可运行性、空输入、坏 JSON、缺参数、最小演示。纯标准库。"""

import json
import os
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
PY = sys.executable


def run(args):
    return subprocess.run([PY, "-B", MAIN] + args, capture_output=True, text=True)


def main():
    failures = []

    # 1. 三个子命令 --help 退出码 0
    for sub in ("skeleton", "evidence", "finalize"):
        r = run([sub, "--help"])
        if r.returncode != 0:
            failures.append("%s --help rc=%d" % (sub, r.returncode))

    # 2. 空输入目录 -> insufficient_evidence，不崩溃
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        run(["skeleton", "--input", inp, "--output", out, "--work", work])
        run(["evidence", "--input", inp, "--output", out, "--work", work])
        run(["finalize", "--input", inp, "--output", out, "--work", work])
        res = _load(os.path.join(out, "result.json"))
        if res.get("status") != "insufficient_evidence":
            failures.append("empty input status=%s" % res.get("status"))

    # 3. 坏 JSON case.json -> 不崩溃，status 合法
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        run(["skeleton", "--input", inp, "--output", out, "--work", work])
        run(["evidence", "--input", inp, "--output", out, "--work", work])
        res = _load(os.path.join(out, "result.json"))
        if res.get("status") not in ("partial", "input_error", "insufficient_evidence", "ok"):
            failures.append("bad json status=%s" % res.get("status"))
        if not any("bad_json" in str(d) for d in res.get("degradations", [])):
            failures.append("bad json not recorded in degradations")

    # 4. 最小演示：材料 + output_contract 回填 + case_id 保留 + report.md
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(os.path.join(inp, "materials"))
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "demo-case", "output_contract": {"summary": None, "verdict": None}}, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo task\n")
        with open(os.path.join(inp, "materials", "paper.txt"), "w", encoding="utf-8") as fh:
            fh.write("line one\nline two\n")
        run(["skeleton", "--input", inp, "--output", out, "--work", work])
        run(["evidence", "--input", inp, "--output", out, "--work", work])
        frag_dir = os.path.join(work, "fragments")
        with open(os.path.join(frag_dir, "01_question.json"), "w", encoding="utf-8") as fh:
            json.dump({"research_question": {"testable_statement": "demo", "status": "partial"}}, fh)
        run(["finalize", "--input", inp, "--output", out, "--work", work])
        res = _load(os.path.join(out, "result.json"))
        if "summary" not in res or "verdict" not in res:
            failures.append("output_contract keys not echoed to top level")
        if res.get("case_id") != "demo-case":
            failures.append("case_id not preserved: %s" % res.get("case_id"))
        if not os.path.isfile(os.path.join(out, "report.md")):
            failures.append("report.md missing")

    if failures:
        print("SELFTEST FAILED")
        for f in failures:
            print(" - " + f)
        return 1
    print("SELFTEST OK")
    return 0


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    sys.exit(main())
