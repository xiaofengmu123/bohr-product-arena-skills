#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 对 Skill 包做结构自检（不依赖 LaTeX，不生成示例答案）。

覆盖：SKILL.md frontmatter、脚本可执行、bootstrap/finalize/selfcheck 全链路、
证据 quote、以及 classify-log 的分类能力。全部测试数据在临时目录中即时生成。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def run(args):
    return subprocess.run([PY, "-B", *args], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def check(cond, name):
    print(("PASS" if cond else "FAIL") + " - " + name)
    return bool(cond)


def main():
    ok = True

    # 1. SKILL.md frontmatter
    skill = os.path.join(ROOT, "SKILL.md")
    ok &= check(os.path.isfile(skill), "SKILL.md 存在")
    if os.path.isfile(skill):
        text = open(skill, encoding="utf-8").read()
        ok &= check(text.startswith("---"), "SKILL.md 以 frontmatter 起始")
        fm = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
        ok &= check(bool(fm), "frontmatter 存在")
        if fm:
            body = fm.group(1)
            for key in ("name:", "description:", "version:"):
                ok &= check(key in body, "frontmatter 含 " + key.strip(':'))

    # 2. 脚本 --help 退出码 0
    for script in ("scripts/main.py", "scripts/latex_tools.py"):
        p = os.path.join(ROOT, script)
        ok &= check(os.path.isfile(p), script + " 存在")
        if os.path.isfile(p):
            r = run([p, "--help"])
            ok &= check(r.returncode == 0, script + " --help 退出码 0")

    main_py = os.path.join(ROOT, "scripts", "main.py")
    latex_py = os.path.join(ROOT, "scripts", "latex_tools.py")

    # 3. bootstrap / finalize / selfcheck 全链路（合成输入）
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(os.path.join(inp, "materials"))
        open(os.path.join(inp, "case.json"), "w", encoding="utf-8").write(
            json.dumps({
                "case_id": "selftest-000",
                "output_contract": {"manuscript_path": "string", "compile_ok": "bool"},
            }))
        open(os.path.join(inp, "task.md"), "w", encoding="utf-8").write(
            "# 自检测试任务\n")
        open(os.path.join(inp, "materials", "paper.md"), "w", encoding="utf-8").write(
            "# 标题\n\n温度 300 K。\n")

        r = run([main_py, "bootstrap", "--input", inp, "--output", out, "--work", work])
        ok &= check(r.returncode == 0, "bootstrap 退出码 0")
        res = os.path.join(out, "result.json")
        ok &= check(os.path.isfile(res), "bootstrap 落盘 result.json")
        if os.path.isfile(res):
            obj = json.load(open(res, encoding="utf-8"))
            ok &= check(obj.get("case_id") == "selftest-000", "case_id 正确")
            ok &= check(set(("manuscript_path", "compile_ok")) <= set(obj),
                        "output_contract 键齐全")

        r = run([main_py, "finalize", "--input", inp, "--output", out, "--work", work])
        ok &= check(r.returncode == 0, "finalize 退出码 0")

        r = run([main_py, "selfcheck", "--input", inp, "--output", out, "--work", work])
        ok &= check(r.returncode == 0, "selfcheck 通过")

        r = run([main_py, "evidence", "quote",
                 "--file", os.path.join(inp, "materials", "paper.md"),
                 "--start", "1", "--end", "3"])
        ok &= check(r.returncode == 0, "evidence quote 退出码 0")

    # 4. classify-log 分类能力（合成 log）
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "t.log")
        open(log, "w", encoding="utf-8").write(
            "(./main.tex\n! Undefined control sequence.\nl.42 \\badcmd\n)\n")
        r = run([latex_py, "classify-log", "--log", log])
        ok &= check(r.returncode == 0, "classify-log 退出码 0")
        out_txt = r.stdout.decode("utf-8", "replace")
        ok &= check("undefined_control_sequence" in out_txt,
                    "classify-log 分类出 undefined_control_sequence")

    print("\n" + ("ALL PASS" if ok else "SOME FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
