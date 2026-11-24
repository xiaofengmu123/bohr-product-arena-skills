#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — offline self-test for the cross-lingual literature search Skill.

Runs a set of deterministic scenarios in a temporary directory and prints a
PASS/FAIL checklist grouped by the topic's scoring dimensions. No network, no
third-party packages, no randomness.

Exit code 0 = all checks pass; 1 = at least one check failed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core
import main as cli


def _args(**kw):
    base = {"input": None, "output": None, "work": None,
            "patch": None, "log": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _run_init(root, name):
    inp = os.path.join(root, name, "input")
    out = os.path.join(root, name, "output")
    work = os.path.join(root, name, "work")
    os.makedirs(inp, exist_ok=True)
    cli.cmd_init(_args(input=inp, output=out, work=work))
    return inp, out, work


def scenario_empty(root):
    """Empty input directory must still write a legal input_error result."""
    _, out, work = _run_init(root, "empty")
    result = _read_json(os.path.join(out, "result.json"))
    checks = []
    checks.append(("empty.status_is_input_error", result.get("status") == "input_error"))
    checks.append(("empty.valid_json", result.get("case_id") == "unknown_case"))
    checks.append(("empty.has_errors", bool(result.get("errors"))))
    return checks


def scenario_bad_json(root):
    """Bad case.json + valid task.md -> errors recorded, still a legal result."""
    inp = os.path.join(root, "badjson", "input")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"), "{ this is not json")
    _write(os.path.join(inp, "task.md"), "# task\ncase_id: case-bad-1\n")
    out = os.path.join(root, "badjson", "output")
    work = os.path.join(root, "badjson", "work")
    cli.cmd_init(_args(input=inp, output=out, work=work))
    result = _read_json(os.path.join(out, "result.json"))
    checks = []
    checks.append(("badjson.case_id_from_task", result.get("case_id") == "case-bad-1"))
    checks.append(("badjson.status_partial", result.get("status") == "partial"))
    checks.append(("badjson.error_recorded", any("bad_json" in e for e in result.get("errors", []))))
    return checks


def scenario_contract(root):
    """Explicit output_contract fields must all appear in result.json."""
    inp = os.path.join(root, "contract", "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "case-contract-1",
        "output_contract": {
            "summary": None,
            "findings": [],
            "terms": [],
        },
    }
    _write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    out = os.path.join(root, "contract", "output")
    work = os.path.join(root, "contract", "work")
    cli.cmd_init(_args(input=inp, output=out, work=work))
    result = _read_json(os.path.join(out, "result.json"))
    checks = []
    checks.append(("contract.case_id", result.get("case_id") == "case-contract-1"))
    for key in ("summary", "findings", "terms"):
        checks.append(("contract.field.%s" % key, key in result))
    checks.append(("contract.status_partial", result.get("status") == "partial"))
    return checks


def scenario_validate_and_patch(root):
    """validate has no issues on a fresh init; patch merges and logs."""
    inp, out, work = _run_init(root, "patch")
    _write(os.path.join(inp, "case.json"), json.dumps({"case_id": "case-patch-1"}))
    _write(os.path.join(inp, "task.md"), "# task\nresearch question here\n")
    # Re-init after adding the inputs so case_id is discovered.
    cli.cmd_init(_args(input=inp, output=out, work=work))
    patch = {
        "research_question": {
            "question": "Find the original Portuguese version of a known paper.",
            "mode": "known_item",
        },
    }
    patch_path = os.path.join(root, "patch", "p.json")
    _write(patch_path, json.dumps(patch, ensure_ascii=False))
    rc = cli.cmd_patch(_args(patch=patch_path, output=out, work=work, log="step-1"))
    result = _read_json(os.path.join(out, "result.json"))
    state = cli._load_state(work)
    issues = core.validate_result(result, state.get("contract_keys"))
    checks = []
    checks.append(("patch.rc_zero", rc == 0))
    checks.append(("patch.deep_merged", result["research_question"]["mode"] == "known_item"))
    checks.append(("patch.log_appended", len(result.get("change_log", [])) == 1))
    checks.append(("patch.validate_clean", len(issues) == 0))
    return checks


def scenario_cli_help():
    """scripts/main.py --help must exit 0."""
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    proc = subprocess.run(
        [sys.executable, main_py, "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    return [("cli.help_exit_zero", proc.returncode == 0)]


def run_all():
    root = tempfile.mkdtemp(prefix="crosslang-selftest-")
    groups = []
    try:
        groups.append(("可靠体验/异常处理", scenario_empty(root)))
        groups.append(("可靠体验/异常处理", scenario_bad_json(root)))
        groups.append(("输出契约完整性", scenario_contract(root)))
        groups.append(("增量改进/可核验", scenario_validate_and_patch(root)))
        groups.append(("调用与易用性", scenario_cli_help()))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    all_ok = True
    print("== cross-lingual literature search selftest ==")
    for group_name, checks in groups:
        print("[%s]" % group_name)
        for name, passed in checks:
            all_ok = all_ok and passed
            print("  %-4s %s" % ("PASS" if passed else "FAIL", name))
    print("==")
    print("selftest:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run_all())
