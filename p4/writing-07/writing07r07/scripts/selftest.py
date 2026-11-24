#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the robustness scenarios of this skill.

Runs scan/emit_skeleton/validate in-process against synthetic temp inputs and
asserts graceful degradation with valid JSON outputs. Synthetic inputs are for
self-test only and are never presented as official cases. Exits 0 iff all pass.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scan
import emit_skeleton
import validate


def _mk(path, content, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(content)


def _mkbytes(path, raw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(raw)


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def scenario_empty(root):
    inp = os.path.join(root, "input"); os.makedirs(inp, exist_ok=True)
    work = os.path.join(root, "work"); out = os.path.join(root, "output")
    assert scan.run(["--input", inp, "--work", work]) == 0
    m = _load(os.path.join(work, "input_manifest.json"))
    assert m["materials"] == [] and m["input_dir_exists"] is True
    assert emit_skeleton.run(["--work", work, "--output", out]) == 0
    r = _load(os.path.join(out, "result.json"))
    assert r["status"] in ("partial", "insufficient_evidence", "input_error")
    return "empty input -> legal partial result"


def scenario_bad_json(root):
    inp = os.path.join(root, "input"); os.makedirs(inp, exist_ok=True)
    _mk(os.path.join(inp, "case.json"), "{ this is not json ")
    _mk(os.path.join(inp, "task.md"), "# task\n要求写出引言")
    work = os.path.join(root, "work"); out = os.path.join(root, "output")
    assert scan.run(["--input", inp, "--work", work]) == 0
    m = _load(os.path.join(work, "input_manifest.json"))
    assert any(e["reason"] == "bad_json" for e in m["errors"])
    assert any(mt["path"].endswith("task.md") for mt in m["materials"])
    assert emit_skeleton.run(["--work", work, "--output", out]) == 0
    r = _load(os.path.join(out, "result.json"))
    assert r["status"] == "input_error"
    return "bad JSON -> input_error, other materials preserved"


def scenario_bad_encoding(root):
    inp = os.path.join(root, "input"); os.makedirs(inp, exist_ok=True)
    _mk(os.path.join(inp, "case.json"), json.dumps({"case_id": "c1", "output_contract": {"intro": "str"}}))
    _mkbytes(os.path.join(inp, "materials/paper.md"), b"\xff\xfe\xff\xfe\x00\x80 not utf8")
    work = os.path.join(root, "work")
    assert scan.run(["--input", inp, "--work", work]) == 0
    m = _load(os.path.join(work, "input_manifest.json"))
    assert any(d["reason"] == "undecodable" for d in m["degradations"])
    assert m["case_id"] == "c1"
    return "bad encoding -> degradation recorded, no crash"


def scenario_oversized(root):
    inp = os.path.join(root, "input"); os.makedirs(inp, exist_ok=True)
    _mk(os.path.join(inp, "case.json"), json.dumps({"case_id": "c2", "output_contract": ["intro", "related_work"]}))
    _mk(os.path.join(inp, "materials/big.md"), "x" * (3 * 1024 * 1024))
    work = os.path.join(root, "work")
    assert scan.run(["--input", inp, "--work", work, "--max-mb", "2"]) == 0
    m = _load(os.path.join(work, "input_manifest.json"))
    assert any(d["reason"] == "oversized" for d in m["degradations"])
    assert len(m["contract_fields"]) == 2
    return "oversized -> content skipped, degradation recorded"


def scenario_missing_params(root):
    # Verify documented defaults (no actual /input /app execution on dev machine).
    assert scan.build_parser().parse_args([]).input == "/input"
    assert scan.build_parser().parse_args([]).work == "/app/work"
    assert emit_skeleton.build_parser().parse_args([]).work == "/app/work"
    assert emit_skeleton.build_parser().parse_args([]).output == "/app/output"
    assert validate.build_parser().parse_args([]).output == "/app/output"
    assert validate.build_parser().parse_args([]).input == "/input"
    return "missing params -> documented defaults /input /app/work /app/output"


def scenario_determinism(root):
    inp = os.path.join(root, "input"); os.makedirs(inp, exist_ok=True)
    _mk(os.path.join(inp, "case.json"), json.dumps({"case_id": "c3", "output_contract": {"intro": "str", "related_work": "str"}}))
    _mk(os.path.join(inp, "task.md"), "# 任务\n写引言")
    _mk(os.path.join(inp, "materials/paper.md"), "方法 A 在 3 个数据集上提升 1.2%。")
    work1 = os.path.join(root, "work1"); work2 = os.path.join(root, "work2")
    assert scan.run(["--input", inp, "--work", work1]) == 0
    assert scan.run(["--input", inp, "--work", work2]) == 0
    a = _load(os.path.join(work1, "input_manifest.json"))
    b = _load(os.path.join(work2, "input_manifest.json"))
    a.pop("input_dir", None); b.pop("input_dir", None)
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    return "determinism -> identical manifests on repeat run"


def scenario_validate(root):
    inp = os.path.join(root, "input"); os.makedirs(inp, exist_ok=True)
    _mk(os.path.join(inp, "case.json"), json.dumps({"case_id": "c4", "output_contract": {"introduction": "str"}}))
    _mk(os.path.join(inp, "task.md"), "# 任务\n写引言")
    work = os.path.join(root, "work"); out = os.path.join(root, "output")
    assert scan.run(["--input", inp, "--work", work]) == 0
    assert emit_skeleton.run(["--work", work, "--output", out]) == 0
    assert validate.run(["--work", work, "--output", out, "--input", inp]) == 0
    rep = _load(os.path.join(work, "validation_report.json"))
    assert rep["errors"] == []  # legal skeleton satisfies contract + standard fields
    assert any(p == "case_id_present" for p in rep["passed"])
    assert any(w["check"] == "manuscript_missing" for w in rep["warnings"])
    return "validate -> zero errors on a legal skeleton, warnings reported"


SCENARIOS = [
    ("empty_input", scenario_empty),
    ("bad_json", scenario_bad_json),
    ("bad_encoding", scenario_bad_encoding),
    ("oversized", scenario_oversized),
    ("missing_params", scenario_missing_params),
    ("determinism", scenario_determinism),
    ("validate", scenario_validate),
]


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    base = tempfile.mkdtemp(prefix="sci_intro_selftest_")
    failures = []
    for name, fn in SCENARIOS:
        root = os.path.join(base, name)
        os.makedirs(root, exist_ok=True)
        try:
            msg = fn(root)
            print(f"PASS  {name:16s} {msg}")
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((name, str(exc)))
            print(f"FAIL  {name:16s} {exc}")
    shutil.rmtree(base, ignore_errors=True)
    if failures:
        print(f"\nselftest: {len(failures)}/{len(SCENARIOS)} FAILED")
        return 1
    print(f"\nselftest: all {len(SCENARIOS)} scenarios PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
