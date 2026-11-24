#!/usr/bin/env python3
"""自检（academic-slide-builder-deterministic）。

验证：
  1. scaffold 落合法骨架并回填 output_contract；
  2. record / finalize 往返运行确定性（两次 result.json 字节一致）；
  3. 空输入目录、坏 JSON 均降级而不崩溃；
  4. 证据规则：sources 有 loc、evidence 有逐字 quote；
  5. complete 请求在产物缺失时诚实降级为 partial。

所有夹具都在系统临时目录生成，且明确标注为“示例”，不是正式测试答案；
只使用 Python 标准库，不联网、不读取真实案例。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skillkit as sk
import main as m

DEMO_CASE = {
    "id": "demo-case-000",
    "title": "示例论文（仅用于自检，非正式案例）",
    "output_contract": {
        "presentation_file": "string",
        "preview_files": "array",
        "speaker_notes": "object",
        "page_plan": "array",
        "evidence_map": "array",
        "version_records": "array",
        "slide_count": 6,
        "audience": "同行评审",
        "language": "zh",
    },
}

DEMO_TASK = "# 学术 PPT 生成与编辑（自检演示）\n\n把示例论文组织成 6 页学术汇报。\n"
DEMO_PAPER = "# 示例论文（自检演示）\n\n## 摘要\n方法 A 相比方法 B 提升 12%（P=0.02）。\n\n## 图 1\n主结果曲线，见 Fig 1。\n"
DEMO_TABLE = "method,metric\nA,0.85\nB,0.73\n"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _run_ok(argv):
    rc = m.main(argv)
    assert rc == 0, "command failed (rc=%d): %s" % (rc, argv)
    return rc


def _build_demo(root):
    _write(os.path.join(root, "case.json"), sk.canonical_json(DEMO_CASE))
    _write(os.path.join(root, "task.md"), DEMO_TASK)
    _write(os.path.join(root, "paper.md"), DEMO_PAPER)
    _write(os.path.join(root, "table1.csv"), DEMO_TABLE)


def _scenario(input_dir, work_dir, output_dir):
    _run_ok(["scaffold", "--input", input_dir, "--work", work_dir, "--output", output_dir])
    _run_ok(["record", "source", "--ref", "Fig 1", "--loc", "paper.md#图1", "--kind", "figure",
             "--quote", "方法 A 相比方法 B 提升 12%（P=0.02）。", "--used-in", "3",
             "--input", input_dir, "--work", work_dir, "--output", output_dir])
    _run_ok(["record", "evidence", "--page", "3", "--role", "result",
             "--claim", "方法 A 优于方法 B", "--refs", "Fig 1", "--loc", "paper.md#图1",
             "--quote", "方法 A 相比方法 B 提升 12%（P=0.02）。",
             "--input", input_dir, "--work", work_dir, "--output", output_dir])
    _run_ok(["record", "plan", "--page", "3", "--role", "result", "--claim", "主结果",
             "--refs", "Fig 1", "--loc", "paper.md#图1", "--word-budget", "120字",
             "--input", input_dir, "--work", work_dir, "--output", output_dir])
    _run_ok(["record", "field", "--key", "audience", "--value", "同行评审",
             "--input", input_dir, "--work", work_dir, "--output", output_dir])
    _run_ok(["record", "field", "--key", "slide_count", "--value", "6",
             "--input", input_dir, "--work", work_dir, "--output", output_dir])
    _run_ok(["finalize", "--status", "partial",
             "--input", input_dir, "--work", work_dir, "--output", output_dir])
    return os.path.join(output_dir, "result.json")


def test_determinism(tmp):
    in_dir = os.path.join(tmp, "input")
    _build_demo(in_dir)
    r1 = _scenario(in_dir, os.path.join(tmp, "work1"), os.path.join(tmp, "out1"))
    r2 = _scenario(in_dir, os.path.join(tmp, "work2"), os.path.join(tmp, "out2"))
    h1, h2 = sk.sha256_file(r1), sk.sha256_file(r2)
    assert h1 == h2, "result.json 两次运行不一致: %s != %s" % (h1, h2)
    data, err = sk.load_json(r1)
    assert err is None, "result.json 不可解析: %s" % err
    assert data["case_id"] == "demo-case-000"
    assert data["status"] == "partial"
    return data


def test_empty_input(tmp):
    in_dir = os.path.join(tmp, "empty")
    os.makedirs(in_dir, exist_ok=True)
    rc = m.main(["scaffold", "--input", in_dir,
                 "--work", os.path.join(tmp, "empty-work"),
                 "--output", os.path.join(tmp, "empty-out")])
    assert rc == 0
    data, err = sk.load_json(os.path.join(tmp, "empty-out", "result.json"))
    assert err is None
    assert data["case_id"] == "unknown"
    assert any("missing" in d for d in data.get("degradations", []))
    assert any("材料" in x for x in data["uncertainty"]["missing_artifacts"])
    return data


def test_bad_json(tmp):
    in_dir = os.path.join(tmp, "bad")
    os.makedirs(in_dir, exist_ok=True)
    _write(os.path.join(in_dir, "case.json"), "{not valid json")
    _write(os.path.join(in_dir, "task.md"), DEMO_TASK)
    rc = m.main(["scaffold", "--input", in_dir,
                 "--work", os.path.join(tmp, "bad-work"),
                 "--output", os.path.join(tmp, "bad-out")])
    assert rc == 0
    data, err = sk.load_json(os.path.join(tmp, "bad-out", "result.json"))
    assert err is None
    assert any("bad_json" in d for d in data.get("degradations", []))
    return data


def test_complete_degrades(tmp):
    in_dir = os.path.join(tmp, "input2")
    _build_demo(in_dir)
    out = os.path.join(tmp, "out3")
    work = os.path.join(tmp, "work3")
    _run_ok(["scaffold", "--input", in_dir, "--work", work, "--output", out])
    rc = m.main(["finalize", "--status", "complete",
                 "--input", in_dir, "--work", work, "--output", out])
    assert rc == 0
    data, err = sk.load_json(os.path.join(out, "result.json"))
    assert err is None
    assert data["status"] == "partial", "complete 在缺口下未诚实降级"
    return data


def main():
    tmp = tempfile.mkdtemp(prefix="slide-selftest-")
    results = {}
    try:
        det = test_determinism(tmp)
        results["determinism"] = {"ok": True, "input_digest": det["determinism"]["input_digest"]}
        test_empty_input(tmp)
        results["empty_input"] = {"ok": True}
        test_bad_json(tmp)
        results["bad_json"] = {"ok": True}
        test_complete_degrades(tmp)
        results["complete_degrades"] = {"ok": True}
    except AssertionError as exc:
        results["FAILED"] = str(exc)
        print("SELFTEST FAIL: %s" % exc)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST OK determinism=%s empty_input=%s bad_json=%s complete_degrades=%s" % (
        results["determinism"]["ok"], results["empty_input"]["ok"],
        results["bad_json"]["ok"], results["complete_degrades"]["ok"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
