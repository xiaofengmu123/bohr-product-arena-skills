#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — Skill 包离线自检（仅标准库，退出码 0 = 全部通过）。

覆盖：
  1. SKILL.md 存在且 YAML frontmatter 含 name/description/version；
  2. scripts/*.py 可被 `python -B` 加载并输出 --help（退出码 0）；
  3. 空输入 → 合法 result.json 且 status 诚实；
  4. 坏 JSON 输入 → 不崩溃，降级为 input_error；
  5. bootstrap → discover → finalize → selfcheck 全链路可运行。
不依赖正式案例；不冒充任何评测结果。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
SKILL_MD = os.path.join(ROOT, "SKILL.md")
PY = sys.executable


def _run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def check_frontmatter():
    if not os.path.isfile(SKILL_MD):
        return [("SKILL.md_missing", "")]
    text = open(SKILL_MD, encoding="utf-8").read()
    if not text.startswith("---"):
        return [("frontmatter_missing", "")]
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return [("frontmatter_unparsed", "")]
    block = m.group(1)
    problems = []
    for key in ("name", "description", "version"):
        if not re.search(r"^%s:\s*\S" % key, block, re.M):
            problems.append(("frontmatter_missing_key", key))
    return problems


def check_scripts():
    problems = []
    for name in sorted(os.listdir(SCRIPT_DIR)):
        if not name.endswith(".py") or name.startswith("_") or name == "selftest.py":
            continue
        rc, out = _run([PY, "-B", os.path.join(SCRIPT_DIR, name), "--help"], ROOT)
        if rc != 0:
            problems.append(("script_help_failed", name, out[:200]))
    return problems


def run_pipeline():
    problems = []
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp, exist_ok=True)

        # 空输入
        rc, _ = _run([PY, "-B", os.path.join(SCRIPT_DIR, "main.py"), "bootstrap",
                      "--input", inp, "--output", out, "--work", work], ROOT)
        result_path = os.path.join(out, "result.json")
        if rc != 0 or not os.path.isfile(result_path):
            problems.append(("bootstrap_empty_failed", "rc=%d" % rc))
        else:
            obj = json.load(open(result_path, encoding="utf-8"))
            if obj.get("status") != "input_error" or obj.get("case_id") != "unknown":
                problems.append(("bootstrap_empty_wrong_status", str(obj.get("status"))))

        # 坏 JSON
        bad = os.path.join(tmp, "bad")
        os.makedirs(os.path.join(bad, "input"), exist_ok=True)
        open(os.path.join(bad, "input", "case.json"), "w", encoding="utf-8").write("{broken")
        os.makedirs(os.path.join(bad, "output"), exist_ok=True)
        os.makedirs(os.path.join(bad, "work"), exist_ok=True)
        rc, _ = _run([PY, "-B", os.path.join(SCRIPT_DIR, "main.py"), "bootstrap",
                      "--input", os.path.join(bad, "input"),
                      "--output", os.path.join(bad, "output"),
                      "--work", os.path.join(bad, "work")], ROOT)
        r2 = os.path.join(bad, "output", "result.json")
        if rc != 0 or not os.path.isfile(r2):
            problems.append(("bad_json_crash", "rc=%d" % rc))
        else:
            obj2 = json.load(open(r2, encoding="utf-8"))
            if obj2.get("case_id") != "unknown":
                problems.append(("bad_json_wrong_case_id", str(obj2.get("case_id"))))

        # 正常 bootstrap → finalize → selfcheck
        inp2 = os.path.join(tmp, "input2")
        os.makedirs(inp2, exist_ok=True)
        case = {"id": "selftest-case", "output_contract": {"title": "", "abstract": ""}}
        open(os.path.join(inp2, "case.json"), "w", encoding="utf-8").write(
            json.dumps(case, ensure_ascii=False))
        out2 = os.path.join(tmp, "out2")
        work2 = os.path.join(tmp, "work2")
        _run([PY, "-B", os.path.join(SCRIPT_DIR, "main.py"), "bootstrap",
              "--input", inp2, "--output", out2, "--work", work2], ROOT)
        _run([PY, "-B", os.path.join(SCRIPT_DIR, "main.py"), "finalize",
              "--input", inp2, "--output", out2, "--work", work2], ROOT)
        rc, _ = _run([PY, "-B", os.path.join(SCRIPT_DIR, "main.py"), "selfcheck",
                      "--input", inp2, "--output", out2, "--work", work2], ROOT)
        if rc != 0:
            problems.append(("selfcheck_failed", "rc=%d" % rc))
    finally:
        for d in (tmp,):
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    return problems


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("selftest.py — 离线自检 SKILL.md frontmatter、脚本可执行性与输入健壮性。")
        return 0
    problems = []
    problems.extend(check_frontmatter())
    problems.extend(check_scripts())
    problems.extend(run_pipeline())
    print(json.dumps({"selftest": "latex-writing-compile",
                      "passed": not problems, "problems": problems},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
