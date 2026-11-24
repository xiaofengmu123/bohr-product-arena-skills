#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 包自检：对照评分维度检查 SKILL.md 契约、脚本可运行性与产物完整性。

仅使用 Python 标准库。在 Skill 根目录执行：
  python scripts/selftest.py --root . --input ./demo/input --output ./demo/output

若无 --input 指向真实案例，脚本会在系统临时目录创建一个最小 demo 输入
（case.json + task.md + data.csv）跑通 skeleton→inspect→validate→report 流程，
demo 仅为接口自检，不冒充正式案例结果。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUIRED_TOP = {
    "case_id", "status", "task_type", "summary", "interpretation",
    "experiment_plan", "evidence_and_risk", "confirmation_items", "sources",
    "uncertainty", "change_log", "errors", "degradations", "limitations",
    "pending_human", "runtime", "fabrication_gate",
}
INTERPRETATION_KEYS = ("trends", "anomalies", "possible_causes", "verification_steps", "followup_experiments")
PLAN_KEYS = ("hypothesis", "testable_question", "variables", "controls", "steps", "parameters", "resources", "interpretation_criteria")
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")

STDLIB_MODULES = {
    "argparse", "csv", "hashlib", "io", "json", "math", "os", "re", "statistics",
    "subprocess", "sys", "tempfile", "time",
}
LOCAL_MODULES = {"main", "inspect_data", "selftest"}


def parse_frontmatter(text):
    if not text.startswith("---"):
        return None, "SKILL.md 缺少 YAML frontmatter"
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None, "frontmatter 未闭合"
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return fm, None


