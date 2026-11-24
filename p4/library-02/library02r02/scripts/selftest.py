#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在临时目录构造最小输入，验证 main.py 的骨架、
全量、坏 JSON、空目录、缺 case.json、--check 六类路径。仅依赖标准库，可跨平台。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def make_input(d, with_case=True, bad_case=False, empty=False):
    os.makedirs(os.path.join(d, "input", "materials"), exist_ok=True)
    if with_case:
        case = {
            "case_id": "selftest-case-1",
            "output_contract": {
                "inventory": None, "versions": [], "answers": None,
            },
            "questions": ["哪个文件记录了缓冲液配方？"],
        }
        if bad_case:
            case_path = os.path.join(d, "input", "case.json")
            with open(case_path, "w", encoding="utf-8") as f:
                f.write("{not valid json")
        else:
            with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as f:
                json.dump(case, f, ensure_ascii=False)
    if not empty:
        with open(os.path.join(d, "input", "task.md"), "w", encoding="utf-8") as f:
            f.write("# 自检任务\n\nchallenge_id: `selftest-case-1`\n\n## 版本说明\n\n"
                    "| file | version | status |\n|-|-|-|\n| buffer_v2.txt | 2 | submitted |\n")
        with open(os.path.join(d, "input", "materials", "buffer_v2.txt"), "w", encoding="utf-8") as f:
            f.write("Buffer A: 10 mM Tris-HCl, pH 8.0.\n")
        with open(os.path.join(d, "input", "materials", "buffer_v2_copy.txt"), "w", encoding="utf-8") as f:
            f.write("Buffer A: 10 mM Tris-HCl, pH 8.0.\n")
        with open(os.path.join(d, "input", "materials", "buffer_v1.txt"), "w", encoding="utf-8") as f:
            f.write("Buffer A: 10 mM Tris-HCl, pH 7.4.\n")


def main():
    tmp = tempfile.mkdtemp(prefix="library-selftest-")
    failures = []
    checks = 0

    def expect(name, cond, detail=""):
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(f"{name}: {detail}")
        print(("PASS" if cond else "FAIL"), name, detail)

    try:
        # 1. --help
        r = run(["--help"])
        expect("help_exit0", r.returncode == 0, f"rc={r.returncode}")

        # 2. 骨架模式
        d = os.path.join(tmp, "skeleton")
        make_input(d)
        r = run(["--input", os.path.join(d, "input"), "--output", os.path.join(d, "out"),
                 "--work", os.path.join(d, "work"), "--skeleton-only"])
        res = json.load(open(os.path.join(d, "out", "result.json"), encoding="utf-8"))
        expect("skeleton_written", r.returncode == 0 and res.get("status") == "partial",
               f"status={res.get('status')}")
        expect("skeleton_contract", res.get("inventory") is None, "contract echoed")

        # 3. 全量模式
        d = os.path.join(tmp, "full")
        make_input(d)
        r = run(["--input", os.path.join(d, "input"), "--output", os.path.join(d, "out"),
                 "--work", os.path.join(d, "work")])
        res = json.load(open(os.path.join(d, "out", "result.json"), encoding="utf-8"))
        expect("full_rc0", r.returncode == 0, f"rc={r.returncode}")
        expect("full_case_id", res.get("case_id") == "selftest-case-1", res.get("case_id"))
        expect("full_status", res.get("status") in ("ok", "partial"), res.get("status"))
        dup = res["result"]["duplicate_groups"]
        expect("full_dedup", len(dup) == 1, f"{len(dup)} dup groups")
        expect("full_contract", res.get("versions") == [], "contract versions echoed")
        artifacts = ["library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json", "library_manifest.md",
                     "operation_log.jsonl"]
        expect("full_artifacts", all(os.path.exists(os.path.join(d, "out", a)) for a in artifacts),
               "all artifacts present")

        # 4. --check 通过
        r = run(["--check", os.path.join(d, "out", "result.json"),
                 "--case-json", os.path.join(d, "input", "case.json")])
        expect("check_ok", r.returncode == 0, r.stdout.strip()[:60])

        # 5. 坏 JSON
        d = os.path.join(tmp, "badjson")
        make_input(d, bad_case=True)
        r = run(["--input", os.path.join(d, "input"), "--output", os.path.join(d, "out"),
                 "--work", os.path.join(d, "work")])
        res = json.load(open(os.path.join(d, "out", "result.json"), encoding="utf-8"))
        expect("badjson_no_crash", r.returncode == 0 and any(
            e.get("type") == "case_json_error" for e in res.get("errors", [])), "error recorded")

        # 6. 空目录
        d = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(d, "input"), exist_ok=True)
        with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "empty-case", "output_contract": {"x": None}}, f)
        r = run(["--input", os.path.join(d, "input"), "--output", os.path.join(d, "out"),
                 "--work", os.path.join(d, "work")])
        res = json.load(open(os.path.join(d, "out", "result.json"), encoding="utf-8"))
        expect("empty_insufficient", r.returncode == 0 and res.get("status") == "insufficient_evidence",
               res.get("status"))

        # 7. 缺 case.json
        d = os.path.join(tmp, "nocase")
        os.makedirs(os.path.join(d, "input"), exist_ok=True)
        r = run(["--input", os.path.join(d, "input"), "--output", os.path.join(d, "out"),
                 "--work", os.path.join(d, "work")])
        res = json.load(open(os.path.join(d, "out", "result.json"), encoding="utf-8"))
        expect("nocase_input_error", r.returncode == 0 and res.get("status") == "input_error",
               res.get("status"))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nselftest: {checks - len(failures)}/{checks} passed")
    if failures:
        for f in failures:
            print("FAILED:", f)
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
