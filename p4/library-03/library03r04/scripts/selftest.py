#!/usr/bin/env python3
"""selftest.py — sci-library-organizer 自检脚本（仅标准库）。

逐条执行 SKILL.md 第 5 节的可靠性检查：接口、空目录、缺目录、坏 JSON、
幂等性、result.json 结构。全部通过返回 0，任一失败返回 1 并打印原因。

运行：
    python3 -B scripts/selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402

MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail else ""))
    if not cond:
        FAILURES.append(name)


def run_main(args, cwd=None):
    return subprocess.run([PY, "-B", MAIN] + args, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)


def load_result(output_dir):
    with open(os.path.join(output_dir, "result.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items()
                if k not in ("change_log", "runtime")}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def make_input(root, with_case=True, bad_json=False):
    os.makedirs(root, exist_ok=True)
    if with_case:
        if bad_json:
            with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
                f.write("{ this is not json")
        else:
            with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
                json.dump({"case_id": "case-selfcheck", "output_contract": {}}, f)
    bib = """@article{doe2024,
      author  = {Jane Doe and John Smith},
      title   = {Robust Materials Informatics for Catalyst Design},
      journal = {Journal of Example Chemistry},
      year    = {2024},
      doi     = {10.1234/example.2024.001},
      keywords = {catalysis, materials science}
    }
    @inproceedings{smith2023,
      author = {John Smith},
      title  = {Efficient Neural Networks for Climate Data},
      booktitle = {Proceedings of the Example Conference},
      year = {2023}
    }
    """
    with open(os.path.join(root, "refs.bib"), "w", encoding="utf-8") as f:
        f.write(bib)
    txt = ("Robust Materials Informatics for Catalyst Design\n"
           "Jane Doe, John Smith\n"
           "doi:10.1234/example.2024.001  arXiv:2401.12345\n"
           "Journal of Example Chemistry, 2024\n")
    with open(os.path.join(root, "paper.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    with open(os.path.join(root, "paper_copy.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    with open(os.path.join(root, "data.csv"), "w", encoding="utf-8") as f:
        f.write("id,value\n1,0.1\n2,0.2\n")
    with open(os.path.join(root, "notes.md"), "w", encoding="utf-8") as f:
        f.write("# Lab notes\nscanned figures, no metadata\n")
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
        f.write("# Task\norganize materials\n")


def test_help():
    r = run_main(["--help"])
    check("--help 退出码 0", r.returncode == 0)
    check("--help 说明完整", "--input" in r.stdout and "--apply" in r.stdout)


def test_empty_dir():
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, "input")
        out = os.path.join(t, "output")
        work = os.path.join(t, "work")
        os.makedirs(inp)
        r = run_main(["--input", inp, "--output", out, "--work", work])
        check("空目录退出码 0", r.returncode == 0)
        res = load_result(out)
        check("空目录 status=insufficient_evidence",
              res.get("status") == "insufficient_evidence")


def test_missing_dir():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "output")
        work = os.path.join(t, "work")
        r = run_main(["--input", os.path.join(t, "no_such"), "--output", out, "--work", work])
        check("缺输入目录退出码 0", r.returncode == 0)
        res = load_result(out)
        check("缺输入目录 status=input_error", res.get("status") == "input_error")


def test_bad_json():
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, "input")
        out = os.path.join(t, "output")
        work = os.path.join(t, "work")
        make_input(inp, bad_json=True)
        r = run_main(["--input", inp, "--output", out, "--work", work])
        check("坏 case.json 退出码 0", r.returncode == 0)
        res = load_result(out)
        check("坏 case.json 记录 degradations",
              any("case.json" in d for d in res.get("degradations", [])))
        check("坏 case.json 仍产出合法结果", res.get("status") in
              ("ok", "partial", "insufficient_evidence", "input_error"))


def test_idempotency():
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, "input")
        make_input(inp)
        runs = []
        for i in range(2):
            out = os.path.join(t, "output%d" % i)
            work = os.path.join(t, "work%d" % i)
            r = run_main(["--input", inp, "--output", out, "--work", work, "--apply"])
            check("第%d次运行退出码 0" % (i + 1), r.returncode == 0)
            runs.append(load_result(out))
        a, b = (strip_volatile(x) for x in runs)
        check("两次运行 catalog/operations 一致（去时间戳）", a == b)
        check("重复文件被识别为 duplicate",
              any(entry.get("duplicate_of") for entry in runs[0]["library"]["catalog"].values()))
        check("版本组/去重可复核", "versions" in runs[0]["library"])


def test_result_structure():
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, "input")
        out = os.path.join(t, "output")
        work = os.path.join(t, "work")
        make_input(inp)
        r = run_main(["--input", inp, "--output", out, "--work", work, "--apply"])
        res = load_result(out)
        for key in ("case_id", "status", "library", "evidence", "operations",
                    "failures", "sources", "uncertainty", "change_log",
                    "limitations", "pending_human", "errors", "degradations", "runtime"):
            check("result.json 含 %s" % key, key in res)
        check("case_id 回填", res.get("case_id") == "case-selfcheck")
        check("status 合法", res.get("status") in
              ("ok", "partial", "insufficient_evidence", "input_error"))
        cat = res["library"]["catalog"]
        check("catalog 覆盖全部文件", len(cat) >= 5)
        check("元数据字段存在", all("metadata" in e for e in cat.values()))
        check("目录层级存在", all("category_path" in e for e in cat.values()))
        check("权限与来源存在",
              bool(res["evidence"]["permissions"]) and bool(res["evidence"]["hashes"]))
        check("uncertainty.counts 存在", "counts" in res["uncertainty"])
        check("limitations 非空", isinstance(res.get("limitations"), list)
              and len(res["limitations"]) >= 3)


def test_core_functions():
    check("sanitize_segment 去除非法字符", "/" not in ci.sanitize_segment('a/b:c*d?"e'))
    check("normalize_tag 同义合并", ci.normalize_tag("Neural Networks") == "neural-networks")
    check("DOI 抽取", ci._first_doi("see https://doi.org/10.1234/foo.bar") == "10.1234/foo.bar")
    check("arXiv 抽取", ci._first_arxiv("arXiv:2401.12345v2") == "2401.12345v2")
    check("BibTeX 解析", len(ci.parse_bibtex(
        "@article{k, title={T}, author={A}, year={2020}}")) == 1)


def main():
    print("== sci-library-organizer selftest ==")
    test_help()
    test_core_functions()
    test_empty_dir()
    test_missing_dir()
    test_bad_json()
    test_idempotency()
    test_result_structure()
    print("==")
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