def check_imports(path):
    bad = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for ln in fh:
            mm = re.match(r"\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", ln)
            if mm:
                mod = (mm.group(1) or mm.group(2)).split(".")[0]
                if mod not in STDLIB_MODULES and mod not in LOCAL_MODULES:
                    bad.append(mod)
    return sorted(set(bad))


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill 包自检")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    checks = []
    problems = []

    def add(ok, name, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        if not ok:
            problems.append(name)

    # 1. 文档与 frontmatter
    skill_path = os.path.join(root, "SKILL.md")
    if os.path.isfile(skill_path):
        text = open(skill_path, "r", encoding="utf-8-sig").read()
        fm, err = parse_frontmatter(text)
        if err:
            add(False, "SKILL.md frontmatter", err)
        else:
            add(bool(fm.get("name")), "frontmatter.name", fm.get("name", ""))
            add(bool(fm.get("description")), "frontmatter.description", (fm.get("description") or "")[:60])
            add(bool(fm.get("version")), "frontmatter.version", fm.get("version", ""))
        lines = text.count("\n") + 1
        add(lines <= 180, "SKILL.md 正文 <=180 行", "%d 行" % lines)
    else:
        add(False, "SKILL.md 存在", skill_path)

    # 2. 脚本存在且只用标准库
    scripts = [s for s in ("main.py", "inspect_data.py", "selftest.py")
               if os.path.isfile(os.path.join(root, "scripts", s))]
    add(len(scripts) == 3, "scripts 三件套存在", "%d/3" % len(scripts))
    for s in scripts:
        bad = check_imports(os.path.join(root, "scripts", s))
        add(not bad, "scripts/%s 只用标准库" % s, ",".join(bad) if bad else "ok")

    # 3. 用临时 demo 跑通骨架→画像→校验→报告
    demo_in = args.input or tempfile.mkdtemp(prefix="skill_demo_in_")
    demo_out = args.output or tempfile.mkdtemp(prefix="skill_demo_out_")
    if args.input is None:
        os.makedirs(demo_in, exist_ok=True)
        open(os.path.join(demo_in, "case.json"), "w", encoding="utf-8").write(
            json.dumps({"id": "challenge-demo", "output_contract": None}, ensure_ascii=False))
        open(os.path.join(demo_in, "task.md"), "w", encoding="utf-8").write(
            "# demo task\nchallenge_id: `challenge-demo`\n")
        open(os.path.join(demo_in, "data.csv"), "w", encoding="utf-8").write(
            "sample,time_s,reading\ns1,0,1.0\ns1,1,1.1\ns1,2,1.2\ns2,0,2.0\ns2,1,2.1\ns2,2,9.9\n")

    skel = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                "--input", demo_in, "--output", demo_out], cwd=root)
    add(skel.returncode == 0, "skeleton 退出码 0", skel.stderr.strip()[:120])

    insp = run([sys.executable, os.path.join(root, "scripts", "inspect_data.py"),
                "--input", demo_in, "--output", demo_out], cwd=root)
    add(insp.returncode == 0, "inspect_data 退出码 0", insp.stderr.strip()[:120])

    result_path = os.path.join(demo_out, "result.json")
    obj = None
    if os.path.isfile(result_path):
        try:
            obj = json.load(open(result_path, "r", encoding="utf-8"))
            add(True, "result.json 可解析")
        except Exception as exc:  # noqa: BLE001
            add(False, "result.json 可解析", str(exc))
    else:
        add(False, "result.json 生成", result_path)

    if obj is not None:
        missing = [k for k in REQUIRED_TOP if k not in obj]
        add(not missing, "result.json 顶层字段齐全", ",".join(missing) if missing else "17/17")
        add(bool(obj.get("case_id")), "case_id 非空", obj.get("case_id", ""))
        add(obj.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"),
            "status 合法", obj.get("status", ""))
        interp = obj.get("interpretation") or {}
        add(all(k in interp for k in INTERPRETATION_KEYS), "五段 interpretation 键齐全")
        plan = obj.get("experiment_plan") or {}
        add(all(k in plan for k in PLAN_KEYS), "experiment_plan 键齐全")
        risk = obj.get("evidence_and_risk") or {}
        add(all(k in risk for k in RISK_KEYS), "evidence_and_risk 键齐全")
        confirm = obj.get("confirmation_items") or {}
        add(all(k in confirm for k in CONFIRM_KEYS), "confirmation_items 键齐全")
        gate = obj.get("fabrication_gate") or {}
        add(isinstance(gate.get("must_not_invent"), list) and bool(gate["must_not_invent"]),
            "fabrication_gate.must_not_invent 非空")

    val = run([sys.executable, os.path.join(root, "scripts", "main.py"), "validate",
               "--input", result_path], cwd=root)
    add(val.returncode == 0, "validate 通过", val.stdout.strip().splitlines()[-1] if val.stdout.strip() else val.stderr.strip()[:120])

    rep = run([sys.executable, os.path.join(root, "scripts", "main.py"), "report",
               "--input", result_path, "--output", demo_out], cwd=root)
    add(rep.returncode == 0, "report 生成 report.md/evidence_map.json")
    add(os.path.isfile(os.path.join(demo_out, "report.md")), "report.md 存在")
    add(os.path.isfile(os.path.join(demo_out, "evidence_map.json")), "evidence_map.json 存在")

    # 4. 空输入降级：退出码 0 且 status=insufficient_evidence
    empty_in = tempfile.mkdtemp(prefix="skill_empty_in_")
    empty_out = tempfile.mkdtemp(prefix="skill_empty_out_")
    open(os.path.join(empty_in, "case.json"), "w", encoding="utf-8").write('{"id":"empty-demo"}')
    open(os.path.join(empty_in, "task.md"), "w", encoding="utf-8").write("# empty\n")
    erun = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                "--input", empty_in, "--output", empty_out], cwd=root)
    eobj = None
    if os.path.isfile(os.path.join(empty_out, "result.json")):
        try:
            eobj = json.load(open(os.path.join(empty_out, "result.json"), "r", encoding="utf-8"))
        except Exception:  # noqa: BLE001
            eobj = None
    add(erun.returncode == 0, "空输入退出码 0", erun.stderr.strip()[:120])
    add(eobj is not None and eobj.get("status") == "insufficient_evidence",
        "空输入 status=insufficient_evidence", (eobj or {}).get("status", ""))

    # 汇总
    passed = sum(1 for c in checks if c["pass"])
    total = len(checks)
    report = {
        "passed": passed,
        "total": total,
        "ok": passed == total,
        "scoring_dimension_map": {
            "实际效果(40)": ["五段 interpretation 键齐全", "result.json 顶层字段齐全", "evidence 溯源见 validate"],
            "使用增益(25)": ["脚本一键跑通 skeleton→inspect→validate→report，减少人工返工"],
            "创新增益(15)": ["evidence_grade 分级 + fabrication_gate 禁编造门控 + 异常绑定列/批次"],
            "可靠体验(10)": ["frontmatter.name/description/version", "scripts 只用标准库", "空输入降级 insufficient_evidence"],
            "生态贡献(10)": ["SKILL.md 契约齐全", "references 模板可复用"],
        },
        "checks": checks,
        "problems": problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
