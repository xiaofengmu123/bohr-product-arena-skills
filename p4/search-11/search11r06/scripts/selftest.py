#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检（纯标准库）。

覆盖评分维度中的"可靠体验 / 异常处理 / 运行稳定性"：
  A. --help 退出码 0；
  B. 空输入目录 → status=insufficient_evidence，退出码 0；
  C. 坏 JSON（case.json 损坏）→ 记录 errors/degradations，退出码 0；
  D. 缺输入目录 → 退出码 0，产物合法；
  E. scaffold → normalize → family → plan → evidence → finalize → validate 全链路；
  F. validate 必须打印 VALID（对自检生成的合法产物）。

用法：python3 scripts/selftest.py [--main scripts/main.py]
退出码 0 表示全部通过；任一失败退出码 1。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
        return True
    print("  FAIL  %s%s" % (name, (" — " + detail) if detail else ""))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="scripts/main.py")
    args = ap.parse_args()
    main_script = os.path.abspath(args.main)
    if not os.path.exists(main_script):
        print("FAIL: main script not found: %s" % main_script)
        sys.exit(1)

    print("== 专利检索 Skill 自检 ==")
    all_ok = True

    # A. --help
    r = run([sys.executable, main_script, "--help"])
    all_ok &= check("A1 --help 退出码 0", r.returncode == 0,
                    "rc=%d\n%s" % (r.returncode, r.stderr.strip()))
    all_ok &= check("A2 --help 含子命令", "scaffold" in (r.stdout or ""))

    tmp = tempfile.mkdtemp(prefix="patentskill_")

    # B. 空输入目录
    empty_in = os.path.join(tmp, "empty_in")
    empty_out = os.path.join(tmp, "empty_out")
    empty_work = os.path.join(tmp, "empty_work")
    os.makedirs(empty_in)
    r = run([sys.executable, main_script, "scaffold",
             "--input", empty_in, "--output", empty_out, "--work", empty_work])
    all_ok &= check("B1 空输入 scaffold 退出码 0", r.returncode == 0,
                    "rc=%d\n%s" % (r.returncode, r.stderr.strip()))
    if r.returncode == 0:
        res = json.load(open(os.path.join(empty_out, "result.json"), encoding="utf-8"))
        all_ok &= check("B2 空输入 status=insufficient_evidence",
                        res.get("status") == "insufficient_evidence", str(res.get("status")))

    # C. 坏 JSON
    bad_in = os.path.join(tmp, "bad_in")
    bad_out = os.path.join(tmp, "bad_out")
    bad_work = os.path.join(tmp, "bad_work")
    os.makedirs(bad_in)
    with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    with open(os.path.join(bad_in, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\n")
    r = run([sys.executable, main_script, "scaffold",
             "--input", bad_in, "--output", bad_out, "--work", bad_work])
    all_ok &= check("C1 坏 JSON scaffold 退出码 0", r.returncode == 0,
                    "rc=%d\n%s" % (r.returncode, r.stderr.strip()))
    if r.returncode == 0:
        res = json.load(open(os.path.join(bad_out, "result.json"), encoding="utf-8"))
        all_ok &= check("C2 坏 JSON 记录 errors",
                        any(e.get("item") == "case_json_unreadable" for e in res.get("errors", [])),
                        json.dumps(res.get("errors"), ensure_ascii=False))
        all_ok &= check("C3 坏 JSON 记录 degradations",
                        any(d.get("item") == "case_json_fallback"
                            for d in res.get("degradations", [])),
                        json.dumps(res.get("degradations"), ensure_ascii=False))

    # D. 缺输入目录
    miss_out = os.path.join(tmp, "miss_out")
    miss_work = os.path.join(tmp, "miss_work")
    r = run([sys.executable, main_script, "scaffold",
             "--input", os.path.join(tmp, "no_such_dir"),
             "--output", miss_out, "--work", miss_work])
    all_ok &= check("D1 缺输入目录退出码 0", r.returncode == 0,
                    "rc=%d\n%s" % (r.returncode, r.stderr.strip()))

    # E. 全链路
    demo_in = os.path.join(tmp, "demo_in")
    demo_out = os.path.join(tmp, "demo_out")
    demo_work = os.path.join(tmp, "demo_work")
    os.makedirs(demo_in)
    contract = {
        "output_contract": {
            "case_id": "string",
            "target_patents": "array",
            "verdict": "string",
        }
    }
    with open(os.path.join(demo_in, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-demo", **contract}, fh, ensure_ascii=False)
    with open(os.path.join(demo_in, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 示例任务\n给定 US 12,345,678 B2 与其申请号 US 16/123,456。\n")

    base = [sys.executable, main_script,
            "--input", demo_in, "--output", demo_out, "--work", demo_work]
    r = run(base + ["scaffold"])
    all_ok &= check("E1 scaffold 退出码 0", r.returncode == 0, r.stderr.strip())

    r = run(base + ["plan", "--object-type", "number_lookup",
                    "--question", "定位并核验 US 12,345,678 B2"])
    all_ok &= check("E2 plan 退出码 0", r.returncode == 0, r.stderr.strip())

    r = run(base + ["normalize", "--text", "US 12,345,678 B2"])
    all_ok &= check("E3 normalize 退出码 0", r.returncode == 0, r.stderr.strip())
    if r.returncode == 0:
        out = json.loads(r.stdout)
        all_ok &= check("E4 normalize 得到 US12345678B2",
                        out and out[0].get("normalized") == "US12345678B2",
                        json.dumps(out, ensure_ascii=False))

    r = run(base + ["extract", "--file", os.path.join(demo_in, "task.md")])
    all_ok &= check("E5 extract 退出码 0", r.returncode == 0, r.stderr.strip())
    if r.returncode == 0:
        out = json.loads(r.stdout)
        all_ok &= check("E6 extract 命中 2 个号码", len(out) == 2,
                        json.dumps(out, ensure_ascii=False))

    records = [
        {"record_id": "r1", "application_number": "US 16/123,456",
         "priority_numbers": ["US 62/000,000"]},
        {"record_id": "r2", "application_number": "US 16/123,456",
         "priority_numbers": []},
        {"record_id": "r3", "application_number": "EP 20180123456",
         "priority_numbers": ["US 62/000,000"]},
    ]
    rec_file = os.path.join(demo_work, "records.json")
    with open(rec_file, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False)
    r = run(base + ["family", "--file", rec_file])
    all_ok &= check("E7 family 退出码 0", r.returncode == 0, r.stderr.strip())
    if r.returncode == 0:
        out = json.loads(r.stdout)
        fams = out.get("families", [])
        all_ok &= check("E8 family 归并为 1 族（传递链接）", len(fams) == 1,
                        json.dumps(fams, ensure_ascii=False))
        all_ok &= check("E9 family 口径 observed + exhaustive=false",
                        out.get("family_type") == "observed"
                        and out.get("exhaustive") is False)

    # 注入证据
    res = json.load(open(os.path.join(demo_out, "result.json"), encoding="utf-8"))
    res["evidence"] = [{
        "id": "ev-001", "locator": "materials/patent.txt#p3",
        "quote": "The invention relates to ...", "claim": "保护主题",
    }]
    res["families"] = [{
        "family_id": "fam-01", "member_ids": ["r1", "r2", "r3"],
        "family_type": "observed", "exhaustive": False,
        "basis": "shared_application_or_priority_number", "evidence": ["ev-001"],
    }]
    with open(os.path.join(demo_out, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False)

    r = run(base + ["evidence"])
    all_ok &= check("E10 evidence 退出码 0", r.returncode == 0, r.stderr.strip())

    r = run(base + ["finalize", "--status", "partial"])
    all_ok &= check("E11 finalize 退出码 0", r.returncode == 0, r.stderr.strip())
    if r.returncode == 0:
        all_ok &= check("E12 report.md 生成",
                        os.path.exists(os.path.join(demo_out, "report.md")))

    r = run(base + ["validate"])
    all_ok &= check("E13 validate 退出码 0", r.returncode == 0, r.stderr.strip())
    all_ok &= check("E14 validate 打印 VALID", "VALID" in (r.stdout or ""),
                    r.stdout.strip() + r.stderr.strip())

    # F. 校验中间产物
    plan_path = os.path.join(demo_work, "plan.json")
    ev_path = os.path.join(demo_work, "evidence_map.json")
    all_ok &= check("F1 plan.json 存在", os.path.exists(plan_path))
    all_ok &= check("F2 evidence_map.json 存在", os.path.exists(ev_path))
    if os.path.exists(plan_path):
        plan = json.load(open(plan_path, encoding="utf-8"))
        all_ok &= check("F3 plan 预算护栏写入",
                        plan.get("budget", {}).get("total_seconds") == 900
                        and plan.get("budget", {}).get("total_model_requests") == 64)

    print("")
    if all_ok:
        print("ALL CHECKS PASSED")
        sys.exit(0)
    print("SOME CHECKS FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
