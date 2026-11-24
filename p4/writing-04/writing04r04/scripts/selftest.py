# -*- coding: utf-8 -*-
"""Standalone self-test for the citation-management skill.

Run from the package root:
    python3 -B scripts/selftest.py

It builds synthetic inputs inside a temporary directory, runs the real
pipeline end to end, and asserts the deliverables. It never writes to
/app/output and never fabricates scores. Exit code 0 == pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402
import main as app  # noqa: E402


def check(name: str, cond: bool, fails: list) -> None:
    print(("[ ok ] " if cond else "[FAIL] ") + name)
    if not cond:
        fails.append(name)


def make_case(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir()
    return d


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def run_self_tests() -> int:
    fails: list = []
    tmp = Path(tempfile.mkdtemp(prefix="cite_skill_test_"))

    try:
        # ------------------------------------------------------------------
        # Case A: complete input (manuscript + BibTeX library + contract).
        # ------------------------------------------------------------------
        case_a = make_case(tmp, "complete")
        write(case_a / "case.json", json.dumps({
            "case_id": "case-a",
            "output_contract": {
                "manuscript": "", "references": "",
                "evidence_map": "", "consistency_report": "",
            },
        }, ensure_ascii=False))
        write(case_a / "task.md", "# 任务：插入引用并同步编号\n")
        write(case_a / "manuscript.md",
              "This claim needs support [2,3]. Another one [1]. Repeat [2-3].\n")
        write(case_a / "library.bib",
              "@article{a,\n author={Smith, John},\n title={Alpha},\n journal={J1},\n year={2019}}\n"
              "@article{b,\n author={Li, Wei},\n title={Beta},\n journal={J2},\n year={2020},\n doi={10.1000/beta}}\n"
              "@article{c,\n author={Li, Wei},\n title={Beta},\n journal={J2},\n year={2020},\n doi={10.1000/beta}}\n")

        out_a = tmp / "out_a"
        out_a.mkdir()
        rc = app.main(["--input", str(case_a), "--output", str(out_a),
                       "--work", str(tmp / "work_a")])
        check("complete case exit 0", rc == 0, fails)
        result = json.loads((out_a / "result.json").read_text(encoding="utf-8"))
        check("case_a case_id", result["case_id"] == "case-a", fails)
        check("case_a status", result["status"] == "partial", fails)
        for key in ("manuscript", "references", "evidence_map", "consistency_report"):
            check(f"case_a contract field {key} filled",
                  isinstance(result.get(key), str) and Path(result[key]).exists(), fails)
        check("case_a artifacts", set(result.get("artifacts", {})) ==
              {"manuscript", "references", "evidence_map", "consistency_report"}, fails)
        check("case_a dedup merged 2->1",
              json.loads((out_a / "evidence_map.json").read_text(encoding="utf-8"))
              .get("merge_evidence", [{}])[0].get("merged_count") == 2, fails)
        ms = (out_a / "manuscript.md").read_text(encoding="utf-8")
        check("case_a renumbered", "[1,2]" in ms, fails)
        check("case_a has limitations", isinstance(result.get("limitations"), list)
              and len(result["limitations"]) >= 1, fails)
        check("case_a has uncertainty", isinstance(result.get("uncertainty"), list)
              and len(result["uncertainty"]) >= 1, fails)

        # ------------------------------------------------------------------
        # Case B: empty input -> insufficient_evidence, still valid JSON.
        # ------------------------------------------------------------------
        case_b = make_case(tmp, "empty")
        write(case_b / "case.json", json.dumps({"case_id": "case-b"}))
        out_b = tmp / "out_b"
        out_b.mkdir()
        rc = app.main(["--input", str(case_b), "--output", str(out_b),
                       "--work", str(tmp / "work_b")])
        check("empty case exit 0", rc == 0, fails)
        rb = json.loads((out_b / "result.json").read_text(encoding="utf-8"))
        check("empty case status insufficient_evidence", rb["status"] == "insufficient_evidence", fails)
        check("empty case artifacts exist", (out_b / "manuscript.md").exists()
              and (out_b / "references.md").exists(), fails)

        # ------------------------------------------------------------------
        # Case C: bad JSON + bad encoding -> no crash, recorded.
        # ------------------------------------------------------------------
        case_c = make_case(tmp, "bad")
        write(case_c / "case.json", "{not valid json")
        (case_c / "library.json").write_bytes(b"\xff\xfe\x00broken")
        write(case_c / "manuscript.md", "Some text [1].\n")
        out_c = tmp / "out_c"
        out_c.mkdir()
        rc = app.main(["--input", str(case_c), "--output", str(out_c),
                       "--work", str(tmp / "work_c")])
        check("bad case exit 0", rc == 0, fails)
        rc_json = json.loads((out_c / "result.json").read_text(encoding="utf-8"))
        check("bad case valid result", isinstance(rc_json, dict), fails)
        check("bad case records errors/degradations",
              isinstance(rc_json.get("errors"), list) or isinstance(rc_json.get("degradations"), list), fails)

        # ------------------------------------------------------------------
        # Case D: determinism (two runs -> identical deterministic artifacts).
        # ------------------------------------------------------------------
        out_d1 = tmp / "out_d1"
        out_d1.mkdir()
        app.main(["--input", str(case_a), "--output", str(out_d1), "--work", str(tmp / "work_d1")])
        out_d2 = tmp / "out_d2"
        out_d2.mkdir()
        app.main(["--input", str(case_a), "--output", str(out_d2), "--work", str(tmp / "work_d2")])
        same_ms = (out_d1 / "manuscript.md").read_text(encoding="utf-8") == \
                  (out_d2 / "manuscript.md").read_text(encoding="utf-8")
        check("determinism manuscript", same_ms, fails)

        # ------------------------------------------------------------------
        # Case E: unit checks on the deterministic engine.
        # ------------------------------------------------------------------
        check("entry_key prefers DOI", ce.entry_key({"doi": "10.1/X"}) == "doi:10.1/x", fails)
        check("collapse_nums range", ce.collapse_nums([1, 2, 3]) == "1-3", fails)
        check("expand_marker multi", ce.expand_marker("1,3-5") == [1, 3, 4, 5], fails)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("selftest FAIL:", len(fails), "checks failed ->", fails)
        return 1
    print("selftest PASS (all checks)")
    return 0


if __name__ == "__main__":
    sys.exit(run_self_tests())
