#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检脚本（纯标准库，无网络）。

在临时目录构造最小输入，端到端验证 main.py 的六个子命令：
  --help 退出码 0；scaffold/normalize/extract/family/finalize/validate；
  空目录、坏 JSON、缺输入三种异常各跑一次（退出码 0 且产物合法）；
  确定性：同一输入两次 scaffold 的 canonical 输出逐字节一致。

用法：python3 scripts/selftest.py  （从 Skill 根目录运行）
退出码 0 = 全部通过；非 0 = 存在失败项。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable or "python3"

RESULTS = []


def run(args, cwd=None):
    return subprocess.run(
        [PY, "-B", MAIN] + args,
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (mark, name, detail))


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_demo(root):
    inp = os.path.join(root, "in")
    os.makedirs(os.path.join(inp, "materials"))
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "case_id": "case-demo",
            "task": "demo task",
            "output_contract": {"candidate_set": [], "omissions": []},
        }, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo task\nUS 12,345,678 B2\nWO 2020/123456 A1\nUS 62/123,456\n")
    with open(os.path.join(inp, "materials", "records.json"), "w", encoding="utf-8") as fh:
        json.dump([
            {"record_id": "R1", "priority_number": "US 62/123,456",
             "publication_number": "US 12,345,678 B2"},
            {"record_id": "R2", "priority_number": "US 62/123,456",
             "publication_number": "WO 2020/123456 A1"},
        ], fh, ensure_ascii=False)
    return inp


def main():
    print("== selftest: 专利检索 Skill ==")

    # 1) --help 退出码 0
    p = run(["--help"])
    check("help_exit0", p.returncode == 0, "exit=%d" % p.returncode)

    root = tempfile.mkdtemp(prefix="patskill_")
    demo_in = make_demo(root)

    # 2) 空输入目录
    empty_in = os.path.join(root, "empty_in")
    os.makedirs(empty_in)
    empty_out = os.path.join(root, "empty_out")
    empty_work = os.path.join(root, "empty_work")
    p = run(["scaffold", "--input", empty_in, "--output", empty_out, "--work", empty_work])
    r = read_json(os.path.join(empty_out, "result.json"))
    check("empty_dir_scaffold", p.returncode == 0 and r["status"] == "insufficient_evidence",
          "status=%s exit=%d" % (r["status"], p.returncode))

    # 3) 坏 JSON
    bad_in = os.path.join(root, "bad_in")
    os.makedirs(bad_in)
    with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    bad_out = os.path.join(root, "bad_out")
    bad_work = os.path.join(root, "bad_work")
    p = run(["scaffold", "--input", bad_in, "--output", bad_out, "--work", bad_work])
    r = read_json(os.path.join(bad_out, "result.json"))
    check("bad_json_scaffold", p.returncode == 0 and r["status"] == "partial",
          "status=%s exit=%d" % (r["status"], p.returncode))

    # 4) 缺输入目录
    p = run(["scaffold", "--input", os.path.join(root, "nope"),
             "--output", os.path.join(root, "no_out"),
             "--work", os.path.join(root, "no_work")])
    r = read_json(os.path.join(root, "no_out", "result.json"))
    check("missing_input_scaffold", p.returncode == 0 and r["status"] == "insufficient_evidence",
          "status=%s exit=%d" % (r["status"], p.returncode))

    # 5) 正常 scaffold + 契约回填
    demo_out = os.path.join(root, "demo_out")
    demo_work = os.path.join(root, "demo_work")
    p = run(["scaffold", "--input", demo_in, "--output", demo_out, "--work", demo_work])
    r = read_json(os.path.join(demo_out, "result.json"))
    check("demo_scaffold_contract",
          p.returncode == 0 and r["case_id"] == "case-demo"
          and "candidate_set" in r and "omissions" in r,
          "case_id=%s" % r["case_id"])

    # 6) normalize
    p = run(["normalize", "--text", "US 12,345,678 B2"])
    n = json.loads(p.stdout)[0]
    check("normalize_us_grant",
          n["country"] == "US" and n["number"] == "12345678" and n["number_type"] == "grant",
          "normalized=%s" % n["normalized"])
    p = run(["normalize", "--text", "WO 2020/123456 A1"])
    n = json.loads(p.stdout)[0]
    check("normalize_wo_pub",
          n["country"] == "WO" and n["number_type"] == "publication",
          "normalized=%s" % n["normalized"])
    p = run(["normalize", "--text", "US 62/123,456"])
    n = json.loads(p.stdout)[0]
    check("normalize_us_priority",
          n["number_type"] == "priority", "type=%s" % n["number_type"])

    # 7) extract
    p = run(["extract", "--file", os.path.join(demo_in, "task.md")])
    hits = json.loads(p.stdout)
    check("extract_hits", len(hits) >= 3, "hits=%d" % len(hits))

    # 8) family
    p = run(["family", "--file", os.path.join(demo_in, "materials", "records.json")])
    fam = json.loads(p.stdout)
    check("family_group", fam.get("exhaustive") is False and len(fam["families"]) == 1
          and len(fam["families"][0]["members"]) == 2,
          "families=%d" % len(fam["families"]))

    # 9) validate（对骨架应为 VALID：无 error 级问题）
    p = run(["validate", "--output", demo_out])
    check("validate_valid", "VALID" in p.stdout, p.stdout.strip().splitlines()[-1])

    # 10) finalize 生成 report.md 与 runtime
    p = run(["finalize", "--output", demo_out])
    report = os.path.join(demo_out, "report.md")
    r = read_json(os.path.join(demo_out, "result.json"))
    check("finalize_report_runtime",
          p.returncode == 0 and os.path.exists(report)
          and r["runtime"].get("finished_at") is not None,
          "report=%s" % os.path.exists(report))

    # 11) 确定性：同一输入两次 scaffold，canonical 输出逐字节一致
    out_a = os.path.join(root, "det_a"); out_b = os.path.join(root, "det_b")
    run(["scaffold", "--input", demo_in, "--output", out_a, "--work", os.path.join(root, "w_a")])
    run(["scaffold", "--input", demo_in, "--output", out_b, "--work", os.path.join(root, "w_b")])
    ca = run(["canonical", "--output", out_a]).stdout
    cb = run(["canonical", "--output", out_b]).stdout
    check("deterministic_repeat", ca == cb, "canonical equal=%s" % (ca == cb))

    # 汇总
    fails = [r for r in RESULTS if not r[1]]
    print("\n== selftest 汇总: %d/%d 通过 ==" % (len(RESULTS) - len(fails), len(RESULTS)))
    for name, _ok, detail in fails:
        print("  FAIL: %s %s" % (name, detail))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
