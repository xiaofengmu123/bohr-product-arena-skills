#!/usr/bin/env python3
"""Built-in self-test for the robustness contract. Runs five scenarios in a
temporary directory and reports PASS/FAIL. This is NOT a formal benchmark: all
inputs are synthetic and clearly labelled. Exit code 0 means the harness ran;
scenario failures are reported in the summary text."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

import core_impl as C
import main as M

SCENARIOS = []


def scenario(name, build, expect_status, check=None):
    SCENARIOS.append({"name": name, "build": build, "expect": expect_status, "check": check})


def _mk(root, **kw):
    os.makedirs(root, exist_ok=True)
    for name, content in kw.items():
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(content, str):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        else:
            with open(path, "wb") as fh:
                fh.write(content)
    return root


def happy_input(root):
    _mk(root, **{
        "case.json": json.dumps({
            "case_id": "demo-1",
            "title": "Synthetic apparatus demo",
            "output_contract": {"entities": None, "relations": None, "data_encoding": None,
                                "legend_annotations": None, "editable_sources": None, "exports": None},
        }, ensure_ascii=False),
        "task.md": "# Demo\n\n1. 配置反应器\n2. 连接冷凝管\n3. 收集产物\n",
    })


def bad_json_input(root):
    _mk(root, **{"case.json": "{ not valid json", "task.md": "step one\n"})


def empty_input(root):
    os.makedirs(root, exist_ok=True)


def oversized_input(root):
    # A ~2.1MB task.md must be skipped, not loaded.
    _mk(root, **{
        "case.json": json.dumps({"case_id": "big-1"}),
        "task.md": ("x" * (C.MAX_TEXT_BYTES + 50000)),
    })


def ir_with_coords(root):
    _mk(root, **{
        "case.json": json.dumps({"case_id": "demo-2", "title": "Coords demo",
                                 "output_contract": {"entities": None, "relations": None}}),
        "task.md": "demo\n",
        "ir.json": json.dumps({
            "meta": {"title": "Coords demo", "kind": "apparatus_technical_route", "layout_mode": "explicit"},
            "entities": [
                {"id": "a", "label": "反应器", "type": "apparatus", "x": 80, "y": 80, "source_ref": "demo#l1"},
                {"id": "b", "label": "冷凝管", "type": "apparatus", "x": 400, "y": 80, "source_ref": "demo#l2"},
            ],
            "relations": [{"from": "a", "to": "b", "kind": "material", "direction": "forward", "source_ref": "demo#l2"}],
            "data_encoding": [{"field": "flow", "rule": "solid blue arrow", "source_ref": "demo"}],
            "legend_annotations": [{"key": "material", "label": "物料流向", "color": "#1f77b4", "shape": "line"}],
            "sources": [{"locator": "task.md#l1", "quote": "配置反应器"}],
        }, ensure_ascii=False),
    })


scenario("empty_input", empty_input, "insufficient_evidence")
scenario("bad_json", bad_json_input, "insufficient_evidence")
scenario("oversized_task", oversized_input, "insufficient_evidence")
scenario("happy_path", happy_input, "complete")
scenario("explicit_ir", ir_with_coords, "complete")


def run_one(sc, root):
    args = M.build_argparser().parse_args([
        "run", "--input", root, "--work", os.path.join(root, "work"),
        "--out", os.path.join(root, "out"), "--no-render",
    ])
    rc = M.cmd_run(args)
    result_path = os.path.join(root, "out", "result.json")
    obj = json.load(open(result_path, encoding="utf-8"))
    ok = True
    msgs = []
    if obj.get("status") != sc["expect"]:
        ok = False
        msgs.append("status=%r expected=%r" % (obj.get("status"), sc["expect"]))
    if not obj.get("case_id"):
        ok = False
        msgs.append("missing case_id")
    if "change_log" not in obj:
        ok = False
        msgs.append("missing change_log")
    if sc["check"]:
        try:
            sc["check"](root, obj, msgs)
        except Exception as exc:
            ok = False
            msgs.append("check error: %s" % exc)
    return ok, msgs, rc


def main():
    base = tempfile.mkdtemp(prefix="scix_selftest_")
    results = []
    try:
        for sc in SCENARIOS:
            root = os.path.join(base, sc["name"])
            try:
                sc["build"](root)
                ok, msgs, rc = run_one(sc, root)
                results.append((sc["name"], ok, msgs))
            except Exception as exc:
                results.append((sc["name"], False, ["uncaught: %r" % exc]))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("selftest scenarios: %d" % len(results))
    all_ok = True
    for name, ok, msgs in results:
        all_ok = all_ok and ok
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + "; ".join(msgs)) if msgs else ""))
    print("selftest result: %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
