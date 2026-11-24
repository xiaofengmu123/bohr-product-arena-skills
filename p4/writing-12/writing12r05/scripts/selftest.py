#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — Skill 提交前自检脚本（仅标准库）。

在临时目录中构造三组输入，端到端验证 main.py / evidence.py 的契约流程：
  1. 空输入目录    -> 不崩溃，result.json 合法（insufficient/input_error）。
  2. 坏 JSON        -> 容错降级，result.json 合法。
  3. 完整契约      -> 全部 output_contract 字段回填后 selfcheck 通过。

退出码：全部通过=0，任一断言失败=1。不写任何正式测试产物，仅用 tempfile。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as main_mod  # noqa: E402
import evidence as evidence_mod  # noqa: E402


def run(cmd_fn, **kw):
    args = argparse.Namespace(**kw)
    return cmd_fn(args)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def scenario_empty(tmp):
    inp = os.path.join(tmp, "empty-input")
    out = os.path.join(tmp, "empty-output")
    work = os.path.join(tmp, "empty-work")
    os.makedirs(inp, exist_ok=True)
    run(main_mod.cmd_bootstrap, input=inp, output=out, work=work)
    run(main_mod.cmd_finalize, input=inp, output=out, work=work)
    code = run(main_mod.cmd_selfcheck, input=inp, output=out, work=work)
    result = load(os.path.join(out, "result.json"))
    assert code == 0, "empty-input selfcheck should pass"
    assert result.get("status") in ("input_error", "insufficient_evidence"), result.get("status")
    assert isinstance(result.get("sources"), list)
    assert isinstance(result.get("change_log"), list) and result["change_log"]
    return "empty-input: ok"


def scenario_bad_json(tmp):
    inp = os.path.join(tmp, "bad-input")
    out = os.path.join(tmp, "bad-output")
    work = os.path.join(tmp, "bad-work")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), "{ not valid json [[[")
    write(os.path.join(inp, "task.md"), "# task\n")
    run(main_mod.cmd_bootstrap, input=inp, output=out, work=work)
    run(main_mod.cmd_finalize, input=inp, output=out, work=work)
    code = run(main_mod.cmd_selfcheck, input=inp, output=out, work=work)
    result = load(os.path.join(out, "result.json"))
    assert code == 0, "bad-json selfcheck should pass"
    assert result.get("status") == "input_error", result.get("status")
    assert any("bad_json" in str(e) or "json_truncated" in str(e)
               for e in result.get("errors", [])), result.get("errors")
    return "bad-json: ok"


def scenario_contract(tmp):
    inp = os.path.join(tmp, "contract-input")
    out = os.path.join(tmp, "contract-output")
    work = os.path.join(tmp, "contract-work")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "demo-case-001",
        "output_contract": {
            "title": "string",
            "abstract": {"type": "string"},
            "keywords": ["list"],
            "manuscript_tex": "path",
        },
    }, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# 生成论文\n")
    write(os.path.join(inp, "materials", "paper.md"),
          "The sample has 42.5 nm pores.\nThe lattice constant is 5.43 A.\n")

    run(main_mod.cmd_bootstrap, input=inp, output=out, work=work)
    skeleton = load(os.path.join(out, "result.json"))
    for key in ("title", "abstract", "keywords", "manuscript_tex"):
        assert key in skeleton, "skeleton must include contract key " + key
        assert skeleton[key] is None, key + " should start null"

    run(main_mod.cmd_contract_set, key="title", value='"A sample title"',
        source="materials/paper.md#L1", output=out, work=work, note="from material")
    run(main_mod.cmd_contract_set, key="abstract", value='"An abstract."',
        output=out, work=work, source=None, note=None)
    run(main_mod.cmd_contract_set, key="keywords", value='["a","b"]',
        output=out, work=work, source=None, note=None)
    run(main_mod.cmd_contract_set, key="manuscript_tex", value='"main.tex"',
        output=out, work=work, source=None, note=None)

    # 证据取证验证
    idx_out = os.path.join(work, "source_index.json")
    run(evidence_mod.cmd_index, root=inp, out=idx_out)
    idx = load(idx_out)
    assert any(f["rel"] == "materials/paper.md" for f in idx["files"])

    run(main_mod.cmd_finalize, input=inp, output=out, work=work)
    code = run(main_mod.cmd_selfcheck, input=inp, output=out, work=work)
    result = load(os.path.join(out, "result.json"))
    assert code == 0, "contract selfcheck should pass (got %s)" % code
    for key in ("title", "abstract", "keywords", "manuscript_tex"):
        assert result.get(key) is not None, "contract field must be filled: " + key
    report = result.get("contract_report", {})
    for key in ("title", "abstract", "keywords", "manuscript_tex"):
        assert report.get(key, {}).get("state") == "filled", key + " not marked filled"
    assert len(result.get("change_log", [])) >= 5, "change_log must record every fill"
    assert result.get("case_id") == "demo-case-001"
    # 记录来源的字段应带 locator。
    assert report["title"].get("source") == "materials/paper.md#L1"
    return "contract: ok"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Skill 提交前自检（仅标准库）")
    parser.add_argument("--tmp", default=None, help="可选：指定临时目录（默认系统临时目录）")
    args = parser.parse_args(argv)

    results = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    if args.tmp:
        tmp = args.tmp
        os.makedirs(tmp, exist_ok=True)
    try:
        for name, fn in (("empty-input", scenario_empty),
                         ("bad-json", scenario_bad_json),
                         ("contract", scenario_contract)):
            try:
                results.append("%s" % fn(tmp))
                print("PASS %s" % name)
            except Exception as exc:  # noqa: BLE001
                results.append("FAIL %s: %s" % (name, exc))
                print("FAIL %s: %s" % (name, exc))
        failed = [r for r in results if r.startswith("FAIL")]
        if failed:
            print("SELFTEST FAILED: %d/%d" % (len(failed), len(results)))
            return 1
        print("SELFTEST PASSED: %d scenarios" % len(results))
        return 0
    finally:
        if not args.tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
