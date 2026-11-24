#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检（纯标准库，可在任意机器复跑）。

覆盖六类健壮性场景与三类评分维度，逐条输出 PASS/FAIL，退出码 0 表示全过：
  1. --help 退出码 0
  2. 空输入目录      -> insufficient_evidence，合法 result.json
  3. 坏 JSON case.json -> input_error 降级，errors 记录，进程不崩
  4. 缺输入目录      -> 合法结果，进程不崩
  5. 正常 case.json（含 output_contract）-> 契约字段全部镜像 + contract_coverage
  6. normalize / extract / family / finalize / validate 全链路

用法：
  python3 scripts/selftest.py            # 使用包内 scripts/main.py
  python3 scripts/selftest.py --main /path/to/main.py
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=None):
    proc = subprocess.run([sys.executable, "-B"] + args, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=120)
    return proc


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (status, name, detail))
    return bool(cond)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default=DEFAULT_MAIN)
    args = ap.parse_args()
    main_py = args.main
    results = []

    # 1. --help
    p = run([main_py, "--help"])
    results.append(check("help_exit0", p.returncode == 0, "exit=%d" % p.returncode))

    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        # 2. 空输入目录
        empty_in = os.path.join(tmp, "empty_in")
        out2 = os.path.join(tmp, "out_empty")
        work2 = os.path.join(tmp, "work_empty")
        os.makedirs(empty_in, exist_ok=True)
        p = run([main_py, "scaffold", "--input", empty_in, "--output", out2, "--work", work2])
        r2 = read_json(os.path.join(out2, "result.json")) if p.returncode == 0 else None
        results.append(check("empty_input", p.returncode == 0 and r2 is not None
                             and r2.get("status") == "insufficient_evidence",
                             "status=%s" % (r2 or {}).get("status")))

        # 3. 坏 JSON case.json
        bad_in = os.path.join(tmp, "bad_in")
        out3 = os.path.join(tmp, "out_bad")
        work3 = os.path.join(tmp, "work_bad")
        write(os.path.join(bad_in, "case.json"), "{ not valid json !!!")
        write(os.path.join(bad_in, "task.md"), "# task")
        p = run([main_py, "scaffold", "--input", bad_in, "--output", out3, "--work", work3])
        r3 = read_json(os.path.join(out3, "result.json")) if p.returncode == 0 else None
        ok3 = (p.returncode == 0 and r3 is not None
               and any(e.get("item") == "case_json_unreadable" for e in (r3.get("errors") or []))
               and r3.get("case_id") == "unknown")
        results.append(check("bad_json", ok3, "errors=%d" % len((r3 or {}).get("errors", []))))

        # 4. 缺输入目录
        missing_in = os.path.join(tmp, "no_such_input")
        out4 = os.path.join(tmp, "out_missing")
        work4 = os.path.join(tmp, "work_missing")
        p = run([main_py, "scaffold", "--input", missing_in, "--output", out4, "--work", work4])
        r4 = read_json(os.path.join(out4, "result.json")) if p.returncode == 0 else None
        results.append(check("missing_input", p.returncode == 0 and r4 is not None
                             and r4.get("status") == "insufficient_evidence",
                             "status=%s" % (r4 or {}).get("status")))

        # 5. 正常 case.json + output_contract（形状式 + schema 式混合）
        normal_in = os.path.join(tmp, "normal_in")
        out5 = os.path.join(tmp, "out_normal")
        work5 = os.path.join(tmp, "work_normal")
        contract = {
            "case_id": "",
            "target_patents": {"count": 0, "records": []},
            "query": {"type": "string"},
            "ranked_results": {"type": "array", "items": {"type": "object"}},
        }
        case = {"case_id": "case-selftest", "output_contract": contract,
                "task": "demo"}
        write(os.path.join(normal_in, "case.json"),
              json.dumps(case, ensure_ascii=False))
        write(os.path.join(normal_in, "task.md"), "# task\nUS 12,345,678 B2")
        p = run([main_py, "scaffold", "--input", normal_in, "--output", out5, "--work", work5])
        r5 = read_json(os.path.join(out5, "result.json")) if p.returncode == 0 else None
        cov = (r5 or {}).get("contract_coverage") or {}
        has_target = isinstance((r5 or {}).get("target_patents"), dict)
        has_query = "query" in (r5 or {})
        has_ranked = (r5 or {}).get("ranked_results") == []
        ok5 = (p.returncode == 0 and r5 is not None and has_target and has_query
               and has_ranked and cov.get("total_fields", 0) >= 4
               and (r5 or {}).get("case_id") == "case-selftest")
        results.append(check("contract_mirror", ok5,
                             "fields=%d filled=%d" % (cov.get("total_fields", 0),
                                                      cov.get("filled", 0))))

        # 6. normalize
        p = run([main_py, "normalize", "--text", "US 12,345,678 B2"])
        norm = json.loads(p.stdout.decode("utf-8")) if p.returncode == 0 else []
        n0 = norm[0] if norm else {}
        results.append(check("normalize", p.returncode == 0
                             and n0.get("normalized") == "US12345678B2"
                             and n0.get("number_type") == "grant",
                             "norm=%s type=%s" % (n0.get("normalized"), n0.get("number_type"))))

        # 7. extract
        write(os.path.join(tmp, "text.md"), "See WO 2020/123456 A1 and EP19123456.7.")
        p = run([main_py, "extract", "--file", os.path.join(tmp, "text.md")])
        hits = json.loads(p.stdout.decode("utf-8")) if p.returncode == 0 else []
        results.append(check("extract", p.returncode == 0 and len(hits) >= 2,
                             "hits=%d" % len(hits)))

        # 8. family
        records = [
            {"record_id": "r1", "application_number": "US16/123456",
             "priority_number": "US201862123456P"},
            {"record_id": "r2", "publication_number_normalized": "US10123456B2",
             "priority_number": "US201862123456P"},
            {"record_id": "r3", "publication_number_normalized": "EP1234567A1"},
        ]
        write(os.path.join(tmp, "records.json"), json.dumps(records))
        p = run([main_py, "family", "--file", os.path.join(tmp, "records.json")])
        fam = json.loads(p.stdout.decode("utf-8")) if p.returncode == 0 else {}
        fams = fam.get("families", [])
        simple = [f for f in fams if f.get("family_type") == "simple"]
        results.append(check("family", p.returncode == 0 and len(simple) == 1
                             and set(simple[0].get("members", [])) == {"r1", "r2"},
                             "families=%d simple=%d" % (len(fams), len(simple))))

        # 9. finalize + validate（对正常 case）
        p = run([main_py, "finalize", "--status", "partial", "--input", normal_in,
                 "--output", out5, "--work", work5])
        r5f = read_json(os.path.join(out5, "result.json"))
        pv = run([main_py, "validate", "--input", normal_in, "--output", out5, "--work", work5])
        valid_line = "VALID" in pv.stdout.decode("utf-8")
        results.append(check("finalize_validate", p.returncode == 0 and valid_line
                             and r5f.get("runtime", {}).get("finished_at"),
                             "validate_exit=%d valid=%s" % (pv.returncode, valid_line)))

        # 10. 空输入目录 finalize + validate 亦应合法
        p = run([main_py, "finalize", "--status", "insufficient_evidence",
                 "--input", empty_in, "--output", out2, "--work", work2])
        pv = run([main_py, "validate", "--input", empty_in, "--output", out2, "--work", work2])
        results.append(check("empty_validate", p.returncode == 0 and pv.returncode == 0,
                             "finalize_exit=%d validate_exit=%d" % (p.returncode, pv.returncode)))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in results if r)
    print("\nselftest: %d/%d passed" % (passed, len(results)))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
