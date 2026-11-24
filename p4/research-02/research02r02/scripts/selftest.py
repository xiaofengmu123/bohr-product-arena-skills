#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本：离线跑通脚手架与校验器，不生成正式答案。

覆盖（对应评分维度的可核验部分）：
  1) SKILL.md frontmatter 齐备（name / description / version）；
  2) 脚本只用 Python 标准库（import 白名单检查）；
  3) 合成案例端到端跑通：骨架落盘 + 契约回填 + 时间预算计划；
  4) 空输入目录：退出码 0 且写合法 input_error 骨架；
  5) 校验器能正确放行合法结果、拦截缺失字段与证据行缺 quote；
  6) 重复运行结果一致（case_id 与契约字段名稳定）。

合成案例仅用于自检，标注 example=True，绝不冒充正式评测结果。
用法：python3 -B scripts/selftest.py [--skill-root <Skill 根目录>]
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

ALLOWED_IMPORTS = {
    "argparse", "hashlib", "json", "os", "re", "subprocess", "sys",
    "time", "datetime", "xml.etree.ElementTree", "collections", "math",
    "shutil", "tempfile", "importlib", "stat", "glob", "copy",
}

PASS = []
FAIL = []


def check(cond, label):
    if cond:
        PASS.append(label)
    else:
        FAIL.append(label)


