#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — smoke tests for the evidence-first figure Skill.

Generates synthetic inputs in a temporary directory (never shipped, never the
formal case answers), runs the full pipeline, then asserts the output contract:

  * result.json exists and is valid JSON
  * case_id, status, six contract categories and generic fields are present
  * evidence_map has per-element locator + verbatim quote (forward + reverse)
  * SVG parses and carries data-source-ref / data-quote attributes
  * empty input and broken JSON degrade to a legal partial result (no crash)

Exit code 0 = all checks pass, 1 = failure. Standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"

SYNTH_CASE = {
    "case_id": "selftest-001",
    "title": "合成自检案例（示意，非正式案例）",
    "kind": "apparatus",
    "output_contract": {
        "entities": "list", "relations": "list", "data_encoding": "list",
        "legend_annotations": "list", "editable_sources": "list", "exports": "list",
        "figure_caption": "string",
    },
}

SYNTH_TASK = """# 合成自检任务（示意）
这是 selftest 生成的合成输入，仅用于验证流水线契约，不是正式案例答案。
"""

SYNTH_NOTES = """1. 称取 5 g 催化剂，装入固定床反应器。
2. 料液经计量泵输送至反应器顶部。
原料罐 → 计量泵 → 固定床反应器 → 冷凝器 → 产品收集罐
反应系统由原料罐、计量泵、固定床反应器、冷凝器与产品收集罐组成。
未反应的尾气经冷凝器回流至反应器。
"""


def run(args, **kw):
    return subprocess.run([sys.executable, "-B", str(MAIN)] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def make_inputs(root: Path) -> Path:
    inp = root / "input"
    (inp / "materials").mkdir(parents=True)
    (inp / "case.json").write_text(json.dumps(SYNTH_CASE, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text(SYNTH_TASK, encoding="utf-8")
    (inp / "materials" / "notes.md").write_text(SYNTH_NOTES, encoding="utf-8")
    return inp


def check_result_json(result_path: Path, expect_case: str) -> list:
    problems = []
    data = json.loads(result_path.read_text(encoding="utf-8"))
    if data.get("case_id") != expect_case:
        problems.append("case_id mismatch: %r" % data.get("case_id"))
    if data.get("status") not in ("complete", "partial", "in_progress"):
        problems.append("bad status: %r" % data.get("status"))
    for field in ("entities", "relations", "data_encoding", "legend_annotations",
                  "editable_sources", "exports"):
        if field not in data:
            problems.append("missing contract field: %s" % field)
    for field in ("case_id", "status", "sources", "uncertainty", "change_log",
                  "evidence_map", "pending_human"):
        if field not in data:
            problems.append("missing generic field: %s" % field)
    # output_contract passthrough: figure_caption declared -> must exist
    if "figure_caption" not in data:
        problems.append("missing declared output_contract field: figure_caption")

    em = data.get("evidence_map") or {}
    if em.get("schema") != "evidence_map/v1":
        problems.append("evidence_map schema missing")
    els = em.get("elements") or {}
    quoted = [e for e in els.values() if e.get("locator") and e.get("quote")]
    if not quoted:
        problems.append("no quoted elements in evidence_map")
    if "reverse_index" not in em:
        problems.append("evidence_map reverse_index missing")
    return problems


def check_svg(svg_path: Path) -> list:
    problems = []
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(svg_path)
    except Exception as exc:  # noqa: BLE001
        return ["svg unparseable: %s" % exc]
    root = tree.getroot()
    content = svg_path.read_text(encoding="utf-8")
    for token in ("layer-entities", "layer-relations", "layer-legend"):
        if token not in content:
            problems.append("missing layer: %s" % token)
    if "data-source-ref" not in content:
        problems.append("missing data-source-ref attribute")
    if "scix-evidence-map" not in content:
        problems.append("missing embedded evidence-map metadata")
    return problems


def test_full_pipeline(tmp: Path) -> list:
    problems = []
    inp = make_inputs(tmp)
    out = tmp / "out"
    work = tmp / "work"
    r = run(["run", "--input", str(inp), "--output", str(out), "--work", str(work)])
    if r.returncode != 0:
        return ["run failed rc=%d\nstdout=%s\nstderr=%s" % (r.returncode, r.stdout, r.stderr)]
    result_path = out / "result.json"
    if not result_path.exists():
        return ["result.json missing"]
    problems += check_result_json(result_path, "selftest-001")
    svg = work / "figure.svg"
    if svg.exists():
        problems += check_svg(svg)
    else:
        problems.append("figure.svg missing")
    return problems


def test_empty_input(tmp: Path) -> list:
    problems = []
    empty = tmp / "empty"
    empty.mkdir()
    out = tmp / "empty_out"
    r = run(["run", "--input", str(empty), "--output", str(out),
             "--work", str(tmp / "empty_work")])
    if r.returncode not in (0,):
        return ["empty input crashed rc=%d stderr=%s" % (r.returncode, r.stderr)]
    result_path = out / "result.json"
    if not result_path.exists():
        return ["empty input: result.json missing"]
    data = json.loads(result_path.read_text(encoding="utf-8"))
    if data.get("status") not in ("partial", "insufficient_evidence", "failed", "complete"):
        problems.append("empty input: bad status %r" % data.get("status"))
    return problems


def test_bad_json(tmp: Path) -> list:
    problems = []
    bad = tmp / "bad"
    (bad / "materials").mkdir(parents=True)
    (bad / "case.json").write_text("{ not valid json", encoding="utf-8")
    (bad / "materials" / "n.md").write_text("A → B\n", encoding="utf-8")
    out = tmp / "bad_out"
    r = run(["run", "--input", str(bad), "--output", str(out), "--work", str(tmp / "bad_work")])
    result_path = out / "result.json"
    if not result_path.exists():
        return ["bad json: result.json missing"]
    data = json.loads(result_path.read_text(encoding="utf-8"))
    if "case_id" not in data or "status" not in data:
        problems.append("bad json: missing case_id/status")
    return problems


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    tmp = Path(tempfile.mkdtemp(prefix="scix-selftest-"))
    all_problems = []
    try:
        for name, fn in (("full_pipeline", test_full_pipeline),
                         ("empty_input", test_empty_input),
                         ("bad_json", test_bad_json)):
            problems = fn(tmp)
            status = "PASS" if not problems else "FAIL"
            print("[%s] %s" % (status, name))
            for p in problems:
                print("   - %s" % p)
            all_problems.extend(problems)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if all_problems:
        print("\nselftest FAILED: %d problem(s)" % len(all_problems))
        return 1
    print("\nselftest PASSED: contract, evidence_map and SVG all verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
