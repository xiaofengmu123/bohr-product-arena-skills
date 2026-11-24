#!/usr/bin/env python3
"""Pre-submission self-check for the research-writing transform skill.

Runs the failure-mode checks the SKILL promises: empty input directory, a broken
case.json, a minimal valid case with an output_contract, and a schema validation
of the final result.json. Prints a checkable PASS/FAIL list and exits non-zero
on any failure. Standard library only; works on any current working directory.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toolkit as tk  # noqa: E402
import main as main_mod  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{}] {} {}".format("PASS" if ok else "FAIL", name, detail))
    return ok


def _run_init(input_dir: str, output_dir: str, work_dir: str):
    args = types.SimpleNamespace(input=input_dir, output=output_dir, work=work_dir)
    return main_mod.cmd_init(args)


def _run_finalize(input_dir: str, output_dir: str, work_dir: str):
    args = types.SimpleNamespace(input=input_dir, output=output_dir, work=work_dir)
    return main_mod.cmd_finalize(args)


def _read_result(output_dir: str):
    path = os.path.join(output_dir, "result.json")
    data, err = tk.load_json(path)
    return data, err, path


def check_frontmatter() -> bool:
    path = os.path.join(SKILL_ROOT, "SKILL.md")
    text, err = tk.read_text(path)
    if err or not text:
        return check("SKILL.md frontmatter", False, str(err))
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return check("SKILL.md frontmatter", False, "no YAML block")
    block = m.group(1)
    has_name = re.search(r"^name:\s*\S+", block, re.M)
    has_desc = re.search(r"^description:\s*\S+", block, re.M)
    has_ver = re.search(r"^version:\s*\S+", block, re.M)
    ok = bool(has_name and has_desc and has_ver)
    return check("SKILL.md frontmatter", ok, "name/description/version")


def check_empty_dir() -> bool:
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in"); os.makedirs(inp)
        out = os.path.join(td, "out"); work = os.path.join(td, "work")
        code = _run_init(inp, out, work)
        data, err, _ = _read_result(out)
        ok = code == 0 and err is None and isinstance(data, dict)
        ok = ok and data.get("status") in ("input_error", "insufficient_evidence", "partial")
        return check("empty input dir", ok, "status={}".format(data.get("status") if isinstance(data, dict) else None))


def check_bad_json() -> bool:
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in"); os.makedirs(inp)
        out = os.path.join(td, "out"); work = os.path.join(td, "work")
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "x", "broken": ')
        code = _run_init(inp, out, work)
        data, err, _ = _read_result(out)
        ok = code == 0 and err is None and isinstance(data, dict)
        ok = ok and any("case.json" in e for e in data.get("errors", []))
        return check("broken case.json", ok, "errors recorded")


def check_minimal_case() -> bool:
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in"); os.makedirs(inp)
        out = os.path.join(td, "out"); work = os.path.join(td, "work")
        case = {
            "case_id": "demo-001",
            "task": {"case_id": "demo-001"},
            "output_contract": {
                "artifacts": [], "evidence_map": [], "pending_human": [],
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo\n")
        code = _run_init(inp, out, work)
        data, err, _ = _read_result(out)
        ok = code == 0 and err is None and isinstance(data, dict)
        ok = ok and data.get("case_id") == "demo-001"
        ok = ok and all(k in data for k in ("artifacts", "evidence_map", "pending_human"))
        # finalize should not fail and should validate cleanly once filled.
        data["status"] = "ok"
        data["uncertainty"] = "none"
        tk.write_json(data, os.path.join(out, "result.json"))
        code2 = _run_finalize(inp, out, work)
        data2, _, _ = _read_result(out)
        ok = ok and code2 == 0 and data2.get("status") == "ok"
        return check("minimal case + finalize", ok, "case_id={}".format(data.get("case_id")))


def main() -> int:
    print("skill_root={}".format(SKILL_ROOT))
    results = [
        check_frontmatter(),
        check_empty_dir(),
        check_bad_json(),
        check_minimal_case(),
    ]
    failed = results.count(False)
    print("summary: {}/{} passed".format(len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
