#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检：用临时合成输入验证 finalize.py 的契约与降级行为。

覆盖：正常入库、空目录、缺 case.json、坏 JSON、坏编码、确定性复跑、--check。
只依赖标准库；通过相对路径定位 finalize.py，不改动真实案例。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FINALIZE = os.path.join(HERE, "finalize.py")


def run_finalize(input_dir, output_dir, extra=None):
    cmd = [sys.executable, "-B", FINALIZE,
           "--input-root", input_dir, "--output-dir", output_dir,
           "--work-dir", os.path.join(output_dir, "_work"),
           "--case-json", os.path.join(input_dir, "case.json"),
           "--task-path", os.path.join(input_dir, "task.md")]
    if extra:
        cmd += extra
    return subprocess.run(cmd, capture_output=True, text=True)


def write(path, content, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode, encoding=None if isinstance(content, bytes) else encoding) as f:
        f.write(content)


def make_case(root):
    case = {
        "case_id": "case-test-0001",
        "task": "整理 materials 目录并去重",
        "output_contract": {
            "case_id": None, "summary": None, "files": None, "duplicate_groups": None,
            "version_candidates": None, "reconciliation": None,
        },
        "questions": ["文件总数是多少", "有哪些重复文件"],
    }
    write(os.path.join(root, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(root, "task.md"),
          "# 测试题目\n\nchallenge_id: `case-test-0001`\n\n版本说明：v1 为草稿。\n")
    write(os.path.join(root, "materials", "data_v1.csv"), "a,b\n1,2\n")
    write(os.path.join(root, "materials", "data_v2.csv"), "a,b\n1,2\n")
    write(os.path.join(root, "materials", "data_v1_copy.csv"), "a,b\n1,2\n")
    write(os.path.join(root, "materials", "catalog.json"),
          json.dumps([{"path": "data_v1.csv", "author": "A", "year": "2024"},
                      {"path": "data_v3.csv"}], ensure_ascii=False))
    write(os.path.join(root, "materials", "bad.json"), "{ not json")
    write(os.path.join(root, "materials", "version_notes.md"),
          "版本说明：v1 为草稿，v2 为定稿。\n")
    write(os.path.join(root, "materials", "gbk.txt"), "中文编码测试".encode("gbk"))
    write(os.path.join(root, "materials", "empty", ".keep"), "")


def check_result(root):
    rp = os.path.join(root, "result.json")
    with open(rp, "r", encoding="utf-8") as f:
        r = json.load(f)
    assert r.get("case_id") == "case-test-0001", "case_id 识别错误"
    assert r.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"}
    for k in ("sources", "uncertainty", "change_log", "errors", "degradations", "limitations"):
        assert k in r, "缺少字段 %s" % k
    assert "summary" in r, "contract 字段 summary 未回填"
    assert "files" in r, "contract 字段 files 未回填"
    assert r["summary"]["duplicate_groups"] >= 1, "重复组未识别"
    for art in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                "recovery_report.json", "library_index.json", "library_manifest.md",
                "operation_log.jsonl"):
        assert os.path.isfile(os.path.join(root, art)), "产物缺失 %s" % art
    return r


def norm_result(root):
    rp = os.path.join(root, "result.json")
    with open(rp, "r", encoding="utf-8") as f:
        r = json.load(f)
    r.pop("runtime", None)
    r.pop("change_log", None)
    return r


def main():
    base = tempfile.mkdtemp(prefix="selftest_lib_")
    results = []

    def record(name, ok, detail=""):
        results.append((name, ok, detail))
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))

    try:
        # 1. 正常案例
        c1 = os.path.join(base, "case1")
        make_case(c1)
        p = run_finalize(c1, os.path.join(base, "out1"))
        record("normal_case", p.returncode == 0, p.stderr.strip() or p.stdout.strip())
        if p.returncode == 0:
            r = check_result(os.path.join(base, "out1"))
            record("contract_backfill", r.get("summary") is not None and r.get("files") is not None)
            record("dedup_content_hash",
                   any(g["count"] >= 3 for g in r["computed"]["duplicate_groups"]))
            record("reconcile_missing",
                   r["computed"]["reconciliation"]["missing_count"] >= 1)
            # 确定性复跑
            run_finalize(c1, os.path.join(base, "out1b"))
            record("determinism",
                   norm_result(os.path.join(base, "out1")) == norm_result(os.path.join(base, "out1b")))
            # --check
            chk = subprocess.run([sys.executable, "-B", FINALIZE, "--check",
                                  os.path.join(base, "out1", "result.json"),
                                  "--case-json", os.path.join(c1, "case.json")],
                                 capture_output=True, text=True)
            record("check_ok", chk.returncode == 0, chk.stdout.strip())

        # 2. 空目录
        c2 = os.path.join(base, "case2_empty")
        os.makedirs(c2, exist_ok=True)
        p2 = run_finalize(c2, os.path.join(base, "out2"))
        with open(os.path.join(base, "out2", "result.json"), encoding="utf-8") as f:
            r2 = json.load(f)
        record("empty_dir", p2.returncode == 0 and r2["status"] == "insufficient_evidence")

        # 3. 缺 case.json
        c3 = os.path.join(base, "case3")
        write(os.path.join(c3, "task.md"), "challenge_id: `case-test-0003`\n")
        p3 = run_finalize(c3, os.path.join(base, "out3"))
        with open(os.path.join(base, "out3", "result.json"), encoding="utf-8") as f:
            r3 = json.load(f)
        record("missing_case_json", p3.returncode == 0 and r3["status"] == "input_error")

        # 4. 坏 JSON case.json
        c4 = os.path.join(base, "case4")
        write(os.path.join(c4, "case.json"), "{ bad")
        write(os.path.join(c4, "task.md"), "challenge_id: `case-test-0004`\n")
        p4 = run_finalize(c4, os.path.join(base, "out4"))
        with open(os.path.join(base, "out4", "result.json"), encoding="utf-8") as f:
            r4 = json.load(f)
        record("bad_case_json", p4.returncode == 0 and any(
            e.get("kind") == "bad_json" for e in r4["errors"]))

        # 5. --skeleton-only 落盘
        c5 = os.path.join(base, "case5")
        make_case(c5)
        p5 = run_finalize(c5, os.path.join(base, "out5"), ["--skeleton-only"])
        with open(os.path.join(base, "out5", "result.json"), encoding="utf-8") as f:
            r5 = json.load(f)
        record("skeleton_only", p5.returncode == 0 and r5["status"] == "partial")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = [x for x in results if not x[1]]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
