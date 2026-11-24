#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检：在临时目录构造合成示例，验证
scripts/main.py 与 scripts/core_impl.py 的契约、鲁棒性与确定性。

说明：本脚本生成的输入是**合成示例**（自检用），不是正式案例答案。
只依赖 Python 标准库。
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable or "python3"


def run(args, **kw):
    return subprocess.run([PY, "-B", MAIN] + args, capture_output=True,
                          text=True, timeout=120, **kw)


def make_demo_input(root):
    """构造一个含重复、多版本、断链、清单、问题的最小合成案例。"""
    os.makedirs(os.path.join(root, "materials", "projA"), exist_ok=True)
    os.makedirs(os.path.join(root, "materials", "empty_dir"), exist_ok=True)
    # 同一内容的重复文件
    with open(os.path.join(root, "materials", "projA", "protocol.txt"), "w",
              encoding="utf-8") as f:
        f.write("# 缓冲液配方\n1.0x TBS: 25 mM Tris, 150 mM NaCl, pH 7.6\n")
    with open(os.path.join(root, "materials", "projA", "protocol_copy.txt"), "w",
              encoding="utf-8") as f:
        f.write("# 缓冲液配方\n1.0x TBS: 25 mM Tris, 150 mM NaCl, pH 7.6\n")
    # 多版本
    with open(os.path.join(root, "materials", "manuscript_v1.txt"), "w",
              encoding="utf-8") as f:
        f.write("Title: Study A\n\nIntroduction\nThis is the first draft.\n")
    with open(os.path.join(root, "materials", "manuscript_v2.txt"), "w",
              encoding="utf-8") as f:
        f.write("Title: Study A\n\nIntroduction\nThis is the revised draft with results.\n")
    # 断链符号链接（Windows 上可能失败，容错）
    try:
        os.symlink(os.path.join(root, "materials", "missing.txt"),
                   os.path.join(root, "materials", "broken_link.txt"))
    except (OSError, NotImplementedError):
        pass
    # 清单
    with open(os.path.join(root, "materials", "library.json"), "w",
              encoding="utf-8") as f:
        json.dump({"files": [
            {"path": "materials/projA/protocol.txt"},
            {"path": "materials/manuscript_v1.txt"},
            {"path": "materials/does_not_exist.pdf"}]}, f, ensure_ascii=False)
    # 版本说明
    with open(os.path.join(root, "materials", "version_notes.md"), "w",
              encoding="utf-8") as f:
        f.write("# Version 1.0\n初始版\n\n# Version 2.0\n修订版\n")
    # case.json
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "selftest-case-001",
                   "output_contract": {"answer": None, "items": []},
                   "question": "缓冲液配方"}, f, ensure_ascii=False)
    # task.md
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 自检任务\n问题：缓冲液配方写在哪一版？\n")


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {detail}" if detail else ""))
    return cond


def main():
    ok = True
    print("== 1. --help 退出码 0 ==")
    r = run(["--help"])
    ok &= check("help exit 0", r.returncode == 0, f"rc={r.returncode}")

    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        make_demo_input(inp)

        print("== 2. 完整运行 ==")
        r = run(["--case-json", os.path.join(inp, "case.json"),
                 "--task-path", os.path.join(inp, "task.md"),
                 "--input-root", inp, "--output-dir", out, "--work-dir", work])
        ok &= check("full run exit 0", r.returncode == 0, r.stderr.strip()[:200])

        result_path = os.path.join(out, "result.json")
        ok &= check("result.json 存在", os.path.exists(result_path))
        if os.path.exists(result_path):
            with open(result_path, encoding="utf-8") as f:
                res = json.load(f)
            ok &= check("case_id", res.get("case_id") == "selftest-case-001",
                        str(res.get("case_id")))
            ok &= check("status 合法", res.get("status") in
                        ("ok", "partial", "insufficient_evidence", "input_error"),
                        str(res.get("status")))
            ok &= check("sources 数组", isinstance(res.get("sources"), list))
            ok &= check("change_log 数组", isinstance(res.get("change_log"), list))
            ok &= check("computed.summary 存在",
                        isinstance(res.get("computed", {}).get("summary"), dict))

        print("== 3. 产物齐全 ==")
        for art in ("library_manifest.csv", "directory_tree.txt",
                    "dedup_version.json", "recovery_report.json",
                    "library_index.json", "library_manifest.md",
                    "operation_log.jsonl"):
            p = os.path.join(out, art)
            ok &= check(art, os.path.exists(p))

        print("== 4. --check 通过 ==")
        r = run(["--check", "--result", result_path, "--case-json",
                 os.path.join(inp, "case.json")])
        ok &= check("check exit 0", r.returncode == 0, r.stdout.strip())

        print("== 5. 空目录 -> insufficient_evidence，退出码 0 ==")
        empty_in = os.path.join(tmp, "empty_in")
        os.makedirs(empty_in, exist_ok=True)
        empty_out = os.path.join(tmp, "empty_out")
        r = run(["--input-root", empty_in, "--output-dir", empty_out,
                 "--work-dir", os.path.join(tmp, "empty_work"),
                 "--case-json", os.path.join(inp, "case.json"),
                 "--task-path", os.path.join(inp, "task.md")])
        ok &= check("empty exit 0", r.returncode == 0, f"rc={r.returncode}")
        er = os.path.join(empty_out, "result.json")
        if os.path.exists(er):
            with open(er, encoding="utf-8") as f:
                e_res = json.load(f)
            ok &= check("empty status", e_res.get("status") == "insufficient_evidence",
                        str(e_res.get("status")))

        print("== 6. 坏 JSON -> input_error，不崩溃 ==")
        bad_in = os.path.join(tmp, "bad_in")
        os.makedirs(bad_in, exist_ok=True)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with open(os.path.join(bad_in, "task.md"), "w", encoding="utf-8") as f:
            f.write("task")
        bad_out = os.path.join(tmp, "bad_out")
        r = run(["--input-root", bad_in, "--output-dir", bad_out,
                 "--work-dir", os.path.join(tmp, "bad_work"),
                 "--case-json", os.path.join(bad_in, "case.json"),
                 "--task-path", os.path.join(bad_in, "task.md")])
        ok &= check("bad-json exit 0", r.returncode == 0, f"rc={r.returncode}")
        br = os.path.join(bad_out, "result.json")
        if os.path.exists(br):
            with open(br, encoding="utf-8") as f:
                b_res = json.load(f)
            ok &= check("bad-json status", b_res.get("status") == "input_error",
                        str(b_res.get("status")))

        print("== 7. 确定性：两次运行除时间戳外一致 ==")
        out2 = os.path.join(tmp, "output2")
        r1 = run(["--case-json", os.path.join(inp, "case.json"),
                  "--task-path", os.path.join(inp, "task.md"),
                  "--input-root", inp, "--output-dir", out, "--work-dir", work])
        r2 = run(["--case-json", os.path.join(inp, "case.json"),
                  "--task-path", os.path.join(inp, "task.md"),
                  "--input-root", inp, "--output-dir", out2,
                  "--work-dir", os.path.join(tmp, "work2")])
        with open(result_path, encoding="utf-8") as f:
            res1 = json.load(f)
        with open(os.path.join(out2, "result.json"), encoding="utf-8") as f:
            res2 = json.load(f)
        strip = lambda r: {k: v for k, v in r.items() if k not in ("runtime", "change_log")}
        ok &= check("deterministic computed", strip(res1) == strip(res2))

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
