#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 包自检：在交付前于任意机器上验证 SKILL.md、脚本可运行性与三类异常场景。

用法（在 Skill 根目录运行）：
    python3 scripts/selftest.py [--root <skill_root>]

不联网、不写输入目录；仅在系统临时目录中造最小用例。退出码 0 表示自检通过。
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


def run(cmd, cwd):
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )
        return proc.returncode, proc.stdout.decode("utf-8", errors="replace"), proc.stderr.decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -999, "", str(exc)


def make_case(base, case_obj=None, task_text=None, bad_case_json=None):
    inp = os.path.join(base, "input")
    os.makedirs(inp, exist_ok=True)
    if bad_case_json is not None:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write(bad_case_json)
    else:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case_obj or {}, fh, ensure_ascii=False)
    if task_text is not None:
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write(task_text)
    return inp


def checks(root):
    results = []

    def add(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    # 1. SKILL.md 与 frontmatter
    skill_path = os.path.join(root, "SKILL.md")
    add("SKILL.md 存在", os.path.exists(skill_path))
    skill_text = ""
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8-sig") as fh:
            skill_text = fh.read()
        m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", skill_text, re.S)
        add("SKILL.md 含 YAML frontmatter", bool(m))
        if m:
            fm = m.group(1)
            add("frontmatter 含 name", bool(re.search(r"^name\s*:", fm, re.M)))
            add("frontmatter 含 description", bool(re.search(r"^description\s*:", fm, re.M)))
            add("frontmatter 含 version", bool(re.search(r"^version\s*:", fm, re.M)))
        add("SKILL.md 正文 ≤180 行", len(skill_text.splitlines()) <= 180,
            f"lines={len(skill_text.splitlines())}")

    # 2. 脚本存在且可被 Python 解析
    main_py = os.path.join(root, "scripts", "main.py")
    add("scripts/main.py 存在", os.path.exists(main_py))
    if os.path.exists(main_py):
        with open(main_py, "r", encoding="utf-8") as fh:
            src = fh.read()
        add("main.py < 30KB", len(src.encode("utf-8")) < 30 * 1024,
            f"bytes={len(src.encode('utf-8'))}")
        try:
            compile(src, "main.py", "exec")
            add("main.py 语法合法", True)
        except SyntaxError as exc:
            add("main.py 语法合法", False, str(exc))
    scripts_dir = os.path.join(root, "scripts")
    if os.path.isdir(scripts_dir):
        for f in sorted(os.listdir(scripts_dir)):
            if not f.endswith(".py"):
                continue
            fp = os.path.join(scripts_dir, f)
            with open(fp, "r", encoding="utf-8") as fh:
                src = fh.read()
            try:
                compile(src, f, "exec")
                add(f"脚本 {f} 语法合法", True)
            except SyntaxError as exc:
                add(f"脚本 {f} 语法合法", False, str(exc))

    return results


def scenario_empty(root):
    with tempfile.TemporaryDirectory() as td:
        inp = make_case(td, case_obj={"id": "case-empty", "output_contract": {}})
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        rc, so, se = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                          "--input", inp, "--output", out, "--work", work], root)
        if rc != 0:
            return False, f"skeleton 退出码 {rc}: {se}"
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        if res.get("case_id") != "case-empty":
            return False, f"case_id 错误: {res.get('case_id')}"
        if res.get("status") != "partial":
            return False, f"skeleton status 应为 partial: {res.get('status')}"
        rc2, _, se2 = run([sys.executable, os.path.join(root, "scripts", "main.py"), "validate",
                           "--input", inp, "--output", out, "--work", work], root)
        # 空契约下 validate 应通过契约字段检查（无缺失键）
        if rc2 not in (0, 1):
            return False, f"validate 异常退出 {rc2}: {se2}"
        return True, ""


def scenario_bad_json(root):
    with tempfile.TemporaryDirectory() as td:
        inp = make_case(td, bad_case_json="{ not valid json ")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        rc, _, se = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                         "--input", inp, "--output", out, "--work", work], root)
        if rc != 0:
            return False, f"坏 JSON 应降级不崩溃，退出码 {rc}: {se}"
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        if res.get("status") not in ("partial", "insufficient_evidence", "input_error"):
            return False, f"坏 JSON 状态异常: {res.get('status')}"
        return True, ""


def scenario_missing_input(root):
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")  # 不创建
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        rc, _, se = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                         "--input", inp, "--output", out, "--work", work], root)
        # 即使 /input 不存在也要能落盘合法 result.json（skeleton 不依赖输入存在）
        if rc != 0:
            return False, f"缺输入目录应不崩溃，退出码 {rc}: {se}"
        res_path = os.path.join(out, "result.json")
        if not os.path.exists(res_path):
            return False, "缺输入目录未落盘 result.json"
        res = json.load(open(res_path, encoding="utf-8"))
        if res.get("case_id") != "unknown":
            return False, f"缺 case 时 case_id 应为 unknown: {res.get('case_id')}"
        return True, ""


def scenario_idempotent(root):
    with tempfile.TemporaryDirectory() as td:
        inp = make_case(td, case_obj={
            "id": "case-idem",
            "output_contract": {"summary": "string", "items": ["x"]},
        }, task_text="# 测试任务")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        r1 = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                  "--input", inp, "--output", out, "--work", work], root)
        a = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        r2 = run([sys.executable, os.path.join(root, "scripts", "main.py"), "skeleton",
                  "--input", inp, "--output", out, "--work", work], root)
        b = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        # runtime 字段会随时间变化，比较除 runtime 外的契约部分
        ka = {k: v for k, v in a.items() if k != "runtime"}
        kb = {k: v for k, v in b.items() if k != "runtime"}
        if ka != kb:
            return False, "重复运行结果不一致（除 runtime 外）"
        if a.get("case_id") != "case-idem":
            return False, f"case_id 错误: {a.get('case_id')}"
        if a.get("summary") is not None or a.get("items") != []:
            return False, "output_contract 占位类型错误"
        return True, ""


def main():
    ap = argparse.ArgumentParser(description="Skill 包自检")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    results = checks(root)
    scenarios = [
        ("空输入目录降级", scenario_empty),
        ("坏 JSON 降级", scenario_bad_json),
        ("缺输入目录", scenario_missing_input),
        ("重复运行确定性", scenario_idempotent),
    ]
    scenario_results = []
    for name, fn in scenarios:
        ok, detail = fn(root)
        scenario_results.append((name, ok, detail))

    failed = [r for r in results if not r[1]] + [s for s in scenario_results if not s[1]]

    print("== 结构检查 ==")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    print("== 场景自检 ==")
    for name, ok, detail in scenario_results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    if failed:
        print(f"\n自检失败 {len(failed)} 项")
        return 1
    print("\n自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
