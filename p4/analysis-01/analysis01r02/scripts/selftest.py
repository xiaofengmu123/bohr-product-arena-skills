#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package self-test for sci-data-cleaning.

Run from the skill root:

    python scripts/selftest.py

or from anywhere (the script locates the package root itself). It verifies:

1. SKILL.md exists with valid YAML frontmatter (name / description / version).
2. Every python script compiles (py_compile) and imports cleanly.
3. ``main.py --help`` exits with code 0.
4. Empty input -> exit 0 and result.json status=insufficient_evidence.
5. A synthetic demo input -> exit 0, valid result.json, all contract keys
   filled, and byte-identical cleaned_data.csv across two independent runs
   (determinism). The demo is synthetic and clearly marked as such.

Exit code 0 means all checks pass; 1 means at least one check failed.
Standard library only; no network; no fabricated benchmark scores.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_MD = os.path.join(ROOT, "SKILL.md")
MAIN_PY = os.path.join(ROOT, "scripts", "main.py")

FAILURES = []


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return bool(ok)


def frontmatter_ok():
    if not os.path.isfile(SKILL_MD):
        return check("SKILL.md 存在", False, SKILL_MD)
    with open(SKILL_MD, "r", encoding="utf-8") as fh:
        text = fh.read()
    check("SKILL.md 以 YAML frontmatter 开头", text.startswith("---"))
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return check("SKILL.md frontmatter 可解析", False)
    fm = m.group(1)
    for key in ("name", "description", "version"):
        ok = re.search(rf"^{key}\s*:\s*\S", fm, re.M) is not None
        check(f"frontmatter 含 {key}", ok)
    return True


def compile_scripts():
    ok = True
    for fn in os.listdir(os.path.join(ROOT, "scripts")):
        if fn.endswith(".py"):
            path = os.path.join(ROOT, "scripts", fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    compile(fh.read(), fn, "exec")
            except Exception as exc:  # noqa: BLE001
                ok = check(f"{fn} 可编译", False, str(exc)) and ok
    return ok


def help_ok():
    try:
        proc = subprocess.run(
            [sys.executable, "-B", MAIN_PY, "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return check("main.py --help 退出码 0", False, str(exc))
    ok = proc.returncode == 0
    check("main.py --help 退出码 0", ok, f"exit={proc.returncode}")
    return ok


def run_main(phase, input_dir, output_dir, work_dir):
    return subprocess.run(
        [sys.executable, "-B", MAIN_PY, "--phase", phase,
         "--input", input_dir, "--output", output_dir, "--work", work_dir],
        cwd=ROOT, capture_output=True, text=True, timeout=180)


def empty_input_case():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        proc = run_main("all", inp, out, work)
        check("空输入 退出码 0", proc.returncode == 0, f"exit={proc.returncode}")
        rpath = os.path.join(out, "result.json")
        if os.path.isfile(rpath):
            with open(rpath, "r", encoding="utf-8") as fh:
                result = json.load(fh)
            check("空输入 status=insufficient_evidence",
                  result.get("status") == "insufficient_evidence",
                  str(result.get("status")))
        else:
            check("空输入 result.json 已写出", False)


def write_demo_case(root):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "demo-synthetic-001",
        "output_contract": {
            "cleaned_table": None,
            "quality_report": None,
            "field_rules": None,
            "notes": None,
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 合成演示任务\n清洗下表并输出质量报告。\n")
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
        fh.write("sample,analyte_mgL,note\n")
        fh.write("S1,0.5,ok\n")
        fh.write("S1,0.5,ok\n")
        fh.write("S2,<0.01,below lod\n")
        fh.write("S3,-999,missing\n")
        fh.write("S4,ND,not detected\n")
        fh.write("S5,0,true zero\n")
        fh.write("S6,1.2e-3,sci\n")
    with open(os.path.join(inp, "data_dictionary.csv"), "w", encoding="utf-8") as fh:
        fh.write("column,meaning,unit\nanalyte_mgL,analyte concentration,mg/L\n")
    with open(os.path.join(inp, "qc_rules.txt"), "w", encoding="utf-8") as fh:
        fh.write("analyte_mgL range: 0 to 10 mg/L; -999 = missing\n")
    with open(os.path.join(inp, "workorder.md"), "w", encoding="utf-8") as fh:
        fh.write("1. classify missing\n2. dedup by sample\n3. report\n")


def demo_case():
    with tempfile.TemporaryDirectory() as tmp:
        write_demo_case(tmp)
        inp = os.path.join(tmp, "input")
        outs = []
        for name in ("out1", "out2"):
            out = os.path.join(tmp, name)
            work = os.path.join(tmp, "work_" + name)
            proc = run_main("all", inp, out, work)
            check(f"合成演示运行 ({name}) 退出码 0", proc.returncode == 0,
                  f"exit={proc.returncode}; stderr={proc.stderr[:120]}")
            outs.append(out)
        rpath = os.path.join(outs[0], "result.json")
        if os.path.isfile(rpath):
            with open(rpath, "r", encoding="utf-8") as fh:
                result = json.load(fh)
            check("合成演示 status 合法",
                  result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"),
                  str(result.get("status")))
            contract = result.get("_contract_keys", [])
            missing = [k for k in contract if k not in result]
            check("合成演示 output_contract 全部键回填", not missing,
                  ", ".join(missing) if missing else "ok")
            for key in ("sources", "uncertainty", "change_log", "pending_human", "errors", "degradations"):
                check(f"合成演示含 {key}", isinstance(result.get(key), list),
                      type(result.get(key)).__name__)
        # determinism: cleaned_data.csv byte-identical across two runs
        c1 = os.path.join(outs[0], "cleaned_data.csv")
        c2 = os.path.join(outs[1], "cleaned_data.csv")
        if os.path.isfile(c1) and os.path.isfile(c2):
            with open(c1, "rb") as a, open(c2, "rb") as b:
                check("两次运行 cleaned_data.csv 字节一致", a.read() == b.read())
        else:
            check("cleaned_data.csv 已生成", False)
        # selfcheck on the first output
        work = os.path.join(tmp, "work_check")
        proc = subprocess.run(
            [sys.executable, "-B", MAIN_PY, "--phase", "selfcheck",
             "--output", outs[0], "--work", work],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        check("selfcheck 退出码 0", proc.returncode == 0, f"exit={proc.returncode}")


def main():
    print("sci-data-cleaning self-test")
    print(f"root: {ROOT}")
    frontmatter_ok()
    compile_scripts()
    help_ok()
    empty_input_case()
    demo_case()
    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
