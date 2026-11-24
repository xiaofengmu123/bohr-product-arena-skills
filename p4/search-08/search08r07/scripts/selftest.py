#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检：包结构 + 脚本语法 + 标准库约束 + 降级烟测。

只依赖标准库，无网络、无第三方依赖。全部通过退出码 0，任一失败退出码 1。
运行：
  python3 -B scripts/selftest.py
"""

import json
import os
import py_compile
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import pipeline  # noqa: E402  (标准库内本地模块)

RESULTS = []


def check(label, passed, detail=""):
    RESULTS.append((label, bool(passed), detail))
    tag = "PASS" if passed else "FAIL"
    print("%s %s%s" % (tag, label, ("  [%s]" % detail) if detail else ""))


def _frontmatter(text):
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    return m.group(1) if m else None


def _yaml_kv(front):
    kv = {}
    for line in front.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line.strip())
        if m:
            kv[m.group(1)] = m.group(2).strip().strip("\"'")
    return kv


def check_package():
    skill = os.path.join(ROOT, "SKILL.md")
    check("SKILL.md exists", os.path.isfile(skill))
    if not os.path.isfile(skill):
        return
    text = open(skill, encoding="utf-8").read()
    front = _frontmatter(text)
    check("SKILL.md has YAML frontmatter", front is not None)
    if front is None:
        return
    kv = _yaml_kv(front)
    check("frontmatter has name", bool(kv.get("name")))
    check("frontmatter has description", bool(kv.get("description")))
    check("frontmatter has version", bool(kv.get("version")))
    body_lines = text.split("---", 2)[2].strip().splitlines()
    check("SKILL.md body <= 180 lines", len(body_lines) <= 180,
          "actual=%d" % len(body_lines))


def check_scripts():
    files = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    check("scripts present (>=1)", len(files) >= 1)
    stdlib = getattr(sys, "stdlib_module_names", None)
    third_party = []
    for f in files:
        p = os.path.join(HERE, f)
        try:
            py_compile.compile(p, doraise=True)
            check("syntax ok: %s" % f, True)
        except py_compile.PyCompileError as e:
            check("syntax ok: %s" % f, False, str(e))
            continue
        src = open(p, encoding="utf-8").read()
        for m in re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w\.]*)", src, re.MULTILINE):
            top = m.split(".")[0]
            if top == "pipeline" or top == "__future__":
                continue
            if stdlib is not None:
                if top not in stdlib:
                    third_party.append("%s(%s)" % (top, f))
            else:
                # 保守白名单：本包实际只用到这些标准库模块
                if top not in {"os", "sys", "json", "csv", "re", "hashlib",
                               "difflib", "io", "argparse", "time", "runpy",
                               "py_compile", "tempfile", "shutil", "pathlib"}:
                    third_party.append("%s(%s)" % (top, f))
    check("scripts use stdlib only", not third_party, "; ".join(sorted(set(third_party))))


def _write(path, content):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def check_smoke():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        # 1) 空输入目录
        inp = os.path.join(tmp, "empty_in"); os.makedirs(inp)
        out = os.path.join(tmp, "empty_out"); work = os.path.join(tmp, "empty_work")
        r = pipeline.run_skeleton(inp, out, work)
        check("empty input -> insufficient_evidence",
              r.get("status") == "insufficient_evidence", "status=%s" % r.get("status"))

        # 2) 坏 JSON
        inp = os.path.join(tmp, "bad_in"); out = os.path.join(tmp, "bad_out")
        work = os.path.join(tmp, "bad_work")
        _write(os.path.join(inp, "case.json"), "{ not valid json")
        _write(os.path.join(inp, "task.md"), "# task")
        r = pipeline.run_skeleton(inp, out, work)
        check("bad JSON -> degradation recorded",
              any(d.get("file") == "case.json" for d in r.get("degradations", [])))

        # 3) 超大文件
        inp = os.path.join(tmp, "big_in"); out = os.path.join(tmp, "big_out")
        work = os.path.join(tmp, "big_work")
        _write(os.path.join(inp, "case.json"), '{"case_id":"c1"}')
        with open(os.path.join(inp, "huge.txt"), "w", encoding="utf-8") as f:
            f.write("x" * (pipeline.MAX_FILE_BYTES + 10))
        r = pipeline.run_skeleton(inp, out, work)
        check("oversize file skipped + degradation",
              any(d.get("file") == "huge.txt" for d in r.get("degradations", [])))

        # 4) 全链路端到端（skeleton -> normalize -> 手工判定 -> finalize -> selfcheck）
        inp = os.path.join(tmp, "full_in"); out = os.path.join(tmp, "full_out")
        work = os.path.join(tmp, "full_work")
        _write(os.path.join(inp, "case.json"), json.dumps({
            "case_id": "demo-001",
            "research_question": "X 是否有效",
            "output_contract": {"candidates": [], "screening_summary": {}},
        }))
        _write(os.path.join(inp, "task.md"), "# task\n研究问题：X 是否有效\n")
        _write(os.path.join(inp, "records.csv"),
               "title,doi,year,abstract\n"
               "Efficacy of X in adults,10.0000/demo1,2020,Randomized trial of X\n"
               "Unrelated weather study,10.0000/demo2,2019,Climate variability\n")
        pipeline.run_skeleton(inp, out, work)
        n = pipeline.run_normalize(work)
        check("end-to-end normalize unique=2", n.get("unique_count") == 2,
              "unique=%s" % n.get("unique_count"))
        jpath = os.path.join(work, "judgments.json")
        jd = json.load(open(os.path.join(work, "judgments_template.json"), encoding="utf-8"))
        for j in jd["judgments"]:
            j["decision"] = "include" if "X" in j["title"] else "exclude"
            j["reason_code"] = ("include_relevant" if "X" in j["title"]
                                else "ex_not_relevant")
            j["evidence_quote"] = j["title"]
        json.dump(jd, open(jpath, "w", encoding="utf-8"), ensure_ascii=False)
        pipeline.run_finalize(inp, out, work)
        ok, _ = pipeline.run_selfcheck(out, work)
        check("end-to-end selfcheck all PASS", ok)
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("end-to-end count closure", res["audit"]["counts"]["closure"] == "closed")
        check("end-to-end status=ok", res["status"] == "ok", "status=%s" % res["status"])

        # 5) 确定性：重复 normalize 结果一致
        d1 = pipeline.build_dedup(json.load(open(os.path.join(work, "records.json"),
                                                 encoding="utf-8"))["records"])
        d2 = pipeline.build_dedup(json.load(open(os.path.join(work, "records.json"),
                                                 encoding="utf-8"))["records"])
        check("deterministic dedup", d1["unique_count"] == d2["unique_count"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("== selftest: package ==")
    check_package()
    print("== selftest: scripts ==")
    check_scripts()
    print("== selftest: smoke ==")
    check_smoke()
    failed = [r for r in RESULTS if not r[1]]
    print("== summary: %d/%d passed ==" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
