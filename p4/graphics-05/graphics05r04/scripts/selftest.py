#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the graphical-abstract skill (stdlib only).

Covers: a synthetic end-to-end case, an empty input directory, a bad JSON input,
and BOM tolerance. Exits 0 when all checks pass, 1 otherwise. This is the
deliverable-facing self-check referenced by SKILL.md §6.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402  (sibling module in scripts/)

CANONICAL = ("completed", "partial", "failed")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _check_result(output_dir, expected_case_id, allow_status):
    rp = os.path.join(output_dir, "result.json")
    assert os.path.isfile(rp), "result.json missing"
    r = json.load(open(rp, encoding="utf-8"))
    assert r.get("case_id") == expected_case_id, f"case_id={r.get('case_id')}"
    assert r.get("status") in allow_status, f"status={r.get('status')}"
    for k in ("sources", "uncertainty", "change_log"):
        assert k in r, f"missing {k}"
    return r


def test_synthetic(tmp):
    inp = os.path.join(tmp, "in")
    out = os.path.join(tmp, "out")
    work = os.path.join(tmp, "work")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    _write(os.path.join(inp, "case.json"), json.dumps({
        "id": "selftest-case-1",
        "output_contract": {
            "graphical_abstract": {"question": None, "method": None,
                                   "results": None, "conclusion": None},
            "editable_source": None,
            "exports": [],
        },
    }))
    _write(os.path.join(inp, "task.md"),
           "制作一张单页图形摘要。材料中 Enzyme A → 抑制 Tumor growth，"
           "处理后增殖率下降 42.3% (n=12)。输出 SVG 与 PNG/PDF。\n")
    _write(os.path.join(inp, "materials", "notes.md"),
           "Enzyme A inhibits Tumor growth; proliferation fell 42.3% (n=12).\n")
    result = main.run(inp, out, work)
    assert result.get("status") in CANONICAL
    r = _check_result(out, "selftest-case-1", CANONICAL)
    assert "graphical_abstract" in r
    svg = os.path.join(out, "fig_selftest-case-1.svg")
    assert os.path.isfile(svg), "SVG missing"
    ET.parse(svg)
    assert isinstance(r.get("exports"), list)
    print("  ok: synthetic case ->", r["status"], "| svg present | exports",
          len(r["exports"]))


def test_empty(tmp):
    inp = os.path.join(tmp, "empty-in")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(tmp, "empty-out")
    work = os.path.join(tmp, "empty-work")
    result = main.run(inp, out, work)
    assert result.get("status") == "failed"
    r = _check_result(out, result.get("case_id"), ("failed",))
    assert any(l.get("kind") == "skeleton" for l in r.get("limitations", [])), \
        "missing skeleton limitation"
    print("  ok: empty input -> failed, limitation recorded")


def test_bad_json(tmp):
    inp = os.path.join(tmp, "bad-in")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"), "{ not valid json ")
    out = os.path.join(tmp, "bad-out")
    work = os.path.join(tmp, "bad-work")
    result = main.run(inp, out, work)
    assert result.get("status") == "failed"
    print("  ok: bad JSON -> failed, no crash")


def test_bom(tmp):
    inp = os.path.join(tmp, "bom-in")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "wb") as fh:
        fh.write("\ufeff".encode("utf-8") + json.dumps(
            {"id": "bom-case", "output_contract": {"x": 1}}).encode("utf-8"))
    out = os.path.join(tmp, "bom-out")
    work = os.path.join(tmp, "bom-work")
    result = main.run(inp, out, work)
    assert result.get("case_id") == "bom-case"
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert r.get("x") == 1, "output_contract field not echoed"
    print("  ok: BOM tolerated, output_contract echoed verbatim")


def main_selftest():
    tmp = tempfile.mkdtemp(prefix="ga-skill-selftest-")
    failures = []
    try:
        for fn in (test_synthetic, test_empty, test_bad_json, test_bom):
            name = fn.__name__
            try:
                fn(tmp)
            except AssertionError as exc:
                failures.append(f"{name}: {exc}")
                print(f"  FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: unexpected {exc!r}")
                print(f"  FAIL {name}: unexpected {exc!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELFTEST FAILED:", "; ".join(failures))
        return 1
    print("SELFTEST OK: 4/4 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main_selftest())
