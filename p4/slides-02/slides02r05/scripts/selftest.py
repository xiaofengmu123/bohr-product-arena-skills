#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本（仅 Python 标准库）。

在临时目录中构造合成输入，验证本 Skill 的契约发现、骨架落盘、增量记录、
终检与诚实降级是否按 SKILL.md 工作。合成输入只用于自检，不含任何正式案例答案。

用法：python3 scripts/selftest.py
退出码：0=全部通过；1=存在失败项。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MAIN = SCRIPTS / "main.py"
CONTRACT = SCRIPTS / "contract.py"

FAILURES = []


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def run(args, env):
    """以当前解释器调用 main.py，返回 (exit_code, stdout_text)。"""
    proc = subprocess.run(
        [sys.executable, "-B", str(MAIN)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60,
    )
    return proc.returncode, proc.stdout


def make_env(inp: Path, out: Path, work: Path) -> dict:
    env = os.environ.copy()
    env["INPUT_DIR"] = str(inp)
    env["OUTPUT_DIR"] = str(out)
    env["WORK_DIR"] = str(work)
    return env


def read_json(path: Path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def test_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp, out, work = root / "input", root / "output", root / "work"
        inp.mkdir()
        code, _ = run(["scaffold"], make_env(inp, out, work))
        r = read_json(out / "result.json")
        check("空输入目录 → 落盘合法结果且退出码 0", code == 0 and r.get("status") == "insufficient_evidence",
              f"status={r.get('status')}")
        check("空输入仍有契约字段骨架", all(k in r for k in
              ("case_id", "status", "presentation_file", "preview_files", "speaker_notes",
               "page_plan", "evidence_map", "version_records", "sources", "uncertainty",
               "pending_human", "change_log")))


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp, out, work = root / "input", root / "output", root / "work"
        inp.mkdir()
        (inp / "case.json").write_text("{ not valid json", encoding="utf-8")
        code, _ = run(["scaffold"], make_env(inp, out, work))
        r = read_json(out / "result.json")
        check("坏 JSON → 优雅降级 input_error", code == 0 and r.get("status") == "input_error",
              f"status={r.get('status')}")
        check("坏 JSON 仍产出合法 result.json", isinstance(r, dict) and "case_id" in r)


def test_custom_contract():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp, out, work = root / "input", root / "output", root / "work"
        inp.mkdir()
        case = {
            "case_id": "demo-case-1",
            "output_contract": {
                "presentation_file": "string",
                "preview_files": "array",
                "custom_metric": "array",
                "custom_meta": "object",
            },
        }
        (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        code, _ = run(["scaffold"], make_env(inp, out, work))
        r = read_json(out / "result.json")
        check("自定义契约字段逐一对齐（含未知键）",
              all(k in r for k in case["output_contract"]) and r.get("case_id") == "demo-case-1",
              f"fields={sorted(r.keys())}")
        check("未知契约字段保留不丢", "custom_metric" in r and "custom_meta" in r)


def test_default_contract_and_record():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp, out, work = root / "input", root / "output", root / "work"
        inp.mkdir()
        (inp / "case.json").write_text(json.dumps({"case_id": "demo-case-2"}, ensure_ascii=False),
                                       encoding="utf-8")
        env = make_env(inp, out, work)
        code, _ = run(["scaffold"], env)
        r0 = read_json(out / "result.json")
        check("缺 output_contract → 默认契约字段齐", all(k in r0 for k in
              ("presentation_file", "preview_files", "speaker_notes", "page_plan",
               "evidence_map", "version_records")), f"status={r0.get('status')}")

        run(["record", "field", "--key", "presentation_file", "--value", "deck.pptx"], env)
        run(["record", "field", "--key", "preview_files", "--value", '["deck.pdf"]'], env)
        run(["record", "field", "--key", "speaker_notes", "--value", '{"pages": {}}'], env)
        run(["record", "field", "--key", "page_plan", "--value", '{"pages": []}'], env)
        run(["record", "evidence", "--page", "1", "--role", "claim",
             "--claim", "page 1 claim", "--refs", "Fig 1", "--loc", "p.2", "--quote", "exact words"], env)
        run(["record", "source", "--ref", "Fig 1", "--loc", "p.2", "--kind", "figure"], env)
        run(["record", "version", "--name", "v1", "--note", "first", "--files", "deck.pptx"], env)

        r1 = read_json(out / "result.json")
        check("record field 逐字段回填契约", r1.get("presentation_file") == "deck.pptx"
              and r1.get("preview_files") == ["deck.pdf"])
        check("evidence_map 含页级证据与逐字 quote",
              isinstance(r1.get("evidence_map"), list) and len(r1["evidence_map"]) == 1
              and r1["evidence_map"][0].get("quote") == "exact words")
        check("sources 含来源定位", isinstance(r1.get("sources"), list)
              and r1["sources"][0].get("loc") == "p.2")
        check("change_log 为一等结构且按 seq 递增",
              isinstance(r1.get("change_log"), list)
              and [e.get("seq") for e in r1["change_log"]] == sorted(e.get("seq") for e in r1["change_log"]))

        # 终检：presentation_file 指向的文件尚不存在，complete 应诚实降级为 partial
        code, _ = run(["finalize", "--status", "complete"], env)
        r2 = read_json(out / "result.json")
        check("complete 前产物缺口 → 诚实降级 partial", r2.get("status") == "partial",
              f"status={r2.get('status')}")
        check("self_check.json 已生成", (out / "self_check.json").exists())


def test_contract_module_runnable():
    proc = subprocess.run(
        [sys.executable, "-B", str(CONTRACT), "/nonexistent/case.json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    check("contract.py 可独立运行且退出码 0", proc.returncode == 0, proc.stdout.strip().splitlines()[0] if proc.stdout else "")


def main() -> int:
    _reconfigure_stdio()
    print(f"selftest 使用解释器: {sys.executable}")
    test_empty_input()
    test_bad_json()
    test_custom_contract()
    test_default_contract_and_record()
    test_contract_module_runnable()
    print("-" * 40)
    if FAILURES:
        print(f"共 {len(FAILURES)} 项失败: {FAILURES}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
