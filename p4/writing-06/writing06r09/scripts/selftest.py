#!/usr/bin/env python3
"""包级自检：提交/交付前运行，确认 Skill 可加载、脚本可运行、result.json
契约在空目录/坏 JSON 等场景下成立。退出码 0 通过，任一 FAIL 非零。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def check(report, name, ok, detail=""):
    report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        report["failed"] += 1
    return bool(ok)


def run_py(args):
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main():
    report = {"failed": 0, "checks": []}

    # 1. SKILL.md 与 frontmatter
    skill = os.path.join(ROOT, "SKILL.md")
    check(report, "SKILL.md 存在", os.path.isfile(skill))
    fm_ok = False
    if os.path.isfile(skill):
        with open(skill, encoding="utf-8-sig", errors="replace") as f:
            head = f.read(2000)
        fm_ok = head.startswith("---") and "name:" in head and "description:" in head and "version:" in head
    check(report, "SKILL.md frontmatter(name/description/version)", fm_ok)

    # 2. 脚本存在
    for s in ("main.py", "structure.py"):
        check(report, "scripts/%s 存在" % s, os.path.isfile(os.path.join(HERE, s)))

    # 3. 空输入目录 build + validate
    with tempfile.TemporaryDirectory() as td:
        inp, out, work = (os.path.join(td, "input"), os.path.join(td, "output"), os.path.join(td, "work"))
        for d in (inp, out, work):
            os.makedirs(d)
        r = run_py([os.path.join(HERE, "main.py"), "build",
                    "--input", inp, "--out", os.path.join(out, "result.json"), "--work", work])
        res_path = os.path.join(out, "result.json")
        ok_empty = r.returncode == 0 and os.path.isfile(res_path)
        check(report, "空输入目录 build 退出码 0 且落盘 result.json", ok_empty, "rc=%d" % r.returncode)
        if ok_empty:
            data = json.load(open(res_path, encoding="utf-8"))
            fields_ok = data.get("status") == "partial" and all(
                k in data for k in ("case_id", "sources", "uncertainty", "change_log"))
            check(report, "空输入骨架 status=partial 且字段完整", fields_ok)
            r2 = run_py([os.path.join(HERE, "main.py"), "validate", "--out", res_path])
            check(report, "validate 对骨架通过", r2.returncode == 0, "rc=%d" % r2.returncode)

    # 4. 坏 JSON 输入不崩溃
    with tempfile.TemporaryDirectory() as td:
        inp, out, work = (os.path.join(td, "input"), os.path.join(td, "output"), os.path.join(td, "work"))
        for d in (inp, out, work):
            os.makedirs(d)
        bad_case = os.path.join(inp, "case.json")
        with open(bad_case, "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
        r = run_py([os.path.join(HERE, "main.py"), "build",
                    "--case", bad_case, "--input", inp,
                    "--out", os.path.join(out, "result.json"), "--work", work])
        check(report, "坏 JSON 输入不崩溃且落盘 result.json",
              r.returncode == 0 and os.path.isfile(os.path.join(out, "result.json")), "rc=%d" % r.returncode)

    # 5. structure.py 在示例文本上运行
    tmp = tempfile.gettempdir()
    sample = os.path.join(tmp, "structure_selftest_sample.txt")
    sample_json = os.path.join(tmp, "structure_selftest_sample.json")
    with open(sample, "w", encoding="utf-8") as f:
        f.write("# 引言\n\n结果表明该方法显著优于基线。\n\n结果表明该方法显著优于基线。\n\n## 方法\n\n我们提出一种新方法。\n")
    r = run_py([os.path.join(HERE, "structure.py"), sample, "--out", sample_json])
    ok_struct = r.returncode == 0
    check(report, "structure.py 正常运行", ok_struct, "rc=%d" % r.returncode)
    if ok_struct:
        data = json.load(open(sample_json, encoding="utf-8"))
        check(report, "structure.py 产出 claims/redundancy",
              "claims" in data and "redundancy" in data)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
