#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地自检：跑 空目录 / 坏 JSON / 最小案例 三种输入，断言 result.json 合法。

用法：python3 scripts/selftest.py
退出码：0 全部通过；1 任一项失败。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _run_case(name, setup, expect_status):
    base = tempfile.mkdtemp(prefix="selftest_mew_")
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    setup(inp)
    try:
        core_impl.cmd_skeleton(inp, out, work)
        core_impl.cmd_inventory(inp, out, work)
        core_impl.cmd_validate(inp, out, work)
        rp = os.path.join(out, "result.json")
        result = json.load(open(rp, encoding="utf-8"))
        assert result.get("case_id"), "case_id missing"
        assert result.get("status") in core_impl.STATUSES, "bad status"
        for k in ("sources", "uncertainty", "change_log"):
            assert k in result, "missing standard field " + k
        # 空输入场景：骨架应保持 insufficient 或 partial，且不抛异常
        if expect_status == "insufficient_evidence":
            assert result.get("status") in ("partial", "insufficient_evidence"), result.get("status")
        print("PASS  {}".format(name))
        return True
    except Exception as e:  # noqa: BLE001
        print("FAIL  {}: {}".format(name, repr(e)))
        return False
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    results = []

    # 1. 空输入目录
    results.append(_run_case("empty_dir", lambda inp: None, "insufficient_evidence"))

    # 2. 坏 JSON
    def bad_json(inp):
        _write(os.path.join(inp, "case.json"), "{ not json ]")
        _write(os.path.join(inp, "task.md"), "# task\n")

    results.append(_run_case("bad_json", bad_json, "insufficient_evidence"))

    # 3. 最小案例（含 output_contract 与 UTF-8 中文材料）
    def minimal(inp):
        _write(
            os.path.join(inp, "case.json"),
            json.dumps(
                {
                    "case_id": "case-min",
                    "output_contract": {"manuscript": None, "parameter_table": None},
                },
                ensure_ascii=False,
            ),
        )
        _write(os.path.join(inp, "task.md"), "# 方法与实验章节\n体裁：论文 Methods\n")
        _write(
            os.path.join(inp, "exp", "config.yaml"),
            "learning_rate: 1e-4\nbatch_size: 32\nseed: 42\ndataset: ImageNet-v1\n",
        )

    results.append(_run_case("minimal", minimal, "partial"))

    ok = all(results)
    print("selftest {} ({} passed, {} failed)".format("PASS" if ok else "FAIL", sum(results), len(results) - sum(results)))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
