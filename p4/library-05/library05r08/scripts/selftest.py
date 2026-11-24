#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 本地冒烟自检（仅标准库）。

用临时目录构造四类兜底场景与一个完整场景，逐项断言：

    A. 空输入目录        → status=insufficient_evidence，退出码 0
    B. 坏 JSON case.json → status=input_error，errors 记录原因，退出码 0
    C. 缺 case.json      → status=input_error，仍盘点材料
    D. 无 draft 直接 build → status=partial，degradations 含 no_draft
    E. 完整主路径         → init/show/check/build/selfcheck 全部退出码 0，status=ok
    F. 确定性             → 同输入两次 build 的 library_index.json 逐字节一致
    G. 溯源校验           → 错误 quote 被 check 拦截，正确 quote 通过

运行：`python -B scripts/selftest.py`。所有断言失败以退出码 1 表达；
这是本地开发自检脚本，不作为运行时 Skill 的正式路径（正式路径退出码恒 0）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import main as skill_main  # noqa: E402


def run(cmd, input_dir, work_dir, output_dir):
    argv = [cmd, "--input", str(input_dir), "--work", str(work_dir),
            "--output", str(output_dir)]
    return skill_main.main(argv)


def read_result(output_dir):
    return json.loads((output_dir / "result.json").read_text(encoding="utf-8"))


def make_case(base, contract=None, queries=None):
    case = {"case_id": "case-selftest", "output_contract": contract}
    if queries:
        case["queries"] = queries
    (base / "case.json").write_text(json.dumps(case, ensure_ascii=False),
                                    encoding="utf-8")
    (base / "task.md").write_text("challenge_id: `challenge-selftest`\n",
                                  encoding="utf-8")


def scenario_empty():
    root = Path(tempfile.mkdtemp(prefix="skt-empty-"))
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir(parents=True)
    make_case(inp, contract={"library": "list"})  # 有 case.json，但无材料
    run("init", inp, work, out)
    run("build", inp, work, out)
    r = read_result(out)
    assert r["status"] == "insufficient_evidence", r["status"]
    return root


def scenario_bad_json():
    root = Path(tempfile.mkdtemp(prefix="skt-badjson-"))
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir(parents=True)
    (inp / "case.json").write_text("{ this is not json", encoding="utf-8")
    (inp / "materials").mkdir()
    (inp / "materials" / "a.txt").write_text("hello", encoding="utf-8")
    run("init", inp, work, out)
    run("build", inp, work, out)
    r = read_result(out)
    assert r["status"] == "input_error", r["status"]
    assert any(e.get("kind") == "bad_case_json" for e in r.get("errors", [])), r.get("errors")
    return root


def scenario_missing_case():
    root = Path(tempfile.mkdtemp(prefix="skt-nocase-"))
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir(parents=True)
    (inp / "materials").mkdir()
    (inp / "materials" / "b.txt").write_text("world", encoding="utf-8")
    run("init", inp, work, out)
    run("build", inp, work, out)
    r = read_result(out)
    assert r["status"] == "input_error", r["status"]
    assert any(e.get("kind") == "missing_case_json" for e in r.get("errors", [])), r.get("errors")
    return root


def scenario_no_draft():
    root = Path(tempfile.mkdtemp(prefix="skt-nodraft-"))
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir(parents=True)
    make_case(inp, contract={"library": "list", "notes": "list", "summary": "object"})
    (inp / "materials").mkdir()
    (inp / "materials" / "report_v1.pdf").write_bytes(b"%PDF-1.4 fake")
    run("init", inp, work, out)
    run("build", inp, work, out)
    r = read_result(out)
    assert r["status"] == "partial", r["status"]
    assert any(d.get("kind") == "no_draft" for d in r.get("degradations", [])), r.get("degradations")
    # 契约字段全部回填
    for key in ("library", "notes", "summary"):
        assert key in r, f"missing contract field {key}"
    return root


