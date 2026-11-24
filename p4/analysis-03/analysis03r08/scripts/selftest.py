#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 包自检脚本（仅标准库）。

在交付前运行，验证：
  S0 目录结构与 SKILL.md frontmatter（name/description/version）完整；
  S1 全部 scripts/*.py 可编译；
  S2 main.py 在 空输入/坏 JSON/缺输入 三种场景均退出码 0 且写出合法 result.json；
  S3 profile.py 能对合成 CSV 生成 data_dictionary.json 与 chart_plan.json；
  S4 result.json 结构含 case_id + status + sources/uncertainty/pending_human/change_log。

用法：
  python3 scripts/selftest.py --package .          # 校验包根目录（含 SKILL.md 与 scripts/）
  python3 scripts/selftest.py --package . --skip-smoke

退出码：全部通过为 0；存在 FAIL 为 1。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

REQUIRED_FIELDS = ("case_id", "status", "sources", "uncertainty",
                   "pending_human", "change_log")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")


def _find(pkg, *parts):
    return os.path.join(pkg, *parts)


def check_frontmatter(pkg):
    sk = _find(pkg, "SKILL.md")
    if not os.path.isfile(sk):
        return ["FAIL SKILL.md 缺失"]
    txt = open(sk, "r", encoding="utf-8", errors="replace").read()
    problems = []
    if not txt.startswith("---"):
        problems.append("FAIL SKILL.md 缺少 YAML frontmatter（首行应为 ---）")
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", txt, re.S)
    if not m:
        problems.append("FAIL SKILL.md frontmatter 未闭合")
        return problems
    fm = m.group(1)
    for key in ("name", "description", "version"):
        if not re.search(r"^%s\s*:" % key, fm, re.M):
            problems.append("FAIL frontmatter 缺少 %s" % key)
    body = txt[m.end():]
    nlines = body.count("\n") + 1
    if nlines > 180:
        problems.append("WARN SKILL.md 正文 %d 行 > 180 行上限" % nlines)
    return problems


def check_scripts(pkg):
    problems = []
    sdir = _find(pkg, "scripts")
    if not os.path.isdir(sdir):
        return ["FAIL scripts/ 目录缺失"]
    pys = sorted(f for f in os.listdir(sdir) if f.endswith(".py"))
    if not pys:
        problems.append("FAIL scripts/ 无 .py 脚本")
    for f in pys:
        path = os.path.join(sdir, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                compile(fh.read(), f, "exec")  # 仅做语法检查，不写 __pycache__
        except (SyntaxError, ValueError, OSError) as e:
            problems.append("FAIL 编译失败 %s: %s" % (f, e))
    return problems


def _run(argv, cwd, timeout=60):
    try:
        cp = subprocess.run([sys.executable, "-B"] + argv, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, text=True)
        return cp.returncode, cp.stdout, cp.stderr
    except Exception as e:
        return -999, "", str(e)


def _smoke_init(pkg, label, make_case):
    problems = []
    tmp = tempfile.mkdtemp(prefix="selftest_%s_" % label)
    try:
        indir = os.path.join(tmp, "input")
        outdir = os.path.join(tmp, "output")
        workdir = os.path.join(tmp, "work")
        os.makedirs(indir)
        make_case(indir)
        rc, _out, err = _run(["scripts/main.py", "init",
                              "--input", indir, "--output", outdir,
                              "--work", workdir], cwd=pkg)
        rp = os.path.join(outdir, "result.json")
        if rc != 0:
            problems.append("FAIL [%s] init 退出码 %d: %s" % (label, rc, err))
            return problems
        if not os.path.isfile(rp):
            problems.append("FAIL [%s] 未写 result.json" % label)
            return problems
        obj = json.load(open(rp, "r", encoding="utf-8"))
        for k in REQUIRED_FIELDS:
            if k not in obj:
                problems.append("FAIL [%s] result.json 缺 %s" % (label, k))
        if obj.get("status") not in STATUS_VALUES:
            problems.append("FAIL [%s] status 非法" % label)
    except Exception as e:
        problems.append("FAIL [%s] 异常: %s" % (label, e))
    return problems


def _smoke_profile(pkg):
    problems = []
    tmp = tempfile.mkdtemp(prefix="selftest_profile_")
    try:
        indir = os.path.join(tmp, "input")
        outdir = os.path.join(tmp, "output")
        workdir = os.path.join(tmp, "work")
        os.makedirs(indir)
        csv = ("sepal_length,sepal_width,species\n"
               "5.1,3.5,setosa\n5.0,3.6,setosa\n6.7,3.0,versicolor\n"
               "7.0,3.2,versicolor\n6.3,2.8,virginica\n6.5,3.0,virginica\n")
        with open(os.path.join(indir, "iris.csv"), "w", encoding="utf-8") as fh:
            fh.write(csv)
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n比较物种间差异并分析分布。\n")
        rc, _out, err = _run(["scripts/profile.py", "profile",
                              "--input", indir, "--output", outdir,
                              "--work", workdir], cwd=pkg)
        if rc != 0:
            problems.append("FAIL profile 退出码 %d: %s" % (rc, err))
        for f in ("data_dictionary.json", "chart_plan.json"):
            fp = os.path.join(outdir, f)
            if not os.path.isfile(fp):
                problems.append("FAIL profile 未生成 %s" % f)
            else:
                obj = json.load(open(fp, "r", encoding="utf-8"))
                if f == "chart_plan.json" and not obj.get("plans"):
                    problems.append("FAIL chart_plan.json 空 plans")
    except Exception as e:
        problems.append("FAIL profile 异常: %s" % e)
    return problems


def main(argv=None):
    p = argparse.ArgumentParser(description="Skill 包自检")
    p.add_argument("--package", default=".")
    p.add_argument("--skip-smoke", action="store_true")
    args = p.parse_args(argv)
    pkg = os.path.abspath(args.package)
    problems = []
    problems += check_frontmatter(pkg)
    problems += check_scripts(pkg)
    if not args.skip_smoke:
        problems += _smoke_init(pkg, "empty", lambda d: None)
        problems += _smoke_init(pkg, "badjson", lambda d: open(
            os.path.join(d, "case.json"), "w", encoding="utf-8").write("{not json"))
        problems += _smoke_init(pkg, "missing", lambda d: open(
            os.path.join(d, "case.json"), "w", encoding="utf-8").write(
                json.dumps({"case_id": "c1", "output_contract": {"figures": None,
                                                                 "statistics": []}})))
        problems += _smoke_profile(pkg)
    fails = [x for x in problems if x.startswith("FAIL")]
    warns = [x for x in problems if x.startswith("WARN")]
    print("== selftest: %s ==\nPASS count: %d" % (pkg, 0))
    for w in warns:
        print(w)
    for f in fails:
        print(f)
    print("FAIL: %d, WARN: %d" % (len(fails), len(warns)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
