#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — sci-library-organizer 自检脚本（仅标准库）。

覆盖可靠体验与确定性验收点：
  1. --help 退出码 0
  2. 空输入目录 -> status=insufficient_evidence，退出码 0
  3. 缺输入目录 -> status=input_error，退出码 0
  4. 坏 case.json -> degradations 记录 + 回退默认契约，退出码 0
  5. 同输入连跑两次（--freeze-clock）-> result.json 逐字节一致
  6. result.json 合法 JSON，含 case_id + 契约全字段 + status/sources/uncertainty/change_log
  7. --apply 幂等：第二次运行 already_present，不重复复制、不覆盖

用法:
    python3 -B scripts/selftest.py [--root <skill根目录>] [--tmp <临时目录>]
退出码：全部通过为 0，任一失败为 1。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

REQUIRED = ("status", "sources", "uncertainty", "change_log")


def run_main(input_dir, output_dir, work_dir, extra=()):
    cmd = [sys.executable, "-B", MAIN, "--input", input_dir,
           "--output", output_dir, "--work", work_dir] + list(extra)
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    result = None
    rp = os.path.join(output_dir, "result.json")
    if os.path.isfile(rp):
        with open(rp, "r", encoding="utf-8") as f:
            try:
                result = json.load(f)
            except (ValueError, TypeError):
                result = None
    return cp.returncode, result, cp.stderr.decode("utf-8", "replace")


def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def make_case(root, case_text=None, files=None):
    os.makedirs(root, exist_ok=True)
    if case_text is None:
        case_text = json.dumps({"case_id": "self-case-001", "task": "整理资料"})
    write(os.path.join(root, "case.json"), case_text)
    write(os.path.join(root, "task.md"), "# 整理这批资料\n")
    for rel, content in (files or {}).items():
        write(os.path.join(root, rel.replace("/", os.sep)), content)
    return root


def check(cond, name, detail=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  " + detail) if detail else ""))
    return bool(cond)


def main():
    ap = argparse.ArgumentParser(description="sci-library-organizer 自检")
    ap.add_argument("--root", default=os.path.dirname(HERE))
    ap.add_argument("--tmp", default=None)
    args = ap.parse_args()

    base = args.tmp or tempfile.mkdtemp(prefix="scio-selftest-")
    os.makedirs(base, exist_ok=True)
    results = []

    cp = subprocess.run([sys.executable, "-B", MAIN, "--help"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    results.append(("--help 退出码 0", check(cp.returncode == 0, "--help 退出码 0")))

    empty = os.path.join(base, "empty")
    make_case(empty)
    rc, r, _ = run_main(empty, os.path.join(base, "out-empty"), os.path.join(base, "work-empty"))
    results.append(("空目录 insufficient_evidence",
                    check(rc == 0 and r and r.get("status") == "insufficient_evidence",
                          "rc=%s status=%s" % (rc, r and r.get("status")))))

    missing = os.path.join(base, "no-such-input")
    rc, r, _ = run_main(missing, os.path.join(base, "out-missing"), os.path.join(base, "work-missing"))
    results.append(("缺输入 input_error",
                    check(rc == 0 and r and r.get("status") == "input_error",
                          "rc=%s status=%s" % (rc, r and r.get("status")))))

    bad = os.path.join(base, "badjson")
    make_case(bad, case_text="{ this is not json", files={"paper.md": "# title\nabstract text"})
    rc, r, _ = run_main(bad, os.path.join(base, "out-bad"), os.path.join(base, "work-bad"))
    results.append(("坏 JSON 降级", check(rc == 0 and bool(r and r.get("degradations")),
                                          "rc=%s degradations=%s" % (rc, bool(r and r.get("degradations"))))))

    det = os.path.join(base, "det")
    make_case(det, files={
        "paper.pdf": "%PDF-1.4\nnot a real pdf but deterministic",
        "refs.bib": "@article{Smith2020,\n title={A Study},\n author={Smith, J and Lee, K},\n year={2020},\n journal={J. Sci.},\n doi={10.1000/xyz123}\n}",
        "data/readme.txt": "dataset description",
        "sub/paper_copy.pdf": "%PDF-1.4\nnot a real pdf but deterministic",
    })
    out_a = os.path.join(base, "out-det-a")
    out_b = os.path.join(base, "out-det-b")
    run_main(det, out_a, os.path.join(base, "work-det-a"), ("--freeze-clock", "--apply"))
    run_main(det, out_b, os.path.join(base, "work-det-b"), ("--freeze-clock", "--apply"))
    ra = open(os.path.join(out_a, "result.json"), "rb").read()
    rb = open(os.path.join(out_b, "result.json"), "rb").read()
    results.append(("--freeze-clock 逐字节一致", check(ra == rb, "len_a=%d len_b=%d" % (len(ra), len(rb)))))

    with open(os.path.join(out_a, "result.json"), "r", encoding="utf-8") as f:
        rd = json.load(f)
    contract_ok = all(k in rd for k in ("library", "evidence", "operations", "failures"))
    required_ok = all(k in rd for k in REQUIRED)
    results.append(("result.json 契约完整",
                    check(contract_ok and required_ok and rd.get("case_id") == "self-case-001",
                          "case_id=%s contract=%s extras=%s" % (rd.get("case_id"), contract_ok, required_ok))))

    run_main(det, out_a, os.path.join(base, "work-det-a2"), ("--freeze-clock", "--apply"))
    with open(os.path.join(out_a, "result.json"), "r", encoding="utf-8") as f:
        rd2 = json.load(f)
    copied2 = [o for o in rd2.get("operations", []) if o.get("action") == "copied"]
    results.append(("--apply 幂等（二次运行无新复制）", check(not copied2, "second-run copied=%d" % len(copied2))))

    print("-" * 50)
    passed = sum(1 for _, ok in results if ok)
    print("自检结果：%d/%d 通过" % (passed, len(results)))
    for name, ok in results:
        if not ok:
            print("  未通过：%s" % name)
    print("临时目录：%s" % base)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
