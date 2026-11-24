# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检（仅标准库，无外部依赖）。

覆盖四类健壮性场景 + 主流程冒烟：
  1) SKILL.md frontmatter 齐备（name/description/version）
  2) 缺参数 / --help 退出码 0
  3) 空输入目录退出码 0 且产出合法 result.json
  4) 坏 JSON 退出码 0 且记录 errors
  5) 正常输入：init -> profile -> seed-plan -> render-figure -> validate 全链路
  6) figure_model 渲染出的 SVG 可被 xml 解析，且确定性哈希可复现

运行：python3 -B scripts/selftest.py（从包根执行）。
"""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402

PASS = 0
FAIL = 0
MESSAGES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        MESSAGES.append("PASS  %s" % name)
    else:
        FAIL += 1
        MESSAGES.append("FAIL  %s  %s" % (name, detail))


def run(argv):
    return core_impl.dispatch(argv)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(base)
    skill_md = os.path.join(root, "SKILL.md")

    # 1) SKILL.md frontmatter
    if os.path.isfile(skill_md):
        text = open(skill_md, "r", encoding="utf-8").read()
        check("SKILL.md 存在", True)
        check(
            "SKILL.md 含 name",
            "\nname:" in text[:800],
            "frontmatter 缺 name",
        )
        check(
            "SKILL.md 含 description",
            "\ndescription:" in text[:800],
            "frontmatter 缺 description",
        )
        check(
            "SKILL.md 含 version",
            "\nversion:" in text[:800],
            "frontmatter 缺 version",
        )
    else:
        check("SKILL.md 存在", False, skill_md)

    for fname in ("main.py", "core_impl.py"):
        check("scripts/%s 存在" % fname, os.path.isfile(os.path.join(base, fname)))

    # 2) 缺参数 / help
    check("--help 退出码 0", run(["--help"]) == 0)
    check("无参数退出码 0", run([]) == 0)

    tmp = tempfile.mkdtemp(prefix="sci_selftest_")
    try:
        # 3) 空输入目录
        empty_in = os.path.join(tmp, "empty_in")
        empty_out = os.path.join(tmp, "empty_out")
        empty_work = os.path.join(tmp, "empty_work")
        os.makedirs(empty_in)
        rc = run(["init", "--input", empty_in, "--output", empty_out, "--work", empty_work])
        res_path = os.path.join(empty_out, "result.json")
        check("空输入 init 退出码 0", rc == 0)
        if os.path.isfile(res_path):
            r = read_json(res_path)
            check("空输入 result.json 合法", isinstance(r, dict) and r.get("status") == "partial")
        else:
            check("空输入 result.json 合法", False, "未生成")

        # 4) 坏 JSON
        bad_in = os.path.join(tmp, "bad_in")
        bad_out = os.path.join(tmp, "bad_out")
        bad_work = os.path.join(tmp, "bad_work")
        os.makedirs(bad_in)
        write_text(os.path.join(bad_in, "case.json"), "{ this is not json ")
        write_text(os.path.join(bad_in, "task.md"), "# 任务\n1. 读取数据\n")
        rc = run(["init", "--input", bad_in, "--output", bad_out, "--work", bad_work])
        check("坏 JSON init 退出码 0", rc == 0)
        if os.path.isfile(os.path.join(bad_out, "result.json")):
            r = read_json(os.path.join(bad_out, "result.json"))
            check("坏 JSON 记录 errors", isinstance(r.get("errors"), list) and len(r["errors"]) > 0)
        else:
            check("坏 JSON 记录 errors", False, "未生成")

        # 5) 正常输入全链路
        good_in = os.path.join(tmp, "good_in")
        good_out = os.path.join(tmp, "good_out")
        good_work = os.path.join(tmp, "good_work")
        mat = os.path.join(good_in, "materials")
        os.makedirs(mat)
        write_text(
            os.path.join(good_in, "case.json"),
            json.dumps(
                {
                    "case_id": "selftest-001",
                    "output_contract": {
                        "cleaned_data": "path",
                        "statistics": "object",
                        "figure": "path",
                        "summary": "text",
                    },
                },
                ensure_ascii=False,
            ),
        )
        write_text(
            os.path.join(good_in, "task.md"),
            "# 案例任务\n1. 清洗数据\n2. 计算均值\n3. 绘制散点图\n",
        )
        write_text(
            os.path.join(mat, "data.csv"),
            "group,x,y\nA,1,2.5\nA,2,3.0\nB,3,4.5\nB,4,5.0\n",
        )
        write_text(os.path.join(mat, "dict.md"), "# 数据字典\n字段 y 单位 g/L\n")
        # sqlite
        import sqlite3

        con = sqlite3.connect(os.path.join(mat, "db.sqlite"))
        con.execute("CREATE TABLE t (id INTEGER, grp TEXT, val REAL)")
        con.executemany("INSERT INTO t VALUES (?,?,?)", [(1, "A", 1.1), (2, "B", 2.2)])
        con.commit()
        con.close()

        rc = run(["init", "--input", good_in, "--output", good_out, "--work", good_work])
        check("init 退出码 0", rc == 0)
        r = read_json(os.path.join(good_out, "result.json"))
        check("init 回填 case_id", r.get("case_id") == "selftest-001")
        for k in ("cleaned_data", "statistics", "figure", "summary"):
            check("init 回填契约键 %s" % k, k in r)
        check("init status=partial", r.get("status") == "partial")

        rc = run(["profile", "--input", good_in, "--output", good_out])
        check("profile 退出码 0", rc == 0)
        pr = read_json(os.path.join(good_out, "data_profile.json"))
        check("profile 发现 5 个文件", pr.get("file_count") == 5, "file_count=%s" % pr.get("file_count"))
        csvf = next((f for f in pr["files"] if f["locator"].endswith("data.csv")), None)
        check("profile 画像 CSV", csvf is not None and csvf["profile"]["n_rows"] == 4)

        rc = run(["seed-plan", "--input", good_in, "--output", good_out, "--work", good_work])
        check("seed-plan 退出码 0", rc == 0)
        plan = read_json(os.path.join(good_work, "plan.json"))
        check("seed-plan 字段候选 > 0", len(plan.get("fields", [])) > 0)
        check("seed-plan 步骤 > 0", len(plan.get("steps", [])) > 0)

        fm = {
            "schema_version": 1,
            "title": "selftest 散点",
            "output": "scatter.svg",
            "data": {"source": "materials/data.csv"},
            "marks": [
                {
                    "type": "scatter",
                    "x": {"field": "x", "title": "X"},
                    "y": {"field": "y", "title": "Y", "unit": "g/L"},
                    "color": {"field": "group"},
                }
            ],
            "legend": {"show": True},
            "evidence": {"locator": "materials/dict.md#L2", "quote": "字段 y 单位 g/L"},
        }
        write_text(os.path.join(good_work, "figure_model.json"), json.dumps(fm, ensure_ascii=False))
        rc = run(
            [
                "render-figure",
                "--model",
                os.path.join(good_work, "figure_model.json"),
                "--input",
                good_in,
                "--output",
                good_out,
                "--work",
                good_work,
            ]
        )
        check("render-figure 退出码 0", rc == 0)
        svg_path = os.path.join(good_out, "scatter.svg")
        if os.path.isfile(svg_path):
            ET.parse(svg_path)
            check("SVG 可解析", True)
        else:
            check("SVG 可解析", False, "未生成")
        fr = read_json(os.path.join(good_out, "figure_render.json"))
        check("figure_render 含确定性哈希", len(fr.get("model_sha256", "")) == 64)

        # 补 sources 后 validate
        r["sources"] = [{"locator": "materials/dict.md#L2", "quote": "字段 y 单位 g/L"}]
        r["status"] = "partial"
        write_text(os.path.join(good_out, "result.json"), json.dumps(r, ensure_ascii=False))
        rc = run(["validate", "--input", good_in, "--output", good_out, "--repair"])
        check("validate 退出码 0", rc == 0)
        vr = read_json(os.path.join(good_out, "validation_report.json"))
        check("validate 产出报告", isinstance(vr.get("checks"), list))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n".join(MESSAGES))
    print("TOTAL pass=%d fail=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
