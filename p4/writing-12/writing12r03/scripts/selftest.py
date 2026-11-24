#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检：确定性、健壮性、契约完整性。

运行方式（在 skill 根目录或任意位置）：
    python3 -B scripts/selftest.py            # 全部检查
    python3 -B scripts/selftest.py determinism
    python3 -B scripts/selftest.py robustness
    python3 -B scripts/selftest.py contract

退出码：全部通过=0；任一硬性检查失败=1。所有检查都在临时目录内进行，
不读写正式产物。仅依赖 Python 标准库。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C          # noqa: E402
import main as M               # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        print("FAIL  %-26s %s" % (name, detail))
    else:
        print("PASS  %-26s %s" % (name, detail))
    return bool(ok)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_input(root, with_case=True, bad_case_json=False):
    os.makedirs(os.path.join(root, "materials"), exist_ok=True)
    if with_case:
        case = {
            "case_id": "selftest-demo",
            "output_contract": {"title": "sample", "sections": [], "abstract": None},
        }
        _write(os.path.join(root, "case.json"),
               json.dumps(case, ensure_ascii=False))
        if bad_case_json:
            _write(os.path.join(root, "case.json"), '{"case_id": "broken",')
    _write(os.path.join(root, "task.md"),
           "# demo task\nWrite a section. 温度 298 K.\n")
    _write(os.path.join(root, "materials", "paper.md"),
           "# Paper\nLine 2: value 298 K.\nLine 3: cite \\cite{smith2024}.\n")


def run_pipeline(input_dir, output_dir, work_dir):
    """等价于 CLI：bootstrap → finalize → selfcheck。返回 result 对象。"""
    M.main(["bootstrap", "--input", input_dir, "--output", output_dir,
            "--work", work_dir])
    # 模拟 agent 写入的确定性内容（不含墙钟时间）
    rp = os.path.join(output_dir, "result.json")
    result, _ = C.read_json(rp)
    result["title"] = "sample"
    result["sections"] = []
    result["abstract"] = None
    result["status"] = "partial"
    result["sources"] = [{
        "locator": "materials/paper.md#L2", "quote": "value 298 K.",
        "usage": "temperature value",
    }]
    result["uncertainty"] = [{"item": "audience not specified", "basis": "task.md"}]
    result["change_log"].append({"step": "draft", "note": "deterministic draft"})
    result["artifacts"] = {"main_tex": "main.tex"}
    _write(os.path.join(output_dir, "main.tex"),
           "\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    C.write_json(rp, result)
    M.main(["finalize", "--input", input_dir, "--output", output_dir,
            "--work", work_dir])
    r, _ = C.read_json(rp)
    return r


def test_help():
    try:
        M.main(["bootstrap", "--help"])
        rc = 0
    except SystemExit as exc:  # argparse --help 以 SystemExit(0) 退出
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return check("help_exit_zero", rc == 0, "bootstrap --help 退出码 0")


def test_empty_input():
    d = tempfile.mkdtemp(prefix="skill-empty-")
    try:
        inp, out, work = (os.path.join(d, x) for x in ("in", "out", "work"))
        os.makedirs(inp)
        rc = M.main(["bootstrap", "--input", inp, "--output", out, "--work", work])
        r, _ = C.read_json(os.path.join(out, "result.json"))
        ok = (rc == 0 and isinstance(r, dict)
              and r.get("status") in ("insufficient_evidence", "input_error"))
        return check("empty_input_degrades", ok,
                     "空输入退出码 0 且 status 诚实")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_json():
    d = tempfile.mkdtemp(prefix="skill-badjson-")
    try:
        inp, out, work = (os.path.join(d, x) for x in ("in", "out", "work"))
        make_input(inp, bad_case_json=True)
        rc = M.main(["bootstrap", "--input", inp, "--output", out, "--work", work])
        r, _ = C.read_json(os.path.join(out, "result.json"))
        ok = (rc == 0 and isinstance(r, dict) and r.get("case_id") == "unknown")
        return check("bad_json_tolerated", ok, "坏 JSON 不崩溃，case_id 降级为 unknown")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_contract_and_determinism():
    d = tempfile.mkdtemp(prefix="skill-det-")
    try:
        runs = []
        for i in (1, 2):
            inp = os.path.join(d, "in%d" % i)
            out = os.path.join(d, "out%d" % i)
            work = os.path.join(d, "work%d" % i)
            make_input(inp)
            runs.append(run_pipeline(inp, out, work))
        r = runs[0]
        contract_ok = all(k in r for k in ("title", "sections", "abstract"))
        check("contract_keys_echoed", contract_ok, "output_contract 键原样回填")
        meta_ok = all(k in r for k in
                      ("case_id", "status", "sources", "uncertainty",
                       "pending_human", "change_log", "runtime"))
        check("required_meta_present", meta_ok, "必需元数据字段齐全")
        src_ok = all(isinstance(s, dict) and s.get("locator") and s.get("quote")
                     for s in r.get("sources", []))
        check("sources_have_quote", src_ok, "证据条目含 locator+quote")

        # 确定性：两次运行 result.json 字节一致
        b1 = open(os.path.join(d, "out1", "result.json"), "rb").read()
        b2 = open(os.path.join(d, "out2", "result.json"), "rb").read()
        check("determinism_result_json", b1 == b2,
              "同输入两次 result.json 字节级一致")
        t1 = open(os.path.join(d, "out1", "main.tex"), "rb").read()
        t2 = open(os.path.join(d, "out2", "main.tex"), "rb").read()
        check("determinism_artifact", t1 == t2,
              "同输入两次主产物字节级一致")
        # finalize 会追加一条 finalize 记录；两次运行应得到相同条数
        check("determinism_change_log", len(runs[0]["change_log"]) == len(runs[1]["change_log"]),
              "change_log 长度一致")
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_evidence_quote():
    d = tempfile.mkdtemp(prefix="skill-quote-")
    try:
        make_input(d)
        p = os.path.join(d, "materials", "paper.md")
        text, err = C.quote_file(p, 2, 2)
        ok = (err is None and "value 298 K." in text)
        return check("evidence_quote_verbatim", ok,
                     "quote 返回逐字片段，非补造")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(argv=None):
    C.reconfigure_stdio()
    which = (argv[1] if argv and len(argv) > 1 else "all")
    if which in ("all", "determinism"):
        test_help()
        test_contract_and_determinism()
        test_evidence_quote()
    if which in ("all", "robustness"):
        test_empty_input()
        test_bad_json()
    if which in ("all", "contract"):
        test_contract_and_determinism()
    failed = [r for r in RESULTS if not r["ok"]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
