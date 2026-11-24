#!/usr/bin/env python3
"""slides-01 自检脚本（仅标准库）。

四场景：空目录 / 坏 JSON / 缺输入 / 正常骨架+校验。退出码 0=通过，1=失败。
用法：python3 scripts/selftest.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core_impl import (  # noqa: E402
    atomic_write_json, build_skeleton, contract_fields, read_json, validate_result,
)

SAMPLE_CONTRACT = {
    "output_contract": {
        "editable_source": {"path": "presentation.pptx", "format": "pptx", "slides": 0},
        "export_artifact": {"pdf": {"path": "presentation.pdf", "pages": 0}},
        "content_map": [],
        "compliance_checks": [],
        "exceptions": [],
    }
}

FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def run_scaffold(case, materials):
    fields, degradations = contract_fields(case)
    result = build_skeleton(case, fields)
    return result, fields, degradations


def test_normal(root: Path):
    inp, out, work = root / "input", root / "output", root / "work"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    work.mkdir(parents=True)
    atomic_write_json(inp / "case.json", {"case_id": "demo-case", **SAMPLE_CONTRACT})
    (inp / "paper.md").write_text("# Demo\n\nWe propose method M with result 0.42.\n", encoding="utf-8")

    result, fields, _ = run_scaffold({"case_id": "demo-case", **SAMPLE_CONTRACT}, [])
    ok = atomic_write_json(out / "result.json", result)
    check("normal_write", ok, "result.json 写入")
    loaded, err = read_json(out / "result.json")
    check("normal_parse", err is None, str(err))
    checks = validate_result(loaded, fields, out)
    errs = [c for c in checks if c["severity"] == "error"]
    check("normal_no_errors", not errs, "校验无 error")
    missing = [k for k in fields if k not in loaded]
    check("normal_fields_complete", not missing, f"契约字段缺失={missing}")


def test_bad_json(root: Path):
    inp, out = root / "input", root / "output"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    (inp / "case.json").write_text("{not json", encoding="utf-8")
    case, err = read_json(inp / "case.json")
    check("bad_json_detected", err is not None and case is None, "坏 JSON 被捕获")
    fields, degradations = contract_fields(case or {})
    result = build_skeleton(case or {}, fields)
    result["status"] = "input_error"
    result["case_id"] = "unknown-case"
    result.setdefault("errors", []).append(str(err))
    ok = atomic_write_json(out / "result.json", result)
    loaded, err2 = read_json(out / "result.json")
    check("bad_json_still_writes", ok and err2 is None, "坏 JSON 仍写合法骨架")


def test_missing_input(root: Path):
    inp, out = root / "absent_input", root / "output"
    out.mkdir(parents=True)
    case, err = read_json(inp / "case.json")
    check("missing_input_detected", err is not None, "缺输入被捕获")
    fields, degradations = contract_fields(case or {})
    result = build_skeleton(case or {}, fields)
    result["status"] = "input_error"
    result["case_id"] = "unknown-case"
    ok = atomic_write_json(out / "result.json", result)
    loaded, err2 = read_json(out / "result.json")
    check("missing_input_case_id", ok and loaded.get("case_id") == "unknown-case", "case_id=unknown-case")


def test_empty_dir(root: Path):
    inp, out = root / "empty_input", root / "output"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    case, err = read_json(inp / "case.json")
    fields, degradations = contract_fields(case or {})
    result = build_skeleton(case or {}, fields)
    result["status"] = "insufficient_evidence"
    ok = atomic_write_json(out / "result.json", result)
    check("empty_dir_writes", ok, "空输入目录仍写合法骨架")


def main() -> int:
    print("== slides-01 selftest ==")
    with tempfile.TemporaryDirectory(prefix="slides01_selftest_") as td:
        root = Path(td)
        test_normal(root)
        test_bad_json(root / "bad")
        test_missing_input(root / "missing")
        test_empty_dir(root / "empty")
    if FAILURES:
        print(f"[result] FAIL — {len(FAILURES)} 项未通过: {FAILURES}")
        return 1
    print("[result] SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