def scenario_full():
    root = Path(tempfile.mkdtemp(prefix="skt-full-"))
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir(parents=True)
    make_case(inp, contract={"library": "list", "annotations": "list"},
              queries=[{"query_id": "Q1", "text": "method baseline"},
                       {"query_id": "Q2", "text": "hypothesis"}])
    m = inp / "materials"
    m.mkdir()
    (m / "notes_a.md").write_text(
        "# Notes\nMethod: baseline calibration.\nThe hypothesis is testable.\n",
        encoding="utf-8")
    (m / "notes_a - 副本.md").write_text(
        "# Notes\nMethod: baseline calibration.\nThe hypothesis is testable.\n",
        encoding="utf-8")  # 与 notes_a.md 完全一致 → duplicate
    (m / "report_v1.txt").write_text("draft one\nmethod baseline\n", encoding="utf-8")
    (m / "report_v2.txt").write_text("draft two\nmethod baseline\n", encoding="utf-8")
    run("init", inp, work, out)
    # 检查 partial 骨架已落盘
    assert (out / "result.json").exists()
    run("show", inp, work, out)

    # 写 draft：正确 quote + 错误 quote（F003=notes_a.md，F004=report_v1.txt）
    draft = {
        "case_id": "case-selftest",
        "notes": [
            {"target": "F003", "kind": "metadata", "confidence": "explicit",
             "text": "notes_a.md 与 notes_a - 副本.md 内容一致，为副本",
             "basis": "sha256 相同",
             "evidence": [{"source_id": "F003", "locator": "materials/notes_a.md",
                           "quote": "baseline calibration"}]},
            {"target": "F004", "kind": "version", "confidence": "heuristic",
             "text": "report_v1 与 report_v2 为候选版本",
             "basis": "stem 相同且含版本标记",
             "evidence": [{"source_id": "F004", "locator": "materials/report_v1.txt",
                           "quote": "NOT_IN_FILE_XYZ"}]},
        ],
        "queries": [
            {"query_id": "Q1", "answer": "baseline calibration",
             "status": "answered",
             "evidence": [{"source_id": "F003", "locator": "materials/notes_a.md",
                           "quote": "baseline calibration"}]},
        ],
    }
    (work / "draft.json").write_text(json.dumps(draft, ensure_ascii=False),
                                     encoding="utf-8")
    run("check", inp, work, out)
    report = json.loads((work / "check_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False, "错误 quote 应被 check 拦截"
    assert report["problems"], report["problems"]

    # 修正错误 quote 后应通过
    draft["notes"][1]["evidence"][0]["quote"] = "draft one"
    (work / "draft.json").write_text(json.dumps(draft, ensure_ascii=False),
                                     encoding="utf-8")
    run("check", inp, work, out)
    report = json.loads((work / "check_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True, report["problems"]

    run("build", inp, work, out)
    run("selfcheck", inp, work, out)
    r = read_result(out)
    assert r["status"] == "ok", r["status"]
    assert r["case_id"] == "case-selftest"
    for key in ("library", "annotations", "sources", "uncertainty", "change_log"):
        assert key in r, f"missing {key}"
    assert (out / "evidence_map.json").exists()
    assert (out / "library_index.json").exists()
    assert (out / "inventory.md").exists()
    return root


def scenario_determinism():
    root = Path(tempfile.mkdtemp(prefix="skt-det-"))
    inp, work_a, out_a = root / "input", root / "work_a", root / "output_a"
    _, work_b, out_b = root / "_", root / "work_b", root / "output_b"
    inp.mkdir(parents=True)
    make_case(inp, contract={"library": "list"})
    m = inp / "materials"
    m.mkdir()
    (m / "p1.txt").write_text("alpha beta\ngamma delta\n", encoding="utf-8")
    (m / "p1_v2.txt").write_text("alpha beta\ngamma delta v2\n", encoding="utf-8")
    for work, out in ((work_a, out_a), (work_b, out_b)):
        run("init", inp, work, out)
        draft = {"case_id": "case-selftest", "notes": [], "queries": []}
        (work / "draft.json").write_text(json.dumps(draft, ensure_ascii=False),
                                         encoding="utf-8")
        run("build", inp, work, out)
    a = (out_a / "library_index.json").read_bytes()
    b = (out_b / "library_index.json").read_bytes()
    assert a == b, "两次运行 library_index.json 不一致"
    return root


def main():
    scenarios = [
        ("空输入目录", scenario_empty),
        ("坏 JSON", scenario_bad_json),
        ("缺 case.json", scenario_missing_case),
        ("无 draft 直接 build", scenario_no_draft),
        ("完整主路径", scenario_full),
        ("确定性", scenario_determinism),
    ]
    failed = []
    for name, fn in scenarios:
        root = None
        try:
            root = fn()
            print(f"[PASS] {name}")
        except Exception as exc:
            failed.append((name, exc))
            print(f"[FAIL] {name}: {exc.__class__.__name__}: {exc}")
        finally:
            if root is not None:
                shutil.rmtree(root, ignore_errors=True)
    if failed:
        print(f"\n{len(failed)} 项失败")
        return 1
    print("\n全部自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())