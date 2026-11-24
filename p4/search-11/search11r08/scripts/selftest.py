#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检脚本（纯标准库，确定性）。

覆盖三类异常与正常路径：
  1. 空输入目录      → 退出码 0，result.json status=insufficient_evidence
  2. 坏 JSON          → 退出码 0，记录 errors/degradations，不崩溃
  3. 缺输入（目录不存在）→ 退出码 0，写合法骨架
  4. 正常 case.json + output_contract → 契约全量回填 + validate VALID
  5. normalize/extract/family 三个分析命令的功能冒烟

用法：python3 scripts/selftest.py
退出码：0 表示全部通过；非 0 表示有失败项（打印 FAIL 明细）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def run(args, cwd=None):
    proc = subprocess.run([sys.executable, MAIN] + args,
                          capture_output=True, text=True, cwd=cwd)
    return proc


def check(name, cond, detail=""):
    if cond:
        print("PASS  %s" % name)
        return True
    print("FAIL  %s%s" % (name, (" — " + detail) if detail else ""))
    return False


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main():
    fails = 0
    base = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # ---- 1. 空输入目录 ----
        empty_in = os.path.join(base, "empty_in")
        empty_out = os.path.join(base, "empty_out")
        empty_work = os.path.join(base, "empty_work")
        os.makedirs(empty_in)
        p = run(["scaffold", "--input", empty_in, "--output", empty_out,
                 "--work", empty_work])
        ok = (p.returncode == 0
              and os.path.exists(os.path.join(empty_out, "result.json")))
        if ok:
            r = read_json(os.path.join(empty_out, "result.json"))
            ok = r.get("status") == "insufficient_evidence" and r.get("case_id")
        fails += 0 if check("empty input dir", ok, p.stderr.strip()) else 1

        # ---- 2. 坏 JSON ----
        bad_in = os.path.join(base, "bad_in")
        bad_out = os.path.join(base, "bad_out")
        bad_work = os.path.join(base, "bad_work")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json")
        p = run(["scaffold", "--input", bad_in, "--output", bad_out,
                 "--work", bad_work])
        ok = p.returncode == 0 and os.path.exists(os.path.join(bad_out, "result.json"))
        if ok:
            r = read_json(os.path.join(bad_out, "result.json"))
            ok = any(e.get("item") == "case_json_unreadable" for e in r.get("errors", []))
        fails += 0 if check("bad JSON degradation", ok, p.stderr.strip()) else 1

        # ---- 3. 缺输入（目录不存在）----
        missing_in = os.path.join(base, "does_not_exist")
        miss_out = os.path.join(base, "miss_out")
        miss_work = os.path.join(base, "miss_work")
        p = run(["scaffold", "--input", missing_in, "--output", miss_out,
                 "--work", miss_work])
        ok = p.returncode == 0 and os.path.exists(os.path.join(miss_out, "result.json"))
        if ok:
            r = read_json(os.path.join(miss_out, "result.json"))
            ok = r.get("status") in ("insufficient_evidence", "partial")
        fails += 0 if check("missing input dir", ok, p.stderr.strip()) else 1

        # ---- 4. 正常 case.json + output_contract ----
        norm_in = os.path.join(base, "norm_in")
        norm_out = os.path.join(base, "norm_out")
        norm_work = os.path.join(base, "norm_work")
        os.makedirs(norm_in)
        contract = {"target_patents": [], "required_answer": None,
                    "extra_key": {"a": 1}}
        case = {"case_id": "case-selftest", "output_contract": contract,
                "task": "locate patent US 10,123,456 B2"}
        with open(os.path.join(norm_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        p = run(["scaffold", "--input", norm_in, "--output", norm_out,
                 "--work", norm_work])
        ok = p.returncode == 0
        if ok:
            r = read_json(os.path.join(norm_out, "result.json"))
            ok = (r.get("case_id") == "case-selftest"
                  and all(k in r for k in contract)
                  and r.get("status") == "partial")
        fails += 0 if check("contract backfill", ok, p.stderr.strip()) else 1

        # ---- 5. normalize / extract / family 冒烟 ----
        p = run(["normalize", "--text", "US 10,123,456 B2"])
        ok = p.returncode == 0
        if ok:
            out = json.loads(p.stdout)
            ok = (out[0]["number_type"] == "publication"
                  and out[0]["normalized"] == "US10123456B2")
        fails += 0 if check("normalize US publication", ok, p.stderr.strip()) else 1

        p = run(["normalize", "--text", "62/123,456"])
        ok = p.returncode == 0
        if ok:
            out = json.loads(p.stdout)
            ok = out[0]["number_type"] == "priority"
        fails += 0 if check("normalize US provisional", ok, p.stderr.strip()) else 1

        p = run(["extract", "--text",
                 "See US 10,123,456 B2 and WO 2020/123456 A1; filed 16/123,456."])
        ok = p.returncode == 0
        if ok:
            out = json.loads(p.stdout)
            ok = len(out) >= 2
        fails += 0 if check("extract numbers", ok, p.stderr.strip()) else 1

        recfile = os.path.join(base, "records.json")
        with open(recfile, "w", encoding="utf-8") as fh:
            json.dump([
                {"record_id": "r1", "publication_number": "US10123456B2",
                 "application_number": "US16123456",
                 "priority_number": "US62123456"},
                {"record_id": "r2", "publication_number": "WO2020123456A1",
                 "application_number": "PCTUS2020012345",
                 "priority_number": "US62123456"},
            ], fh)
        p = run(["family", "--file", recfile])
        ok = p.returncode == 0
        if ok:
            out = json.loads(p.stdout)
            ok = (len(out["families"]) == 1
                  and len(out["families"][0]["members"]) == 2)
        fails += 0 if check("family grouping", ok, p.stderr.strip()) else 1

        # ---- 6. finalize + validate 冒烟 ----
        p = run(["finalize", "--input", norm_in, "--output", norm_out,
                 "--work", norm_work])
        ok = p.returncode == 0 and os.path.exists(os.path.join(norm_out, "report.md"))
        fails += 0 if check("finalize", ok, p.stderr.strip()) else 1
        p = run(["validate", "--output", norm_out])
        ok = p.returncode == 0 and "VALID" in p.stdout
        fails += 0 if check("validate", ok, p.stdout.strip()) else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("")
    if fails:
        print("selftest: %d FAILED" % fails)
        return 1
    print("selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
