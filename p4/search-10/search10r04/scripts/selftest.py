#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-check for the cross-lingual literature-search Skill.

Runs the package end-to-end against temporary synthetic inputs (empty dir,
broken JSON, minimal valid case with output_contract), verifies SKILL.md
frontmatter, patch merging, validation and deterministic re-runs. Uses only
the standard library and the system temp directory; it never writes into
/app/output or /input and never fabricates a formal case answer.

Exit code 0 means every check passed.
"""

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crosslang as cl  # noqa: E402
import main as orchestrator  # noqa: E402


def _ns(input_dir, output_dir, work_dir, patch=None):
    args = argparse.Namespace(input=input_dir, output=output_dir, work=work_dir)
    args.patch = patch
    return args


def _read_json(path):
    obj, err = cl.safe_json_load(path)
    if obj is None:
        raise AssertionError("unreadable %s: %s" % (path, err))
    return obj


def check_frontmatter():
    skill_md = os.path.join(os.path.dirname(HERE), "SKILL.md")
    text, err = cl.read_text(skill_md)
    if text is None:
        raise AssertionError("SKILL.md unreadable: %s" % err)
    if not text.startswith("---"):
        raise AssertionError("SKILL.md missing YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise AssertionError("SKILL.md frontmatter not closed")
    front = text[3:end]
    for key in ("name:", "description:", "version:"):
        if key not in front:
            raise AssertionError("SKILL.md frontmatter missing %s" % key)
    return skill_md


def _run_init(inp, out, work):
    orchestrator.cmd_init(_ns(inp, out, work))
    return _read_json(os.path.join(out, "result.json"))


def test_empty_input(root):
    inp = os.path.join(root, "empty_in"); os.makedirs(inp)
    out = os.path.join(root, "empty_out"); work = os.path.join(root, "empty_work")
    result = _run_init(inp, out, work)
    assert result["status"] in ("input_error", "insufficient_evidence"), result["status"]
    assert result["case_id"]
    assert isinstance(result["errors"], list)
    assert isinstance(result["limitations"], list)
    issues = cl.validate_result(result, [])
    assert not issues, issues
    return "empty input -> input_error skeleton"


def test_bad_json(root):
    inp = os.path.join(root, "bad_in"); os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# some task\noutput_contract {\"hits\": []}\n")
    out = os.path.join(root, "bad_out"); work = os.path.join(root, "bad_work")
    result = _run_init(inp, out, work)
    assert any(e.get("kind") == "parse_or_missing" for e in result.get("errors", [])), result
    # contract recovered from task.md free text
    state = _read_json(os.path.join(work, "state.json"))
    assert "hits" in state.get("contract_fields", []), state
    issues = cl.validate_result(result, state.get("contract_fields", []))
    assert not issues, issues
    return "bad JSON -> recorded + contract recovered from task.md"


def test_minimal_case(root):
    inp = os.path.join(root, "ok_in"); os.makedirs(inp)
    case = {
        "case_id": "demo-case-1",
        "output_contract": {
            "hits": [],
            "summary": None,
            "sorting_basis": {"field": None, "order": None},
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    out = os.path.join(root, "ok_out"); work = os.path.join(root, "ok_work")
    result = _run_init(inp, out, work)
    for key in ("hits", "summary", "sorting_basis"):
        assert key in result, key
    assert result["hits"] == [] and result["summary"] is None
    state = _read_json(os.path.join(work, "state.json"))
    issues = cl.validate_result(result, state.get("contract_fields", []))
    assert not issues, issues
    return "minimal case -> contract echoed, validate clean"


def test_patch(root):
    inp = os.path.join(root, "patch_in"); os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "demo-case-1", "output_contract": {"hits": []}}, fh, ensure_ascii=False)
    out = os.path.join(root, "patch_out"); work = os.path.join(root, "patch_work")
    orchestrator.cmd_init(_ns(inp, out, work))
    patch_path = os.path.join(root, "patch.json")
    with open(patch_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"status": "ok", "change_log": [{"step": "search", "status": "ok"}]},
            fh, ensure_ascii=False,
        )
    orchestrator.cmd_patch(_ns(inp, out, work, patch=patch_path))
    result = _read_json(os.path.join(out, "result.json"))
    assert result["status"] == "ok"
    # change_log appends: init entry + search entry
    assert len(result["change_log"]) == 2, result["change_log"]
    return "patch -> deep merge, change_log appended"


def test_determinism(root):
    inp = os.path.join(root, "ok_in")  # reuse minimal case input
    out_a = os.path.join(root, "det_a"); work_a = os.path.join(root, "det_wa")
    out_b = os.path.join(root, "det_b"); work_b = os.path.join(root, "det_wb")
    orchestrator.cmd_init(_ns(inp, out_a, work_a))
    orchestrator.cmd_init(_ns(inp, out_b, work_b))
    a = _read_json(os.path.join(out_a, "result.json"))
    b = _read_json(os.path.join(out_b, "result.json"))
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(
        b, ensure_ascii=False, sort_keys=True
    ), "non-deterministic output"
    return "deterministic re-run (no wall-clock, fixed sort)"


def main():
    skill_md = check_frontmatter()
    root = tempfile.mkdtemp(prefix="crosslang_selftest_")
    checks = [
        ("frontmatter", lambda: skill_md),
        ("empty_input", lambda: test_empty_input(root)),
        ("bad_json", lambda: test_bad_json(root)),
        ("minimal_case", lambda: test_minimal_case(root)),
        ("patch", lambda: test_patch(root)),
        ("determinism", lambda: test_determinism(root)),
    ]
    failed = 0
    for name, fn in checks:
        try:
            detail = fn()
            print("PASS  %-14s %s" % (name, detail or ""))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL  %-14s %s" % (name, exc))
    print("selftest root: %s" % root)
    print("summary: %d/%d passed" % (len(checks) - failed, len(checks)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
