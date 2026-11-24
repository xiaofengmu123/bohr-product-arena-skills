# -*- coding: utf-8 -*-
"""提交/交付前自检：验证包结构与 result.json 契约，并跑三类降级场景。

覆盖：frontmatter、脚本可执行、空输入 / 坏 JSON / 最小可用输入三种场景，
以及 result.json 的 case_id、status、output_contract 回显、无 NaN、证据定位。
"""

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C

SKILL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SKILL.md")


def check_frontmatter():
    if not os.path.exists(SKILL_PATH):
        return False, "SKILL.md 缺失"
    text = C.read_text(SKILL_PATH)[0] or ""
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False, "SKILL.md 缺少 YAML frontmatter"
    body = m.group(1)
    for key in ("name", "description", "version"):
        if not re.search(r"^%s\s*:" % key, body, re.M):
            return False, "frontmatter 缺少 %s" % key
    return True, "frontmatter 齐备"


def check_scripts_help():
    import subprocess
    d = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = subprocess.run(
            [sys.executable, "-B", os.path.join(d, "main.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0, "main.py --help 退出码 %d" % proc.returncode
    except (OSError, subprocess.SubprocessError) as e:
        return False, "main.py --help 无法运行: %s" % e


def run_pipeline(input_dir, output_dir, work_dir):
    C.run("skeleton", input_dir, output_dir, work_dir)
    C.run("evidence", input_dir, output_dir, work_dir)
    C.run("finalize", input_dir, output_dir, work_dir)
    obj, err, _ = C.read_json(os.path.join(output_dir, "result.json"))
    return obj, err


def check_result_contract(result):
    if not isinstance(result, dict):
        return False, "result.json 不是对象"
    if not result.get("case_id"):
        return False, "缺少 case_id"
    if result.get("status") not in C.STATUSES:
        return False, "status 非法: %r" % result.get("status")
    if C.find_nonfinite(result):
        return False, "result.json 含 NaN/Infinity"
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            return False, "缺少字段 %s" % key
    return True, "契约字段齐备"


def scenario_empty():
    root = tempfile.mkdtemp(prefix="sk_selftest_empty_")
    inp, out, wrk = (os.path.join(root, x) for x in ("input", "output", "work"))
    os.makedirs(inp)
    result, err = run_pipeline(inp, out, wrk)
    ok = (err is None and result.get("status") == "insufficient_evidence"
          and result.get("case_id", "").startswith("unresolved-"))
    return ok, "空输入 → insufficient_evidence（case_id=%s）" % result.get("case_id")


def scenario_bad_json():
    root = tempfile.mkdtemp(prefix="sk_selftest_badjson_")
    inp, out, wrk = (os.path.join(root, x) for x in ("input", "output", "work"))
    os.makedirs(inp)
    C.write_text_file(os.path.join(inp, "case.json"), "{ this is not valid json ")
    C.write_text_file(os.path.join(inp, "task.md"), "# 任务\n- challenge_id: challenge-x\n")
    result, err = run_pipeline(inp, out, wrk)
    has_error = any("bad_json" in str(e.get("error", "")) for e in result.get("errors", []))
    ok = (err is None and result.get("case_id") == "challenge-x" and has_error)
    return ok, "坏 JSON 不崩溃并登记 errors（case_id=%s）" % result.get("case_id")


def scenario_minimal():
    root = tempfile.mkdtemp(prefix="sk_selftest_min_")
    inp, out, wrk = (os.path.join(root, x) for x in ("input", "output", "work"))
    os.makedirs(os.path.join(inp, "materials"))
    C.write_text_file(
        os.path.join(inp, "case.json"),
        json.dumps({"case_id": "case-demo-1", "output_contract": {"final_conclusion": None, "grade": None}},
                   ensure_ascii=False),
    )
    C.write_text_file(os.path.join(inp, "task.md"), "# 任务\n- challenge_id: challenge-demo\n")
    C.write_text_file(os.path.join(inp, "materials", "paper1.txt"),
                      "方法 A 在数据集 X 上取得 0.91 的精度。\n")
    result, err = run_pipeline(inp, out, wrk)
    c, cerr, _ = C.read_json(os.path.join(inp, "case.json"))
    C.apply_output_contract(c, result)
    C.write_result(result, out)
    ok = (
        err is None
        and result.get("case_id") == "case-demo-1"
        and result.get("output_contract") == {"final_conclusion": None, "grade": None}
        and "final_conclusion" in result and "grade" in result
        and any(s.get("path") == "materials/paper1.txt" for s in result.get("sources", []))
    )
    return ok, "最小输入 → 契约回显 + sources 识别（case_id=%s）" % result.get("case_id")


def main():
    checks = []
    checks.append(("frontmatter",) + check_frontmatter())
    checks.append(("scripts_help",) + check_scripts_help())
    checks.append(("scenario_empty",) + scenario_empty())
    checks.append(("scenario_bad_json",) + scenario_bad_json())
    checks.append(("scenario_minimal",) + scenario_minimal())

    all_ok = True
    for name, ok, msg in checks:
        print("[%s] %s — %s" % ("PASS" if ok else "FAIL", name, msg))
        all_ok = all_ok and ok

    print("selftest %s" % ("PASSED" if all_ok else "FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
