#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — Skill 提交前自检脚本（无需真实 TeX 引擎即可运行）。

验证内容：
  1. 空输入目录 → bootstrap 退出码 0，写合法 result.json（insufficient_evidence）；
  2. 坏 JSON / 坏编码 → 容错读入并记录 error，不崩溃；
  3. output_contract 键原样回填；
  4. evidence.py 的 quote/verify/verify-map 逐字校验闭环；
  5. latex_tools.py 的 classify-log 能分类合成日志、缺引擎时优雅降级；
  6. result.json 永远是合法 JSON。

运行：python3 scripts/selftest.py [--work DIR]。默认在临时目录下跑，退出码 0 = 全部通过。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
EVIDENCE = os.path.join(HERE, "evidence.py")
LATEX = os.path.join(HERE, "latex_tools.py")


def run(cmd, **kw):
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ((" - " + detail) if detail else ""))
    return bool(cond)


def main():
    ap = argparse.ArgumentParser(description="Skill self-test")
    ap.add_argument("--work", default=None, help="working directory (temp if omitted)")
    args = ap.parse_args()
    root = os.path.abspath(args.work) if args.work else tempfile.mkdtemp(prefix="selftest_")
    os.makedirs(root, exist_ok=True)
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")

    results = []

    # 1. Empty input
    os.makedirs(inp, exist_ok=True)
    r = run([sys.executable, MAIN, "bootstrap", "--input", inp,
             "--output", out, "--work", work])
    results.append(check("empty-input bootstrap exit 0", r.returncode == 0, r.stderr.strip()))
    res_path = os.path.join(out, "result.json")
    try:
        obj = json.load(open(res_path, encoding="utf-8"))
        results.append(check("empty-input result.json valid & status honest",
                             obj.get("status") == "input_error" or
                             obj.get("status") == "insufficient_evidence",
                             str(obj.get("status"))))
    except Exception as exc:
        results.append(check("empty-input result.json parses", False, str(exc)))

    # 2. selfcheck on the empty result
    r = run([sys.executable, MAIN, "selfcheck", "--input", inp,
             "--output", out, "--work", work])
    results.append(check("selfcheck runs on empty result", r.returncode in (0, 1)))

    # 3. Bad JSON + bad encoding
    bad_in = os.path.join(root, "badinput")
    os.makedirs(bad_in, exist_ok=True)
    with open(os.path.join(bad_in, "case.json"), "wb") as fh:
        fh.write(b'{"case_id": "x", "output_contract": {"title": null},')  # truncated
    with open(os.path.join(bad_in, "materials"), "wb") as fh:
        fh.write(b"\xff\xfe\x00broken\x9c")
    bad_out = os.path.join(root, "badout")
    bad_work = os.path.join(root, "badwork")
    r = run([sys.executable, MAIN, "bootstrap", "--input", bad_in,
             "--output", bad_out, "--work", bad_work])
    results.append(check("bad-json bootstrap exit 0", r.returncode == 0, r.stderr.strip()))
    try:
        obj2 = json.load(open(os.path.join(bad_out, "result.json"), encoding="utf-8"))
        results.append(check("bad-json result still valid & contract echoed",
                             "title" in obj2 and obj2.get("case_id") == "x"))
    except Exception as exc:
        results.append(check("bad-json result parses", False, str(exc)))

    # 4. Evidence quote/verify/verify-map closed loop
    mat_dir = os.path.join(inp, "materials")
    os.makedirs(mat_dir, exist_ok=True)
    paper = os.path.join(mat_dir, "paper.md")
    with open(paper, "w", encoding="utf-8") as fh:
        fh.write("# Paper\n\nThe measured temperature was 300 K.\nLine three.\n")
    r = run([sys.executable, EVIDENCE, "quote", "--file", paper,
             "--start", "3", "--end", "3"])
    q = json.loads(r.stdout)
    quote = q.get("quote", "")
    results.append(check("quote extracts line verbatim",
                         "300 K" in quote, quote.strip()))
    r = run([sys.executable, EVIDENCE, "verify", "--file", paper,
             "--quote", "measured temperature was 300 K."])
    results.append(check("verify confirms verbatim substring",
                         json.loads(r.stdout).get("ok") is True))
    r = run([sys.executable, EVIDENCE, "verify", "--file", paper,
             "--quote", "measured temperature was 299 K."])
    results.append(check("verify rejects non-verbatim quote",
                         json.loads(r.stdout).get("ok") is False))

    # Build a small evidence_map with one good + one bad quote; verify-map downgrades bad.
    emap = os.path.join(work, "evidence_map.json")
    os.makedirs(work, exist_ok=True)
    claims = [
        {"id": "c1", "type": "number", "statement": "T = 300 K",
         "locator": "materials/paper.md#L3", "quote": "300 K", "status": "verified"},
        {"id": "c2", "type": "number", "statement": "T = 299 K",
         "locator": "materials/paper.md#L3", "quote": "299 K", "status": "verified"},
    ]
    json.dump({"schema_version": "1.0", "claims": claims, "conflicts": []},
              open(emap, "w", encoding="utf-8"), ensure_ascii=False)
    r = run([sys.executable, EVIDENCE, "verify-map", "--map", emap, "--root", inp])
    rep = json.loads(r.stdout)
    results.append(check("verify-map downgrades non-verbatim to gap",
                         rep.get("verified") == 1 and rep.get("downgraded") == 1,
                         str(rep)))
    results.append(check("verify-map exit code reflects downgrade", r.returncode == 1))

    # 5. classify-log on a synthetic log
    synlog = os.path.join(work, "syn.log")
    with open(synlog, "w", encoding="utf-8") as fh:
        fh.write("./main.tex:12: ! Undefined control sequence.\n")
        fh.write("./main.tex:20: ! LaTeX Error: File `foo.sty' not found.\n")
        fh.write("LaTeX Warning: Reference `eq:1' on page 2 undefined.\n")
    r = run([sys.executable, LATEX, "classify-log", "--log", synlog, "--main", "main.tex"])
    cls = json.loads(r.stdout)
    cats = {e["category"] for e in cls.get("errors", [])}
    results.append(check("classify-log classifies categories",
                         "undefined_control_sequence" in cats and "missing_package" in cats,
                         str(cats)))
    r = run([sys.executable, LATEX, "refcheck", "--log", synlog])
    rc = json.loads(r.stdout)
    results.append(check("refcheck finds undefined reference",
                         "eq:1" in rc.get("undefined_refs", []), str(rc)))

    # 6. compile degrades gracefully when no engine (no TeX installed assumption)
    dummy = os.path.join(mat_dir, "dummy.tex")
    with open(dummy, "w", encoding="utf-8") as fh:
        fh.write("\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    r = run([sys.executable, LATEX, "compile", "--main", dummy,
             "--outdir", out, "--passes", "1"])
    crep = os.path.join(out, "compile_report.json")
    graceful = False
    if os.path.isfile(crep):
        rep = json.load(open(crep, encoding="utf-8"))
        # Either no engine (honestly reported) or the dummy compiled cleanly.
        graceful = (rep.get("engine_missing") is True) or (rep.get("ok") is True)
    results.append(check("compile degrades or succeeds gracefully",
                         os.path.isfile(crep) and graceful and r.returncode == 0,
                         "graceful=%s" % graceful))

    ok = all(results)
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
