#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 离线自检：用临时目录跑通四类兜底场景。

只依赖标准库；不依赖 /input、/app 等正式目录。
用法： python -B scripts/selftest.py
退出码 0 表示全部通过（打印 PASS）；否则打印 FAIL 详情。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MAIN = SCRIPTS / "main.py"


def run(args, cwd):
    return subprocess.run([sys.executable, "-B", str(MAIN), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=120)


def run_pipeline(inp: Path, work: Path, out: Path, with_draft: bool = False):
    results = {}
    for cmd in ("init", "show"):
        r = run([cmd, "--input", str(inp), "--work", str(work), "--output", str(out)], inp)
        results[cmd] = r.returncode
        assert r.returncode == 0, f"{cmd} rc={r.returncode}\n{r.stderr}"
    if with_draft:
        (work / "draft.json").write_text(json.dumps({
            "case_id": "t", "notes": [
                {"target": "F001", "kind": "metadata", "text": "t", "basis": "t",
                 "confidence": "explicit",
                 "evidence": [{"source_id": "F001", "locator": "a.txt", "quote": "hello"}]}],
            "queries": []}, ensure_ascii=False), encoding="utf-8")
    for cmd in ("check", "build", "selfcheck"):
        r = run([cmd, "--input", str(inp), "--work", str(work), "--output", str(out)], inp)
        results[cmd] = r.returncode
        assert r.returncode == 0, f"{cmd} rc={r.returncode}\n{r.stderr}"
    rj = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert rj.get("case_id") and rj.get("status"), "result.json 缺 case_id/status"
    return rj


def scenario_empty(tmp: Path):
    inp, work, out = tmp / "e_in", tmp / "e_work", tmp / "e_out"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({"case_id": "empty", "output_contract": ["inventory"]}),
                                   encoding="utf-8")
    rj = run_pipeline(inp, work, out)
    assert rj["status"] in ("insufficient_evidence", "partial"), rj["status"]


def scenario_bad_json(tmp: Path):
    inp, work, out = tmp / "b_in", tmp / "b_work", tmp / "b_out"
    inp.mkdir()
    (inp / "case.json").write_text("{bad json", encoding="utf-8")
    (inp / "a.txt").write_text("hello world", encoding="utf-8")
    rj = run_pipeline(inp, work, out)
    assert rj["status"] in ("input_error", "partial"), rj["status"]
    assert any(e.get("scope") == "case.json" for e in rj.get("errors", [])), "应记录坏 JSON"


def scenario_missing_case(tmp: Path):
    inp, work, out = tmp / "m_in", tmp / "m_work", tmp / "m_out"
    inp.mkdir()
    (inp / "notes.md").write_text("别名", encoding="utf-8")
    rj = run_pipeline(inp, work, out)
    assert rj["status"] in ("input_error", "partial"), rj["status"]
    assert rj["result"]["summary"].get("files_total", 0) == 1, "缺 case.json 仍应盘点材料"


def scenario_no_draft(tmp: Path):
    inp, work, out = tmp / "n_in", tmp / "n_work", tmp / "n_out"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({"case_id": "nd", "output_contract": ["inventory"]}),
                                   encoding="utf-8")
    (inp / "a.txt").write_text("alpha", encoding="utf-8")
    (inp / "b.txt").write_text("alpha", encoding="utf-8")  # 与 a.txt 同内容 -> 别名
    rj = run_pipeline(inp, work, out, with_draft=False)
    assert rj["status"] == "partial", rj["status"]
    assert any(d.get("reason") == "no_draft" for d in rj.get("degradations", [])), "应标 no_draft"
    assert rj["result"]["summary"]["alias_groups"] == 1, "应识别出重复文件"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lib_selftest_"))
    checks = [
        ("empty", scenario_empty),
        ("bad_json", scenario_bad_json),
        ("missing_case", scenario_missing_case),
        ("no_draft", scenario_no_draft),
    ]
    failed = []
    for name, fn in checks:
        try:
            fn(tmp)
            print(f"[PASS] {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, str(e)))
            print(f"[FAIL] {name}: {e}")
    if failed:
        print("SELFTEST FAILED:", len(failed), "scenarios")
        return 1
    print("SELFTEST PASS:", len(checks), "scenarios")
    return 0


if __name__ == "__main__":
    sys.exit(main())