def load_main(skill_root):
    spec = importlib.util.spec_from_file_location(
        "litreview_main", os.path.join(skill_root, "scripts", "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_validate(skill_root):
    spec = importlib.util.spec_from_file_location(
        "litreview_validate", os.path.join(skill_root, "scripts", "validate_result.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_frontmatter(skill_root):
    md_path = os.path.join(skill_root, "SKILL.md")
    if not os.path.isfile(md_path):
        check(False, "SKILL.md 存在")
        return
    text = open(md_path, "r", encoding="utf-8").read()
    check(text.startswith("---"), "SKILL.md 含 YAML frontmatter 起始标记")
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        check(False, "SKILL.md frontmatter 可解析")
        return
    fm = m.group(1)
    for key in ("name", "description", "version"):
        check(re.search(r"^%s:\s*\S" % key, fm, re.M) is not None,
              "frontmatter 含 %s" % key)


def check_stdlib_only(skill_root):
    scripts = os.path.join(skill_root, "scripts")
    problems = []
    for name in sorted(os.listdir(scripts)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(scripts, name), "r", encoding="utf-8").read()
        for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z0-9_\.]+)", text, re.M):
            top = m.group(1).split(".")[0]
            if top not in ALLOWED_IMPORTS:
                problems.append("%s: %s" % (name, top))
    check(not problems, "脚本只用标准库" + ("（发现 %s）" % problems if problems else ""))


def run_case(mod, base_dir, case_obj, material_text=None):
    input_dir = os.path.join(base_dir, "input")
    output_dir = os.path.join(base_dir, "output")
    work_dir = os.path.join(base_dir, "work")
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case_obj, fh, ensure_ascii=False)
    if material_text is not None:
        mats = os.path.join(input_dir, "materials")
        os.makedirs(mats, exist_ok=True)
        with open(os.path.join(mats, "paper_a.txt"), "w", encoding="utf-8") as fh:
            fh.write(material_text)
    rc = mod.main(["--input", input_dir, "--output", output_dir, "--work", work_dir])
    return rc, input_dir, output_dir, work_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="Skill 自检")
    parser.add_argument("--skill-root", default=None, help="Skill 根目录")
    args = parser.parse_args(argv)
    skill_root = args.skill_root or os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))

    check_frontmatter(skill_root)
    check_stdlib_only(skill_root)

    mod = load_main(skill_root)
    val = load_validate(skill_root)
    base = tempfile.mkdtemp(prefix="litreview_selftest_")

    try:
        # 场景 1：合成案例 + 材料，端到端。
        case_obj = {
            "case_id": "selftest-001",
            "research_question": "示例问题（仅自检）",
            "output_contract": {
                "type": "object",
                "properties": {
                    "research_question": {"type": "string"},
                    "screening_log": {"type": "array"},
                    "evidence_table": {"type": "array"},
                    "review_draft": {"type": "string"},
                },
            },
            "example": True,
        }
        material = "示例材料段落。仅用于自检，不冒充正式结果。\nMethods: n=42.\n"
        rc, in_dir, out_dir, work_dir = run_case(mod, base, case_obj, material)
        check(rc == 0, "合成案例 main.py 退出码 0")
        rpath = os.path.join(out_dir, "result.json")
        res, err = val.load_json(rpath)
        check(res is not None and not err, "合成案例 result.json 合法")
        if isinstance(res, dict):
            check(res.get("case_id") == "selftest-001", "case_id 正确")
            check(res.get("status") in ("partial", "ok"), "status 合法")
            for fname in ("research_question", "screening_log",
                          "evidence_table", "review_draft"):
                check(fname in res, "契约字段回填: " + fname)
            check(isinstance(res.get("change_log"), list), "change_log 存在")
        check(os.path.isfile(os.path.join(work_dir, "plan.json")), "plan.json 已写")
        check(os.path.isfile(os.path.join(work_dir, "inventory.json")), "inventory.json 已写")
        inv = val.load_json(os.path.join(work_dir, "inventory.json"))[0]
        check(isinstance(inv, dict) and inv.get("materials"), "材料已盘点")

        # 校验器对合法骨架应放行。
        rc2 = val.main(["--output", out_dir, "--case",
                        os.path.join(in_dir, "case.json"),
                        "--work", work_dir])
        check(rc2 == 0, "校验器放行合法骨架")

        # 场景 2：空输入目录。
        empty_base = os.path.join(base, "empty")
        os.makedirs(os.path.join(empty_base, "input"), exist_ok=True)
        rc3 = mod.main(["--input", os.path.join(empty_base, "input"),
                        "--output", os.path.join(empty_base, "output"),
                        "--work", os.path.join(empty_base, "work")])
        res3, _ = val.load_json(os.path.join(empty_base, "output", "result.json"))
        check(rc3 == 0 and res3 is not None and res3.get("status") == "input_error",
              "空输入目录返回 input_error 且退出码 0")

        # 场景 3：校验器拦截缺失字段 / 证据行缺 quote。
        bad_out = os.path.join(base, "bad")
        os.makedirs(bad_out, exist_ok=True)
        with open(os.path.join(bad_out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "bad", "status": "ok"}, fh, ensure_ascii=False)
        rc4 = val.main(["--output", bad_out, "--case",
                        os.path.join(in_dir, "case.json"),
                        "--work", work_dir])
        check(rc4 == 1, "校验器拦截不完整 result.json")

        # 场景 4：证据行缺 quote 被拦截。
        with open(os.path.join(bad_out, "evidence_table.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({"evidence_id": "e1", "source_id": "s1",
                                 "locator": "p1"}) + "\n")
        rc5 = val.main(["--output", bad_out, "--case",
                        os.path.join(in_dir, "case.json"),
                        "--work", work_dir])
        check(rc5 == 1, "校验器拦截证据行缺 quote")

        # 场景 5：重复运行确定性（契约字段名稳定）。
        out2 = os.path.join(base, "run2", "output")
        work2 = os.path.join(base, "run2", "work")
        mod.main(["--input", in_dir, "--output", out2, "--work", work2])
        res_a = val.load_json(rpath)[0]
        res_b = val.load_json(os.path.join(out2, "result.json"))[0]
        check(set(k for k in res_a if k not in ("runtime",)) ==
              set(k for k in res_b if k not in ("runtime",)),
              "重复运行契约字段名稳定")

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\n===== 自检结果 =====")
    print("PASS: %d 项" % len(PASS))
    for label in PASS:
        print("  [PASS] " + label)
    if FAIL:
        print("FAIL: %d 项" % len(FAIL))
        for label in FAIL:
            print("  [FAIL] " + label)
    print("====================")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
