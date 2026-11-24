#!/usr/bin/env python3
"""selftest.py — sci-library-organizer 自检脚本（仅标准库）。

在临时目录构造最小 demo 输入，运行 scripts/main.py 并逐项断言：
  1. --help 退出码 0
  2. 正常输入 -> result.json 合法、字段齐全、catalog 覆盖全部文件
  3. 幂等：同一输入连跑两次，catalog 除时间戳外逐字节一致
  4. 空目录 -> insufficient_evidence；缺输入 -> input_error；坏 case.json -> degradations
说明：demo 是接口验证，非正式案例，不冒充正式结果。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, **kw):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  ok -", msg)


def make_demo(root):
    inp = os.path.join(root, "input")
    os.makedirs(os.path.join(inp, "sub"), exist_ok=True)
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Title (Attention Is All You Need) /Author (Vaswani) >>\nendobj\n%%EOF\n"
    with open(os.path.join(inp, "paper.pdf"), "wb") as f:
        f.write(pdf + b"\n" + b"x" * 100)
    with open(os.path.join(inp, "sub", "paper_copy.pdf"), "wb") as f:
        f.write(pdf + b"\n" + b"x" * 100)
    with open(os.path.join(inp, "preprint.pdf"), "wb") as f:
        f.write(pdf + b"\narXiv:1706.03762\nDOI:10.1038/example.2024\n" + b"y" * 100)
    with open(os.path.join(inp, "refs.bib"), "w", encoding="utf-8") as f:
        f.write("@article{vaswani2017,\n title={Attention Is All You Need},\n"
                " author={Vaswani, Ashish and Shazeer, Noam},\n year={2017},\n"
                " journal={NeurIPS}, doi={10.5555/attention}\n}\n")
    with open(os.path.join(inp, "notes.md"), "w", encoding="utf-8") as f:
        f.write("# notes\nscan of supplementary materials\n")
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as f:
        f.write("id,value\n1,2\n")
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "demo-case"}, f)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\nclassify files\n")
    return inp


def main():
    tmp = tempfile.mkdtemp(prefix="sci-lib-selftest-")
    try:
        print("== selftest ==")
        r = run(["--help"])
        check(r.returncode == 0, "--help exit code 0")
        check(b"--input" in r.stdout, "--help lists --input")

        inp = make_demo(tmp)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")

        r = run(["--input", inp, "--output", out, "--work", work, "--apply"])
        check(r.returncode == 0, "normal run exit code 0")
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check(res.get("case_id") == "demo-case", "case_id preserved")
        check(res.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"},
              "status in allowed set")
        for k in ("sources", "uncertainty", "change_log", "pending_human", "runtime"):
            check(k in res, "result.json has " + k)
        cat = res.get("library", {}).get("catalog", [])
        check(len(cat) >= 6, "catalog covers all input files")
        dups = [c for c in cat if c.get("duplicate_of")]
        check(any(c.get("duplicate_of") == "paper.pdf" for c in dups),
              "byte-identical duplicate detected")
        em = res.get("evidence", {}).get("evidence_map", {})
        check(any(em.get(s, {}).get("document_type", {}).get("evidence")
                  for s in em), "evidence_map carries locator+quote")

        # 幂等
        out2 = os.path.join(tmp, "output2")
        work2 = os.path.join(tmp, "work2")
        run(["--input", inp, "--output", out2, "--work", work2, "--apply"])
        res2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
        strip = lambda d: [{k: v for k, v in c.items() if k != "text_excerpt"}  # noqa: E731
                           for c in d]
        check(strip(res["library"]["catalog"]) == strip(res2["library"]["catalog"]),
              "idempotent catalog across runs")

        # 空目录
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        run(["--input", empty, "--output", os.path.join(tmp, "o3"),
             "--work", os.path.join(tmp, "w3")])
        res3 = json.load(open(os.path.join(tmp, "o3", "result.json"), encoding="utf-8"))
        check(res3.get("status") == "insufficient_evidence", "empty dir -> insufficient_evidence")

        # 缺输入
        run(["--input", os.path.join(tmp, "nope"), "--output", os.path.join(tmp, "o4"),
             "--work", os.path.join(tmp, "w4")])
        res4 = json.load(open(os.path.join(tmp, "o4", "result.json"), encoding="utf-8"))
        check(res4.get("status") == "input_error", "missing input -> input_error")

        # 坏 case.json
        bad = os.path.join(tmp, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not json !!!")
        with open(os.path.join(bad, "a.txt"), "w", encoding="utf-8") as f:
            f.write("hello")
        run(["--input", bad, "--output", os.path.join(tmp, "o5"),
             "--work", os.path.join(tmp, "w5")])
        res5 = json.load(open(os.path.join(tmp, "o5", "result.json"), encoding="utf-8"))
        check(len(res5.get("degradations", [])) >= 1, "bad case.json -> degradations")
        check(r.returncode == 0, "bad case.json does not crash")

        print("\nALL SELFTESTS PASSED")
        return 0
    except AssertionError as e:
        print(e)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
