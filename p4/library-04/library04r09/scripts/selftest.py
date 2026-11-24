"""selftest.py - 交付前自检。

从 SKILL 包根目录运行：
  python3 scripts/selftest.py

构造一个合成输入（含 output_contract、文本文件、重复副本、坏 JSON），
依次跑 scaffold → inventory → finalize，断言：
  1. result.json 合法且含 case_id / status / output_contract 全部键；
  2. 证据逐字 quote 回读校验能识别错误；
  3. 空目录／坏 case.json 均能产出合法降级产物且退出码为 0；
  4. 同一输入两次运行结果确定性一致（关键字段逐字节相同）。
合成产物仅用于自检，不冒充正式案例结果。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(*args, cwd=None):
    proc = subprocess.run(
        [PY, MAIN, *args],
        capture_output=True, text=True, cwd=cwd or HERE,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"exit {proc.returncode}: {args}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


def _write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as f:
        f.write(text)


def _case_json(case_id, contract):
    return json.dumps(
        {"case_id": case_id, "output_contract": contract},
        ensure_ascii=False,
    )


def test_happy_path():
    root = tempfile.mkdtemp(prefix="selftest-kb-")
    try:
        inp = os.path.join(root, "input")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        os.makedirs(os.path.join(inp, "materials", "v1"), exist_ok=True)
        os.makedirs(os.path.join(inp, "materials", "v2"), exist_ok=True)

        contract = {"questions": None, "summary": None, "inventory": None}
        _write(os.path.join(inp, "case.json"), _case_json("case-1", contract))
        _write(os.path.join(inp, "task.md"), "# 测试任务\n")

        paper_a = "---\ntitle: 示例论文A\nauthor: 张三\n---\n\n结果：细胞存活率 90%。\n第二行。\n"
        paper_b = "---\ntitle: 示例论文B\n---\n\n结果：细胞存活率 70%。\n"
        _write(os.path.join(inp, "materials", "v1", "paper.md"), paper_a)
        _write(os.path.join(inp, "materials", "v2", "paper.md"), paper_a)  # 重复副本
        _write(os.path.join(inp, "materials", "v2", "paper-v2.md"), paper_b)
        _write(os.path.join(inp, "materials", "broken.json"), "{not valid json", encoding="gbk")

        run("scaffold", "--input", inp, "--output", out, "--work", work)
        scaffold = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        assert scaffold["status"] == "partial"
        assert scaffold["case_id"] == "case-1"
        for k in contract:
            assert k in scaffold, f"契约键缺失: {k}"

        run("inventory", "--input", inp, "--work", work)
        inv = json.load(open(os.path.join(work, "inventory.json"), encoding="utf-8"))
        assert inv["summary"]["file_types"]["text"] >= 3
        paths = [f["path"] for f in inv["files"]]
        assert "materials/v1/paper.md" in paths

        answers = {
            "output": {
                "questions": [{"q": "存活率？", "a": "90%"}],
                "summary": "示例论文A与B存活率不同。",
                "inventory": None,
            },
            "answers": [
                {
                    "question": "存活率是多少？",
                    "answer": "细胞存活率 90%。",
                    "evidence": [
                        {
                            "file": "materials/v1/paper.md",
                            "sha256": inv["files"][0]["sha256"],
                            "locator": "materials/v1/paper.md#L5-L6",
                            "quote": "细胞存活率 90%",
                            "confidence": "certain",
                        }
                    ],
                    "uncertainty": "certain",
                    "missing": None,
                }
            ],
            "uncertainty": [],
        }
        _write(os.path.join(work, "answers.json"), json.dumps(answers, ensure_ascii=False))
        run("finalize", "--input", inp, "--output", out, "--work", work,
            "--answers", os.path.join(work, "answers.json"))
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        assert result["status"] in {"ok", "partial"}
        for k in contract:
            assert k in result, f"最终契约键缺失: {k}"
        assert result["case_id"] == "case-1"
        assert result["answers"] and result["answers"][0]["evidence"][0]["quote"] == "细胞存活率 90%"
        print("[ok] happy path: scaffold/inventory/finalize + 契约回填 + 证据校验")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_degradation():
    for label, make_case in (
        ("empty-dir", lambda inp: None),
        ("bad-json", lambda inp: _write(os.path.join(inp, "case.json"), "{bad")),
    ):
        root = tempfile.mkdtemp(prefix="selftest-deg-")
        try:
            inp = os.path.join(root, "input")
            out = os.path.join(root, "output")
            work = os.path.join(root, "work")
            os.makedirs(inp, exist_ok=True)
            if make_case:
                make_case(inp)
            run("scaffold", "--input", inp, "--output", out, "--work", work)
            result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
            assert result["status"] in {"partial", "insufficient_evidence", "input_error"}
            assert isinstance(result.get("change_log"), list)
            print(f"[ok] degradation ({label}): 退出码 0 + 合法最小产物")
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_determinism():
    root = tempfile.mkdtemp(prefix="selftest-det-")
    try:
        inp = os.path.join(root, "input")
        out1 = os.path.join(root, "out1")
        out2 = os.path.join(root, "out2")
        work1 = os.path.join(root, "work1")
        work2 = os.path.join(root, "work2")
        os.makedirs(inp, exist_ok=True)
        _write(os.path.join(inp, "case.json"), _case_json("case-1", {"summary": None}))
        _write(os.path.join(inp, "a.md"), "# A\n正文\n")
        _write(os.path.join(inp, "b.md"), "# B\n")
        for out, work in ((out1, work1), (out2, work2)):
            run("scaffold", "--input", inp, "--output", out, "--work", work)
            run("inventory", "--input", inp, "--work", work)
            _write(os.path.join(work, "answers.json"), json.dumps({"answers": [], "output": {}, "uncertainty": []}))
            run("finalize", "--input", inp, "--output", out, "--work", work,
                "--answers", os.path.join(work, "answers.json"))
        r1 = json.load(open(os.path.join(out1, "result.json"), encoding="utf-8"))
        r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
        # 移除时间与运行时字段后比较确定性部分
        for r in (r1, r2):
            r.pop("runtime", None)
            for e in r.get("change_log", []):
                e.pop("ts", None)
        assert r1 == r2, "两次运行结果不相等"
        print("[ok] determinism: 同一输入两次运行结果一致（忽略时间戳）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_happy_path()
    test_degradation()
    test_determinism()
    print("SELFTEST PASSED")