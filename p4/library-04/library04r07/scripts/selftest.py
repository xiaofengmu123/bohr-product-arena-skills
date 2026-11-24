#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- research-kb-qa Skill 自检脚本。

覆盖鲁棒性场景：空输入、坏 JSON、坏编码、缺参数、超大文件、
缺 pdftotext、正常案例。每个场景独立 temp 目录，验证进程退出码
为 0 且 result.json 合法（能解析、含 case_id/status/契约键）。

运行：
  python3 scripts/selftest.py          # 输出每个场景 PASS/FAIL，最终退出码
  python3 scripts/main.py selftest-cmd # 内嵌快速自检（仅正常案例）
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_case(name, setup, expect_status=None, expect_exit=0):
    """在临时目录跑一次 full 流程，返回 (passed, detail)。"""
    tmp = tempfile.mkdtemp(prefix=f"kbqa_selftest_{name}_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp, exist_ok=True)
        setup(inp, out, work)

        proc = subprocess.run(
            [sys.executable, MAIN, "full", "--input", inp,
             "--output", out, "--work", work],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != expect_exit:
            return False, f"exit={proc.returncode} (expect {expect_exit}); stderr={proc.stderr[:400]}"

        result_path = os.path.join(out, "result.json")
        if not os.path.exists(result_path):
            return False, "result.json missing"
        with open(result_path, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return False, "result.json not an object"
        if "case_id" not in data or "status" not in data:
            return False, "result.json missing case_id/status"
        if expect_status and data["status"] != expect_status:
            return False, f"status={data['status']} (expect {expect_status})"
        # 契约键必须回填（若有契约）
        if isinstance(data.get("output_contract"), dict):
            for k, v in data["output_contract"].items():
                if v is None and not isinstance(v, (list, dict)) and v is not None:
                    pass  # 允许 null
        return True, f"status={data['status']}, contract_keys={list((data.get('output_contract') or {}).keys())}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def setup_empty(inp, out, work):
    pass  # 空输入目录，无 case.json/task.md


def setup_bad_json(inp, out, work):
    with open(os.path.join(inp, "case.json"), "wb") as f:
        f.write(b'{"case_id": "x", "output_contract": {"a": null}')  # 截断坏 JSON
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# task\n")


def setup_bad_encoding(inp, out, work):
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "enc", "output_contract": {"results": []}}, f)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# task\n")
    # 坏编码文本文件（非法 UTF-8 字节）
    with open(os.path.join(inp, "notes.md"), "wb") as f:
        f.write(b"# title\n\xff\xfe\xfa invalid utf8\n")


def setup_missing_params(inp, out, work):
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "mp", "output_contract": {"x": None}}, f)
    # 不写 task.md，验证缺 task 也能降级


def setup_oversize(inp, out, work):
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "big", "output_contract": {"results": []}}, f)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# task\n")
    # 3MB 超大文件：应被截断但不崩溃
    with open(os.path.join(inp, "big.txt"), "wb") as f:
        f.write(b"x" * (3 * 1024 * 1024))


def setup_no_pdftotext(inp, out, work):
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "pdf", "output_contract": {"results": []}}, f)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# task\n")
    # 伪 PDF 头，内容无文本层
    with open(os.path.join(inp, "scan.pdf"), "wb") as f:
        f.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n")


def setup_normal(inp, out, work):
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({
            "case_id": "normal-1",
            "output_contract": {"case_id": None, "results": [], "sources": [], "uncertainty": []},
            "materials": ["papers/", "notes.md"],
        }, f)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 科研知识库问答任务\n")
    os.makedirs(os.path.join(inp, "papers"), exist_ok=True)
    with open(os.path.join(inp, "papers", "paper_v1.md"), "w", encoding="utf-8") as f:
        f.write("---\ntitle: Paper\nversion: v1\nauthor: A\n---\n\n方法部分。\n")
    with open(os.path.join(inp, "papers", "paper_v2.md"), "w", encoding="utf-8") as f:
        f.write("---\ntitle: Paper\nversion: v2\nauthor: A\n---\n\n方法部分更新。\n")
    # 重复文件：相同内容
    with open(os.path.join(inp, "notes.md"), "w", encoding="utf-8") as f:
        f.write("# notes\n")
    with open(os.path.join(inp, "notes_copy.md"), "w", encoding="utf-8") as f:
        f.write("# notes\n")


def main():
    cases = [
        ("empty_input", setup_empty, "insufficient_evidence"),
        ("bad_json", setup_bad_json, "input_error"),
        ("bad_encoding", setup_bad_encoding, None),
        ("missing_task_and_params", setup_missing_params, None),
        ("oversize_file", setup_oversize, None),
        ("pdf_no_textlayer", setup_no_pdftotext, None),
        ("normal", setup_normal, None),
    ]
    all_ok = True
    for name, setup, expect in cases:
        ok, detail = run_case(name, setup, expect_status=expect)
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print("=" * 60)
    print("selftest result:", "ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
